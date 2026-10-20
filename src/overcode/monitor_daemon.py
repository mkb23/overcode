#!/usr/bin/env python3
"""
Monitor Daemon - Single source of truth for all session metrics.

This daemon handles all monitoring responsibilities:
- Agent status detection (hook or polling detector)
- Time tracking (green_time_seconds, non_green_time_seconds)
- Agent stats sync (tokens, interactions)
- Presence tracking (graceful degradation on non-macOS)
- Status history logging (CSV)

The Monitor Daemon publishes MonitorDaemonState to a JSON file that
consumers (TUI, Supervisor Daemon) read from.

This separation ensures:
- No duplicate time tracking between TUI and daemon
- Clean interface contract via MonitorDaemonState
- Platform-agnostic core (presence is optional)

Pure business logic is extracted to monitor_daemon_core.py for testability.
"""

import os
import signal
import sys
import threading
import time  # noqa: F401 - tests patch monitor_daemon.time.sleep
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from .clock import SYSTEM_CLOCK, Clock, aware, stamp
from .daemon_logging import BaseDaemonLogger
from .daemon_utils import create_daemon_helpers
from .backends import (
    capability_names,
    session_backend_name,
    session_capabilities,
)
from .monitor_daemon_state import (
    MonitorDaemonState,
    SessionDaemonState,
)
from .pid_utils import (
    acquire_daemon_lock,
    remove_pid_file,
)
from .session_manager import PendingUpdates, SessionIndex, SessionManager
from .settings import (
    DAEMON,
    DAEMON_VERSION,
    ensure_session_dir,
    get_monitor_daemon_pid_path,
    get_monitor_daemon_state_path,
    get_agent_history_path,
    get_supervisor_stats_path,
)
from .config import get_monitor_daemon_config
from .status_constants import (
    AT_REST_STATUSES,
    STATUS_ASLEEP,
    STATUS_DONE,
    STATUS_HEARTBEAT_START,
    STATUS_RUNNING,
    STATUS_RUNNING_HEARTBEAT,
    STATUS_TERMINATED,
    STATUS_UNKNOWN,
    STATUS_WAITING_HEARTBEAT,
    STATUS_WAITING_OVERSIGHT,
    STATUS_WAITING_USER,
    is_green_status,
)
from .pane_capture_gate import PaneCaptureGate, PaneChangeTracker
from .status_patterns import extract_pr_number
from .status_detector_factory import StatusDetectorDispatcher
from .status_history import STATUS_HISTORY_KEEPALIVE_SECONDS, log_agent_status, status_row_due
from .swarm_history import SWARM_HISTORY_FILE, SwarmHistoryWriter
from . import daemon_presence
from .daemon_cadence import Cadence, DaemonCadences
from .daemon_housekeeping import Housekeeping
from .daemon_process_probe import ProcessProbe
from .daemon_stats_sync import StatsSync
from .quota_engine import QuotaEngine, UsageStore
from .child_lifecycle import CHILD_NOT_WORKING, OVERSIGHT_TIMEOUT_REASON, child_rollup, task_result
from .engine_recorders import RecorderStore
from .ledger import billing_name_of
from .engine_wake import WakeLoop
from .namer import Namer
from .swarm_publisher import SwarmPublisher
from . import swarm
from .claude_bg import ClaudeBgSource, fleet_agents
from .claude_bg_ledger import BgLedger
from .daemon_presence import (  # noqa: F401 - TUI_ATTENDED_FRESHNESS: tests
    MACOS_APIS_AVAILABLE,
    NullPresence,
    PresenceComponent,
    TUI_ATTENDED_FRESHNESS,
)
from .monitor_daemon_core import (
    UNOBSERVED_GAP_SECONDS,
    calculate_time_accumulation,
    calculate_median,
    elapsed_seconds,
    parse_datetime_safe,
    is_heartbeat_eligible,
    is_heartbeat_due,
    should_enforce_oversight_timeout,
)
from .tmux_utils import (
    AGENT_ID_OPTION,
    PaneInfo,
    pane_for_window,
    send_text_to_tmux_window,
)

if TYPE_CHECKING:
    from .protocols import StatusDetectorProtocol, TmuxInterface


# Interval settings (in seconds)
INTERVAL_FAST = DAEMON.interval_fast    # When active or agents working
INTERVAL_SLOW = DAEMON.interval_slow    # When all agents need user input
INTERVAL_IDLE = DAEMON.interval_idle    # When no agents at all
INTERVAL_UNATTENDED = DAEMON.interval_unattended  # Nobody watching (see attendance)

# Watchdog: the main loop beats engine.sock every tick and sleep step. Once
# the beat is engine_socket.STALL_SECONDS old the socket thread logs the
# main thread's stack and signals it (SIGUSR1 ends a sleep the kernel never
# woke: seen on macOS after a machine sleep, a 0.25 s sleep that lasted
# 26 hours). Still stuck this long, the daemon exits so the TUI starts a
# fresh one rather than serving frozen status.
WATCHDOG_EXIT_SECONDS = 300.0
# Token/cost/energy columns: synced this often while someone is watching,
# and at the old 60 s cadence (budgets, history) while nobody is.
STATS_SYNC_ATTENDED_SECONDS = 5
STATS_SYNC_UNATTENDED_SECONDS = 60
# Git diff and untracked counts while attended; not read at all unattended.
GIT_SYNC_SECONDS = 15
# Burn over the windows views asked for, while attended.
BURN_SYNC_SECONDS = 5
# The state file is rewritten when what it publishes changed, and otherwise
# this often, so readers that judge liveness by its last_loop_time (is_stale:
# the expected gap plus 30 s) never see a quiet daemon as dead.
STATE_FILE_LIVENESS_SECONDS = 10.0

# The every-60-loops housekeeping (done-agent auto-archive, untracked window
# count, terminated-session archive) was 120 s of wall clock at the 2 s
# loop; it is a wall-clock cadence now so the unattended loop keeps it.
HOUSEKEEPING_INTERVAL_SECONDS = 60 * DAEMON.interval_fast

# Statuses that leave the fleet idle (all_waiting_user)
_QUIET_STATUSES = AT_REST_STATUSES | {STATUS_WAITING_USER, STATUS_UNKNOWN}


# Create PID helper functions using factory
(
    is_monitor_daemon_running,
    get_monitor_daemon_pid,
    stop_monitor_daemon,
) = create_daemon_helpers(get_monitor_daemon_pid_path, "monitor")


def _is_budget_exceeded(session, stats) -> bool:
    """Check if session has exceeded its cost budget (#173)."""
    try:
        budget = session.cost_budget_usd
        return isinstance(budget, (int, float)) and budget > 0 and stats.estimated_cost_usd >= budget
    except (AttributeError, TypeError):
        return False


def _create_monitor_logger(session: str = "agents", log_file: Optional[Path] = None) -> BaseDaemonLogger:
    """Create a logger for the monitor daemon."""
    if log_file is None:
        session_dir = ensure_session_dir(session)
        log_file = session_dir / "monitor_daemon.log"
    return BaseDaemonLogger(log_file)


class MemoryLogger(BaseDaemonLogger):
    """A daemon logger that keeps its lines in memory (``lines``), for tests."""

    def __init__(self):
        import io

        from rich.console import Console

        from .daemon_logging import DAEMON_THEME

        self.log_file = None
        self.lines: List[str] = []
        self.console = Console(file=io.StringIO(), theme=DAEMON_THEME, force_terminal=True)

    def _write_to_file(self, message: str, level: str = "INFO"):
        self.lines.append(f"[{level}] {message}")

    def debug(self, message: str):
        self.lines.append(f"[DEBUG] {message}")


class NullTmux:
    """A tmux server with no sessions, for ``MonitorDaemon.for_test``.

    Every read is empty and every write fails, so a test daemon touches
    no real tmux. Tests that need panes pass their own double.
    """

    def capture_pane(self, session, window, lines=100):
        return None

    def send_keys(self, session, window, keys, enter=True):
        return False

    def has_session(self, session):
        return False

    def new_session(self, session):
        return False

    def new_window(self, session, name, command=None, cwd=None, *,
                   wait_for_prompt=False, options=None):
        return None

    def kill_window(self, session, window):
        return False

    def kill_windows(self, session, match):
        return []

    def rename_window(self, session, window, new_name):
        return False

    def set_window_option(self, session, window, option, value):
        return False

    def kill_session(self, session):
        return False

    def list_windows(self, session):
        return []

    def attach(self, session, window=None, bare=False):
        return None

    def get_pane_pid(self, session, window):
        return None

    def list_panes(self, session):
        return None

    def select_window(self, session, window):
        return False

    def ensure_empty_placeholder_window(self, session, window_name, message):
        return False

    def resize_window(self, session, window, width, height):
        return False


