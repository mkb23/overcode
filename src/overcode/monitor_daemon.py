#!/usr/bin/env python3
"""
Monitor Daemon - Single source of truth for all session metrics.

This daemon handles all monitoring responsibilities:
- Agent status detection (via StatusDetector)
- Time tracking (green_time_seconds, non_green_time_seconds)
- Claude Code stats sync (tokens, interactions)
- Presence tracking (macOS only, graceful degradation)
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
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .daemon_logging import BaseDaemonLogger
from .daemon_utils import create_daemon_helpers
from .history_reader import get_session_stats, get_current_session_id_for_directory
from .monitor_daemon_state import (
    MonitorDaemonState,
    SessionDaemonState,
    get_monitor_daemon_state,
)
from .pid_utils import (
    acquire_daemon_lock,
    remove_pid_file,
)
from .session_manager import SessionManager
from .settings import (
    DAEMON,
    DAEMON_VERSION,
    PATHS,
    ensure_session_dir,
    get_monitor_daemon_pid_path,
    get_monitor_daemon_state_path,
    get_agent_history_path,
    get_activity_signal_path,
    get_supervisor_stats_path,
    get_tui_heartbeat_path,
)
from .config import get_relay_config
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
from .status_detector import StatusDetector, PollingStatusDetector
from .hook_status_detector import HookStatusDetector
from .status_detector_factory import StatusDetectorDispatcher
from .status_history import log_agent_status
from .monitor_daemon_core import (
    calculate_time_accumulation,
    calculate_cost_estimate,
    calculate_total_tokens,
    calculate_median,
    should_sync_stats,
    parse_datetime_safe,
    is_heartbeat_eligible,
    is_heartbeat_due,
    should_auto_archive,
    should_enforce_oversight_timeout,
)
from .tmux_utils import send_text_to_tmux_window


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
    """Presence tracking with graceful degradation for non-macOS."""

    # TUI heartbeat is considered fresh if within this many seconds
    TUI_HEARTBEAT_FRESHNESS = 60

    def __init__(self, tmux_session: str = "agents"):
        self.available = MACOS_APIS_AVAILABLE
        self._logger: Optional[PresenceLogger] = None
        self._tmux_session = tmux_session
        self._last_publish_time: Optional[datetime] = None

        if self.available and PresenceLogger is not None:
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
        slept = gap > 2 * DAEMON.interval_fast
        self._last_publish_time = now
        return slept

    def get_current_state(self) -> tuple:
        """Get current presence state.

        Returns:
            Tuple of (state, idle_seconds, locked) or (None, None, None) if unavailable
        """
        if not self.available or get_current_presence_state is None:
            return None, None, None

        try:
            tui_active = self._is_tui_active()
            slept = self._detect_sleep()

            if slept:
                # Machine just woke — override to asleep state for this sample
                idle = 0.0
                locked = False
                from .presence_logger import classify_state, DEFAULT_IDLE_THRESHOLD
                state = classify_state(
                    locked=locked,
                    idle_seconds=idle,
                    slept=True,
                    idle_threshold=DEFAULT_IDLE_THRESHOLD,
                    tui_active=False,
                )
                return state, idle, locked

            return get_current_presence_state(tui_active=tui_active)
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
    - Claude Code stats sync
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
    ):
        self.tmux_session = tmux_session

        # Ensure session directory exists
        ensure_session_dir(tmux_session)

        # Session-specific paths
        self.pid_path = get_monitor_daemon_pid_path(tmux_session)
        self.state_path = get_monitor_daemon_state_path(tmux_session)
        self.history_path = get_agent_history_path(tmux_session)

        # Dependencies (allow injection for testing)
        self.session_manager = session_manager or SessionManager()
        self.detector = StatusDetectorDispatcher(
            tmux_session,
            polling_detector=status_detector,
        )

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

        # Per-session tracking
        self.previous_states: Dict[str, str] = {}
        self.last_state_times: Dict[str, datetime] = {}
        self.operation_start_times: Dict[str, datetime] = {}

        # Stats sync throttling - None forces immediate sync on first loop
        self._last_stats_sync: Optional[datetime] = None
        self._stats_sync_interval = 60  # seconds

        # Session ID detection runs more frequently than full stats (#116)
        self._last_session_id_sync: Optional[datetime] = None
        self._session_id_sync_interval = 10  # seconds

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

    def _get_parent_name(self, session) -> Optional[str]:
        """Get the name of a session's parent, if any (#244)."""
        if not session.parent_session_id:
            return None
        parent = self.session_manager.get_session(session.parent_session_id)
        return parent.name if parent else None

    def track_session_stats(self, session, status: str) -> SessionDaemonState:
        """Track session state and build SessionDaemonState.

        Returns the session state for inclusion in MonitorDaemonState.
        """
        session_id = session.id
        now = datetime.now()

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
                    self.session_manager.update_stats(
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
            time_context_enabled=session.time_context_enabled,
            agent_value=session.agent_value,
            # Heartbeat state (#171)
            heartbeat_enabled=session.heartbeat_enabled,
            heartbeat_frequency_seconds=session.heartbeat_frequency_seconds,
            heartbeat_paused=session.heartbeat_paused,
            last_heartbeat_time=session.last_heartbeat_time,
            next_heartbeat_due=next_heartbeat_due,
            running_from_heartbeat=running_from_heartbeat,
            waiting_for_heartbeat=waiting_for_heartbeat,
            # Cost budget (#173)
            cost_budget_usd=session.cost_budget_usd,
            budget_exceeded=_is_budget_exceeded(session, stats),
            # Agent hierarchy (#244)
            parent_name=self._get_parent_name(session),
            depth=self.session_manager.compute_depth(session),
            children_count=len(self.session_manager.get_children(session.id)),
            # Oversight system
            oversight_policy=getattr(session, 'oversight_policy', 'wait') or 'wait',
            oversight_timeout_seconds=getattr(session, 'oversight_timeout_seconds', 0.0) or 0.0,
            oversight_deadline=getattr(session, 'oversight_deadline', None),
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

            # Send the heartbeat instruction
            if send_text_to_tmux_window(
                session.tmux_session,
                session.tmux_window,
                session.heartbeat_instruction,
                send_enter=True,
            ):
                self.session_manager.update_session(
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

        # Save to session manager
        self.session_manager.update_stats(
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
        """Detect and bind the current Claude session ID (fast path, #116).

        Reads only the tail of history.jsonl so it's cheap enough to run
        every 10 seconds. This ensures active_claude_session_id updates
        promptly after /clear.
        """
        if not session.start_directory:
            return
        try:
            session_start = datetime.fromisoformat(session.start_time)
            current_id = get_current_session_id_for_directory(
                session.start_directory, session_start
            )
            if current_id:
                self.session_manager.add_claude_session_id(session.id, current_id)
                self.session_manager.set_active_claude_session_id(session.id, current_id)
        except (ValueError, TypeError):
            pass

    def sync_claude_code_stats(self, session) -> None:
        """Sync token/interaction stats from Claude Code history files."""
        try:
            # Session ID detection also runs here for the first sync
            self.sync_session_id(session)

            stats = get_session_stats(session)
            if stats is None:
                return

            now = datetime.now()
            total_tokens = calculate_total_tokens(
                stats.input_tokens,
                stats.output_tokens,
                stats.cache_creation_tokens,
                stats.cache_read_tokens,
            )

            # Estimate cost using configured pricing (defaults to Opus 4.5)
            from .settings import get_user_config
            pricing = get_user_config()
            cost_estimate = calculate_cost_estimate(
                stats.input_tokens,
                stats.output_tokens,
                stats.cache_creation_tokens,
                stats.cache_read_tokens,
                price_input=pricing.price_input,
                price_output=pricing.price_output,
                price_cache_write=pricing.price_cache_write,
                price_cache_read=pricing.price_cache_read,
            )

            self.session_manager.update_stats(
                session.id,
                interaction_count=stats.interaction_count,
                total_tokens=total_tokens,
                input_tokens=stats.input_tokens,
                output_tokens=stats.output_tokens,
                cache_creation_tokens=stats.cache_creation_tokens,
                cache_read_tokens=stats.cache_read_tokens,
                estimated_cost_usd=round(cost_estimate, 4),
                last_stats_update=now.isoformat(),
            )
        except Exception as e:
            self.log.warn(f"Failed to sync stats for {session.name}: {e}")

    def _calculate_median_work_time(self, operation_times: List[float]) -> float:
        """Calculate median operation time."""
        return calculate_median(operation_times)

    def calculate_interval(self, sessions: list, all_waiting_user: bool) -> int:
        """Calculate appropriate loop interval.

        The monitor daemon always uses a fixed 10s interval to maintain
        high-resolution monitoring data. Variable frequency logic is only
        used by the supervisor daemon.
        """
        # Always use fast interval for consistent monitoring resolution
        return INTERVAL_FAST

    def _interruptible_sleep(self, total_seconds: int) -> None:
        """Sleep with activity signal checking."""
        chunk_size = 10
        elapsed = 0

        while elapsed < total_seconds and not self._shutdown:
            remaining = total_seconds - elapsed
            sleep_time = min(chunk_size, remaining)
            time.sleep(sleep_time)
            elapsed += sleep_time

            if check_activity_signal(self.tmux_session):
                self.log.info("User activity detected → waking up")
                self.state.current_interval = INTERVAL_FAST
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
                from .tmux_utils import TmuxHelper
                tmux = TmuxHelper()
                tmux.kill_window(self.tmux_session, session.tmux_window)
            except Exception:
                pass  # Window may already be gone
            self.session_manager.update_session_status(session.id, "terminated")
            self.log.info(f"Auto-archived done agent: {session.name}")

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
            self.session_manager.update_session(
                session.id,
                report_status="failure",
                report_reason="Oversight timeout expired",
            )
            self.session_manager.update_session_status(session.id, "done")
            self.log.info(f"[{session.name}] Oversight timeout expired, marked done")

    def _publish_state(self, session_states: List[SessionDaemonState]) -> None:
        """Publish current state to JSON file."""
        now = datetime.now()

        # Update presence state
        presence_state, presence_idle, _ = self.presence.get_current_state()

        self.state.last_loop_time = now.isoformat()
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
            from .web_api import get_status_data, get_timeline_data

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
                    self.log.debug(f"Relay push OK")
                else:
                    self.state.relay_last_status = "error"
                    self.log.warn(f"Relay push failed: HTTP {resp.status}")

        except urllib.error.URLError as e:
            self.state.relay_last_status = "error"
            self.log.warn(f"Relay push failed: {e.reason}")
        except Exception as e:
            self.state.relay_last_status = "error"
            self.log.warn(f"Relay push error: {e}")

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
        self.log.info(f"Presence tracking: {'available' if self.presence.available else 'unavailable (non-macOS)'}")

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

                # Get sessions belonging to this tmux session only
                all_sessions = self.session_manager.list_sessions()
                sessions = [s for s in all_sessions if s.tmux_session == self.tmux_session]

                # Fast session ID detection every 10s (#116)
                # Ensures active_claude_session_id updates promptly after /clear
                if should_sync_stats(self._last_session_id_sync, now, self._session_id_sync_interval):
                    for session in sessions:
                        self.sync_session_id(session)
                    self._last_session_id_sync = now

                # Full stats sync every 60s (heavier I/O)
                # This ensures the first loop has accurate data (fixes #103)
                if should_sync_stats(self._last_stats_sync, now, self._stats_sync_interval):
                    for session in sessions:
                        self.sync_claude_code_stats(session)
                    self._last_stats_sync = now

                # Send heartbeats before status detection (#171)
                self._heartbeat_triggered_sessions = self.check_and_send_heartbeats(sessions)
                # Add newly triggered sessions to persistent heartbeat tracking
                self._sessions_running_from_heartbeat.update(self._heartbeat_triggered_sessions)
                # Track pending heartbeat starts for timeline marker
                self._heartbeat_start_pending.update(self._heartbeat_triggered_sessions)

                # Detect status and track stats for each session
                session_states = []
                all_waiting_user = True

                for session in sessions:
                    # Skip sessions already known to be terminated/done —
                    # avoids a wasted tmux call and prevents desync where
                    # detect_status returns waiting_user for a gone window.
                    if session.status == "terminated":
                        status, activity = STATUS_TERMINATED, "Session terminated"
                    elif session.status == "done":
                        status, activity = STATUS_DONE, "Completed"
                    else:
                        # Detect status - dispatches per-session via dispatcher (#5)
                        status, activity, _ = self.detector.detect_status(session)

                    # Clear heartbeat tracking when session stops running
                    if status != STATUS_RUNNING and session.id in self._sessions_running_from_heartbeat:
                        self._sessions_running_from_heartbeat.discard(session.id)
                        self._heartbeat_start_pending.discard(session.id)

                    # Refresh git context (branch may have changed)
                    self.session_manager.refresh_git_context(session.id)

                    # Update current task in session
                    self.session_manager.update_stats(
                        session.id,
                        current_task=activity[:100] if activity else ""
                    )

                    # Reload session to get fresh stats
                    session = self.session_manager.get_session(session.id)
                    if session is None:
                        continue

                    # Track stats and build state
                    # Use "asleep" status if session is marked as sleeping (#68)
                    # Use "running_heartbeat" if running due to heartbeat trigger (#171)
                    if session.is_asleep:
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

                    # Persist terminated status so future loops skip detect_status
                    if effective_status == STATUS_TERMINATED and session.status != "terminated":
                        self.session_manager.update_session_status(session.id, "terminated")

                    session_state = self.track_session_stats(session, effective_status)
                    session_state.current_activity = activity
                    session_states.append(session_state)

                    # Log status history to session-specific file
                    log_agent_status(session.name, effective_status, activity, history_file=self.history_path)

                    # Track if any session is not waiting for user
                    if status != "waiting_user":
                        all_waiting_user = False

                # Clean up stale entries for deleted sessions
                current_session_ids = {s.id for s in sessions}
                stale_ids = set(self.operation_start_times.keys()) - current_session_ids
                for stale_id in stale_ids:
                    del self.operation_start_times[stale_id]
                stale_ids = set(self.previous_states.keys()) - current_session_ids
                for stale_id in stale_ids:
                    del self.previous_states[stale_id]

                # Calculate interval
                interval = self.calculate_interval(sessions, all_waiting_user)
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
                if self.state.loop_count % 60 == 0:
                    self._auto_archive_done_agents(sessions)

                # Log summary
                green = sum(1 for s in session_states if s.current_status == STATUS_RUNNING)
                non_green = len(session_states) - green
                self.log.info(f"Loop #{self.state.loop_count}: {len(sessions)} sessions ({green} green, {non_green} non-green), interval={interval}s")

                # Sleep
                self._interruptible_sleep(interval)

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
