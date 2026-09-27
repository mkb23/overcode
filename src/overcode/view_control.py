"""
View control: how an agent (or a script) changes what the TUI shows (#484).

The TUI is the only writer of its own preferences, so nothing edits
tui_preferences.json behind its back. Instead:

    overcode view <verb> ...   appends a command to   sessions/<s>/tui_control.jsonl
    the TUI (once a second)    applies it, and acks in sessions/<s>/tui_control_ack.jsonl
    the CLI                    waits for the ack and prints what changed

The TUI also keeps sessions/<s>/tui_view_state.json current (on change, at
most once a second): focused agent, level, visible columns, sort, filters,
open dialog, recent actions. An agent reads it to know what "this" means.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from .settings import get_session_dir

# A view state older than this means no TUI is applying commands.
STATE_STALE_SECONDS = 10.0
ACK_TIMEOUT_SECONDS = 3.0

VERBS = ("columns", "sort", "detail", "filter", "focus", "toggle", "notify", "point")


def control_path(session: str) -> Path:
    return get_session_dir(session) / "tui_control.jsonl"


def ack_path(session: str) -> Path:
    return get_session_dir(session) / "tui_control_ack.jsonl"


def view_state_path(session: str) -> Path:
    return get_session_dir(session) / "tui_view_state.json"


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    os.replace(tmp, path)


def read_view_state(session: str) -> Optional[dict]:
    """The TUI's view state. `updated` is the later of its write and its last touch:
    an unchanged view is only touched, as a liveness signal."""
    path = view_state_path(session)
    try:
        state = json.loads(path.read_text())
        state["updated"] = max(state.get("updated", 0), path.stat().st_mtime)
        return state
    except (OSError, ValueError, AttributeError):
        return None


def tui_is_live(state: Optional[dict], now: Optional[float] = None) -> bool:
    """A TUI wrote this state recently and its process is still there."""
    if not state:
        return False
    if (now if now is not None else time.time()) - state.get("updated", 0) > STATE_STALE_SECONDS:
        return False
    pid = state.get("pid")
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def send_command(session: str, verb: str, args: dict, timeout: float = ACK_TIMEOUT_SECONDS,
                 via: Optional[str] = None) -> dict:
    """Queue one command for the TUI and wait for its ack.

    Returns the ack ({"id", "ok", "error"?, "result"?}), or
    {"ok": False, "error": "..."} when no TUI picks it up in time.
    """
    cmd_id = uuid.uuid4().hex[:12]
    if via is None:
        via = "agent" if os.environ.get("OVERCODE_SESSION_NAME") else "cli"
    ack_file = ack_path(session)
    try:
        start_offset = ack_file.stat().st_size
    except OSError:
        start_offset = 0
    append_jsonl(control_path(session), {"id": cmd_id, "t": time.time(), "verb": verb,
                                         "args": args, "via": via})
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ack = _find_ack(ack_file, start_offset, cmd_id)
        if ack is not None:
            return ack
        time.sleep(0.1)
    return {"id": cmd_id, "ok": False,
            "error": f"no TUI applied it within {timeout:.0f}s (is the monitor running for '{session}'?)"}


def _find_ack(path: Path, offset: int, cmd_id: str) -> Optional[dict]:
    try:
        with open(path, "rb") as f:
            if f.seek(0, os.SEEK_END) < offset:
                offset = 0  # truncated since
            f.seek(offset)
            for line in f.read().splitlines():
                try:
                    ack = json.loads(line)
                except ValueError:
                    continue
                if ack.get("id") == cmd_id:
                    return ack
    except OSError:
        return None
    return None


class ControlInbox:
    """The TUI's reader: new commands since it last looked, never older ones."""

    def __init__(self, session: str) -> None:
        self.path = control_path(session)
        try:
            self.offset = self.path.stat().st_size  # start at EOF: no replay of old commands
        except OSError:
            self.offset = 0

    def poll(self) -> list[dict]:
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size == self.offset:
            return []
        if size < self.offset:
            self.offset = 0
        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                data = f.read()
        except OSError:
            return []
        # Only whole lines; a partial last line waits for the next poll.
        end = data.rfind(b"\n") + 1
        self.offset += end
        commands = []
        for line in data[:end].splitlines():
            try:
                cmd = json.loads(line)
            except ValueError:
                continue
            if isinstance(cmd, dict) and cmd.get("id") and cmd.get("verb"):
                commands.append(cmd)
        return commands


def suggest(name: str, choices: list[str], limit: int = 3) -> list[str]:
    """Close matches, case-insensitive, each spelling once."""
    import difflib
    by_lower: dict[str, str] = {}
    for c in choices:
        by_lower.setdefault(c.lower(), c)
    hits = difflib.get_close_matches(name.lower(), list(by_lower), n=limit, cutoff=0.5)
    return [by_lower[h] for h in hits]
