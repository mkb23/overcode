"""
API data handlers for the API server (the sister API).

Reuses existing helpers from tui_helpers.py and reads from Monitor Daemon state.
"""

import importlib.metadata
import logging
import subprocess
from datetime import datetime
from typing import Any, Dict, Optional

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
    calculate_uptime,
    get_git_diff_stats,
    get_git_untracked_count,
)
from .config import get_hostname
from .status_constants import (
    get_status_emoji,
    get_status_color,
    is_green_status,
)

logger = logging.getLogger(__name__)


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


def _get_version() -> str:
    """Get the installed overcode version with git info."""
    # Try pyproject.toml first (for editable installs), matching __init__.py logic
    try:
        from pathlib import Path
        import tomllib
        pkg_dir = Path(__file__).resolve().parent
        toml_path = pkg_dir.parent.parent / "pyproject.toml"
        if toml_path.is_file():
            with open(toml_path, "rb") as f:
                base_version = tomllib.load(f)["project"]["version"]
        else:
            base_version = importlib.metadata.version("overcode")
    except Exception:
        try:
            base_version = importlib.metadata.version("overcode")
        except importlib.metadata.PackageNotFoundError:
            base_version = "dev"

    # Add git commit info if available (editable install)
    try:
        from pathlib import Path
        import subprocess
        pkg_dir = Path(__file__).resolve().parent
        result = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            capture_output=True, text=True, cwd=pkg_dir, timeout=2,
        )
        if result.returncode == 0:
            git_info = result.stdout.strip()
            return f"{base_version} ({git_info})"
    except Exception:
        pass

    return base_version


def _capture_agent_pane(tmux_session: str, window_id: int) -> str:
    """Capture pane content for a single agent window.

    Returns the captured text, or empty string on failure.
    """
    try:
        from .implementations import RealTmux
        tmux = RealTmux()
        content = tmux.capture_pane(tmux_session, window_id, lines=100)
        return content or ""
    except (subprocess.SubprocessError, ImportError, OSError) as e:
        logger.debug("Failed to capture pane for window %s: %s", window_id, e)
        return ""


def get_status_data(tmux_session: str) -> Dict[str, Any]:
    """Get current status data for all agents.

    Args:
        tmux_session: tmux session name to monitor

    Returns:
        Dictionary with daemon info, summary, and per-agent data
    """
    state = get_monitor_daemon_state(tmux_session)
    now = datetime.now()

    # Capture pane content for each agent (for sister preview sync)
    pane_contents: Dict[int, str] = {}
    if state and state.sessions:
        for s in state.sessions:
            content = _capture_agent_pane(tmux_session, s.tmux_window)
            if content:
                pane_contents[s.tmux_window] = content

    result = {
        "timestamp": now.isoformat(),
        "hostname": get_hostname(),
        "version": _get_version(),
        "daemon": _build_daemon_info(state),
        "presence": _build_presence_info(state),
        "summary": _build_summary(state),
        "agents": [],
    }

    if state:
        for s in state.sessions:
            pane_content = pane_contents.get(s.tmux_window, "")
            result["agents"].append(_build_agent_info(s, now, pane_content))

    return result


