"""
Unit tests for Monitor Daemon core business logic.

These test the pure functions that have no I/O dependencies.
"""

import pytest
from datetime import datetime, timedelta

from overcode.monitor_daemon_core import (
    TimeAccumulationResult,
    calculate_time_accumulation,
    calculate_cost_estimate,
    calculate_total_tokens,
    calculate_median,
    calculate_green_percentage,
    aggregate_session_stats,
    should_sync_stats,
    parse_datetime_safe,
)


class TestCalculateTimeAccumulation:
    """Tests for calculate_time_accumulation function."""

    def test_running_status_accumulates_green_time(self):
        """Running status should add elapsed time to green."""
        result = calculate_time_accumulation(
            current_status="running",
            previous_status="running",
            elapsed_seconds=60.0,
            current_green=300.0,
            current_non_green=100.0,
            session_start=datetime(2024, 1, 1, 10, 0, 0),
            now=datetime(2024, 1, 1, 10, 10, 0),
        )

        assert result.green_seconds == 360.0  # 300 + 60
        assert result.non_green_seconds == 100.0  # unchanged
        assert result.state_changed is False
        assert result.was_capped is False

    def test_waiting_user_accumulates_non_green_time(self):
        """Waiting user status should add elapsed time to non-green."""
        result = calculate_time_accumulation(
            current_status="waiting_user",
            previous_status="waiting_user",
            elapsed_seconds=30.0,
            current_green=300.0,
            current_non_green=100.0,
            session_start=datetime(2024, 1, 1, 10, 0, 0),
            now=datetime(2024, 1, 1, 10, 10, 0),
        )

        assert result.green_seconds == 300.0  # unchanged
        assert result.non_green_seconds == 130.0  # 100 + 30

    def test_terminated_status_does_not_accumulate(self):
        """Terminated status should not accumulate any time."""
        result = calculate_time_accumulation(
            current_status="terminated",
            previous_status="terminated",
            elapsed_seconds=60.0,
            current_green=300.0,
            current_non_green=100.0,
            session_start=datetime(2024, 1, 1, 10, 0, 0),
            now=datetime(2024, 1, 1, 10, 10, 0),
        )

        assert result.green_seconds == 300.0  # unchanged
        assert result.non_green_seconds == 100.0  # unchanged

    def test_asleep_status_does_not_accumulate(self):
        """Asleep status should not accumulate any time."""
        result = calculate_time_accumulation(
            current_status="asleep",
            previous_status="asleep",
            elapsed_seconds=60.0,
            current_green=300.0,
            current_non_green=100.0,
            session_start=datetime(2024, 1, 1, 10, 0, 0),
            now=datetime(2024, 1, 1, 10, 10, 0),
        )

        assert result.green_seconds == 300.0
        assert result.non_green_seconds == 100.0

    def test_state_changed_detected(self):
        """Should detect when state changes."""
        result = calculate_time_accumulation(
            current_status="waiting_user",
            previous_status="running",
            elapsed_seconds=10.0,
            current_green=300.0,
            current_non_green=100.0,
            session_start=datetime(2024, 1, 1, 10, 0, 0),
            now=datetime(2024, 1, 1, 10, 10, 0),
        )

        assert result.state_changed is True

    def test_state_unchanged_when_same(self):
        """Should not report state change when status is same."""
        result = calculate_time_accumulation(
            current_status="running",
            previous_status="running",
            elapsed_seconds=10.0,
            current_green=300.0,
            current_non_green=100.0,
            session_start=datetime(2024, 1, 1, 10, 0, 0),
            now=datetime(2024, 1, 1, 10, 10, 0),
        )

        assert result.state_changed is False

    def test_first_observation_no_state_change(self):
        """First observation (previous_status=None) should not report change."""
        result = calculate_time_accumulation(
            current_status="running",
            previous_status=None,
            elapsed_seconds=10.0,
            current_green=0.0,
            current_non_green=0.0,
            session_start=datetime(2024, 1, 1, 10, 0, 0),
            now=datetime(2024, 1, 1, 10, 10, 0),
        )

        assert result.state_changed is False

    def test_caps_time_at_uptime(self):
        """Should cap accumulated time to session uptime."""
        # Session started 10 min ago (600s), but we have 800s accumulated
        result = calculate_time_accumulation(
            current_status="running",
            previous_status="running",
            elapsed_seconds=100.0,
            current_green=700.0,  # Already more than uptime
            current_non_green=100.0,
            session_start=datetime(2024, 1, 1, 10, 0, 0),
            now=datetime(2024, 1, 1, 10, 10, 0),  # 10 min = 600s
        )

        # Total should be capped to ~660s (600 * 1.1 tolerance)
        total = result.green_seconds + result.non_green_seconds
        assert total <= 660
        assert result.was_capped is True

    def test_no_cap_when_within_tolerance(self):
        """Should not cap when within tolerance."""
        result = calculate_time_accumulation(
            current_status="running",
            previous_status="running",
            elapsed_seconds=10.0,
            current_green=300.0,
            current_non_green=100.0,
            session_start=datetime(2024, 1, 1, 10, 0, 0),
            now=datetime(2024, 1, 1, 10, 10, 0),  # 600s uptime
        )

        assert result.was_capped is False

    def test_zero_elapsed_returns_unchanged(self):
        """Zero elapsed time should return unchanged values."""
        result = calculate_time_accumulation(
            current_status="running",
            previous_status="running",
            elapsed_seconds=0.0,
            current_green=300.0,
            current_non_green=100.0,
            session_start=datetime(2024, 1, 1, 10, 0, 0),
            now=datetime(2024, 1, 1, 10, 10, 0),
        )

        assert result.green_seconds == 300.0
        assert result.non_green_seconds == 100.0

    def test_negative_elapsed_returns_unchanged(self):
        """Negative elapsed time should return unchanged values."""
        result = calculate_time_accumulation(
            current_status="running",
            previous_status="running",
            elapsed_seconds=-10.0,
            current_green=300.0,
            current_non_green=100.0,
            session_start=datetime(2024, 1, 1, 10, 0, 0),
            now=datetime(2024, 1, 1, 10, 10, 0),
        )

        assert result.green_seconds == 300.0
        assert result.non_green_seconds == 100.0

    def test_none_session_start_skips_cap(self):
        """Should skip cap check when session_start is None."""
        result = calculate_time_accumulation(
            current_status="running",
            previous_status="running",
            elapsed_seconds=10000.0,  # Huge elapsed time
            current_green=300.0,
            current_non_green=100.0,
            session_start=None,
            now=datetime(2024, 1, 1, 10, 10, 0),
        )

        # Should accumulate without capping
        assert result.green_seconds == 10300.0
        assert result.was_capped is False


