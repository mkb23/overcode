"""Archived agents' own files, one gzip member per agent in a day bundle.

An agent's directory (``agents/<session id>/``: hook state, event log,
report, episode log) lives only while its record is in sessions.json. When
the record is archived the directory is packed into one gzip member
appended to its tmux session's bundle for the day,
``sessions/<tmux>/bundles/<YYYY-MM-DD>.agents.gz``, and removed. The
archive record carries a pointer to the member,
``{"day", "off", "len"}``, so one agent is read back by a seek, and
``zcat <day>.agents.gz`` streams the whole day.

The order is write-ahead (``SessionManager.commit_pending``): pack and
fsync the member; append the archive record (with the pointer) and fsync;
take the record out of sessions.json; remove the directory. A crash at any
point leaves a duplicate (an unreferenced member, an archived record still
live, a directory with no record), never a loss: the next pass, or the
orphan sweep, finishes the job.

Each member is one JSON line::

    {"sid", "name", "tmux", "parent", "archived_at",
     "files": {"hook_state.json": {...}, "report.json": {...},
               "hook_events.jsonl": "...", "episodes.jsonl": "..."}}

The episode log is packed from its tail (EPISODES_TAIL_BYTES), so a
member stays small whatever the agent did. Bundles are kept for ever
unless ``agent_history.retention_days`` / ``max_bytes`` are set
(:func:`prune`).
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import zlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from .paths import (
    AGENT_DIR_FILES,
    RESERVED_DIR_NAMES,
    agent_dir,
    agents_dir,
    bundle_path,
    sessions_root,
)

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:  # pragma: no cover - Windows
    HAS_FCNTL = False

# What a member keeps of each file. JSON files are parsed (an unreadable one
# is kept as text); logs are kept as text, cut to their last N bytes on a
# line boundary. The lock, the heartbeat stamp, temp files and the rotated
# episode log (nothing reads episodes yet) are left out.
JSON_FILES = ("hook_state.json", "report.json", "report.previous.json",
              AGENT_DIR_FILES["namer"])
EPISODES_TAIL_BYTES = 256 * 1024
HOOK_EVENTS_TAIL_BYTES = 128 * 1024
OTHER_TAIL_BYTES = 64 * 1024
TEXT_FILES = {
    AGENT_DIR_FILES["episodes"]: EPISODES_TAIL_BYTES,
    AGENT_DIR_FILES["hook_events"]: HOOK_EVENTS_TAIL_BYTES,
    AGENT_DIR_FILES["rename_notice"]: OTHER_TAIL_BYTES,
    # What the namer read (the naming lab reruns advice on archived agents)
    AGENT_DIR_FILES["brief"]: OTHER_TAIL_BYTES,
    AGENT_DIR_FILES["prompts"]: OTHER_TAIL_BYTES,
    AGENT_DIR_FILES["child_notice"]: OTHER_TAIL_BYTES,
}

# An agent directory is an orphan (its record gone) only once it has been
# still this long: a launch makes its record first and its directory just
# after, and a late hook may recreate a removed one.
ORPHAN_MIN_AGE_SECONDS = 600.0

BUNDLE_SUFFIX = ".agents.gz"

# Bytes fed to the decoder at a time when walking a day's members
_CHUNK = 64 * 1024

# Day files whose tail this process has checked, by path: (inode, size it
# left the file at). Before the first append of a run, and after anyone
# else appended, the tail is walked and a torn last member cut off.
_checked_tails: Dict[str, Tuple[int, int]] = {}


def _tail_text(path: Path, limit: int) -> Optional[str]:
    """The last ``limit`` bytes of a text file, from a line start; None if unreadable."""
    try:
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            start = max(0, size - limit)
            f.seek(start)
            data = f.read()
    except OSError:
        return None
    if start > 0:
        newline = data.find(b"\n")
        data = data[newline + 1:] if newline != -1 else b""
    return data.decode("utf-8", errors="replace")


def collect_files(directory: Path, *, keep_hook_events: bool = True) -> Dict[str, object]:
    """What a member keeps of an agent directory (see the module docstring)."""
    files: Dict[str, object] = {}
    for name in JSON_FILES:
        text = _tail_text(directory / name, OTHER_TAIL_BYTES)
        if text is None:
            continue
        try:
            files[name] = json.loads(text)
        except ValueError:
            files[name] = text
    for name, limit in TEXT_FILES.items():
        if name == AGENT_DIR_FILES["hook_events"] and not keep_hook_events:
            continue
        text = _tail_text(directory / name, limit)
        if text is not None:
            files[name] = text
    return files


def _members(data: bytes) -> Iterator[Tuple[int, bytes]]:
    """(end offset, decompressed bytes) of each complete gzip member, in order.

    Fed in chunks, so a day of many members is walked in one pass over its
    bytes; stops at the first torn or damaged member.
    """
    view = memoryview(data)
    pos = 0
    size = len(data)
    while pos < size:
        decoder = zlib.decompressobj(wbits=31)
        out = []
        cursor = pos
        try:
            while not decoder.eof and cursor < size:
                chunk = view[cursor:cursor + _CHUNK]
                cursor += len(chunk)
                out.append(decoder.decompress(chunk))
        except zlib.error:
            return
        if not decoder.eof:
            return  # torn: the member never finished
        end = cursor - len(decoder.unused_data)
        yield end, b"".join(out)
        pos = end


def _good_end(f) -> int:
    """Offset after the last complete gzip member of an open bundle."""
    f.seek(0)
    end = 0
    for end, _ in _members(f.read()):
        pass
    return end


def _append_member(path: Path, member: bytes) -> Tuple[int, int]:
    """Append one gzip member to a day file under its lock; fsynced. Returns (offset, length)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as f:
        if HAS_FCNTL:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            st = os.fstat(f.fileno())
            key = str(path)
            if _checked_tails.get(key) != (st.st_ino, st.st_size):
                good = _good_end(f)
                if good < st.st_size:
                    f.truncate(good)
            offset = os.fstat(f.fileno()).st_size
            f.write(member)
            f.flush()
            os.fsync(f.fileno())
            _checked_tails[key] = (st.st_ino, offset + len(member))
            return offset, len(member)
        finally:
            if HAS_FCNTL:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def pack_agent_dir(
    tmux_session: str,
    session_id: str,
    meta: Optional[dict] = None,
    *,
    now: Optional[datetime] = None,
    keep_hook_events: Optional[bool] = None,
) -> Optional[dict]:
    """Pack agent ``session_id``'s directory into today's bundle.

    Returns the pointer ``{"day", "off", "len"}``, or None when the agent
    has no directory (nothing to keep). ``meta`` (name, parent, ...) is
    stored with the files. The directory is left in place: removing it is
    the caller's last step, once the archive record is durable.
    """
    directory = agent_dir(tmux_session, session_id)
    if not directory.is_dir():
        return None
    if keep_hook_events is None:
        from .config import get_agent_history_config
        keep_hook_events = get_agent_history_config()["keep_hook_events"]
    now = now or datetime.now()
    record = {
        "sid": session_id,
        "tmux": tmux_session,
        **(meta or {}),
        "archived_at": now.isoformat(),
        "files": collect_files(directory, keep_hook_events=keep_hook_events),
    }
    line = (json.dumps(record, default=str) + "\n").encode("utf-8")
    member = gzip.compress(line, mtime=0)
    day = now.strftime("%Y-%m-%d")
    offset, length = _append_member(bundle_path(tmux_session, day), member)
    return {"day": day, "off": offset, "len": length}


