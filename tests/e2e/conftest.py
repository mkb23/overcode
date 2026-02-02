"""
E2E test fixtures for Overcode.

These fixtures set up mock claude environment for fast, deterministic testing.
Includes subprocess coverage collection support for combined unit+e2e coverage.
"""

import os
import signal
import pytest
import subprocess
import time
import json
from pathlib import Path
from typing import Generator, Optional
import tempfile
import shutil


# Paths
TESTS_DIR = Path(__file__).parent.parent
MOCK_CLAUDE = TESTS_DIR / "mock_claude.py"
PROJECT_ROOT = TESTS_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"
COVERAGERC = PROJECT_ROOT / ".coveragerc"

# Test tmux socket (isolated from user's tmux)
TEST_TMUX_SOCKET = "overcode-test"


def stop_daemons_for_session(state_dir: Path, session_name: str) -> None:
    """Stop any daemons running for a test session.

    Args:
        state_dir: The OVERCODE_STATE_DIR for the test
        session_name: The tmux session name
    """
    session_dir = state_dir / session_name

    # Wait briefly for daemon to write PID file (it starts asynchronously)
    for _ in range(10):  # Wait up to 1 second
        monitor_pid_file = session_dir / "monitor_daemon.pid"
        if monitor_pid_file.exists():
            break
        time.sleep(0.1)

    # Stop monitor daemon by PID file
    _kill_by_pid_file(session_dir / "monitor_daemon.pid")

    # Stop supervisor daemon by PID file
    _kill_by_pid_file(session_dir / "supervisor_daemon.pid")

    # Also kill any daemon processes with this session name (backup method)
    # This catches daemons that haven't written their PID file yet
    _kill_daemons_by_session_name(session_name, state_dir)


def _kill_by_pid_file(pid_file: Path) -> None:
    """Kill a process by reading its PID file."""
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


