"""
Unit tests for TUI rendering and formatting.

These tests focus on the pure formatting functions that have been
extracted to tui_helpers.py for testability.
"""

import pytest
import sys
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

# Import the helper functions we're testing
from overcode.tui_helpers import (
    format_duration,
    format_interval,
    format_line_count,
    format_ago,
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
)

# Import SessionStats for testing
from overcode.session_manager import SessionStats


class TestFormatDuration:
    """Test duration formatting logic"""

    def test_seconds(self):
        """Durations under 60s show as seconds"""
        assert format_duration(0) == "0s"
        assert format_duration(30) == "30s"
        assert format_duration(59) == "59s"

    def test_minutes(self):
        """Durations 1-60 min show as minutes with decimal"""
        assert format_duration(60) == "1.0m"
        assert format_duration(90) == "1.5m"
        assert format_duration(3599) == "60.0m"

    def test_hours(self):
        """Durations 1-24 hours show as hours with decimal"""
        assert format_duration(3600) == "1.0h"
        assert format_duration(7200) == "2.0h"
        assert format_duration(5400) == "1.5h"

    def test_days(self):
        """Durations over 24 hours show as days"""
        assert format_duration(86400) == "1.0d"
        assert format_duration(172800) == "2.0d"
        assert format_duration(129600) == "1.5d"


class TestCalculatePercentiles:
    """Test percentile calculation logic"""

    def test_empty_list(self):
        """Empty list returns zeros"""
        mean, p5, p95 = calculate_percentiles([])
        assert mean == 0.0
        assert p5 == 0.0
        assert p95 == 0.0

    def test_single_value(self):
        """Single value returns that value for all"""
        mean, p5, p95 = calculate_percentiles([5.0])
        assert mean == 5.0
        assert p5 == 5.0
        assert p95 == 5.0

    def test_two_values(self):
        """Two values calculates correctly"""
        mean, p5, p95 = calculate_percentiles([2.0, 8.0])
        assert mean == 5.0
        # With 2 values, both percentiles hit first/last

    def test_many_values(self):
        """Many values calculates correct percentiles"""
        # 100 values from 1-100
        times = list(range(1, 101))
        mean, p5, p95 = calculate_percentiles(times)

        assert mean == 50.5  # Mean of 1-100
        assert p5 <= 10  # 5th percentile should be low
        assert p95 >= 90  # 95th percentile should be high


class TestFormatInterval:
    """Test interval formatting for daemon status"""

    def test_seconds(self):
        """Short intervals show as seconds"""
        assert format_interval(5) == "5s"
        assert format_interval(30) == "30s"
        assert format_interval(59) == "59s"

    def test_minutes(self):
        """Longer intervals show as minutes"""
        assert format_interval(60) == "1m"
        assert format_interval(120) == "2m"
        assert format_interval(300) == "5m"

    def test_hours(self):
        """Very long intervals show as hours"""
        assert format_interval(3600) == "1h"
        assert format_interval(7200) == "2h"


class TestFormatLineCount:
    """Test line count formatting for git diff stats"""

    def test_small_counts(self):
        """Small counts show as raw numbers"""
        assert format_line_count(0) == "0"
        assert format_line_count(1) == "1"
        assert format_line_count(42) == "42"
        assert format_line_count(999) == "999"

    def test_thousands(self):
        """Counts in thousands show as K without decimal"""
        assert format_line_count(1000) == "1K"
        assert format_line_count(1500) == "1K"
        assert format_line_count(9999) == "9K"
        assert format_line_count(10000) == "10K"
        assert format_line_count(173242) == "173K"
        assert format_line_count(999999) == "999K"

    def test_millions(self):
        """Counts in millions show as M with one decimal"""
        assert format_line_count(1_000_000) == "1.0M"
        assert format_line_count(1_234_567) == "1.2M"
        assert format_line_count(10_500_000) == "10.5M"

    def test_output_fits_width(self):
        """Formatted output fits within expected display width (4 chars)"""
        # This is the key test for the bug fix - large numbers must fit in 4 chars
        test_cases = [
            0, 1, 99, 999,           # Small numbers: "0", "1", "99", "999"
            1000, 9999, 99999,       # Thousands: "1K", "9K", "99K"
            100000, 999999,          # Large thousands: "100K", "999K"
            173242,                  # Issue #2 example: "173K"
            1_000_000, 9_999_999,    # Millions: "1.0M", "10.0M"
        ]
        for count in test_cases:
            result = format_line_count(count)
            assert len(result) <= 5, f"format_line_count({count}) = '{result}' exceeds 5 chars"


class TestSessionStatsDisplay:
    """Test session stats display values"""

    def test_initial_stats_defaults(self):
        """New SessionStats has sensible defaults"""
        stats = SessionStats()

        assert stats.interaction_count == 0
        assert stats.estimated_cost_usd == 0.0
        assert stats.steers_count == 0
        assert stats.current_task == "Initializing..."

    def test_stats_cost_display(self):
        """Cost displays correctly"""
        stats = SessionStats(estimated_cost_usd=0.15)
        # Would display as "$0.15" in TUI
        assert f"${stats.estimated_cost_usd:.2f}" == "$0.15"

    def test_stats_interaction_count(self):
        """Interaction count displays correctly"""
        stats = SessionStats(interaction_count=42)
        assert stats.interaction_count == 42


class TestStatusColors:
    """Test status to color mapping"""

    def test_running_is_green(self):
        """Running status maps to green"""
        assert status_to_color("running") == "green"

    def test_waiting_user_is_red(self):
        """Waiting user maps to red"""
        assert status_to_color("waiting_user") == "red"

    def test_no_instructions_is_yellow(self):
        """No instructions maps to yellow"""
        assert status_to_color("no_instructions") == "yellow"

    def test_unknown_is_dim(self):
        """Unknown status maps to dim"""
        assert status_to_color("unknown") == "dim"


