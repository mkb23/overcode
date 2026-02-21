"""
TUI helper functions for status display, timeline building, and business logic.

Pure formatting functions (format_*, calculate_uptime, truncate_name,
get_git_diff_stats) live in tui_formatters.py.

Re-exports from tui_formatters are provided for backward compatibility.
"""

from datetime import datetime, timedelta
from typing import List, Optional, Tuple
import statistics

from .status_constants import (
    get_status_symbol as _get_status_symbol,
    get_status_color as _get_status_color,
    get_agent_timeline_char as _get_agent_timeline_char,
    get_presence_timeline_char as _get_presence_timeline_char,
    get_presence_color as _get_presence_color,
    get_daemon_status_style as _get_daemon_status_style,
    STATUS_ASLEEP,
    STATUS_RUNNING,
    STATUS_TERMINATED,
    is_green_status,
)

# Re-export formatting functions for backward compatibility
from .tui_formatters import (  # noqa: F401
    format_interval,
    format_ago,
    format_duration,
    format_tokens,
    format_cost,
    format_budget,
    format_line_count,
    calculate_uptime,
    truncate_name,
    get_git_diff_stats,
)


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
    n = len(sorted_times)
    p5_idx = int(0.05 * (n - 1))
    p95_idx = int(0.95 * (n - 1))
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
        status: One of running, waiting_user, waiting_approval, waiting_heartbeat, etc.

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


def get_current_state_times(stats, now: Optional[datetime] = None, is_asleep: bool = False) -> Tuple[float, float, float]:
    """Get current green, non-green, and sleep times including ongoing state.

    Adds the time elapsed since the last daemon accumulation to the accumulated times.
    This provides real-time updates between daemon polling cycles.

    Args:
        stats: SessionStats object with green_time_seconds, non_green_time_seconds,
               sleep_time_seconds, last_time_accumulation, and current_state
        now: Reference time (defaults to datetime.now())
        is_asleep: If True, treat session as asleep regardless of stats.current_state.
                   This handles the case where user toggles sleep but daemon hasn't
                   updated stats.current_state yet (#141).

    Returns:
        Tuple of (green_time, non_green_time, sleep_time) in seconds
    """
    if now is None:
        now = datetime.now()

    green_time = stats.green_time_seconds
    non_green_time = stats.non_green_time_seconds
    sleep_time = getattr(stats, 'sleep_time_seconds', 0.0)

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
                # Use is_asleep parameter to override stats.current_state when user
                # has toggled sleep but daemon hasn't updated yet (#141)
                effective_state = STATUS_ASLEEP if is_asleep else stats.current_state
                if is_green_status(effective_state):
                    green_time += current_elapsed
                elif effective_state == STATUS_ASLEEP:
                    sleep_time += current_elapsed  # Accumulate sleep time (#141)
                elif effective_state != STATUS_TERMINATED:
                    non_green_time += current_elapsed
                # else: terminated - time is frozen, don't accumulate
        except (ValueError, AttributeError, TypeError):
            pass

    return green_time, non_green_time, sleep_time


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


def get_daemon_status_style(status: str) -> Tuple[str, str]:
    """Get symbol and style for daemon status.

    Args:
        status: Daemon status string

    Returns:
        Tuple of (symbol, style) for display
    """
    return _get_daemon_status_style(status)


def get_summary_content_text(
    mode: str,
    annotation: Optional[str],
    standing_instructions: Optional[str],
    standing_orders_complete: bool,
    preset_name: Optional[str],
    ai_summary_short: Optional[str],
    ai_summary_context: Optional[str],
    heartbeat_enabled: bool,
    heartbeat_paused: bool,
    heartbeat_frequency_seconds: int,
    heartbeat_instruction: Optional[str],
    summarizer_enabled: bool,
    remaining_width: int,
) -> Tuple[str, str]:
    """Compute the text and style category for summary content area.

    Pure function — no side effects, fully testable.

    Args:
        mode: Content mode (annotation, orders, ai_long, heartbeat, ai_short)
        annotation: Human annotation text
        standing_instructions: Standing orders text
        standing_orders_complete: Whether standing orders are complete
        preset_name: Standing instructions preset name
        ai_summary_short: Short AI summary
        ai_summary_context: Long AI context summary
        heartbeat_enabled: Whether heartbeat is enabled
        heartbeat_paused: Whether heartbeat is paused
        heartbeat_frequency_seconds: Heartbeat interval
        heartbeat_instruction: Heartbeat instruction text
        summarizer_enabled: Whether summarizer is enabled
        remaining_width: Available width for text

    Returns:
        Tuple of (text, style_category) where style_category is one of:
        "bold", "dim", "bold_green", "bold_cyan", "bold_yellow", "bold_magenta"
    """
    if mode == "annotation":
        if annotation:
            return f"✏️ {annotation[:remaining_width-3]}", "bold_magenta"
        return "✏️ (no annotation)", "dim"

    elif mode == "orders":
        if standing_instructions:
            if standing_orders_complete:
                return f"🎯✓ {standing_instructions[:remaining_width-4]}", "bold_green"
            elif preset_name:
                prefix = f"🎯 {preset_name}: "
                return f"{prefix}{standing_instructions[:remaining_width-len(prefix)]}"[:remaining_width], "bold_cyan"
            else:
                return f"🎯 {standing_instructions[:remaining_width-3]}", "bold_yellow"
        return "🎯 (no standing orders)", "dim"

    elif mode == "ai_long":
        if ai_summary_context:
            return f"📖 {ai_summary_context[:remaining_width-3]}", "bold"
        elif not summarizer_enabled:
            return "📖 (summarizer disabled - press 'a')", "dim"
        return "📖 (awaiting context...)", "dim"

    elif mode == "heartbeat":
        if heartbeat_enabled:
            freq_str = format_duration(heartbeat_frequency_seconds)
            hb_text = f"💓 {freq_str}: {heartbeat_instruction}"[:remaining_width]
            if heartbeat_paused:
                return hb_text, "dim"
            return hb_text, "bold_magenta"
        return "💓 (no heartbeat configured - press H)", "dim"

    else:
        # ai_short (default)
        if ai_summary_short:
            return f"💬 {ai_summary_short[:remaining_width-3]}", "bold"
        elif not summarizer_enabled:
            return "💬 (summarizer disabled - press 'a')", "dim"
        return "💬 (awaiting summary...)", "dim"


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