def _kill_daemons_by_session_name(session_name: str, state_dir: Path) -> None:
    """Kill any daemon processes matching the session name and state_dir."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", f"monitor_daemon.*{session_name}"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            for pid_str in result.stdout.strip().split('\n'):
                if not pid_str:
                    continue
                try:
                    pid = int(pid_str)
                    # Verify this daemon belongs to our test (check state_dir in env)
                    env_result = subprocess.run(
                        ["ps", "eww", str(pid)],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if str(state_dir) in env_result.stdout:
                        os.kill(pid, signal.SIGTERM)
                        time.sleep(0.2)
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except (OSError, ProcessLookupError):
                            pass
                except (ValueError, OSError, subprocess.SubprocessError):
                    pass
    except (subprocess.SubprocessError, ValueError):
        pass


@pytest.fixture
def test_session_name() -> str:
    """Generate unique test session name.

    Uses a combination of PID and a random suffix to ensure uniqueness
    across multiple tests in the same pytest run.
    """
    import random
    return f"test-agents-{os.getpid()}-{random.randint(10000, 99999)}"


@pytest.fixture
def clean_test_env(test_session_name: str) -> Generator[dict, None, None]:
    """Set up clean test environment with mock claude.

    Yields:
        dict with:
            - session_name: tmux session name
            - state_dir: temp directory for state files
            - env: environment variables to use
    """
    # Create temp directory for state
    state_dir = tempfile.mkdtemp(prefix="overcode-test-")

    # Kill any existing test session
    subprocess.run(
        ["tmux", "-L", TEST_TMUX_SOCKET, "kill-session", "-t", test_session_name],
        capture_output=True
    )

    # Set up environment - both in env dict AND os.environ
    # os.environ must be set for subprocess.Popen() calls that don't pass env=
    # (e.g., TUI's _ensure_monitor_daemon)
    env = os.environ.copy()
    env["CLAUDE_COMMAND"] = str(MOCK_CLAUDE)
    env["OVERCODE_STATE_DIR"] = state_dir
    env["OVERCODE_TMUX_SOCKET"] = TEST_TMUX_SOCKET
    env["PYTHONPATH"] = str(SRC_DIR)

    # Propagate coverage settings to subprocesses for combined coverage
    # This allows e2e subprocess coverage to be collected alongside unit tests
    if COVERAGERC.exists():
        env["COVERAGE_PROCESS_START"] = str(COVERAGERC)
    # Propagate pytest-cov's subprocess coverage vars if present
    for cov_var in ["COV_CORE_SOURCE", "COV_CORE_CONFIG", "COV_CORE_DATAFILE"]:
        if cov_var in os.environ:
            env[cov_var] = os.environ[cov_var]

    # Save original values to restore later
    orig_state_dir = os.environ.get("OVERCODE_STATE_DIR")
    orig_tmux_socket = os.environ.get("OVERCODE_TMUX_SOCKET")

    # Set in os.environ so child processes inherit these
    os.environ["OVERCODE_STATE_DIR"] = state_dir
    os.environ["OVERCODE_TMUX_SOCKET"] = TEST_TMUX_SOCKET

    try:
        yield {
            "session_name": test_session_name,
            "state_dir": Path(state_dir),
            "env": env,
            "tmux_socket": TEST_TMUX_SOCKET,
        }
    finally:
        # Stop any daemons that were started during the test
        stop_daemons_for_session(Path(state_dir), test_session_name)

        # Kill tmux session
        subprocess.run(
            ["tmux", "-L", TEST_TMUX_SOCKET, "kill-session", "-t", test_session_name],
            capture_output=True
        )

        # Remove temp state directory
        shutil.rmtree(state_dir, ignore_errors=True)

        # Restore original environment
        if orig_state_dir is None:
            os.environ.pop("OVERCODE_STATE_DIR", None)
        else:
            os.environ["OVERCODE_STATE_DIR"] = orig_state_dir

        if orig_tmux_socket is None:
            os.environ.pop("OVERCODE_TMUX_SOCKET", None)
        else:
            os.environ["OVERCODE_TMUX_SOCKET"] = orig_tmux_socket


@pytest.fixture
def overcode_cli(clean_test_env: dict):
    """Helper to run overcode CLI commands.

    Returns function that runs overcode with test environment.
    When running under coverage, uses 'coverage run' to collect subprocess coverage.
    """
    def run_cli(*args, timeout: int = 10, **kwargs) -> subprocess.CompletedProcess:
        # Use coverage run when collecting coverage (COVERAGE_PROCESS_START is set)
        if "COVERAGE_PROCESS_START" in clean_test_env["env"]:
            cmd = [
                "coverage", "run", "--parallel-mode",
                "-m", "overcode.cli"
            ] + list(args)
        else:
            cmd = ["python", "-m", "overcode.cli"] + list(args)
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=clean_test_env["env"],
            cwd=PROJECT_ROOT,
            **kwargs
        )

    return run_cli


def wait_for_status(
    overcode_cli,
    agent_name: str,
    session_name: str,
    expected_status: str,
    timeout: float = 10.0,
    poll_interval: float = 0.5
) -> bool:
    """Poll until agent reaches expected status.

    Returns True if status reached, False if timeout.
    """
    start = time.time()
    while time.time() - start < timeout:
        # Get status via list command or direct state file read
        result = overcode_cli("list", "--session", session_name, "--json")
        if result.returncode == 0:
            try:
                sessions = json.loads(result.stdout)
                for session in sessions:
                    if session.get("name") == agent_name:
                        if session.get("status") == expected_status:
                            return True
            except json.JSONDecodeError:
                pass
        time.sleep(poll_interval)
    return False


def get_tmux_pane_content(socket: str, session: str, window: str, lines: int = 50) -> str:
    """Capture tmux pane content."""
    result = subprocess.run(
        ["tmux", "-L", socket, "capture-pane", "-t", f"{session}:{window}", "-p", "-S", f"-{lines}"],
        capture_output=True,
        text=True
    )
    return result.stdout if result.returncode == 0 else ""


def send_to_tmux(socket: str, session: str, window: str, text: str):
    """Send keys to tmux pane."""
    subprocess.run(
        ["tmux", "-L", socket, "send-keys", "-t", f"{session}:{window}", text, "Enter"],
        capture_output=True
    )
