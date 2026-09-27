"""
Usage log: every key, action, click and dialog in the TUI, plus CLI calls (#483).

Records are appended to ~/.overcode/activity/YYYY-MM-DD.jsonl, one JSON
object per line, for the learning journey and the overagent to read back.
Nothing leaves the machine.

Recording is cheap on purpose: record() appends a dict to a list, and
flush() writes the batch with one O_APPEND write per day file. The TUI
flushes from its status timer and at exit.

Fine detail for two weeks, then a summary. compact() folds each day file
older than RAW_DAYS into that day's summary in rollup/YYYY-MM.json and
deletes it, so the log stays about two weeks of records plus a few KB per
day. Readers get both through summarize_log(). Month files written before
day files (YYYY-MM.jsonl) are read the same way and rolled up once every
day in them is older than RAW_DAYS.

Calls to the CLI made by agents (not by the person) are only counted, per
command per day, in agent-cli/YYYY-MM-DD.json: an agent polling
`overcode list` would otherwise be most of the log.

What is never recorded: text typed to agents. Keys typed in those contexts
(the command bar's send / standing-orders / heartbeat-instruction modes)
are logged as "<c>" with no character, and pastes are logged by length
only. Overcode's own metadata (tags, agent names, annotations, palette
queries) is recorded in full.

Switches:
    config.yaml   activity: {record: false}   turns recording off
    OVERCODE_ACTIVITY=0                        turns it off (unit tests set this)
    ActivityRecorder.paused                    off for this TUI run
While off, no record is created at all.
"""

from __future__ import annotations

import json
import os
import socket
from contextlib import contextmanager
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Optional

from .settings import get_overcode_dir

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:  # pragma: no cover - Windows
    HAS_FCNTL = False

SCHEMA_VERSION = 1

# Contexts whose printable keys are text sent to an agent: never record them.
AGENT_TEXT_CONTEXTS = frozenset({
    "command_bar:send",
    "command_bar:standing_orders",
    "command_bar:heartbeat_instruction",
})

REDACTED_CHAR = "<c>"


def get_activity_dir() -> Path:
    return get_overcode_dir() / "activity"


# Days of full records kept; older days survive only as their day summary.
RAW_DAYS = 14
ROLLUP_VERSION = 1


def activity_path_for(ts_ms: float, directory: Optional[Path] = None) -> Path:
    """The day file a record with this timestamp belongs in (local time)."""
    day = datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")
    return (directory or get_activity_dir()) / f"{day}.jsonl"


def _period(name: str) -> Optional[tuple[date, date]]:
    """[first day, day after the last) of a log file's name: a day or (legacy) a month."""
    stem = name.split(".")[0]
    try:
        if len(stem) == 10:
            d = date.fromisoformat(stem)
            return d, d + timedelta(days=1)
        y, m = (int(x) for x in stem.split("-"))
        return date(y, m, 1), date(y + (m == 12), m % 12 + 1, 1)
    except ValueError:
        return None


def _day_start_ms(d: date) -> float:
    return datetime(d.year, d.month, d.day).timestamp() * 1000


def recording_enabled() -> bool:
    """False when OVERCODE_ACTIVITY=0/off/false, or config says activity.record: false."""
    env = os.environ.get("OVERCODE_ACTIVITY")
    if env is not None:
        return env.strip().lower() not in ("0", "off", "false", "no")
    from .config import load_config
    section = load_config().get("activity")
    if isinstance(section, dict) and "record" in section:
        return bool(section["record"])
    return True


def short_host() -> str:
    return socket.gethostname().split(".")[0]


