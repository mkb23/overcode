"""
Fullscreen preview overlay widget for TUI.

Displays a scrollable fullscreen view of an agent's terminal output
with up to 500 lines of scrollback history.
"""

from typing import List, Optional

from textual.binding import Binding
from textual.containers import ScrollableContainer
from textual.widgets import Static
from textual.widget import Widget
from textual import events
from rich.text import Text
from rich.panel import Panel
from rich import box


class FullscreenPreview(ScrollableContainer, can_focus=True):
    """Fullscreen scrollable preview of an agent's terminal output.

    Uses ScrollableContainer so the inner Static child (height: auto)
    grows to fit all content lines, and the container provides native
    keyboard/mouse/trackpad scrolling.
    """

    BINDINGS = [
        Binding("up", "scroll_up", "Scroll Up", show=False),
        Binding("k", "scroll_up_20", "Scroll Up 20", show=False),
        Binding("down", "scroll_down", "Scroll Down", show=False),
        Binding("j", "scroll_down_20", "Scroll Down 20", show=False),
        Binding("pageup", "page_up", "Page Up", show=False),
        Binding("pagedown", "page_down", "Page Down", show=False),
        Binding("home", "scroll_home", "Scroll Home", show=False),
        Binding("end", "scroll_end", "Scroll End", show=False),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._content_lines: List[str] = []
        self._session_name: str = ""
        self._monochrome: bool = False
        self._previous_focus: Optional[Widget] = None
        self._previous_focus_session_id: Optional[str] = None

    def compose(self):
        yield Static(id="fullscreen-content")

    def _build_content(self) -> Panel:
        """Build the Rich Panel renderable from stored content lines.

        Always strips ANSI escape codes — they have zero display width but
        non-zero byte length, so any character-count truncation clips
        mid-sequence and produces garbled/ragged edges.
        """
        content = Text()

        if not self._content_lines:
            content.append("(no output)", style="dim italic")
        else:
            for line in self._content_lines:
                plain = Text.from_ansi(line).plain
                content.append(plain)
                content.append("\n")

        title = Text()
        title.append(f" {self._session_name} ", style="bold bright_white")

        return Panel(
            content,
            title=title,
            subtitle=Text("Esc/f/q close · ↑↓/j/k/PgUp/PgDn scroll", style="dim"),
            border_style="bright_cyan",
            box=box.DOUBLE,
        )

    def show(self, lines: List[str], session_name: str, monochrome: bool) -> None:
        """Show the fullscreen preview with the given content."""
        self._content_lines = lines
        self._session_name = session_name
        self._monochrome = monochrome
        focused = self.app.focused
        self._previous_focus = focused
        self._previous_focus_session_id = getattr(focused, 'session', None) and focused.session.id
        try:
            content_widget = self.query_one("#fullscreen-content", Static)
            content_widget.update(self._build_content())
        except Exception:
            pass
        self.add_class("visible")
        self.scroll_end(animate=False)
        self.focus()

    def hide(self) -> None:
        """Hide the fullscreen preview and restore previous focus.

        Looks up the widget by session ID at restore time in case the original
        widget was remounted during a refresh cycle while the overlay was open.
        """
        self.remove_class("visible")
        restored = False
        if self._previous_focus_session_id:
            from .session_summary import SessionSummary
            for w in self.app.query(SessionSummary):
                if w.session.id == self._previous_focus_session_id:
                    w.focus()
                    restored = True
                    break
        if not restored and self._previous_focus is not None:
            try:
                self._previous_focus.focus()
            except Exception:
                pass
        self._previous_focus = None
        self._previous_focus_session_id = None

    def action_scroll_up_20(self) -> None:
        """Scroll up 20 lines."""
        self.scroll_relative(y=-20, animate=False)

    def action_scroll_down_20(self) -> None:
        """Scroll down 20 lines."""
        self.scroll_relative(y=20, animate=False)

    def on_key(self, event: events.Key) -> None:
        """Handle keyboard input — Esc/f/q close the overlay."""
        if event.key in ("escape", "f", "q"):
            self.hide()
            event.stop()
