"""
Pure helper functions for TUI rendering.

These functions are extracted for testability - they perform
formatting and calculations without requiring Textual or other
UI dependencies.
"""

from datetime import datetime, timedelta
from typing import List, Optional, Tuple
import statistics
import subprocess

from .status_constants import (
    get_status_symbol as _get_status_symbol,
    get_status_color as _get_status_color,
    get_agent_timeline_char as _get_agent_timeline_char,
    get_presence_timeline_char as _get_presence_timeline_char,
    get_presence_color as _get_presence_color,
    get_daemon_status_style as _get_daemon_status_style,
    STATUS_RUNNING,
    STATUS_TERMINATED,
)


def format_interval(seconds: int) -> str:
    """Format integer interval to human readable (s/m/h) without decimals.

    Use for displaying fixed intervals like polling rates: "@30s", "@1m"
    For durations with precision (e.g., work times), use format_duration().

    Examples: 30 -> "30s", 60 -> "1m", 3600 -> "1h"
    """
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m"
    else:
        return f"{seconds // 3600}h"


def format_ago(dt: Optional[datetime], now: Optional[datetime] = None) -> str:
    """Format datetime as time ago string.

    Args:
        dt: The datetime to format
        now: Reference time (defaults to datetime.now())

    Returns:
        String like "30s ago", "5m ago", "2.5h ago", or "never"
    """
    if not dt:
        return "never"
    if now is None:
        now = datetime.now()
    delta = (now - dt).total_seconds()
    if delta < 60:
        return f"{int(delta)}s ago"
    elif delta < 3600:
        return f"{int(delta // 60)}m ago"
    else:
        return f"{delta / 3600:.1f}h ago"


def format_duration(seconds: float) -> str:
    """Format duration in seconds to human readable (s/m/h/d).

    Shows one decimal place for all units except seconds.
    Examples: 45s, 6.3m, 2.5h, 1.2d
    """
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.1f}m"
    elif seconds < 86400:  # Less than 1 day
        return f"{seconds/3600:.1f}h"
    else:
        return f"{seconds/86400:.1f}d"


def format_tokens(tokens: int) -> str:
    """Format token count to human readable (K/M).

    Args:
        tokens: Number of tokens

    Returns:
        Formatted string like "1.2K", "3.5M", or "500" for small counts
    """
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}M"
    elif tokens >= 1_000:
        return f"{tokens / 1_000:.1f}K"
    else:
        return str(tokens)


def format_line_count(count: int) -> str:
    """Format line count (insertions/deletions) to human readable (K/M).

    Args:
        count: Number of lines

    Returns:
        Formatted string like "173K", "1.2M", or "500" for small counts.
        Uses no decimal for K values to keep display compact.
    """
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    elif count >= 1_000:
        return f"{count // 1_000}K"
    else:
        return str(count)


def calculate_uptime(start_time: str, now: Optional[datetime] = None) -> str:
    """Calculate uptime from ISO format start_time.

    Args:
        start_time: ISO format datetime string
        now: Reference time (defaults to datetime.now())

    Returns:
        String like "30m", "4.5h", "2.5d", or "0m" on error
    """
    try:
        if now is None:
            now = datetime.now()
        start = datetime.fromisoformat(start_time)
        delta = now - start
        hours = delta.total_seconds() / 3600
        if hours < 1:
            minutes = delta.total_seconds() / 60
            return f"{int(minutes)}m"
        elif hours < 24:
            return f"{hours:.1f}h"
        else:
            days = hours / 24
            return f"{days:.1f}d"
    except (ValueError, AttributeError, TypeError):
        return "0m"


def calculate_percentiles(times: List[float]) -> Tuple[float, float, float]:
    """Calculate mean, 5th, and 95th percentile of operation times.

    Args:
        times: List of operation times in seconds

    Returns:
        Tuple of (mean, p5, p95)
    """
    if not times:
        return 0.0, 0.0, 0.0

    mean_time = statistics.mean(times)

    if len(times) < 2:
        return mean_time, mean_time, mean_time

    sorted_times = sorted(times)
    p5_idx = int(len(sorted_times) * 0.05)
    p95_idx = int(len(sorted_times) * 0.95)
    p5 = sorted_times[p5_idx]
    p95 = sorted_times[p95_idx]

    return mean_time, p5, p95