class ActivityRecorder:
    """Buffers usage records in memory and appends them to the month file."""

    def __init__(self, tmux_session: str = "", directory: Optional[Path] = None,
                 enabled: Optional[bool] = None) -> None:
        self.tmux_session = tmux_session
        self.directory = directory
        self.enabled = recording_enabled() if enabled is None else enabled
        self.paused = False
        self.sid = uuid.uuid4().hex[:12]
        self.host = short_host()
        self._buffer: list[dict] = []
        self._last_input_ms: Optional[float] = None
        self._last_key: Optional[str] = None
        self._rep = 0

    @property
    def active(self) -> bool:
        return self.enabled and not self.paused

    def record(self, kind: str, **fields: Any) -> None:
        if not self.active:
            return
        rec = {"v": SCHEMA_VERSION, "t": round(time.time() * 1000, 1),
               "host": self.host, "sid": self.sid, "ts": self.tmux_session,
               "kind": kind}
        rec.update({k: v for k, v in fields.items() if v is not None})
        self._buffer.append(rec)

    def record_key(self, key: str, character: Optional[str], ctx: str,
                   printable: bool) -> None:
        """A key press, with the gap since the previous input and a repeat count."""
        if not self.active:
            return
        now = time.time() * 1000
        dt = None if self._last_input_ms is None else int(now - self._last_input_ms)
        self._last_input_ms = now
        if ctx in AGENT_TEXT_CONTEXTS and printable:
            key, character = REDACTED_CHAR, None
        self._rep = self._rep + 1 if key == self._last_key else 1
        self._last_key = key
        self.record("key", key=key,
                    char=character if printable and character != key else None,
                    ctx=ctx, dt=dt, rep=self._rep if self._rep > 1 else None)

    def record_click(self, target: str, ctx: str) -> None:
        if not self.active:
            return
        now = time.time() * 1000
        dt = None if self._last_input_ms is None else int(now - self._last_input_ms)
        self._last_input_ms = now
        self._last_key = None
        self._rep = 0
        self.record("click", target=target, ctx=ctx, dt=dt)

    def flush(self) -> int:
        """Append buffered records to their month files. Returns how many were written.

        Failures drop the batch rather than retry: the usage log must never
        get in the way of the TUI.
        """
        if not self._buffer:
            return 0
        batch, self._buffer = self._buffer, []
        by_path: dict[Path, list[str]] = {}
        for rec in batch:
            by_path.setdefault(activity_path_for(rec["t"], self.directory), []).append(
                json.dumps(rec, separators=(",", ":"), ensure_ascii=False))
        written = 0
        for path, lines in by_path.items():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                data = ("\n".join(lines) + "\n").encode("utf-8")
                fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                try:
                    os.write(fd, data)
                finally:
                    os.close(fd)
                written += len(lines)
            except OSError:
                pass
        return written


# Commands overcode runs itself; hidden commands are added at call time.
# hook-handler fires on every tool call of every agent: it must cost nothing.
_INTERNAL_COMMANDS = frozenset({"hook-handler", "hooks", "monitor-daemon", "supervisor-daemon",
                                "heartbeat", "tmux-resize"})


def cli_command_path(argv: list[str], groups: frozenset[str] | set[str],
                     commands: frozenset[str] | set[str] | None = None) -> str:
    """"config show" for `overcode config show --x`, "launch" for `overcode launch -n a`.

    Only the command words: positional values (agent names, prompts) are
    never taken, since the second word is only read after a group name.
    With `commands` (every top-level command and group), anything else is
    not an overcode command line and gives "".
    """
    words = [a for a in argv if not a.startswith("-")]
    if not words or argv[0].startswith("-"):
        return ""
    if commands is not None and words[0] not in commands:
        return ""
    if words[0] in groups and len(words) > 1 and argv[1] == words[1]:
        return f"{words[0]} {words[1]}"
    return words[0]


def record_cli_invocation(argv: list[str], groups: frozenset[str] | set[str] = frozenset(),
                          commands: frozenset[str] | set[str] | None = None,
                          internal: frozenset[str] | set[str] = frozenset()) -> None:
    """One `cli` record per overcode command: the command path and flag names, never values.

    Calls made by an agent overcode launched (OVERCODE_SESSION_NAME set)
    are only counted (count_agent_cli): they are never the person's own use,
    and an agent polling the CLI would otherwise fill the log.
    """
    cmd = cli_command_path(argv, groups, commands)
    words = cmd.split()
    if (not cmd or words[0] in _INTERNAL_COMMANDS or words[0] in internal
            or any(w.startswith("_") for w in words) or not recording_enabled()):
        return
    if os.environ.get("OVERCODE_SESSION_NAME"):
        count_agent_cli(cmd)
        return
    flags = sorted({a.split("=", 1)[0] for a in argv if a.startswith("-")})
    rec = ActivityRecorder(tmux_session=os.environ.get("OVERCODE_TMUX_SESSION", ""), enabled=True)
    rec.record("cli", cmd=cmd, flags=flags or None, via="cli")
    rec.flush()


def _agent_cli_dir(directory: Optional[Path] = None) -> Path:
    return (directory or get_activity_dir()) / "agent-cli"


