"""Restating archived agents' spend after a transcript-reader fix, once, in batches.

Claude reader version 2 (``history_reader.CLAUDE_READER_VERSION``) counts
each message once across a conversation and all its subagents, workflow
agents included, and prices 1-hour cache writes at their own rate;
version 3 leaves out the history a forked conversation copied from an
earlier one (its parent's, for an ``overcode fork`` child). A live
agent's spend is restated by its next stats sync (``conversation_spend``:
an entry read by an older version is replaced). An archived agent is never
synced again, so this pass does it for the archive: every archived Claude
agent whose transcripts are still on disk is read again and its record's
spend replaced, the ledger getting ``correction`` rows for it
(``ledger.correction_rows``: every billing segment scaled, so billing
totals drop by the over-count). An agent whose transcripts are gone keeps
its figures; so does a conversation of it whose transcript is gone.

A record from before per-conversation spend (totals, no entries) first
folds what version 1 read of the transcripts still there
(``history_reader.legacy_session_reads``), so the restatement removes
exactly the over-count of those and the rest stays as it was.

The monitor daemon runs a bounded batch per housekeeping pass (like
``layout_migration``): the archive is walked month by month, oldest first,
from a cursor kept in ``ledger/.spend_rv<N>.json``; each month's restated
records are written back into that month's file (atomically, under the
archive lock; same records, same order, only their ``stats`` and segment
base change), after their ledger rows (write-ahead: a crash repeats a
correction, which changes nothing, rather than losing one). When the walk
ends, ``ledger/.spend_rv<N>`` marks it done and later calls cost a stat.
It waits for the ledger backfill (``Ledger.backfilled``), so every
archived agent already has its closing row. Idempotent: a restated record
reads at the new version and is skipped.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from . import conversation_spend as spend
from .clock import stamp
from .history_reader import CLAUDE_READER_VERSION
from .month_files import GZIP_SUFFIX, fsync_dir, read_gzip

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:  # pragma: no cover - Windows
    HAS_FCNTL = False

logger = logging.getLogger(__name__)

# Records needing a restatement handled per call, and a wall-time budget
# (seconds) after which a call stops early; the next call carries on.
BATCH = 100
TIME_BUDGET_SECONDS = 2.0
CLAUDE_BACKEND = "claude-code"


def marker_path(session_manager) -> Path:
    return session_manager.ledger.dir / f".spend_rv{CLAUDE_READER_VERSION}"


def cursor_path(session_manager) -> Path:
    return session_manager.ledger.dir / f".spend_rv{CLAUDE_READER_VERSION}.json"


def done(session_manager) -> bool:
    """Whether the archive has been restated at the current reader version."""
    return marker_path(session_manager).exists()


@contextmanager
def _exclusive(path: Path) -> Iterator[bool]:
    """A non-blocking exclusive lock on ``path``: yields whether it was taken."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not HAS_FCNTL:
        yield True
        return
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def needs_restating(record: dict) -> bool:
    """Whether an archive record is a Claude agent's spend read before the current version."""
    backend = record.get("backend") or CLAUDE_BACKEND
    if backend != CLAUDE_BACKEND:
        return False
    stats = record.get("stats")
    if not isinstance(stats, dict):
        return False
    totals = spend.recorded_totals(stats)
    if not any(totals[k] > 0 for k in spend.TOKEN_KEYS):
        return False
    entries = stats.get("conversations")
    if not isinstance(entries, dict) or not entries:
        return True
    return any(isinstance(entry, dict) and spend.count(entry.get("rv", 0)) < CLAUDE_READER_VERSION
               for cid, entry in entries.items() if cid != spend.LEGACY_KEY)


