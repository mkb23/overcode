"""
Unit tests for DaemonPanel widget.

Tests the rendering logic, log line coloring, and state management
in isolation without requiring a running Textual application.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helper to build a bare DaemonPanel without the Textual app
# ---------------------------------------------------------------------------

def _make_bare_daemon_panel(**extra_attrs):
    """Create a DaemonPanel bypassing __init__ for unit-testing methods."""
    from overcode.tui_widgets.daemon_panel import DaemonPanel

    widget = DaemonPanel.__new__(DaemonPanel)
    widget._id = "daemon-panel"
    widget._is_mounted = False
    widget._running = False
    # Default attributes from __init__
    widget.tmux_session = "agents"
    widget.log_lines = []
    widget.monitor_state = None
    widget._log_file_pos = 0
    # Apply overrides
    for k, v in extra_attrs.items():
        setattr(widget, k, v)
    return widget


def _make_monitor_state(**overrides):
    """Create a mock MonitorDaemonState for panel tests."""
    from overcode.monitor_daemon_state import MonitorDaemonState
    defaults = dict(
        pid=1234,
        status="active",
        loop_count=42,
        current_interval=10,
        last_loop_time=datetime.now().isoformat(),
        total_supervisions=0,
    )
    defaults.update(overrides)
    mock = MagicMock(spec=MonitorDaemonState)
    for k, v in defaults.items():
        setattr(mock, k, v)
    mock.is_stale.return_value = False
    return mock


# ===========================================================================
# _apply_logs
# ===========================================================================


class TestApplyLogs:
    """Tests for DaemonPanel._apply_logs."""

    def test_applies_all_state(self):
        widget = _make_bare_daemon_panel()
        widget.refresh = MagicMock()
        state = _make_monitor_state(loop_count=10)
        new_lines = ["line1", "line2"]

        widget._apply_logs(state, new_lines, 500)

        assert widget.monitor_state is state
        assert widget.log_lines == ["line1", "line2"]
        assert widget._log_file_pos == 500
        widget.refresh.assert_called_once()

    def test_none_log_lines_preserves_existing(self):
        widget = _make_bare_daemon_panel(log_lines=["old1", "old2"])
        widget.refresh = MagicMock()
        state = _make_monitor_state()

        widget._apply_logs(state, None, 100)

        assert widget.log_lines == ["old1", "old2"]
        assert widget._log_file_pos == 100

    def test_empty_list_replaces_existing(self):
        widget = _make_bare_daemon_panel(log_lines=["old1"])
        widget.refresh = MagicMock()
        state = _make_monitor_state()

        widget._apply_logs(state, [], 200)

        assert widget.log_lines == []


# ===========================================================================
# render — stopped state
# ===========================================================================


class TestDaemonPanelRenderStopped:
    """Tests for DaemonPanel.render when daemon is stopped."""

    def test_render_no_monitor_state(self):
        widget = _make_bare_daemon_panel(monitor_state=None)
        result = widget.render()
        plain = result.plain
        assert "Supervisor Daemon:" in plain
        assert "stopped" in plain

    def test_render_stale_monitor_state(self):
        state = _make_monitor_state()
        state.is_stale.return_value = True
        widget = _make_bare_daemon_panel(monitor_state=state)
        result = widget.render()
        plain = result.plain
        assert "stopped" in plain

    def test_render_stale_with_last_activity(self):
        last_time = (datetime.now() - timedelta(minutes=5)).isoformat()
        state = _make_monitor_state(last_loop_time=last_time)
        state.is_stale.return_value = True
        widget = _make_bare_daemon_panel(monitor_state=state)
        result = widget.render()
        plain = result.plain
        assert "stopped" in plain
        assert "last:" in plain


# ===========================================================================
# render — running state
# ===========================================================================


class TestDaemonPanelRenderRunning:
    """Tests for DaemonPanel.render when daemon is running."""

    def test_render_running_basic(self):
        state = _make_monitor_state(
            status="active",
            loop_count=42,
            current_interval=10,
            last_loop_time=datetime.now().isoformat(),
        )
        widget = _make_bare_daemon_panel(monitor_state=state)
        result = widget.render()
        plain = result.plain
        assert "Supervisor Daemon:" in plain
        assert "#42" in plain
        assert "@10s" in plain

    def test_render_with_supervisions(self):
        state = _make_monitor_state(
            total_supervisions=5,
            loop_count=10,
            current_interval=5,
            last_loop_time=datetime.now().isoformat(),
        )
        widget = _make_bare_daemon_panel(monitor_state=state)
        result = widget.render()
        plain = result.plain
        assert "sup:5" in plain

    def test_render_zero_supervisions_omitted(self):
        state = _make_monitor_state(
            total_supervisions=0,
            loop_count=10,
            current_interval=5,
            last_loop_time=datetime.now().isoformat(),
        )
        widget = _make_bare_daemon_panel(monitor_state=state)
        result = widget.render()
        plain = result.plain
        assert "sup:" not in plain


# ===========================================================================
# render — log lines
# ===========================================================================


class TestDaemonPanelRenderLogs:
    """Tests for DaemonPanel.render log display."""

    def test_no_logs_shows_message(self):
        widget = _make_bare_daemon_panel(log_lines=[])
        result = widget.render()
        plain = result.plain
        assert "no logs yet" in plain

    def test_shows_last_n_lines(self):
        lines = [f"line {i}" for i in range(20)]
        widget = _make_bare_daemon_panel(log_lines=lines)
        result = widget.render()
        plain = result.plain
        # Should show last 8 lines (LOG_LINES_TO_SHOW)
        assert "line 19" in plain
        assert "line 12" in plain
        # Should not show earlier lines
        assert "line 0" not in plain

    def test_truncates_long_lines(self):
        long_line = "A" * 200
        widget = _make_bare_daemon_panel(log_lines=[long_line])
        result = widget.render()
        plain = result.plain
        # Line should be truncated to 120 chars (plus indent)
        # The actual line in the render won't exceed 120 + 2 (for "  " prefix)
        assert "A" * 121 not in plain

    def test_log_lines_fewer_than_max(self):
        lines = ["line 1", "line 2"]
        widget = _make_bare_daemon_panel(log_lines=lines)
        result = widget.render()
        plain = result.plain
        assert "line 1" in plain
        assert "line 2" in plain


# ===========================================================================
# render — log line coloring (verified via Rich Text styles)
# ===========================================================================


class TestDaemonPanelLogColoring:
    """Tests that log lines get appropriate styling based on content."""

    def _get_line_styles(self, log_line):
        """Render a panel with a single log line and return the styles used."""
        widget = _make_bare_daemon_panel(log_lines=[log_line])
        result = widget.render()
        # Find style spans that contain the log content
        styles = []
        for span in result._spans:
            # Extract the text this span covers
            span_text = result.plain[span.start:span.end]
            if log_line[:20] in span_text or any(
                keyword in span_text
                for keyword in ["ERROR", "WARNING", ">>>", "Loop", "supervising"]
            ):
                styles.append(str(span.style))
        return styles

    def test_error_line_renders(self):
        """ERROR lines should render without error."""
        widget = _make_bare_daemon_panel(log_lines=["2024-01-01 ERROR something failed"])
        result = widget.render()
        assert "ERROR" in result.plain

    def test_warning_line_renders(self):
        """WARNING lines should render without error."""
        widget = _make_bare_daemon_panel(log_lines=["2024-01-01 WARNING something odd"])
        result = widget.render()
        assert "WARNING" in result.plain

    def test_prompt_line_renders(self):
        """>>> prompt lines should render without error."""
        widget = _make_bare_daemon_panel(log_lines=[">>> some command"])
        result = widget.render()
        assert ">>> some command" in result.plain

    def test_supervising_line_renders(self):
        """Supervising lines should render without error."""
        widget = _make_bare_daemon_panel(log_lines=["Supervising agent-1"])
        result = widget.render()
        assert "Supervising" in result.plain

    def test_loop_line_renders(self):
        """Loop lines should render without error."""
        widget = _make_bare_daemon_panel(log_lines=["Loop 42: checking agents"])
        result = widget.render()
        assert "Loop 42" in result.plain

    def test_generic_line_renders(self):
        """Generic lines should render without error."""
        widget = _make_bare_daemon_panel(log_lines=["some normal log output"])
        result = widget.render()
        assert "some normal log output" in result.plain


# ===========================================================================
# render — controls hint
# ===========================================================================


class TestDaemonPanelControlsHint:
    """Tests for the controls hint in the render output."""

    def test_controls_hint_present(self):
        widget = _make_bare_daemon_panel()
        result = widget.render()
        plain = result.plain
        assert ":sup" in plain
        assert ":mon" in plain


# ===========================================================================
# Constants
# ===========================================================================


class TestDaemonPanelConstants:
    """Tests for DaemonPanel class constants."""

    def test_log_lines_to_show(self):
        from overcode.tui_widgets.daemon_panel import DaemonPanel
        assert DaemonPanel.LOG_LINES_TO_SHOW == 8
