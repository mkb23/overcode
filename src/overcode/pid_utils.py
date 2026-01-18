"""
PID file management utilities for Overcode.

Provides common functions for checking process status via PID files,
used by both the daemon and presence logger.

Uses file locking to prevent TOCTOU race conditions when multiple
daemons try to start simultaneously.
"""

import fcntl
import os
import signal
from pathlib import Path
from typing import Optional, Tuple


def is_process_running(pid_file: Path) -> bool:
    """Check if a process is running based on its PID file.

    Args:
        pid_file: Path to the PID file

    Returns:
        True if PID file exists and process is alive, False otherwise.
    """
    if not pid_file.exists():
        return False

    try:
        pid = int(pid_file.read_text().strip())
        # Check if process exists by sending signal 0
        os.kill(pid, 0)
        return True
    except (ValueError, OSError, ProcessLookupError):
        # PID file invalid or process not running
        return False


def get_process_pid(pid_file: Path) -> Optional[int]:
    """Get the PID from a PID file if the process is running.

    Args:
        pid_file: Path to the PID file

    Returns:
        The PID if process is running, None otherwise.
    """
    if not pid_file.exists():
        return None

    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)  # Check if alive
        return pid
    except (ValueError, OSError, ProcessLookupError):
        return None


def write_pid_file(pid_file: Path, pid: Optional[int] = None) -> None:
    """Write a PID to a PID file.

    Args:
        pid_file: Path to the PID file
        pid: PID to write (defaults to current process PID)
    """
    if pid is None:
        pid = os.getpid()
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(pid))


def remove_pid_file(pid_file: Path) -> None:
    """Remove a PID file if it exists.

    Args:
        pid_file: Path to the PID file
    """
    try:
        pid_file.unlink()
    except FileNotFoundError:
        pass


def acquire_daemon_lock(pid_file: Path) -> Tuple[bool, Optional[int]]:
    """Atomically check if daemon is running and acquire the lock if not.

    Uses file locking to prevent TOCTOU race conditions when multiple
    processes try to start the daemon simultaneously.

    Args:
        pid_file: Path to the PID file

    Returns:
        Tuple of (acquired, existing_pid):
        - (True, None) if lock was acquired and PID file written
        - (False, existing_pid) if another daemon is already running
    """
    pid_file.parent.mkdir(parents=True, exist_ok=True)

    # Use a separate lock file to avoid truncation issues
    lock_file = pid_file.with_suffix('.lock')

    try:
        # Open lock file for writing (creates if doesn't exist)
        fd = os.open(str(lock_file), os.O_WRONLY | os.O_CREAT, 0o644)

        try:
            # Try to acquire exclusive lock (non-blocking)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

            # We have the lock - now check if another daemon is running
            if pid_file.exists():
                try:
                    existing_pid = int(pid_file.read_text().strip())
                    # Check if process is still alive
                    os.kill(existing_pid, 0)
                    # Process exists - another daemon is running
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    os.close(fd)
                    return False, existing_pid
                except (ValueError, OSError, ProcessLookupError):
                    # PID file exists but process is dead - clean up
                    pass

            # Write our PID
            current_pid = os.getpid()
            pid_file.write_text(str(current_pid))

            # Release the lock (but keep file for tracking)
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

            return True, None

        except OSError:
            # Lock acquisition failed (another process has it)
            os.close(fd)
            # Read existing PID if available
            if pid_file.exists():
                try:
                    existing_pid = int(pid_file.read_text().strip())
                    return False, existing_pid
                except (ValueError, OSError):
                    pass
            return False, None

    except OSError:
        # Could not open lock file
        return False, None


def count_daemon_processes(pattern: str = "monitor_daemon", session: str = None) -> int:
    """Count running daemon processes matching the pattern.

    Uses pgrep to find processes matching the pattern.

    Args:
        pattern: Pattern to search for in process names/args
        session: If provided, only count daemons for this specific session

    Returns:
        Number of matching processes
    """
    import subprocess

    # Build pattern - if session provided, make it session-specific
    if session:
        search_pattern = f"{pattern} --session {session}"
    else:
        search_pattern = pattern

    try:
        # Use pgrep to find matching processes
        result = subprocess.run(
            ["pgrep", "-f", search_pattern],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        if result.returncode == 0 and result.stdout.strip():
            # Count non-empty lines (each line is a PID)
            pids = [p for p in result.stdout.strip().split('\n') if p]
            return len(pids)
        return 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return 0


def stop_process(pid_file: Path, timeout: float = 5.0) -> bool:
    """Stop a process by reading its PID file and sending SIGTERM.

    Args:
        pid_file: Path to the PID file
        timeout: Seconds to wait for process to terminate

    Returns:
        True if process was stopped, False if it wasn't running.
    """
    import time

    if not pid_file.exists():
        return False

    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGTERM)

        # Wait for process to terminate
        start = time.time()
        while time.time() - start < timeout:
            try:
                os.kill(pid, 0)
                time.sleep(0.1)
            except (OSError, ProcessLookupError):
                # Process terminated
                remove_pid_file(pid_file)
                return True

        # Process didn't terminate, try SIGKILL
        try:
            os.kill(pid, signal.SIGKILL)
            remove_pid_file(pid_file)
            return True
        except (OSError, ProcessLookupError):
            remove_pid_file(pid_file)
            return True

    except (ValueError, OSError, ProcessLookupError):
        # PID file invalid or process not running
        remove_pid_file(pid_file)
        return False
