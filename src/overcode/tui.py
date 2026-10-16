"""
Textual TUI for Overcode monitor: the SupervisorTUI app and run_tui.

The app is the top pane of the `overcode tmux` split and a view of the
engine. What it is made of:

- tui_timers: the periodic timer cadences and the scheduler every timer
  starts through (app.periodic);
- tui_engine_link: the engine subscription and applying what it publishes
  (app.engine_link);
- tui_split: every tmux side effect, a null object outside the split
  (app.split);
- tui_actions/: key-bound actions and message handlers, as mixins (Textual
  dispatches actions and messages on the App): navigation, view, daemon,
  session, input, jobs, sisters, command bar, dialogs (incl. the palette),
  diagnostics, activity, view control, overagent/journey and mentor;
- tui_widgets/: the widgets.

What stays here: composition and mount, the attended state, the focused
pane's capture loop, the session list and its rows, the status bar and
timeline refresh, focus, and the key/quit/unmount plumbing.
"""

from typing import List, Optional
import logging
import sys
import threading
import time

from textual.app import App, ComposeResult
from textual.containers import ScrollableContainer
from textual.widgets import Header, Static, Input, TextArea
from textual.widget import MountError
from textual.reactive import reactive
from textual.css.query import NoMatches
from textual import events, work
from rich.cells import cell_len
from rich.text import Text

from . import __version__, get_dev_version_suffix
from .session_manager import SessionManager, Session
from .job_manager import Job, JobManager
from .job_launcher import JobLauncher
from .launcher import AgentLauncher
from .status_detector_factory import StatusDetectorDispatcher
from .status_constants import DEFAULT_CAPTURE_LINES
from .settings import signal_activity, write_tui_heartbeat, get_event_loop_timing_path, get_status_changes_path, TUIPreferences  # Activity signaling to daemon
from .monitor_daemon_state import get_monitor_daemon_state
from .tui_split import make_split
from .tui_engine_link import (
    ENGINE_BANNER_GRACE_SECONDS,  # noqa: F401 (re-exported for tests)
    ENGINE_BANNER_TEXT,
    EngineBellRang,
    EngineConnectionChanged,
    EngineLink,
    EngineSnapshotReady,
)
from .monitor_daemon import (
    is_monitor_daemon_running,
)
from .pid_utils import spawn_daemon
from .tmux_utils import (
    query_pane_attended,
    tui_pane_target,
    tui_tmux_socket,
)
from .summarizer_component import (
    SummarizerComponent,
    SummarizerConfig,
    AgentSummary,
)
from .sister_poller import SisterPoller
from .usage_monitor import UsageMonitor
from .worker_guard import single_flight, worker_cancelled
from .summary_columns import FleetFlags, compute_fleet_flags
from .tui_logic import (
    sort_sessions,
    filter_visible_sessions,
    compute_child_counts,
    compute_tree_metadata,
    compute_session_widget_diff,
)
from .tui_widgets.daemon_status_bar import LocalBarState, read_local_state
from .tui_widgets.status_timeline import read_history_data, read_swarm_data
from .tui_widgets import (
    HelpOverlay,
    PreviewPane,
    DaemonPanel,
    TuiLogPanel,
    DaemonStatusBar,
    StatusTimeline,
    SessionSummary,
    CommandBar,
    SummaryConfigModal,
    NewAgentDefaultsModal,
    SkillsModal,
    TmuxConfigModal,
    PassthruConfigModal,
    NewAgentModal,
    RenameAgentModal,
    BillingCodeModal,
    SummaryPromptLab,
    JourneyPanel,
    AgentSelectModal,
    SisterSelectionModal,
    InstructionHistoryModal,
    CommandPalette,
    ColumnHeader,
)
from .tui_widgets.swarm_rows import SwarmRows
from .tui_actions import (
    NavigationActionsMixin,
    ViewActionsMixin,
    DaemonActionsMixin,
    SessionActionsMixin,
    InputActionsMixin,
    SwarmMixin,
)
from .tui_actions.activity import ActivityMixin
from .tui_actions.claude_bg import BackgroundSessionsMixin
from .tui_actions.view_control import ViewControlMixin
from .tui_actions.overagent import JourneyMixin, OveragentMixin
from .tui_actions.mentor import MentorMixin
from .tui_actions.jobs import JobsMixin
from .tui_actions.sisters import SistersMixin
from .tui_actions.command_bar import CommandBarMixin
from .tui_actions.dialogs import DialogsMixin
from .tui_actions.diagnostics import (  # noqa: F401 (the cap is re-exported for tests)
    HEARTBEAT_LOG_MAX_ENTRIES,
    DiagnosticsMixin,
)
# The timer tables live in tui_timers (re-exported: tests import them from here)
from .tui_timers import (  # noqa: F401
    PAUSED_WHEN_UNATTENDED,
    TIMER_INTERVALS,
    TIMER_PHASE_OFFSETS,
    PeriodicScheduler,
)


def session_row_key(sessions) -> tuple:
    """What decides the session list's rows and their order, per agent.

    Membership, and the fields row filtering and sorting read (status,
    asleep, tags, parent, name). Two lists with the same key show the same
    rows, so the focused-pane tick re-applies the list only when it moves.
    """
    return tuple(
        (s.id, getattr(s, "name", None), getattr(s, "status", None),
         bool(getattr(s, "is_asleep", False)),
         tuple(getattr(s, "tags", None) or ()), getattr(s, "parent_session_id", None))
        for s in sessions
    )


# An agent that needs a look while it is the focused one (on screen in the
# split's bottom pane) counts as visited after this long.
BELL_SEEN_AFTER_SECONDS = 5.0


REMOTE_VISIT_DAYS = 7  # a sister agent's visit is kept this long


def remote_visits(prefs) -> dict:
    """The persisted visits to sister agents, ignoring anything malformed."""
    saved = getattr(prefs, "remote_visits", None)
    if not isinstance(saved, dict):
        return {}
    return {sid: float(at) for sid, at in saved.items()
            if isinstance(sid, str) and sid.startswith("remote:")
            and isinstance(at, (int, float)) and not isinstance(at, bool)}


