"""
Unit tests for web_control_api.py — control action handlers.

All tests mock Launcher/SessionManager at source module path since
web_control_api uses lazy imports inside functions.
"""

import pytest
from unittest.mock import MagicMock, patch

from overcode.web_control_api import (
    ControlError,
    send_to_agent,
    send_key_to_agent,
    kill_agent,
    restart_agent,
    launch_agent,
    set_standing_orders,
    clear_standing_orders,
    set_budget,
    set_value,
    set_annotation,
    set_sleep,
    configure_heartbeat,
    pause_heartbeat,
    resume_heartbeat,
    set_time_context,
    set_hook_detection,
    transport_all,
    cleanup_agents,
    restart_monitor,
    start_supervisor,
    stop_supervisor,
    _parse_frequency,
)

# Patch targets at source modules (lazy imports inside functions)
SM_PATH = "overcode.session_manager.SessionManager"
LAUNCHER_PATH = "overcode.launcher.ClaudeLauncher"
TMUX_PATH = "overcode.tmux_manager.TmuxManager"


def _mock_session(**kwargs):
    """Create a mock session with sensible defaults."""
    defaults = dict(
        id="sess-1",
        name="test-agent",
        tmux_session="agents",
        tmux_window=1,
        status="running",
        is_asleep=False,
        heartbeat_enabled=False,
        heartbeat_paused=False,
        permissiveness_mode="normal",
        stats=MagicMock(current_state="running"),
    )
    defaults.update(kwargs)
    session = MagicMock()
    for k, v in defaults.items():
        setattr(session, k, v)
    return session


class TestSendToAgent:
    """Tests for send_to_agent handler."""

    @patch(LAUNCHER_PATH)
    @patch(SM_PATH)
    def test_sends_text_to_agent(self, MockSM, MockLauncher):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session()
        launcher = MockLauncher.return_value
        launcher.send_to_session.return_value = True

        result = send_to_agent("agents", "test-agent", "hello", enter=True)

        assert result == {"ok": True}
        launcher.send_to_session.assert_called_once_with("test-agent", "hello", enter=True)

    @patch(LAUNCHER_PATH)
    @patch(SM_PATH)
    def test_auto_wakes_sleeping_agent(self, MockSM, MockLauncher):
        sm = MockSM.return_value
        session = _mock_session(is_asleep=True)
        sm.get_session_by_name.return_value = session
        launcher = MockLauncher.return_value
        launcher.send_to_session.return_value = True

        send_to_agent("agents", "test-agent", "hello")

        sm.update_session.assert_called_once_with(session.id, is_asleep=False)

    @patch(SM_PATH)
    def test_agent_not_found_raises_404(self, MockSM):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = None

        with pytest.raises(ControlError) as exc_info:
            send_to_agent("agents", "nonexistent", "hello")
        assert exc_info.value.status == 404

    @patch(LAUNCHER_PATH)
    @patch(SM_PATH)
    def test_send_failure_raises_500(self, MockSM, MockLauncher):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session()
        launcher = MockLauncher.return_value
        launcher.send_to_session.return_value = False

        with pytest.raises(ControlError) as exc_info:
            send_to_agent("agents", "test-agent", "hello")
        assert exc_info.value.status == 500


class TestSendKeyToAgent:
    """Tests for send_key_to_agent handler."""

    @patch(TMUX_PATH)
    @patch(SM_PATH)
    def test_sends_enter_key(self, MockSM, MockTmux):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session()
        tmux = MockTmux.return_value
        tmux.send_keys.return_value = True

        result = send_key_to_agent("agents", "test-agent", "enter")

        assert result == {"ok": True}
        tmux.send_keys.assert_called_once_with(1, "", enter=True)

    @patch(TMUX_PATH)
    @patch(SM_PATH)
    def test_sends_escape_key(self, MockSM, MockTmux):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session()
        tmux = MockTmux.return_value
        tmux.send_keys.return_value = True

        result = send_key_to_agent("agents", "test-agent", "escape")

        assert result == {"ok": True}
        tmux.send_keys.assert_called_once_with(1, "Escape", enter=False)

    @patch(SM_PATH)
    def test_invalid_key_raises_400(self, MockSM):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session()

        with pytest.raises(ControlError) as exc_info:
            send_key_to_agent("agents", "test-agent", "ctrl-c")
        assert exc_info.value.status == 400


