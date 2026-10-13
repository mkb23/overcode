"""
The session archive: one slim record per archived agent, a JSONL file per month.

``~/.overcode/archive/<YYYY-MM>.jsonl`` holds the agents archived that month
(named by write time), one line each; a closed month is gzipped once at
rollover (``<YYYY-MM>.jsonl.gz``) and never changes again. The archive is
kept for ever unless ``archive.retention_months`` is set.

A record is slim (``slim_record``): the fields history, reports and the
journey read, the whole ``stats`` dict, and a pointer to the agent's packed
files in its day bundle (``agent_bundle``) — not the launch command, not a
sister's API key, not the standing instructions (only whether there were
any). That keeps a record about 1 KB and the archive free of secrets.

Readers stream: a month at a time, gzipped months through ``gzip.open``.
A lookup by name or id goes newest month first, and only parses a line
that contains the name's or id's bytes. The same id can appear twice (a
crash between the archive append and the live file's write; see
``SessionManager.commit_pending``): readers keep the newest.

``SessionArchiveMixin`` is the part of ``SessionManager`` that writes and
reads it. It uses the manager's ``state_dir``, ``archive_dir``,
``old_archive_file`` (the single ``archive.jsonl`` of 0.5/0.6 betas) and
``legacy_archive_file`` (``archive.json``); both older files are split
into months, slimmed, and deleted on first access.
"""

from __future__ import annotations

import functools
import gzip
import json
import logging
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Iterable, Iterator, List, Optional, Tuple

if TYPE_CHECKING:
    from .session_manager import Session

logger = logging.getLogger(__name__)

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    # Windows doesn't have fcntl
    HAS_FCNTL = False

# What an archive record keeps (besides ``stats``, kept whole, and
# ``bundle``, the pointer to the agent's packed files). Session.from_dict
# needs the identity fields; the rest is what history, the journey's
# counts, the ledger backfill and a report read.
ARCHIVE_KEYS = (
    "id", "name", "previous_names", "tmux_session", "tmux_window",
    "parent_session_id", "billing_code", "billing_seg", "billing_base_cost",
    "billing_base_tokens", "tags",
    "backend", "model", "provider", "skill_profile", "wrapper",
    "heartbeat_enabled", "cost_budget_usd", "human_annotation", "agent_value",
    "start_directory", "repo_name", "branch",
    "start_time", "end_time", "status",
    "report_status", "report_reason",
    "stats", "bundle", "orphan",
)

PLAIN_SUFFIX = ".jsonl"
GZIP_SUFFIX = ".jsonl.gz"

# One closed-month gzip pass at a time per process
_rollover_lock = threading.Lock()


def slim_record(record: dict) -> dict:
    """The archive's form of a session record (see ``ARCHIVE_KEYS``)."""
    slim = {key: record[key] for key in ARCHIVE_KEYS if key in record}
    slim["command"] = []  # Session.from_dict needs the key; never the command line
    if "has_standing_instructions" in record:
        slim["has_standing_instructions"] = bool(record["has_standing_instructions"])
    else:
        slim["has_standing_instructions"] = bool(record.get("standing_instructions"))
    return slim


def month_of(when: Optional[str], default: str) -> str:
    """The ``YYYY-MM`` of an ISO timestamp, else ``default``."""
    if isinstance(when, str) and len(when) >= 7:
        try:
            return datetime.fromisoformat(when).strftime("%Y-%m")
        except ValueError:
            pass
    return default


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


def _archived_session(record: dict) -> Optional["Session"]:
    """The ``Session`` for an archive record.

    ``end_time``, the bundle pointer and whether it had standing
    instructions are kept as attributes (``_end_time``, ``_bundle``,
    ``_has_standing_instructions``): they are not Session fields.
    """
    try:
        data = dict(record)
        end_time = data.pop('end_time', None)
        session = _session_type().from_dict(data)
        if session is None:
            return None
        session._end_time = end_time  # type: ignore[attr-defined]
        session._bundle = record.get("bundle")  # type: ignore[attr-defined]
        session._has_standing_instructions = bool(  # type: ignore[attr-defined]
            record.get("has_standing_instructions", record.get("standing_instructions")))
        return session
    except (KeyError, TypeError):
        return None


# The public name (the journey builds sessions from raw month records)
archived_session = _archived_session


def _complete_lines(data: bytes) -> List[bytes]:
    """The whole lines of ``data`` (a crashed append's unfinished last line is left out)."""
    return data[: data.rfind(b"\n") + 1].split(b"\n")


