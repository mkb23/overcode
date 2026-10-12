"""
PID file management utilities for Overcode.

Each overcode background process (monitor daemon, supervisor daemon, API
server, presence logger) takes an exclusive flock on ``<name>.lock`` beside
its ``<name>.pid`` and holds it for its whole life. The OS drops the lock the
moment the process dies, however it dies (SIGKILL, ``os._exit``, a crash), so
**the lock is the single source of truth** for "is it running":

- lock held: the process is running; the PID file (written by the holder
  after it took the lock) says which PID it has.
- lock free: nothing is running, whatever the PID file says. A PID file left
  behind by a dead process may name a PID the OS has since given to an
  unrelated process; it is never trusted or signalled.

Only when the lock cannot be consulted (no ``fcntl``, as on Windows, or no
lock file, i.e. a process from a version that took no lock) do these helpers
fall back to the PID file plus ``kill -0``.
"""

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]


def _lock_path(pid_file: Path) -> Path:
    return pid_file.with_suffix('.lock')


def _read_pid(pid_file: Path) -> Optional[int]:
    """The PID written in a PID file, or None if missing or unreadable."""
    try:
        return int(pid_file.read_text().strip())
    except (ValueError, OSError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return False


def _lock_state(pid_file: Path) -> Optional[bool]:
    """Whether the process lock for ``pid_file`` is held.

    True if held (the process is running), False if free (it is not),
    None if it cannot be told: no ``fcntl`` on this platform, or no lock
    file (the process, if any, took no lock).
    """
    if fcntl is None:
        return None
    lock_file = _lock_path(pid_file)
    try:
        fd = os.open(str(lock_file), os.O_RDONLY)
    except FileNotFoundError:
        return None
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        except OSError:
            return None
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def is_daemon_lock_held(pid_file: Path) -> bool:
    """True if the process lock for ``pid_file`` is held right now.

    Args:
        pid_file: Path to the process's PID file (the lock file is its .lock sibling)
    """
    return _lock_state(pid_file) is True


def get_process_pid(pid_file: Path) -> Optional[int]:
    """The PID of the running process, or None if it is not running.

    With a lock to consult, a free lock means not running, whatever PID the
    file names (it may have been reused by an unrelated process).
    """
    if _lock_state(pid_file) is False:
        return None
    pid = _read_pid(pid_file)
    if pid is None or not _pid_alive(pid):
        return None
    return pid


def is_process_running(pid_file: Path) -> bool:
    """True if the process behind ``pid_file`` is running.

    Decided by its lock when there is one; otherwise by the PID file and
    ``kill -0``.
    """
    state = _lock_state(pid_file)
    if state is not None:
        return state
    return get_process_pid(pid_file) is not None


def write_pid_file(pid_file: Path, pid: Optional[int] = None) -> None:
    """Write a PID to a PID file atomically (no reader sees it half written).

    Args:
        pid_file: Path to the PID file
        pid: PID to write (defaults to current process PID)
    """
    if pid is None:
        pid = os.getpid()
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = pid_file.with_name(f".{pid_file.name}.{os.getpid()}.tmp")
    tmp.write_text(str(pid))
    os.replace(tmp, pid_file)


def remove_pid_file(pid_file: Path, pid: Optional[int] = None) -> None:
    """Remove a PID file if it exists.

    Args:
        pid_file: Path to the PID file
        pid: if given, remove it only while it still names this PID, so a
            successor's fresh PID file is never deleted.
    """
    if pid is not None and _read_pid(pid_file) != pid:
        return
    try:
        pid_file.unlink()
    except FileNotFoundError:
        pass


# Lock fds held for the life of this process, by lock file path. Keeping
# them referenced keeps the locks; the OS releases them when the process exits.
_held_lock_fds: Dict[str, int] = {}

# How long acquire_daemon_lock keeps trying when the lock is busy. A
# lock probe (is_process_running from a TUI) holds it for microseconds,
# so a starting process must not mistake a probe for a running daemon.
_ACQUIRE_ATTEMPTS = 3
_ACQUIRE_RETRY_SECONDS = 0.05


def acquire_daemon_lock(pid_file: Path) -> Tuple[bool, Optional[int]]:
    """Take this process's lock and record its PID, unless one is running.

    The lock is held for the rest of the process's life. Holding it proves
    no other process for ``pid_file`` is alive, so the PID file is then
    overwritten whatever it says: a PID left by a dead process may belong
    to an unrelated process by now.

    Args:
        pid_file: Path to the PID file

    Returns:
        Tuple of (acquired, existing_pid):
        - (True, None) if the lock was taken and the PID file written
        - (False, existing_pid) if another process holds it (existing_pid
          is None when its PID file cannot be read)
    """
    pid_file.parent.mkdir(parents=True, exist_ok=True)

    if fcntl is None:
        # No locks on this platform: the PID file and kill -0 are all there is.
        existing = get_process_pid(pid_file)
        if existing is not None and existing != os.getpid():
            return False, existing
        write_pid_file(pid_file)
        return True, None

    lock_file = _lock_path(pid_file)
    try:
        fd = os.open(str(lock_file), os.O_WRONLY | os.O_CREAT, 0o644)
    except OSError:
        return False, None

    for attempt in range(_ACQUIRE_ATTEMPTS):
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if attempt + 1 == _ACQUIRE_ATTEMPTS:
                os.close(fd)
                return False, get_process_pid(pid_file)
            time.sleep(_ACQUIRE_RETRY_SECONDS)

    try:
        write_pid_file(pid_file)
    except OSError:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        return False, None
    _held_lock_fds[str(lock_file)] = fd
    return True, None


def release_daemon_lock(pid_file: Path) -> None:
    """Release a lock this process took with acquire_daemon_lock (tests, mostly)."""
    fd = _held_lock_fds.pop(str(_lock_path(pid_file)), None)
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass


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


def stop_process(pid_file: Path, timeout: float = 5.0) -> bool:
    """Stop the process behind a PID file: SIGTERM, then SIGKILL after ``timeout``.

    A PID is only signalled while the process's lock is held (or, where
    there is no lock to consult, while the PID is alive). A free lock means
    the PID file is stale and its PID may belong to an unrelated process:
    the file is removed and nothing is signalled.

    Args:
        pid_file: Path to the PID file
        timeout: Seconds to wait for process to terminate

    Returns:
        True if process was stopped, False if it wasn't running.
    """
    locked = _lock_state(pid_file)
    pid = _read_pid(pid_file)

    if locked is False or pid is None or not _pid_alive(pid):
        if locked is not True:
            remove_pid_file(pid_file)  # stale
        return False

    def running() -> bool:
        if locked is True and _lock_state(pid_file) is False:
            return False  # lock dropped: it has exited (or is exiting)
        return _pid_alive(pid)

    try:
        os.kill(pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        remove_pid_file(pid_file, pid)
        return False

    start = time.time()
    while time.time() - start < timeout:
        if not running():
            remove_pid_file(pid_file, pid)
            return True
        time.sleep(0.1)

    # It didn't terminate. Re-check before SIGKILL so a process that has
    # since let go of its lock (and a PID since reused) is never killed.
    if running():
        try:
            os.kill(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    remove_pid_file(pid_file, pid)
    return True