class TestKillAgent:
    """Tests for kill_agent handler."""

    @patch(LAUNCHER_PATH)
    @patch(SM_PATH)
    def test_kills_agent_with_cascade(self, MockSM, MockLauncher):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session()
        launcher = MockLauncher.return_value
        launcher.kill_session.return_value = True

        result = kill_agent("agents", "test-agent", cascade=True)

        assert result == {"ok": True}
        launcher.kill_session.assert_called_once_with("test-agent", cascade=True)

    @patch(LAUNCHER_PATH)
    @patch(SM_PATH)
    def test_kills_agent_without_cascade(self, MockSM, MockLauncher):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session()
        launcher = MockLauncher.return_value
        launcher.kill_session.return_value = True

        result = kill_agent("agents", "test-agent", cascade=False)

        launcher.kill_session.assert_called_once_with("test-agent", cascade=False)


class TestRestartAgent:
    """Tests for restart_agent handler."""

    @patch("time.sleep")
    @patch(TMUX_PATH)
    @patch(SM_PATH)
    def test_sends_ctrl_c_then_restart_command(self, MockSM, MockTmux, mock_sleep):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session(permissiveness_mode="normal")
        tmux = MockTmux.return_value
        tmux.send_keys.return_value = True

        result = restart_agent("agents", "test-agent")

        assert result == {"ok": True}
        # First call: Ctrl-C
        assert tmux.send_keys.call_args_list[0][0] == (1, "C-c")
        assert tmux.send_keys.call_args_list[0][1] == {"enter": False}
        # Second call: restart command
        restart_call = tmux.send_keys.call_args_list[1]
        assert "claude code" in restart_call[0][1]

    @patch("time.sleep")
    @patch(TMUX_PATH)
    @patch(SM_PATH)
    def test_bypass_permissions_in_restart(self, MockSM, MockTmux, mock_sleep):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session(permissiveness_mode="bypass")
        tmux = MockTmux.return_value
        tmux.send_keys.return_value = True

        restart_agent("agents", "test-agent")

        restart_call = tmux.send_keys.call_args_list[1]
        assert "--dangerously-skip-permissions" in restart_call[0][1]


class TestLaunchAgent:
    """Tests for launch_agent handler."""

    @patch(LAUNCHER_PATH)
    @patch(SM_PATH)
    def test_launches_agent_with_defaults(self, MockSM, MockLauncher):
        launcher = MockLauncher.return_value
        mock_session = _mock_session(id="new-sess")
        launcher.launch.return_value = mock_session

        result = launch_agent("agents", "/tmp/project", "new-agent")

        assert result == {"ok": True, "session_id": "new-sess"}
        launcher.launch.assert_called_once_with(
            name="new-agent",
            start_directory="/tmp/project",
            initial_prompt=None,
            skip_permissions=False,
            dangerously_skip_permissions=False,
        )

    @patch(LAUNCHER_PATH)
    @patch(SM_PATH)
    def test_launches_with_permissive_mode(self, MockSM, MockLauncher):
        launcher = MockLauncher.return_value
        launcher.launch.return_value = _mock_session()

        launch_agent("agents", "/tmp", "agent", permissions="permissive")

        launcher.launch.assert_called_once_with(
            name="agent",
            start_directory="/tmp",
            initial_prompt=None,
            skip_permissions=True,
            dangerously_skip_permissions=False,
        )

    def test_invalid_permissions_raises_400(self):
        with pytest.raises(ControlError) as exc_info:
            launch_agent("agents", "/tmp", "agent", permissions="invalid")
        assert exc_info.value.status == 400


