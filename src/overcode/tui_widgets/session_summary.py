"""
Session summary widget for TUI.

Displays expandable session summary with status, metrics, and pane content.
"""

from datetime import datetime
from typing import List, Optional

from textual.widgets import Static
from textual.reactive import reactive
from textual import events
from rich.text import Text

from ..session_manager import Session
from ..status_detector import StatusDetector
from ..history_reader import get_session_stats, ClaudeSessionStats
from ..tui_helpers import (
    format_duration,
    format_tokens,
    format_line_count,
    calculate_uptime,
    get_current_state_times,
    get_status_symbol,
    style_pane_line,
    get_git_diff_stats,
)


def format_standing_instructions(instructions: str, max_len: int = 95) -> str:
    """Format standing instructions for display.

    Shows "[DEFAULT]" if instructions match the configured default,
    otherwise shows the truncated instructions.
    """
    from ..config import get_default_standing_instructions

    if not instructions:
        return ""

    default = get_default_standing_instructions()
    if default and instructions.strip() == default.strip():
        return "[DEFAULT]"

    if len(instructions) > max_len:
        return instructions[:max_len - 3] + "..."
    return instructions


class SessionSummary(Static, can_focus=True):
    """Widget displaying expandable session summary"""

    expanded: reactive[bool] = reactive(True)  # Start expanded
    detail_lines: reactive[int] = reactive(5)  # Lines of output to show (5, 10, 20, 50)
    summary_detail: reactive[str] = reactive("low")  # low, med, full
    summary_content_mode: reactive[str] = reactive("ai_short")  # ai_short, ai_long, orders, annotation (#74)

    def __init__(self, session: Session, status_detector: StatusDetector, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session = session
        self.status_detector = status_detector
        # Initialize from persisted session state, not hardcoded "running"
        self.detected_status = session.stats.current_state if session.stats.current_state else "running"
        self.current_activity = "Initializing..."
        # AI-generated summaries (from daemon's SummarizerComponent)
        self.ai_summary_short: str = ""  # Short: current activity (~50 chars)
        self.ai_summary_context: str = ""  # Context: wider context (~80 chars)
        self.pane_content: List[str] = []  # Cached pane content
        self.claude_stats: Optional[ClaudeSessionStats] = None  # Token/interaction stats
        self.git_diff_stats: Optional[tuple] = None  # (files, insertions, deletions)
        # Track if this is a stalled agent that hasn't been visited yet
        self.is_unvisited_stalled: bool = False
        # Track when status last changed (for immediate time-in-state updates)
        self._status_changed_at: Optional[datetime] = None
        self._last_known_status: str = self.detected_status
        # Start with expanded class since expanded=True by default
        self.add_class("expanded")

    def on_click(self) -> None:
        """Toggle expanded state on click"""
        self.expanded = not self.expanded
        # Notify parent app to save state
        self.post_message(self.ExpandedChanged(self.session.id, self.expanded))
        # Mark as visited if this is an unvisited stalled agent
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

    class ExpandedChanged(events.Message):
        """Message sent when expanded state changes"""
        def __init__(self, session_id: str, expanded: bool):
            super().__init__()
            self.session_id = session_id
            self.expanded = expanded

    class StalledAgentVisited(events.Message):
        """Message sent when user visits a stalled agent (focus or click)"""
        def __init__(self, session_id: str):
            super().__init__()
            self.session_id = session_id

    def watch_expanded(self, expanded: bool) -> None:
        """Called when expanded state changes"""
        # Toggle CSS class for proper height
        if expanded:
            self.add_class("expanded")
        else:
            self.remove_class("expanded")
        self.refresh(layout=True)
        # Notify parent app to save state
        self.post_message(self.ExpandedChanged(self.session.id, expanded))

    def watch_detail_lines(self, detail_lines: int) -> None:
        """Called when detail_lines changes - force layout refresh"""
        self.refresh(layout=True)

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
        # Fetch git diff stats
        git_diff = None
        if self.session.start_directory:
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
            self.pane_content = lines[-50:] if lines else []  # Keep last 50 lines max
        else:
            self.pane_content = []

        # Update detected status for display
        # NOTE: Time tracking removed - Monitor Daemon is the single source of truth
        # The session.stats values are read from what Monitor Daemon has persisted
        # If session is asleep, keep the asleep status instead of the detected status
        new_status = "asleep" if self.session.is_asleep else status

        # Track status changes for immediate time-in-state reset (#73)
        if new_status != self._last_known_status:
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

    def render(self) -> Text:
        """Render session summary (compact or expanded)"""
        import shutil
        s = self.session
        stats = s.stats
        term_width = shutil.get_terminal_size().columns

        # Expansion indicator
        expand_icon = "▼" if self.expanded else "▶"

        # Calculate all values (only use what we need per level)
        uptime = calculate_uptime(self.session.start_time)
        repo_info = f"{s.repo_name or 'n/a'}:{s.branch or 'n/a'}"
        green_time, non_green_time = get_current_state_times(self.session.stats)

        # Get median work time from claude stats (or 0 if unavailable)
        median_work = self.claude_stats.median_work_time if self.claude_stats else 0.0

        # Status indicator - larger emoji circles based on detected status
        # Blue background matching Textual header/footer style
        bg = " on #0d2137"
        status_symbol, base_color = get_status_symbol(self.detected_status)
        status_color = f"bold {base_color}{bg}"

        # Permissiveness mode with emoji
        if s.permissiveness_mode == "bypass":
            perm_emoji = "🔥"  # Fire - burning through all permissions
        elif s.permissiveness_mode == "permissive":
            perm_emoji = "🏃"  # Running permissively
        else:
            perm_emoji = "👮"  # Normal mode with permissions

        content = Text()

        # Determine name width based on detail level (more space in lower detail modes)
        if self.summary_detail == "low":
            name_width = 24
        elif self.summary_detail == "med":
            name_width = 20
        else:  # full
            name_width = 16

        # Truncate name if needed
        display_name = s.name[:name_width].ljust(name_width)

        # Always show: status symbol, time in state, expand icon, agent name
        content.append(f"{status_symbol} ", style=status_color)

        # Show 🔔 indicator for unvisited stalled agents (needs attention)
        if self.is_unvisited_stalled:
            content.append("🔔", style=f"bold blink red{bg}")
        else:
            content.append("  ", style=f"dim{bg}")  # Maintain alignment

        # Time in current state (directly after status light)
        # Use locally tracked change time if more recent than daemon's state_since (#73)
        state_start = None
        if self._status_changed_at:
            state_start = self._status_changed_at
        if stats.state_since:
            try:
                daemon_state_start = datetime.fromisoformat(stats.state_since)
                # Use whichever is more recent (our local detection or daemon's record)
                if state_start is None or daemon_state_start > state_start:
                    state_start = daemon_state_start
            except (ValueError, TypeError):
                pass
        if state_start:
            elapsed = (datetime.now() - state_start).total_seconds()
            content.append(f"{format_duration(elapsed):>5} ", style=status_color)
        else:
            content.append("    - ", style=f"dim{bg}")

        # In list-mode, show focus indicator instead of expand icon
        if "list-mode" in self.classes:
            if self.has_focus:
                content.append("→ ", style=status_color)
            else:
                content.append("  ", style=status_color)
        else:
            content.append(f"{expand_icon} ", style=status_color)
        content.append(f"{display_name}", style=f"bold cyan{bg}")

        # Full detail: add repo:branch (padded to longest across all sessions)
        if self.summary_detail == "full":
            repo_width = getattr(self.app, 'max_repo_info_width', 18)
            content.append(f" {repo_info:<{repo_width}} ", style=f"bold dim{bg}")

        # Med/Full detail: add uptime, running time, stalled time
        if self.summary_detail in ("med", "full"):
            content.append(f" ↑{uptime:>5}", style=f"bold white{bg}")
            content.append(f" ▶{format_duration(green_time):>5}", style=f"bold green{bg}")
            content.append(f" ⏸{format_duration(non_green_time):>5}", style=f"bold red{bg}")
            # Full detail: show percentage active
            if self.summary_detail == "full":
                total_time = green_time + non_green_time
                pct = (green_time / total_time * 100) if total_time > 0 else 0
                content.append(f" {pct:>3.0f}%", style=f"bold green{bg}" if pct >= 50 else f"bold red{bg}")

        # Always show: token usage (from Claude Code)
        # ALIGNMENT: context indicator is always 7 chars " c@NNN%" (or placeholder)
        if self.claude_stats is not None:
            content.append(f" Σ{format_tokens(self.claude_stats.total_tokens):>6}", style=f"bold orange1{bg}")
            # Show current context window usage as percentage (assuming 200K max)
            if self.claude_stats.current_context_tokens > 0:
                max_context = 200_000  # Claude models have 200K context window
                ctx_pct = min(100, self.claude_stats.current_context_tokens / max_context * 100)
                content.append(f" c@{ctx_pct:>3.0f}%", style=f"bold orange1{bg}")
            else:
                content.append(" c@  -%", style=f"dim orange1{bg}")
        else:
            content.append("      - c@  -%", style=f"dim orange1{bg}")

        # Git diff stats (outstanding changes since last commit)
        # ALIGNMENT: Use fixed widths - low/med: 4 chars "Δnn ", full: 16 chars "Δnn +nnnn -nnnn"
        # Large line counts are shortened: 173242 -> "173K", 1234567 -> "1.2M"
        if self.git_diff_stats:
            files, ins, dels = self.git_diff_stats
            if self.summary_detail == "full":
                # Full: show files and lines with fixed widths
                content.append(f" Δ{files:>2}", style=f"bold magenta{bg}")
                content.append(f" +{format_line_count(ins):>4}", style=f"bold green{bg}")
                content.append(f" -{format_line_count(dels):>4}", style=f"bold red{bg}")
            else:
                # Compact: just files changed (fixed 4 char width)
                content.append(f" Δ{files:>2}", style=f"bold magenta{bg}" if files > 0 else f"dim{bg}")
        else:
            # Placeholder matching width for alignment
            if self.summary_detail == "full":
                content.append("  Δ-  +   -  -  ", style=f"dim{bg}")
            else:
                content.append("  Δ-", style=f"dim{bg}")

        # Med/Full detail: add median work time (p50 autonomous work duration)
        if self.summary_detail in ("med", "full"):
            work_str = format_duration(median_work) if median_work > 0 else "0s"
            content.append(f" ⏱{work_str:>5}", style=f"bold blue{bg}")

        # Always show: permission mode, human interactions, robot supervisions
        content.append(f" {perm_emoji}", style=f"bold white{bg}")
        # Human interaction count = total interactions - robot interventions
        if self.claude_stats is not None:
            human_count = max(0, self.claude_stats.interaction_count - stats.steers_count)
            content.append(f" 👤{human_count:>3}", style=f"bold yellow{bg}")
        else:
            content.append(" 👤  -", style=f"dim yellow{bg}")
        # Robot supervision count (from daemon steers) - 3 digit padding
        content.append(f" 🤖{stats.steers_count:>3}", style=f"bold cyan{bg}")

        # Standing orders indicator (after supervision count) - always show for alignment
        if s.standing_instructions:
            if s.standing_orders_complete:
                content.append(" ✓", style=f"bold green{bg}")
            elif s.standing_instructions_preset:
                # Show preset name (truncated to fit)
                preset_display = f" {s.standing_instructions_preset[:8]}"
                content.append(preset_display, style=f"bold cyan{bg}")
            else:
                content.append(" 📋", style=f"bold yellow{bg}")
        else:
            content.append(" ➖", style=f"bold dim{bg}")  # No instructions indicator

        # Agent value indicator (#61)
        # Full detail: show numeric value with money bag
        # Short/med: show priority chevrons (⏫ high, ⏹ normal, ⏬ low)
        if self.summary_detail == "full":
            content.append(f" 💰{s.agent_value:>4}", style=f"bold magenta{bg}")
        else:
            # Priority icon based on value relative to default 1000
            # Note: Rich measures ⏹️ as 2 cells but ⏫️/⏬️ as 3 cells, so we add
            # a trailing space to ⏹️ for alignment
            if s.agent_value > 1000:
                content.append(" ⏫️", style=f"bold red{bg}")  # High priority
            elif s.agent_value < 1000:
                content.append(" ⏬️", style=f"bold blue{bg}")  # Low priority
            else:
                content.append(" ⏹️ ", style=f"dim{bg}")  # Normal (extra space for alignment)

        if not self.expanded:
            # Compact view: show content based on summary_content_mode (#74)
            content.append(" │ ", style=f"bold dim{bg}")
            # Calculate remaining space for content
            current_len = len(content.plain)
            remaining = max(20, term_width - current_len - 2)

            # Determine what to show based on mode
            mode = self.summary_content_mode

            if mode == "annotation":
                # Show human annotation (✏️ icon)
                if s.human_annotation:
                    content.append(f"✏️ {s.human_annotation[:remaining-3]}", style=f"bold magenta{bg}")
                else:
                    content.append("✏️ (no annotation)", style=f"dim italic{bg}")
            elif mode == "orders":
                # Show standing orders (🎯 icon, ✓ if complete)
                if s.standing_instructions:
                    if s.standing_orders_complete:
                        style = f"bold green{bg}"
                        prefix = "🎯✓ "
                    elif s.standing_instructions_preset:
                        style = f"bold cyan{bg}"
                        prefix = f"🎯 {s.standing_instructions_preset}: "
                    else:
                        style = f"bold italic yellow{bg}"
                        prefix = "🎯 "
                    display_text = f"{prefix}{format_standing_instructions(s.standing_instructions, remaining - len(prefix))}"
                    content.append(display_text[:remaining], style=style)
                else:
                    content.append("🎯 (no standing orders)", style=f"dim italic{bg}")
            elif mode == "ai_long":
                # ai_long: show context summary (📖 icon - wider context/goal from AI)
                if self.ai_summary_context:
                    content.append(f"📖 {self.ai_summary_context[:remaining-3]}", style=f"bold italic{bg}")
                else:
                    content.append("📖 (awaiting context...)", style=f"dim italic{bg}")
            else:
                # ai_short: show short summary (💬 icon - current activity from AI)
                if self.ai_summary_short:
                    content.append(f"💬 {self.ai_summary_short[:remaining-3]}", style=f"bold italic{bg}")
                else:
                    content.append("💬 (awaiting summary...)", style=f"dim italic{bg}")

            # Pad to fill terminal width
            current_len = len(content.plain)
            if current_len < term_width:
                content.append(" " * (term_width - current_len), style=f"{bg}")
            return content

        # Pad header line to full width before adding expanded content
        current_len = len(content.plain)
        if current_len < term_width:
            content.append(" " * (term_width - current_len), style=f"{bg}")

        # Expanded view: show standing instructions first if set
        if s.standing_instructions:
            content.append("\n")
            content.append("  ")
            display_instr = format_standing_instructions(s.standing_instructions)
            if s.standing_orders_complete:
                content.append("│ ", style="bold green")
                content.append("✓ ", style="bold green")
                content.append(display_instr, style="green")
            elif s.standing_instructions_preset:
                content.append("│ ", style="cyan")
                content.append(f"{s.standing_instructions_preset}: ", style="bold cyan")
                content.append(display_instr, style="cyan")
            else:
                content.append("│ ", style="cyan")
                content.append("📋 ", style="yellow")
                content.append(display_instr, style="italic yellow")

        # Expanded view: show pane content based on detail_lines setting
        lines_to_show = self.detail_lines
        # Account for standing instructions line if present
        if s.standing_instructions:
            lines_to_show = max(1, lines_to_show - 1)

        # Get the last N lines of pane content
        pane_lines = self.pane_content[-lines_to_show:] if self.pane_content else []

        # Show pane output lines
        for line in pane_lines:
            content.append("\n")
            content.append("  ")  # Indent
            # Truncate long lines and style based on content
            display_line = line[:100] + "..." if len(line) > 100 else line
            prefix_style, content_style = style_pane_line(line)
            content.append("│ ", style=prefix_style)
            content.append(display_line, style=content_style)

        # If no pane content and no standing instructions shown above, show placeholder
        if not pane_lines and not s.standing_instructions:
            content.append("\n")
            content.append("  ")  # Indent
            content.append("│ ", style="cyan")
            content.append("(no output)", style="dim italic")

        return content
