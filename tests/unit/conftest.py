"""
Unit test configuration for Overcode.

This module provides fixtures for unit tests that need isolated state directories.
"""

import os
import pytest
import tempfile
import shutil


@pytest.fixture(autouse=True)
def isolated_state_dir(request):
    """Automatically isolate state directory for tests that use TUI or daemons.

    This fixture is auto-used but only activates for tests that might start
    daemons (e.g., TUI tests). It sets OVERCODE_STATE_DIR to a temp directory
    so any daemons started during tests don't pollute the user's ~/.overcode.

    The fixture checks if the test file contains certain markers that indicate
    it might start daemons.
    """
    # Only activate for tests that might start daemons
    test_file = str(request.fspath)
    needs_isolation = any(marker in test_file for marker in [
        "test_tui.py",
        "test_command_bar.py",
    ])

    if not needs_isolation:
        yield
        return

    # Create temp directory for state
    state_dir = tempfile.mkdtemp(prefix="overcode-unit-test-")

    # Save original value
    orig_state_dir = os.environ.get("OVERCODE_STATE_DIR")

    # Set environment variable so child processes inherit it
    os.environ["OVERCODE_STATE_DIR"] = state_dir

    try:
        yield state_dir
    finally:
        # Stop any daemons that were started during the test
        _stop_test_daemons(state_dir)

        # Wait a bit for daemons to fully exit
        import time
        time.sleep(0.5)

        # Retry cleanup - daemon might have written PID file after first attempt
        _stop_test_daemons(state_dir)

        # Remove temp state directory
        shutil.rmtree(state_dir, ignore_errors=True)

        # Restore original environment
        if orig_state_dir is None:
            os.environ.pop("OVERCODE_STATE_DIR", None)
        else:
            os.environ["OVERCODE_STATE_DIR"] = orig_state_dir


def _stop_test_daemons(state_dir: str) -> None:
    """Stop any daemons that might be running with the test's state directory.

    This finds all session subdirectories and kills any daemons by PID file.
    Also kills any processes that have this state_dir in their environment.
    """
    import signal
    import subprocess
    from pathlib import Path

    state_path = Path(state_dir)

    # Method 1: Kill by PID file
    if state_path.exists():
        for session_dir in state_path.iterdir():
            if not session_dir.is_dir():
                continue
            _kill_daemon_by_pid_file(session_dir / "monitor_daemon.pid")
            _kill_daemon_by_pid_file(session_dir / "supervisor_daemon.pid")

    # Method 2: Kill any daemon processes with this state_dir in their environment
    # This catches daemons that haven't written their PID file yet
    try:
        result = subprocess.run(
            ["pgrep", "-f", f"monitor_daemon.*"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            for pid_str in result.stdout.strip().split('\n'):
                if not pid_str:
                    continue
                pid = int(pid_str)
                # Check if this process has our state_dir
                try:
                    env_result = subprocess.run(
                        ["ps", "eww", str(pid)],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if state_dir in env_result.stdout:
                        os.kill(pid, signal.SIGTERM)
                        import time
                        time.sleep(0.3)
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except (OSError, ProcessLookupError):
                            pass
                except (subprocess.SubprocessError, ValueError, OSError):
                    pass
    except (subprocess.SubprocessError, ValueError):
        pass


def _kill_daemon_by_pid_file(pid_file) -> None:
    """Kill a daemon process by reading its PID file."""
    import signal
    import time

    if not pid_file.exists():
        return

    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        # Wait briefly for graceful shutdown
        time.sleep(0.3)
        try:
            os.kill(pid, 0)
            # Still running, force kill
            os.kill(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    except (ValueError, OSError, ProcessLookupError):
        pass
    finally:
        try:
            pid_file.unlink()
        except FileNotFoundError:
            pass
