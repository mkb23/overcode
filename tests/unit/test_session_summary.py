"""Tests for session_summary widget module."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

from overcode.session_manager import Session, SessionStats
from overcode.history_reader import ClaudeSessionStats
from overcode.tui_widgets.session_summary import (
    SessionSummary,
    _scraped_recap_from_stats,
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
        enhanced_context_enabled=False,
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
    widget.bash_count_ambiguous = False
    widget.live_subagent_count = 0
    widget.pr_number = None
    widget.any_has_pr = False
    widget.is_unvisited_stalled = False
    widget.monochrome = False
    widget.show_cost = "tokens"
    widget.any_has_budget = False
    widget._status_changed_at = None
    widget._last_known_status = "running"
    widget.summary_detail = "low"
    widget.column_overrides = {}
    # Apply any caller-specified overrides
    for k, v in extra_attrs.items():
        setattr(widget, k, v)
    return widget


# ===========================================================================
# column_visible
# ===========================================================================


class TestColumnVisible:
    """Tests for SessionSummary.column_visible with per-level overrides."""

    def test_full_mode_defaults_visible(self):
        """In full mode all columns default to visible (no overrides)."""
        from overcode.summary_columns import SUMMARY_COLUMNS
        widget = _make_bare_widget(summary_detail="full")
        widget.column_overrides = {}
        for col in SUMMARY_COLUMNS:
            assert widget.column_visible(col) is True

    def test_full_mode_respects_false_overrides(self):
        """In full mode, explicit False overrides still hide columns."""
        from overcode.summary_columns import SUMMARY_COLUMNS
        widget = _make_bare_widget(summary_detail="full")
        widget.column_overrides = {"uptime": False}
        uptime_col = next(c for c in SUMMARY_COLUMNS if c.id == "uptime")
        assert widget.column_visible(uptime_col) is False

    def test_default_visibility_from_detail_levels(self):
        """Without overrides, visibility comes from detail_levels."""
        from overcode.summary_columns import SUMMARY_COLUMNS
        widget = _make_bare_widget(summary_detail="low")
        widget.column_overrides = {}
        # status_symbol has ALL detail_levels, should be visible in low
        status_col = next(c for c in SUMMARY_COLUMNS if c.id == "status_symbol")
        assert widget.column_visible(status_col) is True
        # uptime has MED_PLUS, should not be visible in low
        uptime_col = next(c for c in SUMMARY_COLUMNS if c.id == "uptime")
        assert widget.column_visible(uptime_col) is False

    def test_override_adds_column(self):
        """Override can add a column not in default detail_levels."""
        from overcode.summary_columns import SUMMARY_COLUMNS
        widget = _make_bare_widget(summary_detail="low")
        widget.column_overrides = {"uptime": True}
        uptime_col = next(c for c in SUMMARY_COLUMNS if c.id == "uptime")
        assert widget.column_visible(uptime_col) is True

    def test_override_removes_column(self):
        """Override can remove a column that is in default detail_levels."""
        from overcode.summary_columns import SUMMARY_COLUMNS
        widget = _make_bare_widget(summary_detail="med")
        widget.column_overrides = {"uptime": False}
        uptime_col = next(c for c in SUMMARY_COLUMNS if c.id == "uptime")
        assert widget.column_visible(uptime_col) is False

    def test_high_level_includes_subprocess_columns(self):
        """High detail level should include HIGH_PLUS columns like subagent_count."""
        from overcode.summary_columns import SUMMARY_COLUMNS
        widget = _make_bare_widget(summary_detail="high")
        widget.column_overrides = {}
        sub_col = next(c for c in SUMMARY_COLUMNS if c.id == "subagent_count")
        assert widget.column_visible(sub_col) is True


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
        """Content is split into lines and stored (no artificial cap)."""
        content = "\n".join(f"line {i}" for i in range(300))
        widget = _make_bare_widget()
        widget.apply_status_no_refresh("running", "", content, None, None)
        assert len(widget.pane_content) == 300
        assert widget.pane_content[-1] == "line 299"
        assert widget.pane_content[0] == "line 0"

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

    @patch("overcode.tui_widgets.session_summary.extract_from_pane")
    def test_extracts_live_counts_from_content(self, mock_extract):
        """Background bash and subagent counts are extracted from content."""
        from overcode.status_patterns import PaneExtraction
        mock_extract.return_value = PaneExtraction(
            background_bash_count=3, live_subagent_count=2, pr_number=None,
        )
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

    def test_stalled_agent_visited_stores_id(self):
        """StalledAgentVisited message stores session_id."""
        msg = SessionSummary.StalledAgentVisited("stalled-1")
        assert msg.session_id == "stalled-1"


class TestScrapedRecapFromStats:
    """Tests for _scraped_recap_from_stats helper (#440)."""

    def test_returns_none_when_stats_missing(self):
        assert _scraped_recap_from_stats(None) is None

    def test_returns_none_for_empty_current_task(self):
        assert _scraped_recap_from_stats(_make_stats(current_task="")) is None

    def test_returns_none_for_initializing_placeholder(self):
        assert _scraped_recap_from_stats(_make_stats(current_task="Initializing...")) is None

    def test_returns_none_for_idle_placeholder(self):
        assert _scraped_recap_from_stats(_make_stats(current_task="Idle")) is None

    def test_returns_task_when_real(self):
        assert _scraped_recap_from_stats(_make_stats(current_task="Running tests")) == "Running tests"

    def test_strips_whitespace(self):
        assert _scraped_recap_from_stats(_make_stats(current_task="  Wrote file.py  ")) == "Wrote file.py"
