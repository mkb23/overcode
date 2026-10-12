"""One JSON state file, written so that a crash can never lose it.

``sessions.json`` and ``jobs.json`` are each one JSON object, rewritten
whole by every change, by several processes (the monitor daemon, every TUI,
the CLI, hook handlers). :class:`JsonStore` is how both are read and written.

Lock
    Writers serialise on ``<file>.lock``, a separate file that is never
    replaced, with ``flock(LOCK_EX | LOCK_NB)`` polled until a deadline
    (:data:`DEFAULT_LOCK_TIMEOUT`). A writer that cannot get it raises
    :class:`~overcode.exceptions.StateLockTimeout` instead of hanging behind
    a stuck process. Readers take no lock: the data file is only ever
    replaced, never rewritten in place, so an open handle always sees one
    complete version.

Write
    The new content goes to ``<file>.tmp.<pid>.<thread>`` in the same
    directory, is flushed and fsync'd, and ``os.replace``\\ s the file; then
    the directory is fsync'd so the rename itself is durable. A crash at
    any point leaves either the old file or the new one, never a partial
    one.

Backup
    ``<file>.bak`` is the version each write replaced: just before the
    rename the current file is hard-linked to ``.bak`` (no copy, no read),
    so the backup is always exactly one write behind the live file. Where
    hard links are unsupported it is copied instead.

Corruption
    A file that does not parse as a JSON object, or that is empty while the
    backup holds entries, is corrupt. (An empty file with no useful backup
    is what a pre-0.6 first run left behind, and reads as ``{}``.) Readers
    log a warning and get the backup's content, read-only: nothing is copied
    over the live file. A transaction starts from the backup's content too;
    if it writes, the corrupt file is first kept as
    ``<file>.corrupt-<timestamp>`` and then replaced by the new version
    through the normal atomic path. With no usable backup a transaction
    raises :class:`~overcode.exceptions.StateCorruptError` rather than write
    an empty file over the evidence.

On platforms without ``fcntl`` (Windows) there is no lock, as before; the
atomic replace and the backup still apply.
"""

import json
import logging
import os
import shutil
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional, Tuple

from .exceptions import StateCorruptError, StateLockTimeout, StateWriteError
from .stat_gate import FileSignature

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:  # Windows
    HAS_FCNTL = False

logger = logging.getLogger(__name__)

# Seconds a writer waits for the lock before giving up. A transaction holds
# it for one parse and one fsync'd write (milliseconds), so a wait this long
# means another process is stuck holding it.
DEFAULT_LOCK_TIMEOUT = 5.0

_POLL_START = 0.002
_POLL_MAX = 0.05


class Transaction:
    """What :meth:`JsonStore.transaction` yields: the parsed state, and
    whether it must be written back."""

    __slots__ = ("state", "dirty")

    def __init__(self, state: dict):
        self.state = state
        self.dirty = False