def restate(record: dict, *, ledger, now: datetime, transcripts=None,
            history_file=None, projects_path: Optional[Path] = None,
            fork_sources: Sequence[str] = (),
            ) -> Optional[Tuple[dict, List[dict]]]:
    """``(restated record, ledger rows)`` for one archive record; None if nothing changes.

    Reads the agent's transcripts at the current reader version (through
    ``transcripts``, a registry of its own so a pass does not evict the
    live agents' indexes); the record is unchanged when none is there.
    ``history_file`` and ``projects_path`` default to Claude's own;
    ``fork_sources`` are its parent's conversations (``overcode fork``).
    """
    from . import ledger as ledger_mod
    from .daemon_stats_sync import _owned_conversations, spend_pricing
    from . import history_reader
    from .history_reader import get_session_stats, legacy_session_reads
    from .session_archive import archived_session

    session = archived_session(record)
    if session is None:
        return None
    projects = projects_path if projects_path is not None else history_reader.CLAUDE_PROJECTS_PATH
    stats = get_session_stats(session, projects_path=projects, history_file=history_file,
                              transcripts=transcripts, fork_sources=fork_sources)
    reads = getattr(stats, "per_conversation", None)
    if not reads:
        return None
    prices, energy, default_model = spend_pricing(session, stats)
    old_stats = record["stats"]
    entries = old_stats.get("conversations") if isinstance(old_stats.get("conversations"), dict) else None
    new_entries = spend.merge_restating(
        entries, reads,
        earlier_reads=lambda: legacy_session_reads(session, projects_path=projects,
                                                   history_file=history_file),
        prices=prices, energy=energy, default_model=default_model,
        recorded=spend.recorded_totals(old_stats),
        owned_ids=_owned_conversations(session),
        seg=spend.count(record.get("billing_seg", 0)),
    )
    if not spend.replaced(entries or {}, new_entries) and new_entries == (entries or {}):
        return None
    new_record = dict(record)
    new_record["stats"] = {**old_stats, **spend.totals(new_entries),
                           "conversations": new_entries}
    rows: List[dict] = []
    if ledger_mod.spend_of(new_record) != ledger_mod.spend_of(record):
        seg = spend.count(record.get("billing_seg", 0))
        earlier = ledger.segment_rows([record["id"]]).get(record["id"], {}) if seg else {}
        rows, base = ledger_mod.correction_rows(record, new_record, closed=True,
                                                earlier=earlier, now=now)
        if seg:
            new_record.update(base)
    return new_record, rows


def _rewrite_month(session_manager, month: str, replacements: Dict[bytes, bytes]) -> int:
    """Replace whole archive lines of ``month`` (original bytes -> new bytes); returns how many.

    Under the archive lock, from the month's file as it is now (it may
    have been appended to, or gzipped, since it was read): every line
    equal to an original is replaced, everything else (and an unfinished
    last line) kept byte for byte. Written whole to a temp file, fsynced
    and renamed over the month.
    """
    if not replacements:
        return 0
    with session_manager._archive_lock():
        path = dict(session_manager.archive_months()).get(month)
        if path is None:
            return 0
        gzipped = path.name.endswith(GZIP_SUFFIX)
        data = read_gzip(path) if gzipped else path.read_bytes()
        cut = data.rfind(b"\n") + 1
        lines = data[:cut].split(b"\n")
        changed = 0
        for i, line in enumerate(lines):
            new = replacements.get(line)
            if new is not None:
                lines[i] = new
                changed += 1
        if not changed:
            return 0
        content = b"\n".join(lines) + data[cut:]
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        with open(tmp, "wb") as f:
            f.write(gzip.compress(content, mtime=0) if gzipped else content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        fsync_dir(path.parent)
    return changed


class _Parent:
    """An archive record as ``parent_conversations`` asks it: its parent's id."""

    def __init__(self, record: dict) -> None:
        parent = record.get("parent_session_id")
        self.parent_session_id = parent if isinstance(parent, str) else None


def _load_cursor(session_manager) -> Dict[str, Any]:
    try:
        cursor = json.loads(cursor_path(session_manager).read_text())
        if isinstance(cursor, dict) and isinstance(cursor.get("month"), str):
            return {"month": cursor["month"], "line": int(cursor.get("line", 0) or 0)}
    except (OSError, ValueError, TypeError):
        pass
    return {"month": "", "line": 0}


def _save_cursor(session_manager, month: str, line: int) -> None:
    path = cursor_path(session_manager)
    try:
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"month": month, "line": line}))
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("spend restatement cursor not saved: %s", e)


