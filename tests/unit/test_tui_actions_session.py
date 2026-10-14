"""
Unit tests for TUI session actions.

The actions run on ``Tui``, a plain host for ``SessionActionsMixin`` that
carries the attributes SupervisorTUI gives it as real values: a real
``SessionManager`` on this test's state, real ``Session``s and
``SisterState``s, and a ``SisterController`` mock specced to the real class.
A misspelt attribute, field or controller method fails instead of becoming
a new MagicMock. Only Textual widgets (the focused row, the command bar and
the modals) stay mocks, specced to their classes.
"""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from overcode.session_manager import Session, SessionManager
from overcode.sister_controller import ControlResult, SisterController
from overcode.sister_poller import SisterState
from overcode.status_constants import STATUS_RUNNING, STATUS_WAITING_USER
from overcode.tui_actions.session import SessionActionsMixin
from overcode.tui_widgets import SessionSummary

REMOTE_URL = "http://remote:8080"


def _session(**fields):
    """A real Session (a misspelt field is a TypeError, not a new Mock attribute)."""
    base = dict(id="session-123", name="test-agent", tmux_session="agents",
                tmux_window="test-agent", command=["claude"], start_directory="/tmp",
                start_time="2026-10-01T10:00:00")
    base.update(fields)
    return Session(**base)


def _remote(**fields):
    return _session(is_remote=True, name="remote-agent", source_url=REMOTE_URL,
                    source_api_key="key-abc", **fields)


def _row(session, status=STATUS_WAITING_USER):
    """The focused agent's row (a Textual widget)."""
    widget = MagicMock(spec=SessionSummary)
    widget.session = session
    widget.detected_status = status
    return widget


class Tui(SessionActionsMixin):
    """The SupervisorTUI attributes SessionActionsMixin uses, as plain values."""

    tmux_session = "agents"
    has_sisters = False
    local_hostname = "test-host"

    def __init__(self, focused=None, session_manager=None, sessions=(),
                 sisters_reachable=True, fleet_mode="polling", query=None):
        self.focused = focused
        self.session_manager = session_manager
        self.sessions = list(sessions)
        self.detector = SimpleNamespace(mode=fleet_mode)
        self._pending_confirmations = {}
        self._sister_poller = SimpleNamespace(get_sister_states=lambda: [
            SisterState(name="remote", url=REMOTE_URL, reachable=sisters_reachable)])
        self._sister_controller = MagicMock(spec=SisterController)
        self._query = query
        self.notifications = []
        self.executed = []
        self.dialogs_opened = 0

    def notify(self, message, severity="information", timeout=None):
        self.notifications.append((message, severity))

    @property
    def last(self):
        """The one notification shown."""
        assert len(self.notifications) == 1, self.notifications
        return self.notifications[0]

    def query_one(self, selector, expect_type=None):
        if isinstance(self._query, Exception):
            raise self._query
        return self._query

    def _get_focused_widget(self):
        return self.focused

    def _dialog_will_open(self):
        self.dialogs_opened += 1

    # The confirmed local actions drive tmux; record that they were reached.
    def _execute_kill(self, focused, session_name, session_id):
        self.executed.append(("kill", focused, session_name, session_id))

    def _execute_restart(self, focused):
        self.executed.append(("restart", focused))

    def _execute_sync(self, focused):
        self.executed.append(("sync", focused))


@pytest.fixture
def sm(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "sessions"))
    return SessionManager(skip_git_detection=True)


def _registered(sm, **fields):
    """A Session the manager holds too; the returned object is the TUI's own copy."""
    session = _session(**fields)
    sm.create_session(name=session.name, tmux_session="agents", tmux_window=session.tmux_window,
                      command=["claude"], session_id=session.id)
    sm.update_session(session.id, **session.to_dict())
    return session


