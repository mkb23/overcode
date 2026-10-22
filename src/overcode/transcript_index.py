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
  timestamp: its local-naive timestamp as a sortable integer key, the
  five usage counters and the message id's hash, in ``array('q')`` (56
  bytes per message) kept in
  key order, so :meth:`TranscriptIndex.window_usage` is a bisect plus a
  C-level sum over the messages inside the window and is exact at any
  window boundary. A message timestamped earlier than its predecessor
  (local-naive time runs backwards across an autumn DST change) is
  inserted at its sorted position; the window sum is order-independent.

Claude Code writes one line per content block of an assistant message,
and every line repeats the whole message's ``usage`` (a later line may
carry a larger ``output_tokens`` while the message streams). A message is
counted once: by ``message.id``, the last line's usage, dated by the first
line's timestamp (the ``since`` filter and the window both use that one).
A line is deduplicated only when it has a message id and a parseable
timestamp; any other line counts on its own, as before. The full parsers
keep every id they meet; the index remembers the last ``RECENT_IDS``
distinct ids, which covers the content blocks of one message (they are
written together). It also keeps the first ``HEAD_IDS`` messages of the
file and a 64-bit hash per message, so a reader of several files (a
conversation and its subagents, ``history_reader``) can find a message one
file copied from another (a fork starts with a copy of its parent's last
message, with the same id and timestamp) and count it once: the original
is looked up by bisecting to the copy's time (``TranscriptIndex.holds``).

``cache_creation`` splits a message's cache writes by TTL; the 1-hour
writes (``ephemeral_1h_input_tokens``, billed at twice the input price)
are kept beside the total as ``cache_creation_1h_tokens``.

A request whose prompt (input + cache read + cache write tokens) is longer
than its model's long-context threshold (``price_table``: Haiku 5.5 over
100K, Sonnet 4 over 200K) is billed whole at the long-context rates, so
its usage is also added to the ``long_*`` counters: a breakdown of the
totals, never added to them. The window sums (the burn rate) do not keep
it.

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
import re
import threading
from array import array
from collections import OrderedDict
from itertools import compress, count
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from .price_table import long_context_threshold
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
# across all of them (~56 bytes each: a key, five counters and an id hash;
# 1.4 million is ~80 MB, roughly 3 GB of transcript). Past either, the
# least recently queried index is dropped and costs one full parse if it
# is asked for again. Each index also keeps at most RECENT_IDS ids and
# HEAD_IDS head messages.
MAX_INDEXES = 4096
MAX_RECORDS = 1_400_000

_TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_tokens",
    "cache_read_tokens",
    "cache_creation_1h_tokens",
)
_N = len(_TOKEN_KEYS)
# The part of each counter spent by long-context requests (module docstring)
LONG_KEYS = tuple("long_" + key for key in _TOKEN_KEYS)

# Distinct message ids remembered per file to fold the later lines of a
# message into its first (the content blocks of one message are written
# together, so a few suffice; a line whose id has dropped out counts as a
# new message).
RECENT_IDS = 32
# Messages at the start of a file kept for cross-file dedup: a file that
# copies another's messages (a fork) copies them first.
HEAD_IDS = 8
# How long before a copied line its original message may have started: a
# copy keeps the original line's timestamp, and a message's lines are
# written within its own streaming time (``TranscriptIndex.holds``).
COPY_WINDOW_SECONDS = 3600

# Generations are unique across indexes, so one evicted and recreated for
# the same path never matches a position remembered for the old one
_GENERATIONS = count(1)
# Records checked per round when walking a fork's copied prefix
FORK_STEP = 64

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
        "cache_creation_1h_tokens": 0,
        **dict.fromkeys(LONG_KEYS, 0),
        "current_context_tokens": 0,
        "model": None,
        "provider": None,
        "effort": None,
        "pr_number": None,
        "custom_title": None,
        **empty_selection(),
        "model_at": None,
        "effort_at": None,
    }, []


def empty_selection() -> dict:
    """No /model or /effort since the last reply (see ``fold_setting``)."""
    return {"selected_model": None, "selected_effort": None, "effort_cleared": False}