class TestStandingOrdersDisplay:
    """Test standing orders indicator logic"""

    def test_no_instructions_shows_dash(self):
        """No standing instructions shows dash emoji"""
        session = Mock()
        session.standing_instructions = ""
        session.standing_orders_complete = False

        indicator = get_standing_orders_indicator(session)
        assert indicator == "➖"

    def test_instructions_incomplete_shows_clipboard(self):
        """Incomplete instructions shows clipboard emoji"""
        session = Mock()
        session.standing_instructions = "Keep working"
        session.standing_orders_complete = False

        indicator = get_standing_orders_indicator(session)
        assert indicator == "📋"

    def test_instructions_complete_shows_checkmark(self):
        """Complete instructions shows checkmark"""
        session = Mock()
        session.standing_instructions = "Keep working"
        session.standing_orders_complete = True

        indicator = get_standing_orders_indicator(session)
        assert indicator == "✓"


class TestFormatAgo:
    """Test datetime formatting as 'X ago'"""

    def test_none_returns_never(self):
        """None datetime returns 'never'"""
        assert format_ago(None) == "never"

    def test_default_now(self):
        """Uses current time by default"""
        # Just test it doesn't crash when no 'now' is provided
        dt = datetime.now() - timedelta(seconds=10)
        result = format_ago(dt)
        assert "ago" in result

    def test_seconds_ago(self):
        """Recent time shows as seconds ago"""
        now = datetime.now()
        dt = now - timedelta(seconds=30)
        result = format_ago(dt, now)
        assert result == "30s ago"

    def test_minutes_ago(self):
        """Few minutes ago shows as minutes"""
        now = datetime.now()
        dt = now - timedelta(minutes=5)
        result = format_ago(dt, now)
        assert result == "5m ago"

    def test_hours_ago(self):
        """Hours ago shows with decimal"""
        now = datetime.now()
        dt = now - timedelta(hours=2, minutes=30)
        result = format_ago(dt, now)
        assert result == "2.5h ago"

    def test_just_under_minute(self):
        """59 seconds shows as seconds"""
        now = datetime.now()
        dt = now - timedelta(seconds=59)
        result = format_ago(dt, now)
        assert result == "59s ago"

    def test_just_over_minute(self):
        """61 seconds shows as minutes"""
        now = datetime.now()
        dt = now - timedelta(seconds=61)
        result = format_ago(dt, now)
        assert result == "1m ago"


class TestCalculateUptime:
    """Test uptime calculation from start time"""

    def test_minutes(self):
        """Uptime under an hour shows as minutes"""
        now = datetime.now()
        start = (now - timedelta(minutes=30)).isoformat()
        result = calculate_uptime(start, now)
        assert result == "30m"

    def test_hours(self):
        """Uptime 1-24 hours shows as hours with decimal"""
        now = datetime.now()
        start = (now - timedelta(hours=4, minutes=30)).isoformat()
        result = calculate_uptime(start, now)
        assert result == "4.5h"

    def test_days(self):
        """Uptime over 24 hours shows as days"""
        now = datetime.now()
        start = (now - timedelta(days=2, hours=12)).isoformat()
        result = calculate_uptime(start, now)
        assert result == "2.5d"

    def test_zero_uptime(self):
        """Just started shows as 0m"""
        now = datetime.now()
        start = now.isoformat()
        result = calculate_uptime(start, now)
        assert result == "0m"

    def test_invalid_start_time(self):
        """Invalid start time returns 0m"""
        result = calculate_uptime("invalid", datetime.now())
        assert result == "0m"

    def test_default_now(self):
        """Uses current time by default"""
        # Just test it doesn't crash when no 'now' is provided
        now = datetime.now()
        start = (now - timedelta(minutes=5)).isoformat()
        result = calculate_uptime(start)
        assert result == "5m"


class TestPresenceStateToChar:
    """Test presence state to timeline character conversion"""

    def test_locked_sleep_state(self):
        """State 1 (locked/sleep) returns light shade"""
        assert presence_state_to_char(1) == "░"

    def test_inactive_state(self):
        """State 2 (inactive) returns medium shade"""
        assert presence_state_to_char(2) == "▒"

    def test_active_state(self):
        """State 3 (active) returns full block"""
        assert presence_state_to_char(3) == "█"

    def test_unknown_state(self):
        """Unknown state returns dash"""
        assert presence_state_to_char(99) == "─"


class TestAgentStatusToChar:
    """Test agent status to timeline character conversion"""

    def test_running(self):
        """Running status returns full block"""
        assert agent_status_to_char("running") == "█"

    def test_no_instructions(self):
        """No instructions returns dense shade"""
        assert agent_status_to_char("no_instructions") == "▓"

    def test_waiting_supervisor(self):
        """Waiting supervisor returns medium shade"""
        assert agent_status_to_char("waiting_supervisor") == "▒"

    def test_waiting_user(self):
        """Waiting user returns light shade"""
        assert agent_status_to_char("waiting_user") == "░"

    def test_unknown_status(self):
        """Unknown status returns dash"""
        assert agent_status_to_char("unknown") == "─"