def read_agent(tmux_session: str, pointer: dict) -> Optional[dict]:
    """The packed agent a pointer names, or None (expired day, bad pointer)."""
    try:
        path = bundle_path(tmux_session, str(pointer["day"]))
        with open(path, "rb") as f:
            f.seek(int(pointer["off"]))
            data = f.read(int(pointer["len"]))
        record = json.loads(gzip.decompress(data))
    except (KeyError, TypeError, ValueError, OSError, EOFError, zlib.error):
        return None
    return record if isinstance(record, dict) else None


def iter_day(tmux_session: str, day: str) -> Iterator[dict]:
    """Every agent packed on ``day``, in order; stops at a torn or damaged member."""
    try:
        data = bundle_path(tmux_session, day).read_bytes()
    except OSError:
        return
    for _, raw in _members(data):
        try:
            record = json.loads(raw)
        except ValueError:
            continue
        if isinstance(record, dict):
            yield record


def bundle_day_files() -> List[Tuple[str, str, Path]]:
    """(tmux session, day, path) of every bundle, oldest day first.

    One scandir of the sessions root, and one of each session's
    ``bundles/``: a day file per day kept.
    """
    found: List[Tuple[str, str, Path]] = []
    try:
        sessions = [e for e in os.scandir(sessions_root())
                    if e.is_dir() and e.name not in RESERVED_DIR_NAMES]
    except OSError:
        return found
    for entry in sessions:
        try:
            days = list(os.scandir(Path(entry.path) / "bundles"))
        except OSError:
            continue
        for day in days:
            if day.name.endswith(BUNDLE_SUFFIX):
                found.append((entry.name, day.name[: -len(BUNDLE_SUFFIX)], Path(day.path)))
    found.sort(key=lambda t: (t[1], t[0]))
    return found