class TestToggleSleep:
    """Test action_toggle_sleep method."""

    def test_blocks_sleep_on_running_agent(self, sm):
        """Should prevent putting a running agent to sleep (#158)."""
        session = _registered(sm, is_asleep=False)
        tui = Tui(_row(session, STATUS_RUNNING), sm)

        tui.action_toggle_sleep()

        assert tui.last == ("Cannot put a running agent to sleep", "warning")
        assert sm.get_session(session.id).is_asleep is False

    def test_allows_sleep_on_non_running_agent(self, sm):
        """Should allow putting a non-running agent to sleep."""
        session = _registered(sm, is_asleep=False)
        row = _row(session, STATUS_WAITING_USER)
        tui = Tui(row, sm)

        tui.action_toggle_sleep()

        assert sm.get_session(session.id).is_asleep is True
        assert session.is_asleep is True
        assert row.detected_status == "asleep"
        assert "is now asleep" in tui.last[0]
        row.refresh.assert_called_once()

    def test_allows_wake_even_if_running(self, sm):
        """Should allow waking up an asleep agent regardless of detected status."""
        session = _registered(sm, is_asleep=True)
        tui = Tui(_row(session, STATUS_RUNNING), sm)

        tui.action_toggle_sleep()

        assert sm.get_session(session.id).is_asleep is False
        assert session.is_asleep is False
        assert "is now awake" in tui.last[0]

    def test_blocks_sleep_on_child_agent(self, sm):
        """Should prevent toggling sleep on a child agent."""
        session = _registered(sm, name="child-agent", parent_session_id="parent-123")
        tui = Tui(_row(session), sm)

        tui.action_toggle_sleep()

        assert "only available for root agents" in tui.last[0]
        assert tui.last[1] == "warning"
        assert sm.get_session(session.id).is_asleep is False

    def test_no_agent_focused(self):
        """Should show warning when no agent is focused."""
        tui = Tui(focused=None)

        tui.action_toggle_sleep()

        assert tui.last == ("No agent focused", "warning")

    def test_remote_agent_toggle_sleep(self):
        """Should toggle sleep via sister controller for remote agents."""
        tui = Tui(_row(_remote(is_asleep=False)))
        tui._sister_controller.set_sleep.return_value = ControlResult(ok=True)

        tui.action_toggle_sleep()

        tui._sister_controller.set_sleep.assert_called_once_with(
            REMOTE_URL, "key-abc", "remote-agent", asleep=True,
        )
        assert "asleep" in tui.last[0]
        assert tui.last[1] == "information"

    def test_remote_agent_wake(self):
        """Should wake a remote agent via sister controller."""
        tui = Tui(_row(_remote(is_asleep=True)))
        tui._sister_controller.set_sleep.return_value = ControlResult(ok=True)

        tui.action_toggle_sleep()

        tui._sister_controller.set_sleep.assert_called_once_with(
            REMOTE_URL, "key-abc", "remote-agent", asleep=False,
        )
        assert "awake" in tui.last[0]

    def test_unreachable_sister_blocks_remote_sleep(self):
        """A remote action is refused while its sister is unreachable."""
        tui = Tui(_row(_remote(is_asleep=False)), sisters_reachable=False)

        tui.action_toggle_sleep()

        assert "unreachable" in tui.last[0]
        tui._sister_controller.set_sleep.assert_not_called()

    def test_blocks_sleep_with_active_heartbeat(self, sm):
        """Should prevent sleeping agent with active heartbeat (#219)."""
        session = _registered(sm, name="heartbeat-agent", heartbeat_enabled=True,
                              heartbeat_paused=False)
        tui = Tui(_row(session), sm)

        tui.action_toggle_sleep()

        assert "active heartbeat" in tui.last[0]
        assert tui.last[1] == "warning"
        assert sm.get_session(session.id).is_asleep is False


