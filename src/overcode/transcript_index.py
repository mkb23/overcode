"""Incremental reader for Claude Code transcript JSONL files (audit R1, R3, R8).

Every transcript-derived number overcode shows — the token, cost and
context columns (5 s sweep), the burn-rate window (1 Hz status bar) and the
daemon's 60 s stats sync — used to come from a full ``json.loads`` pass over
every owned transcript, from byte zero, on every tick. A transcript only
ever grows by appended lines, so the per-tick work can be proportional to
the bytes appended since the last tick instead of to the file's size.

One :class:`TranscriptIndex` per transcript path keeps:

* the byte offset of the first unconsumed byte, the stat signature of the
  last read (``st_ino``, ``st_mtime_ns``, ``st_size`` via
  :class:`~overcode.stat_gate.FileSignature`) and the last 64 committed
  bytes, so a truncated, replaced or rewritten file is detected and
  re-read from byte zero;
* one fold state per ``since`` filter that
  :func:`~overcode.history_reader.read_session_file_stats` was asked for
  (additive token totals, last-wins model/provider/effort/context, the previous
  user-prompt time and the work-time list) — exactly what
  ``_parse_session_lines`` accumulates, kept incrementally;
* for window queries, one entry per assistant message that carries a
  timestamp: its local-naive timestamp as a sortable integer key and the
  four usage counters, in ``array('q')`` (40 bytes per message) kept in
  key order, so :meth:`TranscriptIndex.window_usage` is a bisect plus a
  C-level sum over the messages inside the window and is exact at any
  window boundary. A message timestamped earlier than its predecessor
  (local-naive time runs backwards across an autumn DST change) is
  inserted at its sorted position; the window sum is order-independent.

A transcript's last line may be incomplete while Claude Code is still
writing it, and a file may legitimately end without a newline. Complete
lines are committed once; the uncommitted tail is parsed tentatively and
re-read whenever the file changes, so results match a full parse whether
or not the writer has finished the line.

Identity with the full-parse readers is exact for well-formed transcripts
and for the malformed lines Claude Code can actually produce (non-JSON,
empty, unknown types, missing or unparseable timestamps). The full parsers
raise on some shapes that never occur in practice — a JSON line whose top
level is not an object, a non-dict ``message``, a non-string timestamp —
where this reader skips the line instead; usage counters that are not
integers (the full parsers add up whatever is there, or fail part-way
through a message) are skipped whole. Lone ``\\r`` line separators, which
text-mode reading would honour, are not treated as line breaks here.

Thread safety: each index serialises its own reads and queries with a
lock (TUI workers run in a thread pool); :class:`TranscriptRegistry`
guards its map with a separate lock that is never held while an index lock
is taken. The registry is an LRU bounded both by index count and by the
total number of retained per-message entries.
"""

import bisect
import json
import os
import threading
from array import array
from collections import OrderedDict
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from .stat_gate import FileSignature, is_settled

# Bytes read per ``f.read`` while consuming appended data. Lines are
# committed per chunk, so a rebuild of a multi-GB file never holds more than
# one chunk (plus one line) in memory.
CHUNK_BYTES = 8 << 20

# Committed bytes remembered before the offset. Before reading appended
# data the same bytes are re-read and compared: a rewrite in place that
# changed them is detected and the file is re-parsed from byte zero.
TAIL_BYTES = 64

# Fold states kept per index. ``since`` is the overcode session's start
# time, one value per session; a transcript is queried under more only
# when several overcode sessions own the same id. A ``since`` past the cap
# evicts the least recently used one and costs one rebuild to recreate.
MAX_STATES_PER_INDEX = 4

# Registry bounds: distinct transcripts kept, and per-message entries kept
# across all of them (~40 bytes each; two million is ~80 MB, roughly 4 GB
# of transcript). Past either, the least recently queried index is dropped
# and costs one full parse if it is asked for again.
MAX_INDEXES = 4096
MAX_RECORDS = 2_000_000

_TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens")

_provider_from_message_id: Optional[Callable[[Optional[str]], Optional[str]]] = None


def _provider_detector() -> Callable[[Optional[str]], Optional[str]]:
    """``history_reader.provider_from_message_id``, imported lazily.

    ``history_reader`` imports this module, so the import cannot sit at
    module level; it is resolved once, on the first parse.
    """
    global _provider_from_message_id
    if _provider_from_message_id is None:
        from .history_reader import provider_from_message_id

        _provider_from_message_id = provider_from_message_id
    return _provider_from_message_id


def empty_stats() -> Tuple[dict, List[float]]:
    """What ``read_session_file_stats`` returns for a missing or unreadable file."""
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "current_context_tokens": 0,
        "model": None,
        "provider": None,
        "effort": None,
    }, []


