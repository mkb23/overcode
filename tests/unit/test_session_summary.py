"""Tests for session_summary widget module."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

from overcode.session_manager import Session, SessionStats
from overcode.history_reader import ClaudeSessionStats
from overcode.tui_widgets.session_summary import (
    format_standing_instructions,
    SessionSummary,
)


# ---------------------------------------------------------------------------
# Helpers to build mock objects
# ---------------------------------------------------------------------------

def _make_stats(**overrides) -> MagicMock:
    """Create a mock SessionStats with sensible defaults."""
    defaults = dict(
        current_state="running",
        state_since=None,
        green_time_seconds=100.0,
        non_green_time_seconds=50.0,
        sleep_time_seconds=0.0,
        steers_count=0,
        estimated_cost_usd=0.0,
        current_task="",
    )
    defaults.update(overrides)
    mock = MagicMock(spec=SessionStats)
    for k, v in defaults.items():
        setattr(mock, k, v)
    return mock


def _make_session(**overrides) -> MagicMock:
    """Create a mock Session with sensible defaults."""
    defaults = dict(
        id="test-id",
        name="test-agent",
        status="active",
        stats=_make_stats(),
        repo_name="test-repo",
        branch="main",
        standing_instructions=None,
        standing_orders_complete=False,
        standing_instructions_preset=None,
        is_asleep=False,
        time_context_enabled=False,
        agent_value=1000,
        permissiveness_mode="normal",
        start_time="2025-01-15T10:00:00",
        human_annotation=None,
        heartbeat_enabled=False,
        heartbeat_frequency_seconds=300,
        heartbeat_paused=False,
        last_heartbeat_time=None,
        heartbeat_instruction=None,
        tmux_window=1,
        start_directory="/tmp/test",
    )
    defaults.update(overrides)
    mock = MagicMock(spec=Session)
    for k, v in defaults.items():
        setattr(mock, k, v)
    return mock


def _make_bare_widget(**extra_attrs) -> SessionSummary:
    """Create a SessionSummary instance bypassing __init__.

    Sets the minimum attributes needed for unit-testing individual methods.
    Textual's reactive descriptor requires ``_id``, ``_is_mounted``, and
    ``_running`` to be present on the instance before reactive attributes
    (like ``summary_detail``) can be read or written.
    """
    widget = SessionSummary.__new__(SessionSummary)
    # Textual internals required for reactive attribute access
    widget._id = "test-widget"
    widget._is_mounted = False
    widget._running = False
    # Defaults that most methods expect to be present
    widget.session = _make_session()
    widget.detected_status = "running"
    widget.current_activity = ""
    widget.pane_content = []
    widget.claude_stats = None
    widget.git_diff_stats = None
    widget.background_bash_count = 0
    widget.live_subagent_count = 0
    widget.is_unvisited_stalled = False
    widget.monochrome = False
    widget.show_cost = False
    widget.any_has_budget = False
    widget._status_changed_at = None
    widget._last_known_status = "running"
    widget.summary_detail = "low"
    widget.summary_groups = {
        "time": True,
        "tokens": True,
        "git": True,
        "supervision": True,
        "priority": True,
        "performance": True,
        "subprocesses": True,
    }
    # Apply any caller-specified overrides
    for k, v in extra_attrs.items():
        setattr(widget, k, v)
    return widget


# ===========================================================================
# format_standing_instructions
# ===========================================================================


class TestFormatStandingInstructions:
    """Tests for the format_standing_instructions pure function."""

    def test_empty_string_returns_empty(self):
        """Empty string input returns empty string."""
        with patch("overcode.config.get_default_standing_instructions", return_value=""):
            assert format_standing_instructions("") == ""

    def test_none_returns_empty(self):
        """None input returns empty string."""
        with patch("overcode.config.get_default_standing_instructions", return_value=""):
            assert format_standing_instructions(None) == ""

    def test_default_instructions_returns_label(self):
        """Instructions matching the configured default show '[DEFAULT]'."""
        default_text = "Always run tests before committing"
        with patch(
            "overcode.config.get_default_standing_instructions",
            return_value=default_text,
        ):
            assert format_standing_instructions(default_text) == "[DEFAULT]"

    def test_default_instructions_with_surrounding_whitespace(self):
        """Whitespace-padded instructions still match the default."""
        default_text = "Always run tests"
        with patch(
            "overcode.config.get_default_standing_instructions",
            return_value=default_text,
        ):
            assert format_standing_instructions("  Always run tests  ") == "[DEFAULT]"

    def test_short_instructions_returned_as_is(self):
        """Instructions within max_len are returned unchanged."""
        text = "Fix the login bug"
        with patch(
            "overcode.config.get_default_standing_instructions",
            return_value="",
        ):
            assert format_standing_instructions(text) == text

    def test_long_instructions_are_truncated(self):
        """Instructions exceeding max_len are truncated with ellipsis."""
        text = "A" * 100  # Longer than default max_len=95
        with patch(
            "overcode.config.get_default_standing_instructions",
            return_value="",
        ):
            result = format_standing_instructions(text)
            assert result.endswith("...")
            assert len(result) == 95

    def test_custom_max_len(self):
        """Custom max_len is respected."""
        text = "A" * 50
        with patch(
            "overcode.config.get_default_standing_instructions",
            return_value="",
        ):
            result = format_standing_instructions(text, max_len=30)
            assert result.endswith("...")
            assert len(result) == 30

    def test_exact_max_len_not_truncated(self):
        """Instructions exactly at max_len are not truncated."""
        text = "A" * 95
        with patch(
            "overcode.config.get_default_standing_instructions",
            return_value="",
        ):
            result = format_standing_instructions(text)
            assert result == text
            assert "..." not in result

    def test_no_default_configured_does_not_match(self):
        """When no default is configured, non-empty instructions are returned as-is."""
        with patch(
            "overcode.config.get_default_standing_instructions",
            return_value="",
        ):
            result = format_standing_instructions("some instructions")
            assert result == "some instructions"


# ===========================================================================
# group_enabled
# ===========================================================================


class TestGroupEnabled:
    """Tests for SessionSummary.group_enabled."""

    def test_non_custom_mode_always_returns_true(self):
        """In low/med/full modes all groups are enabled regardless of settings."""
        for mode in ("low", "med", "full"):
            widget = _make_bare_widget(summary_detail=mode)
            widget.summary_groups = {"time": False, "tokens": False}
            assert widget.group_enabled("time") is True
            assert widget.group_enabled("tokens") is True

    def test_custom_mode_respects_enabled_group(self):
        """In custom mode, an enabled group returns True."""
        widget = _make_bare_widget(
            summary_detail="custom",
            summary_groups={"time": True},
        )
        assert widget.group_enabled("time") is True

    def test_custom_mode_respects_disabled_group(self):
        """In custom mode, a disabled group returns False."""
        widget = _make_bare_widget(
            summary_detail="custom",
            summary_groups={"tokens": False},
        )
        assert widget.group_enabled("tokens") is False

    def test_custom_mode_unknown_group_defaults_true(self):
        """Unknown group IDs default to True even in custom mode."""
        widget = _make_bare_widget(
            summary_detail="custom",
            summary_groups={},
        )
        assert widget.group_enabled("nonexistent_group") is True

    def test_custom_mode_multiple_groups_mixed(self):
        """In custom mode, each group is checked independently."""
        widget = _make_bare_widget(
            summary_detail="custom",
            summary_groups={
                "time": True,
                "tokens": False,
                "git": True,
                "supervision": False,
                "priority": True,
                "performance": False,
            },
        )
        assert widget.group_enabled("time") is True
        assert widget.group_enabled("tokens") is False
        assert widget.group_enabled("git") is True
        assert widget.group_enabled("supervision") is False
        assert widget.group_enabled("priority") is True
        assert widget.group_enabled("performance") is False


# ===========================================================================
# apply_status_no_refresh
# ===========================================================================


class TestApplyStatusNoRefresh:
    """Tests for SessionSummary.apply_status_no_refresh."""

    def test_updates_current_activity(self):
        """Activity string is stored on the widget."""
        widget = _make_bare_widget()
        widget.apply_status_no_refresh("running", "Editing files", "", None, None)
        assert widget.current_activity == "Editing files"

    def test_parses_pane_content_into_lines(self):
        """Content is split into lines and stored (last 200 max)."""
        content = "\n".join(f"line {i}" for i in range(300))
        widget = _make_bare_widget()
        widget.apply_status_no_refresh("running", "", content, None, None)
        assert len(widget.pane_content) == 200
        assert widget.pane_content[-1] == "line 299"
        assert widget.pane_content[0] == "line 100"

    def test_empty_content_clears_pane_and_counts(self):
        """Empty/falsy content resets pane_content and live counts to zero."""
        widget = _make_bare_widget()
        widget.pane_content = ["old line"]
        widget.background_bash_count = 3
        widget.live_subagent_count = 2
        widget.apply_status_no_refresh("running", "", "", None, None)
        assert widget.pane_content == []
        assert widget.background_bash_count == 0
        assert widget.live_subagent_count == 0

    def test_asleep_session_overrides_status(self):
        """If the session is_asleep, detected_status is set to 'asleep'."""
        session = _make_session(is_asleep=True)
        widget = _make_bare_widget(session=session)
        widget.apply_status_no_refresh("running", "Active work", "", None, None)
        assert widget.detected_status == "asleep"

    def test_status_change_updates_timestamp(self):
        """When the status changes, _status_changed_at is updated."""
        widget = _make_bare_widget()
        widget._last_known_status = "running"
        before = datetime.now()
        widget.apply_status_no_refresh("waiting_user", "Waiting", "", None, None)
        after = datetime.now()
        assert widget._status_changed_at is not None
        assert before <= widget._status_changed_at <= after
        assert widget._last_known_status == "waiting_user"

    def test_same_status_does_not_update_timestamp(self):
        """If the status has not changed, _status_changed_at remains unchanged."""
        widget = _make_bare_widget()
        widget._last_known_status = "running"
        widget._status_changed_at = None
        widget.apply_status_no_refresh("running", "Still working", "", None, None)
        assert widget._status_changed_at is None

    def test_claude_stats_stored_when_provided(self):
        """Pre-fetched ClaudeSessionStats are saved on the widget."""
        stats = MagicMock(spec=ClaudeSessionStats)
        widget = _make_bare_widget()
        widget.apply_status_no_refresh("running", "", "", stats, None)
        assert widget.claude_stats is stats

    def test_claude_stats_not_overwritten_when_none(self):
        """Passing None for claude_stats does not clear a previously set value."""
        old_stats = MagicMock(spec=ClaudeSessionStats)
        widget = _make_bare_widget()
        widget.claude_stats = old_stats
        widget.apply_status_no_refresh("running", "", "", None, None)
        assert widget.claude_stats is old_stats

    def test_git_diff_stats_stored_when_provided(self):
        """Pre-fetched git diff stats tuple is saved."""
        diff = (5, 100, 20)
        widget = _make_bare_widget()
        widget.apply_status_no_refresh("running", "", "", None, diff)
        assert widget.git_diff_stats == (5, 100, 20)

    def test_git_diff_stats_not_overwritten_when_none(self):
        """Passing None for git_diff_stats does not clear a previously set value."""
        widget = _make_bare_widget()
        widget.git_diff_stats = (3, 50, 10)
        widget.apply_status_no_refresh("running", "", "", None, None)
        assert widget.git_diff_stats == (3, 50, 10)

    @patch("overcode.tui_widgets.session_summary.extract_background_bash_count", return_value=3)
    @patch("overcode.tui_widgets.session_summary.extract_live_subagent_count", return_value=2)
    def test_extracts_live_counts_from_content(self, mock_sub, mock_bash):
        """Background bash and subagent counts are extracted from content."""
        widget = _make_bare_widget()
        widget.apply_status_no_refresh("running", "", "some pane content", None, None)
        assert widget.background_bash_count == 3
        assert widget.live_subagent_count == 2


# ===========================================================================
# Message classes
# ===========================================================================


class TestMessageClasses:
    """Tests for the Textual message classes on SessionSummary."""

    def test_session_selected_stores_id(self):
        """SessionSelected message stores session_id."""
        msg = SessionSummary.SessionSelected("abc-123")
        assert msg.session_id == "abc-123"

    def test_expanded_changed_stores_id_and_state(self):
        """ExpandedChanged message stores session_id and expanded flag."""
        msg = SessionSummary.ExpandedChanged("abc-123", True)
        assert msg.session_id == "abc-123"
        assert msg.expanded is True

        msg2 = SessionSummary.ExpandedChanged("xyz", False)
        assert msg2.session_id == "xyz"
        assert msg2.expanded is False

    def test_stalled_agent_visited_stores_id(self):
        """StalledAgentVisited message stores session_id."""
        msg = SessionSummary.StalledAgentVisited("stalled-1")
        assert msg.session_id == "stalled-1"
