"""
Textual TUI for Overcode monitor.

TODO: Split this file into smaller modules for maintainability:
- tui_core.py: Main App class and core lifecycle
- tui_panels.py: Panel widgets (StatusPanel, AgentPanel, etc.)
- tui_commands.py: Command handlers and actions
- tui_keybindings.py: Key bindings and input handling
"""

from datetime import datetime
from typing import List, Optional
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
from . import tui_engine
from .engine_socket import EngineClient
from .monitor_daemon import (
    is_monitor_daemon_running,
)
from .pid_utils import is_daemon_lock_held, spawn_daemon
from .tmux_utils import _build_tmux_cmd as _tmux_base
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
from .sister_poller import SisterPoller, SisterState
from .usage_monitor import UsageMonitor
from .implementations import RealTmux
from .tmux_utils import get_pane_base_index, SSH_PROXY_WINDOW_PREFIX
from .worker_guard import single_flight, worker_cancelled
from .tui_helpers import (
    format_duration,
)
from .tui_logic import (
    sort_sessions,
    filter_visible_sessions,
    compute_child_counts,
    running_job_counts,
    compute_tree_metadata,
    compute_session_widget_diff,
    detect_display_changes,
    windows_needing_resize,
)
from .tui_widgets import (
    HelpOverlay,
    PreviewPane,
    DaemonPanel,
    TuiLogPanel,
    DaemonStatusBar,
    StatusTimeline,
    SessionSummary,
    JobSummary,
    CommandBar,
    SummaryConfigModal,
    NewAgentDefaultsModal,
    SkillsModal,
    TmuxConfigModal,
    PassthruConfigModal,
    NewAgentModal,
    RenameAgentModal,
    SummaryPromptLab,
    JourneyPanel,
    AgentSelectModal,
    SisterSelectionModal,
    InstructionHistoryModal,
    CommandPalette,
    ColumnHeader,
    JumpCandidate,
)
from .tui_actions import (
    NavigationActionsMixin,
    ViewActionsMixin,
    DaemonActionsMixin,
    SessionActionsMixin,
    InputActionsMixin,
)
from .tui_actions.activity import ActivityMixin
from .tui_actions.view_control import ViewControlMixin
from .tui_actions.overagent import JourneyMixin, OveragentMixin
from .tui_actions.mentor import MentorMixin

# Event-loop heartbeat probe: the 5 s flush normally drains ~55 rows, so this
# only bites if the flush timer never runs. Without it the buffer grew for the
# life of the process (~10 rows/s => >100 MB after 15 h) whenever the probe
# was disabled, because _mark_event kept appending with no flush scheduled.
HEARTBEAT_LOG_MAX_ENTRIES = 10_000

# Periodic timer cadences (seconds). These are the product's freshness
# contract and are never changed to save CPU. Since 0.6.0 the TUI is a view
# of the engine (docs/design/engine-0.6.md): statuses, stats, git and burn
# arrive on engine.sock as the engine publishes them, so no timer here
# detects, reads stats or polls the daemon's state file. What remains:
# focused_pane is the one capture loop (the focused agent's terminal, for
# its pane-derived columns and the preview) and the row clock (durations
# and countdowns tick); daemon_status is the status bar's local checks
# (supervisor/API server/summarizer processes, usage, mean spin) and the
# engine-connection watch; heartbeat_probe is the event-loop diagnostic;
# attended_watch is the "is anyone looking" poll the engine is told about.
TIMER_INTERVALS = {
    "heartbeat_probe": 0.1,
    "focused_pane": 0.25,
    "daemon_status": 1,
    "focused_job_pane": 1,
    "attended_watch": 1,
    "focused_sister": 1.5,
    "summarizer": 5,
    "refresh_jobs": 5,
    "heartbeat_flush": 5,
    "activity_flush": 2,
    "view_control": 1,
    "status_changes": 5,
    "refresh_sessions": 10,
    "sister_poll": 10,
    "agent_resize": 15,
    "timeline": 30,
}

# Initial delay before each timer's first tick. The intervals above share
# common multiples (5/10/15/30 s), so timers started at the same instant fire
# together forever and their main-thread apply callbacks stack up. Distinct
# offsets (mod 5 s) spread them without touching any cadence. The >= 1 s
# timers are also kept off each other and off the 250 ms focused-pane grid:
# an offset that is not a multiple of 0.25 never lands on a focused tick
# (the hour-long test in test_tui_timers checks every pair). The 0.25 s and
# 0.1 s timers are the grid itself and stay at phase 0.
TIMER_PHASE_OFFSETS = {
    "heartbeat_probe": 0.0,
    "focused_pane": 0.0,
    "daemon_status": 1.55,
    "focused_job_pane": 0.85,
    "attended_watch": 0.45,
    "focused_sister": 0.15,
    "agent_resize": 0.7,
    "refresh_jobs": 1.1,
    "refresh_sessions": 2.3,
    "status_changes": 2.9,
    "summarizer": 3.3,
    "timeline": 3.9,
    "sister_poll": 4.2,
    "heartbeat_flush": 4.6,
    "activity_flush": 1.95,
    "view_control": 1.05,
}

# Timers paused while no tmux client is attached to the pane the TUI runs
# in: the capture, the clock, every refresh and every render-driving apply.
# What keeps running: attended_watch (the signal itself, one tmux command a
# second) and the cheap flush timers that drain buffers. The engine keeps
# recording, and its bells still reach this TUI (notifications go out while
# detached). The whole set resumes, and a full refresh runs, the moment a
# client attaches again (_on_attended_changed).
PAUSED_WHEN_UNATTENDED = frozenset(
    {
        "heartbeat_probe",
        "focused_pane",
        "daemon_status",
        "focused_job_pane",
        "focused_sister",
        "summarizer",
        "refresh_jobs",
        "refresh_sessions",
        "sister_poll",
        "agent_resize",
        "timeline",
    }
)

# A dropped or absent engine.sock is shown after this long (a normal start
# connects well within it), and the daemon is (re)started at most this often
# while the engine stays away.
ENGINE_BANNER_GRACE_SECONDS = 1.5
ENGINE_RESTART_EVERY_SECONDS = 10.0
ENGINE_BANNER_TEXT = "  ⚠ engine not running — starting…  (showing the last known state)  "