class MonitorDaemon:
    """Monitor Daemon - single source of truth for all session metrics.

    Responsibilities:
    - Status detection for all sessions
    - Time tracking (green/non-green)
    - Agent stats sync
    - Presence tracking (optional)
    - Status history logging
    - Publishing MonitorDaemonState

    Each tmux session gets its own Monitor Daemon instance with
    isolated state files and PID tracking.
    """

    def __init__(
        self,
        tmux_session: str = "agents",
        session_manager: Optional[SessionManager] = None,
        status_detector: Optional["StatusDetectorProtocol"] = None,
        tmux: Optional["TmuxInterface"] = None,
        clock: Optional[Clock] = None,
    ):
        # Ensure session directory exists
        ensure_session_dir(tmux_session)

        # One tmux client for the daemon's lifetime (audit R7). The periodic
        # syncs used to build a fresh RealTmux each, so its 30 s cache never
        # hit and every pane-pid lookup was list-sessions + list-windows +
        # list-panes. Injected by tests and the bench. Its reads are bounded:
        # a wedged tmux server costs a tick one timeout, not the daemon.
        if tmux is None:
            from .implementations import TMUX_COMMAND_TIMEOUT_SECONDS, RealTmux

            tmux = RealTmux(command_timeout=TMUX_COMMAND_TIMEOUT_SECONDS)

        from .config import get_hostname
        from .settings import resolve_detection_mode

        self._setup(
            tmux_session,
            tmux=tmux,
            # Session-specific paths
            pid_path=get_monitor_daemon_pid_path(tmux_session),
            state_path=get_monitor_daemon_state_path(tmux_session),
            history_path=get_agent_history_path(tmux_session),
            # Dependencies (allow injection for testing)
            session_manager=session_manager or SessionManager(),
            detection_mode=resolve_detection_mode(tmux_session),
            polling_detector=status_detector,
            # Hostname for history disambiguation
            hostname=get_hostname(),
            # Presence tracking (graceful degradation)
            presence=PresenceComponent(tmux_session=tmux_session),
            # Logging - session-specific log file
            log=_create_monitor_logger(session=tmux_session),
            # Loop interval while nobody is watching (config.yaml
            # monitor_daemon.interval_unattended_seconds; read once).
            interval_unattended=get_monitor_daemon_config()["interval_unattended"],
            clock=clock,
            quota=QuotaEngine(tmux_session),
        )

    @classmethod
    def for_test(
        cls,
        state_dir,
        detector=None,
        tmux: Optional["TmuxInterface"] = None,
        session_manager: Optional[SessionManager] = None,
        presence=None,
        log=None,
        tmux_session: str = "agents",
        clock: Optional[Clock] = None,
        claude_jobs_dir: Optional[Path] = None,
        quota: Optional[QuotaEngine] = None,
    ) -> "MonitorDaemon":
        """A daemon whose files live in ``state_dir``, for tests.

        No presence thread (``presence`` defaults to one that reports
        nothing), no real tmux (``tmux`` defaults to a ``NullTmux``, a
        server with no sessions), a log kept in memory, and a
        ``SessionManager`` on ``state_dir`` unless one is given.
        ``detector`` replaces the status detector dispatcher; ``clock``
        the host's clocks (a test steps one to step every reading). Nothing
        here reads the environment's session directory. Claude background
        sessions are read from ``claude_jobs_dir`` (default: a directory
        under ``state_dir`` that does not exist), never ``~/.claude/jobs``.
        Subscription usage is never fetched: ``quota`` defaults to an engine
        on ``state_dir/usage`` with no login (a test passes its own fetch).
        """
        state_dir = Path(state_dir)
        state_dir.mkdir(parents=True, exist_ok=True)
        tmux = tmux if tmux is not None else NullTmux()
        daemon = cls.__new__(cls)
        daemon._setup(
            tmux_session,
            tmux=tmux,
            pid_path=state_dir / "monitor_daemon.pid",
            state_path=state_dir / "monitor_daemon_state.json",
            history_path=state_dir / "agent_status_history.csv",
            session_manager=(session_manager if session_manager is not None
                             else SessionManager(state_dir=state_dir, skip_git_detection=True)),
            detection_mode="polling",
            detector=detector,
            detector_tmux=tmux,
            hostname="test-host",
            presence=presence if presence is not None else NullPresence(),
            log=log if log is not None else MemoryLogger(),
            interval_unattended=int(DAEMON.interval_unattended),
            clock=clock,
            claude_jobs_dir=(Path(claude_jobs_dir) if claude_jobs_dir is not None
                             else state_dir / "claude-jobs"),
            quota=quota if quota is not None else QuotaEngine(
                tmux_session, store=UsageStore(state_dir / "usage"),
                fetch=lambda: {"error": "no token", "no_token": True},
                account=lambda: "test", threaded=False),
        )
        return daemon

    def _setup(
        self,
        tmux_session: str,
        *,
        tmux: "TmuxInterface",
        pid_path: Path,
        state_path: Path,
        history_path: Path,
        session_manager: SessionManager,
        detection_mode: str,
        hostname: str,
        presence,
        log,
        interval_unattended: int,
        polling_detector: Optional["StatusDetectorProtocol"] = None,
        detector=None,
        detector_tmux: Optional["TmuxInterface"] = None,
        clock: Optional[Clock] = None,
        claude_jobs_dir: Optional[Path] = None,
        quota: Optional[QuotaEngine] = None,
    ) -> None:
        """Every attribute the daemon has, from its resolved dependencies."""
        # Every time the daemon reads, wall and monotonic, comes from here;
        # its components (detectors, housekeeping, recorders, the wake loop)
        # read the same one. Timestamps it writes are aware.
        self.clock: Clock = clock if clock is not None else SYSTEM_CLOCK
        self.tmux_session = tmux_session
        self._tmux = tmux
        self.pid_path = pid_path
        self.state_path = state_path
        self.history_path = history_path
        self.session_manager = session_manager

        # Capture gating (audit R11): each loop plans, from the tick's pane
        # listing, which panes changed and captures only those; the
        # detectors read the rest from the gate's cache. See _plan_captures.
        self._capture_gate = PaneCaptureGate()
        self._pane_tracker = PaneChangeTracker()
        # Child agents still working, by parent id, from the last published
        # tick (one tick behind is fine): the dispatcher's hook detectors
        # read it, so a parent waits on its children (_count_children_working)
        self._children_working: Dict[str, int] = {}
        if detector is None:
            detector = StatusDetectorDispatcher(
                tmux_session,
                tmux=detector_tmux,
                polling_detector=polling_detector,
                mode=detection_mode,
                capture_gate=self._capture_gate,
                children_working=self._children_working,
                clock=self.clock,
            )
        elif isinstance(getattr(detector, "children_working", None), dict):
            self._children_working = detector.children_working
        self.detector = detector

        self._hostname = hostname
        self.presence = presence
        self.log = log

        # State tracking
        self.state = MonitorDaemonState(
            pid=os.getpid(),
            status="starting",
            started_at=stamp(self.clock.wall()),
            daemon_version=DAEMON_VERSION,
        )

        # engine.sock (docs/design/engine-0.6.md): started by run(), so a
        # daemon object built for a test never binds a socket
        self._engine = None
        # The stall's age at its last wake-up signal (None: not stalled)
        self._stall_signalled_at: Optional[float] = None
        self._main_thread_id = threading.main_thread().ident
        # The recorded colour per agent (#507): episodes, bells, visits,
        # persisted in engine_state.json beside the state file
        self.recorder_store = RecorderStore(state_path.parent, log, self._unobserved_gap_seconds,
                                            clock=self.clock)
        # The state file: monotonic time of its last write, and the engine
        # snapshot it holds (None: not written yet); see _state_file_due
        self._last_state_save = 0.0
        self._saved_snapshot = None
        # Expanded parents' delegated work, row by row (agent-observability
        # §6.2): built only for parents a visible view subscribed to
        self.swarm_publisher = SwarmPublisher(
            self._swarm_state, self._conversations, clock=self.clock.time,
            quota_of=self._member_quota)
        # Claude background sessions under the fleet's directories (phase 3):
        # adopted rows, published as agent views to the views only
        self.claude_bg = ClaudeBgSource(claude_jobs_dir)
        # The views those rows were last published as (see _claude_bg_views)
        self._claude_bg_published: Optional[dict] = None
        # Their spend in the billing ledger, under their agent's code
        # (claude_bg_ledger); synced after each rebuild of the rows and at
        # every housekeeping pass
        self.claude_bg_ledger = BgLedger(
            lambda: getattr(getattr(self.session_manager, "ledger", None), "dir", None),
            owner=tmux_session)
        self._claude_bg_ledger_builds = -1

        # Wall time of the previous complete tick, published so consumers can
        # size their staleness window (MonitorDaemonState.is_stale).
        self._last_tick_duration_seconds: float = 0.0
        # Failed ticks in a row, and the last one's error (see _tick_failed)
        self._tick_errors = 0
        self._last_tick_error: Optional[str] = None
        # session id -> (first failure, last error): see _detection_failed
        self._detect_failures: Dict[str, Tuple[datetime, Optional[str]]] = {}

        # This tmux session's panes from one ``list-panes -s`` per tick
        # (``_panes_at``): pid per window for the 5 s / 15 s syncs, and the
        # attached-client count. None when the listing failed.
        self._pane_table: Optional[Dict[str, PaneInfo]] = None
        self._pane_table_at: Optional[datetime] = None
        self.session_attached: Optional[int] = None

        # Per-session tracking
        self.previous_states: Dict[str, str] = {}
        self.last_state_times: Dict[str, datetime] = {}
        self.operation_start_times: Dict[str, datetime] = {}

        # agent_status_history.csv is written on change (audit R10): per
        # session id, the (status, activity) pair last logged and when, so
        # a row goes out when the pair moves, when the last row is a
        # keepalive old, and the first time this daemon sees the session.
        self._last_logged: Dict[str, Tuple[str, str]] = {}
        self._last_keepalive: Dict[str, datetime] = {}
        # The same for the adopted background sessions' rows (by row id)
        self._bg_logged: Dict[str, Tuple[str, str]] = {}
        self._bg_keepalive: Dict[str, datetime] = {}
        # swarm_history.csv beside it: each parent's delegated-work counts,
        # a row per group when they change (the timeline's density rows)
        self.swarm_history = SwarmHistoryWriter(history_path.with_name(SWARM_HISTORY_FILE))

        # What views render beside the status, per session id (0.6.0: the
        # TUI computes none of it). Time accounting as of the last status
        # change: ([green, non_green, sleep], epoch). The pane-derived
        # counts, with the pane text they were extracted from (unchanged
        # text, no re-parse). The transcript-derived values of the last
        # stats sync are stats_sync.stats_views.
        self._time_bases: Dict[str, Tuple[list, float]] = {}
        self._pane_views: Dict[str, Tuple[str, dict]] = {}
        # Git diff/untracked by directory and burn by session id, from the
        # attended-only syncs (see _sync_git, _sync_burn)
        self._git_by_dir: Dict[str, tuple] = {}
        self._burn_by_session: Dict[str, dict] = {}

        # Everything a tick wants persisted to sessions.json is staged here
        # and written once at the end of the tick (audit R5); see
        # _flush_pending_writes. Reads inside the tick go through
        # self._pending.view(session) so they see the staged values.
        self._pending: PendingUpdates = PendingUpdates()

        self._last_hook_phases: Dict[str, str] = {}  # session_id → last logged phase
        # The last tick's live agent names, for detection-mode checks between ticks
        self._live_names: Optional[List[Tuple[str, str]]] = None
        # Finished agents the id and stats syncs have read since they finished
        self._ids_settled: set = set()
        self._stats_settled: set = set()
        # Transcript stats: tokens, cost, model, PR, session ids, and the
        # values views render beside the token columns
        self.stats_sync = StatsSync(session_manager=session_manager, log=log, clock=self.clock)
        # Subscription quota (cost model Phase B): usage polling, the token
        # stream, the fitted model and each agent's predicted weekly points
        self.quota = quota
        # The process table, once per tick, for CPU / RSS and the sandbox probe
        self.process_probe = ProcessProbe()

        # The periodic syncs, each due when its interval has passed since it
        # last ran (never run: due on the first tick)
        self.cadence = DaemonCadences(
            # Transcript stats: STATS_SYNC_ATTENDED_SECONDS or, unattended,
            # STATS_SYNC_UNATTENDED_SECONDS (see _sync_session_stats)
            stats=Cadence(STATS_SYNC_ATTENDED_SECONDS),
            # Session ID detection runs more frequently than full stats (#116)
            session_ids=Cadence(10),
            # Available skills sync (#252) — infrequent, skills rarely change
            skills=Cadence(60),
            # Sandbox state sync (#451) — detect /sandbox toggle via claude PID listeners
            sandbox=Cadence(15),
            # Per-agent CPU / RSS sampling (from `ps`, summed over the agent
            # process tree). Slow enough to avoid adding load, fast enough to
            # flag runaway agents that are dominating the machine.
            resources=Cadence(5),
            git=Cadence(GIT_SYNC_SECONDS),
            burn=Cadence(BURN_SYNC_SECONDS),
            # agent_status_history.csv rotation/retention check (#465, #468) —
            # a cheap stat()-then-maybe-rewrite, but still only worth doing hourly.
            history_rotation=Cadence(3600),
            # models.dev catalog auto-refresh (#473) — opt-in via config.yaml
            # model_metadata.auto_refresh; the hourly check itself is a stat().
            model_metadata=Cadence(3600),
            # Housekeeping is wall-clock: first pass HOUSEKEEPING_INTERVAL_SECONDS
            # after the first tick (loop 60 at 2 s used to be 2 minutes in),
            # then every interval, whatever the loop length.
            housekeeping=Cadence(HOUSEKEEPING_INTERVAL_SECONDS),
        )

        # Archiving, untracked windows, job pruning, history rotation,
        # models.dev refresh
        self.housekeeping = Housekeeping(
            tmux_session,
            tmux=tmux,
            history_path=history_path,
            log=log,
            housekeeping=self.cadence.housekeeping,
            rotation=self.cadence.history_rotation,
            model_metadata=self.cadence.model_metadata,
            clock=self.clock,
        )

        # Loop interval while nobody is watching
        self._interval_unattended: int = interval_unattended

        # The auto-namer and Claude's own session titles (#542): acts on
        # turn ends seen here, calls the model on its own worker thread
        self.namer = Namer(
            tmux_session, session_manager, tmux=tmux, log=log, clock=self.clock,
            titles=lambda: self.stats_sync.transcript_titles,
            spend_path=state_path.parent / "namer_spend.json",
        )


        # Shutdown flag
        self._shutdown = False
        # The sleep between ticks: watchdog beats, the wake scan, quick ticks
        self.wake = WakeLoop(self)

        # Heartbeat tracking (#171)
        self._heartbeat_triggered_sessions: set = set()  # Session IDs that received heartbeat this loop
        self._sessions_running_from_heartbeat: set = set()  # Persistent: sessions currently running due to heartbeat
        self._heartbeat_start_pending: set = set()  # One-shot: sessions awaiting first "running" observation after heartbeat

        # Legacy migration flag — runs once on first tick
        self._legacy_windows_migrated = False

    def _tag_agent_windows(self, sessions: list) -> None:
        """Give each live agent's window its ``@overcode_id`` (auto-naming §1).

        Launches set it; a window launched before the option existed gets
        it here, once per daemon start. The hook handler's pane fallback
        reads it.
        """
        try:
            live = {w['name'] for w in self._tmux.list_windows(self.tmux_session)}
        except Exception as e:
            self.log.warn(f"Window tagging skipped: {e}")
            return
        for session in sessions:
            if session.tmux_window in live and not getattr(session, "is_remote", False):
                self._tmux.set_window_option(
                    self.tmux_session, session.tmux_window, AGENT_ID_OPTION, session.id)

    def _migrate_legacy_window_ids(self, sessions: list) -> None:
        """Migrate legacy digit-string tmux_window values to actual window names."""
        try:
            tmux_windows = self._tmux.list_windows(self.tmux_session)
            if not tmux_windows:
                return
            index_to_name = {str(w['index']): w['name'] for w in tmux_windows}
            for session in sessions:
                if session.tmux_window.isdigit() and session.tmux_window in index_to_name:
                    new_name = index_to_name[session.tmux_window]
                    session.tmux_window = new_name
                    self.session_manager.update_session(session.id, tmux_window=new_name)
                    self.log.info(f"Migrated {session.name} window: {session.tmux_window} → {new_name}")
        except Exception as e:
            self.log.warn(f"Legacy window migration failed: {e}")

    def _swarm_state(self, session_name: str) -> Optional[dict]:
        getter = getattr(self.detector, "get_swarm_state", None)
        return getter(session_name) if callable(getter) else None

    def _conversations(self, session_id: str) -> Optional[dict]:
        session = self.session_manager.sessions_by_id().get(session_id)
        return getattr(getattr(session, "stats", None), "conversations", None)

    def _member_quota(self, entries: dict) -> Optional[float]:
        """A delegated agent's points of the weekly quota, from its spend entries."""
        quota = getattr(self, "quota", None)
        return quota.conversation_pp(entries) if quota is not None else None

    def _swarm_subscriptions(self) -> frozenset:
        engine = self._engine
        return getattr(engine, "swarm_subscriptions", None) or frozenset()

    def _session_index(self) -> SessionIndex:
        """A ``SessionIndex`` over the manager's current snapshot (one stat)."""
        return SessionIndex(self.session_manager.sessions_by_id())

    def track_session_stats(
        self, session, status: str, index: Optional[SessionIndex] = None,
        now: Optional[datetime] = None,
    ) -> SessionDaemonState:
        """Track session state and build SessionDaemonState.

        Returns the session state for inclusion in MonitorDaemonState.

        ``index`` is the tick's ``SessionIndex``; the hierarchy fields
        (parent name, depth, children count) are looked up in it instead of
        being recomputed from the session table per session. Callers
        without one get an index over the current snapshot. ``now`` is the
        tick's time (default: the daemon's clock).
        """
        session_id = session.id
        now = self.clock.wall() if now is None else aware(now)
        if index is None:
            index = self._session_index()

        # Get previous status
        prev_status = self.previous_states.get(session_id, status)

        # Update time tracking
        self._update_state_time(session, status, now)

        # Track state transitions for operation timing
        was_running = is_green_status(prev_status)
        is_running = is_green_status(status)

        # Session went from running to waiting (operation started)
        if was_running and not is_running:
            self.operation_start_times[session_id] = now

        # Session went from waiting to running (operation completed)
        if not was_running and is_running:
            if session_id in self.operation_start_times:
                start_time = self.operation_start_times[session_id]
                op_duration = elapsed_seconds(start_time, now)
                del self.operation_start_times[session_id]

                # Update operation times
                current_stats = session.stats
                op_times = list(current_stats.operation_times)
                if op_duration > 0:
                    op_times.append(op_duration)
                    op_times = op_times[-100:]
                    self._pending.update_stats(
                        session_id,
                        operation_times=op_times,
                        last_activity=stamp(now)
                    )
                    self.log.info(f"[{session.name}] Operation completed ({op_duration:.1f}s)")

        # Update previous state
        self.previous_states[session_id] = status

        # Build session state for publishing
        stats = session.stats
        base = self._time_bases.get(session_id)

        # Calculate next heartbeat due time (#171)
        next_heartbeat_due = None
        if session.heartbeat_enabled and not session.heartbeat_paused:
            last_hb = parse_datetime_safe(session.last_heartbeat_time, near=now)
            if last_hb is None:
                last_hb = parse_datetime_safe(session.start_time, near=now)
            if last_hb:
                from datetime import timedelta
                next_due = last_hb + timedelta(seconds=session.heartbeat_frequency_seconds)
                next_heartbeat_due = stamp(next_due)

        # Check if this session is running from heartbeat (persistent across loops)
        running_from_heartbeat = session_id in self._sessions_running_from_heartbeat

        # Check if this session is waiting for heartbeat to auto-resume
        waiting_for_heartbeat = (
            status == STATUS_WAITING_HEARTBEAT
            or (status not in (STATUS_RUNNING, STATUS_TERMINATED, STATUS_ASLEEP, STATUS_UNKNOWN)
                and session.heartbeat_enabled
                and not session.heartbeat_paused
                and bool(session.heartbeat_instruction))
        )

        last_hook_at = getattr(self.detector, "get_last_hook_at", None)
        last_hook_at = last_hook_at(session.name) if callable(last_hook_at) else None
        result, reason = task_result(session, status)
        swarm_rollup = getattr(self.detector, "get_swarm_rollup", None)
        swarm_rollup = swarm_rollup(session.name) if callable(swarm_rollup) else None
        return SessionDaemonState(
            last_hook_at=last_hook_at if isinstance(last_hook_at, (int, float)) else None,
            session_id=session_id,
            name=session.name,
            tmux_window=session.tmux_window,
            current_status=status,
            current_activity=stats.current_task or "",
            status_since=stats.state_since,
            green_time_seconds=stats.green_time_seconds,
            non_green_time_seconds=stats.non_green_time_seconds,
            sleep_time_seconds=stats.sleep_time_seconds,
            interaction_count=stats.interaction_count,
            input_tokens=stats.input_tokens,
            output_tokens=stats.output_tokens,
            cache_creation_tokens=stats.cache_creation_tokens,
            cache_read_tokens=stats.cache_read_tokens,
            estimated_cost_usd=stats.estimated_cost_usd,
            estimated_energy_j=stats.estimated_energy_j,
            current_context_tokens=stats.current_context_tokens,
            median_work_time=self._calculate_median_work_time(stats.operation_times),
            repo_name=session.repo_name,
            branch=session.branch,
            standing_instructions=session.standing_instructions or "",
            standing_orders_complete=session.standing_orders_complete,
            steers_count=stats.steers_count,
            start_time=session.start_time,
            permissiveness_mode=session.permissiveness_mode,
            start_directory=session.start_directory,
            is_asleep=session.is_asleep,
            enhanced_context_enabled=session.enhanced_context_enabled,
            agent_value=session.agent_value,
            # Heartbeat state (#171)
            heartbeat_enabled=session.heartbeat_enabled,
            heartbeat_frequency_seconds=session.heartbeat_frequency_seconds,
            heartbeat_paused=session.heartbeat_paused,
            last_heartbeat_time=session.last_heartbeat_time,
            next_heartbeat_due=next_heartbeat_due,
            running_from_heartbeat=running_from_heartbeat,
            waiting_for_heartbeat=waiting_for_heartbeat,
            model=session.model,
            effort=getattr(session, 'effort', None),
            provider=session.provider,
            backend=getattr(session, 'backend', None) or 'claude-code',
            backend_capabilities=capability_names(session_capabilities(session)),
            # Tags (#356)
            tags=list(session.tags),
            # Focal repo (#170)
            focal_repo_subdir=getattr(session, 'focal_repo_subdir', None),
            # Cost budget (#173)
            cost_budget_usd=session.cost_budget_usd,
            budget_exceeded=_is_budget_exceeded(session, stats),
            # Agent hierarchy (#244), from the tick's index
            parent_name=index.parent_name(session),
            depth=index.depth(session),
            children_count=index.children_count(session.id),
            # Oversight system
            oversight_policy=getattr(session, 'oversight_policy', 'wait') or 'wait',
            oversight_timeout_seconds=getattr(session, 'oversight_timeout_seconds', 0.0) or 0.0,
            oversight_deadline=getattr(session, 'oversight_deadline', None),
            task_result=result,
            task_reason=reason,
            swarm_rollup=swarm_rollup if isinstance(swarm_rollup, dict) else None,
            subagent_cost_usd=swarm.subagent_cost(getattr(stats, "conversations", None)),
            # Last user command (from history sync)
            last_command=self.stats_sync.last_commands.get(session_id),
            pr_number=session.pr_number,
            # Skills (#252)
            available_skills=session.available_skills,
            loaded_skills=session.loaded_skills,
            # Wrapper/sandbox badges (#437, #451)
            wrapper=session.wrapper,
            sandbox_enabled=session.sandbox_enabled,
            skill_profile=getattr(session, 'skill_profile', None),
            billing_code=billing_name_of(session),
            # Resource usage (summed over the agent process tree)
            cpu_percent=session.cpu_percent,
            rss_bytes=session.rss_bytes,
            time_base=list(base[0]) if base else None,
            time_base_at=base[1] if base else None,
            **self.stats_sync.stats_views.get(session_id, {}),
        )

    def check_and_send_heartbeats(self, sessions: list, now: Optional[datetime] = None) -> set:
        """Check all sessions and send heartbeats if due.

        Args:
            sessions: List of Session objects to check
            now: The tick's time (default: the daemon's clock)

        Returns:
            Set of session IDs that received heartbeats this loop
        """
        now = self.clock.wall() if now is None else aware(now)
        triggered = set()

        for session in sessions:
            prev_status = self.previous_states.get(session.id)
            if not is_heartbeat_eligible(
                heartbeat_enabled=session.heartbeat_enabled,
                heartbeat_paused=session.heartbeat_paused,
                is_asleep=session.is_asleep,
                prev_status_green=bool(prev_status and is_green_status(prev_status)),
                budget_exceeded=_is_budget_exceeded(session, session.stats),
                has_instruction=bool(session.heartbeat_instruction),
            ):
                continue

            if not is_heartbeat_due(
                last_heartbeat_time=session.last_heartbeat_time,
                session_start_time=session.start_time,
                frequency_seconds=session.heartbeat_frequency_seconds,
                now=now,
            ):
                continue

            # In hooks mode, double-check the hook state right before sending.
            # The agent may have started working between the daemon's last
            # detection and now (e.g., user typed something). Sending a
            # heartbeat into a running session corrupts the prompt (#374).
            if self.detector.mode == "hooks":
                hook_detector = self.detector.hooks
                hook_state = hook_detector._read_hook_state(session.name)
                if hook_state:
                    event = hook_state.get("event", "")
                    if event in ("UserPromptSubmit", "PreToolUse", "PostToolUse"):
                        self.log.info(f"[{session.name}] Heartbeat skipped (hook says {event})")
                        continue

            # Send the heartbeat instruction
            if send_text_to_tmux_window(
                session.tmux_session,
                session.tmux_window,
                session.heartbeat_instruction,
                send_enter=True,
            ):
                self._pending.update_session(
                    session.id,
                    last_heartbeat_time=stamp(now)
                )
                triggered.add(session.id)
                self.log.info(f"[{session.name}] Heartbeat sent")

        return triggered

    def _unobserved_gap_seconds(self) -> float:
        """An agent unseen this long was not watched (freeze, downtime, sleep).

        UNOBSERVED_GAP_SECONDS, or three unattended loops if those are
        configured longer, so a slow configured loop is never a gap.
        """
        interval = self._interval_unattended or 0
        return max(UNOBSERVED_GAP_SECONDS, 3.0 * interval)

    def _update_state_time(self, session, status: str, now: datetime) -> None:
        """Update green_time_seconds and non_green_time_seconds.

        Time between two observations goes to the status seen. A gap longer
        than _unobserved_gap_seconds (the daemon was frozen or down, or the
        machine slept — including the downtime before a restart, measured
        from the persisted last_time_accumulation) goes to no status: it is
        dropped, and the view's time base restarts from now.
        """
        session_id = session.id
        current_stats = session.stats

        # Get last recorded time
        last_time = self.last_state_times.get(session_id)
        if last_time is None:
            # First observation after daemon (re)start - use last_time_accumulation
            # to avoid re-adding time that was already accumulated before restart
            last_time = parse_datetime_safe(current_stats.last_time_accumulation, near=now)
            if last_time is None:
                # Fallback for sessions without last_time_accumulation
                last_time = parse_datetime_safe(current_stats.state_since, near=now)
            if last_time is None:
                last_time = now
            self.last_state_times[session_id] = last_time
            # The views count on from the base; after a long downtime they
            # count from now, as the next tick will (the gap is not counted)
            base_at = last_time.timestamp()
            if now.timestamp() - base_at > self._unobserved_gap_seconds():
                base_at = now.timestamp()
            self._time_bases[session_id] = (
                [current_stats.green_time_seconds, current_stats.non_green_time_seconds,
                 current_stats.sleep_time_seconds],
                base_at,
            )
            return  # Don't accumulate on first observation

        # Calculate elapsed time (by the clock: DST-safe)
        elapsed = elapsed_seconds(last_time, now)
        if elapsed <= 0:
            return

        # Get session start time for capping
        session_start = parse_datetime_safe(session.start_time, near=now)

        # Use pure function for time accumulation (with sleep time tracking #141)
        prev_status = self.previous_states.get(session_id, status)
        result = calculate_time_accumulation(
            current_status=status,
            previous_status=prev_status,
            elapsed_seconds=elapsed,
            current_green=current_stats.green_time_seconds,
            current_non_green=current_stats.non_green_time_seconds,
            current_sleep=current_stats.sleep_time_seconds,
            session_start=session_start,
            now=now,
            max_elapsed_seconds=self._unobserved_gap_seconds(),
        )
        if result.unobserved:
            self.log.info(
                f"[{session.name}] {elapsed / 60:.0f} min unobserved "
                f"(daemon down, frozen or machine asleep); not counted"
            )

        if result.was_capped:
            total = current_stats.green_time_seconds + current_stats.non_green_time_seconds + current_stats.sleep_time_seconds
            max_allowed = elapsed_seconds(session_start, now) if session_start else 0
            self.log.warn(
                f"[{session.name}] Time tracking reset: "
                f"accumulated {total/3600:.1f}h > uptime {max_allowed/3600:.1f}h"
            )

        # Update state tracking
        state_since = current_stats.state_since
        if result.state_changed:
            state_since = stamp(now)
        elif not state_since:
            # Initialize state_since if never set (e.g., new session)
            state_since = stamp(now)

        # Staged for the tick's single write
        self._pending.update_stats(
            session_id,
            current_state=status,
            state_since=state_since,
            green_time_seconds=result.green_seconds,
            non_green_time_seconds=result.non_green_seconds,
            sleep_time_seconds=result.sleep_seconds,
            last_time_accumulation=stamp(now),
        )

        self.last_state_times[session_id] = now
        if (result.state_changed or result.was_capped or result.unobserved
                or session_id not in self._time_bases):
            self._time_bases[session_id] = (
                [result.green_seconds, result.non_green_seconds, result.sleep_seconds],
                now.timestamp(),
            )

    def sync_session_id(self, session) -> None:
        """Detect and bind the agent's session ids (``StatsSync.sync_session_id``)."""
        self.stats_sync.sync_session_id(session)

    def sync_agent_stats(self, session) -> None:
        """Stage the agent's transcript stats in this tick's writes (``StatsSync``)."""
        self.stats_sync.sync_agent_stats(session, self._pending)

    def _pane_view(self, session, pane_content: str) -> dict:
        """The pane-derived counts views render, from the text detection read.

        Detection serves an unchanged pane from the capture gate's cache,
        so the same text comes back tick after tick; it is parsed once.
        """
        cached = self._pane_views.get(session.id)
        if cached is not None and cached[0] == pane_content:
            return cached[1]
        if pane_content:
            from .status_patterns import extract_from_pane, get_patterns

            extracted = extract_from_pane(pane_content, get_patterns(session_backend_name(session)))
            view = {
                "background_bash_count": extracted.background_bash_count,
                "bash_count_ambiguous": extracted.bash_count_ambiguous,
                "live_subagent_count": extracted.live_subagent_count,
                "in_session_mode": extracted.in_session_mode,
            }
        else:
            view = {}
        self._pane_views[session.id] = (pane_content, view)
        return view

    def _calculate_median_work_time(self, operation_times: List[float]) -> float:
        """Calculate median operation time."""
        return calculate_median(operation_times)

    def attendance(self) -> str:
        """``"attended"`` or ``"unattended"``: see :func:`daemon_presence.attendance`."""
        return daemon_presence.attendance(
            engine=self._engine,
            session_attached=self.session_attached,
            presence=self.presence,
            tmux_session=self.tmux_session,
        )

    def calculate_interval(
        self, sessions: list, all_waiting_user: bool, unattended: bool = False
    ) -> int:
        """Calculate appropriate loop interval.

        The monitor daemon runs at the fixed fast interval whenever anyone
        is watching, for consistent monitoring resolution (variable
        frequency by agent state is the supervisor daemon's). While
        ``unattended`` (see :meth:`attendance`) it stretches to the
        configured unattended interval: status history is written on
        change, so the timeline it keeps for the user's return has no holes
        at that resolution, and heartbeats, oversight timeouts and the
        housekeeping are wall-clock, so nothing drifts with the loop.
        """
        if unattended:
            return self._interval_unattended
        return INTERVAL_FAST

    def _enforce_oversight_timeouts(self, sessions: list, now: Optional[datetime] = None) -> None:
        """Enforce oversight timeouts for waiting_oversight sessions."""
        now = self.clock.wall() if now is None else aware(now)
        for snapshot in sessions:
            # This tick may have staged a deadline, or cleared one
            session = self._pending.view(snapshot)
            if not should_enforce_oversight_timeout(
                session.status,
                getattr(session, 'oversight_policy', 'wait'),
                getattr(session, 'oversight_deadline', None),
                now,
                deadline_at=getattr(session, 'oversight_deadline_at', None),
            ):
                continue
            self._pending.update_session(
                session.id,
                report_status="failure",
                report_reason=OVERSIGHT_TIMEOUT_REASON,
            )
            self._pending.update_session_status(session.id, "done")
            self.log.info(f"[{session.name}] Oversight timeout expired, marked done")

    def _publish_state(self, session_states: List[SessionDaemonState], save: bool = True) -> None:
        """Publish current state: to engine.sock always, to the JSON file when ``save``."""
        now = self.clock.wall()

        # Update presence state
        presence_state, presence_idle, _ = self.presence.get_current_state()

        self.state.last_loop_time = stamp(now)
        self.state.last_loop_at = now.timestamp()
        self.state.last_tick_duration_seconds = round(self._last_tick_duration_seconds, 3)
        self.state.slow_tick_seconds = (
            round(self.state.last_tick_duration_seconds, 1)
            if self.state.last_tick_duration_seconds > self.state.current_interval else None
        )
        self.state.sessions = session_states
        self.state.presence_available = self.presence.available
        self.state.presence_state = presence_state
        self.state.presence_idle_seconds = presence_idle

        # Read supervisor stats if available (populated by supervisor daemon)
        supervisor_stats_path = get_supervisor_stats_path(self.tmux_session)
        if supervisor_stats_path.exists():
            try:
                import json
                with open(supervisor_stats_path) as f:
                    stats = json.load(f)
                self.state.supervisor_launches = stats.get("supervisor_launches", 0)
                self.state.supervisor_tokens = stats.get("supervisor_tokens", 0)
                # Daemon Claude run tracking
                self.state.supervisor_claude_running = stats.get("supervisor_claude_running", False)
                self.state.supervisor_claude_started_at = stats.get("supervisor_claude_started_at")
                self.state.supervisor_claude_total_run_seconds = stats.get("supervisor_claude_total_run_seconds", 0.0)
                self.state.supervisor_consecutive_timeouts = stats.get("consecutive_timeouts", 0)
                self.state.supervisor_last_timeout_at = stats.get("last_timeout_at")
            except (json.JSONDecodeError, OSError):
                pass

        snapshot = self.state.to_engine_snapshot(now.timestamp())
        if save and self._state_file_due(snapshot):
            error_before = self.state.state_file_error
            self._save_state_file()
            if self.state.state_file_error != error_before:
                # The save failed or recovered: the views must hear which
                snapshot = self.state.to_engine_snapshot(now.timestamp())
            self._saved_snapshot = snapshot
        # Published whether or not the file could be written: a full disk
        # must not freeze every view's status (the error rides along)
        # Adopted Claude background sessions are agent views too, for the
        # views only (the CLI, supervisor and sister API read the state
        # file, which never holds them)
        adopted = self._claude_bg_views()
        self._claude_bg_published = adopted
        engine = self._engine
        if engine is not None:
            try:
                # The expanded parents' virtual rows go to views only, never
                # to the state file (a new object: _saved_snapshot stays as saved)
                virtual = self.swarm_publisher.rows(
                    self._swarm_subscriptions(), session_states)
                if virtual or adopted:
                    from .engine_protocol import Snapshot
                    agents = dict(snapshot.agents, **adopted) if adopted else snapshot.agents
                    snapshot = Snapshot(snapshot.seq, agents, snapshot.fleet, virtual or {})
                engine.publish(snapshot)
            except Exception as e:  # a view problem must never stop the tick
                self.log.warn(f"engine.sock publish failed: {e}")

    def _claude_bg_views(self) -> dict:
        """The adopted background sessions as agent views, by row id."""
        skip = MonitorDaemonState.ENGINE_TICK_AGENT_FIELDS
        return {row.state.session_id: {k: v for k, v in row.state.to_dict().items()
                                       if k not in skip}
                for row in self.claude_bg.rows}

    def _refresh_claude_bg(self, sessions: Optional[list] = None) -> bool:
        """Re-read the background sessions, feed their colours; True if their views moved.

        ``sessions`` (a full tick's) refreshes which agents they may belong
        to; between ticks the last tick's stand. Each row's colour goes to
        the recorder on every refresh, as an agent's does on every tick, so
        a blocked session rings once its red has stood G seconds.
        """
        source = self.claude_bg
        if sessions is not None:
            source.set_agents(fleet_agents(
                sessions, getattr(self.detector, "get_foreign_sessions", None)))
        attended = self.state.interval_mode != "unattended"
        source.refresh(self.clock.monotonic(), self.clock.time(),
                       STATS_SYNC_ATTENDED_SECONDS if attended else STATS_SYNC_UNATTENDED_SECONDS)
        ts = self.clock.time()
        for row in source.rows:
            self.recorder_store.record_adopted(row.state, row.light.detail(), row.started_at,
                                               ts, engine=self._engine)
        self._log_claude_bg_history(source.rows)
        self._sync_claude_bg_ledger()
        return self._claude_bg_views() != self._claude_bg_published

    def _log_claude_bg_history(self, rows) -> None:
        """Status history rows for the 🌙 rows, as for agents: on change and keepalive.

        Keyed by row id (``claude-bg:<sessionId>``) in the session id
        column, so the timeline draws an ordinary row for each. A finished
        one gets its one row and no keepalives.
        """
        now = self.clock.wall()
        ids = set()
        for row in rows:
            state = row.state
            rid = state.session_id
            ids.add(rid)
            status, activity = state.current_status, state.current_activity or ""
            if not status_row_due(self._bg_logged.get(rid), self._bg_keepalive.get(rid),
                                  status, activity, now,
                                  (float("inf") if status in (STATUS_TERMINATED, STATUS_DONE)
                                   else STATUS_HISTORY_KEEPALIVE_SECONDS)):
                continue
            try:
                log_agent_status(state.name, status, activity, history_file=self.history_path,
                                 session_id=rid, hostname=self._hostname, now=now)
            except OSError as e:
                self.log.warn(f"[{state.name}] status history not written: {e}")
            self._bg_logged[rid] = (status, activity[:100])
            self._bg_keepalive[rid] = now
        for rid in [r for r in self._bg_logged if r not in ids]:
            del self._bg_logged[rid]
            self._bg_keepalive.pop(rid, None)

    def _sync_claude_bg_ledger(self, force: bool = False) -> int:
        """Bill the background sessions' spend (claude_bg_ledger); rows written.

        After a rebuild of their rows (spend read, a row come or gone), and
        at every housekeeping pass (``force``: a checkpoint that waited for
        its interval), and when every row went at once (the last agent
        ended, the source switched off): what they spent closes.
        """
        source = self.claude_bg
        vanished = not source.rows and self.claude_bg_ledger.observing
        if not force and not vanished and source.builds == self._claude_bg_ledger_builds:
            return 0
        self._claude_bg_ledger_builds = source.builds
        ledger = getattr(self.session_manager, "ledger", None)
        if ledger is None:
            return 0
        try:
            return self.claude_bg_ledger.sync(source.rows, source.agents, ledger,
                                              self.clock.time(), reread=source.read_spend_of)
        except Exception as e:  # the ledger must never stop the engine
            self.log.warn(f"background-session spend not billed: {e}")
            return 0

    def _state_file_due(self, snapshot) -> bool:
        """Whether to rewrite the state file this tick (engine-0.6.md: on change).

        A quiet fleet changes nothing the views see, so the file (about
        140 KB at 50 agents, fsync'd) is rewritten only for liveness.
        """
        from .engine_protocol import diff

        saved = self._saved_snapshot
        if saved is None or self.state.state_file_error:
            return True
        if self.clock.monotonic() - self._last_state_save >= STATE_FILE_LIVENESS_SECONDS:
            return True
        return not diff(saved, snapshot).empty

    def _save_state_file(self) -> None:
        """Write the state file; on failure keep going and say so in the state."""
        previous = self.state.state_file_error
        self.state.state_file_error = None
        try:
            self.state.save(self.state_path)
        except Exception as e:
            message = f"{type(e).__name__}: {e}"
            if message != previous:
                self.log.error(f"State file not saved: {message}")
            self.state.state_file_error = message
            return
        self._last_state_save = self.clock.monotonic()
        if previous:
            self.log.info("State file saved again")

    def _refresh_detection_mode(self, sessions: Optional[list] = None) -> None:
        """Re-read the fleet default detection mode.

        The legacy global detection_mode file, or "auto": hooks once a live
        agent has a hook file. Per-agent overrides are resolved inside the
        dispatcher, on top of this default. Quick ticks re-read it too, so
        the first hook event of an "auto" fleet is read as one at once;
        they pass no sessions and use the last tick's.
        """
        from .settings import resolve_detection_mode
        if sessions is not None:
            self._live_names = [(s.id, s.name) for s in sessions]
        current_mode = resolve_detection_mode(self.tmux_session, self._live_names)
        self.state.detection_mode = current_mode
        if self.detector.mode != current_mode:
            self.detector.mode = current_mode
            self.log.info(f"Fleet detection mode changed to: {current_mode}")

    def _attach_git_and_burn(self, session, state: SessionDaemonState) -> None:
        from .git_stats import effective_git_directory

        git = self._git_by_dir.get(effective_git_directory(session))
        if git is not None:
            state.git_diff, state.git_untracked = git
        state.burn = self._burn_by_session.get(session.id)
        if self.quota is not None:
            state.quota = self.quota.agent_view(session.id)
        if getattr(session, "trip_reset_at", None):
            from .trip import trip_view, week_start

            usage = self.quota.fleet_usage if self.quota is not None else None
            state.trip = trip_view(session, state, state.quota,
                                   week_start(usage, self.clock.wall().timestamp()))

    def _sync_git(self, sessions: list, now: datetime) -> None:
        """Git diff/untracked per distinct directory, every GIT_SYNC_SECONDS while attended."""
        if self.state.interval_mode == "unattended":
            return
        if not self.cadence.git.due(now):
            return
        self.cadence.git.mark(now)
        from concurrent.futures import ThreadPoolExecutor

        from .git_stats import effective_git_directory, get_git_diff_stats, get_git_untracked_count

        dirs = sorted({d for d in (effective_git_directory(s) for s in sessions
                                   if not getattr(s, "is_remote", False)) if d})
        if not dirs:
            return

        def scan(directory):
            try:
                diff = get_git_diff_stats(directory)
                return directory, (list(diff) if diff else None, get_git_untracked_count(directory))
            except Exception:
                return directory, (None, None)

        with ThreadPoolExecutor(max_workers=min(8, len(dirs))) as pool:
            self._git_by_dir = dict(pool.map(scan, dirs))

    def _sync_burn(self, sessions: list, now: datetime) -> None:
        """Burn over each window a visible view asked for, every BURN_SYNC_SECONDS."""
        engine = self._engine
        windows = engine.burn_windows if engine is not None else set()
        if not windows or self.state.interval_mode == "unattended":
            self._burn_by_session = {}
            return
        if not self.cadence.burn.due(now):
            return
        self.cadence.burn.mark(now)
        from .burn import compute_window_burn

        asleep = {s.id for s in sessions if getattr(s, "is_asleep", False)}
        by_session: Dict[str, dict] = {}
        # Quota points per window for the burn's Q% mode, from the Q% column's model
        quota_pp_of = self.quota.window_pp if self.quota is not None else None
        for hours in sorted(windows):
            stats = compute_window_burn(sessions, asleep, hours, now=now, quota_pp_of=quota_pp_of)
            for sid, burn in stats.per_session.items():
                entry = by_session.setdefault(sid, {})[str(float(hours))] = {
                    "input_tokens": burn.input_tokens,
                    "output_tokens": burn.output_tokens,
                    "cache_creation_tokens": burn.cache_creation_tokens,
                    "cache_read_tokens": burn.cache_read_tokens,
                    "cost_usd": round(burn.cost_usd, 4),
                    "energy_j": round(burn.energy_j, 1),
                }
                if burn.quota_pp is not None:
                    entry["quota_pp"] = burn.quota_pp
        self._burn_by_session = by_session

    def _sync_quota(self, sessions: list, now: datetime) -> None:
        """Subscription quota, every tick: cheap unless tokens moved or a poll is due.

        Reads each agent with this tick's staged stats (``_pending.view``).
        """
        quota = self.quota
        if quota is None:
            return
        quota.sync([self._pending.view(s) for s in sessions], now.timestamp())
        self.state.usage = quota.fleet_usage

    def _start_engine_socket(self) -> None:
        """Serve engine.sock next to the state file; log and carry on if it can't."""
        from .engine_socket import EngineServer, socket_path

        try:
            server = EngineServer(socket_path(self.state_path.parent))
            server.start()
        except OSError as e:
            self.log.warn(f"engine.sock unavailable, views cannot connect: {e}")
            return
        server.on_stall = self._on_main_loop_stall
        self._engine = server
        self.log.info(f"engine.sock: {server.path}")

    def _beat(self) -> None:
        """The main loop is alive: tell engine.sock (and re-arm the watchdog)."""
        engine = self._engine
        if engine is not None:
            engine.beat()
        self._stall_signalled_at = None

    def _on_main_loop_stall(self, age: float) -> None:
        """Socket thread, each second while the main loop has not beaten for ``age`` s."""
        if age >= WATCHDOG_EXIT_SECONDS:
            self.log.error(f"Main loop stuck {age:.0f}s after a wake-up signal; exiting for a restart")
            # os._exit skips the finally that removes it. The lock is what
            # says "running", but don't leave a PID that may get reused.
            try:
                remove_pid_file(self.pid_path, os.getpid())
            except Exception:
                pass
            os._exit(1)
        # Signal again every 30 s: one signal can land outside the sleep
        last = self._stall_signalled_at
        if last is not None and age - last < 30.0:
            return
        self._stall_signalled_at = age
        if last is None:
            frame = sys._current_frames().get(self._main_thread_id)
            stack = "".join(traceback.format_stack(frame)) if frame is not None else "(no frame)"
            self.log.warn(f"Main loop stalled {age:.0f}s; signalling it. Its stack:\n{stack}")
        try:
            signal.pthread_kill(self._main_thread_id, signal.SIGUSR1)
        except (OSError, ValueError) as e:
            self.log.warn(f"could not signal the main loop: {e}")

    # ------------------------------------------------------------------
    # Tick phases — decomposed from the monolithic run() loop
    # ------------------------------------------------------------------

    def _run_tick(self, now: datetime) -> None:
        """One tick, which may fail without ending the daemon.

        A failing tick (a bug in one phase, a corrupt sessions.json) used
        to end the process and with it monitoring for the whole fleet. It
        is logged, published, and the loop carries on: the next tick tries
        again at the usual interval.
        """
        try:
            self._tick(now)
        except Exception as e:
            self._tick_failed(e)
            return
        failed = self._tick_errors
        if failed:
            self.log.info(f"Tick recovered after {failed} failed tick(s)")
            self._tick_errors = 0
            self._last_tick_error = None

    def _tick_failed(self, error: Exception) -> None:
        """Log a failed tick and publish the daemon status as ``error``.

        The traceback is logged when the error is new; the same error
        again logs one line at 1, 2, 4, 8... failures in a row, so a tick
        that fails every loop is visible without filling the log. The
        published status (the views' "Monitor:" symbol) stays ``error``
        until a tick completes; the agents keep their last published state.
        """
        count = self._tick_errors + 1
        self._tick_errors = count
        message = f"{type(error).__name__}: {error}"
        if message != self._last_tick_error:
            self.log.error(f"Tick failed ({count} in a row): {message}\n{traceback.format_exc()}")
        elif count & (count - 1) == 0:
            self.log.error(f"Tick failed ({count} in a row): {message}")
        self._last_tick_error = message
        self.state.status = "error"
        try:
            self._publish_state(self.state.sessions)
        except Exception as e:
            self.log.warn(f"could not publish the failed tick: {e}")

    def _tick(self, now: datetime) -> None:
        """Execute one monitoring loop iteration.

        Times itself: the duration is published by the *next* tick's
        ``_publish_state`` (this tick publishes mid-way, before it knows its
        own length) so consumers can widen their staleness window instead of
        treating a slow tick as a dead daemon.
        """
        now = aware(now)
        tick_t0 = self.clock.monotonic()
        self.state.tick_started_at = stamp(now)
        # Observing from the first tick, with or without agents (#507)
        self.recorder_store.begin(now.timestamp())
        try:
            self._tick_phases(now)
        finally:
            self._last_tick_duration_seconds = self.clock.monotonic() - tick_t0

    def _tick_phases(self, now: datetime) -> None:
        """The phases of one tick, in order."""
        # One snapshot per tick: the index answers every parent/child lookup
        # the tick makes, and ``sessions`` is this tmux session's slice of it
        # in file order (what list_sessions() would return, filtered).
        index = self._session_index()
        sessions = [s for s in index.by_id.values() if s.tmux_session == self.tmux_session]
        self._refresh_detection_mode(sessions)
        if not self._legacy_windows_migrated:
            self._migrate_legacy_window_ids(sessions)
            self._tag_agent_windows(sessions)
            self._legacy_windows_migrated = True
        self._sync_session_ids(sessions, now)
        self._sync_session_stats(sessions, now)
        self._sync_available_skills(sessions, now)
        self._sync_sandbox_state(sessions, now)
        self._sync_process_resources(sessions, now)
        self._sync_git(sessions, now)
        self._sync_burn(sessions, now)
        self._sync_quota(sessions, now)
        self._dispatch_heartbeats(sessions, now)
        newly_done: list = []
        try:
            self.recorder_store.apply_visits(self._engine)
            newly_done = self._settle_children(sessions)
            session_states, all_waiting = self._detect_and_enrich(sessions, now, index)
            self._cleanup_stale(sessions)
            try:
                self._refresh_claude_bg(sessions)
            except Exception as e:  # the agents' own rows must still publish
                self.log.warn(f"background sessions not read: {e}")
            self._publish_and_enforce(sessions, session_states, all_waiting, index, now)
            try:
                self.namer.on_tick(sessions, session_states, now)
            except Exception as e:  # naming is never worth a failed tick
                self.log.warn(f"namer: {type(e).__name__}: {e}")
            self.recorder_store.save({s.id for s in sessions} | self.claude_bg.ids())
        finally:
            # One write for the whole tick, even when a phase raised: what
            # was staged before the failure lands, as it did when each
            # change was written as it was found.
            self._flush_pending_writes()
            self._refund_done_children(newly_done)
        self.housekeeping.maybe_rotate_history(now)
        self.housekeeping.maybe_refresh_model_metadata(now)

    def _flush_pending_writes(self) -> None:
        """Commit this tick's staged mutations to sessions.json in one write.

        Every phase stages what it wants persisted in ``self._pending``
        (current task, state-time accumulators, git context, PR number,
        loaded skills, model, tokens, CPU/RSS, heartbeat and oversight
        stamps, terminal statuses). ``SessionManager.commit_pending``
        rewrites the file once, and only if some staged value differs
        from what is on disk; a tick with nothing staged does not open
        it. The daemon's published state comes from memory, so this only
        moves *when* sessions.json is written within the tick, not what it
        says by the tick's end (audit R5).
        """
        pending = self._pending
        if not pending:
            return
        self._pending = PendingUpdates()
        self.session_manager.commit_pending(pending)

    def _sync_session_ids(self, sessions: list, now: datetime) -> None:
        """Fast session ID detection every 10s (#116).

        Ensures active_agent_session_id updates promptly after /clear.
        """
        if self.cadence.session_ids.due(now):
            for session in self._unsettled(sessions, self._ids_settled):
                self.sync_session_id(session)
            self.cadence.session_ids.mark(now)

    def _unsettled(self, sessions: list, settled: set) -> list:
        """``sessions`` less the finished ones this sync has already read.

        A terminated or done agent's transcript and ids no longer change: it
        is read once more after it finishes (for its last turn), then never.
        Records kept past their end (children awaiting oversight, a long
        grace) would otherwise cost every sync a read each, for ever. One
        that comes back (revive, restart) is read again.
        """
        out = []
        for session in sessions:
            if session.status not in ("terminated", "done"):
                settled.discard(session.id)
                out.append(session)
            elif session.id not in settled:
                settled.add(session.id)
                out.append(session)
        live = {s.id for s in sessions}
        settled.intersection_update(live)
        return out

    def _sync_session_stats(self, sessions: list, now: datetime) -> None:
        """Full transcript stats sync every 60s (heavier I/O).

        Ensures the first loop has accurate data (fixes #103).
        """
        interval = (STATS_SYNC_UNATTENDED_SECONDS if self.state.interval_mode == "unattended"
                    else STATS_SYNC_ATTENDED_SECONDS)
        if self.cadence.stats.due(now, interval):
            for session in self._unsettled(sessions, self._stats_settled):
                self.sync_agent_stats(session)
            self.cadence.stats.mark(now)

    def _sync_available_skills(self, sessions: list, now: datetime) -> None:
        """Scan installed skill directories every 60s (#252)."""
        if self.cadence.skills.due(now):
            from .bundled_skills import get_available_skills
            for session in sessions:
                if session.status in ("terminated", "done"):
                    continue  # its skill list no longer matters
                available = get_available_skills(session.start_directory)
                if available != session.available_skills:
                    self._pending.update_session(session.id, available_skills=available)
            self.cadence.skills.mark(now)

    def _panes_at(self, now: datetime) -> Optional[Dict[str, PaneInfo]]:
        """This tmux session's panes, listed once per ``now`` (one tmux command).

        Every phase of a tick is called with the tick's ``now``, so the 5 s
        process-resources sync and the 15 s sandbox sync share one
        ``list-panes -s`` instead of asking tmux for each agent's pane pid
        (three commands each on a fresh ``RealTmux``, audit R7). A pane's pid
        never changes for the life of its window, and a window that is gone
        is simply absent from the listing. None when the listing failed
        (tmux down, session gone): callers then skip every session, as they
        did when ``get_pane_pid`` returned None. ``session_attached`` is
        kept from the same listing for :meth:`attendance`, and reset to
        None — unknown, which attendance() treats as attended — when the
        listing failed, so tmux going away after a reading of 0 does not
        leave the loop on the unattended interval on a stale count.
        """
        if self._pane_table_at != now:
            self._pane_table_at = now
            self._pane_table = self._tmux.list_panes(self.tmux_session)
            if self._pane_table:
                self.session_attached = next(iter(self._pane_table.values())).session_attached
            else:
                self.session_attached = None
        return self._pane_table

    def _sync_process_resources(self, sessions: list, now: datetime) -> None:
        """Sample CPU and RSS for each agent's claude process tree (every 5 s).

        One batched `ps` per tick (shared with the sandbox sync) feeds
        ``ProcessProbe.sample_resources``; a failed `ps` samples nothing.
        """
        if not self.cadence.resources.due(now):
            return
        table = self.process_probe.table(now)
        if table is not None:
            self.process_probe.sample_resources(
                sessions, table, self._panes_at(now) or {}, self._pending)
        self.cadence.resources.mark(now)

    def _sync_sandbox_state(self, sessions: list, now: datetime) -> None:
        """Detect /sandbox toggle state from claude process listeners (#451)."""
        if not self.cadence.sandbox.due(now):
            return
        table = self.process_probe.table(now)
        if table is not None:
            self.process_probe.sample_sandbox(
                sessions, table, self._panes_at(now) or {}, self._pending)
        self.cadence.sandbox.mark(now)

    def _dispatch_heartbeats(self, sessions: list, now: Optional[datetime] = None) -> None:
        """Send heartbeats before status detection (#171)."""
        self._heartbeat_triggered_sessions = self.check_and_send_heartbeats(sessions, now)
        # Add newly triggered sessions to persistent heartbeat tracking
        self._sessions_running_from_heartbeat.update(self._heartbeat_triggered_sessions)
        # Track pending heartbeat starts for timeline marker
        self._heartbeat_start_pending.update(self._heartbeat_triggered_sessions)

    def _settle_children(self, sessions: list) -> list:
        """Stage done / waiting_oversight for children whose Stop hook fired.

        The rule is ``child_lifecycle.settled_child_status``: a stopped child
        with a report is done, one without is waiting for oversight, window
        or no window. Staged before detection, so ``_detect_and_enrich``
        sees a done child as done and does not mark a stopped child whose
        window is gone terminated (``_is_waiting_on_oversight``). The
        daemon is the one writer of this; the TUI and ``overcode list``
        only read it. Returns the ids of children that became done this
        tick, for ``_refund_done_children`` once the status is written.

        A child that turns done has its report copied onto the session if
        ``overcode report`` could not write it there, so the Result column
        shows it. A stopped child's oversight deadline is staged in
        ``_detect_and_enrich``, on that tick's status.
        """
        from .child_lifecycle import read_report, settled_child_status

        newly_done = []
        for snapshot in sessions:
            if snapshot.parent_session_id is None:
                continue  # no file reads for a top-level agent
            session = self._pending.view(snapshot)
            stopped = self._hook_stopped(session)
            new_status = settled_child_status(session, stopped=stopped)
            if new_status is not None:
                self._pending.update_session_status(session.id, new_status)
                self.log.info(f"[{session.name}] stopped: {new_status}")
                if new_status == "done":
                    newly_done.append(session.id)
                    if not session.report_status:
                        report = read_report(session.tmux_session, session.name, session.id)
                        if report and report.get("status") in ("success", "failure"):
                            self._pending.update_session(
                                session.id, report_status=report["status"],
                                report_reason=report.get("reason") or "")
        return newly_done

    def _stage_oversight_deadline(self, session) -> None:
        """Start a stopped child's oversight timeout, once (policy timeout only).

        The timeout applies whether or not a parent follows the child.
        """
        if (getattr(session, "oversight_policy", "wait") or "wait") != "timeout":
            return
        timeout = getattr(session, "oversight_timeout_seconds", 0.0) or 0.0
        if timeout <= 0 or getattr(session, "oversight_deadline", None):
            return
        if not self._hook_stopped(session):
            return
        from datetime import timedelta
        deadline = self.clock.wall() + timedelta(seconds=timeout)
        self._pending.update_session(session.id, oversight_deadline=stamp(deadline),
                                     oversight_deadline_at=deadline.timestamp())
        self.log.info(f"[{session.name}] oversight timeout in {timeout:.0f}s")

    def _refund_done_children(self, child_ids: list) -> None:
        """Refund the unused budget of children that just became done (#432).

        Runs after the tick's write, so the child's spend is this tick's.
        Once per child: a done child is never settled again, and
        ``reclaim_budget`` caps the child's budget at its spend, so a
        repeated call refunds nothing.
        """
        for child_id in child_ids:
            try:
                refunded = self.session_manager.reclaim_budget(child_id)
            except Exception as e:
                self.log.error(f"budget refund for {child_id} failed: {e}")
                continue
            if refunded and refunded > 0:
                self.log.info(f"Refunded ${refunded:.4f} from child {child_id} to its parent")

    def _detect_and_enrich(
        self, sessions: list, now: datetime, index: Optional[SessionIndex] = None
    ) -> tuple:
        """Detect status and build SessionDaemonState for each session.

        ``index`` is the tick's ``SessionIndex`` (built over the snapshot
        ``sessions`` came from); one is built here when the caller has none.

        Returns:
            (session_states, all_waiting_user) tuple
        """
        now = aware(now)
        session_states = []
        all_waiting_user = True
        if index is None:
            index = self._session_index()
        pending = self._pending
        self._count_children_working(index)
        self._plan_captures(sessions, now)
        # session id -> (first failure, last error): see _detection_failed
        detect_failures = self._detect_failures

        for snapshot in sessions:
            self._beat()  # a long tick over many agents is slow, not stalled
            # Earlier phases of this tick may have staged changes for this
            # session (a heartbeat stamp, tokens, a model, CPU); read through
            # them, as the per-write path re-read the file after each.
            session = pending.view(snapshot)
            pane_content = ""
            if session.status == "done":
                status, activity = STATUS_DONE, "Completed"
            else:
                # Detect status - dispatches per-session via dispatcher (#5)
                try:
                    status, activity, pane_content = self.detector.detect_status(session)
                except Exception as e:
                    # tmux not answering (TmuxTimeoutError) or a detector
                    # bug: this agent is unknown this tick, the others go on
                    unknown = self._detection_failed(session, e, now)
                    if unknown is not None:
                        session_states.append(unknown)
                    all_waiting_user = False
                    continue
                if detect_failures.pop(session.id, None) is not None:
                    self.log.info(f"[{session.name}] status detected again")

                # Log hook events when they change (diagnostic visibility)
                self._log_hook_event(session, status, activity)

                # Track loaded skills from hook events (#252)
                if hasattr(self.detector, 'get_loaded_skills'):
                    new_skills = self.detector.get_loaded_skills(session.name)
                    if new_skills and sorted(new_skills) != sorted(session.loaded_skills):
                        pending.update_session(session.id, loaded_skills=new_skills)

                # Extract PR number from pane content, unless the
                # transcript names the PR (#489)
                if pane_content and session.id not in self.stats_sync.transcript_prs:
                    pr = extract_pr_number(pane_content)
                    if pr is not None and pr != session.pr_number:
                        pending.update_session(session.id, pr_number=pr, pr_branch=session.branch)

            # Clear heartbeat tracking when session stops running
            if status != STATUS_RUNNING and session.id in self._sessions_running_from_heartbeat:
                self._sessions_running_from_heartbeat.discard(session.id)
                self._heartbeat_start_pending.discard(session.id)

            # Refresh git context (branch may have changed)
            git_changed = self._refresh_git_context(session)
            if git_changed and session.pr_number is not None:
                # The session as the file will show it: staged branch included
                refreshed = pending.view(snapshot)
                if refreshed.branch is not None:
                    # Clear if pr_branch not set (pre-migration) or branch mismatch
                    if refreshed.pr_branch is None or refreshed.branch != refreshed.pr_branch:
                        pending.update_session(session.id, pr_number=None, pr_branch=None)

            # Update current task in session
            pending.update_stats(
                session.id,
                current_task=activity[:100] if activity else ""
            )

            # The session with everything staged so far applied — what the
            # reload used to return after the writes above.
            session = pending.view(snapshot)

            # Track stats and build state
            # Precedence: terminated > asleep > heartbeat variants > default (#399, #68, #171)
            if status == STATUS_TERMINATED:
                effective_status = STATUS_TERMINATED
            elif session.is_asleep:
                effective_status = STATUS_ASLEEP
            elif status == STATUS_RUNNING and session.id in self._sessions_running_from_heartbeat:
                if session.id in self._heartbeat_start_pending:
                    effective_status = STATUS_HEARTBEAT_START
                    self._heartbeat_start_pending.discard(session.id)
                else:
                    effective_status = STATUS_RUNNING_HEARTBEAT
            # A child at rest (or done) stays at rest: a heartbeat is
            # scheduled work, which never lifts a child (rule_armed)
            elif (status not in (STATUS_RUNNING, STATUS_TERMINATED, STATUS_ASLEEP, STATUS_UNKNOWN)
                  and status not in AT_REST_STATUSES
                  and session.heartbeat_enabled
                  and not session.heartbeat_paused
                  and session.heartbeat_instruction):
                effective_status = STATUS_WAITING_HEARTBEAT
            else:
                effective_status = status

            # Persist terminated status when window is truly gone.
            # Only persist when pane_content is empty (window gone), not when
            # a shell prompt is briefly visible (e.g. during agent revival).
            if (effective_status == STATUS_TERMINATED
                    and session.status != "terminated"
                    and not pane_content):
                if not self._is_waiting_on_oversight(session):
                    pending.update_session_status(session.id, "terminated")
            # Un-persist terminated if agent is found alive (revival or false positive)
            elif (session.status == "terminated"
                    and effective_status != STATUS_TERMINATED):
                pending.update_session_status(session.id, "running")

            # A child waiting for oversight is timed only while it is not
            # working. Re-prompted, or busy with its own background work, it
            # has no deadline (it gets one when it rests). Decided on this
            # tick's status, so staging and clearing never alternate.
            if session.status == STATUS_WAITING_OVERSIGHT:
                if is_green_status(effective_status):
                    if session.oversight_deadline:
                        pending.update_session(session.id, oversight_deadline=None,
                                               oversight_deadline_at=None)
                else:
                    self._stage_oversight_deadline(session)
                session = pending.view(snapshot)

            session_state = self.track_session_stats(session, effective_status, index, now)
            session_state.current_activity = activity
            for name, value in self._pane_view(session, pane_content).items():
                setattr(session_state, name, value)
            self._attach_git_and_burn(session, session_state)
            self.recorder_store.record(session, session_state, effective_status, now,
                                       detector=self.detector, engine=self._engine)
            session_states.append(session_state)

            # Log status history to session-specific file — on change, on
            # keepalive, and on first sight; a row stands until the next.
            # A finished agent gets its one row and no keepalives: it does
            # not change, and records kept past their end would otherwise
            # write a row each a minute for ever.
            if status_row_due(
                self._last_logged.get(session.id),
                self._last_keepalive.get(session.id),
                effective_status,
                activity,
                now,
                (float("inf") if effective_status in (STATUS_TERMINATED, STATUS_DONE)
                 else STATUS_HISTORY_KEEPALIVE_SECONDS),
            ):
                try:
                    log_agent_status(
                        session.name, effective_status, activity,
                        history_file=self.history_path,
                        session_id=session.id,
                        hostname=self._hostname,
                        now=now,
                    )
                except OSError as e:
                    # History is a record, not the status: a full disk must
                    # not fail the tick that publishes every agent's status
                    self.log.warn(f"[{session.name}] status history not written: {e}")
                self._last_logged[session.id] = (
                    effective_status, activity[:100] if activity else ""
                )
                self._last_keepalive[session.id] = now
            self._record_swarm_history(session, now)

            # Track if any session is not waiting for user (one at rest,
            # or not known yet, is quiet too)
            if status not in _QUIET_STATUSES:
                all_waiting_user = False

        # Compute subtree costs and child rollups for parent agents
        self._compute_subtree_costs(session_states, index)
        self._compute_child_rollups(session_states, index)

        return session_states, all_waiting_user

    def _record_swarm_history(self, session, now: datetime) -> None:
        """Append the parent's delegated-work counts to swarm_history.csv if they moved.

        From the hook state the detector just read (the 👥 rollup's), so
        only a hook event, a keepalive or the record going away writes.
        """
        rollup = getattr(self.detector, "get_swarm_rollup", None)
        rollup = rollup(session.name) if callable(rollup) else None
        try:
            self.swarm_history.record(session.id, session.name, self._swarm_state(session.name),
                                      rollup if isinstance(rollup, dict) else None,
                                      now.timestamp())
        except OSError as e:
            self.log.warn(f"[{session.name}] swarm history not written: {e}")

    def _detection_failed(
        self, session, error: Exception, now: datetime
    ) -> Optional[SessionDaemonState]:
        """This tick could not detect ``session``'s status: what to publish for it.

        A short failure (one tmux read that timed out) keeps the agent's
        last published state; one tick's hiccup is not news. Failing for
        longer than _unobserved_gap_seconds, the agent is published as
        ``unknown`` with no colour, rather than its last colour held for as
        long as tmux stays wedged. Nothing is recorded for it meanwhile (no
        time, no episode, no history row), so when detection is back the
        time totals and the recorder see a gap and attribute it to nothing.
        Logged when the error starts or changes, with the traceback unless
        it is a tmux timeout.
        """
        import dataclasses

        from .protocols import TmuxTimeoutError

        failures = self._detect_failures
        message = f"{type(error).__name__}: {error}"
        first, last_message = failures.get(session.id, (now, None))
        failures[session.id] = (first, message)
        if message != last_message:
            if isinstance(error, TmuxTimeoutError):
                self.log.warn(f"[{session.name}] status not detected: {message}")
            else:
                self.log.error(f"[{session.name}] status detection failed: {message}\n"
                               f"{traceback.format_exc()}")
        previous = next(
            (s for s in self.state.sessions if s.session_id == session.id), None
        )
        if previous is not None and elapsed_seconds(first, now) <= self._unobserved_gap_seconds():
            return previous
        unknown = dict(
            current_status=STATUS_UNKNOWN,
            current_activity=f"Status unknown: {message}"[:100],
            status_since=stamp(first),
            live_colour=None, live_since=None, episode_colour=None, episode_start=None,
            status_detail=None,
        )
        if previous is None:
            return SessionDaemonState(
                session_id=session.id, name=session.name, tmux_window=session.tmux_window,
                billing_code=billing_name_of(session), **unknown,
            )
        return dataclasses.replace(previous, **unknown)

    def _plan_captures(self, sessions: list, now: datetime) -> None:
        """Decide which panes this loop captures; the rest come from the gate's cache.

        The tick's one ``list-panes -s`` (``_panes_at``) carries every
        window's change signature. A pane is captured when that signature
        moved since its last capture, when its hook_state file did (so a
        status transition in hooks mode is always paired with fresh pane
        text), for one follow-up loop after a change (the polling
        detector's running-to-waiting step is "content unchanged since last
        time"), when it was never captured, or when its last capture is
        older than the keepalive — the bound on the listing's same-second
        blind spot (pane_capture_gate). Without a listing (tmux down, session
        gone) nothing is planned and every read is a raw capture: the loop
        as it was before gating. Idle agents therefore cost no capture-pane
        at all, and a changed pane is captured on the very next loop.
        """
        gate = self._capture_gate
        gate.begin_loop()
        panes = self._panes_at(now)
        if panes is None:
            return
        tracker = self._pane_tracker
        clock = self.clock.monotonic()
        for session in sessions:
            if session.status == "done":
                continue
            pane = pane_for_window(panes, session.tmux_window)
            signature = pane.signature if pane is not None else None
            capture = tracker.due(
                session.id, signature, self._hook_state_stamp(session), clock
            )
            gate.plan(session.tmux_window, capture)

    def _hook_state_stamp(self, session) -> Optional[tuple]:
        """(mtime_ns, size) of the session's hook_state file; None without one.

        The file the hook detector reads for status: any hook event rewrites
        it, so its stat moving is the signal that the pane must be re-read
        for the event's enrichment even if the listing saw no pane change.
        """
        path_of = getattr(getattr(self.detector, "hooks", None), "_hook_state_path", None)
        if path_of is None:
            return None
        self._note_agent(session)
        try:
            st = os.stat(path_of(session.name))
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def _refresh_git_context(self, session) -> bool:
        """Stage a changed repo/branch for ``session``; True if it changed.

        What ``SessionManager.refresh_git_context`` did per session, minus
        its rewrite of sessions.json: the change joins the tick's single
        write. ``session`` is the tick's view (staged changes applied).
        """
        if not session.start_directory:
            return False
        repo_name, branch = self.session_manager.read_git_context(session)
        if repo_name != session.repo_name or branch != session.branch:
            self._pending.update_session(session.id, repo_name=repo_name, branch=branch)
            return True
        return False

    def _is_waiting_on_oversight(self, session) -> bool:
        """A child parked in waiting_oversight whose Stop hook has fired.

        Its window is gone, so detection says terminated — but
        ``_settle_children`` keeps exactly this case (parent set, Stop hook,
        no report) waiting_oversight, and the two would rewrite
        sessions.json in turn every tick for as long as the child sat there
        (audit R5). The persisted status stays waiting_oversight; the
        published status is terminated either way, and the oversight
        timeout keeps applying to it.
        """
        if session.status != STATUS_WAITING_OVERSIGHT or session.parent_session_id is None:
            return False
        # The same read _settle_children decided with, so the two agree
        return self._hook_stopped(session)

    def _hook_stopped(self, session) -> bool:
        """Whether ``session``'s last hook event is Stop, read as detection reads it."""
        read = getattr(getattr(self.detector, "hooks", None), "_read_hook_state", None)
        if read is None:
            from .child_lifecycle import hook_stopped
            return hook_stopped(session.tmux_session, session.name, session.id)
        self._note_agent(session)
        try:
            hook_state = read(session.name)
        except Exception:
            return False
        return isinstance(hook_state, dict) and hook_state.get("event") == "Stop"

    def _note_agent(self, session) -> None:
        """Tell the hook detector whose files ``session``'s are (agents/<id>/, item 35)."""
        note = getattr(getattr(self.detector, "hooks", None), "note_agent", None)
        if note is not None:
            note(session.name, session.id)

    def _log_hook_event(self, session, status: str, activity: str) -> None:
        """Log hook events to the daemon log when they change.

        Reads the detector's _last_detect_phase diagnostic and logs
        when a new hook event fires for an agent, showing the agent name,
        hook event, and resulting status.
        """
        # Get the phase from whichever detector is active
        detector = self.detector.hooks if self.detector.mode == "hooks" else self.detector.polling
        phases = getattr(detector, '_last_detect_phase', {})
        current_phase = phases.get(session.id, "")

        prev_phase = self._last_hook_phases.get(session.id)
        if current_phase and current_phase != prev_phase:
            self._last_hook_phases[session.id] = current_phase
            # Format: "agent-name  hook:PostToolUse → running (Using Read)"
            self.log.info(
                f"{session.name}  {current_phase} → {status} ({activity})"
            )

    def _compute_subtree_costs(self, session_states, index: Optional[SessionIndex] = None):
        """Compute subtree cost (self + all descendants) for each parent agent.

        And its trip reading (``trip.subtree_trip_cost``): the members' $ as
        their rows show it, while any of them has a trip.

        Agents are linked by id: each record's ``parent_session_id`` in
        ``index``, so a rename between ticks moves no cost. Without an index
        the published ``parent_name`` links them. Parent links can still
        form a cycle (a corrupt record, duplicate names without an index):
        each walk carries a ``visited`` set so a cycle counts every member
        once instead of recursing until the daemon dies with RecursionError.
        """
        from .trip import subtree_trip_cost

        by_id = {s.session_id: s for s in session_states}
        if index is not None:
            parent_of = {sid: getattr(index.by_id.get(sid), "parent_session_id", None)
                         for sid in by_id}
        else:
            id_by_name = {s.name: s.session_id for s in session_states}
            parent_of = {s.session_id: id_by_name.get(s.parent_name)
                         for s in session_states if s.parent_name}
        children_map = {}
        for sid, parent_id in parent_of.items():
            if parent_id and parent_id in by_id:
                children_map.setdefault(parent_id, []).append(sid)

        def _sum(sid, visited):
            visited.add(sid)
            total = by_id[sid].estimated_cost_usd
            for child in children_map.get(sid, []):
                if child not in visited:
                    total += _sum(child, visited)
            return total

        def _members(sid, visited):
            visited.add(sid)
            yield by_id[sid]
            for child in children_map.get(sid, []):
                if child not in visited:
                    yield from _members(child, visited)

        for s in session_states:
            if children_map.get(s.session_id):
                s.subtree_cost_usd = _sum(s.session_id, set())
                s.subtree_trip_cost_usd = subtree_trip_cost(_members(s.session_id, set()))

    def _count_children_working(self, index: SessionIndex) -> None:
        """Refill the children-working counts from the last published tick.

        In place: the hook detectors hold this dict. A child counts while
        its published status is not at rest, gone, asleep or unknown.
        """
        counts = self._children_working
        counts.clear()
        for state in self.state.sessions:
            if state.current_status in CHILD_NOT_WORKING:
                continue
            child = index.by_id.get(state.session_id)
            parent_id = getattr(child, "parent_session_id", None)
            if parent_id:
                counts[parent_id] = counts.get(parent_id, 0) + 1

    def _compute_child_rollups(self, session_states, index: SessionIndex) -> None:
        """Each parent's children by state, for the 👶 column (children_rollup).

        Counted from this tick's published children (child_lifecycle's
        child_rollup). Parents whose children are in another tmux session
        count only the ones this daemon publishes.
        """
        children: Dict[str, list] = {}
        for state in session_states:
            child = index.by_id.get(state.session_id)
            parent_id = getattr(child, "parent_session_id", None)
            if parent_id:
                children.setdefault(parent_id, []).append(
                    (state.current_status, state.task_result))
        for state in session_states:
            state.children_rollup = child_rollup(children.get(state.session_id, ()))

    def _cleanup_stale(self, sessions: list) -> None:
        """Remove stale tracking entries for deleted sessions."""
        current_session_ids = {s.id for s in sessions}
        stale_ids = set(self.operation_start_times.keys()) - current_session_ids
        for stale_id in stale_ids:
            del self.operation_start_times[stale_id]
        stale_ids = set(self.previous_states.keys()) - current_session_ids
        for stale_id in stale_ids:
            del self.previous_states[stale_id]
        for stale_id in set(self._last_logged) - current_session_ids:
            del self._last_logged[stale_id]
            self._last_keepalive.pop(stale_id, None)
        try:
            self.swarm_history.forget(current_session_ids, self.clock.time())
        except OSError as e:
            self.log.warn(f"swarm history not written: {e}")
        for per_session in (self._time_bases, self.stats_sync.stats_views, self._pane_views,
                            self.last_state_times, self._last_hook_phases, self._detect_failures):
            for stale_id in set(per_session) - current_session_ids:
                del per_session[stale_id]
        self._pane_tracker.forget(current_session_ids)
        self._capture_gate.forget({s.tmux_window for s in sessions})

    def _publish_and_enforce(
        self,
        sessions: list,
        session_states: list,
        all_waiting_user: bool,
        index: Optional[SessionIndex] = None,
        now: Optional[datetime] = None,
    ) -> None:
        """Publish state, enforce policies, and log summary.

        ``index`` is the tick's ``SessionIndex`` over the whole session
        table (every tmux session); the terminated-session archive pass
        walks it. Without one, that pass reads the manager's snapshot.
        ``now`` is the tick's clock (the housekeeping cadence runs on it).
        """
        now = self.clock.wall() if now is None else aware(now)
        # Interval for the sleep that follows this tick, published with the
        # state so consumers size their staleness window to it.
        mode = self.attendance()
        interval = self.calculate_interval(sessions, all_waiting_user, mode == "unattended")
        if mode != self.state.interval_mode:
            self.log.info(f"Loop interval: {mode} ({interval}s)")
        self.state.interval_mode = mode
        self.state.current_interval = interval

        # Update status based on state
        if not sessions:
            self.state.status = "no_agents"
        elif all_waiting_user:
            self.state.status = "idle"
        else:
            self.state.status = "active"

        # Publish state
        self._publish_state(session_states)

        # Enforce oversight timeouts every loop
        self._enforce_oversight_timeouts(sessions, now)

        # Auto-archive "done" agents after their archive delay (#244; 1 h by default)
        # Count untracked tmux windows every 2 minutes (#344)
        # Move terminated sessions to the archive once past their grace
        housekeeping = self.housekeeping
        published_terminated = {st.session_id for st in session_states
                                if st.current_status == STATUS_TERMINATED}
        if housekeeping.due(now):
            housekeeping.auto_archive_done_agents(sessions, self._pending, now)
            self.state.untracked_window_count = housekeeping.count_untracked_windows(sessions)
            all_sessions = (
                index.by_id.values() if index is not None else self.session_manager.list_sessions()
            )
            window_gone = frozenset(
                s.id for s in sessions
                if s.status == STATUS_WAITING_OVERSIGHT and s.id in published_terminated
            )
            housekeeping.archive_terminated_sessions(all_sessions, now, self._pending,
                                                     window_gone=window_gone)
            housekeeping.prune_finished_jobs()
            housekeeping.tidy_storage(self.session_manager)
            housekeeping.migrate_storage(self.session_manager)
            housekeeping.restate_archived_spend(self.session_manager)
            ledger = getattr(self.session_manager, "ledger", None)
            views = housekeeping.fill_billing_codes(
                [self._pending.view(s) for s in sessions], self.session_manager)
            if ledger is not None:
                housekeeping.checkpoint_spend(views, ledger, now)
                self._sync_claude_bg_ledger(force=True)

        # Log summary
        green = sum(1 for s in session_states if s.current_status == STATUS_RUNNING)
        non_green = len(session_states) - green
        self.log.info(f"Loop #{self.state.loop_count}: {len(sessions)} sessions ({green} green, {non_green} non-green), interval={interval}s")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self, check_interval: int = INTERVAL_FAST):
        """Main daemon loop."""
        # Atomically check if already running and acquire lock
        # This prevents TOCTOU race conditions that could cause multiple daemons
        acquired, existing_pid = acquire_daemon_lock(self.pid_path)
        if not acquired:
            if existing_pid:
                self.log.error(f"Monitor daemon already running (PID {existing_pid})")
            else:
                self.log.error("Could not acquire daemon lock (another daemon may be starting)")
            sys.exit(1)

        self.log.section("Monitor Daemon")
        self.log.info(f"PID: {os.getpid()}")
        self.log.info(f"tmux session: {self.tmux_session}")
        self.log.info(f"Presence tracking: available (macOS Quartz: {'yes' if MACOS_APIS_AVAILABLE else 'no'})")

        # Setup signal handlers
        def handle_shutdown(signum, frame):
            self.log.info("Shutdown signal received")
            self._shutdown = True

        signal.signal(signal.SIGTERM, handle_shutdown)
        signal.signal(signal.SIGINT, handle_shutdown)
        # The watchdog's wake-up: a handler (even a no-op) makes the signal
        # interrupt a stuck sleep, which then sees its deadline has passed
        signal.signal(signal.SIGUSR1, lambda signum, frame: None)

        self.state.status = "active"
        self.state.current_interval = check_interval
        # Guarded: views need only engine.sock, so an unwritable state
        # file (full disk) must not stop the engine coming up
        self._save_state_file()
        self.recorder_store.load()
        self._start_engine_socket()
        # Agent directories a crash or an older overcode left (bounded)
        self.housekeeping.tidy_storage(self.session_manager, force=True)
        self.housekeeping.migrate_storage(self.session_manager)

        try:
            while not self._shutdown:
                self.state.loop_count += 1
                self._beat()
                self._run_tick(self.clock.wall())
                self.wake.sleep(self.state.current_interval)
        except Exception as e:
            self.log.error(f"Monitor daemon error: {e}\n{traceback.format_exc()}")
            raise
        finally:
            self.log.info("Monitor daemon shutting down")
            # What quick ticks staged since the last full tick
            try:
                self._flush_pending_writes()
            except Exception as e:
                self.log.warn(f"sessions.json not written at shutdown: {e}")
            # What the last quick tick recorded (a closed episode, a bell)
            # must not be replayed by the next daemon
            try:
                self.recorder_store.save(force=True)
            except Exception as e:
                self.log.warn(f"engine state not saved at shutdown: {e}")
            # Tokens counted but not yet in the quota token stream
            if self.quota is not None:
                try:
                    self.quota.flush(self.clock.wall().timestamp(), force=True)
                except Exception as e:
                    self.log.warn(f"quota token stream not flushed at shutdown: {e}")
            self.presence.stop()
            self.namer.shutdown()
            if self._engine is not None:
                self._engine.stop()
            self.state.status = "stopped"
            self._save_state_file()
            remove_pid_file(self.pid_path)


def main() -> int:
    """CLI entrypoint for monitor daemon."""
    import argparse

    from .paths import mark_entry_point

    mark_entry_point()

    parser = argparse.ArgumentParser(description="Overcode Monitor Daemon")
    parser.add_argument(
        "--session", "-s",
        default="agents",
        help="tmux session name (default: agents)"
    )
    parser.add_argument(
        "--interval", "-i",
        type=int,
        default=INTERVAL_FAST,
        help=f"Check interval in seconds (default: {INTERVAL_FAST})"
    )

    args = parser.parse_args()

    # tmux many times a second: start it with posix_spawn (#486)
    from . import spawn
    spawn.install()
    # Every tmux client in this process (the detectors build their own)
    # waits a bounded time for an answer; a wedged server must not hang a tick
    from .implementations import TMUX_COMMAND_TIMEOUT_SECONDS, RealTmux
    RealTmux.default_command_timeout = TMUX_COMMAND_TIMEOUT_SECONDS

    daemon = MonitorDaemon(tmux_session=args.session)
    daemon.run(check_interval=args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
