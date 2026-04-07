"""
Session summary widget for TUI.

Displays a single-line session summary with status, metrics, and content area.
"""

from datetime import datetime, timedelta
from typing import List, Optional

from textual.widgets import Static
from textual.reactive import reactive
from textual import events
from rich.text import Text

from ..session_manager import Session
from ..protocols import StatusDetectorProtocol
from ..status_constants import get_status_color
from ..status_patterns import extract_from_pane, extract_sleep_duration
from ..history_reader import get_session_stats, ClaudeSessionStats
from ..tui_helpers import (
    calculate_uptime,
    get_current_state_times,
    get_status_symbol,
    get_git_diff_stats,
    get_summary_content_text,
)
from ..summary_columns import ColumnContext, SummaryColumn, SUMMARY_COLUMNS, render_summary_cells, resolve_column_visible, pad_and_join_cells


class SessionSummary(Static, can_focus=True):
    """Widget displaying single-line session summary"""

    summary_detail: reactive[str] = reactive("low")  # low, med, full
    summary_content_mode: reactive[str] = reactive("ai_short")  # ai_short, ai_long, orders, annotation (#74)

    def __init__(self, session: Session, status_detector: StatusDetectorProtocol, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session = session
        self.status_detector = status_detector
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
        self.any_has_oversight_timeout: bool = False  # True if any agent has oversight timeout
        self.any_is_sleeping: bool = False  # True if any agent is busy_sleeping (#289)
        self.any_has_model: bool = False  # True if any agent has a model set
        self.any_has_provider: bool = False  # True if any agent uses non-web provider
        self.oversight_deadline: Optional[str] = None  # ISO deadline for this agent
        self.summarizer_enabled: bool = False  # Track if summarizer is enabled
        self.pane_content: List[str] = []  # Cached pane content
        self.claude_stats: Optional[ClaudeSessionStats] = None  # Token/interaction stats
        self.git_diff_stats: Optional[tuple] = None  # (files, insertions, deletions)
        self.background_bash_count: int = 0  # Live count from status bar (#177)
        self.live_subagent_count: int = 0  # Live count from status bar
        self.file_subagent_count: int = 0  # Live count from file mtime (#256)
        self.pr_number: Optional[int] = session.pr_number  # Widget var — sticky, survives session replacement
        self.any_has_pr: bool = False  # App-level flag, set by TUI
        # Track if this is a stalled agent that hasn't been visited yet
        self.is_unvisited_stalled: bool = False
        # Track when status last changed (for immediate time-in-state updates)
        # Initialize from daemon's persisted state_since to survive TUI restarts (#132)
        self._status_changed_at: Optional[datetime] = None
        if session.stats.state_since:
            try:
                self._status_changed_at = datetime.fromisoformat(session.stats.state_since)
            except (ValueError, TypeError):
                pass
        self._last_known_status: str = self.detected_status
        self.last_command: str = ""  # Last instruction sent to this agent (#413)
        # Per-level column overrides for current detail level
        self.column_overrides: dict = {}
        # Agent hierarchy (#244)
        self.tree_depth: int = 0  # Set by TUI when sort mode is by_tree
        self.tree_prefix: str = ""  # e.g., "├─ " or "└─ " — set by TUI
        self.child_count: int = 0  # Number of direct children — set by TUI
        self.children_collapsed: bool = False  # True when children hidden via X — set by TUI
        # Always single-line display
        self.add_class("list-mode")

    def column_visible(self, col: SummaryColumn) -> bool:
        """Check if a column is visible at the current detail level with overrides."""
        return resolve_column_visible(col, self.summary_detail, self.column_overrides)

    def on_click(self) -> None:
        """Handle click — mark stalled agent as visited."""
        if self.is_unvisited_stalled:
            self.post_message(self.StalledAgentVisited(self.session.id))

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

    def update_status(self) -> None:
        """Update the detected status for this session.

        NOTE: This is now VIEW-ONLY. Time tracking is handled by the Monitor Daemon.
        We only detect status for display and capture pane content for the expanded view.
        """
        # detect_status returns (status, activity, pane_content) - reuse content to avoid
        # duplicate tmux subprocess calls (was 2 calls per widget, now just 1)
        new_status, self.current_activity, content = self.status_detector.detect_status(self.session)
        self.apply_status(new_status, self.current_activity, content)

    def apply_status(self, status: str, activity: str, content: str) -> None:
        """Apply pre-fetched status data to this widget.

        Used by parallel status updates to apply data fetched in background threads.
        Note: This still fetches claude_stats synchronously - used for single widget updates.
        """
        # Fetch claude stats (only for standalone update_status calls)
        claude_stats = get_session_stats(self.session)
        # Fetch git diff stats — remote agents already have this from the sister API
        git_diff = None
        if self.session.is_remote:
            git_diff = self.session.remote_git_diff
        elif self.session.start_directory:
            git_diff = get_git_diff_stats(self.session.start_directory)
        self.apply_status_no_refresh(status, activity, content, claude_stats, git_diff)
        self.refresh()

    def apply_status_no_refresh(self, status: str, activity: str, content: str, claude_stats: Optional[ClaudeSessionStats] = None, git_diff_stats: Optional[tuple] = None) -> None:
        """Apply pre-fetched status data without triggering refresh.

        Used for batched updates where the caller will refresh once at the end.
        All data including claude_stats should be pre-fetched in background thread.
        """
        self.current_activity = activity

        # Use pane content from detect_status (already fetched)
        if content:
            # Keep all lines including blanks for proper formatting, just strip trailing blanks
            lines = content.rstrip().split('\n')
            self.pane_content = lines if lines else []
            # Pure extraction — results stored as widget vars, never on session
            extracted = extract_from_pane(content)
            self.background_bash_count = extracted.background_bash_count
            self.live_subagent_count = extracted.live_subagent_count
        else:
            self.pane_content = []
            self.background_bash_count = 0
            self.live_subagent_count = 0

        # Update detected status for display
        # NOTE: Time tracking removed - Monitor Daemon is the single source of truth
        # The session.stats values are read from what Monitor Daemon has persisted
        # Terminated supersedes asleep (#399); asleep supersedes detected status (#68)
        if status == "terminated":
            new_status = status
        elif self.session.is_asleep:
            new_status = "asleep"
        else:
            new_status = status

        # Track status changes for immediate time-in-state reset (#73)
        # Compare by color group so sub-status changes within the same color
        # (e.g. running ↔ running_heartbeat) don't reset the timer.
        if get_status_color(new_status) != get_status_color(self._last_known_status):
            self._status_changed_at = datetime.now()
        self._last_known_status = new_status

        self.detected_status = new_status

        # Use pre-fetched claude stats (no file I/O on main thread)
        if claude_stats is not None:
            self.claude_stats = claude_stats

        # Use pre-fetched git diff stats
        if git_diff_stats is not None:
            self.git_diff_stats = git_diff_stats

    def watch_summary_detail(self, summary_detail: str) -> None:
        """Called when summary_detail changes"""
        self.refresh()

    def watch_summary_content_mode(self, summary_content_mode: str) -> None:
        """Called when summary_content_mode changes (#74)"""
        self.refresh()

    def _build_column_context(self) -> ColumnContext:
        """Build the ColumnContext with all pre-computed data for column rendering."""
        s = self.session

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
        perm_emoji = get_permissiveness_emoji(s.permissiveness_mode, ef)

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

        return ColumnContext(
            session=s,
            stats=s.stats,
            claude_stats=self.claude_stats,
            git_diff_stats=self.git_diff_stats,
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
            is_compact_mode="compact-mode" in self.classes,
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
            background_bash_count=self.background_bash_count,
            child_count=self.child_count,
            status_changed_at=self._status_changed_at,
            max_name_width=name_width,
            max_repo_width=getattr(self.app, 'max_repo_width', 10),
            max_branch_width=getattr(self.app, 'max_branch_width', 10),
            any_has_oversight_timeout=self.any_has_oversight_timeout,
            oversight_deadline=self.oversight_deadline,
            any_is_sleeping=self.any_is_sleeping,
            sleep_wake_estimate=sleep_wake_estimate,
            # Subtree cost
            subtree_cost_usd=self.subtree_cost_usd,
            any_has_subtree_cost=self.any_has_subtree_cost,
            # PR number (widget var, not session)
            pr_number=self.pr_number,
            any_has_pr=self.any_has_pr,
            # Model
            model=s.model or "",
            any_has_model=self.any_has_model,
            # Provider
            any_has_provider=self.any_has_provider,
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
