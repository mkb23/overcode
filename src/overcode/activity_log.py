"""
Usage log: every key, action, click and dialog in the TUI, plus CLI calls (#483).

Records are appended to ~/.overcode/activity/YYYY-MM.jsonl, one JSON object
per line, for the learning journey and the overagent to read back. Nothing
leaves the machine.

Recording is cheap on purpose: record() appends a dict to a list, and
flush() writes the batch with one O_APPEND write per month file. The TUI
flushes from its status timer and at exit.

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
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

from .settings import get_overcode_dir

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


def activity_path_for(ts_ms: float, directory: Optional[Path] = None) -> Path:
    """The month file a record with this timestamp belongs in."""
    month = datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m")
    return (directory or get_activity_dir()) / f"{month}.jsonl"


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

    Calls made by an agent overcode launched (OVERCODE_SESSION_NAME set) are
    marked via=agent so they never count as the user's own use.
    """
    cmd = cli_command_path(argv, groups, commands)
    words = cmd.split()
    if (not cmd or words[0] in _INTERNAL_COMMANDS or words[0] in internal
            or any(w.startswith("_") for w in words) or not recording_enabled()):
        return
    flags = sorted({a.split("=", 1)[0] for a in argv if a.startswith("-")})
    rec = ActivityRecorder(tmux_session=os.environ.get("OVERCODE_TMUX_SESSION", ""), enabled=True)
    via = "agent" if os.environ.get("OVERCODE_SESSION_NAME") else "cli"
    rec.record("cli", cmd=cmd, flags=flags or None, via=via)
    rec.flush()


def iter_records(since_ms: Optional[float] = None, until_ms: Optional[float] = None,
                 directory: Optional[Path] = None) -> Iterator[dict]:
    """Every record in [since, until), oldest first. The one loader every reader uses.

    Reads plain and gzipped month files. Skips anything that isn't a record:
    lines that don't parse (a write cut off mid-character included), JSON
    that isn't an object, and records without a numeric `t`. A truncated or
    unreadable file ends that file, never the read.
    """
    import gzip
    d = directory or get_activity_dir()
    if not d.is_dir():
        return
    files = sorted(p for p in d.iterdir()
                   if p.name.endswith(".jsonl") or p.name.endswith(".jsonl.gz"))
    for path in files:
        month = path.name.split(".")[0]
        if since_ms is not None:
            # Skip whole months that end before `since`.
            try:
                y, m = (int(x) for x in month.split("-"))
            except ValueError:
                continue
            nxt = datetime(y + (m == 12), m % 12 + 1, 1).timestamp() * 1000
            if nxt <= since_ms:
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
