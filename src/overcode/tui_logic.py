"""
Pure business logic functions for TUI components.

These functions are extracted from the TUI to enable unit testing
without requiring the full Textual framework or actual session objects.

All functions are pure - they take data as input and return new data.
No side effects, no mutations of input data.
"""

from datetime import datetime, timedelta
from typing import List, Set, Optional, TypeVar, Protocol, Tuple
from dataclasses import dataclass

from .status_constants import is_green_status


class SessionLike(Protocol):
    """Protocol for session-like objects used in sorting/filtering."""
    @property
    def name(self) -> str: ...
    @property
    def id(self) -> str: ...
    @property
    def is_asleep(self) -> bool: ...


class SessionWithStats(SessionLike, Protocol):
    """Protocol for sessions with stats for sorting."""
    @property
    def stats(self) -> "StatsLike": ...
    @property
    def agent_value(self) -> float: ...


class StatsLike(Protocol):
    """Protocol for stats-like objects."""
    @property
    def current_state(self) -> Optional[str]: ...


T = TypeVar('T', bound=SessionLike)
S = TypeVar('S', bound=SessionWithStats)


# Status priority orders for sorting
STATUS_ORDER_BY_ATTENTION = {
    "waiting_user": 0,
    "waiting_approval": 1,
    "error": 2,
    "running_heartbeat": 3,
    "heartbeat_start": 3,
    "waiting_heartbeat": 4,
    "running": 5,
    "terminated": 6,
    "asleep": 7,
}

STATUS_ORDER_BY_VALUE = {
    "waiting_user": 0,
    "waiting_approval": 0,
    "error": 0,
    "waiting_heartbeat": 0,
    "running": 1,
    "running_heartbeat": 1,
    "heartbeat_start": 1,
    "terminated": 2,
    "asleep": 2,
}


def sort_sessions_alphabetical(sessions: List[T]) -> List[T]:
    """Sort sessions alphabetically by name (case-insensitive).

    Args:
        sessions: List of session objects with a name attribute

    Returns:
        New sorted list (does not mutate input)
    """
    return sorted(sessions, key=lambda s: s.name.lower())


def sort_sessions_by_status(sessions: List[S]) -> List[S]:
    """Sort sessions by status priority, then alphabetically.

    Priority order: waiting_user, waiting_approval, error,
    running_heartbeat, waiting_heartbeat, running, terminated, asleep.

    Args:
        sessions: List of session objects with stats.current_state

    Returns:
        New sorted list (does not mutate input)
    """
    return sorted(
        sessions,
        key=lambda s: (
            STATUS_ORDER_BY_ATTENTION.get(s.stats.current_state or "running", 4),
            s.name.lower()
        )
    )


def sort_sessions_by_value(sessions: List[S]) -> List[S]:
    """Sort sessions by value (priority) descending, then alphabetically.

    Non-green agents (needing attention) sort first, then by agent_value
    descending within each group.

    Args:
        sessions: List of session objects with stats.current_state and agent_value

    Returns:
        New sorted list (does not mutate input)
    """
    return sorted(
        sessions,
        key=lambda s: (
            STATUS_ORDER_BY_VALUE.get(s.stats.current_state or "running", 1),
            -s.agent_value,
            s.name.lower()
        )
    )


def sort_sessions(sessions: List[S], mode: str) -> List[S]:
    """Sort sessions based on the specified mode.

    Args:
        sessions: List of session objects
        mode: One of "alphabetical", "by_status", "by_value"

    Returns:
        New sorted list (does not mutate input)
    """
    if mode == "alphabetical":
        return sort_sessions_alphabetical(sessions)
    elif mode == "by_status":
        return sort_sessions_by_status(sessions)
    elif mode == "by_value":
        return sort_sessions_by_value(sessions)
    else:
        # Default to alphabetical for unknown modes
        return sort_sessions_alphabetical(sessions)


def filter_visible_sessions(
    active_sessions: List[T],
    terminated_sessions: List[T],
    hide_asleep: bool,
    show_terminated: bool,
) -> List[T]:
    """Filter sessions based on visibility preferences.

    Args:
        active_sessions: List of currently active sessions
        terminated_sessions: List of terminated/killed sessions
        hide_asleep: If True, filter out sleeping agents
        show_terminated: If True, include terminated sessions

    Returns:
        New filtered list (does not mutate inputs)
    """
    result = list(active_sessions)

    # Filter out sleeping agents if requested
    if hide_asleep:
        result = [s for s in result if not s.is_asleep]

    # Include terminated sessions if requested
    if show_terminated:
        active_ids = {s.id for s in active_sessions}
        for session in terminated_sessions:
            if session.id not in active_ids:
                result.append(session)

    return result


