"""
Textual TUI for Overcode monitor.

TODO: Split this file into smaller modules for maintainability:
- tui_core.py: Main App class and core lifecycle
- tui_panels.py: Panel widgets (StatusPanel, AgentPanel, etc.)
- tui_commands.py: Command handlers and actions
- tui_keybindings.py: Key bindings and input handling
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import List, Optional
import subprocess
import sys
import time

from textual.app import App, ComposeResult
from textual.containers import Container, Vertical, ScrollableContainer, Horizontal
from textual.widgets import Header, Footer, Static, Label, Input, TextArea
from textual.reactive import reactive
from textual.css.query import NoMatches
from textual import events, work
from textual.message import Message
from rich.text import Text
from rich.panel import Panel

from . import __version__
from .session_manager import SessionManager, Session
from .launcher import ClaudeLauncher
from .status_detector_factory import StatusDetectorDispatcher
from .status_constants import STATUS_RUNNING, STATUS_RUNNING_HEARTBEAT, STATUS_WAITING_HEARTBEAT, STATUS_WAITING_USER
from .history_reader import get_session_stats, ClaudeSessionStats
from .settings import signal_activity, get_session_dir, get_agent_history_path, TUIPreferences, DAEMON_VERSION  # Activity signaling to daemon
from .monitor_daemon_state import MonitorDaemonState, get_monitor_daemon_state
from .monitor_daemon import (
    is_monitor_daemon_running,
    stop_monitor_daemon,
)
from .pid_utils import count_daemon_processes
from .supervisor_daemon import (
    is_supervisor_daemon_running,
    stop_supervisor_daemon,
)
from .summarizer_component import (
    SummarizerComponent,
    SummarizerConfig,
    AgentSummary,
)
from .summarizer_client import SummarizerClient
from .web_server import (
    is_web_server_running,
    get_web_server_url,
    toggle_web_server,
)
from .config import get_default_standing_instructions
from .status_history import read_agent_status_history
from .presence_logger import read_presence_history, MACOS_APIS_AVAILABLE
from .launcher import ClaudeLauncher
from .implementations import RealTmux
from .tui_helpers import (
    format_interval,
    format_ago,
    format_duration,
    format_tokens,
    format_line_count,
    calculate_uptime,
    presence_state_to_char,
    agent_status_to_char,
    get_current_state_times,
    build_timeline_slots,
    build_timeline_string,
    get_status_symbol,
    get_presence_color,
    get_agent_timeline_color,
    style_pane_line,
    truncate_name,
    get_daemon_status_style,
    get_git_diff_stats,
    calculate_safe_break_duration,
)
from .tui_logic import (
    sort_sessions,
    filter_visible_sessions,
    get_sort_mode_display_name,
    cycle_sort_mode,
    calculate_green_percentage,
    calculate_human_interaction_count,
)
from .tui_widgets import (
    FullscreenPreview,
    HelpOverlay,
    PreviewPane,
    DaemonPanel,
    DaemonStatusBar,
    StatusTimeline,
    SessionSummary,
    CommandBar,
    SummaryConfigModal,
)
from .tui_actions import (
    NavigationActionsMixin,
    ViewActionsMixin,
    DaemonActionsMixin,
    SessionActionsMixin,
    InputActionsMixin,
)


class SupervisorTUI(
    NavigationActionsMixin,
    ViewActionsMixin,
    DaemonActionsMixin,
    SessionActionsMixin,
    InputActionsMixin,
    App,
):
    """Overcode Supervisor TUI"""

    # Disable any size restrictions
    AUTO_FOCUS = None

    # Load CSS from external file
    CSS_PATH = "tui.tcss"


    BINDINGS = [
        ("q", "quit", "Quit"),
        ("h", "toggle_help", "Help"),
        ("question_mark", "toggle_help", "Help"),
        ("d", "toggle_daemon", "Daemon panel"),
        ("t", "toggle_timeline", "Toggle timeline"),
        ("v", "cycle_detail", "Cycle detail"),
        ("s", "cycle_summary", "Summary detail"),
        ("e", "toggle_expand_all", "Expand/Collapse"),
        ("c", "sync_to_main_and_clear", "Sync main+clear"),
        ("space", "toggle_focused", "Toggle"),
        # Navigation between agents
        ("j", "focus_next_session", "Next"),
        ("k", "focus_previous_session", "Prev"),
        ("down", "focus_next_session", "Next"),
        ("up", "focus_previous_session", "Prev"),
        # View mode toggle
        ("m", "toggle_view_mode", "Mode"),
        # Fullscreen preview (expand preview pane)
        ("f", "expand_preview", "Fullscreen"),
        # Command bar (send instructions to agents)
        ("i", "focus_command_bar", "Send"),
        ("colon", "focus_command_bar", "Send"),
        ("o", "focus_standing_orders", "Standing orders"),
        # Daemon controls (simple keys that work everywhere)
        ("left_square_bracket", "supervisor_start", "Start supervisor"),
        ("right_square_bracket", "supervisor_stop", "Stop supervisor"),
        ("backslash", "monitor_restart", "Restart monitor"),
        ("a", "toggle_summarizer", "AI summarizer"),
        # Manual refresh (useful in diagnostics mode)
        ("r", "manual_refresh", "Refresh"),
        # Agent management
        ("x", "kill_focused", "Kill agent"),
        ("R", "restart_focused", "Restart agent"),
        ("n", "new_agent", "New agent"),
        # Send Enter to focused agent (for approvals)
        ("enter", "send_enter_to_focused", "Send Enter"),
        # Send Escape to focused agent (for interrupting)
        ("escape", "send_escape_to_focused", "Send Escape"),
        # Send number keys 1-5 to focused agent (for numbered prompts)
        ("1", "send_1_to_focused", "Send 1"),
        ("2", "send_2_to_focused", "Send 2"),
        ("3", "send_3_to_focused", "Send 3"),
        ("4", "send_4_to_focused", "Send 4"),
        ("5", "send_5_to_focused", "Send 5"),
        # Copy mode - disable mouse capture for native terminal selection
        ("y", "toggle_copy_mode", "Copy mode"),
        # Tmux sync - sync navigation to external tmux pane
        ("p", "toggle_tmux_sync", "Pane sync"),
        # Web server toggle
        ("w", "toggle_web_server", "Web dashboard"),
        # Sleep mode toggle - mark agent as paused (excluded from stats)
        ("z", "toggle_sleep", "Sleep mode"),
        # Show terminated/killed sessions (ghost mode)
        ("g", "toggle_show_terminated", "Show killed"),
        # Jump to sessions needing attention (bell/red)
        ("b", "jump_to_attention", "Jump attention"),
        # Hide sleeping agents from display
        ("Z", "toggle_hide_asleep", "Hide sleeping"),
        # Show/hide done child agents (#244)
        ("D", "toggle_show_done", "Show done"),
        # Collapse/expand children in tree view (#244)
        ("X", "toggle_collapse_children", "Collapse children"),
        # Sort mode cycle (#61)
        ("S", "cycle_sort_mode", "Sort mode"),
        # Edit agent value (#61)
        ("V", "edit_agent_value", "Edit value"),
        # Cost budget (#173)
        ("B", "edit_cost_budget", "Cost budget"),
        # Cycle summary content mode (#74)
        ("l", "cycle_summary_content", "Summary content"),
        # Edit human annotation (#74)
        ("I", "focus_human_annotation", "Annotation"),
        # Baseline time adjustment for mean spin calculation
        ("comma", "baseline_back", "Baseline -15m"),
        ("full_stop", "baseline_forward", "Baseline +15m"),
        ("0", "baseline_reset", "Reset baseline"),
        # Timeline scope cycle (#191)
        ("less_than_sign", "cycle_timeline_hours", "Timeline scope"),
        # Monochrome mode for terminals with ANSI issues (#138)
        ("M", "toggle_monochrome", "Monochrome"),
        # Toggle between token count and dollar cost display
        ("dollar_sign", "toggle_cost_display", "Show $"),
        # Transport/handover - prepare all sessions for handoff (double-press)
        ("T", "transport_all", "Handover all"),
        # Heartbeat configuration (#171)
        ("H", "configure_heartbeat", "Heartbeat config"),
        # Time context toggle - per-agent time awareness hook
        ("F", "toggle_time_context", "Time context"),
        # Hook-based status detection toggle (#5)
        ("K", "toggle_hook_detection", "Hook detection"),
        # Column configuration modal (#178)
        ("C", "open_column_config", "Columns"),
    ]

    # Detail level cycles through 5, 10, 20, 50 lines
    DETAIL_LEVELS = [5, 10, 20, 50]
    # Timeline scope presets in hours (#191)
    TIMELINE_PRESETS = [1, 3, 6, 12, 24]
    # Summary detail levels: low (minimal), med (timing), full (all + repo), custom (user-configured)
    SUMMARY_LEVELS = ["low", "med", "full", "custom"]
    # Sort modes (#61)
    SORT_MODES = ["alphabetical", "by_status", "by_value", "by_tree"]
    # Summary content modes: what to show in the summary line (#74)
    SUMMARY_CONTENT_MODES = ["ai_short", "ai_long", "orders", "annotation", "heartbeat"]

    sessions: reactive[List[Session]] = reactive(list)
    view_mode: reactive[str] = reactive("tree")  # "tree" or "list_preview"
    tmux_sync: reactive[bool] = reactive(False)  # sync navigation to external tmux pane
    show_terminated: reactive[bool] = reactive(False)  # show killed sessions in timeline
    hide_asleep: reactive[bool] = reactive(False)  # hide sleeping agents from display
    show_done: reactive[bool] = reactive(False)  # show "done" child agents (#244)
    summary_content_mode: reactive[str] = reactive("ai_short")  # what to show in summary (#74)
    baseline_minutes: reactive[int] = reactive(0)  # 0=now, 15/30/.../180 = minutes back for mean spin
    monochrome: reactive[bool] = reactive(False)  # B&W mode for terminals with ANSI issues (#138)
    show_cost: reactive[bool] = reactive(False)  # Show $ cost instead of token counts

    def __init__(self, tmux_session: str = "agents", diagnostics: bool = False):
        super().__init__()
        self.tmux_session = tmux_session
        self.diagnostics = diagnostics  # Disable all auto-refresh timers
        self.session_manager = SessionManager()
        self.launcher = ClaudeLauncher(tmux_session)
        self.detector = StatusDetectorDispatcher(tmux_session)
        # Track expanded state per session ID to preserve across refreshes
        self.expanded_states: dict[str, bool] = {}
        # Track collapsed parents in tree view (#244)
        self.collapsed_parents: set[str] = set()
        # Max repo/branch widths for alignment in full detail mode
        self.max_repo_width: int = 10
        self.max_branch_width: int = 10
        self.all_names_match_repos: bool = False

        # Load persisted TUI preferences
        self._prefs = TUIPreferences.load(tmux_session)

        # Current detail level index (cycles through DETAIL_LEVELS)
        # Initialize from saved preferences
        try:
            self.detail_level_index = self.DETAIL_LEVELS.index(self._prefs.detail_lines)
        except ValueError:
            self.detail_level_index = 0  # Default to 5 lines

        # Current summary detail level index (cycles through SUMMARY_LEVELS)
        # Initialize from saved preferences
        try:
            self.summary_level_index = self.SUMMARY_LEVELS.index(self._prefs.summary_detail)
        except ValueError:
            self.summary_level_index = 0  # Default to "low"

        # Track focused session for navigation
        self.focused_session_index = 0
        # Track previous status of each session for detecting transitions to stalled state
        self._previous_statuses: dict[str, str] = {}
        # Session cache to avoid disk I/O on every status update (250ms interval)
        self._sessions_cache: dict[str, Session] = {}
        self._sessions_cache_time: float = 0
        self._sessions_cache_ttl: float = 1.0  # 1 second TTL
        # Flags to prevent overlapping async updates (fast and slow paths are independent)
        self._status_update_in_progress = False
        self._stats_update_in_progress = False
        # Track if we've warned about multiple daemons (to avoid spam)
        self._multiple_daemon_warning_shown = False
        # Track whether sessions have been loaded at least once (for startup sequencing)
        self._initial_sessions_loaded = False
        # Track attention jump state (for 'b' key cycling)
        self._attention_jump_index = 0
        self._attention_jump_list: list = []  # Cached list of sessions needing attention
        # Pending double-press confirmations: action_key -> (session_name | None, timestamp)
        self._pending_confirmations: dict[str, tuple[str | None, float]] = {}
        # Tmux interface for sync operations
        self._tmux = RealTmux()
        # Initialize tmux_sync from preferences
        self.tmux_sync = self._prefs.tmux_sync
        # Initialize show_terminated from preferences
        self.show_terminated = self._prefs.show_terminated
        # Initialize hide_asleep from preferences
        self.hide_asleep = self._prefs.hide_asleep
        # Initialize show_done from preferences (#244)
        self.show_done = self._prefs.show_done
        # Initialize summary_content_mode from preferences (#98)
        self.summary_content_mode = self._prefs.summary_content_mode
        # Initialize baseline_minutes from preferences (for mean spin calculation)
        self.baseline_minutes = self._prefs.baseline_minutes
        # Initialize monochrome from preferences (#138)
        self.monochrome = self._prefs.monochrome
        # Initialize show_cost from preferences
        self.show_cost = self._prefs.show_cost
        # Cache of terminated sessions (killed during this TUI session)
        self._terminated_sessions: dict[str, Session] = {}

        # AI Summarizer - owned by TUI, not daemon (zero cost when TUI closed)
        self._summarizer = SummarizerComponent(
            tmux_session=tmux_session,
            config=SummarizerConfig(enabled=False),  # Disabled by default
        )
        self._summaries: dict[str, AgentSummary] = {}

        # Pre-load session list synchronously so first render has data immediately
        try:
            self._preloaded_sessions: list | None = self.launcher.list_sessions()
        except Exception:
            self._preloaded_sessions = None

    def compose(self) -> ComposeResult:
        """Create child widgets"""
        yield Header(show_clock=True)
        yield DaemonStatusBar(tmux_session=self.tmux_session, session_manager=self.session_manager, id="daemon-status")
        yield StatusTimeline([], tmux_session=self.tmux_session, id="timeline")
        yield DaemonPanel(tmux_session=self.tmux_session, id="daemon-panel")
        yield ScrollableContainer(id="sessions-container")
        yield PreviewPane(id="preview-pane")
        yield CommandBar(id="command-bar")
        # Modal for column configuration (positioned programmatically)
        yield SummaryConfigModal(self._prefs.summary_groups, id="summary-config-modal")
        yield FullscreenPreview(id="fullscreen-preview")
        yield HelpOverlay(id="help-overlay")
        yield Static(
            "h:Help | q:Quit | j/k:Nav | i:Send | n:New | x:Kill | space | m:Mode | p:Sync | d:Daemon | t:Timeline | g:Killed",
            id="help-text"
        )

    def on_mount(self) -> None:
        """Called when app starts"""
        self.title = f"Overcode v{__version__}"
        self._update_subtitle()

        # Auto-start Monitor Daemon if not running
        self._ensure_monitor_daemon()

        # Disable command bar inputs to prevent auto-focus capture
        try:
            cmd_bar = self.query_one("#command-bar", CommandBar)
            cmd_bar.query_one("#cmd-input", Input).disabled = True
            cmd_bar.query_one("#cmd-textarea", TextArea).disabled = True
            # Clear any focus from the command bar
            self.set_focus(None)
        except NoMatches:
            pass

        # Apply persisted preferences
        try:
            timeline = self.query_one("#timeline", StatusTimeline)
            timeline.display = self._prefs.timeline_visible
            timeline.timeline_hours = self._prefs.timeline_hours
        except NoMatches:
            pass

        try:
            daemon_panel = self.query_one("#daemon-panel", DaemonPanel)
            daemon_panel.display = self._prefs.daemon_panel_visible
        except NoMatches:
            pass

        # Apply show_cost preference to daemon status bar
        try:
            status_bar = self.query_one("#daemon-status", DaemonStatusBar)
            status_bar.show_cost = self._prefs.show_cost
        except NoMatches:
            pass

        # Apply monochrome preference to preview pane (#138)
        try:
            preview = self.query_one("#preview-pane", PreviewPane)
            preview.monochrome = self._prefs.monochrome
        except NoMatches:
            pass

        # Set view_mode from preferences (triggers watch_view_mode)
        self.view_mode = self._prefs.view_mode

        # Apply pre-loaded sessions synchronously so widgets exist immediately
        if self._preloaded_sessions is not None:
            self._apply_sessions(self._preloaded_sessions)
            self._preloaded_sessions = None
        else:
            self.refresh_sessions()
        self.update_daemon_status()
        self.update_timeline()
        # Kick off status fetch immediately (widgets already exist from pre-load)
        self.update_all_statuses()

        if self.diagnostics:
            # DIAGNOSTICS MODE: No auto-refresh timers
            self._update_subtitle()  # Will include [DIAGNOSTICS]
            self.notify(
                "DIAGNOSTICS MODE: All auto-refresh disabled. Press 'r' to manually refresh.",
                severity="warning",
                timeout=10
            )
        else:
            # Normal mode: Set up all timers
            # Refresh session list every 10 seconds
            self.set_interval(10, self.refresh_sessions)
            # Fast status updates every 250ms (detect_status + capture_pane only)
            self.set_interval(0.25, self.update_focused_status)
            # Slow stats updates every 5s (claude stats + git diff — heavy file I/O)
            self.set_interval(5, self._update_stats_async)
            # Update daemon status every 5 seconds
            self.set_interval(5, self.update_daemon_status)
            # Update timeline every 30 seconds
            self.set_interval(30, self.update_timeline)
            # Update AI summaries every 5 seconds (only runs if enabled)
            self.set_interval(5, self._update_summaries_async)

    def update_daemon_status(self) -> None:
        """Update daemon status bar (kicks off background worker)"""
        self._fetch_daemon_status_async()

    @work(thread=True, exclusive=True, group="daemon_status")
    def _fetch_daemon_status_async(self) -> None:
        """Fetch daemon status off the main thread, then apply to UI."""
        try:
            daemon_bar = self.query_one("#daemon-status", DaemonStatusBar)
        except NoMatches:
            return

        # All I/O happens here in the worker thread
        monitor_state = get_monitor_daemon_state(self.tmux_session)
        daemon_count = count_daemon_processes("monitor_daemon", session=self.tmux_session)

        # Gather data that DaemonStatusBar.update_status() would fetch
        asleep_ids = set()
        if daemon_bar._session_manager:
            asleep_ids = {
                s.id for s in daemon_bar._session_manager.list_sessions()
                if s.is_asleep and s.tmux_session == self.tmux_session
            }

        # Pre-compute active session names for spin rate calculation
        active_session_names = []
        if monitor_state and monitor_state.sessions:
            active_session_names = [
                s.name for s in monitor_state.sessions
                if s.session_id not in asleep_ids
            ]

        # Fetch all volatile I/O state for the status bar (PID checks, CSV reads, etc.)
        baseline_minutes = getattr(self, 'baseline_minutes', 0)
        daemon_bar.monitor_state = monitor_state
        daemon_bar._asleep_session_ids = asleep_ids
        daemon_bar.fetch_volatile_state(
            baseline_minutes=baseline_minutes,
            active_session_names=active_session_names,
        )

        # Apply results on main thread
        self.call_from_thread(
            self._apply_daemon_status, daemon_bar, monitor_state, daemon_count, asleep_ids
        )

    def _apply_daemon_status(
        self,
        daemon_bar: "DaemonStatusBar",
        monitor_state,
        daemon_count: int,
        asleep_ids: set,
    ) -> None:
        """Apply daemon status results on main thread (no I/O)."""
        daemon_bar.monitor_state = monitor_state
        daemon_bar._asleep_session_ids = asleep_ids
        daemon_bar.refresh()

        # Check for multiple daemon processes (potential time tracking bug)
        if daemon_count > 1 and not self._multiple_daemon_warning_shown:
            self._multiple_daemon_warning_shown = True
            self.notify(
                f"WARNING: {daemon_count} monitor daemons detected! "
                "This causes time tracking bugs. Press \\ to restart daemon.",
                severity="error",
                timeout=30
            )
        elif daemon_count <= 1:
            # Reset warning flag when back to normal
            self._multiple_daemon_warning_shown = False

    def update_timeline(self) -> None:
        """Update the status timeline widget (kicks off background worker)"""
        self._fetch_timeline_async()

    @work(thread=True, exclusive=True, group="timeline")
    def _fetch_timeline_async(self) -> None:
        """Read timeline CSV data off the main thread, then apply to UI."""
        try:
            timeline = self.query_one("#timeline", StatusTimeline)
        except NoMatches:
            return

        # Snapshot sessions for the worker (avoid race with main thread)
        sessions = list(self.sessions)

        # Heavy CSV I/O happens here in the worker thread
        presence_history, agent_histories = timeline.fetch_history_data(sessions)

        # Apply on main thread
        self.call_from_thread(
            timeline.apply_history_data, sessions, presence_history, agent_histories
        )

    def _save_prefs(self) -> None:
        """Save current TUI preferences to disk."""
        self._prefs.save(self.tmux_session)

    def on_resize(self) -> None:
        """Handle terminal resize events"""
        self.refresh()
        self.update_session_widgets()

    def refresh_sessions(self) -> None:
        """Refresh session list (kicks off background worker).

        Uses launcher.list_sessions() to detect terminated sessions
        (tmux windows that no longer exist, e.g., after machine reboot).
        """
        self._fetch_sessions_async()

    @work(thread=True, exclusive=True, group="refresh_sessions")
    def _fetch_sessions_async(self) -> None:
        """Read session list off the main thread, then apply to UI."""
        sessions = self.launcher.list_sessions()
        self.call_from_thread(self._apply_sessions, sessions)

    def _apply_sessions(self, sessions: list) -> None:
        """Apply refreshed session list on main thread (no I/O)."""
        # Capture focus NOW (at apply time) so it reflects the user's current
        # position, not where they were when the async fetch started.
        focused_widget = self._get_focused_widget()
        focused_session_id = focused_widget.session.id if focused_widget else None

        # Detect new sessions for timeline refresh (#244)
        old_names = {s.name for s in self.sessions}

        self._invalidate_sessions_cache()
        self.sessions = sessions
        # Apply sorting (#61)
        self._sort_sessions()
        # Calculate max repo/branch widths for alignment in full detail mode
        widths_changed = self._recalc_repo_widths(self.sessions)
        # Check focus state BEFORE update_session_widgets, which may do DOM
        # changes that cause Textual to drop focus to None.
        focus_was_on_session = isinstance(self.focused, SessionSummary)
        self.update_session_widgets(force_refresh=widths_changed)

        # Update focused_session_index to follow the same session at its new position.
        # Only restore Textual's focus if a SessionSummary had it before the update —
        # never steal focus from the command bar or other input widgets.
        if focused_session_id:
            widgets = self._get_widgets_in_session_order()
            found = False
            for i, widget in enumerate(widgets):
                if widget.session.id == focused_session_id:
                    self.focused_session_index = i
                    if focus_was_on_session:
                        widget.focus()
                    found = True
                    break
            # Focused session disappeared (killed/filtered) — clamp index
            if not found and widgets:
                self.focused_session_index = min(self.focused_session_index, len(widgets) - 1)

        # Trigger timeline refresh when new sessions appear (child agents) (#244)
        new_names = {s.name for s in sessions}
        if new_names - old_names:
            self.update_timeline()

        # On first load, select the first agent and kick off async updates.
        if not self._initial_sessions_loaded:
            self._initial_sessions_loaded = True
            self.update_timeline()
            self.update_daemon_status()
            # Select first agent immediately (no timer delay)
            self._select_first_agent()

    def _recalc_repo_widths(self, sessions) -> bool:
        """Recalculate max repo/branch widths and name-match flag.

        Returns True if any width or flag actually changed.
        """
        old = (self.max_repo_width, self.max_branch_width, self.all_names_match_repos)
        sessions = list(sessions)
        if sessions:
            self.max_repo_width = max(
                (len(s.repo_name or "n/a") for s in sessions), default=5
            )
            self.max_branch_width = max(
                (len(s.branch or "n/a") for s in sessions), default=5
            )
            self.all_names_match_repos = all(
                s.name == s.repo_name for s in sessions if s.repo_name
            )
        else:
            self.max_repo_width = 10
            self.max_branch_width = 10
            self.all_names_match_repos = False
        return old != (self.max_repo_width, self.max_branch_width, self.all_names_match_repos)

    def _sort_sessions(self) -> None:
        """Sort sessions based on current sort mode (#61)."""
        self.sessions = sort_sessions(self.sessions, self._prefs.sort_mode)

    def _get_cached_sessions(self) -> dict[str, Session]:
        """Get sessions with caching to reduce disk I/O.

        Returns cached session data if TTL hasn't expired, otherwise
        reloads from disk and updates the cache.
        """
        import time
        now = time.time()
        if now - self._sessions_cache_time > self._sessions_cache_ttl:
            # Cache expired, reload from disk
            self._sessions_cache = {s.id: s for s in self.session_manager.list_sessions()}
            self._sessions_cache_time = now
        return self._sessions_cache

    def _invalidate_sessions_cache(self) -> None:
        """Invalidate the sessions cache to force reload on next access."""
        self._sessions_cache_time = 0

    def _get_focused_widget(self) -> "SessionSummary | None":
        """Get the selected session widget using focused_session_index.

        Uses the app's own selection state rather than Textual's self.focused,
        which can diverge during DOM reordering or when non-session widgets
        (e.g. command bar) have focus.

        Self-healing: if the index is out of bounds but widgets exist, clamp it
        so that an agent is always focused when agents are present.
        """
        widgets = self._get_widgets_in_session_order()
        if not widgets:
            return None
        if not (0 <= self.focused_session_index < len(widgets)):
            self.focused_session_index = max(0, min(self.focused_session_index, len(widgets) - 1))
        return widgets[self.focused_session_index]

    def update_focused_status(self) -> None:
        """Update all session statuses every 250ms.

        All data fetching (tmux capture_pane, claude stats, git diff) happens
        in a background thread with ThreadPoolExecutor parallelism, so updating
        all widgets is no more expensive than updating one.
        """
        # Skip if an update is already in progress
        if self._status_update_in_progress:
            return

        widgets = list(self.query(SessionSummary))
        if not widgets:
            return

        self._status_update_in_progress = True
        self._fetch_statuses_async(widgets)

    def update_all_statuses(self) -> None:
        """Trigger full async refresh of all session widgets.

        Kicks off both the fast path (detect_status) and slow path (stats/git).
        Primarily used for manual refresh ('r' key) and initial load.
        """
        # Fast path
        if not self._status_update_in_progress:
            widgets = list(self.query(SessionSummary))
            if widgets:
                self._status_update_in_progress = True
                self._fetch_statuses_async(widgets)
        # Slow path
        self._update_stats_async()

    @work(thread=True, exclusive=True, group="fast_status")
    def _fetch_statuses_async(self, widgets: list) -> None:
        """Fast path: fetch detect_status (capture_pane) only, every 250ms.

        This is the critical path for responsive preview pane updates.
        Heavy operations (claude stats, git diff) run on a separate 5s timer.
        """
        try:
            # Load fresh session data (this does file I/O but we're in a thread)
            fresh_sessions = {s.id: s for s in self.session_manager.list_sessions()}

            # Build list of sessions to check (use fresh data if available)
            sessions_to_check = []
            for widget in widgets:
                session = fresh_sessions.get(widget.session.id, widget.session)
                sessions_to_check.append((widget.session.id, session))

            # Fetch only detect_status (capture_pane) in parallel — no heavy I/O
            def fetch_status(session):
                try:
                    if session.status == "terminated":
                        return ("terminated", "(tmux window no longer exists)", "")
                    if session.status == "done":
                        return ("done", "Completed", "")
                    return self.detector.detect_status(session)
                except Exception:
                    return (STATUS_WAITING_USER, "Error", "")

            sessions = [s for _, s in sessions_to_check]
            with ThreadPoolExecutor(max_workers=min(8, len(sessions))) as executor:
                results = list(executor.map(fetch_status, sessions))

            # Package results with session IDs
            status_results = {}
            for (session_id, _), status_result in zip(sessions_to_check, results):
                status_results[session_id] = status_result

            # Enrich status with heartbeat info from daemon state (#171)
            daemon_state = get_monitor_daemon_state(self.tmux_session)
            if daemon_state and daemon_state.sessions:
                heartbeat_sessions = {
                    s.session_id for s in daemon_state.sessions
                    if s.running_from_heartbeat
                }
                for session_id in heartbeat_sessions:
                    if session_id in status_results:
                        status, activity, content = status_results[session_id]
                        if status == STATUS_RUNNING:
                            status_results[session_id] = (STATUS_RUNNING_HEARTBEAT, activity, content)

                waiting_heartbeat_sessions = {
                    s.session_id for s in daemon_state.sessions
                    if s.waiting_for_heartbeat
                }
                for session_id in waiting_heartbeat_sessions:
                    if session_id in status_results:
                        status, activity, content = status_results[session_id]
                        if status not in (STATUS_RUNNING, STATUS_RUNNING_HEARTBEAT):
                            status_results[session_id] = (STATUS_WAITING_HEARTBEAT, activity, content)

            # Use local summaries from TUI's summarizer (not daemon state)
            ai_summaries = {}
            for session_id, summary in self._summaries.items():
                ai_summaries[session_id] = (
                    summary.text or "",
                    summary.context or "",
                )

            # Update UI on main thread
            self.call_from_thread(self._apply_status_results, status_results, fresh_sessions, ai_summaries)
        finally:
            self._status_update_in_progress = False

    @work(thread=True, exclusive=True, group="slow_stats")
    def _update_stats_async(self) -> None:
        """Slow path: fetch claude stats + git diff every 5s.

        These involve heavy file I/O (parsing JSONL session files, running
        git diff subprocess) and don't need 250ms updates. Runs independently
        from the fast status path so it never blocks preview pane updates.
        """
        if self._stats_update_in_progress:
            return
        self._stats_update_in_progress = True
        try:
            widgets = list(self.query(SessionSummary))
            if not widgets:
                return

            fresh_sessions = {s.id: s for s in self.session_manager.list_sessions()}

            sessions_to_check = []
            for widget in widgets:
                session = fresh_sessions.get(widget.session.id, widget.session)
                sessions_to_check.append((widget.session.id, session))

            def fetch_stats(session):
                try:
                    claude_stats = get_session_stats(session)
                    git_diff = None
                    if session.start_directory:
                        git_diff = get_git_diff_stats(session.start_directory)
                    return (claude_stats, git_diff)
                except Exception:
                    return (None, None)

            sessions = [s for _, s in sessions_to_check]
            with ThreadPoolExecutor(max_workers=min(8, len(sessions))) as executor:
                results = list(executor.map(fetch_stats, sessions))

            stats_results = {}
            git_diff_results = {}
            for (session_id, _), (claude_stats, git_diff) in zip(sessions_to_check, results):
                stats_results[session_id] = claude_stats
                git_diff_results[session_id] = git_diff

            self.call_from_thread(self._apply_stats_results, stats_results, git_diff_results)
        finally:
            self._stats_update_in_progress = False

    def _apply_status_results(self, status_results: dict, fresh_sessions: dict, ai_summaries: dict = None) -> None:
        """Apply fast-path status results to widgets (runs on main thread)."""
        prefs_changed = False
        ai_summaries = ai_summaries or {}

        # Recalculate repo/branch widths from fresh session data (#143)
        if fresh_sessions:
            self._recalc_repo_widths(fresh_sessions.values())

        for widget in self.query(SessionSummary):
            session_id = widget.session.id

            # Update widget's session with fresh data
            if session_id in fresh_sessions:
                widget.session = fresh_sessions[session_id]

            # Update AI summaries (if available)
            if session_id in ai_summaries:
                widget.ai_summary_short, widget.ai_summary_context = ai_summaries[session_id]

            # Apply status if we have results for this widget
            if session_id in status_results:
                status, activity, content = status_results[session_id]

                # Detect transitions TO stalled state (waiting_user)
                prev_status = self._previous_statuses.get(session_id)
                if status == STATUS_WAITING_USER and prev_status != STATUS_WAITING_USER:
                    self._prefs.visited_stalled_agents.discard(session_id)
                    prefs_changed = True

                self._previous_statuses[session_id] = status

                is_unvisited_stalled = (
                    status == STATUS_WAITING_USER and
                    session_id not in self._prefs.visited_stalled_agents and
                    not widget.session.is_asleep
                )
                widget.is_unvisited_stalled = is_unvisited_stalled

                # Pass None for claude_stats/git_diff — those come from the slow path
                widget.apply_status_no_refresh(status, activity, content, None, None)
                widget.refresh()

        if prefs_changed:
            self._save_prefs()

        # Update preview pane on the fast path (250ms) for responsive updates
        if self.view_mode == "list_preview":
            self._update_preview()

    def _apply_stats_results(self, stats_results: dict, git_diff_results: dict) -> None:
        """Apply slow-path stats results to widgets (runs on main thread)."""
        for widget in self.query(SessionSummary):
            session_id = widget.session.id
            claude_stats = stats_results.get(session_id)
            git_diff = git_diff_results.get(session_id)
            if claude_stats is not None:
                widget.claude_stats = claude_stats
                widget.file_subagent_count = claude_stats.live_subagent_count
            if git_diff is not None:
                widget.git_diff_stats = git_diff
            if claude_stats is not None or git_diff is not None:
                widget.refresh()

    @work(thread=True, exclusive=True, name="summarizer")
    def _update_summaries_async(self) -> None:
        """Background thread for AI summarization.

        Only runs if summarizer is enabled. Updates are applied to widgets
        via call_from_thread.
        """
        if not self._summarizer.enabled:
            return

        # Get fresh session list (filtered to this tmux session)
        all_sessions = self.session_manager.list_sessions()
        sessions = [s for s in all_sessions if s.tmux_session == self.tmux_session]
        if not sessions:
            return

        # Update summaries (this makes API calls)
        summaries = self._summarizer.update(sessions)

        # Apply to widgets on main thread
        self.call_from_thread(self._apply_summaries, summaries)

    def _apply_summaries(self, summaries: dict) -> None:
        """Apply AI summaries to session widgets (runs on main thread)."""
        self._summaries = summaries
        is_enabled = self._summarizer.config.enabled

        for widget in self.query(SessionSummary):
            widget.summarizer_enabled = is_enabled
            session_id = widget.session.id
            if session_id in summaries:
                summary = summaries[session_id]
                widget.ai_summary_short = summary.text or ""
                widget.ai_summary_context = summary.context or ""
            widget.refresh()

    def update_session_widgets(self, force_refresh: bool = True) -> None:
        """Update the session display incrementally.

        Only adds/removes widgets when sessions change, rather than
        destroying and recreating all widgets (which causes UI stutter).

        Args:
            force_refresh: If False, only refresh widgets whose session data
                actually changed. Set to True when column widths changed or
                on structural changes that require all widgets to repaint.
        """
        container = self.query_one("#sessions-container", ScrollableContainer)

        # Check if any session has a cost budget for column alignment (#173)
        any_has_budget = any(s.cost_budget_usd > 0 for s in self.sessions)

        # Build the list of sessions to display using extracted logic
        display_sessions = filter_visible_sessions(
            active_sessions=self.sessions,
            terminated_sessions=list(self._terminated_sessions.values()),
            hide_asleep=self.hide_asleep,
            show_terminated=self.show_terminated,
            show_done=self.show_done,
            collapsed_parents=self.collapsed_parents if self._prefs.sort_mode == "by_tree" else None,
        )

        # Get existing widgets and their session IDs
        existing_widgets = {w.session.id: w for w in self.query(SessionSummary)}
        new_session_ids = {s.id for s in display_sessions}
        existing_session_ids = set(existing_widgets.keys())

        # Check if we have an empty message widget that needs removal
        # (Static widgets that aren't SessionSummary)
        has_empty_message = any(
            isinstance(w, Static) and not isinstance(w, SessionSummary)
            for w in container.children
        )

        # If sessions changed or we need to show/hide empty message, do incremental update
        sessions_added = new_session_ids - existing_session_ids
        sessions_removed = existing_session_ids - new_session_ids

        if not sessions_added and not sessions_removed and not has_empty_message:
            # No structural changes needed - just update session data in existing widgets
            session_map = {s.id: s for s in display_sessions}
            for widget in existing_widgets.values():
                if widget.session.id in session_map:
                    new_session = session_map[widget.session.id]
                    old_budget = widget.any_has_budget
                    # Check if anything display-relevant actually changed
                    changed = (
                        force_refresh
                        or widget.session != new_session
                        or old_budget != any_has_budget
                    )
                    widget.session = new_session
                    widget.any_has_budget = any_has_budget
                    # Update terminated visual state
                    if widget.session.status == "terminated":
                        widget.add_class("terminated")
                    else:
                        widget.remove_class("terminated")
                    # Only refresh if data actually changed (#218 alignment still
                    # handled via force_refresh=True when widths change)
                    if changed:
                        widget.refresh()
            # Still reorder widgets to handle sort mode changes
            self._reorder_session_widgets(container)
            return

        # Remove widgets for deleted sessions
        for session_id in sessions_removed:
            widget = existing_widgets[session_id]
            widget.remove()

        # Clear empty message if we now have sessions
        if has_empty_message and display_sessions:
            container.remove_children()

        # Handle empty state
        if not display_sessions:
            if not has_empty_message:
                container.remove_children()
                container.mount(Static(
                    "\n  No active sessions.\n\n  Launch a session with:\n  overcode launch --name my-agent code\n",
                    classes="dim"
                ))
            return

        # Add widgets for new sessions
        for session in display_sessions:
            if session.id in sessions_added:
                widget = SessionSummary(session, self.detector)
                # Restore expanded state if we have it saved
                if session.id in self.expanded_states:
                    widget.expanded = self.expanded_states[session.id]
                # Apply current detail level
                widget.detail_lines = self.DETAIL_LEVELS[self.detail_level_index]
                # Apply current summary detail level
                widget.summary_detail = self.SUMMARY_LEVELS[self.summary_level_index]
                # Apply current summary content mode (#140)
                widget.summary_content_mode = self.summary_content_mode
                # Apply cost display mode
                widget.show_cost = self.show_cost
                widget.any_has_budget = any_has_budget
                # Apply column group visibility (#178)
                widget.summary_groups = self._prefs.summary_groups
                # Apply list-mode class if in list_preview view
                if self.view_mode == "list_preview":
                    widget.add_class("list-mode")
                    widget.expanded = False  # Force collapsed in list mode
                # Mark terminated sessions with visual styling and status
                if session.status == "terminated":
                    widget.add_class("terminated")
                    widget.detected_status = "terminated"
                    widget.current_activity = "(tmux window no longer exists)"
                # Set summarizer enabled state
                widget.summarizer_enabled = self._summarizer.config.enabled
                # Apply existing summary if available
                if session.id in self._summaries:
                    summary = self._summaries[session.id]
                    widget.ai_summary_short = summary.text or ""
                    widget.ai_summary_context = summary.context or ""
                container.mount(widget)
                # NOTE: Don't call update_status() here - it does blocking tmux calls
                # The 250ms interval (update_all_statuses) will update status shortly

        # Reorder widgets to match display_sessions order
        # This must run after any structural changes AND after sort mode changes
        self._reorder_session_widgets(container)

    def on_session_summary_expanded_changed(self, message: SessionSummary.ExpandedChanged) -> None:
        """Handle expanded state changes from session widgets"""
        # Don't save forced-collapsed state in list_preview mode
        if self.view_mode == "list_preview":
            return
        self.expanded_states[message.session_id] = message.expanded

    def on_session_summary_stalled_agent_visited(self, message: SessionSummary.StalledAgentVisited) -> None:
        """Handle when user visits a stalled agent - mark as visited"""
        session_id = message.session_id
        self._prefs.visited_stalled_agents.add(session_id)
        self._save_prefs()

        # Update the widget's state
        for widget in self.query(SessionSummary):
            if widget.session.id == session_id:
                widget.is_unvisited_stalled = False
                widget.refresh()
                break

    def on_session_summary_session_selected(self, message: SessionSummary.SessionSelected) -> None:
        """Handle session selection - update .selected class to preserve highlight when unfocused"""
        session_id = message.session_id
        for widget in self.query(SessionSummary):
            if widget.session.id == session_id:
                widget.add_class("selected")
            else:
                widget.remove_class("selected")

    def _get_widgets_in_session_order(self) -> List[SessionSummary]:
        """Get session widgets sorted to match self.sessions order.

        query() returns widgets in DOM/mount order, but we want navigation
        to follow self.sessions order for consistency with display.
        """
        widgets = list(self.query(SessionSummary))
        if not widgets:
            return []
        # Build session_id -> order mapping from self.sessions
        session_order = {s.id: i for i, s in enumerate(self.sessions)}
        # Sort widgets by their session's position in self.sessions
        widgets.sort(key=lambda w: session_order.get(w.session.id, 999))
        return widgets

    def _reorder_session_widgets(self, container: ScrollableContainer) -> None:
        """Reorder session widgets in container to match session display order.

        When new widgets are mounted, they're appended at the end.
        This method reorders them to match the display order (active + terminated).
        """
        widgets = {w.session.id: w for w in self.query(SessionSummary)}
        if not widgets:
            return

        # Build display sessions list (active + terminated if enabled)
        display_sessions = list(self.sessions)
        if self.show_terminated:
            active_ids = {s.id for s in self.sessions}
            for session in self._terminated_sessions.values():
                if session.id not in active_ids:
                    display_sessions.append(session)

        # Get desired order from display_sessions
        ordered_widgets = []
        for session in display_sessions:
            if session.id in widgets:
                ordered_widgets.append(widgets[session.id])

        # Skip DOM moves if order already matches — avoids unnecessary relayout
        # which cause Textual repaint glitches every 10s
        current_order = [
            w for w in container.children if isinstance(w, SessionSummary)
        ]
        if current_order != ordered_widgets:
            # Reorder by moving each widget to the correct position.
            # Save focused widget so we can restore if move_child() drops focus.
            had_focus = self.focused
            for i, widget in enumerate(ordered_widgets):
                if i == 0:
                    container.move_child(widget, before=0)
                else:
                    container.move_child(widget, after=ordered_widgets[i - 1])
            # Restore focus if move_child() caused it to be lost
            if had_focus is not None and self.focused is None:
                had_focus.focus()

        # Update tree prefix and child count for hierarchy display (#244)
        # Always runs (not gated by reorder) so prefixes are set on first mount.
        is_tree_mode = self._prefs.sort_mode == "by_tree"
        # Build child count map from self.sessions (not just visible widgets)
        # so collapsed parents still show the correct count.
        child_counts: dict[str, int] = {}
        for s in self.sessions:
            if s.parent_session_id is not None:
                child_counts[s.parent_session_id] = child_counts.get(s.parent_session_id, 0) + 1
        for widget in ordered_widgets:
            widget.child_count = child_counts.get(widget.session.id, 0)
            widget.children_collapsed = widget.session.id in self.collapsed_parents
            if is_tree_mode:
                depth = self.session_manager.compute_depth(widget.session)
                # Determine if this is the last sibling at its level
                parent_id = widget.session.parent_session_id
                siblings = [w for w in ordered_widgets if w.session.parent_session_id == parent_id]
                is_last = siblings and siblings[-1] is widget
                if depth == 0:
                    widget.tree_prefix = ""
                else:
                    indent = "  " * (depth - 1)
                    connector = "└─" if is_last else "├─"
                    widget.tree_prefix = indent + connector
                widget.tree_depth = depth
            else:
                widget.tree_prefix = ""
                widget.tree_depth = 0

    def _sync_tmux_window(self, widget: Optional["SessionSummary"] = None) -> None:
        """Sync external tmux pane to show the focused session's window.

        Args:
            widget: The session widget to sync to. If None, uses self.focused.
        """
        if not self.tmux_sync:
            return

        try:
            target = widget if widget is not None else self.focused
            if isinstance(target, SessionSummary):
                window_index = target.session.tmux_window
                if window_index is not None:
                    self._tmux.select_window(self.tmux_session, window_index)
        except Exception:
            pass  # Silent fail - don't disrupt navigation

    def watch_view_mode(self, view_mode: str) -> None:
        """React to view mode changes."""
        # Update subtitle to show current mode
        self._update_subtitle()

        try:
            preview = self.query_one("#preview-pane", PreviewPane)
            container = self.query_one("#sessions-container", ScrollableContainer)
            if view_mode == "list_preview":
                # Collapse all sessions, show preview pane
                container.add_class("list-mode")
                for widget in self.query(SessionSummary):
                    widget.add_class("list-mode")
                    widget.expanded = False  # Force collapsed
                preview.add_class("visible")
                self._update_preview()
            else:
                # Restore tree mode, hide preview
                container.remove_class("list-mode")
                for widget in self.query(SessionSummary):
                    widget.remove_class("list-mode")
                    # Restore saved expanded state
                    saved = self.expanded_states.get(widget.session.id, True)
                    widget.expanded = saved
                preview.remove_class("visible")
        except NoMatches:
            pass

    def _update_subtitle(self) -> None:
        """Update the header subtitle to show session and view mode."""
        mode_label = "Tree" if self.view_mode == "tree" else "List+Preview"
        sync_label = " [Sync]" if self.tmux_sync else ""
        if self.diagnostics:
            self.sub_title = f"{self.tmux_session} [{mode_label}]{sync_label} [DIAGNOSTICS]"
        else:
            self.sub_title = f"{self.tmux_session} [{mode_label}]{sync_label}"

    def _select_first_agent(self) -> None:
        """Select the first agent so something is highlighted from the start."""
        try:
            widgets = list(self.query(SessionSummary))
            if widgets:
                self.focused_session_index = 0
                widgets[0].focus()
                if self.view_mode == "list_preview":
                    self._update_preview()
        except NoMatches:
            pass

    def _update_preview(self) -> None:
        """Update preview pane with the selected session's content.

        Uses focused_session_index (the app's own selection state) rather
        than self.focused (Textual's internal focus) because DOM reordering
        in _reorder_session_widgets() and async focus changes can cause
        self.focused to diverge from the visually highlighted row.
        """
        try:
            preview = self.query_one("#preview-pane", PreviewPane)
            widgets = self._get_widgets_in_session_order()
            if 0 <= self.focused_session_index < len(widgets):
                preview.update_from_widget(widgets[self.focused_session_index])
        except NoMatches:
            pass

    def on_command_bar_send_requested(self, message: CommandBar.SendRequested) -> None:
        """Handle send request from command bar."""
        # Auto-wake sleeping agent if needed (#168)
        session = self.session_manager.get_session_by_name(message.session_name)
        if session and session.is_asleep:
            self.session_manager.update_session(session.id, is_asleep=False)
            # Update widget display immediately
            for widget in self.query(SessionSummary):
                if widget.session.id == session.id:
                    widget.session.is_asleep = False
                    if widget.detected_status == "asleep":
                        widget.detected_status = "running"
                    widget.refresh()
                    break
            self.notify(f"Woke agent '{message.session_name}' to send command", severity="information")

        launcher = ClaudeLauncher(
            tmux_session=self.tmux_session,
            session_manager=self.session_manager
        )
        success = launcher.send_to_session(message.session_name, message.text)
        if success:
            self._invalidate_sessions_cache()  # Refresh to show updated stats
            self.notify(f"Sent to {message.session_name}")
        else:
            self.notify(f"Failed to send to {message.session_name}", severity="error")

    def on_command_bar_standing_order_requested(self, message: CommandBar.StandingOrderRequested) -> None:
        """Handle standing order request from command bar."""
        session = self.session_manager.get_session_by_name(message.session_name)
        if session:
            self.session_manager.set_standing_instructions(session.id, message.text)
            if message.text:
                self.notify(f"Standing order set for {message.session_name}")
            else:
                self.notify(f"Standing order cleared for {message.session_name}")
            # Refresh session list to show updated standing order
            self.refresh_sessions()
        else:
            self.notify(f"Session '{message.session_name}' not found", severity="error")

    def on_command_bar_value_updated(self, message: CommandBar.ValueUpdated) -> None:
        """Handle agent value update from command bar (#61)."""
        session = self.session_manager.get_session_by_name(message.session_name)
        if session:
            self.session_manager.set_agent_value(session.id, message.value)
            self.notify(f"Value set to {message.value} for {message.session_name}")
            # Refresh and re-sort session list
            self.refresh_sessions()
        else:
            self.notify(f"Session '{message.session_name}' not found", severity="error")

    def on_command_bar_budget_updated(self, message: CommandBar.BudgetUpdated) -> None:
        """Handle cost budget update from command bar (#173)."""
        session = self.session_manager.get_session_by_name(message.session_name)
        if session:
            self.session_manager.set_cost_budget(session.id, message.budget_usd)
            if message.budget_usd > 0:
                self.notify(f"Budget set to ${message.budget_usd:.2f} for {message.session_name}")
            else:
                self.notify(f"Budget cleared for {message.session_name}")
            self.refresh_sessions()
        else:
            self.notify(f"Session '{message.session_name}' not found", severity="error")

    def on_command_bar_annotation_updated(self, message: CommandBar.AnnotationUpdated) -> None:
        """Handle human annotation update from command bar (#74)."""
        session = self.session_manager.get_session_by_name(message.session_name)
        if session:
            self.session_manager.set_human_annotation(session.id, message.annotation)
            if message.annotation:
                self.notify(f"Annotation set for {message.session_name}")
            else:
                self.notify(f"Annotation cleared for {message.session_name}")
            # Refresh session list to show updated annotation
            self.refresh_sessions()
        else:
            self.notify(f"Session '{message.session_name}' not found", severity="error")

    def on_command_bar_heartbeat_updated(self, message: CommandBar.HeartbeatUpdated) -> None:
        """Handle heartbeat configuration update from command bar (#171)."""
        session = self.session_manager.get_session_by_name(message.session_name)
        if not session:
            self.notify(f"Session not found: {message.session_name}", severity="error")
            return

        self.session_manager.update_session(
            session.id,
            heartbeat_enabled=message.enabled,
            heartbeat_frequency_seconds=message.frequency,
            heartbeat_instruction=message.instruction,
        )

        # Wake daemon so status updates immediately (#212)
        signal_activity(self.tmux_session)

        if message.enabled:
            freq_str = format_duration(message.frequency)
            self.notify(f"Heartbeat enabled: every {freq_str}", severity="information")
        else:
            self.notify("Heartbeat disabled", severity="information")

        # Refresh session list to show updated heartbeat config
        self.refresh_sessions()

    def on_command_bar_clear_requested(self, message: CommandBar.ClearRequested) -> None:
        """Handle clear request - hide and unfocus command bar."""
        try:
            # Disable and hide the command bar
            cmd_bar = self.query_one("#command-bar", CommandBar)
            target_session_name = cmd_bar.target_session  # Remember before disabling
            cmd_bar.query_one("#cmd-input", Input).disabled = True
            cmd_bar.query_one("#cmd-textarea", TextArea).disabled = True
            cmd_bar.remove_class("visible")

            # Focus the targeted session (not first session) to keep preview on it
            if self.sessions:
                widgets = self._get_widgets_in_session_order()
                if widgets:
                    # Find widget matching target session, fall back to current index
                    target_widget = None
                    for i, w in enumerate(widgets):
                        if w.session.name == target_session_name:
                            target_widget = w
                            self.focused_session_index = i
                            break
                    if target_widget:
                        target_widget.focus()
                    else:
                        self.focused_session_index = min(self.focused_session_index, len(widgets) - 1)
                        widgets[self.focused_session_index].focus()
                    if self.view_mode == "list_preview":
                        self._update_preview()
        except NoMatches:
            pass

    def on_command_bar_new_agent_requested(self, message: CommandBar.NewAgentRequested) -> None:
        """Handle new agent creation request."""
        agent_name = message.agent_name
        directory = message.directory
        bypass_permissions = message.bypass_permissions

        # Validate name (no spaces, reasonable length)
        if not agent_name or len(agent_name) > 50:
            self.notify("Invalid agent name", severity="error")
            return

        if ' ' in agent_name:
            self.notify("Agent name cannot contain spaces", severity="error")
            return

        # Check if agent with this name already exists
        existing = self.session_manager.get_session_by_name(agent_name)
        if existing:
            self.notify(f"Agent '{agent_name}' already exists", severity="error")
            return

        # Create new agent using launcher
        launcher = ClaudeLauncher(
            tmux_session=self.tmux_session,
            session_manager=self.session_manager
        )

        try:
            launcher.launch(
                name=agent_name,
                start_directory=directory,
                dangerously_skip_permissions=bypass_permissions
            )
            dir_info = f" in {directory}" if directory else ""
            perm_info = " (bypass mode)" if bypass_permissions else ""
            self.notify(f"Created agent: {agent_name}{dir_info}{perm_info}", severity="information")
            # Refresh to show new agent
            self.refresh_sessions()
        except Exception as e:
            self.notify(f"Failed to create agent: {e}", severity="error")

    def _ensure_monitor_daemon(self) -> None:
        """Start the Monitor Daemon if not running.

        Called automatically on TUI mount to ensure continuous monitoring.
        The Monitor Daemon handles status tracking, time accumulation,
        stats sync, and user presence detection.
        """
        # Check PID file first
        if is_monitor_daemon_running(self.tmux_session):
            return  # Already running

        # Also check for running processes (in case PID file is stale or daemon is starting)
        # This prevents race conditions where multiple TUIs start daemons simultaneously
        daemon_count = count_daemon_processes("monitor_daemon", session=self.tmux_session)
        if daemon_count > 0:
            return  # Daemon process exists, just PID file might be missing/stale

        try:
            subprocess.Popen(
                [sys.executable, "-m", "overcode.monitor_daemon",
                 "--session", self.tmux_session],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.notify("Monitor Daemon started", severity="information")
        except (OSError, subprocess.SubprocessError) as e:
            self.notify(f"Failed to start Monitor Daemon: {e}", severity="warning")

    def _execute_kill(self, focused: "SessionSummary", session_name: str, session_id: str) -> None:
        """Execute the actual kill operation after confirmation."""
        # Save a copy of the session for showing when show_terminated is True
        session_copy = focused.session
        # Mark it as terminated for display purposes
        from dataclasses import replace
        terminated_session = replace(session_copy, status="terminated")

        # Use launcher to kill the session
        launcher = ClaudeLauncher(
            tmux_session=self.tmux_session,
            session_manager=self.session_manager
        )

        if launcher.kill_session(session_name):
            self.notify(f"Killed agent: {session_name}", severity="information")

            # Store in terminated sessions cache for ghost mode
            self._terminated_sessions[session_id] = terminated_session

            # Remove the widget (will be re-added if show_terminated is True)
            focused.remove()
            # Update session cache
            if session_id in self._sessions_cache:
                del self._sessions_cache[session_id]
            if session_id in self.expanded_states:
                del self.expanded_states[session_id]

            # If showing terminated sessions, refresh to add it back
            if self.show_terminated:
                self.update_session_widgets()
            # Clear preview pane and focus next agent if in list_preview mode
            if self.view_mode == "list_preview":
                try:
                    preview = self.query_one("#preview-pane", PreviewPane)
                    preview.session_name = ""
                    preview.content_lines = []
                    try:
                        content_widget = preview.query_one("#preview-content", Static)
                        content_widget.update("")
                    except Exception:
                        pass
                    # Focus next available agent
                    widgets = list(self.query(SessionSummary))
                    if widgets:
                        self.focused_session_index = min(self.focused_session_index, len(widgets) - 1)
                        widgets[self.focused_session_index].focus()
                        self._update_preview()
                except NoMatches:
                    pass
        else:
            self.notify(f"Failed to kill agent: {session_name}", severity="error")

    def _execute_restart(self, focused: "SessionSummary") -> None:
        """Execute the actual restart operation after confirmation (#133).

        Sends Ctrl-C to kill the current Claude process, then restarts it
        with the same configuration (directory, permissions).
        """
        import os
        session = focused.session
        session_name = session.name

        # Build the claude command based on permissiveness mode
        claude_command = os.environ.get("CLAUDE_COMMAND", "claude")
        if claude_command == "claude":
            cmd_parts = ["claude", "code"]
        else:
            cmd_parts = [claude_command]

        if session.permissiveness_mode == "bypass":
            cmd_parts.append("--dangerously-skip-permissions")
        elif session.permissiveness_mode == "permissive":
            cmd_parts.extend(["--permission-mode", "dontAsk"])

        cmd_str = " ".join(cmd_parts)

        # Get tmux manager
        from .tmux_manager import TmuxManager
        tmux = TmuxManager(self.tmux_session)

        # Send Ctrl-C to kill the current process
        if not tmux.send_keys(session.tmux_window, "C-c", enter=False):
            self.notify(f"Failed to send Ctrl-C to '{session_name}'", severity="error")
            return

        # Brief delay to allow process to terminate
        import time
        time.sleep(0.5)

        # Send the claude command to restart
        if tmux.send_keys(session.tmux_window, cmd_str, enter=True):
            self.notify(f"Restarted agent: {session_name}", severity="information")
            # Reset session stats for fresh start
            self.session_manager.update_stats(
                session.id,
                current_task="Restarting..."
            )
            # Clear the claude session IDs since this is a new claude instance
            self.session_manager.update_session(session.id, claude_session_ids=[])
        else:
            self.notify(f"Failed to restart agent: {session_name}", severity="error")

    def action_open_column_config(self) -> None:
        """Open the column configuration modal (#178)."""
        try:
            modal = self.query_one("#summary-config-modal", SummaryConfigModal)
            # Save original state for cancel
            self._column_config_original_detail = self._prefs.summary_detail
            self._column_config_original_index = self.summary_level_index
            # Switch to custom mode immediately so live summary lines update
            self._prefs.summary_detail = "custom"
            self.summary_level_index = self.SUMMARY_LEVELS.index("custom")
            for widget in self.query(SessionSummary):
                widget.summary_detail = "custom"
                widget.summary_groups = self._prefs.summary_groups
            modal.show(self._prefs.summary_groups, self)
        except NoMatches:
            pass

    def on_summary_config_modal_config_changed(self, message: SummaryConfigModal.ConfigChanged) -> None:
        """Handle column configuration changes from modal (#178)."""
        self._prefs.summary_groups = message.summary_groups
        # Switch to "custom" summary detail level
        self._prefs.summary_detail = "custom"
        self.summary_level_index = self.SUMMARY_LEVELS.index("custom")
        self._save_prefs()

        # Update all session widgets with new group visibility and custom mode
        for widget in self.query(SessionSummary):
            widget.summary_groups = message.summary_groups
            widget.summary_detail = "custom"
            widget.refresh()

        self.notify("Custom column config saved (press 's' to cycle modes)", severity="information")

    def on_summary_config_modal_cancelled(self, message: SummaryConfigModal.Cancelled) -> None:
        """Handle modal cancellation (#178)."""
        # Restore original detail level
        if hasattr(self, '_column_config_original_detail'):
            self._prefs.summary_detail = self._column_config_original_detail
            self.summary_level_index = self._column_config_original_index
            for widget in self.query(SessionSummary):
                widget.summary_detail = self._column_config_original_detail
                widget.refresh()

    def on_key(self, event: events.Key) -> None:
        """Signal activity to daemon on any keypress."""
        signal_activity(self.tmux_session)

        # Auto-recover if focus was lost (e.g., after tabbing back to terminal)
        if self.focused is None:
            widget = self._get_focused_widget()
            if widget is not None:
                widget.focus()

        # Handle Escape to close fullscreen preview
        try:
            from .tui_widgets import FullscreenPreview
            fs_preview = self.query_one("#fullscreen-preview", FullscreenPreview)
            if fs_preview.has_class("visible") and event.key == "escape":
                fs_preview.hide()
                event.stop()
                return
        except Exception:
            pass

        # Handle Escape to close help overlay (#175)
        try:
            from .tui_widgets import HelpOverlay
            help_overlay = self.query_one("#help-overlay", HelpOverlay)
            if help_overlay.has_class("visible") and event.key == "escape":
                help_overlay.remove_class("visible")
                event.stop()
        except Exception:
            pass

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        """Check if an action should be allowed (#175).

        When help overlay is visible, only allow help toggle and quit.
        Other actions are blocked - pressing those keys just closes help.
        """
        # Block actions when fullscreen preview is visible
        try:
            from .tui_widgets import FullscreenPreview
            fs_preview = self.query_one("#fullscreen-preview", FullscreenPreview)
            if fs_preview.has_class("visible"):
                if action in ("expand_preview", "quit"):
                    return True
                fs_preview.hide()
                return False
        except Exception:
            pass

        # Only intercept when help is visible
        try:
            from .tui_widgets import HelpOverlay
            help_overlay = self.query_one("#help-overlay", HelpOverlay)
            if help_overlay.has_class("visible"):
                # Allow these actions when help is visible
                if action in ("toggle_help", "quit"):
                    return True
                # Block all other actions - close help instead
                help_overlay.remove_class("visible")
                return False
        except Exception:
            pass
        # Default: allow the action
        return True

    def on_unmount(self) -> None:
        """Clean up terminal state on exit"""
        import sys
        # Stop the summarizer (release API client resources)
        self._summarizer.stop()

        # Ensure mouse tracking is disabled
        sys.stdout.write('\033[?1000l')  # Disable mouse tracking
        sys.stdout.write('\033[?1002l')  # Disable cell motion tracking
        sys.stdout.write('\033[?1003l')  # Disable all motion tracking
        sys.stdout.flush()


def run_tui(tmux_session: str = "agents", diagnostics: bool = False):
    """Run the TUI supervisor"""
    import os
    import sys

    # Ensure we're using a proper terminal
    if not sys.stdout.isatty():
        print("Error: Must run in a TTY terminal", file=sys.stderr)
        sys.exit(1)

    # Force terminal size detection
    os.environ.setdefault('TERM', 'xterm-256color')

    app = SupervisorTUI(tmux_session, diagnostics=diagnostics)
    # Use driver=None to auto-detect, and size will be detected from terminal
    app.run()


if __name__ == "__main__":
    import sys
    tmux_session = sys.argv[1] if len(sys.argv) > 1 else "agents"
    run_tui(tmux_session)
