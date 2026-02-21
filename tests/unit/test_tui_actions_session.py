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
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)

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
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)

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
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)

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
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)

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

    def test_remote_agent_toggle_sleep(self):
        """Should toggle sleep via sister controller for remote agents."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = True
        mock_session.is_asleep = False
        mock_session.name = "remote-agent"
        mock_session.source_url = "http://remote:8080"
        mock_session.source_api_key = "key-abc"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_result = MagicMock()
        mock_result.ok = True

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)
        mock_tui._remote_notify = SessionActionsMixin._remote_notify.__get__(mock_tui)
        mock_tui._sister_controller.set_sleep.return_value = mock_result

        SessionActionsMixin.action_toggle_sleep(mock_tui)

        mock_tui._sister_controller.set_sleep.assert_called_once_with(
            "http://remote:8080", "key-abc", "remote-agent", asleep=True,
        )
        mock_tui.notify.assert_called_once()
        assert "asleep" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "information"

    def test_remote_agent_wake(self):
        """Should wake a remote agent via sister controller."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = True
        mock_session.is_asleep = True
        mock_session.name = "remote-agent"
        mock_session.source_url = "http://remote:8080"
        mock_session.source_api_key = "key-abc"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_result = MagicMock()
        mock_result.ok = True

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)
        mock_tui._remote_notify = SessionActionsMixin._remote_notify.__get__(mock_tui)
        mock_tui._sister_controller.set_sleep.return_value = mock_result

        SessionActionsMixin.action_toggle_sleep(mock_tui)

        mock_tui._sister_controller.set_sleep.assert_called_once_with(
            "http://remote:8080", "key-abc", "remote-agent", asleep=False,
        )
        mock_tui.notify.assert_called_once()
        assert "awake" in mock_tui.notify.call_args[0][0]

    def test_blocks_sleep_with_active_heartbeat(self):
        """Should prevent sleeping agent with active heartbeat (#219)."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary
        from overcode.status_constants import STATUS_WAITING_USER

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.is_asleep = False
        mock_session.name = "heartbeat-agent"
        mock_session.parent_session_id = None
        mock_session.heartbeat_enabled = True
        mock_session.heartbeat_paused = False

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session
        mock_widget.detected_status = STATUS_WAITING_USER

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)

        SessionActionsMixin.action_toggle_sleep(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "active heartbeat" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "warning"
        mock_tui.session_manager.update_session.assert_not_called()


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


class TestToggleHeartbeatPause:
    """Test action_toggle_heartbeat_pause method."""

    def test_no_agent_focused(self):
        """Should warn when no agent is focused."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_tui = MagicMock()
        mock_tui.focused = MagicMock()  # Not a SessionSummary

        SessionActionsMixin.action_toggle_heartbeat_pause(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "No agent focused" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "warning"

    def test_child_agent_blocked(self):
        """Should block heartbeat pause on child agents."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.parent_session_id = "parent-123"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)

        SessionActionsMixin.action_toggle_heartbeat_pause(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "only available for root agents" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "warning"

    def test_no_heartbeat_configured(self):
        """Should warn when no heartbeat is configured."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.parent_session_id = None
        mock_session.heartbeat_enabled = False

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)

        SessionActionsMixin.action_toggle_heartbeat_pause(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "No heartbeat configured" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "warning"

    @patch("overcode.settings.signal_activity")
    def test_successful_pause(self, mock_signal):
        """Should pause an active heartbeat."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.parent_session_id = None
        mock_session.heartbeat_enabled = True
        mock_session.heartbeat_paused = False
        mock_session.is_asleep = False
        mock_session.name = "test-agent"
        mock_session.id = "session-123"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)

        SessionActionsMixin.action_toggle_heartbeat_pause(mock_tui)

        mock_tui.session_manager.update_session.assert_called_once_with(
            "session-123", heartbeat_paused=True
        )
        assert mock_session.heartbeat_paused is True
        mock_signal.assert_called_once_with(mock_tui.tmux_session)
        mock_tui.notify.assert_called_once()
        assert "paused" in mock_tui.notify.call_args[0][0]
        mock_widget.refresh.assert_called_once()

    @patch("overcode.settings.signal_activity")
    def test_successful_resume(self, mock_signal):
        """Should resume a paused heartbeat."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.parent_session_id = None
        mock_session.heartbeat_enabled = True
        mock_session.heartbeat_paused = True
        mock_session.is_asleep = False
        mock_session.name = "test-agent"
        mock_session.id = "session-123"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)

        SessionActionsMixin.action_toggle_heartbeat_pause(mock_tui)

        mock_tui.session_manager.update_session.assert_called_once_with(
            "session-123", heartbeat_paused=False
        )
        assert mock_session.heartbeat_paused is False
        mock_signal.assert_called_once_with(mock_tui.tmux_session)
        mock_tui.notify.assert_called_once()
        assert "resumed" in mock_tui.notify.call_args[0][0]

    def test_sleeping_agent_cannot_resume(self):
        """Should prevent resuming heartbeat on a sleeping agent (#265)."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.parent_session_id = None
        mock_session.heartbeat_enabled = True
        mock_session.heartbeat_paused = True
        mock_session.is_asleep = True
        mock_session.name = "sleeping-agent"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)

        SessionActionsMixin.action_toggle_heartbeat_pause(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "Cannot resume heartbeat on sleeping agent" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "warning"
        mock_tui.session_manager.update_session.assert_not_called()

    def test_remote_agent_pause(self):
        """Should pause heartbeat on remote agent via sister controller."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = True
        mock_session.heartbeat_paused = False
        mock_session.name = "remote-agent"
        mock_session.source_url = "http://remote:8080"
        mock_session.source_api_key = "key-abc"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_result = MagicMock()
        mock_result.ok = True

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)
        mock_tui._remote_notify = SessionActionsMixin._remote_notify.__get__(mock_tui)
        mock_tui._sister_controller.pause_heartbeat.return_value = mock_result

        SessionActionsMixin.action_toggle_heartbeat_pause(mock_tui)

        mock_tui._sister_controller.pause_heartbeat.assert_called_once_with(
            "http://remote:8080", "key-abc", "remote-agent",
        )
        mock_tui.notify.assert_called_once()
        assert "paused" in mock_tui.notify.call_args[0][0]

    def test_remote_agent_resume(self):
        """Should resume heartbeat on remote agent via sister controller."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = True
        mock_session.heartbeat_paused = True
        mock_session.name = "remote-agent"
        mock_session.source_url = "http://remote:8080"
        mock_session.source_api_key = "key-abc"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_result = MagicMock()
        mock_result.ok = True

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)
        mock_tui._remote_notify = SessionActionsMixin._remote_notify.__get__(mock_tui)
        mock_tui._sister_controller.resume_heartbeat.return_value = mock_result

        SessionActionsMixin.action_toggle_heartbeat_pause(mock_tui)

        mock_tui._sister_controller.resume_heartbeat.assert_called_once_with(
            "http://remote:8080", "key-abc", "remote-agent",
        )
        mock_tui.notify.assert_called_once()
        assert "resumed" in mock_tui.notify.call_args[0][0]


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
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)

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
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)

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

    def test_remote_agent_toggle_time_context(self):
        """Should toggle time context via sister controller for remote agents."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = True
        mock_session.time_context_enabled = False
        mock_session.name = "remote-agent"
        mock_session.source_url = "http://remote:8080"
        mock_session.source_api_key = "key-abc"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_result = MagicMock()
        mock_result.ok = True

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)
        mock_tui._remote_notify = SessionActionsMixin._remote_notify.__get__(mock_tui)
        mock_tui._sister_controller.set_time_context.return_value = mock_result

        SessionActionsMixin.action_toggle_time_context(mock_tui)

        mock_tui._sister_controller.set_time_context.assert_called_once_with(
            "http://remote:8080", "key-abc", "remote-agent", enabled=True,
        )
        mock_tui.notify.assert_called_once()
        assert "enabled" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "information"
        # Should NOT update local session manager for remote agents
        mock_tui.session_manager.update_session.assert_not_called()

    def test_remote_agent_disable_time_context(self):
        """Should disable time context via sister controller for remote agents."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = True
        mock_session.time_context_enabled = True
        mock_session.name = "remote-agent"
        mock_session.source_url = "http://remote:8080"
        mock_session.source_api_key = "key-abc"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_result = MagicMock()
        mock_result.ok = True

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)
        mock_tui._remote_notify = SessionActionsMixin._remote_notify.__get__(mock_tui)
        mock_tui._sister_controller.set_time_context.return_value = mock_result

        SessionActionsMixin.action_toggle_time_context(mock_tui)

        mock_tui._sister_controller.set_time_context.assert_called_once_with(
            "http://remote:8080", "key-abc", "remote-agent", enabled=False,
        )
        mock_tui.notify.assert_called_once()
        assert "disabled" in mock_tui.notify.call_args[0][0]

    def test_remote_agent_time_context_failure(self):
        """Should notify error when remote time context toggle fails."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = True
        mock_session.time_context_enabled = False
        mock_session.name = "remote-agent"
        mock_session.source_url = "http://remote:8080"
        mock_session.source_api_key = "key-abc"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_result = MagicMock()
        mock_result.ok = False
        mock_result.error = "Connection refused"

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)
        mock_tui._remote_notify = SessionActionsMixin._remote_notify.__get__(mock_tui)
        mock_tui._sister_controller.set_time_context.return_value = mock_result

        SessionActionsMixin.action_toggle_time_context(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "Remote error: Connection refused" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "error"


class TestToggleHookDetection:
    """Test action_toggle_hook_detection method."""

    def test_no_agent_focused(self):
        """Should warn when no agent is focused."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_tui = MagicMock()
        mock_tui.focused = MagicMock()  # Not a SessionSummary

        SessionActionsMixin.action_toggle_hook_detection(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "No agent focused" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "warning"

    def test_enable_hook_detection(self):
        """Should enable hook detection on a local agent."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.hook_status_detection = False
        mock_session.name = "test-agent"
        mock_session.id = "session-123"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)

        SessionActionsMixin.action_toggle_hook_detection(mock_tui)

        mock_tui.session_manager.update_session.assert_called_once_with(
            "session-123", hook_status_detection=True
        )
        assert mock_session.hook_status_detection is True
        mock_tui.notify.assert_called_once()
        assert "enabled" in mock_tui.notify.call_args[0][0]
        mock_widget.refresh.assert_called_once()

    def test_disable_hook_detection(self):
        """Should disable hook detection on a local agent."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.hook_status_detection = True
        mock_session.name = "test-agent"
        mock_session.id = "session-123"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)

        SessionActionsMixin.action_toggle_hook_detection(mock_tui)

        mock_tui.session_manager.update_session.assert_called_once_with(
            "session-123", hook_status_detection=False
        )
        assert mock_session.hook_status_detection is False
        mock_tui.notify.assert_called_once()
        assert "disabled" in mock_tui.notify.call_args[0][0]
        assert "polling" in mock_tui.notify.call_args[0][0]

    def test_remote_agent_enable_hook_detection(self):
        """Should enable hook detection via sister controller for remote agents."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = True
        mock_session.hook_status_detection = False
        mock_session.name = "remote-agent"
        mock_session.source_url = "http://remote:8080"
        mock_session.source_api_key = "key-abc"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_result = MagicMock()
        mock_result.ok = True

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)
        mock_tui._remote_notify = SessionActionsMixin._remote_notify.__get__(mock_tui)
        mock_tui._sister_controller.set_hook_detection.return_value = mock_result

        SessionActionsMixin.action_toggle_hook_detection(mock_tui)

        mock_tui._sister_controller.set_hook_detection.assert_called_once_with(
            "http://remote:8080", "key-abc", "remote-agent", enabled=True,
        )
        mock_tui.notify.assert_called_once()
        assert "enabled" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "information"
        # Should NOT update local session manager for remote agents
        mock_tui.session_manager.update_session.assert_not_called()

    def test_remote_agent_disable_hook_detection(self):
        """Should disable hook detection via sister controller for remote agents."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = True
        mock_session.hook_status_detection = True
        mock_session.name = "remote-agent"
        mock_session.source_url = "http://remote:8080"
        mock_session.source_api_key = "key-abc"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_result = MagicMock()
        mock_result.ok = True

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)
        mock_tui._remote_notify = SessionActionsMixin._remote_notify.__get__(mock_tui)
        mock_tui._sister_controller.set_hook_detection.return_value = mock_result

        SessionActionsMixin.action_toggle_hook_detection(mock_tui)

        mock_tui._sister_controller.set_hook_detection.assert_called_once_with(
            "http://remote:8080", "key-abc", "remote-agent", enabled=False,
        )
        mock_tui.notify.assert_called_once()
        assert "disabled" in mock_tui.notify.call_args[0][0]

    def test_remote_agent_hook_detection_failure(self):
        """Should notify error when remote hook detection toggle fails."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = True
        mock_session.hook_status_detection = False
        mock_session.name = "remote-agent"
        mock_session.source_url = "http://remote:8080"
        mock_session.source_api_key = "key-abc"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_result = MagicMock()
        mock_result.ok = False
        mock_result.error = "Timeout"

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)
        mock_tui._remote_notify = SessionActionsMixin._remote_notify.__get__(mock_tui)
        mock_tui._sister_controller.set_hook_detection.return_value = mock_result

        SessionActionsMixin.action_toggle_hook_detection(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "Remote error: Timeout" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "error"


class TestExecuteRemoteKill:
    """Test _execute_remote_kill method."""

    def test_sends_kill_via_sister_controller(self):
        """Should send kill command via sister controller."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_session = MagicMock()
        mock_session.source_url = "http://remote:8080"
        mock_session.source_api_key = "key-abc"
        mock_session.name = "remote-agent"

        mock_result = MagicMock()
        mock_result.ok = True

        mock_tui = MagicMock()
        mock_tui._remote_notify = SessionActionsMixin._remote_notify.__get__(mock_tui)
        mock_tui._sister_controller.kill_agent.return_value = mock_result

        SessionActionsMixin._execute_remote_kill(mock_tui, mock_session)

        mock_tui._sister_controller.kill_agent.assert_called_once_with(
            "http://remote:8080", "key-abc", "remote-agent",
        )
        mock_tui.notify.assert_called_once()
        assert "Killed remote agent" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "information"

    def test_kill_failure_shows_error(self):
        """Should show error notification when remote kill fails."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_session = MagicMock()
        mock_session.source_url = "http://remote:8080"
        mock_session.source_api_key = "key-abc"
        mock_session.name = "remote-agent"

        mock_result = MagicMock()
        mock_result.ok = False
        mock_result.error = "Agent not found"

        mock_tui = MagicMock()
        mock_tui._remote_notify = SessionActionsMixin._remote_notify.__get__(mock_tui)
        mock_tui._sister_controller.kill_agent.return_value = mock_result

        SessionActionsMixin._execute_remote_kill(mock_tui, mock_session)

        mock_tui.notify.assert_called_once()
        assert "Remote error: Agent not found" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "error"


class TestExecuteRemoteRestart:
    """Test _execute_remote_restart method."""

    def test_sends_restart_via_sister_controller(self):
        """Should send restart command via sister controller."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_session = MagicMock()
        mock_session.source_url = "http://remote:8080"
        mock_session.source_api_key = "key-abc"
        mock_session.name = "remote-agent"

        mock_result = MagicMock()
        mock_result.ok = True

        mock_tui = MagicMock()
        mock_tui._remote_notify = SessionActionsMixin._remote_notify.__get__(mock_tui)
        mock_tui._sister_controller.restart_agent.return_value = mock_result

        SessionActionsMixin._execute_remote_restart(mock_tui, mock_session)

        mock_tui._sister_controller.restart_agent.assert_called_once_with(
            "http://remote:8080", "key-abc", "remote-agent",
        )
        mock_tui.notify.assert_called_once()
        assert "Restarted remote agent" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "information"

    def test_restart_failure_shows_error(self):
        """Should show error notification when remote restart fails."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_session = MagicMock()
        mock_session.source_url = "http://remote:8080"
        mock_session.source_api_key = "key-abc"
        mock_session.name = "remote-agent"

        mock_result = MagicMock()
        mock_result.ok = False
        mock_result.error = "Restart failed"

        mock_tui = MagicMock()
        mock_tui._remote_notify = SessionActionsMixin._remote_notify.__get__(mock_tui)
        mock_tui._sister_controller.restart_agent.return_value = mock_result

        SessionActionsMixin._execute_remote_restart(mock_tui, mock_session)

        mock_tui.notify.assert_called_once()
        assert "Remote error: Restart failed" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "error"


class TestExecuteTransportAll:
    """Test _execute_transport_all method."""

    @patch("overcode.launcher.ClaudeLauncher", autospec=True)
    def test_mixed_local_and_remote(self, MockLauncher):
        """Should send to both local and remote agents."""
        from overcode.tui_actions.session import SessionActionsMixin

        local_session = MagicMock()
        local_session.is_remote = False
        local_session.name = "local-agent"

        remote_session = MagicMock()
        remote_session.is_remote = True
        remote_session.name = "remote-agent"
        remote_session.source_url = "http://remote:8080"
        remote_session.source_api_key = "key-abc"

        mock_result = MagicMock()
        mock_result.ok = True

        mock_launcher_instance = MockLauncher.return_value
        mock_launcher_instance.send_to_session.return_value = True

        mock_tui = MagicMock()
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)
        mock_tui._sister_controller.send_instruction.return_value = mock_result

        SessionActionsMixin._execute_transport_all(mock_tui, [local_session, remote_session])

        # Local agent should be sent via launcher
        mock_launcher_instance.send_to_session.assert_called_once()
        assert mock_launcher_instance.send_to_session.call_args[0][0] == "local-agent"

        # Remote agent should be sent via sister controller
        mock_tui._sister_controller.send_instruction.assert_called_once()
        call_args = mock_tui._sister_controller.send_instruction.call_args
        assert call_args[0][0] == "http://remote:8080"
        assert call_args[0][1] == "key-abc"
        assert call_args[0][2] == "remote-agent"

        # Both succeeded so notification should report all sent
        mock_tui.notify.assert_called_once()
        assert "2 agent(s)" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "information"

    @patch("overcode.launcher.ClaudeLauncher", autospec=True)
    def test_partial_failures(self, MockLauncher):
        """Should report partial failures when some sends fail."""
        from overcode.tui_actions.session import SessionActionsMixin

        session1 = MagicMock()
        session1.is_remote = False
        session1.name = "agent-1"

        session2 = MagicMock()
        session2.is_remote = False
        session2.name = "agent-2"

        mock_launcher_instance = MockLauncher.return_value
        # First send succeeds, second fails
        mock_launcher_instance.send_to_session.side_effect = [True, False]

        mock_tui = MagicMock()
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)

        SessionActionsMixin._execute_transport_all(mock_tui, [session1, session2])

        mock_tui.notify.assert_called_once()
        assert "Sent to 1" in mock_tui.notify.call_args[0][0]
        assert "failed 1" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "warning"

    @patch("overcode.launcher.ClaudeLauncher", autospec=True)
    def test_all_local_success(self, MockLauncher):
        """Should report success when all local agents are transported."""
        from overcode.tui_actions.session import SessionActionsMixin

        session1 = MagicMock()
        session1.is_remote = False
        session1.name = "agent-1"

        session2 = MagicMock()
        session2.is_remote = False
        session2.name = "agent-2"

        mock_launcher_instance = MockLauncher.return_value
        mock_launcher_instance.send_to_session.return_value = True

        mock_tui = MagicMock()
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)

        SessionActionsMixin._execute_transport_all(mock_tui, [session1, session2])

        assert mock_launcher_instance.send_to_session.call_count == 2
        mock_tui.notify.assert_called_once()
        assert "2 agent(s)" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "information"

    @patch("overcode.launcher.ClaudeLauncher", autospec=True)
    def test_remote_failure(self, MockLauncher):
        """Should count remote failure in partial failure report."""
        from overcode.tui_actions.session import SessionActionsMixin

        remote_session = MagicMock()
        remote_session.is_remote = True
        remote_session.name = "remote-agent"
        remote_session.source_url = "http://remote:8080"
        remote_session.source_api_key = "key-abc"

        mock_result = MagicMock()
        mock_result.ok = False

        mock_tui = MagicMock()
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)
        mock_tui._sister_controller.send_instruction.return_value = mock_result

        SessionActionsMixin._execute_transport_all(mock_tui, [remote_session])

        mock_tui.notify.assert_called_once()
        assert "Sent to 0" in mock_tui.notify.call_args[0][0]
        assert "failed 1" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "warning"


