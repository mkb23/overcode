"""
Pure business logic functions for TUI components.

These functions are extracted from the TUI to enable unit testing
without requiring the full Textual framework or actual session objects.

All functions are pure - they take data as input and return new data.
No side effects, no mutations of input data.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Mapping, Set, Optional, TypeVar, Protocol, Tuple, TYPE_CHECKING
from dataclasses import dataclass

from .settings import DAEMON
from .status_constants import is_green_status
from .tmux_utils import pane_for_window

if TYPE_CHECKING:
    from .pane_capture_gate import PaneChangeTracker
    from .tmux_utils import PaneInfo


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


def sort_sessions_alphabetical(sessions: List[T], parent_id_fn=None, reverse=False) -> List[T]:
    """Sort sessions alphabetically by name, keeping siblings grouped under parents.

    Args:
        sessions: List of session objects with a name attribute
        parent_id_fn: Function to get parent_session_id from a session.

    Returns:
        New sorted list (does not mutate input)
    """
    return _tree_aware_sort(
        sessions,
        key=lambda s: (s.name.lower(),),
        parent_id_fn=parent_id_fn,
        reverse=reverse,
    )


def sort_sessions_by_status(sessions: List[S], parent_id_fn=None, reverse=False) -> List[S]:
    """Sort sessions by status priority, keeping siblings grouped under parents.

    Roots are sorted by status, then children are sorted by status under
    their parent. This prevents siblings from being scattered across the list.

    Priority order: waiting_user, waiting_approval, error,
    running_heartbeat, waiting_heartbeat, running, terminated, asleep.

    Args:
        sessions: List of session objects with stats.current_state
        parent_id_fn: Function to get parent_session_id from a session.

    Returns:
        New sorted list (does not mutate input)
    """
    return _tree_aware_sort(
        sessions,
        key=lambda s: (
            STATUS_ORDER_BY_ATTENTION.get(s.stats.current_state or "running", 4),
            s.name.lower()
        ),
        parent_id_fn=parent_id_fn,
        reverse=reverse,
    )


def sort_sessions_by_value(sessions: List[S], parent_id_fn=None, reverse=False) -> List[S]:
    """Sort sessions by value (priority) descending, keeping siblings grouped.

    Roots are sorted by value, then children are sorted by value under
    their parent. This prevents siblings from being scattered across the list.

    Args:
        sessions: List of session objects with stats.current_state and agent_value
        parent_id_fn: Function to get parent_session_id from a session.

    Returns:
        New sorted list (does not mutate input)
    """
    return _tree_aware_sort(
        sessions,
        key=lambda s: (
            STATUS_ORDER_BY_VALUE.get(s.stats.current_state or "running", 1),
            -s.agent_value,
            s.name.lower()
        ),
        parent_id_fn=parent_id_fn,
        reverse=reverse,
    )


def _tree_aware_sort(sessions, key=None, parent_id_fn=None, reverse=False, order=None):
    """Sort sessions preserving tree hierarchy: roots sorted by key, children sorted under parent.

    Args:
        sessions: List of session objects
        key: Sort key function for ordering within each level
        parent_id_fn: Function to get parent_session_id from a session.
        reverse: Reverse the key order (#487).
        order: Instead of key/reverse, a function that returns one level's
            sessions in order (used by column sorts, which put unknowns last).

    Returns:
        New sorted list
    """
    if order is None:
        order = lambda items: sorted(items, key=key, reverse=reverse)
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

    # Sort roots by the provided key (local and remote intermixed)
    roots = order(roots)
    for pid, kids in children_map.items():
        children_map[pid] = order(kids)

    # DFS from roots
    result = []

    def dfs(session):
        result.append(session)
        for child in children_map.get(session.id, []):
            dfs(child)

    for root in roots:
        dfs(root)

    return result


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

    # Sort roots alphabetically (local and remote intermixed)
    roots.sort(key=lambda s: s.name.lower())
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


def sort_sessions_by_column(
    sessions: List[T], values: Dict[str, Any], descending: bool, parent_id_fn=None,
) -> List[T]:
    """Sort by per-session column values, keeping siblings grouped (#487).

    `values` maps session id to the column's sort key. Sessions with no
    value (None or missing) go last in either direction; ties keep name
    order, as do values that cannot be compared with each other.
    """
    def order(items):
        items = sorted(items, key=lambda s: s.name.lower())
        present = [s for s in items if values.get(s.id) is not None]
        missing = [s for s in items if values.get(s.id) is None]
        try:
            present.sort(key=lambda s: values[s.id], reverse=descending)
        except TypeError:
            present.sort(key=lambda s: str(values[s.id]), reverse=descending)
        return present + missing

    return _tree_aware_sort(sessions, parent_id_fn=parent_id_fn, order=order)


def sort_sessions(
    sessions: List[S], mode: str, reverse: bool = False,
    values: Optional[Dict[str, Any]] = None,
) -> List[S]:
    """Sort sessions based on the specified mode.

    Args:
        sessions: List of session objects
        mode: One of "alphabetical", "by_status", "by_value", "by_tree", or
            "col:<column id>" to sort by a summary column (#487)
        reverse: Flip the mode's natural direction (ignored for by_tree)
        values: For "col:" modes, session id -> the column's sort key

    Returns:
        New sorted list (does not mutate input)
    """
    if mode == "alphabetical":
        return sort_sessions_alphabetical(sessions, reverse=reverse)
    elif mode == "by_status":
        return sort_sessions_by_status(sessions, reverse=reverse)
    elif mode == "by_value":
        return sort_sessions_by_value(sessions, reverse=reverse)
    elif mode == "by_tree":
        return sort_sessions_by_tree(sessions)
    elif mode.startswith(COLUMN_SORT_PREFIX):
        return sort_sessions_by_column(
            sessions, values or {}, sort_descending(mode, reverse),
        )
    else:
        # Default to alphabetical for unknown modes
        return sort_sessions_alphabetical(sessions)


# Column sorts (#487). Three header columns are the named presets, so
# clicking Name, Status or Value selects the same order `overcode list
# --sort` knows; every other sortable column is "col:<id>".
COLUMN_SORT_PREFIX = "col:"
PRESET_FOR_COLUMN = {
    "agent_name": "alphabetical",
    "status_symbol": "by_status",
    "agent_value": "by_value",
}


def sort_mode_for_column(column_id: str) -> str:
    """The sort_mode that sorts by a column."""
    return PRESET_FOR_COLUMN.get(column_id, COLUMN_SORT_PREFIX + column_id)


def sort_column_for_mode(mode: str) -> Optional[str]:
    """The column a sort_mode sorts by; None for tree order."""
    if mode.startswith(COLUMN_SORT_PREFIX):
        return mode[len(COLUMN_SORT_PREFIX):]
    for col_id, preset in PRESET_FOR_COLUMN.items():
        if preset == mode:
            return col_id
    return None


def sort_descending(mode: str, reverse: bool) -> bool:
    """Whether a sort_mode currently runs largest-first (drawn ▼)."""
    col_id = sort_column_for_mode(mode)
    if col_id is None:
        return False
    from .summary_columns import COLUMNS_BY_ID
    col = COLUMNS_BY_ID.get(col_id)
    natural = col.sort_desc if col is not None else False
    return natural != reverse


def filter_visible_sessions(
    active_sessions: List[T],
    terminated_sessions: List[T],
    hide_asleep: bool,
    show_terminated: bool,
    show_done: bool = False,
    collapsed_parents: Optional[Set[str]] = None,
    tag_filter: Optional[str] = None,
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

    # Filter out terminated agents from active_sessions unless show_terminated (#456).
    # Without this, a session that flips to status="terminated" inside active_sessions
    # (e.g. after launcher.list_sessions detects a missing tmux window during a stale
    # background read) would linger in the summary list until the next refresh cycle
    # because nothing here removes it.
    if not show_terminated:
        result = [s for s in result if getattr(s, 'status', None) != 'terminated']

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

    # Tag filter (#357): keep agents whose tags contain the chosen tag.
    # Tag matching is case-insensitive to match how add_tags lower-cases
    # everything. Empty / None disables the filter.
    if tag_filter:
        tf = tag_filter.strip().lower()
        if tf:
            result = [
                s for s in result
                if any(t.lower() == tf for t in (getattr(s, 'tags', None) or []))
            ]

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
    if mode.startswith(COLUMN_SORT_PREFIX):
        from .summary_columns import COLUMNS_BY_ID
        col = COLUMNS_BY_ID.get(mode[len(COLUMN_SORT_PREFIX):])
        return col.name if col is not None and col.name else mode
    return mode_names.get(mode, mode)


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


# A history row stands for its agent until the agent's next row (rows are
# written on change plus a keepalive, audit R10), or for this long when none
# follows: two keepalives. A daemon gap longer than that counts as "no
# samples", the way it did with a row per tick.
SPIN_ROW_VALIDITY_SECONDS = 2 * DAEMON.status_history_keepalive_seconds


def calculate_mean_spin_from_history(
    history: list,
    agent_names: List[str],
    baseline_minutes: int,
    now: Optional[datetime] = None,
) -> Tuple[float, int]:
    """Calculate mean spin rate from CSV history within a time window.

    This provides a time-windowed average of how many agents were running,
    as opposed to the cumulative calculation in calculate_spin_stats().

    Time-weighted: each row stands for its agent from its timestamp until
    the agent's next row (or SPIN_ROW_VALIDITY_SECONDS, or ``now``), clipped
    to the window, and the mean is the green share of that covered time
    scaled to the agent count. With a row per agent per tick this is the
    old "fraction of samples that were running" to within one tick; with
    change-only logging it is the same number from far fewer rows. Rows
    before the cutoff set each agent's state at the window's left edge.

    Args:
        history: List of (timestamp, agent, status, ...) tuples from CSV,
            oldest first; rows before the cutoff are welcome
        agent_names: List of active (non-sleeping) agent names to include
        baseline_minutes: Minutes back from now (0 = instantaneous, not used)
        now: Reference time (defaults to datetime.now())

    Returns:
        Tuple of (mean_spin, sample_count) where:
        - mean_spin: Average number of agents in "running" state during window
        - sample_count: Rows inside the window for the named agents (0 if no
          data; the status bar's has-data gate)
    """
    if now is None:
        now = datetime.now()

    if baseline_minutes <= 0 or not agent_names:
        return (0.0, 0)

    cutoff = now - timedelta(minutes=baseline_minutes)
    names = set(agent_names)
    validity = timedelta(seconds=SPIN_ROW_VALIDITY_SECONDS)

    green_seconds = 0.0
    covered_seconds = 0.0
    sample_count = 0
    # Per agent: (timestamp, is_green) of its latest row seen so far
    open_rows: dict = {}

    def close(agent: str, until: datetime) -> None:
        nonlocal green_seconds, covered_seconds
        ts, is_green = open_rows[agent]
        end = min(until, ts + validity)
        start = max(ts, cutoff)
        if end > start:
            seconds = (end - start).total_seconds()
            covered_seconds += seconds
            if is_green:
                green_seconds += seconds

    for ts, agent, status, *_ in history:
        if agent not in names or ts > now:
            continue
        if ts >= cutoff:
            sample_count += 1
        if agent in open_rows:
            close(agent, ts)
        open_rows[agent] = (ts, is_green_status(status))
    for agent in open_rows:
        close(agent, now)

    if sample_count == 0 or covered_seconds <= 0:
        return (0.0, sample_count)

    # mean_spin = (green share of the covered agent-time) * num_agents
    # This gives "average number of agents running at any point in time"
    # Example: 2 agents, running half the time each -> mean_spin = 1.0
    num_agents = len(agent_names)
    mean_spin = (green_seconds / covered_seconds) * num_agents

    return (mean_spin, sample_count)


@dataclass
class WindowBurnStats:
    """Token/cost spend over a time window — used for both per-session and
    aggregated totals. When holding an aggregate, ``per_session`` maps
    session id → that session's own WindowBurnStats (whose per_session is
    always empty)."""
    window_hours: float
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    per_session: dict = None  # Optional[Dict[str, WindowBurnStats]]

    def __post_init__(self):
        if self.per_session is None:
            self.per_session = {}

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def tokens_per_hour(self) -> float:
        if self.window_hours <= 0:
            return 0.0
        return self.total_tokens / self.window_hours

    @property
    def cost_per_hour(self) -> float:
        if self.window_hours <= 0:
            return 0.0
        return self.cost_usd / self.window_hours


def compute_window_burn(
    sessions,  # iterable of full Session objects (NOT SessionDaemonState)
    asleep_session_ids: Set[str],
    hours: float,
    now: Optional[datetime] = None,
) -> WindowBurnStats:
    """Aggregate token burn across sessions over the past ``hours`` window (#174).

    Re-parses each session's transcript files filtered by the window's start
    timestamp, sums tokens, and computes cost using each session's per-model
    pricing. Skips asleep sessions and sessions without start_directory or
    agent_session_ids.

    Designed to run on a worker thread — does file I/O proportional to
    `(active sessions) × (agent_session_ids per session)`. Safe for the
    typical 5-15 session range; cache externally if you have hundreds.
    """
    from .stats_reader import stats_reader_for_session
    from .pricing import calculate_cost_estimate
    from .settings import get_user_config, get_model_pricing

    stats = WindowBurnStats(window_hours=hours)
    if hours <= 0:
        return stats

    if now is None:
        now = datetime.now()
    # `since` is local-naive to match read_window_token_usage, which normalises
    # message timestamps to the local zone via .astimezone() before dropping
    # tzinfo. Mixing naive UTC with local-naive (as some older callers in
    # this module do) skews the cutoff by the local UTC offset.
    since = now - timedelta(hours=hours)

    config = None
    for session in sessions:
        if session.id in asleep_session_ids:
            continue

        u = stats_reader_for_session(session).get_window_token_usage(session, since)
        if not (u["input_tokens"] or u["output_tokens"]
                or u["cache_creation_tokens"] or u["cache_read_tokens"]):
            continue

        # Lazily load user config — only needed if we actually have tokens
        if config is None:
            config = get_user_config()
        mp = get_model_pricing(
            getattr(session, 'model', None), config,
            provider=getattr(session, 'provider', None),
        )
        cost = calculate_cost_estimate(
            u["input_tokens"],
            u["output_tokens"],
            u["cache_creation_tokens"],
            u["cache_read_tokens"],
            price_input=mp.input,
            price_output=mp.output,
            price_cache_write=mp.cache_write,
            price_cache_read=mp.cache_read,
        )

        stats.input_tokens += u["input_tokens"]
        stats.output_tokens += u["output_tokens"]
        stats.cache_creation_tokens += u["cache_creation_tokens"]
        stats.cache_read_tokens += u["cache_read_tokens"]
        stats.cost_usd += cost

        # Per-session breakdown for the burn-rate column (#174)
        stats.per_session[session.id] = WindowBurnStats(
            window_hours=hours,
            input_tokens=u["input_tokens"],
            output_tokens=u["output_tokens"],
            cache_creation_tokens=u["cache_creation_tokens"],
            cache_read_tokens=u["cache_read_tokens"],
            cost_usd=cost,
        )

    return stats


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


def compute_child_counts(sessions: List[T], parent_id_fn=None) -> dict:
    """Compute child counts only — lightweight alternative to compute_tree_metadata.

    Use when tree display prefixes/depths aren't needed (e.g., non-tree sort modes).

    Args:
        sessions: List of session objects
        parent_id_fn: Function to get parent_session_id from a session.

    Returns:
        dict mapping session_id -> child_count (int)
    """
    if parent_id_fn is None:
        parent_id_fn = lambda s: getattr(s, 'parent_session_id', None)

    child_counts: dict = {}
    for s in sessions:
        child_counts.setdefault(s.id, 0)
        pid = parent_id_fn(s)
        if pid is not None:
            child_counts[pid] = child_counts.get(pid, 0) + 1

    return child_counts


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
) -> Tuple[bool, bool, bool]:
    """Compute budget, oversight, and PR flag changes from sessions.

    Pure function — no side effects, fully testable.

    Args:
        sessions: List of session objects
        any_has_budget: Current value of any_has_budget flag
        any_has_oversight: Current value of any_has_oversight flag

    Returns:
        Tuple of (new_any_has_budget, new_any_has_oversight, new_any_has_pr)
    """
    new_budget = any(getattr(s, 'cost_budget_usd', 0) > 0 for s in sessions)
    new_oversight = any(
        getattr(s, 'oversight_policy', 'wait') == 'timeout'
        and getattr(s, 'oversight_timeout_seconds', 0) > 0
        for s in sessions
    )
    new_pr = any(getattr(s, 'pr_number', None) is not None for s in sessions)
    return new_budget, new_oversight, new_pr


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


# ── Polling load shaping ──────────────────────────────────────────────
#
# The TUI's fast path captures tmux panes every 250ms. Every capture is a
# tmux client round-trip that the (single-threaded) tmux server has to
# serve in line with keystrokes, so the *number* of commands per second is
# what makes typing in agent panes feel laggy at fleet scale. Only the
# focused agent needs 4 Hz (its pane feeds the preview); the daemon owns
# every other agent's status and refreshes it every 2s anyway, so their
# captures — which only feed the bash/subagent/auto-accept columns — are
# spread round-robin across ticks.

NON_FOCUSED_CAPTURE_EVERY = 4  # ticks; at 250ms that's ~1 Hz per agent


NON_FOCUSED_CAPTURES_PER_TICK = 12  # the rotation period grows past this many per tick


def capture_rotation_period(
    n_nonfocused: int,
    min_every: int = NON_FOCUSED_CAPTURE_EVERY,
    max_per_tick: int = NON_FOCUSED_CAPTURES_PER_TICK,
) -> int:
    """Ticks between two captures of the same non-focused session.

    1-in-``min_every`` (about 1 Hz at 250 ms ticks) until that would put more
    than ``max_per_tick`` non-focused captures on one tick; past that the
    period grows with N so a tick issues at most ~``1 + max_per_tick``
    capture-pane commands whatever the fleet size: 48 agents -> every 4
    (13/tick), 50 -> every 5 (11/tick), 200 -> every 17 (13/tick). The tmux
    server is single-threaded and shared by every overcode process on the
    host, so the per-tick command count is what has to be bounded; each
    non-focused status is still refreshed every ``every`` * 250 ms.
    """
    every = max(1, min_every)
    if n_nonfocused > 0:
        every = max(every, -(-n_nonfocused // max(1, max_per_tick)))  # ceil
    return every


def select_capture_sessions(
    session_ids: List[str],
    focused_id: Optional[str],
    tick: int,
    always_ids: Set[str] = frozenset(),
    every: Optional[int] = None,
) -> Set[str]:
    """Pick which sessions get a tmux capture on this fast-path tick.

    Always: the focused session, and ``always_ids`` (sessions never captured
    yet, so their pane-derived columns fill in on first sight — at most one
    extra capture per session lifetime). Everyone else is captured on a
    rotating 1-in-``every`` slot (``capture_rotation_period`` when ``every``
    is None), *whether or not the daemon is reporting on them*: daemon
    freshness decides where a skipped session's status comes from (the
    daemon, or its last known value), never how many panes a tick captures.
    The previous design captured every session on every tick as soon as the
    daemon looked stale — 4N capture-pane/s on the shared tmux server, 200/s
    at 50 agents — and a daemon tick slower than 5 s was enough to trip it.
    """
    n_nonfocused = sum(1 for sid in session_ids if sid != focused_id)
    if every is None:
        every = capture_rotation_period(n_nonfocused)
    every = max(1, every)
    slot = tick % every
    chosen: Set[str] = set()
    for i, sid in enumerate(session_ids):
        if sid == focused_id or sid in always_ids or i % every == slot:
            chosen.add(sid)
    return chosen


def gate_worth_a_listing(n_nonfocused: int, every: Optional[int] = None) -> bool:
    """Whether one ``list-panes`` per tick can save the fast path a command.

    The listing costs one command and can only remove the rotation's
    non-focused picks, so it pays when the rotation would issue more than
    one of them per tick: ``ceil(n_nonfocused / every) > 1``. Below that
    (up to ``every`` non-focused agents) the rotation runs as it is.
    """
    if every is None:
        every = capture_rotation_period(n_nonfocused)
    return n_nonfocused > max(1, every)


def gate_capture_ids(
    capture_ids: Set[str],
    focused_id: Optional[str],
    windows: Mapping[str, str],
    panes: Optional[Mapping[str, "PaneInfo"]],
    tracker: "PaneChangeTracker",
    now: float,
) -> Set[str]:
    """Drop the rotation's non-focused picks whose pane has not changed (audit R11).

    ``panes`` is this tick's ``list-panes -s`` (window name -> PaneInfo);
    a pick stays when the tracker finds its signature moved since the
    session's last capture, when it was never captured, for the one
    follow-up capture after a change, or on the keepalive (see
    pane_capture_gate). The focused session always stays — captured every
    tick, unconditionally — and its signature is recorded so its record is
    current when focus moves on. ``windows`` maps session id to tmux window;
    a session without one, or absent from the listing, has signature None
    (window gone): captured once, then only when it reappears. With no
    listing (``panes`` None, tmux could not answer) every pick stands and
    the tick is the plain rotation.
    """
    if panes is None:
        return set(capture_ids)
    kept: Set[str] = set()
    for sid in capture_ids:
        window = windows.get(sid)
        info = pane_for_window(panes, window) if window is not None else None
        signature = info.signature if info is not None else None
        if tracker.due(sid, signature, None, now) or sid == focused_id:
            kept.add(sid)
    return kept


def windows_needing_resize(
    current_sizes: dict,
    windows: List[str],
    width: int,
    height: int,
) -> List[str]:
    """Windows whose recorded (width, height) differs from the target.

    ``tmux resize-window`` is not a no-op at the same size: it still walks
    the resize path, fires ``window-layout-changed`` hooks and schedules a
    redraw for every client showing the window. Skipping already-correct
    windows turns the periodic reconcile sweep into a single list-windows
    call in the steady state.
    """
    return [w for w in windows if current_sizes.get(w) != (width, height)]


GIT_STATS_EVERY_SWEEPS = 3  # 5s stats sweeps between git diff/untracked scans


def should_scan_git(sweep: int, every: int = GIT_STATS_EVERY_SWEEPS) -> bool:
    """Whether stats sweep number ``sweep`` (0-based) should run git scans.

    ``git ls-files --others`` walks the whole working tree and ``git diff
    --stat HEAD`` reads every tracked file's stat — heavy on big repos, and
    the previous per-agent-every-5s cadence turned that into a periodic
    CPU/IO burst. Diff and untracked columns tolerate a slower refresh.
    """
    return sweep % max(1, every) == 0