def get_single_agent_status(tmux_session: str, agent_name: str) -> Optional[Dict[str, Any]]:
    """Get status data for a single agent (lightweight — only captures one pane).

    Args:
        tmux_session: tmux session name to monitor
        agent_name: Name of the agent to fetch

    Returns:
        Agent info dict, or None if not found
    """
    state = get_monitor_daemon_state(tmux_session)
    if not state or not state.sessions:
        return None

    now = datetime.now()

    # Find the matching session
    target = None
    for s in state.sessions:
        if s.name == agent_name:
            target = s
            break

    if target is None:
        return None

    pane_content = _capture_agent_pane(tmux_session, target.tmux_window)
    return _build_agent_info(target, now, pane_content)


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

    from .status_constants import PRESENCE_STATE_NAMES

    return {
        "available": True,
        "state": state.presence_state,
        "state_name": PRESENCE_STATE_NAMES.get(state.presence_state, "unknown"),
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


def _build_status_info(s: SessionDaemonState) -> Dict[str, Any]:
    """Build status and identity fields for an agent."""
    status_color = get_status_color(s.current_status)
    from .status_constants import PERMISSIVENESS_EMOJIS
    perm_emoji = PERMISSIVENESS_EMOJIS.get(s.permissiveness_mode, "👮")

    from .tui_helpers import effective_git_directory
    _gdir = effective_git_directory(s)
    git_diff = get_git_diff_stats(_gdir) if _gdir else None
    git_untracked = get_git_untracked_count(_gdir) if _gdir else None

    return {
        "name": s.name,
        "status": s.current_status,
        "status_emoji": get_status_emoji(s.current_status),
        "status_color": status_color,
        "status_color_hex": get_web_color(status_color),
        "activity": s.current_activity[:100] if s.current_activity else "",
        "repo": s.repo_name or "",
        "branch": s.branch or "",
        "permissiveness_mode": s.permissiveness_mode,
        "perm_emoji": perm_emoji,
        "standing_orders": bool(s.standing_instructions),
        "standing_orders_complete": s.standing_orders_complete,
        "git_diff_files": git_diff[0] if git_diff else 0,
        "git_diff_insertions": git_diff[1] if git_diff else 0,
        "git_diff_deletions": git_diff[2] if git_diff else 0,
        "git_untracked": git_untracked,
        "activity_summary": s.activity_summary or "",
        "activity_summary_context": s.activity_summary_context or "",
        "activity_summary_updated": s.activity_summary_updated,
        "heartbeat_enabled": s.heartbeat_enabled,
        "heartbeat_frequency_seconds": s.heartbeat_frequency_seconds,
        "heartbeat_paused": s.heartbeat_paused,
        "last_heartbeat_time": s.last_heartbeat_time,
        "is_asleep": s.is_asleep,
        "sleep_time_raw": s.sleep_time_seconds,
        "enhanced_context_enabled": s.enhanced_context_enabled,
        "human_annotation": getattr(s, "human_annotation", ""),
        "start_time": s.start_time or "",
        "parent_name": s.parent_name or "",
        "model": getattr(s, "model", "") or "",
        "effort": getattr(s, "effort", "") or "",
        "provider": getattr(s, "provider", "web") or "web",
        "tmux_window": s.tmux_window or "",
        # Skills (#252)
        "available_skills": s.available_skills,
        "loaded_skills": s.loaded_skills,
        # Tags (#356)
        "tags": list(getattr(s, "tags", []) or []),
        # Focal repo for multi-repo workspaces (#170)
        "focal_repo_subdir": getattr(s, "focal_repo_subdir", None),
        # Wrapper/sandbox badges (#437, #451)
        "wrapper": getattr(s, "wrapper", None),
        "sandbox_enabled": getattr(s, "sandbox_enabled", None),
        "skill_profile": getattr(s, "skill_profile", None),
        # Resource usage
        "cpu_percent": getattr(s, "cpu_percent", 0.0),
        "rss_bytes": getattr(s, "rss_bytes", 0),
    }


def _build_time_info(s: SessionDaemonState, now: datetime) -> Dict[str, Any]:
    """Build time-tracking fields for an agent."""
    time_in_state = 0.0
    if s.status_since:
        try:
            state_start = datetime.fromisoformat(s.status_since)
            time_in_state = (now - state_start).total_seconds()
        except ValueError:
            pass

    green_time = s.green_time_seconds
    non_green_time = s.non_green_time_seconds

    if is_green_status(s.current_status):
        green_time += time_in_state
    elif s.current_status != "terminated":
        non_green_time += time_in_state

    total_time = green_time + non_green_time
    percent_active = (green_time / total_time * 100) if total_time > 0 else 0

    uptime = calculate_uptime(s.start_time, now) if s.start_time else "-"

    return {
        "green_time": format_duration(green_time),
        "green_time_raw": green_time,
        "non_green_time": format_duration(non_green_time),
        "non_green_time_raw": non_green_time,
        "percent_active": round(percent_active),
        "time_in_state": format_duration(time_in_state),
        "time_in_state_raw": time_in_state,
        "median_work_time": format_duration(s.median_work_time) if s.median_work_time > 0 else "-",
        "median_work_time_raw": s.median_work_time,
        "uptime": uptime,
    }


def _build_cost_info(s: SessionDaemonState) -> Dict[str, Any]:
    """Build cost and interaction fields for an agent."""
    human_interactions = max(0, s.interaction_count - s.steers_count)

    return {
        "human_interactions": human_interactions,
        "robot_steers": s.steers_count,
        "tokens": format_tokens(s.input_tokens + s.output_tokens),
        "tokens_raw": s.input_tokens + s.output_tokens,
        "cost_usd": round(s.estimated_cost_usd, 2),
        "cost_budget_usd": s.cost_budget_usd,
        "budget_exceeded": s.budget_exceeded,
        "subtree_cost_usd": s.subtree_cost_usd,
    }


def _build_agent_info(s: SessionDaemonState, now: datetime, pane_content: str = "") -> Dict[str, Any]:
    """Build agent info dict from SessionDaemonState."""
    info: Dict[str, Any] = {}
    info.update(_build_status_info(s))
    info.update(_build_time_info(s, now))
    info.update(_build_cost_info(s))
    info["pane_content"] = pane_content
    # Raw daemon state — sisters can forward any field without manual mapping.
    info["daemon_state"] = s.to_dict()
    return info


def get_raw_timeline_data(tmux_session: str, hours: float = 3.0) -> Dict[str, Any]:
    """Get raw timeline history as (timestamp, status) pairs per agent.

    Returns the raw entries so callers (sister TUIs) can re-slot at their
    own dynamic terminal width. Entries are the rows as written —
    each valid for its agent until the agent's next entry — led by the
    carry (the state at the window's cutoff), so the sister's forward-fill
    starts at its left edge too.

    Args:
        tmux_session: tmux session name
        hours: How many hours of history (default 3)

    Returns:
        Dictionary with raw timeline entries per agent
    """
    history_path = get_agent_history_path(tmux_session)
    all_history = read_agent_status_history(hours=hours, history_file=history_path, carry=True)

    agents: Dict[str, list] = {}
    for ts, agent, status, activity, *_ in all_history:
        if agent not in agents:
            agents[agent] = []
        agents[agent].append({"t": ts.isoformat(), "s": status})

    return {"hours": hours, "agents": agents}


def get_health_data() -> Dict[str, Any]:
    """Get health check data."""
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "version": _get_version(),
    }