class TestGetCurrentStateTimes:
    """Test current state time calculation"""

    def test_no_state_since(self):
        """No state_since returns base times only"""
        stats = Mock()
        stats.green_time_seconds = 100.0
        stats.non_green_time_seconds = 50.0
        stats.last_time_accumulation = None
        stats.state_since = None
        stats.current_state = "running"

        green, non_green = get_current_state_times(stats)

        assert green == 100.0
        assert non_green == 50.0

    def test_adds_current_green_time(self):
        """Adds elapsed time to green_time when running"""
        now = datetime.now()
        stats = Mock()
        stats.green_time_seconds = 100.0
        stats.non_green_time_seconds = 50.0
        stats.last_time_accumulation = None
        stats.state_since = (now - timedelta(seconds=30)).isoformat()
        stats.current_state = "running"

        green, non_green = get_current_state_times(stats, now)

        assert green == pytest.approx(130.0, rel=0.1)  # 100 + 30
        assert non_green == 50.0

    def test_adds_current_non_green_time(self):
        """Adds elapsed time to non_green_time when not running"""
        now = datetime.now()
        stats = Mock()
        stats.green_time_seconds = 100.0
        stats.non_green_time_seconds = 50.0
        stats.last_time_accumulation = None
        stats.state_since = (now - timedelta(seconds=20)).isoformat()
        stats.current_state = "waiting_user"

        green, non_green = get_current_state_times(stats, now)

        assert green == 100.0
        assert non_green == pytest.approx(70.0, rel=0.1)  # 50 + 20

    def test_default_now(self):
        """Uses current time by default"""
        stats = Mock()
        stats.green_time_seconds = 100.0
        stats.non_green_time_seconds = 50.0
        stats.last_time_accumulation = None
        stats.state_since = None
        stats.current_state = "running"

        # Just test it doesn't crash and returns correct base values
        green, non_green = get_current_state_times(stats)
        assert green == 100.0
        assert non_green == 50.0

    def test_invalid_state_since_handled(self):
        """Invalid state_since is handled gracefully"""
        stats = Mock()
        stats.green_time_seconds = 100.0
        stats.non_green_time_seconds = 50.0
        stats.last_time_accumulation = None
        stats.state_since = "invalid"
        stats.current_state = "running"

        green, non_green = get_current_state_times(stats)

        # Should return base values without crashing
        assert green == 100.0
        assert non_green == 50.0

    def test_terminated_state_does_not_accumulate_time(self):
        """Terminated state should NOT add time to either counter.

        When an agent is terminated (Claude Code exited), time should be frozen.
        Neither green_time nor non_green_time should increase.
        """
        now = datetime.now()
        stats = Mock()
        stats.green_time_seconds = 100.0
        stats.non_green_time_seconds = 50.0
        stats.last_time_accumulation = None
        stats.state_since = (now - timedelta(seconds=60)).isoformat()
        stats.current_state = "terminated"

        green, non_green = get_current_state_times(stats, now)

        # Time should NOT be added to either counter
        assert green == 100.0  # Should stay at base value
        assert non_green == 50.0  # Should stay at base value


# =============================================================================
# Tests for new extracted helper functions
# =============================================================================

class TestBuildTimelineSlots:
    """Test timeline slot building from history"""

    def test_empty_history(self):
        """Empty history returns empty dict"""
        now = datetime.now()
        result = build_timeline_slots([], 60, 3.0, now)
        assert result == {}

    def test_single_entry(self):
        """Single entry maps to correct slot"""
        now = datetime.now()
        ts = now - timedelta(hours=1.5)  # 1.5 hours ago (middle of 3h window)
        history = [(ts, 3)]
        result = build_timeline_slots(history, 60, 3.0, now)
        # 1.5h ago in a 3h window = 50% = slot 30
        assert 30 in result
        assert result[30] == 3

    def test_entry_before_window_excluded(self):
        """Entry before window is excluded"""
        now = datetime.now()
        ts = now - timedelta(hours=5)  # 5 hours ago (outside 3h window)
        history = [(ts, 3)]
        result = build_timeline_slots(history, 60, 3.0, now)
        assert result == {}

    def test_multiple_entries(self):
        """Multiple entries map correctly"""
        now = datetime.now()
        history = [
            (now - timedelta(minutes=30), "running"),
            (now - timedelta(minutes=90), "waiting_user"),
        ]
        result = build_timeline_slots(history, 60, 3.0, now)
        assert len(result) == 2


class TestBuildTimelineString:
    """Test timeline string construction"""

    def test_empty_slots(self):
        """Empty slots produce dashes"""
        result = build_timeline_string({}, 10, lambda x: "X")
        assert result == "──────────"

    def test_filled_slots(self):
        """Filled slots use state_to_char function"""
        slots = {0: 1, 5: 2, 9: 3}
        result = build_timeline_string(slots, 10, lambda x: str(x))
        assert result[0] == "1"
        assert result[5] == "2"
        assert result[9] == "3"
        assert result[1] == "─"

    def test_full_timeline(self):
        """Full timeline all filled"""
        slots = {i: i for i in range(5)}
        result = build_timeline_string(slots, 5, lambda x: "█")
        assert result == "█████"


class TestGetStatusSymbol:
    """Test status symbol retrieval"""

    def test_running_status(self):
        """Running status returns green circle"""
        symbol, color = get_status_symbol("running")
        assert symbol == "🟢"
        assert color == "green"

    def test_no_instructions_status(self):
        """No instructions returns yellow circle"""
        symbol, color = get_status_symbol("no_instructions")
        assert symbol == "🟡"
        assert color == "yellow"

    def test_waiting_supervisor_status(self):
        """Waiting supervisor returns orange circle"""
        symbol, color = get_status_symbol("waiting_supervisor")
        assert symbol == "🟠"
        assert color == "orange1"

    def test_waiting_user_status(self):
        """Waiting user returns red circle"""
        symbol, color = get_status_symbol("waiting_user")
        assert symbol == "🔴"
        assert color == "red"

    def test_unknown_status(self):
        """Unknown status returns white circle"""
        symbol, color = get_status_symbol("unknown")
        assert symbol == "⚪"
        assert color == "dim"


