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
        mock_session.is_asleep = False
        mock_session.name = "test-agent"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session
        mock_widget.detected_status = STATUS_RUNNING

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget

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
        mock_session.is_asleep = False
        mock_session.name = "test-agent"
        mock_session.id = "session-123"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session
        mock_widget.detected_status = STATUS_WAITING_USER

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget

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
        mock_session.is_asleep = True  # Already asleep
        mock_session.name = "test-agent"
        mock_session.id = "session-123"

        mock_widget = MagicMock(spec=SessionSummary)
        mock_widget.session = mock_session
        # Even if detected_status shows running, wake should work
        mock_widget.detected_status = STATUS_RUNNING

        mock_tui = MagicMock()
        mock_tui.focused = mock_widget

        SessionActionsMixin.action_toggle_sleep(mock_tui)

        # Should wake up the agent
        mock_tui.session_manager.update_session.assert_called_once_with(
            "session-123", is_asleep=False
        )
        assert mock_session.is_asleep is False
        mock_tui.notify.assert_called_once()
        assert "is now awake" in mock_tui.notify.call_args[0][0]

    def test_no_agent_focused(self):
        """Should show warning when no agent is focused."""
        from overcode.tui_actions.session import SessionActionsMixin

        mock_tui = MagicMock()
        mock_tui.focused = None  # No agent focused

        SessionActionsMixin.action_toggle_sleep(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "No agent focused" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "warning"


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
