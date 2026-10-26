"""
Status constants and mappings for Overcode.

Centralizes all status-related constants, colors, emojis, and display
mappings used throughout the application.
"""

from dataclasses import dataclass, field
from typing import Optional, Tuple


# =============================================================================
# Capture Defaults
# =============================================================================

DEFAULT_CAPTURE_LINES = 500  # Base capture depth for display-oriented pane captures
STATUS_CAPTURE_LINES = 100   # Reduced capture for status detection only (non-focused agents)


# =============================================================================
# Agent Status Values
# =============================================================================

STATUS_RUNNING = "running"
STATUS_WAITING_USER = "waiting_user"
STATUS_TERMINATED = "terminated"  # Agent CLI exited, shell prompt showing
STATUS_ASLEEP = "asleep"  # Human marked agent as paused/snoozed (excluded from stats)
STATUS_RUNNING_HEARTBEAT = "running_heartbeat"  # Running from automated heartbeat (#171)
STATUS_WAITING_APPROVAL = "waiting_approval"  # Waiting on approval/plan/decision (#22)
STATUS_WAITING_HEARTBEAT = "waiting_heartbeat"  # Waiting but heartbeat will auto-resume
STATUS_ERROR = "error"  # API timeout, etc. (#22)
STATUS_HEARTBEAT_START = "heartbeat_start"  # First observation of heartbeat-triggered run (timeline only)
STATUS_DONE = "done"  # Child agent completed its delegated work (#244)
STATUS_WAITING_OVERSIGHT = "waiting_oversight"  # Child stopped, awaiting oversight report
STATUS_BUSY_SLEEPING = "busy_sleeping"  # Agent running but executing a sleep command (#289) or waiting on a live Monitor stream (#441)
STATUS_UNKNOWN = "unknown"  # Not known yet (booting, before its first hook) or detection failing

# All valid agent status values
ALL_STATUSES = [
    STATUS_RUNNING,
    STATUS_WAITING_USER,
    STATUS_TERMINATED,
    STATUS_ASLEEP,
    STATUS_RUNNING_HEARTBEAT,
    STATUS_WAITING_APPROVAL,
    STATUS_WAITING_HEARTBEAT,
    STATUS_ERROR,
    STATUS_HEARTBEAT_START,
    STATUS_DONE,
    STATUS_WAITING_OVERSIGHT,
    STATUS_BUSY_SLEEPING,
    STATUS_UNKNOWN,
]

# A child agent whose turn is over, reported or not: at rest (⚪). Its
# outcome is the result's (RESULT_KINDS, the ``l`` result mode), not the light's.
AT_REST_STATUSES = frozenset({STATUS_DONE, STATUS_WAITING_OVERSIGHT})


# =============================================================================
# Daemon Status Values
# =============================================================================

DAEMON_STATUS_ACTIVE = "active"
DAEMON_STATUS_IDLE = "idle"
DAEMON_STATUS_WAITING = "waiting"
DAEMON_STATUS_SUPERVISING = "supervising"
DAEMON_STATUS_SLEEPING = "sleeping"
DAEMON_STATUS_STOPPED = "stopped"
DAEMON_STATUS_NO_AGENTS = "no_agents"


# =============================================================================
# Presence State Values
# =============================================================================

PRESENCE_ASLEEP = 0
PRESENCE_LOCKED = 1
PRESENCE_IDLE = 2       # Was PRESENCE_INACTIVE
PRESENCE_ACTIVE = 3
PRESENCE_TUI_ACTIVE = 4

PRESENCE_STATE_NAMES = {
    PRESENCE_ASLEEP: "asleep",
    PRESENCE_LOCKED: "locked",
    PRESENCE_IDLE: "idle",
    PRESENCE_ACTIVE: "active",
    PRESENCE_TUI_ACTIVE: "tui_active",
}


# =============================================================================
# Status to Symbol+Color: the classic status theme
# =============================================================================
#
# What a status shows comes from the active theme (status_theme.py,
# get_status_symbol below); these tables are the classic one, for readers
# that need a fixed glyph (the supervisor's prompt, old tests).