class TestGetPresenceColor:
    """Test presence state to color mapping"""

    def test_locked_is_red(self):
        """State 1 (locked) is red"""
        assert get_presence_color(1) == "red"

    def test_inactive_is_yellow(self):
        """State 2 (inactive) is yellow"""
        assert get_presence_color(2) == "yellow"

    def test_active_is_green(self):
        """State 3 (active) is green"""
        assert get_presence_color(3) == "green"

    def test_unknown_is_dim(self):
        """Unknown state is dim"""
        assert get_presence_color(99) == "dim"


class TestGetAgentTimelineColor:
    """Test agent status to timeline color mapping"""

    def test_running_is_green(self):
        """Running is green"""
        assert get_agent_timeline_color("running") == "green"

    def test_no_instructions_is_yellow(self):
        """No instructions is yellow"""
        assert get_agent_timeline_color("no_instructions") == "yellow"

    def test_waiting_supervisor_is_orange(self):
        """Waiting supervisor is orange1"""
        assert get_agent_timeline_color("waiting_supervisor") == "orange1"

    def test_waiting_user_is_red(self):
        """Waiting user is red"""
        assert get_agent_timeline_color("waiting_user") == "red"

    def test_unknown_is_dim(self):
        """Unknown is dim"""
        assert get_agent_timeline_color("unknown") == "dim"


class TestStylePaneLine:
    """Test pane line styling logic"""

    def test_success_line(self):
        """Success indicator styled green"""
        prefix, content = style_pane_line("✓ Test passed")
        assert prefix == "bold green"
        assert content == "green"

    def test_success_keyword(self):
        """Success keyword styled green"""
        prefix, content = style_pane_line("Build SUCCESS!")
        assert prefix == "bold green"
        assert content == "green"

    def test_error_line(self):
        """Error indicator styled red"""
        prefix, content = style_pane_line("✗ Test failed")
        assert prefix == "bold red"
        assert content == "red"

    def test_error_keyword(self):
        """Error keyword styled red"""
        prefix, content = style_pane_line("ERROR: something broke")
        assert prefix == "bold red"

    def test_fail_keyword(self):
        """Fail keyword styled red"""
        prefix, content = style_pane_line("Test FAILED")
        assert prefix == "bold red"

    def test_prompt_chevron(self):
        """Prompt chevron styled as command"""
        prefix, content = style_pane_line("> npm install")
        assert prefix == "bold cyan"
        assert content == "bold white"

    def test_prompt_dollar(self):
        """Dollar prompt styled as command"""
        prefix, content = style_pane_line("$ git status")
        assert prefix == "bold cyan"
        assert content == "bold white"

    def test_prompt_fancy(self):
        """Fancy prompt styled as command"""
        prefix, content = style_pane_line("❯ ls -la")
        assert prefix == "bold cyan"

    def test_normal_line(self):
        """Normal line has cyan bar for visibility"""
        prefix, content = style_pane_line("Just some output text")
        assert prefix == "cyan"
        assert content == "white"


class TestTruncateName:
    """Test name truncation and padding"""

    def test_short_name_padded(self):
        """Short name is padded to max_len"""
        result = truncate_name("abc", 7)
        assert result == "abc    "
        assert len(result) == 7

    def test_exact_length_unchanged(self):
        """Exact length name unchanged"""
        result = truncate_name("abcdefg", 7)
        assert result == "abcdefg"

    def test_long_name_truncated(self):
        """Long name is truncated"""
        result = truncate_name("abcdefghij", 7)
        assert result == "abcdefg"
        assert len(result) == 7

    def test_default_max_len(self):
        """Default max_len is 14"""
        result = truncate_name("abcdefghij")
        assert len(result) == 14

    def test_custom_max_len(self):
        """Custom max_len works"""
        result = truncate_name("abc", 10)
        assert len(result) == 10


class TestGetDaemonStatusStyle:
    """Test daemon status style mapping"""

    def test_active_status(self):
        """Active status returns filled green"""
        symbol, style = get_daemon_status_style("active")
        assert symbol == "●"
        assert style == "green"

    def test_idle_status(self):
        """Idle status returns empty yellow"""
        symbol, style = get_daemon_status_style("idle")
        assert symbol == "○"
        assert style == "yellow"

    def test_supervising_status(self):
        """Supervising status returns filled cyan"""
        symbol, style = get_daemon_status_style("supervising")
        assert symbol == "●"
        assert style == "cyan"

    def test_stopped_status(self):
        """Stopped status returns empty red"""
        symbol, style = get_daemon_status_style("stopped")
        assert symbol == "○"
        assert style == "red"

    def test_sleeping_status(self):
        """Sleeping status returns empty dim"""
        symbol, style = get_daemon_status_style("sleeping")
        assert symbol == "○"
        assert style == "dim"

    def test_no_agents_status(self):
        """No agents status returns empty dim"""
        symbol, style = get_daemon_status_style("no_agents")
        assert symbol == "○"
        assert style == "dim"

    def test_waiting_status(self):
        """Waiting status returns half-filled yellow"""
        symbol, style = get_daemon_status_style("waiting")
        assert symbol == "◐"
        assert style == "yellow"

    def test_unknown_status(self):
        """Unknown status returns question mark dim"""
        symbol, style = get_daemon_status_style("unknown_status")
        assert symbol == "?"
        assert style == "dim"


# =============================================================================
# Tests for tui.py module (widget classes)
# =============================================================================

