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
from rich.text import Text

from ..session_manager import Session
from ..status_patterns import extract_from_pane, extract_sleep_duration
from ..history_reader import AgentSessionStats, synthesize_remote_stats
from .. import tui_engine
from ..tui_helpers import (
    calculate_uptime,
    get_current_state_times,
    get_status_symbol,
    get_summary_content_text,
)
from ..summary_columns import (
    COLUMNS_BY_ID, ColumnContext, SummaryColumn, SUMMARY_COLUMNS, column_at,
    render_summary_cells, resolve_column_visible, pad_and_join_cells,
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


class SessionSummary(Static, can_focus=True):
    """Widget displaying single-line session summary"""

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
        self.monochrome: bool = False  # Legacy, kept for compatibility but no longer used for summaries
        self.emoji_free: bool = False  # ASCII fallbacks for emoji (#315)
        self.show_cost: str = "tokens"  # "tokens", "cost", "joules" — cycle with $
        self.any_has_budget: bool = False  # True if any agent has a cost budget (#173)
        self.subtree_cost_usd: float = 0.0  # Subtree cost from daemon
        self.any_has_subtree_cost: bool = False  # True if any parent has subtree cost
        self.window_burn = None  # Per-session WindowBurnStats over timeline window (#174)
        self.any_has_burn: bool = False  # True if any agent has non-zero burn data
        self.any_has_oversight_timeout: bool = False  # True if any agent has oversight timeout
        self.any_is_sleeping: bool = False  # True if any agent is busy_sleeping (#289)
        self.any_has_status_detail: bool = False  # True if any agent has a populated StatusDetail (#TBD)
        self.any_has_model: bool = False  # True if any agent has a model set
        self.any_has_effort: bool = False  # True if any agent has an effort detected (#497)
        # Columns every row shows the same value in — hidden (set by TUI)
        self.uniform_columns: dict = {}
        self.any_has_provider: bool = False  # True if any agent uses non-web provider
        self.mixed_backends: bool = False  # True if the fleet spans >1 agent CLI
        self.any_has_cpu: bool = False      # True if any agent has a non-zero CPU reading
        self.any_has_ram: bool = False      # True if any agent has a non-zero RAM reading
        self.oversight_deadline: Optional[str] = None  # ISO deadline for this agent
        self.summarizer_enabled: bool = False  # Track if summarizer is enabled
        self.pane_content: List[str] = []  # Cached pane content
        self.claude_stats: Optional[AgentSessionStats] = None  # Token/interaction stats
        self.git_diff_stats: Optional[tuple] = None  # (files, insertions, deletions)
        self.git_untracked_count: Optional[int] = None  # Untracked file count (#455)
        self.background_bash_count: int = 0  # Live count from status bar (#177)
        self.bash_count_ambiguous: bool = False  # Count came from lone "(running)" (#259)
        self.auto_accept_mode: bool = False  # In-session auto-accept-edits (#444)
        self.live_subagent_count: int = 0  # Live count from status bar
        self.file_subagent_count: int = 0  # Live count from file mtime (#256)
        self.pr_number: Optional[int] = session.pr_number  # Widget var — sticky, survives session replacement
        self.any_has_pr: bool = False  # App-level flag, set by TUI
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
                self._status_changed_at = datetime.fromisoformat(session.stats.state_since)
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
        # Always single-line display
        self.add_class("list-mode")
        self._hover_column: Optional[str] = None  # column whose popup is showing

    @property
    def status_detail(self):
        """The engine's 4-colour detail (badge countdowns as of now), or None."""
        return tui_engine.status_detail(self.engine, time.time()) if self.engine else None

    def column_visible(self, col: SummaryColumn) -> bool:
        """Check if a column is visible at the current detail level with overrides."""
        return resolve_column_visible(col, self.summary_detail, self.column_overrides,
                                      self.uniform_columns)

    def column_under(self, event: events.MouseEvent) -> Optional[SummaryColumn]:
        """The column under the pointer, from the same widths the row is
        padded to (as the header row does, #477)."""
        widths = getattr(self.app, "column_widths", None)
        offset = event.get_content_offset(self)
        if not widths or offset is None:
            return None
        ids = [c.id for c in SUMMARY_COLUMNS if self.column_visible(c)]
        return COLUMNS_BY_ID.get(column_at(offset.x, ids, widths))

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
                tuple(getattr(self, n) for n in tui_engine.PANE_FIELDS))

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
                  self.git_diff_stats, self.git_untracked_count, self.is_unvisited_stalled)
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
        pane_changed = self.apply_pane_content(s.pane_content or "")
        return pane_changed or before != (
            self.detected_status, self.current_activity, self.claude_stats,
            self.git_diff_stats, self.git_untracked_count, self.is_unvisited_stalled)

    def rendered_session(self) -> Session:
        """The session as the row draws it: sessions.json with the engine's values."""
        key = (id(self.session), id(self.engine))
        if key != self._overlay_key:
            self._overlay_key = key
            self._overlay = tui_engine.session_with_view(self.session, self.engine)
        return self._overlay

    def watch_summary_detail(self, summary_detail: str) -> None:
        """Called when summary_detail changes"""
        self.refresh()

    def watch_summary_content_mode(self, summary_content_mode: str) -> None:
        """Called when summary_content_mode changes (#74)"""
        self.refresh()

    def _build_column_context(self) -> ColumnContext:
        """Build the ColumnContext with all pre-computed data for column rendering."""
        s = self.rendered_session()

        # Pre-compute times
        uptime = calculate_uptime(s.start_time)
        green_time, non_green_time, sleep_time = get_current_state_times(
            s.stats, is_asleep=s.is_asleep
        )
        median_work = self.claude_stats.median_work_time if self.claude_stats else 0.0

        # Status styling
        from ..status_constants import get_permissiveness_emoji
        ef = self.emoji_free
        is_highlighted = self.has_focus or "selected" in self.classes
        bg = " on #1a3a50" if is_highlighted else " on #0d2137"
        status_symbol, base_color = get_status_symbol(self.detected_status, emoji_free=ef)
        status_color = f"bold {base_color}{bg}"
        # Auto-accept-edits is a runtime-only mode that overlays "normal" launch
        # mode (#444). Bypass / permissive launches override it visually because
        # they're more permissive.
        effective_mode = s.permissiveness_mode
        if self.auto_accept_mode and effective_mode == "normal":
            effective_mode = "auto"
        perm_emoji = get_permissiveness_emoji(effective_mode, ef)

        # Name width: grows to longest agent name, capped by detail level
        if self.summary_detail == "low":
            name_cap = 34
        elif self.summary_detail == "med":
            name_cap = 30
        else:
            name_cap = 26
        raw_max = getattr(self.app, 'max_name_width', name_cap)
        name_width = max(8, min(raw_max, name_cap))

        # Fold indicator for parents with collapsed children
        fold_suffix = " ▶" if self.children_collapsed else ""

        # Apply tree indentation when in tree sort mode (#244)
        if self.tree_prefix:
            tree_str = self.tree_prefix
            available = name_width - len(tree_str) - len(fold_suffix)
            display_name = (tree_str + s.name[:available] + fold_suffix).ljust(name_width)
        else:
            available = name_width - len(fold_suffix)
            display_name = (s.name[:available] + fold_suffix).ljust(name_width)

        # Compute sleep wake estimate (#289)
        sleep_wake_estimate = None
        if self.detected_status == "busy_sleeping" and self._status_changed_at:
            dur = extract_sleep_duration(s.stats.current_task or "")
            if dur:
                sleep_wake_estimate = self._status_changed_at + timedelta(seconds=dur)

        # The engine publishes the detail already bridged from the legacy
        # heartbeat statuses; a sister's agent gets the bridge here.
        if self.engine:
            effective_detail = tui_engine.status_detail(self.engine, time.time())
        else:
            from ..hook_status_detector import augment_with_legacy_heartbeat
            effective_detail = augment_with_legacy_heartbeat(None, self.detected_status)

        return ColumnContext(
            session=s,
            stats=s.stats,
            claude_stats=self.claude_stats,
            git_diff_stats=self.git_diff_stats,
            git_untracked_count=self.git_untracked_count,
            auto_accept_mode=self.auto_accept_mode,
            status_symbol=status_symbol,
            status_color=status_color,
            bg=bg,
            monochrome=self.monochrome,
            emoji_free=self.emoji_free,
            summary_detail=self.summary_detail,
            show_cost=self.show_cost,
            any_has_budget=self.any_has_budget,
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
            status_changed_at=self._status_changed_at,
            max_name_width=name_width,
            max_repo_width=getattr(self.app, 'max_repo_width', 10),
            max_branch_width=getattr(self.app, 'max_branch_width', 10),
            any_has_oversight_timeout=self.any_has_oversight_timeout,
            oversight_deadline=self.oversight_deadline,
            any_is_sleeping=self.any_is_sleeping,
            sleep_wake_estimate=sleep_wake_estimate,
            status_detail=effective_detail,
            any_has_status_detail=self.any_has_status_detail,
            # Subtree cost
            subtree_cost_usd=self.subtree_cost_usd,
            any_has_subtree_cost=self.any_has_subtree_cost,
            # Burn rate (#174)
            window_burn=self.window_burn,
            any_has_burn=self.any_has_burn,
            # PR number (widget var, not session)
            pr_number=self.pr_number,
            any_has_pr=self.any_has_pr,
            # Model
            model=s.model or "",
            any_has_model=self.any_has_model,
            # Reasoning effort (#497)
            effort=getattr(s, 'effort', None) or "",
            any_has_effort=self.any_has_effort,
            # Provider
            any_has_provider=self.any_has_provider,
            # Agent CLI backend
            mixed_backends=self.mixed_backends,
            # Resource usage (CPU / RAM)
            any_has_cpu=self.any_has_cpu,
            any_has_ram=self.any_has_ram,
            # Sister integration (#245)
            source_host=s.source_host,
            is_remote=s.is_remote,
            has_sisters=getattr(self.app, 'has_sisters', False),
            local_hostname=getattr(self.app, 'local_hostname', ''),
        )

    def _render_content_area(self, content: Text, ctx: ColumnContext, term_width: int) -> None:
        """Render the collapsed content area after the | separator."""
        s = ctx.session
        content.append(" │ ", style=ctx.mono(f"bold dim{ctx.bg}", "dim"))
        current_len = len(content.plain)
        remaining = max(20, term_width - current_len - 2)

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
        )

        # Map style categories to Rich styles
        style_map = {
            "bold": ctx.mono(f"bold italic{ctx.bg}", "bold"),
            "dim": ctx.mono(f"dim italic{ctx.bg}", "dim"),
            "bold_green": ctx.mono(f"bold green{ctx.bg}", "bold"),
            "bold_cyan": ctx.mono(f"bold cyan{ctx.bg}", "bold"),
            "bold_yellow": ctx.mono(f"bold italic yellow{ctx.bg}", "bold"),
            "bold_magenta": ctx.mono(f"bold magenta{ctx.bg}", "bold"),
        }
        content.append(text, style=style_map.get(style_cat, style_map["dim"]))

    def render(self) -> Text:
        """Render single-line session summary."""
        text = self._render_row()
        self._last_rendered = text
        return text

    def refresh_if_changed(self) -> bool:
        """Refresh only if the row would now draw differently.

        Building the row's Text is ~40% of a refresh; Textual turning it into
        strips and compositing is the rest, so an unchanged row (most of them,
        most ticks) skips that. Comparing against what render() last returned
        means whatever changed the row — status, a ticking duration, focus,
        width — still shows on the same tick.
        """
        if self._last_rendered is not None and self._render_row() == self._last_rendered:
            return False
        self.refresh()
        return True

    def _render_row(self) -> Text:
        import shutil
        term_width = shutil.get_terminal_size().columns
        ctx = self._build_column_context()

        # Render columns via shared canonical loop with auto-alignment
        cells = render_summary_cells(ctx, column_filter=self.column_visible)
        column_widths = getattr(self.app, 'column_widths', None)
        pad_style = ctx.mono(f"{ctx.bg}", "") if ctx.bg else ""
        if column_widths:
            content = pad_and_join_cells(cells, column_widths, pad_style=pad_style)
        else:
            # Fallback: no alignment data yet (first render before batch update)
            from rich.text import Text
            content = Text()
            for cell in cells:
                content.append_text(cell)

        self._render_content_area(content, ctx, term_width)
        # Pad to fill terminal width
        current_len = len(content.plain)
        if current_len < term_width:
            content.append(" " * (term_width - current_len), style=ctx.mono(f"{ctx.bg}", ""))
        return content
