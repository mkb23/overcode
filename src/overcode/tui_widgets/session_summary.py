"""
Session summary widget for TUI.

Displays a single-line session summary with status, metrics, and content area.
"""

import time
from datetime import datetime, timedelta
from typing import List, Optional

from textual.widgets import Static
from textual.reactive import reactive
from textual import events
from rich.cells import cell_len
from rich.text import Text

from ..clock import from_iso
from ..name_source import name_marker
from ..session_manager import Session
from ..status_patterns import extract_from_pane, extract_sleep_duration
from ..history_reader import AgentSessionStats, synthesize_remote_stats
from .. import swarm_view, tui_engine
from ..tui_helpers import (
    calculate_uptime,
    get_current_state_times,
    get_status_symbol,
    get_summary_content_text,
)
from ..summary_columns import (
    COLUMNS_BY_ID, ColumnContext, FleetFlags, FleetRow, SummaryColumn, SUMMARY_COLUMNS, column_at,
    cell_segments, cell_text, grid_room, render_cell, resolve_column_visible, pad_and_join_cells,
)


_SCRAPED_RECAP_PLACEHOLDERS = frozenset({
    "", "Initializing...", "Idle", "Restarting...", "Reviving...",
    "Synced to main",
})


def _scraped_recap_from_stats(stats) -> Optional[str]:
    """Return the scraped last-activity string from stats, skipping placeholders (#440)."""
    if not stats:
        return None
    task = (getattr(stats, "current_task", "") or "").strip()
    if not task or task in _SCRAPED_RECAP_PLACEHOLDERS:
        return None
    return task


# Assigning any public attribute of a row may change what it draws, so it
# drops the row's built cells (SessionSummary.__setattr__); these do not.
_NOT_ROW_INPUTS = frozenset({"tooltip"})
# Private attributes the row draws from
_PRIVATE_ROW_INPUTS = frozenset({"_status_changed_at"})
# The attributes that decide which columns show
_VISIBILITY_INPUTS = frozenset({"summary_detail", "column_overrides", "uniform_columns"})
_UNSET = object()

# The NAME column grows to the widest name (tree prefix, markers and fold
# included: the app's max_name_width) up to this many cells, by detail
# level: room for a nested child's or a 🌙 row's name, less where more
# columns compete for the line
NAME_WIDTH_CAPS = {"low": 42, "med": 38, "high": 34, "full": 34}
NAME_WIDTH_MIN = 8
FOLD_SUFFIX = " ▶"


def name_lead(session, emoji_free: bool = False) -> str:
    """What leads the name in its cell: 🌙 for a Claude background session
    (agent-observability §4.0), then "~" for an auto name (#542)."""
    marker = ""
    if getattr(session, "kind", None) == "claude_bg":
        from ..claude_bg import MARKER, MARKER_ASCII
        marker = (MARKER_ASCII if emoji_free else MARKER) + " "
    return marker + name_marker(getattr(session, "name_source", None))


class _BuiltRow:
    """A row's cells as last built, and the line made from them.

    ``cells_key`` is what the cells read from outside the row (focus, the
    app's name widths, sisters); ``widths``/``width`` are what the line was
    padded to. Assigning one of the row's own inputs drops the whole thing.
    """

    __slots__ = ("cells_key", "ctx", "columns", "segments", "cells", "ticking",
                 "widths", "width", "text")

    def __init__(self, cells_key, ctx, columns):
        self.cells_key = cells_key
        self.ctx = ctx
        self.columns = columns
        self.segments = [cell_segments(c, ctx) for c in columns]
        self.cells = [cell_text(segs) for segs in self.segments]
        self.ticking = tuple(i for i, col in enumerate(columns) if col.ticks)
        self.widths = None
        self.width = None
        self.text: Optional[Text] = None