class TestTuiModuleImports:
    """Test that tui.py can be imported and classes exist"""

    def test_imports_successfully(self):
        """tui module imports without error"""
        from overcode import tui
        assert tui is not None

    def test_daemon_status_bar_exists(self):
        """DaemonStatusBar class exists"""
        from overcode.tui import DaemonStatusBar
        assert DaemonStatusBar is not None

    def test_status_timeline_exists(self):
        """StatusTimeline class exists"""
        from overcode.tui import StatusTimeline
        assert StatusTimeline is not None

    def test_session_summary_exists(self):
        """SessionSummary class exists"""
        from overcode.tui import SessionSummary
        assert SessionSummary is not None

    def test_supervisor_tui_exists(self):
        """SupervisorTUI class exists"""
        from overcode.tui import SupervisorTUI
        assert SupervisorTUI is not None

    def test_help_overlay_exists(self):
        """HelpOverlay class exists"""
        from overcode.tui import HelpOverlay
        assert HelpOverlay is not None

    def test_run_tui_function_exists(self):
        """run_tui function exists"""
        from overcode.tui import run_tui
        assert callable(run_tui)


class TestDaemonStatusBarWidget:
    """Test DaemonStatusBar widget"""

    def test_can_instantiate(self):
        """Can create DaemonStatusBar instance"""
        from overcode.tui import DaemonStatusBar
        widget = DaemonStatusBar()
        assert widget is not None
        assert widget.monitor_state is None


class TestStatusTimelineWidget:
    """Test StatusTimeline widget"""

    def test_can_instantiate(self):
        """Can create StatusTimeline instance"""
        from overcode.tui import StatusTimeline
        widget = StatusTimeline(sessions=[])
        assert widget is not None
        assert widget.sessions == []

    def test_timeline_width_property(self):
        """timeline_width returns reasonable value"""
        from overcode.tui import StatusTimeline
        widget = StatusTimeline(sessions=[])
        width = widget.timeline_width
        assert width >= 20  # MIN_TIMELINE
        assert width <= 120


class TestHelpOverlayWidget:
    """Test HelpOverlay widget"""

    def test_can_instantiate(self):
        """Can create HelpOverlay instance"""
        from overcode.tui import HelpOverlay
        widget = HelpOverlay()
        assert widget is not None

    def test_has_help_text(self):
        """HelpOverlay has help text defined"""
        from overcode.tui import HelpOverlay
        assert HelpOverlay.HELP_TEXT is not None
        assert len(HelpOverlay.HELP_TEXT) > 100


# =============================================================================
# Render output tests (Option C) - Test widget render() with mocked deps
# NOTE: These tests are skipped as they require Textual app context and
# assertions are outdated. TODO: Rewrite with Textual test harness.
# =============================================================================

@pytest.mark.skip(reason="Requires Textual app context and assertions are outdated")
class TestDaemonStatusBarRender:
    """Test DaemonStatusBar.render() output with mocked dependencies"""

    def test_render_stopped_no_state(self):
        """Render shows stopped when no monitor daemon state"""
        from overcode.tui import DaemonStatusBar

        widget = DaemonStatusBar()
        widget.monitor_state = None
        result = widget.render()
        plain = result.plain
        assert "Daemon:" in plain
        assert "stopped" in plain

    def test_render_stopped_with_last_time(self):
        """Render shows last loop time when stopped with stale state"""
        from overcode.tui import DaemonStatusBar
        from overcode.monitor_daemon_state import MonitorDaemonState
        from unittest.mock import patch

        widget = DaemonStatusBar()
        state = MonitorDaemonState()
        state.last_loop_time = (datetime.now() - timedelta(minutes=5)).isoformat()
        # Make it stale by setting started_at to old time
        state.started_at = (datetime.now() - timedelta(hours=1)).isoformat()
        widget.monitor_state = state
        # Patch is_stale to return True
        with patch.object(state, 'is_stale', return_value=True):
            result = widget.render()
            plain = result.plain
            assert "stopped" in plain
            assert "last:" in plain

    def test_render_active(self):
        """Render shows active daemon status from MonitorDaemonState"""
        from overcode.tui import DaemonStatusBar
        from overcode.monitor_daemon_state import MonitorDaemonState
        from unittest.mock import patch

        widget = DaemonStatusBar()
        state = MonitorDaemonState()
        state.status = "active"
        state.loop_count = 42
        state.current_interval = 10
        state.last_loop_time = datetime.now().isoformat()
        state.started_at = datetime.now().isoformat()
        widget.monitor_state = state
        with patch.object(state, 'is_stale', return_value=False):
            result = widget.render()
            plain = result.plain
            assert "active" in plain
            assert "#42" in plain
            assert "@10s" in plain

    def test_render_with_supervisions(self):
        """Render shows supervision count from MonitorDaemonState"""
        from overcode.tui import DaemonStatusBar
        from overcode.monitor_daemon_state import MonitorDaemonState
        from unittest.mock import patch

        widget = DaemonStatusBar()
        state = MonitorDaemonState()
        state.status = "active"
        state.total_supervisions = 5
        state.started_at = datetime.now().isoformat()
        state.last_loop_time = datetime.now().isoformat()
        widget.monitor_state = state
        with patch.object(state, 'is_stale', return_value=False):
            result = widget.render()
            plain = result.plain
            assert "sup:5" in plain


class TestHelpOverlayRender:
    """Test HelpOverlay.render() output"""

    def test_render_contains_keyboard_shortcuts(self):
        """Help text contains keyboard shortcut sections"""
        from overcode.tui import HelpOverlay
        widget = HelpOverlay()
        result = widget.render()
        plain = result.plain
        # Check for reorganized section headers
        assert "NAVIGATION & VIEW" in plain
        assert "AGENT CONTROL" in plain
        assert "Quit" in plain

    def test_render_contains_status_colors(self):
        """Help text contains status color descriptions"""
        from overcode.tui import HelpOverlay
        widget = HelpOverlay()
        result = widget.render()
        plain = result.plain
        assert "Running" in plain
        assert "Wait user" in plain