STATUS_SYMBOLS = {
    STATUS_RUNNING: ("🟢", "green"),
    STATUS_WAITING_USER: ("🔴", "red"),
    STATUS_TERMINATED: ("⚫", "dim"),
    STATUS_ASLEEP: ("💤", "dim"),  # Sleeping/snoozed
    STATUS_RUNNING_HEARTBEAT: ("💚", "green"),  # Heartbeat-triggered (#171)
    STATUS_WAITING_APPROVAL: ("🟠", "orange1"),  # Approval waiting (#22)
    STATUS_WAITING_HEARTBEAT: ("💛", "yellow"),  # Waiting but heartbeat will auto-resume
    STATUS_ERROR: ("🟣", "magenta"),  # Error state (#22)
    STATUS_HEARTBEAT_START: ("💚", "green"),  # Heartbeat commencement (timeline only)
    STATUS_DONE: ("⚪", "grey85"),  # Child at rest, reported (#244)
    STATUS_WAITING_OVERSIGHT: ("⚪", "grey85"),  # Child at rest, no report yet
    STATUS_BUSY_SLEEPING: ("🟡", "yellow"),  # Running but sleeping (#289) or watching a live Monitor (#441)
    STATUS_UNKNOWN: ("❔", "dim"),  # Booting before its first hook, or detection failing
}


# =============================================================================
# Derived Emoji and Color Mappings (classic theme)
# =============================================================================

STATUS_EMOJIS = {k: v[0] for k, v in STATUS_SYMBOLS.items()}
STATUS_COLORS = {k: v[1] for k, v in STATUS_SYMBOLS.items()}


# ASCII fallbacks for all emoji used in the TUI (#315)
# Used when emoji_free mode is active (terminals without emoji font support).
EMOJI_ASCII = {
    # Status indicators
    "🟢": "[R]",
    "🔴": "[W]",
    "⚫": "[X]",
    "💤": "[Z]",
    "💚": "[H]",
    "🟠": "[A]",
    "💛": "[h]",
    "🟣": "[E]",
    "🟡": "[S]",
    "⚪": "[.]",
    "❔": "[?]",
    # Results (RESULT_KINDS)
    "📭": "Un",
    "✅": "ok",
    "❌": "XX",
    # Tool indicators
    "🖥️": "Sh",
    "📖": "Rd",
    "✏️": "Wr",
    "🔨": "Ed",  # Edit
    "🔧": "Tl",  # wrench — DTL tool badge
    "🔍": "Gl",
    "🔎": "Gr",
    "🌐": "Wf",
    "🕵️": "Ws",
    "🧵": "Tk",
    "📓": "Nb",
    "📋": "Cb",
    "📝": "Tw",
    "🔹": "--",
    # Permission modes
    "🔥": "B!",
    "🏃": "P>",
    "👮": "N:",
    "🧠": "A~",  # Claude auto mode (a classifier decides); ⚡ accept edits is ">>"
    "🧭": "P?",  # plan mode
    # Activity/metrics
    "🔔": "(!)",
    "⏰": "@@",  # as the schedule_wakeup badge (was "AL": two forms in one column)
    "📚": "CW",
    "💓": "<3",
    "💰": "$$",
    "⏳": "~~",
    "⌛": "TO",  # oversight timeout expired (a result)
    "⌚": "Md",  # median work time (was ⏱, text-default: an outline glyph)
    "🤖": "Ro",
    "👤": "Hu",
    "🐚": "Bg",
    "👶": "Ch",
    "🚜": "Jb",
    "🎣": "Hk",  # enhanced context hooks
    "🧱": "Br",  # Bedrock provider
    "🤝": "Tm",
    "🕐": "Tc",
    # Content modes
    "💬": "Sm",
    "🎯": "SO",
    # Presence states
    "⏻": "Pw",
    "🔒": "Lk",
    "🧘": "Id",
    "🚶": "Ac",
    # Value arrows
    "⏫️": "^^",
    "⏬️": "vv",
    "⏹️": "==",
    # Misc
    "⚠": "!W",
    "➖": "--",
    "✓": "ok",
    "▼": "v ",
    "▶": "> ",
    # Status detail badges (#TBD — two-column status model)
    "🔁": "Cr",
    "📡": "Mo",
    "🔌": "Bg",
    "🏭": "Bb",
    "🔭": "Wa",
    "📻": "Bs",
    "🧟": "St",
    "⚡": ">>",
    "😴": "Sl",
    "🔐": "Pm",  # permission (was 🛡 — text-default)
    "❗": "!W",  # error (was ⚠ — text-default)
    "❓": "??",
    "📥": "In",
    "👥": "Sa",
    # A reset trip counter on the spend cells (trip.py; "~" marks an auto name)
    "↺": "*",
}


