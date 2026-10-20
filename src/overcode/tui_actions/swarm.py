"""→ / ←: a parent's delegated work as read-only rows (0.6.1 observability, phase 2).

docs/design/agent-observability-0.6.1.md §4. The engine publishes an
expanded parent's virtual rows (its subagents, forks, workflow agents and
teammates) when this view subscribes to ``swarm:<id>``; the subscription
is the set of parents expanded here. The rows are drawn by a
``SwarmRows`` widget right under the parent's row, laid out flat by
``swarm_view`` (live members, then those finished within
``finished_row_seconds``, as ``swarm_finished`` allows). The highlight
can move onto them (``swarm_cursor``) while the parent's row keeps Textual
focus, so the split's bottom pane stays on the parent: a virtual row has
no pane of its own.

Which parents are expanded: the swarm-detail cycle (``f``) sets a standing
rule (``off``: none; ``groups``: every agent with delegated work in
flight; ``needs-you``: every agent with a 🟠 member, listing only those
until the rest are opened), and → / ← / ``X`` override it per parent. The
rule, the overrides, opened "+k more" lists (``<id>/members``) and what
finished members show as (``swarm_finished``, the visibility dialog)
persist in ``tui_preferences.json``; the group, phase and done keys of
earlier builds are dropped on load.

The TYPE column (each member's agent type) earns its width only while the
members on screen are of two or more types: ``swarm_types_shown``, carried
to every row in ``FleetFlags``.

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
    "new_agent_in_worktree",
    "send_escape_to_focused", "send_ctrl_o_to_focused",
    "send_1_to_focused", "send_2_to_focused", "send_3_to_focused",
    "send_4_to_focused", "send_5_to_focused",
    "focus_command_bar", "focus_standing_orders", "focus_human_annotation",
    "toggle_sleep", "toggle_heartbeat_pause", "configure_heartbeat",
    "toggle_enhanced_context", "edit_agent_value", "edit_cost_budget",
    "sync_to_main_and_clear", "cycle_focal_repo", "set_billing_code",
    "resize_focused_window",
})

# The swarm-detail cycle (design §4.4): which parents open by themselves
SWARM_DETAIL_MODES = ("off", "groups", "needs_you")
SWARM_DETAIL_NOTES = {
    "off": "Delegated work: rollup only (→ opens one agent's)",
    "groups": "Delegated work: open for every agent with work in flight",
    "needs_you": "Delegated work: open only where a subagent needs you (🟠)",
}


def _parent_of(key: str) -> str:
    return key.split("/", 1)[0]


def _open_key(key: Any) -> bool:
    """A key ``swarm_opened`` keeps: ``<id>/members``. The group, phase and
    done keys of earlier layouts (0.6.1 betas) mean nothing now and are
    dropped."""
    if not isinstance(key, str) or key.count("/") != 1:
        return False
    return key.endswith(swarm_view.MEMBERS_SUFFIX)


class SwarmMixin:
    """Expand/collapse delegated work into virtual rows; move through them."""

    def _init_swarm_state(self) -> None:
        # The standing rule: which parents open by themselves (SWARM_DETAIL_MODES)
        self.swarm_detail: str = "off"
        # What finished members show as (swarm_view.FINISHED_MODES)
        self.swarm_finished: str = swarm_view.FINISHED_SHOW
        # Per-parent overrides of the rule: True expanded, False collapsed
        self.swarm_manual: Dict[str, bool] = {}
        # Parents whose delegated work shows as rows (the rule plus the
        # overrides, over the agents on screen): the engine subscription
        self.swarm_expanded: Set[str] = set()
        # "+k more" lists (<id>/members) the person opened; the rest are
        # closed (swarm_view.AUTO_COLLAPSE_ABOVE)
        self.swarm_opened: Dict[str, bool] = {}
        # The members on screen are of two or more types: the TYPE column shows
        self.swarm_types_shown: bool = False
        # Members whose detail line is open
        self.swarm_details: Set[str] = set()
        # The virtual row under the highlight (None: the agent's own row)
        self.swarm_cursor: Optional[str] = None
        self._swarm_lines: Dict[str, List[SwarmLine]] = {}
        # The SwarmRows widget under each expanded parent (by parent id)
        self._swarm_widgets: Dict[str, Any] = {}
        self._swarm_reported: Optional[frozenset] = None

    def _load_swarm_prefs(self) -> None:
        """The saved rule, overrides and open lines (an odd value is dropped)."""
        prefs = self._prefs
        mode = getattr(prefs, "swarm_detail", "off")
        self.swarm_detail = mode if mode in SWARM_DETAIL_MODES else "off"
        finished = getattr(prefs, "swarm_finished", swarm_view.FINISHED_SHOW)
        self.swarm_finished = finished if finished in swarm_view.FINISHED_MODES \
            else swarm_view.FINISHED_SHOW
        manual = getattr(prefs, "swarm_expanded", None)
        self.swarm_manual = {k: v for k, v in manual.items()
                             if isinstance(k, str) and isinstance(v, bool)} \
            if isinstance(manual, dict) else {}
        opened = getattr(prefs, "swarm_opened", None)
        self.swarm_opened = {k: v for k, v in opened.items()
                             if _open_key(k) and isinstance(v, bool)} \
            if isinstance(opened, dict) else {}

    def _save_swarm_prefs(self) -> None:
        """Persist the rule, the overrides and the open lines, dropping
        whatever belongs to an agent that no longer exists."""
        known = {s.id for s in getattr(self, "sessions", ()) if not s.is_remote}
        manual, opened = self.swarm_manual, self.swarm_opened
        if known:  # before the first refresh nothing is known: prune nothing
            manual = {k: v for k, v in manual.items() if k in known}
            opened = {k: v for k, v in opened.items() if _parent_of(k) in known}
            self.swarm_manual, self.swarm_opened = manual, opened
        self._prefs.swarm_detail = self.swarm_detail
        self._prefs.swarm_finished = self.swarm_finished
        self._prefs.swarm_expanded = dict(manual)
        self._prefs.swarm_opened = dict(opened)
        self._save_prefs()

    # ── what the engine published ────────────────────────────────────

    def _swarm_virtual(self) -> dict:
        return getattr(self.engine_link, "virtual", None) or {}

    def _swarm_layout_of(self, parent_id: str, base_depth: int = 0) -> swarm_view.Layout:
        return swarm_view.Layout(parent_id, self._swarm_virtual(), self.swarm_opened,
                                 self.swarm_details, emoji_free=self.emoji_free,
                                 needs_you=self.swarm_detail == "needs_you",
                                 base_depth=base_depth, finished=self.swarm_finished)

    def _swarm_layout(self, parent_id: str, base_depth: int = 0) -> List[SwarmLine]:
        return self._swarm_layout_of(parent_id, base_depth).build()

    # ── which parents are expanded ────────────────────────────────────

    def _swarm_auto(self, widget: Any) -> bool:
        """Whether the standing rule opens this agent's delegated work."""
        rollup = widget.swarm_rollup
        if not rollup or widget.session.is_remote or self.swarm_detail == "off":
            return False
        if self.swarm_detail == "needs_you":
            return bool(rollup.get("waiting"))
        return bool(rollup.get("running") or rollup.get("yellow") or rollup.get("waiting")
                    or rollup.get("idle") or rollup.get("workflows_running"))

    def _swarm_wanted(self) -> Set[str]:
        from ..tui_widgets import SessionSummary

        out = set()
        for widget in self.query(SessionSummary):
            if widget.session.is_remote:
                continue
            sid = widget.session.id
            want = self.swarm_manual.get(sid)
            if want if want is not None else self._swarm_auto(widget):
                out.add(sid)
        return out

    def _swarm_subscription(self) -> frozenset:
        """What the engine is asked for: the expanded parents."""
        return frozenset(self.swarm_expanded)

    def _report_swarm(self) -> None:
        subs = self._swarm_subscription()
        if subs == self._swarm_reported:
            return
        client = getattr(self.engine_link, "client", None)
        if client is not None and hasattr(client, "set_swarm"):
            client.set_swarm(set(subs))
            self._swarm_reported = subs

    def _swarm_refresh(self) -> None:
        """Re-apply the rule and overrides; resubscribe and redraw if that
        changed anything. Cheap when nothing moved (called on every update)."""
        wanted = self._swarm_wanted()
        if wanted != self.swarm_expanded:
            self.swarm_expanded = wanted
            self._report_swarm()
            self._sync_swarm_rows()
        elif self._swarm_subscription() != self._swarm_reported:
            self._report_swarm()

    def _swarm_set(self, widget: Any, expanded: bool) -> None:
        """→ / ← / X on one parent: an override, unless the rule agrees."""
        sid = widget.session.id
        if expanded == self._swarm_auto(widget):
            self.swarm_manual.pop(sid, None)
        else:
            self.swarm_manual[sid] = expanded

    def _swarm_changed(self) -> None:
        """After the person changed what is open: resubscribe, redraw, save."""
        self.swarm_expanded = self._swarm_wanted()
        self._report_swarm()
        self._sync_swarm_rows()
        self._swarm_scroll_to_cursor()
        self._save_swarm_prefs()

    # ── the rows under each expanded parent ──────────────────────────

    def _sync_swarm_rows(self) -> None:
        """Put each expanded parent's lines right under its row; drop the rest.

        The lines render on the agent rows' column grid: when their cells
        changed, the aligned widths are worked out again over agent and
        virtual rows (and the agent rows repaint if that moved them).
        """
        self._sync_timeline_fold()  # the timeline's density rows fold in step (phase 4)
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
        regrid = False
        for pid, widget in existing.items():
            if pid not in parents:
                widget.remove()
                self._swarm_lines.pop(pid, None)
                regrid = True
        self._swarm_widgets = {}
        for widget in self.query(SessionSummary):
            expanded = widget.session.id in parents
            if (widget.swarm_expanded != expanded
                    or widget.swarm_finished != self.swarm_finished):
                widget.swarm_expanded = expanded
                widget.swarm_finished = self.swarm_finished
                widget.refresh()
        focused = self._get_focused_widget()
        focused_id = focused.session.id if focused is not None else None
        for pid, parent in parents.items():
            lines = self._swarm_layout(pid, getattr(parent, "tree_depth", 0) or 0)
            self._swarm_lines[pid] = lines
            rows = existing.get(pid)
            if rows is None:
                rows = SwarmRows(pid)
                container.mount(rows, after=parent)
                regrid = True
            else:
                children = list(container.children)
                try:
                    if children.index(rows) != children.index(parent) + 1:
                        container.move_child(rows, after=parent)
                except ValueError:
                    pass
            self._swarm_widgets[pid] = rows
            cursor = self.swarm_cursor if pid == focused_id else None
            if cursor is not None and swarm_view.line_for(lines, cursor) is None:
                # Its row went away (finished and folded): back to the parent
                cursor = self.swarm_cursor = None
            rows.set_lines(lines, cursor, parent)
        if self.swarm_cursor is not None and focused_id not in parents:
            self.swarm_cursor = None
        self._fit_swarm_names()
        # TYPE earns its width at two or more member types on screen; every
        # row (agent and virtual) reads the switch from its FleetFlags
        types = swarm_view.types_of(ln for pid in self._swarm_widgets
                                    for ln in self._swarm_lines.get(pid) or ())
        flipped = (len(types) >= 2) != self.swarm_types_shown
        if flipped:
            self.swarm_types_shown = not self.swarm_types_shown
            self._update_fleet_flags()
            regrid = True
        for rows in self._swarm_widgets.values():
            regrid = rows.build_cells() or regrid
        if regrid:
            old = list(self.column_widths)
            self._column_widths_dirty = True
            self._recompute_cell_column_widths()
            if flipped or self.column_widths != old:
                for widget in self.query(SessionSummary):
                    widget.refresh_if_changed()
        self._paint_swarm_rows()

    def _fit_swarm_names(self) -> None:
        """Widen the NAME column for the virtual rows' names (connector,
        marker, title), as for a longer agent name; the detail level's cap
        still applies (SessionSummary)."""
        from rich.cells import cell_len

        width = 0
        for lines in (self._swarm_lines.get(pid) or [] for pid in self._swarm_widgets):
            for ln in lines:
                if ln.kind != "detail":
                    width = max(width, cell_len(ln.connector) + (3 if ln.marker else 0)
                                + cell_len(ln.label))
        if width == getattr(self, "_swarm_name_width", 0):
            return
        self._swarm_name_width = width
        name_width = max(getattr(self, "_session_name_width", self.max_name_width), width)
        if name_width != self.max_name_width:
            self.max_name_width = name_width
            self._column_widths_dirty = True

    def _swarm_row_widgets(self) -> list:
        return list(getattr(self, "_swarm_widgets", {}).values())

    def _swarm_cells(self) -> list:
        """Every expanded parent's virtual rows' cells, built now (for the widths)."""
        out = []
        for rows in self._swarm_row_widgets():
            rows.build_cells()
            out.extend(rows.measure())
        return out

    def _paint_swarm_rows(self) -> None:
        """Pad the virtual rows to the aligned widths (a no-op when unchanged)."""
        widths = self.column_widths
        edges = getattr(self, "column_edges", [])
        mode = getattr(self, "summary_content_mode", "ai_short")
        width = self.size.width
        for rows in self._swarm_row_widgets():
            rows.paint(widths, edges, mode, width)

    def _tick_swarm_rows(self) -> None:
        """The row clock: live members' durations."""
        for rows in self._swarm_row_widgets():
            rows.tick()

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

    def _tree_children(self, session_id: str) -> bool:
        return self._prefs.sort_mode == "by_tree" and any(
            s.parent_session_id == session_id for s in self.sessions)

    def action_expand_swarm(self) -> None:
        """→: expand the focused agent (its folded children, then its
        delegated work), or go one level into its rows."""
        widget = self._get_focused_widget()
        if widget is None:
            return
        pid = widget.session.id
        if self.swarm_cursor is None:
            if pid in self.collapsed_parents and self._tree_children(pid):
                self._fold_children(pid, False)
                return
            if widget.session.is_remote:
                self.notify("Delegated work shows for this machine's agents only",
                            severity="information")
                return
            if not widget.swarm_rollup:
                self.notify(f"{widget.session.name} has not delegated any work",
                            severity="information")
                return
            if pid not in self.swarm_expanded:
                self._swarm_set(widget, True)
                self._swarm_changed()
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
        if line.kind == "more":
            self.swarm_opened[swarm_view.members_key(pid)] = True
        elif line.kind == "member":
            self.swarm_details.add(line.key)
        self._swarm_changed()

    def action_collapse_swarm(self) -> None:
        """←: close what the cursor is on, or go up a level; on the agent,
        collapse its delegated work, then its children (tree sort), then
        move to its parent."""
        widget = self._get_focused_widget()
        if widget is None:
            return
        pid = widget.session.id
        if self.swarm_cursor is None:
            if pid in self.swarm_expanded:
                self._swarm_set(widget, False)
                # A long list opens compact again next time
                self.swarm_opened.pop(swarm_view.members_key(pid), None)
                self._swarm_changed()
            elif self._tree_children(pid) and pid not in self.collapsed_parents:
                self._fold_children(pid, True)
            elif self._prefs.sort_mode == "by_tree" and widget.session.parent_session_id:
                self._focus_session_id(widget.session.parent_session_id)
            return
        line = swarm_view.line_for(self._swarm_lines.get(pid) or [], self.swarm_cursor)
        if line is None:
            self.swarm_cursor = None
        elif line.kind == "member" and line.key in self.swarm_details:
            self.swarm_details.discard(line.key)
        else:
            self.swarm_cursor = line.owner or None
        self._swarm_changed()

    def _focus_session_id(self, session_id: str) -> bool:
        for i, w in enumerate(self._get_widgets_in_session_order()):
            if w.session.id == session_id:
                self._user_navigated = True
                self.focused_session_index = i
                return True
        return False

    # ── X and f: everything at once ───────────────────────────────────

    def action_toggle_expand_all(self) -> None:
        """X: collapse everything open (delegated work, and children in tree
        sort); with nothing open, expand every agent's delegated work and
        every fold. The swarm-detail rule stays as it is."""
        from ..tui_widgets import SessionSummary

        tree = self._prefs.sort_mode == "by_tree"
        ids = {s.id for s in self.sessions}
        parents = {s.parent_session_id for s in self.sessions
                   if s.parent_session_id in ids} if tree else set()
        widgets = [w for w in self.query(SessionSummary) if not w.session.is_remote]
        if self.swarm_expanded or (parents - self.collapsed_parents):
            for widget in widgets:
                if widget.session.id in self.swarm_expanded:
                    self._swarm_set(widget, False)
            self.swarm_cursor = None
            folds = parents
            note = "Collapsed all"
        else:
            delegating = [w for w in widgets if w.swarm_rollup]
            for widget in delegating:
                self._swarm_set(widget, True)
            folds = set()
            if not delegating and not (tree and self.collapsed_parents & parents):
                self.notify("Nothing to expand", severity="information")
                return
            note = "Expanded all"
        self.notify(note, severity="information")
        self._swarm_changed()
        if tree and folds != (self.collapsed_parents & parents):
            self.collapsed_parents = set(folds)
            self._prefs.collapsed_parents = set(folds)
            self._save_prefs()
            self._sort_sessions()
            self.update_session_widgets()
            self.update_timeline()

    def action_cycle_swarm_detail(self) -> None:
        """f: off → groups → needs-you, at the agent level. Starts afresh:
        per-agent choices and opened lists are dropped, so the rule decides."""
        i = SWARM_DETAIL_MODES.index(self.swarm_detail) if self.swarm_detail in SWARM_DETAIL_MODES else -1
        self._set_swarm_detail(SWARM_DETAIL_MODES[(i + 1) % len(SWARM_DETAIL_MODES)])

    def _set_swarm_detail(self, mode: str, notify: bool = True) -> None:
        """Set the rule (f, the visibility dialog), starting afresh."""
        self.swarm_detail = mode if mode in SWARM_DETAIL_MODES else "off"
        self.swarm_manual.clear()
        self.swarm_opened.clear()
        self.swarm_cursor = None
        self._swarm_changed()
        if notify:
            self.notify(SWARM_DETAIL_NOTES[self.swarm_detail], severity="information")

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
