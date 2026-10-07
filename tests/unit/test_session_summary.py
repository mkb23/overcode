"""Tests for session_summary widget module."""

from datetime import datetime
from unittest.mock import MagicMock, patch

from overcode.session_manager import Session, SessionStats
from overcode.history_reader import AgentSessionStats
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
    widget.git_untracked_count = None
    widget.background_bash_count = 0
    widget.bash_count_ambiguous = False
    widget.live_subagent_count = 0
    widget.auto_accept_mode = False
    widget.pr_number = None
    widget.any_has_pr = False
    widget.uniform_columns = {}
    widget.is_unvisited_stalled = False
    widget.monochrome = False
    widget.show_cost = "tokens"
    widget.any_has_budget = False
    widget._status_changed_at = None
    widget.engine = {}
    widget.local_pane = False
    widget.file_subagent_count = 0
    widget.window_burn = None
    widget.subtree_cost_usd = 0.0
    widget.last_command = ""
    widget._overlay_key = None
    widget._overlay = None
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
# apply_engine: the row is the engine's published view (0.6.0)
# ===========================================================================


def _view(**fields):
    base = dict(
        session_id="test-id", name="test-agent", current_status="running",
        current_activity="Editing files", status_since="2026-10-07T12:00:00",
    )
    base.update(fields)
    return base


class TestApplyEngine:
    """SessionSummary.apply_engine: status, stats, git, burn and attention from a view."""

    def test_status_and_activity(self):
        widget = _make_bare_widget()
        assert widget.apply_engine(_view(current_status="waiting_user",
                                         current_activity="Waiting")) is True
        assert (widget.detected_status, widget.current_activity) == ("waiting_user", "Waiting")

    def test_unchanged_view_reports_no_change(self):
        widget = _make_bare_widget()
        widget.apply_engine(_view())
        assert widget.apply_engine(_view()) is False

    def test_asleep_session_overrides_status(self):
        """The local z toggle shows at once, before the engine says asleep."""
        widget = _make_bare_widget(session=_make_session(is_asleep=True))
        widget.apply_engine(_view(current_status="running"))
        assert widget.detected_status == "asleep"

    def test_terminated_wins(self):
        widget = _make_bare_widget(session=_make_session(is_asleep=True))
        widget.apply_engine(_view(current_status="terminated"))
        assert widget.detected_status == "terminated"

    def test_stats_render_only_when_the_backend_has_them(self):
        widget = _make_bare_widget()
        widget.apply_engine(_view(stats_available=False, input_tokens=5))
        assert widget.claude_stats is None
        widget.apply_engine(_view(stats_available=True, input_tokens=1000, output_tokens=500,
                                  interaction_count=7, work_median_seconds=90.0,
                                  current_context_tokens=50_000, context_window=200_000,
                                  file_subagent_count=2))
        stats = widget.claude_stats
        assert isinstance(stats, AgentSessionStats)
        assert stats.total_tokens == 1500
        assert stats.interaction_count == 7
        assert stats.median_work_time == 90.0
        assert stats.max_context_tokens == 200_000
        assert widget.file_subagent_count == 2

    def test_git_columns(self):
        widget = _make_bare_widget()
        widget.apply_engine(_view(git_diff=[5, 100, 20], git_untracked=3))
        assert widget.git_diff_stats == (5, 100, 20)
        assert widget.git_untracked_count == 3
        widget.apply_engine(_view(git_diff=None, git_untracked=None))  # not read yet
        assert widget.git_diff_stats == (5, 100, 20)

    def test_pane_derived_counts_come_from_the_engine(self):
        widget = _make_bare_widget()
        widget.apply_engine(_view(background_bash_count=3, live_subagent_count=2,
                                  auto_accept_mode=True, bash_count_ambiguous=False))
        assert (widget.background_bash_count, widget.live_subagent_count,
                widget.auto_accept_mode) == (3, 2, True)

    def test_the_focused_capture_keeps_its_own_pane_counts(self):
        widget = _make_bare_widget(local_pane=True, background_bash_count=1)
        widget.apply_engine(_view(background_bash_count=4))
        assert widget.background_bash_count == 1
        widget.end_local_pane()
        assert widget.background_bash_count == 4

    def test_burn_for_the_asked_window(self):
        widget = _make_bare_widget()
        widget.apply_engine(_view(burn={"1.0": {"input_tokens": 3000, "output_tokens": 600,
                                                "cost_usd": 1.5, "energy_j": 7200.0}}),
                            burn_hours=1.0)
        assert widget.window_burn.tokens_per_hour == 3600
        assert widget.window_burn.cost_per_hour == 1.5
        assert widget.window_burn.watts == 2.0
        widget.apply_engine(widget.engine, burn_hours=0.5)  # not computed for 30 min
        assert widget.window_burn is None

    def test_time_in_state_follows_the_recorded_episode(self):
        widget = _make_bare_widget()
        widget.apply_engine(_view(live_colour="green", episode_colour="green",
                                  episode_start=1_000.0, live_since=1_000.0))
        assert widget._status_changed_at == datetime.fromtimestamp(1_000.0)
        # A pending excursion: the timer shows the live colour's own start
        widget.apply_engine(_view(live_colour="red", episode_colour="green",
                                  episode_start=1_000.0, live_since=1_500.0))
        assert widget._status_changed_at == datetime.fromtimestamp(1_500.0)

    def test_unvisited_from_the_attention_fields(self):
        widget = _make_bare_widget()
        widget.apply_engine(_view(input_needed_since=200.0, visited_at=100.0))
        assert widget.is_unvisited_stalled is True
        widget.apply_engine(_view(input_needed_since=200.0, visited_at=300.0))
        assert widget.is_unvisited_stalled is False
        # A visit this TUI made that the engine has not published yet
        widget.apply_engine(_view(input_needed_since=200.0, visited_at=100.0), visited_here=250.0)
        assert widget.is_unvisited_stalled is False

    def test_badges_count_down_from_eta_at(self):
        widget = _make_bare_widget()
        widget.apply_engine(_view(status_detail={
            "color": "yellow", "legacy_status": "waiting_user",
            "badges": [{"kind": "schedule_wakeup", "eta_at": 1_300}],
        }))
        with patch("overcode.tui_widgets.session_summary.time.time", return_value=1_000.0):
            detail = widget.status_detail
        assert detail.color == "yellow"
        assert detail.badges[0].eta_seconds == 300

    def test_the_row_draws_the_engines_session_values(self):
        session = Session(id="test-id", name="test-agent", tmux_session="agents",
                          tmux_window="test-agent", command=["claude"], start_directory=None,
                          start_time="2026-10-07T11:00:00", model="claude-sonnet-4")
        widget = _make_bare_widget(session=session)
        widget.apply_engine(_view(model="claude-opus-4", cpu_percent=12.5,
                                  estimated_cost_usd=2.5, steers_count=3,
                                  time_base=[10.0, 20.0, 0.0], time_base_at=1_000.0))
        shown = widget.rendered_session()
        assert shown.model == "claude-opus-4"
        assert shown.cpu_percent == 12.5
        assert shown.stats.estimated_cost_usd == 2.5
        assert shown.stats.steers_count == 3
        assert shown.stats.green_time_seconds == 10.0
        assert shown.stats.current_task == "Editing files"
        assert widget.session.model != "claude-opus-4"  # the shared snapshot is untouched


