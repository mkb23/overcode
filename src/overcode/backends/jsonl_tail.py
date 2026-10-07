"""Incremental folds over append-only JSONL logs (#524, #525).

codex's rollout file and grok's ``updates.jsonl``/``prompt_history.jsonl``
only ever grow. The stats readers used to re-parse them from byte 0 on every
call — once per agent per second for the burn rate — so the cost grew with
the log. ``fold_jsonl`` keeps, per (kind, path), the byte offset parsed so
far, the file's identity, and the reader's running accumulators, and on each
call parses only what was appended since. Every call still ``stat``s the file
and reflects it as it is now: no TTL, no polling interval.

Identity is ``(st_dev, st_ino, st_size, st_mtime_ns)``:

- unchanged: answer from the accumulators, no read at all;
- grown: read only the appended bytes. A line without its newline yet is not
  consumed (it is re-read next time) — unless it already parses as a JSON
  object, in which case it is folded in so the answer matches a from-scratch
  read now. If the bytes that later continue such a line are anything but
  whitespace, the whole line was really invalid (a from-scratch read skips
  it), so the fold resets;
- shrunk, a different inode, mtime moved back, or the same size with a new
  mtime: reset and re-parse from 0. Growth also checks the last
  ``_CHECK_BYTES`` bytes already parsed are still there, which catches a
  truncate-and-rewrite that left the file larger than before.

Lines are split on ``\\n``, decoded as UTF-8, stripped and ``json.loads``ed —
what iterating the file in text mode did, except that a line which is not
valid UTF-8 is skipped instead of aborting the read.

Each entry has its own lock (the TUI calls readers from a thread pool), and
the table is bounded: past ``_MAX_ENTRIES`` it drops entries idle longer
than ``_IDLE_SECONDS``, then the least recently used.
"""

import json
import os
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple, TypeVar

S = TypeVar("S")
R = TypeVar("R")

_CHUNK_BYTES = 1 << 20
_CHECK_BYTES = 64
_MAX_ENTRIES = 256
_IDLE_SECONDS = 3600.0

# Test seam: eviction without sleeping.
clock = time.monotonic

# Test seam: every file the folds open, so tests can count reads.
_open = open


class _Entry:
    __slots__ = ("lock", "ident", "offset", "check", "open_line", "state", "used")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.ident: Optional[Tuple[int, int, int, int]] = None
        self.offset = 0
        # The last bytes before `offset`, re-verified when the file grows.
        self.check = b""
        # True when the bytes just before `offset` are a JSON object whose
        # newline has not been written yet (see module docstring).
        self.open_line = False
        self.state: Any = None
        self.used = 0.0


_entries: Dict[Tuple[str, str], _Entry] = {}
_entries_lock = threading.Lock()


class _Reset(Exception):
    """Already-folded bytes are no longer what the file holds."""


def fold_jsonl(
    path: Any,
    kind: str,
    new_state: Callable[[], S],
    apply: Callable[[S, Any], None],
    view: Callable[[S], R],
) -> R:
    """``view`` of the state ``apply`` folds from every line of ``path``.

    ``new_state()`` makes an empty state, ``apply(state, entry)`` folds one
    parsed line into it (in file order), and ``view(state)`` returns the
    caller's answer; it runs under the entry's lock, so it must copy out
    anything mutable. A missing or unreadable file reads as ``new_state()``.
    """
    key = (kind, os.fspath(path))
    try:
        st = os.stat(key[1])
    except OSError:
        with _entries_lock:
            _entries.pop(key, None)
        return view(new_state())
    entry = _entry_for(key)
    with entry.lock:
        if entry.state is None:
            entry.state = new_state()
        try:
            _advance(entry, key[1], st, new_state, apply)
        except OSError:
            entry.ident = None
            entry.state = new_state()
            entry.offset, entry.check, entry.open_line = 0, b"", False
            return view(new_state())
        return view(entry.state)