# A workflow run (🧩) in emoji-free mode: the SubAgents cell, the list's
# workflow rows and the timeline strip. Not an EMOJI_ASCII entry, since 🧩
# is also the unknown-skill emoji; not "Wf", which is WebFetch's (🌐).
WORKFLOW_GLYPH, WORKFLOW_ASCII = "🧩", "Wk"


def emoji_or_ascii(char: str, emoji_free: bool) -> str:
    """Return ASCII fallback if emoji_free mode is active, else the emoji."""
    if emoji_free:
        return EMOJI_ASCII.get(char, char)
    return _safe_emoji(char)


# Variation Selector-16 (U+FE0F) forces emoji-style rendering for codepoints
# whose default presentation is text (🖥 desktop, ✏ pencil, 🕵 detective, 🏖
# beach, etc.). Mac/iTerm-class terminals handle the VS16 cluster correctly
# at width 2; Windows Terminal and KDE Konsole historically render the base
# at width 1 + a placeholder for the selector, which throws off Rich's cell
# accounting and overlaps subsequent text. Strip VS16 outside the whitelist
# so the base character renders as text-style (thinner / monochrome) but at
# a width every terminal agrees on.
_VS16 = "️"


def _detect_terminal_emoji_support() -> bool:
    """True if the terminal handles VS16-bearing emoji clusters without drift.

    Whitelist approach — only known-good terminals opt in. Respects
    OVERCODE_EMOJI_PRESENTATION=color|text|auto for explicit override.
    """
    import os
    override = os.environ.get("OVERCODE_EMOJI_PRESENTATION", "").lower()
    if override == "color":
        return True
    if override == "text":
        return False
    # Known-good (full-color VS16): iTerm2, modern Apple Terminal, WezTerm,
    # Kitty, Alacritty, Ghostty. Anything else → safe text-style fallback.
    if os.environ.get("WT_SESSION"):
        return False  # Windows Terminal — known to drift
    if os.environ.get("KONSOLE_VERSION"):
        return False  # KDE Konsole — variable, conservative
    term_program = os.environ.get("TERM_PROGRAM", "")
    if term_program in {"iTerm.app", "Apple_Terminal", "WezTerm", "ghostty"}:
        return True
    if os.environ.get("KITTY_WINDOW_ID"):
        return True
    if "alacritty" in os.environ.get("TERM", "").lower():
        return True
    return False  # default: safe


_FULL_COLOR_EMOJI = _detect_terminal_emoji_support()


def _safe_emoji(s: str) -> str:
    """Strip VS16 from an emoji string when the terminal isn't trusted to
    render the variation-selector cluster at the expected cell width."""
    if _FULL_COLOR_EMOJI or _VS16 not in s:
        return s
    return s.replace(_VS16, "")


# Permissiveness mode → emoji mapping (shared by TUI, CLI, and web API)
PERMISSIVENESS_EMOJIS = {
    "bypass": "🔥",
    "permissive": "🏃",
    "normal": "👮",
    # Synthetic modes: the in-session mode shift+tab switches to, read from
    # the pane's status bar (status_patterns.extract_in_session_mode, #444).
    # Never stored on the session; see effective_permission_mode.
    "accept_edits": "⚡",
    "auto": "🧠",
    "plan": "🧭",
}

# What each mode means: the MOD column's hover.
PERMISSIVENESS_MEANINGS = {
    "bypass": "bypass: launched skipping every permission check",
    "permissive": "permissive: launched not to ask (Claude: don't ask mode)",
    "normal": "normal: asks before edits and commands",
    "accept_edits": "accept edits: edits go through, commands still ask",
    "auto": "auto mode: a classifier decides what to allow",
    "plan": "plan mode: read-only until the plan is approved",
}


def effective_permission_mode(launch_mode: str, in_session_mode: str = "") -> str:
    """The mode an agent is in now: its launch setting, overlaid by the
    in-session mode its status bar shows.

    The status bar is the live truth: a mode switched on in the session
    (Shift+Tab) shows whatever the agent was launched with. Bypass and
    don't-ask show no in-session mode, so the launch mode stands for them.
    """
    if in_session_mode in ("plan", "accept_edits", "auto"):
        return in_session_mode
    return launch_mode