def presence_state_to_char(state: int) -> str:
    """Convert presence state to timeline character.

    Args:
        state: 1=locked/sleep, 2=inactive, 3=active

    Returns:
        Block character for timeline visualization
    """
    return _get_presence_timeline_char(state)


def agent_status_to_char(status: str) -> str:
    """Convert agent status to timeline character.

    Args:
        status: One of running, no_instructions, waiting_supervisor, waiting_user

    Returns:
        Block character for timeline visualization
    """
    return _get_agent_timeline_char(status)


def status_to_color(status: str) -> str:
    """Map agent status to display color name.

    Args:
        status: Agent status string

    Returns:
        Color name for Rich styling
    """
    return _get_status_color(status)


def get_standing_orders_indicator(session) -> str:
    """Get standing orders display indicator.

    Args:
        session: Session object with standing_instructions and standing_orders_complete

    Returns:
        Emoji indicator: "➖" (none), "📋" (active), "✓" (complete)
    """
    if not session.standing_instructions:
        return "➖"
    elif session.standing_orders_complete:
        return "✓"
    else:
        return "📋"


def get_current_state_times(stats, now: Optional[datetime] = None) -> Tuple[float, float]:
    """Get current green and non-green times including ongoing state.

    Adds the time elapsed since the last daemon accumulation to the accumulated times.
    This provides real-time updates between daemon polling cycles.

    Args:
        stats: SessionStats object with green_time_seconds, non_green_time_seconds,
               last_time_accumulation, and current_state
        now: Reference time (defaults to datetime.now())

    Returns:
        Tuple of (green_time, non_green_time) in seconds
    """
    if now is None:
        now = datetime.now()

    green_time = stats.green_time_seconds
    non_green_time = stats.non_green_time_seconds

    # Add elapsed time since the daemon last accumulated times
    # Use last_time_accumulation (when daemon last updated), NOT state_since (when state started)
    # This prevents double-counting: daemon already accumulated time up to last_time_accumulation
    time_anchor = stats.last_time_accumulation or stats.state_since
    if time_anchor:
        try:
            anchor_time = datetime.fromisoformat(time_anchor)
            current_elapsed = (now - anchor_time).total_seconds()

            # Only add positive elapsed time
            if current_elapsed > 0:
                if stats.current_state == STATUS_RUNNING:
                    green_time += current_elapsed
                elif stats.current_state != STATUS_TERMINATED:
                    # Only count non-green time for non-terminated states
                    non_green_time += current_elapsed
                # else: terminated state - time is frozen, don't accumulate
        except (ValueError, AttributeError, TypeError):
            pass

    return green_time, non_green_time


def build_timeline_slots(
    history: list,
    width: int,
    hours: float,
    now: Optional[datetime] = None
) -> dict:
    """Build a dictionary mapping slot indices to states from history data.

    Args:
        history: List of (timestamp, state) tuples
        width: Number of slots in the timeline
        hours: Number of hours the timeline covers
        now: Reference time (defaults to datetime.now())

    Returns:
        Dict mapping slot index to state value
    """
    if now is None:
        now = datetime.now()

    if not history:
        return {}

    start_time = now - timedelta(hours=hours)
    slot_duration_sec = (hours * 3600) / width
    slot_states = {}

    for ts, state in history:
        if ts < start_time:
            continue
        elapsed = (ts - start_time).total_seconds()
        slot_idx = int(elapsed / slot_duration_sec)
        if 0 <= slot_idx < width:
            slot_states[slot_idx] = state

    return slot_states


def build_timeline_string(
    slot_states: dict,
    width: int,
    state_to_char: callable
) -> str:
    """Build a timeline string from slot states.

    Args:
        slot_states: Dict mapping slot index to state
        width: Number of characters in timeline
        state_to_char: Function to convert state to display character

    Returns:
        String of width characters representing the timeline
    """
    timeline = []
    for i in range(width):
        if i in slot_states:
            timeline.append(state_to_char(slot_states[i]))
        else:
            timeline.append("─")
    return "".join(timeline)


def get_status_symbol(status: str) -> Tuple[str, str]:
    """Get status emoji and base style for agent status.

    Args:
        status: Agent status string

    Returns:
        Tuple of (emoji, color) for the status
    """
    return _get_status_symbol(status)


