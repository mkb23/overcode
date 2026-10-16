"""→ / ←: a parent's delegated work as read-only rows (0.6.1 observability, phase 2).

docs/design/agent-observability-0.6.1.md §4. The engine publishes an
expanded parent's virtual rows (subagents, workflow groups with their
phases, teammates) when this view subscribes to ``swarm:<id>``; the
subscription is the set of parents expanded here. The rows are drawn by a
``SwarmRows`` widget right under the parent's row, laid out by
``swarm_view``. The highlight can move onto them (``swarm_cursor``) while
the parent's row keeps Textual focus, so the split's bottom pane stays on
the parent: a virtual row has no pane of its own.

Virtual rows are read only. With the cursor on one, the actions that act
on "the focused agent" (send keys, kill, restart, rename, …) refuse with a
notice rather than reach the parent; Enter moves to the parent's pane.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from .. import swarm_view
from ..swarm_view import SwarmLine
from .activity import _action_name

# Actions that would act on the agent under the highlight. On a virtual row
# they do nothing but say why (the row belongs to the parent's session).
READ_ONLY_BLOCKED = frozenset({
    "kill_focused", "restart_focused", "fork_focused", "rename_focused",
    "send_escape_to_focused", "send_ctrl_o_to_focused",
    "send_1_to_focused", "send_2_to_focused", "send_3_to_focused",
    "send_4_to_focused", "send_5_to_focused",
    "focus_command_bar", "focus_standing_orders", "focus_human_annotation",
    "toggle_sleep", "toggle_heartbeat_pause", "configure_heartbeat",
    "toggle_enhanced_context", "edit_agent_value", "edit_cost_budget",
    "sync_to_main_and_clear", "cycle_focal_repo", "set_billing_code",
    "resize_focused_window",
})


class SwarmMixin:
    """Expand/collapse delegated work into virtual rows; move through them."""

    def _init_swarm_state(self) -> None:
        # Parents whose delegated work shows as rows (the engine subscription)
        self.swarm_expanded: Set[str] = set()
        # Groups and phases the person opened (True) or closed (False);
        # the rest follow their size (swarm_view.AUTO_COLLAPSE_ABOVE)
        self.swarm_opened: Dict[str, bool] = {}
        # Members whose detail line is open
        self.swarm_details: Set[str] = set()
        # The virtual row under the highlight (None: the agent's own row)
        self.swarm_cursor: Optional[str] = None
        self._swarm_lines: Dict[str, List[SwarmLine]] = {}

    # ── what the engine published ────────────────────────────────────

    def _swarm_virtual(self) -> dict:
        return getattr(self.engine_link, "virtual", None) or {}

    def _swarm_show_kind(self) -> bool:
        from ..summary_columns import COLUMNS_BY_ID

        widget = self._get_focused_widget()
        col = COLUMNS_BY_ID.get("kind")
        return bool(widget is not None and col is not None and widget.column_visible(col))

    def _swarm_layout(self, parent_id: str) -> List[SwarmLine]:
        return swarm_view.layout(parent_id, self._swarm_virtual(), self.swarm_opened,
                                 self.swarm_details, emoji_free=self.emoji_free,
                                 show_kind=self._swarm_show_kind())

    def _report_swarm(self) -> None:
        client = getattr(self.engine_link, "client", None)
        if client is not None and hasattr(client, "set_swarm"):
            client.set_swarm(set(self.swarm_expanded))

    # ── the rows under each expanded parent ──────────────────────────

    def _sync_swarm_rows(self) -> None:
        """Put each expanded parent's lines right under its row; drop the rest."""
        from textual.containers import ScrollableContainer
        from textual.css.query import NoMatches

        from ..tui_widgets import SessionSummary
        from ..tui_widgets.swarm_rows import SwarmRows

        try:
            container = self.query_one("#sessions-container", ScrollableContainer)
        except NoMatches:
            return
        parents = {w.session.id: w for w in self.query(SessionSummary)
                   if w.session.id in self.swarm_expanded and w.display}
        existing = {w.parent_id: w for w in self.query(SwarmRows)}
        for pid, widget in existing.items():
            if pid not in parents:
                widget.remove()
                self._swarm_lines.pop(pid, None)
        for widget in self.query(SessionSummary):
            expanded = widget.session.id in parents
            if widget.swarm_expanded != expanded:
                widget.swarm_expanded = expanded
                widget.refresh()
        focused = self._get_focused_widget()
        focused_id = focused.session.id if focused is not None else None
        for pid, parent in parents.items():
            lines = self._swarm_layout(pid)
            self._swarm_lines[pid] = lines
            rows = existing.get(pid)
            if rows is None:
                rows = SwarmRows(pid)
                container.mount(rows, after=parent)
            else:
                children = list(container.children)
                try:
                    if children.index(rows) != children.index(parent) + 1:
                        container.move_child(rows, after=parent)
                except ValueError:
                    pass
            cursor = self.swarm_cursor if pid == focused_id else None
            if cursor is not None and swarm_view.line_for(lines, cursor) is None:
                # Its row went away (finished and folded): back to the parent
                cursor = self.swarm_cursor = None
            rows.show(lines, cursor)
        if self.swarm_cursor is not None and focused_id not in parents:
            self.swarm_cursor = None

    def _swarm_scroll_to_cursor(self) -> None:
        from textual.geometry import Region

        from ..tui_widgets.swarm_rows import SwarmRows

        if self.swarm_cursor is None:
            return
        for rows in self.query(SwarmRows):
            offset = rows.line_offset(self.swarm_cursor)
            if offset is None:
                continue
            try:
                container = rows.parent
                region = rows.virtual_region
                container.scroll_to_region(
                    Region(region.x, region.y + offset, region.width, 1), animate=False)
            except Exception:
                pass
            return

    def _swarm_focus_moved(self, widget: Any) -> None:
        """The highlight moved to another agent's row (j/k, a click, a jump)."""
        if self.swarm_cursor is not None and not self.swarm_cursor.startswith(
                f"{widget.session.id}/"):
            self.swarm_cursor = None
            self._sync_swarm_rows()

    # ── → / ← ──────────────────────────────────────────────────────

    def action_expand_swarm(self) -> None:
        """→: expand the focused agent's delegated work, or go one level in."""
        widget = self._get_focused_widget()
        if widget is None:
            return
        pid = widget.session.id
        if self.swarm_cursor is None:
            if widget.session.is_remote:
                self.notify("Delegated work shows for this machine's agents only",
                            severity="information")
                return
            if not widget.swarm_rollup:
                self.notify(f"{widget.session.name} has not delegated any work",
                            severity="information")
                return
            if pid not in self.swarm_expanded:
                self.swarm_expanded.add(pid)
                self._report_swarm()
                self._sync_swarm_rows()
                return
            lines = self._swarm_lines.get(pid) or []
            first, _ = swarm_view.step(lines, None, 1)
            if first is not None:
                self.swarm_cursor = first
                self._sync_swarm_rows()
                self._swarm_scroll_to_cursor()
            return
        line = swarm_view.line_for(self._swarm_lines.get(pid) or [], self.swarm_cursor)
        if line is None:
            return
        if line.kind in ("group", "phase"):
            layout = swarm_view.Layout(pid, self._swarm_virtual(), self.swarm_opened)
            if not layout.is_open(line.key):
                self.swarm_opened[line.key] = True
            else:
                nxt, inside = swarm_view.step(self._swarm_lines[pid], line.key, 1)
                if inside and nxt is not None:
                    self.swarm_cursor = nxt
        elif line.kind == "more":
            self.swarm_opened[line.owner] = True
        elif line.kind == "member":
            self.swarm_details.add(line.key)
        self._sync_swarm_rows()
        self._swarm_scroll_to_cursor()

    def action_collapse_swarm(self) -> None:
        """←: close what the cursor is on, or go up a level; on the agent, collapse."""
        widget = self._get_focused_widget()
        if widget is None:
            return
        pid = widget.session.id
        if self.swarm_cursor is None:
            if pid in self.swarm_expanded:
                self.swarm_expanded.discard(pid)
                self._report_swarm()
                self._sync_swarm_rows()
            return
        line = swarm_view.line_for(self._swarm_lines.get(pid) or [], self.swarm_cursor)
        if line is None:
            self.swarm_cursor = None
        elif line.kind == "member" and line.key in self.swarm_details:
            self.swarm_details.discard(line.key)
        elif line.kind in ("group", "phase") and swarm_view.Layout(
                pid, self._swarm_virtual(), self.swarm_opened).is_open(line.key):
            self.swarm_opened[line.key] = False
        else:
            self.swarm_cursor = line.owner or None
        self._sync_swarm_rows()
        self._swarm_scroll_to_cursor()

    # ── j / k through the virtual rows ───────────────────────────────

    def _swarm_step(self, direction: int) -> bool:
        """Move the highlight through the focused parent's rows; True if it did."""
        widgets = self._get_widgets_in_session_order()
        if not widgets:
            return False
        index = self.focused_session_index
        if not 0 <= index < len(widgets):
            return False
        pid = widgets[index].session.id
        lines = self._swarm_lines.get(pid) if pid in self.swarm_expanded else None
        if lines:
            cursor, inside = swarm_view.step(lines, self.swarm_cursor, direction)
            if inside or (direction > 0 and cursor is not None):
                self.swarm_cursor = cursor
                self._sync_swarm_rows()
                self._swarm_scroll_to_cursor()
                return True
            if self.swarm_cursor is not None:
                self.swarm_cursor = None  # leaving the rows: the next agent below
                self._sync_swarm_rows()
        if direction < 0 and self.swarm_cursor is None and index > 0:
            # Up from an agent's row onto the previous agent's last line
            above = widgets[index - 1].session.id
            above_lines = self._swarm_lines.get(above) if above in self.swarm_expanded else None
            keys = [ln.key for ln in (above_lines or []) if ln.kind != "detail"]
            if keys:
                self._user_navigated = True
                self.focused_session_index = index - 1
                self.swarm_cursor = keys[-1]
                self._sync_swarm_rows()
                self._swarm_scroll_to_cursor()
                return True
        return False

    # ── read only ───────────────────────────────────────────────────

    async def run_action(self, action: Any, default_namespace: Any = None,
                         namespaces: Any = None) -> bool:
        if self.swarm_cursor is not None and (default_namespace is None
                                              or default_namespace is self):
            name = _action_name(action)
            if name == "send_enter_to_focused":
                # A virtual row has no pane: Enter goes to the parent's
                self.split.focus_bottom_pane()
                return True
            if name in READ_ONLY_BLOCKED:
                self.notify("Read-only row: delegated work belongs to its parent's session "
                            "(← back to the agent)", severity="information")
                return True
        elif default_namespace is None or default_namespace is self:
            # A 🌙 background session takes only Claude's own actions
            handled = getattr(self, "_background_action", None)
            if handled is not None and handled(_action_name(action)):
                return True
        return await super().run_action(action, default_namespace, namespaces)
