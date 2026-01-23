"""
Textual TUI for Overcode monitor.

TODO: Split this file into smaller modules for maintainability:
- tui_core.py: Main App class and core lifecycle
- tui_panels.py: Panel widgets (StatusPanel, AgentPanel, etc.)
- tui_commands.py: Command handlers and actions
- tui_keybindings.py: Key bindings and input handling
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import List, Optional
import subprocess
import sys
import time

from textual.app import App, ComposeResult
from textual.containers import Container, Vertical, ScrollableContainer, Horizontal
from textual.widgets import Header, Footer, Static, Label, Input, TextArea
from textual.reactive import reactive
from textual.css.query import NoMatches
from textual import events, work
from textual.message import Message
from rich.text import Text
from rich.panel import Panel

from .session_manager import SessionManager, Session
from .launcher import ClaudeLauncher
from .status_detector import StatusDetector
from .status_constants import STATUS_WAITING_USER
from .history_reader import get_session_stats, ClaudeSessionStats
from .settings import signal_activity, get_session_dir, get_agent_history_path, TUIPreferences, DAEMON_VERSION  # Activity signaling to daemon
from .monitor_daemon_state import MonitorDaemonState, get_monitor_daemon_state
from .monitor_daemon import (
    is_monitor_daemon_running,
    stop_monitor_daemon,
)
from .pid_utils import count_daemon_processes
from .supervisor_daemon import (
    is_supervisor_daemon_running,
    stop_supervisor_daemon,
)
from .web_server import (
    is_web_server_running,
    get_web_server_url,
    toggle_web_server,
)
from .config import get_default_standing_instructions
from .status_history import read_agent_status_history
from .presence_logger import read_presence_history, MACOS_APIS_AVAILABLE
from .launcher import ClaudeLauncher
from .implementations import RealTmux
from .tui_helpers import (
    format_interval,
    format_ago,
    format_duration,
    format_tokens,
    format_line_count,
    calculate_uptime,
    presence_state_to_char,
    agent_status_to_char,
    get_current_state_times,
    build_timeline_slots,
    build_timeline_string,
    get_status_symbol,
    get_presence_color,
    get_agent_timeline_color,
    style_pane_line,
    truncate_name,
    get_daemon_status_style,
    get_git_diff_stats,
    calculate_safe_break_duration,
)


def format_standing_instructions(instructions: str, max_len: int = 95) -> str:
    """Format standing instructions for display.

    Shows "[DEFAULT]" if instructions match the configured default,
    otherwise shows the truncated instructions.
    """
    if not instructions:
        return ""

    default = get_default_standing_instructions()
    if default and instructions.strip() == default.strip():
        return "[DEFAULT]"

    if len(instructions) > max_len:
        return instructions[:max_len - 3] + "..."
    return instructions


class DaemonStatusBar(Static):
    """Widget displaying daemon status.

    Shows Monitor Daemon and Supervisor Daemon status explicitly.
    Presence is shown only when available (macOS with monitor daemon running).
    """

    def __init__(self, tmux_session: str = "agents", session_manager: Optional["SessionManager"] = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tmux_session = tmux_session
        self.monitor_state: Optional[MonitorDaemonState] = None
        self._session_manager = session_manager
        self._asleep_session_ids: set = set()  # Cache of asleep session IDs

    def update_status(self) -> None:
        """Refresh daemon state from file"""
        self.monitor_state = get_monitor_daemon_state(self.tmux_session)
        # Update cache of asleep session IDs from session manager
        if self._session_manager:
            self._asleep_session_ids = {
                s.id for s in self._session_manager.list_sessions() if s.is_asleep
            }
        self.refresh()

    def render(self) -> Text:
        """Render daemon status bar.

        Shows Monitor Daemon and Supervisor Daemon status explicitly.
        """
        content = Text()

        # Monitor Daemon status
        content.append("Monitor: ", style="bold")
        monitor_running = self.monitor_state and not self.monitor_state.is_stale()

        if monitor_running:
            state = self.monitor_state
            symbol, style = get_daemon_status_style(state.status)
            content.append(f"{symbol} ", style=style)
            content.append(f"#{state.loop_count}", style="cyan")
            content.append(f" @{format_interval(state.current_interval)}", style="dim")
            # Version mismatch warning
            if state.daemon_version != DAEMON_VERSION:
                content.append(f" ⚠v{state.daemon_version}→{DAEMON_VERSION}", style="bold yellow")
        else:
            content.append("○ ", style="red")
            content.append("stopped", style="red")

        content.append(" │ ", style="dim")

        # Supervisor Daemon status
        content.append("Supervisor: ", style="bold")
        supervisor_running = is_supervisor_daemon_running(self.tmux_session)

        if supervisor_running:
            content.append("● ", style="green")
            # Show if daemon Claude is currently running
            if monitor_running and self.monitor_state.supervisor_claude_running:
                # Calculate current run duration
                run_duration = ""
                if self.monitor_state.supervisor_claude_started_at:
                    try:
                        started = datetime.fromisoformat(self.monitor_state.supervisor_claude_started_at)
                        elapsed = (datetime.now() - started).total_seconds()
                        run_duration = format_duration(elapsed)
                    except (ValueError, TypeError):
                        run_duration = "?"
                content.append(f"🤖 RUNNING {run_duration}", style="bold yellow")
            # Show supervision stats if available from monitor state
            elif monitor_running and self.monitor_state.total_supervisions > 0:
                content.append(f"sup:{self.monitor_state.total_supervisions}", style="magenta")
                if self.monitor_state.supervisor_tokens > 0:
                    content.append(f" {format_tokens(self.monitor_state.supervisor_tokens)}", style="blue")
                # Show cumulative daemon Claude run time
                if self.monitor_state.supervisor_claude_total_run_seconds > 0:
                    total_run = format_duration(self.monitor_state.supervisor_claude_total_run_seconds)
                    content.append(f" ⏱{total_run}", style="dim")
            else:
                content.append("ready", style="green")
        else:
            content.append("○ ", style="red")
            content.append("stopped", style="red")

        # Spin rate stats (only when monitor running with sessions)
        if monitor_running and self.monitor_state.sessions:
            content.append(" │ ", style="dim")
            # Filter out sleeping agents from stats
            all_sessions = self.monitor_state.sessions
            active_sessions = [s for s in all_sessions if s.session_id not in self._asleep_session_ids]
            sleeping_count = len(all_sessions) - len(active_sessions)

            total_agents = len(active_sessions)
            # Recalculate green_now excluding sleeping agents
            green_now = sum(1 for s in active_sessions if s.current_status == "running")

            # Calculate mean spin rate from green_time percentages (exclude sleeping)
            mean_spin = 0.0
            for s in active_sessions:
                total_time = s.green_time_seconds + s.non_green_time_seconds
                if total_time > 0:
                    mean_spin += s.green_time_seconds / total_time

            content.append("Spin: ", style="bold")
            content.append(f"{green_now}", style="bold green" if green_now > 0 else "dim")
            content.append(f"/{total_agents}", style="dim")
            if sleeping_count > 0:
                content.append(f" 💤{sleeping_count}", style="dim")  # Show sleeping count
            if mean_spin > 0:
                content.append(f" μ{mean_spin:.1f}x", style="cyan")

            # Safe break duration (time until 50%+ agents need attention) - exclude sleeping
            safe_break = calculate_safe_break_duration(active_sessions)
            if safe_break is not None:
                content.append(" │ ", style="dim")
                content.append("☕", style="bold")
                if safe_break < 60:
                    content.append(f" <1m", style="bold red")
                elif safe_break < 300:  # < 5 min
                    content.append(f" {format_duration(safe_break)}", style="bold yellow")
                else:
                    content.append(f" {format_duration(safe_break)}", style="bold green")

        # Presence status (only show if available via monitor daemon on macOS)
        if monitor_running and self.monitor_state.presence_available:
            content.append(" │ ", style="dim")
            state = self.monitor_state.presence_state
            idle = self.monitor_state.presence_idle_seconds or 0

            state_names = {1: "🔒", 2: "💤", 3: "👤"}
            state_colors = {1: "red", 2: "yellow", 3: "green"}

            icon = state_names.get(state, "?")
            color = state_colors.get(state, "dim")
            content.append(f"{icon}", style=color)
            content.append(f" {int(idle)}s", style="dim")

        # Relay status (small indicator)
        if monitor_running and self.monitor_state.relay_enabled:
            content.append(" │ ", style="dim")
            relay_status = self.monitor_state.relay_last_status
            if relay_status == "ok":
                content.append("📡", style="green")
            elif relay_status == "error":
                content.append("📡", style="red")
            else:
                content.append("📡", style="dim")

        # Web server status
        web_running = is_web_server_running(self.tmux_session)
        if web_running:
            content.append(" │ ", style="dim")
            url = get_web_server_url(self.tmux_session)
            content.append("🌐", style="green")
            if url:
                # Just show port
                port = url.split(":")[-1] if url else ""
                content.append(f":{port}", style="cyan")

        return content


class StatusTimeline(Static):
    """Widget displaying historical status timelines for user presence and agents.

    Shows the last 3 hours with each character representing a time slice.
    - User presence: green=active, yellow=inactive, red/gray=locked/away
    - Agent status: green=running, red=waiting, grey=terminated
    """

    TIMELINE_HOURS = 3.0  # Show last 3 hours
    LABEL_WIDTH = 12      # Width of labels like "  User:   " or "  agent:  "
    MIN_TIMELINE = 20     # Minimum timeline width
    DEFAULT_TIMELINE = 60 # Fallback if can't detect width

    def __init__(self, sessions: list, tmux_session: str = "agents", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sessions = sessions
        self.tmux_session = tmux_session
        self._presence_history = []
        self._agent_histories = {}

    @property
    def timeline_width(self) -> int:
        """Calculate timeline width based on available space."""
        import shutil
        try:
            # Try to get terminal size directly - most reliable
            term_width = shutil.get_terminal_size().columns
            # Subtract label width and some padding
            available = term_width - self.LABEL_WIDTH - 6
            return max(self.MIN_TIMELINE, min(available, 120))
        except (OSError, ValueError):
            # No terminal available or invalid size
            return self.DEFAULT_TIMELINE

    def update_history(self, sessions: list) -> None:
        """Refresh history data from log files."""
        self.sessions = sessions
        self._presence_history = read_presence_history(hours=self.TIMELINE_HOURS)
        self._agent_histories = {}

        # Get agent names from sessions
        agent_names = [s.name for s in sessions]

        # Read agent history from session-specific file and group by agent
        history_path = get_agent_history_path(self.tmux_session)
        all_history = read_agent_status_history(hours=self.TIMELINE_HOURS, history_file=history_path)
        for ts, agent, status, activity in all_history:
            if agent not in self._agent_histories:
                self._agent_histories[agent] = []
            self._agent_histories[agent].append((ts, status))

        # Force layout refresh when content changes (agent count may have changed)
        self.refresh(layout=True)

    def _build_timeline(self, history: list, state_to_char: callable) -> str:
        """Build a timeline string from history data.

        Args:
            history: List of (timestamp, state) tuples
            state_to_char: Function to convert state to display character

        Returns:
            String of timeline_width characters representing the timeline
        """
        width = self.timeline_width
        if not history:
            return "─" * width

        now = datetime.now()
        start_time = now - timedelta(hours=self.TIMELINE_HOURS)
        slot_duration = timedelta(hours=self.TIMELINE_HOURS) / width

        # Initialize timeline with empty slots
        timeline = ["─"] * width

        # Fill in slots based on history
        for ts, state in history:
            if ts < start_time:
                continue
            # Calculate which slot this belongs to
            elapsed = ts - start_time
            slot_idx = int(elapsed / slot_duration)
            if 0 <= slot_idx < width:
                timeline[slot_idx] = state_to_char(state)

        return "".join(timeline)

    def render(self) -> Text:
        """Render the timeline visualization."""
        content = Text()
        now = datetime.now()
        width = self.timeline_width

        # Time scale header
        content.append("Timeline: ", style="bold")
        content.append(f"-{self.TIMELINE_HOURS:.0f}h", style="dim")
        header_padding = max(0, width - 10)
        content.append(" " * header_padding, style="dim")
        content.append("now", style="dim")
        content.append("\n")

        # User presence timeline - group by time slots like agent timelines
        # Align with agent names (14 chars): "  " + name + " " = 17 chars total
        content.append(f"  {'User:':<14} ", style="cyan")
        if self._presence_history:
            slot_states = build_timeline_slots(
                self._presence_history, width, self.TIMELINE_HOURS, now
            )
            # Render timeline with colors
            for i in range(width):
                if i in slot_states:
                    state = slot_states[i]
                    char = presence_state_to_char(state)
                    color = get_presence_color(state)
                    content.append(char, style=color)
                else:
                    content.append("─", style="dim")
        elif not MACOS_APIS_AVAILABLE:
            # Show install instructions when presence deps not installed (macOS only)
            msg = "macOS only - pip install overcode[presence]"
            content.append(msg[:width], style="dim italic")
        else:
            content.append("─" * width, style="dim")
        content.append("\n")

        # Agent timelines
        for session in self.sessions:
            agent_name = session.name
            history = self._agent_histories.get(agent_name, [])

            # Truncate name to fit
            display_name = truncate_name(agent_name)
            content.append(f"  {display_name} ", style="cyan")

            green_slots = 0
            total_slots = 0
            if history:
                slot_states = build_timeline_slots(history, width, self.TIMELINE_HOURS, now)
                # Render timeline with colors
                for i in range(width):
                    if i in slot_states:
                        status = slot_states[i]
                        char = agent_status_to_char(status)
                        color = get_agent_timeline_color(status)
                        content.append(char, style=color)
                        total_slots += 1
                        if status == "running":
                            green_slots += 1
                    else:
                        content.append("─", style="dim")
            else:
                content.append("─" * width, style="dim")

            # Show percentage green in last 3 hours
            if total_slots > 0:
                pct = green_slots / total_slots * 100
                pct_style = "bold green" if pct >= 50 else "bold red"
                content.append(f" {pct:>3.0f}%", style=pct_style)
            else:
                content.append("   - ", style="dim")

            content.append("\n")

        # Legend (combined on one line to save space)
        content.append(f"  {'Legend:':<14} ", style="dim")
        content.append("█", style="green")
        content.append("active/running ", style="dim")
        content.append("▒", style="yellow")
        content.append("inactive ", style="dim")
        content.append("░", style="red")
        content.append("waiting/away ", style="dim")
        content.append("×", style="dim")
        content.append("terminated", style="dim")

        return content


class HelpOverlay(Static):
    """Help overlay explaining all TUI metrics and controls"""

    HELP_TEXT = """