def get_presence_color(state: int) -> str:
    """Get color for presence state.

    Args:
        state: Presence state (1=locked/sleep, 2=inactive, 3=active)

    Returns:
        Color name for Rich styling
    """
    return _get_presence_color(state)


def get_agent_timeline_color(status: str) -> str:
    """Get color for agent status in timeline.

    Args:
        status: Agent status string

    Returns:
        Color name for Rich styling
    """
    return _get_status_color(status)


def style_pane_line(line: str) -> Tuple[str, str]:
    """Determine styling for a pane content line.

    Args:
        line: The line content to style

    Returns:
        Tuple of (prefix_style, content_style) color names
    """
    if line.startswith('✓') or 'success' in line.lower():
        return ("bold green", "green")
    elif line.startswith('✗') or 'error' in line.lower() or 'fail' in line.lower():
        return ("bold red", "red")
    elif line.startswith('>') or line.startswith('$') or line.startswith('❯'):
        return ("bold cyan", "bold white")
    else:
        return ("cyan", "white")  # Punchier bar color


def truncate_name(name: str, max_len: int = 14) -> str:
    """Truncate and pad name for display.

    Args:
        name: Name to truncate
        max_len: Maximum length (default 14 for timeline view)

    Returns:
        Name truncated and left-justified to max_len
    """
    return name[:max_len].ljust(max_len)


def get_daemon_status_style(status: str) -> Tuple[str, str]:
    """Get symbol and style for daemon status.

    Args:
        status: Daemon status string

    Returns:
        Tuple of (symbol, style) for display
    """
    return _get_daemon_status_style(status)


def calculate_safe_break_duration(sessions: list, now: Optional[datetime] = None) -> Optional[float]:
    """Calculate how long you can be AFK before 50%+ of agents need attention.

    For each running agent:
    - Get their median work time (p50 autonomous operation time)
    - Subtract time already spent in current running state
    - That gives expected time until they need attention

    Returns the duration (in seconds) until 50%+ of agents will turn red,
    or None if no running agents or insufficient data.

    Args:
        sessions: List of SessionDaemonState objects
        now: Reference time (defaults to datetime.now())

    Returns:
        Safe break duration in seconds, or None if cannot calculate
    """
    if now is None:
        now = datetime.now()

    # Get running agents with valid median work times
    time_until_attention = []
    for s in sessions:
        # Only consider running agents
        if s.current_status != "running":
            continue

        # Need median work time data
        if s.median_work_time <= 0:
            continue

        # Calculate time in current state
        time_in_state = 0.0
        if s.status_since:
            try:
                state_start = datetime.fromisoformat(s.status_since)
                time_in_state = (now - state_start).total_seconds()
            except (ValueError, TypeError):
                pass

        # Expected time until needing attention
        remaining = s.median_work_time - time_in_state
        # If already past median, they could need attention any moment (0 remaining)
        time_until_attention.append(max(0, remaining))

    if not time_until_attention:
        return None

    # Sort by time until attention
    time_until_attention.sort()

    # Find when 50%+ will need attention
    # If we have N agents, we need to find when ceil(N/2) have turned red
    half_point = (len(time_until_attention) + 1) // 2
    return time_until_attention[half_point - 1]


def get_git_diff_stats(directory: str) -> Optional[Tuple[int, int, int]]:
    """Get git diff stats for a directory.

    Args:
        directory: Path to the git repository

    Returns:
        Tuple of (files_changed, insertions, deletions) or None if not a git repo
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode != 0:
            return None

        # Parse the last line which looks like:
        # "3 files changed, 10 insertions(+), 5 deletions(-)"
        # or just "1 file changed, 2 insertions(+)"
        lines = result.stdout.strip().split('\n')
        if not lines or not lines[-1]:
            return (0, 0, 0)  # No changes

        summary = lines[-1]
        files = 0
        insertions = 0
        deletions = 0

        import re
        files_match = re.search(r'(\d+) files? changed', summary)
        ins_match = re.search(r'(\d+) insertions?', summary)
        del_match = re.search(r'(\d+) deletions?', summary)

        if files_match:
            files = int(files_match.group(1))
        if ins_match:
            insertions = int(ins_match.group(1))
        if del_match:
            deletions = int(del_match.group(1))

        return (files, insertions, deletions)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
