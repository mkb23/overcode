"""
Preview pane widget for TUI.

Shows focused agent's terminal output in list+preview mode.

The focused agent's pane changes on nearly every 250 ms tick while it
works (its spinner alone), so this is the TUI's hottest widget. It is a
ScrollView on Textual's line API, like textual's Log: each raw line is
parsed from ANSI and turned into strips once, and a refresh draws only
the rows on screen. As a Static holding one big Text it was re-parsed,
word-wrapped and re-laid-out in full on every change — most of the
TUI's CPU (#486).
"""

import logging
import re
from typing import Dict, List, Optional, TYPE_CHECKING

from rich.cells import cell_len
from rich.console import Console
from rich.style import Style
from rich.text import Text
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.strip import Strip

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .session_summary import SessionSummary
    from .job_summary import JobSummary

# Clean ANSI for Rich's Text.from_ansi():
# 1. Strip non-SGR CSI sequences (\x1b[K, \x1b[?25l, etc.) — Rich only handles SGR.
# 2. Strip background color SGR codes — unreset backgrounds leak to fill the
#    full widget width when Textual pads the line.
_NON_SGR_CSI = re.compile(r'\x1b\[[0-9;?]*[a-lA-Ln-zA-Z]')   # CSI not ending in 'm'
_OSC_SEQ = re.compile(r'\x1b\].*?(?:\x07|\x1b\\)')            # OSC sequences
_OTHER_ESC = re.compile(r'\x1b[^[\]][^\x1b]*')                 # other escapes e.g. \x1b(B
_BG_COLOR = re.compile(r'\x1b\[(?:4[0-9]|10[0-7]|48;[0-9;]+)m')  # background SGR codes


# Renders Text to segments. Styles resolve the same on any console; a
# module-level one also works before the widget is mounted (tests).
_CONSOLE = Console(color_system="truecolor", force_terminal=True, width=1000)


def _hard_wrap(line: Text, width: int) -> List[Text]:
    """Split ``line`` into rows of at most ``width`` cells, as a terminal would.

    A line that fits, which is nearly every line, is returned as is.
    """
    plain = line.plain
    if width <= 0 or cell_len(plain) <= width:
        return [line]
    offsets = []
    used = 0
    for i, ch in enumerate(plain):
        w = cell_len(ch)
        if used + w > width:
            offsets.append(i)
            used = 0
        used += w
    return list(line.divide(offsets))


def _sanitize_ansi(line: str) -> str:
    """Strip non-SGR escapes and background colors for safe Rich rendering."""
    line = _OSC_SEQ.sub('', line)
    line = _NON_SGR_CSI.sub('', line)
    line = _OTHER_ESC.sub('', line)
    line = _BG_COLOR.sub('', line)
    return line


