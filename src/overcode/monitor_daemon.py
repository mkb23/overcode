#!/usr/bin/env python3
"""
Monitor Daemon - Single source of truth for all session metrics.

This daemon handles all monitoring responsibilities:
- Agent status detection (via StatusDetector)
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
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from .daemon_logging import BaseDaemonLogger
from .daemon_utils import create_daemon_helpers
from .backends import (
    BackendCapability,
    capability_names,
    get_backend,
    session_backend_name,
    session_capabilities,
    session_supports,
)
from .claude_pid import is_session_id_owned_by_others
from .stats_reader import AgentSessionStats, stats_reader_for_session
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
    get_activity_signal_path,
    get_supervisor_stats_path,
    get_tui_heartbeat_path,
    tui_attended_age_seconds,
    TUI_ATTENDED_TOUCH_SECONDS,
)
from .config import get_monitor_daemon_config, get_relay_config
from .status_constants import (
    STATUS_ASLEEP,
    STATUS_DONE,
    STATUS_HEARTBEAT_START,
    STATUS_RUNNING,
    STATUS_RUNNING_HEARTBEAT,
    STATUS_TERMINATED,
    STATUS_WAITING_HEARTBEAT,
    STATUS_WAITING_OVERSIGHT,
    is_green_status,
)
from .pane_capture_gate import PaneCaptureGate, PaneChangeTracker
from .status_detector import StatusDetector
from .status_patterns import extract_pr_number
from .status_detector_factory import StatusDetectorDispatcher
from .status_history import STATUS_HISTORY_KEEPALIVE_SECONDS, log_agent_status, status_row_due
from .monitor_daemon_core import (
    calculate_time_accumulation,
    calculate_cost_estimate,
    calculate_total_tokens,
    calculate_median,
    should_sync_stats,
    parse_datetime_safe,
    is_heartbeat_eligible,
    is_heartbeat_due,
    should_archive_terminated,
    should_auto_archive,
    should_enforce_oversight_timeout,
)
from .tmux_utils import (
    PaneInfo,
    pane_for_window,
    send_text_to_tmux_window,
    untracked_window_names,
)

if TYPE_CHECKING:
    from .protocols import TmuxInterface


# Check for macOS presence APIs (optional)
try:
    from .presence_logger import (
        MACOS_APIS_AVAILABLE,
        get_current_presence_state,
        PresenceLogger,
        PresenceLoggerConfig,
    )
except ImportError:
    MACOS_APIS_AVAILABLE = False
    get_current_presence_state = None
    PresenceLogger = None
    PresenceLoggerConfig = None


# Interval settings (in seconds)
INTERVAL_FAST = DAEMON.interval_fast    # When active or agents working
INTERVAL_SLOW = DAEMON.interval_slow    # When all agents need user input
INTERVAL_IDLE = DAEMON.interval_idle    # When no agents at all
INTERVAL_UNATTENDED = DAEMON.interval_unattended  # Nobody watching (see attendance)

# A TUI touches its attended file every TUI_ATTENDED_TOUCH_SECONDS while a
# client is attached to it; three missed touches and it is not there.
TUI_ATTENDED_FRESHNESS = 3 * TUI_ATTENDED_TOUCH_SECONDS

# The every-60-loops housekeeping (done-agent auto-archive, untracked window
# count, terminated-session archive) was 120 s of wall clock at the 2 s
# loop; it is a wall-clock cadence now so the unattended loop keeps it.
HOUSEKEEPING_INTERVAL_SECONDS = 60 * DAEMON.interval_fast


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


def check_activity_signal(session: str = None) -> bool:
    """Check for and consume the activity signal from TUI.

    Args:
        session: tmux session name (default: from config)
    """
    if session is None:
        session = DAEMON.default_tmux_session
    signal_path = get_activity_signal_path(session)
    # Atomic: just try to unlink, don't check exists() first (TOCTOU race)
    try:
        signal_path.unlink()
        return True
    except FileNotFoundError:
        # Signal doesn't exist - that's fine
        return False
    except OSError:
        # Other error (permissions, etc) - signal may exist but can't consume
        return False


def _create_monitor_logger(session: str = "agents", log_file: Optional[Path] = None) -> BaseDaemonLogger:
    """Create a logger for the monitor daemon."""
    if log_file is None:
        session_dir = ensure_session_dir(session)
        log_file = session_dir / "monitor_daemon.log"
    return BaseDaemonLogger(log_file)


class PresenceComponent:
    """Presence tracking (works on all platforms; richer on macOS with Quartz)."""

    # TUI heartbeat is considered fresh if within this many seconds
    TUI_HEARTBEAT_FRESHNESS = 60

    def __init__(self, tmux_session: str = "agents"):
        self.available = True
        self._logger: Optional[PresenceLogger] = None
        self._tmux_session = tmux_session
        self._last_publish_time: Optional[datetime] = None

        if PresenceLogger is not None:
            heartbeat_path = str(get_tui_heartbeat_path(tmux_session))
            config = PresenceLoggerConfig(
                tui_heartbeat_path=heartbeat_path,
                tui_heartbeat_freshness=self.TUI_HEARTBEAT_FRESHNESS,
            )
            self._logger = PresenceLogger(config)
            self._logger.start()

    def _is_tui_active(self) -> bool:
        """Check if TUI heartbeat file has a recent timestamp."""
        try:
            heartbeat_path = get_tui_heartbeat_path(self._tmux_session)
            if not heartbeat_path.exists():
                return False
            ts_str = heartbeat_path.read_text().strip()
            ts = datetime.fromisoformat(ts_str)
            age = (datetime.now() - ts).total_seconds()
            return age <= self.TUI_HEARTBEAT_FRESHNESS
        except (ValueError, OSError):
            return False

    def _detect_sleep(self) -> bool:
        """Detect if the machine likely slept since last publish.

        Returns True if the gap since last publish exceeds 2x the daemon interval.
        """
        now = datetime.now()
        if self._last_publish_time is None:
            self._last_publish_time = now
            return False
        gap = (now - self._last_publish_time).total_seconds()
        slept = gap > 20  # Machine sleep produces gaps of 30s+; absolute threshold avoids false positives
        self._last_publish_time = now
        return slept

    def get_current_state(self) -> tuple:
        """Get current presence state.

        Returns:
            Tuple of (state, idle_seconds, locked) or (None, None, None) if unavailable.
            On non-macOS, idle is always 0 and locked is always False, but sleep
            detection and TUI heartbeat still produce meaningful state values.
        """
        try:
            from .presence_logger import classify_state, DEFAULT_IDLE_THRESHOLD

            tui_active = self._is_tui_active()
            slept = self._detect_sleep()

            if slept:
                # Machine just woke — override to asleep state for this sample
                state = classify_state(
                    locked=False,
                    idle_seconds=0.0,
                    slept=True,
                    idle_threshold=DEFAULT_IDLE_THRESHOLD,
                    tui_active=False,
                )
                return state, 0.0, False

            if MACOS_APIS_AVAILABLE and get_current_presence_state is not None:
                return get_current_presence_state(tui_active=tui_active)

            # Non-macOS: idle=0, locked=False; TUI heartbeat still works
            state = classify_state(
                locked=False,
                idle_seconds=0.0,
                slept=False,
                idle_threshold=DEFAULT_IDLE_THRESHOLD,
                tui_active=tui_active,
            )
            return state, 0.0, False
        except Exception:
            return None, None, None

    def stop(self):
        """Stop the presence logger if running."""
        if self._logger is not None:
            self._logger.stop()


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
        status_detector: Optional[StatusDetector] = None,
        tmux: Optional["TmuxInterface"] = None,
    ):
        self.tmux_session = tmux_session

        # Ensure session directory exists
        ensure_session_dir(tmux_session)

        # One tmux client for the daemon's lifetime (audit R7). The periodic
        # syncs used to build a fresh RealTmux each, so its 30 s cache never
        # hit and every pane-pid lookup was list-sessions + list-windows +
        # list-panes. Injected by tests and the bench.
        if tmux is None:
            from .implementations import RealTmux

            tmux = RealTmux()
        self._tmux = tmux

        # Session-specific paths
        self.pid_path = get_monitor_daemon_pid_path(tmux_session)
        self.state_path = get_monitor_daemon_state_path(tmux_session)
        self.history_path = get_agent_history_path(tmux_session)

        # Dependencies (allow injection for testing)
        self.session_manager = session_manager or SessionManager()
        from .settings import resolve_detection_mode
        detection_mode = resolve_detection_mode(tmux_session)
        # Capture gating (audit R11): each loop plans, from the tick's pane
        # listing, which panes changed and captures only those; the
        # detectors read the rest from the gate's cache. See _plan_captures.
        self._capture_gate = PaneCaptureGate()
        self._pane_tracker = PaneChangeTracker()
        self._loop_clock = time.monotonic  # the keepalive's clock; tests freeze it
        self.detector = StatusDetectorDispatcher(
            tmux_session,
            polling_detector=status_detector,
            mode=detection_mode,
            capture_gate=self._capture_gate,
        )

        # Hostname for history disambiguation
        from .config import get_hostname
        self._hostname = get_hostname()

        # Presence tracking (graceful degradation)
        self.presence = PresenceComponent(tmux_session=tmux_session)

        # Logging - session-specific log file
        self.log = _create_monitor_logger(session=tmux_session)

        # State tracking
        self.state = MonitorDaemonState(
            pid=os.getpid(),
            status="starting",
            started_at=datetime.now().isoformat(),
            daemon_version=DAEMON_VERSION,
        )

        # Wall time of the previous complete tick, published so consumers can
        # size their staleness window (MonitorDaemonState.is_stale).
        self._last_tick_duration_seconds: float = 0.0

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

        # Everything a tick wants persisted to sessions.json is staged here
        # and written once at the end of the tick (audit R5); see
        # _flush_pending_writes. Reads inside the tick go through
        # self._pending.view(session) so they see the staged values.
        self._pending: PendingUpdates = PendingUpdates()

        # When each terminated entry in sessions.json was first seen so by
        # this daemon (any tmux session); after the configured grace the
        # every-60-loops housekeeping moves it to the archive.
        self._terminated_since: Dict[str, datetime] = {}
        self._last_hook_phases: Dict[str, str] = {}  # session_id → last logged phase
        self._last_commands: Dict[str, str] = {}  # session_id → last user prompt

        # Stats sync throttling - None forces immediate sync on first loop
        self._last_stats_sync: Optional[datetime] = None
        self._stats_sync_interval = 60  # seconds

        # Session ID detection runs more frequently than full stats (#116)
        self._last_session_id_sync: Optional[datetime] = None
        self._session_id_sync_interval = 10  # seconds

        # Available skills sync (#252) — infrequent, skills rarely change
        self._last_skills_sync: Optional[datetime] = None
        self._skills_sync_interval = 60  # seconds

        # Sandbox state sync (#451) — detect /sandbox toggle via claude PID listeners
        self._last_sandbox_sync: Optional[datetime] = None
        self._sandbox_sync_interval = 15  # seconds

        # Per-agent CPU / RSS sampling (from `ps`, summed over the agent process tree).
        # Slow enough to avoid adding load, fast enough to flag runaway agents
        # that are dominating the machine.
        self._last_resources_sync: Optional[datetime] = None
        self._resources_sync_interval = 5  # seconds

        # agent_status_history.csv rotation/retention check (#465, #468) —
        # a cheap stat()-then-maybe-rewrite, but still only worth doing hourly.
        self._last_history_rotation_check: Optional[datetime] = None
        self._history_rotation_check_interval = 3600  # seconds

        # models.dev catalog auto-refresh (#473) — opt-in via config.yaml
        # model_metadata.auto_refresh; the hourly check itself is a stat().
        # The fetch runs on a background thread so a packet-dropping network
        # can never stall the loop, and a failure backs off for hours.
        self._last_model_metadata_check: Optional[datetime] = None
        self._model_metadata_check_interval = 3600  # seconds
        self._model_metadata_thread: Optional[threading.Thread] = None
        self._model_metadata_backoff_until: Optional[datetime] = None
        self._model_metadata_failure_backoff = 6 * 3600  # seconds

        # Loop interval while nobody is watching (config.yaml
        # monitor_daemon.interval_unattended_seconds; read once, like relay).
        self._interval_unattended: int = get_monitor_daemon_config()["interval_unattended"]

        # Housekeeping is wall-clock: first pass HOUSEKEEPING_INTERVAL_SECONDS
        # after the first tick (loop 60 at 2 s used to be 2 minutes in), then
        # every interval, whatever the loop length.
        self._last_housekeeping: Optional[datetime] = None

        # Relay configuration (for pushing state to cloud)
        self._relay_config = get_relay_config()
        self._last_relay_push = datetime.min
        if self._relay_config:
            self.log.info(f"Relay enabled: {self._relay_config['url']}")

        # Shutdown flag
        self._shutdown = False

        # Heartbeat tracking (#171)
        self._heartbeat_triggered_sessions: set = set()  # Session IDs that received heartbeat this loop
        self._sessions_running_from_heartbeat: set = set()  # Persistent: sessions currently running due to heartbeat
        self._heartbeat_start_pending: set = set()  # One-shot: sessions awaiting first "running" observation after heartbeat

        # Legacy migration flag — runs once on first tick
        self._legacy_windows_migrated = False

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
            self.log.warning(f"Legacy window migration failed: {e}")

    def _session_index(self) -> SessionIndex:
        """A ``SessionIndex`` over the manager's current snapshot (one stat)."""
        return SessionIndex(self.session_manager.sessions_by_id())

    def _get_parent_name(self, session, index: Optional[SessionIndex] = None) -> Optional[str]:
        """Get the name of a session's parent, if any (#244)."""
        return (index or self._session_index()).parent_name(session)

    def track_session_stats(
        self, session, status: str, index: Optional[SessionIndex] = None
    ) -> SessionDaemonState:
        """Track session state and build SessionDaemonState.

        Returns the session state for inclusion in MonitorDaemonState.

        ``index`` is the tick's ``SessionIndex``; the hierarchy fields
        (parent name, depth, children count) are looked up in it instead of
        being recomputed from the session table per session. Callers
        without one get an index over the current snapshot.
        """
        session_id = session.id
        now = datetime.now()
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
                op_duration = (now - start_time).total_seconds()
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
                        last_activity=now.isoformat()
                    )
                    self.log.info(f"[{session.name}] Operation completed ({op_duration:.1f}s)")

        # Update previous state
        self.previous_states[session_id] = status

        # Build session state for publishing
        stats = session.stats

        # Calculate next heartbeat due time (#171)
        next_heartbeat_due = None
        if session.heartbeat_enabled and not session.heartbeat_paused:
            last_hb = parse_datetime_safe(session.last_heartbeat_time)
            if last_hb is None:
                last_hb = parse_datetime_safe(session.start_time)
            if last_hb:
                from datetime import timedelta
                next_due = last_hb + timedelta(seconds=session.heartbeat_frequency_seconds)
                next_heartbeat_due = next_due.isoformat()

        # Check if this session is running from heartbeat (persistent across loops)
        running_from_heartbeat = session_id in self._sessions_running_from_heartbeat

        # Check if this session is waiting for heartbeat to auto-resume
        waiting_for_heartbeat = (
            status == STATUS_WAITING_HEARTBEAT
            or (status not in (STATUS_RUNNING, STATUS_TERMINATED, STATUS_ASLEEP)
                and session.heartbeat_enabled
                and not session.heartbeat_paused
                and bool(session.heartbeat_instruction))
        )

        return SessionDaemonState(
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
            # Last user command (from history sync)
            last_command=self._last_commands.get(session_id),
            # Skills (#252)
            available_skills=session.available_skills,
            loaded_skills=session.loaded_skills,
            # Wrapper/sandbox badges (#437, #451)
            wrapper=session.wrapper,
            sandbox_enabled=session.sandbox_enabled,
            # Resource usage (summed over the agent process tree)
            cpu_percent=session.cpu_percent,
            rss_bytes=session.rss_bytes,
        )

    def check_and_send_heartbeats(self, sessions: list) -> set:
        """Check all sessions and send heartbeats if due.

        Args:
            sessions: List of Session objects to check

        Returns:
            Set of session IDs that received heartbeats this loop
        """
        now = datetime.now()
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
                    last_heartbeat_time=now.isoformat()
                )
                triggered.add(session.id)
                self.log.info(f"[{session.name}] Heartbeat sent")

        return triggered

    def _update_state_time(self, session, status: str, now: datetime) -> None:
        """Update green_time_seconds and non_green_time_seconds."""
        session_id = session.id
        current_stats = session.stats

        # Get last recorded time
        last_time = self.last_state_times.get(session_id)
        if last_time is None:
            # First observation after daemon (re)start - use last_time_accumulation
            # to avoid re-adding time that was already accumulated before restart
            last_time = parse_datetime_safe(current_stats.last_time_accumulation)
            if last_time is None:
                # Fallback for sessions without last_time_accumulation
                last_time = parse_datetime_safe(current_stats.state_since)
            if last_time is None:
                last_time = now
            self.last_state_times[session_id] = last_time
            return  # Don't accumulate on first observation

        # Calculate elapsed time
        elapsed = (now - last_time).total_seconds()
        if elapsed <= 0:
            return

        # Get session start time for capping
        session_start = parse_datetime_safe(session.start_time)

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
        )

        if result.was_capped:
            total = current_stats.green_time_seconds + current_stats.non_green_time_seconds + current_stats.sleep_time_seconds
            max_allowed = (now - session_start).total_seconds() if session_start else 0
            self.log.warn(
                f"[{session.name}] Time tracking reset: "
                f"accumulated {total/3600:.1f}h > uptime {max_allowed/3600:.1f}h"
            )

        # Update state tracking
        state_since = current_stats.state_since
        if result.state_changed:
            state_since = now.isoformat()
        elif not state_since:
            # Initialize state_since if never set (e.g., new session)
            state_since = now.isoformat()

        # Staged for the tick's single write
        self._pending.update_stats(
            session_id,
            current_state=status,
            state_since=state_since,
            green_time_seconds=result.green_seconds,
            non_green_time_seconds=result.non_green_seconds,
            sleep_time_seconds=result.sleep_seconds,
            last_time_accumulation=now.isoformat(),
        )

        self.last_state_times[session_id] = now

    def sync_session_id(self, session) -> None:
        """Detect and bind Claude session IDs (#116, #373).

        For newly launched agents, the launcher prescribes --session-id at
        launch and immediately binds it. This method handles two scenarios:

        1. Post-/clear detection: after /clear, Claude restarts with a new
           sessionId that appears in history.jsonl.
        2. Unmatched prescribed IDs: when Claude Code doesn't honor
           --session-id (e.g. after restart), the actual sessionIds need
           to be discovered from history.jsonl.

        Uses an ownership guard to prevent cross-contamination when multiple
        agents share the same working directory — a sessionId already owned
        by another agent is never stolen.

        Runs every 10 seconds.
        """
        if not session.start_directory:
            return

        reader = stats_reader_for_session(session)
        try:
            session_start = datetime.fromisoformat(session.start_time)

            # Fast path: discover the most recent sessionId for this directory.
            # Handles post-/clear detection.
            current_id = reader.get_current_session_id(session, session_start)
            if current_id:
                all_sessions = [
                    s for s in self.session_manager.list_sessions()
                    if s.tmux_session == self.tmux_session
                ]
                if not is_session_id_owned_by_others(current_id, session.id, all_sessions):
                    self.session_manager.add_agent_session_id(session.id, current_id)
                    self.session_manager.set_active_agent_session_id(session.id, current_id)

            # Slow path: if the agent has owned session IDs but they produced
            # zero tokens in the last stats sync, scan history.jsonl for ALL
            # unowned sessionIds in this directory and adopt them.  This
            # recovers from --session-id not being honored by Claude Code.
            owned_ids = session.agent_session_ids or []
            stats = session.stats
            has_zero_tokens = (
                owned_ids
                and stats.input_tokens == 0
                and stats.output_tokens == 0
                and stats.last_stats_update is not None  # at least one sync happened
            )
            if has_zero_tokens:
                self._discover_all_session_ids(session, session_start, reader)
        except (ValueError, TypeError):
            pass

    def _discover_all_session_ids(
        self, session, session_start: datetime, reader=None
    ) -> None:
        """Adopt unowned agent session ids the backend can see on disk.

        When the prescribed --session-id wasn't honored by Claude Code,
        the agent's actual sessionIds are unknown.  The reader scans for
        ids matching this agent's directory+timestamp that no other agent
        owns; this method persists what it finds.
        """
        reader = reader or stats_reader_for_session(session)
        all_sessions = [
            s for s in self.session_manager.list_sessions()
            if s.tmux_session == self.tmux_session
        ]

        discovered = reader.discover_session_ids(session, session_start, all_sessions)

        for sid in discovered.ids:
            self.session_manager.add_agent_session_id(session.id, sid)
            self.log.info(f"[{session.name}] Discovered unowned sessionId: {sid[:8]}...")

        if discovered.latest:
            self.session_manager.set_active_agent_session_id(
                session.id, discovered.latest
            )

    def _apply_container_stats(self, session, stats: AgentSessionStats) -> None:
        """Persist stats read from inside a container agent's filesystem."""
        detected_model = stats.model
        detected_provider = stats.provider

        # Update model/provider if detected
        if detected_model and detected_model != session.model:
            self._pending.update_session(session.id, model=detected_model)
        if detected_provider and detected_provider != session.provider:
            self._pending.update_session(session.id, provider=detected_provider)

        # Cost estimate
        from .settings import get_user_config, get_model_pricing
        config = get_user_config()
        mp = get_model_pricing(
            detected_model or session.model, config,
            provider=detected_provider or session.provider,
        )
        cost = calculate_cost_estimate(
            stats.input_tokens, stats.output_tokens,
            stats.cache_creation_tokens, stats.cache_read_tokens,
            price_input=mp.input, price_output=mp.output,
            price_cache_write=mp.cache_write, price_cache_read=mp.cache_read,
        )

        now = datetime.now()
        total_tokens = calculate_total_tokens(
            stats.input_tokens, stats.output_tokens,
            stats.cache_creation_tokens, stats.cache_read_tokens,
        )
        self._pending.update_stats(
            session.id,
            total_tokens=total_tokens,
            input_tokens=stats.input_tokens,
            output_tokens=stats.output_tokens,
            cache_creation_tokens=stats.cache_creation_tokens,
            cache_read_tokens=stats.cache_read_tokens,
            estimated_cost_usd=round(cost, 4),
            current_context_tokens=stats.current_context_tokens,
            last_stats_update=now.isoformat(),
        )

    def sync_agent_stats(self, session) -> None:
        """Sync token/interaction stats from the backend's transcripts.

        Backends without readable transcripts get a NullStatsReader, which
        answers "unknown" for everything — nothing is written, so their
        columns render placeholders instead of zeros.
        """
        reader = stats_reader_for_session(session)
        try:
            # Container agents: read stats via docker exec
            if session.wrapper:
                container_stats = reader.get_container_stats(session)
                if container_stats is not None:
                    self._apply_container_stats(session, container_stats)
                    return

            # Session ID detection also runs here for the first sync
            self.sync_session_id(session)

            stats = reader.get_stats(session)
            if stats is None:
                return

            now = datetime.now()
            total_tokens = calculate_total_tokens(
                stats.input_tokens,
                stats.output_tokens,
                stats.cache_creation_tokens,
                stats.cache_read_tokens,
            )

            # Update session-level model and provider from history files.
            # Provider is detected from the assistant message ID prefix
            # ("msg_bdrk_" = bedrock, "msg_" = web), which stays correct
            # across /clear (fixing cases where a bedrock agent switches
            # to Claude Max and vice versa).
            if stats.model and stats.model != session.model:
                self._pending.update_session(session.id, model=stats.model)
            if stats.provider and stats.provider != session.provider:
                self._pending.update_session(session.id, provider=stats.provider)

            # Cache last command for daemon state publishing
            if stats.last_command:
                self._last_commands[session.id] = stats.last_command

            # Estimate cost using per-model pricing (falls back to global config)
            from .settings import get_user_config, get_model_pricing
            config = get_user_config()
            mp = get_model_pricing(
                stats.model or session.model, config,
                provider=stats.provider or session.provider,
            )
            cost_estimate = calculate_cost_estimate(
                stats.input_tokens,
                stats.output_tokens,
                stats.cache_creation_tokens,
                stats.cache_read_tokens,
                price_input=mp.input,
                price_output=mp.output,
                price_cache_write=mp.cache_write,
                price_cache_read=mp.cache_read,
            )

            # Some backends record what the provider actually charged (opencode
            # keeps a per-session `cost`). That beats a pricing-table estimate,
            # so it wins when present; a zero/absent figure falls back above.
            stored_cost_reader = getattr(reader, "get_stored_cost", None)
            if stored_cost_reader is not None:
                stored_cost = stored_cost_reader(session)
                if stored_cost:
                    cost_estimate = stored_cost

            # The stats object's fallback recovers the persona the agent is
            # actually running. For backends that honor launch-time --agent,
            # only fill an empty persona — never clobber the launcher's
            # choice. For backends without AGENT_INJECTION (opencode2, codex),
            # a stored persona was never applied to the CLI, so the detected
            # persona is the truth and replaces it.
            detected_agent = getattr(stats, "agent", None)
            if detected_agent:
                honors_launch_agent = bool(
                    get_backend(session_backend_name(session)).capabilities
                    & BackendCapability.AGENT_INJECTION
                )
                if honors_launch_agent:
                    if not getattr(session, "agent_persona", None):
                        self._pending.update_session(
                            session.id, agent_persona=detected_agent
                        )
                else:
                    self._pending.update_session(
                        session.id, agent_persona=detected_agent
                    )

            self._pending.update_stats(
                session.id,
                interaction_count=stats.interaction_count,
                total_tokens=total_tokens,
                input_tokens=stats.input_tokens,
                output_tokens=stats.output_tokens,
                cache_creation_tokens=stats.cache_creation_tokens,
                cache_read_tokens=stats.cache_read_tokens,
                estimated_cost_usd=round(cost_estimate, 4),
                current_context_tokens=stats.current_context_tokens,
                last_stats_update=now.isoformat(),
            )
        except Exception as e:
            self.log.warn(f"Failed to sync stats for {session.name}: {e}")

    # Pre-backend name, kept until the Phase 6 rename sweep.
    sync_claude_code_stats = sync_agent_stats

    def _calculate_median_work_time(self, operation_times: List[float]) -> float:
        """Calculate median operation time."""
        return calculate_median(operation_times)

    def attendance(self) -> str:
        """``"attended"`` or ``"unattended"``: is anyone watching this fleet?

        Unattended only when all three say nobody is: this tick's pane
        listing counted no client attached to the agents tmux session
        (``session_attached``; an unknown count — listing failed — counts
        as attended), the TUI keypress heartbeat is not fresh
        (PresenceComponent's 60 s window), and no TUI has touched its
        attended file within TUI_ATTENDED_FRESHNESS. The touch is what a
        TUI in another tmux session or a plain terminal has — the first
        two cannot see it — so the daemon never slows under a dashboard
        someone is reading. Web dashboard and sister readers carry no
        signal and are not covered.
        """
        attached = self.session_attached
        if attached is None or attached > 0:
            return "attended"
        if self.presence._is_tui_active():
            return "attended"
        age = tui_attended_age_seconds(self.tmux_session)
        if age is not None and age <= TUI_ATTENDED_FRESHNESS:
            return "attended"
        return "unattended"

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

    def _housekeeping_due(self, now: datetime) -> bool:
        """Wall-clock replacement for ``loop_count % 60 == 0``.

        The first tick starts the clock (the loop-count rule first fired
        two minutes in, not on loop 1); after that every
        HOUSEKEEPING_INTERVAL_SECONDS, at the 2 s loop and the unattended
        one alike. Tests move ``_last_housekeeping`` back to force a pass.
        """
        last = self._last_housekeeping
        if last is None:
            self._last_housekeeping = now
            return False
        if (now - last).total_seconds() >= HOUSEKEEPING_INTERVAL_SECONDS:
            self._last_housekeeping = now
            return True
        return False

    def _interruptible_sleep(self, total_seconds: int) -> None:
        """Sleep with activity signal checking.

        The signal (a TUI keypress, or a TUI re-attaching) ends the sleep
        and puts the next loop on the fast interval, so an unattended
        daemon is back within a second of the user's return; a bare
        ``tmux attach`` with no TUI is seen by the next tick's listing.
        """
        chunk_size = 1
        elapsed = 0

        while elapsed < total_seconds and not self._shutdown:
            remaining = total_seconds - elapsed
            sleep_time = min(chunk_size, remaining)
            time.sleep(sleep_time)
            elapsed += sleep_time

            if check_activity_signal(self.tmux_session):
                self.log.info("User activity detected → waking up")
                self.state.current_interval = INTERVAL_FAST
                self.state.interval_mode = "attended"
                self.state.save(self.state_path)
                return

    def _auto_archive_done_agents(self, sessions: list) -> None:
        """Auto-archive done agents that have been done for over 1 hour (#244).

        Kills the tmux window and marks as terminated so cleanup can remove them.
        """
        now = datetime.now()

        for session in sessions:
            if not should_auto_archive(
                session.status,
                session.stats.state_since,
                now,
            ):
                continue

            # Archive: kill tmux window and mark terminated
            try:
                from .implementations import RealTmux
                tmux = RealTmux()
                tmux.kill_window(self.tmux_session, session.tmux_window)
            except Exception:
                pass  # Window may already be gone
            self._pending.update_session_status(session.id, "terminated")
            self.log.info(f"Auto-archived done agent: {session.name}")

    def _count_untracked_windows(self, sessions: list, tmux=None) -> int:
        """Count tmux windows not tracked by any active session (#344).

        Uses the same predicate as ``overcode cleanup --untracked``
        (``tmux_utils.untracked_window_names``): window 0, live agents'
        windows and overcode's own windows (the dead-window placeholder,
        the supervisor daemon's claude window, SSH proxy windows) are not
        untracked, so the count never advertises a cleanup that would kill
        them.

        Args:
            sessions: Sessions from the current tick.
            tmux: A ``TmuxInterface``; the daemon's own client when None.
                Injectable so the count can be tested against a fake tmux —
                the previous code called a ``session_exists`` method that
                ``RealTmux`` never had, so it raised on every run and the
                ``except`` below silently reported 0 untracked windows forever.
        """
        try:
            if tmux is None:
                tmux = self._tmux
            if not tmux.has_session(self.tmux_session):
                return 0
            tmux_windows = tmux.list_windows(self.tmux_session)
            active_sessions = [s for s in sessions if s.status != "terminated"]
            tracked_windows = {s.tmux_window for s in active_sessions}
            return len(untracked_window_names(tmux_windows, tracked_windows))
        except Exception:
            return 0

    def _enforce_oversight_timeouts(self, sessions: list) -> None:
        """Enforce oversight timeouts for waiting_oversight sessions."""
        now = datetime.now()
        for session in sessions:
            if not should_enforce_oversight_timeout(
                session.status,
                getattr(session, 'oversight_policy', 'wait'),
                getattr(session, 'oversight_deadline', None),
                now,
            ):
                continue
            self._pending.update_session(
                session.id,
                report_status="failure",
                report_reason="Oversight timeout expired",
            )
            self._pending.update_session_status(session.id, "done")
            self.log.info(f"[{session.name}] Oversight timeout expired, marked done")

    def _publish_state(self, session_states: List[SessionDaemonState]) -> None:
        """Publish current state to JSON file."""
        now = datetime.now()

        # Update presence state
        presence_state, presence_idle, _ = self.presence.get_current_state()

        self.state.last_loop_time = now.isoformat()
        # getattr: test doubles built via __new__ skip __init__
        self.state.last_tick_duration_seconds = round(
            getattr(self, "_last_tick_duration_seconds", 0.0), 3
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

        self.state.save(self.state_path)

        # Push to relay if configured and interval elapsed
        self._maybe_push_to_relay()

    def _maybe_push_to_relay(self) -> None:
        """Push state to cloud relay if configured."""
        # Update relay enabled status
        self.state.relay_enabled = self._relay_config is not None

        if not self._relay_config:
            self.state.relay_last_status = "disabled"
            return

        now = datetime.now()
        interval = self._relay_config.get("interval", 30)
        if (now - self._last_relay_push).total_seconds() < interval:
            return

        self._last_relay_push = now

        try:
            import json
            import urllib.request
            import urllib.error

            # Build status payload using web_api format
            from .web_api import get_status_data

            payload = get_status_data(self.tmux_session)

            # Optionally include timeline (less frequent)
            # payload["timeline"] = get_timeline_data(self.tmux_session)

            data = json.dumps(payload).encode("utf-8")

            req = urllib.request.Request(
                self._relay_config["url"],
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": self._relay_config["api_key"],
                },
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    self.state.relay_last_push = now.isoformat()
                    self.state.relay_last_status = "ok"
                    self.log.debug("Relay push OK")
                else:
                    self.state.relay_last_status = "error"
                    self.log.warn(f"Relay push failed: HTTP {resp.status}")

        except urllib.error.URLError as e:
            self.state.relay_last_status = "error"
            self.log.warn(f"Relay push failed: {e.reason}")
        except Exception as e:
            self.state.relay_last_status = "error"
            self.log.warn(f"Relay push error: {e}")

    # ------------------------------------------------------------------
    # Tick phases — decomposed from the monolithic run() loop
    # ------------------------------------------------------------------

    def _tick(self, now: datetime) -> None:
        """Execute one monitoring loop iteration.

        Times itself: the duration is published by the *next* tick's
        ``_publish_state`` (this tick publishes mid-way, before it knows its
        own length) so consumers can widen their staleness window instead of
        treating a slow tick as a dead daemon.
        """
        tick_t0 = time.monotonic()
        self.state.tick_started_at = now.isoformat()
        try:
            self._tick_phases(now)
        finally:
            self._last_tick_duration_seconds = time.monotonic() - tick_t0

    def _tick_phases(self, now: datetime) -> None:
        """The phases of one tick, in order."""
        # Re-read the fleet default detection mode (the legacy global
        # detection_mode file). Per-agent overrides are resolved inside the
        # dispatcher, on top of this default.
        from .settings import resolve_detection_mode
        current_mode = resolve_detection_mode(self.tmux_session)
        if self.detector.mode != current_mode:
            self.detector.mode = current_mode
            self.log.info(f"Fleet detection mode changed to: {current_mode}")
        # One snapshot per tick: the index answers every parent/child lookup
        # the tick makes, and ``sessions`` is this tmux session's slice of it
        # in file order (what list_sessions() would return, filtered).
        index = self._session_index()
        sessions = [s for s in index.by_id.values() if s.tmux_session == self.tmux_session]
        if not self._legacy_windows_migrated:
            self._migrate_legacy_window_ids(sessions)
            self._legacy_windows_migrated = True
        self._sync_session_ids(sessions, now)
        self._sync_session_stats(sessions, now)
        self._sync_available_skills(sessions, now)
        self._sync_sandbox_state(sessions, now)
        self._sync_process_resources(sessions, now)
        self._dispatch_heartbeats(sessions)
        try:
            session_states, all_waiting = self._detect_and_enrich(sessions, now, index)
            self._cleanup_stale(sessions)
            self._publish_and_enforce(sessions, session_states, all_waiting, index, now)
        finally:
            # One write for the whole tick, even when a phase raised: what
            # was staged before the failure lands, as it did when each
            # change was written as it was found.
            self._flush_pending_writes()
        self._maybe_rotate_history(now)
        self._maybe_refresh_model_metadata(now)

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

    def _maybe_rotate_history(self, now: datetime) -> None:
        """Rotate/compress agent_status_history.csv and prune old archives (#465, #468).

        Runs at most hourly — a size/age stat check is cheap, but there's no
        reason to do even that on every 2-30s tick.
        """
        if not should_sync_stats(
            self._last_history_rotation_check, now, self._history_rotation_check_interval
        ):
            return
        self._last_history_rotation_check = now
        try:
            from .config import get_history_retention_config
            from .status_history import rotate_and_retain

            cfg = get_history_retention_config()
            result = rotate_and_retain(
                self.history_path,
                rotate_mb=cfg["status_history_rotate_mb"],
                retention_days=cfg["status_history_max_days"],
                now=now,
            )
            if result["archived"]:
                self.log.info(f"Rotated agent_status_history.csv -> {result['archived'].name}")
            if result["deleted"]:
                self.log.info(
                    f"Pruned {len(result['deleted'])} expired agent_status_history archive(s)"
                )
        except Exception as e:
            self.log.error(f"History rotation check failed: {e}")

    def _maybe_refresh_model_metadata(self, now: datetime) -> None:
        """Refresh the local models.dev cache when opted in and stale (#473).

        Runs its stat() check at most hourly; the fetch only happens when
        ``model_metadata.auto_refresh`` is true *and* the local cache is
        missing or older than ``max_age_days``. The fetch itself runs on a
        daemon thread — the loop never waits on the network — and a failure
        backs off for ``_model_metadata_failure_backoff`` seconds with one
        warning, so an air-gapped or packet-dropping host sees neither a
        stall nor an hourly log line. Lookups keep working off whatever
        catalog is already on disk throughout.
        """
        if not should_sync_stats(
            self._last_model_metadata_check, now, self._model_metadata_check_interval
        ):
            return
        self._last_model_metadata_check = now
        thread = self._model_metadata_thread
        if thread is not None and thread.is_alive():
            return
        if self._model_metadata_backoff_until and now < self._model_metadata_backoff_until:
            return
        try:
            from .config import get_model_metadata_config
            from . import model_metadata

            cfg = get_model_metadata_config()
            if not cfg["auto_refresh"]:
                return
            age = model_metadata.local_cache_age_days()
            if age is not None and age < cfg["max_age_days"]:
                return
        except Exception as e:
            self.log.warning(f"Model metadata auto-refresh check failed: {e}")
            return

        thread = threading.Thread(
            target=self._run_model_metadata_refresh,
            name="model-metadata-refresh",
            daemon=True,
        )
        self._model_metadata_thread = thread
        thread.start()

    def _run_model_metadata_refresh(self) -> None:
        """Background body of the auto-refresh: fetch, write atomically, log once."""
        try:
            from . import model_metadata

            info = model_metadata.refresh_local_cache()
            self._model_metadata_backoff_until = None
            self.log.info(
                f"Refreshed model metadata catalog from models.dev: "
                f"{info['model_count']} models -> {info['path']}"
            )
        except Exception as e:
            self._model_metadata_backoff_until = datetime.now() + timedelta(
                seconds=self._model_metadata_failure_backoff
            )
            hours = self._model_metadata_failure_backoff / 3600
            self.log.warning(
                f"Model metadata auto-refresh failed; not retrying for {hours:g}h "
                f"(lookups keep using the catalog already on disk): {e}"
            )

    def _sync_session_ids(self, sessions: list, now: datetime) -> None:
        """Fast session ID detection every 10s (#116).

        Ensures active_agent_session_id updates promptly after /clear.
        """
        if should_sync_stats(self._last_session_id_sync, now, self._session_id_sync_interval):
            for session in sessions:
                self.sync_session_id(session)
            self._last_session_id_sync = now

    def _sync_session_stats(self, sessions: list, now: datetime) -> None:
        """Full transcript stats sync every 60s (heavier I/O).

        Ensures the first loop has accurate data (fixes #103).
        """
        if should_sync_stats(self._last_stats_sync, now, self._stats_sync_interval):
            for session in sessions:
                self.sync_agent_stats(session)
            self._last_stats_sync = now

    def _sync_available_skills(self, sessions: list, now: datetime) -> None:
        """Scan installed skill directories every 60s (#252)."""
        if should_sync_stats(self._last_skills_sync, now, self._skills_sync_interval):
            from .bundled_skills import get_available_skills
            for session in sessions:
                available = get_available_skills(session.start_directory)
                if available != session.available_skills:
                    self._pending.update_session(session.id, available_skills=available)
            self._last_skills_sync = now

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
        kept from the same listing for consumers that want it.
        """
        if self._pane_table_at != now:
            self._pane_table_at = now
            self._pane_table = self._tmux.list_panes(self.tmux_session)
            if self._pane_table:
                self.session_attached = next(iter(self._pane_table.values())).session_attached
        return self._pane_table

    def _sync_process_resources(self, sessions: list, now: datetime) -> None:
        """Sample CPU and RSS for each agent's claude process tree.

        One batched `ps` call populates cpu_percent (sum of per-CPU %) and
        rss_bytes (sum of resident set size) across the claude process and
        every descendant. Tools spawned under a bash call (e.g. a runaway
        `tsc --watch`) therefore show up on the parent agent's row.
        """
        if not should_sync_stats(
            self._last_resources_sync, now, self._resources_sync_interval
        ):
            return
        from .doctor import (
            find_agent_process, session_process_argv_markers,
            session_process_basenames,
        )
        from .process_resources import (
            snapshot_processes, build_children_index, aggregate_tree,
        )

        snapshot = snapshot_processes()
        if not snapshot:
            self._last_resources_sync = now
            return
        # build_children_index walks the whole snapshot, so the argv_by_pid
        # shape doctor expects is derived on the fly.
        children = build_children_index(snapshot)
        argv_by_pid = {pid: info.argv for pid, info in snapshot.items()}
        panes = self._panes_at(now) or {}
        for session in sessions:
            if getattr(session, "is_remote", False):
                continue
            pane = pane_for_window(panes, session.tmux_window)
            if pane is None:
                continue
            claude_pid, _ = find_agent_process(
                pane.pane_pid, children, argv_by_pid, session_process_basenames(session),
                session_process_argv_markers(session),
            )
            if claude_pid is None:
                # Reset to 0 so a dead/missing agent doesn't pin a stale reading.
                if session.cpu_percent or session.rss_bytes:
                    self._pending.update_session(
                        session.id, cpu_percent=0.0, rss_bytes=0,
                    )
                continue
            cpu, rss = aggregate_tree(claude_pid, snapshot, children)
            # Only stage when the value moved meaningfully — avoids a JSON
            # write every 5s for an idle agent whose CPU is drifting by 0.1%.
            # Everything staged lands in the tick's single write.
            if (
                abs(cpu - session.cpu_percent) >= 1.0
                or abs(rss - session.rss_bytes) >= 1024 * 1024  # 1 MiB
            ):
                self._pending.update_session(
                    session.id, cpu_percent=cpu, rss_bytes=rss,
                )
        self._last_resources_sync = now

    def _sync_sandbox_state(self, sessions: list, now: datetime) -> None:
        """Detect /sandbox toggle state from claude process listeners (#451)."""
        if not should_sync_stats(self._last_sandbox_sync, now, self._sandbox_sync_interval):
            return
        from .doctor import (
            _snapshot_process_table, _build_child_index, find_agent_process,
            session_process_argv_markers, session_process_basenames,
        )
        from .sandbox_detect import detect_sandbox_states

        rows = _snapshot_process_table()
        if not rows:
            self._last_sandbox_sync = now
            return
        children, argv_by_pid = _build_child_index(rows)
        panes = self._panes_at(now) or {}
        # Gather all local claude PIDs with one lsof call (#451 optimization).
        session_pids: dict = {}  # session.id -> claude_pid
        for session in sessions:
            if getattr(session, "is_remote", False):
                continue
            # The loopback-listener heuristic only means anything for
            # backends that have a sandbox toggle.
            if not session_supports(session, BackendCapability.SANDBOX_PROBE):
                continue
            pane = pane_for_window(panes, session.tmux_window)
            if pane is None:
                continue
            claude_pid, _ = find_agent_process(
                pane.pane_pid, children, argv_by_pid, session_process_basenames(session),
                session_process_argv_markers(session),
            )
            if claude_pid is not None:
                session_pids[session.id] = claude_pid
        states = detect_sandbox_states(session_pids.values())
        for session in sessions:
            if session.id not in session_pids:
                continue
            detected = states.get(session_pids[session.id])
            if detected != session.sandbox_enabled:
                self._pending.update_session(session.id, sandbox_enabled=detected)
        self._last_sandbox_sync = now

    def _dispatch_heartbeats(self, sessions: list) -> None:
        """Send heartbeats before status detection (#171)."""
        self._heartbeat_triggered_sessions = self.check_and_send_heartbeats(sessions)
        # Add newly triggered sessions to persistent heartbeat tracking
        self._sessions_running_from_heartbeat.update(self._heartbeat_triggered_sessions)
        # Track pending heartbeat starts for timeline marker
        self._heartbeat_start_pending.update(self._heartbeat_triggered_sessions)

    def _detect_and_enrich(
        self, sessions: list, now: datetime, index: Optional[SessionIndex] = None
    ) -> tuple:
        """Detect status and build SessionDaemonState for each session.

        ``index`` is the tick's ``SessionIndex`` (built over the snapshot
        ``sessions`` came from); one is built here when the caller has none.

        Returns:
            (session_states, all_waiting_user) tuple
        """
        session_states = []
        all_waiting_user = True
        if index is None:
            index = self._session_index()
        pending = self._pending
        self._plan_captures(sessions, now)

        for snapshot in sessions:
            # Earlier phases of this tick may have staged changes for this
            # session (a heartbeat stamp, tokens, a model, CPU); read through
            # them, as the per-write path re-read the file after each.
            session = pending.view(snapshot)
            pane_content = ""
            if session.status == "done":
                status, activity = STATUS_DONE, "Completed"
            else:
                # Detect status - dispatches per-session via dispatcher (#5)
                status, activity, pane_content = self.detector.detect_status(session)

                # Log hook events when they change (diagnostic visibility)
                self._log_hook_event(session, status, activity)

                # Track loaded skills from hook events (#252)
                if hasattr(self.detector, 'get_loaded_skills'):
                    new_skills = self.detector.get_loaded_skills(session.name)
                    if new_skills and sorted(new_skills) != sorted(session.loaded_skills):
                        pending.update_session(session.id, loaded_skills=new_skills)

                # Extract PR number from pane content
                if pane_content:
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
            elif (status not in (STATUS_RUNNING, STATUS_TERMINATED, STATUS_ASLEEP)
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

            session_state = self.track_session_stats(session, effective_status, index)
            session_state.current_activity = activity
            session_states.append(session_state)

            # Log status history to session-specific file — on change, on
            # keepalive, and on first sight; a row stands until the next.
            if status_row_due(
                self._last_logged.get(session.id),
                self._last_keepalive.get(session.id),
                effective_status,
                activity,
                now,
                STATUS_HISTORY_KEEPALIVE_SECONDS,
            ):
                log_agent_status(
                    session.name, effective_status, activity,
                    history_file=self.history_path,
                    session_id=session.id,
                    hostname=self._hostname,
                )
                self._last_logged[session.id] = (
                    effective_status, activity[:100] if activity else ""
                )
                self._last_keepalive[session.id] = now

            # Track if any session is not waiting for user
            if status != "waiting_user":
                all_waiting_user = False

        # Compute subtree costs for parent agents
        self._compute_subtree_costs(session_states)

        return session_states, all_waiting_user

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
        clock = self._loop_clock()
        for session in sessions:
            if session.status == "done":
                continue
            pane = pane_for_window(panes, session.tmux_window)
            signature = pane.signature if pane is not None else None
            capture = tracker.due(
                session.id, signature, self._hook_state_stamp(session.name), clock
            )
            gate.plan(session.tmux_window, capture)

    def _hook_state_stamp(self, session_name: str) -> Optional[tuple]:
        """(mtime_ns, size) of the session's hook_state file; None without one.

        The file the hook detector reads for status: any hook event rewrites
        it, so its stat moving is the signal that the pane must be re-read
        for the event's enrichment even if the listing saw no pane change.
        """
        path_of = getattr(getattr(self.detector, "hooks", None), "_hook_state_path", None)
        if path_of is None:
            return None
        try:
            st = os.stat(path_of(session_name))
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

        Its window is gone, so detection says terminated — but the TUI's
        ``AgentLauncher.list_sessions`` re-asserts waiting_oversight for
        exactly this case (parent set, Stop hook, no report) on its next
        pass, and the two used to rewrite sessions.json in turn every 10 s
        for as long as the child sat there (audit R5). The persisted status
        stays waiting_oversight; the published status is terminated either
        way, and the oversight timeout keeps applying to it.
        """
        if session.status != STATUS_WAITING_OVERSIGHT or session.parent_session_id is None:
            return False
        try:
            hook_state = self.detector.hooks._read_hook_state(session.name)
        except Exception:
            return False
        return isinstance(hook_state, dict) and hook_state.get("event") == "Stop"

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

    def _compute_subtree_costs(self, session_states):
        """Compute subtree cost (self + all descendants) for each parent agent.

        Agents are linked by *name*, and names are not guaranteed unique: a
        duplicate name whose entry lists itself (or an ancestor) as parent
        forms a cycle in ``children_map``. Each walk carries a ``visited`` set
        so such a cycle counts every member once instead of recursing until
        the daemon dies with RecursionError.
        """
        by_name = {s.name: s for s in session_states}
        children_map = {}
        for s in session_states:
            if s.parent_name and s.parent_name in by_name:
                children_map.setdefault(s.parent_name, []).append(s.name)

        def _sum(name, visited):
            visited.add(name)
            total = by_name[name].estimated_cost_usd
            for child in children_map.get(name, []):
                if child not in visited:
                    total += _sum(child, visited)
            return total

        for s in session_states:
            if children_map.get(s.name):
                s.subtree_cost_usd = _sum(s.name, set())

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
        self._pane_tracker.forget(current_session_ids)
        self._capture_gate.forget({s.tmux_window for s in sessions})

    def _archive_terminated_sessions(self, all_sessions, now: datetime) -> None:
        """Stage terminated entries that have outstayed the grace for the archive.

        Runs from the every-60-loops housekeeping over every entry in
        sessions.json, whatever its tmux session: an entry left behind by
        a tmux session with no daemon would otherwise stay forever, and
        every TUI and daemon on the host parses the file whole. The clock
        starts when this daemon first sees the entry terminated (a restart
        starts it again, so an entry waits at most one extra grace) and is
        dropped for an entry that is revived or removed. The move itself is
        part of the tick's single commit, with the record ``overcode
        cleanup`` writes.
        """
        from .config import get_session_archive_config

        grace = get_session_archive_config()["terminated_grace_seconds"]
        seen = set()
        for session in all_sessions:
            if session.status != STATUS_TERMINATED:
                continue
            seen.add(session.id)
            since = self._terminated_since.setdefault(session.id, now)
            if should_archive_terminated(since, now, grace):
                self._pending.archive_session(session.id)
                del self._terminated_since[session.id]
                self.log.info(f"Archived terminated session: {session.name}")
        for session_id in [sid for sid in self._terminated_since if sid not in seen]:
            del self._terminated_since[session_id]

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
        if now is None:
            now = datetime.now()
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
        self._enforce_oversight_timeouts(sessions)

        # Auto-archive "done" agents after 1 hour (#244)
        # Count untracked tmux windows every 2 minutes (#344)
        # Move terminated sessions to the archive once past their grace
        if self._housekeeping_due(now):
            self._auto_archive_done_agents(sessions)
            self.state.untracked_window_count = self._count_untracked_windows(sessions)
            all_sessions = (
                index.by_id.values() if index is not None else self.session_manager.list_sessions()
            )
            self._archive_terminated_sessions(all_sessions, now)

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

        self.state.status = "active"
        self.state.current_interval = check_interval
        self.state.save(self.state_path)

        try:
            while not self._shutdown:
                self.state.loop_count += 1
                now = datetime.now()
                self._tick(now)
                self._interruptible_sleep(self.state.current_interval)
        except Exception as e:
            self.log.error(f"Monitor daemon error: {e}")
            raise
        finally:
            self.log.info("Monitor daemon shutting down")
            self.presence.stop()
            self.state.status = "stopped"
            self.state.save(self.state_path)
            remove_pid_file(self.pid_path)


def main() -> int:
    """CLI entrypoint for monitor daemon."""
    import argparse

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

    daemon = MonitorDaemon(tmux_session=args.session)
    daemon.run(check_interval=args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