class TestToggleHeartbeatPause:
    """Test action_toggle_heartbeat_pause method."""

    def test_no_agent_focused(self):
        """Should warn when no agent is focused."""
        tui = Tui(focused=object())  # Not a SessionSummary

        tui.action_toggle_heartbeat_pause()

        assert tui.last == ("No agent focused", "warning")

    def test_child_agent_blocked(self, sm):
        """Should block heartbeat pause on child agents."""
        session = _registered(sm, parent_session_id="parent-123", heartbeat_enabled=True)
        tui = Tui(_row(session), sm)

        tui.action_toggle_heartbeat_pause()

        assert "only available for root agents" in tui.last[0]
        assert tui.last[1] == "warning"

    def test_no_heartbeat_configured(self, sm):
        """Should warn when no heartbeat is configured."""
        session = _registered(sm, heartbeat_enabled=False)
        tui = Tui(_row(session), sm)

        tui.action_toggle_heartbeat_pause()

        assert "No heartbeat configured" in tui.last[0]
        assert tui.last[1] == "warning"

    @patch("overcode.settings.signal_activity")
    def test_successful_pause(self, mock_signal, sm):
        """Should pause an active heartbeat."""
        session = _registered(sm, heartbeat_enabled=True, heartbeat_paused=False)
        row = _row(session)
        tui = Tui(row, sm)

        tui.action_toggle_heartbeat_pause()

        assert sm.get_session(session.id).heartbeat_paused is True
        assert session.heartbeat_paused is True
        mock_signal.assert_called_once_with("agents")
        assert "paused" in tui.last[0]
        row.refresh.assert_called_once()

    @patch("overcode.settings.signal_activity")
    def test_successful_resume(self, mock_signal, sm):
        """Should resume a paused heartbeat, restarting its clock (#392)."""
        session = _registered(sm, heartbeat_enabled=True, heartbeat_paused=True,
                              last_heartbeat_time="2026-10-01T09:00:00")
        tui = Tui(_row(session), sm)

        tui.action_toggle_heartbeat_pause()

        stored = sm.get_session(session.id)
        assert stored.heartbeat_paused is False
        assert stored.last_heartbeat_time is None
        assert session.heartbeat_paused is False
        assert session.last_heartbeat_time is None
        mock_signal.assert_called_once_with("agents")
        assert "resumed" in tui.last[0]

    def test_sleeping_agent_cannot_resume(self, sm):
        """Should prevent resuming heartbeat on a sleeping agent (#265)."""
        session = _registered(sm, name="sleeping-agent", heartbeat_enabled=True,
                              heartbeat_paused=True, is_asleep=True)
        tui = Tui(_row(session), sm)

        tui.action_toggle_heartbeat_pause()

        assert "Cannot resume heartbeat on sleeping agent" in tui.last[0]
        assert tui.last[1] == "warning"
        assert sm.get_session(session.id).heartbeat_paused is True

    def test_remote_agent_pause(self):
        """Should pause heartbeat on remote agent via sister controller."""
        tui = Tui(_row(_remote(heartbeat_paused=False)))
        tui._sister_controller.pause_heartbeat.return_value = ControlResult(ok=True)

        tui.action_toggle_heartbeat_pause()

        tui._sister_controller.pause_heartbeat.assert_called_once_with(
            REMOTE_URL, "key-abc", "remote-agent",
        )
        assert "paused" in tui.last[0]

    def test_remote_agent_resume(self):
        """Should resume heartbeat on remote agent via sister controller."""
        tui = Tui(_row(_remote(heartbeat_paused=True)))
        tui._sister_controller.resume_heartbeat.return_value = ControlResult(ok=True)

        tui.action_toggle_heartbeat_pause()

        tui._sister_controller.resume_heartbeat.assert_called_once_with(
            REMOTE_URL, "key-abc", "remote-agent",
        )
        assert "resumed" in tui.last[0]


class TestToggleEnhancedContext:
    """Test action_toggle_enhanced_context method."""

    def test_enables_time_context(self, sm):
        """Should enable enhanced context on a session that has it disabled."""
        session = _registered(sm, enhanced_context_enabled=False)
        row = _row(session)
        tui = Tui(row, sm)

        tui.action_toggle_enhanced_context()

        assert sm.get_session(session.id).enhanced_context_enabled is True
        assert session.enhanced_context_enabled is True
        assert "enabled" in tui.last[0]
        row.refresh.assert_called_once()

    def test_disables_time_context(self, sm):
        """Should disable enhanced context on a session that has it enabled."""
        session = _registered(sm, enhanced_context_enabled=True)
        tui = Tui(_row(session), sm)

        tui.action_toggle_enhanced_context()

        assert sm.get_session(session.id).enhanced_context_enabled is False
        assert session.enhanced_context_enabled is False
        assert "disabled" in tui.last[0]

    def test_no_agent_focused(self):
        """Should warn when no agent is focused."""
        tui = Tui(focused=object())  # Not a SessionSummary

        tui.action_toggle_enhanced_context()

        assert "No agent focused" in tui.last[0]

    @pytest.mark.parametrize("was, now, word", [(False, True, "enabled"), (True, False, "disabled")])
    def test_remote_agent_toggles_time_context(self, sm, was, now, word):
        """Remote agents are toggled via the sister controller, never locally."""
        tui = Tui(_row(_remote(enhanced_context_enabled=was)), sm)
        tui._sister_controller.set_enhanced_context.return_value = ControlResult(ok=True)

        tui.action_toggle_enhanced_context()

        tui._sister_controller.set_enhanced_context.assert_called_once_with(
            REMOTE_URL, "key-abc", "remote-agent", enabled=now,
        )
        assert word in tui.last[0]
        assert tui.last[1] == "information"
        assert sm.list_sessions() == []

    def test_remote_agent_time_context_failure(self):
        """Should notify error when remote enhanced context toggle fails."""
        tui = Tui(_row(_remote(enhanced_context_enabled=False)))
        tui._sister_controller.set_enhanced_context.return_value = ControlResult(
            ok=False, error="Connection refused")

        tui.action_toggle_enhanced_context()

        assert tui.last == ("Remote error: Connection refused", "error")