def parse_since(text: str, now: Optional[datetime] = None) -> datetime:
    """A ``--since`` value: ``30d``, ``2w``, ``12h``, ``2026-09`` or ``2026-09-15``."""
    now = now or datetime.now()
    text = text.strip()
    units = {"d": "days", "w": "weeks", "h": "hours"}
    if text[-1:].lower() in units and text[:-1].isdigit():
        return now - timedelta(**{units[text[-1].lower()]: int(text[:-1])})
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"not a date or a duration (30d, 2w, 2026-09): {text!r}")


class SessionArchiveMixin:
    """Archive sessions and read them back (see module docstring)."""

    # Gzip a closed month on a background thread (tests may turn it off)
    rollover_in_background = True

    # ── files and locks ─────────────────────────────────────────────────

    @property
    def archive_file(self) -> Path:
        """This month's archive file, where the next record goes."""
        return self.archive_dir / f"{datetime.now().strftime('%Y-%m')}{PLAIN_SUFFIX}"

    @contextmanager
    def _archive_lock(self, shared: bool = False):
        """The archive directory's lock: appends, rollover and migration take it."""
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        if not HAS_FCNTL:
            yield
            return
        fd = os.open(self.archive_dir / ".lock", os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def archive_months(self) -> List[Tuple[str, Path]]:
        """Each month's archive file, oldest first: the gzipped one when both exist."""
        months: Dict[str, Path] = {}
        try:
            entries = list(os.scandir(self.archive_dir))
        except OSError:
            return []
        for entry in entries:
            name = entry.name
            if name.endswith(GZIP_SUFFIX):
                months[name[: -len(GZIP_SUFFIX)]] = Path(entry.path)
            elif name.endswith(PLAIN_SUFFIX):
                months.setdefault(name[: -len(PLAIN_SUFFIX)], Path(entry.path))
        return sorted(months.items())

    # ── writing ─────────────────────────────────────────────────────────

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
            f.write((json.dumps(record, default=str) + "\n").encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())

    def _append_month_records(self, month: str, records: List[dict]) -> None:
        """Append slim ``records`` to ``month``'s plain file (caller holds the lock)."""
        path = self.archive_dir / f"{month}{PLAIN_SUFFIX}"
        with open(path, 'a+b') as f:
            self._write_archive_lines(f, records)

    def _append_archive_records(self, records: List[dict]) -> None:
        """Add ``records`` to this month's file, slimmed: one lock, one fsync."""
        if not records:
            return
        self._migrate_legacy_archive()
        month = datetime.now().strftime("%Y-%m")
        with self._archive_lock():
            self._append_month_records(month, [slim_record(r) for r in records])
        self._maybe_roll_over(month)

    def _archive_session(self, session_data: dict):
        """Add a session to the archive."""
        self._append_archive_records([session_data])

    # ── rollover and retention ──────────────────────────────────────────

    def _maybe_roll_over(self, current: str) -> None:
        """Gzip any closed month still plain: once, at the first archive of a month."""
        stale = [m for m, path in self.archive_months()
                 if m < current and path.name.endswith(PLAIN_SUFFIX)
                 and not path.name.endswith(GZIP_SUFFIX)]
        if not stale:
            return
        if self.rollover_in_background:
            threading.Thread(target=self.gzip_closed_months, args=(current,),
                             name="archive-rollover", daemon=True).start()
        else:
            self.gzip_closed_months(current)

    def gzip_closed_months(self, current: Optional[str] = None) -> List[str]:
        """Gzip every plain month before ``current`` (default: this month); returns them.

        Write ``.gz.tmp``, rename it into place, then remove the plain file,
        under the archive lock: a reader sees one or the other, whole.
        """
        current = current or datetime.now().strftime("%Y-%m")
        done: List[str] = []
        if not _rollover_lock.acquire(blocking=False):
            return done
        try:
            with self._archive_lock():
                for month, path in self.archive_months():
                    plain = self.archive_dir / f"{month}{PLAIN_SUFFIX}"
                    if month >= current or not plain.exists():
                        continue
                    target = self.archive_dir / f"{month}{GZIP_SUFFIX}"
                    try:
                        if not target.exists():
                            tmp = target.with_name(target.name + ".tmp")
                            with open(plain, "rb") as src, gzip.open(tmp, "wb") as dst:
                                while chunk := src.read(1 << 20):
                                    dst.write(chunk)
                            with open(tmp, "rb") as f:
                                os.fsync(f.fileno())
                            os.replace(tmp, target)
                        else:
                            # Records reached a closed month (a re-run migration):
                            # add them as one more gzip member, never drop them. A
                            # crash between this and the unlink repeats them, and
                            # readers keep one record per id.
                            with open(plain, "rb") as src, open(target, "ab") as raw:
                                with gzip.GzipFile(fileobj=raw, mode="wb") as dst:
                                    while chunk := src.read(1 << 20):
                                        dst.write(chunk)
                                raw.flush()
                                os.fsync(raw.fileno())
                        plain.unlink()
                        done.append(month)
                    except OSError as e:
                        logger.warning("could not gzip archive month %s: %s", month, e)
        finally:
            _rollover_lock.release()
        return done

    def prune_archive(self, retention_months: int, now: Optional[datetime] = None) -> List[str]:
        """Delete archive months older than ``retention_months`` (0: keep all); returns them."""
        if retention_months <= 0:
            return []
        now = now or datetime.now()
        year, month = now.year, now.month - retention_months
        while month <= 0:
            month += 12
            year -= 1
        cutoff = f"{year:04d}-{month:02d}"
        removed = []
        with self._archive_lock():
            for name, _ in self.archive_months():
                if name < cutoff:
                    for suffix in (PLAIN_SUFFIX, GZIP_SUFFIX):
                        try:
                            (self.archive_dir / f"{name}{suffix}").unlink()
                        except OSError:
                            pass
                    removed.append(name)
        return removed

    # ── migration from the single-file archives ─────────────────────────

    def _migrate_legacy_archive(self) -> None:
        """Split ``archive.json`` / ``archive.jsonl`` into slim months, then delete them.

        One-time and idempotent: two ``exists()`` per archive access once
        done. Records go to the month of their ``end_time``, slimmed: the
        old files held every launch command and any sister's API key, so
        they are deleted (and ``archive.json.migrated``, an earlier
        migration's copy), not kept. An unreadable ``archive.json`` is set
        aside as ``archive.json.unreadable`` for the user.
        """
        old_jsonl, legacy = self.old_archive_file, self.legacy_archive_file
        if not old_jsonl.exists() and not legacy.exists():
            return
        with self._archive_lock():
            by_month: Dict[str, List[dict]] = {}
            default = datetime.now().strftime("%Y-%m")
            try:
                for record in self._read_old_archives():
                    month = month_of(record.get("end_time") or record.get("start_time"), default)
                    by_month.setdefault(month, []).append(slim_record(record))
            except OSError as e:
                logger.warning("could not read %s, leaving it for next time: %s", old_jsonl, e)
                return
            for month in sorted(by_month):
                self._append_month_records(month, by_month[month])
            for path in (old_jsonl, legacy, legacy.with_name(legacy.name + ".migrated")):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as e:
                    logger.warning("could not remove %s: %s", path, e)
        self.gzip_closed_months()

    def _read_old_archives(self) -> Iterator[dict]:
        """The records of ``archive.json`` (dict order) then ``archive.jsonl`` (file order)."""
        legacy = self.legacy_archive_file
        if legacy.exists():
            try:
                with open(legacy, 'r') as lf:
                    records = json.load(lf)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("could not migrate %s: %s", legacy, e)
                try:
                    legacy.rename(legacy.with_name(legacy.name + ".unreadable"))
                except OSError:
                    pass
                records = {}
            if isinstance(records, dict):
                yield from (r for r in records.values()
                            if isinstance(r, dict) and isinstance(r.get("id"), str))
        if not self.old_archive_file.exists():
            return
        # An unreadable archive.jsonl raises: the migration must not delete it
        data = self.old_archive_file.read_bytes()
        for line in _complete_lines(data):
            record = _archive_record(line)
            if record is not None:
                yield record

    # ── reading ─────────────────────────────────────────────────────────

    @staticmethod
    def _month_lines(path: Path) -> List[bytes]:
        """A month file's whole lines (gzipped or plain); [] if it cannot be read."""
        try:
            if path.name.endswith(GZIP_SUFFIX):
                with gzip.open(path, "rb") as f:
                    data = f.read()
            else:
                data = path.read_bytes()
        except (OSError, EOFError, gzip.BadGzipFile):
            return []
        return _complete_lines(data)

    def _months_since(self, since: Optional[datetime]) -> List[Tuple[str, Path]]:
        self._migrate_legacy_archive()
        months = self.archive_months()
        if since is None:
            return months
        first = since.strftime("%Y-%m")
        return [(m, p) for m, p in months if m >= first]

    def iter_archived_records(
        self, since: Optional[datetime] = None, *, newest_first: bool = False,
        needle: Optional[bytes] = None,
    ) -> Iterator[dict]:
        """Raw archive records, a month at a time.

        Oldest first, every line (a repeated id appears twice); or newest
        first, each id once (its newest record). ``since`` skips the months
        before it (records are filed by the month they were archived in).
        ``needle`` parses only lines containing those bytes.
        """
        months = self._months_since(since)
        seen: set = set()
        for _, path in (reversed(months) if newest_first else months):
            lines = self._month_lines(path)
            for line in (reversed(lines) if newest_first else lines):
                if needle is not None and needle not in line:
                    continue
                record = _archive_record(line)
                if record is None:
                    continue
                if newest_first:
                    if record["id"] in seen:
                        continue
                    seen.add(record["id"])
                yield record

    def iter_archived_sessions(
        self, since: Optional[datetime] = None, *, newest_first: bool = False,
    ) -> Iterator[Session]:
        """Archived sessions one at a time (see ``iter_archived_records``)."""
        for record in self.iter_archived_records(since, newest_first=newest_first):
            session = _archived_session(record)
            if session is not None:
                yield session

    def list_archived_sessions(self, since: Optional[datetime] = None) -> List[Session]:
        """Every archived session (since ``since``), one per id (its newest record).

        In the order each id was first archived. Builds the whole list:
        callers that only filter should use ``iter_archived_sessions``.
        """
        by_id: Dict[str, Session] = {}
        for record in self.iter_archived_records(since):
            session = _archived_session(record)
            if session is not None:
                by_id[record["id"]] = session
        return list(by_id.values())

    def find_archived(self, name_or_id: str) -> Optional[Session]:
        """The newest archived agent with this id, name or former name; None if none.

        Newest month first, and a line is parsed only if it contains the
        bytes being looked for, so a lookup costs a scan, not a parse, of
        the months it reads.
        """
        if not name_or_id:
            return None
        needle = json.dumps(name_or_id)[1:-1].encode("utf-8")
        for record in self.iter_archived_records(newest_first=True, needle=needle):
            if (record["id"] == name_or_id or record.get("name") == name_or_id
                    or name_or_id in (record.get("previous_names") or [])):
                session = _archived_session(record)
                if session is not None:
                    return session
        return None

    def is_archived(self, session_id: str) -> bool:
        """Whether the archive holds a record of ``session_id`` (newest months first)."""
        return session_id in self.archived_ids({session_id})

    def archived_ids(self, session_ids: Iterable[str]) -> set:
        """Those of ``session_ids`` the archive holds: one pass, newest month first.

        Stops as soon as all are found. A record's id is its first key
        (``slim_record``), so a line is matched without parsing it.
        """
        wanted = set(session_ids)
        found: set = set()
        if not wanted:
            return found
        prefix = b'{"id": "'
        for _, path in reversed(self._months_since(None)):
            for line in self._month_lines(path):
                if line.startswith(prefix):
                    end = line.find(b'"', len(prefix))
                    sid = line[len(prefix):end].decode("utf-8", "replace")
                else:
                    record = _archive_record(line)
                    sid = record["id"] if record else None
                if sid in wanted:
                    found.add(sid)
                    if found == wanted:
                        return found
        return found

    def read_archive_month(self, path: Path, *, offset: int = 0) -> Tuple[List[dict], int, int]:
        """Records of one month file from byte ``offset`` (plain files only; 0 for a gzip).

        Returns ``(records, end offset of the last whole line, inode)``,
        for incremental readers (the journey's counts).
        """
        try:
            st = path.stat()
            if path.name.endswith(GZIP_SUFFIX):
                lines, end, offset = self._month_lines(path), 0, 0
            else:
                with open(path, "rb") as f:
                    f.seek(offset)
                    data = f.read()
                cut = data.rfind(b"\n") + 1
                lines, end = data[:cut].split(b"\n"), offset + cut
        except OSError:
            return [], offset, 0
        records = [r for r in (_archive_record(line) for line in lines) if r is not None]
        return records, end, st.st_ino
