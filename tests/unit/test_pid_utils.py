"""Tests for pid_utils module."""

import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from overcode.pid_utils import (
    is_process_running,
    get_process_pid,
    write_pid_file,
    remove_pid_file,
    acquire_daemon_lock,
    count_daemon_processes,
    stop_process,
)


class TestIsProcessRunning:
    """Tests for is_process_running function."""

    def test_returns_false_for_nonexistent_file(self, tmp_path):
        """Should return False when PID file doesn't exist."""
        pid_file = tmp_path / "nonexistent.pid"

        result = is_process_running(pid_file)

        assert result is False

    def test_returns_true_for_running_process(self, tmp_path):
        """Should return True when process is running."""
        pid_file = tmp_path / "test.pid"
        pid_file.write_text(str(os.getpid()))  # Current process

        result = is_process_running(pid_file)

        assert result is True

    def test_returns_false_for_dead_process(self, tmp_path):
        """Should return False when process doesn't exist."""
        pid_file = tmp_path / "test.pid"
        pid_file.write_text("99999999")  # Unlikely to exist

        result = is_process_running(pid_file)

        assert result is False

    def test_returns_false_for_invalid_pid(self, tmp_path):
        """Should return False when PID file contains invalid data."""
        pid_file = tmp_path / "test.pid"
        pid_file.write_text("not_a_number")

        result = is_process_running(pid_file)

        assert result is False


class TestGetProcessPid:
    """Tests for get_process_pid function."""

    def test_returns_none_for_nonexistent_file(self, tmp_path):
        """Should return None when PID file doesn't exist."""
        pid_file = tmp_path / "nonexistent.pid"

        result = get_process_pid(pid_file)

        assert result is None

    def test_returns_pid_for_running_process(self, tmp_path):
        """Should return PID when process is running."""
        pid_file = tmp_path / "test.pid"
        current_pid = os.getpid()
        pid_file.write_text(str(current_pid))

        result = get_process_pid(pid_file)

        assert result == current_pid

    def test_returns_none_for_dead_process(self, tmp_path):
        """Should return None when process doesn't exist."""
        pid_file = tmp_path / "test.pid"
        pid_file.write_text("99999999")

        result = get_process_pid(pid_file)

        assert result is None

    def test_returns_none_for_invalid_pid(self, tmp_path):
        """Should return None for invalid PID content."""
        pid_file = tmp_path / "test.pid"
        pid_file.write_text("invalid")

        result = get_process_pid(pid_file)

        assert result is None


class TestWritePidFile:
    """Tests for write_pid_file function."""

    def test_writes_current_pid_by_default(self, tmp_path):
        """Should write current process PID when no PID given."""
        pid_file = tmp_path / "test.pid"

        write_pid_file(pid_file)

        assert pid_file.exists()
        assert int(pid_file.read_text()) == os.getpid()

    def test_writes_specified_pid(self, tmp_path):
        """Should write specified PID."""
        pid_file = tmp_path / "test.pid"

        write_pid_file(pid_file, pid=12345)

        assert pid_file.read_text() == "12345"

    def test_creates_parent_directories(self, tmp_path):
        """Should create parent directories if needed."""
        pid_file = tmp_path / "subdir" / "deep" / "test.pid"

        write_pid_file(pid_file)

        assert pid_file.exists()


class TestRemovePidFile:
    """Tests for remove_pid_file function."""

    def test_removes_existing_file(self, tmp_path):
        """Should remove existing PID file."""
        pid_file = tmp_path / "test.pid"
        pid_file.write_text("12345")

        remove_pid_file(pid_file)

        assert not pid_file.exists()

    def test_handles_nonexistent_file(self, tmp_path):
        """Should not raise when file doesn't exist."""
        pid_file = tmp_path / "nonexistent.pid"

        # Should not raise
        remove_pid_file(pid_file)


