"""
The session archive: ``archive.jsonl``, one record per line, append-only.

archive.json was a dict rewritten whole on every archive: archiving one
session cost a parse and an indent=2 dump of every session ever archived,
and the daemon now archives on its own (terminated sessions after a
grace), so that cost would have run on a timer. A line per record makes
an archive O(record) and a read O(what was appended).

``SessionArchiveMixin`` is the part of ``SessionManager`` that writes and
reads it. It uses the manager's ``state_dir``, ``archive_file``,
``legacy_archive_file``, ``_archive_tail`` and ``_archive_cache``, all set
in ``SessionManager.__init__``. ``delete_session`` and ``commit_pending``
archive through ``_append_archive_records``.
"""

from __future__ import annotations

import functools
import json
import logging
import os
from contextlib import contextmanager
from typing import TYPE_CHECKING, Dict, Iterable, Iterator, List, Optional, Tuple

from .stat_gate import FileSignature

if TYPE_CHECKING:
    from .session_manager import Session

logger = logging.getLogger(__name__)

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    # Windows doesn't have fcntl
    HAS_FCNTL = False


@functools.cache
def _session_type() -> type:
    """``Session``, imported on first use (session_manager imports this module)."""
    from .session_manager import Session

    return Session


def _archive_record(line: bytes) -> Optional[dict]:
    """The record on one archive line, or None for a blank or damaged line."""
    if not line.strip():
        return None
    try:
        record = json.loads(line)
    except ValueError:
        return None
    if isinstance(record, dict) and isinstance(record.get("id"), str):
        return record
    return None


def _parse_archive_lines(data: bytes) -> Tuple[Dict[str, dict], int]:
    """Records by id from archive bytes, and the offset after the last complete line.

    Bytes after the final newline are an unfinished line (a crashed
    append) and are left for the next read. A repeated id keeps the last
    record at the position of the first, as ``json.load`` of the old dict
    did.
    """
    records: Dict[str, dict] = {}
    end = data.rfind(b"\n") + 1
    for line in data[:end].split(b"\n"):
        record = _archive_record(line)
        if record is not None:
            records[record["id"]] = record
    return records, end


def _archived_session(record: dict) -> Optional["Session"]:
    """The ``Session`` for an archive record, with ``end_time`` kept as an attribute."""
    try:
        data = dict(record)
        end_time = data.pop('end_time', None)  # not a Session field
        session = _session_type().from_dict(data)
        if session is None:
            return None
        session._end_time = end_time  # type: ignore
        return session
    except (KeyError, TypeError):
        return None