def empty_window() -> dict:
    """What ``read_window_token_usage`` returns for a missing or unreadable file."""
    return {key: 0 for key in _TOKEN_KEYS}


def datetime_key(dt: datetime) -> int:
    """A naive datetime as an integer that orders exactly like the datetime.

    Naive datetimes compare field by field (year, month, ..., microsecond);
    this is the same tuple as a mixed-radix number, so ``a < b`` holds for
    the keys iff it holds for the datetimes. Kept as integers so the
    per-message timestamps fit in ``array('q')`` and bisect cheaply. Not
    epoch seconds on purpose: converting a local-naive time through the
    zone would be ambiguous across a DST fold, whereas the field order is
    what the full parsers actually compare.
    """
    return (
        ((((dt.year * 13 + dt.month) * 32 + dt.day) * 24 + dt.hour) * 60 + dt.minute) * 60
        + dt.second
    ) * 1_000_000 + dt.microsecond


def parse_message_time(ts_str) -> Optional[datetime]:
    """A transcript timestamp as a local-naive datetime, or None when unparseable.

    The same conversion the full parsers apply: ISO-8601 with ``Z`` mapped
    to ``+00:00``, shifted to the local zone, tzinfo dropped.
    """
    if not isinstance(ts_str, str):
        return None
    try:
        return (
            datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
        )
    except (ValueError, TypeError, OverflowError, OSError):
        return None


# A parsed line, as far as the readers care:
#   ("a", key_or_None, input, output, cache_creation, cache_read, model, provider,
#    effort)
#       an assistant message with a usage block; key is the timestamp key or
#       None when the timestamp is missing or unparseable
#   ("u", key, datetime)
#       a user prompt (not a tool result) with a parseable timestamp
# Anything else parses to None.
_Event = tuple


def parse_line(
    text: str, provider_of: Callable[[Optional[str]], Optional[str]]
) -> Optional[_Event]:
    """Reduce one transcript line to the event the readers fold, or None."""
    line = text.strip()
    if not line:
        return None
    try:
        data = json.loads(line)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    msg_type = data.get("type")
    if msg_type == "assistant":
        message = data.get("message", {})
        if not isinstance(message, dict):
            return None
        usage = message.get("usage", {})
        if not usage:
            return None
        if not isinstance(usage, dict):
            return None
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_creation = usage.get("cache_creation_input_tokens", 0)
        if not (
            isinstance(input_tokens, int)
            and isinstance(output_tokens, int)
            and isinstance(cache_read, int)
            and isinstance(cache_creation, int)
        ):
            return None
        key = None
        ts_str = data.get("timestamp")
        if ts_str and isinstance(ts_str, str):
            # parse_message_time, inlined: this runs once per message.
            try:
                key = datetime_key(
                    datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    .astimezone()
                    .replace(tzinfo=None)
                )
            except (ValueError, TypeError, OverflowError, OSError):
                pass
        msg_id = message.get("id")
        provider = provider_of(msg_id) if isinstance(msg_id, str) else None
        effort = data.get("effort")
        return (
            "a",
            key,
            input_tokens,
            output_tokens,
            cache_creation,
            cache_read,
            message.get("model"),
            provider,
            effort if isinstance(effort, str) else None,
        )
    if msg_type == "user":
        message = data.get("message", {})
        if not isinstance(message, dict):
            return None
        content = message.get("content", "")
        if isinstance(content, list) and content:
            first = content[0]
            if not isinstance(first, dict) or first.get("type") == "tool_result":
                return None
        ts_str = data.get("timestamp")
        if not ts_str or not isinstance(ts_str, str):
            return None
        try:
            dt = (
                datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                .astimezone()
                .replace(tzinfo=None)
            )
        except (ValueError, TypeError, OverflowError, OSError):
            return None
        return ("u", datetime_key(dt), dt)
    return None


class _StatsState:
    """``_parse_session_lines`` accumulators for one ``since`` filter."""

    __slots__ = ("since", "since_key", "totals", "work_times", "last_prompt")

    def __init__(self, since: Optional[datetime]):
        self.since = since
        self.since_key = None if since is None else datetime_key(since)
        self.reset()

    def reset(self) -> None:
        self.totals, self.work_times = empty_stats()
        self.last_prompt: Optional[datetime] = None