def get_permissiveness_emoji(mode: str, emoji_free: bool = False) -> str:
    """Get the emoji for a permissiveness mode."""
    return emoji_or_ascii(PERMISSIVENESS_EMOJIS.get(mode, "👮"), emoji_free)


def get_status_emoji(status: str, emoji_free: bool = False) -> str:
    """The status's glyph in the active status theme (status_theme.py)."""
    return get_status_symbol(status, emoji_free)[0]


def get_status_color(status: str) -> str:
    """The status's colour in the active status theme."""
    from .status_theme import active_theme
    return active_theme().colour_for(status)


def get_status_symbol(status: str, emoji_free: bool = False) -> Tuple[str, str]:
    """(glyph, colour) for a status in the active status theme.

    STATUS_SYMBOLS above is the classic theme, kept for its readers; what
    a status shows comes from here, so a theme change shows at once.
    """
    from .status_theme import active_theme
    return active_theme().symbol_for(status, emoji_free)


# =============================================================================
# Timeline Character Mappings
# =============================================================================

AGENT_TIMELINE_CHARS = {
    STATUS_RUNNING: "█",
    STATUS_WAITING_USER: "░",
    STATUS_TERMINATED: "×",  # Small X - terminated
    STATUS_ASLEEP: "░",  # Light shade hatching (grey) - sleeping/paused
    STATUS_RUNNING_HEARTBEAT: "█",  # Same block but green color (#171)
    STATUS_WAITING_APPROVAL: "▒",  # Medium shade (#22)
    STATUS_WAITING_HEARTBEAT: "▒",  # Medium shade - waiting but heartbeat will auto-resume
    STATUS_ERROR: "▓",  # Dense shade (#22)
    STATUS_HEARTBEAT_START: "💚",  # Green heart emoji - rendered specially by timeline widget
    STATUS_DONE: "·",  # Child at rest (#244)
    STATUS_WAITING_OVERSIGHT: "·",  # Child at rest, no report yet
    STATUS_BUSY_SLEEPING: "█",  # Solid - doesn't need human input (#289)
    STATUS_UNKNOWN: "?",  # Not known (booting, detection failing)
}


def get_agent_timeline_char(status: str) -> str:
    """Get timeline character for an agent status."""
    return AGENT_TIMELINE_CHARS.get(status, "─")


PRESENCE_TIMELINE_CHARS = {
    PRESENCE_ASLEEP: " ",
    PRESENCE_LOCKED: "░",
    PRESENCE_IDLE: "▒",
    PRESENCE_ACTIVE: "▓",
    PRESENCE_TUI_ACTIVE: "█",
}


def get_presence_timeline_char(state: int) -> str:
    """Get timeline character for a presence state."""
    return PRESENCE_TIMELINE_CHARS.get(state, "─")


# =============================================================================
# Presence State Colors
# =============================================================================

PRESENCE_COLORS = {
    PRESENCE_ASLEEP: "#1a1a2e",
    PRESENCE_LOCKED: "red",
    PRESENCE_IDLE: "orange1",
    PRESENCE_ACTIVE: "yellow",
    PRESENCE_TUI_ACTIVE: "green",
}


def get_presence_color(state: int) -> str:
    """Get color for a presence state."""
    return PRESENCE_COLORS.get(state, "dim")


# =============================================================================
# Daemon Status Display
# =============================================================================

DAEMON_STATUS_STYLES = {
    DAEMON_STATUS_ACTIVE: ("●", "green"),
    DAEMON_STATUS_IDLE: ("○", "yellow"),
    DAEMON_STATUS_WAITING: ("◐", "yellow"),
    DAEMON_STATUS_SUPERVISING: ("●", "cyan"),
    DAEMON_STATUS_SLEEPING: ("○", "dim"),
    DAEMON_STATUS_STOPPED: ("○", "red"),
    DAEMON_STATUS_NO_AGENTS: ("○", "dim"),
}


def get_daemon_status_style(status: str) -> Tuple[str, str]:
    """Get (symbol, color) for daemon status display."""
    return DAEMON_STATUS_STYLES.get(status, ("?", "dim"))


# =============================================================================
# Status Categorization
# =============================================================================


GREEN_STATUSES = frozenset({STATUS_RUNNING, STATUS_RUNNING_HEARTBEAT, STATUS_HEARTBEAT_START, STATUS_BUSY_SLEEPING})
ASLEEP_STATUSES = frozenset({STATUS_ASLEEP})