╔══════════════════════════════════════════════════════════════════════════════╗
║                           OVERCODE MONITOR HELP                            ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  AGENT STATUS LINE                                                           ║
║  ────────────────────────────────────────────────────────────────────────────║
║  🟢 agent-name    repo:branch    ↑4.2h  ▶ 2.1h ⏸ 2.1h  12i  $0.45  ⏱3.2s 🏃 5s║
║  │   │            │              │      │      │       │    │      │     │  │ ║
║  │   │            │              │      │      │       │    │      │     │  └─ steers: overcode interventions
║  │   │            │              │      │      │       │    │      │     └──── mode: 🔥bypass 🏃permissive 👮normal
║  │   │            │              │      │      │       │    │      └────────── avg op time (seconds)
║  │   │            │              │      │      │       │    └───────────────── estimated cost (USD)
║  │   │            │              │      │      │       └────────────────────── interactions (claude turns)
║  │   │            │              │      │      └─────────────────────────────── paused time (non-green)
║  │   │            │              │      └────────────────────────────────────── active time (green/running)
║  │   │            │              └───────────────────────────────────────────── uptime since launch
║  │   │            └──────────────────────────────────────────────────────────── git repo:branch
║  │   └───────────────────────────────────────────────────────────────────────── agent name
║  └───────────────────────────────────────────────────────────────────────────── status (see below)
║                                                                              ║
║  STATUS COLORS                                                               ║
║  ────────────────────────────────────────────────────────────────────────────║
║  🟢 Running     - Agent is actively working                                  ║
║  🟡 No Instruct - Running but no standing instructions set                   ║
║  🟠 Wait Super  - Waiting for overcode supervisor                          ║
║  🔴 Wait User   - Blocked! Needs user input (permission prompt, question)    ║
║  ⚫ Terminated  - Claude exited, shell prompt showing (ready for cleanup)    ║
║                                                                              ║
║  DAEMON STATUS LINE                                                          ║
║  ────────────────────────────────────────────────────────────────────────────║
║  Daemon: ● active │ #42 @10s (5s ago) │ sup:3 │ Presence: ● active (3s idle) ║
║          │ │      │ │   │    │            │              │ │       │         ║
║          │ │      │ │   │    │            │              │ │       └── idle seconds
║          │ │      │ │   │    │            │              │ └────────── user state
║          │ │      │ │   │    │            │              └───────────── presence logger status
║          │ │      │ │   │    │            └──────────────────────────── supervisor launches
║          │ │      │ │   │    └───────────────────────────────────────── time since last loop
║          │ │      │ │   └────────────────────────────────────────────── current interval
║          │ │      │ └────────────────────────────────────────────────── loop count
║          │ └──────┴──────────────────────────────────────────────────── daemon status
║          └───────────────────────────────────────────────────────────── status indicator
║                                                                              ║
║  KEYBOARD SHORTCUTS                                                          ║
║  ────────────────────────────────────────────────────────────────────────────║
║  q       Quit                    d       Toggle daemon panel                 ║
║  h/?     Toggle this help        t       Toggle timeline                     ║
║  v       Cycle detail lines      s       Cycle summary detail                ║
║  e       Expand all agents       c       Collapse all agents                 ║
║  space   Toggle focused agent    i/:     Focus command bar                   ║
║  n       Create new agent        x       Kill focused agent                  ║
║  click   Toggle agent expand/collapse                                        ║
║                                                                              ║
║  COMMAND BAR (i or : to focus)                                               ║
║  ────────────────────────────────────────────────────────────────────────────║
║  Enter   Send instruction        Esc     Clear & unfocus                     ║
║  Ctrl+E  Toggle multi-line       Ctrl+O  Set as standing order               ║
║  Ctrl+Enter  Send (multi-line)                                               ║
║                                                                              ║
║  DAEMON CONTROLS (work anywhere)                                             ║
║  ────────────────────────────────────────────────────────────────────────────║
║  [       Start supervisor        ]       Stop supervisor                     ║
║  \\      Restart monitor         d       Toggle daemon log panel             ║
║  w       Toggle web dashboard (analytics server)                             ║
║                                                                              ║
║  SUMMARY DETAIL LEVELS (s key)                                               ║
║  ────────────────────────────────────────────────────────────────────────────║
║  low     Name, tokens, ctx% (context usage), git Δ, mode, steers, orders     ║
║  med     + uptime, running time, stalled time, latency                       ║
║  full    + repo:branch, % active, git diff details (+ins -del)               ║
║                                                                              ║
║                              Press h or ? to close                           ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

    def render(self) -> Text:
        return Text(self.HELP_TEXT.strip())