class TestCalculateCostEstimate:
    """Tests for calculate_cost_estimate function."""

    def test_zero_tokens_zero_cost(self):
        """Zero tokens should result in zero cost."""
        cost = calculate_cost_estimate(0, 0, 0, 0)
        assert cost == 0.0

    def test_input_tokens_only(self):
        """Input tokens: $15/MTok (Opus 4.5 default)."""
        cost = calculate_cost_estimate(
            input_tokens=1_000_000,
            output_tokens=0,
        )
        assert cost == 15.0

    def test_output_tokens_only(self):
        """Output tokens: $75/MTok (Opus 4.5 default)."""
        cost = calculate_cost_estimate(
            input_tokens=0,
            output_tokens=1_000_000,
        )
        assert cost == 75.0

    def test_cache_creation_tokens(self):
        """Cache creation: $18.75/MTok (Opus 4.5 default)."""
        cost = calculate_cost_estimate(
            input_tokens=0,
            output_tokens=0,
            cache_creation_tokens=1_000_000,
        )
        assert cost == 18.75

    def test_cache_read_tokens(self):
        """Cache read: $1.50/MTok (Opus 4.5 default)."""
        cost = calculate_cost_estimate(
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=1_000_000,
        )
        assert cost == 1.50

    def test_custom_pricing(self):
        """Should use custom pricing when provided."""
        cost = calculate_cost_estimate(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            price_input=3.0,
            price_output=15.0,
        )
        assert cost == 18.0  # 3 + 15

    def test_mixed_tokens(self):
        """Combined token types (Opus 4.5 pricing)."""
        cost = calculate_cost_estimate(
            input_tokens=500_000,   # $7.50
            output_tokens=100_000,  # $7.50
            cache_creation_tokens=200_000,  # $3.75
            cache_read_tokens=1_000_000,    # $1.50
        )
        assert abs(cost - 20.25) < 0.001

    def test_realistic_session(self):
        """Realistic session with typical token counts (Opus 4.5 pricing)."""
        cost = calculate_cost_estimate(
            input_tokens=50_000,
            output_tokens=10_000,
        )
        # 50k * 15/M + 10k * 75/M = 0.75 + 0.75 = 1.50
        assert abs(cost - 1.50) < 0.001