class JsonStore:
    """A JSON object kept in one file: lock-free reads, locked atomic writes."""

    def __init__(self, path: Path, *, lock_timeout: float = DEFAULT_LOCK_TIMEOUT):
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.backup_path = self.path.with_name(self.path.name + ".bak")
        self.lock_timeout = lock_timeout
        # Signature of the last corrupt file warned about, so a reader
        # polling a corrupt file logs once, not five times a second.
        self._warned: Optional[FileSignature] = None

    # ── lock ─────────────────────────────────────────────────────────────

    @contextmanager
    def lock(self) -> Iterator[None]:
        """Hold the exclusive writer lock, or raise ``StateLockTimeout``."""
        if not HAS_FCNTL:
            yield
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            deadline = time.monotonic() + self.lock_timeout
            delay = _POLL_START
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise StateLockTimeout(
                            f"Timed out after {self.lock_timeout:g}s waiting for "
                            f"{self.lock_path}: another overcode process is holding it"
                        ) from None
                    time.sleep(min(delay, remaining))
                    delay = min(delay * 2, _POLL_MAX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    # ── read ─────────────────────────────────────────────────────────────

    def read(self) -> Tuple[Optional[FileSignature], dict]:
        """``(signature, state)`` of the file now, without a lock.

        The signature is that of the bytes parsed; it is ``None`` whenever
        the state did not come from one clean read of the file (missing,
        unreadable, or corrupt and served from the backup), so a cache
        never remembers such a result.
        """
        sig, state, corrupt = self._read_live()
        if not corrupt:
            return sig, state
        backup = self._backup_state()
        if sig != self._warned:
            self._warned = sig
            logger.warning(
                "%s is corrupt (%s); using %s read-only (%d entries) until the next write",
                self.path, corrupt, self.backup_path.name, len(backup or {}),
            )
        return None, backup or {}

    def load(self) -> dict:
        """The state as a fresh dict the caller may modify."""
        return self.read()[1]

    def _read_live(self) -> Tuple[Optional[FileSignature], dict, Optional[str]]:
        """``(signature, state, why_corrupt)`` of the live file.

        ``why_corrupt`` is ``None`` for a usable file; a missing or
        unreadable file is usable and empty (with a ``None`` signature).
        """
        try:
            with open(self.path, "rb") as f:
                sig = FileSignature.of(os.fstat(f.fileno()))
                data = f.read()
        except FileNotFoundError:
            return None, {}, None
        except OSError as e:
            logger.warning("Could not read %s: %s", self.path, e)
            return None, {}, None
        if not data.strip():
            # Empty: a pre-0.6 first run created the file empty. It is only
            # a lost fleet if the backup says there was one.
            if self._backup_state():
                return sig, {}, "empty file"
            return sig, {}, None
        try:
            state = json.loads(data)
        except ValueError as e:
            return sig, {}, f"invalid JSON: {e}"
        if not isinstance(state, dict):
            return sig, {}, f"not a JSON object but {type(state).__name__}"
        return sig, state, None

    def _backup_state(self) -> Optional[dict]:
        """The backup's content, or ``None`` if it is missing or unusable."""
        try:
            with open(self.backup_path, "rb") as f:
                state = json.loads(f.read())
        except (OSError, ValueError):
            return None
        return state if isinstance(state, dict) else None

    # ── write ────────────────────────────────────────────────────────────

    @contextmanager
    def transaction(self) -> Iterator[Transaction]:
        """Read-modify-write under the lock, writing only if ``dirty``.

        Yields a :class:`Transaction` whose ``state`` is the file as it is
        under the lock. If the body sets ``dirty`` and returns normally the
        state is written back atomically; if it raises, nothing is written.
        """
        with self.lock():
            _, state, corrupt = self._read_live()
            if corrupt:
                backup = self._backup_state()
                if backup is None:
                    raise StateCorruptError(
                        f"{self.path} is corrupt ({corrupt}) and {self.backup_path.name} "
                        f"is missing or unreadable; not overwriting it. Move it aside "
                        f"to start empty, or repair it."
                    )
                logger.warning(
                    "%s is corrupt (%s); continuing from %s (%d entries)",
                    self.path, corrupt, self.backup_path.name, len(backup),
                )
                state = backup
            txn = Transaction(state)
            yield txn
            if txn.dirty:
                self._write(state, keep_aside="corrupt" if corrupt else None)

    def save(self, state: dict) -> None:
        """Replace the whole state, under the lock."""
        with self.lock():
            _, _, corrupt = self._read_live() if self.path.exists() else (None, None, None)
            self._write(state, keep_aside="corrupt" if corrupt else None)

    def restore_from_backup(self) -> bool:
        """Make the backup the live state, keeping the current file aside.

        Returns False (and changes nothing) if there is no usable backup.
        """
        with self.lock():
            backup = self._backup_state()
            if backup is None:
                return False
            self._write(backup, keep_aside="pre-restore")
            return True

    def _write(self, state: dict, keep_aside: Optional[str] = None) -> None:
        """Atomically replace the file with ``state``. Caller holds the lock.

        The current file becomes the backup, unless ``keep_aside`` names a
        reason to keep it as ``<file>.<reason>-<timestamp>`` instead (a
        corrupt file must not become the backup).
        """
        tmp = self.path.with_name(f"{self.path.name}.tmp.{os.getpid()}.{threading.get_ident()}")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            if self.path.exists():
                if keep_aside:
                    stamp = datetime.now().strftime("%Y%m%dT%H%M%S.%f")
                    aside = self.path.with_name(f"{self.path.name}.{keep_aside}-{stamp}")
                    _link_or_copy(self.path, aside)
                    logger.warning("Kept the replaced %s as %s", self.path.name, aside.name)
                else:
                    self._refresh_backup()
            _replace(tmp, self.path)
        except OSError as e:
            raise StateWriteError(f"Failed to save {self.path}: {e}") from e
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
        _fsync_dir(self.path.parent)

    def _refresh_backup(self) -> None:
        """Point ``.bak`` at the current file (best effort)."""
        tmp = self.backup_path.with_name(f"{self.backup_path.name}.tmp.{os.getpid()}.{threading.get_ident()}")
        try:
            _link_or_copy(self.path, tmp)
            os.replace(tmp, self.backup_path)
        except OSError as e:
            logger.warning("Could not refresh %s: %s", self.backup_path, e)
            try:
                tmp.unlink()
            except OSError:
                pass


def _link_or_copy(src: Path, dst: Path) -> None:
    """Make ``dst`` a hard link to ``src``, or a copy where links fail."""
    try:
        os.unlink(dst)
    except FileNotFoundError:
        pass
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _replace(src: Path, dst: Path) -> None:
    """``os.replace``; on Windows, retry while a reader has ``dst`` open."""
    if HAS_FCNTL:
        os.replace(src, dst)
        return
    deadline = time.monotonic() + 1.0
    while True:
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


def _fsync_dir(directory: Path) -> None:
    """Make a rename in ``directory`` durable (a no-op where unsupported)."""
    if not HAS_FCNTL:
        return
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