class TestToggleHookDetection:
    """Test action_toggle_hook_detection — per-agent mode toggle."""

    @staticmethod
    def _agent(sm, **fields):
        return _registered(sm, **{
            "id": "sess-1",
            "name": "agent-1",
            "backend": "claude-code",
            "hook_status_detection": True,
            "detection_mode_override": None,
            **fields,
        })

    def test_toggle_from_polling_to_hooks(self, sm):
        """Should record a hooks override on the focused agent only."""
        session = self._agent(sm, detection_mode_override="polling")
        tui = Tui(_row(session), sm, fleet_mode="polling")

        with patch("overcode.claude_config.ClaudeConfigEditor.are_overcode_hooks_installed", return_value=True):
            tui.action_toggle_hook_detection()

        stored = sm.get_session("sess-1")
        assert (stored.detection_mode_override, stored.hook_status_detection) == ("hooks", True)
        assert session.detection_mode_override == "hooks"
        # Fleet default is untouched — this is a per-agent switch
        assert tui.detector.mode == "polling"
        assert "hooks" in tui.last[0] and "agent-1" in tui.last[0]

    def test_toggle_from_hooks_to_polling(self, sm):
        """Should record a polling override on the focused agent only."""
        session = self._agent(sm)
        tui = Tui(_row(session), sm, fleet_mode="hooks")

        tui.action_toggle_hook_detection()

        stored = sm.get_session("sess-1")
        assert (stored.detection_mode_override, stored.hook_status_detection) == ("polling", False)
        assert session.detection_mode_override == "polling"
        assert tui.detector.mode == "hooks"
        assert "polling" in tui.last[0]

    def test_toggle_round_trips_for_one_agent(self, sm):
        """Two presses return the agent to its starting mode."""
        session = self._agent(sm)
        tui = Tui(_row(session), sm, fleet_mode="hooks")

        with patch("overcode.claude_config.ClaudeConfigEditor.are_overcode_hooks_installed", return_value=True):
            tui.action_toggle_hook_detection()
            assert session.detection_mode_override == "polling"
            tui.action_toggle_hook_detection()

        assert session.detection_mode_override == "hooks"
        assert session.hook_status_detection is True
        assert sm.get_session("sess-1").detection_mode_override == "hooks"

    def test_no_focused_agent_warns(self):
        """Without a focused agent the toggle is a no-op warning."""
        tui = Tui(focused=None)

        tui.action_toggle_hook_detection()

        assert tui.last[1] == "warning"

    def test_hooks_blocked_without_hook_activity(self, sm):
        """Switching to hooks needs evidence hooks are firing."""
        session = self._agent(sm, detection_mode_override="polling")
        tui = Tui(_row(session), sm, fleet_mode="polling")

        with patch("overcode.claude_config.ClaudeConfigEditor.are_overcode_hooks_installed", return_value=False), \
             patch("overcode.settings._session_has_hook_activity", return_value=False):
            tui.action_toggle_hook_detection()

        assert sm.get_session("sess-1").detection_mode_override == "polling"
        assert session.detection_mode_override == "polling"
        assert tui.last[1] == "warning"

    def test_hooks_blocked_for_backend_without_hook_events(self, sm):
        """A backend with no HOOK_EVENTS capability can't be switched to hooks."""
        from overcode.backends import register_backend, unregister_backend
        from tests.unit.backend_doubles import PollingOnlyBackend

        register_backend(PollingOnlyBackend())
        try:
            session = self._agent(sm, backend=PollingOnlyBackend.name)
            tui = Tui(_row(session), sm, fleet_mode="hooks")
            tui.action_toggle_hook_detection()
        finally:
            unregister_backend(PollingOnlyBackend.name)

        assert sm.get_session("sess-1").detection_mode_override is None
        assert tui.last[1] == "warning"