class TestSetStandingOrders:
    """Tests for set_standing_orders handler."""

    @patch(SM_PATH)
    def test_sets_custom_text(self, MockSM):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session()

        result = set_standing_orders("agents", "test-agent", text="Do the thing")

        assert result == {"ok": True}
        sm.set_standing_instructions.assert_called_once_with("sess-1", "Do the thing")

    @patch("overcode.standing_instructions.resolve_instructions")
    @patch(SM_PATH)
    def test_sets_preset(self, MockSM, mock_resolve):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session()
        mock_resolve.return_value = ("Full coding instructions", "CODING")

        result = set_standing_orders("agents", "test-agent", preset="CODING")

        assert result == {"ok": True, "preset": "CODING"}
        sm.set_standing_instructions.assert_called_once_with(
            "sess-1", "Full coding instructions", preset_name="CODING"
        )

    @patch(SM_PATH)
    def test_no_text_or_preset_raises_400(self, MockSM):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session()

        with pytest.raises(ControlError):
            set_standing_orders("agents", "test-agent")


class TestClearStandingOrders:
    """Tests for clear_standing_orders handler."""

    @patch(SM_PATH)
    def test_clears_standing_orders(self, MockSM):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session()

        result = clear_standing_orders("agents", "test-agent")

        assert result == {"ok": True}
        sm.set_standing_instructions.assert_called_once_with("sess-1", "", preset_name=None)


class TestSetBudget:
    """Tests for set_budget handler."""

    @patch(SM_PATH)
    def test_sets_budget(self, MockSM):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session()

        result = set_budget("agents", "test-agent", usd=5.0)

        assert result == {"ok": True}
        sm.set_cost_budget.assert_called_once_with("sess-1", 5.0)

    @patch(SM_PATH)
    def test_zero_budget_clears(self, MockSM):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session()

        result = set_budget("agents", "test-agent", usd=0)

        assert result == {"ok": True}
        sm.set_cost_budget.assert_called_once_with("sess-1", 0)

    @patch(SM_PATH)
    def test_negative_budget_raises_400(self, MockSM):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session()

        with pytest.raises(ControlError):
            set_budget("agents", "test-agent", usd=-1.0)


class TestSetValue:
    """Tests for set_value handler."""

    @patch(SM_PATH)
    def test_sets_value(self, MockSM):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session()

        result = set_value("agents", "test-agent", value=1500)

        assert result == {"ok": True}
        sm.set_agent_value.assert_called_once_with("sess-1", 1500)


class TestSetAnnotation:
    """Tests for set_annotation handler."""

    @patch(SM_PATH)
    def test_sets_annotation(self, MockSM):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session()

        result = set_annotation("agents", "test-agent", text="Important agent")

        assert result == {"ok": True}
        sm.set_human_annotation.assert_called_once_with("sess-1", "Important agent")

    @patch(SM_PATH)
    def test_clears_annotation_with_empty_text(self, MockSM):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session()

        result = set_annotation("agents", "test-agent", text="")

        assert result == {"ok": True}
        sm.set_human_annotation.assert_called_once_with("sess-1", "")


class TestSetSleep:
    """Tests for set_sleep handler."""

    @patch(SM_PATH)
    def test_puts_agent_to_sleep(self, MockSM):
        sm = MockSM.return_value
        session = _mock_session(
            stats=MagicMock(current_state="waiting"),
            heartbeat_enabled=False,
        )
        sm.get_session_by_name.return_value = session

        result = set_sleep("agents", "test-agent", asleep=True)

        assert result == {"ok": True}
        sm.update_session.assert_called_once_with("sess-1", is_asleep=True)

    @patch(SM_PATH)
    def test_wakes_agent(self, MockSM):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session()

        result = set_sleep("agents", "test-agent", asleep=False)

        assert result == {"ok": True}
        sm.update_session.assert_called_once_with("sess-1", is_asleep=False)

    @patch(SM_PATH)
    def test_rejects_sleep_on_running_agent(self, MockSM):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session(
            stats=MagicMock(current_state="running"),
        )

        with pytest.raises(ControlError) as exc_info:
            set_sleep("agents", "test-agent", asleep=True)
        assert exc_info.value.status == 409

    @patch(SM_PATH)
    def test_rejects_sleep_with_active_heartbeat(self, MockSM):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session(
            stats=MagicMock(current_state="waiting"),
            heartbeat_enabled=True,
            heartbeat_paused=False,
        )

        with pytest.raises(ControlError) as exc_info:
            set_sleep("agents", "test-agent", asleep=True)
        assert exc_info.value.status == 409


