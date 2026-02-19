"""
Unit tests for TUI input actions.

Tests the helper functions that can be tested in isolation.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch


class TestIsFreetextOption:
    """Test _is_freetext_option helper method."""

    def _create_mixin_instance(self):
        """Create a minimal instance with _is_freetext_option method."""
        from overcode.tui_actions.input import InputActionsMixin

        # Create a mock object that has the mixin's methods
        instance = Mock()
        instance._is_freetext_option = InputActionsMixin._is_freetext_option.__get__(instance)
        return instance

    def test_detects_tell_claude_option(self):
        """Should detect 'tell claude what to do' option."""
        instance = self._create_mixin_instance()

        pane_content = """
1. Yes, proceed
2. No, don't do that
3. No, and tell Claude what to do differently (esc)
"""

        result = instance._is_freetext_option(pane_content, "3")
        assert result is True

    def test_returns_false_for_regular_option(self):
        """Should return False for non-freetext options."""
        instance = self._create_mixin_instance()

        pane_content = """
1. Yes, proceed
2. No, don't do that
3. No, and tell Claude what to do differently (esc)
"""

        result = instance._is_freetext_option(pane_content, "1")
        assert result is False

        result = instance._is_freetext_option(pane_content, "2")
        assert result is False

    def test_handles_different_numbering_formats(self):
        """Should handle various numbering formats."""
        instance = self._create_mixin_instance()

        # With period
        pane_content = "3. Tell Claude what to do"
        assert instance._is_freetext_option(pane_content, "3") is True

        # With parenthesis
        pane_content = "3) Tell Claude what to do"
        assert instance._is_freetext_option(pane_content, "3") is True

        # With colon
        pane_content = "3: Tell Claude what to do"
        assert instance._is_freetext_option(pane_content, "3") is True

    def test_case_insensitive_matching(self):
        """Should match case-insensitively."""
        instance = self._create_mixin_instance()

        pane_content = "3. TELL CLAUDE WHAT TO DO"
        assert instance._is_freetext_option(pane_content, "3") is True

        pane_content = "3. Tell Claude What To Do"
        assert instance._is_freetext_option(pane_content, "3") is True

    def test_returns_false_for_empty_content(self):
        """Should return False for empty pane content."""
        instance = self._create_mixin_instance()

        result = instance._is_freetext_option("", "3")
        assert result is False

    def test_returns_false_for_missing_option(self):
        """Should return False when option number not found."""
        instance = self._create_mixin_instance()

        pane_content = """
1. Yes, proceed
2. No, don't do that
"""

        result = instance._is_freetext_option(pane_content, "5")
        assert result is False

    def test_handles_multiline_content(self):
        """Should handle multiline pane content correctly."""
        instance = self._create_mixin_instance()

        pane_content = """
\u256d\u2500 Bash \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u256e
\u2502 rm -rf /tmp/test                                                            \u2502
\u2570\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u256f

1. Yes, allow this action
2. Yes, and don't ask again for this session
3. No, and tell Claude what to do differently (esc)
"""

        result = instance._is_freetext_option(pane_content, "3")
        assert result is True

    def test_handles_whitespace_in_options(self):
        """Should handle leading whitespace in option lines."""
        instance = self._create_mixin_instance()

        pane_content = """
   3. No, and tell Claude what to do differently (esc)
