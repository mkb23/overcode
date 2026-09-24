"""
Column header row above the agent list (#477, #487).

Hovering a header shows what the column means; clicking it sorts the list
by that column, and clicking the sorted column again reverses it. The row
is a single Static, as before — it works out which column is under the
pointer from the same widths the agent rows are padded to, so header and
rows cannot disagree.
"""

from __future__ import annotations

from typing import List, Optional

from rich.text import Text
from textual import events
from textual.message import Message
from textual.widgets import Static

from ..summary_columns import COLUMNS_BY_ID, column_at


class ColumnHeader(Static):
    """The header row: tooltips and click-to-sort over render_header_cells()."""

    class Clicked(Message):
        """A header was clicked: sort by `column_id`."""
        def __init__(self, column_id: str) -> None:
            super().__init__()
            self.column_id = column_id

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.column_ids: List[str] = []
        self.column_widths: List[int] = []
        self.sort_column: Optional[str] = None
        self.sort_descending: bool = False
        self._hover: Optional[str] = None

    def set_columns(
        self,
        column_ids: List[str],
        column_widths: List[int],
        sort_column: Optional[str] = None,
        sort_descending: bool = False,
    ) -> None:
        """What is drawn where, and how the list is sorted — for hit tests
        and tooltip text. Called whenever the header is redrawn."""
        self.column_ids = list(column_ids)
        self.column_widths = list(column_widths)
        self.sort_column = sort_column
        self.sort_descending = sort_descending
        if self._hover is not None:
            self._show_tooltip(self._hover, force=True)

    def column_at_event(self, event: events.MouseEvent) -> Optional[str]:
        offset = event.get_content_offset(self)
        if offset is None:
            return None
        return column_at(offset.x, self.column_ids, self.column_widths)

    def on_mouse_move(self, event: events.MouseMove) -> None:
        self._show_tooltip(self.column_at_event(event))

    def on_leave(self, event: events.Leave) -> None:
        self._hover = None
        self.tooltip = None

    def on_click(self, event: events.Click) -> None:
        col_id = self.column_at_event(event)
        if col_id is not None:
            event.stop()
            self.post_message(self.Clicked(col_id))

    def _show_tooltip(self, col_id: Optional[str], force: bool = False) -> None:
        if col_id == self._hover and not force:
            return
        self._hover = col_id
        self.tooltip = self.tooltip_for(col_id) if col_id else None

    def tooltip_for(self, col_id: str) -> Optional[Text]:
        col = COLUMNS_BY_ID.get(col_id)
        if col is None:
            return None
        text = Text()
        text.append(col.name or col.id, style="bold")
        if col.header:
            text.append(f"  {col.header}", style="dim")
        if col.description:
            text.append("\n" + col.description)
        text.append("\n")
        if col.sort_key is None:
            text.append("Not sortable", style="dim italic")
        elif col_id == self.sort_column:
            arrow = "▼" if self.sort_descending else "▲"
            text.append(f"Sorted {arrow} · click to reverse", style="dim italic")
        else:
            text.append("Click to sort · S to choose a sort", style="dim italic")
        return text