class TestApplyPaneContent:
    """The focused capture, or a sister's polled pane: lines and pane-derived counts."""

    def test_parses_pane_content_into_lines(self):
        content = "\n".join(f"line {i}" for i in range(300))
        widget = _make_bare_widget()
        assert widget.apply_pane_content(content) is True
        assert len(widget.pane_content) == 300
        assert widget.pane_content[-1] == "line 299"

    def test_empty_content_clears_pane_and_counts(self):
        widget = _make_bare_widget()
        widget.pane_content = ["old line"]
        widget.background_bash_count = 3
        widget.live_subagent_count = 2
        widget.apply_pane_content("")
        assert widget.pane_content == []
        assert widget.background_bash_count == 0
        assert widget.live_subagent_count == 0

    @patch("overcode.tui_widgets.session_summary.extract_from_pane")
    def test_extracts_live_counts_from_content(self, mock_extract):
        from overcode.status_patterns import PaneExtraction
        mock_extract.return_value = PaneExtraction(
            background_bash_count=3, live_subagent_count=2, pr_number=None,
        )
        widget = _make_bare_widget()
        widget.apply_pane_content("some pane content")
        assert widget.background_bash_count == 3
        assert widget.live_subagent_count == 2

    def test_unchanged_content_is_no_change(self):
        widget = _make_bare_widget()
        widget.apply_pane_content("x")
        assert widget.apply_pane_content("x") is False


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


class TestRefreshIfChanged:
    """#486: the 250 ms tick refreshes only rows that would draw differently."""

    @staticmethod
    def _widget(row_text):
        from rich.text import Text
        widget = _make_bare_widget(_last_rendered=None)
        widget.refresh = MagicMock()
        widget._render_row = MagicMock(side_effect=lambda: Text(row_text[0]))
        return widget

    def test_first_tick_refreshes(self):
        widget = self._widget(["row"])
        assert widget.refresh_if_changed() is True
        widget.refresh.assert_called_once()

    def test_unchanged_row_is_skipped(self):
        widget = self._widget(["row"])
        widget.render()  # what is on screen
        assert widget.refresh_if_changed() is False
        widget.refresh.assert_not_called()

    def test_changed_row_refreshes(self):
        text = ["  7s working"]
        widget = self._widget(text)
        widget.render()
        text[0] = "  8s working"
        assert widget.refresh_if_changed() is True
        widget.refresh.assert_called_once()

    def test_compares_against_what_render_last_drew(self):
        # A refresh from elsewhere (focus, resize) redraws the row; the
        # next tick compares against that drawing
        text = ["a"]
        widget = self._widget(text)
        widget.render()
        text[0] = "b"
        widget.render()
        assert widget.refresh_if_changed() is False
