#!/usr/bin/env python3
"""
Supervisor Daemon - Claude orchestration for Overcode.

This daemon handles:
- Launching daemon claude when sessions need attention
- Waiting for daemon claude to complete
- Tracking interventions and steers

The Supervisor Daemon reads session status from the Monitor Daemon's
published state (MonitorDaemonState) rather than detecting status directly.

Prerequisites:
- Monitor Daemon must be running (publishes session state)

Architecture:
    Monitor Daemon (metrics) → monitor_daemon_state.json → Supervisor Daemon (claude)

TODO: Add unit tests (currently 0% coverage)
TODO: Extract _send_prompt_to_window to a shared tmux utilities module
(duplicated in launcher.py)
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .daemon_logging import SupervisorDaemonLogger
from .daemon_utils import create_daemon_helpers
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
    PATHS,
    ensure_session_dir,
    get_supervisor_daemon_pid_path,
    get_supervisor_log_path,
    get_supervisor_stats_path,
)
from .status_constants import (
    STATUS_RUNNING,
    STATUS_WAITING_USER,
    get_status_emoji,
)
from .tmux_manager import TmuxManager
from .history_reader import encode_project_path, read_token_usage_from_session_file


@dataclass
class SupervisorStats:
    """Persistent stats for supervisor daemon token tracking.

    Tracks cumulative tokens used by daemon claude across restarts.
    """

    supervisor_launches: int = 0
    supervisor_tokens: int = 0           # input + output
    supervisor_input_tokens: int = 0
    supervisor_output_tokens: int = 0
    supervisor_cache_tokens: int = 0     # creation + read
    last_sync_time: Optional[str] = None
    seen_session_ids: List[str] = field(default_factory=list)

    # Daemon Claude run tracking
    supervisor_claude_running: bool = False
    supervisor_claude_started_at: Optional[str] = None  # ISO timestamp
    supervisor_claude_total_run_seconds: float = 0.0   # Cumulative run time

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "supervisor_launches": self.supervisor_launches,
            "supervisor_tokens": self.supervisor_tokens,
            "supervisor_input_tokens": self.supervisor_input_tokens,
            "supervisor_output_tokens": self.supervisor_output_tokens,
            "supervisor_cache_tokens": self.supervisor_cache_tokens,
            "last_sync_time": self.last_sync_time,
            "seen_session_ids": self.seen_session_ids,
            "supervisor_claude_running": self.supervisor_claude_running,
            "supervisor_claude_started_at": self.supervisor_claude_started_at,
            "supervisor_claude_total_run_seconds": self.supervisor_claude_total_run_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SupervisorStats":
        """Create from dictionary."""
        return cls(
            supervisor_launches=data.get("supervisor_launches", 0),
            supervisor_tokens=data.get("supervisor_tokens", 0),
            supervisor_input_tokens=data.get("supervisor_input_tokens", 0),
            supervisor_output_tokens=data.get("supervisor_output_tokens", 0),
            supervisor_cache_tokens=data.get("supervisor_cache_tokens", 0),
            last_sync_time=data.get("last_sync_time"),
            seen_session_ids=data.get("seen_session_ids", []),
            supervisor_claude_running=data.get("supervisor_claude_running", False),
            supervisor_claude_started_at=data.get("supervisor_claude_started_at"),
            supervisor_claude_total_run_seconds=data.get("supervisor_claude_total_run_seconds", 0.0),
        )

    def save(self, path: Path) -> None:
        """Save stats to file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: Path) -> "SupervisorStats":
        """Load stats from file, returns empty stats if file doesn't exist."""
        if not path.exists():
            return cls()
        try:
            with open(path) as f:
                data = json.load(f)
            return cls.from_dict(data)
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            return cls()


