"""
Time context generation for Claude Code hook integration.

Outputs a compact time-awareness line injected into every prompt via
a UserPromptSubmit hook, giving Claude continuous temporal context.

Example output:
    Clock: 14:32 PST | User: active | Office: yes | Uptime: 1h23m | Heartbeat: 15m (next: 7m)

All functions are pure (no Rich/Typer dependencies) for easy testing.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple


def get_agent_identity() -> Tuple[Optional[str], Optional[str]]:
    """Get session name and tmux session from environment variables.

    Returns:
        (session_name, tmux_session) tuple, either may be None
    """
    name = os.environ.get("OVERCODE_SESSION_NAME")
    tmux = os.environ.get("OVERCODE_TMUX_SESSION")
    return name, tmux


def format_clock(now: datetime) -> str:
    """Format current time as 'HH:MM TZ'.

    Args:
        now: Current datetime (should be timezone-aware)

    Returns:
        e.g. '14:32 PST'
    """
    tz_abbr = now.strftime("%Z") or "UTC"
    return f"{now.strftime('%H:%M')} {tz_abbr}"


def format_presence(presence_state: Optional[int]) -> str:
    """Format user presence state from monitor daemon.

    Args:
        presence_state: 1=locked/sleep, 2=inactive, 3=active, None=unknown

    Returns:
        One of: 'active', 'inactive', 'locked', 'unknown'
    """
    mapping = {1: "locked", 2: "inactive", 3: "active"}
    return mapping.get(presence_state, "unknown")


def format_office_hours(now: datetime, start: int, end: int) -> str:
    """Check if current hour is within office hours.

    Supports midnight wrap (e.g. start=22, end=6 means 22:00-06:00).

    Args:
        now: Current datetime
        start: Office start hour (0-23)
        end: Office end hour (0-23)

    Returns:
        'yes' or 'no'
    """
    hour = now.hour
    if start <= end:
        # Normal range (e.g. 9-17)
        return "yes" if start <= hour < end else "no"
    else:
        # Midnight wrap (e.g. 22-6)
        return "yes" if hour >= start or hour < end else "no"


def format_uptime(start_iso: Optional[str], now: datetime) -> Optional[str]:
    """Format session uptime as compact duration.

    Args:
        start_iso: ISO timestamp of session start, or None
        now: Current datetime

    Returns:
        e.g. '1h23m', '45m', '2h0m', or None if start_iso is None
    """
    if not start_iso:
        return None

    try:
        start = datetime.fromisoformat(start_iso)
        # Make both naive for comparison if needed
        if start.tzinfo and not now.tzinfo:
            start = start.replace(tzinfo=None)
        elif now.tzinfo and not start.tzinfo:
            start = start.replace(tzinfo=now.tzinfo)

        seconds = (now - start).total_seconds()
        if seconds < 0:
            return "0m"
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        if hours > 0:
            return f"{hours}h{minutes}m"
        return f"{minutes}m"
    except (ValueError, TypeError):
        return None


def format_heartbeat(
    interval_minutes: Optional[int],
    last_iso: Optional[str],
    now: datetime,
) -> Optional[str]:
    """Format heartbeat status.

    Args:
        interval_minutes: Heartbeat interval in minutes, or None if disabled
        last_iso: ISO timestamp of last heartbeat, or None
        now: Current datetime

    Returns:
        e.g. '15m (next: 7m)', '15m (next: now)', or None if disabled
    """
    if not interval_minutes:
        return None

    if not last_iso:
        return f"{interval_minutes}m (next: now)"

    try:
        last = datetime.fromisoformat(last_iso)
        if last.tzinfo and not now.tzinfo:
            last = last.replace(tzinfo=None)
        elif now.tzinfo and not last.tzinfo:
            last = last.replace(tzinfo=now.tzinfo)

        elapsed = (now - last).total_seconds()
        remaining = (interval_minutes * 60) - elapsed

        if remaining <= 0:
            return f"{interval_minutes}m (next: now)"

        remaining_min = int(remaining // 60)
        return f"{interval_minutes}m (next: {remaining_min}m)"
    except (ValueError, TypeError):
        return f"{interval_minutes}m (next: now)"


def read_heartbeat_timestamp(tmux_session: str, session_name: str) -> Optional[str]:
    """Read last heartbeat timestamp from file.

    Args:
        tmux_session: Tmux session name
        session_name: Agent session name

    Returns:
        ISO timestamp string, or None if file doesn't exist
    """
    path = (
        Path.home()
        / ".overcode"
        / "sessions"
        / tmux_session
        / f"heartbeat_{session_name}.last"
    )
    try:
        return path.read_text().strip()
    except (FileNotFoundError, IOError):
        return None


def build_time_context_line(
    clock: str,
    presence: str,
    office: str,
    uptime: Optional[str] = None,
    heartbeat: Optional[str] = None,
) -> str:
    """Assemble the final time context line.

    Omits fields that are None.

    Args:
        clock: Formatted clock string
        presence: Formatted presence string
        office: Formatted office hours string
        uptime: Formatted uptime string, or None to omit
        heartbeat: Formatted heartbeat string, or None to omit

    Returns:
        Single-line string like 'Clock: 14:32 PST | User: active | Office: yes | Uptime: 1h23m'
    """
    parts = [
        f"Clock: {clock}",
        f"User: {presence}",
        f"Office: {office}",
    ]
    if uptime is not None:
        parts.append(f"Uptime: {uptime}")
    if heartbeat is not None:
        parts.append(f"Heartbeat: {heartbeat}")
    return " | ".join(parts)


def _load_daemon_state(tmux_session: str) -> Optional[dict]:
    """Load monitor daemon state JSON.

    Args:
        tmux_session: Tmux session name

    Returns:
        Parsed dict, or None if unavailable
    """
    state_path = (
        Path.home()
        / ".overcode"
        / "sessions"
        / tmux_session
        / "monitor_daemon_state.json"
    )
    # Also respect OVERCODE_STATE_DIR for testing
    state_dir = os.environ.get("OVERCODE_STATE_DIR")
    if state_dir:
        state_path = Path(state_dir) / tmux_session / "monitor_daemon_state.json"

    try:
        with open(state_path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, IOError):
        return None


def _find_session_in_state(state: dict, session_name: str) -> Optional[dict]:
    """Find a session by name in daemon state.

    Args:
        state: Parsed daemon state dict
        session_name: Agent session name

    Returns:
        Session dict, or None
    """
    for s in state.get("sessions", []):
        if s.get("name") == session_name:
            return s
    return None


def generate_time_context(
    tmux_session: str,
    session_name: str,
    now: Optional[datetime] = None,
    config: Optional[dict] = None,
) -> str:
    """Orchestrator: reads state, config, calls formatters, returns final line.

    Args:
        tmux_session: Tmux session name
        session_name: Agent session name
        now: Current datetime (defaults to now with local timezone)
        config: Time context config dict (defaults to loading from config.yaml)

    Returns:
        Complete time context line
    """
    if now is None:
        now = datetime.now().astimezone()

    # Check time_context_enabled flag in daemon state before doing any work.
    # If the session exists in state and flag is False (or missing), suppress output.
    # If the session is not found or state is unavailable, allow output (graceful degradation).
    state = _load_daemon_state(tmux_session)
    if state:
        session_data = _find_session_in_state(state, session_name)
        if session_data and not session_data.get("time_context_enabled", False):
            return ""

    if config is None:
        from .config import get_time_context_config
        config = get_time_context_config()

    # Clock
    clock = format_clock(now)

    # Presence from daemon state (reuse already-loaded state)
    presence_state = None
    start_time = None

    if state:
        presence_state = state.get("presence_state")
        session_data = _find_session_in_state(state, session_name)
        if session_data:
            start_time = session_data.get("start_time")

    presence = format_presence(presence_state)

    # Office hours
    office_start = config.get("office_start", 9)
    office_end = config.get("office_end", 17)
    office = format_office_hours(now, office_start, office_end)

    # Uptime
    uptime = format_uptime(start_time, now)

    # Heartbeat
    interval = config.get("heartbeat_interval_minutes")
    last_heartbeat = read_heartbeat_timestamp(tmux_session, session_name)
    heartbeat = format_heartbeat(interval, last_heartbeat, now)

    line = build_time_context_line(clock, presence, office, uptime, heartbeat)
    line += "\nPrefix each response with [HH:MM] using the Clock value above."
    return line
