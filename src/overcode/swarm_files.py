"""The files Claude writes beside a parent's transcript for its delegated work.

docs/design/agent-observability-0.6.1.md §6.1, phase 2. A virtual row's
name, phase and model are not in any hook payload; they live next to the
parent's transcript (``<transcript>.jsonl`` → ``<transcript>/subagents/``):

* ``agent-<id>.meta.json`` (``workflows/<run>/agent-<id>.meta.json`` for a
  workflow agent): description, agentType, workflowPhase, model. Written by
  the agent's first tool call and never changed: read once per agent.
* ``workflows/<run>/journal.jsonl``: ``started {agentId, label, phase}``
  and ``result {agentId, result}`` as the run goes. Read on from where the
  last read stopped when its (mtime, size) moves: one stat per live run of
  an expanded parent (and, for ``workflow_progress``, of every agent).

Only the engine reads these, and only for parents a view has expanded
(``swarm.virtual_rows`` takes one ``Details`` per parent).
"""

from __future__ import annotations

import json
import os
import time
from typing import Callable, Dict, Optional, Tuple

# A meta file not there yet (the agent has not made a tool call) is looked
# for again at most this often
META_RETRY_SECONDS = 2.0
# Bounds on the caches (agents and runs seen by expanded parents)
META_CACHE_MAX = 4000
JOURNAL_CACHE_MAX = 200


def _base(transcript_path: Optional[str]) -> Optional[str]:
    if not isinstance(transcript_path, str) or not transcript_path.endswith(".jsonl"):
        return None
    return os.path.expanduser(transcript_path[: -len(".jsonl")])


def _signature(path: str) -> Optional[Tuple[int, int]]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


