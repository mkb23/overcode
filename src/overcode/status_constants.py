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
STATUS_BUSY_SLEEPING = "busy_sleeping"  # Agent running but executing a sleep command (#289)

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


# =============================================================================
# Status to Emoji Mappings
# =============================================================================

STATUS_EMOJIS = {
    STATUS_RUNNING: "🟢",
    STATUS_WAITING_USER: "🔴",
    STATUS_TERMINATED: "⚫",  # Black circle - Claude exited
    STATUS_ASLEEP: "💤",  # Sleeping/snoozed - human marked as paused
    STATUS_RUNNING_HEARTBEAT: "💚",  # Green heart for heartbeat-triggered (#171)
    STATUS_WAITING_APPROVAL: "🟠",  # Orange for approval waiting (#22)
    STATUS_WAITING_HEARTBEAT: "💛",  # Yellow heart - waiting but heartbeat will auto-resume
    STATUS_ERROR: "🟣",  # Purple for errors (#22)
    STATUS_HEARTBEAT_START: "💚",  # Heartbeat commencement marker (timeline only)
    STATUS_DONE: "☑️",  # Child agent completed delegated work (#244)
    STATUS_WAITING_OVERSIGHT: "👁️",  # Waiting for oversight report
    STATUS_BUSY_SLEEPING: "🟡",  # Running but sleeping (#289)
}


def get_status_emoji(status: str) -> str:
    """Get emoji for an agent status."""
    return STATUS_EMOJIS.get(status, "⚪")


# =============================================================================
# Status to Color Mappings (for Rich/Textual styling)
# =============================================================================

STATUS_COLORS = {
    STATUS_RUNNING: "green",
    STATUS_WAITING_USER: "red",
    STATUS_TERMINATED: "dim",  # Grey for terminated
    STATUS_ASLEEP: "dim",  # Grey for sleeping
    STATUS_RUNNING_HEARTBEAT: "green",  # Green for heartbeat-triggered (#171)
    STATUS_WAITING_APPROVAL: "orange1",  # Orange for approval waiting (#22)
    STATUS_WAITING_HEARTBEAT: "yellow",  # Yellow - waiting but heartbeat will auto-resume
    STATUS_ERROR: "magenta",  # Purple for errors (#22)
    STATUS_HEARTBEAT_START: "green",  # Heartbeat commencement (timeline only)
    STATUS_DONE: "dim",  # Done child agent (#244)
    STATUS_WAITING_OVERSIGHT: "yellow",  # Waiting for oversight report
    STATUS_BUSY_SLEEPING: "yellow",  # Running but sleeping (#289)
}


def get_status_color(status: str) -> str:
    """Get color name for an agent status."""
    return STATUS_COLORS.get(status, "dim")


# =============================================================================
# Status to Symbol+Color (combined for display)
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
    STATUS_BUSY_SLEEPING: ("🟡", "yellow"),  # Running but sleeping (#289)
}


def get_status_symbol(status: str) -> Tuple[str, str]:
    """Get (emoji, color) tuple for an agent status."""
    return STATUS_SYMBOLS.get(status, ("⚪", "dim"))


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


def is_green_status(status: str) -> bool:
    """Check if a status is considered 'green' (actively working)."""
    return status in (STATUS_RUNNING, STATUS_RUNNING_HEARTBEAT, STATUS_HEARTBEAT_START)


def is_waiting_status(status: str) -> bool:
    """Check if a status is a waiting state."""
    return status in (STATUS_WAITING_USER, STATUS_WAITING_HEARTBEAT)


def is_user_blocked(status: str) -> bool:
    """Check if status indicates user intervention is required."""
    return status == STATUS_WAITING_USER


def is_asleep(status: str) -> bool:
    """Check if status indicates agent is asleep (paused by human)."""
    return status == STATUS_ASLEEP


def is_busy_sleeping(status: str) -> bool:
    """Check if status indicates agent is sleeping via a bash sleep command (#289)."""
    return status == STATUS_BUSY_SLEEPING


def is_done(status: str) -> bool:
    """Check if status indicates child agent completed its work (#244)."""
    return status == STATUS_DONE


def is_waiting_oversight(status: str) -> bool:
    """Check if status indicates child is waiting for oversight report."""
    return status == STATUS_WAITING_OVERSIGHT
