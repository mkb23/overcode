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
from .settings import get_agent_history_path
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

    # Get agent history from session-specific file
    history_path = get_agent_history_path(tmux_session)
    all_history = read_agent_status_history(hours=hours, history_file=history_path)

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


# =============================================================================
# Analytics API Endpoints (for `overcode web` historical analytics dashboard)
# =============================================================================


def get_analytics_sessions(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Get all sessions (active + archived) within a time range.

    Args:
        start: Filter sessions that started after this time
        end: Filter sessions that started before this time

    Returns:
        Dictionary with sessions list and summary stats
    """
    from .session_manager import SessionManager
    from .history_reader import get_session_stats

    sessions_mgr = SessionManager()
    all_sessions = []

    # Get active sessions
    for s in sessions_mgr.list_sessions():
        record = _session_to_analytics_record(s, is_archived=False)
        # Get detailed stats from Claude Code history
        stats = get_session_stats(s)
        if stats:
            record["work_times"] = stats.work_times
            record["median_work_time"] = stats.median_work_time
        all_sessions.append(record)

    # Get archived sessions
    for s in sessions_mgr.list_archived_sessions():
        record = _session_to_analytics_record(s, is_archived=True)
        record["end_time"] = getattr(s, "_end_time", None)
        all_sessions.append(record)

    # Filter by time range
    if start or end:
        filtered = []
        for s in all_sessions:
            try:
                session_start = datetime.fromisoformat(s["start_time"])
                if start and session_start < start:
                    continue
                if end and session_start > end:
                    continue
                filtered.append(s)
            except (ValueError, TypeError):
                continue
        all_sessions = filtered

    # Sort by start_time descending (newest first)
    all_sessions.sort(key=lambda x: x.get("start_time", ""), reverse=True)

    # Calculate summary stats
    total_tokens = sum(s.get("total_tokens", 0) for s in all_sessions)
    total_cost = sum(s.get("estimated_cost_usd", 0) for s in all_sessions)
    total_green_time = sum(s.get("green_time_seconds", 0) for s in all_sessions)
    total_non_green_time = sum(s.get("non_green_time_seconds", 0) for s in all_sessions)
    total_time = total_green_time + total_non_green_time
    avg_green_pct = (total_green_time / total_time * 100) if total_time > 0 else 0

    return {
        "sessions": all_sessions,
        "summary": {
            "session_count": len(all_sessions),
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 2),
            "total_green_time_seconds": total_green_time,
            "total_non_green_time_seconds": total_non_green_time,
            "avg_green_percent": round(avg_green_pct, 1),
        },
    }


def _session_to_analytics_record(session, is_archived: bool) -> Dict[str, Any]:
    """Convert a Session to a analytics record dictionary."""
    stats = session.stats
    green_time = stats.green_time_seconds
    non_green_time = stats.non_green_time_seconds
    total_time = green_time + non_green_time
    green_pct = (green_time / total_time * 100) if total_time > 0 else 0

    return {
        "id": session.id,
        "name": session.name,
        "start_time": session.start_time,
        "end_time": None,
        "repo_name": session.repo_name,
        "branch": session.branch,
        "is_archived": is_archived,
        "interaction_count": stats.interaction_count,
        "steers_count": stats.steers_count,
        "total_tokens": stats.total_tokens,
        "input_tokens": stats.input_tokens,
        "output_tokens": stats.output_tokens,
        "cache_creation_tokens": stats.cache_creation_tokens,
        "cache_read_tokens": stats.cache_read_tokens,
        "estimated_cost_usd": round(stats.estimated_cost_usd, 4),
        "green_time_seconds": green_time,
        "non_green_time_seconds": non_green_time,
        "green_percent": round(green_pct, 1),
        "work_times": [],  # Will be populated if available
        "median_work_time": 0.0,
    }


def get_analytics_timeline(
    tmux_session: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Get agent status timeline within a time range.

    Args:
        tmux_session: tmux session name
        start: Start of time range
        end: End of time range

    Returns:
        Dictionary with timeline events grouped by agent
    """
    from .presence_logger import read_presence_history

    # Default to last 24 hours if no range specified
    if start is None:
        start = datetime.now() - timedelta(hours=24)
    if end is None:
        end = datetime.now()

    hours = (end - start).total_seconds() / 3600.0

    # Get agent status history from session-specific file
    history_path = get_agent_history_path(tmux_session)
    all_history = read_agent_status_history(hours=hours, history_file=history_path)

    # Filter to time range and group by agent
    agent_events: Dict[str, List[Dict[str, Any]]] = {}
    for ts, agent_name, status, activity in all_history:
        if ts < start or ts > end:
            continue

        if agent_name not in agent_events:
            agent_events[agent_name] = []

        agent_events[agent_name].append({
            "timestamp": ts.isoformat(),
            "status": status,
            "activity": activity[:100] if activity else "",
            "color": get_web_color(get_status_color(status)),
        })

    # Get presence history
    presence_history = read_presence_history(hours=hours)
    presence_events = []
    state_names = {1: "locked", 2: "inactive", 3: "active"}
    presence_colors = {1: "#6b7280", 2: "#eab308", 3: "#22c55e"}

    for ts, state in presence_history:
        if ts < start or ts > end:
            continue
        presence_events.append({
            "timestamp": ts.isoformat(),
            "state": state,
            "state_name": state_names.get(state, "unknown"),
            "color": presence_colors.get(state, "#6b7280"),
        })

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "agents": agent_events,
        "presence": presence_events,
        "status_colors": {k: get_web_color(get_status_color(k)) for k in AGENT_TIMELINE_CHARS},
    }