class SessionSummary(Static, can_focus=True):
    """Widget displaying single-line session summary

    The 250 ms row clock (``refresh_if_changed``) must stay cheap for a
    fleet of rows: each row keeps its cells (``_BuiltRow``) and the tick
    redraws only the cells of ``ticks`` columns, repainting only if one
    moved. The cells are built again when anything they read changes:
    assigning a row input (any public attribute, see ``__setattr__``), a
    ``refresh()`` from outside (the convention after changing a session in
    place), focus or selection, or the app's name widths. ``_render_row``
    is the uncached render the cache must always equal.
    """

    # Class defaults, so a row built without __init__ (tests) has them
    _built: Optional[_BuiltRow] = None
    _visible: Optional[tuple] = None
    _visible_ids: frozenset = frozenset()
    _pending: bool = False  # the tick built the line the next render() draws
    _full_builds: int = 0  # times the cells were built from scratch (tests)
    # Its hide-when-uniform columns' values when the app last looked
    # (SupervisorTUI._update_uniform_columns)
    _uniform_values: Optional[tuple] = None
    _last_rendered: Optional[Text] = None
    task_result: Optional[str] = None
    task_reason: str = ""
    child_rollup: Optional[dict] = None
    swarm_rollup: Optional[dict] = None

    summary_detail: reactive[str] = reactive("low")  # low, med, full
    summary_content_mode: reactive[str] = reactive("ai_short")  # ai_short, ai_long, orders, annotation (#74)

    def __init__(self, session: Session, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The row as last drawn, so the 250 ms tick can skip rows that would
        # draw the same (refresh_if_changed, #486)
        self._last_rendered: Optional[Text] = None
        self.session = session
        # Initialize from session status (for terminated) or persisted state
        if session.status == "terminated":
            self.detected_status = "terminated"
            self.current_activity = "(tmux window no longer exists)"
        else:
            self.detected_status = session.stats.current_state if session.stats.current_state else "running"
            self.current_activity = "Initializing..."
        # AI-generated summaries (from daemon's SummarizerComponent)
        self.ai_summary_short: str = ""  # Short: current activity (~50 chars)
        self.ai_summary_context: str = ""  # Context: wider context (~80 chars)
        self.emoji_free: bool = False  # ASCII fallbacks for emoji (#315)
        self.show_cost: str = "tokens"  # "tokens", "cost", "joules" — cycle with $
        # Which fleet-gated columns show: the app's one FleetFlags (set by
        # the TUI, compute_fleet_flags); an equal value again is no change
        self.fleet: FleetFlags = FleetFlags()
        self.subtree_cost_usd: float = 0.0  # Subtree cost from daemon
        self.window_burn = None  # Per-session WindowBurnStats over timeline window (#174)
        # Columns every row shows the same value in — hidden (set by TUI)
        self.uniform_columns: dict = {}
        self.oversight_deadline: Optional[str] = None  # ISO deadline for this agent
        # A child's task outcome, a parent's children by state and its
        # delegated work (👥), as the engine (or a sister) publishes them
        # (task_result / children_rollup / swarm_rollup)
        self.task_result: Optional[str] = None
        self.task_reason: str = ""
        self.child_rollup: Optional[dict] = None
        self.swarm_rollup: Optional[dict] = None
        self.summarizer_enabled: bool = False  # Track if summarizer is enabled
        self.pane_content: List[str] = []  # Cached pane content
        self.claude_stats: Optional[AgentSessionStats] = None  # Token/interaction stats
        self.git_diff_stats: Optional[tuple] = None  # (files, insertions, deletions)
        self.git_untracked_count: Optional[int] = None  # Untracked file count (#455)
        self.background_bash_count: int = 0  # Live count from status bar (#177)
        self.bash_count_ambiguous: bool = False  # Count came from lone "(running)" (#259)
        self.in_session_mode: str = ""  # Status bar's in-session mode (#444)
        self.live_subagent_count: int = 0  # Live count from status bar
        self.file_subagent_count: int = 0  # Live count from file mtime (#256)
        self.pr_number: Optional[int] = session.pr_number  # Widget var — sticky, survives session replacement
        # Needs a look: an input-needed stretch began after the last visit
        # (from the engine's input_needed_since / visited_at)
        self.is_unvisited_stalled: bool = False
        # What the engine publishes for this agent (engine.sock agent view);
        # empty until the first snapshot names it. The row renders from it.
        self.engine: dict = {}
        # True while this TUI's own focused-pane capture supplies the
        # pane-derived counts (fresher than the engine's) for this row
        self.local_pane: bool = False
        # Time in state counts from here (the engine's episode; the
        # session's persisted state_since until the engine reports)
        self._status_changed_at: Optional[datetime] = None
        if session.stats.state_since:
            try:
                self._status_changed_at = from_iso(session.stats.state_since)
            except (ValueError, TypeError):
                pass
        self.last_command: str = ""  # Last instruction sent to this agent (#413)
        # This row's cell widths when the columns were last aligned (TUI)
        self._cell_widths: Optional[list] = None
        # session_with_view(session, engine), rebuilt when either changes
        self._overlay_key: Optional[tuple] = None
        self._overlay: Optional[Session] = None
        # Per-level column overrides for current detail level
        self.column_overrides: dict = {}
        # Agent hierarchy (#244)
        self.tree_depth: int = 0  # Set by TUI when sort mode is by_tree
        self.tree_prefix: str = ""  # e.g., "├─ " or "└─ " — set by TUI
        self.child_count: int = 0  # Number of direct children — set by TUI
        self.job_count: int = 0  # Running jobs it launched — set by TUI (#463)
        self.children_collapsed: bool = False  # True when children hidden via X — set by TUI
        self.swarm_expanded: bool = False  # delegated work shown as rows below (→) — set by TUI
        self.swarm_finished: str = "show"  # finished members shown? (swarm_view) — set by TUI
        # Always single-line display
        self.add_class("list-mode")
        self._hover_column: Optional[str] = None  # column whose popup is showing

    def __setattr__(self, name: str, value) -> None:
        """A row input assigned a new value: build the cells again on the
        next draw. The same value again (the 10 s session sync re-assigns
        every flag) is no change; whoever changes an object in place calls
        refresh(), as the actions do."""
        if name[0] == "_":
            if name not in _PRIVATE_ROW_INPUTS:
                super().__setattr__(name, value)
                return
        elif name in _NOT_ROW_INPUTS:
            super().__setattr__(name, value)
            return
        elif name in _VISIBILITY_INPUTS:
            # Rare, and the override dicts are updated in place and assigned
            # again: always work the columns out afresh
            super().__setattr__(name, value)
            d = self.__dict__
            d["_built"] = None
            d["_visible"] = None
            return
        try:
            old = getattr(self, name)
        except Exception:
            old = _UNSET
        super().__setattr__(name, value)
        if old is value:
            return
        if old is not _UNSET and type(old) is type(value):
            try:
                if old == value:
                    return
            except Exception:
                pass
        self.__dict__["_built"] = None

    @property
    def leaving(self) -> bool:
        """Removed, waiting for Textual to prune it: not a row any more."""
        return bool(self.__dict__.get("_leaving"))

    def leave(self):
        """Remove the row; it stops counting as one at once (``leaving``)."""
        self.__dict__["_leaving"] = True
        return self.remove()

    def refresh(self, *args, **kwargs):
        """Redraw. Whoever asks may have changed a session or the column
        overrides in place, so the row is worked out again (the row clock
        repaints through Static.refresh)."""
        d = self.__dict__
        d["_built"] = None
        d["_visible"] = None
        return super().refresh(*args, **kwargs)

    @property
    def status_detail(self):
        """The engine's 4-colour detail (badge countdowns as of now), or None."""
        return tui_engine.status_detail(self.engine, time.time()) if self.engine else None

    def visible_columns(self) -> tuple:
        """The columns this row shows, worked out again only when the detail
        level, the overrides or the uniform columns are assigned."""
        cols = self._visible
        if cols is None:
            cols = tuple(c for c in SUMMARY_COLUMNS
                         if resolve_column_visible(c, self.summary_detail, self.column_overrides,
                                                   self.uniform_columns))
            d = self.__dict__
            d["_visible"] = cols
            d["_visible_ids"] = frozenset(c.id for c in cols)
        return cols

    def column_visible(self, col: SummaryColumn) -> bool:
        """Check if a column is visible at the current detail level with overrides."""
        self.visible_columns()
        return col.id in self._visible_ids

    def column_under(self, event: events.MouseEvent) -> Optional[SummaryColumn]:
        """The column under the pointer, from the same widths the row is
        padded to (as the header row does, #477)."""
        widths = getattr(self.app, "column_widths", None)
        offset = event.get_content_offset(self)
        if not widths or offset is None:
            return None
        ids = [c.id for c in self.visible_columns()]
        return COLUMNS_BY_ID.get(column_at(offset.x, ids, widths, self._row_width()))

    def on_mouse_move(self, event: events.MouseMove) -> None:
        """Hovering an emoji cell pops up what its emoji stand for."""
        col = self.column_under(event)
        col_id = col.id if col is not None else None
        if col_id == self._hover_column:
            return
        self._hover_column = col_id
        self.tooltip = col.hover(self._build_column_context()) if col and col.hover else None

    def on_leave(self, event: events.Leave) -> None:
        self._hover_column = None
        self.tooltip = None

    def on_click(self) -> None:
        """Handle click — select this agent (as j/k would, so the tmux pane
        follows) and mark it visited if it was stalled."""
        if self.is_unvisited_stalled:
            self.post_message(self.StalledAgentVisited(self.session.id))
        self.post_message(self.Clicked(self.session.id))

    class Clicked(events.Message):
        """Message sent when the user clicks this agent's row"""
        def __init__(self, session_id: str):
            super().__init__()
            self.session_id = session_id

    def on_focus(self) -> None:
        """Handle focus event - mark stalled agent as visited and update selection"""
        if self.is_unvisited_stalled:
            self.post_message(self.StalledAgentVisited(self.session.id))
        # Notify app to update selection highlighting
        self.post_message(self.SessionSelected(self.session.id))

    class SessionSelected(events.Message):
        """Message sent when a session is selected/focused"""
        def __init__(self, session_id: str):
            super().__init__()
            self.session_id = session_id

    class StalledAgentVisited(events.Message):
        """Message sent when user visits a stalled agent (focus or click)"""
        def __init__(self, session_id: str):
            super().__init__()
            self.session_id = session_id

    def _status_patterns(self):
        """Pane chrome for this agent's backend."""
        from ..backends import session_backend_name
        from ..status_patterns import get_patterns
        return get_patterns(session_backend_name(self.session))

    def _effective_status(self, status: str) -> str:
        """Terminated supersedes asleep (#399); asleep supersedes the status (#68).

        ``session.is_asleep`` is the local toggle, so z shows at once.
        """
        if status == "terminated" or self.session.status == "terminated":
            return "terminated"
        if self.session.is_asleep:
            return "asleep"
        return status

    def apply_engine(self, view: dict, burn_hours: float = 0.0,
                     visited_here: Optional[float] = None) -> bool:
        """Take this agent's published view; True if the row may draw differently.

        Everything comes from ``view`` except the pane-derived counts while
        ``local_pane`` holds (the focused agent's own capture).
        """
        before = self._engine_inputs()
        self.engine = view or {}
        v = self.engine
        if v.get("current_status"):
            self.detected_status = self._effective_status(v["current_status"])
        if "current_activity" in v:
            self.current_activity = v.get("current_activity") or ""
        self.claude_stats = tui_engine.agent_stats(v)
        self.file_subagent_count = v.get("file_subagent_count") or 0
        diff = tui_engine.git_diff(v)
        if diff is not None:
            self.git_diff_stats = diff
        if v.get("git_untracked") is not None:
            self.git_untracked_count = v["git_untracked"]
        if not self.local_pane:
            for name in tui_engine.PANE_FIELDS:
                if name in v:
                    setattr(self, name, v[name])
        self.window_burn = tui_engine.window_burn(v, burn_hours)
        self.subtree_cost_usd = v.get("subtree_cost_usd") or 0.0
        self._apply_results(v)
        if v.get("last_command"):
            self.last_command = v["last_command"]
        if "pr_number" in v:
            self.pr_number = v["pr_number"]
        changed_at = tui_engine.status_changed_at(v)
        if changed_at is not None:
            self._status_changed_at = changed_at
        self.is_unvisited_stalled = (
            not self.session.is_asleep and tui_engine.is_unvisited(v, visited_here)
        )
        return self._engine_inputs() != before

    def _engine_inputs(self) -> tuple:
        return (self.engine, self.detected_status, self.current_activity,
                self.window_burn, self.is_unvisited_stalled,
                self.task_result, self.task_reason, self.child_rollup, self.swarm_rollup,
                tuple(getattr(self, n) for n in tui_engine.PANE_FIELDS))

    def _apply_results(self, view: dict) -> None:
        """The result (the ``l`` result mode), the 👶 and the 👥 rollups from a published view."""
        self.task_result = view.get("task_result") or None
        self.task_reason = view.get("task_reason") or ""
        rollup = view.get("children_rollup")
        self.child_rollup = rollup if isinstance(rollup, dict) else None
        swarm = view.get("swarm_rollup")
        self.swarm_rollup = swarm if isinstance(swarm, dict) else None

    def apply_pane_content(self, content: str) -> bool:
        """This agent's terminal text (the focused capture, or a sister's poll).

        Keeps the lines for the preview pane and extracts the pane-derived
        counts from them. Returns True if anything changed.
        """
        changed = False
        new_pane = content.rstrip().split('\n') if content else []
        if self.pane_content != new_pane:
            self.pane_content = new_pane
            changed = True
        extracted = extract_from_pane(content, self._status_patterns()) if content else None
        for name in tui_engine.PANE_FIELDS:
            value = getattr(extracted, name) if extracted is not None else type(getattr(self, name))()
            if getattr(self, name) != value:
                setattr(self, name, value)
                changed = True
        return changed

    def end_local_pane(self) -> None:
        """Focus moved on: the engine's pane-derived counts apply again."""
        if not self.local_pane:
            return
        self.local_pane = False
        for name in tui_engine.PANE_FIELDS:
            if name in self.engine:
                setattr(self, name, self.engine[name])

    def apply_remote(self, visited_here: Optional[float] = None) -> bool:
        """A sister's agent, from what its API returned (sisters stay on HTTP polling)."""
        s = self.session
        before = (self.detected_status, self.current_activity, self.claude_stats,
                  self.git_diff_stats, self.git_untracked_count, self.is_unvisited_stalled,
                  self.task_result, self.task_reason, self.child_rollup, self.swarm_rollup)
        stats = s.stats
        self.detected_status = self._effective_status(stats.current_state or "running")
        self.current_activity = stats.current_task or ""
        self.claude_stats = synthesize_remote_stats(s)
        if s.remote_git_diff:
            self.git_diff_stats = tuple(s.remote_git_diff)
        if s.remote_git_untracked is not None:
            self.git_untracked_count = s.remote_git_untracked
        # A 0.6 sister publishes the same attention fields as a local engine
        rds = s.remote_daemon_state or {}
        self.is_unvisited_stalled = not s.is_asleep and tui_engine.is_unvisited(rds, visited_here)
        self._apply_results(rds)
        # Time in state: the sister's published episode (as for a local row),
        # else the poll's state_since (an older sister)
        changed_at = tui_engine.status_changed_at(rds)
        if changed_at is None and stats.state_since:
            try:
                changed_at = from_iso(stats.state_since)
            except (ValueError, TypeError):
                changed_at = None
        if changed_at is not None:
            self._status_changed_at = changed_at
        pane_changed = self.apply_pane_content(s.pane_content or "")
        return pane_changed or before != (
            self.detected_status, self.current_activity, self.claude_stats,
            self.git_diff_stats, self.git_untracked_count, self.is_unvisited_stalled,
            self.task_result, self.task_reason, self.child_rollup, self.swarm_rollup)

    def rendered_session(self) -> Session:
        """The session as the row draws it: sessions.json with the engine's values."""
        key = (id(self.session), id(self.engine))
        if key != self._overlay_key:
            self._overlay_key = key
            self._overlay = tui_engine.session_with_view(self.session, self.engine)
        return self._overlay

    def fleet_row(self) -> FleetRow:
        """What the fleet flags read from this row."""
        return FleetRow(
            session=self.rendered_session(), status=self.detected_status,
            pr_number=self.pr_number, subtree_cost_usd=self.subtree_cost_usd,
            window_burn=self.window_burn,
            has_status_detail=bool(self.engine.get("status_detail")),
            has_swarm=self.swarm_rollup is not None,
            has_live_subagents=max(self.live_subagent_count, self.file_subagent_count) > 0,
            has_quota=isinstance(self.engine.get("quota"), dict),
        )

    def watch_summary_detail(self, summary_detail: str) -> None:
        """Called when summary_detail changes"""
        self.refresh()

    def watch_summary_content_mode(self, summary_content_mode: str) -> None:
        """Called when summary_content_mode changes (#74)"""
        self.refresh()

    def _build_column_context(self) -> ColumnContext:
        """Build the ColumnContext with all pre-computed data for column rendering."""
        s = self.rendered_session()

        uptime, green_time, non_green_time, sleep_time = self._clock_times(s)
        median_work = self.claude_stats.median_work_time if self.claude_stats else 0.0

        # Status styling
        from ..status_constants import effective_permission_mode, get_permissiveness_emoji
        ef = self.emoji_free
        is_highlighted = self.has_focus or "selected" in self.classes
        bg = " on #1a3a50" if is_highlighted else " on #0d2137"
        status_symbol, base_color = get_status_symbol(self.detected_status, emoji_free=ef)
        status_color = f"bold {base_color}{bg}"
        # The status bar's in-session mode (published by the engine) overlays
        # the launch mode (#444).
        perm_emoji = get_permissiveness_emoji(
            effective_permission_mode(s.permissiveness_mode, self.in_session_mode), ef)

        # Name width: grows to the widest name cell, capped by detail level
        name_cap = NAME_WIDTH_CAPS.get(self.summary_detail, NAME_WIDTH_CAPS["full"])
        raw_max = getattr(self.app, 'max_name_width', name_cap)
        name_width = max(NAME_WIDTH_MIN, min(raw_max, name_cap))

        # Fold indicator for parents with collapsed children
        fold_suffix = FOLD_SUFFIX if self.children_collapsed else ""

        # The 🌙 / "~" markers' cells come off the name
        marker = name_lead(s, ef)
        marker_cells = cell_len(marker)

        # Apply tree indentation when in tree sort mode (#244)
        if self.tree_prefix:
            tree_str = self.tree_prefix
            available = name_width - len(tree_str) - len(fold_suffix) - marker_cells
            display_name = tree_str + marker + s.name[:available] + fold_suffix
        else:
            available = name_width - len(fold_suffix) - marker_cells
            display_name = marker + s.name[:available] + fold_suffix
        display_name += " " * max(0, name_width - cell_len(display_name))

        # Compute sleep wake estimate (#289)
        sleep_wake_estimate = None
        if self.detected_status == "busy_sleeping" and self._status_changed_at:
            dur = extract_sleep_duration(s.stats.current_task or "")
            if dur:
                sleep_wake_estimate = self._status_changed_at + timedelta(seconds=dur)

        # The engine publishes the detail already bridged from the legacy
        # heartbeat statuses; a sister's agent gets the bridge here.
        if self.engine:
            effective_detail = self.status_detail
        else:
            from ..hook_status_detector import augment_with_legacy_heartbeat
            effective_detail = augment_with_legacy_heartbeat(None, self.detected_status)

        return ColumnContext(
            session=s,
            stats=s.stats,
            claude_stats=self.claude_stats,
            git_diff_stats=self.git_diff_stats,
            git_untracked_count=self.git_untracked_count,
            in_session_mode=self.in_session_mode,
            status_symbol=status_symbol,
            status_color=status_color,
            bg=bg,
            emoji_free=self.emoji_free,
            summary_detail=self.summary_detail,
            show_cost=self.show_cost,
            fleet=self.fleet,
            expand_icon="",
            is_list_mode=True,
            has_focus=self.has_focus,
            is_unvisited_stalled=self.is_unvisited_stalled,
            uptime=uptime,
            green_time=green_time,
            non_green_time=non_green_time,
            sleep_time=sleep_time,
            median_work=median_work,
            repo_name=s.repo_name or "n/a",
            branch=s.branch or "n/a",
            display_name=display_name,
            perm_emoji=perm_emoji,
            all_names_match_repos=getattr(self.app, 'all_names_match_repos', False),
            live_subagent_count=max(self.live_subagent_count, self.file_subagent_count),
            # #259: suppress the ambiguous lone-"(running)" bash count when the
            # file-based subagent count confirms a subagent is running — the
            # token almost certainly refers to that subagent, not a bash.
            background_bash_count=(
                0 if self.bash_count_ambiguous and self.file_subagent_count > 0
                else self.background_bash_count
            ),
            child_count=self.child_count,
            job_count=self.job_count,
            status=self.detected_status,
            child_rollup=self.child_rollup,
            swarm_rollup=swarm_view.shown_rollup(self.swarm_rollup, self.swarm_finished),
            swarm_expanded=self.swarm_expanded,
            subagent_cost_usd=self.engine.get("subagent_cost_usd") if self.engine else None,
            quota=self.engine.get("quota") if self.engine else None,
            status_changed_at=self._status_changed_at,
            max_name_width=name_width,
            max_repo_width=getattr(self.app, 'max_repo_width', 10),
            max_branch_width=getattr(self.app, 'max_branch_width', 10),
            oversight_deadline=self.oversight_deadline,
            sleep_wake_estimate=sleep_wake_estimate,
            status_detail=effective_detail,
            # Subtree cost
            subtree_cost_usd=self.subtree_cost_usd,
            # Burn rate (#174)
            window_burn=self.window_burn,
            # PR number (widget var, not session)
            pr_number=self.pr_number,
            # Model
            model=s.model or "",
            # Reasoning effort (#497)
            effort=getattr(s, 'effort', None) or "",
            # Sister integration (#245)
            source_host=s.source_host,
            is_remote=s.is_remote,
            has_sisters=getattr(self.app, 'has_sisters', False),
            local_hostname=getattr(self.app, 'local_hostname', ''),
        )

    @staticmethod
    def _clock_times(s: Session) -> tuple:
        """The context's values that move with the clock: uptime, and the
        running / stalled / asleep totals up to now."""
        green_time, non_green_time, sleep_time = get_current_state_times(
            s.stats, is_asleep=s.is_asleep
        )
        return calculate_uptime(s.start_time), green_time, non_green_time, sleep_time

    def _tick_context(self, ctx: ColumnContext) -> None:
        """Move ``ctx``'s clock values to now (the badge ETAs included)."""
        ctx.uptime, ctx.green_time, ctx.non_green_time, ctx.sleep_time = \
            self._clock_times(ctx.session)
        if self.engine:
            ctx.status_detail = self.status_detail

    def _render_content_area(self, content: Text, ctx: ColumnContext, term_width: int) -> None:
        """Render the collapsed content area after the | separator."""
        s = ctx.session
        content.append(" │ ", style=f"bold dim{ctx.bg}")
        remaining = max(20, term_width - cell_len(content.plain))

        text, style_cat = get_summary_content_text(
            mode=self.summary_content_mode,
            annotation=s.human_annotation,
            standing_instructions=s.standing_instructions,
            standing_orders_complete=s.standing_orders_complete,
            preset_name=s.standing_instructions_preset,
            ai_summary_short=self.ai_summary_short,
            ai_summary_context=self.ai_summary_context,
            heartbeat_enabled=s.heartbeat_enabled,
            heartbeat_paused=s.heartbeat_paused,
            heartbeat_frequency_seconds=s.heartbeat_frequency_seconds,
            heartbeat_instruction=s.heartbeat_instruction,
            summarizer_enabled=self.summarizer_enabled,
            remaining_width=remaining,
            last_command=self.last_command,
            scraped_recap=_scraped_recap_from_stats(s.stats),
            task_result=self.task_result,
            task_reason=self.task_reason,
            emoji_free=ctx.emoji_free,
        )

        # Map style categories to Rich styles
        style_map = {
            "bold": f"bold italic{ctx.bg}",
            "dim": f"dim italic{ctx.bg}",
            "bold_green": f"bold green{ctx.bg}",
            "bold_cyan": f"bold cyan{ctx.bg}",
            "bold_yellow": f"bold italic yellow{ctx.bg}",
            "bold_magenta": f"bold magenta{ctx.bg}",
            "bold_red": f"bold red{ctx.bg}",
        }
        content.append(text, style=style_map.get(style_cat, style_map["dim"]))

    # ── Drawing the row ────────────────────────────────────────────────

    def _cells_key(self) -> tuple:
        """What the cells read from outside the row's own attributes."""
        app = self.app
        return (self.has_focus, "selected" in self.classes,
                getattr(app, 'max_name_width', None), getattr(app, 'max_repo_width', 10),
                getattr(app, 'max_branch_width', 10), getattr(app, 'all_names_match_repos', False),
                getattr(app, 'has_sisters', False), getattr(app, 'local_hostname', ''))

    def _row_width(self) -> int:
        """The width the line fills: the row's content width, inside its
        padding (the app's, less the padding, before layout)."""
        width = self.content_size.width
        if not width:
            width = self.app.size.width
            if not width:
                import shutil
                width = shutil.get_terminal_size().columns
            width = max(0, width - 2)
        return width

    def _current_row(self, tick: bool = True) -> _BuiltRow:
        """The row's cells, up to date: built again if an input changed,
        otherwise only the ticking cells redrawn (when ``tick``)."""
        key = self._cells_key()
        row = self._built
        if row is None or row.cells_key != key:
            self.__dict__["_full_builds"] = self._full_builds + 1
            ctx = self._build_column_context()
            columns = self.visible_columns()
            row = _BuiltRow(key, ctx, columns)
            self.__dict__["_built"] = row
        elif tick and row.ticking:
            ctx = row.ctx
            self._tick_context(ctx)
            segments = row.segments
            for i in row.ticking:
                segs = cell_segments(row.columns[i], ctx)
                if segs != segments[i]:
                    segments[i] = segs
                    row.cells[i] = cell_text(segs)
                    row.text = None
        return row

    def row_cells(self) -> list:
        """This row's cells as drawn now (for aligning the columns)."""
        return self._current_row().cells

    def _row_text(self, tick: bool = True) -> Text:
        """The line from the built cells; padded again only if a cell, the
        column widths or the row's width moved."""
        row = self._current_row(tick)
        widths = getattr(self.app, 'column_widths', None)
        width = self._row_width()
        if row.text is None or row.widths != widths or row.width != width:
            row.text = self._compose_line(row.ctx, row.cells, widths, width)
            row.widths = list(widths) if widths else widths
            row.width = width
        return row.text

    def render(self) -> Text:
        """Render single-line session summary."""
        # A row the clock just found changed draws the line the clock built
        text = self._row_text(tick=not self._pending)
        self.__dict__["_pending"] = False
        self._last_rendered = text
        return text

    def refresh_if_changed(self) -> bool:
        """Refresh only if the row would now draw differently.

        The row clock calls this on every row 4 times a second. Only the
        ticking cells are redrawn (the rest are kept until an input
        changes), and Textual's strips and compositing run only for a row
        that changed. Comparing against what render() last returned means
        whatever changed the row (status, a ticking duration, focus,
        width) still shows on the same tick.
        """
        text = self._row_text()
        last = self._last_rendered
        if last is not None and (text is last or text == last):
            return False
        self.__dict__["_pending"] = True
        super().refresh()
        return True

    def _compose_line(self, ctx: ColumnContext, cells: list, column_widths, width: int) -> Text:
        """The line: cells padded to the aligned widths, the content area, fill."""
        pad_style = ctx.bg
        if column_widths:
            content = pad_and_join_cells(cells, column_widths, pad_style=pad_style)
            # The summary keeps its minimum: the grid's right end is cut first
            room = grid_room(column_widths, width)
            if room < cell_len(content.plain):
                content.truncate(room, overflow="crop")
        else:
            # Fallback: no alignment data yet (first render before batch update)
            content = Text()
            for cell in cells:
                content.append_text(cell)

        self._render_content_area(content, ctx, width)
        # Fill the row, and crop it at the edge: a line longer than the row
        # would wrap at a word boundary and lose the end that fitted
        used = cell_len(content.plain)
        if used < width:
            content.append(" " * (width - used), style=ctx.bg)
        elif used > width:
            content.truncate(width, overflow="crop")
        content.no_wrap = True
        return content

    def _render_row(self) -> Text:
        """The row rendered from scratch, bypassing every cache: what the
        cached line must always equal."""
        ctx = self._build_column_context()
        cells = [render_cell(c, ctx) for c in SUMMARY_COLUMNS
                 if resolve_column_visible(c, self.summary_detail, self.column_overrides,
                                           self.uniform_columns)]
        return self._compose_line(ctx, cells, getattr(self.app, 'column_widths', None),
                                  self._row_width())
