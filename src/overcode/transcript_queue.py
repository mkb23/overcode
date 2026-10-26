"""The transcript archive's capture queue: what the hooks hand the daemon.

Claude Code's ``SessionEnd`` and ``PreCompact`` hooks append the event's
``transcript_path`` here, one JSON line each, and return: the SessionEnd
budget is 1.5 s for every hook together, so the hook never copies or
compresses anything. The daemon drains the queue within about a minute
(``transcript_archive.ArchiveScheduler``); the hourly sweep is the
guarantee, the queue only makes capture sooner.

A line holds the path, the event, the conversation id and the agent's
overcode name and id when the hook ran inside one. Never content.

This module imports nothing from overcode but ``paths``: the hook handler
imports it on every SessionEnd.
"""

import json
import os
import time
from pathlib import Path
from typing import List, Tuple

from .paths import transcripts_dir

QUEUE_FILE = "queue.jsonl"
# The file a drain works from: the queue is renamed to it first, so hooks
# that fire during a drain start a new queue rather than racing the reader
DRAINING_FILE = "queue.draining.jsonl"

CAPTURE_EVENTS = frozenset({"SessionEnd", "PreCompact"})

# A hook line is a few hundred bytes; a path longer than this is not one
_MAX_PATH = 4096


def queue_path(root: "Path | None" = None) -> Path:
    return (root or transcripts_dir()) / QUEUE_FILE


def enqueue(data: dict, root: "Path | None" = None) -> bool:
    """Append ``data``'s transcript path to the queue; True when a line was written.

    ``data`` is the hook payload. One ``O_APPEND`` write of one line (atomic
    for lines this size), mode 600, under a 700 directory. Never raises: a
    hook must not fail the agent's session end.
    """
    path = data.get("transcript_path")
    if not isinstance(path, str) or not path or len(path) > _MAX_PATH:
        return False
    entry = {
        "t": round(time.time(), 3),
        "event": data.get("hook_event_name"),
        "path": path,
        "cc_session": data.get("session_id") if isinstance(data.get("session_id"), str) else None,
        "agent": os.environ.get("OVERCODE_SESSION_NAME") or None,
        "agent_id": os.environ.get("OVERCODE_SESSION_ID") or None,
    }
    try:
        root = root or transcripts_dir()
        if not root.is_dir():
            os.makedirs(root, mode=0o700, exist_ok=True)
        line = (json.dumps(entry, separators=(",", ":")) + "\n").encode("utf-8")
        fd = os.open(root / QUEUE_FILE, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
        return True
    except Exception:  # the hook must never fail on the archive
        return False


def take(root: Path) -> Tuple[List[dict], Path]:
    """The queued entries, moved aside for draining; and the file they came from.

    The queue is renamed to ``queue.draining.jsonl`` (unless an earlier
    drain left one, which is read first), so new hook lines start a fresh
    queue. The caller deletes the returned file once every entry is handled;
    a crash before that drains the same entries again, which is harmless.
    """
    draining = root / DRAINING_FILE
    queue = root / QUEUE_FILE
    if not draining.exists():
        try:
            os.replace(queue, draining)
        except FileNotFoundError:
            return [], draining
    entries: List[dict] = []
    try:
        raw = draining.read_bytes()
    except FileNotFoundError:
        return [], draining
    for line in raw.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
            entries.append(entry)
    return entries, draining


def pending(root: Path) -> int:
    """How many entries wait in the queue (and an unfinished drain)."""
    count = 0
    for name in (QUEUE_FILE, DRAINING_FILE):
        try:
            with open(root / name, "rb") as f:
                count += sum(1 for line in f if line.strip())
        except OSError:
            pass
    return count
