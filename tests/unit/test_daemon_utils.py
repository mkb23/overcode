"""Tests for daemon_utils module."""

import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from overcode.daemon_utils import create_daemon_helpers


class TestCreateDaemonHelpers:
    """Tests for create_daemon_helpers factory function."""

    def test_returns_three_callables(self, tmp_path):
        """Should return three callable functions."""
        def get_pid_path(session):
            return tmp_path / f"{session}.pid"

        is_running, get_pid, stop = create_daemon_helpers(get_pid_path, "test_daemon")

        assert callable(is_running)
        assert callable(get_pid)
        assert callable(stop)

    def test_is_running_returns_false_when_no_file(self, tmp_path):
        """is_running should return False when PID file doesn't exist."""
        def get_pid_path(session):
            return tmp_path / f"{session}.pid"

        is_running, _, _ = create_daemon_helpers(get_pid_path, "test_daemon")

        result = is_running("test_session")

        assert result is False

    def test_is_running_returns_true_when_running(self, tmp_path):
        """is_running should return True when process is running."""
        def get_pid_path(session):
            return tmp_path / f"{session}.pid"

        pid_file = tmp_path / "test_session.pid"
        pid_file.write_text(str(os.getpid()))

        is_running, _, _ = create_daemon_helpers(get_pid_path, "test_daemon")

        result = is_running("test_session")

        assert result is True

    def test_is_running_uses_default_session(self, tmp_path):
        """is_running should use default session when None."""
        def get_pid_path(session):
            return tmp_path / f"{session}.pid"

        is_running, _, _ = create_daemon_helpers(get_pid_path, "test_daemon")

        with patch('overcode.daemon_utils.DAEMON') as mock_daemon:
            mock_daemon.default_tmux_session = "default_session"

            pid_file = tmp_path / "default_session.pid"
            pid_file.write_text(str(os.getpid()))

            result = is_running()

        assert result is True

    def test_get_pid_returns_none_when_no_file(self, tmp_path):
        """get_pid should return None when PID file doesn't exist."""
        def get_pid_path(session):
            return tmp_path / f"{session}.pid"

        _, get_pid, _ = create_daemon_helpers(get_pid_path, "test_daemon")

        result = get_pid("test_session")

        assert result is None

    def test_get_pid_returns_pid_when_running(self, tmp_path):
        """get_pid should return PID when process is running."""
        def get_pid_path(session):
            return tmp_path / f"{session}.pid"

        pid_file = tmp_path / "test_session.pid"
        current_pid = os.getpid()
        pid_file.write_text(str(current_pid))

        _, get_pid, _ = create_daemon_helpers(get_pid_path, "test_daemon")

        result = get_pid("test_session")

        assert result == current_pid

    def test_get_pid_uses_default_session(self, tmp_path):
        """get_pid should use default session when None."""
        def get_pid_path(session):
            return tmp_path / f"{session}.pid"

        _, get_pid, _ = create_daemon_helpers(get_pid_path, "test_daemon")

        with patch('overcode.daemon_utils.DAEMON') as mock_daemon:
            mock_daemon.default_tmux_session = "default_session"

            result = get_pid()

        # Should not raise, just return None since no file
        assert result is None

    def test_stop_returns_false_when_not_running(self, tmp_path):
        """stop should return False when daemon not running."""
        def get_pid_path(session):
            return tmp_path / f"{session}.pid"

        _, _, stop = create_daemon_helpers(get_pid_path, "test_daemon")

        result = stop("test_session")

        assert result is False

    def test_stop_sends_sigterm(self, tmp_path):
        """stop should send SIGTERM and wait for process to exit."""
        def get_pid_path(session):
            return tmp_path / f"{session}.pid"

        pid_file = tmp_path / "test_session.pid"
        pid_file.write_text("12345")

        _, _, stop = create_daemon_helpers(get_pid_path, "test_daemon")

        def kill_side_effect(pid, sig):
            if sig == 0:
                raise ProcessLookupError("No such process")

        with patch('os.kill', side_effect=kill_side_effect) as mock_kill:
            with patch('overcode.daemon_utils.get_process_pid', return_value=12345):
                result = stop("test_session")

                mock_kill.assert_any_call(12345, 15)  # SIGTERM = 15
                assert result is True

    def test_stop_removes_pid_file_on_success(self, tmp_path):
        """stop should remove PID file after process exits."""
        def get_pid_path(session):
            return tmp_path / f"{session}.pid"

        pid_file = tmp_path / "test_session.pid"
        pid_file.write_text("12345")

        _, _, stop = create_daemon_helpers(get_pid_path, "test_daemon")

        def kill_side_effect(pid, sig):
            if sig == 0:
                raise ProcessLookupError("No such process")

        with patch('os.kill', side_effect=kill_side_effect):
            with patch('overcode.daemon_utils.get_process_pid', return_value=12345):
                stop("test_session")

                assert not pid_file.exists()

    def test_stop_removes_pid_file_on_oserror(self, tmp_path):
        """stop should remove PID file even if kill fails."""
        def get_pid_path(session):
            return tmp_path / f"{session}.pid"

        pid_file = tmp_path / "test_session.pid"
        pid_file.write_text("12345")

        _, _, stop = create_daemon_helpers(get_pid_path, "test_daemon")

        with patch('os.kill', side_effect=OSError("No such process")):
            with patch('overcode.daemon_utils.get_process_pid', return_value=12345):
                result = stop("test_session")

                assert result is False
                assert not pid_file.exists()

    def test_stop_uses_default_session(self, tmp_path):
        """stop should use default session when None."""
        def get_pid_path(session):
            return tmp_path / f"{session}.pid"

        _, _, stop = create_daemon_helpers(get_pid_path, "test_daemon")

        with patch('overcode.daemon_utils.DAEMON') as mock_daemon:
            mock_daemon.default_tmux_session = "default_session"

            result = stop()

        # Should not raise
        assert result is False