def is_green_status(status: str) -> bool:
    """Check if a status is considered 'green' (actively working)."""
    return status in GREEN_STATUSES


def is_asleep(status: str) -> bool:
    """Check if status indicates agent is asleep (paused by human)."""
    return status in ASLEEP_STATUSES


# =============================================================================
# Two-column status model (#TBD)
# =============================================================================
#
# Column 1 — a 4-color summary of *why* the agent isn't immediately available
# for new instruction. Priority RED>ORANGE>GREEN>YELLOW when multiple buckets
# apply (e.g. mid-tool-call AND a cron is armed → GREEN; permission prompt
# fires mid-tool → ORANGE).
#
# Column 2 — a list of detail badges that explain the column-1 color. Lives
# in the ⏰ slot today; rendered as stacked emoji + optional ETA/count.
#
# Lifecycle states (ASLEEP / TERMINATED / ERROR) sit *outside* this scheme —
# they keep their existing status enum and have no column.

STATUS_COLOR_GREEN = "color_green"      # acting (generating | tool in flight | sync-blocked on external)
STATUS_COLOR_ORANGE = "color_orange"    # waiting on a quick yes/no approval
STATUS_COLOR_YELLOW = "color_yellow"    # armed — will resume on its own (timer / cron / monitor / bg task)
STATUS_COLOR_RED = "color_red"          # needs substantive user input/decision
STATUS_COLOR_REST = "color_rest"        # a child whose turn is over: at rest, never rings

# Priority order (higher index = higher priority). Used when a single agent is
# in multiple buckets simultaneously.
_COLOR_PRIORITY = {
    STATUS_COLOR_REST: -1,
    STATUS_COLOR_YELLOW: 0,
    STATUS_COLOR_GREEN: 1,
    STATUS_COLOR_ORANGE: 2,
    STATUS_COLOR_RED: 3,
}


def color_priority(color: str) -> int:
    """Return priority for a status color (higher = wins)."""
    return _COLOR_PRIORITY.get(color, -2)


# Single source of truth for badge emoji + the column it belongs to. New
# badge kinds add a row here; everything else (hook reducer, column render,
# ASCII fallback) picks them up automatically.
#
# Fields: (emoji, color, ascii_fallback)

BADGE_KINDS: dict[str, Tuple[str, str, str]] = {
    # ---- YELLOW (armed) ----
    "schedule_wakeup": ("⏰", STATUS_COLOR_YELLOW, "@@"),  # ScheduleWakeup pending
    "cron":            ("🔁", STATUS_COLOR_YELLOW, "Cr"),  # CronCreate registered
    "monitor":         ("📡", STATUS_COLOR_YELLOW, "Mo"),  # Monitor stream open
    "bg_task":         ("🔌", STATUS_COLOR_YELLOW, "Bg"),  # Background Bash/Workflow
    # Background shells by kind (#515, background_shells): a service still
    # arms the agent (owner decision), it just says what it is
    "bg_batch":        ("🏭", STATUS_COLOR_YELLOW, "Bb"),  # build / tests: ends and wakes it
    "bg_watcher":      ("🔭", STATUS_COLOR_YELLOW, "Wa"),  # waits for something, then wakes it
    "bg_service":      ("📻", STATUS_COLOR_YELLOW, "Bs"),  # dev server, tail -f: never ends
    "subagent":        ("👥", STATUS_COLOR_YELLOW, "Sa"),  # Subagent still running (#507)
    "heartbeat":       ("💓", STATUS_COLOR_YELLOW, "Hb"),  # Overcode heartbeat will re-prompt
    "children":        ("👶", STATUS_COLOR_YELLOW, "Ch"),  # turn over, child agents still working

    # ---- GREEN (acting) ----
    "generating":      ("⚡", STATUS_COLOR_GREEN,  ">>"),  # token generation, no tool
    # Note: emoji-default codepoints only. Text-default ones (⚙, 🛡, ⚠)
    # render with ambiguous width on Windows Terminal / KDE Konsole — they
    # look like 1-cell monochrome glyphs there while Rich budgets 2 cells,
    # cascading into column overlap. Pick swaps with Emoji_Presentation=Yes.
    "tool":            ("🔧", STATUS_COLOR_GREEN, "Tl"),   # generic foreground tool in flight
    "blocked_ci":      ("🌐", STATUS_COLOR_GREEN, "CI"),   # gh run watch / pr checks --watch
    "blocked_process": ("⏳", STATUS_COLOR_GREEN, "Wt"),   # tail -f / kubectl wait / docker wait
    "blocked_sleep":   ("😴", STATUS_COLOR_GREEN, "Sl"),   # bash `sleep N` (foreground)

    # ---- ORANGE (approval) ----
    "permission":      ("🔐", STATUS_COLOR_ORANGE, "Pm"),  # PermissionRequest
    "plan_approval":   ("📋", STATUS_COLOR_ORANGE, "Pl"),  # ExitPlanMode
    # Pre-0.6 sisters may still publish this for a stopped child: it draws
    # as nothing (the light says at rest). Drop after one release.
    "oversight":       ("", STATUS_COLOR_REST, ""),

    "ask_question":    ("❓", STATUS_COLOR_ORANGE, "??"),  # AskUserQuestion open: a question is an approval

    # ---- RED (needs input) ----
    "awaiting_input":  ("📥", STATUS_COLOR_RED,   "In"),   # Stop fired, no obligations, awaiting next prompt
    "drowsy":          ("😴", STATUS_COLOR_RED,   "zz"),   # awaiting input past idle.drowsy_after (views only)
    "error":           ("❗", STATUS_COLOR_RED,   "!W"),    # API/hook error needing intervention
    "overdue":         ("⌛", STATUS_COLOR_RED,   "Od"),    # a wakeup well past its time never came

    # ---- any colour: a warning beside the light (conversation_warning) ----
    "conv_adopted":    ("🔀", STATUS_COLOR_REST,  "Cv"),   # resumed a conversation overcode didn't start
    "conv_shared":     ("🔀", STATUS_COLOR_REST,  "Cv"),   # in another agent's conversation
    "conv_unplaced":   ("🔀", STATUS_COLOR_REST,  "Cv"),   # a conversation it can't place: ignored
    "bg_worker":       ("🌙", STATUS_COLOR_REST,  "Bw"),   # its conversation is in Claude's background worker
    "bg_stuck":        ("🧟", STATUS_COLOR_REST,  "St"),   # a watcher still polling a target that ended (#515)
}


