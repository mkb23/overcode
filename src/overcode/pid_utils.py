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
import subprocess
import threading
from pathlib import Path
from typing import List, Optional, Tuple


def _read_pid_file(pid_file: Path) -> Optional[int]:
    """Read a PID file and validate the process is alive.

    Args:
        pid_file: Path to the PID file

    Returns:
        The PID if file exists and process is alive, None otherwise.
    """
    if not pid_file.exists():
        return None

    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (ValueError, OSError, ProcessLookupError):
        return None


def is_process_running(pid_file: Path) -> bool:
    """Check if a process is running based on its PID file.

    Args:
        pid_file: Path to the PID file

    Returns:
        True if PID file exists and process is alive, False otherwise.
    """
    return _read_pid_file(pid_file) is not None


def get_process_pid(pid_file: Path) -> Optional[int]:
    """Get the PID from a PID file if the process is running.

    Args:
        pid_file: Path to the PID file

    Returns:
        The PID if process is running, None otherwise.
    """
    return _read_pid_file(pid_file)


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

    IMPORTANT: The lock is held for the daemon's entire lifetime. The lock
    file descriptor is stored in a module-level variable and released
    automatically when the process exits (normal or crash).

    Args:
        pid_file: Path to the PID file

    Returns:
        Tuple of (acquired, existing_pid):
        - (True, None) if lock was acquired and PID file written
        - (False, existing_pid) if another daemon is already running
    """
    global _held_lock_fd

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
            # (handles case where previous daemon crashed without releasing lock)
            if pid_file.exists():
                try:
                    existing_pid = int(pid_file.read_text().strip())
                    # Check if process is still alive
                    os.kill(existing_pid, 0)
                    # Process exists - another daemon is running
                    # This shouldn't happen if locking works, but check anyway
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    os.close(fd)
                    return False, existing_pid
                except (ValueError, OSError, ProcessLookupError):
                    # PID file exists but process is dead - clean up
                    pass

            # Write our PID
            current_pid = os.getpid()
            pid_file.write_text(str(current_pid))

            # IMPORTANT: Keep the lock held for the daemon's entire lifetime!
            # The OS will automatically release it when the process exits.
            # Store fd in module-level variable to prevent garbage collection.
            _held_lock_fd = fd

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


# Module-level variable to hold the lock file descriptor.
# This prevents garbage collection from closing the fd and releasing the lock.
_held_lock_fd: Optional[int] = None


def is_daemon_lock_held(pid_file: Path) -> bool:
    """Check if a daemon lock is currently held (i.e., daemon is actively running).

    Probes the fcntl lock file that the daemon holds for its entire lifetime.
    More reliable than pgrep: immune to zombie processes, PID reuse, and
    substring false matches.

    Args:
        pid_file: Path to the daemon's PID file (lock file is .lock suffix)

    Returns:
        True if the lock is held (daemon is running), False otherwise.
    """
    lock_file = pid_file.with_suffix('.lock')
    if not lock_file.exists():
        return False
    try:
        fd = os.open(str(lock_file), os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # We got the lock — no daemon holds it
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        except OSError:
            # Lock is held by another process = daemon is running
            return True
        finally:
            os.close(fd)
    except OSError:
        return False


def spawn_daemon(args: List[str]) -> Optional[int]:
    """Spawn a daemon process without leaving zombies.

    Uses start_new_session=True so the child survives parent exit,
    and a background reaper thread to prevent zombie accumulation.

    Args:
        args: Command arguments (e.g., [sys.executable, "-m", "overcode.monitor_daemon", ...])

    Returns:
        The daemon's PID, or None if spawn failed.
    """
    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        # Reaper thread prevents zombie when daemon exits while parent is alive
        threading.Thread(
            target=proc.wait, daemon=True, name=f"reap-{proc.pid}"
        ).start()
        return proc.pid
    except (OSError, subprocess.SubprocessError):
        return None


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
