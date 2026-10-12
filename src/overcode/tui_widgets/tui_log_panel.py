"""
TUI diagnostic log panel widget.

Inline panel showing the TUI's own diagnostic log output.
Toggled with 'O'. Shows tui.log which records sister operations,
command bar events, and other TUI-level diagnostics.
"""

import logging
from typing import Optional

from textual.widgets import Static
from textual import work
from rich.text import Text

from ..daemon_logging import follow_log
from ..settings import get_tui_log_path


log = logging.getLogger("overcode.tui")


def setup_tui_file_logger(tmux_session: str) -> Optional[logging.FileHandler]:
    """Set up a file handler for the overcode.tui logger.

    Returns the handler so it can be removed on shutdown.
    """
    log_path = get_tui_log_path(tmux_session)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.FileHandler(str(log_path), encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-5s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    ))
    handler.setLevel(logging.DEBUG)

    # Attach to the overcode root so overcode.tui.* and overcode.sister.* all route here
    root = logging.getLogger("overcode")
    root.addHandler(handler)
    if root.level > logging.DEBUG:
        root.setLevel(logging.DEBUG)

    return handler


class TuiLogPanel(Static):
    """Inline panel showing the TUI's own diagnostic log tail."""

    LOG_LINES_TO_SHOW = 20

    def __init__(self, tmux_session: str = "agents", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tmux_session = tmux_session
        self.log_lines: list[str] = []
        self._log_file_pos = 0

    def on_mount(self) -> None:
        """Read the log once now; the app's scheduler ticks _refresh_logs each second."""
        self._refresh_logs()

    def _refresh_logs(self) -> None:
        """Kick off background log refresh (if visible)."""
        if not self.display:
            return
        self._fetch_logs_async()

    @work(thread=True, exclusive=True, group="tui_logs")
    def _fetch_logs_async(self) -> None:
        """Read TUI log off the main thread (a stat until it grows)."""
        new_log_lines, new_file_pos = follow_log(
            get_tui_log_path(self.tmux_session), self.log_lines, self._log_file_pos)
        if new_log_lines is None and new_file_pos == self._log_file_pos:
            return  # unchanged: nothing to apply or repaint
        self.app.call_from_thread(
            self._apply_logs, new_log_lines, new_file_pos
        )

    def _apply_logs(self, new_log_lines, new_file_pos) -> None:
        """Apply fetched log data on main thread."""
        self._log_file_pos = new_file_pos
        if new_log_lines is not None:
            self.log_lines = new_log_lines
        self.refresh()

    def render(self) -> Text:
        """Render the TUI log panel."""
        content = Text()

        # Header
        content.append("TUI Diagnostic Log", style="bold")
        content.append("  ")
        log_path = get_tui_log_path(self.tmux_session)
        content.append(str(log_path), style="dim italic")
        content.append("\n")

        # Log lines
        display_lines = self.log_lines[-self.LOG_LINES_TO_SHOW:] if self.log_lines else []

        if not display_lines:
            content.append("  (no log output yet)", style="dim italic")
            content.append("\n")
        else:
            for line in display_lines:
                content.append("  ")
                display_line = line[:160] if len(line) > 160 else line

                if "ERROR" in line:
                    style = "red"
                elif "WARN" in line:
                    style = "yellow"
                elif "sister" in line.lower() or "remote" in line.lower():
                    style = "cyan"
                elif "launch" in line.lower():
                    style = "bold green"
                elif "DEBUG" in line:
                    style = "dim"
                else:
                    style = ""

                content.append(display_line, style=style)
                content.append("\n")

        return content