class TestExecuteRemoteKill:
    """Test _execute_remote_kill method."""

    def test_sends_kill_via_sister_controller(self):
        """Should send kill command via sister controller."""
        tui = Tui()
        tui._sister_controller.kill_agent.return_value = ControlResult(ok=True)

        tui._execute_remote_kill(_remote())

        tui._sister_controller.kill_agent.assert_called_once_with(
            REMOTE_URL, "key-abc", "remote-agent",
        )
        assert tui.last == ("Killed remote agent: remote-agent", "information")

    def test_kill_failure_shows_error(self):
        """Should show error notification when remote kill fails."""
        tui = Tui()
        tui._sister_controller.kill_agent.return_value = ControlResult(ok=False, error="Agent not found")

        tui._execute_remote_kill(_remote())

        assert tui.last == ("Remote error: Agent not found", "error")


class TestExecuteRemoteRestart:
    """Test _execute_remote_restart method."""

    def test_sends_restart_via_sister_controller(self):
        """Should send restart command via sister controller."""
        tui = Tui()
        tui._sister_controller.restart_agent.return_value = ControlResult(ok=True)

        tui._execute_remote_restart(_remote())

        tui._sister_controller.restart_agent.assert_called_once_with(
            REMOTE_URL, "key-abc", "remote-agent",
        )
        assert tui.last == ("Restarted remote agent: remote-agent", "information")

    def test_restart_failure_shows_error(self):
        """Should show error notification when remote restart fails."""
        tui = Tui()
        tui._sister_controller.restart_agent.return_value = ControlResult(ok=False, error="Restart failed")

        tui._execute_remote_restart(_remote())

        assert tui.last == ("Remote error: Restart failed", "error")


class TestKillFocused:
    """Test action_kill_focused method."""

    def test_first_press_sets_pending(self):
        """First press should set pending confirmation and show warning."""
        tui = Tui(_row(_session()))

        tui.action_kill_focused()

        assert tui._pending_confirmations["kill"][0] == "test-agent"
        assert "Press x again" in tui.last[0]
        assert tui.executed == []

    def test_second_press_within_window_executes_kill(self):
        """Second press within 3s should execute the kill."""
        row = _row(_session())
        tui = Tui(row)
        tui._pending_confirmations["kill"] = ("test-agent", time.time())

        tui.action_kill_focused()

        assert tui.executed == [("kill", row, "test-agent", "session-123")]
        assert "kill" not in tui._pending_confirmations

    def test_second_press_after_timeout_resets(self):
        """Second press after 3s should start a new confirmation."""
        tui = Tui(_row(_session()))
        tui._pending_confirmations["kill"] = ("test-agent", time.time() - 5.0)

        tui.action_kill_focused()

        assert tui.executed == []
        assert tui._pending_confirmations["kill"][0] == "test-agent"
        assert "Press x again" in tui.last[0]

    def test_different_session_resets_pending(self):
        """Pressing kill on different session should start new confirmation."""
        tui = Tui(_row(_session(name="other-agent", id="session-456")))
        tui._pending_confirmations["kill"] = ("test-agent", time.time())

        tui.action_kill_focused()

        assert tui.executed == []
        assert tui._pending_confirmations["kill"][0] == "other-agent"

    def test_no_agent_focused(self):
        """Should warn when no agent is focused."""
        tui = Tui(focused=object())

        tui.action_kill_focused()

        assert "No agent focused" in tui.last[0]

    @pytest.mark.parametrize("status", ["terminated", "done"])
    def test_finished_agent_triggers_cleanup(self, status):
        """A terminated or done agent is cleaned up, not killed."""
        tui = Tui(_row(_session(name="done-agent", id="session-789", status=status)))

        tui.action_kill_focused()

        assert "cleanup" in tui._pending_confirmations
        assert "kill" not in tui._pending_confirmations
        assert "clean up" in tui.last[0]

    def test_remote_agent_uses_sister_controller(self):
        """Should use sister controller kill path for remote agents."""
        tui = Tui(_row(_remote()))
        tui._sister_controller.kill_agent.return_value = ControlResult(ok=True)

        tui.action_kill_focused()
        assert "kill" in tui._pending_confirmations
        assert "remote agent" in tui.last[0]

        # Second press within timeout: the sister kills it
        tui.action_kill_focused()
        tui._sister_controller.kill_agent.assert_called_once_with(REMOTE_URL, "key-abc", "remote-agent")
        assert tui.executed == []


