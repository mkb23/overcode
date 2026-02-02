"""
Unit tests for TUI daemon actions.

Tests daemon control logic that can be isolated from the full TUI.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch


class TestToggleDaemon:
    """Test action_toggle_daemon method."""

    def test_toggles_panel_visibility(self):
        """Should toggle daemon panel display."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        mock_panel = MagicMock()
        mock_panel.display = False

        mock_tui = MagicMock()
        mock_tui.query_one.return_value = mock_panel
        mock_tui._prefs = MagicMock()

        DaemonActionsMixin.action_toggle_daemon(mock_tui)

        assert mock_panel.display is True
        mock_tui._save_prefs.assert_called_once()
        mock_tui.notify.assert_called_once()
        assert "shown" in mock_tui.notify.call_args[0][0]

    def test_hides_visible_panel(self):
        """Should hide panel when already visible."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        mock_panel = MagicMock()
        mock_panel.display = True

        mock_tui = MagicMock()
        mock_tui.query_one.return_value = mock_panel
        mock_tui._prefs = MagicMock()

        DaemonActionsMixin.action_toggle_daemon(mock_tui)

        assert mock_panel.display is False
        assert "hidden" in mock_tui.notify.call_args[0][0]

    def test_handles_missing_panel(self):
        """Should handle when daemon panel doesn't exist."""
        from overcode.tui_actions.daemon import DaemonActionsMixin
        from textual.css.query import NoMatches

        mock_tui = MagicMock()
        mock_tui.query_one.side_effect = NoMatches()

        # Should not raise
        DaemonActionsMixin.action_toggle_daemon(mock_tui)


class TestSupervisorStartIntegration:
    """Test action_supervisor_start method with proper imports."""

    def test_method_exists(self):
        """Should have action_supervisor_start method."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        assert hasattr(DaemonActionsMixin, 'action_supervisor_start')
        assert callable(getattr(DaemonActionsMixin, 'action_supervisor_start'))


class TestSupervisorStopIntegration:
    """Test action_supervisor_stop method."""

    def test_method_exists(self):
        """Should have action_supervisor_stop method."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        assert hasattr(DaemonActionsMixin, 'action_supervisor_stop')
        assert callable(getattr(DaemonActionsMixin, 'action_supervisor_stop'))


class TestToggleSummarizerIntegration:
    """Test action_toggle_summarizer method."""

    def test_method_exists(self):
        """Should have action_toggle_summarizer method."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        assert hasattr(DaemonActionsMixin, 'action_toggle_summarizer')
        assert callable(getattr(DaemonActionsMixin, 'action_toggle_summarizer'))


class TestMonitorRestartIntegration:
    """Test action_monitor_restart method."""

    def test_method_exists(self):
        """Should have action_monitor_restart method."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        assert hasattr(DaemonActionsMixin, 'action_monitor_restart')
        assert callable(getattr(DaemonActionsMixin, 'action_monitor_restart'))

    def test_start_monitor_daemon_method_exists(self):
        """Should have _start_monitor_daemon method."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        assert hasattr(DaemonActionsMixin, '_start_monitor_daemon')


class TestToggleWebServerIntegration:
    """Test action_toggle_web_server method."""

    def test_method_exists(self):
        """Should have action_toggle_web_server method."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        assert hasattr(DaemonActionsMixin, 'action_toggle_web_server')
        assert callable(getattr(DaemonActionsMixin, 'action_toggle_web_server'))


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