def prune(
    *,
    retention_days: float = 0,
    max_bytes: int = 0,
    now: Optional[datetime] = None,
    limit: Optional[int] = None,
) -> List[Path]:
    """Delete bundle days past the retention, oldest first; returns what went.

    Off by default (both limits 0: bundles are kept for ever). Days older
    than ``retention_days`` go, across every tmux session; then, while the
    total is over ``max_bytes``, the oldest days. Today's day file is never
    deleted. ``limit`` bounds the unlinks (a CLI pass).
    """
    if not retention_days and not max_bytes:
        return []
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    days = bundle_day_files()
    removed: List[Path] = []

    def remove(path: Path) -> bool:
        if limit is not None and len(removed) >= limit:
            return False
        try:
            path.unlink()
        except OSError:
            return True
        removed.append(path)
        return True

    kept = []
    if retention_days:
        cutoff = (now - timedelta(days=retention_days)).strftime("%Y-%m-%d")
        for item in days:
            if item[1] < cutoff and item[1] != today:
                if not remove(item[2]):
                    return removed
            else:
                kept.append(item)
    else:
        kept = days
    if max_bytes:
        sizes = []
        for item in kept:
            try:
                sizes.append((item, item[2].stat().st_size))
            except OSError:
                pass
        total = sum(size for _, size in sizes)
        for item, size in sizes:
            if total <= max_bytes:
                break
            if item[1] == today:
                continue
            if not remove(item[2]):
                return removed
            total -= size
    return removed


def sweep_orphans(
    tmux_session: str,
    live_ids: Callable[[], set],
    *,
    archived: Callable[[set], set],
    record: Callable[[str, dict], None],
    attach: Optional[Callable[[str, dict], None]] = None,
    now: Optional[float] = None,
    min_age: float = ORPHAN_MIN_AGE_SECONDS,
    limit: int = 200,
) -> Dict[str, int]:
    """Clear agent directories whose record has gone; bounded, never raises.

    One scandir of ``agents/`` (the size of the live fleet once orphans
    are gone). A directory is an orphan when its session id is not in a
    sessions.json snapshot taken after the scandir (``live_ids``) and it
    has not changed for ``min_age`` seconds. The archive is asked once,
    for all of them, which are archived already (``archived(ids)`` returns
    those): they are removed, unless their record has no bundle (packing
    failed when they were archived: ``archived`` then returns a mapping
    ``{sid: has_bundle}``), when they are packed and ``attach(sid,
    pointer)`` gives the record its bundle first. Any other is packed
    first and ``record(sid, pointer)`` writes its archive record before
    the directory goes. At most ``limit`` orphans per call.

    Returns counts: ``removed`` (archived already), ``packed``, ``kept``
    (too recent, or packing failed).
    """
    import time

    counts = {"removed": 0, "packed": 0, "kept": 0}
    now = time.time() if now is None else now
    try:
        entries = [e for e in os.scandir(agents_dir(tmux_session)) if e.is_dir()]
    except OSError:
        return counts
    if not entries:
        return counts
    try:
        live = live_ids()
    except Exception:
        return counts  # can't tell what is live: touch nothing
    orphans = []
    for entry in entries:
        if len(orphans) >= limit:
            break
        if entry.name in live:
            continue
        try:
            if now - entry.stat().st_mtime < min_age:
                counts["kept"] += 1
                continue
        except OSError:
            continue
        orphans.append(entry)
    if not orphans:
        return counts
    try:
        already = archived({e.name for e in orphans})
    except Exception:
        return counts
    for entry in orphans:
        sid = entry.name
        try:
            if sid in already:
                unpacked = isinstance(already, dict) and not already[sid]
                if not unpacked or attach is None:
                    shutil.rmtree(entry.path, ignore_errors=True)
                    counts["removed"] += 1
                    continue
                # Archived, but its packing failed then: pack it now
                pointer = pack_agent_dir(tmux_session, sid, {"retry": True})
                if pointer is None:
                    continue
                attach(sid, pointer)
            else:
                pointer = pack_agent_dir(tmux_session, sid, {"orphan": True})
                if pointer is None:
                    continue
                record(sid, pointer)
        except Exception:
            counts["kept"] += 1
            continue
        shutil.rmtree(entry.path, ignore_errors=True)
        counts["packed"] += 1
    return counts