def fold_event(
    totals: dict,
    work_times: List[float],
    last_prompt: Optional[datetime],
    since_key: Optional[int],
    event: _Event,
) -> Optional[datetime]:
    """Apply one event to ``_parse_session_lines`` accumulators; returns the new last prompt.

    Mirrors the full parser line for line: an assistant message is skipped
    only when a ``since`` is set, its timestamp parsed, and it is earlier;
    a user prompt is skipped when earlier than ``since``; consecutive
    prompt times yield a work time when positive.
    """
    if event[0] == "a":
        key = event[1]
        if since_key is not None and key is not None and key < since_key:
            return last_prompt
        input_tokens, output_tokens, cache_creation, cache_read = event[2:6]
        totals["input_tokens"] += input_tokens
        totals["output_tokens"] += output_tokens
        totals["cache_creation_tokens"] += cache_creation
        totals["cache_read_tokens"] += cache_read
        context_size = input_tokens + cache_read + cache_creation
        if context_size > 0:
            totals["current_context_tokens"] = context_size
        if input_tokens + output_tokens + cache_creation + cache_read > 0:
            if event[6]:
                totals["model"] = event[6]
            if event[7]:
                totals["provider"] = event[7]
            if event[8]:
                totals["effort"] = event[8]
        return last_prompt
    key, prompt_time = event[1], event[2]
    if since_key is not None and key < since_key:
        return last_prompt
    if last_prompt is not None:
        duration = (prompt_time - last_prompt).total_seconds()
        if duration > 0:
            work_times.append(duration)
    return prompt_time


