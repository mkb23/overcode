"""
Unit tests for TUI navigation actions.

Tests navigation logic that can be isolated from the full TUI.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch


class TestFocusNextSession:
    """Test action_focus_next_session method."""

    def test_returns_early_when_no_widgets(self):
        """Should return early when no widgets available."""
        from overcode.tui_actions.navigation import NavigationActionsMixin

        mock_tui = MagicMock()
        mock_tui._get_widgets_in_session_order.return_value = []

        NavigationActionsMixin.action_focus_next_session(mock_tui)

        # Should not try to focus anything
        mock_tui._sync_tmux_window.assert_not_called()

    def test_cycles_to_next_widget(self):
        """Should set focused_session_index to next widget (watcher handles focus)."""
        from overcode.tui_actions.navigation import NavigationActionsMixin

        mock_widget1 = MagicMock()
        mock_widget2 = MagicMock()
        mock_widget3 = MagicMock()

        mock_tui = MagicMock()
        mock_tui._get_widgets_in_session_order.return_value = [mock_widget1, mock_widget2, mock_widget3]
        mock_tui.focused_session_index = 0
        mock_tui.view_mode = "list"

        NavigationActionsMixin.action_focus_next_session(mock_tui)

        assert mock_tui.focused_session_index == 1

    def test_wraps_around_at_end(self):
        """Should wrap around to beginning when at end."""
        from overcode.tui_actions.navigation import NavigationActionsMixin

        mock_widget1 = MagicMock()
        mock_widget2 = MagicMock()

        mock_tui = MagicMock()
        mock_tui._get_widgets_in_session_order.return_value = [mock_widget1, mock_widget2]
        mock_tui.focused_session_index = 1  # At end
        mock_tui.view_mode = "list"

        NavigationActionsMixin.action_focus_next_session(mock_tui)

        assert mock_tui.focused_session_index == 0


class TestFocusPreviousSession:
    """Test action_focus_previous_session method."""

    def test_returns_early_when_no_widgets(self):
        """Should return early when no widgets available."""
        from overcode.tui_actions.navigation import NavigationActionsMixin

        mock_tui = MagicMock()
        mock_tui._get_widgets_in_session_order.return_value = []

        NavigationActionsMixin.action_focus_previous_session(mock_tui)

        mock_tui._sync_tmux_window.assert_not_called()

    def test_cycles_to_previous_widget(self):
        """Should set focused_session_index to previous widget (watcher handles focus)."""
        from overcode.tui_actions.navigation import NavigationActionsMixin

        mock_widget1 = MagicMock()
        mock_widget2 = MagicMock()
        mock_widget3 = MagicMock()

        mock_tui = MagicMock()
        mock_tui._get_widgets_in_session_order.return_value = [mock_widget1, mock_widget2, mock_widget3]
        mock_tui.focused_session_index = 1
        mock_tui.view_mode = "list"

        NavigationActionsMixin.action_focus_previous_session(mock_tui)

        assert mock_tui.focused_session_index == 0

    def test_wraps_around_at_beginning(self):
        """Should wrap around to end when at beginning."""
        from overcode.tui_actions.navigation import NavigationActionsMixin

        mock_widget1 = MagicMock()
        mock_widget2 = MagicMock()

        mock_tui = MagicMock()
        mock_tui._get_widgets_in_session_order.return_value = [mock_widget1, mock_widget2]
        mock_tui.focused_session_index = 0  # At beginning
        mock_tui.view_mode = "list"

        NavigationActionsMixin.action_focus_previous_session(mock_tui)

        assert mock_tui.focused_session_index == 1


class TestJumpToAttention:
    """Test action_jump_to_attention method."""

    def test_returns_early_when_no_widgets(self):
        """Should return early when no widgets available."""
        from overcode.tui_actions.navigation import NavigationActionsMixin

        mock_tui = MagicMock()
        mock_tui._get_widgets_in_session_order.return_value = []

        NavigationActionsMixin.action_jump_to_attention(mock_tui)

        mock_tui.notify.assert_not_called()

    def test_notifies_when_no_sessions_need_attention(self):
        """Should notify when no sessions need attention."""
        from overcode.tui_actions.navigation import NavigationActionsMixin

        # Create widget with running status (doesn't need attention)
        mock_widget = MagicMock()
        mock_widget.detected_status = "running"
        mock_widget.is_unvisited_stalled = False

        mock_tui = MagicMock()
        mock_tui._get_widgets_in_session_order.return_value = [mock_widget]
        mock_tui._attention_jump_list = []
        mock_tui._attention_jump_index = 0

        NavigationActionsMixin.action_jump_to_attention(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "No sessions need attention" in mock_tui.notify.call_args[0][0]

    def test_prioritizes_bell_sessions(self):
        """Should prioritize sessions with bell indicator."""
        from overcode.tui_actions.navigation import NavigationActionsMixin

        # Create widgets with different priorities
        mock_widget_bell = MagicMock()
        mock_widget_bell.detected_status = "waiting_user"
        mock_widget_bell.is_unvisited_stalled = True  # Bell!
        mock_widget_bell.session.name = "bell-agent"

        mock_widget_waiting = MagicMock()
        mock_widget_waiting.detected_status = "waiting_user"
        mock_widget_waiting.is_unvisited_stalled = False
        mock_widget_waiting.session.name = "waiting-agent"

        mock_tui = MagicMock()
        mock_tui._get_widgets_in_session_order.return_value = [mock_widget_waiting, mock_widget_bell]
        mock_tui._attention_jump_list = []
        mock_tui._attention_jump_index = 0
        mock_tui.view_mode = "list"

        NavigationActionsMixin.action_jump_to_attention(mock_tui)

        # Bell session should be first in jump list
        assert mock_tui._attention_jump_list[0] is mock_widget_bell

    def test_includes_waiting_user_sessions(self):
        """Should include waiting_user sessions."""
        from overcode.tui_actions.navigation import NavigationActionsMixin

        mock_widget = MagicMock()
        mock_widget.detected_status = "waiting_user"
        mock_widget.is_unvisited_stalled = False
        mock_widget.session.name = "waiting-agent"

        mock_tui = MagicMock()
        mock_tui._get_widgets_in_session_order.return_value = [mock_widget]
        mock_tui._attention_jump_list = []
        mock_tui._attention_jump_index = 0
        mock_tui.view_mode = "list"

        NavigationActionsMixin.action_jump_to_attention(mock_tui)

        assert len(mock_tui._attention_jump_list) == 1

    def test_includes_waiting_approval_sessions(self):
        """Should include waiting_approval sessions."""
        from overcode.tui_actions.navigation import NavigationActionsMixin

        mock_widget = MagicMock()
        mock_widget.detected_status = "waiting_approval"
        mock_widget.is_unvisited_stalled = False
        mock_widget.session.name = "needs-approval"

        mock_tui = MagicMock()
        mock_tui._get_widgets_in_session_order.return_value = [mock_widget]
        mock_tui._attention_jump_list = []
        mock_tui._attention_jump_index = 0
        mock_tui.view_mode = "list"

        NavigationActionsMixin.action_jump_to_attention(mock_tui)

        assert len(mock_tui._attention_jump_list) == 1

    def test_skips_waiting_heartbeat_sessions(self):
        """Should skip waiting_heartbeat sessions (#224) - they auto-resume."""
        from overcode.tui_actions.navigation import NavigationActionsMixin

        mock_widget = MagicMock()
        mock_widget.detected_status = "waiting_heartbeat"
        mock_widget.is_unvisited_stalled = False
        mock_widget.session.name = "needs-heartbeat"

        mock_tui = MagicMock()
        mock_tui._get_widgets_in_session_order.return_value = [mock_widget]
        mock_tui._attention_jump_list = []
        mock_tui._attention_jump_index = 0
        mock_tui.view_mode = "list"

        NavigationActionsMixin.action_jump_to_attention(mock_tui)

        mock_tui.notify.assert_called_once_with("No sessions need attention", severity="information")

    def test_cycles_through_attention_list(self):
        """Should cycle through attention list on subsequent calls."""
        from overcode.tui_actions.navigation import NavigationActionsMixin

        mock_widget1 = MagicMock()
        mock_widget1.detected_status = "waiting_user"
        mock_widget1.is_unvisited_stalled = False
        mock_widget1.session.name = "agent1"

        mock_widget2 = MagicMock()
        mock_widget2.detected_status = "waiting_user"
        mock_widget2.is_unvisited_stalled = False
        mock_widget2.session.name = "agent2"

        mock_tui = MagicMock()
        mock_tui._get_widgets_in_session_order.return_value = [mock_widget1, mock_widget2]
        mock_tui._attention_jump_list = [mock_widget1, mock_widget2]  # Already built
        mock_tui._attention_jump_index = 0
        mock_tui.view_mode = "list"

        NavigationActionsMixin.action_jump_to_attention(mock_tui)

        # Should cycle to next
        assert mock_tui._attention_jump_index == 1


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
