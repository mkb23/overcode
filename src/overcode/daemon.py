#!/usr/bin/env python3
"""
DEPRECATED: Legacy Overcode Daemon

This module is deprecated. Use instead:
- overcode.monitor_daemon - For metrics tracking and status monitoring
- overcode.supervisor_daemon - For autonomous Claude orchestration

The CLI commands 'overcode daemon start/stop/status' are also deprecated.
Use 'overcode monitor-daemon' and 'overcode supervisor-daemon' instead.

This file is kept for backwards compatibility with existing CLI commands
and will be removed in a future version.
"""

import os
import tempfile
import time
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict
from rich.console import Console
from rich.text import Text
from rich.theme import Theme
from .session_manager import SessionManager
from .status_detector import StatusDetector
from .tmux_manager import TmuxManager
from .pid_utils import is_process_running, get_process_pid, write_pid_file, remove_pid_file
from .status_constants import (
    STATUS_RUNNING,
    STATUS_WAITING_USER,
    get_status_color,
    get_status_emoji,
)
from .daemon_state import DaemonState, MODE_MONITOR, MODE_SUPERVISE
from .status_history import log_agent_status, read_agent_status_history
from .history_reader import get_session_stats
from .settings import DAEMON, get_activity_signal_path


# Interval settings (in seconds)
INTERVAL_FAST = 10       # When active or agents working
INTERVAL_SLOW = 300      # When all agents need user input (5 min)
INTERVAL_IDLE = 3600     # When no agents at all (1 hour)

# File locations
OVERCODE_DIR = Path.home() / '.overcode'
DAEMON_STATE_FILE = OVERCODE_DIR / 'daemon_state.json'
DAEMON_LOG_FILE = OVERCODE_DIR / 'daemon.log'
DAEMON_PID_FILE = OVERCODE_DIR / 'daemon.pid'
ACTIVITY_SIGNAL_FILE = OVERCODE_DIR / 'activity_signal'
AGENT_STATUS_HISTORY_FILE = OVERCODE_DIR / 'agent_status_history.csv'


def is_daemon_running() -> bool:
    """Check if the daemon process is currently running.

    Returns True if PID file exists and process is alive.
    Can be called from other modules (e.g., TUI).
    """
    return is_process_running(DAEMON_PID_FILE)


def get_daemon_pid() -> Optional[int]:
    """Get the daemon PID if running, None otherwise."""
    return get_process_pid(DAEMON_PID_FILE)


def stop_daemon() -> bool:
    """Stop the daemon process if running.

    Returns True if daemon was stopped, False if it wasn't running.
    Also cleans up stale PID files.

    Note: Uses quick stop (just SIGTERM, no wait) for responsive CLI.
    """
    import signal

    pid = get_process_pid(DAEMON_PID_FILE)
    if pid is None:
        # Clean up stale PID file if exists
        remove_pid_file(DAEMON_PID_FILE)
        return False

    try:
        os.kill(pid, signal.SIGTERM)
        remove_pid_file(DAEMON_PID_FILE)
        return True
    except (OSError, ProcessLookupError):
        remove_pid_file(DAEMON_PID_FILE)
        return False


def signal_activity(session: str = None) -> None:
    """Signal user activity to the daemon (called by TUI on keypress).

    Creates a signal file that the daemon checks each loop.

    Args:
        session: tmux session name (default: from config)
    """
    if session is None:
        session = DAEMON.default_tmux_session
    signal_path = get_activity_signal_path(session)
    try:
        signal_path.parent.mkdir(parents=True, exist_ok=True)
        signal_path.touch()
    except OSError:
        pass  # Best effort


def check_activity_signal() -> bool:
    """Check for and consume the activity signal (called by daemon).

    Returns True if signal was present (and deletes the file).
    """
    if ACTIVITY_SIGNAL_FILE.exists():
        try:
            ACTIVITY_SIGNAL_FILE.unlink()
            return True
        except OSError:
            pass
    return False


def _write_pid_file() -> None:
    """Write current PID to file."""
    write_pid_file(DAEMON_PID_FILE)


def _remove_pid_file() -> None:
    """Remove PID file."""
    remove_pid_file(DAEMON_PID_FILE)


# Rich theme for daemon logs
DAEMON_THEME = Theme({
    "info": "cyan",
    "warn": "yellow",
    "error": "bold red",
    "success": "bold green",
    "daemon_claude": "magenta",
    "dim": "dim white",
    "highlight": "bold white",
})


