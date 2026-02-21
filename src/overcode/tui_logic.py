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
    "done": 6,
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
    "done": 2,
    "asleep": 2,
}


def _remote_sort_key(s) -> tuple:
    """Primary sort key: local (0) first, then remote (1) grouped by host."""
    is_remote = getattr(s, 'is_remote', False)
    if not isinstance(is_remote, bool):
        is_remote = False
    host = getattr(s, 'source_host', '') or '' if is_remote else ''
    if not isinstance(host, str):
        host = ''
    return (1 if is_remote else 0, host.lower())


def sort_sessions_alphabetical(sessions: List[T]) -> List[T]:
    """Sort sessions alphabetically by name (case-insensitive).

    Args:
        sessions: List of session objects with a name attribute

    Returns:
        New sorted list (does not mutate input)
    """
    return sorted(sessions, key=lambda s: (*_remote_sort_key(s), s.name.lower()))


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
            *_remote_sort_key(s),
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
            *_remote_sort_key(s),
            STATUS_ORDER_BY_VALUE.get(s.stats.current_state or "running", 1),
            -s.agent_value,
            s.name.lower()
        )
    )


def sort_sessions_by_tree(sessions: List[T], parent_id_fn=None) -> List[T]:
    """Sort sessions in tree order: roots first (alphabetical), children immediately after parent.

    Args:
        sessions: List of session objects
        parent_id_fn: Function to get parent_session_id from a session.
            Defaults to accessing session.parent_session_id attribute.

    Returns:
        New sorted list (does not mutate input)
    """
    if parent_id_fn is None:
        parent_id_fn = lambda s: getattr(s, 'parent_session_id', None)

    # Build parent_id -> children map
    children_map: dict = {}
    roots = []
    for s in sessions:
        pid = parent_id_fn(s)
        if pid is None:
            roots.append(s)
        else:
            children_map.setdefault(pid, []).append(s)

    # Sort each group: local first, then remote by host, then alphabetically
    roots.sort(key=lambda s: (*_remote_sort_key(s), s.name.lower()))
    for kids in children_map.values():
        kids.sort(key=lambda s: s.name.lower())

    # DFS from roots
    result = []

    def dfs(session):
        result.append(session)
        for child in children_map.get(session.id, []):
            dfs(child)

    for root in roots:
        dfs(root)

    return result


def sort_sessions(sessions: List[S], mode: str) -> List[S]:
    """Sort sessions based on the specified mode.

    Args:
        sessions: List of session objects
        mode: One of "alphabetical", "by_status", "by_value", "by_tree"

    Returns:
        New sorted list (does not mutate input)
    """
    if mode == "alphabetical":
        return sort_sessions_alphabetical(sessions)
    elif mode == "by_status":
        return sort_sessions_by_status(sessions)
    elif mode == "by_value":
        return sort_sessions_by_value(sessions)
    elif mode == "by_tree":
        return sort_sessions_by_tree(sessions)
    else:
        # Default to alphabetical for unknown modes
        return sort_sessions_alphabetical(sessions)


def filter_visible_sessions(
    active_sessions: List[T],
    terminated_sessions: List[T],
    hide_asleep: bool,
    show_terminated: bool,
    show_done: bool = False,
    collapsed_parents: Optional[Set[str]] = None,
) -> List[T]:
    """Filter sessions based on visibility preferences.

    Args:
        active_sessions: List of currently active sessions
        terminated_sessions: List of terminated/killed sessions
        hide_asleep: If True, filter out sleeping agents
        show_terminated: If True, include terminated sessions
        show_done: If True, include "done" child agents (#244)
        collapsed_parents: Set of session IDs whose children should be hidden (#244)

    Returns:
        New filtered list (does not mutate inputs)
    """
    result = list(active_sessions)

    # Filter out sleeping agents if requested
    if hide_asleep:
        result = [s for s in result if not s.is_asleep]

    # Filter out "done" agents unless show_done (#244)
    if not show_done:
        result = [s for s in result if getattr(s, 'status', None) != 'done']

    # Include terminated sessions if requested
    if show_terminated:
        active_ids = {s.id for s in active_sessions}
        for session in terminated_sessions:
            if session.id not in active_ids:
                result.append(session)

    # Hide descendants of collapsed parents (#244)
    if collapsed_parents:
        hidden_ids = _get_collapsed_descendants(result, collapsed_parents)
        if hidden_ids:
            result = [s for s in result if s.id not in hidden_ids]

    return result


