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

TODO: Add unit tests (currently 0% coverage)
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
from .history_reader import get_session_stats
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
)
from .config import get_relay_config
from .status_constants import STATUS_RUNNING, STATUS_TERMINATED
from .status_detector import StatusDetector
from .status_history import log_agent_status
from .summarizer_component import SummarizerComponent, SummarizerConfig


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

    def __init__(self):
        self.available = MACOS_APIS_AVAILABLE
        self._logger: Optional[PresenceLogger] = None

        if self.available and PresenceLogger is not None:
            config = PresenceLoggerConfig()
            self._logger = PresenceLogger(config)
            self._logger.start()

    def get_current_state(self) -> tuple:
        """Get current presence state.

        Returns:
            Tuple of (state, idle_seconds, locked) or (None, None, None) if unavailable
        """
        if not self.available or get_current_presence_state is None:
            return None, None, None

        try:
            return get_current_presence_state()
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
        self.status_detector = status_detector or StatusDetector(tmux_session)

        # Presence tracking (graceful degradation)
        self.presence = PresenceComponent()

        # Summarizer component (graceful degradation if no API key)
        self.summarizer = SummarizerComponent(
            tmux_session=tmux_session,
            config=SummarizerConfig(enabled=False),  # Off by default, enable via CLI
        )

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

        # Stats sync throttling
        self._last_stats_sync = datetime.now()
        self._stats_sync_interval = 60  # seconds

        # Relay configuration (for pushing state to cloud)
        self._relay_config = get_relay_config()
        self._last_relay_push = datetime.min
        if self._relay_config:
            self.log.info(f"Relay enabled: {self._relay_config['url']}")

        # Shutdown flag
        self._shutdown = False

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
        was_running = prev_status == STATUS_RUNNING
        is_running = status == STATUS_RUNNING

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
        return SessionDaemonState(
            session_id=session_id,
            name=session.name,
            tmux_window=session.tmux_window,
            current_status=status,
            current_activity=stats.current_task or "",
            status_since=stats.state_since,
            green_time_seconds=stats.green_time_seconds,
            non_green_time_seconds=stats.non_green_time_seconds,
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
        )

    def _update_state_time(self, session, status: str, now: datetime) -> None:
        """Update green_time_seconds and non_green_time_seconds."""
        session_id = session.id
        current_stats = session.stats

        # Get last recorded time
        last_time = self.last_state_times.get(session_id)
        if last_time is None:
            # First observation after daemon (re)start - use last_time_accumulation
            # to avoid re-adding time that was already accumulated before restart
            if current_stats.last_time_accumulation:
                try:
                    last_time = datetime.fromisoformat(current_stats.last_time_accumulation)
                except ValueError:
                    last_time = now
            elif current_stats.state_since:
                # Fallback for sessions without last_time_accumulation
                try:
                    last_time = datetime.fromisoformat(current_stats.state_since)
                except ValueError:
                    last_time = now
            else:
                last_time = now
            self.last_state_times[session_id] = last_time
            return  # Don't accumulate on first observation

        # Calculate elapsed time
        elapsed = (now - last_time).total_seconds()
        if elapsed <= 0:
            return

        # Accumulate time based on state
        green_time = current_stats.green_time_seconds
        non_green_time = current_stats.non_green_time_seconds

        if status == STATUS_RUNNING:
            green_time += elapsed
        elif status != STATUS_TERMINATED:
            # Only count non-green time for non-terminated states
            non_green_time += elapsed
        # else: terminated - don't accumulate time

        # INVARIANT CHECK: accumulated time should never exceed uptime
        # This catches bugs like multiple daemons running simultaneously
        if session.start_time:
            try:
                session_start = datetime.fromisoformat(session.start_time)
                max_allowed = (now - session_start).total_seconds()
                total_accumulated = green_time + non_green_time

                if total_accumulated > max_allowed * 1.1:  # 10% tolerance for timing jitter
                    # Reset to sane values based on ratio
                    ratio = max_allowed / total_accumulated if total_accumulated > 0 else 1.0
                    green_time = green_time * ratio
                    non_green_time = non_green_time * ratio
                    self.log.warn(
                        f"[{session.name}] Time tracking reset: "
                        f"accumulated {total_accumulated/3600:.1f}h > uptime {max_allowed/3600:.1f}h"
                    )
            except (ValueError, TypeError):
                pass

        # Update state tracking
        prev_status = self.previous_states.get(session_id, status)
        state_since = current_stats.state_since
        if prev_status != status:
            state_since = now.isoformat()
        elif not state_since:
            # Initialize state_since if never set (e.g., new session)
            state_since = now.isoformat()

        # Save to session manager
        self.session_manager.update_stats(
            session_id,
            current_state=status,
            state_since=state_since,
            green_time_seconds=green_time,
            non_green_time_seconds=non_green_time,
            last_time_accumulation=now.isoformat(),
        )

        self.last_state_times[session_id] = now

    def sync_claude_code_stats(self, session) -> None:
        """Sync token/interaction stats from Claude Code history files."""
        try:
            stats = get_session_stats(session)
            if stats is None:
                return

            now = datetime.now()
            total_tokens = (
                stats.input_tokens +
                stats.output_tokens +
                stats.cache_creation_tokens +
                stats.cache_read_tokens
            )

            # Estimate cost
            cost_estimate = (
                (stats.input_tokens / 1_000_000) * 3.0 +
                (stats.output_tokens / 1_000_000) * 15.0 +
                (stats.cache_creation_tokens / 1_000_000) * 3.75 +
                (stats.cache_read_tokens / 1_000_000) * 0.30
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
        if not operation_times:
            return 0.0
        sorted_times = sorted(operation_times)
        n = len(sorted_times)
        if n % 2 == 0:
            return (sorted_times[n // 2 - 1] + sorted_times[n // 2]) / 2
        return sorted_times[n // 2]

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

        # Update summarizer stats
        self.state.summarizer_available = self.summarizer.available
        self.state.summarizer_enabled = self.summarizer.enabled
        self.state.summarizer_calls = self.summarizer.total_calls
        # Estimate cost: ~$0.0007 per call (4K input tokens + 150 output tokens)
        self.state.summarizer_cost_usd = round(self.summarizer.total_calls * 0.0007, 4)

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

                # Get all sessions
                sessions = self.session_manager.list_sessions()

                # Detect status and track stats for each session
                session_states = []
                all_waiting_user = True

                for session in sessions:
                    # Detect status
                    status, activity, _ = self.status_detector.detect_status(session)

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
                    session_state = self.track_session_stats(session, status)
                    session_state.current_activity = activity
                    session_states.append(session_state)

                    # Log status history to session-specific file
                    log_agent_status(session.name, status, activity, history_file=self.history_path)

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

                # Sync Claude Code stats periodically (git context is refreshed every loop above)
                if (now - self._last_stats_sync).total_seconds() >= self._stats_sync_interval:
                    for session in sessions:
                        self.sync_claude_code_stats(session)
                    self._last_stats_sync = now

                # Update summaries (if enabled)
                summaries = self.summarizer.update(sessions)
                for session_state in session_states:
                    summary = summaries.get(session_state.session_id)
                    if summary:
                        session_state.activity_summary = summary.text
                        session_state.activity_summary_updated = summary.updated_at

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
            self.summarizer.stop()
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
