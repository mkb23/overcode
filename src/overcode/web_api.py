"""
API data handlers for web server.

Reuses existing helpers from tui_helpers.py and reads from Monitor Daemon state.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .monitor_daemon_state import (
    get_monitor_daemon_state,
    MonitorDaemonState,
    SessionDaemonState,
)
from .status_history import read_agent_status_history
from .tui_helpers import (
    format_duration,
    format_tokens,
    build_timeline_slots,
    calculate_uptime,
    get_git_diff_stats,
)
from .status_constants import (
    get_status_emoji,
    get_status_color,
    AGENT_TIMELINE_CHARS,
    PRESENCE_TIMELINE_CHARS,
)


# CSS color values for web (Rich/Textual colors -> CSS hex)
WEB_COLORS = {
    "green": "#22c55e",
    "yellow": "#eab308",
    "orange1": "#f97316",
    "red": "#ef4444",
    "dim": "#6b7280",
    "cyan": "#06b6d4",
}


def get_web_color(status_color: str) -> str:
    """Convert Rich color name to CSS hex color."""
    return WEB_COLORS.get(status_color, "#6b7280")


def get_status_data(tmux_session: str) -> Dict[str, Any]:
    """Get current status data for all agents.

    Args:
        tmux_session: tmux session name to monitor

    Returns:
        Dictionary with daemon info, summary, and per-agent data
    """
    state = get_monitor_daemon_state(tmux_session)
    now = datetime.now()

    result = {
        "timestamp": now.isoformat(),
        "daemon": _build_daemon_info(state),
        "presence": _build_presence_info(state),
        "summary": _build_summary(state),
        "agents": [],
    }

    if state:
        for s in state.sessions:
            result["agents"].append(_build_agent_info(s, now))

    return result


def _build_daemon_info(state: Optional[MonitorDaemonState]) -> Dict[str, Any]:
    """Build daemon status information."""
    if state is None:
        return {
            "running": False,
            "status": "stopped",
            "loop_count": 0,
            "interval": 0,
            "last_loop": None,
            "supervisor_claude_running": False,
        }

    running = not state.is_stale()

    return {
        "running": running,
        "status": state.status if running else "stopped",
        "loop_count": state.loop_count,
        "interval": state.current_interval,
        "last_loop": state.last_loop_time,
        "supervisor_claude_running": state.supervisor_claude_running,
        "summarizer_enabled": state.summarizer_enabled,
        "summarizer_available": state.summarizer_available,
        "summarizer_calls": state.summarizer_calls,
        "summarizer_cost_usd": state.summarizer_cost_usd,
    }


def _build_presence_info(state: Optional[MonitorDaemonState]) -> Dict[str, Any]:
    """Build presence information."""
    if not state or not state.presence_available:
        return {"available": False}

    state_names = {1: "locked", 2: "inactive", 3: "active"}
    return {
        "available": True,
        "state": state.presence_state,
        "state_name": state_names.get(state.presence_state, "unknown"),
        "idle_seconds": state.presence_idle_seconds or 0,
    }


def _build_summary(state: Optional[MonitorDaemonState]) -> Dict[str, Any]:
    """Build summary statistics."""
    if not state:
        return {
            "total_agents": 0,
            "green_agents": 0,
            "total_green_time": 0,
            "total_non_green_time": 0,
        }

    return {
        "total_agents": len(state.sessions),
        "green_agents": state.green_sessions,
        "total_green_time": state.total_green_time,
        "total_non_green_time": state.total_non_green_time,
    }


def _build_agent_info(s: SessionDaemonState, now: datetime) -> Dict[str, Any]:
    """Build agent info dict from SessionDaemonState."""
    # Calculate time in current state
    time_in_state = 0.0
    if s.status_since:
        try:
            state_start = datetime.fromisoformat(s.status_since)
            time_in_state = (now - state_start).total_seconds()
        except ValueError:
            pass

    # Calculate current green/non-green time including elapsed
    green_time = s.green_time_seconds
    non_green_time = s.non_green_time_seconds

    if s.current_status == "running":
        green_time += time_in_state
    elif s.current_status != "terminated":
        non_green_time += time_in_state

    total_time = green_time + non_green_time
    percent_active = (green_time / total_time * 100) if total_time > 0 else 0

    # Calculate human interactions (total - robot)
    human_interactions = max(0, s.interaction_count - s.steers_count)

    status_color = get_status_color(s.current_status)

    # Calculate uptime from start_time
    uptime = calculate_uptime(s.start_time, now) if s.start_time else "-"

    # Get git diff stats if start_directory available
    git_diff = None
    if s.start_directory:
        git_diff = get_git_diff_stats(s.start_directory)

    # Permission mode emoji (matching TUI)
    perm_emoji = "👮"  # normal
    if s.permissiveness_mode == "bypass":
        perm_emoji = "🔥"
    elif s.permissiveness_mode == "permissive":
        perm_emoji = "🏃"

    return {
        "name": s.name,
        "status": s.current_status,
        "status_emoji": get_status_emoji(s.current_status),
        "status_color": status_color,
        "status_color_hex": get_web_color(status_color),
        "activity": s.current_activity[:100] if s.current_activity else "",
        "repo": s.repo_name or "",
        "branch": s.branch or "",
        "green_time": format_duration(green_time),
        "green_time_raw": green_time,
        "non_green_time": format_duration(non_green_time),
        "non_green_time_raw": non_green_time,
        "percent_active": round(percent_active),
        "human_interactions": human_interactions,
        "robot_steers": s.steers_count,
        "tokens": format_tokens(s.input_tokens + s.output_tokens),
        "tokens_raw": s.input_tokens + s.output_tokens,
        "cost_usd": round(s.estimated_cost_usd, 2),
        "standing_orders": bool(s.standing_instructions),
        "standing_orders_complete": s.standing_orders_complete,
        "time_in_state": format_duration(time_in_state),
        "time_in_state_raw": time_in_state,
        "median_work_time": format_duration(s.median_work_time) if s.median_work_time > 0 else "-",
        # New fields for TUI parity
        "uptime": uptime,
        "permissiveness_mode": s.permissiveness_mode,
        "perm_emoji": perm_emoji,
        "git_diff_files": git_diff[0] if git_diff else 0,
        "git_diff_insertions": git_diff[1] if git_diff else 0,
        "git_diff_deletions": git_diff[2] if git_diff else 0,
        # Activity summary (if summarizer enabled)
        "activity_summary": s.activity_summary or "",
        "activity_summary_updated": s.activity_summary_updated,
    }


def get_timeline_data(tmux_session: str, hours: float = 3.0, slots: int = 60) -> Dict[str, Any]:
    """Get timeline history data.

    Args:
        tmux_session: tmux session name
        hours: How many hours of history (default 3)
        slots: Number of time slots for the timeline (default 60)

    Returns:
        Dictionary with timeline slot data per agent
    """
    now = datetime.now()

    result: Dict[str, Any] = {
        "hours": hours,
        "slot_count": slots,
        "agents": {},
        "status_chars": AGENT_TIMELINE_CHARS,
        "status_colors": {k: get_web_color(get_status_color(k)) for k in AGENT_TIMELINE_CHARS},
    }

    # Get agent history
    all_history = read_agent_status_history(hours=hours)

    # Group by agent
    agent_histories: Dict[str, List] = {}
    for ts, agent, status, activity in all_history:
        if agent not in agent_histories:
            agent_histories[agent] = []
        agent_histories[agent].append((ts, status))

    # Build timeline for each agent
    for agent_name, history in agent_histories.items():
        slot_states = build_timeline_slots(history, slots, hours, now)

        # Count green slots
        green_slots = sum(1 for s in slot_states.values() if s == "running")
        total_slots = len(slot_states) if slot_states else 1
        percent_green = (green_slots / total_slots * 100) if total_slots > 0 else 0

        # Build slot list with status and color
        slot_list = []
        for i in range(slots):
            if i in slot_states:
                status = slot_states[i]
                slot_list.append({
                    "index": i,
                    "status": status,
                    "char": AGENT_TIMELINE_CHARS.get(status, "─"),
                    "color": get_web_color(get_status_color(status)),
                })

        result["agents"][agent_name] = {
            "slots": slot_list,
            "percent_green": round(percent_green),
        }

    return result


def get_health_data() -> Dict[str, Any]:
    """Get health check data."""
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
    }