def _get_collapsed_descendants(
    sessions: List[T],
    collapsed_parents: Set[str],
) -> Set[str]:
    """Get IDs of all sessions that should be hidden due to collapsed parents.

    Walks down from each collapsed parent, hiding all descendants recursively.
    """
    # Build parent_id -> children map
    children_map: dict = {}
    for s in sessions:
        pid = getattr(s, 'parent_session_id', None)
        if pid is not None:
            children_map.setdefault(pid, []).append(s)

    hidden: Set[str] = set()

    def hide_subtree(parent_id: str) -> None:
        for child in children_map.get(parent_id, []):
            hidden.add(child.id)
            hide_subtree(child.id)

    for parent_id in collapsed_parents:
        hide_subtree(parent_id)

    return hidden


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
        "by_tree": "By Tree (hierarchy)",
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


@dataclass
class TreeNodeMeta:
    """Tree metadata for a single session node."""
    depth: int
    prefix: str        # "├─", "└─", or "" for roots
    child_count: int
    is_last: bool


def compute_tree_metadata(sessions: List[T], parent_id_fn=None) -> dict:
    """Compute tree depth, prefix, and child count for each session.

    Works with any session list (local, remote, or mixed).
    Does NOT rely on session_manager — uses only the passed-in list.

    Args:
        sessions: List of session objects (already sorted in tree order)
        parent_id_fn: Function to get parent_session_id from a session.
            Defaults to accessing session.parent_session_id attribute.

    Returns:
        dict mapping session_id -> TreeNodeMeta
    """
    if parent_id_fn is None:
        parent_id_fn = lambda s: getattr(s, 'parent_session_id', None)

    # Build id -> session lookup
    id_to_session = {s.id: s for s in sessions}

    # Build parent -> children map (preserving input order)
    children_map: dict = {}
    for s in sessions:
        pid = parent_id_fn(s)
        if pid is not None:
            children_map.setdefault(pid, []).append(s)

    # Compute child counts
    child_counts: dict = {}
    for s in sessions:
        child_counts[s.id] = len(children_map.get(s.id, []))

    # Compute depth by walking parent chains
    depth_cache: dict = {}

    def _get_depth(session_id: str) -> int:
        if session_id in depth_cache:
            return depth_cache[session_id]
        s = id_to_session.get(session_id)
        if s is None:
            depth_cache[session_id] = 0
            return 0
        pid = parent_id_fn(s)
        if pid is None or pid not in id_to_session:
            depth_cache[session_id] = 0
            return 0
        depth_cache[session_id] = _get_depth(pid) + 1
        return depth_cache[session_id]

    result = {}
    for s in sessions:
        depth = _get_depth(s.id)
        pid = parent_id_fn(s)
        siblings = children_map.get(pid, []) if pid is not None else []
        is_last = bool(siblings) and siblings[-1].id == s.id

        if depth == 0:
            prefix = ""
        else:
            indent = "  " * (depth - 1)
            connector = "└─" if is_last else "├─"
            prefix = indent + connector

        result[s.id] = TreeNodeMeta(
            depth=depth,
            prefix=prefix,
            child_count=child_counts.get(s.id, 0),
            is_last=is_last,
        )

    return result


@dataclass
class StallState:
    """Result of stall detection computation."""
    is_new_stall: bool          # Session just transitioned TO stalled
    is_unvisited_stalled: bool  # Session is stalled and not yet visited
    should_clear_tracking: bool  # Session left stalled state, clear tracking


