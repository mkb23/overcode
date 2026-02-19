"""
Unit tests for DaemonStatusBar widget.

Tests the rendering logic, static methods, and state management
in isolation without requiring a running Textual application.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock


# ---------------------------------------------------------------------------
# Helper to build a bare DaemonStatusBar without the Textual app
# ---------------------------------------------------------------------------

def _make_bare_status_bar(**extra_attrs):
    """Create a DaemonStatusBar bypassing __init__ for unit-testing.

    Textual's ``app`` is a property (on MessagePump) that uses a ContextVar,
    so we create a test subclass that overrides it with a simple attribute.
    """
    from overcode.tui_widgets.daemon_status_bar import DaemonStatusBar

    class _TestableDaemonStatusBar(DaemonStatusBar):
        _mock_app = MagicMock()

        @property
        def app(self):
            return self._mock_app

    widget = _TestableDaemonStatusBar.__new__(_TestableDaemonStatusBar)
    widget._id = "daemon-status"
    widget._is_mounted = False
    widget._running = False
    # Default attributes from __init__
    widget.tmux_session = "agents"
    widget.monitor_state = None
    widget._session_manager = None
    widget._asleep_session_ids = set()
    widget.show_cost = False
    widget._usage_snapshot = None
    widget._supervisor_running = False
    widget._summarizer_available = False
    widget._web_running = False
    widget._web_url = None
    widget._mean_spin = 0.0
    widget._spin_sample_count = 0
    widget._spin_baseline_minutes = 0
    widget._sister_states = []
    # Each instance gets its own mock app
    widget._mock_app = MagicMock()
    widget._mock_app._summarizer.enabled = False
    widget._mock_app._summarizer.total_calls = 0
    # Apply overrides
    for k, v in extra_attrs.items():
        if k == "app":
            widget._mock_app = v
        else:
            setattr(widget, k, v)
    return widget


def _make_monitor_state(**overrides):
    """Create a mock MonitorDaemonState."""
    from overcode.monitor_daemon_state import MonitorDaemonState
    defaults = dict(
        pid=1234,
        status="active",
        loop_count=42,
        current_interval=10,
        last_loop_time=datetime.now().isoformat(),
        started_at=datetime.now().isoformat(),
        daemon_version=2,
        sessions=[],
        presence_available=False,
        presence_state=None,
        presence_idle_seconds=None,
        total_supervisions=0,
        supervisor_launches=0,
        supervisor_tokens=0,
        supervisor_claude_running=False,
        supervisor_claude_started_at=None,
        supervisor_claude_total_run_seconds=0.0,
        relay_enabled=False,
        relay_last_status="disabled",
    )
    defaults.update(overrides)
    mock = MagicMock(spec=MonitorDaemonState)
    for k, v in defaults.items():
        setattr(mock, k, v)
    mock.is_stale.return_value = False
    return mock


def _make_session_state(**overrides):
    """Create a mock SessionDaemonState."""
    from overcode.monitor_daemon_state import SessionDaemonState
    defaults = dict(
        session_id="sess-1",
        name="agent-1",
        current_status="running",
        current_activity="working",
        status_since=datetime.now().isoformat(),
        input_tokens=1000,
        output_tokens=500,
        estimated_cost_usd=0.05,
        median_work_time=600.0,
        is_asleep=False,
    )
    defaults.update(overrides)
    mock = MagicMock(spec=SessionDaemonState)
    for k, v in defaults.items():
        setattr(mock, k, v)
    return mock


# ===========================================================================
# _usage_pct_style
# ===========================================================================


class TestUsagePctStyle:
    """Tests for DaemonStatusBar._usage_pct_style static method."""

    def test_90_plus_is_bold_red(self):
        from overcode.tui_widgets.daemon_status_bar import DaemonStatusBar
        assert DaemonStatusBar._usage_pct_style(90) == "bold red"
        assert DaemonStatusBar._usage_pct_style(95) == "bold red"
        assert DaemonStatusBar._usage_pct_style(100) == "bold red"

    def test_75_to_89_is_bold_yellow(self):
        from overcode.tui_widgets.daemon_status_bar import DaemonStatusBar
        assert DaemonStatusBar._usage_pct_style(75) == "bold yellow"
        assert DaemonStatusBar._usage_pct_style(89) == "bold yellow"

    def test_50_to_74_is_yellow(self):
        from overcode.tui_widgets.daemon_status_bar import DaemonStatusBar
        assert DaemonStatusBar._usage_pct_style(50) == "yellow"
        assert DaemonStatusBar._usage_pct_style(74) == "yellow"

    def test_below_50_is_green(self):
        from overcode.tui_widgets.daemon_status_bar import DaemonStatusBar
        assert DaemonStatusBar._usage_pct_style(0) == "green"
        assert DaemonStatusBar._usage_pct_style(49) == "green"
        assert DaemonStatusBar._usage_pct_style(25) == "green"


# ===========================================================================
# _get_active_session_names
# ===========================================================================


class TestGetActiveSessionNames:
    """Tests for DaemonStatusBar._get_active_session_names."""

    def test_returns_empty_when_no_monitor_state(self):
        widget = _make_bare_status_bar(monitor_state=None)
        assert widget._get_active_session_names() == []

    def test_returns_empty_when_no_sessions(self):
        state = _make_monitor_state(sessions=[])
        widget = _make_bare_status_bar(monitor_state=state)
        assert widget._get_active_session_names() == []

    def test_returns_all_names_when_none_asleep(self):
        s1 = _make_session_state(session_id="s1", name="agent-1")
        s2 = _make_session_state(session_id="s2", name="agent-2")
        state = _make_monitor_state(sessions=[s1, s2])
        widget = _make_bare_status_bar(monitor_state=state)
        names = widget._get_active_session_names()
        assert sorted(names) == ["agent-1", "agent-2"]

    def test_excludes_asleep_sessions(self):
        s1 = _make_session_state(session_id="s1", name="agent-1")
        s2 = _make_session_state(session_id="s2", name="agent-2")
        state = _make_monitor_state(sessions=[s1, s2])
        widget = _make_bare_status_bar(
            monitor_state=state,
            _asleep_session_ids={"s2"},
        )
        names = widget._get_active_session_names()
        assert names == ["agent-1"]

    def test_all_asleep_returns_empty(self):
        s1 = _make_session_state(session_id="s1", name="agent-1")
        state = _make_monitor_state(sessions=[s1])
        widget = _make_bare_status_bar(
            monitor_state=state,
            _asleep_session_ids={"s1"},
        )
        assert widget._get_active_session_names() == []


# ===========================================================================
# render — stopped states
# ===========================================================================


class TestDaemonStatusBarRenderStopped:
    """Tests for DaemonStatusBar.render when daemon is stopped/stale."""

    def test_render_no_monitor_state(self):
        widget = _make_bare_status_bar(monitor_state=None)
        result = widget.render()
        plain = result.plain
        assert "Monitor:" in plain
        assert "stopped" in plain

    def test_render_stale_monitor_state(self):
        state = _make_monitor_state()
        state.is_stale.return_value = True
        widget = _make_bare_status_bar(monitor_state=state)
        result = widget.render()
        plain = result.plain
        assert "Monitor:" in plain
        assert "stopped" in plain

    def test_render_supervisor_stopped(self):
        widget = _make_bare_status_bar(
            monitor_state=None,
            _supervisor_running=False,
        )
        result = widget.render()
        plain = result.plain
        assert "Supervisor:" in plain
        assert "stopped" in plain


# ===========================================================================
# render — running monitor
# ===========================================================================


class TestDaemonStatusBarRenderRunning:
    """Tests for DaemonStatusBar.render when daemon is running."""

    def test_render_running_monitor(self):
        state = _make_monitor_state(loop_count=42, current_interval=10)
        widget = _make_bare_status_bar(monitor_state=state)
        result = widget.render()
        plain = result.plain
        assert "Monitor:" in plain
        assert "#42" in plain
        assert "@10s" in plain

    def test_render_supervisor_running_ready(self):
        state = _make_monitor_state(
            total_supervisions=0,
            supervisor_claude_running=False,
        )
        widget = _make_bare_status_bar(
            monitor_state=state,
            _supervisor_running=True,
        )
        result = widget.render()
        plain = result.plain
        assert "Supervisor:" in plain
        assert "ready" in plain

    def test_render_supervisor_with_supervisions(self):
        state = _make_monitor_state(
            total_supervisions=5,
            supervisor_tokens=10000,
            supervisor_claude_running=False,
            supervisor_claude_total_run_seconds=120.0,
        )
        widget = _make_bare_status_bar(
            monitor_state=state,
            _supervisor_running=True,
        )
        result = widget.render()
        plain = result.plain
        assert "sup:5" in plain

    def test_render_supervisor_claude_running(self):
        now = datetime.now()
        state = _make_monitor_state(
            supervisor_claude_running=True,
            supervisor_claude_started_at=(now - timedelta(seconds=30)).isoformat(),
        )
        widget = _make_bare_status_bar(
            monitor_state=state,
            _supervisor_running=True,
        )
        result = widget.render()
        plain = result.plain
        assert "RUNNING" in plain


# ===========================================================================
# render — AI section
# ===========================================================================


class TestDaemonStatusBarRenderAI:
    """Tests for the AI section of DaemonStatusBar.render."""

    def test_ai_available_enabled_with_calls(self):
        widget = _make_bare_status_bar(_summarizer_available=True)
        widget.app._summarizer.enabled = True
        widget.app._summarizer.total_calls = 5
        result = widget.render()
        plain = result.plain
        assert "AI:" in plain
        assert "5" in plain

    def test_ai_available_enabled_no_calls(self):
        widget = _make_bare_status_bar(_summarizer_available=True)
        widget.app._summarizer.enabled = True
        widget.app._summarizer.total_calls = 0
        result = widget.render()
        plain = result.plain
        assert "AI:" in plain
        assert "on" in plain

    def test_ai_available_disabled(self):
        widget = _make_bare_status_bar(_summarizer_available=True)
        widget.app._summarizer.enabled = False
        result = widget.render()
        plain = result.plain
        assert "AI:" in plain
        assert "off" in plain

    def test_ai_not_available(self):
        widget = _make_bare_status_bar(_summarizer_available=False)
        result = widget.render()
        plain = result.plain
        assert "AI:" in plain
        assert "n/a" in plain


# ===========================================================================
# render — spin stats
# ===========================================================================


class TestDaemonStatusBarRenderSpinStats:
    """Tests for spin stats section of DaemonStatusBar.render."""

    def test_spin_stats_with_running_agents(self):
        s1 = _make_session_state(
            session_id="s1", name="agent-1", current_status="running",
            input_tokens=1000, output_tokens=500,
        )
        s2 = _make_session_state(
            session_id="s2", name="agent-2", current_status="waiting_user",
            input_tokens=2000, output_tokens=1000,
        )
        state = _make_monitor_state(sessions=[s1, s2])
        widget = _make_bare_status_bar(monitor_state=state)
        result = widget.render()
        plain = result.plain
        assert "Spin:" in plain
        # 1 green out of 2 total
        assert "1" in plain
        assert "/2" in plain

    def test_spin_stats_with_sleeping_agents(self):
        s1 = _make_session_state(session_id="s1", name="agent-1", current_status="running")
        s2 = _make_session_state(session_id="s2", name="agent-2", current_status="running")
        state = _make_monitor_state(sessions=[s1, s2])
        widget = _make_bare_status_bar(
            monitor_state=state,
            _asleep_session_ids={"s2"},
        )
        result = widget.render()
        plain = result.plain
        assert "Spin:" in plain

    def test_spin_stats_show_cost_mode(self):
        s1 = _make_session_state(
            session_id="s1", name="agent-1", current_status="running",
            estimated_cost_usd=1.50, input_tokens=1000, output_tokens=500,
        )
        state = _make_monitor_state(sessions=[s1])
        widget = _make_bare_status_bar(
            monitor_state=state,
            show_cost=True,
        )
        result = widget.render()
        plain = result.plain
        assert "$" in plain or "1.5" in plain

    def test_spin_stats_show_token_mode(self):
        s1 = _make_session_state(
            session_id="s1", name="agent-1", current_status="running",
            input_tokens=50000, output_tokens=20000,
        )
        state = _make_monitor_state(sessions=[s1])
        widget = _make_bare_status_bar(
            monitor_state=state,
            show_cost=False,
        )
        result = widget.render()
        plain = result.plain
        # Should have a token sum indicator
        assert "Spin:" in plain

    def test_mean_spin_with_baseline(self):
        s1 = _make_session_state(session_id="s1", current_status="running")
        state = _make_monitor_state(sessions=[s1])
        widget = _make_bare_status_bar(
            monitor_state=state,
            _spin_baseline_minutes=15,
            _mean_spin=0.8,
            _spin_sample_count=10,
        )
        result = widget.render()
        plain = result.plain
        assert "0.8" in plain
        assert "15m" in plain

    def test_mean_spin_hours_label(self):
        s1 = _make_session_state(session_id="s1", current_status="running")
        state = _make_monitor_state(sessions=[s1])
        widget = _make_bare_status_bar(
            monitor_state=state,
            _spin_baseline_minutes=60,
            _mean_spin=2.0,
            _spin_sample_count=5,
        )
        result = widget.render()
        plain = result.plain
        assert "1h" in plain

    def test_mean_spin_hours_and_minutes_label(self):
        s1 = _make_session_state(session_id="s1", current_status="running")
        state = _make_monitor_state(sessions=[s1])
        widget = _make_bare_status_bar(
            monitor_state=state,
            _spin_baseline_minutes=90,
            _mean_spin=1.5,
            _spin_sample_count=5,
        )
        result = widget.render()
        plain = result.plain
        assert "1h30m" in plain

    def test_mean_spin_no_data(self):
        s1 = _make_session_state(session_id="s1", current_status="running")
        state = _make_monitor_state(sessions=[s1])
        widget = _make_bare_status_bar(
            monitor_state=state,
            _spin_baseline_minutes=15,
            _mean_spin=0.0,
            _spin_sample_count=0,
        )
        result = widget.render()
        plain = result.plain
        assert "no data" in plain


# ===========================================================================
# render — presence
# ===========================================================================


class TestDaemonStatusBarRenderPresence:
    """Tests for presence section of DaemonStatusBar.render."""

    def test_presence_locked(self):
        state = _make_monitor_state(
            presence_available=True,
            presence_state=1,
            presence_idle_seconds=120,
        )
        widget = _make_bare_status_bar(monitor_state=state)
        result = widget.render()
        plain = result.plain
        assert "120s" in plain

    def test_presence_active(self):
        state = _make_monitor_state(
            presence_available=True,
            presence_state=3,
            presence_idle_seconds=5,
        )
        widget = _make_bare_status_bar(monitor_state=state)
        result = widget.render()
        plain = result.plain
        assert "5s" in plain

    def test_presence_not_available_not_shown(self):
        state = _make_monitor_state(presence_available=False)
        widget = _make_bare_status_bar(monitor_state=state)
        result = widget.render()
        plain = result.plain
        # Should not have presence idle seconds
        # Just verify presence icons are absent
        assert "🔒" not in plain
        assert "💤" not in plain


# ===========================================================================
# render — usage snapshot
# ===========================================================================


class TestDaemonStatusBarRenderUsage:
    """Tests for usage snapshot section of DaemonStatusBar.render."""

    def test_usage_snapshot_shown(self):
        snap = MagicMock()
        snap.error = False
        snap.five_hour_pct = 45.0
        snap.seven_day_pct = 60.0
        widget = _make_bare_status_bar(_usage_snapshot=snap)
        result = widget.render()
        plain = result.plain
        assert "Usage:" in plain
        assert "5h:" in plain
        assert "45%" in plain
        assert "7d:" in plain
        assert "60%" in plain

    def test_usage_snapshot_error(self):
        snap = MagicMock()
        snap.error = True
        widget = _make_bare_status_bar(_usage_snapshot=snap)
        result = widget.render()
        plain = result.plain
        assert "Usage:" in plain
        assert "--" in plain

    def test_usage_snapshot_none_not_shown(self):
        widget = _make_bare_status_bar(_usage_snapshot=None)
        result = widget.render()
        plain = result.plain
        assert "Usage:" not in plain


# ===========================================================================
# render — relay and web server
# ===========================================================================


class TestDaemonStatusBarRenderExtras:
    """Tests for relay and web server sections."""

    def test_relay_ok(self):
        state = _make_monitor_state(
            relay_enabled=True,
            relay_last_status="ok",
        )
        widget = _make_bare_status_bar(monitor_state=state)
        result = widget.render()
        plain = result.plain
        assert "📡" in plain

    def test_relay_error(self):
        state = _make_monitor_state(
            relay_enabled=True,
            relay_last_status="error",
        )
        widget = _make_bare_status_bar(monitor_state=state)
        result = widget.render()
        plain = result.plain
        assert "📡" in plain

    def test_relay_disabled_not_shown(self):
        state = _make_monitor_state(relay_enabled=False)
        widget = _make_bare_status_bar(monitor_state=state)
        result = widget.render()
        plain = result.plain
        # Relay section won't have the separator before it
        # (but emoji might still appear in other sections, so check context)
        # Just verify it doesn't crash

    def test_web_server_running(self):
        widget = _make_bare_status_bar(
            _web_running=True,
            _web_url="http://localhost:8080",
        )
        result = widget.render()
        plain = result.plain
        assert ":8080" in plain

    def test_web_server_not_running_not_shown(self):
        widget = _make_bare_status_bar(_web_running=False)
        result = widget.render()
        plain = result.plain
        assert ":8080" not in plain


# ===========================================================================
# render — sister states
# ===========================================================================


class TestDaemonStatusBarRenderSisters:
    """Tests for sister states section of DaemonStatusBar.render."""

    def test_sister_reachable(self):
        sister = MagicMock()
        sister.name = "remote-1"
        sister.reachable = True
        sister.green_agents = 3
        sister.total_agents = 5
        widget = _make_bare_status_bar(_sister_states=[sister])
        result = widget.render()
        plain = result.plain
        assert "Sisters:" in plain
        assert "remote-1(3/5)" in plain

    def test_sister_unreachable(self):
        sister = MagicMock()
        sister.name = "remote-2"
        sister.reachable = False
        widget = _make_bare_status_bar(_sister_states=[sister])
        result = widget.render()
        plain = result.plain
        assert "Sisters:" in plain
        assert "remote-2(--)" in plain

    def test_no_sisters_not_shown(self):
        widget = _make_bare_status_bar(_sister_states=[])
        result = widget.render()
        plain = result.plain
        assert "Sisters:" not in plain

    def test_multiple_sisters(self):
        s1 = MagicMock(name="s1", reachable=True, green_agents=1, total_agents=2)
        s2 = MagicMock(name="s2", reachable=True, green_agents=0, total_agents=1)
        # MagicMock overrides name, need to set it explicitly
        s1.name = "sister-a"
        s2.name = "sister-b"
        widget = _make_bare_status_bar(_sister_states=[s1, s2])
        result = widget.render()
        plain = result.plain
        assert "sister-a(1/2)" in plain
        assert "sister-b(0/1)" in plain
