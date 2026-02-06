"""
Status constants and mappings for Overcode.

Centralizes all status-related constants, colors, emojis, and display
mappings used throughout the application.
"""

from typing import Tuple


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

PRESENCE_LOCKED = 1
PRESENCE_INACTIVE = 2
PRESENCE_ACTIVE = 3


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
}


def get_agent_timeline_char(status: str) -> str:
    """Get timeline character for an agent status."""
    return AGENT_TIMELINE_CHARS.get(status, "─")


PRESENCE_TIMELINE_CHARS = {
    PRESENCE_LOCKED: "░",
    PRESENCE_INACTIVE: "▒",
    PRESENCE_ACTIVE: "█",
}


def get_presence_timeline_char(state: int) -> str:
    """Get timeline character for a presence state."""
    return PRESENCE_TIMELINE_CHARS.get(state, "─")


# =============================================================================
# Presence State Colors
# =============================================================================

PRESENCE_COLORS = {
    PRESENCE_LOCKED: "red",
    PRESENCE_INACTIVE: "yellow",
    PRESENCE_ACTIVE: "green",
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
    return status in (STATUS_RUNNING, STATUS_RUNNING_HEARTBEAT)


def is_waiting_status(status: str) -> bool:
    """Check if a status is a waiting state."""
    return status in (STATUS_WAITING_USER, STATUS_WAITING_HEARTBEAT)


def is_user_blocked(status: str) -> bool:
    """Check if status indicates user intervention is required."""
    return status == STATUS_WAITING_USER


def is_asleep(status: str) -> bool:
    """Check if status indicates agent is asleep (paused by human)."""
    return status == STATUS_ASLEEP