class TestConfigureHeartbeat:
    """Tests for configure_heartbeat handler."""

    @patch(SM_PATH)
    def test_enables_heartbeat(self, MockSM):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session()

        result = configure_heartbeat("agents", "test-agent", enabled=True, frequency="5m")

        assert result == {"ok": True}
        sm.update_session.assert_called_once_with(
            "sess-1",
            heartbeat_enabled=True,
            heartbeat_frequency_seconds=300,
        )

    @patch(SM_PATH)
    def test_rejects_frequency_under_30s(self, MockSM):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session()

        with pytest.raises(ControlError):
            configure_heartbeat("agents", "test-agent", enabled=True, frequency="10")


class TestPauseResumeHeartbeat:
    """Tests for pause_heartbeat and resume_heartbeat handlers."""

    @patch(SM_PATH)
    def test_pauses_heartbeat(self, MockSM):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session(
            heartbeat_enabled=True, heartbeat_paused=False,
        )

        result = pause_heartbeat("agents", "test-agent")

        assert result == {"ok": True}
        sm.update_session.assert_called_once_with("sess-1", heartbeat_paused=True)

    @patch(SM_PATH)
    def test_pause_rejects_when_no_heartbeat(self, MockSM):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session(heartbeat_enabled=False)

        with pytest.raises(ControlError) as exc_info:
            pause_heartbeat("agents", "test-agent")
        assert exc_info.value.status == 409

    @patch(SM_PATH)
    def test_pause_rejects_when_already_paused(self, MockSM):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session(
            heartbeat_enabled=True, heartbeat_paused=True,
        )

        with pytest.raises(ControlError) as exc_info:
            pause_heartbeat("agents", "test-agent")
        assert exc_info.value.status == 409

    @patch(SM_PATH)
    def test_resumes_heartbeat(self, MockSM):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session(
            heartbeat_enabled=True, heartbeat_paused=True, is_asleep=False,
        )

        result = resume_heartbeat("agents", "test-agent")

        assert result == {"ok": True}
        sm.update_session.assert_called_once_with("sess-1", heartbeat_paused=False)

    @patch(SM_PATH)
    def test_resume_rejects_on_sleeping_agent(self, MockSM):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session(
            heartbeat_enabled=True, heartbeat_paused=True, is_asleep=True,
        )

        with pytest.raises(ControlError) as exc_info:
            resume_heartbeat("agents", "test-agent")
        assert exc_info.value.status == 409


class TestFeatureToggles:
    """Tests for set_time_context and set_hook_detection."""

    @patch(SM_PATH)
    def test_enables_time_context(self, MockSM):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session()

        result = set_time_context("agents", "test-agent", enabled=True)

        assert result == {"ok": True}
        sm.update_session.assert_called_once_with("sess-1", time_context_enabled=True)

    @patch(SM_PATH)
    def test_disables_hook_detection(self, MockSM):
        sm = MockSM.return_value
        sm.get_session_by_name.return_value = _mock_session()

        result = set_hook_detection("agents", "test-agent", enabled=False)

        assert result == {"ok": True}
        sm.update_session.assert_called_once_with("sess-1", hook_status_detection=False)


class TestTransportAll:
    """Tests for transport_all handler."""

    @patch(LAUNCHER_PATH)
    @patch(SM_PATH)
    def test_sends_handover_to_active_agents(self, MockSM, MockLauncher):
        sm = MockSM.return_value
        active_session = _mock_session(tmux_session="agents", status="running", is_asleep=False)
        sleeping_session = _mock_session(tmux_session="agents", status="running", is_asleep=True)
        sm.list_sessions.return_value = [active_session, sleeping_session]

        launcher = MockLauncher.return_value
        launcher.send_to_session.return_value = True

        result = transport_all("agents")

        assert result["ok"] is True
        assert result["sent"] == 1
        assert result["total"] == 1

    @patch(SM_PATH)
    def test_no_active_agents_raises_409(self, MockSM):
        sm = MockSM.return_value
        sm.list_sessions.return_value = []

        with pytest.raises(ControlError) as exc_info:
            transport_all("agents")
        assert exc_info.value.status == 409