"""

        result = instance._is_freetext_option(pane_content, "3")
        assert result is True


class TestAutoWakeIfSleeping:
    """Test _auto_wake_if_sleeping method."""

    def test_returns_false_when_not_sleeping(self):
        """Should return False when the agent is not sleeping."""
        from overcode.tui_actions.input import InputActionsMixin

        mock_tui = MagicMock()
        mock_session = MagicMock()
        mock_session.is_asleep = False

        mock_widget = MagicMock()
        mock_widget.detected_status = "running"

        result = InputActionsMixin._auto_wake_if_sleeping(mock_tui, mock_session, mock_widget)
        assert result is False
        mock_tui.session_manager.update_session.assert_not_called()
        mock_tui.notify.assert_not_called()

    def test_returns_false_when_not_sleeping_no_widget(self):
        """Should return False when not sleeping and no widget provided."""
        from overcode.tui_actions.input import InputActionsMixin

        mock_tui = MagicMock()
        mock_session = MagicMock()
        mock_session.is_asleep = False

        result = InputActionsMixin._auto_wake_if_sleeping(mock_tui, mock_session, None)
        assert result is False
        mock_tui.session_manager.update_session.assert_not_called()

    def test_wakes_when_session_flag_is_asleep(self):
        """Should wake agent when session.is_asleep is True."""
        from overcode.tui_actions.input import InputActionsMixin

        mock_tui = MagicMock()
        mock_session = MagicMock()
        mock_session.is_asleep = True
        mock_session.id = "session-123"
        mock_session.name = "test-agent"

        result = InputActionsMixin._auto_wake_if_sleeping(mock_tui, mock_session, None)
        assert result is True
        mock_tui.session_manager.update_session.assert_called_once_with("session-123", is_asleep=False)
        assert mock_session.is_asleep is False
        mock_tui.notify.assert_called_once()
        assert "Woke agent" in mock_tui.notify.call_args[0][0]

    def test_wakes_when_widget_detected_status_is_asleep(self):
        """Should wake agent when widget.detected_status is 'asleep' even if session flag is False."""
        from overcode.tui_actions.input import InputActionsMixin

        mock_tui = MagicMock()
        mock_session = MagicMock()
        mock_session.is_asleep = False
        mock_session.id = "session-456"
        mock_session.name = "sleepy-agent"

        mock_widget = MagicMock()
        mock_widget.detected_status = "asleep"
        mock_widget.session = mock_session

        result = InputActionsMixin._auto_wake_if_sleeping(mock_tui, mock_session, mock_widget)
        assert result is True
        mock_tui.session_manager.update_session.assert_called_once_with("session-456", is_asleep=False)
        assert mock_session.is_asleep is False

    def test_updates_widget_when_waking(self):
        """Should update widget display when waking an agent."""
        from overcode.tui_actions.input import InputActionsMixin

        mock_tui = MagicMock()
        mock_session = MagicMock()
        mock_session.is_asleep = True
        mock_session.id = "session-789"
        mock_session.name = "test-agent"

        mock_widget = MagicMock()
        mock_widget.detected_status = "asleep"
        mock_widget.session = MagicMock()
        mock_widget.session.is_asleep = True

        result = InputActionsMixin._auto_wake_if_sleeping(mock_tui, mock_session, mock_widget)
        assert result is True
        # Widget's session should be updated
        assert mock_widget.session.is_asleep is False
        # Widget's detected_status should be reset from "asleep" to "running"
        assert mock_widget.detected_status == "running"
        mock_widget.refresh.assert_called_once()

    def test_does_not_reset_detected_status_if_not_asleep(self):
        """Should not reset detected_status if it is not 'asleep'."""
        from overcode.tui_actions.input import InputActionsMixin

        mock_tui = MagicMock()
        mock_session = MagicMock()
        mock_session.is_asleep = True  # session flag says asleep
        mock_session.id = "session-abc"
        mock_session.name = "test-agent"

        mock_widget = MagicMock()
        mock_widget.detected_status = "waiting_user"  # widget says something else
        mock_widget.session = MagicMock()
        mock_widget.session.is_asleep = True

        result = InputActionsMixin._auto_wake_if_sleeping(mock_tui, mock_session, mock_widget)
        assert result is True
        # Widget's session should still be updated
        assert mock_widget.session.is_asleep is False
        # But detected_status should NOT be changed since it wasn't "asleep"
        assert mock_widget.detected_status == "waiting_user"
        mock_widget.refresh.assert_called_once()


class TestSendRemoteKey:
    """Test _send_remote_key method."""

    def test_successful_send(self):
        """Should send key via sister controller and notify on success."""
        from overcode.tui_actions.input import InputActionsMixin

        mock_tui = MagicMock()
        mock_result = MagicMock()
        mock_result.ok = True
        mock_tui._sister_controller.send_key.return_value = mock_result

        mock_session = MagicMock()
        mock_session.source_url = "http://remote:8080"
        mock_session.source_api_key = "key-123"
        mock_session.name = "remote-agent"

        result = InputActionsMixin._send_remote_key(mock_tui, mock_session, "enter")
        assert result is True
        mock_tui._sister_controller.send_key.assert_called_once_with(
            "http://remote:8080", "key-123", "remote-agent", "enter",
        )
        mock_tui.notify.assert_called_once()
        assert "Sent 'enter'" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "information"

    def test_failed_send(self):
        """Should notify error when sister controller send fails."""
        from overcode.tui_actions.input import InputActionsMixin

        mock_tui = MagicMock()
        mock_result = MagicMock()
        mock_result.ok = False
        mock_result.error = "Connection refused"
        mock_tui._sister_controller.send_key.return_value = mock_result

        mock_session = MagicMock()
        mock_session.source_url = "http://remote:8080"
        mock_session.source_api_key = "key-123"
        mock_session.name = "remote-agent"

        result = InputActionsMixin._send_remote_key(mock_tui, mock_session, "escape")
        assert result is False
        mock_tui.notify.assert_called_once()
        assert "Remote error: Connection refused" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "error"


class TestSendEnterToFocused:
    """Test action_send_enter_to_focused method."""

    def test_notifies_when_no_agent_focused(self):
        """Should notify when no agent is focused."""
        from overcode.tui_actions.input import InputActionsMixin

        # Create mock TUI instance
        mock_tui = MagicMock()
        mock_tui.focused = Mock()  # Not a SessionSummary
        mock_tui.notify = Mock()

        # Bind the mixin method
        InputActionsMixin.action_send_enter_to_focused(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "No agent focused" in mock_tui.notify.call_args[0][0]

    def test_sends_to_remote_agent(self):
        """Should send via _send_remote_key for remote agents."""
        from overcode.tui_actions.input import InputActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = True
        mock_session.name = "remote-agent"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget

        InputActionsMixin.action_send_enter_to_focused(mock_tui)

        mock_tui._send_remote_key.assert_called_once_with(mock_session, "enter")


class TestSendEscapeToFocused:
    """Test action_send_escape_to_focused method."""

    def test_notifies_when_no_agent_focused(self):
        """Should notify when no agent is focused."""
        from overcode.tui_actions.input import InputActionsMixin

        mock_tui = MagicMock()
        mock_tui.focused = Mock()  # Not a SessionSummary
        mock_tui.notify = Mock()

        InputActionsMixin.action_send_escape_to_focused(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "No agent focused" in mock_tui.notify.call_args[0][0]

    def test_sends_to_remote_agent(self):
        """Should send via _send_remote_key for remote agents."""
        from overcode.tui_actions.input import InputActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = True
        mock_session.name = "remote-agent"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget

        InputActionsMixin.action_send_escape_to_focused(mock_tui)

        mock_tui._send_remote_key.assert_called_once_with(mock_session, "escape")

    @patch("overcode.launcher.ClaudeLauncher", autospec=True)
    def test_sends_escape_to_local_agent(self, MockLauncher):
        """Should send escape to local agent via launcher."""
        from overcode.tui_actions.input import InputActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.name = "local-agent"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget

        mock_launcher_instance = MockLauncher.return_value
        mock_launcher_instance.send_to_session.return_value = True

        InputActionsMixin.action_send_escape_to_focused(mock_tui)

        mock_launcher_instance.send_to_session.assert_called_once_with("local-agent", "escape")
        mock_tui.notify.assert_called_once()
        assert "Sent Escape" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "information"

    @patch("overcode.launcher.ClaudeLauncher", autospec=True)
    def test_notifies_error_on_failed_send(self, MockLauncher):
        """Should notify error when send fails."""
        from overcode.tui_actions.input import InputActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = False
        mock_session.name = "local-agent"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget

        mock_launcher_instance = MockLauncher.return_value
        mock_launcher_instance.send_to_session.return_value = False

        InputActionsMixin.action_send_escape_to_focused(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "Failed to send Escape" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "error"


class TestSendKeyToFocused:
    """Test _send_key_to_focused method."""

    def test_notifies_when_no_agent_focused(self):
        """Should notify when no agent is focused."""
        from overcode.tui_actions.input import InputActionsMixin

        mock_tui = MagicMock()
        mock_tui.focused = Mock()  # Not a SessionSummary
        mock_tui.notify = Mock()

        InputActionsMixin._send_key_to_focused(mock_tui, "1")

        mock_tui.notify.assert_called_once()
        assert "No agent focused" in mock_tui.notify.call_args[0][0]

    def test_sends_to_remote_agent(self):
        """Should send via _send_remote_key for remote agents."""
        from overcode.tui_actions.input import InputActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.is_remote = True
        mock_session.name = "remote-agent"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget

        InputActionsMixin._send_key_to_focused(mock_tui, "3")

        mock_tui._send_remote_key.assert_called_once_with(mock_session, "3")


class TestNumberedKeyActions:
    """Test action_send_1_to_focused through action_send_5_to_focused."""

    def test_action_send_1_calls_send_key_with_1(self):
        """action_send_1_to_focused should call _send_key_to_focused with '1'."""
        from overcode.tui_actions.input import InputActionsMixin

        mock_tui = MagicMock()
        InputActionsMixin.action_send_1_to_focused(mock_tui)
        mock_tui._send_key_to_focused.assert_called_once_with("1")

    def test_action_send_2_calls_send_key_with_2(self):
        """action_send_2_to_focused should call _send_key_to_focused with '2'."""
        from overcode.tui_actions.input import InputActionsMixin

        mock_tui = MagicMock()
        InputActionsMixin.action_send_2_to_focused(mock_tui)
        mock_tui._send_key_to_focused.assert_called_once_with("2")

    def test_action_send_3_calls_send_key_with_3(self):
        """action_send_3_to_focused should call _send_key_to_focused with '3'."""
        from overcode.tui_actions.input import InputActionsMixin

        mock_tui = MagicMock()
        InputActionsMixin.action_send_3_to_focused(mock_tui)
        mock_tui._send_key_to_focused.assert_called_once_with("3")

    def test_action_send_4_calls_send_key_with_4(self):
        """action_send_4_to_focused should call _send_key_to_focused with '4'."""
        from overcode.tui_actions.input import InputActionsMixin

        mock_tui = MagicMock()
        InputActionsMixin.action_send_4_to_focused(mock_tui)
        mock_tui._send_key_to_focused.assert_called_once_with("4")

    def test_action_send_5_calls_send_key_with_5(self):
        """action_send_5_to_focused should call _send_key_to_focused with '5'."""
        from overcode.tui_actions.input import InputActionsMixin

        mock_tui = MagicMock()
        InputActionsMixin.action_send_5_to_focused(mock_tui)
        mock_tui._send_key_to_focused.assert_called_once_with("5")


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
