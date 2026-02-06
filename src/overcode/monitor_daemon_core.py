"""
Pure business logic for Monitor Daemon.

These functions contain no I/O and are fully unit-testable.
They are used by MonitorDaemon but can be tested independently.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Tuple

from .status_constants import STATUS_RUNNING, STATUS_RUNNING_HEARTBEAT, STATUS_TERMINATED, STATUS_ASLEEP, is_green_status


@dataclass
class TimeAccumulationResult:
    """Result of time accumulation calculation."""
    green_seconds: float
    non_green_seconds: float
    sleep_seconds: float  # Track sleep time separately (#141)
    state_changed: bool
    was_capped: bool  # True if time was capped to uptime


def calculate_time_accumulation(
    current_status: str,
    previous_status: Optional[str],
    elapsed_seconds: float,
    current_green: float,
    current_non_green: float,
    current_sleep: float,
    session_start: Optional[datetime],
    now: datetime,
    tolerance: float = 1.1,  # 10% tolerance for timing jitter
) -> TimeAccumulationResult:
    """Calculate accumulated green/non-green/sleep time based on current status.

    Pure function - no side effects, fully testable.

    Args:
        current_status: Current agent status (running, waiting_user, etc.)
        previous_status: Previous status (None if first observation)
        elapsed_seconds: Seconds since last observation
        current_green: Current accumulated green time
        current_non_green: Current accumulated non-green time
        current_sleep: Current accumulated sleep time (#141)
        session_start: When the session started (for cap calculation)
        now: Current time
        tolerance: How much accumulated time can exceed uptime (1.1 = 10%)

    Returns:
        TimeAccumulationResult with updated times and metadata
    """
    if elapsed_seconds <= 0:
        return TimeAccumulationResult(
            green_seconds=current_green,
            non_green_seconds=current_non_green,
            sleep_seconds=current_sleep,
            state_changed=False,
            was_capped=False,
        )

    green = current_green
    non_green = current_non_green
    sleep = current_sleep

    # Accumulate based on status
    if current_status in (STATUS_RUNNING, STATUS_RUNNING_HEARTBEAT):
        green += elapsed_seconds
    elif current_status == STATUS_ASLEEP:
        sleep += elapsed_seconds  # Track sleep time separately (#141)
    elif current_status != STATUS_TERMINATED:
        non_green += elapsed_seconds
    # else: terminated - don't accumulate time

    # Cap accumulated time to session uptime
    was_capped = False
    if session_start is not None:
        max_allowed = (now - session_start).total_seconds()
        total_accumulated = green + non_green + sleep

        if total_accumulated > max_allowed * tolerance:
            # Scale down to sane values
            ratio = max_allowed / total_accumulated if total_accumulated > 0 else 1.0
            green = green * ratio
            non_green = non_green * ratio
            sleep = sleep * ratio
            was_capped = True

        # Clamp individual components so they don't exceed uptime (#154)
        # Even if total is within tolerance, individual components must be sane
        green = min(green, max_allowed)
        non_green = min(non_green, max_allowed - green)
        sleep = min(sleep, max_allowed - green - non_green)

    state_changed = previous_status is not None and previous_status != current_status

    return TimeAccumulationResult(
        green_seconds=green,
        non_green_seconds=non_green,
        sleep_seconds=sleep,
        state_changed=state_changed,
        was_capped=was_capped,
    )


def calculate_cost_estimate(
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    price_input: float = 15.0,
    price_output: float = 75.0,
    price_cache_write: float = 18.75,
    price_cache_read: float = 1.50,
) -> float:
    """Calculate estimated cost from token counts.

    Pure function - no side effects, fully testable.

    Args:
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        cache_creation_tokens: Number of cache creation tokens
        cache_read_tokens: Number of cache read tokens
        price_input: Price per million input tokens (default: Opus 4.5)
        price_output: Price per million output tokens (default: Opus 4.5)
        price_cache_write: Price per million cache write tokens (default: Opus 4.5)
        price_cache_read: Price per million cache read tokens (default: Opus 4.5)

    Returns:
        Estimated cost in USD
    """
    return (
        (input_tokens / 1_000_000) * price_input +
        (output_tokens / 1_000_000) * price_output +
        (cache_creation_tokens / 1_000_000) * price_cache_write +
        (cache_read_tokens / 1_000_000) * price_cache_read
    )


def calculate_total_tokens(
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> int:
    """Calculate total token count.

    Pure function - no side effects, fully testable.
    """
    return input_tokens + output_tokens + cache_creation_tokens + cache_read_tokens


def calculate_median(values: List[float]) -> float:
    """Calculate median of a list of values.

    Pure function - no side effects, fully testable.

    Args:
        values: List of numeric values

    Returns:
        Median value, or 0.0 if list is empty
    """
    if not values:
        return 0.0
    sorted_values = sorted(values)
    n = len(sorted_values)
    if n % 2 == 0:
        return (sorted_values[n // 2 - 1] + sorted_values[n // 2]) / 2
    return sorted_values[n // 2]


def calculate_green_percentage(green_seconds: float, non_green_seconds: float) -> int:
    """Calculate percentage of time spent in green (running) state.

    Pure function - no side effects, fully testable.

    Args:
        green_seconds: Total green time
        non_green_seconds: Total non-green time

    Returns:
        Integer percentage (0-100)
    """
    total = green_seconds + non_green_seconds
    if total <= 0:
        return 0
    return int((green_seconds / total) * 100)


def aggregate_session_stats(
    sessions: List[dict],
) -> Tuple[int, float, float, int]:
    """Aggregate statistics across multiple sessions.

    Pure function - no side effects, fully testable.

    Args:
        sessions: List of session dicts with 'status', 'green_time_seconds',
                  'non_green_time_seconds', 'is_asleep' keys

    Returns:
        Tuple of (green_count, total_green_time, total_non_green_time, active_count)
    """
    green_count = 0
    total_green = 0.0
    total_non_green = 0.0
    active_count = 0

    for session in sessions:
        # Skip asleep sessions
        if session.get('is_asleep', False):
            continue

        active_count += 1
        status = session.get('status', '')

        if is_green_status(status):
            green_count += 1

        total_green += session.get('green_time_seconds', 0.0)
        total_non_green += session.get('non_green_time_seconds', 0.0)

    return green_count, total_green, total_non_green, active_count


def should_sync_stats(
    last_sync: Optional[datetime],
    now: datetime,
    interval_seconds: float,
) -> bool:
    """Determine if stats should be synced based on interval.

    Pure function - no side effects, fully testable.

    Args:
        last_sync: Time of last sync (None if never synced)
        now: Current time
        interval_seconds: Minimum seconds between syncs

    Returns:
        True if sync should occur
    """
    if last_sync is None:
        return True
    return (now - last_sync).total_seconds() >= interval_seconds


def parse_datetime_safe(value: Optional[str]) -> Optional[datetime]:
    """Safely parse an ISO datetime string.

    Pure function - no side effects, fully testable.

    Args:
        value: ISO format datetime string, or None

    Returns:
        Parsed datetime, or None if parsing fails
    """
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