def count_agent_cli(cmd: str, now_ms: Optional[float] = None,
                    directory: Optional[Path] = None) -> None:
    """Add one to today's count of `cmd` called by agents. Never raises."""
    day = datetime.fromtimestamp((now_ms or time.time() * 1000) / 1000).strftime("%Y-%m-%d")
    path = _agent_cli_dir(directory) / f"{day}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if HAS_FCNTL:
                fcntl.flock(fd, fcntl.LOCK_EX)
            raw = b""
            while chunk := os.read(fd, 65536):
                raw += chunk
            try:
                counts = json.loads(raw) if raw.strip() else {}
                if not isinstance(counts, dict):
                    counts = {}
            except ValueError:
                counts = {}
            n = counts.get(cmd)
            counts[cmd] = (n if isinstance(n, int) else 0) + 1
            data = json.dumps(counts, separators=(",", ":")).encode()
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, data)
        finally:
            os.close(fd)
    except OSError:
        pass


def _read_agent_cli(path: Path) -> dict[str, int]:
    try:
        counts = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(counts, dict):
        return {}
    return {str(k): n for k, n in counts.items() if isinstance(n, int) and n > 0}


def iter_records(since_ms: Optional[float] = None, until_ms: Optional[float] = None,
                 directory: Optional[Path] = None,
                 _paths: Optional[list[Path]] = None) -> Iterator[dict]:
    """Every raw record in [since, until), oldest first: the last RAW_DAYS of the log.

    Older days exist only as day summaries; summarize_log() reads both.
    Reads day files and legacy (plain or gzipped) month files, a month
    before its days. Skips anything that isn't a record:
    lines that don't parse (a write cut off mid-character included), JSON
    that isn't an object, and records without a numeric `t`. A truncated or
    unreadable file ends that file, never the read.
    """
    import gzip
    if _paths is not None:
        files = _paths
    else:
        d = directory or get_activity_dir()
        if not d.is_dir():
            return
        files = sorted((p for p in d.iterdir() if p.is_file()
                        and (p.name.endswith(".jsonl") or p.name.endswith(".jsonl.gz"))
                        and _period(p.name) is not None),
                       key=lambda p: (_period(p.name)[0], len(p.name.split(".")[0]), p.name))
    for path in files:
        period = _period(path.name)
        if period is None:
            continue
        # Skip whole files that end before `since`.
        if since_ms is not None and _day_start_ms(period[1]) <= since_ms:
            continue
        opener = gzip.open if path.name.endswith(".gz") else open
        try:
            with opener(path, "rt", encoding="utf-8", errors="replace") as f:
                for line in f:
                    rec = parse_record(line)
                    if rec is None:
                        continue
                    t = rec["t"]
                    if since_ms is not None and t < since_ms:
                        continue
                    if until_ms is not None and t >= until_ms:
                        continue
                    yield rec
        except (OSError, EOFError, ValueError):
            continue


def parse_record(line: str | bytes) -> Optional[dict]:
    """One log line as a record, or None if it isn't one (see iter_records)."""
    try:
        rec = json.loads(line)
    except ValueError:  # includes UnicodeDecodeError for bytes
        return None
    if not isinstance(rec, dict):
        return None
    t = rec.get("t")
    if isinstance(t, bool) or not isinstance(t, (int, float)):
        return None
    return rec


# ── rollup: fine detail for RAW_DAYS, then one summary per day ───────────

def _rollup_dir(directory: Optional[Path] = None) -> Path:
    return (directory or get_activity_dir()) / "rollup"


@contextmanager
def _rollup_lock(directory: Optional[Path], exclusive: bool, wait: bool = True):
    """Compaction holds it exclusively; readers share it, so a day is never
    counted from its raw file and its summary at once. Yields False if a
    non-waiting exclusive lock was busy."""
    d = directory or get_activity_dir()
    handle = None
    try:
        d.mkdir(parents=True, exist_ok=True)
        handle = open(d / ".rollup.lock", "a+")  # noqa: SIM115
        if HAS_FCNTL:
            flags = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(handle.fileno(), flags | (0 if wait else fcntl.LOCK_NB))
    except OSError:
        if handle is not None:
            handle.close()
        if exclusive and not wait:
            yield False
            return
        handle = None  # can't lock: carry on unlocked, as elsewhere without fcntl
    try:
        yield True
    finally:
        if handle is not None:
            handle.close()


def _load_rollup(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict) and data.get("v") == ROLLUP_VERSION \
                and isinstance(data.get("days"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"v": ROLLUP_VERSION, "days": {}}


def _save_rollup(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False))
    os.replace(tmp, path)


