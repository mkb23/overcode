"""
Shared daemon cleanup utilities for test conftest files.

Consolidates duplicate PID-file killing and process hunting logic
used by both unit and e2e test teardowns.
"""

import os
import signal
import subprocess
import time
from pathlib import Path


def kill_by_pid_file(pid_file: Path) -> None:
    """Kill a daemon process by reading its PID file.

    Sends SIGTERM, waits briefly, then SIGKILL if still alive.
    Always removes the PID file.
    """
    if not pid_file.exists():
        return

    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.3)
        try:
            os.kill(pid, 0)
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


def kill_daemons_by_env(state_dir: str, pgrep_pattern: str = "monitor_daemon") -> None:
    """Kill daemon processes that have state_dir in their environment.

    Uses pgrep to find candidate processes, then verifies via ps eww
    that the process actually belongs to this test's state directory.

    Args:
        state_dir: The OVERCODE_STATE_DIR to match against
        pgrep_pattern: Pattern for pgrep -f (default: "monitor_daemon")
    """
    try:
        result = subprocess.run(
            ["pgrep", "-f", pgrep_pattern],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return

        for pid_str in result.stdout.strip().split("\n"):
            if not pid_str:
                continue
            try:
                pid = int(pid_str)
                env_result = subprocess.run(
                    ["ps", "eww", str(pid)],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if state_dir in env_result.stdout:
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


def stop_daemons_in_state_dir(state_dir: str, session_name: str | None = None) -> None:
    """Stop all daemons associated with a test state directory.

    Combines PID-file based killing with process hunting as a fallback.

    Args:
        state_dir: Path to the OVERCODE_STATE_DIR
        session_name: Optional tmux session name for more targeted pgrep
    """
    state_path = Path(state_dir)

    # Method 1: Kill by PID file
    if state_path.exists():
        for session_dir in state_path.iterdir():
            if not session_dir.is_dir():
                continue
            kill_by_pid_file(session_dir / "monitor_daemon.pid")
            kill_by_pid_file(session_dir / "supervisor_daemon.pid")

    # Method 2: Hunt by process environment
    pattern = f"monitor_daemon.*{session_name}" if session_name else "monitor_daemon.*"
    kill_daemons_by_env(state_dir, pgrep_pattern=pattern)