class SupervisorDaemon:
    """Background daemon that orchestrates daemon claude for non-green sessions.

    The Supervisor Daemon reads session state from the Monitor Daemon's published
    interface (MonitorDaemonState) and launches daemon claude when needed.
    """

    DAEMON_CLAUDE_WINDOW_NAME = "_daemon_claude"

    def __init__(
        self,
        tmux_session: str = None,
        session_manager: SessionManager = None,
        tmux_manager: TmuxManager = None,
        logger: SupervisorDaemonLogger = None,
    ):
        """Initialize the supervisor daemon.

        Args:
            tmux_session: Name of the tmux session to manage
            session_manager: Optional SessionManager for dependency injection
            tmux_manager: Optional TmuxManager for dependency injection
            logger: Optional SupervisorDaemonLogger for dependency injection
        """
        self.tmux_session = tmux_session or DAEMON.default_tmux_session
        self.session_manager = session_manager or SessionManager()
        self.tmux = tmux_manager or TmuxManager(self.tmux_session)

        # Ensure session directory exists
        ensure_session_dir(self.tmux_session)

        # Session-specific paths
        self.pid_path = get_supervisor_daemon_pid_path(self.tmux_session)
        self.stats_path = get_supervisor_stats_path(self.tmux_session)
        self.log_path = get_supervisor_log_path(self.tmux_session)

        # Logger with session-specific log file
        self.log = logger or SupervisorDaemonLogger(log_file=self.log_path)

        # Load persistent supervisor stats
        self.supervisor_stats = SupervisorStats.load(self.stats_path)

        # Daemon claude tracking
        self.daemon_claude_window: Optional[int] = None
        self.daemon_claude_launch_time: Optional[datetime] = None

        # State tracking
        self.loop_count = 0
        self.daemon_claude_launches = 0
        self.status = "starting"
        self.started_at: Optional[datetime] = None

    # =========================================================================
    # Daemon Claude Management
    # =========================================================================

    def is_daemon_claude_running(self) -> bool:
        """Check if daemon claude is still running."""
        if self.daemon_claude_window is None:
            return False
        return self.tmux.window_exists(self.daemon_claude_window)

    def is_daemon_claude_done(self) -> bool:
        """Check if daemon claude has finished its task.

        Returns True if:
        - Window doesn't exist (closed/crashed)
        - Window shows empty prompt AND no active work indicators
        """
        if not self.is_daemon_claude_running():
            return True

        try:
            result = subprocess.run(
                [
                    "tmux", "capture-pane",
                    "-t", f"{self.tmux_session}:{self.daemon_claude_window}",
                    "-p",
                    "-S", "-30",
                ],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode != 0:
                return True

            content = result.stdout

            # Active work indicators
            active_indicators = [
                '· ',
                'Running…',
                '(esc to interrupt',
                '✽',
            ]
            for indicator in active_indicators:
                if indicator in content:
                    return False

            # Check for tool calls without results
            lines = content.split('\n')
            for i, line in enumerate(lines):
                if '⏺' in line and '(' in line:
                    remaining = '\n'.join(lines[i+1:])
                    if '⎿' not in remaining:
                        return False

            # Check for empty prompt
            last_lines = [l.strip() for l in lines[-8:] if l.strip()]
            for line in last_lines:
                if line == '>' or line == '›':
                    return True

            return False

        except subprocess.TimeoutExpired:
            return False
        except subprocess.SubprocessError:
            return False

    def _has_daemon_claude_started(self) -> bool:
        """Check if daemon claude has started working."""
        if not self.is_daemon_claude_running():
            return False

        try:
            result = subprocess.run(
                [
                    "tmux", "capture-pane",
                    "-t", f"{self.tmux_session}:{self.daemon_claude_window}",
                    "-p",
                    "-S", "-30",
                ],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode != 0:
                return False

            content = result.stdout
            activity_indicators = ['⏺', 'Read(', 'Write(', 'Edit(', 'Bash(', 'Grep(', 'Glob(']
            for indicator in activity_indicators:
                if indicator in content:
                    return True

            return False

        except subprocess.SubprocessError:
            return False

    def wait_for_daemon_claude(
        self,
        timeout: int = None,
        poll_interval: int = None
    ) -> bool:
        """Wait for daemon claude to complete its task.

        Args:
            timeout: Max seconds to wait (default from settings)
            poll_interval: Seconds between checks (default from settings)

        Returns:
            True if daemon claude completed, False if timed out
        """
        timeout = timeout or DAEMON.daemon_claude_timeout
        poll_interval = poll_interval or DAEMON.daemon_claude_poll

        if not self.is_daemon_claude_running():
            return True

        self.log.info(f"Waiting for daemon claude to complete (timeout {timeout}s)...")
        start_time = time.time()
        has_seen_activity = False

        while time.time() - start_time < timeout:
            self.capture_daemon_claude_output()

            if not has_seen_activity:
                has_seen_activity = self._has_daemon_claude_started()
                if has_seen_activity:
                    self.log.info("Daemon claude started working...")

            if has_seen_activity and self.is_daemon_claude_done():
                elapsed = int(time.time() - start_time)
                self.log.success(f"Daemon claude completed in {elapsed}s")
                return True

            time.sleep(poll_interval)

        self.log.warn(f"Daemon claude timed out after {timeout}s")
        return False

    def kill_daemon_claude(self) -> None:
        """Kill daemon claude window if it exists."""
        if self.daemon_claude_window is not None and self.tmux.window_exists(self.daemon_claude_window):
            self.log.info(f"Killing daemon claude window {self.daemon_claude_window}")
            self.tmux.kill_window(self.daemon_claude_window)
        self.daemon_claude_window = None

    def cleanup_stale_daemon_claudes(self) -> None:
        """Clean up any orphaned daemon claude windows."""
        if self.daemon_claude_window is not None and not self.tmux.window_exists(self.daemon_claude_window):
            self.log.info(f"Daemon claude window {self.daemon_claude_window} no longer exists")
            self.daemon_claude_window = None

        windows = self.tmux.list_windows()
        for window in windows:
            if window['name'] == self.DAEMON_CLAUDE_WINDOW_NAME:
                window_idx = int(window['index'])
                if self.daemon_claude_window != window_idx:
                    self.log.info(f"Killing orphaned daemon claude window {window_idx}")
                    self.tmux.kill_window(window_idx)

    def capture_daemon_claude_output(self) -> None:
        """Capture and log output from daemon claude window."""
        if not self.is_daemon_claude_running():
            return

        try:
            result = subprocess.run(
                [
                    "tmux", "capture-pane",
                    "-t", f"{self.tmux_session}:{self.daemon_claude_window}",
                    "-p",
                    "-S", "-50",
                ],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                lines = [line for line in result.stdout.split('\n') if line.strip()]
                if lines:
                    self.log.daemon_claude_output(lines)

        except subprocess.SubprocessError:
            pass

    # =========================================================================
    # Intervention Tracking
    # =========================================================================

    def count_interventions_from_log(self, session_names: List[str]) -> Dict[str, int]:
        """Count interventions per session from supervisor log since daemon claude launch.

        Args:
            session_names: List of session names to check for

        Returns:
            Dict mapping session name to intervention count
        """
        if not self.daemon_claude_launch_time:
            return {}

        log_path = self.log_path
        if not log_path.exists():
            return {}

        counts: Dict[str, int] = {}
        session_set = set(session_names)

        action_phrases = [
            "approved",
            "rejected",
            "sent ",
            "provided",
            "unblocked",
        ]

        no_action_phrases = [
            "no intervention needed",
            "no action needed",
        ]

        try:
            with open(log_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        if ": " not in line:
                            continue
                        timestamp_part = line.split(": ")[0]
                        entry_time = None
                        for fmt in ["%a %d %b %Y %H:%M:%S %Z", "%a  %d %b %Y %H:%M:%S %Z"]:
                            try:
                                entry_time = datetime.strptime(timestamp_part.strip(), fmt)
                                break
                            except ValueError:
                                continue
                        if entry_time is None:
                            continue
                    except (ValueError, IndexError):
                        continue

                    if entry_time < self.daemon_claude_launch_time:
                        continue

                    for name in session_set:
                        if f"{name} - " in line:
                            line_lower = line.lower()
                            if any(phrase in line_lower for phrase in no_action_phrases):
                                break
                            if any(phrase in line_lower for phrase in action_phrases):
                                counts[name] = counts.get(name, 0) + 1
                            break

        except IOError:
            pass

        return counts

    def update_intervention_counts(self, session_names: List[str]) -> None:
        """Update steers_count for sessions based on supervisor log interventions."""
        counts = self.count_interventions_from_log(session_names)
        if not counts:
            return

        sessions = self.session_manager.list_sessions()
        session_by_name = {s.name: s for s in sessions}

        for name, intervention_count in counts.items():
            if name in session_by_name:
                session = session_by_name[name]
                current_stats = session.stats
                self.session_manager.update_stats(
                    session.id,
                    steers_count=current_stats.steers_count + intervention_count,
                )
                self.log.info(f"[{name}] +{intervention_count} daemon interventions")

    def _mark_daemon_claude_stopped(self) -> None:
        """Mark daemon claude as stopped and accumulate run time."""
        if self.supervisor_stats.supervisor_claude_running:
            # Calculate run duration
            if self.supervisor_stats.supervisor_claude_started_at:
                try:
                    started_at = datetime.fromisoformat(self.supervisor_stats.supervisor_claude_started_at)
                    run_seconds = (datetime.now() - started_at).total_seconds()
                    self.supervisor_stats.supervisor_claude_total_run_seconds += run_seconds
                except (ValueError, TypeError):
                    pass

            self.supervisor_stats.supervisor_claude_running = False
            self.supervisor_stats.supervisor_claude_started_at = None
            self.supervisor_stats.save(self.stats_path)

    def _sync_daemon_claude_tokens(self) -> None:
        """Sync token usage from daemon claude's Claude Code history.

        Reads token usage from Claude Code's session files for the ~/.overcode/
        working directory and updates the persistent supervisor stats.
        """
        overcode_dir = Path.home() / ".overcode"
        encoded_path = encode_project_path(str(overcode_dir))
        projects_dir = Path.home() / ".claude" / "projects" / encoded_path

        if not projects_dir.exists():
            return

        now = datetime.now()

        # Find all session files
        try:
            session_files = list(projects_dir.glob("*.jsonl"))
        except OSError:
            return

        new_tokens = 0
        new_input = 0
        new_output = 0
        new_cache = 0
        new_sessions = []

        for session_file in session_files:
            session_id = session_file.stem
            if session_id in self.supervisor_stats.seen_session_ids:
                continue

            # Read tokens from this new session
            try:
                usage = read_token_usage_from_session_file(session_file)
                input_tokens = usage.get("input_tokens", 0)
                output_tokens = usage.get("output_tokens", 0)
                cache_creation = usage.get("cache_creation_tokens", 0)
                cache_read = usage.get("cache_read_tokens", 0)

                new_input += input_tokens
                new_output += output_tokens
                new_cache += cache_creation + cache_read
                new_tokens += input_tokens + output_tokens
                new_sessions.append(session_id)

            except (OSError, IOError, ValueError):
                continue

        if new_tokens > 0:
            self.supervisor_stats.supervisor_input_tokens += new_input
            self.supervisor_stats.supervisor_output_tokens += new_output
            self.supervisor_stats.supervisor_cache_tokens += new_cache
            self.supervisor_stats.supervisor_tokens += new_tokens
            self.supervisor_stats.seen_session_ids.extend(new_sessions)
            self.supervisor_stats.last_sync_time = now.isoformat()
            self.supervisor_stats.save(self.stats_path)

            self.log.info(f"Daemon claude tokens: +{new_tokens} ({new_input} in, {new_output} out)")

    # =========================================================================
    # Daemon Claude Launch
    # =========================================================================

    def build_daemon_claude_context(
        self,
        non_green_sessions: List[SessionDaemonState]
    ) -> str:
        """Build initial context for daemon claude."""
        context_parts = []

        context_parts.append("You are the Overcode daemon claude agent.")
        context_parts.append("Your mission: Make all RED/YELLOW/ORANGE sessions GREEN.")
        context_parts.append("")
        context_parts.append(f"TMUX SESSION: {self.tmux_session}")
        context_parts.append(f"Sessions needing attention: {len(non_green_sessions)}")
        context_parts.append("")

        for session in non_green_sessions:
            emoji = get_status_emoji(session.current_status)
            context_parts.append(f"{emoji} {session.name} (window {session.tmux_window})")
            if session.standing_instructions:
                context_parts.append(f"   Autopilot: {session.standing_instructions}")
            else:
                context_parts.append(f"   No autopilot instructions set")
            if session.repo_name:
                context_parts.append(f"   Repo: {session.repo_name}")
            context_parts.append("")

        context_parts.append("Read the daemon claude skill for how to control sessions via tmux.")
        context_parts.append("Start by reading ~/.overcode/sessions/sessions.json to see full state.")
        context_parts.append("Then check each non-green session and help them make progress.")

        return "\n".join(context_parts)

    def _send_prompt_to_window(self, window_index: int, prompt: str) -> bool:
        """Send a large prompt to a tmux window via load-buffer/paste-buffer."""
        lines = prompt.split('\n')
        batch_size = 10

        for i in range(0, len(lines), batch_size):
            batch = lines[i:i + batch_size]
            text = '\n'.join(batch)
            if i + batch_size < len(lines):
                text += '\n'

            try:
                with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
                    temp_path = f.name
                    f.write(text)

                subprocess.run(['tmux', 'load-buffer', temp_path], timeout=5, check=True)
                subprocess.run([
                    'tmux', 'paste-buffer', '-t',
                    f"{self.tmux.session_name}:{window_index}"
                ], timeout=5, check=True)
            except subprocess.SubprocessError as e:
                self.log.error(f"Failed to send prompt batch: {e}")
                return False
            finally:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

            time.sleep(0.1)

        # Send Enter to submit
        subprocess.run([
            'tmux', 'send-keys', '-t',
            f"{self.tmux.session_name}:{window_index}",
            '', 'Enter'
        ])

        return True

    def launch_daemon_claude(self, non_green_sessions: List[SessionDaemonState]) -> bool:
        """Launch daemon claude to handle non-green sessions.

        Returns:
            True if launched successfully, False otherwise
        """
        context = self.build_daemon_claude_context(non_green_sessions)

        # Get skill content
        skill_path = Path(__file__).parent / "daemon_claude_skill.md"
        try:
            with open(skill_path) as f:
                skill_content = f.read()
        except IOError as e:
            self.log.error(f"Failed to read daemon claude skill: {e}")
            return False

        full_prompt = f"{skill_content}\n\n---\n\n{context}"

        # Ensure tmux session exists
        if not self.tmux.ensure_session():
            self.log.error(f"Failed to create tmux session '{self.tmux.session_name}'")
            return False

        # Create window
        window_index = self.tmux.create_window(
            self.DAEMON_CLAUDE_WINDOW_NAME,
            str(Path.home() / '.overcode')
        )
        if window_index is None:
            self.log.error("Failed to create daemon claude window")
            return False

        self.daemon_claude_window = window_index
        self.daemon_claude_launch_time = datetime.now()

        # Start Claude with auto-permissions
        claude_cmd = "claude code --dangerously-skip-permissions"
        if not self.tmux.send_keys(window_index, claude_cmd, enter=True):
            self.log.error("Failed to start Claude in daemon claude window")
            return False

        # Wait for Claude startup
        time.sleep(3.0)

        # Send prompt
        return self._send_prompt_to_window(window_index, full_prompt)

    # =========================================================================
    # Main Loop
    # =========================================================================

    def get_non_green_sessions(
        self,
        monitor_state: MonitorDaemonState
    ) -> List[SessionDaemonState]:
        """Get sessions that are not in running state from monitor daemon state."""
        return [
            s for s in monitor_state.sessions
            if s.current_status != STATUS_RUNNING and s.name != 'daemon_claude'
        ]

    def wait_for_monitor_daemon(self, timeout: int = 30, poll_interval: int = 2) -> bool:
        """Wait for monitor daemon to be running.

        Args:
            timeout: Max seconds to wait
            poll_interval: Seconds between checks

        Returns:
            True if monitor daemon is running, False if timed out
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            state = get_monitor_daemon_state(self.tmux_session)
            if state is not None and not state.is_stale():
                return True
            time.sleep(poll_interval)
        return False

    def run(self, check_interval: int = None):
        """Main supervisor daemon loop.

        Args:
            check_interval: Override check interval (default from settings)
        """
        check_interval = check_interval or DAEMON.interval_fast

        # Atomically check if already running and acquire lock
        # This prevents TOCTOU race conditions that could cause multiple daemons
        acquired, existing_pid = acquire_daemon_lock(self.pid_path)
        if not acquired:
            if existing_pid:
                self.log.error(f"Supervisor daemon already running (PID {existing_pid})")
            else:
                self.log.error("Could not acquire daemon lock (another daemon may be starting)")
            sys.exit(1)

        self.log.section("Supervisor Daemon")
        self.log.info(f"PID: {os.getpid()}")
        self.log.info(f"Tmux session: {self.tmux_session}")
        self.log.info(f"Check interval: {check_interval}s")

        # Setup signal handlers for graceful shutdown
        def handle_shutdown(signum, frame):
            self.log.info("Shutdown signal received")
            self._shutdown = True

        signal.signal(signal.SIGTERM, handle_shutdown)
        signal.signal(signal.SIGINT, handle_shutdown)
        self._shutdown = False

        # Wait for monitor daemon
        self.log.info("Waiting for Monitor Daemon...")
        if not self.wait_for_monitor_daemon():
            self.log.error("Monitor Daemon not running. Start it first with: overcode monitor-daemon start")
            remove_pid_file(self.pid_path)
            sys.exit(1)
        self.log.success("Monitor Daemon connected")

        self.started_at = datetime.now()
        self.status = "active"

        try:
            while not self._shutdown:
                self.loop_count += 1

                # Cleanup orphaned daemon claudes
                self.cleanup_stale_daemon_claudes()

                # Read state from monitor daemon
                monitor_state = get_monitor_daemon_state(self.tmux_session)
                if monitor_state is None or monitor_state.is_stale():
                    self.log.warn("Monitor Daemon state stale, waiting...")
                    self.status = "waiting_monitor"
                    time.sleep(check_interval)
                    continue

                # Get non-green sessions
                non_green = self.get_non_green_sessions(monitor_state)
                total = len(monitor_state.sessions)
                green_count = total - len(non_green)

                self.log.status_summary(
                    total=total,
                    green=green_count,
                    non_green=len(non_green),
                    loop=self.loop_count
                )

                # Check if all non-green are waiting for user
                all_waiting_user = (
                    non_green and
                    all(s.current_status == STATUS_WAITING_USER for s in non_green)
                )

                # Check if any have standing instructions
                any_has_instructions = any(
                    s.standing_instructions for s in non_green
                )

                if non_green:
                    if all_waiting_user and not any_has_instructions:
                        self.status = "waiting_user"
                        self.log.warn("All sessions waiting for user input (no instructions)")
                    else:
                        # Launch daemon claude if not running
                        if not self.is_daemon_claude_running():
                            reason = "with instructions" if any_has_instructions else "non-user-blocked"
                            self.log.info(f"Launching daemon claude for {len(non_green)} session(s) ({reason})...")
                            if self.launch_daemon_claude(non_green):
                                self.daemon_claude_launches += 1
                                self.supervisor_stats.supervisor_launches += 1
                                # Track daemon claude run start
                                self.supervisor_stats.supervisor_claude_running = True
                                self.supervisor_stats.supervisor_claude_started_at = datetime.now().isoformat()
                                self.supervisor_stats.save(self.stats_path)
                                self.status = "supervising"
                                self.log.success(f"Daemon claude launched in window {self.daemon_claude_window}")

                        # Wait for daemon claude
                        if self.is_daemon_claude_running():
                            completed = self.wait_for_daemon_claude()
                            self.capture_daemon_claude_output()

                            # Track daemon claude run end
                            self._mark_daemon_claude_stopped()

                            if completed:
                                self.kill_daemon_claude()
                                session_names = [s.name for s in non_green]
                                self.update_intervention_counts(session_names)
                                self._sync_daemon_claude_tokens()
                            else:
                                self.log.warn("Daemon claude still working, continuing...")
                else:
                    if total > 0:
                        self.status = "idle"
                        self.log.success("All sessions GREEN")
                    else:
                        self.status = "no_agents"

                time.sleep(check_interval)

        except Exception as e:
            self.log.error(f"Supervisor daemon error: {e}")
            raise
        finally:
            self.log.info("Supervisor daemon shutting down")
            self.status = "stopped"
            remove_pid_file(self.pid_path)


# Create PID helper functions using factory
(
    is_supervisor_daemon_running,
    get_supervisor_daemon_pid,
    stop_supervisor_daemon,
) = create_daemon_helpers(get_supervisor_daemon_pid_path, "supervisor")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Overcode Supervisor Daemon")
    parser.add_argument(
        "--session",
        default=None,
        help=f"Tmux session to manage (default: {DAEMON.default_tmux_session})"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help=f"Check interval in seconds (default: {DAEMON.interval_fast})"
    )

    args = parser.parse_args()

    daemon = SupervisorDaemon(tmux_session=args.session)
    daemon.run(check_interval=args.interval)


if __name__ == "__main__":
    main()