class SupervisorTUI(
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
                 initial_jobs_mode: bool = False, sync_target: Optional[str] = None):
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
        # Max repo/branch/name widths for alignment in full detail mode
        self.max_repo_width: int = 10
        self.max_branch_width: int = 10
        self.max_name_width: int = 10
        self.all_names_match_repos: bool = False
        self.column_widths: list = []  # Per-cell column widths for alignment
        # {column id: value} for columns every row shows the same value in —
        # hidden, with the value noted in the header row (uniform_columns)
        self.uniform_columns: dict = {}
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
        # engine.sock and renders what it publishes. The client is started
        # on mount; _engine_agents is the last snapshot's agents.
        self._engine_client: Optional[EngineClient] = None
        self._engine_connected: bool = False
        self._engine_agents: dict = {}
        self._engine_fleet: dict = {}
        self._engine_down_since: float = time.monotonic()
        self._engine_start_attempt: float = float("-inf")
        self._engine_focus: Optional[str] = None
        self._engine_pending = None  # the latest snapshot, not yet applied
        self._engine_apply_posted = False
        # Visits this TUI made that the engine may not have published yet
        self._visited_here: dict[str, float] = {}
        # The focused-pane capture in flight (one at a time, never queued)
        self._focused_capture_in_flight = False
        # Rows changed while detached; repainted on re-attach
        self._repaint_on_attach = False
        # Track whether sessions have been loaded at least once (for startup sequencing)
        self._initial_sessions_loaded = False
        # Track attention jump state (for 'b' key cycling)
        self._attention_jump_index = 0
        self._attention_jump_list: list = []  # Cached list of sessions needing attention
        # Instruction history for reinject modal (#376)
        self._instruction_history: list = []
        # Pending double-press confirmations: action_key -> (session_name | None, timestamp)
        self._pending_confirmations: dict[str, tuple[str | None, float]] = {}
        # Tmux interface for sync operations
        self._tmux = RealTmux()
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
        # Every periodic Timer by TIMER_INTERVALS name, for pause/resume.
        self._periodic_timers: dict[str, object] = {}
        # SSH proxy windows for remote agents: session_id -> tmux window name
        self._ssh_proxies: dict[str, str] = {}
        # TmuxManager instance for creating proxy windows
        from .tmux_manager import TmuxManager
        self._tmux_manager = TmuxManager(tmux_session)
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
        self._engine_start_attempt = time.monotonic()
        self._ensure_monitor_daemon()

        # Clean up stale SSH proxy windows from previous TUI sessions
        self._cleanup_stale_ssh_proxies()

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
        self._start_engine_client()

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

    def _start_periodic(self, name: str, callback) -> None:
        """Start the ``name`` timer at its TIMER_INTERVALS cadence, phase-shifted.

        A non-zero TIMER_PHASE_OFFSETS entry delays the first tick by that
        many seconds (set_timer, then set_interval), so the timer fires at
        offset + k * interval instead of k * interval. The Timer is kept in
        ``self._periodic_timers`` so the attended watcher can pause and
        resume it. Whether it starts paused is decided when it actually
        starts — after the delay — from the attended state at that moment:
        a PAUSED_WHEN_UNATTENDED timer starts paused while nobody is
        attached.
        """
        interval = TIMER_INTERVALS[name]
        delay = TIMER_PHASE_OFFSETS[name]

        def start() -> None:
            start_paused = name in PAUSED_WHEN_UNATTENDED and not getattr(self, "attended", True)
            self._periodic_timers[name] = self.set_interval(interval, callback, pause=start_paused)

        if delay > 0:
            self.set_timer(delay, start)
        else:
            start()

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
        for name in PAUSED_WHEN_UNATTENDED:
            timer = self._periodic_timers.get(name)
            if timer is None:
                continue
            if attended:
                timer.resume()
            else:
                timer.pause()
        if self._engine_client is not None:
            self._engine_client.set_visible(attended)
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

    def _start_engine_client(self) -> None:
        """Subscribe to engine.sock; the client reconnects by itself for good."""
        if self._engine_client is not None:
            return
        client = EngineClient(
            tui_engine.engine_socket_path(self.tmux_session),
            on_change=self._on_engine_change,
            on_bell=self._on_engine_bell,
            on_connection=self._on_engine_connection,
        )
        self._engine_client = client
        # Stated now, restated by the client on every (re)connect
        client.set_visible(self.attended)
        client.set_burn_window(self._burn_hours() or None)
        self._report_focus(self._get_focused_widget())
        client.set_focus(self._engine_focus)
        client.start()

    def _burn_hours(self) -> float:
        """The burn window: the same baseline as the mean spin (0: none)."""
        return (getattr(self, "baseline_minutes", 0) or 0) / 60.0

    def _report_burn_window(self) -> None:
        """Ask the engine for the current window's burn; re-read what it has."""
        if self._engine_client is not None:
            self._engine_client.set_burn_window(self._burn_hours() or None)
        self._apply_engine_views(force=True)

    def _report_focus(self, widget: Optional["SessionSummary"]) -> None:
        """Tell the engine which local agent has focus; hand back the old row's pane."""
        sid = widget.session.id if widget is not None and not widget.session.is_remote else None
        if sid == self._engine_focus:
            return
        for w in self.query(SessionSummary):
            if w.local_pane and w.session.id != sid:
                w.end_local_pane()
        self._engine_focus = sid
        if self._engine_client is not None:
            self._engine_client.set_focus(sid)

    class EngineEvent(events.Message):
        """Something the engine client heard, for the main thread."""

        def __init__(self, kind: str, args: tuple) -> None:
            super().__init__()
            self.kind = kind
            self.args = args

    # The client's callbacks run on its thread. They post a message rather
    # than call_from_thread, which would block the client on the main
    # thread (and deadlock an exit that joins the client). Snapshots
    # coalesce: a burst of deltas is applied once, as the latest.

    def _on_engine_change(self, snapshot) -> None:
        self._engine_pending = snapshot
        if not getattr(self, "_engine_apply_posted", False):
            self._engine_apply_posted = True
            self.post_message(self.EngineEvent("snapshot", ()))

    def _on_engine_bell(self, agent: str, episode: dict) -> None:
        self.post_message(self.EngineEvent("bell", (agent, episode)))

    def _on_engine_connection(self, connected: bool) -> None:
        self.post_message(self.EngineEvent("connection", (connected,)))

    def on_supervisor_tui_engine_event(self, event: "SupervisorTUI.EngineEvent") -> None:
        if event.kind == "snapshot":
            self._engine_apply_posted = False
            snapshot = self._engine_pending
            if snapshot is not None:
                self._apply_engine_snapshot(snapshot)
        elif event.kind == "bell":
            self._ring(*event.args)
        elif event.kind == "connection":
            self._set_engine_connected(*event.args)

    def _apply_engine_snapshot(self, snapshot) -> None:
        """Main thread: the engine's current snapshot (a full one or a delta applied)."""
        self._mark_event("apply_engine_start")
        if not self._engine_connected:
            # A snapshot means a live connection, whatever order callbacks land in
            self._set_engine_connected(True)
        self._engine_agents = snapshot.agents
        self._engine_fleet = snapshot.fleet
        self._apply_engine_views()
        self._mark_event("apply_engine_end")

    def _apply_engine_views(self, force: bool = False) -> None:
        """Give each local row its agent's view; repaint only the rows that changed.

        ``force`` re-applies unchanged views too (the burn window moved).
        Fleet-wide flags that decide whether a column shows are recomputed;
        when one flips, or the aligned column widths move, every row
        repaints, since every row's layout changed.
        """
        agents = self._engine_agents
        hours = self._burn_hours()
        changed = []
        for widget in self.query(SessionSummary):
            if widget.session.is_remote:
                continue
            view = agents.get(widget.session.id)
            if view is None or (view == widget.engine and not force):
                continue
            old_status = widget.detected_status
            if widget.apply_engine(view, hours, self._visited_here.get(widget.session.id)):
                changed.append(widget)
                if self._prefs.status_change_logging and old_status != widget.detected_status:
                    self._log_status_change(
                        widget.session.name, old_status, widget.detected_status,
                        source="engine", focused=widget.session.id == self._engine_focus,
                    )
        self._apply_engine_to_status_bar()
        if not changed:
            return
        if self._update_fleet_flags():
            changed = list(self.query(SessionSummary))
        if not self.attended:
            self._repaint_on_attach = True
            return
        self._repaint(changed)

    def _repaint(self, changed: list) -> None:
        """Re-align columns, then repaint ``changed`` (every row if widths moved)."""
        old_widths = list(self.column_widths)
        self._column_widths_dirty = True
        self._recompute_cell_column_widths()
        rows = list(self.query(SessionSummary)) if self.column_widths != old_widths else changed
        for widget in rows:
            widget.refresh_if_changed()
        if self.preview_visible:
            self._update_preview()

    def _repaint_rows(self) -> None:
        """Every row from what it holds now (after a detach, or a full refresh)."""
        self._repaint_on_attach = False
        self._update_fleet_flags()
        self._repaint(list(self.query(SessionSummary)))

    def _update_fleet_flags(self) -> bool:
        """The any_* column gates that depend on engine data; True if one flipped."""
        widgets = list(self.query(SessionSummary))
        flags = {
            "any_has_burn": any(
                w.window_burn is not None
                and (w.window_burn.tokens_per_hour > 0 or w.window_burn.cost_per_hour > 0)
                for w in widgets
            ),
            "any_has_subtree_cost": any(w.subtree_cost_usd > 0 for w in widgets),
            "any_is_sleeping": any(w.detected_status == "busy_sleeping" for w in widgets),
            "any_has_status_detail": any(w.status_detail is not None for w in widgets),
        }
        flipped = False
        for widget in widgets:
            for name, value in flags.items():
                if getattr(widget, name) != value:
                    setattr(widget, name, value)
                    flipped = True
        return flipped

    def _apply_engine_to_status_bar(self) -> None:
        """The daemon status bar's fleet fields and the aggregate burn, from the snapshot."""
        try:
            bar = self.query_one("#daemon-status", DaemonStatusBar)
        except NoMatches:
            return
        local_ids = {s.id for s in self.sessions if not s.is_remote}
        agents = {sid: v for sid, v in self._engine_agents.items()
                  if not local_ids or sid in local_ids}
        bar.monitor_state = tui_engine.monitor_state(self._engine_fleet, agents)
        bar.presence_idle_since = self._engine_fleet.get("presence_idle_since")
        bar.engine_connected = self._engine_connected
        bar._asleep_session_ids = {s.id for s in self.sessions if s.is_asleep}
        bar._burn_window_hours = self._burn_hours()
        bar._burn_stats = tui_engine.fleet_burn(agents, self._burn_hours())
        if self.attended:
            bar.refresh()

    def _set_engine_connected(self, connected: bool) -> None:
        """Main thread: the subscription came up or went away."""
        if connected == self._engine_connected:
            return
        self._engine_connected = connected
        if connected:
            self._show_engine_banner(False)
        else:
            self._engine_down_since = time.monotonic()
            # Show the banner at once: the engine was there and went away
            self._show_engine_banner(True)
            self._start_engine()
        try:
            bar = self.query_one("#daemon-status", DaemonStatusBar)
            bar.engine_connected = connected
            bar.refresh()
        except NoMatches:
            pass

    def _engine_watch(self) -> None:
        """Each second: a missing engine is shown, and started again now and then."""
        if self._engine_connected:
            return
        now = time.monotonic()
        if now - self._engine_down_since >= ENGINE_BANNER_GRACE_SECONDS:
            self._show_engine_banner(True)
        if now - self._engine_start_attempt >= ENGINE_RESTART_EVERY_SECONDS:
            self._start_engine()

    def _start_engine(self) -> None:
        """Start the monitor daemon (the engine) unless it is already starting."""
        self._engine_start_attempt = time.monotonic()
        self._ensure_monitor_daemon()

    def _show_engine_banner(self, show: bool) -> None:
        """The "engine not running" banner; the agent list dims while it shows."""
        try:
            banner = self.query_one("#engine-banner", Static)
            container = self.query_one("#sessions-container", ScrollableContainer)
        except NoMatches:
            return
        if show:
            banner.add_class("visible")
            container.add_class("engine-stale")
        else:
            banner.remove_class("visible")
            container.remove_class("engine-stale")

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
        if widget is not None and not widget.session.is_remote and self._engine_client is not None:
            self._engine_client.visit(session_id)
        if widget is not None and widget.is_unvisited_stalled:
            widget.is_unvisited_stalled = False
            widget.refresh()

    # ── The focused agent's pane: the one capture loop ─────────────────

    def _focused_pane_tick(self) -> None:
        """Every 250 ms: capture the focused agent's pane; tick every row's clock.

        The capture feeds the focused row's pane-derived columns (and the
        preview); the rest of the row, and every other row, is the engine's.
        The same worker re-reads the session table (a stat while
        sessions.json is unchanged). The clock repaints a row only if it
        would now draw differently (a duration or countdown moved).
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
            self._capture_focused_async(widget.session.id if window else None, window)
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

    @work(thread=True, exclusive=True, group="focused_pane")
    def _capture_focused_async(self, session_id: Optional[str], window: Optional[str]) -> None:
        """Worker: one capture-pane of the focused agent (if any), and the session table.

        The session table is a stat while sessions.json is unchanged; a
        changed one (a rename, standing orders, a value) reaches the rows
        within a tick.
        """
        try:
            content = (self.detector.get_pane_content(window) or "") if window else None
            fresh = {s.id: s for s in self.session_manager.list_sessions()}
            self.call_from_thread(self._apply_focused_capture, session_id, content, fresh)
        finally:
            self._focused_capture_in_flight = False

    def _apply_focused_capture(self, session_id: Optional[str], content: Optional[str],
                               fresh: dict) -> None:
        """Main thread: the focused row's pane, and fresh session metadata for every row."""
        self._mark_event("apply_focused_start")
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
        if changed:
            self._repaint(changed)
        self._mark_event("apply_focused_end")

    # ── The status bar ─────────────────────────────────────────────────

    def update_daemon_status(self) -> None:
        """Each second: the engine watch, and the status bar's own local checks."""
        self._engine_watch()
        self._fetch_daemon_status_async()

    @work(thread=True, group="daemon_status")
    @single_flight("daemon_status")
    def _fetch_daemon_status_async(self) -> None:
        """Worker: what the status bar shows that is not about agents.

        Whether the supervisor and API server are running and the
        summarizer is reachable (pid files), the account's usage (fetched
        at most every 5 minutes) and the mean spin over the baseline window
        (the daemon's history CSV, read incrementally). Everything about
        the agents themselves comes from the engine's snapshot.
        """
        try:
            daemon_bar = self.query_one("#daemon-status", DaemonStatusBar)
        except NoMatches:
            return
        agents = list(self._engine_agents.values())
        # Subscription usage is fleet-level, not per-agent: the widget shows
        # the user's own Claude limits, which stay true for a mixed fleet as
        # long as one agent is on a subscription-metered backend.
        if self._fleet_has_subscription_usage(agents):
            self._usage_monitor.fetch()  # Internally throttled to 5min
        asleep = {s.id for s in self.sessions if s.is_asleep}
        active_names = [v.get("name") for sid, v in self._engine_agents.items()
                        if sid not in asleep and v.get("name")]
        daemon_bar.fetch_local_state(
            baseline_minutes=getattr(self, "baseline_minutes", 0),
            active_session_names=active_names,
        )
        if self.has_sisters:
            daemon_bar._sister_states = self._sister_poller.get_sister_states()
        self.call_from_thread(self._apply_daemon_status, daemon_bar)

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

    def _apply_daemon_status(self, daemon_bar: "DaemonStatusBar") -> None:
        """Main thread: repaint the status bar (no I/O)."""
        daemon_bar._usage_snapshot = self._usage_monitor.snapshot
        daemon_bar.engine_connected = self._engine_connected
        daemon_bar.refresh()

    def update_timeline(self) -> None:
        """Update the status timeline widget (kicks off background worker)"""
        self._fetch_timeline_async()

    @work(thread=True, group="timeline")
    @single_flight("timeline")
    def _fetch_timeline_async(self) -> None:
        """Read timeline CSV data off the main thread, then apply to UI."""
        try:
            timeline = self.query_one("#timeline", StatusTimeline)
        except NoMatches:
            return

        # Snapshot sessions + filter state for the worker (avoid race with main thread)
        sessions = filter_visible_sessions(
            active_sessions=list(self.sessions),
            terminated_sessions=list(self._terminated_sessions.values()),
            hide_asleep=self.hide_asleep,
            show_terminated=self.show_terminated,
            show_done=self.show_done,
            collapsed_parents=self.collapsed_parents if self._prefs.sort_mode == "by_tree" else None,
            tag_filter=self.tag_filter,
        )

        # Heavy CSV I/O happens here in the worker thread
        presence_history, agent_histories = timeline.fetch_history_data(sessions)

        # Merge remote timeline data from sisters (#296)
        if self._sister_poller.has_sisters:
            remote_histories = self._sister_poller.poll_all_timelines(
                timeline.timeline_hours
            )
            agent_histories.update(remote_histories)

        # Apply on main thread
        self.call_from_thread(
            timeline.apply_history_data, sessions, presence_history, agent_histories
        )

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
        if self.in_split:
            self._resize_agent_windows_async()

    @work(thread=True, group="agent_resize")
    @single_flight("agent_resize")
    def _resize_agent_windows_async(self) -> None:
        """Worker: read bottom-pane size, then resize each agent window."""
        import subprocess
        try:
            result = subprocess.run(
                [*_tmux_base(), "display-message", "-t", self._bottom_pane_target(),
                 "-p", "#{pane_width} #{pane_height}"],
                capture_output=True, text=True, timeout=2,
            )
        except (subprocess.SubprocessError, OSError):
            return
        if result.returncode != 0 or not result.stdout.strip():
            return
        parts = result.stdout.strip().split()
        if len(parts) != 2:
            return
        try:
            width, height = int(parts[0]), int(parts[1])
        except ValueError:
            return
        sync_session = self.tmux_sync_target
        # Local sessions only — remote sessions have their own resize path
        windows = [
            session.tmux_window for session in list(self.sessions)
            if not getattr(session, "is_remote", False)
            and getattr(session, "tmux_window", None)
        ]
        # One list-windows call tells us which windows are already the right
        # size; resize-window is not free even when nothing changes (it fires
        # layout hooks and redraws), so in the steady state this sweep sends
        # no per-window commands at all.
        current_sizes: dict[str, tuple[int, int]] = {}
        try:
            listed = subprocess.run(
                [*_tmux_base(), "list-windows", "-t", sync_session,
                 "-F", "#{window_name}\t#{window_width}\t#{window_height}"],
                capture_output=True, text=True, timeout=2,
            )
            if listed.returncode == 0:
                for line in listed.stdout.splitlines():
                    parts = line.split("\t")
                    if len(parts) == 3:
                        try:
                            current_sizes[parts[0]] = (int(parts[1]), int(parts[2]))
                        except ValueError:
                            continue
        except (subprocess.SubprocessError, OSError):
            pass  # Unknown sizes → resize everything, as before
        for window in windows_needing_resize(current_sizes, windows, width, height):
            if worker_cancelled():
                return  # app exit; the next pass picks up the remaining windows
            target = f"{sync_session}:{window}"
            try:
                subprocess.run(
                    [*_tmux_base(), "resize-window", "-t", target,
                     "-x", str(width), "-y", str(height)],
                    capture_output=True, timeout=2,
                )
            except (subprocess.SubprocessError, OSError):
                continue

    def _update_capture_lines(self) -> None:
        """Scale capture buffer to at least 2x terminal height."""
        height = self.size.height if self.size.height > 0 else 40
        self.detector.capture_lines = max(DEFAULT_CAPTURE_LINES, 2 * height)

    def refresh_sessions(self) -> None:
        """Refresh session list (kicks off background worker).

        Uses launcher.list_sessions() to detect terminated sessions
        (tmux windows that no longer exist, e.g., after machine reboot).
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
        self._gc_terminated_sessions()
        # Detect new sessions for timeline refresh (#244)
        old_names = {s.name for s in self.sessions}
        # Which agent is highlighted, read against the *old* order — the
        # index alone is meaningless once self.sessions is replaced (#471).
        selected_id = self._selected_session_id()

        # Merge local + remote sessions (#245), filtering disabled sisters (#323)
        self.sessions = sessions + self._visible_remote_sessions()
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

        # First-load sync: align the external tmux pane with the TUI's focused
        # agent exactly once. The periodic-refresh guard in the focus watcher
        # (see _user_navigated) would otherwise leave the bottom pane stranded
        # on whatever window tmux happened to pick when `overcode tmux` opened
        # the split.
        if not self._initial_tmux_sync_done and self.in_split:
            try:
                widget = self._get_focused_widget()
            except Exception:
                widget = None
            if widget is not None:
                self._sync_tmux_window(widget)
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

        Collects render_summary_cells() output from every SessionSummary
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
        from .summary_columns import render_summary_cells, compute_column_widths
        widgets = list(self.query(SessionSummary))
        if not widgets:
            self.column_widths = []
            self._column_widths_dirty = False
            return
        all_cells = []
        for w in widgets:
            ctx = w._build_column_context()
            cells = render_summary_cells(ctx, column_filter=w.column_visible)
            all_cells.append(cells)
        self.column_widths = compute_column_widths(all_cells)
        self._column_widths_dirty = False
        # Update column headers if visible
        self._update_column_headers()

    def _resolve_remote_parents(self) -> None:
        """Resolve parent_session_id for remote children whose parents are local or on other sisters.

        After merging local + remote sessions, remote children may have parent_name set
        but parent_session_id unset (because the parent wasn't available when the sister
        was polled). This function fixes cross-machine parent relationships by matching
        parent_name to session names in the combined list.

        Fixes flipping behavior where remote children appeared/disappeared from their
        parent's subtree (#245).
        """
        # Build name -> session mapping for fast lookup
        name_to_session = {s.name: s for s in self.sessions}

        for session in self.sessions:
            # Skip if already has parent_session_id set
            if session.parent_session_id:
                continue
            # Only process remote sessions that don't have a parent yet
            if not getattr(session, 'is_remote', False):
                continue

            # Extract parent_name from remote_daemon_state (contains parent_name from the sister)
            parent_name = None
            remote_daemon = getattr(session, 'remote_daemon_state', None)
            if remote_daemon and isinstance(remote_daemon, dict):
                parent_name = remote_daemon.get('parent_name')

            # Try to resolve parent by name in the merged session list
            if parent_name and parent_name in name_to_session:
                parent_session = name_to_session[parent_name]
                session.parent_session_id = parent_session.id

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

    def _any_modal_visible(self) -> bool:
        """Check if any modal dialog is currently visible.

        Queries for widgets with both 'modal' and 'visible' CSS classes.
        New modals automatically get focus protection and key blocking
        by adding classes="modal" in compose().
        """
        return bool(self.query(".modal.visible"))

    def _any_dialog_visible(self) -> bool:
        """Check if any dialog (modal or help overlay) is visible."""
        if self._any_modal_visible():
            return True
        try:
            from .tui_widgets import HelpOverlay
            help_overlay = self.query_one("#help-overlay", HelpOverlay)
            if help_overlay.has_class("visible"):
                return True
        except Exception:
            pass
        return False

    @property
    def in_split(self) -> bool:
        """True when running as the top pane of the `overcode tmux` split.

        run_tui always passes the linked session, so this is the normal
        case. An app built without one (unit tests) never touches tmux:
        no zoom, no window switching, no resizing, no detach.
        """
        return self.tmux_sync_target is not None

    def _tui_pane_target(self) -> str:
        """Return tmux target for the TUI (top) pane, respecting pane-base-index."""
        base = get_pane_base_index()
        return f"overcode:overcode-tmux.{base}"

    def _bottom_pane_target(self) -> str:
        """Return tmux target for the bottom (terminal) pane, respecting pane-base-index."""
        base = get_pane_base_index()
        return f"overcode:overcode-tmux.{base + 1}"

    def _dialog_will_open(self) -> None:
        """Zoom the tmux monitor pane when a dialog opens.

        Uses tmux's pane zoom to temporarily hide the bottom (terminal)
        pane, giving the TUI the full window for rendering dialogs.
        """
        if not self.in_split:
            return
        import subprocess
        target = self._tui_pane_target()
        # Don't zoom if already zoomed
        info = subprocess.run(
            [*_tmux_base(), "display-message", "-t", target, "-p", "#{window_zoomed_flag}"],
            capture_output=True, text=True,
        )
        if info.returncode == 0 and info.stdout.strip() == "1":
            return
        subprocess.run(
            [*_tmux_base(), "resize-pane", "-t", target, "-Z"],
            capture_output=True,
        )

    def _dialog_did_close(self) -> None:
        """Unzoom the tmux monitor pane when all dialogs are closed."""
        if not self.in_split:
            return
        # Don't unzoom if another dialog is still visible
        if self._any_dialog_visible():
            return
        # Don't unzoom if sister view or jobs view is holding the zoom open
        if self._sister_zoom_active:
            return
        if self.tui_mode == "jobs":
            return
        self._unzoom_tui_pane()

    def _unzoom_tui_pane(self) -> None:
        """Clear the tmux zoom on the TUI pane, if set, revealing the bottom pane.

        ``resize-pane -Z`` toggles, so check ``window_zoomed_flag`` first.
        """
        if not self.in_split:
            return
        import subprocess
        target = self._tui_pane_target()
        info = subprocess.run(
            [*_tmux_base(), "display-message", "-t", target, "-p", "#{window_zoomed_flag}"],
            capture_output=True, text=True,
        )
        if info.returncode == 0 and info.stdout.strip() == "1":
            subprocess.run(
                [*_tmux_base(), "resize-pane", "-t", target, "-Z"],
                capture_output=True,
            )

    def _enter_sister_view(self) -> None:
        """Zoom TUI pane and show preview for viewing a sister agent.

        Called automatically when navigating to a remote agent without SSH. The preview pane shows the sister's polled
        pane_content. The bottom terminal pane is hidden via tmux zoom.
        """
        if self._sister_zoom_active:
            return
        self._sister_zoom_active = True
        # Zoom the TUI pane (same as dialog zoom)
        self._dialog_will_open()
        # Show preview pane for sister content
        self.preview_visible = True

    def _exit_sister_view(self) -> None:
        """Restore the split layout when navigating back to a local agent.

        Reverses _enter_sister_view(): hides preview pane and
        unzooms the TUI pane to reveal the bottom terminal pane.
        """
        if not self._sister_zoom_active:
            return
        self._sister_zoom_active = False
        # Hide the preview pane (local agents show in the bottom pane)
        self.preview_visible = False
        # Unzoom — but only if no dialog is holding the zoom open
        if not self._any_dialog_visible():
            self._unzoom_tui_pane()

    def _should_recover_focus(self) -> bool:
        """Check if focus recovery should run.

        Returns False when an overlay is visible (help, any modal), the
        command bar is open, or focus is already on a session/input widget.
        """
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
        self._report_focus(widget)
        if self.preview_visible:
            self._update_preview()
        # Only sync tmux on explicit user navigation, not programmatic restores.
        # Periodic refresh_sessions → update_session_widgets can temporarily lose
        # remote sessions (sister poll timing), causing focused_session_index to
        # land on a local agent and switch the bottom pane away.
        if getattr(self, '_user_navigated', False):
            self._user_navigated = False
            self._sync_tmux_window(widget)
            self._fix_window_size_if_needed(widget)

    # ── Sister integration (#245) ──────────────────────────────────────

    def _poll_sisters(self) -> None:
        """Kick off sister polling in background thread."""
        self._poll_sisters_async()

    @work(thread=True, group="sister_poll")
    @single_flight("sister_poll")
    def _poll_sisters_async(self) -> None:
        """Fetch remote sessions from all sisters."""
        remote = self._sister_poller.poll_all()
        self.call_from_thread(self._apply_remote_sessions, remote)

    def _visible_remote_sessions(self) -> List[Session]:
        """Return remote sessions excluding disabled sisters (#323)."""
        disabled = self._prefs.disabled_sisters
        if not disabled:
            return list(self._remote_sessions)
        disabled_urls = {
            s.url for s in self._sister_poller.get_sister_states()
            if s.name in disabled
        }
        return [s for s in self._remote_sessions if s.source_url not in disabled_urls]

    def _apply_remote_sessions(self, remote_sessions: List[Session]) -> None:
        """Store remote sessions and rebuild widget list."""
        self._mark_event("apply_sisters_start")
        had_remote = len(self._remote_sessions) > 0
        self._remote_sessions = remote_sessions
        self.refresh_sessions()
        # On first remote arrival, kick off timeline update so remote
        # agent timelines appear immediately instead of waiting for the
        # 30s timeline cycle (#296).
        if not had_remote and remote_sessions:
            self.update_timeline()
        self._mark_event("apply_sisters_end")

    def _poll_focused_sister(self) -> None:
        """Fast-poll the focused remote agent (1.5s) for responsive preview."""
        focused = self._get_focused_widget()
        if focused is None or not focused.session.is_remote:
            return
        session = focused.session
        if not session.source_url or not session.name:
            return
        self._poll_focused_sister_async(
            session.source_url, session.source_api_key, session.name, session.id
        )

    @work(thread=True, group="focused_sister_poll")
    @single_flight("focused_sister_poll")
    def _poll_focused_sister_async(
        self, source_url: str, source_api_key: str, agent_name: str, session_id: str
    ) -> None:
        """Fetch single remote agent status in background thread."""
        updated = self._sister_poller.poll_single_agent(source_url, source_api_key, agent_name)
        if updated is not None:
            self.call_from_thread(self._apply_focused_sister, updated, session_id)

    def _apply_focused_sister(self, updated_session: "Session", session_id: str) -> None:
        """Apply fast-polled remote session data to the matching widget."""
        # Update the session in _remote_sessions so the fast path picks it up
        for i, rs in enumerate(self._remote_sessions):
            if rs.id == session_id:
                self._remote_sessions[i] = updated_session
                break

        # Update the widget directly for immediate feedback
        for widget in self.query(SessionSummary):
            if widget.session.id == session_id:
                widget.session = updated_session
                # Sync pr_number from session (propagates both detection and clearing)
                widget.pr_number = updated_session.pr_number
                widget.apply_remote(self._visited_here.get(session_id))
                widget.refresh()
                break

        # Refresh preview pane if in list_preview mode
        if self.preview_visible:
            self._update_preview()

    def _optimistic_update_remote(self, session_id: str, **fields) -> None:
        """Optimistically update a remote session's local copy for instant UI feedback.

        After a successful remote API call, update the in-memory session and
        widget immediately instead of waiting for the next polling cycle (#305).
        """
        from dataclasses import replace
        for i, rs in enumerate(self._remote_sessions):
            if rs.id == session_id:
                self._remote_sessions[i] = replace(rs, **fields)
                break
        for widget in self.query(SessionSummary):
            if widget.session.id == session_id:
                widget.session = replace(widget.session, **fields)
                widget.refresh()
                break
        if self.preview_visible:
            self._update_preview()

    # ── End sister integration ────────────────────────────────────────

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
        # Capture focus state before DOM mutations
        if preserve_focus:
            _focused_widget = self._get_focused_widget()
            _focused_session_id = _focused_widget.session.id if _focused_widget else None
            _focus_was_on_session = isinstance(self.focused, SessionSummary)
        else:
            _focused_session_id = None
            _focus_was_on_session = False

        container = self.query_one("#sessions-container", ScrollableContainer)

        # Check if any session has a cost budget / oversight timeout / PR
        any_has_budget, any_has_oversight_timeout, any_has_pr = detect_display_changes(
            self.sessions, False, False
        )

        # Subtree cost of a sister's agent comes with its forwarded state; a
        # local agent's with the engine's view (apply_engine)
        remote_subtree = {
            s.id: s.remote_daemon_state['subtree_cost_usd'] for s in self._remote_sessions
            if (getattr(s, 'remote_daemon_state', None) or {}).get('subtree_cost_usd', 0) > 0
        }
        # Also check widget pr_number vars (sticky — survive session replacement)
        if not any_has_pr:
            any_has_pr = any(
                getattr(w, 'pr_number', None) is not None
                for w in self.query(SessionSummary)
            )

        # Each local agent as the engine reports it (model, effort, CPU/RAM)
        views = self._engine_agents
        shown = [tui_engine.session_with_view(s, views.get(s.id, {})) for s in self.sessions]
        any_has_model = any(s.model for s in shown)
        any_has_effort = any(getattr(s, 'effort', None) for s in shown)

        # Check if any agent uses a non-web provider
        any_has_provider = any(
            getattr(s, 'provider', 'web') not in ('web', None, '')
            for s in self.sessions
        )

        # Backend badge only earns a column once the fleet is mixed — a
        # Claude-only dashboard looks exactly as it did before opencode.
        from .backends import session_backend_name
        mixed_backends = len({
            session_backend_name(s) for s in self.sessions
        }) > 1

        # Check if any agent has a non-zero CPU / RAM reading
        any_has_cpu = any(
            (getattr(s, 'cpu_percent', 0.0) or 0.0) > 0.0
            for s in shown
        )
        any_has_ram = any(
            (getattr(s, 'rss_bytes', 0) or 0) > 0
            for s in shown
        )

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
            isinstance(w, Static) and not isinstance(w, SessionSummary)
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
                    old_budget = widget.any_has_budget
                    # Check if anything display-relevant actually changed
                    changed = (
                        force_refresh
                        or widget.session != new_session
                        or old_budget != any_has_budget
                    )
                    widget.session = new_session
                    # Sync display modes
                    widget.emoji_free = self.emoji_free
                    if new_session.is_remote:
                        changed = widget.apply_remote(self._visited_here.get(new_session.id)) or changed
                        widget.subtree_cost_usd = remote_subtree.get(new_session.id, 0.0)
                    if "pr_number" not in widget.engine:
                        # Sync pr_number from session (propagates detection and clearing)
                        widget.pr_number = new_session.pr_number
                    widget.any_has_budget = any_has_budget
                    widget.any_has_oversight_timeout = any_has_oversight_timeout
                    widget.any_has_pr = any_has_pr
                    widget.any_has_model = any_has_model
                    widget.any_has_effort = any_has_effort
                    widget.any_has_provider = any_has_provider
                    widget.mixed_backends = mixed_backends
                    widget.any_has_cpu = any_has_cpu
                    widget.any_has_ram = any_has_ram
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
            if self._update_fleet_flags():
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
                widget.any_has_budget = any_has_budget
                widget.any_has_oversight_timeout = any_has_oversight_timeout
                widget.any_has_pr = any_has_pr
                widget.any_has_model = any_has_model
                widget.any_has_effort = any_has_effort
                widget.any_has_provider = any_has_provider
                widget.mixed_backends = mixed_backends
                widget.oversight_deadline = getattr(session, 'oversight_deadline', None)
                # What the engine (or a sister) already says about it
                if session.is_remote:
                    widget.apply_remote(self._visited_here.get(session.id))
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

    def _update_uniform_columns(self) -> None:
        """Hide the columns every shown agent has the same value in."""
        from .summary_columns import uniform_columns
        widgets = [w for w in self.query(SessionSummary) if w.display]
        self.uniform_columns = uniform_columns([w._build_column_context() for w in widgets])
        for widget in self.query(SessionSummary):
            widget.uniform_columns = self.uniform_columns

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
        def _dismiss() -> None:
            self._bell_dismiss_timers.pop(session_id, None)
            # Only if still focused on this agent
            if self._selected_session_id() == session_id:
                self._visit(session_id)

        self._bell_dismiss_timers[session_id] = self.set_timer(5.0, _dismiss)

    def on_session_summary_clicked(self, message: SessionSummary.Clicked) -> None:
        """A click on an agent's row selects it the way j/k do: focus,
        and the split's bottom pane switches to it."""
        for i, widget in enumerate(self._get_widgets_in_session_order()):
            if widget.session.id != message.session_id:
                continue
            if i == self.focused_session_index:
                # Already selected, so the index watcher won't fire. The
                # pane may have been switched by hand since; bring it back.
                self._sync_tmux_window(widget)
                self._fix_window_size_if_needed(widget)
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
        if is_tree_mode:
            tree_meta = compute_tree_metadata(self.sessions)
            for widget in ordered_widgets:
                meta = tree_meta.get(widget.session.id)
                widget.child_count = meta.child_count if meta else 0
                widget.children_collapsed = widget.session.id in self.collapsed_parents
                widget.tree_prefix = meta.prefix if meta else ""
                widget.tree_depth = meta.depth if meta else 0
        else:
            child_counts = compute_child_counts(self.sessions)
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

    def _sync_tmux_window(self, widget: Optional["SessionSummary"] = None) -> None:
        """Sync external tmux pane to show the focused session's window.

        Switches the window of the linked session (tmux_sync_target) that
        the split's bottom pane shows.

        For remote agents with SSH configured, creates a local SSH proxy
        window that attaches to the remote tmux window. The proxy persists
        so switching back is instant.

        For remote agents without SSH, auto-zooms the TUI pane and shows
        the preview pane with polled content.

        Args:
            widget: The session widget to sync to. If None, uses self.focused.
        """
        if not self.in_split:
            return

        try:
            target = widget if widget is not None else self.focused
            if isinstance(target, SessionSummary):
                session = target.session
                sync_session = self.tmux_sync_target

                if session.is_remote and session.source_ssh:
                    # SSH proxy: create or reuse a local tmux window connected via SSH
                    # Exit sister zoom if it was active (SSH proxy uses the bottom pane)
                    if self._sister_zoom_active:
                        self._exit_sister_view()

                    proxy_window = self._get_or_create_ssh_proxy(session)
                    if proxy_window:
                        self._tmux.select_window(sync_session, proxy_window)
                    return

                # Non-SSH remote: sister zoom (existing behavior)
                if session.is_remote and not self._sister_zoom_active:
                    self._enter_sister_view()
                elif not session.is_remote and self._sister_zoom_active:
                    self._exit_sister_view()

                window_index = session.tmux_window
                # For remote agents without a tmux window, try to sync to parent's window
                if (not window_index or window_index == "") and session.is_remote and session.parent_session_id:
                    parent = self.session_manager.get_session(session.parent_session_id)
                    if parent:
                        window_index = parent.tmux_window

                if window_index is not None and window_index != "":
                    if not self._tmux.select_window(sync_session, window_index):
                        self._select_empty_placeholder(sync_session, session.name)
                        self.notify(
                            f"Window for '{session.name}' no longer exists",
                            severity="warning",
                        )
                else:
                    # Local agent with no tmux_window value — same dead-window
                    # symptom for the user. Fall back to the placeholder.
                    self._select_empty_placeholder(sync_session, session.name)
        except Exception:
            pass  # Silent fail - don't disrupt navigation

    def _select_empty_placeholder(self, sync_session: str, agent_name: str) -> None:
        """Switch the bottom pane to a static placeholder window (#457).

        Called when a real agent window is missing so the user sees a clear
        empty-state instead of stale content from the previous focus target.
        Idempotent — the placeholder is created on first need and reused.
        """
        from .tmux_manager import EMPTY_PLACEHOLDER_WINDOW
        msg = (
            f"\n  This agent has no tmux window.\n"
            f"  ({agent_name} may have been killed, is mid-launch,\n"
            f"   or its window vanished.)\n"
        )
        try:
            if self._tmux.ensure_empty_placeholder_window(
                sync_session, EMPTY_PLACEHOLDER_WINDOW, msg
            ):
                self._tmux.select_window(sync_session, EMPTY_PLACEHOLDER_WINDOW)
        except Exception:
            pass

    def _get_or_create_ssh_proxy(self, session: Session) -> Optional[str]:
        """Get or create an SSH proxy window for a remote agent.

        Returns the local tmux window name, or None on failure.
        """
        proxy_name = f"{SSH_PROXY_WINDOW_PREFIX}{session.source_host}:{session.name}"

        # Check if proxy already exists in our cache
        if session.id in self._ssh_proxies:
            window_name = self._ssh_proxies[session.id]
            # Verify it still exists
            if self._tmux_manager.window_exists(window_name):
                return window_name
            # Window was closed — remove stale entry
            del self._ssh_proxies[session.id]

        # Kill any stale proxy windows with this name (e.g., leftover from
        # a previous TUI session) to avoid duplicates that break window lookup.
        try:
            sess = self._tmux_manager._get_session()
            if sess:
                for win in list(sess.windows):
                    if win.window_name == proxy_name:
                        win.kill()
        except Exception:
            pass

        # Need the remote window name to attach to.
        # tmux_window may be empty if remote runs an older version — fall back to agent name
        # (the SSH command does prefix matching to find "name-uuid" windows).
        remote_window = session.tmux_window or session.name
        if not remote_window:
            return None

        # Create proxy window
        remote_tmux_session = session.source_tmux_session or "agents"

        window_name = self._tmux_manager.create_ssh_proxy_window(
            window_name=proxy_name,
            ssh_target=session.source_ssh,
            remote_tmux_session=remote_tmux_session,
            remote_window=remote_window,
        )
        if window_name:
            self._ssh_proxies[session.id] = window_name
        return window_name

    def _cleanup_stale_ssh_proxies(self) -> None:
        """Kill any ssh: proxy windows left over from a previous TUI session.

        Called on startup to prevent duplicate window names that break
        libtmux's window lookup. Uses direct libtmux iteration to handle
        duplicates safely (TmuxManager.kill_window fails on duplicate names).
        """
        try:
            sess = self._tmux_manager._get_session()
            if sess is None:
                return
            for win in list(sess.windows):
                if win.window_name.startswith(SSH_PROXY_WINDOW_PREFIX):
                    try:
                        win.kill()
                    except Exception:
                        pass
        except Exception:
            pass

    def _cleanup_ssh_proxies(self) -> None:
        """Kill all SSH proxy windows. Called on TUI exit."""
        for session_id, window_name in list(self._ssh_proxies.items()):
            try:
                self._tmux_manager.kill_window(window_name)
            except Exception:
                pass
        self._ssh_proxies.clear()

    def _fix_window_size_if_needed(self, widget: "SessionSummary") -> None:
        """Auto-fix tmux window size mismatch when switching sessions.

        When terminal is resized while a session is not active, only the active
        window gets resized. When switching to that session, its window might be
        the wrong size. This method detects the mismatch and sends a tmux command
        to resize it to match the current terminal dimensions.

        For remote agents with SSH, sends a resize command via the sister API
        so the remote tmux window matches the local proxy pane dimensions.

        The fix is silent and non-blocking — if it fails, navigation continues.
        """
        if not self.in_split or not self.size or self.size.height <= 0:
            return

        try:
            session = widget.session

            # Remote agents with SSH: resize via API
            if session.is_remote and session.source_ssh and session.name:
                self._resize_remote_agent(session)
                return

            window_index = session.tmux_window
            if not window_index or window_index == "":
                return

            sync_session = self.tmux_sync_target
            # Get current terminal size from the active window
            # and apply it to the target window
            current_height = self.size.height
            current_width = self.size.width

            # Get the target window's current size by querying tmux
            # If sizes don't match, resize the target window
            self._tmux.resize_window(sync_session, window_index, current_width, current_height)
        except Exception:
            pass  # Silent fail - size mismatch isn't critical

    @work(thread=True)
    def _resize_remote_agent(self, session: Session) -> None:
        """Resize a remote agent's tmux window via the sister API.

        Reads the bottom pane dimensions from tmux and sends them to the
        remote overcode instance, which resizes the agent's window to match.
        Runs in a background thread to avoid blocking navigation.
        """
        import subprocess

        try:
            # Get the bottom pane dimensions. The split layout has 2 panes
            # in the overcode session — find the one that isn't the TUI.
            from .tmux_utils import get_pane_base_index
            tui_pane_index = get_pane_base_index()

            result = subprocess.run(
                [*_tmux_base(), "list-panes", "-t", "overcode:0",
                 "-F", "#{pane_index} #{pane_width} #{pane_height}"],
                capture_output=True, text=True, timeout=2,
            )
            if result.returncode != 0:
                return

            width = height = 0
            for line in result.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) == 3 and parts[0] != str(tui_pane_index):
                    width, height = int(parts[1]), int(parts[2])
                    break

            if width <= 0 or height <= 0:
                return

            from .sister_controller import SisterController
            SisterController(timeout=3).resize_agent(
                session.source_url, session.source_api_key,
                session.name, width, height,
            )
        except Exception:
            pass  # Silent fail

    # =========================================================================
    # Jobs Mode
    # =========================================================================

    def action_toggle_tui_mode(self) -> None:
        """Toggle between agents and jobs view."""
        self.tui_mode = "jobs" if self.tui_mode == "agents" else "agents"

    def watch_show_terminated(self, show_terminated: bool) -> None:
        """Immediately refresh jobs when ghost toggle changes."""
        if self.is_running and self.tui_mode == "jobs":
            self._refresh_jobs()

    def watch_tui_mode(self, mode: str) -> None:
        """React to tui_mode changes — swap visible containers."""
        if not self.is_running:
            return
        try:
            sessions_container = self.query_one("#sessions-container", ScrollableContainer)
            jobs_container = self.query_one("#jobs-container", ScrollableContainer)
            if mode == "jobs":
                sessions_container.display = False
                jobs_container.add_class("visible")
                jobs_container.display = True
                self._refresh_jobs()
                # Zoom the TUI and show the preview (as in sister view)
                self._dialog_will_open()
                self.preview_visible = True
                # Focus first job widget if available
                job_widgets = list(self.query(JobSummary))
                if job_widgets:
                    self.focused_job_index = 0
                    job_widgets[0].focus()
            else:
                jobs_container.remove_class("visible")
                jobs_container.display = False
                sessions_container.display = True
                # Hide the preview and unzoom (as on leaving sister view)
                self.preview_visible = False
                self._dialog_did_close()
                # Refocus agents
                widget = self._get_focused_widget()
                if widget:
                    widget.focus()
        except NoMatches:
            pass
        self._update_subtitle()
        self._update_footer()
        self._update_column_headers()

    def watch_focused_job_index(self, new_index: int) -> None:
        """Focus the job widget at the new index and update preview."""
        if self.tui_mode != "jobs":
            return
        job_widgets = self._get_job_widgets()
        if not job_widgets:
            return
        if not (0 <= new_index < len(job_widgets)):
            new_index = max(0, min(new_index, len(job_widgets) - 1))
            self.focused_job_index = new_index
            return
        widget = job_widgets[new_index]
        widget.focus()
        # Remove .selected from all, add to focused
        for w in job_widgets:
            w.remove_class("selected")
        widget.add_class("selected")
        # Update preview pane
        if self.preview_visible:
            try:
                preview = self.query_one("#preview-pane", PreviewPane)
                preview.update_from_job_widget(widget)
            except NoMatches:
                pass

    def _apply_job_counts(self) -> None:
        """Running jobs into each agent's JOB column; orphans into the monitor bar (#463)."""
        local_ids = {s.id for s in self.sessions if not s.is_remote}
        self._job_counts, orphans = running_job_counts(self.jobs, local_ids)
        for widget in self.query(SessionSummary):
            count = self._job_counts.get(widget.session.id, 0)
            if widget.job_count != count:
                widget.job_count = count
                widget.refresh()
        try:
            bar = self.query_one("#daemon-status", DaemonStatusBar)
        except NoMatches:
            return
        if bar.orphan_job_count != orphans:
            bar.orphan_job_count = orphans
            bar.refresh()

    def _get_job_widgets(self) -> List[JobSummary]:
        """Get job widgets in order."""
        return list(self.query(JobSummary))

    @work(thread=True, group="refresh_jobs")
    @single_flight("refresh_jobs")
    def _refresh_jobs(self) -> None:
        """Refresh jobs list from state file."""
        try:
            show_completed = self.show_terminated  # Reuse the ghost toggle
            jobs = self._job_launcher.list_jobs(include_completed=show_completed)
            self.call_from_thread(self._apply_jobs, jobs)
        except Exception:
            pass

    def _apply_jobs(self, jobs: List[Job]) -> None:
        """Apply refreshed job list to TUI (main thread)."""
        self.jobs = jobs
        self._apply_job_counts()
        try:
            container = self.query_one("#jobs-container", ScrollableContainer)
        except NoMatches:
            return

        # Compute dynamic name column width (min 12, max 30)
        name_width = max((len(j.name) for j in jobs), default=12)
        name_width = max(12, min(name_width, 30))

        existing = {w.job.id: w for w in self.query(JobSummary)}
        current_ids = {j.id for j in jobs}

        # Remove widgets for deleted jobs
        for job_id, widget in existing.items():
            if job_id not in current_ids:
                widget.remove()

        # Update existing or mount new
        for job in jobs:
            if job.id in existing:
                w = existing[job.id]
                w.name_width = name_width
                w.refresh_job(job)
            else:
                widget = JobSummary(job)
                widget.monochrome = self.monochrome
                widget.emoji_free = self.emoji_free
                widget.name_width = name_width
                container.mount(widget)

        # Capture pane content for focused job
        if self.tui_mode == "jobs":
            self._update_focused_job_pane_content()

    def _update_focused_job_pane_content(self) -> None:
        """Capture tmux pane content for the focused job widget."""
        job_widgets = self._get_job_widgets()
        if not job_widgets:
            return
        idx = self.focused_job_index
        if not (0 <= idx < len(job_widgets)):
            return
        widget = job_widgets[idx]
        job = widget.job
        if job.status == "running" and job.tmux_window:
            try:
                tmux = self._job_launcher.tmux
                pane = tmux._get_pane(job.tmux_window)
                if pane:
                    lines = pane.capture_pane()
                    widget.pane_content = lines if lines else []
            except Exception:
                pass

    def _poll_focused_job_pane(self) -> None:
        """Poll focused job's tmux pane content (1s interval)."""
        if self.tui_mode != "jobs":
            return
        self._update_focused_job_pane_content()
        # Update preview pane
        job_widgets = self._get_job_widgets()
        idx = self.focused_job_index
        if job_widgets and 0 <= idx < len(job_widgets):
            widget = job_widgets[idx]
            if self.preview_visible:
                try:
                    preview = self.query_one("#preview-pane", PreviewPane)
                    preview.update_from_job_widget(widget)
                except NoMatches:
                    pass

    def _action_kill_focused_job(self) -> None:
        """Kill the focused job."""
        job_widgets = self._get_job_widgets()
        if not job_widgets:
            return
        idx = self.focused_job_index
        if not (0 <= idx < len(job_widgets)):
            return
        job = job_widgets[idx].job
        if job.status != "running":
            self.notify(f"Job '{job.name}' is not running", severity="warning")
            return
        if self._job_launcher.kill_job(job.name):
            self.notify(f"Killed job '{job.name}'")
            self._refresh_jobs()
        else:
            self.notify(f"Failed to kill job '{job.name}'", severity="error")

    def _action_clear_completed_jobs(self) -> None:
        """Clear all completed/failed/killed jobs."""
        self._job_manager.clear_completed()
        self.notify("Cleared completed jobs")
        self._refresh_jobs()

    def _action_send_enter_to_focused_job(self) -> None:
        """Send Enter to the focused job's tmux window."""
        job_widgets = self._get_job_widgets()
        if not job_widgets:
            return
        idx = self.focused_job_index
        if not (0 <= idx < len(job_widgets)):
            return
        job = job_widgets[idx].job
        if job.status == "running" and job.tmux_window:
            self._job_launcher.tmux.send_keys(job.tmux_window, "", enter=True)

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
        """Select the first agent so something is highlighted from the start."""
        try:
            widgets = list(self.query(SessionSummary))
            if widgets:
                self.focused_session_index = 0  # Watcher handles focus + preview + tmux sync
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

    def _stale_banner_for(self, session) -> str:
        """Build a "sister unreachable" banner for a remote session (#385).

        Returns an empty string for local sessions or when the source sister
        is still reachable — the preview pane then renders normally.
        """
        if not getattr(session, 'is_remote', False):
            return ""
        source_url = getattr(session, 'source_url', '')
        if not source_url:
            return ""
        for sister in self._sister_poller.get_sister_states():
            if sister.url != source_url:
                continue
            if sister.reachable:
                return ""
            age = self._sister_last_fetch_age(sister)
            host = sister.name or "sister"
            return f"{host} unreachable — last updated {age}"
        return ""

    @staticmethod
    def _sister_last_fetch_age(sister) -> str:
        """Format how long ago the sister last succeeded, for the stale banner."""
        last_fetch = getattr(sister, 'last_fetch', None)
        if not last_fetch:
            return "never"
        try:
            ts = datetime.fromisoformat(last_fetch)
        except (TypeError, ValueError):
            return "unknown"
        secs = (datetime.now() - ts).total_seconds()
        if secs < 60:
            return f"{int(secs)}s ago"
        if secs < 3600:
            return f"{int(secs / 60)}m ago"
        return f"{int(secs / 3600)}h ago"

    def _find_any_session_by_name(self, name: str):
        """Find a session by name, including remote sessions.

        Deprecated: prefer _find_any_session_by_id() for unambiguous routing.
        """
        # Check local sessions first
        session = self.session_manager.resolve_session_name(name)
        if session:
            return session
        # Check remote sessions in self.sessions
        for s in self.sessions:
            if s.name == name and getattr(s, 'is_remote', False):
                return s
        return None

    def _find_any_session_by_id(self, session_id: str):
        """Find a session by ID, including remote sessions."""
        if not session_id:
            return None
        session = self.session_manager.get_session(session_id)
        if session:
            return session
        for s in self.sessions:
            if s.id == session_id:
                return s
        return None

    def _resolve_command_bar_session(self, session_id: str, session_name: str):
        """Resolve session from command bar message, preferring ID over name."""
        if session_id:
            return self._find_any_session_by_id(session_id)
        return self._find_any_session_by_name(session_name)

    def on_command_bar_send_requested(self, message: CommandBar.SendRequested) -> None:
        """Handle send request from command bar."""
        session = self._resolve_command_bar_session(message.session_id, message.session_name)

        # Remote agent — dispatch through sister controller
        if session and getattr(session, 'is_remote', False):
            if self._guard_remote(session):
                return
            result = self._sister_controller.send_instruction(
                session.source_url, session.source_api_key,
                session.name, text=message.text,
            )
            if result.ok:
                self._record_instruction(message.text, message.session_name)
                self.notify(f"Sent to remote agent {message.session_name}")
            else:
                self.notify(f"Remote error: {result.error}", severity="error")
            return

        # Local agent — auto-wake sleeping agent if needed (#168)
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

        launcher = AgentLauncher(
            tmux_session=self.tmux_session,
            session_manager=self.session_manager
        )
        success = launcher.send_to_session_by_id(session.id, message.text) if session else False
        if success:
            self._record_instruction(message.text, message.session_name)
            self.notify(f"Sent to {message.session_name}")
        else:
            self.notify(f"Failed to send to {message.session_name}", severity="error")

    def on_command_bar_standing_order_requested(self, message: CommandBar.StandingOrderRequested) -> None:
        """Handle standing order request from command bar."""
        session = self._resolve_command_bar_session(message.session_id, message.session_name)
        if not session:
            self.notify(f"Session '{message.session_name}' not found", severity="error")
            return

        if getattr(session, 'is_remote', False):
            if self._guard_remote(session):
                return
            if message.text:
                result = self._sister_controller.set_standing_orders(
                    session.source_url, session.source_api_key,
                    session.name, text=message.text,
                )
            else:
                result = self._sister_controller.clear_standing_orders(
                    session.source_url, session.source_api_key, session.name,
                )
            if result.ok:
                action = "set" if message.text else "cleared"
                self.notify(f"Standing order {action} for remote {message.session_name}")
                self._optimistic_update_remote(session.id, standing_instructions=message.text)
            else:
                self.notify(f"Remote error: {result.error}", severity="error")
            return

        self.session_manager.set_standing_instructions(session.id, message.text)
        if message.text:
            self.notify(f"Standing order set for {message.session_name}")
        else:
            self.notify(f"Standing order cleared for {message.session_name}")
        self.refresh_sessions()

    def on_command_bar_value_updated(self, message: CommandBar.ValueUpdated) -> None:
        """Handle agent value update from command bar (#61)."""
        session = self._resolve_command_bar_session(message.session_id, message.session_name)
        if not session:
            self.notify(f"Session '{message.session_name}' not found", severity="error")
            return

        if getattr(session, 'is_remote', False):
            if self._guard_remote(session):
                return
            result = self._sister_controller.set_value(
                session.source_url, session.source_api_key,
                session.name, value=message.value,
            )
            if result.ok:
                self.notify(f"Value set to {message.value} for remote {message.session_name}")
                self._optimistic_update_remote(session.id, agent_value=message.value)
            else:
                self.notify(f"Remote error: {result.error}", severity="error")
            return

        self.session_manager.set_agent_value(session.id, message.value)
        self.notify(f"Value set to {message.value} for {message.session_name}")
        self.refresh_sessions()

    def on_command_bar_budget_updated(self, message: CommandBar.BudgetUpdated) -> None:
        """Handle cost budget update from command bar (#173)."""
        session = self._resolve_command_bar_session(message.session_id, message.session_name)
        if not session:
            self.notify(f"Session '{message.session_name}' not found", severity="error")
            return

        if getattr(session, 'is_remote', False):
            if self._guard_remote(session):
                return
            result = self._sister_controller.set_budget(
                session.source_url, session.source_api_key,
                session.name, usd=message.budget_usd,
            )
            if result.ok:
                if message.budget_usd > 0:
                    self.notify(f"Budget set to ${message.budget_usd:.2f} for remote {message.session_name}")
                else:
                    self.notify(f"Budget cleared for remote {message.session_name}")
                self._optimistic_update_remote(session.id, cost_budget_usd=message.budget_usd)
            else:
                self.notify(f"Remote error: {result.error}", severity="error")
            return

        self.session_manager.set_cost_budget(session.id, message.budget_usd)
        if message.budget_usd > 0:
            self.notify(f"Budget set to ${message.budget_usd:.2f} for {message.session_name}")
        else:
            self.notify(f"Budget cleared for {message.session_name}")
        self.refresh_sessions()

    def on_command_bar_annotation_updated(self, message: CommandBar.AnnotationUpdated) -> None:
        """Handle human annotation update from command bar (#74)."""
        session = self._resolve_command_bar_session(message.session_id, message.session_name)
        if not session:
            self.notify(f"Session '{message.session_name}' not found", severity="error")
            return

        if getattr(session, 'is_remote', False):
            if self._guard_remote(session):
                return
            result = self._sister_controller.set_annotation(
                session.source_url, session.source_api_key,
                session.name, text=message.annotation,
            )
            if result.ok:
                action = "set" if message.annotation else "cleared"
                self.notify(f"Annotation {action} for remote {message.session_name}")
                self._optimistic_update_remote(session.id, human_annotation=message.annotation)
            else:
                self.notify(f"Remote error: {result.error}", severity="error")
            return

        self.session_manager.set_human_annotation(session.id, message.annotation)
        if message.annotation:
            self.notify(f"Annotation set for {message.session_name}")
        else:
            self.notify(f"Annotation cleared for {message.session_name}")
        self.refresh_sessions()

    def on_command_bar_heartbeat_updated(self, message: CommandBar.HeartbeatUpdated) -> None:
        """Handle heartbeat configuration update from command bar (#171)."""
        session = self._resolve_command_bar_session(message.session_id, message.session_name)
        if not session:
            self.notify(f"Session not found: {message.session_name}", severity="error")
            return

        if getattr(session, 'is_remote', False):
            if self._guard_remote(session):
                return
            result = self._sister_controller.configure_heartbeat(
                session.source_url, session.source_api_key,
                session.name,
                enabled=message.enabled,
                frequency=str(message.frequency),
                instruction=message.instruction,
            )
            if result.ok:
                if message.enabled:
                    freq_str = format_duration(message.frequency)
                    self.notify(f"Heartbeat enabled: every {freq_str} (remote)", severity="information")
                else:
                    self.notify("Heartbeat disabled (remote)", severity="information")
                self._optimistic_update_remote(
                    session.id,
                    heartbeat_enabled=message.enabled,
                    heartbeat_frequency_seconds=message.frequency,
                    heartbeat_instruction=message.instruction,
                )
            else:
                self.notify(f"Remote error: {result.error}", severity="error")
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
            target_session_id = cmd_bar.target_session_id  # Remember before disabling
            target_session_name = cmd_bar.target_session  # Fallback for name match
            cmd_bar.query_one("#cmd-input", Input).disabled = True
            cmd_bar.query_one("#cmd-textarea", TextArea).disabled = True
            cmd_bar.remove_class("visible")

            # Focus the targeted session (not first session) to keep preview on it
            if self.sessions:
                widgets = self._get_widgets_in_session_order()
                if widgets:
                    # Find widget matching target session by ID, fall back to name
                    target_widget = None
                    for i, w in enumerate(widgets):
                        if target_session_id and w.session.id == target_session_id:
                            target_widget = w
                            self._user_navigated = True
                            self.focused_session_index = i
                            break
                        elif not target_session_id and w.session.name == target_session_name:
                            target_widget = w
                            self._user_navigated = True
                            self.focused_session_index = i
                            break
                    if target_widget:
                        target_widget.focus()
                    else:
                        self.focused_session_index = min(self.focused_session_index, len(widgets) - 1)
                        widgets[self.focused_session_index].focus()
                    if self.preview_visible:
                        self._update_preview()
        except NoMatches:
            pass

    def on_new_agent_modal_launch_requested(self, message: NewAgentModal.LaunchRequested) -> None:
        """Handle launch from the unified new-agent modal."""
        import logging
        _log = logging.getLogger("overcode.tui.new_agent")

        name = message.name
        if not name or len(name) > 50 or ' ' in name:
            self.notify("Invalid agent name", severity="error")
            self._dialog_did_close()
            return

        if message.is_remote:
            # Launch on remote sister
            from .config import get_sister_by_name
            sister_config = get_sister_by_name(message.host)
            if not sister_config:
                self.notify(f"Sister '{message.host}' not found", severity="error")
                self._dialog_did_close()
                return

            # Check reachability
            for state in self._sister_poller.get_sister_states():
                if state.name == message.host and not state.reachable:
                    self.notify(f"Sister '{message.host}' is unreachable", severity="warning")
                    self._dialog_did_close()
                    return

            permissions = "bypass" if message.bypass_permissions else "normal"
            if message.extra_cli_args:
                self.notify("Backend args are not yet supported for remote launches — ignored", severity="warning")
            from .backends import DEFAULT_BACKEND
            if message.backend and message.backend != DEFAULT_BACKEND:
                self.notify(
                    f"Backend '{message.backend}' is not yet supported for remote "
                    f"launches — the sister will use {DEFAULT_BACKEND}",
                    severity="warning",
                )
            try:
                result = self._sister_controller.launch_agent(
                    sister_url=sister_config["url"],
                    api_key=sister_config.get("api_key", ""),
                    directory=message.directory or ".",
                    name=name,
                    permissions=permissions,
                    provider=message.provider,
                )
            except Exception as e:
                _log.error("Remote launch exception: %s", e, exc_info=True)
                self.notify(f"Remote launch failed: {e}", severity="error")
                self._dialog_did_close()
                return

            if result.ok:
                self.notify(f"Remote agent '{name}' launched on {message.host}", severity="information")
            else:
                self.notify(f"Remote launch failed: {result.error}", severity="error")
        else:
            # Launch locally
            launcher = AgentLauncher(
                tmux_session=self.tmux_session,
                session_manager=self.session_manager,
            )
            try:
                launcher.launch(
                    name=name,
                    start_directory=message.directory,
                    dangerously_skip_permissions=message.bypass_permissions,
                    agent_teams=message.agent_teams,
                    agent_persona=message.agent_persona,
                    provider=message.provider,
                    backend=message.backend,
                    wrapper=message.wrapper,
                    extra_cli_args=message.extra_cli_args or None,
                    skill_profile=message.skill_profile,
                )
                parts = [f"Created agent: {name}"]
                if message.wrapper:
                    parts.append(f"wrapper: {message.wrapper}")
                if message.skill_profile and message.skill_profile != "none":
                    parts.append(f"skills: {message.skill_profile}")
                self.notify(" ".join(parts), severity="information")
                self.refresh_sessions()
            except Exception as e:
                self.notify(f"Failed to create agent: {e}", severity="error")

        self._dialog_did_close()

    def on_new_agent_modal_cancelled(self, message: NewAgentModal.Cancelled) -> None:
        """Handle cancel from new-agent modal."""
        self._dialog_did_close()

    def on_rename_agent_modal_rename_requested(
        self, message: RenameAgentModal.RenameRequested,
    ) -> None:
        """Rename the agent the dialog was opened for (#478).

        Off the UI thread: a live agent is stopped, waited on (seconds) and
        relaunched, and the busy check captures its pane.
        """
        self._dialog_did_close()
        session = self.session_manager.get_session(message.session_id)
        if session is None:
            self.notify(f"Agent '{message.old_name}' no longer exists", severity="error")
            return
        self.notify(f"Renaming '{message.old_name}' → '{message.new_name}'...")
        threading.Thread(
            target=self._run_rename,
            args=(session, message.new_name, message.force),
            daemon=True,
        ).start()

    def _run_rename(self, session: "Session", new_name: str, force: bool) -> None:
        """Worker thread for a TUI rename; reports back through notify."""
        from .exceptions import AgentBusyError, InvalidSessionNameError
        from .launcher import AgentLauncher

        old_name = session.name
        launcher = AgentLauncher(self.tmux_session, session_manager=self.session_manager)
        try:
            renamed = launcher.rename(session, new_name, force=force)
        except AgentBusyError as e:
            state = "could not be checked" if e.status == "unknown" else f"is busy ({e.status})"
            self.call_from_thread(
                self.notify,
                f"'{old_name}' {state} — not renamed. Try when it is idle, or turn force on.",
                severity="warning",
            )
            return
        except (InvalidSessionNameError, ValueError) as e:
            self.call_from_thread(self.notify, f"Rename failed: {e}", severity="error")
            return
        except Exception as e:  # never let a worker die silently
            self.call_from_thread(self.notify, f"Rename failed: {e}", severity="error")
            return

        current = self.session_manager.get_session(session.id)
        if renamed:
            self.call_from_thread(
                self.notify, f"Renamed '{old_name}' → '{new_name}'", severity="information",
            )
        elif current is not None and current.name == new_name:
            self.call_from_thread(
                self.notify,
                f"Renamed to '{new_name}', but the relaunch failed — press R to restart it",
                severity="warning",
            )
        else:
            self.call_from_thread(
                self.notify,
                f"tmux refused the rename; '{old_name}' was restarted under its old name",
                severity="error",
            )
        self.call_from_thread(self.refresh_sessions)

    def on_summary_prompt_lab_closed(self, message: SummaryPromptLab.Closed) -> None:
        """The prompt lab closed (#491)."""
        self._dialog_did_close()

    def on_rename_agent_modal_cancelled(self, message: RenameAgentModal.Cancelled) -> None:
        """Handle cancel from the rename modal."""
        self._dialog_did_close()

    def on_command_bar_fork_requested(self, message: CommandBar.ForkRequested) -> None:
        """Handle fork agent request (#347)."""
        source_session = message.source_session
        fork_name = message.fork_name

        # Validate name
        if not fork_name or len(fork_name) > 50:
            self.notify("Invalid fork name", severity="error")
            return
        if ' ' in fork_name:
            self.notify("Fork name cannot contain spaces", severity="error")
            return

        # Remote agents: fork via sister controller (#368)
        if getattr(source_session, 'is_remote', False):
            try:
                result = self._sister_controller.fork_agent(
                    source_session.source_url,
                    source_session.source_api_key,
                    source_session.name,
                    fork_name,
                )
                if result.ok:
                    self.notify(f"Forked remote '{source_session.name}' → '{fork_name}'", severity="information")
                    self.refresh_sessions()
                else:
                    self.notify(f"Remote fork failed: {result.error}", severity="error")
            except Exception as e:
                self.notify(f"Failed to fork remote agent: {e}", severity="error")
            return

        launcher = AgentLauncher(
            tmux_session=self.tmux_session,
            session_manager=self.session_manager
        )

        try:
            result = launcher.launch_fork(
                name=fork_name,
                source_session=source_session,
            )
            if result:
                self.notify(f"Forked '{source_session.name}' → '{fork_name}'", severity="information")
                self.refresh_sessions()
            else:
                self.notify(f"Failed to fork '{source_session.name}'", severity="error")
        except Exception as e:
            self.notify(f"Failed to fork: {e}", severity="error")

    def _ensure_monitor_daemon(self) -> None:
        """Start or restart the Monitor Daemon as needed.

        Called automatically on TUI mount to ensure continuous monitoring.
        Also auto-restarts if the running daemon is an old version (#386).
        """
        from .settings import get_monitor_daemon_pid_path, DAEMON_VERSION
        from .monitor_daemon import stop_monitor_daemon

        running = (
            is_daemon_lock_held(get_monitor_daemon_pid_path(self.tmux_session))
            or is_monitor_daemon_running(self.tmux_session)
        )

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

    @work(thread=True, group="ssh_provision")
    @single_flight("ssh_provision")
    def _provision_ssh_sisters(self) -> None:
        """Provision SSH-configured sisters in a background thread.

        Checks each sister with SSH configured to ensure overcode is running
        and version-matched. Bootstraps or upgrades via uvx as needed.
        """
        from .ssh_provisioner import ensure_remote_ready
        from .config import get_web_host

        sisters_with_ssh: list[SisterState] = [
            s for s in self._sister_poller.get_sister_states() if s.ssh
        ]
        if not sisters_with_ssh:
            return

        for sister in sisters_with_ssh:
            if worker_cancelled():
                return
            try:
                # Extract port from sister URL
                from urllib.parse import urlparse
                parsed = urlparse(sister.url)
                port = parsed.port or 8080
                host = get_web_host()

                result = ensure_remote_ready(
                    ssh_target=sister.ssh,
                    sister_url=sister.url,
                    api_key=sister.api_key,
                    port=port,
                    host=host,
                )
                if result.ok:
                    if result.action == "bootstrapped":
                        self.call_from_thread(
                            self.notify,
                            f"[b]{sister.name}[/b]: overcode started via uvx",
                            severity="information",
                        )
                    elif result.action == "upgraded":
                        self.call_from_thread(
                            self.notify,
                            f"[b]{sister.name}[/b]: upgraded to v{result.remote_version}",
                            severity="information",
                        )
                else:
                    self.call_from_thread(
                        self.notify,
                        f"[b]{sister.name}[/b]: provision failed — {result.error}",
                        severity="warning",
                    )
            except Exception as e:
                self.call_from_thread(
                    self.notify,
                    f"[b]{sister.name}[/b]: SSH provision error — {e}",
                    severity="warning",
                )

    def _execute_kill(self, focused: "SessionSummary", session_name: str, session_id: str) -> None:
        """Execute the actual kill operation after confirmation."""
        # Save a copy of the session for showing when show_terminated is True
        session_copy = focused.session
        # Mark it as terminated for display purposes
        from dataclasses import replace
        terminated_session = replace(session_copy, status="terminated")

        # Use launcher to kill the session
        launcher = AgentLauncher(
            tmux_session=self.tmux_session,
            session_manager=self.session_manager
        )

        if launcher.kill_session(session_name):
            self.notify(f"Killed agent: {session_name}", severity="information")

            # Store in terminated sessions cache for ghost mode
            import time as _time
            self._terminated_sessions[session_id] = terminated_session
            self._terminated_times[session_id] = _time.monotonic()

            # Remove from self.sessions so j/k ordering stays consistent
            self.sessions = [s for s in self.sessions if s.id != session_id]

            # Remove the widget (will be re-added if show_terminated is True)
            focused.remove()
            # Reconcile widgets immediately — Textual's Widget.remove() is async,
            # and without this kicker the row could linger until the next 10 s
            # refresh_sessions tick (#456). update_session_widgets diffs DOM vs
            # display_sessions and force-removes anything stale, *and* re-adds
            # the row from _terminated_sessions when show_terminated is True.
            self.update_session_widgets()

            # Focus next available agent (uses session order for j/k consistency)
            widgets = self._get_widgets_in_session_order()
            if widgets:
                self.focused_session_index = min(self.focused_session_index, len(widgets) - 1)
                # Watcher handles .focus(), preview update, and tmux sync
        else:
            self.notify(f"Failed to kill agent: {session_name}", severity="error")

    def _execute_cleanup(self, focused: "SessionSummary", session_name: str, session_id: str) -> None:
        """Clean up a terminated/done agent: archive and remove from display."""
        self.session_manager.delete_session(session_id)

        self.notify(f"Cleaned up agent: {session_name}", severity="information")

        # Remove from self.sessions so j/k ordering stays consistent
        self.sessions = [s for s in self.sessions if s.id != session_id]

        # Remove from caches
        if session_id in self._terminated_sessions:
            del self._terminated_sessions[session_id]
        self._terminated_times.pop(session_id, None)
        # Remove the widget
        focused.remove()
        # Same reconciliation as _execute_kill (#456) — force a diff so the row
        # disappears immediately rather than waiting for the next refresh tick.
        self.update_session_widgets()

        # Focus next available agent (uses session order for j/k consistency)
        widgets = self._get_widgets_in_session_order()
        if widgets:
            self.focused_session_index = min(self.focused_session_index, len(widgets) - 1)
            # Watcher handles .focus(), preview update, and tmux sync

    def _execute_restart(self, focused: "SessionSummary") -> None:
        """Execute the actual restart operation after confirmation (#133).

        Delegates to AgentLauncher.restart, which rebuilds the full launch
        environment (hooks/permissions --settings, wrapper, env prefix,
        model, persona, allowed tools, extra args) from the stored Session
        and relaunches in the existing tmux window, resuming the prior
        Claude conversation.

        If the tmux window is gone (terminated agent), delegates to
        _execute_revive.
        """
        from .launcher import AgentLauncher
        from .tmux_manager import TmuxManager
        session = focused.session
        session_name = session.name
        tmux = TmuxManager(self.tmux_session)

        if not tmux.window_exists(session.tmux_window):
            self._execute_revive(focused, tmux)
            return

        launcher = AgentLauncher(
            self.tmux_session,
            tmux_manager=tmux,
            session_manager=self.session_manager,
        )
        if launcher.restart(session):
            self.notify(f"Restarted agent: {session_name}", severity="information")
        else:
            self.notify(f"Failed to restart agent: {session_name}", severity="error")

    def _execute_revive(self, focused: "SessionSummary", tmux: "TmuxManager") -> None:  # noqa: F821
        """Revive a terminated agent by creating a new tmux window and relaunching.

        Delegates to AgentLauncher.revive, which rebuilds the full launch
        environment from the stored Session (hooks/permissions --settings,
        wrapper, env prefix, model, persona, allowed tools, extra args) and
        resumes the prior Claude conversation when available.
        """
        from .launcher import AgentLauncher
        session = focused.session
        session_name = session.name

        launcher = AgentLauncher(
            self.tmux_session,
            tmux_manager=tmux,
            session_manager=self.session_manager,
        )
        if not launcher.revive(session):
            self.notify(f"Failed to revive agent: {session_name}", severity="error")
            return

        # Remove from terminated cache
        if session.id in self._terminated_sessions:
            del self._terminated_sessions[session.id]
        self._terminated_times.pop(session.id, None)

        resume_info = " (resuming session)" if session.active_agent_session_id else ""
        self.notify(f"Revived agent: {session_name}{resume_info}", severity="information")
        self.refresh_sessions()

    def action_open_column_config(self) -> None:
        """Open the column configuration modal (#178).

        Edits the currently active detail level. Full shows all columns
        and cannot be configured.
        """
        current_level = self.SUMMARY_LEVELS[self.summary_level_index]
        if current_level == "full":
            self.notify("Full shows all columns", severity="information")
            return
        try:
            modal = self.query_one("#summary-config-modal", SummaryConfigModal)
            overrides = self._prefs.column_config.get(current_level, {})
            self._live_column_overrides = dict(overrides)
            self._dialog_will_open()
            modal.show(current_level, overrides, self)
        except NoMatches:
            pass

    def on_summary_config_modal_config_changed(self, message: SummaryConfigModal.ConfigChanged) -> None:
        """Handle column configuration changes from modal (#178)."""
        self._apply_column_config(message.level, message.overrides)
        self.notify(f"Column config saved for {message.level}", severity="information")
        self._dialog_did_close()

    def _apply_column_config(self, level: str, overrides: dict) -> None:
        """Save one level's column overrides and redraw with them (C dialog, overcode view)."""
        if overrides:
            self._prefs.column_config[level] = overrides
        elif level in self._prefs.column_config:
            # Empty overrides after reset — remove the key
            del self._prefs.column_config[level]
        self._save_prefs()

        # Push updated overrides to all widgets
        current_level = self.SUMMARY_LEVELS[self.summary_level_index]
        new_overrides = self._prefs.column_config.get(current_level, {})
        for widget in self.query(SessionSummary):
            widget.column_overrides = new_overrides
            widget.refresh()
        self._live_column_overrides = None
        self._column_widths_dirty = True
        self._recompute_cell_column_widths()

    def on_summary_config_modal_cancelled(self, message: SummaryConfigModal.Cancelled) -> None:
        """Handle modal cancellation (#178)."""
        # Restore original overrides
        current_level = self.SUMMARY_LEVELS[self.summary_level_index]
        original_overrides = self._prefs.column_config.get(current_level, {})
        for widget in self.query(SessionSummary):
            widget.column_overrides = original_overrides
            widget.refresh()
        self._live_column_overrides = None
        self._column_widths_dirty = True
        self._recompute_cell_column_widths()
        self._dialog_did_close()

    def action_open_new_agent_defaults(self) -> None:
        """Open the new-agent defaults modal."""
        from .config import get_new_agent_defaults
        try:
            modal = self.query_one("#new-agent-defaults-modal", NewAgentDefaultsModal)
            self._dialog_will_open()
            modal.show(get_new_agent_defaults(), self)
        except NoMatches:
            pass

    def on_new_agent_defaults_modal_defaults_changed(self, message: NewAgentDefaultsModal.DefaultsChanged) -> None:
        """Handle new-agent defaults applied from modal."""
        from .config import save_new_agent_defaults
        save_new_agent_defaults(message.defaults)
        self.notify("Defaults saved", severity="information")
        self._dialog_did_close()

    def on_new_agent_defaults_modal_cancelled(self, message: NewAgentDefaultsModal.Cancelled) -> None:
        """Handle new-agent defaults modal cancellation."""
        self._dialog_did_close()

    def action_open_skills(self) -> None:
        """Open the skill profiles dialog (#499), on the focused agent's profile."""
        from pathlib import Path
        from .skill_library import skill_usage
        focused = self._get_focused_widget()
        session = focused.session if focused else None
        try:
            modal = self.query_one("#skills-modal", SkillsModal)
        except NoMatches:
            return
        self._dialog_will_open()
        modal.show(
            usage=skill_usage(self.session_manager.list_sessions()),
            profile=getattr(session, "skill_profile", None),
            folder=(session.start_directory if session else None) or str(Path.cwd()),
            app_ref=self,
        )

    def on_skills_modal_closed(self, message: SkillsModal.Closed) -> None:
        self.refresh_sessions()
        self._dialog_did_close()

    def action_open_tmux_config(self) -> None:
        """Open the tmux toggle-key modal (#442)."""
        try:
            modal = self.query_one("#tmux-config-modal", TmuxConfigModal)
            self._dialog_will_open()
            modal.show(self)
        except NoMatches:
            pass

    def on_tmux_config_modal_toggle_key_changed(
        self, message: TmuxConfigModal.ToggleKeyChanged
    ) -> None:
        """Handle new toggle key selected from modal (#442)."""
        if message.reinstalled:
            self.notify(
                f"Toggle key set to {message.label} (tmux bindings reinstalled)",
                severity="information",
            )
        else:
            self.notify(
                f"Toggle key set to {message.label} — run 'overcode tmux' to install bindings",
                severity="information",
            )
        # Refresh the TERMINAL ACTIVE banner so it shows the new key label
        try:
            banner = self.query_one("#terminal-active-banner", Static)
            banner.update(self._terminal_active_banner_text())
        except NoMatches:
            pass
        self._update_footer()  # it names the toggle key in split mode
        self._dialog_did_close()

    def on_tmux_config_modal_cancelled(self, message: TmuxConfigModal.Cancelled) -> None:
        """Handle tmux config modal cancellation (#442)."""
        self._dialog_did_close()

    def action_open_passthru_config(self) -> None:
        """Open the passthru-key configuration modal (#446)."""
        try:
            modal = self.query_one("#passthru-config-modal", PassthruConfigModal)
            self._dialog_will_open()
            modal.show(self)
        except NoMatches:
            pass

    def on_passthru_config_modal_saved(
        self, message: PassthruConfigModal.Saved
    ) -> None:
        """Handle saved passthru-key config (#446)."""
        enabled = sorted(message.mapping.keys())
        self.notify(
            f"Passthru keys saved: {', '.join(enabled) if enabled else '(none enabled)'}",
            severity="information",
        )
        self._dialog_did_close()

    def on_passthru_config_modal_cancelled(
        self, message: PassthruConfigModal.Cancelled
    ) -> None:
        """Handle passthru config modal cancellation (#446)."""
        self._dialog_did_close()

    def action_open_sister_selection(self) -> None:
        """Open the sister management modal (#323)."""
        sisters = [
            {
                "name": s.name, "url": s.url, "api_key": s.api_key,
                "version": s.version, "reachable": s.reachable,
                "daemon_running": s.daemon_running,
                "green_agents": s.green_agents, "total_agents": s.total_agents,
                "last_error": s.last_error,
            }
            for s in self._sister_poller.get_sister_states()
        ]
        if not sisters:
            self.notify("No sisters configured", severity="warning")
            return
        try:
            modal = self.query_one("#sister-selection-modal", SisterSelectionModal)
            self._dialog_will_open()
            modal.show(sisters, self._prefs.disabled_sisters, self)
        except NoMatches:
            pass

    def on_sister_selection_modal_selection_changed(self, message: SisterSelectionModal.SelectionChanged) -> None:
        """Handle sister visibility changes from modal (#323)."""
        self._prefs.disabled_sisters = message.disabled_sisters
        self._save_prefs()
        # Re-filter remote sessions and rebuild widget list
        self.refresh_sessions()
        count = len(message.disabled_sisters)
        if count:
            self.notify(f"{count} sister(s) hidden", severity="information")
        else:
            self.notify("All sisters visible", severity="information")
        self._dialog_did_close()

    def on_sister_selection_modal_cancelled(self, message: SisterSelectionModal.Cancelled) -> None:
        """Handle sister selection modal cancellation (#323)."""
        self._dialog_did_close()

    def on_sister_selection_modal_restart_daemon(self, message: SisterSelectionModal.RestartDaemon) -> None:
        """Handle daemon restart request from sister management modal."""
        self.notify(f"Restarting daemon on {message.sister_name}...", severity="information")
        result = self._sister_controller.restart_monitor(message.sister_url, message.api_key)
        if result.ok:
            self.notify(f"Daemon restarted on {message.sister_name}", severity="information")
        else:
            self.notify(f"Restart failed: {result.error}", severity="error")

    # -- Instruction history (#376) ------------------------------------------

    def _record_instruction(self, text: str, agent_name: str) -> None:
        """Record an instruction in the history ring buffer."""
        from .tui_widgets.instruction_history_modal import HistoryEntry, MAX_HISTORY

        self._instruction_history.insert(0, HistoryEntry(text=text, agent_name=agent_name))
        self._instruction_history = self._instruction_history[:MAX_HISTORY]

        # Update per-agent last command on the widget (#413)
        for widget in self.query(SessionSummary):
            if widget.session.name == agent_name:
                widget.last_command = text
                break

    def action_open_instruction_history(self) -> None:
        """Open the instruction history modal (#376)."""
        if not self._instruction_history:
            self.notify("No instructions sent yet — send one with 'i' first", severity="warning")
            return
        modal = self.query_one("#instruction-history-modal", InstructionHistoryModal)
        self._dialog_will_open()
        modal.show(self._instruction_history, self)

    def on_instruction_history_modal_reinject_requested(
        self, message: InstructionHistoryModal.ReinjectRequested
    ) -> None:
        """Handle reinject request from instruction history modal (#376)."""
        focused = self.focused
        if not isinstance(focused, SessionSummary):
            # No agent focused — pre-fill the command bar so user can pick a target
            self.action_focus_command_bar()
            try:
                command_bar = self.query_one("#command-bar", CommandBar)
                input_widget = command_bar.query_one("#cmd-input")
                input_widget.value = message.text
            except NoMatches:
                pass
            self._dialog_did_close()
            return

        session = focused.session
        if getattr(session, 'is_remote', False):
            if self._guard_remote(session):
                self._dialog_did_close()
                return
            result = self._sister_controller.send_instruction(
                session.source_url, session.source_api_key,
                session.name, text=message.text,
            )
            if result.ok:
                self._record_instruction(message.text, session.name)
                self.notify(f"Reinjected to remote agent {session.name}")
            else:
                self.notify(f"Remote error: {result.error}", severity="error")
            self._dialog_did_close()
            return

        launcher = AgentLauncher(
            tmux_session=self.tmux_session,
            session_manager=self.session_manager
        )
        if launcher.send_to_session_by_id(session.id, message.text):
            self._record_instruction(message.text, session.name)
            self.notify(f"Reinjected to {session.name}")
        else:
            self.notify(f"Failed to send to {session.name}", severity="error")
        self._dialog_did_close()

    def on_instruction_history_modal_cancelled(
        self, message: InstructionHistoryModal.Cancelled
    ) -> None:
        """Handle instruction history modal dismissal (#376)."""
        self._dialog_did_close()

    # --- Command palette: agents, tags and commands (#420, #357, #482) ---

    def action_command_palette(self) -> None:
        """Open the command palette on commands (#482)."""
        self._open_palette("commands")

    def action_jump_to_agent(self) -> None:
        """Open the palette on agents: VSCode-style jump by name (#420)."""
        if self.tui_mode == "jobs":
            return
        if not self._get_widgets_in_session_order():
            self.notify("No agents to jump to", severity="information")
            return
        self._open_palette("agents")

    def action_filter_by_tag(self) -> None:
        """Filter visible agents by tag (#357).

        Opens the palette on the distinct tags currently in use (across
        local + remote), with a `(clear filter)` row while a filter is on.
        """
        if self.tui_mode == "jobs":
            return
        if not self._palette_tags():
            self.notify(
                "No tags in use yet — apply with `overcode tag <agent> <tag>`.",
                severity="information",
            )
            return
        self._open_palette("tags")

    def _open_palette(self, mode: str) -> None:
        try:
            palette = self.query_one("#command-palette", CommandPalette)
        except NoMatches:
            return
        self._dialog_will_open()
        palette.open(
            mode,
            agents=self._palette_agents(),
            tags=self._palette_tags(),
            sorts=self._palette_sorts(),
            keymap=self._effective_keymap().keys_by_action(),
            recent=self._prefs.recent_commands,
            app_ref=self,
        )

    def _palette_agents(self) -> list:
        if self.tui_mode == "jobs":
            return []
        from .status_constants import get_status_symbol
        candidates = []
        for w in self._get_widgets_in_session_order():
            symbol, color = get_status_symbol(w.detected_status, self.emoji_free)
            candidates.append(JumpCandidate(
                session_id=w.session.id,
                name=w.session.name,
                repo=getattr(w.session, 'repo_name', '') or '',
                branch=getattr(w.session, 'branch', '') or '',
                status=symbol,
                status_style=color,
            ))
        return candidates

    def _palette_sorts(self) -> list:
        from .command_palette import sort_choices
        return sort_choices(self._prefs.sort_mode, self._prefs.sort_reversed)

    def _palette_tags(self) -> list:
        # Count tags over all sessions (not just visible) so the user can
        # filter back in to a tag they just filtered out.
        tag_counts: dict[str, int] = {}
        for s in self.sessions:
            for t in (getattr(s, 'tags', None) or []):
                tag_counts[t] = tag_counts.get(t, 0) + 1
        candidates: list[JumpCandidate] = []
        if self.tag_filter:
            candidates.append(JumpCandidate(
                session_id="", name="(clear filter)", repo=f"current: {self.tag_filter}",
            ))
        for tag in sorted(tag_counts):
            n = tag_counts[tag]
            candidates.append(JumpCandidate(
                session_id=tag, name=tag, repo=f"{n} agent{'s' if n != 1 else ''}",
            ))
        return candidates

    def on_command_palette_agent_chosen(self, message: CommandPalette.AgentChosen) -> None:
        for i, w in enumerate(self._get_widgets_in_session_order()):
            if w.session.id == message.session_id:
                self._user_navigated = True
                self.focused_session_index = i
                break
        self._dialog_did_close()

    def on_command_palette_tag_chosen(self, message: CommandPalette.TagChosen) -> None:
        self.tag_filter = message.tag
        if message.tag is None:
            self.notify("Tag filter cleared", severity="information")
        else:
            self.notify(f"Filtering by tag: {message.tag}", severity="information")
        self._dialog_did_close()
        self.update_session_widgets()

    def on_command_palette_command_chosen(self, message: CommandPalette.CommandChosen) -> None:
        """Run a command picked in the palette.

        Arrives after the palette handed focus back, so actions that read
        the focused agent see the right one. Called directly rather than
        through run_action: check_action blocks every action while a modal
        is visible, which the palette still is when kept open (Tab).
        """
        self.record_activity("action", action=message.action, via="palette",
                             q=message.query or None, rank=message.rank)
        if self._activity.active:
            self.note_recent_action(message.action, "palette")
        self.mentor_saw_action(message.action, "palette")
        recent = [message.action] + [a for a in self._prefs.recent_commands if a != message.action]
        self._prefs.recent_commands = recent[:10]
        self._save_prefs()
        if not message.keep_open:
            self._dialog_did_close()
        method = getattr(self, f"action_{message.action}", None)
        if method is None:
            return
        target = message.focus_target
        self.screen.set_focus(target if target is not None and target.is_attached else None)
        method()
        if message.keep_open:
            try:
                palette = self.query_one("#command-palette", CommandPalette)
            except NoMatches:
                return
            self.screen.set_focus(palette)
            palette.refresh()

    def on_command_palette_sort_chosen(self, message: CommandPalette.SortChosen) -> None:
        """A sort picked in the S picker (#487). With Tab the palette stays
        open, and its rows are refreshed so the lit row and arrow follow."""
        if not message.keep_open:
            self._dialog_did_close()
        self.set_sort_mode(message.mode)
        if message.keep_open:
            try:
                palette = self.query_one("#command-palette", CommandPalette)
            except NoMatches:
                return
            palette.update_sorts(self._palette_sorts())

    def on_column_header_clicked(self, message: ColumnHeader.Clicked) -> None:
        """Click a header to sort by it; click it again to reverse (#487)."""
        from .summary_columns import COLUMNS_BY_ID
        from .tui_logic import sort_mode_for_column
        self.record_activity("action", action="sort_by_column", via="click",
                             column=message.column_id)
        col = COLUMNS_BY_ID.get(message.column_id)
        if col is None or col.sort_key is None:
            name = col.name if col is not None else message.column_id
            self.notify(f"{name} is not sortable", severity="information")
            return
        self.set_sort_mode(sort_mode_for_column(message.column_id))

    def on_command_palette_closed(self, message: CommandPalette.Closed) -> None:
        self._dialog_did_close()

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
        if self.in_split:
            # Detach first so the user returns to their previous session, then
            # exit so the viewer process stops consuming CPU.
            import subprocess
            subprocess.run([*_tmux_base(), "detach-client"], capture_output=True)
        self.exit()

    def _resize_split(self, delta: int) -> None:
        """Resize the tmux split pane by delta rows."""
        if not self.in_split:
            return
        import subprocess
        subprocess.run(
            [*_tmux_base(), "resize-pane", "-t", self._tui_pane_target(),
             "-U" if delta > 0 else "-D", str(abs(delta))],
            capture_output=True,
        )
        # Resize linked session windows to match the new bottom pane size
        linked = self.tmux_sync_target
        if linked:
            result = subprocess.run(
                [*_tmux_base(), "list-windows", "-t", linked, "-F", "#{window_id}"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                for win_id in result.stdout.strip().splitlines():
                    subprocess.run(
                        [*_tmux_base(), "resize-window", "-t", win_id, "-A"],
                        capture_output=True,
                    )

    def action_split_grow(self) -> None:
        """Grow the monitor pane (shrink terminal pane)."""
        self._resize_split(3)

    def action_split_shrink(self) -> None:
        """Shrink the monitor pane (grow terminal pane)."""
        self._resize_split(-3)

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

    # ── Event loop heartbeat probe ──────────────────────────────────────

    def _record_heartbeat(self) -> None:
        """Record one heartbeat tick. Runs every 100ms on the event loop."""
        if not getattr(self, "_heartbeat_enabled", False):
            return
        now = time.monotonic()
        if self._heartbeat_last > 0:
            delta_ms = (now - self._heartbeat_last) * 1000.0
            iso_ts = datetime.now().isoformat(timespec="milliseconds")
            self._append_heartbeat((iso_ts, f"{delta_ms:.1f}", ""))
        self._heartbeat_last = now

    def _mark_event(self, name: str) -> None:
        """Record a named event marker in the heartbeat log.

        A no-op when the probe is disabled: the flush timer only exists when
        it is enabled, so appending here would grow the buffer unbounded.
        """
        if not getattr(self, "_heartbeat_enabled", False):
            return
        now = time.monotonic()
        delta_ms = (now - self._heartbeat_last) * 1000.0 if self._heartbeat_last > 0 else 0.0
        iso_ts = datetime.now().isoformat(timespec="milliseconds")
        self._append_heartbeat((iso_ts, f"{delta_ms:.1f}", name))
        self._heartbeat_last = now

    def _append_heartbeat(self, row: tuple) -> None:
        """Buffer one probe row, dropping the oldest half at the cap (backstop)."""
        log = self._heartbeat_log
        if len(log) >= HEARTBEAT_LOG_MAX_ENTRIES:
            del log[: len(log) // 2]
        log.append(row)

    def _flush_heartbeat(self) -> None:
        """Write buffered heartbeat data to CSV. Runs every 5s."""
        if not self._heartbeat_log:
            return
        buf = self._heartbeat_log
        self._heartbeat_log = []
        try:
            path = self._heartbeat_csv_path
            path.parent.mkdir(parents=True, exist_ok=True)
            write_header = not path.exists()
            with open(path, "a") as f:
                if write_header:
                    f.write("timestamp,delta_ms,event\n")
                for ts, delta, event in buf:
                    f.write(f"{ts},{delta},{event}\n")
            # Diagnostic-only file — hard cap as a backstop (#465). getattr
            # default covers lightweight test doubles built via __new__.
            from .settings import cap_diagnostics_csv
            cap_diagnostics_csv(path, getattr(self, "_heartbeat_cap_mb", 100.0))
        except OSError:
            pass  # Best effort

    def _log_status_change(self, agent_name: str, old_status: str, new_status: str,
                           source: str, focused: bool, content_changed: bool = False) -> None:
        """Record a status change event for diagnostics."""
        iso_ts = datetime.now().isoformat(timespec="milliseconds")
        self._status_change_log.append(
            (iso_ts, agent_name, old_status, new_status, source, "Y" if focused else "N", "Y" if content_changed else "N")
        )

    def _flush_status_changes(self) -> None:
        """Write buffered status change data to CSV."""
        if not self._status_change_log:
            return
        buf = self._status_change_log
        self._status_change_log = []
        try:
            path = self._status_change_csv_path
            path.parent.mkdir(parents=True, exist_ok=True)
            write_header = not path.exists()
            with open(path, "a") as f:
                if write_header:
                    f.write("timestamp,agent,old_status,new_status,source,focused,content_changed\n")
                for row in buf:
                    f.write(",".join(row) + "\n")
            # Same diagnostic-only backstop as event_loop_timing.csv (#465):
            # opt-in, but once on it is append-only for the life of the TUI.
            from .settings import cap_diagnostics_csv
            cap_diagnostics_csv(path, getattr(self, "_heartbeat_cap_mb", 100.0))
        except OSError:
            pass

    # ── End heartbeat probe ──────────────────────────────────────────

    def on_unmount(self) -> None:
        """Clean up terminal state on exit"""
        import sys
        # A dialog/sister-view zoom must not outlive the viewer. After exit
        # the pane holds at a relaunch prompt (cli/split.py _hold_wrapper) or
        # is respawned by `overcode tmux`; a leftover zoom would keep the
        # bottom terminal pane hidden behind the monitor.
        try:
            self._unzoom_tui_pane()
        except Exception:
            pass
        if self._engine_client is not None:
            self._engine_client.stop()
        # Clean up SSH proxy windows
        self._cleanup_ssh_proxies()
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
