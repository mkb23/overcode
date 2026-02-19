"""Tests for web_server_runner module."""

import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from overcode.web_server_runner import get_log_path, log, main


class TestGetLogPath:
    """Tests for get_log_path."""

    def test_returns_path_in_session_dir(self):
        with patch("overcode.settings.get_session_dir") as mock_dir:
            mock_dir.return_value = Path("/tmp/overcode/sessions/agents")
            result = get_log_path("agents")

        assert result == Path("/tmp/overcode/sessions/agents/web_server.log")

    def test_uses_correct_session_name(self):
        with patch("overcode.settings.get_session_dir") as mock_dir:
            mock_dir.return_value = Path("/tmp/sessions/my-session")
            get_log_path("my-session")

        mock_dir.assert_called_once_with("my-session")


class TestLog:
    """Tests for log function."""

    def test_writes_log_message(self, tmp_path):
        log_file = tmp_path / "web_server.log"
        with patch("overcode.settings.get_session_dir", return_value=tmp_path):
            log("agents", "test message")

        assert log_file.exists()
        content = log_file.read_text()
        assert "test message" in content
        assert "]" in content  # timestamp bracket

    def test_appends_to_existing_log(self, tmp_path):
        log_file = tmp_path / "web_server.log"
        log_file.write_text("existing content\n")

        with patch("overcode.settings.get_session_dir", return_value=tmp_path):
            log("agents", "new message")

        content = log_file.read_text()
        assert "existing content" in content
        assert "new message" in content

    def test_creates_parent_dirs(self, tmp_path):
        nested_dir = tmp_path / "deep" / "nested"
        with patch("overcode.settings.get_session_dir", return_value=nested_dir):
            log("agents", "nested log")

        assert (nested_dir / "web_server.log").exists()

    def test_silently_fails_on_error(self):
        """Log should never raise, even on I/O errors."""
        with patch("overcode.settings.get_session_dir", side_effect=Exception("boom")):
            # Should not raise
            log("agents", "test")


class TestMain:
    """Tests for the main() entry point."""

    def test_main_parses_args_and_starts_server(self):
        test_args = ["prog", "--session", "test-session", "--port", "9999"]

        with patch.object(sys, "argv", test_args):
            with patch("overcode.settings.get_session_dir", return_value=Path("/tmp/test")):
                with patch("overcode.pid_utils.write_pid_file"):
                    with patch("overcode.settings.get_web_server_pid_path",
                               return_value=MagicMock()):
                        with patch("overcode.settings.get_web_server_port_path") as mock_port_path:
                            mock_port_obj = MagicMock()
                            mock_port_path.return_value = mock_port_obj
                            with patch("overcode.web_server_runner.HTTPServer") as mock_server_cls:
                                mock_server = MagicMock()
                                mock_server.serve_forever.side_effect = KeyboardInterrupt()
                                mock_server_cls.return_value = mock_server
                                with patch("overcode.web_server_runner.OvercodeHandler", create=True):
                                    with patch("builtins.open", MagicMock()):
                                        try:
                                            main()
                                        except (KeyboardInterrupt, SystemExit):
                                            pass

    def test_main_cleans_up_on_error(self):
        test_args = ["prog", "--session", "test-session", "--port", "9999"]

        mock_pid_path = MagicMock()
        mock_port_path = MagicMock()

        with patch.object(sys, "argv", test_args):
            with patch("overcode.settings.get_session_dir", return_value=Path("/tmp/test")):
                with patch("overcode.pid_utils.write_pid_file",
                           side_effect=Exception("write failed")):
                    with patch("overcode.settings.get_web_server_pid_path",
                               return_value=mock_pid_path):
                        with patch("overcode.settings.get_web_server_port_path",
                                   return_value=mock_port_path):
                            with pytest.raises(SystemExit):
                                main()

            # PID and port files should be cleaned up
            mock_pid_path.unlink.assert_called_once_with(missing_ok=True)
            mock_port_path.unlink.assert_called_once_with(missing_ok=True)