def recompute_archived_spend(session_manager, *, batch: int = BATCH,
                             time_budget: float = TIME_BUDGET_SECONDS,
                             now: Optional[datetime] = None, transcripts=None,
                             history_file=None,
                             projects_path: Optional[Path] = None) -> Dict[str, int]:
    """Restate one batch of the archive's spend (see the module docstring); never raises.

    Returns counts: ``examined`` records that needed it, ``restated``
    records rewritten, ``rows`` ledger rows written, ``done`` (1 once the
    marker is written).
    """
    counts = {"examined": 0, "restated": 0, "rows": 0, "done": 0}
    try:
        if done(session_manager):
            counts["done"] = 1
            return counts
        ledger = session_manager.ledger
        if not ledger.backfilled():
            return counts  # the closing rows a correction restates come first
        with _exclusive(ledger.dir / f".spend_rv{CLAUDE_READER_VERSION}.lock") as mine:
            if not mine:
                return counts  # another daemon is walking the archive
            return _walk(session_manager, ledger, counts, batch=batch,
                         time_budget=time_budget, now=now, transcripts=transcripts,
                         history_file=history_file, projects_path=projects_path)
    except Exception as e:  # housekeeping must never fail the tick
        logger.warning("archived spend not restated: %s", e)
        return counts


def _walk(session_manager, ledger, counts: Dict[str, int], *, batch: int, time_budget: float,
          now: Optional[datetime], transcripts, history_file,
          projects_path: Optional[Path]) -> Dict[str, int]:
    from .daemon_stats_sync import parent_conversations
    from .session_archive import _complete_lines
    from .transcript_index import TranscriptRegistry

    now = now or datetime.now()
    registry = transcripts if transcripts is not None else TranscriptRegistry(max_indexes=64)
    started = time.monotonic()
    cursor = _load_cursor(session_manager)
    parents: Dict[str, list] = {}  # archived parents' conversations, looked up once
    for month, path in session_manager.archive_months():
        if month < cursor["month"]:
            continue
        start = cursor["line"] if month == cursor["month"] else 0
        try:
            data = read_gzip(path) if path.name.endswith(GZIP_SUFFIX) else path.read_bytes()
        except OSError:
            continue
        lines = _complete_lines(data)
        replacements: Dict[bytes, bytes] = {}
        rows: List[dict] = []
        stopped_at: Optional[int] = None
        for index in range(start, len(lines)):
            if counts["examined"] >= batch or time.monotonic() - started > time_budget:
                stopped_at = index
                break
            line = lines[index]
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict) or not needs_restating(record):
                continue
            counts["examined"] += 1
            try:
                sources = parent_conversations(_Parent(record), session_manager, parents)
                result = restate(record, ledger=ledger, now=now, transcripts=registry,
                                 history_file=history_file, projects_path=projects_path,
                                 fork_sources=sources)
            except Exception as e:
                logger.warning("spend of archived %s not restated: %s", record.get("id"), e)
                continue
            if result is None:
                continue
            new_record, new_rows = result
            replacements[line] = json.dumps(new_record, default=str).encode("utf-8")
            rows.extend(new_rows)
        if rows:
            ledger.append(rows, now=now)  # write-ahead of the records
            counts["rows"] += len(rows)
        counts["restated"] += _rewrite_month(session_manager, month, replacements)
        if stopped_at is not None:
            _save_cursor(session_manager, month, stopped_at)
            return counts
        _save_cursor(session_manager, month, len(lines))
    try:
        marker = marker_path(session_manager)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(stamp(now) + "\n")
        counts["done"] = 1
    except OSError:
        pass
    return counts