class TestRestartFocused:
    """Test action_restart_focused method."""

    def test_first_press_sets_pending(self):
        """First press should set pending confirmation and show warning."""
        tui = Tui(_row(_session()))

        tui.action_restart_focused()

        assert tui._pending_confirmations["restart"][0] == "test-agent"
        assert "Press R again to restart" in tui.last[0]

    def test_first_press_on_a_finished_agent_offers_revive(self):
        tui = Tui(_row(_session(status="terminated")))

        tui.action_restart_focused()

        assert "Press R again to revive" in tui.last[0]

    def test_second_press_within_window_executes_restart(self):
        """Second press within 3s should execute the restart."""
        row = _row(_session())
        tui = Tui(row)
        tui._pending_confirmations["restart"] = ("test-agent", time.time())

        tui.action_restart_focused()

        assert tui.executed == [("restart", row)]
        assert "restart" not in tui._pending_confirmations

    def test_second_press_after_timeout_resets(self):
        """Second press after 3s should start a new confirmation."""
        tui = Tui(_row(_session()))
        tui._pending_confirmations["restart"] = ("test-agent", time.time() - 5.0)

        tui.action_restart_focused()

        assert tui.executed == []
        assert tui._pending_confirmations["restart"][0] == "test-agent"

    def test_no_agent_focused(self):
        """Should warn when no agent is focused."""
        tui = Tui(focused=object())

        tui.action_restart_focused()

        assert "No agent focused" in tui.last[0]

    def test_remote_agent_uses_sister_controller(self):
        """Should use sister controller restart path for remote agents."""
        tui = Tui(_row(_remote()))
        tui._sister_controller.restart_agent.return_value = ControlResult(ok=True)

        tui.action_restart_focused()
        assert "restart" in tui._pending_confirmations
        assert "remote agent" in tui.last[0]

        tui.action_restart_focused()
        tui._sister_controller.restart_agent.assert_called_once_with(REMOTE_URL, "key-abc", "remote-agent")
        assert tui.executed == []


class TestSyncToMainAndClear:
    """Test action_sync_to_main_and_clear method."""

    def test_first_press_sets_pending(self):
        """First press should set pending confirmation and show warning."""
        tui = Tui(_row(_session()))

        tui.action_sync_to_main_and_clear()

        assert tui._pending_confirmations["sync"][0] == "test-agent"
        assert "Press c again" in tui.last[0]

    def test_second_press_within_window_executes_sync(self):
        """Second press within 3s should execute the sync."""
        row = _row(_session())
        tui = Tui(row)
        tui._pending_confirmations["sync"] = ("test-agent", time.time())

        tui.action_sync_to_main_and_clear()

        assert tui.executed == [("sync", row)]
        assert "sync" not in tui._pending_confirmations

    def test_second_press_after_timeout_resets(self):
        """Second press after 3s should start a new confirmation."""
        tui = Tui(_row(_session()))
        tui._pending_confirmations["sync"] = ("test-agent", time.time() - 5.0)

        tui.action_sync_to_main_and_clear()

        assert tui.executed == []
        assert tui._pending_confirmations["sync"][0] == "test-agent"

    def test_no_agent_focused(self):
        """Should warn when no agent is focused."""
        tui = Tui(focused=object())

        tui.action_sync_to_main_and_clear()

        assert "No agent focused" in tui.last[0]


class TestNewAgent:
    """Test action_new_agent method."""

    def test_opens_modal_with_defaults(self, tmp_path):
        """Should open new-agent modal pre-filled with defaults."""
        from overcode.tui_widgets import NewAgentModal

        modal = MagicMock(spec=NewAgentModal)
        tui = Tui(_row(_session(start_directory=str(tmp_path))), query=modal,
                  sessions=[_session(name="taken")])

        with patch("overcode.agent_scanner.scan_agents", return_value=[]):
            with patch("overcode.wrapper.list_available_wrappers", return_value=[]):
                tui.action_new_agent()

        assert tui.dialogs_opened == 1
        modal.show.assert_called_once()
        shown = modal.show.call_args.kwargs
        assert shown["directory"] == str(tmp_path)
        assert shown["existing_names"] == {"taken"}
        assert shown["local_hostname"] == "test-host"
        assert shown["sister_names"] == []

    def test_handles_no_matches(self):
        """Should handle NoMatches gracefully when modal is not found."""
        from textual.css.query import NoMatches

        tui = Tui(query=NoMatches())

        tui.action_new_agent()

        assert "modal not found" in tui.last[0]
        assert tui.dialogs_opened == 0


