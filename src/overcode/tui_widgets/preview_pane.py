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
    Auto-scrolls to bottom unless the user has scrolled up to review.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.content_lines: List[str] = []
        self.monochrome: bool = False
        self.session_name: str = ""
        self._auto_scroll = True
        self._user_scrolled = False  # Set True by mouse wheel, cleared by auto-scroll

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
            for line in self.content_lines:
                if self.monochrome:
                    parsed = Text.from_ansi(line)
                    content.append(parsed.plain)
                else:
                    content.append(Text.from_ansi(line))
                content.append("\n")

        return content

    def update_from_widget(self, widget: "SessionSummary") -> None:
        """Update preview content from a SessionSummary widget."""
        self.session_name = widget.session.name
        self.content_lines = list(widget.pane_content) if widget.pane_content else []

        # Save scroll position before content replacement
        saved_scroll = self.scroll_offset.y
        was_auto = self._auto_scroll

        try:
            content_widget = self.query_one("#preview-content", Static)
            content_widget.update(self._build_content())
        except Exception:
            pass

        if was_auto:
            # Follow new content at bottom
            self.call_after_refresh(lambda: self.scroll_end(animate=False))
        else:
            # Restore user's scroll position after content replacement
            self.call_after_refresh(lambda: self.scroll_to(y=saved_scroll, animate=False))

    def on_mouse_scroll_up(self, event) -> None:
        """User scrolled up with mouse wheel — disable auto-scroll."""
        self._auto_scroll = False
        self._user_scrolled = True

    def on_mouse_scroll_down(self, event) -> None:
        """User scrolled down with mouse wheel — re-enable if at bottom."""
        self._user_scrolled = True
        # Check after the scroll is applied
        self.call_after_refresh(self._check_at_bottom)

    def _check_at_bottom(self) -> None:
        """Re-enable auto-scroll if user has scrolled back to bottom."""
        if self.max_scroll_y <= 0 or self.scroll_offset.y >= self.max_scroll_y - 1:
            self._auto_scroll = True
            self._user_scrolled = False
