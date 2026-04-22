"""
Status constants and mappings for Overcode.

Centralizes all status-related constants, colors, emojis, and display
mappings used throughout the application.
"""

from typing import Tuple


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
STATUS_TERMINATED = "terminated"  # Claude Code exited, shell prompt showing
STATUS_ASLEEP = "asleep"  # Human marked agent as paused/snoozed (excluded from stats)
STATUS_RUNNING_HEARTBEAT = "running_heartbeat"  # Running from automated heartbeat (#171)
STATUS_WAITING_APPROVAL = "waiting_approval"  # Waiting on approval/plan/decision (#22)
STATUS_WAITING_HEARTBEAT = "waiting_heartbeat"  # Waiting but heartbeat will auto-resume
STATUS_ERROR = "error"  # API timeout, etc. (#22)
STATUS_HEARTBEAT_START = "heartbeat_start"  # First observation of heartbeat-triggered run (timeline only)
STATUS_DONE = "done"  # Child agent completed its delegated work (#244)
STATUS_WAITING_OVERSIGHT = "waiting_oversight"  # Child stopped, awaiting oversight report
STATUS_BUSY_SLEEPING = "busy_sleeping"  # Agent running but executing a sleep command (#289) or waiting on a live Monitor stream (#441)

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
]


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
# Status to Symbol+Color (single source of truth for emoji + color)
# =============================================================================

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
    STATUS_DONE: ("☑️", "dim"),  # Done child agent (#244)
    STATUS_WAITING_OVERSIGHT: ("👁️", "yellow"),  # Waiting for oversight report
    STATUS_BUSY_SLEEPING: ("🟡", "yellow"),  # Running but sleeping (#289) or watching a live Monitor (#441)
}


# =============================================================================
# Derived Emoji and Color Mappings
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
    "☑️": "[D]",
    "👁️": "[O]",
    "🟡": "[S]",
    "⚪": "[?]",
    # Tool indicators
    "🖥️": "Sh",
    "📖": "Rd",
    "✏️": "Wr",
    "🔧": "Ed",
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
    # Activity/metrics
    "🔔": "(!)",
    "⏰": "AL",
    "📚": "CW",
    "💓": "<3",
    "💰": "$$",
    "⏳": "~~",
    "🤖": "Ro",
    "👤": "Hu",
    "🤿": "Su",
    "🐚": "Bg",
    "👶": "Ch",
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
}


def emoji_or_ascii(char: str, emoji_free: bool) -> str:
    """Return ASCII fallback if emoji_free mode is active, else the emoji."""
    if emoji_free:
        return EMOJI_ASCII.get(char, char)
    return char


# Permissiveness mode → emoji mapping (shared by TUI, CLI, and web API)
PERMISSIVENESS_EMOJIS = {
    "bypass": "🔥",
    "permissive": "🏃",
    "normal": "👮",
}


def get_permissiveness_emoji(mode: str, emoji_free: bool = False) -> str:
    """Get the emoji for a permissiveness mode."""
    return emoji_or_ascii(PERMISSIVENESS_EMOJIS.get(mode, "👮"), emoji_free)


def get_status_emoji(status: str, emoji_free: bool = False) -> str:
    """Get emoji for an agent status."""
    e = STATUS_EMOJIS.get(status, "⚪")
    return emoji_or_ascii(e, emoji_free)


def get_status_color(status: str) -> str:
    """Get color name for an agent status."""
    return STATUS_COLORS.get(status, "dim")


def get_status_symbol(status: str, emoji_free: bool = False) -> Tuple[str, str]:
    """Get (emoji, color) tuple for an agent status."""
    symbol, color = STATUS_SYMBOLS.get(status, ("⚪", "dim"))
    return (emoji_or_ascii(symbol, emoji_free), color)


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
    STATUS_DONE: "✓",  # Done child agent (#244)
    STATUS_WAITING_OVERSIGHT: "▒",  # Waiting for oversight report
    STATUS_BUSY_SLEEPING: "█",  # Solid - doesn't need human input (#289)
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


GREEN_STATUSES = frozenset({STATUS_RUNNING, STATUS_RUNNING_HEARTBEAT, STATUS_HEARTBEAT_START})
WAITING_STATUSES = frozenset({STATUS_WAITING_USER, STATUS_WAITING_HEARTBEAT})
USER_BLOCKED_STATUSES = frozenset({STATUS_WAITING_USER})
ASLEEP_STATUSES = frozenset({STATUS_ASLEEP})
BUSY_SLEEPING_STATUSES = frozenset({STATUS_BUSY_SLEEPING})
DONE_STATUSES = frozenset({STATUS_DONE})
WAITING_OVERSIGHT_STATUSES = frozenset({STATUS_WAITING_OVERSIGHT})


def is_green_status(status: str) -> bool:
    """Check if a status is considered 'green' (actively working)."""
    return status in GREEN_STATUSES


def is_waiting_status(status: str) -> bool:
    """Check if a status is a waiting state."""
    return status in WAITING_STATUSES


def is_user_blocked(status: str) -> bool:
    """Check if status indicates user intervention is required."""
    return status in USER_BLOCKED_STATUSES


def is_asleep(status: str) -> bool:
    """Check if status indicates agent is asleep (paused by human)."""
    return status in ASLEEP_STATUSES


def is_busy_sleeping(status: str) -> bool:
    """Check if status indicates agent is sleeping via a bash sleep command (#289)."""
    return status in BUSY_SLEEPING_STATUSES


def is_done(status: str) -> bool:
    """Check if status indicates child agent completed its work (#244)."""
    return status in DONE_STATUSES


def is_waiting_oversight(status: str) -> bool:
    """Check if status indicates child is waiting for oversight report."""
    return status in WAITING_OVERSIGHT_STATUSES
