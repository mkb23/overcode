"""Aligning rows: column widths, padding, the header row, the column under a point."""

from typing import Callable, List, Optional, TYPE_CHECKING

from .column import SummaryColumn
from .registry import SUMMARY_COLUMNS

if TYPE_CHECKING:
    from rich.text import Text


def column_at(x: int, column_ids: List[str], column_widths: List[int]) -> Optional[str]:
    """The column under cell offset `x` of an aligned row, or None.

    `column_ids` and `column_widths` are parallel: the visible columns in
    order and their widths from compute_column_widths(). Zero-width columns
    are never hit. Used by the header row for hover and click (#477, #487).
    """
    if x < 0:
        return None
    edge = 0
    for col_id, w in zip(column_ids, column_widths):
        edge += w
        if x < edge:
            return col_id
    return None


def compute_column_widths(cell_rows: "List[List[Text]]") -> "List[int]":
    """Compute max visual width per column across all rows.

    Args:
        cell_rows: List of rows, each a list of Text cells from render_summary_cells().

    Returns:
        List of max visual widths, one per column position.
    """
    from rich.cells import cell_len
    if not cell_rows:
        return []
    n_cols = max(len(row) for row in cell_rows)
    max_widths = [0] * n_cols
    for row in cell_rows:
        for i, cell in enumerate(row):
            w = cell_len(cell.plain)
            if w > max_widths[i]:
                max_widths[i] = w
    return max_widths


def pad_and_join_cells(cells: "List[Text]", column_widths: "List[int]", pad_style: str = "") -> "Text":
    """Pad one row's cells to the given column widths and join into a single Text.

    This is the shared alignment function used by both TUI and CLI.

    Args:
        pad_style: Style applied to padding spaces (e.g. "on #0d2137" for TUI background).
    """
    from rich.cells import cell_len
    from rich.text import Text
    line = Text()
    for i, cell in enumerate(cells):
        line.append_text(cell)
        if i < len(column_widths):
            pad = column_widths[i] - cell_len(cell.plain)
            if pad > 0:
                line.append(" " * pad, style=pad_style)
    return line


def align_summary_rows(cell_rows: "List[List[Text]]") -> "List[Text]":
    """Convenience: compute column widths and pad all rows in one call.

    Used by the CLI which has all rows available at once.
    """
    if not cell_rows:
        return []
    widths = compute_column_widths(cell_rows)
    return [pad_and_join_cells(row, widths) for row in cell_rows]


def render_header_cells(
    column_filter: Optional[Callable[[SummaryColumn], bool]] = None,
    column_widths: Optional["List[int]"] = None,
    sort_column: Optional[str] = None,
    sort_descending: bool = False,
) -> "Text":
    """Render a column header row using short header labels.

    Args:
        column_filter: Same filter used for data rows — ensures headers align.
        column_widths: Per-column widths from compute_column_widths(). Headers
            are truncated to fit but never contribute to width computation.
        sort_column: Column the list is sorted by; its header is drawn bold
            with ▼ (descending) or ▲ (ascending) at the end (#487).

    Returns:
        A single Text line with dim-styled abbreviated headers.
    """
    from rich.text import Text
    from rich.cells import cell_len
    line = Text()
    col_idx = 0
    for col in SUMMARY_COLUMNS:
        if column_filter is not None:
            if not column_filter(col):
                continue
        header = " " + col.header if col.header else ""
        style = "dim"
        if col.id == sort_column:
            header = (header or " ") + ("▼" if sort_descending else "▲")
            style = "bold"
        if column_widths and col_idx < len(column_widths):
            w = column_widths[col_idx]
            # Truncate header to column width, keeping the sort arrow
            if cell_len(header) > w:
                if col.id == sort_column and w > 0:
                    header = header[:w - 1] + header[-1]
                else:
                    header = header[:w]
            # Pad to column width
            pad = w - cell_len(header)
            line.append(header, style=style)
            if pad > 0:
                line.append(" " * pad, style="dim")
        else:
            line.append(header, style=style)
        col_idx += 1
    return line