# What Claude Code writes when /model or /effort answers in the CLI itself
# (a ``<local-command-stdout>`` user record, the moment the setting takes):
#   Set model to `Sonnet 5.5` and saved as your default for new sessions
#   Set effort level to low (this session only): Quick, ...
#   Effort 'max' exceeds the cap for X ...; set to 'xhigh' instead ...
#   Effort level set to auto
# The model is named for people; the families below map back to the id its
# replies carry (claude-sonnet-5-5). Any other name is left to the next reply.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_STDOUT = re.compile(r"^<local-command-stdout>(.*?)</local-command-stdout>\s*$", re.S)
_SET_MODEL = re.compile(r"^Set model to `([^`]+)`")
_MODEL_NAME = re.compile(
    r"^(Opus|Sonnet|Haiku)\s+(\d+(?:\.\d+)*)(\s*\(1M context\))?(?:\s*\(default\))?$", re.I)
_SET_EFFORT = re.compile(r"^Set effort level to ([a-z]+)\b")
_CAPPED_EFFORT = re.compile(r"^Effort '[^']*' exceeds the cap .*?; set to '([a-z]+)' instead", re.S)
_AUTO_EFFORT = "Effort level set to auto"


def local_command_setting(content) -> Optional[Tuple[str, Optional[str]]]:
    """``("model", id)`` or ``("effort", level)`` a slash command's confirmation set, else None.

    ``("effort", None)``: effort back to the model's default, which only
    the next reply names.
    """
    if not isinstance(content, str) or not content.startswith("<local-command-stdout>"):
        return None
    match = _STDOUT.match(content)
    if not match:
        return None
    text = _ANSI.sub("", match.group(1)).strip()
    model = _SET_MODEL.match(text)
    if model:
        name = _MODEL_NAME.match(model.group(1).strip())
        if not name:
            return None
        family, version, one_m = name.groups()
        return "model", (f"claude-{family.lower()}-{version.replace('.', '-')}"
                         + ("[1m]" if one_m else ""))
    effort = _SET_EFFORT.match(text) or _CAPPED_EFFORT.match(text)
    if effort:
        return "effort", effort.group(1)
    if text.startswith(_AUTO_EFFORT):
        return "effort", None
    return None


def fold_setting(totals: dict, key: Optional[int], setting: Tuple[str, Optional[str]]) -> None:
    """Apply a /model or /effort confirmation (``local_command_setting``).

    It holds until the next reply, whose own model and effort replace it
    (``fold_assistant``). A switch to another model leaves the effort
    unknown: Claude picks it per model and says which only in that reply.
    ``model_at`` and ``effort_at`` are when each was last stated.
    """
    kind, value = setting
    if kind == "model":
        current = totals["selected_model"] or totals["model"]
        totals["selected_model"] = value
        totals["model_at"] = key
        if value != current:
            totals["selected_effort"] = None
            totals["effort_cleared"] = True
            totals["effort_at"] = key
    else:
        totals["selected_effort"] = value
        totals["effort_cleared"] = value is None
        totals["effort_at"] = key


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


def key_datetime(key: int) -> datetime:
    """The naive datetime a ``datetime_key`` was made from."""
    rest, microsecond = divmod(key, 1_000_000)
    rest, second = divmod(rest, 60)
    rest, minute = divmod(rest, 60)
    rest, hour = divmod(rest, 24)
    rest, day = divmod(rest, 32)
    year, month = divmod(rest, 13)
    return datetime(year, month, day, hour, minute, second, microsecond)


def key_before(key: int, seconds: float) -> int:
    """The key ``seconds`` earlier than ``key`` (keys are not linear across months)."""
    try:
        return datetime_key(key_datetime(key) - timedelta(seconds=seconds))
    except (ValueError, OverflowError):
        return 0


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
#   ("a", key_or_None, input, output, cache_creation, cache_read, cache_creation_1h,
#    model, provider, effort, message_id_or_None, long)
#       an assistant message line with a usage block; key is the timestamp
#       key or None when the timestamp is missing or unparseable; long
#       whether its prompt is past the model's long-context threshold
#   ("u", key, datetime)
#       a user prompt (not a tool result) with a parseable timestamp
#   ("s", key, datetime, setting)
#       the same, when it is a /model or /effort confirmation
#       (``local_command_setting``)
#   ("p", pr_number)
#       a pr-link record: the PR Claude Code linked the session to (#489)
#   ("t", title)
#       a custom-title record: the session's name (``-n``, ``/rename``; #542)
# Anything else parses to None.
_Event = tuple


