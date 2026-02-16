"""
Unit tests for TUI session actions.

Tests session control logic that can be isolated from the full TUI.
"""

import pytest
from unittest.mock import MagicMock, patch


class TestToggleSleep:
    """Test action_toggle_sleep method."""

    def test_blocks_sleep_on_running_agent(self):
        """Should prevent putting a running agent to sleep (#158)."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary
        from overcode.status_constants import STATUS_RUNNING

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.is_asleep = False
        mock_session.name = "test-agent"
        mock_session.parent_session_id = None  # root agent

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session
        mock_widget.detected_status = STATUS_RUNNING

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget
        mock_tui._guard_remote = SessionActionsMixin._guard_remote.__get__(mock_tui)

        SessionActionsMixin.action_toggle_sleep(mock_tui)

        # Should show warning and not toggle sleep
        mock_tui.notify.assert_called_once()
        assert "Cannot put a running agent to sleep" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "warning"
        # Should not update session manager
        mock_tui.session_manager.update_session.assert_not_called()

    def test_allows_sleep_on_non_running_agent(self):
        """Should allow putting a non-running agent to sleep."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary
        from overcode.status_constants import STATUS_WAITING_USER

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.is_asleep = False
        mock_session.name = "test-agent"
        mock_session.id = "session-123"
        mock_session.parent_session_id = None  # root agent

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session
        mock_widget.detected_status = STATUS_WAITING_USER

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget
        mock_tui._guard_remote = SessionActionsMixin._guard_remote.__get__(mock_tui)

        SessionActionsMixin.action_toggle_sleep(mock_tui)

        # Should update session to asleep
        mock_tui.session_manager.update_session.assert_called_once_with(
            "session-123", is_asleep=True
        )
        assert mock_session.is_asleep is True
        mock_tui.notify.assert_called_once()
        assert "is now asleep" in mock_tui.notify.call_args[0][0]

    def test_allows_wake_even_if_running(self):
        """Should allow waking up an asleep agent regardless of detected status."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary
        from overcode.status_constants import STATUS_RUNNING

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.is_asleep = True  # Already asleep
        mock_session.name = "test-agent"
        mock_session.id = "session-123"
        mock_session.parent_session_id = None  # root agent

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session
        # Even if detected_status shows running, wake should work
        mock_widget.detected_status = STATUS_RUNNING

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget
        mock_tui._guard_remote = SessionActionsMixin._guard_remote.__get__(mock_tui)

        SessionActionsMixin.action_toggle_sleep(mock_tui)

        # Should wake up the agent
        mock_tui.session_manager.update_session.assert_called_once_with(
            "session-123", is_asleep=False
        )
        assert mock_session.is_asleep is False
        mock_tui.notify.assert_called_once()
        assert "is now awake" in mock_tui.notify.call_args[0][0]

    def test_blocks_sleep_on_child_agent(self):
        """Should prevent toggling sleep on a child agent."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary
        from overcode.status_constants import STATUS_WAITING_USER

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.is_asleep = False
        mock_session.name = "child-agent"
        mock_session.parent_session_id = "parent-123"  # child agent

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session
        mock_widget.detected_status = STATUS_WAITING_USER

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget
        mock_tui._guard_remote = SessionActionsMixin._guard_remote.__get__(mock_tui)

        SessionActionsMixin.action_toggle_sleep(mock_tui)

        # Should show warning and not toggle sleep
        mock_tui.notify.assert_called_once()
        assert "only available for root agents" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "warning"
        mock_tui.session_manager.update_session.assert_not_called()

    def test_no_agent_focused(self):
        """Should show warning when no agent is focused."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_tui = MagicMock()
        mock_tui.focused = None  # No agent focused

        SessionActionsMixin.action_toggle_sleep(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "No agent focused" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "warning"


class TestToggleFocused:
    """Test action_toggle_focused method."""

    def test_toggles_expansion_in_tree_mode(self):
        """Should toggle expanded state when in tree mode."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.expanded = False

        mock_tui = MagicMock()
        mock_tui.view_mode = "tree"
        mock_tui.focused = mock_widget

        SessionActionsMixin.action_toggle_focused(mock_tui)

        assert mock_widget.expanded is True

    def test_collapses_expanded_widget(self):
        """Should collapse an expanded widget."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.expanded = True

        mock_tui = MagicMock()
        mock_tui.view_mode = "tree"
        mock_tui.focused = mock_widget

        SessionActionsMixin.action_toggle_focused(mock_tui)

        assert mock_widget.expanded is False

    def test_noop_in_list_preview_mode(self):
        """Should do nothing when in list_preview mode."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.expanded = False

        mock_tui = MagicMock()
        mock_tui.view_mode = "list_preview"
        mock_tui.focused = mock_widget

        SessionActionsMixin.action_toggle_focused(mock_tui)

        # expanded should remain False (not toggled)
        assert mock_widget.expanded is False

    def test_noop_when_no_session_focused(self):
        """Should do nothing when focused widget is not a SessionSummary."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_tui = MagicMock()
        mock_tui.view_mode = "tree"
        mock_tui.focused = MagicMock()  # Not a SessionSummary

        # Should not raise
        SessionActionsMixin.action_toggle_focused(mock_tui)


class TestToggleTimeContext:
    """Test action_toggle_time_context method."""

    def test_enables_time_context(self):
        """Should enable time context on a session that has it disabled."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.time_context_enabled = False
        mock_session.name = "test-agent"
        mock_session.id = "session-123"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget
        mock_tui._guard_remote = SessionActionsMixin._guard_remote.__get__(mock_tui)

        SessionActionsMixin.action_toggle_time_context(mock_tui)

        mock_tui.session_manager.update_session.assert_called_once_with(
            "session-123", time_context_enabled=True
        )
        assert mock_session.time_context_enabled is True
        assert "enabled" in mock_tui.notify.call_args[0][0]
        mock_widget.refresh.assert_called_once()

    def test_disables_time_context(self):
        """Should disable time context on a session that has it enabled."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.time_context_enabled = True
        mock_session.name = "test-agent"
        mock_session.id = "session-123"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget
        mock_tui._guard_remote = SessionActionsMixin._guard_remote.__get__(mock_tui)

        SessionActionsMixin.action_toggle_time_context(mock_tui)

        mock_tui.session_manager.update_session.assert_called_once_with(
            "session-123", time_context_enabled=False
        )
        assert mock_session.time_context_enabled is False
        assert "disabled" in mock_tui.notify.call_args[0][0]

    def test_no_agent_focused(self):
        """Should warn when no agent is focused."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_tui = MagicMock()
        mock_tui.focused = MagicMock()  # Not a SessionSummary

        SessionActionsMixin.action_toggle_time_context(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "No agent focused" in mock_tui.notify.call_args[0][0]


class TestKillFocused:
    """Test action_kill_focused method."""

    def _make_mock_tui(self, focused_widget):
        """Create a mock TUI with _confirm_double_press bound."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_tui = MagicMock()
        mock_tui.focused = focused_widget
        mock_tui._pending_confirmations = {}
        mock_tui._confirm_double_press = SessionActionsMixin._confirm_double_press.__get__(mock_tui)
        mock_tui._guard_remote = SessionActionsMixin._guard_remote.__get__(mock_tui)
        return mock_tui

    def test_first_press_sets_pending(self):
        """First press should set pending confirmation and show warning."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.name = "test-agent"
        mock_session.id = "session-123"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_tui = self._make_mock_tui(mock_widget)

        SessionActionsMixin.action_kill_focused(mock_tui)

        assert "kill" in mock_tui._pending_confirmations
        assert mock_tui._pending_confirmations["kill"][0] == "test-agent"
        mock_tui.notify.assert_called_once()
        assert "Press x again" in mock_tui.notify.call_args[0][0]

    def test_second_press_within_window_executes_kill(self):
        """Second press within 3s should execute the kill."""
        import time
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.name = "test-agent"
        mock_session.id = "session-123"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_tui = self._make_mock_tui(mock_widget)
        mock_tui._pending_confirmations["kill"] = ("test-agent", time.time())

        SessionActionsMixin.action_kill_focused(mock_tui)

        mock_tui._execute_kill.assert_called_once_with(
            mock_widget, "test-agent", "session-123"
        )
        assert "kill" not in mock_tui._pending_confirmations

    def test_second_press_after_timeout_resets(self):
        """Second press after 3s should start a new confirmation."""
        import time
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.name = "test-agent"
        mock_session.id = "session-123"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_tui = self._make_mock_tui(mock_widget)
        mock_tui._pending_confirmations["kill"] = ("test-agent", time.time() - 5.0)

        SessionActionsMixin.action_kill_focused(mock_tui)

        mock_tui._execute_kill.assert_not_called()
        assert "kill" in mock_tui._pending_confirmations
        assert mock_tui._pending_confirmations["kill"][0] == "test-agent"
        mock_tui.notify.assert_called_once()
        assert "Press x again" in mock_tui.notify.call_args[0][0]

    def test_different_session_resets_pending(self):
        """Pressing kill on different session should start new confirmation."""
        import time
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.name = "other-agent"
        mock_session.id = "session-456"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_tui = self._make_mock_tui(mock_widget)
        mock_tui._pending_confirmations["kill"] = ("test-agent", time.time())

        SessionActionsMixin.action_kill_focused(mock_tui)

        mock_tui._execute_kill.assert_not_called()
        assert mock_tui._pending_confirmations["kill"][0] == "other-agent"

    def test_no_agent_focused(self):
        """Should warn when no agent is focused."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_tui = MagicMock()
        mock_tui.focused = MagicMock()  # Not a SessionSummary

        SessionActionsMixin.action_kill_focused(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "No agent focused" in mock_tui.notify.call_args[0][0]


class TestRestartFocused:
    """Test action_restart_focused method."""

    def _make_mock_tui(self, focused_widget):
        """Create a mock TUI with _confirm_double_press bound."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_tui = MagicMock()
        mock_tui.focused = focused_widget
        mock_tui._pending_confirmations = {}
        mock_tui._confirm_double_press = SessionActionsMixin._confirm_double_press.__get__(mock_tui)
        mock_tui._guard_remote = SessionActionsMixin._guard_remote.__get__(mock_tui)
        return mock_tui

    def test_first_press_sets_pending(self):
        """First press should set pending confirmation and show warning."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.name = "test-agent"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_tui = self._make_mock_tui(mock_widget)

        SessionActionsMixin.action_restart_focused(mock_tui)

        assert "restart" in mock_tui._pending_confirmations
        assert mock_tui._pending_confirmations["restart"][0] == "test-agent"
        assert "Press R again" in mock_tui.notify.call_args[0][0]

    def test_second_press_within_window_executes_restart(self):
        """Second press within 3s should execute the restart."""
        import time
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.name = "test-agent"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_tui = self._make_mock_tui(mock_widget)
        mock_tui._pending_confirmations["restart"] = ("test-agent", time.time())

        SessionActionsMixin.action_restart_focused(mock_tui)

        mock_tui._execute_restart.assert_called_once_with(mock_widget)
        assert "restart" not in mock_tui._pending_confirmations

    def test_second_press_after_timeout_resets(self):
        """Second press after 3s should start a new confirmation."""
        import time
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.name = "test-agent"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_tui = self._make_mock_tui(mock_widget)
        mock_tui._pending_confirmations["restart"] = ("test-agent", time.time() - 5.0)

        SessionActionsMixin.action_restart_focused(mock_tui)

        mock_tui._execute_restart.assert_not_called()
        assert mock_tui._pending_confirmations["restart"][0] == "test-agent"

    def test_no_agent_focused(self):
        """Should warn when no agent is focused."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_tui = MagicMock()
        mock_tui.focused = MagicMock()

        SessionActionsMixin.action_restart_focused(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "No agent focused" in mock_tui.notify.call_args[0][0]


class TestSyncToMainAndClear:
    """Test action_sync_to_main_and_clear method."""

    def _make_mock_tui(self, focused_widget):
        """Create a mock TUI with _confirm_double_press bound."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_tui = MagicMock()
        mock_tui.focused = focused_widget
        mock_tui._pending_confirmations = {}
        mock_tui._confirm_double_press = SessionActionsMixin._confirm_double_press.__get__(mock_tui)
        mock_tui._guard_remote = SessionActionsMixin._guard_remote.__get__(mock_tui)
        return mock_tui

    def test_first_press_sets_pending(self):
        """First press should set pending confirmation and show warning."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.name = "test-agent"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_tui = self._make_mock_tui(mock_widget)

        SessionActionsMixin.action_sync_to_main_and_clear(mock_tui)

        assert "sync" in mock_tui._pending_confirmations
        assert mock_tui._pending_confirmations["sync"][0] == "test-agent"
        assert "Press c again" in mock_tui.notify.call_args[0][0]

    def test_second_press_within_window_executes_sync(self):
        """Second press within 3s should execute the sync."""
        import time
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.name = "test-agent"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_tui = self._make_mock_tui(mock_widget)
        mock_tui._pending_confirmations["sync"] = ("test-agent", time.time())

        SessionActionsMixin.action_sync_to_main_and_clear(mock_tui)

        mock_tui._execute_sync.assert_called_once_with(mock_widget)
        assert "sync" not in mock_tui._pending_confirmations

    def test_second_press_after_timeout_resets(self):
        """Second press after 3s should start a new confirmation."""
        import time
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.name = "test-agent"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_tui = self._make_mock_tui(mock_widget)
        mock_tui._pending_confirmations["sync"] = ("test-agent", time.time() - 5.0)

        SessionActionsMixin.action_sync_to_main_and_clear(mock_tui)

        mock_tui._execute_sync.assert_not_called()
        assert mock_tui._pending_confirmations["sync"][0] == "test-agent"

    def test_no_agent_focused(self):
        """Should warn when no agent is focused."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_tui = MagicMock()
        mock_tui.focused = MagicMock()

        SessionActionsMixin.action_sync_to_main_and_clear(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "No agent focused" in mock_tui.notify.call_args[0][0]


class TestTransportAll:
    """Test action_transport_all method."""

    def _make_mock_tui(self, sessions):
        """Create a mock TUI with _confirm_double_press bound."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_tui = MagicMock()
        mock_tui.sessions = sessions
        mock_tui._pending_confirmations = {}
        mock_tui._confirm_double_press = SessionActionsMixin._confirm_double_press.__get__(mock_tui)
        mock_tui._guard_remote = SessionActionsMixin._guard_remote.__get__(mock_tui)
        return mock_tui

    def test_first_press_sets_pending_with_active_sessions(self):
        """First press with active sessions should set pending confirmation."""
        from overcode.tui_actions.session import SessionActionsMixin

        session1 = MagicMock()
        session1.status = "running"
        session1.is_asleep = False

        session2 = MagicMock()
        session2.status = "waiting"
        session2.is_asleep = False

        mock_tui = self._make_mock_tui([session1, session2])

        SessionActionsMixin.action_transport_all(mock_tui)

        assert "transport" in mock_tui._pending_confirmations
        assert "Press H again" in mock_tui.notify.call_args[0][0]
        assert "2 agent(s)" in mock_tui.notify.call_args[0][0]

    def test_excludes_terminated_and_sleeping_sessions(self):
        """Should exclude terminated and sleeping sessions from count."""
        from overcode.tui_actions.session import SessionActionsMixin

        active = MagicMock()
        active.status = "running"
        active.is_asleep = False

        terminated = MagicMock()
        terminated.status = "terminated"
        terminated.is_asleep = False

        sleeping = MagicMock()
        sleeping.status = "running"
        sleeping.is_asleep = True

        mock_tui = self._make_mock_tui([active, terminated, sleeping])

        SessionActionsMixin.action_transport_all(mock_tui)

        assert "1 agent(s)" in mock_tui.notify.call_args[0][0]

    def test_no_active_sessions_warns(self):
        """Should warn if there are no active sessions."""
        from overcode.tui_actions.session import SessionActionsMixin

        terminated = MagicMock()
        terminated.status = "terminated"
        terminated.is_asleep = False

        mock_tui = self._make_mock_tui([terminated])

        SessionActionsMixin.action_transport_all(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "No active sessions" in mock_tui.notify.call_args[0][0]
        assert "transport" not in mock_tui._pending_confirmations

    def test_second_press_within_window_executes(self):
        """Second press within 3s should call _execute_transport_all."""
        import time
        from overcode.tui_actions.session import SessionActionsMixin

        session1 = MagicMock()
        session1.status = "running"
        session1.is_asleep = False

        mock_tui = self._make_mock_tui([session1])
        mock_tui._pending_confirmations["transport"] = (None, time.time())

        SessionActionsMixin.action_transport_all(mock_tui)

        mock_tui._execute_transport_all.assert_called_once_with([session1])
        assert "transport" not in mock_tui._pending_confirmations

    def test_second_press_after_timeout_resets(self):
        """Second press after 3s should start a new confirmation."""
        import time
        from overcode.tui_actions.session import SessionActionsMixin

        session1 = MagicMock()
        session1.status = "running"
        session1.is_asleep = False

        mock_tui = self._make_mock_tui([session1])
        mock_tui._pending_confirmations["transport"] = (None, time.time() - 5.0)

        SessionActionsMixin.action_transport_all(mock_tui)

        mock_tui._execute_transport_all.assert_not_called()
        assert "transport" in mock_tui._pending_confirmations


class TestNewAgent:
    """Test action_new_agent method."""

    def test_opens_command_bar_in_new_agent_dir_mode(self):
        """Should open command bar with new_agent_dir mode."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_cmd_bar = MagicMock()
        mock_input = MagicMock()
        mock_cmd_bar.query_one.return_value = mock_input

        mock_tui = MagicMock()
        mock_tui.query_one.return_value = mock_cmd_bar

        SessionActionsMixin.action_new_agent(mock_tui)

        mock_cmd_bar.add_class.assert_called_once_with("visible")
        mock_cmd_bar.set_mode.assert_called_once_with("new_agent_dir")
        mock_cmd_bar.focus_input.assert_called_once()

    def test_handles_no_matches(self):
        """Should handle NoMatches gracefully when command bar is not found."""
        from overcode.tui_actions.session import SessionActionsMixin
        from textual.css.query import NoMatches

        mock_tui = MagicMock()
        mock_tui.query_one.side_effect = NoMatches()

        SessionActionsMixin.action_new_agent(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "Command bar not found" in mock_tui.notify.call_args[0][0]


class TestFocusCommandBar:
    """Test action_focus_command_bar method."""

    def _make_mock_tui(self, cmd_bar, focused_widget=None, sessions=None):
        """Create a mock TUI with _open_command_bar bound."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_tui = MagicMock()
        mock_tui.query_one.return_value = cmd_bar
        mock_tui._get_focused_widget.return_value = focused_widget
        mock_tui._open_command_bar = SessionActionsMixin._open_command_bar.__get__(mock_tui)
        mock_tui._guard_remote = SessionActionsMixin._guard_remote.__get__(mock_tui)
        if sessions is not None:
            mock_tui.sessions = sessions
        return mock_tui

    def test_opens_and_focuses_command_bar(self):
        """Should show and focus the command bar."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.name = "test-agent"

        mock_widget = MagicMock()
        mock_widget.session = mock_session

        mock_input = MagicMock()
        mock_cmd_bar = MagicMock()
        mock_cmd_bar.query_one.return_value = mock_input

        mock_tui = self._make_mock_tui(mock_cmd_bar, focused_widget=mock_widget)

        SessionActionsMixin.action_focus_command_bar(mock_tui)

        mock_cmd_bar.add_class.assert_called_once_with("visible")
        mock_cmd_bar.set_target.assert_called_once_with("test-agent")
        mock_input.focus.assert_called_once()

    def test_defaults_to_first_session_when_no_focus(self):
        """Should default to first session when no session is focused."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.name = "first-agent"

        mock_input = MagicMock()
        mock_cmd_bar = MagicMock()
        mock_cmd_bar.target_session = None
        mock_cmd_bar.query_one.return_value = mock_input

        mock_tui = self._make_mock_tui(mock_cmd_bar, sessions=[mock_session])

        SessionActionsMixin.action_focus_command_bar(mock_tui)

        mock_cmd_bar.set_target.assert_called_once_with("first-agent")

    def test_handles_no_matches(self):
        """Should handle NoMatches gracefully."""
        from overcode.tui_actions.session import SessionActionsMixin
        from textual.css.query import NoMatches

        mock_tui = MagicMock()
        mock_tui.query_one.side_effect = NoMatches()
        mock_tui._open_command_bar = SessionActionsMixin._open_command_bar.__get__(mock_tui)

        # Should not raise
        SessionActionsMixin.action_focus_command_bar(mock_tui)


class TestFocusStandingOrders:
    """Test action_focus_standing_orders method."""

    def test_opens_command_bar_in_standing_orders_mode(self):
        """Should open command bar with standing_orders mode and pre-fill."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.name = "test-agent"
        mock_session.standing_instructions = "Do the thing"

        mock_widget = MagicMock()
        mock_widget.session = mock_session

        mock_input = MagicMock()
        mock_cmd_bar = MagicMock()
        mock_cmd_bar.query_one.return_value = mock_input

        mock_tui = MagicMock()
        mock_tui.query_one.return_value = mock_cmd_bar
        mock_tui._get_focused_widget.return_value = mock_widget
        mock_tui._open_command_bar = SessionActionsMixin._open_command_bar.__get__(mock_tui)

        SessionActionsMixin.action_focus_standing_orders(mock_tui)

        mock_cmd_bar.set_target.assert_called_once_with("test-agent")
        mock_cmd_bar.set_mode.assert_called_once_with("standing_orders")
        # Should pre-fill with existing standing orders
        assert mock_input.value == "Do the thing"


class TestFocusHumanAnnotation:
    """Test action_focus_human_annotation method."""

    def test_opens_command_bar_in_annotation_mode(self):
        """Should open command bar with annotation mode and pre-fill."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.name = "test-agent"
        mock_session.human_annotation = "Needs review"

        mock_widget = MagicMock()
        mock_widget.session = mock_session

        mock_input = MagicMock()
        mock_cmd_bar = MagicMock()
        mock_cmd_bar.query_one.return_value = mock_input

        mock_tui = MagicMock()
        mock_tui.query_one.return_value = mock_cmd_bar
        mock_tui._get_focused_widget.return_value = mock_widget
        mock_tui._open_command_bar = SessionActionsMixin._open_command_bar.__get__(mock_tui)

        SessionActionsMixin.action_focus_human_annotation(mock_tui)

        mock_cmd_bar.set_target.assert_called_once_with("test-agent")
        mock_cmd_bar.set_mode.assert_called_once_with("annotation")
        assert mock_input.value == "Needs review"


class TestEditAgentValue:
    """Test action_edit_agent_value method."""

    def test_opens_command_bar_in_value_mode(self):
        """Should open command bar with value mode and pre-fill."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.name = "test-agent"
        mock_session.agent_value = 500

        mock_widget = MagicMock()
        mock_widget.session = mock_session

        mock_input = MagicMock()
        mock_cmd_bar = MagicMock()
        mock_cmd_bar.query_one.return_value = mock_input

        mock_tui = MagicMock()
        mock_tui.query_one.return_value = mock_cmd_bar
        mock_tui._get_focused_widget.return_value = mock_widget
        mock_tui._open_command_bar = SessionActionsMixin._open_command_bar.__get__(mock_tui)

        SessionActionsMixin.action_edit_agent_value(mock_tui)

        mock_cmd_bar.set_target.assert_called_once_with("test-agent")
        mock_cmd_bar.set_mode.assert_called_once_with("value")
        assert mock_input.value == "500"

    def test_defaults_to_1000_when_no_session_focused(self):
        """Should default to 1000 when no session is focused."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.name = "first-agent"

        mock_input = MagicMock()
        mock_cmd_bar = MagicMock()
        mock_cmd_bar.target_session = None
        mock_cmd_bar.query_one.return_value = mock_input

        mock_tui = MagicMock()
        mock_tui.query_one.return_value = mock_cmd_bar
        mock_tui._get_focused_widget.return_value = None
        mock_tui.sessions = [mock_session]
        mock_tui._open_command_bar = SessionActionsMixin._open_command_bar.__get__(mock_tui)

        SessionActionsMixin.action_edit_agent_value(mock_tui)

        mock_cmd_bar.set_target.assert_called_once_with("first-agent")
        assert mock_input.value == "1000"


class TestConfigureHeartbeat:
    """Test action_configure_heartbeat method."""

    def test_opens_command_bar_in_heartbeat_freq_mode_enabled(self):
        """Should pre-fill with existing frequency when heartbeat is enabled."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.name = "test-agent"
        mock_session.heartbeat_enabled = True
        mock_session.heartbeat_frequency_seconds = 600

        mock_widget = MagicMock()
        mock_widget.session = mock_session

        mock_input = MagicMock()
        mock_cmd_bar = MagicMock()
        mock_cmd_bar.query_one.return_value = mock_input

        mock_tui = MagicMock()
        mock_tui.query_one.return_value = mock_cmd_bar
        mock_tui._get_focused_widget.return_value = mock_widget
        mock_tui._open_command_bar = SessionActionsMixin._open_command_bar.__get__(mock_tui)

        SessionActionsMixin.action_configure_heartbeat(mock_tui)

        mock_cmd_bar.set_target.assert_called_once_with("test-agent")
        mock_cmd_bar.set_mode.assert_called_once_with("heartbeat_freq")
        assert mock_input.value == "600"

    def test_opens_command_bar_in_heartbeat_freq_mode_disabled(self):
        """Should pre-fill with 300 (default) when heartbeat is disabled."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.name = "test-agent"
        mock_session.heartbeat_enabled = False

        mock_widget = MagicMock()
        mock_widget.session = mock_session

        mock_input = MagicMock()
        mock_cmd_bar = MagicMock()
        mock_cmd_bar.query_one.return_value = mock_input

        mock_tui = MagicMock()
        mock_tui.query_one.return_value = mock_cmd_bar
        mock_tui._get_focused_widget.return_value = mock_widget
        mock_tui._open_command_bar = SessionActionsMixin._open_command_bar.__get__(mock_tui)

        SessionActionsMixin.action_configure_heartbeat(mock_tui)

        assert mock_input.value == "300"

    def test_handles_no_matches(self):
        """Should handle NoMatches gracefully."""
        from overcode.tui_actions.session import SessionActionsMixin
        from textual.css.query import NoMatches

        mock_tui = MagicMock()
        mock_tui.query_one.side_effect = NoMatches()
        mock_tui._open_command_bar = SessionActionsMixin._open_command_bar.__get__(mock_tui)

        # Should not raise
        SessionActionsMixin.action_configure_heartbeat(mock_tui)


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