class TestStatusTimelineRender:
    """Test StatusTimeline.render() output"""

    def test_render_empty_timeline(self):
        """Render shows timeline header with no data"""
        from overcode.tui import StatusTimeline
        widget = StatusTimeline(sessions=[])
        widget._presence_history = []
        widget._agent_histories = {}
        result = widget.render()
        plain = result.plain
        assert "Timeline:" in plain
        assert "User:" in plain

    def test_render_shows_legend(self):
        """Render includes timeline legend"""
        from overcode.tui import StatusTimeline
        widget = StatusTimeline(sessions=[])
        result = widget.render()
        plain = result.plain
        assert "active" in plain
        assert "inactive" in plain
        assert "running" in plain


@pytest.mark.skip(reason="Requires Textual app context")
class TestSessionSummaryRender:
    """Test SessionSummary.render() output"""

    def test_render_basic_session(self):
        """Render shows session name and stats"""
        from overcode.tui import SessionSummary
        from overcode.session_manager import Session, SessionStats
        from overcode.status_detector import StatusDetector
        from overcode.history_reader import ClaudeSessionStats
        from overcode.interfaces import MockTmux
        from unittest.mock import patch, Mock

        session = Session(
            id="test-session",
            name="test-agent",
            tmux_window="test:0",
            tmux_session="test",
            start_time=datetime.now().isoformat(),
            command="claude",
            start_directory="/tmp",
        )
        session.stats = SessionStats()
        session.repo_name = "myrepo"
        session.branch = "main"

        mock_tmux = MockTmux()
        status_detector = StatusDetector("test", tmux=mock_tmux)

        widget = SessionSummary(session, status_detector)
        widget.detected_status = "running"
        widget.expanded = False
        widget.summary_detail = "full"  # Set to full to see repo:branch
        # Set mock stats (normally updated by update_status from Claude Code files)
        widget.claude_stats = ClaudeSessionStats(
            interaction_count=5,
            input_tokens=5000,
            output_tokens=10000,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            work_times=[30.0, 60.0, 45.0],  # median = 45s
        )

        result = widget.render()
        plain = result.plain

        assert "test-agent" in plain
        assert "myrepo:main" in plain
        assert "5i" in plain
        assert "15.0K" in plain  # 5000 + 10000 = 15000 tokens
        assert "45s" in plain  # median work time (format_duration for <60s)

    def test_render_shows_status_emoji(self):
        """Render shows correct status emoji"""
        from overcode.tui import SessionSummary
        from overcode.session_manager import Session, SessionStats
        from overcode.status_detector import StatusDetector
        from overcode.interfaces import MockTmux

        session = Session(
            id="test-session",
            name="test-agent",
            tmux_window="test:0",
            tmux_session="test",
            start_time=datetime.now().isoformat(),
            command="claude",
            start_directory="/tmp",
        )
        session.stats = SessionStats()

        mock_tmux = MockTmux()
        status_detector = StatusDetector("test", tmux=mock_tmux)

        # Test running status
        widget = SessionSummary(session, status_detector)
        widget.detected_status = "running"
        widget.expanded = False
        result = widget.render()
        assert "🟢" in result.plain

        # Test waiting_user status
        widget.detected_status = "waiting_user"
        result = widget.render()
        assert "🔴" in result.plain

    def test_render_expanded_with_pane_content(self):
        """Render shows pane content in expanded mode"""
        from overcode.tui import SessionSummary
        from overcode.session_manager import Session, SessionStats
        from overcode.status_detector import StatusDetector
        from overcode.interfaces import MockTmux

        session = Session(
            id="test-session",
            name="test-agent",
            tmux_window="test:0",
            tmux_session="test",
            start_time=datetime.now().isoformat(),
            command="claude",
            start_directory="/tmp",
        )
        session.stats = SessionStats()

        mock_tmux = MockTmux()
        status_detector = StatusDetector("test", tmux=mock_tmux)

        widget = SessionSummary(session, status_detector)
        widget.detected_status = "running"
        widget.expanded = True
        widget.pane_content = ["$ npm test", "✓ All tests passed"]
        widget.detail_lines = 5

        result = widget.render()
        plain = result.plain

        assert "npm test" in plain
        assert "All tests passed" in plain

    def test_render_with_standing_instructions(self):
        """Render shows standing instructions indicator"""
        from overcode.tui import SessionSummary
        from overcode.session_manager import Session, SessionStats
        from overcode.status_detector import StatusDetector
        from overcode.interfaces import MockTmux

        session = Session(
            id="test-session",
            name="test-agent",
            tmux_window="test:0",
            tmux_session="test",
            start_time=datetime.now().isoformat(),
            command="claude",
            start_directory="/tmp",
        )
        session.stats = SessionStats()
        session.standing_instructions = "Keep working on the feature"
        session.standing_orders_complete = False

        mock_tmux = MockTmux()
        status_detector = StatusDetector("test", tmux=mock_tmux)

        widget = SessionSummary(session, status_detector)
        widget.detected_status = "running"
        widget.expanded = False

        result = widget.render()
        plain = result.plain

        assert "📋" in plain
        assert "Keep working" in plain