def cache_write_1h(usage: dict, cache_creation: int) -> int:
    """The 1-hour TTL part of a message's cache writes (0 when not split).

    Claude Code reports ``usage.cache_creation = {"ephemeral_1h_input_tokens",
    "ephemeral_5m_input_tokens"}`` beside the total; without it every write
    is taken at the 5-minute rate. Never more than the total.
    """
    split = usage.get("cache_creation")
    if not isinstance(split, dict):
        return 0
    one_hour = split.get("ephemeral_1h_input_tokens", 0)
    if not isinstance(one_hour, int) or isinstance(one_hour, bool) or one_hour <= 0:
        return 0
    return min(one_hour, max(0, cache_creation))


def is_long_request(model, input_tokens: int, cache_creation: int, cache_read: int) -> bool:
    """Whether a request is billed at its model's long-context rates: its
    prompt (input + cache write + cache read tokens) is past the model's
    threshold (``price_table.long_context_threshold``)."""
    threshold = long_context_threshold(model) if isinstance(model, str) else None
    return threshold is not None and input_tokens + cache_creation + cache_read > threshold


def message_hash(msg_id: str) -> int:
    """A message id as a non-zero 64-bit integer (0 marks "no id")."""
    h = hash(msg_id)
    return h if h != 0 else 1


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
        if not isinstance(msg_id, str) or not msg_id:
            msg_id = None
        provider = provider_of(msg_id) if msg_id is not None else None
        effort = data.get("effort")
        model = message.get("model")
        return (
            "a",
            key,
            input_tokens,
            output_tokens,
            cache_creation,
            cache_read,
            cache_write_1h(usage, cache_creation),
            model,
            provider,
            effort if isinstance(effort, str) else None,
            msg_id,
            is_long_request(model, input_tokens, cache_creation, cache_read),
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
        setting = local_command_setting(content)
        if setting is not None:
            return ("s", datetime_key(dt), dt, setting)
        return ("u", datetime_key(dt), dt)
    if msg_type == "pr-link":
        pr = data.get("prNumber")
        return ("p", pr) if isinstance(pr, int) else None
    if msg_type == "custom-title":
        title = data.get("customTitle")
        return ("t", title) if isinstance(title, str) and title.strip() else None
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


def fold_assistant(
    totals: dict, since_key: Optional[int], message_key: Optional[int], delta, event: _Event
) -> None:
    """Apply one assistant line to ``_parse_session_lines`` accumulators.

    ``message_key`` is the timestamp key of the message's first line (the
    line's own for a line that is not deduplicated) and ``delta`` what the
    line adds to the message's usage (all of it for a first line). The
    message is skipped when a ``since`` is set, its key parsed, and it is
    earlier. Context and model come from the line itself.
    """
    if since_key is not None and message_key is not None and message_key < since_key:
        return
    for name, value in zip(_TOKEN_KEYS, delta):
        totals[name] += value
    if event[11]:
        for name, value in zip(LONG_KEYS, delta):
            totals[name] += value
    input_tokens, output_tokens, cache_creation, cache_read = event[2:6]
    context_size = input_tokens + cache_read + cache_creation
    if context_size > 0:
        totals["current_context_tokens"] = context_size
    if input_tokens + output_tokens + cache_creation + cache_read > 0:
        if event[7]:
            totals["model"] = event[7]
            totals["model_at"] = message_key
        if event[8]:
            totals["provider"] = event[8]
        if event[9]:
            totals["effort"] = event[9]
            totals["effort_at"] = message_key
        # The reply says what it ran on: any /model or /effort before it is spent
        totals.update(empty_selection())


def fold_event(
    totals: dict,
    work_times: List[float],
    last_prompt: Optional[datetime],
    since_key: Optional[int],
    event: _Event,
) -> Optional[datetime]:
    """Apply one user, pr-link or custom-title event to the accumulators; returns the new last prompt.

    (Assistant lines go through ``fold_assistant``, which needs to know
    the message they belong to.) A user prompt is skipped when earlier
    than ``since``; consecutive prompt times yield a work time when
    positive.
    """
    if event[0] == "p":
        totals["pr_number"] = event[1]
        return last_prompt
    if event[0] == "t":
        totals["custom_title"] = event[1]
        return last_prompt
    key, prompt_time = event[1], event[2]
    if since_key is not None and key < since_key:
        return last_prompt
    if event[0] == "s":
        # A /model or /effort confirmation; still a user record's time, as before
        fold_setting(totals, key, event[3])
    if last_prompt is not None:
        duration = (prompt_time - last_prompt).total_seconds()
        if duration > 0:
            work_times.append(duration)
    return prompt_time


def _line_usage(event: _Event) -> Tuple[int, ...]:
    return event[2:7]


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
        # Bumped whenever the records start over (a rebuild): positions a
        # caller remembers (``message_ids``, ``prefix_usage``) are stale then
        self.generation = 0
        self._reset_parse()

    # ── parse state ───────────────────────────────────────────────────

    def _reset_parse(self) -> None:
        self._offset = 0
        self._tail = b""
        self._pending: Optional[_Event] = None
        self._keys = array("q")
        self._values = [array("q") for _ in _TOKEN_KEYS]
        self._ids = array("q")
        # Whether each record was a long-context request (``prefix_usage``)
        self._longs = array("b")
        self._grand = [0] * _N
        self.generation = next(_GENERATIONS)
        # Recently seen message ids (hash -> None), newest last
        self._recent: "OrderedDict[int, None]" = OrderedDict()
        # The file's first HEAD_IDS messages: [hash, key, usage list + long flag]
        self._head: List[list] = []
        self._head_at: Dict[int, int] = {}
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

    def _find(self, h: int) -> int:
        """Position of the record with message hash ``h`` (one in ``_recent``), or -1."""
        ids = self._ids
        i = len(ids) - 1
        stop = max(-1, i - 64)
        while i > stop:
            if ids[i] == h:
                return i
            i -= 1
        try:
            return ids.index(h)
        except ValueError:
            return -1

    def _match(self, event: _Event) -> Tuple[int, int]:
        """(hash, position of its message's record or -1) for a dedupable line; (0, -1) else."""
        if event[1] is None or event[10] is None:
            return 0, -1
        h = message_hash(event[10])
        if h in self._recent:
            return h, self._find(h)
        return h, -1

    def _commit_assistant(self, event: _Event):
        """Fold an assistant line into the records; returns (message key, delta)."""
        key = event[1]
        usage = _line_usage(event)
        if key is None:
            return None, usage
        keys, values, ids, grand = self._keys, self._values, self._ids, self._grand
        h, pos = self._match(event)
        if pos >= 0:
            # A later line of a message already recorded: its usage replaces
            delta = tuple(usage[i] - values[i][pos] for i in range(_N))
            for i in range(_N):
                values[i][pos] = usage[i]
                grand[i] += delta[i]
            self._recent.move_to_end(h)
            self._longs[pos] = 1 if event[11] else 0
            at = self._head_at.get(h)
            if at is not None:
                self._head[at][2] = list(usage) + [event[11]]
            return keys[pos], delta
        if not keys or key >= keys[-1]:
            keys.append(key)
            for i in range(_N):
                values[i].append(usage[i])
            ids.append(h)
            self._longs.append(1 if event[11] else 0)
        else:
            pos = bisect.bisect_right(keys, key)
            keys.insert(pos, key)
            for i in range(_N):
                values[i].insert(pos, usage[i])
            ids.insert(pos, h)
            self._longs.insert(pos, 1 if event[11] else 0)
        for i in range(_N):
            grand[i] += usage[i]
        if h:
            self._recent[h] = None
            if len(self._recent) > RECENT_IDS:
                self._recent.popitem(last=False)
            if len(self._head) < HEAD_IDS and h not in self._head_at:
                self._head_at[h] = len(self._head)
                self._head.append([h, key, list(usage) + [event[11]]])
        return key, usage

    def _tentative(self, event: _Event):
        """(message key, delta) the uncommitted tail line would fold, without folding it."""
        usage = _line_usage(event)
        if event[1] is None:
            return None, usage
        _, pos = self._match(event)
        if pos >= 0:
            return self._keys[pos], tuple(usage[i] - self._values[i][pos] for i in range(_N))
        return event[1], usage

    def _commit_block(self, block: bytes, provider_of) -> None:
        """Fold a block of whole lines (ending in a newline) into the index."""
        states = list(self._states.values())
        parse = parse_line
        for text in block.decode("utf-8", "replace").split("\n"):
            event = parse(text, provider_of)
            if event is None:
                continue
            if event[0] == "a":
                message_key, delta = self._commit_assistant(event)
                for state in states:
                    fold_assistant(state.totals, state.since_key, message_key, delta, event)
                continue
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
            pending = self._pending
            if pending is not None:
                if pending[0] == "a":
                    message_key, delta = self._tentative(pending)
                    fold_assistant(totals, state.since_key, message_key, delta, pending)
                else:
                    fold_event(totals, work_times, state.last_prompt, state.since_key, pending)
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
                message_key, delta = self._tentative(pending)
                if message_key is not None and message_key >= since_key:
                    for i in range(_N):
                        totals[i] += delta[i]
            return dict(zip(_TOKEN_KEYS, totals))

    def _sum_from(self, since_key: int) -> List[int]:
        """Usage totals of the committed messages timestamped at or after ``since_key``."""
        idx = bisect.bisect_left(self._keys, since_key)
        if idx == 0:
            return list(self._grand)
        return [sum(v[idx:]) for v in self._values]

    def head(self, refresh: bool = True) -> List[Tuple[int, int, Tuple[int, ...]]]:
        """The file's first ``HEAD_IDS`` messages: (id hash, key, usage), in file order.

        ``usage`` is the five counters, then whether the message was a
        long-context request. Committed lines only. A file that copied another's messages (a
        fork's copy of its parent's last message) has them here.
        ``refresh`` False answers from what was last read.
        """
        with self._lock:
            if refresh and not self._refresh():
                return []
            if self._missing:
                return []
            return [(h, key, tuple(usage)) for h, key, usage in self._head]

    def span(self) -> Optional[Tuple[int, int]]:
        """(first, last) message key recorded, or None (no refresh)."""
        with self._lock:
            if self._missing or not self._keys:
                return None
            return self._keys[0], self._keys[-1]

    def message_ids(self, start: int = 0) -> Tuple[int, List[Tuple[int, int]]]:
        """(generation, [(id hash, key), ...]) of the committed records from position ``start``.

        Records are in key (time) order; no refresh. A conversation that
        forked another copies the other's messages first, so its first
        records are the copies (``history_reader.fork_prefix``).
        """
        with self._lock:
            if self._missing:
                return self.generation, []
            keys, ids = self._keys, self._ids
            return self.generation, list(zip(ids[start:], keys[start:]))

    def prefix_usage(self, n: int, since_key: Optional[int] = None) -> Tuple[int, ...]:
        """The usage of the first ``n`` records (key order) dated at or after ``since_key``.

        The five counters, then their long-context part (``LONG_KEYS``):
        what a fork's copied prefix adds to ``stats`` (no refresh).
        """
        with self._lock:
            n = min(n, len(self._keys))
            lo = 0 if since_key is None else bisect.bisect_left(self._keys, since_key)
            if n <= lo:
                return (0,) * (2 * _N)
            longs = self._longs[lo:n]
            totals = [sum(v[lo:n]) for v in self._values]
            parts = [sum(compress(v[lo:n], longs)) for v in self._values]
            return tuple(totals + parts)

    def holds(self, messages: Sequence[Tuple[int, int]], refresh: bool = True) -> Set[int]:
        """Those of ``messages`` (id hash, key of a copy) this file holds the original of.

        A copy keeps its original line's timestamp, so the original's
        record (dated by its first line) is at most ``COPY_WINDOW_SECONDS``
        before it: two bisects and a scan of the records in between, per
        message. Nothing is kept between calls.
        """
        with self._lock:
            if refresh and not self._refresh():
                return set()
            if self._missing:
                return set()
            keys, ids = self._keys, self._ids
            found: Set[int] = set()
            for h, key in messages:
                lo = bisect.bisect_left(keys, key_before(key, COPY_WINDOW_SECONDS))
                hi = bisect.bisect_right(keys, key)
                if hi > lo and h in ids[lo:hi]:
                    found.add(h)
            return found


class TranscriptRegistry:
    """Path -> :class:`TranscriptIndex`, LRU-bounded by count and retained entries."""

    def __init__(self, max_indexes: int = MAX_INDEXES, max_records: int = MAX_RECORDS):
        self._lock = threading.Lock()
        self._indexes: "OrderedDict[str, TranscriptIndex]" = OrderedDict()
        self._records = 0
        self.max_indexes = max_indexes
        self.max_records = max_records
        # path -> [generation, records checked, copied prefix length, done,
        #          the earlier conversations it was checked against]
        self._forks: "OrderedDict[str, list]" = OrderedDict()

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

    def head(self, path, refresh: bool = True) -> List[Tuple[int, int, Tuple[int, ...]]]:
        index = self.get(path)
        result = index.head(refresh)
        self._account(index)
        return result

    def __len__(self) -> int:
        return len(self._indexes)

    def __contains__(self, path) -> bool:
        return os.fspath(path) in self._indexes

    @property
    def record_count(self) -> int:
        return self._records

    def fork_prefix(self, path, earlier: Sequence) -> int:
        """How many of ``path``'s first records (time order) are copies from ``earlier`` files.

        A Claude conversation forked from another (``--fork-session``,
        ``/branch``, ``/bg``) starts with a copy of the other's history:
        the same message ids, timestamps and usage. Those messages were
        spent by the conversation they came from, so the fork's own spend
        starts after them. ``earlier`` are the conversations ``path`` may
        have been forked from (they came first: the original is never
        among the later ones). The copied prefix ends at the first record
        no earlier file holds (``TranscriptIndex.holds``).

        Incremental: a file whose first message no earlier file holds is
        not a fork, and a fork's prefix ends once it has a message of its
        own; both are remembered until the file is rebuilt (or ``earlier``
        changes), so a settled answer costs a lookup. Uses the index as
        last read (the caller has just read ``path``).
        """
        key = os.fspath(path)
        sources = tuple(os.fspath(p) for p in earlier if os.fspath(p) != key)
        if not sources:
            return 0
        index = self.get(key)
        with self._lock:
            state = self._forks.get(key)
            if state is not None:
                self._forks.move_to_end(key)
        if state is None or state[0] != index.generation or state[4] != sources:
            state = [index.generation, 0, 0, False, sources]
        if not state[3]:
            generation, records = index.message_ids(state[1])
            if generation != state[0]:
                state = [generation, 0, 0, False, sources]
                generation, records = index.message_ids(0)
            originals = [self.get(p) for p in sources if os.path.exists(p)]
            while records and not state[3]:
                # The first record alone first: most files are not forks
                size = 1 if state[1] == 0 else FORK_STEP
                step, records = records[:size], records[size:]
                held: Set[int] = set()
                for original in originals:
                    held |= original.holds([m for m in step if m[0] not in held])
                for h, _key in step:
                    if h in held:
                        state[2] += 1
                    else:
                        state[3] = True  # its own message: the prefix has ended
                        break
                state[1] += len(step)
            for original in originals:
                self._account(original)
        with self._lock:
            self._forks[key] = state
            while len(self._forks) > self.max_indexes:
                self._forks.popitem(last=False)
        return state[2]

    def prefix_usage(self, path, n: int, since_key: Optional[int] = None) -> Tuple[int, ...]:
        return self.get(path).prefix_usage(n, since_key)

    def clear(self) -> None:
        with self._lock:
            self._indexes.clear()
            self._forks.clear()
            self._records = 0
