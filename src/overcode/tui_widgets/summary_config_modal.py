"""
Summary line configuration modal for TUI.

Edits per-level column overrides (low/med/high), updating the live
summary lines as you toggle groups and columns.

It is also the guide to the columns (#490): every column is listed under
its group with the header code it shows above the summary line, what it
looks like for the focused agent, and what it means; the foot explains
the highlighted one in full.
"""

import logging
from typing import Dict, List, Optional, Any, Tuple

from textual.message import Message
from textual import events
from rich.text import Text

from ..summary_groups import SUMMARY_GROUPS, SUMMARY_GROUPS_BY_ID
from ..summary_columns import COLUMNS_BY_ID, SUMMARY_COLUMNS, SummaryColumn, resolve_column_visible
from . import dialog_style as ds
from .modal_base import ModalBase

logger = logging.getLogger(__name__)

# Build group -> columns mapping. Excludes CLI-only synthetic columns —
# they never render in the TUI and aren't user-togglable, so surfacing
# them in the configurator is just noise.
def _columns_by_group() -> Dict[str, List[SummaryColumn]]:
    result: Dict[str, List[SummaryColumn]] = {}
    for col in SUMMARY_COLUMNS:
        if col.cli_only:
            continue
        result.setdefault(col.group, []).append(col)
    return result


def _sample(col: SummaryColumn, ctx: Any) -> Optional[Text]:
    """What `col` shows for the agent behind `ctx`, trimmed of alignment
    padding and row colours; None when it doesn't apply to that agent."""
    try:
        if col.visible is not None and not col.visible(ctx):
            return None
        segments = col.render(ctx)
    except Exception as e:
        logger.debug("Column %s failed to render a sample: %s", col.id, e)
        return None
    text = Text()
    for seg, style in segments or ():
        # Drop the row background the summary line paints behind each cell
        text.append(seg, style=(style or "").split(" on ")[0])
    # Trim the alignment padding
    lead = len(text.plain) - len(text.plain.lstrip())
    text = text[lead:]
    text.rstrip()
    return text


