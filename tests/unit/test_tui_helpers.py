"""
Unit tests for TUI helper functions.

These tests verify the pure helper functions used in TUI rendering
without requiring any UI framework dependencies.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock

from overcode.tui_helpers import (
    format_interval,
    format_ago,
    format_duration,
    format_tokens,
    format_cost,
    format_line_count,
    calculate_uptime,
    calculate_percentiles,
    presence_state_to_char,
    agent_status_to_char,
    status_to_color,
    get_standing_orders_indicator,
    get_current_state_times,
    build_timeline_slots,
    build_timeline_string,
    get_status_symbol,
    get_presence_color,
    get_agent_timeline_color,
    style_pane_line,
    truncate_name,
    get_daemon_status_style,
    calculate_safe_break_duration,
    get_git_diff_stats,
)


class TestFormatInterval:
    """Tests for format_interval function."""

    def test_seconds(self):
        """Should format seconds."""
        assert format_interval(30) == "30s"
        assert format_interval(59) == "59s"

    def test_minutes(self):
        """Should format minutes."""
        assert format_interval(60) == "1m"
        assert format_interval(120) == "2m"
        assert format_interval(3599) == "59m"

    def test_hours(self):
        """Should format hours."""
        assert format_interval(3600) == "1h"
        assert format_interval(7200) == "2h"


class TestFormatAgo:
    """Tests for format_ago function."""

    def test_none_returns_never(self):
        """Should return 'never' for None."""
        assert format_ago(None) == "never"

    def test_seconds_ago(self):
        """Should format seconds ago."""
        now = datetime.now()
        dt = now - timedelta(seconds=30)
        assert format_ago(dt, now) == "30s ago"

    def test_minutes_ago(self):
        """Should format minutes ago."""
        now = datetime.now()
        dt = now - timedelta(minutes=5)
        assert format_ago(dt, now) == "5m ago"

    def test_hours_ago(self):
        """Should format hours ago."""
        now = datetime.now()
        dt = now - timedelta(hours=2, minutes=30)
        assert format_ago(dt, now) == "2.5h ago"

    def test_default_now_parameter(self):
        """Should use datetime.now() when now parameter is not provided."""
        # Use a recent time so we get a reasonable "ago" string
        dt = datetime.now() - timedelta(seconds=5)
        result = format_ago(dt)  # No explicit now parameter
        assert "ago" in result


class TestFormatDuration:
    """Tests for format_duration function."""

    def test_seconds(self):
        """Should format seconds as integers."""
        assert format_duration(45) == "45s"
        assert format_duration(0) == "0s"

    def test_minutes(self):
        """Should format minutes with decimal."""
        assert format_duration(90) == "1.5m"
        assert format_duration(360) == "6.0m"

    def test_hours(self):
        """Should format hours with decimal."""
        assert format_duration(5400) == "1.5h"
        assert format_duration(7200) == "2.0h"

    def test_days(self):
        """Should format days with decimal."""
        assert format_duration(86400) == "1.0d"
        assert format_duration(129600) == "1.5d"


class TestFormatTokens:
    """Tests for format_tokens function."""

    def test_small_counts(self):
        """Should return raw number for small counts."""
        assert format_tokens(500) == "500"
        assert format_tokens(999) == "999"

    def test_thousands(self):
        """Should format thousands with K."""
        assert format_tokens(1000) == "1.0K"
        assert format_tokens(1500) == "1.5K"
        assert format_tokens(999999) == "1000.0K"

    def test_millions(self):
        """Should format millions with M."""
        assert format_tokens(1000000) == "1.0M"
        assert format_tokens(2500000) == "2.5M"


class TestFormatCost:
    """Tests for format_cost function."""

    def test_small_costs(self):
        """Should format small costs with 2 decimals and $ prefix."""
        assert format_cost(0.05) == "$0.05"
        assert format_cost(1.23) == "$1.23"
        assert format_cost(9.99) == "$9.99"

    def test_tens(self):
        """Should format tens with 1 decimal."""
        assert format_cost(10.0) == "$10.0"
        assert format_cost(12.34) == "$12.3"
        assert format_cost(99.95) == "$100.0"

    def test_hundreds(self):
        """Should format hundreds with no decimal."""
        assert format_cost(100.0) == "$100"
        assert format_cost(123.45) == "$123"
        assert format_cost(999.99) == "$1000"

    def test_thousands(self):
        """Should format thousands with K suffix."""
        assert format_cost(1000) == "$1.0K"
        assert format_cost(1500) == "$1.5K"
        assert format_cost(12345) == "$12.3K"

    def test_millions(self):
        """Should format millions with M suffix."""
        assert format_cost(1000000) == "$1.0M"
        assert format_cost(2500000) == "$2.5M"


class TestFormatLineCount:
    """Tests for format_line_count function."""

    def test_small_counts(self):
        """Should return raw number for small counts."""
        assert format_line_count(500) == "500"

    def test_thousands(self):
        """Should format thousands with K (no decimal)."""
        assert format_line_count(1000) == "1K"
        assert format_line_count(1500) == "1K"  # Integer division
        assert format_line_count(173000) == "173K"

    def test_millions(self):
        """Should format millions with M."""
        assert format_line_count(1000000) == "1.0M"
        assert format_line_count(1200000) == "1.2M"


class TestCalculateUptime:
    """Tests for calculate_uptime function."""

    def test_minutes(self):
        """Should format uptime in minutes."""
        now = datetime.now()
        start = (now - timedelta(minutes=30)).isoformat()
        assert calculate_uptime(start, now) == "30m"

    def test_hours(self):
        """Should format uptime in hours."""
        now = datetime.now()
        start = (now - timedelta(hours=4, minutes=30)).isoformat()
        assert calculate_uptime(start, now) == "4.5h"

    def test_days(self):
        """Should format uptime in days."""
        now = datetime.now()
        start = (now - timedelta(days=2, hours=12)).isoformat()
        assert calculate_uptime(start, now) == "2.5d"

    def test_invalid_format_returns_zero(self):
        """Should return '0m' for invalid format."""
        assert calculate_uptime("invalid") == "0m"
        assert calculate_uptime("") == "0m"


class TestCalculatePercentiles:
    """Tests for calculate_percentiles function."""

    def test_empty_list(self):
        """Should return zeros for empty list."""
        mean, p5, p95 = calculate_percentiles([])
        assert mean == 0.0
        assert p5 == 0.0
        assert p95 == 0.0

    def test_single_value(self):
        """Should return same value for all percentiles."""
        mean, p5, p95 = calculate_percentiles([100.0])
        assert mean == 100.0
        assert p5 == 100.0
        assert p95 == 100.0

    def test_multiple_values(self):
        """Should calculate correct percentiles."""
        times = list(range(1, 101))  # 1 to 100
        mean, p5, p95 = calculate_percentiles(times)
        assert mean == 50.5  # Mean of 1-100
        # Percentile calculation uses int index: int(100 * 0.05) = 5, times[5] = 6
        assert p5 == 6  # 5th percentile (index 5 in 1-100 list)
        assert p95 == 96  # 95th percentile (index 95 in 1-100 list)


class TestPresenceStateToChar:
    """Tests for presence_state_to_char function."""

    def test_locked_state(self):
        """State 1 should return a character."""
        char = presence_state_to_char(1)
        assert len(char) == 1

    def test_inactive_state(self):
        """State 2 should return a character."""
        char = presence_state_to_char(2)
        assert len(char) == 1

    def test_active_state(self):
        """State 3 should return a character."""
        char = presence_state_to_char(3)
        assert len(char) == 1


class TestAgentStatusToChar:
    """Tests for agent_status_to_char function."""

    def test_running_status(self):
        """Running should return a character."""
        char = agent_status_to_char("running")
        assert len(char) == 1

    def test_waiting_user_status(self):
        """Waiting user should return a character."""
        char = agent_status_to_char("waiting_user")
        assert len(char) == 1


class TestStatusToColor:
    """Tests for status_to_color function."""

    def test_running_color(self):
        """Running should return a color name."""
        color = status_to_color("running")
        assert isinstance(color, str)
        assert len(color) > 0

    def test_waiting_user_color(self):
        """Waiting user should return a color name."""
        color = status_to_color("waiting_user")
        assert isinstance(color, str)


class TestGetStandingOrdersIndicator:
    """Tests for get_standing_orders_indicator function."""

    def test_no_instructions(self):
        """Should return dash when no instructions."""
        session = Mock(standing_instructions=None, standing_orders_complete=False)
        assert get_standing_orders_indicator(session) == "➖"

    def test_active_instructions(self):
        """Should return clipboard when instructions active."""
        session = Mock(standing_instructions="some instructions", standing_orders_complete=False)
        assert get_standing_orders_indicator(session) == "📋"

    def test_complete_instructions(self):
        """Should return check when complete."""
        session = Mock(standing_instructions="some instructions", standing_orders_complete=True)
        assert get_standing_orders_indicator(session) == "✓"


class TestGetCurrentStateTimes:
    """Tests for get_current_state_times function."""

    def test_returns_accumulated_times(self):
        """Should return accumulated times from stats."""
        stats = Mock(
            green_time_seconds=100.0,
            non_green_time_seconds=50.0,
            sleep_time_seconds=25.0,
            last_time_accumulation=None,
            state_since=None,
            current_state="running",
        )

        green, non_green, sleep = get_current_state_times(stats)

        assert green == 100.0
        assert non_green == 50.0
        assert sleep == 25.0

    def test_adds_elapsed_for_running_state(self):
        """Should add elapsed time for running state."""
        now = datetime.now()
        anchor = (now - timedelta(seconds=60)).isoformat()
        stats = Mock(
            green_time_seconds=100.0,
            non_green_time_seconds=50.0,
            sleep_time_seconds=0.0,
            last_time_accumulation=anchor,
            state_since=anchor,
            current_state="running",
        )

        green, non_green, sleep = get_current_state_times(stats, now)

        assert green > 100.0  # Should have added ~60 seconds
        assert non_green == 50.0
        assert sleep == 0.0

    def test_adds_elapsed_for_non_green_state(self):
        """Should add elapsed time for non-green states like waiting_user."""
        now = datetime.now()
        anchor = (now - timedelta(seconds=60)).isoformat()
        stats = Mock(
            green_time_seconds=100.0,
            non_green_time_seconds=50.0,
            sleep_time_seconds=0.0,
            last_time_accumulation=anchor,
            state_since=anchor,
            current_state="waiting_user",
        )

        green, non_green, sleep = get_current_state_times(stats, now)

        assert green == 100.0  # Green unchanged
        assert non_green > 50.0  # Should have added ~60 seconds
        assert sleep == 0.0

    def test_terminated_state_freezes_time(self):
        """Should not accumulate time for terminated state."""
        now = datetime.now()
        anchor = (now - timedelta(seconds=60)).isoformat()
        stats = Mock(
            green_time_seconds=100.0,
            non_green_time_seconds=50.0,
            sleep_time_seconds=0.0,
            last_time_accumulation=anchor,
            state_since=anchor,
            current_state="terminated",
        )

        green, non_green, sleep = get_current_state_times(stats, now)

        assert green == 100.0  # Unchanged
        assert non_green == 50.0  # Unchanged
        assert sleep == 0.0

    def test_asleep_state_accumulates_sleep_time(self):
        """Should accumulate sleep time for asleep state (#141)."""
        now = datetime.now()
        anchor = (now - timedelta(seconds=60)).isoformat()
        stats = Mock(
            green_time_seconds=100.0,
            non_green_time_seconds=50.0,
            sleep_time_seconds=30.0,
            last_time_accumulation=anchor,
            state_since=anchor,
            current_state="asleep",
        )

        green, non_green, sleep = get_current_state_times(stats, now)

        assert green == 100.0  # Unchanged
        assert non_green == 50.0  # Unchanged
        assert sleep > 30.0  # Should have added ~60 seconds

    def test_is_asleep_parameter_overrides_current_state(self):
        """is_asleep parameter should override stats.current_state (#141).

        This handles the case where user toggles sleep but daemon hasn't
        updated stats.current_state yet. The TUI passes is_asleep=True
        to ensure sleep time is accumulated immediately.
        """
        now = datetime.now()
        anchor = (now - timedelta(seconds=60)).isoformat()
        stats = Mock(
            green_time_seconds=100.0,
            non_green_time_seconds=50.0,
            sleep_time_seconds=0.0,
            last_time_accumulation=anchor,
            state_since=anchor,
            current_state="running",  # Daemon hasn't updated yet
        )

        # Without is_asleep, would add to green_time (because current_state is "running")
        green, non_green, sleep = get_current_state_times(stats, now, is_asleep=False)
        assert green > 100.0  # Added ~60 seconds
        assert non_green == 50.0
        assert sleep == 0.0

        # With is_asleep=True, should add to sleep_time instead
        green, non_green, sleep = get_current_state_times(stats, now, is_asleep=True)
        assert green == 100.0  # Unchanged
        assert non_green == 50.0  # Unchanged
        assert sleep > 0.0  # Added ~60 seconds

    def test_handles_invalid_time_anchor(self):
        """Should handle invalid time anchor gracefully."""
        stats = Mock(
            green_time_seconds=100.0,
            non_green_time_seconds=50.0,
            sleep_time_seconds=0.0,
            last_time_accumulation="invalid",
            state_since=None,
            current_state="running",
        )

        green, non_green, sleep = get_current_state_times(stats)

        assert green == 100.0
        assert non_green == 50.0
        assert sleep == 0.0


class TestBuildTimelineSlots:
    """Tests for build_timeline_slots function."""

    def test_empty_history(self):
        """Should return empty dict for empty history."""
        result = build_timeline_slots([], width=10, hours=1.0)
        assert result == {}

    def test_maps_states_to_slots(self):
        """Should map history entries to slots."""
        now = datetime.now()
        history = [
            (now - timedelta(minutes=30), 3),
            (now - timedelta(minutes=15), 2),
        ]

        result = build_timeline_slots(history, width=60, hours=1.0, now=now)

        assert len(result) > 0

    def test_filters_old_entries(self):
        """Should filter entries older than time window."""
        now = datetime.now()
        history = [
            (now - timedelta(hours=5), 3),  # Too old
        ]

        result = build_timeline_slots(history, width=60, hours=1.0, now=now)

        assert result == {}


class TestBuildTimelineString:
    """Tests for build_timeline_string function."""

    def test_empty_slots(self):
        """Should return dashes for empty slots."""
        result = build_timeline_string({}, width=5, state_to_char=lambda x: "X")
        assert result == "─────"

    def test_filled_slots(self):
        """Should use state_to_char for filled slots."""
        slots = {0: "A", 2: "B", 4: "C"}
        result = build_timeline_string(slots, width=5, state_to_char=lambda x: x)
        assert result == "A─B─C"


class TestGetStatusSymbol:
    """Tests for get_status_symbol function."""

    def test_returns_tuple(self):
        """Should return (emoji, color) tuple."""
        emoji, color = get_status_symbol("running")
        assert isinstance(emoji, str)
        assert isinstance(color, str)


class TestGetPresenceColor:
    """Tests for get_presence_color function."""

    def test_returns_color_string(self):
        """Should return color string for each state."""
        assert isinstance(get_presence_color(1), str)
        assert isinstance(get_presence_color(2), str)
        assert isinstance(get_presence_color(3), str)


class TestGetAgentTimelineColor:
    """Tests for get_agent_timeline_color function."""

    def test_returns_color_string(self):
        """Should return color string for status."""
        assert isinstance(get_agent_timeline_color("running"), str)
        assert isinstance(get_agent_timeline_color("waiting_user"), str)


class TestStylePaneLine:
    """Tests for style_pane_line function."""

    def test_success_line(self):
        """Should return green styles for success."""
        prefix, content = style_pane_line("✓ Test passed")
        assert "green" in prefix
        assert "green" in content

    def test_error_line(self):
        """Should return red styles for error."""
        prefix, content = style_pane_line("✗ Test failed")
        assert "red" in prefix
        assert "red" in content

    def test_command_line(self):
        """Should return cyan/white for command prompt."""
        prefix, content = style_pane_line("> command here")
        assert "cyan" in prefix
        assert "white" in content

    def test_normal_line(self):
        """Should return default styles for normal text."""
        prefix, content = style_pane_line("normal text")
        assert "cyan" in prefix


class TestTruncateName:
    """Tests for truncate_name function."""

    def test_short_name_padded(self):
        """Should pad short names."""
        result = truncate_name("abc", max_len=10)
        assert len(result) == 10
        assert result.startswith("abc")

    def test_long_name_truncated(self):
        """Should truncate long names."""
        result = truncate_name("abcdefghijklmnop", max_len=10)
        assert len(result) == 10
        assert result == "abcdefghij"


class TestGetDaemonStatusStyle:
    """Tests for get_daemon_status_style function."""

    def test_returns_tuple(self):
        """Should return (symbol, style) tuple."""
        symbol, style = get_daemon_status_style("running")
        assert isinstance(symbol, str)
        assert isinstance(style, str)


class TestCalculateSafeBreakDuration:
    """Tests for calculate_safe_break_duration function."""

    def test_no_running_agents(self):
        """Should return None when no running agents."""
        sessions = [Mock(current_status="waiting_user", median_work_time=100)]
        result = calculate_safe_break_duration(sessions)
        assert result is None

    def test_no_median_work_time(self):
        """Should return None when no median work time data."""
        sessions = [Mock(current_status="running", median_work_time=0)]
        result = calculate_safe_break_duration(sessions)
        assert result is None

    def test_calculates_break_duration(self):
        """Should calculate break duration based on median work time."""
        now = datetime.now()
        sessions = [
            Mock(
                current_status="running",
                median_work_time=300,  # 5 minutes
                status_since=(now - timedelta(seconds=60)).isoformat(),  # 1 minute in state
            ),
            Mock(
                current_status="running",
                median_work_time=600,  # 10 minutes
                status_since=(now - timedelta(seconds=120)).isoformat(),  # 2 minutes in state
            ),
        ]

        result = calculate_safe_break_duration(sessions, now)

        assert result is not None
        assert result > 0

    def test_returns_zero_when_past_median(self):
        """Should return 0 when agents are past their median work time."""
        now = datetime.now()
        sessions = [
            Mock(
                current_status="running",
                median_work_time=60,  # 1 minute median
                status_since=(now - timedelta(seconds=120)).isoformat(),  # Already 2 minutes in
            ),
        ]

        result = calculate_safe_break_duration(sessions, now)

        assert result == 0

    def test_handles_invalid_status_since(self):
        """Should handle invalid status_since gracefully."""
        sessions = [
            Mock(
                current_status="running",
                median_work_time=300,
                status_since="invalid-date",
            ),
        ]

        result = calculate_safe_break_duration(sessions)

        # Should still calculate, using 0 for time_in_state
        assert result is not None
        assert result == 300  # Full median work time

    def test_handles_none_status_since(self):
        """Should handle None status_since gracefully."""
        sessions = [
            Mock(
                current_status="running",
                median_work_time=300,
                status_since=None,
            ),
        ]

        result = calculate_safe_break_duration(sessions)

        assert result is not None
        assert result == 300


class TestGetGitDiffStats:
    """Tests for get_git_diff_stats function."""

    def test_returns_none_for_nonexistent_dir(self):
        """Should return None for non-existent directory."""
        result = get_git_diff_stats("/nonexistent/path")
        assert result is None

    def test_returns_tuple_for_git_repo(self, tmp_path):
        """Should return tuple for valid git repo."""
        import subprocess

        # Initialize a git repo
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=tmp_path,
            capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=tmp_path,
            capture_output=True
        )

        # Create initial commit
        (tmp_path / "file.txt").write_text("initial")
        subprocess.run(["git", "add", "file.txt"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=tmp_path,
            capture_output=True
        )

        result = get_git_diff_stats(str(tmp_path))

        assert result is not None
        assert len(result) == 3
        files, insertions, deletions = result
        assert isinstance(files, int)
        assert isinstance(insertions, int)
        assert isinstance(deletions, int)

    def test_returns_changes_with_modified_file(self, tmp_path):
        """Should return actual change counts when files are modified."""
        import subprocess

        # Initialize a git repo
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=tmp_path,
            capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=tmp_path,
            capture_output=True
        )

        # Create initial commit
        (tmp_path / "file.txt").write_text("line1\nline2\n")
        subprocess.run(["git", "add", "file.txt"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=tmp_path,
            capture_output=True
        )

        # Modify the file
        (tmp_path / "file.txt").write_text("line1\nmodified\nline3\n")

        result = get_git_diff_stats(str(tmp_path))

        assert result is not None
        files, insertions, deletions = result
        assert files == 1  # One file changed
        assert insertions >= 1  # At least one insertion
        assert deletions >= 1  # At least one deletion

    def test_returns_zero_for_clean_repo(self, tmp_path):
        """Should return zeros for clean repo with no changes."""
        import subprocess

        # Initialize a git repo
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=tmp_path,
            capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=tmp_path,
            capture_output=True
        )

        # Create initial commit
        (tmp_path / "file.txt").write_text("initial")
        subprocess.run(["git", "add", "file.txt"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=tmp_path,
            capture_output=True
        )

        result = get_git_diff_stats(str(tmp_path))

        assert result == (0, 0, 0)

    def test_returns_none_for_non_git_directory(self, tmp_path):
        """Should return None for directory that isn't a git repo."""
        # Create a directory without git init
        (tmp_path / "subdir").mkdir()

        result = get_git_diff_stats(str(tmp_path / "subdir"))

        assert result is None


class TestHeartbeatStartStatus:
    """Tests for heartbeat_start status integration."""

    def test_is_green_status(self):
        """heartbeat_start should count as green (actively working)."""
        from overcode.status_constants import is_green_status
        assert is_green_status("heartbeat_start") is True

    def test_has_timeline_char(self):
        """heartbeat_start should have a timeline character mapping."""
        assert agent_status_to_char("heartbeat_start") == "💚"

    def test_has_timeline_color(self):
        """heartbeat_start should have green timeline color."""
        assert get_agent_timeline_color("heartbeat_start") == "green"


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