class TestCleanupAgents:
    """Tests for cleanup_agents handler."""

    @patch(SM_PATH)
    def test_cleans_terminated_agents(self, MockSM):
        sm = MockSM.return_value
        terminated = _mock_session(tmux_session="agents", status="terminated")
        running = _mock_session(tmux_session="agents", status="running")
        sm.list_sessions.return_value = [terminated, running]

        result = cleanup_agents("agents")

        assert result == {"ok": True, "cleaned": 1}
        sm.delete_session.assert_called_once_with(terminated.id)

    @patch(SM_PATH)
    def test_includes_done_when_requested(self, MockSM):
        sm = MockSM.return_value
        terminated = _mock_session(tmux_session="agents", status="terminated")
        done = _mock_session(tmux_session="agents", status="done", id="sess-done")
        sm.list_sessions.return_value = [terminated, done]

        result = cleanup_agents("agents", include_done=True)

        assert result == {"ok": True, "cleaned": 2}


class TestRestartMonitor:
    """Tests for restart_monitor handler."""

    @patch("overcode.web_control_api.subprocess")
    @patch("time.sleep")
    @patch("overcode.monitor_daemon.stop_monitor_daemon")
    @patch("overcode.monitor_daemon.is_monitor_daemon_running")
    def test_restarts_running_monitor(self, mock_is_running, mock_stop, mock_sleep, mock_subprocess):
        mock_is_running.return_value = True

        result = restart_monitor("agents")

        assert result == {"ok": True}
        mock_stop.assert_called_once_with("agents")
        mock_subprocess.Popen.assert_called_once()


class TestStartStopSupervisor:
    """Tests for start_supervisor and stop_supervisor handlers."""

    @patch("overcode.web_control_api.subprocess")
    @patch("overcode.supervisor_daemon.is_supervisor_daemon_running")
    def test_starts_supervisor(self, mock_is_running, mock_subprocess):
        mock_is_running.return_value = False

        result = start_supervisor("agents")

        assert result == {"ok": True}
        mock_subprocess.Popen.assert_called_once()

    @patch("overcode.supervisor_daemon.is_supervisor_daemon_running")
    def test_start_rejects_when_already_running(self, mock_is_running):
        mock_is_running.return_value = True

        with pytest.raises(ControlError) as exc_info:
            start_supervisor("agents")
        assert exc_info.value.status == 409

    @patch("overcode.supervisor_daemon.stop_supervisor_daemon")
    @patch("overcode.supervisor_daemon.is_supervisor_daemon_running")
    def test_stops_supervisor(self, mock_is_running, mock_stop):
        mock_is_running.return_value = True
        mock_stop.return_value = True

        result = stop_supervisor("agents")

        assert result == {"ok": True}
        mock_stop.assert_called_once_with("agents")

    @patch("overcode.supervisor_daemon.is_supervisor_daemon_running")
    def test_stop_rejects_when_not_running(self, mock_is_running):
        mock_is_running.return_value = False

        with pytest.raises(ControlError) as exc_info:
            stop_supervisor("agents")
        assert exc_info.value.status == 409


class TestParseFrequency:
    """Tests for _parse_frequency helper."""

    def test_parses_minutes(self):
        assert _parse_frequency("5m") == 300

    def test_parses_hours(self):
        assert _parse_frequency("1h") == 3600

    def test_parses_seconds_suffix(self):
        assert _parse_frequency("60s") == 60

    def test_parses_bare_number_as_seconds(self):
        assert _parse_frequency("300") == 300

    def test_strips_whitespace(self):
        assert _parse_frequency(" 5m ") == 300


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