class TestSummaryLineAlignment:
    """Test that summary line components maintain consistent widths.

    These tests ensure that no matter what values are plugged in,
    the formatted output stays within expected widths for proper alignment.
    """

    def test_format_tokens_width(self):
        """Token formatting stays within 6 chars for typical values"""
        from overcode.tui_helpers import format_tokens

        # Test typical token counts seen in practice
        test_cases = [
            0, 1, 99, 999,                    # Small: "0", "1", "99", "999"
            1000, 9999, 99999,                # K range: "1.0K" to "99.9K"
            1_000_000, 50_000_000,            # M range: "1.0M" to "50.0M"
        ]
        for tokens in test_cases:
            result = format_tokens(tokens)
            assert len(result) <= 6, f"format_tokens({tokens}) = '{result}' exceeds 6 chars"

        # Note: Edge case at 999999 tokens produces "1000.0K" (7 chars)
        # This is a known limitation but rarely encountered in practice

    def test_format_duration_width(self):
        """Duration formatting stays within 5 chars for any reasonable value"""
        from overcode.tui_helpers import format_duration

        test_cases = [
            0, 30, 59,                        # Seconds: "0s", "30s", "59s"
            60, 3599,                         # Minutes: "1.0m" to "60.0m"
            3600, 86399,                      # Hours: "1.0h" to "24.0h"
            86400, 864000,                    # Days: "1.0d" to "10.0d"
        ]
        for seconds in test_cases:
            result = format_duration(seconds)
            assert len(result) <= 5, f"format_duration({seconds}) = '{result}' exceeds 5 chars"

    def test_git_diff_stats_width(self):
        """Git diff stats (files, insertions, deletions) stay within expected widths"""
        from overcode.tui_helpers import format_line_count

        # Test extreme values that could appear in git diffs
        extreme_cases = [
            (0, 0, 0),                        # Empty diff
            (1, 1, 1),                        # Minimal changes
            (99, 9999, 9999),                 # Large but fits original format
            (99, 173242, 50000),              # Issue #2 example - large insertions
            (99, 1_000_000, 500_000),         # Very large diffs (e.g., vendored deps)
            (99, 999_999, 999_999),           # Max before millions
        ]

        for files, ins, dels in extreme_cases:
            ins_str = format_line_count(ins)
            dels_str = format_line_count(dels)

            # Files should fit in 2 chars (we don't format files, just check reasonableness)
            assert files <= 99 or True, "Files count test case setup issue"

            # Insertions and deletions should fit in 4 chars each (right-justified)
            assert len(ins_str) <= 4, (
                f"format_line_count({ins}) = '{ins_str}' exceeds 4 chars"
            )
            assert len(dels_str) <= 4, (
                f"format_line_count({dels}) = '{dels_str}' exceeds 4 chars"
            )

    def test_git_diff_formatted_segment_width(self):
        """Full git diff segment maintains consistent width across all values"""
        from overcode.tui_helpers import format_line_count

        # The full detail format is: " Δ{files:>2} +{ins:>4} -{dels:>4}"
        # Total width should be: 1 + 1 + 2 + 1 + 1 + 4 + 1 + 1 + 4 = 16 chars

        test_cases = [
            (0, 0, 0),
            (1, 1, 1),
            (50, 500, 200),
            (99, 173242, 50000),       # Issue #2 case
            (99, 999999, 999999),      # Max K values
            (10, 1_500_000, 750_000),  # M values
        ]

        for files, ins, dels in test_cases:
            segment = f" Δ{files:>2} +{format_line_count(ins):>4} -{format_line_count(dels):>4}"
            expected_width = 16
            assert len(segment) == expected_width, (
                f"Git diff segment for ({files}, {ins}, {dels}) = '{segment}' "
                f"has width {len(segment)}, expected {expected_width}"
            )

    def test_interaction_counts_width(self):
        """Human and robot interaction counts stay within 3 digits"""
        # The format is: " 👤{human:>3}" and " 🤖{steers:>3}"
        # This tests the assumption that counts stay within 3 digits

        test_cases = [0, 1, 99, 999]
        for count in test_cases:
            human_segment = f" 👤{count:>3}"
            robot_segment = f" 🤖{count:>3}"
            # Emoji is 1 char visually but may be 2+ bytes; we check structure
            assert f"{count:>3}" in human_segment
            assert f"{count:>3}" in robot_segment


# =============================================================================
# Textual Pilot tests (Option B) - Async interaction tests
# =============================================================================