class SupervisorTUI(
    SwarmMixin,
    BackgroundSessionsMixin,
    ActivityMixin,
    ViewControlMixin,
    OveragentMixin,
    JourneyMixin,
    MentorMixin,
    NavigationActionsMixin,
    ViewActionsMixin,
    DaemonActionsMixin,
    SessionActionsMixin,
    InputActionsMixin,
    JobsMixin,
    SistersMixin,
    CommandBarMixin,
    DialogsMixin,
    DiagnosticsMixin,
    App,
):
    """Overcode Supervisor TUI"""

    # Disable any size restrictions
    AUTO_FOCUS = None

    # Free Ctrl+P for our own palette (#420, #482). Textual's built-in
    # App opens its system command palette on Ctrl+P, which would otherwise
    # shadow the binding before it reaches us.
    ENABLE_COMMAND_PALETTE = False

    # Load CSS from external file
    CSS_PATH = "tui.tcss"


    # The default keys (#510): the `default` key preset IS this list. Presets
    # (data/keymaps/*.yaml) and the user's `keys:` config are deltas applied
    # on top at startup (keymap.py → apply_keymap), so add new keys here as
    # plain tuples. Read keys through self.keymap, never this class attribute.
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("h", "toggle_help", "Help"),
        ("question_mark", "toggle_help", "Help"),
        ("d", "toggle_daemon", "Daemon panel"),
        ("O", "toggle_tui_log", "TUI logs"),
        ("t", "toggle_timeline", "Toggle timeline"),
        ("s", "cycle_summary", "Summary detail"),
        ("c", "sync_to_main_and_clear", "Sync main+clear"),
        # Navigation between agents
        ("j", "focus_next_session", "Next"),
        ("k", "focus_previous_session", "Prev"),
        ("down", "focus_next_session", "Next"),
        ("up", "focus_previous_session", "Prev"),
        # The split's agent-pane keys work in the TUI pane too
        ("alt+j", "focus_next_session", "Next"),
        ("alt+k", "focus_previous_session", "Prev"),
        # Command bar (send instructions to agents)
        ("i", "focus_command_bar", "Send"),
        ("colon", "focus_command_bar", "Send"),
        ("o", "focus_standing_orders", "Standing orders"),
        # Daemon controls (simple keys that work everywhere)
        ("left_square_bracket", "supervisor_start", "Start supervisor"),
        ("right_square_bracket", "supervisor_stop", "Stop supervisor"),
        ("backslash", "monitor_restart", "Restart monitor"),
        ("a", "focus_human_annotation", "Annotation"),
        ("A", "toggle_summarizer", "AI summarizer"),
        # Resize focused agent's tmux window to match pane size
        ("r", "resize_focused_window", "Resize pane"),
        # Agent management
        ("x", "kill_focused", "Kill/Clean up"),
        ("R", "restart_focused", "Restart agent"),
        ("n", "new_agent", "New agent"),
        ("e", "open_overagent", "Overagent"),
        ("u", "open_journey", "Journey"),
        ("ctrl+n", "rename_focused", "Rename agent"),
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
        # Ctrl+O forwarded to focused agent by default (#446)
        ("ctrl+o", "send_ctrl_o_to_focused", "Send Ctrl+O"),
        # Passthru key configuration modal (#446)
        ("ctrl+k", "open_passthru_config", "Passthru keys"),
        # Copy mode - disable mouse capture for native terminal selection
        ("y", "toggle_copy_mode", "Copy mode"),
        # Heartbeat pause/resume toggle (#265) - promoted to lowercase
        ("p", "toggle_heartbeat_pause", "Pause heartbeat"),
        # API server (sister API) toggle
        ("w", "toggle_web_server", "API server (sisters)"),
        # Sleep mode toggle - mark agent as paused (excluded from stats)
        ("z", "toggle_sleep", "Sleep mode"),
        # Show terminated/killed sessions (ghost mode)
        ("g", "toggle_show_terminated", "Show killed"),
        # Jump to sessions needing attention (bell/red)
        ("b", "jump_to_attention", "Jump attention"),
        # VSCode-style jump-to-agent by name (#420); `>` in it lists commands
        ("ctrl+p", "jump_to_agent", "Jump to agent"),
        # Command palette: search every command, see its key and state (#482)
        ("slash", "command_palette", "Commands"),
        # Filter agents by tag (#357). With no tags currently in use this
        # is a no-op; otherwise opens the same fuzzy modal seeded with the
        # set of tags so the user can pick one. Press T again with the
        # filter active and pick `(clear)` to remove.
        ("T", "filter_by_tag", "Filter by tag"),
        # Cycle focal repo for the focused agent (#170). No-op when the
        # agent's start_directory is a single repo.
        ("ctrl+r", "cycle_focal_repo", "Cycle focal repo"),
        # Hide sleeping agents from display
        ("Z", "toggle_hide_asleep", "Hide sleeping"),
        # Show/hide done child agents (#244)
        ("D", "toggle_show_done", "Show done"),
        # Collapse/expand children in tree view (#244)
        ("X", "toggle_collapse_children", "Collapse children"),
        # Delegated work as read-only rows under its parent (0.6.1 phase 2)
        ("right", "expand_swarm", "Expand delegated work"),
        ("left", "collapse_swarm", "Collapse delegated work"),
        # Sort picker: the palette on sort choices (#61, #487)
        ("S", "choose_sort", "Sort by…"),
        # Edit agent value (#61)
        ("V", "edit_agent_value", "Edit value"),
        # Cost budget (#173)
        ("B", "edit_cost_budget", "Cost budget"),
        # Cycle summary content mode (#74)
        ("l", "cycle_summary_content", "Summary content"),
        # Split resize
        ("equals_sign", "split_shrink", "Split shrink"),
        ("minus", "split_grow", "Split grow"),
        # Baseline time adjustment for mean spin calculation
        ("comma", "baseline_back", "Baseline -15m"),
        ("full_stop", "baseline_forward", "Baseline +15m"),
        ("0", "baseline_reset", "Reset baseline"),
        # Timeline scope cycle (#191)
        ("less_than_sign", "cycle_timeline_hours", "Timeline scope"),
        # Monochrome mode for terminals with ANSI issues (#138)
        ("M", "toggle_monochrome", "Monochrome"),
        # Light / dark colour theme (#508)
        ("Y", "toggle_theme", "Light/dark theme"),
        # Emoji-free mode for terminals without emoji fonts (#315)
        ("E", "toggle_emoji_free", "Emoji-free"),
        # Cycle between token count, dollar cost, and joules display
        ("dollar_sign", "toggle_cost_display", "Cycle $/⚡"),
        # Heartbeat configuration (#171)
        ("H", "configure_heartbeat", "Heartbeat config"),
        # Fork agent - create child with source's conversation context (#347)
        ("F", "fork_focused", "Fork agent"),
        # Enhanced context toggle - per-agent context injection hook
        ("ctrl+t", "toggle_enhanced_context", "Enhanced context"),
        # Hook-based status detection toggle (#5)
        ("K", "toggle_hook_detection", "Agent detection"),
        # Column configuration modal (#178)
        ("C", "open_column_config", "Columns"),
        # Column headers toggle
        ("L", "toggle_column_headers", "Column headers"),
        # New agent defaults modal
        ("G", "open_new_agent_defaults", "Agent defaults"),
        ("W", "open_skills", "Skill profiles"),
        # Tmux pane-toggle key modal (#442)
        ("ctrl+g", "open_tmux_config", "Tmux toggle key"),
        # Sister selection modal (#323)
        ("U", "open_sister_selection", "Sisters"),
        # Instruction history modal (#376)
        ("I", "open_instruction_history", "Instruction history"),
        # Jobs mode toggle
        ("J", "toggle_tui_mode", "Jobs"),
    ]

    # Timeline scope presets in hours (#191)
    TIMELINE_PRESETS = [1, 3, 6, 12, 24]
    # Summary detail levels: low (minimal), med (timing), high (all metrics), full (everything)
    SUMMARY_LEVELS = ["low", "med", "high", "full"]
    # Summary content modes: what to show in the summary line (#74)
    SUMMARY_CONTENT_MODES = ["ai_short", "ai_long", "orders", "annotation", "heartbeat", "last_command"]

    sessions: reactive[List[Session]] = reactive(list)
    focused_session_index: reactive[int] = reactive(0, always_update=True)
    # The preview pane shows only for a sister agent or in jobs view; local
    # agents' terminals are the split's bottom pane.
    preview_visible: reactive[bool] = reactive(False)
    show_terminated: reactive[bool] = reactive(False)  # show killed sessions in timeline
    hide_asleep: reactive[bool] = reactive(False)  # hide sleeping agents from display
    # Tag filter (#357): when set, only agents whose `tags` contains the
    # value are displayed. None disables the filter.
    tag_filter: reactive[Optional[str]] = reactive(None)
    show_done: reactive[bool] = reactive(False)  # show "done" child agents (#244)
    summary_content_mode: reactive[str] = reactive("ai_short")  # what to show in summary (#74)
    baseline_minutes: reactive[int] = reactive(0)  # 0=now, 15/30/.../180 = minutes back for mean spin
    monochrome: reactive[bool] = reactive(False)  # B&W mode for terminals with ANSI issues (#138)
    emoji_free: reactive[bool] = reactive(False)  # ASCII fallbacks for emoji (#315)
    ui_theme: str = "dark"  # "dark" | "light" (#508); App.theme is Textual's own
    show_cost: reactive[str] = reactive("tokens")  # "tokens", "cost", "joules" — cycle with $
    tui_mode: reactive[str] = reactive("agents")  # "agents" | "jobs"
    focused_job_index: reactive[int] = reactive(0, always_update=True)
    jobs: reactive[List[Job]] = reactive(list)

    def __init__(self, tmux_session: str = "agents", diagnostics: bool = False,
                 initial_jobs_mode: bool = False, sync_target: Optional[str] = None,
                 timers: bool = True):
        super().__init__()
        self.tmux_session = tmux_session
        # The linked session the split's bottom pane shows (`overcode tmux`
        # passes it). Navigation switches its window; see in_split.
        self.tmux_sync_target: Optional[str] = sync_target
        # Effective keys (#510): BINDINGS + key preset + config overrides.
        from .keymap import effective_keymap
        self.apply_keymap(effective_keymap(), refresh=False)
        self._init_activity()  # usage log (#483)
        self._init_view_control()  # overcode view (#484)
        self._init_mentor()  # occasional tips, off by default (#483)
        self.diagnostics = diagnostics  # Disable all auto-refresh timers
        self._initial_jobs_mode = initial_jobs_mode  # Start in jobs view
        self._sister_zoom_active = False  # True when zoomed for a remote/sister agent view
        self.session_manager = SessionManager()
        # One manager per process: the launcher's reads share the app's
        # stat-gated snapshot instead of parsing sessions.json a second time.
        self.launcher = AgentLauncher(tmux_session, session_manager=self.session_manager)
        from .settings import resolve_detection_mode
        detection_mode = resolve_detection_mode(tmux_session)
        self.detector = StatusDetectorDispatcher(tmux_session, mode=detection_mode)
        # Track collapsed parents in tree view (#244)
        self.collapsed_parents: set[str] = set()
        # Delegated work expanded into virtual rows (tui_actions/swarm.py)
        self._init_swarm_state()
        # Max repo/branch/name widths for alignment in full detail mode
        self.max_repo_width: int = 10
        self.max_branch_width: int = 10
        self.max_name_width: int = 10
        self.all_names_match_repos: bool = False
        self.column_widths: list = []  # Per-cell column widths for alignment
        # {column id: value} for columns every row shows the same value in —
        # hidden, with the value noted in the header row (uniform_columns)
        self.uniform_columns: dict = {}
        # Which fleet-gated columns show (budget, PR, model, backend…), from
        # every row; each row holds this one value (_update_fleet_flags)
        self.fleet = FleetFlags()
        self._column_widths_dirty: bool = True  # Recompute on first render
        # Live overrides while the C-modal is open — header/width lookups use
        # these instead of the persisted prefs so toggling updates everything
        # in sync (#449).
        self._live_column_overrides: Optional[dict] = None

        # Load persisted TUI preferences
        self._prefs = TUIPreferences.load(tmux_session)
        if self.in_split:
            # The top pane is short: the timeline starts hidden (t shows it)
            self._prefs.timeline_visible = False
        # Folded parents survive a TUI restart (#464)
        self.collapsed_parents = set(self._prefs.collapsed_parents)

        # Current summary detail level index (cycles through SUMMARY_LEVELS)
        # Initialize from saved preferences
        try:
            self.summary_level_index = self.SUMMARY_LEVELS.index(self._prefs.summary_detail)
        except ValueError:
            self.summary_level_index = 0  # Default to "low"

        # Suppress focus watcher during command bar interaction etc.
        self._suppress_focus_watcher = False
        # Timers for auto-dismissing bell when the stalled agent is already focused
        self._bell_dismiss_timers: dict[str, object] = {}
        # The engine (docs/design/engine-0.6.md): this TUI subscribes to
        # engine.sock and renders what it publishes (tui_engine_link). The
        # client is started on mount.
        self.engine_link = EngineLink(self)
        # Visits this TUI made that the engine may not have published yet,
        # and visits to sister agents (which no engine here records), kept
        # in the prefs so a restart does not bring their 🔔 back
        self._visited_here: dict[str, float] = remote_visits(self._prefs)
        # The focused-pane capture in flight (one at a time, never queued),
        # and the one long-lived thread that does it: the 250 ms tick sets
        # _capture_request and the Event, the thread posts a FocusedCapture
        # back (started on the first tick, stopped on unmount)
        self._focused_capture_in_flight = False
        self._capture_request: tuple = (None, None)
        self._capture_wake = threading.Event()
        self._capture_stop = threading.Event()
        self._capture_thread: Optional[threading.Thread] = None
        # The session table every row was last given by a capture (None:
        # rows may since hold other objects); see _apply_focused_capture
        self._capture_fresh_applied: Optional[dict] = None
        # Sisters' agents: the input-needed stretch each was last seen in,
        # so a new one notifies (sisters stay on HTTP polling, no bell message)
        self._remote_stretch_seen: dict[str, Optional[float]] = {}
        # Track whether sessions have been loaded at least once (for startup sequencing)
        self._initial_sessions_loaded = False
        # Track attention jump state (for 'b' key cycling)
        self._attention_jump_index = 0
        self._attention_jump_list: list = []  # Cached list of sessions needing attention
        # Instruction history for reinject modal (#376)
        self._instruction_history: list = []
        # Pending double-press confirmations: action_key -> (session_name | None, timestamp)
        self._pending_confirmations: dict[str, tuple[str | None, float]] = {}
        # Attended state: whether a tmux client is attached to the pane this
        # TUI runs in. False pauses every timer in PAUSED_WHEN_UNATTENDED
        # (_on_attended_changed is the one place that reacts). Written only
        # through _set_attended. run_tui only runs inside tmux; an app with
        # no pane (unit tests) stays attended.
        self.attended: bool = True
        self._tui_tmux_pane: Optional[str] = tui_pane_target()
        # This pane's own server, which the poll addresses explicitly (a
        # pane id means nothing on another server).
        self._tui_tmux_socket: Optional[str] = tui_tmux_socket()
        self._tui_tmux_session: Optional[str] = None  # learned from the first poll
        # Every periodic timer (TIMER_INTERVALS names), for pause/resume.
        # timers=False only in tests (tests/tui_harness.py): no periodic
        # timer starts, and the test calls the tick methods itself.
        self.periodic = PeriodicScheduler(self, enabled=timers)
        # Flag: set by user navigation (j/k), cleared after sync.
        # Prevents programmatic focused_session_index changes (from
        # refresh_sessions) from switching the bottom pane.
        self._user_navigated: bool = False
        # Flag: True after the first tmux-sync has fired on startup. Without
        # this, the `_user_navigated` guard in watch_focused_session_index
        # leaves the bottom pane stuck on whatever window tmux picked when
        # the split was opened.
        self._initial_tmux_sync_done: bool = False
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
        # Initialize emoji_free from preferences (#315)
        self.emoji_free = self._prefs.emoji_free
        # Colour theme from preferences (#508)
        from .tui_theme import LightThemeFilter, normalize_theme
        self._light_filter = LightThemeFilter()
        self.ui_theme = normalize_theme(self._prefs.theme)
        self._apply_ui_theme()
        # Initialize show_cost from preferences
        self.show_cost = self._prefs.show_cost
        # macOS notification integration (#235)
        from .notifier import MacNotifier
        self._notifier = MacNotifier(mode=self._prefs.notifications)
        # Cache of terminated sessions (killed during this TUI session)
        self._terminated_sessions: dict[str, Session] = {}
        # session_row_key of the local session list last applied: the
        # focused-pane tick applies a new list when this no longer matches
        self._session_row_key: tuple = ()
        # The local session list last applied, and the 🌙 rows (adopted
        # Claude background sessions, built from the engine's views) with
        # the claude_bg.adopted_key they were built for
        self._local_sessions: list = []
        self._adopted_sessions: list = []
        self._adopted_key: tuple = ()
        self._terminated_times: dict[str, float] = {}  # session_id -> monotonic time
        self._TERMINATED_GC_SECONDS: float = 600.0  # 10 minutes

        # Usage monitor (Claude Code subscription limits)
        self._usage_monitor = UsageMonitor()

        # AI Summarizer - owned by TUI, not daemon (zero cost when TUI closed)
        from .config import get_summarizer_config as _get_sum_cfg
        _sum_cfg = _get_sum_cfg()
        self._summarizer = SummarizerComponent(
            tmux_session=tmux_session,
            config=SummarizerConfig(enabled=False, cost_cap=_sum_cfg.get("cost_cap", 100.0)),
        )
        self._summaries: dict[str, AgentSummary] = {}

        # Jobs mode — tracked bash jobs in a separate tmux session
        self._job_manager = JobManager()
        self._job_launcher = JobLauncher(job_manager=self._job_manager)
        self._job_counts: dict = {}  # agent id -> running jobs it launched (#463)

        # Sister integration (#245) - remote agent monitoring + control
        self._sister_poller = SisterPoller()
        self.has_sisters: bool = self._sister_poller.has_sisters
        self.local_hostname: str = self._sister_poller.local_hostname
        self._remote_sessions: List[Session] = []
        from .sister_controller import SisterController
        self._sister_controller = SisterController()

        # Pre-load session list synchronously so first render has data immediately
        try:
            self._preloaded_sessions: list | None = self.launcher.list_sessions()
        except Exception:
            self._preloaded_sessions = None

        # Event loop heartbeat probe — measures event loop responsiveness.
        # Diagnostic-only (#465): enabled by default but a config knob can
        # turn it off, and the CSV is hard-capped as a backstop either way.
        from .config import get_history_retention_config
        _history_cfg = get_history_retention_config()
        self._heartbeat_last: float = 0.0  # monotonic timestamp of last tick
        self._heartbeat_log: list = []  # buffered (iso_timestamp, delta_ms, event) tuples
        self._heartbeat_csv_path = get_event_loop_timing_path(tmux_session)
        self._heartbeat_enabled = _history_cfg["event_loop_timing_enabled"]
        self._heartbeat_cap_mb = _history_cfg["event_loop_timing_cap_mb"]

        # Status change diagnostic log — tracks every per-agent status transition
        self._status_change_log: list = []
        self._status_change_csv_path = get_status_changes_path(tmux_session)

    def _terminal_active_banner_text(self) -> str:
        """Build the TERMINAL ACTIVE banner with the configured toggle key."""
        from .config import get_tmux_toggle_key
        from .cli.split import TOGGLE_KEY_CHOICES, DEFAULT_TOGGLE_KEY
        key = get_tmux_toggle_key() or DEFAULT_TOGGLE_KEY
        label = next((lbl for lbl, k in TOGGLE_KEY_CHOICES if k == key), key)
        return f"  ↓ TERMINAL ACTIVE — {label} to return ↓  "

    def compose(self) -> ComposeResult:
        """Create child widgets"""
        yield Header(show_clock=True)
        yield DaemonStatusBar(tmux_session=self.tmux_session, id="daemon-status")
        yield Static(ENGINE_BANNER_TEXT, id="engine-banner")
        yield StatusTimeline([], tmux_session=self.tmux_session, id="timeline")
        yield DaemonPanel(tmux_session=self.tmux_session, id="daemon-panel")
        yield TuiLogPanel(tmux_session=self.tmux_session, id="tui-log-panel")
        yield ColumnHeader("", id="column-headers")
        yield ScrollableContainer(id="sessions-container")
        yield ScrollableContainer(id="jobs-container")
        yield PreviewPane(id="preview-pane")
        yield Static(self._terminal_active_banner_text(), id="terminal-active-banner")
        yield CommandBar(id="command-bar")
        # Modal for column configuration (positioned programmatically)
        yield SummaryConfigModal(id="summary-config-modal", classes="modal")
        # Modal for new-agent defaults
        yield NewAgentDefaultsModal(id="new-agent-defaults-modal", classes="modal")
        yield SkillsModal(id="skills-modal", classes="modal")
        # Modal for tmux pane-toggle key (#442)
        yield TmuxConfigModal(id="tmux-config-modal", classes="modal")
        # Modal for passthru key configuration (#446)
        yield PassthruConfigModal(id="passthru-config-modal", classes="modal")
        # Modal for new agent creation (unified form)
        yield NewAgentModal(id="new-agent-modal", classes="modal")
        yield RenameAgentModal(id="rename-agent-modal", classes="modal")
        yield BillingCodeModal(id="billing-code-modal", classes="modal")
        # Summarizer prompt editor with live results (#491)
        yield SummaryPromptLab(id="summary-prompt-lab", classes="modal")
        # The learning journey (#483)
        yield JourneyPanel(id="journey-panel", classes="modal")
        # Modal for agent selection during new agent creation
        yield AgentSelectModal(id="agent-select-modal", classes="modal")
        # Modal for sister instance visibility (#323)
        yield SisterSelectionModal(id="sister-selection-modal", classes="modal")
        # Modal for instruction history (#376)
        yield InstructionHistoryModal(id="instruction-history-modal", classes="modal")
        # Command palette: jump to agent, filter by tag, run commands (#420, #482)
        yield CommandPalette(id="command-palette", classes="modal")
        yield HelpOverlay(id="help-overlay")
        yield Static(
            self._build_footer_text(),
            id="help-text"
        )

    # -- keymap (#510) --------------------------------------------------------

    def apply_keymap(self, km, refresh: bool = True) -> None:
        """Run with keymap `km`: rebind the app and every keyed widget live."""
        from .keymap import apply_to_widget, set_active, textual_bindings_map
        self.keymap = km
        set_active(km)
        self._bindings = textual_bindings_map(type(self), km.bindings("app"))
        if not refresh:
            return
        from .tui_widgets.summary_prompt_lab import SummaryPromptLab
        for w in self.query(SummaryPromptLab):
            apply_to_widget(w, "summary_prompt_lab", km)
        try:
            self.query_one("#command-bar", CommandBar).refresh_key_hint()
        except NoMatches:
            pass
        self._update_footer()
        try:
            self.query_one("#help-overlay").refresh_content()
        except Exception:
            pass
        self.refresh_bindings()

    def _effective_keymap(self):
        from .keymap import keymap_of
        return keymap_of(self)

    def action_cycle_key_preset(self) -> None:
        """Switch to the next key preset, save it, and rebind live (#510)."""
        from .keymap import effective_keymap, list_presets, set_configured_preset
        presets = list_presets()
        current = self._effective_keymap().preset
        nxt = presets[(presets.index(current) + 1) % len(presets)] if current in presets else presets[0]
        try:
            set_configured_preset(nxt)
        except Exception as e:
            self.notify(f"Could not save key preset: {e}", severity="error")
            return
        km = effective_keymap(preset=nxt)
        self.apply_keymap(km)
        note = f" — {len(km.warnings)} warning(s): overcode keys --conflicts" if km.warnings else ""
        self.notify(f"Key preset: {nxt}{note}", severity="warning" if km.warnings else "information")

    def on_mount(self) -> None:
        """Called when app starts"""
        self.title = f"Overcode v{__version__}{get_dev_version_suffix()}"
        self._update_subtitle()
        self._update_capture_lines()

        # Set up TUI diagnostic file logger
        from .tui_widgets.tui_log_panel import setup_tui_file_logger
        self._tui_log_handler = setup_tui_file_logger(self.tmux_session)

        # Keymap problems (#510) are warnings, never crashes: say so once.
        km_warnings = getattr(getattr(self, "keymap", None), "warnings", None)
        if km_warnings:
            self.notify(f"{len(km_warnings)} key binding warning(s) — run: overcode keys --conflicts",
                        severity="warning", timeout=10)

        # Auto-start Monitor Daemon if not running
        self.engine_link.start_attempt = time.monotonic()
        self._ensure_monitor_daemon()

        # Clean up stale SSH proxy windows from previous TUI sessions
        self.split.cleanup_stale_ssh_proxies()

        # Provision SSH-configured sisters in background
        self._provision_ssh_sisters()

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

        try:
            tui_log_panel = self.query_one("#tui-log-panel", TuiLogPanel)
            tui_log_panel.display = self._prefs.tui_log_panel_visible
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

        # Hide column headers widget initially (shown via L key)
        try:
            header_widget = self.query_one("#column-headers", Static)
            header_widget.display = self._prefs.show_column_headers
        except NoMatches:
            pass

        # Update footer with current detail level
        self._update_footer()

        # Apply pre-loaded sessions synchronously so widgets exist immediately
        if self._preloaded_sessions is not None:
            self._apply_sessions(self._preloaded_sessions)
            self._preloaded_sessions = None
        else:
            self.refresh_sessions()
        self.update_daemon_status()
        self.update_timeline()
        # Subscribe to the engine: every status, stat and bell arrives here
        self.engine_link.start()

        # Event loop heartbeat probe — on by default (negligible per-tick
        # overhead), but disable via history_retention.event_loop_timing_enabled
        # in config.yaml if you don't need it (#465).
        if self._heartbeat_enabled:
            self._heartbeat_last = time.monotonic()
            self._start_periodic("heartbeat_probe", self._record_heartbeat)
            self._start_periodic("heartbeat_flush", self._flush_heartbeat)
        if self._prefs.status_change_logging:
            self._start_periodic("status_changes", self._flush_status_changes)
        self._start_periodic("activity_flush", self._flush_activity)
        self._start_periodic("view_control", self._view_control_tick)
        # The `d` and `O` panels tail their logs each second while shown
        try:
            self._start_periodic("daemon_panel", self.query_one("#daemon-panel", DaemonPanel)._refresh_logs)
            self._start_periodic("tui_log_panel", self.query_one("#tui-log-panel", TuiLogPanel)._refresh_logs)
        except NoMatches:
            pass
        self.record_activity("tui", phase="start", version=__version__,
                             size=f"{self.size.width}x{self.size.height}")

        if self.diagnostics:
            # DIAGNOSTICS MODE: No auto-refresh timers
            self._update_subtitle()  # Will include [DIAGNOSTICS]
            self.notify(
                "DIAGNOSTICS MODE: All auto-refresh disabled. Press 'r' to manually refresh.",
                severity="warning",
                timeout=10
            )
        else:
            # Normal mode: set up all timers. Cadences and phase offsets are
            # the TIMER_INTERVALS / TIMER_PHASE_OFFSETS tables (see the note
            # there on why the long timers are de-phased).
            # Refresh session list every 10 seconds
            self._start_periodic("refresh_sessions", self.refresh_sessions)
            # The focused agent's pane, and the row clock, every 250 ms
            self._start_periodic("focused_pane", self._focused_pane_tick)
            self._start_periodic("daemon_status", self.update_daemon_status)
            # Update timeline every 30 seconds
            self._start_periodic("timeline", self.update_timeline)
            # Update AI summaries every 5 seconds (only runs if enabled)
            self._start_periodic("summarizer", self._update_summaries_async)
            # Poll sister instances every 10 seconds (only runs if configured)
            if self.has_sisters:
                self._start_periodic("sister_poll", self._poll_sisters)
                self._poll_sisters()  # Initial fetch
                # Fast poll for the focused remote agent (1.5s)
                self._start_periodic("focused_sister", self._poll_focused_sister)
            # Refresh jobs list every 5 seconds
            self._start_periodic("refresh_jobs", self._refresh_jobs)
            # Refresh focused job pane content every second
            self._start_periodic("focused_job_pane", self._poll_focused_job_pane)
            # Periodically reconcile nested agent tmux windows with the outer
            # pane size — on_resize catches most changes, but tmux auto-resize
            # misfires after splits/zooms leave windows stuck at the old size.
            self._start_periodic("agent_resize", self._periodic_agent_resize)
            # Unattended low-power mode: once a second ask tmux whether a
            # client is attached to this pane; the engine is told (visible).
            self._start_periodic("attended_watch", self._attended_watch_tick)

        # Apply initial jobs mode if requested (e.g. --jobs flag)
        if self._initial_jobs_mode:
            self.tui_mode = "jobs"

    @property
    def periodic(self) -> PeriodicScheduler:
        """The app's PeriodicScheduler (made on first use for bare test apps)."""
        scheduler = self.__dict__.get("_periodic")
        if scheduler is None:
            scheduler = PeriodicScheduler(self)
            self.__dict__["_periodic"] = scheduler
        return scheduler

    @periodic.setter
    def periodic(self, scheduler: PeriodicScheduler) -> None:
        self.__dict__["_periodic"] = scheduler

    @property
    def _periodic_timers(self) -> dict:
        """Every started periodic Timer by TIMER_INTERVALS name."""
        return self.periodic.timers

    @_periodic_timers.setter
    def _periodic_timers(self, timers: dict) -> None:
        self.periodic.timers = timers

    def _start_periodic(self, name: str, callback) -> None:
        """Start the ``name`` timer through the scheduler (tui_timers)."""
        self.periodic.start(name, callback)

    # ── Unattended low-power mode ──────────────────────────────────────

    def _attended_watch_tick(self) -> None:
        """Once a second: ask this pane's own tmux server whether a client is attached.

        One ``display-message`` to the server the pane lives on. With no
        pane (unit tests) there is nothing to ask and the state stays
        attended. The engine hears about every change (``visible``).
        """
        if self._tui_tmux_pane is not None:
            self._poll_attended_async()

    @work(thread=True, group="attended_watch")
    @single_flight("attended_watch")
    def _poll_attended_async(self) -> None:
        """Worker: one tmux command to this pane's own server, applied on the main thread."""
        result = query_pane_attended(self._tui_tmux_pane, socket_path=self._tui_tmux_socket)
        self.call_from_thread(self._apply_attended_poll, result)

    def _apply_attended_poll(self, result: Optional[tuple]) -> None:
        """Main thread: a poll answer; None (tmux could not answer) changes nothing."""
        if result is None:
            return
        session_name, attached = result
        self._tui_tmux_session = session_name
        self._set_attended(attached > 0)

    def _set_attended(self, attended: bool) -> None:
        """The single write to the attended state; the watcher does the rest."""
        if attended == self.attended:
            return
        self.attended = attended
        self.record_activity("tui", phase="attach" if attended else "detach")
        if not attended:
            self._flush_activity()
        self._on_attended_changed(attended)

    def _on_attended_changed(self, attended: bool) -> None:
        """Watcher: pause or resume the timers, tell the engine, refresh on return.

        A resumed Textual timer fires its pending tick at once, so every
        paused path is back within one of its own ticks; the explicit
        refresh makes that one full pass (sessions, rows, daemon bar,
        timeline, jobs, sisters) rather than whatever each timer was due
        for. The engine hears ``visible`` either way: it is what keeps the
        engine on its attended cadences (and wakes an unattended one).
        Nothing here runs while the state is unchanged.
        """
        self.periodic.set_attended(attended)
        self.engine_link.set_visible(attended)
        if attended:
            # The probe measured nothing while paused; a delta spanning the
            # pause would log as one enormous stall.
            self._heartbeat_last = time.monotonic()
            signal_activity(self.tmux_session)
            self._full_refresh()

    def _full_refresh(self) -> None:
        """Kick every periodic worker once (each coalesces with a tick in flight)."""
        self.refresh_sessions()
        self.update_daemon_status()
        self.update_timeline()
        self._repaint_rows()
        self._refresh_jobs()
        if self.has_sisters:
            self._poll_sisters()

    # ── End unattended low-power mode ──────────────────────────────────

    # ── The engine (docs/design/engine-0.6.md) ─────────────────────────

    def _burn_hours(self) -> float:
        """The burn window: the same baseline as the mean spin (0: none)."""
        return (getattr(self, "baseline_minutes", 0) or 0) / 60.0

    @property
    def _engine_agents(self) -> dict:
        """The engine's last snapshot's agents (engine_link.agents)."""
        return self.engine_link.agents

    # The engine client's messages (tui_engine_link), on the main thread

    def on_engine_snapshot_ready(self, message: EngineSnapshotReady) -> None:
        self.engine_link.take_snapshot()

    def on_engine_bell_rang(self, message: EngineBellRang) -> None:
        self._ring(message.agent, message.episode)

    def on_engine_connection_changed(self, message: EngineConnectionChanged) -> None:
        self.engine_link.set_connected(message.connected)

    def _repaint(self, changed: list) -> None:
        """Re-align columns if ``changed`` moved them, then repaint.

        Only the changed rows are measured: the aligned widths are worked
        out again from every row only when a changed row no longer fits
        them, or was the widest in a column and narrowed. Then every row
        repaints (its padding moved); otherwise only ``changed`` does.
        """
        if not self._column_widths_dirty and self._rows_fit_column_widths(changed):
            rows = changed
        else:
            old_widths = list(self.column_widths)
            self._column_widths_dirty = True
            self._recompute_cell_column_widths()
            rows = list(self.query(SessionSummary)) if self.column_widths != old_widths else changed
        for widget in rows:
            widget.refresh_if_changed()
        if self.preview_visible:
            self._update_preview()

    def _rows_fit_column_widths(self, rows: list) -> bool:
        """True if ``rows``, measured again, leave the aligned widths as they are."""
        widths = self.column_widths
        if not widths:
            return False
        measured = []
        for widget in rows:
            # The row's own cells: the repaint that follows reuses them
            new = [cell_len(c.plain) for c in widget.row_cells()]
            old = getattr(widget, "_cell_widths", None)
            if old is None or len(new) != len(widths) or len(old) != len(widths):
                return False
            for n, o, w in zip(new, old, widths):
                if n > w or (o == w and n < o):
                    return False
            measured.append((widget, new))
        for widget, new in measured:
            widget._cell_widths = new
        return True

    def _repaint_rows(self) -> None:
        """Every row from what it holds now (after a detach, or a full refresh)."""
        self._update_fleet_flags()
        self._repaint(list(self.query(SessionSummary)))

    def _update_fleet_flags(self) -> bool:
        """The fleet-gated columns, from the rows on show (compute_fleet_flags,
        as ``overcode list`` does); True if a row's flags changed."""
        widgets = list(self.query(SessionSummary))
        fleet = compute_fleet_flags(w.fleet_row() for w in widgets)
        self.fleet = fleet
        flipped = False
        for widget in widgets:
            if widget.fleet != fleet:
                flipped = True
            # The same value again is no change to the row (__setattr__)
            widget.fleet = fleet
        return flipped

    def _ring(self, agent: str, episode: dict) -> None:
        """Main thread: the engine rang the bell for ``agent`` (an input-needed episode began).

        The row lights 🔔 (the next delta says the same), a macOS
        notification goes out, and if the person is already on that agent
        it counts as seen after a few seconds.
        """
        widget = next((w for w in self.query(SessionSummary) if w.session.id == agent), None)
        name = episode.get("name") or (widget.session.name if widget is not None else agent)
        task = widget.current_activity if widget is not None else None
        if widget is not None and not widget.session.is_asleep:
            widget.is_unvisited_stalled = True
            if self.attended:
                widget.refresh()
        self._notifier.queue(name, task)
        self._notifier.flush()
        if self.attended and self._selected_session_id() == agent:
            self._schedule_bell_dismiss(agent)

    def _visit(self, session_id: str) -> None:
        """The person looked at ``session_id``: tell the engine, clear the 🔔 now."""
        self._visited_here[session_id] = time.time()
        self._bell_dismiss_timers.pop(session_id, None)
        widget = next((w for w in self.query(SessionSummary) if w.session.id == session_id), None)
        if widget is not None and not widget.session.is_remote:
            self.engine_link.visit(session_id)
        elif widget is not None:
            self._save_remote_visit(session_id)
        if widget is not None and widget.is_unvisited_stalled:
            widget.is_unvisited_stalled = False
            widget.refresh()

    def _save_remote_visit(self, session_id: str) -> None:
        """Persist a visit to a sister agent; forget ones a week old."""
        cutoff = time.time() - REMOTE_VISIT_DAYS * 86400
        self._prefs.remote_visits = {
            sid: at for sid, at in self._visited_here.items()
            if sid.startswith("remote:") and (sid == session_id or at > cutoff)}
        self._save_prefs()

    # ── The focused agent's pane: the one capture loop ─────────────────

    def _focused_pane_tick(self) -> None:
        """Every 250 ms: capture the focused agent's pane; tick every row's clock.

        The capture feeds the focused row's pane-derived columns (and the
        preview); the rest of the row, and every other row, is the engine's.
        The same worker re-reads the session table (a stat while
        sessions.json is unchanged), so an agent added, removed or settled
        (terminated, done) gets or loses its row within a tick. The clock
        repaints a row only if it would now draw differently (a duration or
        countdown moved).
        """
        if not self._focused_capture_in_flight:
            widget = self._get_focused_widget()
            window = None
            if (
                widget is not None
                and self.in_split  # an app built without the split (tests) never reaches tmux
                and self.tui_mode != "jobs"
                and not widget.session.is_remote
                and widget.session.status not in ("terminated", "done")
                and widget.session.tmux_window
            ):
                window = widget.session.tmux_window
            self._focused_capture_in_flight = True
            self._request_focused_capture(widget.session.id if window else None, window)
        for row in self.query(SessionSummary):
            row.refresh_if_changed()
        self._notifier.flush()
        # Proactive focus recovery: if focus was lost or landed on a
        # non-interactive widget (e.g. a click on the preview pane), restore
        # it within a tick so the selection highlight never disappears.
        if self._should_recover_focus():
            focused = self._get_focused_widget()
            if focused is not None:
                focused.focus()

    class FocusedCapture(events.Message):
        """The capture thread's result, for the main thread (see _capture_focused)."""

        def __init__(self, session_id: Optional[str], content: Optional[str], fresh: dict,
                     local: Optional[list], local_key: Optional[tuple]) -> None:
            super().__init__()
            self.session_id = session_id
            self.content = content
            self.fresh = fresh
            self.local = local
            self.local_key = local_key

    def _request_focused_capture(self, session_id: Optional[str], window: Optional[str]) -> None:
        """Main thread: hand one capture to the capture thread (started on first use).

        The caller has set ``_focused_capture_in_flight``; the result's
        handler clears it, so there is never more than one capture queued
        or running.
        """
        self._capture_request = (session_id, window)
        if self._capture_thread is None:
            self._capture_thread = threading.Thread(
                target=self._capture_loop, name="focused-capture", daemon=True)
            self._capture_thread.start()
        self._capture_wake.set()

    def _capture_loop(self) -> None:
        """The capture thread: sleep until woken, capture, post, until stopped."""
        while True:
            self._capture_wake.wait()
            self._capture_wake.clear()
            if self._capture_stop.is_set():
                return
            session_id, window = self._capture_request
            try:
                args = self._capture_focused(session_id, window)
            except Exception:  # a failed capture must not end the loop
                logging.getLogger("overcode.tui").exception("focused-pane capture failed")
                self._focused_capture_in_flight = False
                continue
            # A message, not call_from_thread: the thread never blocks on
            # the main thread (and so never deadlocks an exit that joins it)
            if not self.post_message(self.FocusedCapture(*args)):
                self._focused_capture_in_flight = False

    def _capture_focused(self, session_id: Optional[str], window: Optional[str]) -> tuple:
        """Capture thread: one capture-pane of the focused agent (if any), and the session table.

        The session table is a stat while sessions.json is unchanged; a
        changed one (a rename, standing orders, a value) reaches the rows
        within a tick, and so does a new or removed agent (``local``, this
        tmux session's agents, read-only).
        """
        content = (self.detector.get_pane_content(window) or "") if window else None
        fresh = {s.id: s for s in self.session_manager.list_sessions()}
        local = self.launcher.list_sessions()
        # The key is built here, off the main thread; the main thread compares
        return (session_id, content, fresh, local, session_row_key(local))

    def on_supervisor_tui_focused_capture(self, event: "SupervisorTUI.FocusedCapture") -> None:
        try:
            self._apply_focused_capture(event.session_id, event.content, event.fresh,
                                        event.local, event.local_key)
        finally:
            self._focused_capture_in_flight = False

    def _stop_capture_thread(self) -> None:
        self._capture_stop.set()
        self._capture_wake.set()
        thread = self._capture_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

    def _apply_focused_capture(self, session_id: Optional[str], content: Optional[str],
                               fresh: dict, local: Optional[list] = None,
                               local_key: Optional[tuple] = None) -> None:
        """Main thread: the focused row's pane, and fresh session metadata for every row.

        ``local`` is the session list ``refresh_sessions`` would apply; when
        its row set differs from what is shown (an agent launched, killed,
        terminated or done since), it is applied now rather than on the
        next 10 s refresh.
        """
        self._mark_event("apply_focused_start")
        if local is not None:
            if local_key is None:
                local_key = session_row_key(local)
            if local_key != self._session_row_key:
                self._apply_sessions(local)
        # Most ticks the session table is the same snapshot objects the rows
        # already hold: then only the focused row's pane is news, and no
        # row needs walking (_apply_sessions forgets what was applied)
        applied = self._capture_fresh_applied
        if applied is not None and len(applied) == len(fresh) \
                and all(applied.get(k) is v for k, v in fresh.items()):
            if content is not None:
                widget = self._get_focused_widget()
                if widget is not None and widget.session.id == session_id:
                    widget.local_pane = True
                    if widget.apply_pane_content(content):
                        self._repaint([widget])
            self._mark_event("apply_focused_end")
            return
        changed = []
        focused_id = self._selected_session_id()
        for widget in self.query(SessionSummary):
            new_sess = fresh.get(widget.session.id)
            if new_sess is not None and new_sess is not widget.session:
                widget.session = new_sess
                if "pr_number" not in widget.engine:
                    widget.pr_number = new_sess.pr_number
                changed.append(widget)
            if content is not None and widget.session.id == session_id == focused_id:
                widget.local_pane = True
                if widget.apply_pane_content(content) and widget not in changed:
                    changed.append(widget)
        self._capture_fresh_applied = fresh
        if changed:
            self._repaint(changed)
        self._mark_event("apply_focused_end")

    # ── The status bar ─────────────────────────────────────────────────

    def update_daemon_status(self) -> None:
        """Each second: the engine watch, and the status bar's own local checks.

        What the worker needs is read here, on the main thread; it returns
        plain data that _apply_daemon_status puts on the bar.
        """
        self.engine_link.watch()
        if not self.query("#daemon-status"):
            return
        agents = self.engine_link.agents
        asleep = {s.id for s in self.sessions if s.is_asleep}
        active_names = [v.get("name") for sid, v in agents.items()
                        if sid not in asleep and v.get("name")]
        self._fetch_daemon_status_async(
            self.tmux_session,
            getattr(self, "baseline_minutes", 0),
            active_names,
            # Subscription usage is fleet-level, not per-agent: the widget
            # shows the user's own Claude limits, which stay true for a mixed
            # fleet as long as one agent is on a subscription-metered backend.
            self._fleet_has_subscription_usage(list(agents.values())),
        )

    @work(thread=True, group="daemon_status")
    @single_flight("daemon_status")
    def _fetch_daemon_status_async(self, tmux_session: str, baseline_minutes: int,
                                   active_names: list, fetch_usage: bool) -> None:
        """Worker: what the status bar shows that is not about agents.

        Whether the supervisor and API server are running and the
        summarizer is reachable (pid files), the account's usage (fetched
        at most every 5 minutes) and the mean spin over the baseline window
        (the daemon's history CSV, read incrementally). Everything about
        the agents themselves comes from the engine's snapshot. Reads no
        widget and no app state: its inputs are the arguments.
        """
        if fetch_usage:
            self._usage_monitor.fetch()  # Internally throttled to 5min
        state = read_local_state(tmux_session, baseline_minutes, active_names)
        self.call_from_thread(self._apply_daemon_status, state)

    def _fleet_has_subscription_usage(self, agents: list) -> bool:
        """True when any agent runs on a subscription-metered backend.

        An empty fleet counts as yes — the widget is about the user's
        account, and hiding it on an idle dashboard would read as a bug.
        """
        from types import SimpleNamespace

        from .backends import BackendCapability, session_supports
        if not agents:
            return True
        return any(
            session_supports(SimpleNamespace(backend=v.get("backend")),
                             BackendCapability.SUBSCRIPTION_USAGE)
            for v in agents
        )

    def _apply_daemon_status(self, state: LocalBarState) -> None:
        """Main thread: the worker's local checks onto the bar, then repaint (no I/O)."""
        try:
            daemon_bar = self.query_one("#daemon-status", DaemonStatusBar)
        except NoMatches:
            return
        daemon_bar.apply_local_state(state)
        if self.has_sisters:
            daemon_bar._sister_states = self._sister_poller.get_sister_states()
        daemon_bar._usage_snapshot = self._usage_monitor.snapshot
        daemon_bar.engine_connected = self.engine_link.connected
        daemon_bar.refresh_if_changed()

    def update_timeline(self) -> None:
        """Refresh the status timeline: rows chosen here, history read in a worker."""
        try:
            timeline = self.query_one("#timeline", StatusTimeline)
        except NoMatches:
            return
        sessions = filter_visible_sessions(
            # 🌙 rows included: the engine logs their status history too
            active_sessions=list(self.sessions),
            terminated_sessions=list(self._terminated_sessions.values()),
            hide_asleep=self.hide_asleep,
            show_terminated=self.show_terminated,
            show_done=self.show_done,
            collapsed_parents=self.collapsed_parents if self._prefs.sort_mode == "by_tree" else None,
            tag_filter=self.tag_filter,
        )
        self._fetch_timeline_async(sessions, timeline.tmux_session, timeline.timeline_hours,
                                   self._sister_poller.has_sisters)

    @work(thread=True, group="timeline")
    @single_flight("timeline")
    def _fetch_timeline_async(self, sessions: list, tmux_session: str, hours: float,
                              has_sisters: bool) -> None:
        """Worker: read the timeline's CSV history (and sisters'), then apply on the main thread.

        Reads no widget and no app state: its inputs are the arguments.
        """
        presence_history, agent_histories = read_history_data(tmux_session, hours)
        # The swarm density rows' counts (swarm_history.csv, phase 4)
        swarm_histories = read_swarm_data(tmux_session, hours)
        # Merge remote timeline data from sisters (#296)
        if has_sisters:
            agent_histories.update(self._sister_poller.poll_all_timelines(hours))
        self.call_from_thread(self._apply_timeline, sessions, presence_history, agent_histories,
                              swarm_histories)

    def _apply_timeline(self, sessions: list, presence_history: list, agent_histories: dict,
                        swarm_histories: Optional[dict] = None) -> None:
        """Main thread: the worker's history onto the timeline (no I/O)."""
        try:
            timeline = self.query_one("#timeline", StatusTimeline)
        except NoMatches:
            return
        timeline.swarm_expanded = frozenset(getattr(self, "swarm_expanded", ()))
        timeline.apply_history_data(sessions, presence_history, agent_histories, swarm_histories)

    def _sync_timeline_fold(self) -> None:
        """The timeline's density rows fold with the list (→ / ←): no file read.

        Called whenever the list's expansion is re-synced; repaints only when
        an expanded or collapsed parent has strips on screen.
        """
        try:
            timeline = self.query_one("#timeline", StatusTimeline)
        except NoMatches:
            return
        timeline.set_swarm_fold(getattr(self, "swarm_expanded", ()))

    def _save_prefs(self) -> None:
        """Save current TUI preferences to disk."""
        self._prefs.save(self.tmux_session)

    def on_app_blur(self) -> None:
        """Terminal lost focus — show banner, remove header highlight."""
        try:
            self.query_one("#terminal-active-banner").add_class("visible")
        except NoMatches:
            pass
        try:
            self.query_one("Header").remove_class("monitor-active")
        except NoMatches:
            pass

    def on_app_focus(self) -> None:
        """Terminal gained focus — hide banner, highlight header."""
        try:
            self.query_one("#terminal-active-banner").remove_class("visible")
        except NoMatches:
            pass
        try:
            self.query_one("Header").add_class("monitor-active")
        except NoMatches:
            pass

    def on_resize(self) -> None:
        """Handle terminal resize events"""
        self._update_capture_lines()
        # Re-centre open dialogs — after the refresh, since app.size still
        # holds the old size while this handler runs
        for dialog in self.query(".modal.visible"):
            if hasattr(dialog, "relayout"):
                self.call_after_refresh(dialog.relayout)
        self.refresh()
        self.update_session_widgets()
        # Cascade to the nested agent tmux windows so they track the outer
        # terminal size — otherwise you get either zoom artefacts (content
        # cut off) or dotted-fill padding when the outer pane has grown.
        self._schedule_agent_window_resize()

    def _periodic_agent_resize(self) -> None:
        """Reconcile agent tmux window sizes with the bottom pane."""
        self._schedule_agent_window_resize()

    def _schedule_agent_window_resize(self) -> None:
        """Resize every agent's tmux window to match the bottom pane.

        Runs in a worker so we don't block the main thread on tmux subprocess
        calls (one per agent window). Called from on_resize and from a
        periodic timer to catch drift.
        """
        self.split.schedule_agent_window_resize()

    @work(thread=True, group="agent_resize")
    @single_flight("agent_resize")
    def _resize_agent_windows_async(self, windows: list) -> None:
        """Worker: resize each of ``windows`` to the bottom pane (tui_split)."""
        self.split.resize_agent_windows(windows)

    def _update_capture_lines(self) -> None:
        """Scale capture buffer to at least 2x terminal height."""
        height = self.size.height if self.size.height > 0 else 40
        self.detector.capture_lines = max(DEFAULT_CAPTURE_LINES, 2 * height)

    def refresh_sessions(self) -> None:
        """Refresh session list (kicks off background worker).

        Reads ``launcher.list_sessions()``, which writes nothing: the
        monitor daemon decides terminated / done / waiting_oversight and
        the TUI shows what it wrote. New and removed agents also arrive
        through the focused-pane tick (``_apply_focused_capture``).
        """
        self._fetch_sessions_async()

    @work(thread=True, group="refresh_sessions")
    @single_flight("refresh_sessions")
    def _fetch_sessions_async(self) -> None:
        """Read session list off the main thread, then apply to UI."""
        sessions = self.launcher.list_sessions()
        self.call_from_thread(self._apply_sessions, sessions)

    def _apply_sessions(self, sessions: list) -> None:
        """Apply refreshed session list on main thread (no I/O)."""
        self._session_row_key = session_row_key(sessions)
        self._gc_terminated_sessions()
        # Detect new sessions for timeline refresh (#244)
        old_names = {s.name for s in self.sessions}
        # Which agent is highlighted, read against the *old* order — the
        # index alone is meaningless once self.sessions is replaced (#471).
        selected_id = self._selected_session_id()

        # Merge local + adopted (🌙) + remote sessions (#245), filtering
        # disabled sisters (#323)
        self._local_sessions = sessions
        self.sessions = sessions + self._background_sessions() + self._visible_remote_sessions()
        # Resolve cross-machine parent relationships (#245)
        self._resolve_remote_parents()
        # Apply sorting (#61), re-anchoring the highlight to that agent (#471)
        self._sort_sessions(selected_id=selected_id)
        # update_session_widgets handles focus preservation internally
        # Guard against race where background worker delivers sessions before
        # the ScrollableContainer is fully mounted (#286). The race surfaces
        # as MountError when the container exists but is not yet mounted, or
        # as NoMatches when the query runs before it is even in the DOM.
        try:
            self.update_session_widgets(force_refresh=False)
        except (MountError, NoMatches):
            return

        # First load: highlight the agent highlighted when the TUI last ran
        if not self._initial_sessions_loaded:
            self._reanchor_selection(self._prefs.last_focused_agent or None)

        # First-load sync: align the external tmux pane with the TUI's focused
        # agent exactly once. The periodic-refresh guard in the focus watcher
        # (see _user_navigated) would otherwise leave the bottom pane stranded
        # on whatever window tmux happened to pick when `overcode tmux` opened
        # the split.
        if not self._initial_tmux_sync_done and self.split.active:
            try:
                widget = self._get_focused_widget()
            except Exception:
                widget = None
            if widget is not None:
                self.split.show_agent(widget)
                self._initial_tmux_sync_done = True

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

    def _background_sessions(self) -> list:
        """The 🌙 rows' Sessions, from the engine's views (rebuilt when their key moves)."""
        from .claude_bg import adopted_key, session_from_view

        agents = self.engine_link.agents
        key = adopted_key(agents)
        if key != self._adopted_key:
            self._adopted_key = key
            self._adopted_sessions = [session_from_view(agents[sid]) for sid, *_ in key]
        return list(self._adopted_sessions)

    def _background_sessions_moved(self) -> None:
        """A 🌙 row appeared, went or moved: re-apply the session list (no I/O)."""
        from .claude_bg import adopted_key

        if adopted_key(self.engine_link.agents) == self._adopted_key:
            return
        if not self._initial_sessions_loaded:
            self._background_sessions()  # the first local list will bring them
            return
        self._apply_sessions(self._local_sessions)

    def _recalc_column_widths(self, sessions) -> bool:
        """Recalculate max name/repo/branch widths and name-match flag.

        Returns True if any width or flag actually changed.
        Also marks column widths dirty when changes are detected.
        """
        old = (self.max_name_width, self.max_repo_width, self.max_branch_width, self.all_names_match_repos)
        sessions = list(sessions)
        if sessions:
            self.max_name_width = max(
                (len(s.name) for s in sessions), default=8
            )
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
            self.max_name_width = 10
            self.max_repo_width = 10
            self.max_branch_width = 10
            self.all_names_match_repos = False
        changed = old != (self.max_name_width, self.max_repo_width, self.max_branch_width, self.all_names_match_repos)
        if changed:
            self._column_widths_dirty = True
        return changed

    def _recompute_cell_column_widths(self, force: bool = False) -> None:
        """Recompute per-cell column widths across all visible widgets.

        Collects the cells (row_cells(), as rendered) from every SessionSummary
        widget, then computes max visual width per column position. Stored
        as self.column_widths for use by widget render() via pad_and_join_cells().

        Skips recomputation if not dirty (no structural changes since last call)
        unless force=True.

        This ensures all widgets align perfectly regardless of content width.
        Same compute_column_widths() function is used by CLI's align_summary_rows(),
        so testing `overcode list` validates the TUI alignment code.
        """
        if not force and not self._column_widths_dirty:
            return
        from .summary_columns import compute_column_widths
        widgets = list(self.query(SessionSummary))
        if not widgets:
            self.column_widths = []
            self._column_widths_dirty = False
            return
        all_cells = []
        for w in widgets:
            cells = w.row_cells()
            all_cells.append(cells)
            w._cell_widths = [cell_len(c.plain) for c in cells]
        self.column_widths = compute_column_widths(all_cells)
        self._column_widths_dirty = False
        # Update column headers if visible
        self._update_column_headers()

    def _selected_session_id(self) -> Optional[str]:
        """Id of the agent the highlight is on, in the *current* display order.

        ``focused_session_index`` is a row number, not an identity — it only
        means something relative to whatever order ``self.sessions`` is in
        at the moment it is read. Capture this *before* anything re-orders
        the list (#471).
        """
        try:
            widgets = self._get_widgets_in_session_order()
        except Exception:
            return None
        if 0 <= self.focused_session_index < len(widgets):
            return widgets[self.focused_session_index].session.id
        return None

    def _reanchor_selection(self, session_id: Optional[str]) -> None:
        """Move ``focused_session_index`` back onto ``session_id`` after a re-order (#471).

        Silent on purpose: the *agent* under the highlight hasn't changed,
        only its row, so the external tmux pane is already showing it. The
        watcher is suppressed here and ``update_session_widgets``'s own
        focus-restore re-applies Textual focus afterwards. If the agent is
        no longer displayed the index is left alone for that restore to clamp.
        """
        if not session_id:
            return
        try:
            widgets = self._get_widgets_in_session_order()
        except Exception:
            return
        for i, widget in enumerate(widgets):
            if widget.session.id == session_id:
                if i != self.focused_session_index:
                    was_suppressed = self._suppress_focus_watcher
                    self._suppress_focus_watcher = True
                    try:
                        self.focused_session_index = i
                    finally:
                        self._suppress_focus_watcher = was_suppressed
                return

    def _sort_sessions(self, selected_id: Optional[str] = None) -> None:
        """Sort sessions based on current sort mode (#61), keeping the
        highlight on the same agent (#471).

        The by_status / by_value modes re-order the list whenever an agent
        changes state, so a bare re-sort would leave ``focused_session_index``
        pointing at whichever agent *landed* on that row — the TUI highlight
        and the tmux pane (still on the original agent) then drift apart.
        Callers that have already replaced ``self.sessions`` must pass the id
        they captured beforehand via ``selected_id``.
        """
        if selected_id is None:
            selected_id = self._selected_session_id()
        self.sessions = sort_sessions(
            self.sessions, self._prefs.sort_mode,
            reverse=self._prefs.sort_reversed,
            values=self._column_sort_values(self._prefs.sort_mode),
        )
        self._reanchor_selection(selected_id)

    def _column_sort_values(self, mode: str) -> Optional[dict]:
        """For a column sort (#487), each agent's sort key, read from its
        row widget's ColumnContext — the same data the row draws, so the
        order matches what is on screen. Agents without a widget yet (just
        launched) have no value and sort last until the next refresh."""
        from .tui_logic import COLUMN_SORT_PREFIX
        from .summary_columns import COLUMNS_BY_ID
        if not mode.startswith(COLUMN_SORT_PREFIX):
            return None
        col = COLUMNS_BY_ID.get(mode[len(COLUMN_SORT_PREFIX):])
        if col is None or col.sort_key is None:
            return None
        values = {}
        for w in self.query(SessionSummary):
            try:
                values[w.session.id] = col.sort_key(w._build_column_context())
            except Exception:
                values[w.session.id] = None
        return values

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

    @property
    def in_split(self) -> bool:
        """True when running as the top pane of the `overcode tmux` split.

        run_tui always passes the linked session, so this is the normal
        case. An app built without one (unit tests) never touches tmux:
        no zoom, no window switching, no resizing, no detach.
        """
        return self.tmux_sync_target is not None

    @property
    def split(self):
        """Every tmux side effect (tui_split): a TmuxSplit in the split, else the null one.

        Made on first use from ``tmux_sync_target`` (so bare test apps get
        one too); tests may assign a stand-in.
        """
        split = self.__dict__.get("_split")
        if split is None:
            split = make_split(self, getattr(self, "tmux_sync_target", None))
            self.__dict__["_split"] = split
        return split

    @split.setter
    def split(self, split) -> None:
        self.__dict__["_split"] = split

    def _should_recover_focus(self) -> bool:
        """Check if focus recovery should run.

        Returns False when focus is already on a session/input widget (the
        usual case, answered first and without a DOM query: this runs every
        250 ms), an overlay is visible (help, any modal), or the command bar
        is open.
        """
        if isinstance(self.focused, (SessionSummary, Input, TextArea)):
            return False
        # Don't steal focus from overlays
        try:
            ho = self.query_one("#help-overlay")
            if ho.has_class("visible"):
                return False
        except NoMatches:
            pass
        if self._any_modal_visible():
            return False
        # Don't steal focus from command bar during instruction input (#321)
        try:
            cmd_bar = self.query_one("#command-bar")
            if cmd_bar.has_class("visible"):
                return False
        except NoMatches:
            pass
        # Only recover if focus is not on a session or input widget
        if self.focused is None:
            return True
        if isinstance(self.focused, SessionSummary):
            return False
        if isinstance(self.focused, (Input, TextArea)):
            return False
        return True

    def watch_focused_session_index(self, new_index: int) -> None:
        """Auto-focus the widget at the new index, update preview, and sync tmux."""
        if self._suppress_focus_watcher:
            return
        if self._any_modal_visible():
            return
        widget = self._get_focused_widget()
        if widget is None:
            # No widgets — exit sister zoom if active (e.g. all sisters removed)
            if self._sister_zoom_active:
                self._exit_sister_view()
            return
        if self._prefs.status_change_logging:
            self._log_status_change(
                widget.session.name, widget.detected_status, widget.detected_status,
                source="focus_switch", focused=True,
            )
        widget.focus()
        self.engine_link.report_focus(widget)
        self._swarm_focus_moved(widget)
        if self.preview_visible:
            self._update_preview()
        # Only sync tmux on explicit user navigation, not programmatic restores.
        # Periodic refresh_sessions → update_session_widgets can temporarily lose
        # remote sessions (sister poll timing), causing focused_session_index to
        # land on a local agent and switch the bottom pane away.
        if getattr(self, '_user_navigated', False):
            self._user_navigated = False
            self.split.show_agent(widget)
            self.split.fit_window(widget)
            self._remember_focus(widget.session.id)

    def _remember_focus(self, session_id: str) -> None:
        """Keep the agent the user moved to for the next start."""
        if session_id and session_id != self._prefs.last_focused_agent:
            self._prefs.last_focused_agent = session_id
            self._save_prefs()

    def _gc_terminated_sessions(self) -> None:
        """Remove terminated sessions older than _TERMINATED_GC_SECONDS."""
        if not self._terminated_times:
            return
        import time as _time
        now = _time.monotonic()
        expired = [
            sid for sid, t in self._terminated_times.items()
            if (now - t) > self._TERMINATED_GC_SECONDS
        ]
        for sid in expired:
            del self._terminated_sessions[sid]
            del self._terminated_times[sid]

    @work(thread=True, group="summarizer", name="summarizer")
    @single_flight("summarizer")
    def _update_summaries_async(self) -> None:
        """Background thread for AI summarization.

        Only runs if summarizer is enabled. Auto-pauses after idle timeout
        (no TUI keypresses) to prevent runaway API costs.
        """
        if not self._summarizer.enabled:
            return

        # Auto-pause if TUI has been idle beyond the configured timeout
        if self._last_keypress > 0:
            idle_secs = time.monotonic() - self._last_keypress
            if idle_secs >= self._summarizer.config.idle_timeout:
                self._summarizer_idle_paused = True
                self._summarizer.config.enabled = False
                if self._summarizer._client:
                    self._summarizer._client.close()
                    self._summarizer._client = None
                idle_mins = int(idle_secs // 60)
                self.call_from_thread(
                    self.notify,
                    f"AI Summarizer paused ({idle_mins}m idle — press any key to resume)",
                    severity="warning",
                )
                return

        # Hard-halt if cost cap exceeded (requires TUI restart to reset)
        cap = self._summarizer.config.cost_cap
        if cap > 0 and self._summarizer.total_cost_usd >= cap:
            self._summarizer.cost_cap_hit = True
            self._summarizer.config.enabled = False
            if self._summarizer._client:
                self._summarizer._client.close()
                self._summarizer._client = None
            from .tui_helpers import format_cost
            self.call_from_thread(
                self.notify,
                f"AI Summarizer HALTED — cost cap {format_cost(cap)} reached. Restart TUI to reset.",
                severity="error",
            )
            return

        # Get fresh session list (filtered to this tmux session)
        all_sessions = self.session_manager.list_sessions()
        sessions = [s for s in all_sessions if s.tmux_session == self.tmux_session]
        if not sessions:
            return

        # Update summaries (this makes API calls). One HTTP round trip per
        # agent: a 50-agent pass outlasts the 5 s tick. Ticks that land
        # mid-pass are coalesced by @single_flight into one rerun, so the
        # pass runs to completion; the cancel check between agents fires
        # only on app exit, and the round-robin cursor resumes from that
        # agent on the next pass.
        summaries = self._summarizer.update(sessions, should_stop=worker_cancelled)

        # Apply to widgets on main thread
        self.call_from_thread(self._apply_summaries, summaries)

    def _apply_summaries(self, summaries: dict) -> None:
        """Apply AI summaries to session widgets (runs on main thread)."""
        self._mark_event("apply_summaries_start")
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
        self._mark_event("apply_summaries_end")

    def update_session_widgets(self, force_refresh: bool = True, preserve_focus: bool = True) -> None:
        """Update the session display incrementally.

        Only adds/removes widgets when sessions change, rather than
        destroying and recreating all widgets (which causes UI stutter).

        Args:
            force_refresh: If False, only refresh widgets whose session data
                actually changed. Set to True when column widths changed or
                on structural changes that require all widgets to repaint.
            preserve_focus: If True, capture the currently focused session
                before DOM mutations and restore focused_session_index after.
                Focus is only restored to a SessionSummary if one had Textual
                focus before the update (never steals from command bar).
        """
        self._capture_fresh_applied = None  # rows get other session objects here
        # Capture focus state before DOM mutations
        if preserve_focus:
            _focused_widget = self._get_focused_widget()
            _focused_session_id = _focused_widget.session.id if _focused_widget else None
            _focus_was_on_session = isinstance(self.focused, SessionSummary)
        else:
            _focused_session_id = None
            _focus_was_on_session = False

        container = self.query_one("#sessions-container", ScrollableContainer)

        # Subtree cost of a sister's agent comes with its forwarded state; a
        # local agent's with the engine's view (apply_engine)
        remote_subtree = {
            s.id: s.remote_daemon_state['subtree_cost_usd'] for s in self._remote_sessions
            if (getattr(s, 'remote_daemon_state', None) or {}).get('subtree_cost_usd', 0) > 0
        }
        # Build the list of sessions to display using extracted logic
        display_sessions = filter_visible_sessions(
            active_sessions=self.sessions,
            terminated_sessions=list(self._terminated_sessions.values()),
            hide_asleep=self.hide_asleep,
            show_terminated=self.show_terminated,
            show_done=self.show_done,
            collapsed_parents=self.collapsed_parents if self._prefs.sort_mode == "by_tree" else None,
            tag_filter=self.tag_filter,
        )

        # Column widths computed from exactly what will be rendered
        if self._recalc_column_widths(display_sessions):
            force_refresh = True

        # Get existing widgets and their session IDs
        existing_widgets = {w.session.id: w for w in self.query(SessionSummary)}
        existing_session_ids = set(existing_widgets.keys())

        # Check if we have an empty message widget that needs removal
        # (Static widgets that aren't SessionSummary)
        has_empty_message = any(
            isinstance(w, Static) and not isinstance(w, (SessionSummary, SwarmRows))
            for w in container.children
        )

        # Compute which widgets to add/remove
        sessions_added, sessions_removed = compute_session_widget_diff(
            existing_session_ids, [s.id for s in display_sessions]
        )

        if not sessions_added and not sessions_removed and not has_empty_message:
            # No structural changes needed - just update session data in existing widgets
            session_map = {s.id: s for s in display_sessions}
            for widget in existing_widgets.values():
                if widget.session.id in session_map:
                    new_session = session_map[widget.session.id]
                    # Check if anything display-relevant actually changed
                    # (the fleet flags are worked out below)
                    changed = force_refresh or widget.session != new_session
                    widget.session = new_session
                    # Sync display modes
                    widget.emoji_free = self.emoji_free
                    if new_session.is_remote:
                        changed = self._apply_remote(widget) or changed
                        widget.subtree_cost_usd = remote_subtree.get(new_session.id, 0.0)
                    if "pr_number" not in widget.engine:
                        # Sync pr_number from session (propagates detection and clearing)
                        widget.pr_number = new_session.pr_number
                    widget.oversight_deadline = getattr(new_session, 'oversight_deadline', None)
                    # Update terminated visual state
                    if widget.session.status == "terminated":
                        widget.add_class("terminated")
                    else:
                        widget.remove_class("terminated")
                    # Only refresh if data actually changed (#218 alignment still
                    # handled via force_refresh=True when widths change)
                    if changed:
                        widget.refresh()
            if self._update_fleet_flags() | self._update_uniform_columns():
                for widget in existing_widgets.values():
                    widget.refresh()
            # Recompute cell column widths for alignment after data update
            self._column_widths_dirty = True
            self._recompute_cell_column_widths()
            # Still reorder widgets to handle sort mode changes
            self._reorder_session_widgets(container)
            self._restore_focus_in_update(_focused_session_id, _focus_was_on_session)
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
                widget = SessionSummary(session)
                # Apply current summary detail level
                widget.summary_detail = self.SUMMARY_LEVELS[self.summary_level_index]
                # Apply current summary content mode (#140)
                widget.summary_content_mode = self.summary_content_mode
                # Apply display modes
                widget.emoji_free = self.emoji_free
                widget.show_cost = self.show_cost
                widget.fleet = self.fleet  # until _update_fleet_flags below
                widget.oversight_deadline = getattr(session, 'oversight_deadline', None)
                # What the engine (or a sister) already says about it
                if session.is_remote:
                    self._apply_remote(widget)
                    widget.subtree_cost_usd = remote_subtree.get(session.id, 0.0)
                elif session.id in self._engine_agents:
                    widget.apply_engine(self._engine_agents[session.id], self._burn_hours(),
                                        self._visited_here.get(session.id))
                # Apply per-level column overrides
                current_level = self.SUMMARY_LEVELS[self.summary_level_index]
                widget.column_overrides = self._prefs.column_config.get(current_level, {})
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

        self._update_fleet_flags()
        # Reorder widgets to match display_sessions order
        # This must run after any structural changes AND after sort mode changes
        self._reorder_session_widgets(container)
        self._update_uniform_columns()
        # Recompute cell column widths for alignment after structural changes
        self._column_widths_dirty = True
        self._recompute_cell_column_widths()
        self._restore_focus_in_update(_focused_session_id, _focus_was_on_session)

    def _update_uniform_columns(self, changed: Optional[list] = None) -> bool:
        """Hide the columns every shown agent has the same value in.

        With ``changed`` (rows whose view just moved), the set is worked out
        again only if one of those rows' values in a hide-when-uniform
        column moved (a rebill, a /model switch). True if the set changed:
        the columns, their widths and the header's "all: …" note moved.
        """
        from .summary_columns import SUMMARY_COLUMNS, uniform_columns

        cols = [c for c in SUMMARY_COLUMNS if c.hide_when_uniform and c.render_plain is not None]

        def values(ctx) -> tuple:
            out = []
            for col in cols:
                try:
                    out.append(col.render_plain(ctx))
                except Exception:
                    out.append(None)
            return tuple(out)

        if changed is not None:
            moved = False
            for w in changed:
                key = values(w._build_column_context())
                if key != w._uniform_values:
                    w._uniform_values = key
                    moved = True
            if not moved:
                return False
        rows = list(self.query(SessionSummary))
        contexts = []
        for w in rows:
            ctx = w._build_column_context()
            w._uniform_values = values(ctx)
            if w.display:
                contexts.append(ctx)
        uniform = uniform_columns(contexts)
        flipped = uniform != self.uniform_columns
        self.uniform_columns = uniform
        for widget in rows:
            if widget.uniform_columns != uniform:
                widget.uniform_columns = uniform
        if flipped:
            self._column_widths_dirty = True
        return flipped

    def _restore_focus_in_update(self, focused_session_id: str | None, focus_was_on_session: bool) -> None:
        """Restore focused_session_index after update_session_widgets DOM mutations.

        If the previously focused session still exists, move the index to its
        new position. If it was removed/filtered, clamp the index.
        Only fires the watcher (which calls .focus()) when a SessionSummary
        had Textual focus before the update — never steals from command bar.
        """
        if not focused_session_id:
            return
        widgets = self._get_widgets_in_session_order()
        if not widgets:
            return
        # Suppress watcher if focus wasn't on a session (e.g. command bar open)
        if not focus_was_on_session:
            self._suppress_focus_watcher = True
        try:
            for i, widget in enumerate(widgets):
                if widget.session.id == focused_session_id:
                    self.focused_session_index = i
                    return
            # Focused session was removed/filtered — clamp index
            self.focused_session_index = min(self.focused_session_index, len(widgets) - 1)
        finally:
            self._suppress_focus_watcher = False

    def on_session_summary_stalled_agent_visited(self, message: SessionSummary.StalledAgentVisited) -> None:
        """The person reached a 🔔 agent (focus or click): a visit."""
        self._visit(message.session_id)

    def _schedule_bell_dismiss(self, session_id: str) -> None:
        """A bell for the agent already on screen counts as seen after 5 s."""
        if session_id in self._bell_dismiss_timers:
            return

        def _dismiss() -> None:
            self._bell_dismiss_timers.pop(session_id, None)
            # Only if still focused on this agent
            if self._selected_session_id() == session_id:
                self._visit(session_id)

        self._bell_dismiss_timers[session_id] = self.set_timer(BELL_SEEN_AFTER_SECONDS, _dismiss)

    def on_session_summary_clicked(self, message: SessionSummary.Clicked) -> None:
        """A click on an agent's row selects it the way j/k do: focus,
        and the split's bottom pane switches to it."""
        for i, widget in enumerate(self._get_widgets_in_session_order()):
            if widget.session.id != message.session_id:
                continue
            if i == self.focused_session_index:
                # Already selected, so the index watcher won't fire. The
                # pane may have been switched by hand since; bring it back.
                self.split.show_agent(widget)
                self.split.fit_window(widget)
            else:
                self._user_navigated = True
                self.focused_session_index = i
            return

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
        # In tree mode: full metadata (prefix, depth, child_count) via compute_tree_metadata
        # Otherwise: lightweight child_count only via compute_child_counts
        is_tree_mode = self._prefs.sort_mode == "by_tree"
        # 👶 counts overcode children; a 🌙 row nests under its agent
        # (parent_session_id) without being one
        from .claude_bg import is_background
        child_counts = compute_child_counts([s for s in self.sessions if not is_background(s)])
        if is_tree_mode:
            tree_meta = compute_tree_metadata(self.sessions)
            for widget in ordered_widgets:
                meta = tree_meta.get(widget.session.id)
                widget.child_count = child_counts.get(widget.session.id, 0)
                widget.children_collapsed = widget.session.id in self.collapsed_parents
                widget.tree_prefix = meta.prefix if meta else ""
                widget.tree_depth = meta.depth if meta else 0
        else:
            for widget in ordered_widgets:
                widget.child_count = child_counts.get(widget.session.id, 0)
                widget.children_collapsed = widget.session.id in self.collapsed_parents
                widget.tree_prefix = ""
                widget.tree_depth = 0
        # Remote agents: prefer the daemon-reported children_count from the
        # source host. Local computation only sees this host's sessions and
        # would otherwise always say 0 for a parent whose children live on
        # another machine (#271).
        for widget in ordered_widgets:
            if not widget.session.is_remote:
                continue
            rds = getattr(widget.session, 'remote_daemon_state', None) or {}
            remote_children = rds.get('children_count')
            if remote_children is not None:
                widget.child_count = remote_children
        for widget in ordered_widgets:
            widget.job_count = self._job_counts.get(widget.session.id, 0)
        # Expanded parents' virtual rows follow their parent's row
        if self.swarm_expanded or self._swarm_lines:
            self._sync_swarm_rows()

    def watch_preview_visible(self, preview_visible: bool) -> None:
        """React to preview pane visibility changes."""
        if not self.is_running:
            return  # App not composed yet — nothing to update
        self._update_subtitle()

        try:
            preview = self.query_one("#preview-pane", PreviewPane)
            sessions_container = self.query_one("#sessions-container", ScrollableContainer)
            jobs_container = self.query_one("#jobs-container", ScrollableContainer)
            if preview_visible:
                sessions_container.add_class("list-mode")
                jobs_container.add_class("list-mode")
                preview.add_class("visible")
                self._update_preview()
            else:
                sessions_container.remove_class("list-mode")
                jobs_container.remove_class("list-mode")
                preview.remove_class("visible")
        except NoMatches:
            pass

    def _update_subtitle(self) -> None:
        """Update the header subtitle to show session info."""
        session_label = "jobs" if self.tui_mode == "jobs" else self.tmux_session
        self.sub_title = f"{session_label} [DIAGNOSTICS]" if self.diagnostics else session_label

    def _build_footer_text(self) -> Text:
        """The footer: a few keys to start with, leading with `/`.

        Deliberately short. The palette lists every command with its key
        and state, so the footer's job is to send people there (#482).
        """
        from .keymap import keymap_of
        from .tui_widgets.dialog_style import KEY, KEYCAP
        km = keymap_of(self)
        if self.tui_mode == "jobs":
            keys = [(km.label("toggle_tui_mode"), "Agents"), ("j/k", "Jobs"), ("x", "Kill"), ("c", "Clear done")]
        else:
            nav = "/".join(filter(None, (km.label("focus_next_session").split("/")[0],
                                         km.label("focus_previous_session").split("/")[0])))
            keys = [(km.label("new_agent"), "New agent"), (nav, "Next/prev"),
                    (km.label("jump_to_agent"), "Jump to agent")]
            from .config import get_tmux_toggle_key
            from .cli.split import TOGGLE_KEY_CHOICES, DEFAULT_TOGGLE_KEY
            toggle = get_tmux_toggle_key() or DEFAULT_TOGGLE_KEY
            label = next((lbl for lbl, k in TOGGLE_KEY_CHOICES if k == toggle), toggle)
            keys.append((label.split(" ")[0], "Switch pane"))
        keys += [(km.label("toggle_help").split("/")[-1], "Help"), (km.label("quit"), "Quit")]

        palette_key = km.label("command_palette").split("/")[0] or "/"
        text = Text()
        text.append(f" {palette_key} ", style=KEYCAP)
        text.append(" Commands", style="bold")
        for key, label in keys:
            if not key:
                continue
            text.append("   ")
            text.append(key, style=KEY)
            text.append(f" {label}")
        return text

    def _update_footer(self) -> None:
        """Rebuild the footer (view mode or toggle key changed)."""
        try:
            help_text = self.query_one("#help-text", Static)
            help_text.update(self._build_footer_text())
        except NoMatches:
            pass

    def _current_column_overrides(self, level: str) -> dict:
        """Override lookup used by header + cell-width computation.

        Live modal overrides (#449) take precedence over persisted prefs so
        header labels re-render as the user toggles columns in the C modal.
        """
        if self._live_column_overrides is not None:
            return self._live_column_overrides
        return self._prefs.column_config.get(level, {})

    def _update_column_headers(self) -> None:
        """Update the column headers widget based on current state."""
        try:
            header_widget = self.query_one("#column-headers", ColumnHeader)
            if not self._prefs.show_column_headers:
                header_widget.display = False
                return
            header_widget.display = True

            if self.tui_mode == "jobs":
                header_widget.set_columns([], [])
                from rich.text import Text
                # Compute name width from current jobs (same logic as _apply_jobs)
                nw = max((len(j.name) for j in self.jobs), default=12)
                nw = max(12, min(nw, 30))
                header = Text()
                header.append("  ", style="")
                header.append(f"{'Name':<{nw}} ", style="bold dim")
                header.append(f"{'Agent':<16} ", style="bold dim")
                header.append(f"{'Command':<80} ", style="bold dim")
                header.append(f"{'Started':>16} ", style="bold dim")
                header.append(f"{'Time':>6} ", style="bold dim")
                header.append("  ", style="")
                header.append("Status", style="bold dim")
                header_widget.update(header)
                return

            from .summary_columns import (
                COLUMNS_BY_ID, SUMMARY_COLUMNS, render_header_cells, resolve_column_visible,
            )
            from .tui_logic import sort_column_for_mode, sort_descending
            level = self.SUMMARY_LEVELS[self.summary_level_index]
            overrides = self._current_column_overrides(level)

            def col_filter(col):
                return resolve_column_visible(col, level, overrides, self.uniform_columns)

            sort_col = sort_column_for_mode(self._prefs.sort_mode)
            desc = sort_descending(self._prefs.sort_mode, self._prefs.sort_reversed)
            header_line = render_header_cells(
                column_filter=col_filter,
                column_widths=self.column_widths,
                sort_column=sort_col,
                sort_descending=desc,
            )
            # What the hidden same-for-everyone columns would have said
            shared = [
                f"{COLUMNS_BY_ID[cid].header or cid} {value}"
                for cid, value in self.uniform_columns.items()
                if value and col_filter(COLUMNS_BY_ID[cid]) is False
                and resolve_column_visible(COLUMNS_BY_ID[cid], level, overrides)
            ]
            if shared:
                header_line.append("   all: " + " · ".join(shared), style="dim")
            header_widget.update(header_line)
            header_widget.set_columns(
                [c.id for c in SUMMARY_COLUMNS if col_filter(c)],
                self.column_widths, sort_col, desc,
            )
        except NoMatches:
            pass

    def _select_first_agent(self) -> None:
        """Highlight something from the start: the agent remembered from the
        last run (already re-anchored onto), else the first."""
        try:
            widgets = list(self.query(SessionSummary))
            if widgets:
                remembered = self._prefs.last_focused_agent
                keep = remembered and self._selected_session_id() == remembered
                # Watcher handles focus + preview + tmux sync
                self.focused_session_index = self.focused_session_index if keep else 0
        except NoMatches:
            pass

    def _update_preview(self) -> None:
        """Update preview pane with the selected session's content.

        Uses focused_session_index (the app's own selection state) rather
        than self.focused (Textual's internal focus) because DOM reordering
        in _reorder_session_widgets() and async focus changes can cause
        self.focused to diverge from the visually highlighted row.
        """
        # Don't overwrite job preview with agent content
        if self.tui_mode == "jobs":
            return
        try:
            preview = self.query_one("#preview-pane", PreviewPane)
            widgets = self._get_widgets_in_session_order()
            if 0 <= self.focused_session_index < len(widgets):
                widget = widgets[self.focused_session_index]
                preview.update_from_widget(widget, stale_banner=self._stale_banner_for(widget.session))
        except NoMatches:
            pass

    def _ensure_monitor_daemon(self) -> None:
        """Start or restart the Monitor Daemon as needed.

        Called automatically on TUI mount to ensure continuous monitoring.
        Also auto-restarts if the running daemon is an old version (#386).
        """
        from .settings import DAEMON_VERSION
        from .monitor_daemon import stop_monitor_daemon

        # Decided by the daemon's lock, not the PID file: a stale PID file
        # may name a reused PID (pid_utils).
        running = is_monitor_daemon_running(self.tmux_session)

        if running:
            # Check for version mismatch (#386)
            daemon_state = get_monitor_daemon_state(self.tmux_session)
            if daemon_state and daemon_state.daemon_version != DAEMON_VERSION:
                old_v = daemon_state.daemon_version
                self.notify(
                    f"Restarting daemon (v{old_v} → v{DAEMON_VERSION})",
                    severity="information",
                )
                stop_monitor_daemon(self.tmux_session)
                # Delay start to let the old process exit
                self.set_timer(0.5, self._start_monitor_daemon_on_mount)
            return

        self._start_monitor_daemon_on_mount()

    def _start_monitor_daemon_on_mount(self) -> None:
        """Start the monitor daemon (called from _ensure_monitor_daemon)."""
        pid = spawn_daemon([
            sys.executable, "-m", "overcode.monitor_daemon",
            "--session", self.tmux_session,
        ])
        if pid:
            self.notify("Monitor Daemon started", severity="information")
        else:
            self.notify("Failed to start Monitor Daemon", severity="warning")

    def action_cycle_focal_repo(self) -> None:
        """Cycle the focal repo for the focused agent (#170).

        No-op for single-repo workspaces and for remote agents (the focal
        is owned by the source host's session manager and would need a
        sister-side control API to mutate).
        """
        focused = self._get_focused_widget()
        if focused is None:
            return
        session = focused.session
        if getattr(session, 'is_remote', False):
            self.notify(
                "Cycling focal repo on remote agents isn't supported yet.",
                severity="warning",
            )
            return
        try:
            new_focal = self.session_manager.cycle_focal_repo(session.id)
        except ValueError as e:
            self.notify(str(e), severity="warning")
            return
        if new_focal is None:
            self.notify(
                f"'{session.name}' is single-repo — no candidates to cycle.",
                severity="information",
            )
            return
        # Refresh sessions so the widget picks up the new repo_name/branch
        # the SessionManager just wrote, then trigger a redraw.
        self.refresh_sessions()
        self.notify(f"{session.name} focal repo: {new_focal}", severity="information")

    # Throttle TUI heartbeat writes to once per 5 seconds
    _last_heartbeat_write: float = 0.0
    # Track last keypress for summariser idle auto-shutoff
    _last_keypress: float = 0.0
    _summarizer_idle_paused: bool = False

    def on_key(self, event: events.Key) -> None:
        """Signal activity to daemon on any keypress."""
        signal_activity(self.tmux_session)
        # A key can only come from an attached client: back to attended at
        # once, without waiting for the watch's next poll.
        if not getattr(self, "attended", True):
            self._set_attended(True)

        # Write TUI heartbeat (throttled to every 5s)
        now = time.monotonic()
        self._last_keypress = now
        if now - self._last_heartbeat_write >= 5.0:
            self._last_heartbeat_write = now
            write_tui_heartbeat(self.tmux_session)

        # Re-enable summariser if it was auto-paused due to idle (not if cost cap hit)
        if self._summarizer_idle_paused and not self._summarizer.cost_cap_hit:
            self._summarizer_idle_paused = False
            self._summarizer.config.enabled = True
            if not self._summarizer._client:
                from .summarizer_client import SummarizerClient
                self._summarizer._client = SummarizerClient()
            self.notify("AI Summarizer resumed (activity detected)", severity="information")
            self._update_summaries_async()

        # Auto-recover if focus was lost or landed on a non-interactive widget
        # (e.g., clicking the terminal window focuses the preview pane)
        if self._should_recover_focus():
            widget = self._get_focused_widget()
            if widget is not None:
                widget.focus()


        # Handle Escape to close help overlay (#175)
        try:
            from .tui_widgets import HelpOverlay
            help_overlay = self.query_one("#help-overlay", HelpOverlay)
            if help_overlay.has_class("visible"):
                if event.key == "escape":
                    help_overlay.remove_class("visible")
                    self._dialog_did_close()
                    event.stop()
                elif help_overlay.scroll_key(event.key):
                    # PgUp/PgDn/Home/End scroll a help taller than the screen
                    event.stop()
        except Exception:
            pass


    def action_quit(self) -> None:
        """Quit: in split mode, detach the client AND stop the viewer process.

        Detaching returns the user to their previous tmux session; exiting the
        app stops the CPU it spends rendering (the old behaviour only detached,
        leaving the viewer running). The pane is launched via cli/split.py's
        _hold_wrapper, so after exit the pane holds (split preserved) with a
        one-key relaunch. The monitor daemon is a separate process and keeps
        collecting stats throughout.
        """
        # Detach first so the user returns to their previous session, then
        # exit so the viewer process stops consuming CPU.
        self.split.detach()
        self.exit()

    def action_split_grow(self) -> None:
        """Grow the monitor pane (shrink terminal pane)."""
        self.split.resize_split(3)

    def action_split_shrink(self) -> None:
        """Shrink the monitor pane (grow terminal pane)."""
        self.split.resize_split(-3)

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        """Check if an action should be allowed (#175).

        When help overlay is visible, only allow help toggle and quit.
        Other actions are blocked - pressing those keys just closes help.
        """

        # Only intercept when help is visible
        try:
            from .tui_widgets import HelpOverlay
            help_overlay = self.query_one("#help-overlay", HelpOverlay)
            if help_overlay.has_class("visible"):
                # Allow these actions when help is visible
                if action in ("toggle_help", "quit"):
                    return True
                # Next/previous agent keys scroll the help instead (#510)
                if action in ("focus_next_session", "focus_previous_session"):
                    help_overlay.scroll_lines(1 if action == "focus_next_session" else -1)
                    return False
                # Block all other actions - close help instead
                help_overlay.remove_class("visible")
                self._dialog_did_close()
                return False
        except Exception:
            pass

        # Block actions when any modal is visible (generic — covers all .modal widgets)
        if self._any_modal_visible():
            if action == "quit":
                return True
            return False

        # Default: allow the action
        return True

    def on_unmount(self) -> None:
        """Clean up terminal state on exit"""
        import sys
        # A dialog/sister-view zoom must not outlive the viewer. After exit
        # the pane holds at a relaunch prompt (cli/split.py _hold_wrapper) or
        # is respawned by `overcode tmux`; a leftover zoom would keep the
        # bottom terminal pane hidden behind the monitor.
        try:
            self.split.unzoom()
        except Exception:
            pass
        self._stop_capture_thread()
        self.engine_link.stop()
        # Clean up SSH proxy windows
        self.split.cleanup_ssh_proxies()
        # Stop the summarizer (release API client resources)
        self._summarizer.stop()

        # Flush remaining diagnostic data
        self.record_activity("tui", phase="stop")
        self._flush_activity()
        self._flush_heartbeat()
        if self._prefs.status_change_logging:
            self._flush_status_changes()

        # Ensure mouse tracking is disabled
        sys.stdout.write('\033[?1000l')  # Disable mouse tracking
        sys.stdout.write('\033[?1002l')  # Disable cell motion tracking
        sys.stdout.write('\033[?1003l')  # Disable all motion tracking
        sys.stdout.flush()


def run_tui(
    tmux_session: str,
    sync_target: str,
    diagnostics: bool = False,
    initial_jobs_mode: bool = False,
):
    """Run the monitor as the top pane of the `overcode tmux` split.

    Only `overcode monitor --sync-target` (the command the split puts in its
    top pane) calls this; `overcode`, `overcode monitor` and `overcode tmux`
    build the split first, attaching to it from a plain terminal.

    Args:
        sync_target: the linked session the bottom pane shows; navigating
            agents switches its window. `overcode tmux` creates it.
    """
    import os
    import sys

    from .paths import mark_entry_point

    mark_entry_point()

    # Ensure we're using a proper terminal
    if not sys.stdout.isatty():
        print("Error: Must run in a TTY terminal", file=sys.stderr)
        sys.exit(1)

    # Force terminal size detection
    os.environ.setdefault('TERM', 'xterm-256color')

    # tmux/git many times a second: start them with posix_spawn (#486)
    from . import spawn
    spawn.install()

    app = SupervisorTUI(tmux_session, diagnostics=diagnostics,
                        initial_jobs_mode=initial_jobs_mode, sync_target=sync_target)
    # Use driver=None to auto-detect, and size will be detected from terminal
    app.run()