def get_sort_mode_display_name(mode: str) -> str:
    """Get human-readable display name for sort mode.

    Args:
        mode: Sort mode identifier

    Returns:
        Human-readable name
    """
    mode_names = {
        "alphabetical": "Alphabetical",
        "by_status": "By Status",
        "by_value": "By Value (priority)",
    }
    return mode_names.get(mode, mode)


def cycle_sort_mode(current_mode: str, available_modes: List[str]) -> str:
    """Get the next sort mode in the cycle.

    Args:
        current_mode: Current sort mode
        available_modes: List of available sort modes

    Returns:
        Next sort mode in the cycle
    """
    if not available_modes:
        return current_mode

    try:
        current_idx = available_modes.index(current_mode)
    except ValueError:
        current_idx = -1

    new_idx = (current_idx + 1) % len(available_modes)
    return available_modes[new_idx]


@dataclass
class SpinStats:
    """Statistics for spin rate display."""
    green_count: int
    total_count: int
    sleeping_count: int
    mean_spin: float
    total_tokens: int


def calculate_spin_stats(
    sessions: List,
    asleep_session_ids: Set[str],
) -> SpinStats:
    """Calculate spin rate statistics from sessions.

    Args:
        sessions: List of session daemon states with green_time_seconds,
                  non_green_time_seconds, current_status, input_tokens, output_tokens
        asleep_session_ids: Set of session IDs that are asleep

    Returns:
        SpinStats dataclass with calculated values
    """
    # Filter out sleeping agents for active stats
    active_sessions = [s for s in sessions if s.session_id not in asleep_session_ids]
    sleeping_count = len(sessions) - len(active_sessions)

    total_count = len(active_sessions)
    green_count = sum(1 for s in active_sessions if is_green_status(s.current_status))

    # Calculate mean spin rate
    mean_spin = 0.0
    for s in active_sessions:
        total_time = s.green_time_seconds + s.non_green_time_seconds
        if total_time > 0:
            mean_spin += s.green_time_seconds / total_time

    # Total tokens (include sleeping agents)
    total_tokens = sum(s.input_tokens + s.output_tokens for s in sessions)

    return SpinStats(
        green_count=green_count,
        total_count=total_count,
        sleeping_count=sleeping_count,
        mean_spin=mean_spin,
        total_tokens=total_tokens,
    )


def calculate_mean_spin_from_history(
    history: List[Tuple[datetime, str, str, str]],
    agent_names: List[str],
    baseline_minutes: int,
    now: Optional[datetime] = None,
) -> Tuple[float, int]:
    """Calculate mean spin rate from CSV history within a time window.

    This provides a time-windowed average of how many agents were running,
    as opposed to the cumulative calculation in calculate_spin_stats().

    Args:
        history: List of (timestamp, agent, status, activity) tuples from CSV
        agent_names: List of active (non-sleeping) agent names to include
        baseline_minutes: Minutes back from now (0 = instantaneous, not used)
        now: Reference time (defaults to datetime.now())

    Returns:
        Tuple of (mean_spin, sample_count) where:
        - mean_spin: Average number of agents in "running" state during window
        - sample_count: Total samples in the window (0 if no data)
    """
    if now is None:
        now = datetime.now()

    if baseline_minutes <= 0 or not agent_names:
        return (0.0, 0)

    cutoff = now - timedelta(minutes=baseline_minutes)

    # Filter to window and active agents only
    window_history = [
        (ts, agent, status)
        for ts, agent, status, _ in history
        if cutoff <= ts <= now and agent in agent_names
    ]

    if not window_history:
        return (0.0, 0)

    running_count = sum(1 for _, _, status in window_history if is_green_status(status))
    total_count = len(window_history)

    # mean_spin = (fraction of samples that were "running") * num_agents
    # This gives "average number of agents running at any point in time"
    # Example: 2 agents, 50% of samples are "running" -> mean_spin = 1.0
    num_agents = len(agent_names)
    mean_spin = (running_count / total_count) * num_agents if total_count > 0 else 0.0

    return (mean_spin, total_count)


def calculate_green_percentage(green_time: float, non_green_time: float) -> float:
    """Calculate the percentage of time spent in green (running) state.

    Args:
        green_time: Total green time in seconds
        non_green_time: Total non-green time in seconds

    Returns:
        Percentage (0-100) of time in green state
    """
    total_time = green_time + non_green_time
    if total_time <= 0:
        return 0.0
    return green_time / total_time * 100


def calculate_human_interaction_count(
    total_interactions: Optional[int],
    robot_interactions: int,
) -> int:
    """Calculate number of human interactions.

    Args:
        total_interactions: Total interaction count (or None)
        robot_interactions: Number of robot/supervisor interactions

    Returns:
        Number of human interactions (clamped to 0 minimum)
    """
    if total_interactions is None:
        return 0
    return max(0, total_interactions - robot_interactions)