def _entry_for(key: Tuple[str, str]) -> _Entry:
    now = clock()
    with _entries_lock:
        entry = _entries.get(key)
        if entry is None:
            entry = _entries[key] = _Entry()
            entry.used = now
            if len(_entries) > _MAX_ENTRIES:
                _evict(now)
        entry.used = now
        return entry


def _evict(now: float) -> None:
    for stale in [k for k, e in _entries.items() if now - e.used >= _IDLE_SECONDS]:
        del _entries[stale]
    if len(_entries) > _MAX_ENTRIES:
        by_use = sorted(_entries, key=lambda k: _entries[k].used)
        for old in by_use[: len(by_use) - _MAX_ENTRIES // 2]:
            del _entries[old]


def clear() -> None:
    """Forget every fold (tests, benchmarks)."""
    with _entries_lock:
        _entries.clear()


def _advance(entry: _Entry, path: str, st: os.stat_result, new_state, apply) -> None:
    ident = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
    old = entry.ident
    if old == ident:
        return
    if (
        old is None
        or old[:2] != ident[:2]
        or st.st_size < old[2]
        or st.st_mtime_ns < old[3]
        or (st.st_size == old[2] and st.st_mtime_ns != old[3])
    ):
        _reset(entry, new_state)
    try:
        complete = _read_from(entry, path, st.st_size, apply)
    except _Reset:
        _reset(entry, new_state)
        complete = _read_from(entry, path, st.st_size, apply)
    # A short read (the file shrank after the stat) leaves no identity, so
    # the next call re-stats and resets.
    entry.ident = ident if complete else None


def _reset(entry: _Entry, new_state) -> None:
    entry.state = new_state()
    entry.offset, entry.check, entry.open_line = 0, b"", False


def _read_from(entry: _Entry, path: str, size: int, apply) -> bool:
    """Fold bytes ``entry.offset .. size``; raise ``_Reset`` on a rewrite.

    False when the file ended before ``size``.
    """
    check = entry.check
    start = entry.offset - len(check)
    with _open(path, "rb") as handle:
        handle.seek(start)
        if check and handle.read(len(check)) != check:
            raise _Reset()
        remaining = size - entry.offset
        offset = entry.offset
        open_line = entry.open_line
        state = entry.state
        tail = check
        buf = b""
        complete = True
        while remaining > 0:
            chunk = handle.read(min(_CHUNK_BYTES, remaining))
            if not chunk:
                complete = False
                break
            remaining -= len(chunk)
            buf += chunk
            last_nl = buf.rfind(b"\n")
            if last_nl < 0:
                if open_line and buf.strip():
                    raise _Reset()
                continue
            lines = buf[:last_nl].split(b"\n")
            if open_line:
                if lines[0].strip():
                    raise _Reset()
                lines = lines[1:]
                open_line = False
            for raw in lines:
                parsed = _parse(raw)
                if parsed is not None:
                    apply(state, parsed)
            consumed = buf[: last_nl + 1]
            offset += len(consumed)
            tail = (tail + consumed)[-_CHECK_BYTES:]
            buf = buf[last_nl + 1:]

        # What is left has no newline yet. An open line's whitespace is part
        # of that line; otherwise fold a fragment that is already a whole
        # JSON object, so this answer matches a from-scratch read.
        if open_line:
            offset += len(buf)
            tail = (tail + buf)[-_CHECK_BYTES:]
        elif buf.strip():
            parsed = _parse(buf)
            if isinstance(parsed, dict):
                apply(state, parsed)
                offset += len(buf)
                tail = (tail + buf)[-_CHECK_BYTES:]
                open_line = True
    entry.offset = offset
    entry.check = tail
    entry.open_line = open_line
    return complete


def _parse(raw: bytes) -> Any:
    try:
        line = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    if not line:
        return None
    try:
        return json.loads(line)
    except (ValueError, TypeError):
        return None


__all__ = ["clear", "fold_jsonl"]