class PreviewPane(ScrollView):
    """Preview pane showing focused agent's terminal output in list+preview mode.

    Scrolls natively (mouse wheel / trackpad). Auto-scrolls to bottom
    unless the user has scrolled up to review.
    """

    # Rows are wrapped to the width, so never scroll sideways; the gutter
    # keeps that width fixed when the vertical scrollbar comes and goes
    DEFAULT_CSS = """
    PreviewPane {
        overflow-x: hidden;
        scrollbar-gutter: stable;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.content_lines: List[str] = []
        self.monochrome: bool = False
        self.session_name: str = ""
        self.stale_banner: str = ""  # Non-empty = show stale banner above content (#385)
        self._auto_scroll = True
        self._user_scrolled = False  # Set True by mouse wheel, cleared by auto-scroll
        # The rows on screen now; what they were built from, so an unchanged
        # update costs nothing; and each raw line's rows, so a changed update
        # only parses the lines that are new (#486)
        self._rows: List[Strip] = []
        self._shown_key: tuple = ()
        self._row_cache: Dict[tuple, List[Strip]] = {}

    def _content_width(self) -> int:
        """Cells available to a line: inside the padding and the scrollbar."""
        try:
            width = self.scrollable_content_region.width
        except Exception:
            width = 0
        if width <= 0:
            width = self.size.width if self.size.width > 0 else 80
        return width

    def _base_style(self) -> Optional[Style]:
        """The widget's own style (background), under every row."""
        try:
            return self.rich_style
        except Exception:  # not mounted
            return None

    @staticmethod
    def _strip(text: Text, base: Optional[Style]) -> Strip:
        if base is not None:
            text.stylize_before(base)
        return Strip(text.render(_CONSOLE), text.cell_len)

    def _parse_rows(self, line: str, width: int) -> List[Text]:
        """``line`` parsed from ANSI and hard-wrapped to ``width``."""
        if self.monochrome:
            parsed = Text(Text.from_ansi(line).plain)
        else:
            parsed = Text.from_ansi(_sanitize_ansi(line))
        return _hard_wrap(parsed, width)

    def _build_rows(self) -> List[Strip]:
        """Every row of the pane: header, stale banner, then the content."""
        width = self._content_width()
        base = self._base_style()
        rows: List[Strip] = []

        # Header with session name - pad to full pane width
        header = f"─── {self.session_name} " if self.session_name else "─── Preview "
        header_style = "bold" if self.monochrome else "bold cyan"
        header_row = Text(header, style=header_style)
        header_row.append("─" * max(0, width - len(header)), style="dim")
        rows.append(self._strip(header_row, base))

        # Stale-content banner for unreachable sisters (#385)
        if self.stale_banner:
            banner_style = "bold" if self.monochrome else "bold yellow"
            for row in _hard_wrap(Text(f"⚠ {self.stale_banner}", style=banner_style), width):
                rows.append(self._strip(row, base))

        if not self.content_lines:
            rows.append(self._strip(Text("(no output)", style="dim italic"), base))
            return rows

        # Reuse the rows of lines already on screen; keep only this
        # update's lines, so the cache is bounded by the pane
        previous = self._row_cache
        current: Dict[tuple, List[Strip]] = {}
        for line in self.content_lines:
            key = (line, width, self.monochrome, base)
            line_rows = current.get(key) or previous.get(key)
            if line_rows is None:
                line_rows = [self._strip(row, base) for row in self._parse_rows(line, width)]
            current[key] = line_rows
            rows.extend(line_rows)
        self._row_cache = current
        return rows

    def _show(self) -> bool:
        """Lay out the current lines and redraw; False if nothing changed."""
        width = self._content_width()
        key = (self.session_name, tuple(self.content_lines), self.stale_banner,
               self.monochrome, width, self._base_style())
        if key == self._shown_key:
            return False
        self._shown_key = key
        self._rows = self._build_rows()
        self.virtual_size = Size(width, len(self._rows))
        self.refresh()
        return True

    def render_line(self, y: int) -> Strip:
        """One screen row: only the rows in view are ever drawn."""
        scroll_x, scroll_y = self.scroll_offset
        width = self.scrollable_content_region.width
        base = self.rich_style
        index = scroll_y + y
        if index >= len(self._rows):
            return Strip.blank(width, base)
        return self._rows[index].crop_extend(scroll_x, scroll_x + width, base)

    def on_resize(self, event) -> None:
        """Re-wrap for the new width."""
        self._show()

    def update_from_widget(self, widget: "SessionSummary", stale_banner: str = "") -> None:
        """Update preview content from a SessionSummary widget.

        Args:
            widget: Source summary widget whose pane content drives the preview.
            stale_banner: Optional banner text shown above content when the
                source sister is unreachable (#385). Empty string = no banner.
        """
        self.session_name = widget.session.name
        self.content_lines = list(widget.pane_content) if widget.pane_content else []
        self.stale_banner = stale_banner

        # Save scroll position before content replacement
        saved_scroll = self.scroll_offset.y
        was_auto = self._auto_scroll

        if not self._show():
            return

        if was_auto:
            # Follow new content at bottom
            self.call_after_refresh(lambda: self.scroll_end(animate=False))
        else:
            # Restore user's scroll position after content replacement
            self.call_after_refresh(lambda: self.scroll_to(y=saved_scroll, animate=False))

    def update_from_job_widget(self, widget: "JobSummary") -> None:
        """Update preview content from a JobSummary widget."""
        self.session_name = widget.job.name
        self.content_lines = list(widget.pane_content) if widget.pane_content else []

        saved_scroll = self.scroll_offset.y
        was_auto = self._auto_scroll

        if not self._show():
            return

        if was_auto:
            self.call_after_refresh(lambda: self.scroll_end(animate=False))
        else:
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