def compute_stall_state(
    status: str,
    prev_status: Optional[str],
    session_id: str,
    visited_stalled_agents: Set[str],
    is_asleep: bool,
) -> StallState:
    """Compute stall state transitions for a session.

    Pure function — no side effects, fully testable.

    Args:
        status: Current agent status
        prev_status: Previous agent status (or None)
        session_id: The session's ID
        visited_stalled_agents: Set of session IDs already visited while stalled
        is_asleep: Whether the session is asleep

    Returns:
        StallState with transition flags
    """
    is_waiting = status == "waiting_user"
    prev_was_green = prev_status is not None and is_green_status(prev_status)

    # Only green→waiting or None→waiting is a potential new stall.
    # Non-green waiting states (waiting_heartbeat, error, approval) transitioning
    # back to waiting_user should NOT count as a new stall.
    is_new_stall = is_waiting and (prev_was_green or prev_status is None)

    # Only clear stall tracking during green (actively working) statuses.
    # Non-green, non-waiting states (waiting_heartbeat, error, approval) are still
    # conceptually "stalled" and should NOT clear notification tracking.
    should_clear_tracking = is_green_status(status)

    is_unvisited_stalled = (
        is_waiting
        and session_id not in visited_stalled_agents
        and not is_asleep
    )

    return StallState(
        is_new_stall=is_new_stall,
        is_unvisited_stalled=is_unvisited_stalled,
        should_clear_tracking=should_clear_tracking,
    )


def should_send_stall_notification(
    status: str,
    is_notified: bool,
    is_asleep: bool,
    has_stall_start: bool,
    stall_age_seconds: float,
    uptime_seconds: float,
) -> bool:
    """Determine whether a macOS stall notification should be sent.

    Pure function — no side effects, fully testable.

    Args:
        status: Current agent status
        is_notified: Whether we already sent a notification for this stall
        is_asleep: Whether the session is asleep
        has_stall_start: Whether we have a recorded stall start time
        stall_age_seconds: How long the session has been stalled
        uptime_seconds: Total session uptime

    Returns:
        True if a notification should be sent
    """
    if status != "waiting_user":
        return False
    if is_notified:
        return False
    if is_asleep:
        return False
    if not has_stall_start:
        return False
    return stall_age_seconds >= 30 and uptime_seconds >= 60


def compute_session_widget_diff(
    existing_ids: Set[str],
    display_ids: List[str],
) -> Tuple[Set[str], Set[str]]:
    """Compute which session widgets need to be added/removed.

    Pure function — no side effects, fully testable.

    Args:
        existing_ids: Set of session IDs currently in the widget tree
        display_ids: List of session IDs that should be displayed

    Returns:
        Tuple of (to_add, to_remove) sets of session IDs
    """
    new_ids = set(display_ids)
    to_add = new_ids - existing_ids
    to_remove = existing_ids - new_ids
    return to_add, to_remove


def detect_display_changes(
    sessions: List,
    any_has_budget: bool,
    any_has_oversight: bool,
) -> Tuple[bool, bool]:
    """Compute budget and oversight flag changes from sessions.

    Pure function — no side effects, fully testable.

    Args:
        sessions: List of session objects
        any_has_budget: Current value of any_has_budget flag
        any_has_oversight: Current value of any_has_oversight flag

    Returns:
        Tuple of (new_any_has_budget, new_any_has_oversight)
    """
    new_budget = any(getattr(s, 'cost_budget_usd', 0) > 0 for s in sessions)
    new_oversight = any(
        getattr(s, 'oversight_policy', 'wait') == 'timeout'
        and getattr(s, 'oversight_timeout_seconds', 0) > 0
        for s in sessions
    )
    return new_budget, new_oversight


def compute_active_session_names(
    sessions: List,
    asleep_ids: Set[str],
) -> List[str]:
    """Compute the names of active (non-asleep) sessions.

    Pure function — no side effects, fully testable.

    Args:
        sessions: List of session objects with session_id and name attributes
        asleep_ids: Set of session IDs that are asleep

    Returns:
        List of session names that are not asleep
    """
    return [s.name for s in sessions if s.session_id not in asleep_ids]


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