class TestCalculateTotalTokens:
    """Tests for calculate_total_tokens function."""

    def test_zero_tokens(self):
        """Zero inputs should return zero."""
        assert calculate_total_tokens(0, 0, 0, 0) == 0

    def test_sums_all_token_types(self):
        """Should sum all token types."""
        total = calculate_total_tokens(
            input_tokens=100,
            output_tokens=50,
            cache_creation_tokens=25,
            cache_read_tokens=10,
        )
        assert total == 185

    def test_defaults_cache_tokens_to_zero(self):
        """Cache tokens should default to zero."""
        total = calculate_total_tokens(input_tokens=100, output_tokens=50)
        assert total == 150


class TestCalculateMedian:
    """Tests for calculate_median function."""

    def test_empty_list_returns_zero(self):
        """Empty list should return 0.0."""
        assert calculate_median([]) == 0.0

    def test_single_value(self):
        """Single value should return that value."""
        assert calculate_median([42.0]) == 42.0

    def test_odd_count_returns_middle(self):
        """Odd count should return middle value."""
        assert calculate_median([10.0, 20.0, 30.0]) == 20.0

    def test_even_count_returns_average_of_middle(self):
        """Even count should return average of middle two."""
        assert calculate_median([10.0, 20.0, 30.0, 40.0]) == 25.0

    def test_unsorted_input(self):
        """Should handle unsorted input."""
        assert calculate_median([30.0, 10.0, 20.0]) == 20.0

    def test_duplicate_values(self):
        """Should handle duplicate values."""
        assert calculate_median([5.0, 5.0, 5.0]) == 5.0


class TestCalculateGreenPercentage:
    """Tests for calculate_green_percentage function."""

    def test_zero_total_returns_zero(self):
        """Zero total time should return 0%."""
        assert calculate_green_percentage(0.0, 0.0) == 0

    def test_all_green(self):
        """All green time should return 100%."""
        assert calculate_green_percentage(100.0, 0.0) == 100

    def test_all_non_green(self):
        """All non-green time should return 0%."""
        assert calculate_green_percentage(0.0, 100.0) == 0

    def test_fifty_percent(self):
        """Equal green/non-green should return 50%."""
        assert calculate_green_percentage(50.0, 50.0) == 50

    def test_seventy_five_percent(self):
        """75% green should return 75."""
        assert calculate_green_percentage(75.0, 25.0) == 75

    def test_returns_integer(self):
        """Should return integer percentage."""
        result = calculate_green_percentage(33.33, 66.67)
        assert isinstance(result, int)