class DaemonPanel(Static):
    """Inline daemon panel with status and log viewer (like timeline)"""

    LOG_LINES_TO_SHOW = 8  # Number of log lines to display

    def __init__(self, tmux_session: str = "agents", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tmux_session = tmux_session
        self.log_lines: list[str] = []
        self.monitor_state: Optional[MonitorDaemonState] = None
        self._log_file_pos = 0

    def on_mount(self) -> None:
        """Start log tailing when mounted"""
        self.set_interval(1.0, self._refresh_logs)
        self._refresh_logs()

    def _refresh_logs(self) -> None:
        """Refresh daemon status and logs"""
        from pathlib import Path

        # Only refresh if visible
        if not self.display:
            return

        # Update daemon state from Monitor Daemon
        self.monitor_state = get_monitor_daemon_state(self.tmux_session)

        # Read log lines from session-specific monitor_daemon.log
        session_dir = get_session_dir(self.tmux_session)
        log_file = session_dir / "monitor_daemon.log"
        if log_file.exists():
            try:
                with open(log_file, 'r') as f:
                    if not self.log_lines:
                        # First read: get last 100 lines of file
                        all_lines = f.readlines()
                        self.log_lines = [l.rstrip() for l in all_lines[-100:]]
                        self._log_file_pos = f.tell()
                    else:
                        # Subsequent reads: only get new content
                        f.seek(self._log_file_pos)
                        new_content = f.read()
                        self._log_file_pos = f.tell()

                        if new_content:
                            new_lines = new_content.strip().split('\n')
                            self.log_lines.extend(new_lines)
                            # Keep last 100 lines
                            self.log_lines = self.log_lines[-100:]
            except (OSError, IOError, ValueError):
                # Log file not available, read error, or seek error
                pass

        self.refresh()

    def render(self) -> Text:
        """Render daemon panel inline (similar to timeline style)"""
        content = Text()

        # Header with status - match DaemonStatusBar format exactly
        content.append("🤖 Supervisor Daemon: ", style="bold")

        # Check Monitor Daemon state
        if self.monitor_state and not self.monitor_state.is_stale():
            state = self.monitor_state
            symbol, style = get_daemon_status_style(state.status)

            content.append(f"{symbol} ", style=style)
            content.append(f"{state.status}", style=style)

            # State details
            content.append("  │  ", style="dim")
            content.append(f"#{state.loop_count}", style="cyan")
            content.append(f" @{format_interval(state.current_interval)}", style="dim")
            last_loop = datetime.fromisoformat(state.last_loop_time) if state.last_loop_time else None
            content.append(f" ({format_ago(last_loop)})", style="dim")
            if state.total_supervisions > 0:
                content.append(f"  sup:{state.total_supervisions}", style="magenta")
        else:
            # Monitor Daemon not running or stale
            content.append("○ ", style="red")
            content.append("stopped", style="red")
            # Show last activity if available from stale state
            if self.monitor_state and self.monitor_state.last_loop_time:
                try:
                    last_time = datetime.fromisoformat(self.monitor_state.last_loop_time)
                    content.append(f" (last: {format_ago(last_time)})", style="dim")
                except ValueError:
                    pass

        # Controls hint
        content.append("  │  ", style="dim")
        content.append("[", style="bold green")
        content.append(":sup ", style="dim")
        content.append("]", style="bold red")
        content.append(":sup ", style="dim")
        content.append("\\", style="bold yellow")
        content.append(":mon", style="dim")

        content.append("\n")

        # Log lines
        display_lines = self.log_lines[-self.LOG_LINES_TO_SHOW:] if self.log_lines else []

        if not display_lines:
            content.append("  (no logs yet - daemon may not have run)", style="dim italic")
            content.append("\n")
        else:
            for line in display_lines:
                content.append("  ", style="")
                # Truncate line
                display_line = line[:120] if len(line) > 120 else line

                # Color based on content
                if "ERROR" in line or "error" in line:
                    style = "red"
                elif "WARNING" in line or "warning" in line:
                    style = "yellow"
                elif ">>>" in line:
                    style = "bold cyan"
                elif "supervising" in line.lower() or "steering" in line.lower():
                    style = "magenta"
                elif "Loop" in line:
                    style = "dim cyan"
                else:
                    style = "dim"

                content.append(display_line, style=style)
                content.append("\n")

        return content


class SessionSummary(Static, can_focus=True):
    """Widget displaying expandable session summary"""

    expanded: reactive[bool] = reactive(True)  # Start expanded
    detail_lines: reactive[int] = reactive(5)  # Lines of output to show (5, 10, 20, 50)
    summary_detail: reactive[str] = reactive("low")  # low, med, full

    def __init__(self, session: Session, status_detector: StatusDetector, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session = session
        self.status_detector = status_detector
        # Initialize from persisted session state, not hardcoded "running"
        self.detected_status = session.stats.current_state if session.stats.current_state else "running"
        self.current_activity = "Initializing..."
        self.pane_content: List[str] = []  # Cached pane content
        self.claude_stats: Optional[ClaudeSessionStats] = None  # Token/interaction stats
        self.git_diff_stats: Optional[tuple] = None  # (files, insertions, deletions)
        # Track if this is a stalled agent that hasn't been visited yet
        self.is_unvisited_stalled: bool = False
        # Start with expanded class since expanded=True by default
        self.add_class("expanded")

    def on_click(self) -> None:
        """Toggle expanded state on click"""
        self.expanded = not self.expanded
        # Notify parent app to save state
        self.post_message(self.ExpandedChanged(self.session.id, self.expanded))
        # Mark as visited if this is an unvisited stalled agent
        if self.is_unvisited_stalled:
            self.post_message(self.StalledAgentVisited(self.session.id))

    def on_focus(self) -> None:
        """Handle focus event - mark stalled agent as visited and update selection"""
        if self.is_unvisited_stalled:
            self.post_message(self.StalledAgentVisited(self.session.id))
        # Notify app to update selection highlighting
        self.post_message(self.SessionSelected(self.session.id))

    class SessionSelected(events.Message):
        """Message sent when a session is selected/focused"""
        def __init__(self, session_id: str):
            super().__init__()
            self.session_id = session_id

    class ExpandedChanged(events.Message):
        """Message sent when expanded state changes"""
        def __init__(self, session_id: str, expanded: bool):
            super().__init__()
            self.session_id = session_id
            self.expanded = expanded

    class StalledAgentVisited(events.Message):
        """Message sent when user visits a stalled agent (focus or click)"""
        def __init__(self, session_id: str):
            super().__init__()
            self.session_id = session_id

    def watch_expanded(self, expanded: bool) -> None:
        """Called when expanded state changes"""
        # Toggle CSS class for proper height
        if expanded:
            self.add_class("expanded")
        else:
            self.remove_class("expanded")
        self.refresh(layout=True)
        # Notify parent app to save state
        self.post_message(self.ExpandedChanged(self.session.id, expanded))

    def watch_detail_lines(self, detail_lines: int) -> None:
        """Called when detail_lines changes - force layout refresh"""
        self.refresh(layout=True)

    def update_status(self) -> None:
        """Update the detected status for this session.

        NOTE: This is now VIEW-ONLY. Time tracking is handled by the Monitor Daemon.
        We only detect status for display and capture pane content for the expanded view.
        """
        # detect_status returns (status, activity, pane_content) - reuse content to avoid
        # duplicate tmux subprocess calls (was 2 calls per widget, now just 1)
        new_status, self.current_activity, content = self.status_detector.detect_status(self.session)
        self.apply_status(new_status, self.current_activity, content)

    def apply_status(self, status: str, activity: str, content: str) -> None:
        """Apply pre-fetched status data to this widget.

        Used by parallel status updates to apply data fetched in background threads.
        Note: This still fetches claude_stats synchronously - used for single widget updates.
        """
        # Fetch claude stats (only for standalone update_status calls)
        claude_stats = get_session_stats(self.session)
        # Fetch git diff stats
        git_diff = None
        if self.session.start_directory:
            git_diff = get_git_diff_stats(self.session.start_directory)
        self.apply_status_no_refresh(status, activity, content, claude_stats, git_diff)
        self.refresh()

    def apply_status_no_refresh(self, status: str, activity: str, content: str, claude_stats: Optional[ClaudeSessionStats] = None, git_diff_stats: Optional[tuple] = None) -> None:
        """Apply pre-fetched status data without triggering refresh.

        Used for batched updates where the caller will refresh once at the end.
        All data including claude_stats should be pre-fetched in background thread.
        """
        self.current_activity = activity

        # Use pane content from detect_status (already fetched)
        if content:
            # Keep all lines including blanks for proper formatting, just strip trailing blanks
            lines = content.rstrip().split('\n')
            self.pane_content = lines[-50:] if lines else []  # Keep last 50 lines max
        else:
            self.pane_content = []

        # Update detected status for display
        # NOTE: Time tracking removed - Monitor Daemon is the single source of truth
        # The session.stats values are read from what Monitor Daemon has persisted
        # If session is asleep, keep the asleep status instead of the detected status
        if self.session.is_asleep:
            self.detected_status = "asleep"
        else:
            self.detected_status = status

        # Use pre-fetched claude stats (no file I/O on main thread)
        if claude_stats is not None:
            self.claude_stats = claude_stats

        # Use pre-fetched git diff stats
        if git_diff_stats is not None:
            self.git_diff_stats = git_diff_stats

    def watch_summary_detail(self, summary_detail: str) -> None:
        """Called when summary_detail changes"""
        self.refresh()

    def render(self) -> Text:
        """Render session summary (compact or expanded)"""
        import shutil
        s = self.session
        stats = s.stats
        term_width = shutil.get_terminal_size().columns

        # Expansion indicator
        expand_icon = "▼" if self.expanded else "▶"

        # Calculate all values (only use what we need per level)
        uptime = calculate_uptime(self.session.start_time)
        repo_info = f"{s.repo_name or 'n/a'}:{s.branch or 'n/a'}"
        green_time, non_green_time = get_current_state_times(self.session.stats)

        # Get median work time from claude stats (or 0 if unavailable)
        median_work = self.claude_stats.median_work_time if self.claude_stats else 0.0

        # Status indicator - larger emoji circles based on detected status
        # Blue background matching Textual header/footer style
        bg = " on #0d2137"
        status_symbol, base_color = get_status_symbol(self.detected_status)
        status_color = f"bold {base_color}{bg}"

        # Permissiveness mode with emoji
        if s.permissiveness_mode == "bypass":
            perm_emoji = "🔥"  # Fire - burning through all permissions
        elif s.permissiveness_mode == "permissive":
            perm_emoji = "🏃"  # Running permissively
        else:
            perm_emoji = "👮"  # Normal mode with permissions

        content = Text()

        # Determine name width based on detail level (more space in lower detail modes)
        if self.summary_detail == "low":
            name_width = 24
        elif self.summary_detail == "med":
            name_width = 20
        else:  # full
            name_width = 16

        # Truncate name if needed
        display_name = s.name[:name_width].ljust(name_width)

        # Always show: status symbol, time in state, expand icon, agent name
        content.append(f"{status_symbol} ", style=status_color)

        # Show 🔔 indicator for unvisited stalled agents (needs attention)
        if self.is_unvisited_stalled:
            content.append("🔔", style=f"bold blink red{bg}")
        else:
            content.append("  ", style=f"dim{bg}")  # Maintain alignment

        # Time in current state (directly after status light)
        if stats.state_since:
            try:
                state_start = datetime.fromisoformat(stats.state_since)
                elapsed = (datetime.now() - state_start).total_seconds()
                content.append(f"{format_duration(elapsed):>5} ", style=status_color)
            except (ValueError, TypeError):
                content.append("    - ", style=f"dim{bg}")
        else:
            content.append("    - ", style=f"dim{bg}")

        # In list-mode, show focus indicator instead of expand icon
        if "list-mode" in self.classes:
            if self.has_focus:
                content.append("→ ", style=status_color)
            else:
                content.append("  ", style=status_color)
        else:
            content.append(f"{expand_icon} ", style=status_color)
        content.append(f"{display_name}", style=f"bold cyan{bg}")

        # Full detail: add repo:branch (padded to longest across all sessions)
        if self.summary_detail == "full":
            repo_width = getattr(self.app, 'max_repo_info_width', 18)
            content.append(f" {repo_info:<{repo_width}} ", style=f"bold dim{bg}")

        # Med/Full detail: add uptime, running time, stalled time
        if self.summary_detail in ("med", "full"):
            content.append(f" ↑{uptime:>5}", style=f"bold white{bg}")
            content.append(f" ▶{format_duration(green_time):>5}", style=f"bold green{bg}")
            content.append(f" ⏸{format_duration(non_green_time):>5}", style=f"bold red{bg}")
            # Full detail: show percentage active
            if self.summary_detail == "full":
                total_time = green_time + non_green_time
                pct = (green_time / total_time * 100) if total_time > 0 else 0
                content.append(f" {pct:>3.0f}%", style=f"bold green{bg}" if pct >= 50 else f"bold red{bg}")

        # Always show: token usage (from Claude Code)
        # ALIGNMENT: context indicator is always 7 chars " c@NNN%" (or placeholder)
        if self.claude_stats is not None:
            content.append(f" Σ{format_tokens(self.claude_stats.total_tokens):>6}", style=f"bold orange1{bg}")
            # Show current context window usage as percentage (assuming 200K max)
            if self.claude_stats.current_context_tokens > 0:
                max_context = 200_000  # Claude models have 200K context window
                ctx_pct = min(100, self.claude_stats.current_context_tokens / max_context * 100)
                content.append(f" c@{ctx_pct:>3.0f}%", style=f"bold orange1{bg}")
            else:
                content.append(" c@  -%", style=f"dim orange1{bg}")
        else:
            content.append("      - c@  -%", style=f"dim orange1{bg}")

        # Git diff stats (outstanding changes since last commit)
        # ALIGNMENT: Use fixed widths - low/med: 4 chars "Δnn ", full: 16 chars "Δnn +nnnn -nnnn"
        # Large line counts are shortened: 173242 -> "173K", 1234567 -> "1.2M"
        if self.git_diff_stats:
            files, ins, dels = self.git_diff_stats
            if self.summary_detail == "full":
                # Full: show files and lines with fixed widths
                content.append(f" Δ{files:>2}", style=f"bold magenta{bg}")
                content.append(f" +{format_line_count(ins):>4}", style=f"bold green{bg}")
                content.append(f" -{format_line_count(dels):>4}", style=f"bold red{bg}")
            else:
                # Compact: just files changed (fixed 4 char width)
                content.append(f" Δ{files:>2}", style=f"bold magenta{bg}" if files > 0 else f"dim{bg}")
        else:
            # Placeholder matching width for alignment
            if self.summary_detail == "full":
                content.append("  Δ-  +   -  -  ", style=f"dim{bg}")
            else:
                content.append("  Δ-", style=f"dim{bg}")

        # Med/Full detail: add median work time (p50 autonomous work duration)
        if self.summary_detail in ("med", "full"):
            work_str = format_duration(median_work) if median_work > 0 else "0s"
            content.append(f" ⏱{work_str:>5}", style=f"bold blue{bg}")

        # Always show: permission mode, human interactions, robot supervisions
        content.append(f" {perm_emoji}", style=f"bold white{bg}")
        # Human interaction count = total interactions - robot interventions
        if self.claude_stats is not None:
            human_count = max(0, self.claude_stats.interaction_count - stats.steers_count)
            content.append(f" 👤{human_count:>3}", style=f"bold yellow{bg}")
        else:
            content.append(" 👤  -", style=f"dim yellow{bg}")
        # Robot supervision count (from daemon steers) - 3 digit padding
        content.append(f" 🤖{stats.steers_count:>3}", style=f"bold cyan{bg}")

        # Standing orders indicator (after supervision count) - always show for alignment
        if s.standing_instructions:
            if s.standing_orders_complete:
                content.append(" ✓", style=f"bold green{bg}")
            elif s.standing_instructions_preset:
                # Show preset name (truncated to fit)
                preset_display = f" {s.standing_instructions_preset[:8]}"
                content.append(preset_display, style=f"bold cyan{bg}")
            else:
                content.append(" 📋", style=f"bold yellow{bg}")
        else:
            content.append(" ➖", style=f"bold dim{bg}")  # No instructions indicator

        if not self.expanded:
            # Compact view: show standing orders or current activity
            content.append(" │ ", style=f"bold dim{bg}")
            # Calculate remaining space for standing orders/activity
            current_len = len(content.plain)
            remaining = max(20, term_width - current_len - 2)

            if s.standing_instructions:
                # Show standing orders with completion indicator
                if s.standing_orders_complete:
                    style = f"bold green{bg}"
                    prefix = "✓ "
                elif s.standing_instructions_preset:
                    style = f"bold cyan{bg}"
                    prefix = f"{s.standing_instructions_preset}: "
                else:
                    style = f"bold italic yellow{bg}"
                    prefix = ""
                display_text = f"{prefix}{format_standing_instructions(s.standing_instructions, remaining - len(prefix))}"
                content.append(display_text[:remaining], style=style)
            else:
                content.append(self.current_activity[:remaining], style=f"bold italic{bg}")
            # Pad to fill terminal width
            current_len = len(content.plain)
            if current_len < term_width:
                content.append(" " * (term_width - current_len), style=f"{bg}")
            return content

        # Pad header line to full width before adding expanded content
        current_len = len(content.plain)
        if current_len < term_width:
            content.append(" " * (term_width - current_len), style=f"{bg}")

        # Expanded view: show standing instructions first if set
        if s.standing_instructions:
            content.append("\n")
            content.append("  ")
            display_instr = format_standing_instructions(s.standing_instructions)
            if s.standing_orders_complete:
                content.append("│ ", style="bold green")
                content.append("✓ ", style="bold green")
                content.append(display_instr, style="green")
            elif s.standing_instructions_preset:
                content.append("│ ", style="cyan")
                content.append(f"{s.standing_instructions_preset}: ", style="bold cyan")
                content.append(display_instr, style="cyan")
            else:
                content.append("│ ", style="cyan")
                content.append("📋 ", style="yellow")
                content.append(display_instr, style="italic yellow")

        # Expanded view: show pane content based on detail_lines setting
        lines_to_show = self.detail_lines
        # Account for standing instructions line if present
        if s.standing_instructions:
            lines_to_show = max(1, lines_to_show - 1)

        # Get the last N lines of pane content
        pane_lines = self.pane_content[-lines_to_show:] if self.pane_content else []

        # Show pane output lines
        for line in pane_lines:
            content.append("\n")
            content.append("  ")  # Indent
            # Truncate long lines and style based on content
            display_line = line[:100] + "..." if len(line) > 100 else line
            prefix_style, content_style = style_pane_line(line)
            content.append("│ ", style=prefix_style)
            content.append(display_line, style=content_style)

        # If no pane content and no standing instructions shown above, show placeholder
        if not pane_lines and not s.standing_instructions:
            content.append("\n")
            content.append("  ")  # Indent
            content.append("│ ", style="cyan")
            content.append("(no output)", style="dim italic")

        return content


class PreviewPane(Static):
    """Preview pane showing focused agent's terminal output in list+preview mode."""

    content_lines: reactive[List[str]] = reactive(list, init=False)
    session_name: str = ""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.content_lines = []

    def render(self) -> Text:
        content = Text()
        # Use widget width for layout, with sensible fallback
        pane_width = self.size.width if self.size.width > 0 else 80

        # Header with session name - pad to full pane width
        header = f"─── {self.session_name} " if self.session_name else "─── Preview "
        content.append(header, style="bold cyan")
        content.append("─" * max(0, pane_width - len(header)), style="dim")
        content.append("\n")

        if not self.content_lines:
            content.append("(no output)", style="dim italic")
        else:
            # Calculate available lines based on widget height
            # Reserve 2 lines for header and some padding
            available_lines = max(10, self.size.height - 2) if self.size.height > 0 else 30
            # Show last N lines of output - plain text, no decoration
            # Truncate lines to pane width to match tmux display
            max_line_len = max(pane_width - 1, 40)  # Leave room for newline, minimum 40
            for line in self.content_lines[-available_lines:]:
                # Truncate long lines to pane width
                display_line = line[:max_line_len] if len(line) > max_line_len else line
                content.append(display_line + "\n")

        return content

    def update_from_widget(self, widget: "SessionSummary") -> None:
        """Update preview content from a SessionSummary widget."""
        self.session_name = widget.session.name
        self.content_lines = list(widget.pane_content) if widget.pane_content else []
        self.refresh()


class CommandBar(Static):
    """Inline command bar for sending instructions to agents.

    Supports single-line (Input) and multi-line (TextArea) modes.
    Toggle with Ctrl+E. Send with Enter (single) or Ctrl+Enter (multi).
    Use Ctrl+O to set as standing order instead of sending.

    Modes:
    - "send": Default mode for sending instructions to an agent
    - "standing_orders": Mode for editing standing orders for an agent
    - "new_agent_dir": First step of new agent creation - enter working directory
    - "new_agent_name": Second step of new agent creation - enter agent name
    - "new_agent_perms": Third step of new agent creation - choose permission mode

    Key handling is done via on_key() since Input/TextArea consume most keys.
    """

    expanded = reactive(False)  # Toggle single/multi-line mode
    target_session: Optional[str] = None
    mode: str = "send"  # "send", "standing_orders", "new_agent_dir", "new_agent_name", or "new_agent_perms"
    new_agent_dir: Optional[str] = None  # Store directory between steps
    new_agent_name: Optional[str] = None  # Store name between steps

    class SendRequested(Message):
        """Message sent when user wants to send text to a session."""
        def __init__(self, session_name: str, text: str):
            super().__init__()
            self.session_name = session_name
            self.text = text

    class StandingOrderRequested(Message):
        """Message sent when user wants to set a standing order."""
        def __init__(self, session_name: str, text: str):
            super().__init__()
            self.session_name = session_name
            self.text = text

    class NewAgentRequested(Message):
        """Message sent when user wants to create a new agent."""
        def __init__(self, agent_name: str, directory: Optional[str] = None, bypass_permissions: bool = False):
            super().__init__()
            self.agent_name = agent_name
            self.directory = directory
            self.bypass_permissions = bypass_permissions

    def compose(self) -> ComposeResult:
        """Create command bar widgets."""
        with Horizontal(id="cmd-bar-container"):
            yield Label("", id="target-label")
            yield Input(id="cmd-input", placeholder="Type instruction (Enter to send)...", disabled=True)
            yield TextArea(id="cmd-textarea", classes="hidden", disabled=True)
            yield Label("[^E]", id="expand-hint")

    def on_mount(self) -> None:
        """Initialize command bar state."""
        self._update_target_label()
        # Ensure widgets start disabled to prevent auto-focus
        self.query_one("#cmd-input", Input).disabled = True
        self.query_one("#cmd-textarea", TextArea).disabled = True

    def _update_target_label(self) -> None:
        """Update the target session label based on mode."""
        label = self.query_one("#target-label", Label)
        input_widget = self.query_one("#cmd-input", Input)

        if self.mode == "new_agent_dir":
            label.update("[New Agent: Directory] ")
            input_widget.placeholder = "Enter working directory path..."
        elif self.mode == "new_agent_name":
            label.update("[New Agent: Name] ")
            input_widget.placeholder = "Enter agent name (or Enter to accept default)..."
        elif self.mode == "new_agent_perms":
            label.update("[New Agent: Permissions] ")
            input_widget.placeholder = "Type 'bypass' for --dangerously-skip-permissions, or Enter for normal..."
        elif self.mode == "standing_orders":
            if self.target_session:
                label.update(f"[{self.target_session} Standing Orders] ")
            else:
                label.update("[Standing Orders] ")
            input_widget.placeholder = "Enter standing orders (or empty to clear)..."
        elif self.target_session:
            label.update(f"[{self.target_session}] ")
            input_widget.placeholder = "Type instruction (Enter to send)..."
        else:
            label.update("[no session] ")
            input_widget.placeholder = "Type instruction (Enter to send)..."

    def set_target(self, session_name: Optional[str]) -> None:
        """Set the target session for commands."""
        self.target_session = session_name
        self.mode = "send"  # Reset to send mode when target changes
        self._update_target_label()

    def set_mode(self, mode: str) -> None:
        """Set the command bar mode ('send' or 'new_agent')."""
        self.mode = mode
        self._update_target_label()

    def watch_expanded(self, expanded: bool) -> None:
        """Toggle between single-line and multi-line mode."""
        input_widget = self.query_one("#cmd-input", Input)
        textarea = self.query_one("#cmd-textarea", TextArea)

        if expanded:
            # Switch to multi-line
            input_widget.add_class("hidden")
            input_widget.disabled = True
            textarea.remove_class("hidden")
            textarea.disabled = False
            # Transfer content
            textarea.text = input_widget.value
            input_widget.value = ""
            textarea.focus()
        else:
            # Switch to single-line
            textarea.add_class("hidden")
            textarea.disabled = True
            input_widget.remove_class("hidden")
            input_widget.disabled = False
            # Transfer content (first line only for single-line)
            if textarea.text:
                first_line = textarea.text.split('\n')[0]
                input_widget.value = first_line
            textarea.text = ""
            input_widget.focus()

    def on_key(self, event: events.Key) -> None:
        """Handle key events for command bar shortcuts."""
        if event.key == "ctrl+e":
            self.action_toggle_expand()
            event.stop()
        elif event.key == "ctrl+o":
            self.action_set_standing_order()
            event.stop()
        elif event.key == "escape":
            self.action_clear_and_unfocus()
            event.stop()
        elif event.key == "ctrl+enter" and self.expanded:
            self.action_send_multiline()
            event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter in single-line mode."""
        if event.input.id == "cmd-input":
            text = event.value.strip()

            if self.mode == "new_agent_dir":
                # Step 1: Directory entered, validate and move to name step
                # Note: _handle_new_agent_dir sets input value to default name, don't clear it
                self._handle_new_agent_dir(text if text else None)
                return
            elif self.mode == "new_agent_name":
                # Step 2: Name entered (or default accepted), move to permissions step
                # If empty, use the pre-filled default
                name = text if text else event.input.value.strip()
                if not name:
                    # Derive from directory as fallback
                    from pathlib import Path
                    name = Path(self.new_agent_dir).name if self.new_agent_dir else "agent"
                self._handle_new_agent_name(name)
                event.input.value = ""
                return
            elif self.mode == "new_agent_perms":
                # Step 3: Permissions chosen, create agent
                bypass = text.lower().strip() in ("bypass", "y", "yes", "!")
                self._create_new_agent(self.new_agent_name, bypass)
                event.input.value = ""
                self.action_clear_and_unfocus()
                return
            elif self.mode == "standing_orders":
                # Set standing orders (empty string clears them)
                self._set_standing_order(text)
                event.input.value = ""
                self.action_clear_and_unfocus()
                return

            # Default "send" mode
            if not text:
                return
            self._send_message(text)
            event.input.value = ""
            self.action_clear_and_unfocus()

    def _send_message(self, text: str) -> None:
        """Send message to target session."""
        if not self.target_session or not text.strip():
            return
        self.post_message(self.SendRequested(self.target_session, text.strip()))

    def _handle_new_agent_dir(self, directory: Optional[str]) -> None:
        """Handle directory input for new agent creation.

        Validates directory and transitions to name input step.
        """
        from pathlib import Path

        # Expand ~ and resolve path
        if directory:
            dir_path = Path(directory).expanduser().resolve()
            if not dir_path.exists():
                # Try to create it or warn
                self.app.notify(f"Directory does not exist: {dir_path}", severity="warning")
                return
            if not dir_path.is_dir():
                self.app.notify(f"Not a directory: {dir_path}", severity="error")
                return
            self.new_agent_dir = str(dir_path)
        else:
            # Use current working directory if none specified
            self.new_agent_dir = str(Path.cwd())

        # Derive default agent name from directory basename
        default_name = Path(self.new_agent_dir).name

        # Transition to name step
        self.mode = "new_agent_name"
        self._update_target_label()

        # Pre-fill the input with the default name
        input_widget = self.query_one("#cmd-input", Input)
        input_widget.value = default_name

    def _handle_new_agent_name(self, name: str) -> None:
        """Handle name input for new agent creation.

        Stores the name and transitions to permissions step.
        """
        self.new_agent_name = name

        # Transition to permissions step
        self.mode = "new_agent_perms"
        self._update_target_label()

    def _create_new_agent(self, name: str, bypass_permissions: bool = False) -> None:
        """Create a new agent with the given name, directory, and permission mode."""
        self.post_message(self.NewAgentRequested(name, self.new_agent_dir, bypass_permissions))
        # Reset state
        self.new_agent_dir = None
        self.new_agent_name = None
        self.mode = "send"
        self._update_target_label()

    def _set_standing_order(self, text: str) -> None:
        """Set text as standing order."""
        if not self.target_session or not text.strip():
            return
        self.post_message(self.StandingOrderRequested(self.target_session, text.strip()))

    def action_toggle_expand(self) -> None:
        """Toggle between single and multi-line mode."""
        self.expanded = not self.expanded

    def action_send_multiline(self) -> None:
        """Send content from multi-line textarea."""
        textarea = self.query_one("#cmd-textarea", TextArea)
        self._send_message(textarea.text)
        textarea.text = ""
        self.action_clear_and_unfocus()

    def action_set_standing_order(self) -> None:
        """Set current content as standing order."""
        if self.expanded:
            textarea = self.query_one("#cmd-textarea", TextArea)
            self._set_standing_order(textarea.text)
            textarea.text = ""
        else:
            input_widget = self.query_one("#cmd-input", Input)
            self._set_standing_order(input_widget.value)
            input_widget.value = ""

    def action_clear_and_unfocus(self) -> None:
        """Clear input and unfocus command bar."""
        if self.expanded:
            textarea = self.query_one("#cmd-textarea", TextArea)
            textarea.text = ""
        else:
            input_widget = self.query_one("#cmd-input", Input)
            input_widget.value = ""
        # Reset mode and state
        self.mode = "send"
        self.new_agent_dir = None
        self.new_agent_name = None
        self._update_target_label()
        # Let parent handle unfocus
        self.post_message(self.ClearRequested())

    def focus_input(self) -> None:
        """Focus the command bar input and enable it."""
        input_widget = self.query_one("#cmd-input", Input)
        input_widget.disabled = False
        input_widget.focus()

    class ClearRequested(Message):
        """Message sent when user clears the command bar."""
        pass


class SupervisorTUI(App):
    """Overcode Supervisor TUI"""

    # Disable any size restrictions
    AUTO_FOCUS = None

    CSS = """
    Screen {
        background: $background;
        overflow: hidden;
        height: 100%;
    }

    Header {
        dock: top;
        height: 1;
    }

    #daemon-status {
        height: 1;
        width: 100%;
        background: $panel;
        padding: 0 1;
    }

    #timeline {
        height: auto;
        min-height: 4;
        max-height: 20;
        width: 100%;
        background: $surface;
        padding: 0 1;
        border-bottom: solid $panel;
    }

    #sessions-container {
        height: 1fr;
        width: 100%;
        overflow: auto auto;
        padding: 0;
    }

    /* In list+preview mode, sessions container is compact (auto-size to content) */
    #sessions-container.list-mode {
        height: auto;
        max-height: 30%;
    }

    SessionSummary {
        height: 1;
        width: 100%;
        padding: 0 1;
        margin: 0;
        border: none;
        background: $surface;
        overflow: hidden;
    }

    SessionSummary.expanded {
        height: auto;
        min-height: 2;
        max-height: 55;  /* Support up to 50 lines detail + header/instructions */
        background: #1c1c1c;
        border-bottom: solid #5588aa;
    }

    SessionSummary:hover {
        background: $boost;
    }

    SessionSummary:focus {
        background: #2d4a5a;
        text-style: bold;
    }

    /* .selected class preserves highlight when app loses focus */
    SessionSummary.selected {
        background: #2d4a5a;
        text-style: bold;
    }

    #help-text {
        dock: bottom;
        height: 1;
        width: 100%;
        background: $panel;
        color: $text-muted;
        padding: 0 1;
    }

    #help-overlay {
        display: none;
        layer: above;
        dock: top;
        width: 100%;
        height: 100%;
        background: $surface 90%;
        padding: 1 2;
        overflow-y: auto;
    }

    #help-overlay.visible {
        display: block;
    }

    #daemon-panel {
        display: none;
        height: auto;
        min-height: 2;
        max-height: 12;
        width: 100%;
        background: $surface;
        padding: 0 1;
        border-bottom: solid $panel;
    }

    CommandBar {
        dock: bottom;
        height: auto;
        min-height: 1;
        max-height: 8;
        width: 100%;
        background: $surface;
        border-top: solid $primary;
        padding: 0 1;
        display: none;  /* Hidden by default, shown with 'i' key */
    }

    CommandBar.visible {
        display: block;
    }

    #cmd-bar-container {
        width: 100%;
        height: auto;
    }

    #target-label {
        width: auto;
        color: $primary;
        text-style: bold;
    }

    #cmd-input {
        width: 1fr;
        min-width: 20;
    }

    #cmd-input.hidden {
        display: none;
    }

    #cmd-textarea {
        width: 1fr;
        min-width: 20;
        height: 4;
    }

    #cmd-textarea.hidden {
        display: none;
    }

    #expand-hint {
        width: auto;
        color: $text-muted;
        padding-left: 1;
    }

    /* List mode - always collapsed */
    /* List mode: compact single-line, no borders/dividers */
    SessionSummary.list-mode {
        height: 1;
        border: none;
        margin: 0;
        padding: 0 1;
    }

    /* Preview pane - hidden by default, shown via .visible class */
    #preview-pane {
        display: none;
        height: 1fr;
        border-top: solid $primary;
        padding: 0 1;
        background: $surface;
        overflow-y: auto;
    }

    #preview-pane.visible {
        display: block;
    }

    /* Focused indicator in list mode */
    SessionSummary:focus.list-mode {
        background: $accent;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("h", "toggle_help", "Help"),
        ("question_mark", "toggle_help", "Help"),
        ("d", "toggle_daemon", "Daemon panel"),
        ("t", "toggle_timeline", "Toggle timeline"),
        ("v", "cycle_detail", "Cycle detail"),
        ("s", "cycle_summary", "Summary detail"),
        ("e", "expand_all", "Expand all"),
        ("c", "collapse_all", "Collapse all"),
        ("space", "toggle_focused", "Toggle"),
        # Navigation between agents
        ("j", "focus_next_session", "Next"),
        ("k", "focus_previous_session", "Prev"),
        ("down", "focus_next_session", "Next"),
        ("up", "focus_previous_session", "Prev"),
        # View mode toggle
        ("m", "toggle_view_mode", "Mode"),
        # Command bar (send instructions to agents)
        ("i", "focus_command_bar", "Send"),
        ("colon", "focus_command_bar", "Send"),
        ("o", "focus_standing_orders", "Standing orders"),
        # Daemon controls (simple keys that work everywhere)
        ("left_square_bracket", "supervisor_start", "Start supervisor"),
        ("right_square_bracket", "supervisor_stop", "Stop supervisor"),
        ("backslash", "monitor_restart", "Restart monitor"),
        # Manual refresh (useful in diagnostics mode)
        ("r", "manual_refresh", "Refresh"),
        # Agent management
        ("x", "kill_focused", "Kill agent"),
        ("n", "new_agent", "New agent"),
        # Send Enter to focused agent (for approvals)
        ("enter", "send_enter_to_focused", "Send Enter"),
        # Send number keys 1-5 to focused agent (for numbered prompts)
        ("1", "send_1_to_focused", "Send 1"),
        ("2", "send_2_to_focused", "Send 2"),
        ("3", "send_3_to_focused", "Send 3"),
        ("4", "send_4_to_focused", "Send 4"),
        ("5", "send_5_to_focused", "Send 5"),
        # Copy mode - disable mouse capture for native terminal selection
        ("y", "toggle_copy_mode", "Copy mode"),
        # Tmux sync - sync navigation to external tmux pane
        ("p", "toggle_tmux_sync", "Pane sync"),
        # Web server toggle
        ("w", "toggle_web_server", "Web dashboard"),
        # Sleep mode toggle - mark agent as paused (excluded from stats)
        ("z", "toggle_sleep", "Sleep mode"),
    ]

    # Detail level cycles through 5, 10, 20, 50 lines
    DETAIL_LEVELS = [5, 10, 20, 50]
    # Summary detail levels: low (minimal), med (timing), full (all + repo)
    SUMMARY_LEVELS = ["low", "med", "full"]

    sessions: reactive[List[Session]] = reactive(list)
    view_mode: reactive[str] = reactive("tree")  # "tree" or "list_preview"
    tmux_sync: reactive[bool] = reactive(False)  # sync navigation to external tmux pane

    def __init__(self, tmux_session: str = "agents", diagnostics: bool = False):
        super().__init__()
        self.tmux_session = tmux_session
        self.diagnostics = diagnostics  # Disable all auto-refresh timers
        self.session_manager = SessionManager()
        self.launcher = ClaudeLauncher(tmux_session)
        self.status_detector = StatusDetector(tmux_session)
        # Track expanded state per session ID to preserve across refreshes
        self.expanded_states: dict[str, bool] = {}
        # Max repo:branch width for alignment in full detail mode
        self.max_repo_info_width: int = 18

        # Load persisted TUI preferences
        self._prefs = TUIPreferences.load(tmux_session)

        # Current detail level index (cycles through DETAIL_LEVELS)
        # Initialize from saved preferences
        try:
            self.detail_level_index = self.DETAIL_LEVELS.index(self._prefs.detail_lines)
        except ValueError:
            self.detail_level_index = 0  # Default to 5 lines

        # Current summary detail level index (cycles through SUMMARY_LEVELS)
        # Initialize from saved preferences
        try:
            self.summary_level_index = self.SUMMARY_LEVELS.index(self._prefs.summary_detail)
        except ValueError:
            self.summary_level_index = 0  # Default to "low"

        # Track focused session for navigation
        self.focused_session_index = 0
        # Track previous status of each session for detecting transitions to stalled state
        self._previous_statuses: dict[str, str] = {}
        # Session cache to avoid disk I/O on every status update (250ms interval)
        self._sessions_cache: dict[str, Session] = {}
        self._sessions_cache_time: float = 0
        self._sessions_cache_ttl: float = 1.0  # 1 second TTL
        # Flag to prevent overlapping async status updates
        self._status_update_in_progress = False
        # Track if we've warned about multiple daemons (to avoid spam)
        self._multiple_daemon_warning_shown = False
        # Pending kill confirmation (session name, timestamp)
        self._pending_kill: tuple[str, float] | None = None
        # Tmux interface for sync operations
        self._tmux = RealTmux()
        # Initialize tmux_sync from preferences
        self.tmux_sync = self._prefs.tmux_sync

    def compose(self) -> ComposeResult:
        """Create child widgets"""
        yield Header(show_clock=True)
        yield DaemonStatusBar(tmux_session=self.tmux_session, session_manager=self.session_manager, id="daemon-status")
        yield StatusTimeline([], tmux_session=self.tmux_session, id="timeline")
        yield DaemonPanel(tmux_session=self.tmux_session, id="daemon-panel")
        yield ScrollableContainer(id="sessions-container")
        yield PreviewPane(id="preview-pane")
        yield CommandBar(id="command-bar")
        yield HelpOverlay(id="help-overlay")
        yield Static(
            "h:Help | q:Quit | j/k:Nav | i:Send | n:New | x:Kill | space | m:Mode | p:Sync | d:Daemon | t:Timeline",
            id="help-text"
        )

    def on_mount(self) -> None:
        """Called when app starts"""
        self.title = "Overcode Monitor"
        self._update_subtitle()

        # Auto-start Monitor Daemon if not running
        self._ensure_monitor_daemon()

        # Disable command bar inputs to prevent auto-focus capture
        try:
            cmd_bar = self.query_one("#command-bar", CommandBar)
            cmd_bar.query_one("#cmd-input", Input).disabled = True
            cmd_bar.query_one("#cmd-textarea", TextArea).disabled = True
            # Clear any focus from the command bar
            self.set_focus(None)
        except NoMatches:
            pass

        # Apply persisted preferences
        try:
            timeline = self.query_one("#timeline", StatusTimeline)
            timeline.display = self._prefs.timeline_visible
        except NoMatches:
            pass

        try:
            daemon_panel = self.query_one("#daemon-panel", DaemonPanel)
            daemon_panel.display = self._prefs.daemon_panel_visible
        except NoMatches:
            pass

        # Set view_mode from preferences (triggers watch_view_mode)
        self.view_mode = self._prefs.view_mode

        self.refresh_sessions()
        self.update_daemon_status()
        self.update_timeline()
        # Schedule initial status fetch after widgets are mounted (small delay ensures DOM is ready)
        self.set_timer(0.1, self.update_all_statuses)
        # Select first agent for preview pane (slightly longer delay to ensure widgets exist)
        self.set_timer(0.2, self._select_first_agent)

        if self.diagnostics:
            # DIAGNOSTICS MODE: No auto-refresh timers
            self._update_subtitle()  # Will include [DIAGNOSTICS]
            self.notify(
                "DIAGNOSTICS MODE: All auto-refresh disabled. Press 'r' to manually refresh.",
                severity="warning",
                timeout=10
            )
        else:
            # Normal mode: Set up all timers
            # Refresh session list every 10 seconds
            self.set_interval(10, self.refresh_sessions)
            # Update status very frequently for real-time detail view
            self.set_interval(0.25, self.update_all_statuses)
            # Update daemon status every 5 seconds
            self.set_interval(5, self.update_daemon_status)
            # Update timeline every 30 seconds
            self.set_interval(30, self.update_timeline)

    def update_daemon_status(self) -> None:
        """Update daemon status bar"""
        try:
            daemon_bar = self.query_one("#daemon-status", DaemonStatusBar)
            daemon_bar.update_status()
        except NoMatches:
            pass

        # Check for multiple daemon processes (potential time tracking bug)
        daemon_count = count_daemon_processes("monitor_daemon", session=self.tmux_session)
        if daemon_count > 1 and not self._multiple_daemon_warning_shown:
            self._multiple_daemon_warning_shown = True
            self.notify(
                f"WARNING: {daemon_count} monitor daemons detected! "
                "This causes time tracking bugs. Press \\ to restart daemon.",
                severity="error",
                timeout=30
            )
        elif daemon_count <= 1:
            # Reset warning flag when back to normal
            self._multiple_daemon_warning_shown = False

    def update_timeline(self) -> None:
        """Update the status timeline widget"""
        try:
            timeline = self.query_one("#timeline", StatusTimeline)
            timeline.update_history(self.sessions)
        except NoMatches:
            pass

    def _save_prefs(self) -> None:
        """Save current TUI preferences to disk."""
        self._prefs.save(self.tmux_session)

    def action_toggle_timeline(self) -> None:
        """Toggle timeline visibility"""
        try:
            timeline = self.query_one("#timeline", StatusTimeline)
            timeline.display = not timeline.display
            self._prefs.timeline_visible = timeline.display
            self._save_prefs()
            state = "shown" if timeline.display else "hidden"
            self.notify(f"Timeline {state}", severity="information")
        except NoMatches:
            pass

    def action_toggle_help(self) -> None:
        """Toggle help overlay visibility"""
        try:
            help_overlay = self.query_one("#help-overlay", HelpOverlay)
            if help_overlay.has_class("visible"):
                help_overlay.remove_class("visible")
            else:
                help_overlay.add_class("visible")
        except NoMatches:
            pass

    def action_manual_refresh(self) -> None:
        """Manually trigger a full refresh (useful in diagnostics mode)"""
        self.refresh_sessions()
        self.update_all_statuses()
        self.update_daemon_status()
        self.update_timeline()
        self.notify("Refreshed", severity="information", timeout=2)

    def on_resize(self) -> None:
        """Handle terminal resize events"""
        self.refresh()
        self.update_session_widgets()

    def refresh_sessions(self) -> None:
        """Refresh session list (checks for new/removed sessions)

        Uses launcher.list_sessions() to detect terminated sessions
        (tmux windows that no longer exist, e.g., after machine reboot).
        """
        self._invalidate_sessions_cache()  # Force cache refresh
        self.sessions = self.launcher.list_sessions()
        # Calculate max repo:branch width for alignment in full detail mode
        self.max_repo_info_width = max(
            (len(f"{s.repo_name or 'n/a'}:{s.branch or 'n/a'}") for s in self.sessions),
            default=18
        )
        self.max_repo_info_width = max(self.max_repo_info_width, 10)  # Minimum 10 chars
        self.update_session_widgets()
        # NOTE: Don't call update_timeline() here - it has its own 30s interval
        # and reading log files during session refresh causes UI stutter

    def _get_cached_sessions(self) -> dict[str, Session]:
        """Get sessions with caching to reduce disk I/O.

        Returns cached session data if TTL hasn't expired, otherwise
        reloads from disk and updates the cache.
        """
        import time
        now = time.time()
        if now - self._sessions_cache_time > self._sessions_cache_ttl:
            # Cache expired, reload from disk
            self._sessions_cache = {s.id: s for s in self.session_manager.list_sessions()}
            self._sessions_cache_time = now
        return self._sessions_cache

    def _invalidate_sessions_cache(self) -> None:
        """Invalidate the sessions cache to force reload on next access."""
        self._sessions_cache_time = 0

    def update_all_statuses(self) -> None:
        """Trigger async status update for all session widgets.

        This is NON-BLOCKING - it kicks off a background worker that fetches
        all statuses in parallel, then updates widgets when done.
        """
        # Skip if an update is already in progress
        if self._status_update_in_progress:
            return
        self._status_update_in_progress = True

        # Gather widget info needed for the background fetch
        widgets = list(self.query(SessionSummary))
        if not widgets:
            self._status_update_in_progress = False
            return

        # Kick off async status fetch
        self._fetch_statuses_async(widgets)

    @work(thread=True, exclusive=True)
    def _fetch_statuses_async(self, widgets: list) -> None:
        """Fetch all statuses in background thread, then update UI.

        Uses ThreadPoolExecutor to parallelize tmux calls within the worker.
        The @work decorator runs this in a background thread so it doesn't
        block the main event loop.
        """
        try:
            # Load fresh session data (this does file I/O but we're in a thread)
            fresh_sessions = {s.id: s for s in self.session_manager.list_sessions()}

            # Build list of sessions to check (use fresh data if available)
            sessions_to_check = []
            for widget in widgets:
                session = fresh_sessions.get(widget.session.id, widget.session)
                sessions_to_check.append((widget.session.id, session))

            # Fetch all statuses AND claude stats AND git diff stats in parallel
            def fetch_all(session):
                """Fetch status, stats, and git diff for a session (runs in thread pool)."""
                try:
                    # For terminated sessions, return status directly without checking tmux
                    if session.status == "terminated":
                        status_result = ("terminated", "(tmux window no longer exists)", "")
                    else:
                        status_result = self.status_detector.detect_status(session)
                    # Also fetch claude stats here (heavy file I/O)
                    claude_stats = get_session_stats(session)
                    # Fetch git diff stats
                    git_diff = None
                    if session.start_directory:
                        git_diff = get_git_diff_stats(session.start_directory)
                    return (status_result, claude_stats, git_diff)
                except Exception:
                    return ((StatusDetector.STATUS_WAITING_USER, "Error", ""), None, None)

            sessions = [s for _, s in sessions_to_check]
            with ThreadPoolExecutor(max_workers=min(8, len(sessions))) as executor:
                results = list(executor.map(fetch_all, sessions))

            # Package results with session IDs
            status_results = {}
            stats_results = {}
            git_diff_results = {}
            for (session_id, _), (status_result, claude_stats, git_diff) in zip(sessions_to_check, results):
                status_results[session_id] = status_result
                stats_results[session_id] = claude_stats
                git_diff_results[session_id] = git_diff

            # Update UI on main thread
            self.call_from_thread(self._apply_status_results, status_results, stats_results, git_diff_results, fresh_sessions)
        finally:
            self._status_update_in_progress = False

    def _apply_status_results(self, status_results: dict, stats_results: dict, git_diff_results: dict, fresh_sessions: dict) -> None:
        """Apply fetched status results to widgets (runs on main thread).

        All data has been pre-fetched in background - this just updates widget state.
        No file I/O happens here.
        """
        prefs_changed = False

        for widget in self.query(SessionSummary):
            session_id = widget.session.id

            # Update widget's session with fresh data
            if session_id in fresh_sessions:
                widget.session = fresh_sessions[session_id]

            # Apply status and stats if we have results for this widget
            if session_id in status_results:
                status, activity, content = status_results[session_id]
                claude_stats = stats_results.get(session_id)
                git_diff = git_diff_results.get(session_id)

                # Detect transitions TO stalled state (waiting_user)
                prev_status = self._previous_statuses.get(session_id)
                if status == STATUS_WAITING_USER and prev_status != STATUS_WAITING_USER:
                    # Agent just became stalled - mark as unvisited
                    self._prefs.visited_stalled_agents.discard(session_id)
                    prefs_changed = True

                # Update previous status for next round
                self._previous_statuses[session_id] = status

                # Update widget's unvisited state
                is_unvisited_stalled = (
                    status == STATUS_WAITING_USER and
                    session_id not in self._prefs.visited_stalled_agents
                )
                widget.is_unvisited_stalled = is_unvisited_stalled

                widget.apply_status_no_refresh(status, activity, content, claude_stats, git_diff)
                widget.refresh()  # Refresh each widget to repaint

        # Save preferences if we marked any agents as unvisited
        if prefs_changed:
            self._save_prefs()

        # Update preview pane if in list_preview mode
        if self.view_mode == "list_preview":
            self._update_preview()

    def update_session_widgets(self) -> None:
        """Update the session display incrementally.

        Only adds/removes widgets when sessions change, rather than
        destroying and recreating all widgets (which causes UI stutter).
        """
        container = self.query_one("#sessions-container", ScrollableContainer)

        # Get existing widgets and their session IDs
        existing_widgets = {w.session.id: w for w in self.query(SessionSummary)}
        new_session_ids = {s.id for s in self.sessions}
        existing_session_ids = set(existing_widgets.keys())

        # Check if we have an empty message widget that needs removal
        # (Static widgets that aren't SessionSummary)
        has_empty_message = any(
            isinstance(w, Static) and not isinstance(w, SessionSummary)
            for w in container.children
        )

        # If sessions changed or we need to show/hide empty message, do incremental update
        sessions_added = new_session_ids - existing_session_ids
        sessions_removed = existing_session_ids - new_session_ids

        if not sessions_added and not sessions_removed and not has_empty_message:
            # No structural changes needed - just update session data in existing widgets
            session_map = {s.id: s for s in self.sessions}
            for widget in existing_widgets.values():
                if widget.session.id in session_map:
                    widget.session = session_map[widget.session.id]
            return

        # Remove widgets for deleted sessions
        for session_id in sessions_removed:
            widget = existing_widgets[session_id]
            widget.remove()

        # Clear empty message if we now have sessions
        if has_empty_message and self.sessions:
            container.remove_children()

        # Handle empty state
        if not self.sessions:
            if not has_empty_message:
                container.remove_children()
                container.mount(Static(
                    "\n  No active sessions.\n\n  Launch a session with:\n  overcode launch --name my-agent code\n",
                    classes="dim"
                ))
            return

        # Add widgets for new sessions
        for session in self.sessions:
            if session.id in sessions_added:
                widget = SessionSummary(session, self.status_detector)
                # Restore expanded state if we have it saved
                if session.id in self.expanded_states:
                    widget.expanded = self.expanded_states[session.id]
                # Apply current detail level
                widget.detail_lines = self.DETAIL_LEVELS[self.detail_level_index]
                # Apply current summary detail level
                widget.summary_detail = self.SUMMARY_LEVELS[self.summary_level_index]
                # Apply list-mode class if in list_preview view
                if self.view_mode == "list_preview":
                    widget.add_class("list-mode")
                    widget.expanded = False  # Force collapsed in list mode
                container.mount(widget)
                # NOTE: Don't call update_status() here - it does blocking tmux calls
                # The 250ms interval (update_all_statuses) will update status shortly

        # Reorder widgets to match self.sessions order
        # New widgets are appended at end, but should appear in correct position
        if sessions_added:
            self._reorder_session_widgets(container)

    def action_expand_all(self) -> None:
        """Expand all sessions"""
        for widget in self.query(SessionSummary):
            widget.expanded = True
            self.expanded_states[widget.session.id] = True

    def action_collapse_all(self) -> None:
        """Collapse all sessions"""
        for widget in self.query(SessionSummary):
            widget.expanded = False
            self.expanded_states[widget.session.id] = False

    def action_cycle_detail(self) -> None:
        """Cycle through detail levels (5, 10, 20, 50 lines)"""
        self.detail_level_index = (self.detail_level_index + 1) % len(self.DETAIL_LEVELS)
        new_level = self.DETAIL_LEVELS[self.detail_level_index]

        # Update all session widgets
        for widget in self.query(SessionSummary):
            widget.detail_lines = new_level

        # Save preference
        self._prefs.detail_lines = new_level
        self._save_prefs()

        self.notify(f"Detail: {new_level} lines", severity="information")

    def action_cycle_summary(self) -> None:
        """Cycle through summary detail levels (low, med, full)"""
        self.summary_level_index = (self.summary_level_index + 1) % len(self.SUMMARY_LEVELS)
        new_level = self.SUMMARY_LEVELS[self.summary_level_index]

        # Update all session widgets
        for widget in self.query(SessionSummary):
            widget.summary_detail = new_level

        # Save preference
        self._prefs.summary_detail = new_level
        self._save_prefs()

        self.notify(f"Summary: {new_level}", severity="information")

    def on_session_summary_expanded_changed(self, message: SessionSummary.ExpandedChanged) -> None:
        """Handle expanded state changes from session widgets"""
        self.expanded_states[message.session_id] = message.expanded

    def on_session_summary_stalled_agent_visited(self, message: SessionSummary.StalledAgentVisited) -> None:
        """Handle when user visits a stalled agent - mark as visited"""
        session_id = message.session_id
        self._prefs.visited_stalled_agents.add(session_id)
        self._save_prefs()

        # Update the widget's state
        for widget in self.query(SessionSummary):
            if widget.session.id == session_id:
                widget.is_unvisited_stalled = False
                widget.refresh()
                break

    def on_session_summary_session_selected(self, message: SessionSummary.SessionSelected) -> None:
        """Handle session selection - update .selected class to preserve highlight when unfocused"""
        session_id = message.session_id
        for widget in self.query(SessionSummary):
            if widget.session.id == session_id:
                widget.add_class("selected")
            else:
                widget.remove_class("selected")

    def action_toggle_focused(self) -> None:
        """Toggle expansion of focused session (only in tree mode)"""
        if self.view_mode == "list_preview":
            return  # Don't toggle in list mode
        focused = self.focused
        if isinstance(focused, SessionSummary):
            focused.expanded = not focused.expanded

    def _get_widgets_in_session_order(self) -> List[SessionSummary]:
        """Get session widgets sorted to match self.sessions order.

        query() returns widgets in DOM/mount order, but we want navigation
        to follow self.sessions order for consistency with display.
        """
        widgets = list(self.query(SessionSummary))
        if not widgets:
            return []
        # Build session_id -> order mapping from self.sessions
        session_order = {s.id: i for i, s in enumerate(self.sessions)}
        # Sort widgets by their session's position in self.sessions
        widgets.sort(key=lambda w: session_order.get(w.session.id, 999))
        return widgets

    def _reorder_session_widgets(self, container: ScrollableContainer) -> None:
        """Reorder session widgets in container to match self.sessions order.

        When new widgets are mounted, they're appended at the end.
        This method reorders them to match self.sessions order.
        """
        widgets = {w.session.id: w for w in self.query(SessionSummary)}
        if not widgets:
            return

        # Get desired order from self.sessions
        ordered_widgets = []
        for session in self.sessions:
            if session.id in widgets:
                ordered_widgets.append(widgets[session.id])

        # Reorder by moving each widget to the correct position
        for i, widget in enumerate(ordered_widgets):
            if i == 0:
                # First widget should be at the start
                container.move_child(widget, before=0)
            else:
                # Each subsequent widget should be after the previous one
                container.move_child(widget, after=ordered_widgets[i - 1])

    def action_focus_next_session(self) -> None:
        """Focus the next session in the list."""
        widgets = self._get_widgets_in_session_order()
        if not widgets:
            return
        self.focused_session_index = (self.focused_session_index + 1) % len(widgets)
        target_widget = widgets[self.focused_session_index]
        target_widget.focus()
        if self.view_mode == "list_preview":
            self._update_preview()
        self._sync_tmux_window(target_widget)

    def action_focus_previous_session(self) -> None:
        """Focus the previous session in the list."""
        widgets = self._get_widgets_in_session_order()
        if not widgets:
            return
        self.focused_session_index = (self.focused_session_index - 1) % len(widgets)
        target_widget = widgets[self.focused_session_index]
        target_widget.focus()
        if self.view_mode == "list_preview":
            self._update_preview()
        self._sync_tmux_window(target_widget)

    def action_toggle_view_mode(self) -> None:
        """Toggle between tree and list+preview view modes."""
        if self.view_mode == "tree":
            self.view_mode = "list_preview"
        else:
            self.view_mode = "tree"

        # Save preference
        self._prefs.view_mode = self.view_mode
        self._save_prefs()

    def action_toggle_tmux_sync(self) -> None:
        """Toggle tmux pane sync - syncs navigation to external tmux pane."""
        self.tmux_sync = not self.tmux_sync

        # Save preference
        self._prefs.tmux_sync = self.tmux_sync
        self._save_prefs()

        # Update subtitle to show sync state
        self._update_subtitle()

        # If enabling, sync to currently focused session immediately
        if self.tmux_sync:
            self._sync_tmux_window()

    def _sync_tmux_window(self, widget: Optional["SessionSummary"] = None) -> None:
        """Sync external tmux pane to show the focused session's window.

        Args:
            widget: The session widget to sync to. If None, uses self.focused.
        """
        if not self.tmux_sync:
            return

        try:
            target = widget if widget is not None else self.focused
            if isinstance(target, SessionSummary):
                window_index = target.session.tmux_window
                if window_index is not None:
                    self._tmux.select_window(self.tmux_session, window_index)
        except Exception:
            pass  # Silent fail - don't disrupt navigation

    def watch_view_mode(self, view_mode: str) -> None:
        """React to view mode changes."""
        # Update subtitle to show current mode
        self._update_subtitle()

        try:
            preview = self.query_one("#preview-pane", PreviewPane)
            container = self.query_one("#sessions-container", ScrollableContainer)
            if view_mode == "list_preview":
                # Collapse all sessions, show preview pane
                container.add_class("list-mode")
                for widget in self.query(SessionSummary):
                    widget.add_class("list-mode")
                    widget.expanded = False  # Force collapsed
                preview.add_class("visible")
                self._update_preview()
            else:
                # Restore tree mode, hide preview
                container.remove_class("list-mode")
                for widget in self.query(SessionSummary):
                    widget.remove_class("list-mode")
                preview.remove_class("visible")
        except NoMatches:
            pass

    def _update_subtitle(self) -> None:
        """Update the header subtitle to show session and view mode."""
        mode_label = "Tree" if self.view_mode == "tree" else "List+Preview"
        sync_label = " [Sync]" if self.tmux_sync else ""
        if self.diagnostics:
            self.sub_title = f"{self.tmux_session} [{mode_label}]{sync_label} [DIAGNOSTICS]"
        else:
            self.sub_title = f"{self.tmux_session} [{mode_label}]{sync_label}"

    def _select_first_agent(self) -> None:
        """Select the first agent for initial preview pane display."""
        if self.view_mode != "list_preview":
            return
        try:
            widgets = list(self.query(SessionSummary))
            if widgets:
                self.focused_session_index = 0
                widgets[0].focus()
                self._update_preview()
        except NoMatches:
            pass

    def _update_preview(self) -> None:
        """Update preview pane with focused session's content."""
        try:
            preview = self.query_one("#preview-pane", PreviewPane)
            widgets = self._get_widgets_in_session_order()
            if widgets and 0 <= self.focused_session_index < len(widgets):
                preview.update_from_widget(widgets[self.focused_session_index])
        except NoMatches:
            pass

    def action_focus_command_bar(self) -> None:
        """Focus the command bar for input."""
        try:
            cmd_bar = self.query_one("#command-bar", CommandBar)

            # Show the command bar
            cmd_bar.add_class("visible")

            # Get the currently focused session (if any)
            focused = self.focused
            if isinstance(focused, SessionSummary):
                cmd_bar.set_target(focused.session.name)
            elif not cmd_bar.target_session and self.sessions:
                # Default to first session if none focused
                cmd_bar.set_target(self.sessions[0].name)

            # Enable and focus the input
            cmd_input = cmd_bar.query_one("#cmd-input", Input)
            cmd_input.disabled = False
            cmd_input.focus()
        except NoMatches:
            pass

    def action_focus_standing_orders(self) -> None:
        """Focus the command bar for editing standing orders."""
        try:
            cmd_bar = self.query_one("#command-bar", CommandBar)

            # Show the command bar
            cmd_bar.add_class("visible")

            # Get the currently focused session (if any)
            focused = self.focused
            if isinstance(focused, SessionSummary):
                cmd_bar.set_target(focused.session.name)
                # Pre-fill with existing standing orders
                cmd_input = cmd_bar.query_one("#cmd-input", Input)
                cmd_input.value = focused.session.standing_instructions or ""
            elif not cmd_bar.target_session and self.sessions:
                # Default to first session if none focused
                cmd_bar.set_target(self.sessions[0].name)

            # Set mode to standing_orders
            cmd_bar.set_mode("standing_orders")

            # Enable and focus the input
            cmd_input = cmd_bar.query_one("#cmd-input", Input)
            cmd_input.disabled = False
            cmd_input.focus()
        except NoMatches:
            pass

    def on_command_bar_send_requested(self, message: CommandBar.SendRequested) -> None:
        """Handle send request from command bar."""
        from datetime import datetime

        launcher = ClaudeLauncher(
            tmux_session=self.tmux_session,
            session_manager=self.session_manager
        )
        success = launcher.send_to_session(message.session_name, message.text)
        if success:
            # Reset the state timer immediately so UI shows instant feedback
            session = self.session_manager.get_session_by_name(message.session_name)
            if session:
                self.session_manager.update_stats(
                    session.id,
                    state_since=datetime.now().isoformat()
                )
            self._invalidate_sessions_cache()  # Refresh to show updated stats
            self.notify(f"Sent to {message.session_name}")
        else:
            self.notify(f"Failed to send to {message.session_name}", severity="error")

    def on_command_bar_standing_order_requested(self, message: CommandBar.StandingOrderRequested) -> None:
        """Handle standing order request from command bar."""
        session = self.session_manager.get_session_by_name(message.session_name)
        if session:
            self.session_manager.set_standing_instructions(session.id, message.text)
            self.notify(f"Standing order set for {message.session_name}")
            # Refresh session list to show updated standing order
            self.refresh_sessions()
        else:
            self.notify(f"Session '{message.session_name}' not found", severity="error")

    def on_command_bar_clear_requested(self, message: CommandBar.ClearRequested) -> None:
        """Handle clear request - hide and unfocus command bar."""
        try:
            # Disable and hide the command bar
            cmd_bar = self.query_one("#command-bar", CommandBar)
            target_session_name = cmd_bar.target_session  # Remember before disabling
            cmd_bar.query_one("#cmd-input", Input).disabled = True
            cmd_bar.query_one("#cmd-textarea", TextArea).disabled = True
            cmd_bar.remove_class("visible")

            # Focus the targeted session (not first session) to keep preview on it
            if self.sessions:
                widgets = self._get_widgets_in_session_order()
                if widgets:
                    # Find widget matching target session, fall back to current index
                    target_widget = None
                    for i, w in enumerate(widgets):
                        if w.session.name == target_session_name:
                            target_widget = w
                            self.focused_session_index = i
                            break
                    if target_widget:
                        target_widget.focus()
                    else:
                        widgets[self.focused_session_index].focus()
                    if self.view_mode == "list_preview":
                        self._update_preview()
        except NoMatches:
            pass

    def on_command_bar_new_agent_requested(self, message: CommandBar.NewAgentRequested) -> None:
        """Handle new agent creation request."""
        agent_name = message.agent_name
        directory = message.directory
        bypass_permissions = message.bypass_permissions

        # Validate name (no spaces, reasonable length)
        if not agent_name or len(agent_name) > 50:
            self.notify("Invalid agent name", severity="error")
            return

        if ' ' in agent_name:
            self.notify("Agent name cannot contain spaces", severity="error")
            return

        # Check if agent with this name already exists
        existing = self.session_manager.get_session_by_name(agent_name)
        if existing:
            self.notify(f"Agent '{agent_name}' already exists", severity="error")
            return

        # Create new agent using launcher
        launcher = ClaudeLauncher(
            tmux_session=self.tmux_session,
            session_manager=self.session_manager
        )

        try:
            launcher.launch(
                name=agent_name,
                start_directory=directory,
                dangerously_skip_permissions=bypass_permissions
            )
            dir_info = f" in {directory}" if directory else ""
            perm_info = " (bypass mode)" if bypass_permissions else ""
            self.notify(f"Created agent: {agent_name}{dir_info}{perm_info}", severity="information")
            # Refresh to show new agent
            self.refresh_sessions()
        except Exception as e:
            self.notify(f"Failed to create agent: {e}", severity="error")

    def action_toggle_daemon(self) -> None:
        """Toggle daemon panel visibility (like timeline)."""
        try:
            daemon_panel = self.query_one("#daemon-panel", DaemonPanel)
            daemon_panel.display = not daemon_panel.display
            if daemon_panel.display:
                # Force immediate refresh when becoming visible
                daemon_panel._refresh_logs()
            # Save preference
            self._prefs.daemon_panel_visible = daemon_panel.display
            self._save_prefs()
            state = "shown" if daemon_panel.display else "hidden"
            self.notify(f"Daemon panel {state}", severity="information")
        except NoMatches:
            pass

    def action_supervisor_start(self) -> None:
        """Start the Supervisor Daemon (handles Claude orchestration)."""
        # Ensure Monitor Daemon is running first (Supervisor depends on it)
        if not is_monitor_daemon_running(self.tmux_session):
            self._ensure_monitor_daemon()
            import time
            time.sleep(1.0)

        if is_supervisor_daemon_running(self.tmux_session):
            self.notify("Supervisor Daemon already running", severity="warning")
            return

        try:
            panel = self.query_one("#daemon-panel", DaemonPanel)
            panel.log_lines.append(">>> Starting Supervisor Daemon...")
        except NoMatches:
            pass

        try:
            subprocess.Popen(
                [sys.executable, "-m", "overcode.supervisor_daemon",
                 "--session", self.tmux_session],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.notify("Started Supervisor Daemon", severity="information")
            self.set_timer(1.0, self.update_daemon_status)
        except (OSError, subprocess.SubprocessError) as e:
            self.notify(f"Failed to start Supervisor Daemon: {e}", severity="error")

    def action_supervisor_stop(self) -> None:
        """Stop the Supervisor Daemon."""
        if not is_supervisor_daemon_running(self.tmux_session):
            self.notify("Supervisor Daemon not running", severity="warning")
            return

        if stop_supervisor_daemon(self.tmux_session):
            self.notify("Stopped Supervisor Daemon", severity="information")
            try:
                panel = self.query_one("#daemon-panel", DaemonPanel)
                panel.log_lines.append(">>> Supervisor Daemon stopped")
            except NoMatches:
                pass
        else:
            self.notify("Failed to stop Supervisor Daemon", severity="error")

        self.update_daemon_status()

    def action_monitor_restart(self) -> None:
        """Restart the Monitor Daemon (handles metrics/state tracking)."""
        import time

        try:
            panel = self.query_one("#daemon-panel", DaemonPanel)
            panel.log_lines.append(">>> Restarting Monitor Daemon...")
        except NoMatches:
            pass

        # Stop if running
        if is_monitor_daemon_running(self.tmux_session):
            stop_monitor_daemon(self.tmux_session)
            time.sleep(0.5)

        # Start fresh
        try:
            subprocess.Popen(
                [sys.executable, "-m", "overcode.monitor_daemon",
                 "--session", self.tmux_session],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            self.notify("Monitor Daemon restarted", severity="information")
            try:
                panel = self.query_one("#daemon-panel", DaemonPanel)
                panel.log_lines.append(">>> Monitor Daemon restarted")
            except NoMatches:
                pass
            self.set_timer(1.0, self.update_daemon_status)
        except (OSError, subprocess.SubprocessError) as e:
            self.notify(f"Failed to restart Monitor Daemon: {e}", severity="error")

    def _ensure_monitor_daemon(self) -> None:
        """Start the Monitor Daemon if not running.

        Called automatically on TUI mount to ensure continuous monitoring.
        The Monitor Daemon handles status tracking, time accumulation,
        stats sync, and user presence detection.
        """
        # Check PID file first
        if is_monitor_daemon_running(self.tmux_session):
            return  # Already running

        # Also check for running processes (in case PID file is stale or daemon is starting)
        # This prevents race conditions where multiple TUIs start daemons simultaneously
        daemon_count = count_daemon_processes("monitor_daemon", session=self.tmux_session)
        if daemon_count > 0:
            return  # Daemon process exists, just PID file might be missing/stale

        try:
            subprocess.Popen(
                [sys.executable, "-m", "overcode.monitor_daemon",
                 "--session", self.tmux_session],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.notify("Monitor Daemon started", severity="information")
        except (OSError, subprocess.SubprocessError) as e:
            self.notify(f"Failed to start Monitor Daemon: {e}", severity="warning")

    def action_toggle_web_server(self) -> None:
        """Toggle the web analytics dashboard server on/off."""
        is_running, msg = toggle_web_server(self.tmux_session)

        if is_running:
            url = get_web_server_url(self.tmux_session)
            self.notify(f"Web server: {url}", severity="information")
            try:
                panel = self.query_one("#daemon-panel", DaemonPanel)
                panel.log_lines.append(f">>> Web server started: {url}")
            except NoMatches:
                pass
        else:
            self.notify(f"Web server: {msg}", severity="information")
            try:
                panel = self.query_one("#daemon-panel", DaemonPanel)
                panel.log_lines.append(f">>> Web server: {msg}")
            except NoMatches:
                pass

        self.update_daemon_status()

    def action_toggle_sleep(self) -> None:
        """Toggle sleep mode for the focused agent.

        Sleep mode marks an agent as 'asleep' (human doesn't want it to do anything).
        Sleeping agents are excluded from stats calculations.
        Press z again to wake the agent.
        """
        focused = self.focused
        if not isinstance(focused, SessionSummary):
            self.notify("No agent focused", severity="warning")
            return

        session = focused.session
        new_asleep_state = not session.is_asleep

        # Update the session in the session manager
        self.session_manager.update_session(session.id, is_asleep=new_asleep_state)

        # Update the local session object
        session.is_asleep = new_asleep_state

        # Update the widget's display status if sleeping
        if new_asleep_state:
            focused.detected_status = "asleep"
            self.notify(f"Agent '{session.name}' is now asleep (excluded from stats)", severity="information")
        else:
            # Wake up - status will be refreshed on next update cycle
            self.notify(f"Agent '{session.name}' is now awake", severity="information")

        # Force a refresh
        focused.refresh()

    def action_kill_focused(self) -> None:
        """Kill the currently focused agent (requires confirmation)."""
        focused = self.focused
        if not isinstance(focused, SessionSummary):
            self.notify("No agent focused", severity="warning")
            return

        session_name = focused.session.name
        session_id = focused.session.id
        now = time.time()

        # Check if this is a confirmation of a pending kill
        if self._pending_kill:
            pending_name, pending_time = self._pending_kill
            # Confirm if same session and within 3 second window
            if pending_name == session_name and (now - pending_time) < 3.0:
                self._pending_kill = None  # Clear pending state
                self._execute_kill(focused, session_name, session_id)
                return
            else:
                # Different session or expired - start new confirmation
                self._pending_kill = None

        # First press - request confirmation
        self._pending_kill = (session_name, now)
        self.notify(
            f"Press x again to kill '{session_name}'",
            severity="warning",
            timeout=3
        )

    def _execute_kill(self, focused: "SessionSummary", session_name: str, session_id: str) -> None:
        """Execute the actual kill operation after confirmation."""
        # Use launcher to kill the session
        launcher = ClaudeLauncher(
            tmux_session=self.tmux_session,
            session_manager=self.session_manager
        )

        if launcher.kill_session(session_name):
            self.notify(f"Killed agent: {session_name}", severity="information")
            # Remove the widget and refresh
            focused.remove()
            # Update session cache
            if session_id in self._sessions_cache:
                del self._sessions_cache[session_id]
            if session_id in self.expanded_states:
                del self.expanded_states[session_id]
            # Clear preview pane and focus next agent if in list_preview mode
            if self.view_mode == "list_preview":
                try:
                    preview = self.query_one("#preview-pane", PreviewPane)
                    preview.session_name = ""
                    preview.content_lines = []
                    preview.refresh()
                    # Focus next available agent
                    widgets = list(self.query(SessionSummary))
                    if widgets:
                        self.focused_session_index = min(self.focused_session_index, len(widgets) - 1)
                        widgets[self.focused_session_index].focus()
                        self._update_preview()
                except NoMatches:
                    pass
        else:
            self.notify(f"Failed to kill agent: {session_name}", severity="error")

    def action_new_agent(self) -> None:
        """Prompt for directory and name to create a new agent.

        Two-step flow:
        1. Enter working directory (or press Enter for current directory)
        2. Enter agent name (defaults to directory basename)
        """
        from pathlib import Path

        try:
            command_bar = self.query_one("#command-bar", CommandBar)
            command_bar.add_class("visible")  # Must show the command bar first
            command_bar.set_mode("new_agent_dir")
            # Pre-fill with current working directory
            input_widget = command_bar.query_one("#cmd-input", Input)
            input_widget.value = str(Path.cwd())
            command_bar.focus_input()
        except NoMatches:
            self.notify("Command bar not found", severity="error")

    def action_toggle_copy_mode(self) -> None:
        """Toggle mouse capture to allow native terminal text selection.

        When copy mode is ON:
        - Mouse events pass through to terminal
        - You can select text and Cmd+C to copy
        - Press 'y' again to exit copy mode
        """
        if not hasattr(self, '_copy_mode'):
            self._copy_mode = False

        self._copy_mode = not self._copy_mode

        if self._copy_mode:
            # Write escape sequences directly to the driver's file (stderr)
            # This is what Textual uses internally for terminal output
            # We bypass the driver methods because they check _mouse flag
            driver_file = self._driver._file

            # Disable all mouse tracking modes
            driver_file.write("\x1b[?1000l")  # Disable basic mouse tracking
            driver_file.write("\x1b[?1002l")  # Disable cell motion tracking
            driver_file.write("\x1b[?1003l")  # Disable all motion tracking
            driver_file.write("\x1b[?1015l")  # Disable urxvt extended mode
            driver_file.write("\x1b[?1006l")  # Disable SGR extended mode
            driver_file.flush()

            self.notify("COPY MODE - select with mouse, Cmd+C to copy, 'y' to exit", severity="warning")
        else:
            # Re-enable mouse support using driver's method
            self._driver._mouse = True  # Ensure flag is set so enable actually sends codes
            self._driver._enable_mouse_support()
            self.refresh()
            self.notify("Copy mode OFF", severity="information")

    def action_send_enter_to_focused(self) -> None:
        """Send Enter keypress to the focused agent (for approvals)."""
        focused = self.focused
        if not isinstance(focused, SessionSummary):
            self.notify("No agent focused", severity="warning")
            return

        session_name = focused.session.name
        launcher = ClaudeLauncher(
            tmux_session=self.tmux_session,
            session_manager=self.session_manager
        )

        # Send "enter" which the launcher handles as just pressing Enter
        if launcher.send_to_session(session_name, "enter"):
            self.notify(f"Sent Enter to {session_name}", severity="information")
        else:
            self.notify(f"Failed to send Enter to {session_name}", severity="error")

    def _send_key_to_focused(self, key: str) -> None:
        """Send a key to the focused agent."""
        focused = self.focused
        if not isinstance(focused, SessionSummary):
            self.notify("No agent focused", severity="warning")
            return

        session_name = focused.session.name
        launcher = ClaudeLauncher(
            tmux_session=self.tmux_session,
            session_manager=self.session_manager
        )

        # Send the key followed by Enter (to select the numbered option)
        if launcher.send_to_session(session_name, key, enter=True):
            self.notify(f"Sent '{key}' to {session_name}", severity="information")
        else:
            self.notify(f"Failed to send '{key}' to {session_name}", severity="error")

    def action_send_1_to_focused(self) -> None:
        """Send '1' to focused agent."""
        self._send_key_to_focused("1")

    def action_send_2_to_focused(self) -> None:
        """Send '2' to focused agent."""
        self._send_key_to_focused("2")

    def action_send_3_to_focused(self) -> None:
        """Send '3' to focused agent."""
        self._send_key_to_focused("3")

    def action_send_4_to_focused(self) -> None:
        """Send '4' to focused agent."""
        self._send_key_to_focused("4")

    def action_send_5_to_focused(self) -> None:
        """Send '5' to focused agent."""
        self._send_key_to_focused("5")

    def on_key(self, event: events.Key) -> None:
        """Signal activity to daemon on any keypress."""
        signal_activity(self.tmux_session)

    def on_unmount(self) -> None:
        """Clean up terminal state on exit"""
        import sys
        # Ensure mouse tracking is disabled
        sys.stdout.write('\033[?1000l')  # Disable mouse tracking
        sys.stdout.write('\033[?1002l')  # Disable cell motion tracking
        sys.stdout.write('\033[?1003l')  # Disable all motion tracking
        sys.stdout.flush()


def run_tui(tmux_session: str = "agents", diagnostics: bool = False):
    """Run the TUI supervisor"""
    import os
    import sys

    # Ensure we're using a proper terminal
    if not sys.stdout.isatty():
        print("Error: Must run in a TTY terminal", file=sys.stderr)
        sys.exit(1)

    # Force terminal size detection
    os.environ.setdefault('TERM', 'xterm-256color')

    app = SupervisorTUI(tmux_session, diagnostics=diagnostics)
    # Use driver=None to auto-detect, and size will be detected from terminal
    app.run()


if __name__ == "__main__":
    import sys
    tmux_session = sys.argv[1] if len(sys.argv) > 1 else "agents"
    run_tui(tmux_session)
