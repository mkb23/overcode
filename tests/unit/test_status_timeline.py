"""
Unit tests for StatusTimeline widget.

Tests the label/timeline width calculations, timeline building logic,
and rendering in isolation without requiring a running Textual application.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock


# ---------------------------------------------------------------------------
# Helper to build a bare StatusTimeline without the Textual app
# ---------------------------------------------------------------------------

def _make_mock_session(name="agent-1"):
    """Create a minimal mock session with a name attribute."""
    mock = MagicMock()
    mock.name = name
    return mock


def _make_bare_timeline(sessions=None, **extra_attrs):
    """Create a StatusTimeline bypassing __init__ for unit-testing methods.

    Textual's ``app`` is a property that uses a ContextVar, so we create a
    test subclass that overrides it with a simple attribute.
    """
    from overcode.tui_widgets.status_timeline import StatusTimeline

    class _TestableStatusTimeline(StatusTimeline):
        _mock_app = MagicMock()

        @property
        def app(self):
            return self._mock_app

    widget = _TestableStatusTimeline.__new__(_TestableStatusTimeline)
    widget._id = "status-timeline"
    widget._is_mounted = False
    widget._running = False
    # Default attributes from __init__
    widget.sessions = sessions or []
    widget.tmux_session = "agents"
    widget._presence_history = []
    widget._agent_histories = {}
    widget.timeline_hours = 3.0
    # Each instance gets its own mock app
    widget._mock_app = MagicMock()
    widget._mock_app.baseline_minutes = 0
    # Apply overrides
    for k, v in extra_attrs.items():
        if k == "app":
            widget._mock_app = v
        else:
            setattr(widget, k, v)
    return widget


# ===========================================================================
# label_width property
# ===========================================================================


class TestLabelWidth:
    """Tests for StatusTimeline.label_width."""

    def test_no_sessions_returns_min(self):
        widget = _make_bare_timeline(sessions=[])
        assert widget.label_width == widget.MIN_NAME_WIDTH  # 6

    def test_short_names(self):
        sessions = [_make_mock_session("abc"), _make_mock_session("de")]
        widget = _make_bare_timeline(sessions=sessions)
        # Longest is 3, but min is 6
        assert widget.label_width == 6

    def test_medium_names(self):
        sessions = [_make_mock_session("agent-number-one"), _make_mock_session("ab")]
        widget = _make_bare_timeline(sessions=sessions)
        # "agent-number-one" is 16 chars
        assert widget.label_width == 16

    def test_very_long_name_capped(self):
        sessions = [_make_mock_session("a" * 50)]
        widget = _make_bare_timeline(sessions=sessions)
        assert widget.label_width == widget.MAX_NAME_WIDTH  # 30

    def test_exact_min_width(self):
        sessions = [_make_mock_session("abcdef")]  # 6 chars = MIN
        widget = _make_bare_timeline(sessions=sessions)
        assert widget.label_width == 6

    def test_exact_max_width(self):
        sessions = [_make_mock_session("a" * 30)]  # 30 chars = MAX
        widget = _make_bare_timeline(sessions=sessions)
        assert widget.label_width == 30


# ===========================================================================
# timeline_width property
# ===========================================================================


class TestTimelineWidth:
    """Tests for StatusTimeline.timeline_width."""

    def test_returns_positive_width(self):
        widget = _make_bare_timeline()
        width = widget.timeline_width
        assert isinstance(width, int)
        assert width >= widget.MIN_TIMELINE

    @patch("shutil.get_terminal_size")
    def test_calculates_from_terminal_size(self, mock_term_size):
        mock_term_size.return_value = MagicMock(columns=120)
        widget = _make_bare_timeline(sessions=[_make_mock_session("agent")])
        width = widget.timeline_width
        # 120 - label_width(6) - 3 - 5 - 2 = 104
        assert width == 104

    @patch("shutil.get_terminal_size")
    def test_narrow_terminal_respects_minimum(self, mock_term_size):
        mock_term_size.return_value = MagicMock(columns=30)
        widget = _make_bare_timeline(sessions=[_make_mock_session("agent")])
        width = widget.timeline_width
        assert width >= widget.MIN_TIMELINE

    @patch("shutil.get_terminal_size", side_effect=OSError)
    def test_oserror_returns_default(self, mock_term_size):
        widget = _make_bare_timeline()
        width = widget.timeline_width
        assert width == widget.DEFAULT_TIMELINE  # 60


# ===========================================================================
# _build_timeline
# ===========================================================================


class TestBuildTimeline:
    """Tests for StatusTimeline._build_timeline."""

    def test_empty_history_returns_dashes(self):
        widget = _make_bare_timeline()
        # Use a fixed timeline_width for predictability
        with patch.object(type(widget), 'timeline_width', new_callable=PropertyMock, return_value=10):
            result = widget._build_timeline([], lambda s: "X")
        assert result == "─" * 10

    def test_single_event_at_recent_time(self):
        """A single event within the timeline window should appear."""
        now = datetime.now()
        history = [(now - timedelta(minutes=5), "active")]
        widget = _make_bare_timeline(timeline_hours=1.0)
        with patch.object(type(widget), 'timeline_width', new_callable=PropertyMock, return_value=60):
            result = widget._build_timeline(history, lambda s: "A")
        # Should have exactly one 'A' character
        assert result.count("A") == 1
        assert len(result) == 60

    def test_event_before_window_excluded(self):
        """Events before the timeline window should be excluded."""
        now = datetime.now()
        history = [(now - timedelta(hours=5), "old_event")]
        widget = _make_bare_timeline(timeline_hours=1.0)
        with patch.object(type(widget), 'timeline_width', new_callable=PropertyMock, return_value=60):
            result = widget._build_timeline(history, lambda s: "X")
        assert "X" not in result

    def test_multiple_events(self):
        """Multiple events should appear in the timeline."""
        now = datetime.now()
        history = [
            (now - timedelta(minutes=50), "state1"),
            (now - timedelta(minutes=30), "state2"),
            (now - timedelta(minutes=10), "state3"),
        ]

        def state_to_char(s):
            return {"state1": "1", "state2": "2", "state3": "3"}[s]

        widget = _make_bare_timeline(timeline_hours=1.0)
        with patch.object(type(widget), 'timeline_width', new_callable=PropertyMock, return_value=60):
            result = widget._build_timeline(history, state_to_char)
        assert "1" in result
        assert "2" in result
        assert "3" in result
        assert len(result) == 60

    def test_state_to_char_function_called(self):
        """The state_to_char function should be called with the state value."""
        now = datetime.now()
        history = [(now - timedelta(minutes=30), 42)]
        calls = []

        def recorder(s):
            calls.append(s)
            return "X"

        widget = _make_bare_timeline(timeline_hours=1.0)
        with patch.object(type(widget), 'timeline_width', new_callable=PropertyMock, return_value=60):
            widget._build_timeline(history, recorder)
        assert 42 in calls


# ===========================================================================
# set_hours
# ===========================================================================


class TestSetHours:
    """Tests for StatusTimeline.set_hours."""

    def test_updates_hours_and_calls_update(self):
        widget = _make_bare_timeline(timeline_hours=3.0)
        widget.update_history = MagicMock()
        sessions = [_make_mock_session("a")]
        widget.set_hours(6.0, sessions)
        assert widget.timeline_hours == 6.0
        widget.update_history.assert_called_once_with(sessions)


# ===========================================================================
# apply_history_data
# ===========================================================================


class TestApplyHistoryData:
    """Tests for StatusTimeline.apply_history_data."""

    def test_stores_data_and_refreshes(self):
        widget = _make_bare_timeline()
        widget.refresh = MagicMock()
        sessions = [_make_mock_session("a")]
        presence = [(datetime.now(), 3)]
        agent_hist = {"a": [(datetime.now(), "running")]}

        widget.apply_history_data(sessions, presence, agent_hist)

        assert widget.sessions == sessions
        assert widget._presence_history == presence
        assert widget._agent_histories == agent_hist
        widget.refresh.assert_called_once_with(layout=True)


# ===========================================================================
# render — basic structure
# ===========================================================================


class TestStatusTimelineRender:
    """Tests for StatusTimeline.render output structure."""

    def test_render_empty_sessions(self):
        widget = _make_bare_timeline(sessions=[])
        # Mock app.baseline_minutes
        widget.app.baseline_minutes = 0
        with patch.object(type(widget), 'timeline_width', new_callable=PropertyMock, return_value=40):
            with patch.object(type(widget), 'label_width', new_callable=PropertyMock, return_value=6):
                result = widget.render()
        plain = result.plain
        assert "Timeline:" in plain
        assert "now" in plain
        assert "User:" in plain
        assert "Legend:" in plain

    def test_render_with_agent_no_history(self):
        sessions = [_make_mock_session("test-agent")]
        widget = _make_bare_timeline(sessions=sessions)
        widget.app.baseline_minutes = 0
        with patch.object(type(widget), 'timeline_width', new_callable=PropertyMock, return_value=40):
            with patch.object(type(widget), 'label_width', new_callable=PropertyMock, return_value=10):
                result = widget.render()
        plain = result.plain
        assert "test-agent" in plain
        # Should show dash percentage when no data
        assert "-" in plain

    def test_render_with_agent_history(self):
        now = datetime.now()
        sessions = [_make_mock_session("worker")]
        agent_hist = {"worker": [(now - timedelta(minutes=30), "running")]}
        widget = _make_bare_timeline(sessions=sessions, _agent_histories=agent_hist)
        widget.app.baseline_minutes = 0
        with patch.object(type(widget), 'timeline_width', new_callable=PropertyMock, return_value=40):
            with patch.object(type(widget), 'label_width', new_callable=PropertyMock, return_value=6):
                result = widget.render()
        plain = result.plain
        assert "worker" in plain
        # Should show a percentage (since there's one running slot)
        assert "%" in plain

    def test_render_legend_contains_key_entries(self):
        widget = _make_bare_timeline(sessions=[])
        widget.app.baseline_minutes = 0
        with patch.object(type(widget), 'timeline_width', new_callable=PropertyMock, return_value=40):
            with patch.object(type(widget), 'label_width', new_callable=PropertyMock, return_value=6):
                result = widget.render()
        plain = result.plain
        assert "active/running" in plain
        assert "waiting/away" in plain
        assert "terminated" in plain
        assert "heartbeat start" in plain

    def test_render_with_baseline_marker(self):
        widget = _make_bare_timeline(sessions=[], timeline_hours=3.0)
        widget.app.baseline_minutes = 60  # 1 hour baseline
        with patch.object(type(widget), 'timeline_width', new_callable=PropertyMock, return_value=40):
            with patch.object(type(widget), 'label_width', new_callable=PropertyMock, return_value=6):
                result = widget.render()
        plain = result.plain
        # Baseline marker should appear as "|"
        assert "|" in plain

    def test_render_without_baseline_no_marker(self):
        widget = _make_bare_timeline(sessions=[])
        widget.app.baseline_minutes = 0
        with patch.object(type(widget), 'timeline_width', new_callable=PropertyMock, return_value=40):
            with patch.object(type(widget), 'label_width', new_callable=PropertyMock, return_value=6):
                result = widget.render()
        plain = result.plain
        # The user row shouldn't have "|" baseline marker
        lines = plain.split("\n")
        user_line = [l for l in lines if "User:" in l]
        if user_line:
            assert "|" not in user_line[0]

    def test_render_no_app_context(self):
        """Should handle missing app context gracefully.

        The render method catches exceptions when accessing app.baseline_minutes
        via a try/except block.
        """
        from overcode.tui_widgets.status_timeline import StatusTimeline

        # Use the base class (no app override) — app property raises
        class _NoAppTimeline(StatusTimeline):
            @property
            def app(self):
                raise AttributeError("no app context")

        widget = _NoAppTimeline.__new__(_NoAppTimeline)
        widget._id = "no-app-timeline"
        widget._is_mounted = False
        widget._running = False
        widget.sessions = []
        widget.tmux_session = "agents"
        widget._presence_history = []
        widget._agent_histories = {}
        widget.timeline_hours = 3.0
        with patch.object(type(widget), 'timeline_width', new_callable=PropertyMock, return_value=40):
            with patch.object(type(widget), 'label_width', new_callable=PropertyMock, return_value=6):
                # Should not raise
                result = widget.render()
        assert result is not None

    def test_render_percentage_high_green(self):
        """Agent with mostly green slots shows high percentage."""
        now = datetime.now()
        sessions = [_make_mock_session("busy")]
        # Create many running entries
        agent_hist = {
            "busy": [
                (now - timedelta(minutes=i), "running")
                for i in range(1, 100)  # lots of running events
            ]
        }
        widget = _make_bare_timeline(sessions=sessions, _agent_histories=agent_hist)
        widget.app.baseline_minutes = 0
        with patch.object(type(widget), 'timeline_width', new_callable=PropertyMock, return_value=40):
            with patch.object(type(widget), 'label_width', new_callable=PropertyMock, return_value=6):
                result = widget.render()
        plain = result.plain
        # Should show some percentage
        assert "%" in plain


# ===========================================================================
# Constants
# ===========================================================================