class SummaryConfigModal(ModalBase):
    """Modal dialog for configuring per-level column visibility.

    One scrolling list: each group's header (space toggles the group),
    then its columns. Navigate with j/k, toggle with space.
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

    TITLE = "Columns"
    WIDTH = 120
    _NAME_W = 20
    _HEADER_W = 7
    _SAMPLE_W = 16
    _TIP_LINES = 2
    # Rows around the list: the table heading and its rule, then the rule
    # and tip lines at the foot — plus the border
    _CHROME = 2 + 1 + _TIP_LINES + 2

    def __init__(self, current_config: dict = None, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.level: str = "med"
        self.overrides: Dict[str, bool] = {}
        self.original_overrides: Dict[str, bool] = {}
        self.cursor_pos: int = 0
        self._scroll: int = 0
        self._cols_by_group = _columns_by_group()
        self._flat_rows: List[Tuple[str, str]] = []
        # What each column shows for one agent, so the list doubles as a
        # guide to the summary line (#490); filled in by show()
        self._samples: Dict[str, Optional[Text]] = {}
        self._sample_agent: str = ""
        self._rebuild_flat_rows()

    def update_frame(self) -> None:
        self.border_title = f"Columns · {self.level} detail"
        self.border_subtitle = ds.hints(
            ("space", "show/hide"), ("a", "save"), ("r", "reset"), ("esc", "cancel"))

    def _rebuild_flat_rows(self) -> None:
        """Rebuild flattened row list — all groups and columns shown."""
        rows: List[Tuple[str, str]] = []
        for group in SUMMARY_GROUPS:
            rows.append(("group", group.id))
            for col in self._cols_by_group.get(group.id, []):
                rows.append(("column", col.id))
        self._flat_rows = rows
        # Clamp cursor
        if self._flat_rows:
            self.cursor_pos = min(self.cursor_pos, len(self._flat_rows) - 1)

    # -- samples ------------------------------------------------------------

    def _load_samples(self) -> None:
        """Render every column for the focused agent (else the first), to
        show beside its name what it looks like on the summary line."""
        self._samples = {}
        self._sample_agent = ""
        app = self._app_ref
        if app is None:
            return
        try:
            from .session_summary import SessionSummary
            widget = app.focused if isinstance(app.focused, SessionSummary) else None
            if widget is None:
                widget = next(iter(app.query(SessionSummary)), None)
            if widget is None:
                return
            ctx = widget._build_column_context()
            self._sample_agent = widget.session.name
        except Exception as e:
            logger.debug("No agent to sample columns from: %s", e)
            return
        for col in SUMMARY_COLUMNS:
            self._samples[col.id] = _sample(col, ctx)

    # -- sizing -------------------------------------------------------------

    # Agent rows kept in view above the dialog, to watch toggles land (#449)
    _KEEP_AGENTS = 3
    _MIN_LIST = 8

    def relayout(self) -> None:
        """Sit at the foot of the screen rather than near the top, so the
        column headers and the first agents stay visible above it: toggles
        show on them live."""
        super().relayout()
        try:
            screen_h = self.app.size.height
            container = self.app.query_one("#sessions-container")
            keep = container.region.y + self._KEEP_AGENTS
        except Exception:
            return
        room = screen_h - 1 - keep - self._CHROME   # 1: the footer
        if room < self._MIN_LIST:
            room = screen_h - 2 - self._CHROME       # too short: use it all
        self._list_room = max(3, room)
        height = self._list_height + self._CHROME
        x = self.styles.offset.x.value
        self.styles.offset = (int(x), max(0, screen_h - 1 - height))

    @property
    def _list_height(self) -> int:
        room = getattr(self, "_list_room", None)
        if room is None:
            room = getattr(self, "_screen_height", 40) - 4 - self._CHROME
        return max(3, min(len(self._flat_rows), room))

    def _ensure_visible(self) -> None:
        h = self._list_height
        # Keep a group's header in view above its first column
        top = self.cursor_pos
        if top > 0 and self._flat_rows[top - 1][0] == "group" and self._flat_rows[top][0] == "column":
            top -= 1
        if top < self._scroll:
            self._scroll = top
        elif self.cursor_pos >= self._scroll + h:
            self._scroll = self.cursor_pos - h + 1
        self._scroll = max(0, min(self._scroll, len(self._flat_rows) - h))

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
        """One list row, `inner_width` wide: a group header, or a column
        with its header code, a sample and what it means."""
        w = self.inner_width
        row_type, row_id = self._flat_rows[index]
        sel = index == self.cursor_pos

        if row_type == "group":
            group = SUMMARY_GROUPS_BY_ID.get(row_id)
            if group is None:
                return None
            state = self._group_state(row_id)
            lead = ds.check({"all": True, "none": False}.get(state), locked=group.always_visible)
            return ds.section(group.name, w, selected=sel, lead=lead)

        col = COLUMNS_BY_ID.get(row_id)
        if col is None:
            return None
        is_on = self._col_effective(row_id)
        is_default = self._col_default(row_id)
        locked = col.group == "identity"

        line = ds.item(sel)
        line.append("  ")  # indent under the group
        line.append_text(ds.check(is_on, locked=locked))
        if is_on != is_default:
            line.append("+" if is_on else "−", style=f"bold {ds.ACCENT}")
        else:
            line.append(" ")
        line.append(" ")
        name_style = "bold" if sel else (ds.TEXT if is_on else ds.MUTED)
        line.append_text(ds.fit(Text(col.name or col.id, style=name_style), self._NAME_W))
        line.append_text(ds.fit(Text(col.header, style=ds.KEY if is_on else ds.STATE_OTHER), self._HEADER_W))
        if self._samples:
            line.append_text(ds.fit(self._sample_text(row_id), self._SAMPLE_W))
            line.append("  ")
        line.append(col.description, style=ds.MUTED)
        return ds.finish(line, w)

    def _sample_text(self, col_id: str) -> Text:
        sample = self._samples.get(col_id)
        if sample is None:
            return Text("n/a", style=f"italic {ds.STATE_OTHER}")
        return sample.copy() if sample.plain.strip() else Text("–", style=ds.STATE_OTHER)

    def _heading(self) -> Text:
        """Names the table's columns."""
        line = Text(" " * 6)
        line.append(f"{'column':<{self._NAME_W}}{'header':<{self._HEADER_W}}", style=ds.MUTED)
        if self._samples:
            line.append_text(ds.fit(Text(f"for {self._sample_agent}", style=ds.MUTED), self._SAMPLE_W))
            line.append("  ")
        line.append("what it shows", style=ds.MUTED)
        return ds.finish(line, self.inner_width)

    def _tip(self) -> List[Text]:
        """The highlighted row explained: its full description, then its
        defaults, whether it sorts, and what the markers mean."""
        w = self.inner_width
        if not self._flat_rows:
            return ds.wrap("", w, self._TIP_LINES)
        row_type, row_id = self._flat_rows[self.cursor_pos]
        facts = Text(style=ds.MUTED)
        if row_type == "group":
            group = SUMMARY_GROUPS_BY_ID.get(row_id)
            cols = self._cols_by_group.get(row_id, [])
            on = sum(1 for c in cols if self._col_effective(c.id))
            lines = ds.wrap(f"{group.name if group else row_id}: {on} of {len(cols)} columns shown", w, 1)
            if group is not None and group.always_visible:
                facts.append("Always shown")
            else:
                facts.append("space shows or hides the whole group")
            return lines + [ds.finish(facts, w)]

        col = COLUMNS_BY_ID[row_id]
        head = f"{col.header} · " if col.header else ""
        lines = ds.wrap(f"{head}{col.name or col.id} — {col.description}", w, self._TIP_LINES - 1)
        levels = [lv for lv in ("low", "med", "high", "full") if lv in col.detail_levels or lv == "full"]
        facts.append("default at " + " ".join(levels))
        if col.group == "identity":
            facts.append(" · always shown")
        elif self._col_effective(row_id) != self._col_default(row_id):
            facts.append(f" · you turned it {'on' if self._col_effective(row_id) else 'off'} at {self.level}",
                         style=ds.ACCENT)
        if col.sort_key is not None:
            facts.append(" · sort with S or click its header")
        if col.visible is not None:
            facts.append(" · appears only when it applies")
        return lines + [ds.finish(facts, w)]

    def render(self) -> Text:
        """The columns as one scrolling table, grouped, with the
        highlighted one explained at the foot (#490)."""
        w = self.inner_width
        self._ensure_visible()
        text = Text(no_wrap=True, overflow="crop")
        text.append_text(self._heading())
        text.append("\n")
        text.append_text(ds.rule(w))

        h = self._list_height
        for i in range(self._scroll, min(len(self._flat_rows), self._scroll + h)):
            row = self._render_row(i)
            if row is not None:
                text.append("\n")
                text.append_text(row)

        text.append("\n")
        text.append_text(ds.rule(w))
        more = len(self._flat_rows) - h
        if more > 0:
            # How far down the list, set into the rule's right end
            pos = f" {self._scroll + 1}–{self._scroll + h} of {len(self._flat_rows)} "
            text.right_crop(len(pos))
            text.append(pos, style=ds.MUTED)
        for line in self._tip():
            text.append("\n")
            text.append_text(line)
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
        self._scroll = 0
        self._rebuild_flat_rows()
        self._save_focus(app_ref)
        self._load_samples()
        self._show()