class SwarmFiles:
    """Cached reads of meta files and workflow journals, for the whole fleet."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._meta: Dict[str, dict] = {}
        self._meta_missing: Dict[str, float] = {}
        self._journals: Dict[str, Tuple[Tuple[int, int], list, int]] = {}
        self.reads = 0  # files opened (tests and diagnostics)

    def details(self, transcript_path: Optional[str]) -> "Details":
        return Details(self, _base(transcript_path))

    def meta(self, path: str) -> Optional[dict]:
        hit = self._meta.get(path)
        if hit is not None:
            return hit
        missed = self._meta_missing.get(path)
        now = self.clock()
        if missed is not None and now - missed < META_RETRY_SECONDS:
            return None
        try:
            with open(path) as f:
                self.reads += 1
                data = json.load(f)
        except (OSError, ValueError):
            data = None
        if not isinstance(data, dict):
            if len(self._meta_missing) >= META_CACHE_MAX:
                self._meta_missing.clear()
            self._meta_missing[path] = now
            return None
        self._meta_missing.pop(path, None)
        if len(self._meta) >= META_CACHE_MAX:
            self._meta.clear()
        self._meta[path] = data
        return data

    def journal(self, path: str) -> Optional[list]:
        """A journal's events. Re-read only when its (mtime, size) moved, and
        then from where the last read stopped (it is append-only): only
        whole lines are taken, so a line still being written is read next
        time. A file that shrank is read again from the start."""
        sig = _signature(path)
        if sig is None:
            return None
        hit = self._journals.get(path)
        if hit is not None and hit[0] == sig:
            return hit[1]
        offset, events = (hit[2], list(hit[1])) if hit is not None and sig[1] >= hit[2] else (0, [])
        try:
            with open(path, "rb") as f:
                self.reads += 1
                f.seek(offset)
                data = f.read()
        except OSError:
            return None
        end = data.rfind(b"\n") + 1
        for line in data[:end].splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                events.append(event)
        if hit is None and len(self._journals) >= JOURNAL_CACHE_MAX:
            self._journals.clear()
        self._journals[path] = (sig, events, offset + end)
        return events

    def journal_signature(self, path: str) -> Optional[Tuple[int, int]]:
        """The (mtime, size) a journal had when last read, None if never read."""
        hit = self._journals.get(path)
        return hit[0] if hit is not None else None

    def journals_changed(self) -> bool:
        """Whether a journal read before has moved since (one stat each)."""
        return any(_signature(path) != hit[0] for path, hit in self._journals.items())

    def forget_journals(self, keep) -> None:
        """Stop watching journals not in ``keep`` (paths of runs still shown)."""
        for path in [p for p in self._journals if p not in keep]:
            del self._journals[path]


class Details:
    """``swarm.virtual_rows``'s lookups for one parent (its transcript's directory)."""

    def __init__(self, files: SwarmFiles, base: Optional[str]) -> None:
        self.files = files
        self.base = base
        self.journal_paths: set = set()

    def meta(self, agent_id: str, run_id: Optional[str]) -> Optional[dict]:
        if self.base is None or not agent_id:
            return None
        sub = os.path.join(self.base, "subagents")
        if run_id:
            sub = os.path.join(sub, "workflows", run_id)
        return self.files.meta(os.path.join(sub, f"agent-{agent_id}.meta.json"))

    def journal(self, run_id: str) -> Optional[list]:
        if self.base is None or not run_id:
            return None
        path = os.path.join(self.base, "subagents", "workflows", run_id, "journal.jsonl")
        self.journal_paths.add(path)
        return self.files.journal(path)


# ---------------------------------------------------------------------------
# A subagent's own background shells
# ---------------------------------------------------------------------------
#
# A subagent that ends its turn with a shell of its own running stays 🟡
# (swarm: own_work) until a hook says otherwise. Claude normally resumes it
# when the shell ends, and its next SubagentStop settles it; one it does not
# resume (a /clear left it behind, 2.1.302) fires nothing more, and the row
# stayed 🟡 until the parent's next Stop, hours later when it sat idle.
# Claude writes each shell's output to
# ``<tmp>/claude-<uid>/<project>/<session>/tasks/<id>.output`` and ends it
# with ``[exited with code N]`` when the shell exits: that line is the end.

SHELL_EXIT_MARK = b"[exited with code "
SHELL_TAIL_BYTES = 256
SHELL_CACHE_MAX = 2000


def claude_tmp_root() -> str:
    """Where Claude keeps its task output (``CLAUDE_CODE_TMPDIR`` or /tmp)."""
    base = os.environ.get("CLAUDE_CODE_TMPDIR") or "/tmp"
    return os.path.join(base, f"claude-{os.getuid()}")


class ShellEnds:
    """When a background shell exited (its output file's mtime), or None
    while it runs or its file is not found. One stat per running shell per
    call, a read only when the file moved; an ended shell is remembered."""

    def __init__(self, root: Optional[str] = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.root = root
        self.clock = clock
        self._paths: Dict[str, str] = {}
        self._missing: Dict[str, float] = {}
        self._seen: Dict[str, Tuple[int, int]] = {}
        self._ended: Dict[str, float] = {}
        self.reads = 0

    def _path(self, shell_id: str) -> Optional[str]:
        path = self._paths.get(shell_id)
        if path is not None:
            return path
        now = self.clock()
        missed = self._missing.get(shell_id)
        if missed is not None and now - missed < META_RETRY_SECONDS:
            return None
        import glob
        root = self.root or claude_tmp_root()
        found = glob.glob(os.path.join(glob.escape(root), "*", "*", "tasks",
                                       glob.escape(shell_id) + ".output"))
        if not found:
            if len(self._missing) >= SHELL_CACHE_MAX:
                self._missing.clear()
            self._missing[shell_id] = now
            return None
        self._missing.pop(shell_id, None)
        if len(self._paths) >= SHELL_CACHE_MAX:
            self._paths.clear()
        self._paths[shell_id] = found[0]
        return found[0]

    def __call__(self, shell_id: str) -> Optional[float]:
        if shell_id in self._ended:
            return self._ended[shell_id]
        path = self._path(shell_id)
        if path is None:
            return None
        try:
            st = os.stat(path)
        except OSError:
            return None
        sig = (st.st_mtime_ns, st.st_size)
        if self._seen.get(shell_id) == sig:
            return None
        self._seen[shell_id] = sig
        try:
            with open(path, "rb") as f:
                self.reads += 1
                f.seek(max(0, st.st_size - SHELL_TAIL_BYTES))
                tail = f.read()
        except OSError:
            return None
        if SHELL_EXIT_MARK not in tail:
            return None
        if len(self._ended) >= SHELL_CACHE_MAX:
            self._ended.clear()
        self._ended[shell_id] = st.st_mtime
        return st.st_mtime
