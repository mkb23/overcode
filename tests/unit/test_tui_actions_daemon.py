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


class TestSupervisorStart:
    """Test action_supervisor_start method."""

    def test_method_exists(self):
        """Should have action_supervisor_start method."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        assert hasattr(DaemonActionsMixin, 'action_supervisor_start')
        assert callable(getattr(DaemonActionsMixin, 'action_supervisor_start'))

    @patch("overcode.supervisor_daemon.is_supervisor_daemon_running", return_value=True)
    @patch("overcode.monitor_daemon.is_monitor_daemon_running", return_value=True)
    def test_warns_when_already_running(self, mock_monitor_running, mock_supervisor_running):
        """Should notify warning when supervisor is already running."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        mock_tui = MagicMock()
        mock_tui.tmux_session = "test"

        DaemonActionsMixin.action_supervisor_start(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "already running" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "warning"

    @patch("time.sleep")
    @patch("overcode.supervisor_daemon.is_supervisor_daemon_running", return_value=False)
    @patch("overcode.monitor_daemon.is_monitor_daemon_running", return_value=False)
    def test_ensures_monitor_daemon_when_not_running(self, mock_monitor_running, mock_supervisor_running, mock_sleep):
        """Should call _ensure_monitor_daemon when monitor is not running."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        mock_tui = MagicMock()
        mock_tui.tmux_session = "test"

        DaemonActionsMixin.action_supervisor_start(mock_tui)

        mock_tui._ensure_monitor_daemon.assert_called_once()
        mock_sleep.assert_called_once_with(1.0)

    @patch("overcode.tui_actions.daemon.subprocess")
    @patch("overcode.tui_actions.daemon.sys")
    @patch("overcode.supervisor_daemon.is_supervisor_daemon_running", return_value=False)
    @patch("overcode.monitor_daemon.is_monitor_daemon_running", return_value=True)
    def test_successful_start_calls_popen(self, mock_monitor_running, mock_supervisor_running, mock_sys, mock_subprocess):
        """Should call Popen to start supervisor daemon."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        mock_tui = MagicMock()
        mock_tui.tmux_session = "test"

        mock_sys.executable = "/usr/bin/python3"

        DaemonActionsMixin.action_supervisor_start(mock_tui)

        mock_subprocess.Popen.assert_called_once()
        call_args = mock_subprocess.Popen.call_args
        assert call_args[0][0] == ["/usr/bin/python3", "-m", "overcode.supervisor_daemon", "--session", "test"]
        assert call_args[1]["start_new_session"] is True
        mock_tui.notify.assert_called_once()
        assert "Started" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "information"
        mock_tui.set_timer.assert_called_once_with(1.0, mock_tui.update_daemon_status)

    @patch("overcode.tui_actions.daemon.subprocess")
    @patch("overcode.tui_actions.daemon.sys")
    @patch("overcode.supervisor_daemon.is_supervisor_daemon_running", return_value=False)
    @patch("overcode.monitor_daemon.is_monitor_daemon_running", return_value=True)
    def test_start_logs_to_daemon_panel(self, mock_monitor_running, mock_supervisor_running, mock_sys, mock_subprocess):
        """Should log startup message to daemon panel."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        mock_panel = MagicMock()
        mock_panel.log_lines = []

        mock_tui = MagicMock()
        mock_tui.tmux_session = "test"
        mock_tui.query_one.return_value = mock_panel

        DaemonActionsMixin.action_supervisor_start(mock_tui)

        assert any("Starting Supervisor Daemon" in line for line in mock_panel.log_lines)

    @patch("overcode.tui_actions.daemon.subprocess.Popen", side_effect=OSError("No such file"))
    @patch("overcode.supervisor_daemon.is_supervisor_daemon_running", return_value=False)
    @patch("overcode.monitor_daemon.is_monitor_daemon_running", return_value=True)
    def test_start_handles_oserror(self, mock_monitor_running, mock_supervisor_running, mock_popen):
        """Should notify error when Popen raises OSError."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        mock_tui = MagicMock()
        mock_tui.tmux_session = "test"

        DaemonActionsMixin.action_supervisor_start(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "Failed" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "error"

    @patch("overcode.tui_actions.daemon.subprocess")
    @patch("overcode.tui_actions.daemon.sys")
    @patch("overcode.supervisor_daemon.is_supervisor_daemon_running", return_value=False)
    @patch("overcode.monitor_daemon.is_monitor_daemon_running", return_value=True)
    def test_start_handles_missing_panel(self, mock_monitor_running, mock_supervisor_running, mock_sys, mock_subprocess):
        """Should not crash when daemon panel is not mounted."""
        from overcode.tui_actions.daemon import DaemonActionsMixin
        from textual.css.query import NoMatches

        mock_tui = MagicMock()
        mock_tui.tmux_session = "test"
        mock_tui.query_one.side_effect = NoMatches()

        # Should not raise
        DaemonActionsMixin.action_supervisor_start(mock_tui)

        mock_subprocess.Popen.assert_called_once()


class TestSupervisorStop:
    """Test action_supervisor_stop method."""

    def test_method_exists(self):
        """Should have action_supervisor_stop method."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        assert hasattr(DaemonActionsMixin, 'action_supervisor_stop')
        assert callable(getattr(DaemonActionsMixin, 'action_supervisor_stop'))

    @patch("overcode.supervisor_daemon.is_supervisor_daemon_running", return_value=False)
    def test_warns_when_not_running(self, mock_is_running):
        """Should notify warning when supervisor is not running."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        mock_tui = MagicMock()
        mock_tui.tmux_session = "test"

        DaemonActionsMixin.action_supervisor_stop(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "not running" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "warning"

    @patch("overcode.supervisor_daemon.stop_supervisor_daemon", return_value=True)
    @patch("overcode.supervisor_daemon.is_supervisor_daemon_running", return_value=True)
    def test_successful_stop(self, mock_is_running, mock_stop):
        """Should stop daemon and notify success."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        mock_panel = MagicMock()
        mock_panel.log_lines = []

        mock_tui = MagicMock()
        mock_tui.tmux_session = "test"
        mock_tui.query_one.return_value = mock_panel

        DaemonActionsMixin.action_supervisor_stop(mock_tui)

        mock_stop.assert_called_once_with("test")
        mock_tui.notify.assert_called_once()
        assert "Stopped" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "information"
        assert any("stopped" in line for line in mock_panel.log_lines)
        mock_tui.update_daemon_status.assert_called_once()

    @patch("overcode.supervisor_daemon.stop_supervisor_daemon", return_value=False)
    @patch("overcode.supervisor_daemon.is_supervisor_daemon_running", return_value=True)
    def test_failed_stop(self, mock_is_running, mock_stop):
        """Should notify error when stop fails."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        mock_tui = MagicMock()
        mock_tui.tmux_session = "test"

        DaemonActionsMixin.action_supervisor_stop(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "Failed" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "error"
        mock_tui.update_daemon_status.assert_called_once()

    @patch("overcode.supervisor_daemon.stop_supervisor_daemon", return_value=True)
    @patch("overcode.supervisor_daemon.is_supervisor_daemon_running", return_value=True)
    def test_stop_handles_missing_panel(self, mock_is_running, mock_stop):
        """Should not crash when daemon panel is not mounted."""
        from overcode.tui_actions.daemon import DaemonActionsMixin
        from textual.css.query import NoMatches

        mock_tui = MagicMock()
        mock_tui.tmux_session = "test"
        mock_tui.query_one.side_effect = NoMatches()

        # Should not raise
        DaemonActionsMixin.action_supervisor_stop(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "Stopped" in mock_tui.notify.call_args[0][0]


class TestToggleSummarizer:
    """Test action_toggle_summarizer method."""

    def test_method_exists(self):
        """Should have action_toggle_summarizer method."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        assert hasattr(DaemonActionsMixin, 'action_toggle_summarizer')
        assert callable(getattr(DaemonActionsMixin, 'action_toggle_summarizer'))

    @patch("overcode.summarizer_client.SummarizerClient.is_available", return_value=False)
    def test_warns_when_unavailable(self, mock_is_available):
        """Should notify warning when OPENAI_API_KEY is not set."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        mock_tui = MagicMock()

        DaemonActionsMixin.action_toggle_summarizer(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "unavailable" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "warning"

    @patch("overcode.summarizer_client.SummarizerClient.is_available", return_value=True)
    @patch("overcode.summarizer_client.SummarizerClient", autospec=False)
    def test_enables_summarizer(self, mock_client_class, mock_is_available):
        """Should enable summarizer, create client, and update widgets."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        mock_widget1 = MagicMock()
        mock_widget2 = MagicMock()

        mock_tui = MagicMock()
        mock_tui._summarizer = MagicMock()
        mock_tui._summarizer.config.enabled = False
        mock_tui._summarizer._client = None
        mock_tui.query.return_value = [mock_widget1, mock_widget2]

        # is_available is a staticmethod, so we restore it for the check
        mock_client_class.is_available = mock_is_available

        DaemonActionsMixin.action_toggle_summarizer(mock_tui)

        # Should have toggled enabled to True
        assert mock_tui._summarizer.config.enabled is True
        # Should have created a client
        assert mock_tui._summarizer._client is not None
        # Should have updated widgets
        assert mock_widget1.summarizer_enabled is True
        assert mock_widget2.summarizer_enabled is True
        # Should have triggered immediate update
        mock_tui._update_summaries_async.assert_called_once()
        # Should notify enabled
        mock_tui.notify.assert_called()
        notify_messages = [call[0][0] for call in mock_tui.notify.call_args_list]
        assert any("enabled" in msg for msg in notify_messages)
        # Should refresh status bar
        mock_tui.update_daemon_status.assert_called_once()

    @patch("overcode.summarizer_client.SummarizerClient.is_available", return_value=True)
    @patch("overcode.summarizer_client.SummarizerClient", autospec=False)
    def test_disables_summarizer(self, mock_client_class, mock_is_available):
        """Should disable summarizer, close client, and clear summaries."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        mock_client_instance = MagicMock()
        mock_widget1 = MagicMock()

        mock_tui = MagicMock()
        mock_tui._summarizer = MagicMock()
        mock_tui._summarizer.config.enabled = True  # Currently enabled
        mock_tui._summarizer._client = mock_client_instance
        mock_tui._summaries = {"agent1": "some summary"}
        mock_tui.query.return_value = [mock_widget1]

        mock_client_class.is_available = mock_is_available

        DaemonActionsMixin.action_toggle_summarizer(mock_tui)

        # Should have toggled enabled to False
        assert mock_tui._summarizer.config.enabled is False
        # Should have closed the client
        mock_client_instance.close.assert_called_once()
        # Should have set client to None
        assert mock_tui._summarizer._client is None
        # Should have cleared summaries
        assert mock_tui._summaries == {}
        # Should have cleared widget summaries and disabled
        assert mock_widget1.ai_summary_short == ""
        assert mock_widget1.ai_summary_context == ""
        assert mock_widget1.summarizer_enabled is False
        mock_widget1.refresh.assert_called_once()
        # Should notify disabled
        mock_tui.notify.assert_called()
        notify_messages = [call[0][0] for call in mock_tui.notify.call_args_list]
        assert any("disabled" in msg for msg in notify_messages)
        # Should refresh status bar
        mock_tui.update_daemon_status.assert_called_once()

    @patch("overcode.summarizer_client.SummarizerClient.is_available", return_value=True)
    @patch("overcode.summarizer_client.SummarizerClient", autospec=False)
    def test_disable_with_no_client_skips_close(self, mock_client_class, mock_is_available):
        """Should skip close() when client is already None during disable."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        mock_tui = MagicMock()
        mock_tui._summarizer = MagicMock()
        mock_tui._summarizer.config.enabled = True  # Currently enabled
        mock_tui._summarizer._client = None  # No client to close
        mock_tui._summaries = {}
        mock_tui.query.return_value = []

        mock_client_class.is_available = mock_is_available

        # Should not raise
        DaemonActionsMixin.action_toggle_summarizer(mock_tui)

        assert mock_tui._summarizer.config.enabled is False


class TestMonitorRestart:
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

    @patch("overcode.monitor_daemon.stop_monitor_daemon")
    @patch("overcode.monitor_daemon.is_monitor_daemon_running", return_value=True)
    def test_restart_when_running_stops_then_uses_timer(self, mock_is_running, mock_stop):
        """Should stop daemon and use timer to start again when currently running."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        mock_panel = MagicMock()
        mock_panel.log_lines = []

        mock_tui = MagicMock()
        mock_tui.tmux_session = "test"
        mock_tui.query_one.return_value = mock_panel

        DaemonActionsMixin.action_monitor_restart(mock_tui)

        mock_stop.assert_called_once_with("test")
        mock_tui.set_timer.assert_called_once()
        # Timer should be 0.5 seconds
        assert mock_tui.set_timer.call_args[0][0] == 0.5
        # Timer callback should be _start_monitor_daemon bound to mock_tui
        assert mock_tui.set_timer.call_args[0][1] == mock_tui._start_monitor_daemon
        assert any("Restarting" in line for line in mock_panel.log_lines)

    @patch("overcode.monitor_daemon.is_monitor_daemon_running", return_value=False)
    def test_start_when_not_running(self, mock_is_running):
        """Should call _start_monitor_daemon directly when not currently running."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        mock_tui = MagicMock()
        mock_tui.tmux_session = "test"

        DaemonActionsMixin.action_monitor_restart(mock_tui)

        mock_tui._start_monitor_daemon.assert_called_once()
        # Should NOT use a timer when not running
        mock_tui.set_timer.assert_not_called()

    @patch("overcode.monitor_daemon.is_monitor_daemon_running", return_value=False)
    def test_restart_handles_missing_panel(self, mock_is_running):
        """Should not crash when daemon panel is not mounted."""
        from overcode.tui_actions.daemon import DaemonActionsMixin
        from textual.css.query import NoMatches

        mock_tui = MagicMock()
        mock_tui.tmux_session = "test"
        mock_tui.query_one.side_effect = NoMatches()

        # Should not raise
        DaemonActionsMixin.action_monitor_restart(mock_tui)

        mock_tui._start_monitor_daemon.assert_called_once()


class TestStartMonitorDaemon:
    """Test _start_monitor_daemon method."""

    @patch("overcode.tui_actions.daemon.subprocess")
    @patch("overcode.tui_actions.daemon.sys")
    def test_successful_start(self, mock_sys, mock_subprocess):
        """Should call Popen to start monitor daemon."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        mock_sys.executable = "/usr/bin/python3"

        mock_panel = MagicMock()
        mock_panel.log_lines = []

        mock_tui = MagicMock()
        mock_tui.tmux_session = "test"
        mock_tui.query_one.return_value = mock_panel

        DaemonActionsMixin._start_monitor_daemon(mock_tui)

        mock_subprocess.Popen.assert_called_once()
        call_args = mock_subprocess.Popen.call_args
        assert call_args[0][0] == ["/usr/bin/python3", "-m", "overcode.monitor_daemon", "--session", "test"]
        assert call_args[1]["start_new_session"] is True
        mock_tui.notify.assert_called_once()
        assert "restarted" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "information"
        assert any("restarted" in line for line in mock_panel.log_lines)
        mock_tui.set_timer.assert_called_once_with(1.0, mock_tui.update_daemon_status)

    @patch("overcode.tui_actions.daemon.subprocess.Popen", side_effect=OSError("Command not found"))
    def test_oserror(self, mock_popen):
        """Should notify error when Popen raises OSError."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        mock_tui = MagicMock()
        mock_tui.tmux_session = "test"

        DaemonActionsMixin._start_monitor_daemon(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "Failed" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "error"
        # Should NOT set timer on failure
        mock_tui.set_timer.assert_not_called()

    @patch("overcode.tui_actions.daemon.subprocess")
    @patch("overcode.tui_actions.daemon.sys")
    def test_start_handles_missing_panel(self, mock_sys, mock_subprocess):
        """Should not crash when daemon panel is not mounted."""
        from overcode.tui_actions.daemon import DaemonActionsMixin
        from textual.css.query import NoMatches

        mock_tui = MagicMock()
        mock_tui.tmux_session = "test"
        mock_tui.query_one.side_effect = NoMatches()

        # Should not raise
        DaemonActionsMixin._start_monitor_daemon(mock_tui)

        mock_subprocess.Popen.assert_called_once()
        mock_tui.notify.assert_called_once()
        assert "restarted" in mock_tui.notify.call_args[0][0]


class TestToggleWebServer:
    """Test action_toggle_web_server method."""

    def test_method_exists(self):
        """Should have action_toggle_web_server method."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        assert hasattr(DaemonActionsMixin, 'action_toggle_web_server')
        assert callable(getattr(DaemonActionsMixin, 'action_toggle_web_server'))

    @patch("overcode.web_server.get_web_server_url", return_value="http://localhost:8080")
    @patch("overcode.web_server.toggle_web_server", return_value=(True, "started"))
    def test_toggling_on(self, mock_toggle, mock_get_url):
        """Should start web server, notify with URL, and log to panel."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        mock_panel = MagicMock()
        mock_panel.log_lines = []

        mock_tui = MagicMock()
        mock_tui.tmux_session = "test"
        mock_tui.query_one.return_value = mock_panel

        DaemonActionsMixin.action_toggle_web_server(mock_tui)

        mock_toggle.assert_called_once_with("test")
        mock_get_url.assert_called_once_with("test")
        mock_tui.notify.assert_called_once()
        assert "http://localhost:8080" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "information"
        assert any("http://localhost:8080" in line for line in mock_panel.log_lines)
        mock_tui.update_daemon_status.assert_called_once()

    @patch("overcode.web_server.toggle_web_server", return_value=(False, "stopped"))
    def test_toggling_off(self, mock_toggle):
        """Should stop web server and notify with message."""
        from overcode.tui_actions.daemon import DaemonActionsMixin

        mock_panel = MagicMock()
        mock_panel.log_lines = []

        mock_tui = MagicMock()
        mock_tui.tmux_session = "test"
        mock_tui.query_one.return_value = mock_panel

        DaemonActionsMixin.action_toggle_web_server(mock_tui)

        mock_toggle.assert_called_once_with("test")
        mock_tui.notify.assert_called_once()
        assert "stopped" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "information"
        assert any("stopped" in line for line in mock_panel.log_lines)
        mock_tui.update_daemon_status.assert_called_once()

    @patch("overcode.web_server.toggle_web_server", return_value=(False, "stopped"))
    def test_toggle_off_handles_missing_panel(self, mock_toggle):
        """Should not crash when daemon panel is not mounted."""
        from overcode.tui_actions.daemon import DaemonActionsMixin
        from textual.css.query import NoMatches

        mock_tui = MagicMock()
        mock_tui.tmux_session = "test"
        mock_tui.query_one.side_effect = NoMatches()

        # Should not raise
        DaemonActionsMixin.action_toggle_web_server(mock_tui)

        mock_tui.notify.assert_called_once()
        mock_tui.update_daemon_status.assert_called_once()

    @patch("overcode.web_server.get_web_server_url", return_value="http://localhost:8080")
    @patch("overcode.web_server.toggle_web_server", return_value=(True, "started"))
    def test_toggle_on_handles_missing_panel(self, mock_toggle, mock_get_url):
        """Should not crash when daemon panel is not mounted during start."""
        from overcode.tui_actions.daemon import DaemonActionsMixin
        from textual.css.query import NoMatches

        mock_tui = MagicMock()
        mock_tui.tmux_session = "test"
        mock_tui.query_one.side_effect = NoMatches()

        # Should not raise
        DaemonActionsMixin.action_toggle_web_server(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "http://localhost:8080" in mock_tui.notify.call_args[0][0]


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
