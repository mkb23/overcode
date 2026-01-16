"""
E2E test fixtures for Overcode.

These fixtures set up mock claude environment for fast, deterministic testing.
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

# Test tmux socket (isolated from user's tmux)
TEST_TMUX_SOCKET = "overcode-test"


def stop_daemons_for_session(state_dir: Path, session_name: str) -> None:
    """Stop any daemons running for a test session.

    Args:
        state_dir: The OVERCODE_STATE_DIR for the test
        session_name: The tmux session name
    """
    session_dir = state_dir / session_name

    # Stop monitor daemon
    monitor_pid_file = session_dir / "monitor_daemon.pid"
    if monitor_pid_file.exists():
        try:
            pid = int(monitor_pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            # Wait briefly for graceful shutdown
            time.sleep(0.5)
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
                monitor_pid_file.unlink()
            except FileNotFoundError:
                pass

    # Stop supervisor daemon
    supervisor_pid_file = session_dir / "supervisor_daemon.pid"
    if supervisor_pid_file.exists():
        try:
            pid = int(supervisor_pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
                os.kill(pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
        except (ValueError, OSError, ProcessLookupError):
            pass
        finally:
            try:
                supervisor_pid_file.unlink()
            except FileNotFoundError:
                pass


@pytest.fixture
def test_session_name() -> str:
    """Generate unique test session name."""
    return f"test-agents-{os.getpid()}"


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

    # Set up environment
    env = os.environ.copy()
    env["CLAUDE_COMMAND"] = str(MOCK_CLAUDE)
    env["OVERCODE_STATE_DIR"] = state_dir
    env["OVERCODE_TMUX_SOCKET"] = TEST_TMUX_SOCKET
    env["PYTHONPATH"] = str(SRC_DIR)

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


@pytest.fixture
def overcode_cli(clean_test_env: dict):
    """Helper to run overcode CLI commands.

    Returns function that runs overcode with test environment.
    """
    def run_cli(*args, timeout: int = 10, **kwargs) -> subprocess.CompletedProcess:
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