class DaemonLogger:
    """Rich-based logger for daemon with pretty console output and file logging."""

    def __init__(self, log_file: Path = DAEMON_LOG_FILE):
        self.log_file = log_file
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.console = Console(theme=DAEMON_THEME, force_terminal=True)
        # Track seen lines by content to avoid duplicates when output scrolls
        self._seen_daemon_claude_lines: set = set()

    def _write_to_file(self, message: str, level: str):
        """Write plain text to log file."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] [{level}] {message}"
        with open(self.log_file, 'a') as f:
            f.write(line + '\n')

    def info(self, message: str):
        """Log info message."""
        self._write_to_file(message, "INFO")
        self.console.print(f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim] [info]INFO[/info]  {message}")

    def warn(self, message: str):
        """Log warning message."""
        self._write_to_file(message, "WARN")
        self.console.print(f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim] [warn]WARN[/warn]  {message}")

    def error(self, message: str):
        """Log error message."""
        self._write_to_file(message, "ERROR")
        self.console.print(f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim] [error]ERROR[/error] {message}")

    def success(self, message: str):
        """Log success message."""
        self._write_to_file(message, "INFO")
        self.console.print(f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim] [success]OK[/success]    {message}")

    def daemon_claude_output(self, lines: List[str]):
        """Log daemon claude output, showing only new lines.

        Uses content-based tracking (set) instead of positional comparison,
        which correctly handles terminal scrolling where lines shift up.
        """
        new_lines = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Check if we've seen this line before
            if stripped not in self._seen_daemon_claude_lines:
                new_lines.append(stripped)
                self._seen_daemon_claude_lines.add(stripped)

        # Limit the set size to prevent unbounded memory growth
        # Keep only the most recent lines by clearing old entries periodically
        if len(self._seen_daemon_claude_lines) > 500:
            # Keep lines from current capture to maintain context
            current_lines = {line.strip() for line in lines if line.strip()}
            self._seen_daemon_claude_lines = current_lines

        if new_lines:
            # Log new output with daemon claude styling
            for line in new_lines:
                self._write_to_file(f"[DAEMON_CLAUDE] {line}", "INFO")
                # Style the output based on content
                if line.startswith('✓') or 'success' in line.lower():
                    self.console.print(f"  [success]│[/success] {line}")
                elif line.startswith('✗') or 'error' in line.lower() or 'fail' in line.lower():
                    self.console.print(f"  [error]│[/error] {line}")
                elif line.startswith('>') or line.startswith('$'):
                    self.console.print(f"  [highlight]│[/highlight] {line}")
                else:
                    self.console.print(f"  [daemon_claude]│[/daemon_claude] {line}")

    def section(self, title: str):
        """Print a section divider."""
        self._write_to_file(f"=== {title} ===", "INFO")
        self.console.print()
        self.console.rule(f"[bold]{title}[/bold]", style="dim")

    def status_summary(self, total: int, green: int, non_green: int, loop: int):
        """Print a status summary line."""
        status_text = Text()
        status_text.append(f"Loop #{loop}: ", style="dim")
        status_text.append(f"{total} agents ", style="highlight")
        status_text.append("(", style="dim")
        status_text.append(f"{green} green", style="success")
        status_text.append(", ", style="dim")
        status_text.append(f"{non_green} non-green", style="warn" if non_green else "dim")
        status_text.append(")", style="dim")

        self._write_to_file(f"Loop #{loop}: {total} agents ({green} green, {non_green} non-green)", "INFO")
        self.console.print(f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim] ", end="")
        self.console.print(status_text)

    def agent_status(self, name: str, status: str, activity: str):
        """Log individual agent status."""
        style = get_status_color(status)

        self._write_to_file(f"  {name}: {status} - {activity[:60]}", "INFO")
        self.console.print(f"  [{style}]●[/{style}] [bold]{name}[/bold]: [{style}]{status}[/{style}] - [dim]{activity[:60]}[/dim]")


class Daemon:
    """Background daemon that auto-launches daemon claude when needed"""

    # Special window name for daemon's claude (not tracked in sessions.json)
    DAEMON_CLAUDE_WINDOW_NAME = "_daemon_claude"

    # Rough cost estimates per interaction (USD)
    # Based on typical Claude Code interaction costs
    COST_PER_INTERACTION = {
        "opus": 0.05,    # ~$0.05 per interaction for Opus
        "sonnet": 0.01,  # ~$0.01 per interaction for Sonnet
        "default": 0.03  # Default estimate
    }

    def __init__(
        self,
        tmux_session: str = "agents",
        session_manager: SessionManager = None,
        status_detector: StatusDetector = None,
        tmux_manager: TmuxManager = None,
        logger: "DaemonLogger" = None,
        mode: str = MODE_SUPERVISE,
    ):
        """Initialize the daemon.

        Args:
            tmux_session: Name of the tmux session to manage
            session_manager: Optional SessionManager for dependency injection (testing)
            status_detector: Optional StatusDetector for dependency injection (testing)
            tmux_manager: Optional TmuxManager for dependency injection (testing)
            logger: Optional DaemonLogger for dependency injection (testing)
            mode: Daemon mode - MODE_MONITOR (stats only) or MODE_SUPERVISE (full)
        """
        self.tmux_session = tmux_session
        self.session_manager = session_manager if session_manager else SessionManager()
        self.status_detector = status_detector if status_detector else StatusDetector(tmux_session)
        self.tmux = tmux_manager if tmux_manager else TmuxManager(tmux_session)
        self.daemon_claude_window: Optional[int] = None
        self.state = DaemonState()
        self.state.mode = mode
        self.last_controller_check: Optional[datetime] = None
        self.controller_line_count = 0
        self.log = logger if logger else DaemonLogger()
        # Track previous states to detect transitions
        self.previous_states: Dict[str, str] = {}  # session_id -> last known status
        # Track when operations started (for timing)
        self.operation_start_times: Dict[str, datetime] = {}  # session_id -> when went non-running
        # Track when daemon claude was launched (for counting interventions)
        self.daemon_claude_launch_time: Optional[datetime] = None
        # Track last state change time per session for state time tracking
        self.last_state_times: Dict[str, datetime] = {}  # session_id -> last state change time

    def track_session_stats(self, session, status: str) -> None:
        """Track session state transitions and update stats.

        Called for each session on each daemon loop to detect:
        - Transitions from non-running to running (= 1 interaction completed)
        - Operation duration (time spent waiting)
        - State time accumulation (green_time_seconds, non_green_time_seconds)
        """
        session_id = session.id
        now = datetime.now()

        # Get previous status (default to current if first time seeing this session)
        prev_status = self.previous_states.get(session_id, status)

        # Track state time accumulation
        self._update_state_time(session, status, now)

        # Detect state transitions
        was_running = prev_status == STATUS_RUNNING
        is_running = status == STATUS_RUNNING

        # Session went from running to waiting (operation started)
        if was_running and not is_running:
            self.operation_start_times[session_id] = now

        # Session went from waiting back to running (operation completed)
        if not was_running and is_running:
            # Calculate operation time if we have a start time
            op_duration = None
            if session_id in self.operation_start_times:
                start_time = self.operation_start_times[session_id]
                op_duration = (now - start_time).total_seconds()
                del self.operation_start_times[session_id]

            # Update operation times for latency tracking (keep last 100)
            current_stats = session.stats
            op_times = list(current_stats.operation_times)
            if op_duration is not None and op_duration > 0:
                op_times.append(op_duration)
                op_times = op_times[-100:]  # Keep last 100

                # Save updated operation times
                self.session_manager.update_stats(
                    session_id,
                    operation_times=op_times,
                    last_activity=now.isoformat()
                )

                self.log.info(f"[{session.name}] Operation completed ({op_duration:.1f}s)")

        # Update previous state for next check
        self.previous_states[session_id] = status

    def _update_state_time(self, session, status: str, now: datetime) -> None:
        """Update green_time_seconds and non_green_time_seconds for a session.

        Called each loop to accumulate time in current state.
        """
        session_id = session.id
        current_stats = session.stats

        # Get last recorded time for this session
        last_time = self.last_state_times.get(session_id)
        if last_time is None:
            # First time seeing this session, initialize from state_since if available
            if current_stats.state_since:
                try:
                    last_time = datetime.fromisoformat(current_stats.state_since)
                except ValueError:
                    last_time = now
            else:
                last_time = now
            self.last_state_times[session_id] = last_time
            return  # Don't accumulate on first observation

        # Calculate time elapsed since last check
        elapsed = (now - last_time).total_seconds()
        if elapsed <= 0:
            return

        # Accumulate time based on current state
        green_time = current_stats.green_time_seconds
        non_green_time = current_stats.non_green_time_seconds

        if status == STATUS_RUNNING:
            green_time += elapsed
        else:
            non_green_time += elapsed

        # Update state tracking if state changed
        prev_status = self.previous_states.get(session_id, status)
        state_since = current_stats.state_since
        if prev_status != status:
            state_since = now.isoformat()
        elif not state_since:
            # Initialize state_since if never set (e.g., new session)
            state_since = now.isoformat()

        # Save updated times
        self.session_manager.update_stats(
            session_id,
            current_state=status,
            state_since=state_since,
            green_time_seconds=green_time,
            non_green_time_seconds=non_green_time,
        )

        # Update tracking time
        self.last_state_times[session_id] = now

    def sync_claude_code_stats(self, session) -> None:
        """Sync token/interaction stats from Claude Code history files.

        Reads from ~/.claude/projects/ to get actual token usage and
        persists to SessionStats for historical reference.
        """
        try:
            stats = get_session_stats(session)
            if stats is None:
                return

            now = datetime.now()

            # Calculate total tokens
            total_tokens = (
                stats.input_tokens +
                stats.output_tokens +
                stats.cache_creation_tokens +
                stats.cache_read_tokens
            )

            # Estimate cost (rough approximation)
            # Using ~$3/1M input, ~$15/1M output for Claude
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
            # Don't fail the loop if stats sync fails
            self.log.warn(f"Failed to sync stats for {session.name}: {e}")

    def is_daemon_claude_running(self) -> bool:
        """Check if daemon claude is still running"""
        if self.daemon_claude_window is None:
            return False

        # Check if our tracked window still exists
        return self.tmux.window_exists(self.daemon_claude_window)

    def is_daemon_claude_done(self) -> bool:
        """Check if daemon claude has finished its task (at empty prompt or gone).

        Returns True if:
        - Window doesn't exist (closed/crashed)
        - Window shows empty prompt AND no active work indicators
        """
        if not self.is_daemon_claude_running():
            return True

        # Capture pane content to check status
        try:
            result = subprocess.run(
                [
                    "tmux", "capture-pane",
                    "-t", f"{self.tmux_session}:{self.daemon_claude_window}",
                    "-p",
                    "-S", "-30",  # Last 30 lines for better context
                ],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode != 0:
                return True  # Can't capture = assume done

            content = result.stdout

            # If Claude is actively working, NOT done
            # These indicators show Claude is thinking or running tools
            active_indicators = [
                '· ',           # Thinking indicator (· Thinking…, · Combobulating…)
                'Running…',     # Tool is running
                '(esc to interrupt',  # Active work message
                '✽',            # Another thinking indicator
            ]
            for indicator in active_indicators:
                if indicator in content:
                    return False  # Still working

            # Also check for tool calls that just started (no result yet)
            # Pattern: ⏺ ToolName(...) without ⎿ result after
            lines = content.split('\n')
            for i, line in enumerate(lines):
                if '⏺' in line and '(' in line:
                    # Found a tool call - check if there's a result after it
                    remaining = '\n'.join(lines[i+1:])
                    if '⎿' not in remaining:
                        return False  # Tool call without result = still working

            # Check for empty prompt in last few lines
            last_lines = [l.strip() for l in lines[-8:] if l.strip()]
            for line in last_lines:
                # Empty Claude Code prompt (just > or › with nothing after)
                if line == '>' or line == '›':
                    return True

            return False

        except subprocess.TimeoutExpired:
            # Timeout checking status != daemon claude is done
            # Return False to keep waiting - it may still be working
            return False
        except subprocess.SubprocessError:
            # Other subprocess errors - can't determine status
            # Return False to be safe (keep waiting rather than kill)
            return False

    def wait_for_daemon_claude(self, timeout: int = 300, poll_interval: int = 5) -> bool:
        """Wait for daemon claude to complete its task.

        Args:
            timeout: Max seconds to wait (default 5 minutes)
            poll_interval: Seconds between checks (default 5s)

        Returns:
            True if daemon claude completed, False if timed out
        """
        if not self.is_daemon_claude_running():
            return True

        self.log.info(f"Waiting for daemon claude to complete (timeout {timeout}s)...")
        start_time = time.time()
        has_seen_activity = False

        while time.time() - start_time < timeout:
            # Capture and log output while waiting
            self.capture_daemon_claude_output()

            # Check if daemon claude has started working (shows tool use indicator)
            if not has_seen_activity:
                has_seen_activity = self._has_daemon_claude_started()
                if has_seen_activity:
                    self.log.info("Daemon claude started working...")

            # Only check for completion after we've seen activity
            if has_seen_activity and self.is_daemon_claude_done():
                elapsed = int(time.time() - start_time)
                self.log.success(f"Daemon claude completed in {elapsed}s")
                return True

            time.sleep(poll_interval)

        self.log.warn(f"Daemon claude timed out after {timeout}s")
        return False

    def _has_daemon_claude_started(self) -> bool:
        """Check if daemon claude has started working (shows activity indicators)."""
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

            # Look for signs Claude has started working:
            # - Tool use indicator (⏺)
            # - Thinking/processing
            # - Any substantial output beyond just the prompt
            activity_indicators = ['⏺', 'Read(', 'Write(', 'Edit(', 'Bash(', 'Grep(', 'Glob(']
            for indicator in activity_indicators:
                if indicator in content:
                    return True

            return False

        except subprocess.SubprocessError:
            return False

    def kill_daemon_claude(self) -> None:
        """Kill daemon claude window if it exists"""
        if self.daemon_claude_window is not None and self.tmux.window_exists(self.daemon_claude_window):
            self.log.info(f"Killing daemon claude window {self.daemon_claude_window}")
            self.tmux.kill_window(self.daemon_claude_window)
        self.daemon_claude_window = None

    def cleanup_stale_daemon_claudes(self) -> None:
        """Clean up any orphaned daemon claude windows.

        This handles:
        1. Our tracked daemon claude window that no longer exists
        2. Orphaned windows with the daemon claude name from previous daemon runs
        """
        # Clear our reference if the window is gone
        if self.daemon_claude_window is not None and not self.tmux.window_exists(self.daemon_claude_window):
            self.log.info(f"Daemon claude window {self.daemon_claude_window} no longer exists")
            self.daemon_claude_window = None

        # Also kill any orphaned daemon claude windows (from previous daemon runs)
        windows = self.tmux.list_windows()
        for window in windows:
            if window['name'] == self.DAEMON_CLAUDE_WINDOW_NAME:
                window_idx = int(window['index'])
                # If we're not tracking this window, it's orphaned
                if self.daemon_claude_window != window_idx:
                    self.log.info(f"Killing orphaned daemon claude window {window_idx}")
                    self.tmux.kill_window(window_idx)

    def capture_daemon_claude_output(self) -> None:
        """Capture and log output from daemon claude window."""
        if not self.is_daemon_claude_running():
            return

        try:
            # Capture the pane content
            result = subprocess.run(
                [
                    "tmux", "capture-pane",
                    "-t", f"{self.tmux_session}:{self.daemon_claude_window}",
                    "-p",  # Print to stdout
                    "-S", "-50",  # Start from 50 lines before current position
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
            pass  # Silently ignore capture errors

    def count_interventions_from_log(self, sessions: list) -> Dict[str, int]:
        """Count interventions per session from supervisor log since daemon claude launch.

        Parses ~/.overcode/supervisor.log for entries after daemon_claude_launch_time
        and counts actual interventions (not "No intervention needed" entries).

        Args:
            sessions: List of sessions to check for

        Returns:
            Dict mapping session name to intervention count
        """
        if not self.daemon_claude_launch_time:
            return {}

        log_path = Path.home() / ".overcode" / "supervisor.log"
        if not log_path.exists():
            return {}

        counts: Dict[str, int] = {}
        session_names = {s.name for s in sessions}

        # Phrases that indicate an action WAS taken
        action_phrases = [
            "approved",
            "rejected",
            "sent ",  # "sent /exit", "sent feedback"
            "provided",
            "unblocked",
        ]

        # Phrases that indicate NO action was taken (overrides action phrases)
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

                    # Parse timestamp - format: "Fri  2 Jan 2026 07:11:36 GMT: ..."
                    # Also handles "Fri  2 Jan..." (double space)
                    try:
                        if ": " not in line:
                            continue
                        timestamp_part = line.split(": ")[0]
                        # Try both single and double space after weekday
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

                    # Skip entries before daemon claude launch
                    if entry_time < self.daemon_claude_launch_time:
                        continue

                    # Check if this entry is about one of our sessions
                    for name in session_names:
                        if f"{name} - " in line:
                            line_lower = line.lower()
                            # First check if explicitly says "no action"
                            if any(phrase in line_lower for phrase in no_action_phrases):
                                break
                            # Then check if an action was taken
                            if any(phrase in line_lower for phrase in action_phrases):
                                counts[name] = counts.get(name, 0) + 1
                            break

        except IOError:
            pass

        return counts

    def update_intervention_counts(self, sessions: list) -> None:
        """Update steers_count for sessions based on supervisor log interventions.

        Called after daemon claude completes to track actual interventions.
        """
        counts = self.count_interventions_from_log(sessions)
        if not counts:
            return

        for session in sessions:
            if session.name in counts:
                intervention_count = counts[session.name]
                current_stats = session.stats
                self.session_manager.update_stats(
                    session.id,
                    steers_count=current_stats.steers_count + intervention_count,
                )
                self.log.info(f"[{session.name}] +{intervention_count} daemon interventions")

    def get_non_green_sessions(self) -> list:
        """Get all sessions that are not in running state"""
        sessions = self.session_manager.list_sessions()
        non_green = []

        for session in sessions:
            # Skip the daemon claude itself (shouldn't appear, but just in case)
            if session.name == 'daemon_claude':
                continue

            status, _, _ = self.status_detector.detect_status(session)
            if status != STATUS_RUNNING:
                non_green.append((session, status))

        return non_green

    def build_daemon_claude_context(self, non_green_sessions: list) -> str:
        """Build initial context for daemon claude"""
        context_parts = []

        context_parts.append("You are the Overcode daemon claude agent.")
        context_parts.append("Your mission: Make all RED/YELLOW/ORANGE sessions GREEN.")
        context_parts.append("")
        context_parts.append(f"TMUX SESSION: {self.tmux_session}")
        context_parts.append(f"Sessions needing attention: {len(non_green_sessions)}")
        context_parts.append("")

        for session, status in non_green_sessions:
            emoji = get_status_emoji(status)
            context_parts.append(f"{emoji} {session.name} (window {session.tmux_window})")
            if session.standing_instructions:
                context_parts.append(f"   Autopilot: {session.standing_instructions}")
            else:
                context_parts.append(f"   No autopilot instructions set")
            context_parts.append(f"   Working dir: {session.start_directory or 'unknown'}")
            context_parts.append("")

        context_parts.append("Read the daemon claude skill for how to control sessions via tmux.")
        context_parts.append("Start by reading ~/.overcode/sessions/sessions.json to see full state.")
        context_parts.append("Then check each non-green session and help them make progress.")

        return "\n".join(context_parts)

    def launch_daemon_claude(self, non_green_sessions: list):
        """Launch daemon claude to handle non-green sessions.

        This creates a non-interactive daemon claude directly in a tmux window
        WITHOUT registering it in sessions.json. It's a background worker, not a
        user-facing agent.
        """
        # Build context message
        context = self.build_daemon_claude_context(non_green_sessions)

        # Get the daemon claude skill path
        skill_path = Path(__file__).parent / "daemon_claude_skill.md"

        # Read the daemon claude skill content
        with open(skill_path) as f:
            skill_content = f.read()

        # Build full prompt with skill + context
        full_prompt = f"{skill_content}\n\n---\n\n{context}"

        # Ensure tmux session exists
        if not self.tmux.ensure_session():
            self.log.error(f"Failed to create tmux session '{self.tmux.session_name}'")
            return

        # Create window for daemon claude (uses special name prefix)
        window_index = self.tmux.create_window(
            self.DAEMON_CLAUDE_WINDOW_NAME,
            str(Path.home() / '.overcode')
        )
        if window_index is None:
            self.log.error("Failed to create daemon claude window")
            return

        self.daemon_claude_window = window_index
        self.daemon_claude_launch_time = datetime.now()

        # Start Claude with auto-permissions
        # Use dangerously-skip-permissions so daemon claude can run tmux commands
        # without prompting. This is safe because daemon claude only operates on
        # the agent sessions within the monitored tmux session.
        claude_cmd = "claude code --dangerously-skip-permissions"
        if not self.tmux.send_keys(window_index, claude_cmd, enter=True):
            self.log.error("Failed to start Claude in daemon claude window")
            return

        # Wait for Claude to start up
        time.sleep(3.0)

        # Send the prompt via tmux load-buffer/paste-buffer for large text
        self._send_prompt_to_window(window_index, full_prompt)

    def _send_prompt_to_window(self, window_index: int, prompt: str) -> bool:
        """Send a large prompt to a tmux window via load-buffer/paste-buffer."""
        import os

        lines = prompt.split('\n')
        batch_size = 10

        for i in range(0, len(lines), batch_size):
            batch = lines[i:i + batch_size]
            text = '\n'.join(batch)
            if i + batch_size < len(lines):
                text += '\n'  # Add newline between batches

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

        # Send Enter to submit the prompt
        subprocess.run([
            'tmux', 'send-keys', '-t',
            f"{self.tmux.session_name}:{window_index}",
            '', 'Enter'
        ])

        return True

    def _interruptible_sleep(self, total_seconds: int) -> None:
        """Sleep for total_seconds, but check for activity signal every 10s.

        This allows the daemon to wake up quickly when the user becomes active,
        even if we're in a long sleep interval (e.g., 1 hour idle).
        """
        chunk_size = 10  # Check every 10 seconds
        elapsed = 0

        while elapsed < total_seconds:
            remaining = total_seconds - elapsed
            sleep_time = min(chunk_size, remaining)
            time.sleep(sleep_time)
            elapsed += sleep_time

            # Check for activity signal
            if check_activity_signal():
                self.log.info("User activity detected → waking up")
                self.state.current_interval = INTERVAL_FAST
                self.state.last_activity = datetime.now()
                self.state.save()
                return  # Exit sleep early

    def is_controller_active(self) -> bool:
        """Check if user is actively using the controller (by monitoring output changes)"""
        try:
            # Check the overcode-controller session's bottom pane
            result = subprocess.run(
                ["tmux", "capture-pane", "-t", "overcode-controller:0.1", "-p"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode != 0:
                return False

            # Count non-empty lines
            lines = [l for l in result.stdout.split('\n') if l.strip()]
            current_count = len(lines)

            # Compare with last check
            now = datetime.now()
            if self.last_controller_check:
                time_since_check = (now - self.last_controller_check).total_seconds()
                # If output changed recently, user is active
                if current_count != self.controller_line_count and time_since_check < 60:
                    self.state.last_activity = now
                    self.controller_line_count = current_count
                    self.last_controller_check = now
                    return True

            self.controller_line_count = current_count
            self.last_controller_check = now

            # Also consider "recently active" if activity was in last 2 minutes
            if self.state.last_activity:
                since_activity = (now - self.state.last_activity).total_seconds()
                if since_activity < 120:
                    return True

            return False
        except subprocess.SubprocessError:
            # tmux command failed (timeout, not found, etc.) - assume not active
            return False
        except (OSError, IOError) as e:
            # File/process errors - log and assume not active
            self.log.warn(f"Error checking controller activity: {e}")
            return False

    def calculate_interval(self, sessions: list, non_green: list, all_waiting_user: bool) -> int:
        """Calculate appropriate loop interval based on current state"""
        # If user is active, stay fast
        if self.is_controller_active():
            return INTERVAL_FAST

        # No sessions at all - go idle
        if not sessions:
            return INTERVAL_IDLE

        # All sessions waiting for user - slow down
        if all_waiting_user and not self.is_daemon_claude_running():
            return INTERVAL_SLOW

        # Agents are working or daemon claude is active - stay fast
        return INTERVAL_FAST

    def run(self, check_interval: int = 10):
        """Main daemon loop with adaptive speed"""
        # Check if another daemon is already running (via PID file)
        existing_pid = get_daemon_pid()
        if existing_pid is not None and existing_pid != os.getpid():
            self.log.error(f"Another daemon is already running (PID {existing_pid})")
            self.log.info(f"Kill it with: kill {existing_pid}")
            sys.exit(1)

        # Also check for orphaned daemon processes (not tracked in PID file)
        try:
            result = subprocess.run(
                ["pgrep", "-f", "overcode.*daemon|overcode\\.daemon"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                other_pids = [int(p) for p in result.stdout.strip().split('\n') if p]
                other_pids = [p for p in other_pids if p != os.getpid()]
                if other_pids:
                    self.log.warn(f"Found orphaned daemon process(es): {other_pids}")
                    self.log.info(f"Kill them with: kill {' '.join(map(str, other_pids))}")
                    # Don't exit - just warn. Let user decide to kill orphans.
        except (subprocess.SubprocessError, ValueError):
            pass  # pgrep failed, proceed anyway

        # Write PID file for process detection
        _write_pid_file()
        self.log.section("Overcode Daemon")
        self.log.info(f"PID: {os.getpid()}")
        self.log.info(f"Mode: {self.state.mode}")
        self.log.info(f"Monitoring tmux session: {self.tmux_session}")
        self.log.info(f"Base interval: {check_interval}s (adaptive)")

        self.state.started_at = datetime.now()
        self.state.current_interval = check_interval
        self.state.status = "active"
        self.state.save()

        try:
            while True:
                self.state.loop_count += 1
                self.state.last_loop_time = datetime.now()

                # Check for user activity signal from TUI
                if check_activity_signal():
                    if self.state.current_interval != INTERVAL_FAST:
                        self.log.info("User activity detected → fast interval")
                        self.state.current_interval = INTERVAL_FAST
                    self.state.last_activity = datetime.now()

                # Cleanup any orphaned daemon claude windows from previous daemon runs
                self.cleanup_stale_daemon_claudes()

                # Get all sessions and their statuses
                sessions = self.session_manager.list_sessions()
                non_green = self.get_non_green_sessions()

                # Log loop status with pretty formatting
                green_count = len(sessions) - len(non_green)
                self.log.status_summary(
                    total=len(sessions),
                    green=green_count,
                    non_green=len(non_green),
                    loop=self.state.loop_count
                )

                # Log each agent's individual status and track stats
                for session in sessions:
                    status, activity, _ = self.status_detector.detect_status(session)
                    self.log.agent_status(session.name, status, activity)
                    # Track state transitions and update interaction stats
                    self.track_session_stats(session, status)
                    # Also log to history file for timeline visualization
                    log_agent_status(session.name, status, activity)

                # Sync Claude Code stats periodically (every 6 loops = ~1 minute at fast interval)
                if self.state.loop_count % 6 == 0:
                    for session in sessions:
                        self.sync_claude_code_stats(session)

                # Check if ALL non-green sessions are waiting for user
                all_waiting_user = (
                    non_green and
                    all(status == STATUS_WAITING_USER for _, status in non_green)
                )

                # Check if any session has standing instructions (daemon claude can help)
                any_has_instructions = any(
                    session.standing_instructions
                    for session, _ in non_green
                )

                if non_green:
                    # Skip daemon claude only if ALL waiting_user AND none have instructions
                    if all_waiting_user and not any_has_instructions:
                        self.state.status = "waiting"
                        self.log.warn("All sessions waiting for user input (no instructions set)")
                    elif self.state.mode == MODE_MONITOR:
                        # Monitor mode: track stats but never launch daemon claude
                        self.state.status = "monitoring"
                    else:
                        # Supervise mode: Launch daemon claude if not already running
                        if not self.is_daemon_claude_running():
                            reason = "with instructions" if any_has_instructions else "non-user-blocked"
                            self.log.info(f"Launching daemon claude for {len(non_green)} session(s) ({reason})...")
                            self.launch_daemon_claude(non_green)
                            self.state.daemon_claude_launches += 1
                            self.state.status = "supervising"
                            self.log.success(f"Daemon claude launched in window {self.daemon_claude_window}")

                        # Wait for daemon claude to complete its task
                        if self.is_daemon_claude_running():
                            completed = self.wait_for_daemon_claude(timeout=300)

                            # Capture final output
                            self.capture_daemon_claude_output()

                            if completed:
                                # Only kill on successful completion
                                self.kill_daemon_claude()

                                # Update intervention counts from supervisor log
                                sessions_handled = [session for session, _ in non_green]
                                self.update_intervention_counts(sessions_handled)
                            else:
                                # Timeout - let daemon claude keep working
                                # Don't kill it, just log and continue to next loop
                                self.log.warn("Daemon claude still working after 5 min, continuing...")
                else:
                    if sessions:
                        self.state.status = "idle"
                        self.log.success("All sessions GREEN")
                    else:
                        self.state.status = "no_agents"

                # Calculate next interval
                new_interval = self.calculate_interval(sessions, non_green, all_waiting_user)
                if new_interval != self.state.current_interval:
                    interval_names = {
                        INTERVAL_FAST: "fast (10s)",
                        INTERVAL_SLOW: "slow (5m)",
                        INTERVAL_IDLE: "idle (1h)"
                    }
                    self.log.info(f"Loop speed → {interval_names.get(new_interval, f'{new_interval}s')}")
                    self.state.current_interval = new_interval

                # Save state for TUI
                self.state.save()

                # Sleep in chunks, checking for activity signal periodically
                self._interruptible_sleep(self.state.current_interval)

        except KeyboardInterrupt:
            self.log.section("Shutting Down")
            self.state.status = "stopped"
            self.state.save()
            _remove_pid_file()
            self.log.info("Daemon stopped")
            sys.exit(0)
        finally:
            # Ensure PID file is removed even on unexpected exit
            _remove_pid_file()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Overcode daemon")
    parser.add_argument(
        "--session",
        default="agents",
        help="Tmux session to monitor (default: agents)"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=10,
        help="Check interval in seconds (default: 10)"
    )

    args = parser.parse_args()

    daemon = Daemon(args.session)
    daemon.run(args.interval)


if __name__ == "__main__":
    main()