def _command_bar():
    """The command bar and its input (Textual widgets)."""
    from textual.widgets import Input
    from overcode.tui_widgets import CommandBar

    cmd_input = MagicMock(spec=Input)
    cmd_bar = MagicMock(spec=CommandBar)
    cmd_bar.target_session = None
    cmd_bar.query_one.return_value = cmd_input
    return cmd_bar, cmd_input


class TestFocusCommandBar:
    """Test action_focus_command_bar method."""

    def test_opens_and_focuses_command_bar(self):
        """Should show and focus the command bar."""
        session = _session()
        cmd_bar, cmd_input = _command_bar()
        tui = Tui(_row(session), query=cmd_bar)

        tui.action_focus_command_bar()

        cmd_bar.add_class.assert_called_once_with("visible")
        cmd_bar.set_target.assert_called_once_with("test-agent", session_id=session.id)
        cmd_input.focus.assert_called_once()

    def test_defaults_to_first_session_when_no_focus(self):
        """Should default to first session when no session is focused."""
        first = _session(name="first-agent")
        cmd_bar, _ = _command_bar()
        tui = Tui(None, query=cmd_bar, sessions=[first])

        tui.action_focus_command_bar()

        cmd_bar.set_target.assert_called_once_with("first-agent", session_id=first.id)

    def test_handles_no_matches(self):
        """Should handle NoMatches gracefully."""
        from textual.css.query import NoMatches

        # Should not raise
        Tui(query=NoMatches()).action_focus_command_bar()


class TestCommandBarPrefill:
    """The command bar opens in a mode, pre-filled from the focused agent."""

    @pytest.mark.parametrize("action, fields, mode, prefill", [
        ("action_focus_standing_orders", {"standing_instructions": "Do the thing"},
         "standing_orders", "Do the thing"),
        ("action_focus_human_annotation", {"human_annotation": "Needs review"},
         "annotation", "Needs review"),
        ("action_edit_agent_value", {"agent_value": 500}, "value", "500"),
        ("action_configure_heartbeat",
         {"heartbeat_enabled": True, "heartbeat_frequency_seconds": 600}, "heartbeat_freq", "600"),
        ("action_configure_heartbeat", {"heartbeat_enabled": False}, "heartbeat_freq", "300"),
    ])
    def test_opens_in_mode_with_prefill(self, action, fields, mode, prefill):
        session = _session(**fields)
        cmd_bar, cmd_input = _command_bar()
        tui = Tui(_row(session), query=cmd_bar)

        getattr(tui, action)()

        cmd_bar.set_target.assert_called_once_with("test-agent", session_id=session.id)
        cmd_bar.set_mode.assert_called_once_with(mode)
        assert cmd_input.value == prefill

    def test_value_defaults_to_1000_when_no_session_focused(self):
        """Should default to 1000 when no session is focused."""
        first = _session(name="first-agent")
        cmd_bar, cmd_input = _command_bar()
        tui = Tui(None, query=cmd_bar, sessions=[first])

        tui.action_edit_agent_value()

        cmd_bar.set_target.assert_called_once_with("first-agent", session_id=first.id)
        assert cmd_input.value == "1000"

    def test_heartbeat_handles_no_matches(self):
        """Should handle NoMatches gracefully."""
        from textual.css.query import NoMatches

        # Should not raise
        Tui(query=NoMatches()).action_configure_heartbeat()


class TestRestartWhenTmuxIsSlow:
    """R on a local agent while tmux does not answer: an error, not a crash."""

    def test_a_tmux_timeout_is_notified(self, sm):
        from overcode.tmux_manager import TmuxManager
        from overcode.tui_actions.session import SessionActionsMixin as Mixin
        from tests.tmux_doubles import MockTmux

        class RealRestart(Tui):
            _execute_restart = Mixin._execute_restart

        session = _registered(sm)
        tui = RealRestart(_row(session), sm)
        tmux = MockTmux()
        tmux.unresponsive = True
        with patch("overcode.tmux_manager.TmuxManager",
                   lambda name: TmuxManager(name, tmux=tmux)):
            tui._execute_restart(_row(session))
        message, severity = tui.last
        assert severity == "error" and "not responding" in message
