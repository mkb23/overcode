"""The read-only lines of an expanded parent's delegated work (0.6.1, phase 2).

One ``SwarmRows`` sits right under each expanded parent's row
(docs/design/agent-observability-0.6.1.md §4.2). ``swarm_view.layout``
says which lines exist; each line renders through the summary columns as
an agent row does (``ColumnContext.for_virtual``, ``columns.render_virtual``)
and is padded to the same aligned widths (``pad_and_join_cells``), so its
cells sit under the agent rows' cells. A column that does not apply shows
its dim "-" placeholder. A member's detail line (→) is free text that
starts at the NAME column.

It never takes focus: the parent's row keeps it, and the app's
``swarm_cursor`` says which of these lines the highlight is on. A virtual
row has no pane, is never sent keys and never rings.

Work: the cells are built when the lines, the cursor, the columns or the
parent's view settings change; the line is padded again when the widths,
the summary mode or the width move; the 250 ms row clock (``tick``)
redraws only the live members' durations. Textual repaints only when the
text changed.
"""

from __future__ import annotations

from typing import List, Optional

from rich.cells import cell_len
from rich.text import Text
from textual import events
from textual.widgets import Static

from ..columns.layout import column_at, pad_and_join_cells
from ..columns.registry import cell_segments, cell_text
from ..columns.render_virtual import (
    ROW_BG, SELECTED_BG, placeholder, ticks, virtual_summary,
)
from ..summary_columns import ColumnContext
from ..swarm_view import SwarmLine


def _ctx_key(ctx: ColumnContext) -> tuple:
    """What a virtual row reads from its parent's context."""
    return (ctx.emoji_free, ctx.show_cost, ctx.max_name_width, ctx.summary_detail, ctx.fleet,
            ctx.has_sisters, ctx.local_hostname)


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
        self.parent_row = None  # the parent's SessionSummary
        self._cells_key: Optional[tuple] = None
        self._columns: tuple = ()
        self._ctxs: List[Optional[ColumnContext]] = []
        self._cells: List[Optional[list]] = []  # per line: segments per column (None: detail)
        self._ticking: List[tuple] = []  # (line index, column index) the clock moves
        self._paint_args: Optional[tuple] = None
        self._shown: Optional[Text] = None
        self._emoji_free = False
        self.paints = 0  # tests: count the work
        self.builds = 0

    # -- inputs ----------------------------------------------------------

    def set_lines(self, lines: List[SwarmLine], cursor: Optional[str], parent_row) -> None:
        self.lines = lines
        self.cursor = cursor
        self.parent_row = parent_row

    def build_cells(self) -> bool:
        """Render each line's cells if an input changed; True if it did."""
        parent = self.parent_row
        if parent is None:
            return False
        ctx = parent._current_row(tick=False).ctx
        columns = parent.visible_columns()
        key = (list(self.lines), self.cursor, tuple(c.id for c in columns), _ctx_key(ctx))
        if key == self._cells_key:
            return False
        self._cells_key = key
        self._columns = columns
        self._emoji_free = ctx.emoji_free
        self._ctxs, self._cells, self._ticking = [], [], []
        ticking_cols = [i for i, c in enumerate(columns) if c.ticks and c.virtual_render]
        for li, line in enumerate(self.lines):
            if line.kind == "detail":
                self._ctxs.append(None)
                self._cells.append(None)
                continue
            vctx = ColumnContext.for_virtual(line, ctx, selected=line.key == self.cursor)
            self._ctxs.append(vctx)
            self._cells.append([cell_segments(c, vctx) for c in columns])
            if ticks(line):
                self._ticking.extend((li, ci) for ci in ticking_cols)
        self.builds += 1
        return True

    def measure(self) -> List[list]:
        """The lines' cells as Text, for aligning the columns (placeholders
        count as empty: they never widen a column)."""
        return [[cell_text(segs) for segs in cells] for cells in self._cells if cells is not None]

    # -- drawing ---------------------------------------------------------

    def paint(self, widths, edges, mode: str, width: int) -> bool:
        """Draw the lines padded to ``widths``; False when nothing changed."""
        self._paint_args = (list(widths or []), list(edges or []), mode, width)
        return self._paint()

    def _paint(self) -> bool:
        widths, edges, mode, width = self._paint_args
        width = self.outer_size.width or width
        text = Text(no_wrap=True, overflow="crop")
        ids = [c.id for c in self._columns]
        name_x = sum(widths[:ids.index("agent_name")]) if "agent_name" in ids and widths else 0
        emoji_free = self._emoji_free
        for i, line in enumerate(self.lines):
            if i:
                text.append("\n")
            bg = SELECTED_BG if line.key == self.cursor else ROW_BG
            row = Text(style=bg)
            cells = self._cells[i] if i < len(self._cells) else None
            if line.kind == "detail" or cells is None:
                row.append(" " * name_x + line.connector)
                row.append(line.summary, style="italic")
            else:
                texts = [cell_text(segs if segs else placeholder(edges[j] if j < len(edges) else 0))
                         for j, segs in enumerate(cells)]
                if widths and len(widths) == len(texts):
                    row.append_text(pad_and_join_cells(texts, widths))
                else:
                    for t in texts:
                        row.append_text(t)
                row.append(" │ ", style="bold dim")
                remaining = max(20, width - cell_len(row.plain) - 2)
                words, style = virtual_summary(line, mode, emoji_free)
                if cell_len(words) > remaining:
                    words = words[:max(0, remaining - 1)] + "…"
                row.append(words, style=style)
            used = cell_len(row.plain)
            if used < width:
                row.append(" " * (width - used))
            text.append_text(row)
        self.display = bool(self.lines)
        if self._shown is not None and text == self._shown:
            return False
        self._shown = text
        self.update(text)
        self.paints += 1
        return True

    def tick(self) -> bool:
        """The row clock: redraw the live members' durations; True if any moved."""
        if not self._ticking or self._paint_args is None:
            return False
        moved = False
        for li, ci in self._ticking:
            segs = cell_segments(self._columns[ci], self._ctxs[li])
            if segs != self._cells[li][ci]:
                self._cells[li][ci] = segs
                moved = True
        return self._paint() if moved else False

    def on_resize(self, event: events.Resize) -> None:
        if self._paint_args is not None:
            self._paint()

    # -- pointer ---------------------------------------------------------

    def line_offset(self, key: Optional[str]) -> Optional[int]:
        """Which line ``key`` is on (to scroll it into view)."""
        for i, line in enumerate(self.lines):
            if line.key == key:
                return i
        return None

    def on_mouse_move(self, event: events.MouseMove) -> None:
        """Hovering a cell with a popup (RES: the result text; NAME: the
        title, type and phase) shows it, as on an agent row."""
        offset = event.get_content_offset(self)
        self.tooltip = None
        if offset is None or self._paint_args is None:
            return
        if not 0 <= offset.y < len(self._ctxs) or self._ctxs[offset.y] is None:
            return
        from ..summary_columns import COLUMNS_BY_ID
        col = COLUMNS_BY_ID.get(column_at(offset.x, [c.id for c in self._columns],
                                          self._paint_args[0]))
        if col is not None and col.virtual_hover is not None:
            self.tooltip = col.virtual_hover(self._ctxs[offset.y])

    def on_leave(self, event: events.Leave) -> None:
        self.tooltip = None