class TestKillFocused:
    """Test action_kill_focused method."""

    def _make_mock_tui(self, focused_widget):
        """Create a mock TUI with _confirm_double_press bound."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_tui = MagicMock()
        mock_tui.focused = focused_widget
        mock_tui._pending_confirmations = {}
        mock_tui._confirm_double_press = SessionActionsMixin._confirm_double_press.__get__(mock_tui)
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)
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

    def test_terminated_agent_triggers_cleanup(self):
        """Should use cleanup confirmation for terminated agent instead of kill."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.name = "done-agent"
        mock_session.id = "session-789"
        mock_session.status = "terminated"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_tui = self._make_mock_tui(mock_widget)

        SessionActionsMixin.action_kill_focused(mock_tui)

        # Should set "cleanup" pending, not "kill"
        assert "cleanup" in mock_tui._pending_confirmations
        assert "kill" not in mock_tui._pending_confirmations
        mock_tui.notify.assert_called_once()
        assert "clean up" in mock_tui.notify.call_args[0][0]

    def test_done_agent_triggers_cleanup(self):
        """Should use cleanup confirmation for done agent instead of kill."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.name = "done-agent"
        mock_session.id = "session-789"
        mock_session.status = "done"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_tui = self._make_mock_tui(mock_widget)

        SessionActionsMixin.action_kill_focused(mock_tui)

        assert "cleanup" in mock_tui._pending_confirmations
        mock_tui.notify.assert_called_once()
        assert "clean up" in mock_tui.notify.call_args[0][0]

    def test_remote_agent_uses_sister_controller(self):
        """Should use sister controller kill path for remote agents."""
        import time
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = True
        mock_session.name = "remote-agent"
        mock_session.source_url = "http://remote:8080"
        mock_session.source_api_key = "key-abc"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_tui = self._make_mock_tui(mock_widget)

        # First press
        SessionActionsMixin.action_kill_focused(mock_tui)
        assert "kill" in mock_tui._pending_confirmations
        assert "remote agent" in mock_tui.notify.call_args[0][0]

        # Second press within timeout - should call _execute_remote_kill
        SessionActionsMixin.action_kill_focused(mock_tui)
        mock_tui._execute_remote_kill.assert_called_once_with(mock_session)


class TestRestartFocused:
    """Test action_restart_focused method."""

    def _make_mock_tui(self, focused_widget):
        """Create a mock TUI with _confirm_double_press bound."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_tui = MagicMock()
        mock_tui.focused = focused_widget
        mock_tui._pending_confirmations = {}
        mock_tui._confirm_double_press = SessionActionsMixin._confirm_double_press.__get__(mock_tui)
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)
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

    def test_remote_agent_uses_sister_controller(self):
        """Should use sister controller restart path for remote agents."""
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = True
        mock_session.name = "remote-agent"
        mock_session.source_url = "http://remote:8080"
        mock_session.source_api_key = "key-abc"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_tui = self._make_mock_tui(mock_widget)

        # First press
        SessionActionsMixin.action_restart_focused(mock_tui)
        assert "restart" in mock_tui._pending_confirmations
        assert "remote agent" in mock_tui.notify.call_args[0][0]

        # Second press within timeout - should call _execute_remote_restart
        SessionActionsMixin.action_restart_focused(mock_tui)
        mock_tui._execute_remote_restart.assert_called_once_with(mock_session)


class TestSyncToMainAndClear:
    """Test action_sync_to_main_and_clear method."""

    def _make_mock_tui(self, focused_widget):
        """Create a mock TUI with _confirm_double_press bound."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_tui = MagicMock()
        mock_tui.focused = focused_widget
        mock_tui._pending_confirmations = {}
        mock_tui._confirm_double_press = SessionActionsMixin._confirm_double_press.__get__(mock_tui)
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)
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
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)
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
        mock_tui._is_remote = SessionActionsMixin._is_remote.__get__(mock_tui)
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