# What each badge means, for the hover popup on the status-detail column
BADGE_MEANINGS: dict[str, str] = {
    "schedule_wakeup": "wakeup scheduled",
    "cron":            "cron job registered",
    "monitor":         "watching a Monitor stream",
    "bg_task":         "background shell or task running",
    "bg_batch":        "background shell running (build, tests): wakes the agent when it ends",
    "bg_watcher":      "background watcher: wakes the agent when what it waits for happens",
    "bg_service":      "background service (server, tail -f): runs until stopped",
    "heartbeat":       "overcode heartbeat will re-prompt",
    "subagent":        "subagent, workflow or teammate still running",
    "children":        "child agents still working",
    "oversight":       "child at rest (an older sister)",
    "generating":      "generating a response",
    "tool":            "running a tool",
    "blocked_ci":      "waiting on CI",
    "blocked_process": "waiting on a process",
    "blocked_sleep":   "sleeping",
    "permission":      "permission prompt open",
    "plan_approval":   "plan waiting for approval",
    "ask_question":    "asking you a question",
    "awaiting_input":  "waiting for your next prompt",
    "drowsy":          "idle a while (idle.drowsy_after), nothing pending",
    "error":           "error needs attention",
    "overdue":         "a scheduled wakeup never came",
    "conv_adopted":    "resumed a conversation overcode did not start; now tracked as this agent's",
    "conv_shared":     "in a conversation another agent owns; not adopted, restart returns to the old one",
    "conv_unplaced":   "a conversation overcode can't place ran in this agent's name; its events are ignored",
    "bg_worker":       "running in Claude's background worker (Remote Control, /background); still this agent",
    "bg_stuck":        "a watcher still polling something that ended: it will never fire (overcode shells)",
}


# =============================================================================
# The legend: what the traffic light and the results say
# =============================================================================
#
# One table each; the help overlay and docs/tui-guide.md are checked against
# them (tests/unit/test_legend_consistency.py). Every glyph is emoji
# presentation by default, so it is two cells wide in every terminal.

@dataclass(frozen=True)
class LegendEntry:
    glyph: str
    name: str
    meaning: str
    statuses: Tuple[str, ...] = ()
    variant: bool = False  # a shade of a main light (heartbeat, error, asleep)
    key: str = ""    # the light's name in a status theme (status_theme.py)
    ascii: str = ""  # its emoji-free form, whatever glyph a theme gives it


