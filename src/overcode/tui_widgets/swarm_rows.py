"""The read-only lines of an expanded parent's delegated work (0.6.1, phase 2).

One ``SwarmRows`` sits right under each expanded parent's row
(docs/design/agent-observability-0.6.1.md §4.2). It draws the lines
``swarm_view.layout`` arranges from the rows the engine publishes and never
takes focus: the parent's row keeps it, and the app's ``swarm_cursor``
says which of these lines the highlight is on. A virtual row has no pane,
is never sent keys and never rings.
"""

from __future__ import annotations

from typing import List, Optional

from rich.text import Text
from textual.widgets import Static

from ..swarm_view import SwarmLine


class SwarmRows(Static, can_focus=False):
    """The lines under one parent; repainted only when they change."""

    DEFAULT_CSS = """
    SwarmRows {
        height: auto;
        width: 100%;
        padding: 0 1;
        margin: 0;
        background: $surface;
    }
    """

    def __init__(self, parent_id: str) -> None:
        super().__init__("", classes="swarm-rows")
        self.parent_id = parent_id
        self.lines: List[SwarmLine] = []
        self.cursor: Optional[str] = None
        self._shown: Optional[tuple] = None
        self.paints = 0  # tests: count the work

    def show(self, lines: List[SwarmLine], cursor: Optional[str]) -> bool:
        """Draw ``lines`` with ``cursor`` highlighted; False when nothing changed."""
        key = (tuple((ln.key, tuple(ln.segments)) for ln in lines), cursor)
        if key == self._shown:
            return False
        self._shown = key
        self.lines = lines
        self.cursor = cursor
        text = Text(no_wrap=True, overflow="ellipsis")
        for i, line in enumerate(lines):
            if i:
                text.append("\n")
            selected = line.key == cursor
            for chunk, style in line.segments:
                text.append(chunk, style=(f"{style} reverse".strip() if selected else style) or None)
        self.update(text)
        self.display = bool(lines)
        self.paints += 1
        return True

    def line_offset(self, key: Optional[str]) -> Optional[int]:
        """Which line ``key`` is on (to scroll it into view)."""
        for i, line in enumerate(self.lines):
            if line.key == key:
                return i
        return None
