"""
Fullscreen preview overlay widget for TUI.

Displays a scrollable fullscreen view of an agent's terminal output
with up to 500 lines of scrollback history.
"""

from typing import List

from textual.widgets import Static
from textual import events
from rich.text import Text
from rich.panel import Panel
from rich import box


class FullscreenPreview(Static, can_focus=True):
    """Fullscreen scrollable preview of an agent's terminal output."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._content_lines: List[str] = []
        self._session_name: str = ""
        self._monochrome: bool = False

    def render(self) -> Panel:
        content = Text()

        if not self._content_lines:
            content.append("(no output)", style="dim italic")
        else:
            pane_width = self.size.width - 6 if self.size.width > 6 else 80
            max_line_len = max(pane_width, 40)
            for line in self._content_lines:
                display_line = line[:max_line_len] if len(line) > max_line_len else line
                if self._monochrome:
                    parsed = Text.from_ansi(display_line)
                    content.append(parsed.plain)
                else:
                    content.append(Text.from_ansi(display_line))
                content.append("\n")

        title = Text()
        title.append(f" {self._session_name} ", style="bold bright_white")

        return Panel(
            content,
            title=title,
            subtitle=Text("Press Esc/f/q to close", style="dim"),
            border_style="bright_cyan",
            box=box.DOUBLE,
        )

    def show(self, lines: List[str], session_name: str, monochrome: bool) -> None:
        """Show the fullscreen preview with the given content."""
        self._content_lines = lines
        self._session_name = session_name
        self._monochrome = monochrome
        self.add_class("visible")
        self.refresh()
        self.focus()

    def hide(self) -> None:
        """Hide the fullscreen preview."""
        self.remove_class("visible")

    def on_key(self, event: events.Key) -> None:
        """Handle keyboard input — Esc/f/q close the overlay."""
        if event.key in ("escape", "f", "q"):
            self.hide()
            event.stop()
