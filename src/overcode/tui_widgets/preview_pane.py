"""
Preview pane widget for TUI.

Shows focused agent's terminal output in list+preview mode.
"""

from typing import List, TYPE_CHECKING

from textual.widgets import Static
from textual.reactive import reactive
from rich.text import Text

if TYPE_CHECKING:
    from .session_summary import SessionSummary


class PreviewPane(Static):
    """Preview pane showing focused agent's terminal output in list+preview mode."""

    content_lines: reactive[List[str]] = reactive(list, init=False)
    monochrome: reactive[bool] = reactive(False)
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
        header_style = "bold" if self.monochrome else "bold cyan"
        border_style = "dim" if self.monochrome else "dim"
        content.append(header, style=header_style)
        content.append("─" * max(0, pane_width - len(header)), style=border_style)
        content.append("\n")

        if not self.content_lines:
            content.append("(no output)", style="dim italic")
        else:
            # Calculate available lines based on widget height
            # Reserve 2 lines for header and some padding
            available_lines = max(10, self.size.height - 2) if self.size.height > 0 else 30
            # Show last N lines of output with ANSI color support
            # Truncate lines to pane width to match tmux display
            max_line_len = max(pane_width - 1, 40)  # Leave room for newline, minimum 40
            for line in self.content_lines[-available_lines:]:
                # Truncate long lines to pane width
                display_line = line[:max_line_len] if len(line) > max_line_len else line
                if self.monochrome:
                    # Strip ANSI colors - use plain text only
                    parsed = Text.from_ansi(display_line)
                    content.append(parsed.plain)
                else:
                    # Parse ANSI escape sequences to preserve colors from tmux
                    # Note: Text.from_ansi() strips trailing newlines, so add newline separately
                    content.append(Text.from_ansi(display_line))
                content.append("\n")

        return content

    def update_from_widget(self, widget: "SessionSummary") -> None:
        """Update preview content from a SessionSummary widget."""
        self.session_name = widget.session.name
        self.content_lines = list(widget.pane_content) if widget.pane_content else []
        self.refresh()