class TestAggregateSessionStats:
    """Tests for aggregate_session_stats function."""

    def test_empty_list(self):
        """Empty list should return zeros."""
        green_count, total_green, total_non_green, active = aggregate_session_stats([])
        assert green_count == 0
        assert total_green == 0.0
        assert total_non_green == 0.0
        assert active == 0

    def test_single_running_session(self):
        """Single running session."""
        sessions = [
            {'status': 'running', 'green_time_seconds': 100.0, 'non_green_time_seconds': 50.0}
        ]
        green_count, total_green, total_non_green, active = aggregate_session_stats(sessions)

        assert green_count == 1
        assert total_green == 100.0
        assert total_non_green == 50.0
        assert active == 1

    def test_mixed_sessions(self):
        """Multiple sessions with different statuses."""
        sessions = [
            {'status': 'running', 'green_time_seconds': 100.0, 'non_green_time_seconds': 50.0},
            {'status': 'waiting_user', 'green_time_seconds': 80.0, 'non_green_time_seconds': 20.0},
            {'status': 'running', 'green_time_seconds': 60.0, 'non_green_time_seconds': 40.0},
        ]
        green_count, total_green, total_non_green, active = aggregate_session_stats(sessions)

        assert green_count == 2  # Two running
        assert total_green == 240.0  # 100 + 80 + 60
        assert total_non_green == 110.0  # 50 + 20 + 40
        assert active == 3

    def test_excludes_asleep_sessions(self):
        """Asleep sessions should be excluded from all counts."""
        sessions = [
            {'status': 'running', 'green_time_seconds': 100.0, 'non_green_time_seconds': 50.0},
            {'status': 'running', 'green_time_seconds': 200.0, 'non_green_time_seconds': 100.0, 'is_asleep': True},
        ]
        green_count, total_green, total_non_green, active = aggregate_session_stats(sessions)

        assert green_count == 1  # Only non-asleep running
        assert total_green == 100.0  # Excludes asleep session
        assert total_non_green == 50.0
        assert active == 1

    def test_missing_fields_default_to_zero(self):
        """Missing fields should default to zero/empty."""
        sessions = [
            {'status': 'running'},  # Missing time fields
        ]
        green_count, total_green, total_non_green, active = aggregate_session_stats(sessions)

        assert green_count == 1
        assert total_green == 0.0
        assert total_non_green == 0.0
        assert active == 1


class TestShouldSyncStats:
    """Tests for should_sync_stats function."""

    def test_none_last_sync_returns_true(self):
        """Should sync when never synced before."""
        result = should_sync_stats(
            last_sync=None,
            now=datetime.now(),
            interval_seconds=60.0,
        )
        assert result is True

    def test_interval_not_elapsed(self):
        """Should not sync when interval hasn't elapsed."""
        now = datetime.now()
        last_sync = now - timedelta(seconds=30)

        result = should_sync_stats(
            last_sync=last_sync,
            now=now,
            interval_seconds=60.0,
        )
        assert result is False

    def test_interval_elapsed(self):
        """Should sync when interval has elapsed."""
        now = datetime.now()
        last_sync = now - timedelta(seconds=90)

        result = should_sync_stats(
            last_sync=last_sync,
            now=now,
            interval_seconds=60.0,
        )
        assert result is True

    def test_exact_interval(self):
        """Should sync when exactly at interval."""
        now = datetime.now()
        last_sync = now - timedelta(seconds=60)

        result = should_sync_stats(
            last_sync=last_sync,
            now=now,
            interval_seconds=60.0,
        )
        assert result is True


class TestParseDatetimeSafe:
    """Tests for parse_datetime_safe function."""

    def test_none_returns_none(self):
        """None input should return None."""
        assert parse_datetime_safe(None) is None

    def test_valid_iso_datetime(self):
        """Valid ISO datetime should be parsed."""
        result = parse_datetime_safe("2024-01-15T10:30:00")
        assert result is not None
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15
        assert result.hour == 10
        assert result.minute == 30

    def test_invalid_string_returns_none(self):
        """Invalid string should return None."""
        assert parse_datetime_safe("not-a-date") is None

    def test_empty_string_returns_none(self):
        """Empty string should return None."""
        assert parse_datetime_safe("") is None

    def test_partial_date_may_fail(self):
        """Partial date may or may not parse depending on format."""
        # This tests that we handle errors gracefully
        result = parse_datetime_safe("2024-01")
        # Could be None or parsed depending on Python version
        assert result is None or isinstance(result, datetime)


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