def get_analytics_stats(
    tmux_session: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Get aggregate statistics for a time range.

    Args:
        tmux_session: tmux session name
        start: Start of time range
        end: End of time range

    Returns:
        Dictionary with aggregate efficiency metrics
    """
    # Get sessions in range
    sessions_data = get_analytics_sessions(start, end)
    sessions = sessions_data["sessions"]
    summary = sessions_data["summary"]

    # Calculate efficiency metrics
    total_interactions = sum(s.get("interaction_count", 0) for s in sessions)
    total_steers = sum(s.get("steers_count", 0) for s in sessions)
    total_cost = summary["total_cost_usd"]

    # Cost efficiency
    cost_per_interaction = (total_cost / total_interactions) if total_interactions > 0 else 0
    total_hours = (summary["total_green_time_seconds"] + summary["total_non_green_time_seconds"]) / 3600
    cost_per_hour = (total_cost / total_hours) if total_hours > 0 else 0

    # Spin rate (steers / interactions)
    spin_rate = (total_steers / total_interactions * 100) if total_interactions > 0 else 0

    # Work time percentiles
    all_work_times = []
    for s in sessions:
        work_times = s.get("work_times", [])
        if work_times:
            all_work_times.extend(work_times)

    work_time_stats = _calculate_percentiles(all_work_times)

    # Calculate presence-based efficiency metrics
    presence_efficiency = _calculate_presence_efficiency(tmux_session, start, end)

    return {
        "time_range": {
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
        },
        "summary": summary,
        "efficiency": {
            "green_percent": summary["avg_green_percent"],
            "cost_per_interaction": round(cost_per_interaction, 4),
            "cost_per_hour": round(cost_per_hour, 2),
            "spin_rate_percent": round(spin_rate, 1),
        },
        "presence_efficiency": presence_efficiency,
        "interactions": {
            "total": total_interactions,
            "human": total_interactions - total_steers,
            "robot_steers": total_steers,
        },
        "work_times": work_time_stats,
    }


def _calculate_percentiles(values: List[float]) -> Dict[str, float]:
    """Calculate work time percentiles."""
    if not values:
        return {
            "mean": 0.0,
            "median": 0.0,
            "p5": 0.0,
            "p95": 0.0,
            "min": 0.0,
            "max": 0.0,
        }

    sorted_vals = sorted(values)
    n = len(sorted_vals)

    def percentile(p: float) -> float:
        idx = int(p * (n - 1))
        return sorted_vals[idx]

    return {
        "mean": round(sum(values) / n, 1),
        "median": round(percentile(0.5), 1),
        "p5": round(percentile(0.05), 1),
        "p95": round(percentile(0.95), 1),
        "min": round(sorted_vals[0], 1),
        "max": round(sorted_vals[-1], 1),
    }


def _calculate_presence_efficiency(
    tmux_session: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    sample_interval_seconds: int = 60,
) -> Dict[str, Any]:
    """Calculate agent efficiency metrics segmented by user presence.

    Samples agent status at regular intervals and calculates what percentage
    of agents were "green" (running) during:
    - Present periods: user presence state = 3 (active)
    - AFK periods: user presence state = 1 (locked) or 2 (inactive)

    Args:
        tmux_session: tmux session name
        start: Start of time range
        end: End of time range
        sample_interval_seconds: How often to sample (default 60s)

    Returns:
        Dictionary with presence and AFK efficiency metrics
    """
    from .presence_logger import read_presence_history

    # Default to last 24 hours if no range specified
    if end is None:
        end = datetime.now()
    if start is None:
        start = end - timedelta(hours=24)

    hours = (end - start).total_seconds() / 3600.0

    # Get agent status history from session-specific file
    history_path = get_agent_history_path(tmux_session)
    agent_history = read_agent_status_history(hours=hours, history_file=history_path)

    # Get presence history: list of (timestamp, state)
    presence_history = read_presence_history(hours=hours)

    # Filter to time range
    agent_history = [(ts, name, status, act) for ts, name, status, act in agent_history
                     if start <= ts <= end]
    presence_history = [(ts, state) for ts, state in presence_history
                        if start <= ts <= end]

    # If no data, return zeros
    if not agent_history or not presence_history:
        return {
            "present_efficiency": 0.0,
            "afk_efficiency": 0.0,
            "present_samples": 0,
            "afk_samples": 0,
            "has_data": False,
        }

    # Sort histories by timestamp
    agent_history.sort(key=lambda x: x[0])
    presence_history.sort(key=lambda x: x[0])

    # Get unique agent names
    agent_names = sorted(set(name for _, name, _, _ in agent_history))

    # Build lookup: for each agent, sorted list of (timestamp, status)
    agent_status_timeline: Dict[str, List[tuple]] = {name: [] for name in agent_names}
    for ts, name, status, _ in agent_history:
        agent_status_timeline[name].append((ts, status))

    # Sample at regular intervals
    present_green_percents: List[float] = []
    afk_green_percents: List[float] = []

    current_time = start
    while current_time <= end:
        # Find user presence state at this time (most recent entry before current_time)
        user_state = None
        for ts, state in reversed(presence_history):
            if ts <= current_time:
                user_state = state
                break

        # If no presence data before this time, skip
        if user_state is None:
            current_time += timedelta(seconds=sample_interval_seconds)
            continue

        # Find agent statuses at this time
        green_count = 0
        total_agents = 0
        for name in agent_names:
            timeline = agent_status_timeline[name]
            agent_status = None
            for ts, status in reversed(timeline):
                if ts <= current_time:
                    agent_status = status
                    break

            if agent_status is not None:
                total_agents += 1
                if agent_status == "running":
                    green_count += 1

        # Calculate green percentage for this sample
        if total_agents > 0:
            green_percent = (green_count / total_agents) * 100

            # Bucket by presence state
            if user_state == 3:  # Active/present
                present_green_percents.append(green_percent)
            else:  # state 1 (locked) or 2 (inactive) = AFK
                afk_green_percents.append(green_percent)

        current_time += timedelta(seconds=sample_interval_seconds)

    # Calculate averages
    present_efficiency = (
        sum(present_green_percents) / len(present_green_percents)
        if present_green_percents else 0.0
    )
    afk_efficiency = (
        sum(afk_green_percents) / len(afk_green_percents)
        if afk_green_percents else 0.0
    )

    return {
        "present_efficiency": round(present_efficiency, 1),
        "afk_efficiency": round(afk_efficiency, 1),
        "present_samples": len(present_green_percents),
        "afk_samples": len(afk_green_percents),
        "has_data": len(present_green_percents) + len(afk_green_percents) > 0,
    }


def get_analytics_daily(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Get daily aggregated stats for charting.

    Args:
        start: Start of time range
        end: End of time range

    Returns:
        Dictionary with daily stats arrays
    """
    # Get sessions in range
    sessions_data = get_analytics_sessions(start, end)
    sessions = sessions_data["sessions"]

    # Group sessions by date
    daily_stats: Dict[str, Dict[str, Any]] = {}

    for s in sessions:
        try:
            session_start = datetime.fromisoformat(s["start_time"])
            date_key = session_start.strftime("%Y-%m-%d")

            if date_key not in daily_stats:
                daily_stats[date_key] = {
                    "date": date_key,
                    "sessions": 0,
                    "tokens": 0,
                    "cost_usd": 0.0,
                    "green_time_seconds": 0.0,
                    "non_green_time_seconds": 0.0,
                    "interactions": 0,
                    "steers": 0,
                }

            daily_stats[date_key]["sessions"] += 1
            daily_stats[date_key]["tokens"] += s.get("total_tokens", 0)
            daily_stats[date_key]["cost_usd"] += s.get("estimated_cost_usd", 0)
            daily_stats[date_key]["green_time_seconds"] += s.get("green_time_seconds", 0)
            daily_stats[date_key]["non_green_time_seconds"] += s.get("non_green_time_seconds", 0)
            daily_stats[date_key]["interactions"] += s.get("interaction_count", 0)
            daily_stats[date_key]["steers"] += s.get("steers_count", 0)
        except (ValueError, TypeError):
            continue

    # Sort by date and convert to list
    sorted_dates = sorted(daily_stats.keys())
    daily_list = []

    for date_key in sorted_dates:
        day = daily_stats[date_key]
        total_time = day["green_time_seconds"] + day["non_green_time_seconds"]
        day["green_percent"] = round(
            (day["green_time_seconds"] / total_time * 100) if total_time > 0 else 0, 1
        )
        day["cost_usd"] = round(day["cost_usd"], 2)
        daily_list.append(day)

    return {
        "days": daily_list,
        "labels": sorted_dates,
    }


def get_time_presets() -> List[Dict[str, str]]:
    """Get configured time presets from config or defaults."""
    from .config import get_web_time_presets

    return get_web_time_presets()
