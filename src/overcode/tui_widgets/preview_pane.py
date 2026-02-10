"""
Preview pane widget for TUI.

Shows focused agent's terminal output in list+preview mode.
Uses ScrollableContainer for native mouse wheel / trackpad scrolling.
"""

from typing import List, TYPE_CHECKING

from textual.containers import ScrollableContainer
from textual.widgets import Static
from rich.text import Text

if TYPE_CHECKING:
    from .session_summary import SessionSummary


class PreviewPane(ScrollableContainer):
    """Preview pane showing focused agent's terminal output in list+preview mode.

    Wraps a child Static whose height grows to fit all content lines.
    The container provides native mouse wheel / trackpad scrolling.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.content_lines: List[str] = []
        self.monochrome: bool = False
        self.session_name: str = ""
        self._auto_scroll = True  # Track whether user has scrolled away from bottom

    def compose(self):
        yield Static(id="preview-content")

    def _build_content(self) -> Text:
        """Build the Rich Text renderable from stored content lines."""
        content = Text()
        # Use widget width for layout, with sensible fallback
        pane_width = self.size.width if self.size.width > 0 else 80

        # Header with session name - pad to full pane width
        header = f"─── {self.session_name} " if self.session_name else "─── Preview "
        header_style = "bold" if self.monochrome else "bold cyan"
        border_style = "dim" if self.monochrome else "dim"
        content.append(header, style=header_style)
        content.append("─" * max(0, pane_width - len(header)), style=border_style)
        content.append("\n")

        if not self.content_lines:
            content.append("(no output)", style="dim italic")
        else:
            max_line_len = max(pane_width - 1, 40)
            for line in self.content_lines:
                display_line = line[:max_line_len] if len(line) > max_line_len else line
                if self.monochrome:
                    parsed = Text.from_ansi(display_line)
                    content.append(parsed.plain)
                else:
                    content.append(Text.from_ansi(display_line))
                content.append("\n")

        return content

    def update_from_widget(self, widget: "SessionSummary") -> None:
        """Update preview content from a SessionSummary widget."""
        self.session_name = widget.session.name
        self.content_lines = list(widget.pane_content) if widget.pane_content else []
        try:
            content_widget = self.query_one("#preview-content", Static)
            content_widget.update(self._build_content())
        except Exception:
            pass
        # Auto-scroll to bottom if user hasn't scrolled away
        if self._auto_scroll:
            self.scroll_end(animate=False)

    def on_scroll_up(self) -> None:
        """User scrolled up — disable auto-scroll to bottom."""
        self._auto_scroll = False

    def on_scroll_down(self) -> None:
        """User scrolled down — re-enable auto-scroll if at bottom."""
        if self.scroll_offset.y >= self.max_scroll_y:
            self._auto_scroll = True

    def watch_scroll_y(self, value: float) -> None:
        """Track scroll position to manage auto-scroll behavior."""
        if self.max_scroll_y > 0 and value >= self.max_scroll_y - 1:
            self._auto_scroll = True
        elif self.max_scroll_y > 0 and value < self.max_scroll_y - 1:
            self._auto_scroll = False
