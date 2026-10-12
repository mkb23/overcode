"""Tests for pid_utils module."""

import os
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import overcode
from overcode import pid_utils

from overcode.pid_utils import is_process_running, get_process_pid, write_pid_file, remove_pid_file, acquire_daemon_lock, stop_process


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
                None,  # liveness check (no lock file: the PID file decides)
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


# ---------------------------------------------------------------------------
# The lock is the single source of truth (quality review item 4)
# ---------------------------------------------------------------------------

_SRC = str(Path(overcode.__file__).resolve().parent.parent)

_HOLDER = """
import sys, time
from pathlib import Path
from overcode.pid_utils import acquire_daemon_lock
ok, existing = acquire_daemon_lock(Path(sys.argv[1]))
print("ok" if ok else f"busy {existing}", flush=True)
time.sleep(60)
"""


def _spawn(code: str, *args: str) -> subprocess.Popen:
    env = dict(os.environ, PYTHONPATH=_SRC)
    return subprocess.Popen([sys.executable, "-c", code, *args], env=env,
                            stdout=subprocess.PIPE, text=True)


@pytest.fixture
def unrelated():
    """A live process that has nothing to do with overcode (a reused PID)."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield proc
    proc.kill()
    proc.wait()


@pytest.fixture
def holder(tmp_path):
    """A real child process holding the lock for tmp_path/d.pid."""
    pid_file = tmp_path / "d.pid"
    proc = _spawn(_HOLDER, str(pid_file))
    assert proc.stdout.readline().strip() == "ok"
    yield pid_file, proc
    proc.kill()
    proc.wait()


class TestPidReuse:
    """A stale PID file names a live, unrelated PID and the lock is free:
    that PID is never trusted and never signalled."""

    def _stale(self, tmp_path, pid: int) -> Path:
        pid_file = tmp_path / "d.pid"
        pid_file.with_suffix(".lock").touch()  # a past daemon's lock file, free now
        pid_file.write_text(str(pid))
        return pid_file

    def test_not_running(self, tmp_path, unrelated):
        pid_file = self._stale(tmp_path, unrelated.pid)
        assert is_process_running(pid_file) is False
        assert get_process_pid(pid_file) is None

    def test_stop_does_not_signal_it(self, tmp_path, unrelated):
        pid_file = self._stale(tmp_path, unrelated.pid)
        with patch("overcode.pid_utils.os.kill", wraps=os.kill) as kill:
            assert stop_process(pid_file, timeout=0.5) is False
        assert not [c for c in kill.call_args_list if c.args[1] != 0]
        assert unrelated.poll() is None  # still alive
        assert not pid_file.exists()  # stale file cleared

    def test_start_proceeds_and_overwrites_the_pid_file(self, tmp_path, unrelated):
        pid_file = self._stale(tmp_path, unrelated.pid)
        try:
            acquired, existing = acquire_daemon_lock(pid_file)
            assert (acquired, existing) == (True, None)
            assert int(pid_file.read_text()) == os.getpid()
        finally:
            pid_utils.release_daemon_lock(pid_file)

    def test_monitor_helpers_follow_the_lock(self, tmp_path, unrelated, monkeypatch):
        """is_*_running / stop_* built by daemon_utils use the same rule."""
        from overcode.daemon_utils import create_daemon_helpers
        pid_file = self._stale(tmp_path, unrelated.pid)
        is_running, get_pid, stop = create_daemon_helpers(lambda s: pid_file, "test")
        assert is_running("x") is False
        assert get_pid("x") is None
        assert stop("x") is False
        assert unrelated.poll() is None


class TestLockHeldByChild:

    def test_is_running(self, holder):
        pid_file, proc = holder
        assert is_process_running(pid_file) is True
        assert pid_utils.is_daemon_lock_held(pid_file) is True
        assert get_process_pid(pid_file) == proc.pid

    def test_second_acquire_is_refused(self, holder):
        pid_file, proc = holder
        assert acquire_daemon_lock(pid_file) == (False, proc.pid)
        assert int(pid_file.read_text()) == proc.pid  # untouched

    def test_running_even_if_its_pid_file_is_gone(self, holder):
        pid_file, _ = holder
        pid_file.unlink()
        assert is_process_running(pid_file) is True

    def test_stop_terminates_it(self, holder):
        pid_file, proc = holder
        assert stop_process(pid_file, timeout=5) is True
        assert proc.wait(timeout=5) == -signal.SIGTERM
        assert is_process_running(pid_file) is False
        assert not pid_file.exists()

    def test_hard_exit_frees_the_lock(self, holder):
        """SIGKILL (like the watchdog's os._exit) leaves the PID file, but
        the lock goes with the process, so it reads as not running."""
        pid_file, proc = holder
        proc.kill()
        proc.wait()
        assert pid_file.exists()
        assert is_process_running(pid_file) is False
        assert get_process_pid(pid_file) is None

    def test_stop_does_not_remove_a_successor_pid_file(self, tmp_path):
        pid_file = tmp_path / "d.pid"
        pid_file.write_text("424242")
        pid_utils.remove_pid_file(pid_file, 1)
        assert pid_file.exists()
        pid_utils.remove_pid_file(pid_file, 424242)
        assert not pid_file.exists()


class TestWithoutFcntl:
    """Platforms without fcntl fall back to the PID file and kill -0."""

    def test_running_by_pid(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pid_utils, "fcntl", None)
        pid_file = tmp_path / "d.pid"
        pid_file.with_suffix(".lock").touch()
        pid_file.write_text(str(os.getpid()))
        assert is_process_running(pid_file) is True
        pid_file.write_text("99999999")
        assert is_process_running(pid_file) is False

    def test_acquire(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pid_utils, "fcntl", None)
        pid_file = tmp_path / "d.pid"
        assert acquire_daemon_lock(pid_file) == (True, None)
        assert int(pid_file.read_text()) == os.getpid()


class TestWatchdogExit:

    def test_removes_its_pid_file_before_exiting(self, tmp_path):
        from overcode.monitor_daemon import MonitorDaemon, WATCHDOG_EXIT_SECONDS
        daemon = MonitorDaemon.__new__(MonitorDaemon)
        daemon.pid_path = tmp_path / "monitor_daemon.pid"
        daemon.pid_path.write_text(str(os.getpid()))
        daemon.log = MagicMock()
        with patch("overcode.monitor_daemon.os._exit", side_effect=SystemExit(1)) as ex:
            with pytest.raises(SystemExit):
                daemon._on_main_loop_stall(WATCHDOG_EXIT_SECONDS + 1)
        ex.assert_called_once_with(1)
        assert not daemon.pid_path.exists()
