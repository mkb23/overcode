"""
Summary line configuration modal for TUI.

Groups with individual columns shown inline.
Edits per-level column overrides (low/med/high).
Updates live summary lines as you toggle groups/columns.
"""

import logging
from typing import Dict, List, Optional, Any, Tuple

from textual.message import Message
from textual import events
from rich.text import Text

logger = logging.getLogger(__name__)

from ..summary_groups import SUMMARY_GROUPS, SUMMARY_GROUPS_BY_ID
from ..summary_columns import SUMMARY_COLUMNS, SummaryColumn, resolve_column_visible
from .modal_base import ModalBase


# Build group -> columns mapping
def _columns_by_group() -> Dict[str, List[SummaryColumn]]:
    result: Dict[str, List[SummaryColumn]] = {}
    for col in SUMMARY_COLUMNS:
        result.setdefault(col.group, []).append(col)
    return result


class SummaryConfigModal(ModalBase):
    """Modal dialog for configuring per-level column visibility.

    Groups with individual columns always shown.
    Navigate with j/k, toggle with space.
    """

    class ConfigChanged(Message):
        """Message sent when configuration is applied."""

        def __init__(self, level: str, overrides: Dict[str, bool]) -> None:
            super().__init__()
            self.level = level
            self.overrides = overrides

    class Cancelled(Message):
        """Message sent when modal is cancelled."""
        pass

    # Two-column layout — left column width in cells (#443)
    _LEFT_COL_WIDTH = 32

    def __init__(self, current_config: dict = None, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.level: str = "med"
        self.overrides: Dict[str, bool] = {}
        self.original_overrides: Dict[str, bool] = {}
        self.cursor_pos: int = 0
        self._cols_by_group = _columns_by_group()
        self._flat_rows: List[Tuple[str, str]] = []
        # Index where the right column starts — everything < breakpoint is left (#443)
        self._column_breakpoint: int = 0
        self._rebuild_flat_rows()

    def _rebuild_flat_rows(self) -> None:
        """Rebuild flattened row list — all groups and columns shown."""
        rows: List[Tuple[str, str]] = []
        for group in SUMMARY_GROUPS:
            rows.append(("group", group.id))
            for col in self._cols_by_group.get(group.id, []):
                rows.append(("column", col.id))
        self._flat_rows = rows
        # Split at the group boundary that best balances column height (#443).
        # Walking groups in order, cut as soon as the running total crosses
        # half — keeps a group and its columns together in the same column.
        half = len(rows) / 2
        running = 0
        breakpoint = len(rows)
        for group in SUMMARY_GROUPS:
            group_size = 1 + len(self._cols_by_group.get(group.id, []))
            if running >= half:
                breakpoint = running
                break
            running += group_size
        else:
            breakpoint = running
        self._column_breakpoint = breakpoint
        # Clamp cursor
        if self._flat_rows:
            self.cursor_pos = min(self.cursor_pos, len(self._flat_rows) - 1)

    def _col_effective(self, col_id: str) -> bool:
        """Get effective visibility for a column at current level."""
        col = next((c for c in SUMMARY_COLUMNS if c.id == col_id), None)
        if col is None:
            return False
        return resolve_column_visible(col, self.level, self.overrides)

    def _col_default(self, col_id: str) -> bool:
        """Get default visibility for a column at current level (no overrides)."""
        col = next((c for c in SUMMARY_COLUMNS if c.id == col_id), None)
        if col is None:
            return False
        return self.level in col.detail_levels

    def _group_state(self, group_id: str) -> str:
        """Get group checkbox state: 'all', 'none', or 'mixed'."""
        cols = self._cols_by_group.get(group_id, [])
        if not cols:
            return "all"
        on_count = sum(1 for c in cols if self._col_effective(c.id))
        if on_count == len(cols):
            return "all"
        elif on_count == 0:
            return "none"
        return "mixed"

    def _render_row(self, index: int) -> Optional[Text]:
        """Render a single flat-row (group or column) as one Text line (no newline)."""
        row_type, row_id = self._flat_rows[index]
        is_cursor = index == self.cursor_pos
        prefix = "> " if is_cursor else "  "
        cursor_style = "bold cyan" if is_cursor else ""
        line = Text()

        if row_type == "group":
            group = SUMMARY_GROUPS_BY_ID.get(row_id)
            if group is None:
                return None
            is_identity = group.always_visible
            state = self._group_state(row_id)

            if state == "all":
                check = "[x]"
                check_style = "bold green" if not is_identity else "dim green"
            elif state == "none":
                check = "[ ]"
                check_style = "dim"
            else:
                check = "[-]"
                check_style = "bold yellow"

            line.append(prefix, style=cursor_style)
            line.append(check, style=check_style if not is_identity else "dim")
            line.append(" ", style="")
            name_style = "bold" if is_cursor else ("dim" if is_identity else "")
            line.append(group.name, style=name_style)
            return line

        elif row_type == "column":
            col = next((c for c in SUMMARY_COLUMNS if c.id == row_id), None)
            if col is None:
                return None
            is_on = self._col_effective(row_id)
            is_default = self._col_default(row_id)
            is_identity = col.group == "identity"

            check = "[x]" if is_on else "[ ]"
            check_style = "bold green" if is_on else "dim"

            marker = ""
            if is_on and not is_default:
                marker = " +"
            elif not is_on and is_default:
                marker = " -"

            display_name = col.name or col.id

            line.append(prefix, style=cursor_style)
            line.append("     ", style="")  # indent under group
            line.append(check, style=check_style if not is_identity else "dim")
            line.append(" ", style="")
            name_style = "bold" if is_cursor else ("dim" if is_identity else "")
            line.append(display_name, style=name_style)
            if marker:
                line.append(marker, style="bold cyan" if marker == " +" else "bold red")
            return line

        return None

    def render(self) -> Text:
        """Render the modal content in two side-by-side columns (#443)."""
        text = Text()
        text.append(f"Column Configuration ({self.level})\n", style="bold cyan")
        text.append("j/k:move  space:toggle  a:accept  q:cancel  r:reset\n\n", style="dim")

        breakpoint = self._column_breakpoint
        left_rows = [self._render_row(i) for i in range(breakpoint)]
        right_rows = [self._render_row(i) for i in range(breakpoint, len(self._flat_rows))]
        left_rows = [r for r in left_rows if r is not None]
        right_rows = [r for r in right_rows if r is not None]

        max_lines = max(len(left_rows), len(right_rows))
        for i in range(max_lines):
            if i < len(left_rows):
                left = left_rows[i]
                pad = max(0, self._LEFT_COL_WIDTH - len(left.plain))
                text.append(left)
                if pad:
                    text.append(" " * pad)
            else:
                text.append(" " * self._LEFT_COL_WIDTH)

            text.append("  ")  # inter-column separator

            if i < len(right_rows):
                text.append(right_rows[i])

            text.append("\n")

        return text

    def _update_live_summaries(self) -> None:
        """Update the live summary lines with current overrides."""
        if self._app_ref is None:
            return
        try:
            from .session_summary import SessionSummary
            # Publish the live overrides so the app's header/width lookups
            # see them instead of the persisted prefs (#449).
            if hasattr(self._app_ref, "_live_column_overrides"):
                self._app_ref._live_column_overrides = dict(self.overrides)
            for widget in self._app_ref.query(SessionSummary):
                widget.column_overrides = self.overrides
                widget.refresh()
            # Recompute column widths (also refreshes the header via
            # _update_column_headers at the tail of the recompute).
            if hasattr(self._app_ref, '_recompute_cell_column_widths'):
                self._app_ref._column_widths_dirty = True
                self._app_ref._recompute_cell_column_widths()
                for widget in self._app_ref.query(SessionSummary):
                    widget.refresh()
        except Exception as e:
            logger.debug("Failed to update live summaries: %s", e)

    def on_key(self, event: events.Key) -> None:
        """Handle keyboard navigation."""
        key = event.key
        if not self._flat_rows:
            if key in ("escape", "q", "Q"):
                self._cancel()
                event.stop()
            return

        if key in ("j", "down"):
            self.cursor_pos = (self.cursor_pos + 1) % len(self._flat_rows)
            self.refresh()
            event.stop()

        elif key in ("k", "up"):
            self.cursor_pos = (self.cursor_pos - 1) % len(self._flat_rows)
            self.refresh()
            event.stop()

        elif key in ("space", "enter"):
            self._toggle_current()
            self.refresh()
            self._update_live_summaries()
            event.stop()

        elif key in ("a", "A"):
            self._apply_config()
            event.stop()

        elif key in ("r", "R"):
            # Reset current level to defaults (clear all overrides)
            self.overrides = {}
            self.refresh()
            self._update_live_summaries()
            event.stop()

        elif key in ("escape", "q", "Q"):
            self._cancel()
            event.stop()

    def _toggle_current(self) -> None:
        """Toggle the currently selected row."""
        if not self._flat_rows:
            return
        row_type, row_id = self._flat_rows[self.cursor_pos]

        if row_type == "group":
            group = SUMMARY_GROUPS_BY_ID.get(row_id)
            if group and group.always_visible:
                return  # Can't toggle identity group
            cols = self._cols_by_group.get(row_id, [])
            # Determine current group state
            state = self._group_state(row_id)
            # If all on -> set all off; otherwise -> set all on
            new_val = state != "all"
            for col in cols:
                self.overrides[col.id] = new_val

        elif row_type == "column":
            col = next((c for c in SUMMARY_COLUMNS if c.id == row_id), None)
            if col and col.group == "identity":
                return  # Can't toggle identity columns
            current = self._col_effective(row_id)
            self.overrides[row_id] = not current

    def _apply_config(self) -> None:
        """Apply the current configuration."""
        # Clean up overrides that match defaults (no need to store them)
        cleaned = {}
        for col_id, val in self.overrides.items():
            if val != self._col_default(col_id):
                cleaned[col_id] = val
        self.post_message(self.ConfigChanged(self.level, cleaned))
        self._hide()

    def _cancel(self) -> None:
        """Cancel and restore original config."""
        self.overrides = dict(self.original_overrides)
        self._update_live_summaries()
        self.post_message(self.Cancelled())
        self._hide()

    def show(self, level: str, overrides: Dict[str, bool], app_ref: Optional[Any] = None) -> None:
        """Show the modal for editing a specific level's column overrides."""
        self.level = level
        self.overrides = dict(overrides)
        self.original_overrides = dict(overrides)
        self.cursor_pos = 0
        self._rebuild_flat_rows()
        self._save_focus(app_ref)
        self.refresh()
        self.add_class("visible")
        try:
            self.focus()
        except (AttributeError, Exception) as e:
            logger.debug("Failed to focus modal: %s", e)