class TranscriptIndex:
    """Incremental state for one transcript file. See the module docstring."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        # Accounted per-message entries, maintained by the registry.
        self.accounted_records = 0
        self._states: "OrderedDict[Optional[datetime], _StatsState]" = OrderedDict()
        self._need_rebuild = False
        self._seen_sig: Optional[FileSignature] = None
        self._trusted = False
        self._missing = False
        self._reset_parse()

    # ── parse state ───────────────────────────────────────────────────

    def _reset_parse(self) -> None:
        self._offset = 0
        self._tail = b""
        self._pending: Optional[_Event] = None
        self._keys = array("q")
        self._values = [array("q") for _ in _TOKEN_KEYS]
        self._grand = [0, 0, 0, 0]
        for state in self._states.values():
            state.reset()

    @property
    def record_count(self) -> int:
        return len(self._keys)

    @property
    def offset(self) -> int:
        """Bytes committed so far (for tests and diagnostics)."""
        return self._offset

    def _state_for(self, since: Optional[datetime]) -> _StatsState:
        state = self._states.get(since)
        if state is not None:
            self._states.move_to_end(since)
            return state
        state = _StatsState(since)
        # A state created after bytes were consumed has missed them: the
        # next refresh re-parses from byte zero into every state.
        if self._offset > 0 or self._pending is not None:
            self._need_rebuild = True
        self._states[since] = state
        while len(self._states) > MAX_STATES_PER_INDEX:
            self._states.popitem(last=False)
        return state

    # ── reading ───────────────────────────────────────────────────────

    def _refresh(self) -> bool:
        """Bring the index up to date with the file; False if it could not be read."""
        try:
            st = os.stat(self.path)
        except OSError:
            self._reset_parse()
            self._seen_sig, self._trusted = None, False
            self._missing = True
            return True
        self._missing = False
        sig = FileSignature.of(st)
        if not self._need_rebuild and self._trusted and sig == self._seen_sig:
            return True

        rebuild = self._need_rebuild
        if not rebuild and self._seen_sig is not None and sig != self._seen_sig:
            # Replaced (new inode), truncated below what was consumed, or
            # modified without growing (an in-place rewrite; a transcript
            # only ever grows): start over.
            if (
                sig.ino != self._seen_sig.ino
                or st.st_size < self._offset
                or st.st_size == self._seen_sig.size
            ):
                rebuild = True
        if rebuild:
            self._reset_parse()

        try:
            with open(self.path, "rb") as f:
                if self._offset > 0:
                    f.seek(self._offset - len(self._tail))
                    if f.read(len(self._tail)) != self._tail:
                        self._reset_parse()
                        f.seek(0)
                self._consume(f)
        except OSError:
            return False
        self._seen_sig = sig
        self._trusted = is_settled(sig)
        self._need_rebuild = False
        return True

    def _consume(self, f) -> None:
        """Commit every complete line from the current position; parse the tail tentatively."""
        provider_of = _provider_detector()
        parts: List[bytes] = []
        while True:
            chunk = f.read(CHUNK_BYTES)
            if not chunk:
                break
            if b"\n" not in chunk:
                parts.append(chunk)
                continue
            if parts:
                parts.append(chunk)
                chunk = b"".join(parts)
                parts = []
            cut = chunk.rfind(b"\n") + 1
            if cut < len(chunk):
                parts.append(chunk[cut:])
                chunk = chunk[:cut]
            self._commit_block(chunk, provider_of)
        tail = b"".join(parts)
        self._pending = parse_line(tail.decode("utf-8", "replace"), provider_of) if tail else None

    def _commit_block(self, block: bytes, provider_of) -> None:
        """Fold a block of whole lines (ending in a newline) into the index."""
        states = list(self._states.values())
        keys = self._keys
        values = self._values
        grand = self._grand
        parse = parse_line
        for text in block.decode("utf-8", "replace").split("\n"):
            event = parse(text, provider_of)
            if event is None:
                continue
            if event[0] == "a":
                key = event[1]
                if key is not None:
                    if not keys or key >= keys[-1]:
                        keys.append(key)
                        for i in range(4):
                            values[i].append(event[2 + i])
                    else:
                        pos = bisect.bisect_right(keys, key)
                        keys.insert(pos, key)
                        for i in range(4):
                            values[i].insert(pos, event[2 + i])
                    for i in range(4):
                        grand[i] += event[2 + i]
            for state in states:
                state.last_prompt = fold_event(
                    state.totals, state.work_times, state.last_prompt, state.since_key, event
                )
        self._offset += len(block)
        if len(block) >= TAIL_BYTES:
            self._tail = block[-TAIL_BYTES:]
        else:
            self._tail = (self._tail + block)[-TAIL_BYTES:]

    # ── queries ───────────────────────────────────────────────────────

    def stats(self, since: Optional[datetime] = None) -> Tuple[dict, List[float]]:
        """What ``read_session_file_stats(path, since)`` returns, from the index."""
        with self._lock:
            state = self._state_for(since)
            if not self._refresh() or self._missing:
                return empty_stats()
            totals = dict(state.totals)
            work_times = list(state.work_times)
            if self._pending is not None:
                fold_event(totals, work_times, state.last_prompt, state.since_key, self._pending)
            return totals, work_times

    def window_usage(self, since: datetime) -> dict:
        """What ``read_window_token_usage(path, since)`` returns, from the index."""
        with self._lock:
            if not self._refresh() or self._missing:
                return empty_window()
            since_key = datetime_key(since)
            totals = self._sum_from(since_key)
            pending = self._pending
            if pending is not None and pending[0] == "a":
                key = pending[1]
                if key is not None and key >= since_key:
                    for i in range(4):
                        totals[i] += pending[2 + i]
            return dict(zip(_TOKEN_KEYS, totals))

    def _sum_from(self, since_key: int) -> List[int]:
        """Usage totals of the committed messages timestamped at or after ``since_key``."""
        idx = bisect.bisect_left(self._keys, since_key)
        if idx == 0:
            return list(self._grand)
        return [sum(v[idx:]) for v in self._values]


class TranscriptRegistry:
    """Path -> :class:`TranscriptIndex`, LRU-bounded by count and retained entries."""

    def __init__(self, max_indexes: int = MAX_INDEXES, max_records: int = MAX_RECORDS):
        self._lock = threading.Lock()
        self._indexes: "OrderedDict[str, TranscriptIndex]" = OrderedDict()
        self._records = 0
        self.max_indexes = max_indexes
        self.max_records = max_records

    def get(self, path) -> TranscriptIndex:
        """The index for ``path`` (created empty on first use); marks it most recently used."""
        key = os.fspath(path)
        with self._lock:
            index = self._indexes.get(key)
            if index is None:
                index = TranscriptIndex(key)
                self._indexes[key] = index
            else:
                self._indexes.move_to_end(key)
        return index

    def _account(self, index: TranscriptIndex) -> None:
        with self._lock:
            delta = index.record_count - index.accounted_records
            index.accounted_records += delta
            self._records += delta
            while len(self._indexes) > 1 and (
                len(self._indexes) > self.max_indexes or self._records > self.max_records
            ):
                _, victim = self._indexes.popitem(last=False)
                self._records -= victim.accounted_records
                victim.accounted_records = 0

    def stats(self, path, since: Optional[datetime] = None) -> Tuple[dict, List[float]]:
        index = self.get(path)
        result = index.stats(since)
        self._account(index)
        return result

    def window_usage(self, path, since: datetime) -> dict:
        index = self.get(path)
        result = index.window_usage(since)
        self._account(index)
        return result

    def __len__(self) -> int:
        return len(self._indexes)

    def __contains__(self, path) -> bool:
        return os.fspath(path) in self._indexes

    @property
    def record_count(self) -> int:
        return self._records

    def clear(self) -> None:
        with self._lock:
            self._indexes.clear()
            self._records = 0