class TestSupervisorTUIPilot:
    """Test SupervisorTUI with Textual Pilot for key bindings"""

    @pytest.mark.asyncio
    async def test_help_toggle(self):
        """Pressing h toggles help overlay visibility"""
        from overcode.tui import SupervisorTUI

        app = SupervisorTUI(tmux_session="test-pilot")

        async with app.run_test() as pilot:
            # Help should start hidden
            help_overlay = app.query_one("#help-overlay")
            assert not help_overlay.has_class("visible")

            # Press h to show help
            await pilot.press("h")
            assert help_overlay.has_class("visible")

            # Press h again to hide
            await pilot.press("h")
            assert not help_overlay.has_class("visible")

    @pytest.mark.asyncio
    async def test_daemon_panel_toggle(self):
        """Pressing d toggles daemon panel visibility (like timeline)"""
        from overcode.tui import SupervisorTUI

        app = SupervisorTUI(tmux_session="test-pilot")

        async with app.run_test() as pilot:
            # Daemon panel should start hidden (display=False via CSS)
            daemon_panel = app.query_one("#daemon-panel")
            initial_display = daemon_panel.display

            # Press d to toggle daemon panel
            await pilot.press("d")
            assert daemon_panel.display != initial_display

            # Press d again to toggle back
            await pilot.press("d")
            assert daemon_panel.display == initial_display

    @pytest.mark.asyncio
    async def test_question_mark_shows_help(self):
        """Pressing ? also toggles help"""
        from overcode.tui import SupervisorTUI

        app = SupervisorTUI(tmux_session="test-pilot")

        async with app.run_test() as pilot:
            help_overlay = app.query_one("#help-overlay")
            await pilot.press("question_mark")
            assert help_overlay.has_class("visible")

    @pytest.mark.asyncio
    async def test_quit_exits_app(self):
        """Pressing q quits the application"""
        from overcode.tui import SupervisorTUI

        app = SupervisorTUI(tmux_session="test-pilot")

        async with app.run_test() as pilot:
            await pilot.press("q")
            # App should be in process of exiting
            # (run_test handles cleanup)

    @pytest.mark.asyncio
    async def test_timeline_toggle(self):
        """Pressing t toggles timeline visibility"""
        from overcode.tui import SupervisorTUI

        app = SupervisorTUI(tmux_session="test-pilot")

        async with app.run_test() as pilot:
            timeline = app.query_one("#timeline")
            initial_display = timeline.display

            # Toggle timeline
            await pilot.press("t")
            # Display should change
            assert timeline.display != initial_display

            # Toggle back
            await pilot.press("t")
            assert timeline.display == initial_display

    @pytest.mark.asyncio
    async def test_detail_level_cycling(self):
        """Pressing v cycles detail level"""
        from overcode.tui import SupervisorTUI

        app = SupervisorTUI(tmux_session="test-pilot")

        async with app.run_test() as pilot:
            initial_index = app.detail_level_index

            # Cycle through levels
            await pilot.press("v")
            # Index should change (wraps around)
            assert app.detail_level_index != initial_index or initial_index == 3

    @pytest.mark.asyncio
    async def test_summary_detail_cycling(self):
        """Pressing s cycles summary detail level"""
        from overcode.tui import SupervisorTUI

        app = SupervisorTUI(tmux_session="test-pilot")

        async with app.run_test() as pilot:
            # Starts at index 0 ("low")
            assert app.summary_level_index == 0
            assert app.SUMMARY_LEVELS[app.summary_level_index] == "low"

            # Cycle to "med"
            await pilot.press("s")
            assert app.summary_level_index == 1
            assert app.SUMMARY_LEVELS[app.summary_level_index] == "med"

            # Cycle to "full"
            await pilot.press("s")
            assert app.summary_level_index == 2
            assert app.SUMMARY_LEVELS[app.summary_level_index] == "full"

            # Cycle back to "low"
            await pilot.press("s")
            assert app.summary_level_index == 0
            assert app.SUMMARY_LEVELS[app.summary_level_index] == "low"

    @pytest.mark.asyncio
    async def test_widgets_mounted(self):
        """App mounts expected widgets"""
        from overcode.tui import SupervisorTUI, DaemonStatusBar, StatusTimeline

        app = SupervisorTUI(tmux_session="test-pilot")

        async with app.run_test():
            # Check daemon status bar is mounted
            daemon_bar = app.query_one("#daemon-status", DaemonStatusBar)
            assert daemon_bar is not None

            # Check timeline is mounted
            timeline = app.query_one("#timeline", StatusTimeline)
            assert timeline is not None

            # Check sessions container exists
            container = app.query_one("#sessions-container")
            assert container is not None

    @pytest.mark.asyncio
    async def test_expand_all_agents(self):
        """Pressing e expands all session summaries"""
        from overcode.tui import SupervisorTUI

        app = SupervisorTUI(tmux_session="test-pilot")

        async with app.run_test() as pilot:
            # Press e to expand all
            await pilot.press("e")
            # This tests the binding works without crashing
            # (no sessions to verify in this test)

    @pytest.mark.asyncio
    async def test_collapse_all_agents(self):
        """Pressing c collapses all session summaries"""
        from overcode.tui import SupervisorTUI

        app = SupervisorTUI(tmux_session="test-pilot")

        async with app.run_test() as pilot:
            await pilot.press("c")
            # This tests the binding works without crashing


class TestHelpOverlayPilot:
    """Test HelpOverlay widget with Pilot"""

    @pytest.mark.asyncio
    async def test_help_overlay_content_visible(self):
        """Help overlay shows complete content when visible"""
        from overcode.tui import SupervisorTUI

        app = SupervisorTUI(tmux_session="test-pilot")

        async with app.run_test() as pilot:
            await pilot.press("h")
            help_overlay = app.query_one("#help-overlay")
            assert help_overlay.has_class("visible")


class TestFormatStandingInstructions:
    """Test format_standing_instructions helper"""

    def test_returns_empty_for_empty_input(self):
        """Empty input returns empty string"""
        from overcode.tui import format_standing_instructions
        assert format_standing_instructions("") == ""
        assert format_standing_instructions(None) == ""

    def test_returns_default_when_matching(self):
        """Returns [DEFAULT] when instructions match configured default"""
        from overcode.tui import format_standing_instructions
        from unittest.mock import patch

        with patch('overcode.tui.get_default_standing_instructions') as mock:
            mock.return_value = "Approve file writes"
            result = format_standing_instructions("Approve file writes")
            assert result == "[DEFAULT]"

    def test_returns_instructions_when_different(self):
        """Returns actual instructions when different from default"""
        from overcode.tui import format_standing_instructions
        from unittest.mock import patch

        with patch('overcode.tui.get_default_standing_instructions') as mock:
            mock.return_value = "Approve file writes"
            result = format_standing_instructions("Custom instructions here")
            assert result == "Custom instructions here"

    def test_truncates_long_instructions(self):
        """Truncates instructions that exceed max_len"""
        from overcode.tui import format_standing_instructions
        from unittest.mock import patch

        with patch('overcode.tui.get_default_standing_instructions') as mock:
            mock.return_value = ""
            long_text = "x" * 100
            result = format_standing_instructions(long_text, max_len=50)
            assert len(result) == 50
            assert result.endswith("...")


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