class SessionArchiveMixin:
    """Archive sessions and read them back (see module docstring)."""

    def _migrate_legacy_archive(self) -> None:
        """One-time, idempotent move of a legacy ``archive.json`` into the JSONL.

        One ``exists()`` per archive access. The legacy records are
        appended in their dict order under the JSONL's exclusive lock
        (two processes migrating at once serialise; the second finds the
        file gone) and the legacy file is renamed ``archive.json.migrated``;
        an unreadable one becomes ``archive.json.unreadable`` and is left
        for the user. Should an older overcode write archive.json again, it
        is migrated again — the reader keeps the last record per id, which
        is what the dict did.
        """
        if not self.legacy_archive_file.exists():
            return
        with self._archive_appender(migrate=False) as f:
            self._migrate_legacy_archive_into(f)

    def _migrate_legacy_archive_into(self, f) -> None:
        legacy = self.legacy_archive_file
        if not legacy.exists():  # re-checked under the lock
            return
        try:
            with open(legacy, 'r') as lf:
                records = json.load(lf)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("could not migrate %s: %s", legacy, e)
            try:
                legacy.rename(legacy.with_name(legacy.name + ".unreadable"))
            except OSError:
                pass
            return
        if isinstance(records, dict):
            self._write_archive_lines(f, [r for r in records.values() if isinstance(r, dict)])
        try:
            legacy.rename(legacy.with_name(legacy.name + ".migrated"))
        except OSError:
            pass

    @contextmanager
    def _archive_appender(self, migrate: bool = True):
        """The JSONL open for appending, under its exclusive lock."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with open(self.archive_file, 'a+b') as f:
            if HAS_FCNTL:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                if migrate:
                    self._migrate_legacy_archive_into(f)
                yield f
            finally:
                if HAS_FCNTL:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _write_archive_lines(f, records: Iterable[dict]) -> None:
        """Append ``records`` as lines and fsync once.

        A crash mid-append leaves a line without its newline; the reader
        skips that line and the next append terminates it first, so one
        record is lost rather than two merged.
        """
        f.seek(0, os.SEEK_END)
        if f.tell() > 0:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                f.write(b"\n")
        for record in records:
            f.write((json.dumps(record) + "\n").encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())

    def _append_archive_records(self, records: List[dict]) -> None:
        """Add ``records`` to the archive: one lock, one fsync for all of them."""
        if not records:
            return
        with self._archive_appender() as f:
            self._write_archive_lines(f, records)

    def _archive_session(self, session_data: dict):
        """Add a session to the archive."""
        self._append_archive_records([session_data])

    def _read_archive_bytes(self) -> Tuple[Optional[os.stat_result], bytes, int]:
        """``(fstat, bytes from offset, offset)`` of the archive under a shared lock.

        ``offset`` is the previous parse's end when the file is the same
        inode and has not shrunk — the archive only grows, so everything
        before it was parsed already — and 0 otherwise.
        """
        self._migrate_legacy_archive()
        if not self.archive_file.exists():
            return None, b"", 0
        prev_ino, prev_offset, _ = self._archive_tail
        try:
            with open(self.archive_file, 'rb') as f:
                if HAS_FCNTL:
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    st = os.fstat(f.fileno())
                    offset = prev_offset if (st.st_ino == prev_ino and st.st_size >= prev_offset) else 0
                    f.seek(offset)
                    return st, f.read(), offset
                finally:
                    if HAS_FCNTL:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except OSError:
            return None, b"", 0

    def _archive_snapshot(self) -> Dict[str, Session]:
        """The archived ``Session`` objects by entry id, extended when archive.jsonl grows."""
        return self._archive_cache.get(self.archive_file, self._parse_archive_file)

    def _parse_archive_file(self) -> Tuple[Optional[FileSignature], Dict[str, Session]]:
        """The stat-gated cache's reader: parses only the lines appended since last time.

        Objects for records parsed before are reused (the same sharing
        contract as the live snapshot); a shrunk or replaced file is parsed
        from the start.
        """
        st, data, offset = self._read_archive_bytes()
        if st is None:
            return None, {}
        _, _, prev_by_id = self._archive_tail
        by_id: Dict[str, Session] = dict(prev_by_id) if offset else {}
        records, end = _parse_archive_lines(data)
        for key, record in records.items():
            session = _archived_session(record)
            if session is not None:
                by_id[key] = session
        self._archive_tail = (st.st_ino, offset + end, by_id)
        return FileSignature.of(st), by_id

    def is_archived(self, session_id: str) -> bool:
        """Whether the archive holds a record of ``session_id``."""
        return session_id in self._archive_snapshot()

    def list_archived_sessions(self) -> List[Session]:
        """List all archived sessions (skips corrupted entries).

        Same sharing contract as ``list_sessions``: the objects are reused
        until ``archive.jsonl`` changes, and then for every record that was
        already there.
        """
        return list(self._archive_snapshot().values())

    def iter_archived_sessions(self) -> Iterator[Session]:
        """Archived sessions one at a time, in file order.

        For a caller that only needs a filter (``overcode history <name>``)
        and need not hold every record at once: the bytes are read under
        the shared lock, each record is built as the caller advances, and
        nothing is cached. Every line is yielded, so a re-migrated
        duplicate id appears twice where ``list_archived_sessions`` keeps
        the last record.
        """
        self._migrate_legacy_archive()
        if not self.archive_file.exists():
            return
        try:
            with open(self.archive_file, 'rb') as f:
                if HAS_FCNTL:
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    data = f.read()
                finally:
                    if HAS_FCNTL:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except OSError:
            return
        for line in data[: data.rfind(b"\n") + 1].split(b"\n"):
            record = _archive_record(line)
            if record is None:
                continue
            session = _archived_session(record)
            if session is not None:
                yield session

    def archived_sessions_since(
        self, offset: int = 0, inode: Optional[int] = None
    ) -> Tuple[List[Session], int, Optional[int], bool]:
        """Sessions appended to the archive after byte ``offset``, for incremental readers.

        The archive is append-only, so a reader that remembers where it
        stopped (and the file's inode) only ever parses new lines. Returns
        ``(sessions, new_offset, inode, reset)``; ``reset`` is True when the
        file was replaced or shrank since, in which case it was read from the
        start and the caller must drop what it had. Every line is returned,
        as in ``iter_archived_sessions``.
        """
        self._migrate_legacy_archive()
        try:
            with open(self.archive_file, 'rb') as f:
                if HAS_FCNTL:
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    st = os.fstat(f.fileno())
                    reset = inode != st.st_ino or offset > st.st_size
                    start = 0 if reset else offset
                    f.seek(start)
                    data = f.read()
                finally:
                    if HAS_FCNTL:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except OSError:
            return [], 0, None, inode is not None
        end = data.rfind(b"\n") + 1
        sessions = []
        for line in data[:end].split(b"\n"):
            record = _archive_record(line)
            if record is not None:
                session = _archived_session(record)
                if session is not None:
                    sessions.append(session)
        return sessions, start + end, st.st_ino, reset