# The traffic light answers one question: what is the agent doing, and
# does it need me?
LIGHT_LEGEND: Tuple[LegendEntry, ...] = (
    LegendEntry("🟢", "Working", "generating or running a tool",
                (STATUS_RUNNING,), key="working", ascii="[R]"),
    LegendEntry("🟡", "Waiting on something automatic",
                "background work, a wakeup or child agents will resume it",
                (STATUS_BUSY_SLEEPING,), key="automatic", ascii="[S]"),
    LegendEntry("🟠", "Needs approval", "a permission prompt, a question or a plan to approve",
                (STATUS_WAITING_APPROVAL,), key="approval", ascii="[A]"),
    LegendEntry("🔴", "Needs input", "its turn is over and it waits for you",
                (STATUS_WAITING_USER,), key="input", ascii="[W]"),
    LegendEntry("⚪", "At rest", "a child agent that stopped, reported or not",
                (STATUS_DONE, STATUS_WAITING_OVERSIGHT), key="rest", ascii="[.]"),
    LegendEntry("❔", "Unknown", "starting up, or its status cannot be read",
                (STATUS_UNKNOWN,), key="unknown", ascii="[?]"),
    LegendEntry("⚫", "Gone", "the agent exited or its window is gone",
                (STATUS_TERMINATED,), key="gone", ascii="[X]"),
    LegendEntry("💚", "Working (heartbeat)", "a heartbeat started this turn",
                (STATUS_RUNNING_HEARTBEAT, STATUS_HEARTBEAT_START), variant=True,
                key="heartbeat", ascii="[H]"),
    LegendEntry("💛", "Waiting on a heartbeat", "a heartbeat will re-prompt it",
                (STATUS_WAITING_HEARTBEAT,), variant=True, key="heartbeat_waiting", ascii="[h]"),
    LegendEntry("🟣", "Error", "an API or hook error needs attention",
                (STATUS_ERROR,), variant=True, key="error", ascii="[E]"),
    LegendEntry("💤", "Asleep", "paused by you, left out of stats",
                (STATUS_ASLEEP,), variant=True, key="asleep", ascii="[Z]"),
)


# A child's task outcome (the ``l`` result mode), separate from the light.
# Fields: (emoji, ascii_fallback, style, meaning)
RESULT_UNREPORTED = "unreported"
RESULT_SUCCESS = "success"
RESULT_FAILURE = "failure"
RESULT_TIMEOUT = "timeout"

RESULT_KINDS: dict[str, Tuple[str, str, str, str]] = {
    RESULT_UNREPORTED: ("📭", "Un", "yellow", "stopped without a report"),
    RESULT_SUCCESS:    ("✅", "ok", "green", "reported success"),
    RESULT_FAILURE:    ("❌", "XX", "red", "reported failure"),
    RESULT_TIMEOUT:    ("⌛", "TO", "red", "oversight timeout expired"),
}


@dataclass
class StatusBadge:
    """A single detail badge for column 2.

    `kind` is a key in BADGE_KINDS. `label` is an optional human-readable
    suffix (e.g. tool name for `tool`, "in 4m" for `schedule_wakeup`). `count`
    lets us stack identical kinds compactly: monitor×2 instead of two badges.
    `eta_seconds` is the seconds-until-fire for wake-time-aware kinds;
    `eta_at`, when known, is the same moment as epoch seconds. It holds
    still between ticks, so it is what the engine publishes.
    """

    kind: str
    label: Optional[str] = None
    count: int = 1
    eta_seconds: Optional[float] = None
    eta_at: Optional[float] = None


@dataclass
class StatusDetail:
    """Structured status produced by the hook reducer.

    Consumed by the ⏰ column and (later) any UI surface that wants a
    color-coded view. `legacy_status` is the existing enum value
    (STATUS_RUNNING/STATUS_WAITING_USER/...) returned for backward
    compatibility.
    """

    color: str                                  # STATUS_COLOR_*
    badges: list[StatusBadge] = field(default_factory=list)
    legacy_status: str = STATUS_RUNNING         # what detect_status returns today

    def emoji(self, badge: StatusBadge, emoji_free: bool = False) -> str:
        emoji, _, ascii_fb = BADGE_KINDS.get(badge.kind, ("?", "", "?"))
        return ascii_fb if emoji_free else emoji