def _add_to_rollup(directory: Optional[Path], day: str, source: str, state_of) -> None:
    """Merge one source's summary for `day` into its month rollup, once per source.

    Idempotent: a source already merged (a crash between writing the rollup
    and deleting the source) is not merged twice.
    """
    from .usage_analytics import from_state, merge, to_state
    path = _rollup_dir(directory) / f"{day[:7]}.json"
    data = _load_rollup(path)
    entry = data["days"].get(day) or {"sources": [], "state": None}
    if source in entry["sources"]:
        return
    summary = state_of()
    if entry["state"] is not None:
        try:
            summary = merge(from_state(entry["state"]), summary)
        except ValueError:
            pass  # a damaged day summary is replaced rather than kept forever
    entry["state"] = to_state(summary)
    entry["sources"].append(source)
    data["days"][day] = entry
    _save_rollup(path, data)


def _agent_cli_summary(counts: dict[str, int]):
    from .usage_analytics import ActionUse, Summary
    s = Summary(since_ms=None)
    for cmd, n in counts.items():
        s.cli[cmd] += n
        use = s.actions.setdefault("cli:" + cmd, ActionUse())
        use.uses += n
        use.by_via["agent"] += n
    return s


def compact(now_ms: Optional[float] = None, directory: Optional[Path] = None) -> int:
    """Roll every day older than RAW_DAYS into its day summary. Returns files rolled up.

    Never raises, and skips the work if another process is already doing it.
    Only files under the activity directory are touched.
    """
    from .usage_analytics import fold
    d = directory or get_activity_dir()
    if not d.is_dir():
        return 0
    today = datetime.fromtimestamp((now_ms or time.time() * 1000) / 1000).date()
    cutoff = today - timedelta(days=RAW_DAYS)
    done = 0
    try:
        with _rollup_lock(directory, exclusive=True, wait=False) as locked:
            if not locked:
                return 0
            for path in sorted(d.iterdir()):
                period = _period(path.name)
                if period is None or not path.is_file() or period[1] > cutoff \
                        or not (path.name.endswith(".jsonl") or path.name.endswith(".jsonl.gz")):
                    continue
                if period[1] - period[0] == timedelta(days=1):
                    day = period[0].isoformat()
                    _add_to_rollup(directory, day, path.name,
                                   lambda path=path: fold(iter_records(_paths=[path])))
                else:  # a month file from before day files: one summary per day in it
                    by_day: dict[str, list] = {}
                    for rec in iter_records(_paths=[path]):
                        key = datetime.fromtimestamp(rec["t"] / 1000).strftime("%Y-%m-%d")
                        by_day.setdefault(key, []).append(rec)
                    for day, recs in by_day.items():
                        _add_to_rollup(directory, day, path.name, lambda recs=recs: fold(recs))
                path.unlink()
                done += 1
            agent_dir = _agent_cli_dir(directory)
            if agent_dir.is_dir():
                for path in sorted(agent_dir.glob("*.json")):
                    period = _period(path.name)
                    if period is None or period[1] > cutoff:
                        continue
                    counts = _read_agent_cli(path)
                    _add_to_rollup(directory, period[0].isoformat(), "agent-cli/" + path.name,
                                   lambda counts=counts: _agent_cli_summary(counts))
                    path.unlink()
                    done += 1
    except Exception:  # noqa: BLE001 — the usage log must never get in the way
        pass
    return done


def summarize_log(since_ms: Optional[float] = None, bound_keys: frozenset = frozenset(),
                  keys_by_action: Optional[dict] = None, now_ms: Optional[float] = None,
                  directory: Optional[Path] = None):
    """The usage Summary since `since_ms` (all time if None): day summaries + recent records.

    Compacts first. A day summary counts whole: a window reaching past
    RAW_DAYS starts at the beginning of its first day.
    """
    from .usage_analytics import Summary, finish, fold, from_state, merge
    compact(now_ms, directory)
    s = Summary(since_ms=since_ms)
    with _rollup_lock(directory, exclusive=False):
        rollups = _rollup_dir(directory)
        if rollups.is_dir():
            for path in sorted(rollups.glob("*.json")):
                for day, entry in sorted(_load_rollup(path)["days"].items()):
                    period = _period(day)
                    if period is None or (since_ms is not None
                                          and _day_start_ms(period[1]) <= since_ms):
                        continue
                    try:
                        merge(s, from_state(entry["state"]))
                    except (ValueError, KeyError, TypeError):
                        continue
        merge(s, fold(iter_records(since_ms=since_ms, directory=directory)))
        agent_dir = _agent_cli_dir(directory)
        if agent_dir.is_dir():
            for path in sorted(agent_dir.glob("*.json")):
                period = _period(path.name)
                if period is None or (since_ms is not None
                                      and _day_start_ms(period[1]) <= since_ms):
                    continue
                merge(s, _agent_cli_summary(_read_agent_cli(path)))
    s.since_ms = since_ms
    return finish(s, bound_keys, keys_by_action)