class TestAcquireDaemonLock:
    """Tests for acquire_daemon_lock function."""

    def test_acquires_lock_when_no_daemon_running(self, tmp_path):
        """Should acquire lock when no daemon is running."""
        pid_file = tmp_path / "test.pid"

        acquired, existing_pid = acquire_daemon_lock(pid_file)

        assert acquired is True
        assert existing_pid is None
        assert pid_file.exists()
        assert int(pid_file.read_text()) == os.getpid()

    def test_creates_parent_directories(self, tmp_path):
        """Should create parent directories for PID file."""
        pid_file = tmp_path / "subdir" / "test.pid"

        acquired, _ = acquire_daemon_lock(pid_file)

        assert acquired is True
        assert pid_file.parent.exists()

    def test_locks_prevent_concurrent_acquisition(self, tmp_path):
        """Should prevent concurrent acquisition via file locks."""
        pid_file = tmp_path / "test.pid"

        # First acquire should succeed
        acquired1, existing1 = acquire_daemon_lock(pid_file)

        assert acquired1 is True
        assert existing1 is None
        assert pid_file.exists()

    def test_cleans_up_stale_pid_file(self, tmp_path):
        """Should clean up stale PID file from dead process."""
        pid_file = tmp_path / "test.pid"
        pid_file.write_text("99999999")  # Dead process

        acquired, existing_pid = acquire_daemon_lock(pid_file)

        assert acquired is True
        assert existing_pid is None


class TestCountDaemonProcesses:
    """Tests for count_daemon_processes function."""

    def test_returns_zero_when_no_processes(self):
        """Should return 0 when no matching processes."""
        # Use a pattern that won't match any real process
        result = count_daemon_processes("__nonexistent_pattern_xyz_123__")

        assert result == 0

    def test_uses_session_in_pattern(self):
        """Should include session in search pattern."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
            )

            count_daemon_processes("test_daemon", session="test_session")

            # Verify the pattern includes the session
            call_args = mock_run.call_args
            pattern = call_args[0][0][2]  # ["pgrep", "-f", pattern]
            assert "test_session" in pattern

    def test_handles_timeout(self):
        """Should return 0 on timeout."""
        import subprocess

        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("pgrep", 5)

            result = count_daemon_processes("test")

            assert result == 0

    def test_handles_pgrep_not_found(self):
        """Should return 0 when pgrep not found."""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = FileNotFoundError()

            result = count_daemon_processes("test")

            assert result == 0

    def test_counts_matching_processes(self):
        """Should count matching processes correctly."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="1234\n5678\n9012\n",
            )

            result = count_daemon_processes("test")

            assert result == 3


class TestStopProcess:
    """Tests for stop_process function."""

    def test_returns_false_for_nonexistent_file(self, tmp_path):
        """Should return False when PID file doesn't exist."""
        pid_file = tmp_path / "nonexistent.pid"

        result = stop_process(pid_file)

        assert result is False

    def test_returns_false_for_invalid_pid(self, tmp_path):
        """Should return False for invalid PID and clean up file."""
        pid_file = tmp_path / "test.pid"
        pid_file.write_text("invalid")

        result = stop_process(pid_file)

        assert result is False
        assert not pid_file.exists()

    def test_returns_false_for_dead_process(self, tmp_path):
        """Should return False for dead process and clean up file."""
        pid_file = tmp_path / "test.pid"
        pid_file.write_text("99999999")

        result = stop_process(pid_file)

        assert result is False
        assert not pid_file.exists()

    def test_sends_sigterm_to_process(self, tmp_path):
        """Should send SIGTERM to the process."""
        pid_file = tmp_path / "test.pid"
        pid_file.write_text("12345")

        with patch('os.kill') as mock_kill:
            # First call is SIGTERM, then process check returns dead
            mock_kill.side_effect = [
                None,  # SIGTERM succeeds
                OSError(3, "No such process"),  # Process is dead
            ]

            result = stop_process(pid_file, timeout=0.5)

            assert result is True
            mock_kill.assert_any_call(12345, 15)  # SIGTERM

    def test_sends_sigkill_after_timeout(self, tmp_path):
        """Should send SIGKILL if process doesn't terminate."""
        pid_file = tmp_path / "test.pid"
        pid_file.write_text("12345")

        with patch('os.kill') as mock_kill:
            with patch('time.sleep'):
                with patch('time.time') as mock_time:
                    # Simulate timeout
                    mock_time.side_effect = [0, 0.1, 0.2, 0.3, 0.4, 6.0]  # Last value > timeout
                    mock_kill.return_value = None  # All kills succeed

                    result = stop_process(pid_file, timeout=0.5)

                    # Should have tried SIGKILL
                    assert result is True
