"""
Daemon panel widget for TUI.

Inline panel showing daemon status and log viewer.
"""

from datetime import datetime
from typing import Optional

from textual.widgets import Static
from textual import work
from rich.text import Text

from ..monitor_daemon_state import MonitorDaemonState, get_monitor_daemon_state
from ..settings import get_session_dir
from ..tui_helpers import (
    format_interval,
    format_ago,
    get_daemon_status_style,
)


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
        """Kick off background log refresh (if visible)."""
        if not self.display:
            return
        self._fetch_logs_async()

    @work(thread=True, exclusive=True, group="daemon_logs")
    def _fetch_logs_async(self) -> None:
        """Read daemon state and logs off the main thread."""
        # All file I/O happens here in the worker thread
        monitor_state = get_monitor_daemon_state(self.tmux_session)

        session_dir = get_session_dir(self.tmux_session)
        log_file = session_dir / "monitor_daemon.log"

        new_log_lines = None
        new_file_pos = self._log_file_pos

        if log_file.exists():
            try:
                with open(log_file, 'r') as f:
                    if not self.log_lines:
                        # First read: get last 100 lines of file
                        all_lines = f.readlines()
                        new_log_lines = [l.rstrip() for l in all_lines[-100:]]
                        new_file_pos = f.tell()
                    else:
                        # Subsequent reads: only get new content
                        f.seek(self._log_file_pos)
                        new_content = f.read()
                        new_file_pos = f.tell()

                        if new_content:
                            new_lines = new_content.strip().split('\n')
                            new_log_lines = (self.log_lines + new_lines)[-100:]
            except (OSError, IOError, ValueError):
                pass

        # Apply on main thread
        self.app.call_from_thread(
            self._apply_logs, monitor_state, new_log_lines, new_file_pos
        )

    def _apply_logs(self, monitor_state, new_log_lines, new_file_pos) -> None:
        """Apply fetched log data on main thread (no I/O)."""
        self.monitor_state = monitor_state
        self._log_file_pos = new_file_pos
        if new_log_lines is not None:
            self.log_lines = new_log_lines
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
            try:
                last_loop = datetime.fromisoformat(state.last_loop_time) if state.last_loop_time else None
            except (ValueError, TypeError):
                last_loop = None
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
