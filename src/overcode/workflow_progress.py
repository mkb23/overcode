"""How far through its workflow runs a Claude agent is (#498).

A Claude Code Workflow ("ultracode") runs a script of agents inside one
Claude agent's session. overcode already counts the run and its agents in
the 👥 rollup (``swarm``); this works out each run's progress, per phase:
``review-changes · 2/3 · 7/12✅`` (phase 2 of 3, 7 of the 12 agents
started so far have finished).

What is knowable, best first (docs/design/agent-observability-0.6.1.md
F3, F7, F10; captures 05 and 06):

* **The script's ``meta``** (``export const meta = {name, description,
  phases: [{title, detail}]}``, a pure literal by the tool's own rule):
  the run's name and its declared phases. The hook side parses it from the
  Workflow call's ``tool_input.script`` (``parse_meta``: a literal parser,
  never JS execution); a run started by ``name`` or ``scriptPath`` has its
  script saved at the response's ``scriptPath``, read here once.
* **The run's journal** (``<transcript>/subagents/workflows/<run>/
  journal.jsonl``): ``started {agentId, label, phase}`` and ``result
  {agentId, result}`` as they happen. Exact agents started and finished,
  per phase. Re-read only when its (mtime, size) moved, from where the last
  read stopped.
* **The run's final record** (``<transcript>/workflows/<run>.json``),
  written once the run completes: each agent's ``state`` and
  ``phaseTitle``, the declared ``phases``. Exact, failures included.
* **Hooks alone** (no transcript files found): workflow agents report only
  as ``workflow-subagent``, with no run id until their ``SubagentStop``
  names their transcript. Started and finished counts, no phases.

How many agents a run will start in all is not knowable: a script decides
as it goes. ``7/12`` is finished of started so far.

The engine computes this per agent on its ticks into
``SessionDaemonState.workflow_progress`` (counts, names and absolute times,
so a quiet run publishes nothing); the TUI and ``overcode show`` only draw it.
"""

from __future__ import annotations

import os
import re
from typing import Callable, Dict, Iterable, List, Optional, Set

# Bounds on what the hook state keeps per run
PHASES_KEEP = 20
PHASE_TITLE_CHARS = 40
NAME_CHARS = 80
# The meta block sits at the top of the script; nothing past this is parsed
SCRIPT_HEAD_CHARS = 64 * 1024
# Finished runs published beside the running ones (newest)
RECENT_RUNS = 3
# Bound on the cache of scripts read from disk
SCRIPT_CACHE_MAX = 500

SOURCE_FINAL = "final"
SOURCE_JOURNAL = "journal"
SOURCE_HOOKS = "hooks"

# A final record's agent states that mean it failed (Claude Code's own words
# for a finished agent seen so far are "done"; anything else unfinished)
_FAILED_STATES = frozenset({"failed", "error", "errored", "killed", "cancelled", "canceled",
                            "stopped", "aborted", "timeout", "timed_out"})
_DONE_STATES = frozenset({"done", "completed", "complete", "succeeded", "success", "ok"})


# ---------------------------------------------------------------------------
# The script's meta block: a pure JS object literal
# ---------------------------------------------------------------------------

_META = re.compile(r"\bexport\s+const\s+meta\s*=\s*")
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_NUMBER = re.compile(r"[-+]?(?:0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
_MAX_DEPTH = 16
_MAX_ITEMS = 500


class _NotLiteral(ValueError):
    pass


class _Literal:
    """A recursive-descent reader for one JS object literal (JSON plus
    unquoted keys, single quotes, plain template strings, comments and
    trailing commas). Anything computed raises ``_NotLiteral``."""

    def __init__(self, text: str, pos: int) -> None:
        self.text = text
        self.pos = pos
        self.items = 0

    def _skip(self) -> None:
        text, n = self.text, len(self.text)
        while self.pos < n:
            ch = text[self.pos]
            if ch.isspace():
                self.pos += 1
            elif text.startswith("//", self.pos):
                end = text.find("\n", self.pos)
                self.pos = n if end < 0 else end + 1
            elif text.startswith("/*", self.pos):
                end = text.find("*/", self.pos + 2)
                if end < 0:
                    raise _NotLiteral("unterminated comment")
                self.pos = end + 2
            else:
                return

    def _peek(self) -> str:
        self._skip()
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def _expect(self, ch: str) -> None:
        if self._peek() != ch:
            raise _NotLiteral(f"expected {ch!r} at {self.pos}")
        self.pos += 1

    def value(self, depth: int = 0):
        if depth > _MAX_DEPTH:
            raise _NotLiteral("too deep")
        self.items += 1
        if self.items > _MAX_ITEMS:
            raise _NotLiteral("too big")
        ch = self._peek()
        if ch == "{":
            return self._object(depth)
        if ch == "[":
            return self._array(depth)
        if ch in "'\"`":
            return self._string()
        m = _NUMBER.match(self.text, self.pos)
        if m and m.group(0) not in ("+", "-"):
            self.pos = m.end()
            return m.group(0)
        m = _IDENT.match(self.text, self.pos)
        if m and m.group(0) in ("true", "false", "null", "undefined"):
            self.pos = m.end()
            return {"true": True, "false": False}.get(m.group(0))
        raise _NotLiteral(f"not a literal at {self.pos}")

    def _string(self) -> str:
        quote = self.text[self.pos]
        self.pos += 1
        out = []
        text, n = self.text, len(self.text)
        while self.pos < n:
            ch = text[self.pos]
            if ch == quote:
                self.pos += 1
                return "".join(out)
            if ch == "\\":
                if self.pos + 1 >= n:
                    break
                nxt = text[self.pos + 1]
                if nxt == "u" and self.pos + 6 <= n:
                    try:
                        out.append(chr(int(text[self.pos + 2:self.pos + 6], 16)))
                    except ValueError:
                        raise _NotLiteral("bad escape") from None
                    self.pos += 6
                    continue
                if nxt == "\n":  # a line continuation
                    self.pos += 2
                    continue
                out.append(_ESCAPES.get(nxt, nxt))
                self.pos += 2
                continue
            if quote == "`" and text.startswith("${", self.pos):
                raise _NotLiteral("template substitution")
            if ch == "\n" and quote != "`":
                break
            out.append(ch)
            self.pos += 1
        raise _NotLiteral("unterminated string")

    def _key(self) -> str:
        ch = self._peek()
        if ch in "'\"":
            return self._string()
        m = _IDENT.match(self.text, self.pos) or _NUMBER.match(self.text, self.pos)
        if not m:
            raise _NotLiteral(f"not a key at {self.pos}")
        self.pos = m.end()
        return m.group(0)

    def _object(self, depth: int) -> dict:
        self._expect("{")
        out: dict = {}
        while True:
            if self._peek() == "}":
                self.pos += 1
                return out
            key = self._key()
            self._expect(":")
            out[key] = self.value(depth + 1)
            if self._peek() == ",":
                self.pos += 1
            elif self._peek() != "}":
                raise _NotLiteral(f"expected ',' or '}}' at {self.pos}")

    def _array(self, depth: int) -> list:
        self._expect("[")
        out: list = []
        while True:
            if self._peek() == "]":
                self.pos += 1
                return out
            out.append(self.value(depth + 1))
            if self._peek() == ",":
                self.pos += 1
            elif self._peek() != "]":
                raise _NotLiteral(f"expected ',' or ']' at {self.pos}")


def _clip(text, chars: int) -> Optional[str]:
    if not isinstance(text, str):
        return None
    text = " ".join(text.split())
    if not text:
        return None
    return text if len(text) <= chars else text[:chars - 1] + "…"


def phase_titles(phases) -> List[str]:
    """Declared phase titles from a ``phases`` list (``[{title}]`` or plain strings)."""
    titles = []
    for phase in phases if isinstance(phases, list) else ():
        title = phase.get("title") if isinstance(phase, dict) else phase
        title = _clip(title, PHASE_TITLE_CHARS)
        if title:
            titles.append(title)
        if len(titles) >= PHASES_KEEP:
            break
    return titles


def parse_meta(script) -> Optional[dict]:
    """``{"name", "phases": [title, ...]}`` from a workflow script's
    ``export const meta = {...}``, or None when there is none or it is not
    a plain literal. Never runs the script."""
    if not isinstance(script, str):
        return None
    head = script[:SCRIPT_HEAD_CHARS]
    m = _META.search(head)
    if m is None:
        return None
    try:
        meta = _Literal(head, m.end()).value()
    except (_NotLiteral, RecursionError):
        return None
    if not isinstance(meta, dict):
        return None
    out = {}
    name = _clip(meta.get("name"), NAME_CHARS)
    if name:
        out["name"] = name
    titles = phase_titles(meta.get("phases"))
    if titles:
        out["phases"] = titles
    return out or None


# ---------------------------------------------------------------------------
# One run's progress (pure)
# ---------------------------------------------------------------------------

def _from_final(final: dict) -> Optional[dict]:
    """Agents per phase from a run's final record (``workflows/<run>.json``)."""
    progress = final.get("workflowProgress")
    if not isinstance(progress, list):
        return None
    agents = []
    for item in progress:
        if not isinstance(item, dict) or item.get("type") != "workflow_agent":
            continue
        state = str(item.get("state") or "").lower()
        agents.append({"phase": _clip(item.get("phaseTitle"), PHASE_TITLE_CHARS),
                       "done": state in _DONE_STATES or state in _FAILED_STATES,
                       "failed": state in _FAILED_STATES})
    return {"agents": agents, "phases": phase_titles(final.get("phases"))}


def _from_journal(events) -> Optional[dict]:
    """Agents per phase from a run's journal, in the order they started."""
    started: Dict[str, dict] = {}
    for event in events or ():
        if not isinstance(event, dict) or not isinstance(event.get("agentId"), str):
            continue
        agent_id = event["agentId"]
        if event.get("type") == "started":
            started.pop(agent_id, None)  # started again (a retry): counted once, as latest
            started[agent_id] = {"phase": _clip(event.get("phase"), PHASE_TITLE_CHARS),
                                 "done": False, "failed": False}
        elif event.get("type") == "result" and agent_id in started:
            started[agent_id]["done"] = True
    if not started:
        return None
    return {"agents": list(started.values()), "phases": []}


def run_progress(run: dict, journal=None, final=None, script_meta=None,
                 hook_counts=None) -> dict:
    """One run's published progress.

    ``run`` is the hook state's record (``swarm.workflows[task]``: name,
    status, times, ``phases`` parsed from its script); ``journal`` its
    journal's events, ``final`` its final record, ``script_meta`` the meta
    of its saved script (``parse_meta``), ``hook_counts`` ``(started,
    finished)`` from hooks alone. The best one present decides the counts.
    """
    declared = list(run.get("phases") or ())
    name = run.get("name")
    if isinstance(script_meta, dict):
        declared = declared or list(script_meta.get("phases") or ())
        name = name or script_meta.get("name")
    agents = None
    source = SOURCE_HOOKS
    if isinstance(final, dict):
        parsed = _from_final(final)
        if parsed is not None:
            agents, source = parsed["agents"], SOURCE_FINAL
            declared = declared or parsed["phases"]
    if agents is None:
        parsed = _from_journal(journal)
        if parsed is not None:
            agents, source = parsed["agents"], SOURCE_JOURNAL
    out = {"name": _clip(name, NAME_CHARS) or "workflow",
           "status": run.get("status") or "running",
           "started_at": run.get("started_at"), "ended_at": run.get("ended_at"),
           "source": source}
    if agents is None:
        started, finished = hook_counts or (0, 0)
        out.update(started=started, done=finished, failed=0)
        if declared:
            out["phase_count"] = len(declared)
        return {k: v for k, v in out.items() if v is not None}
    out.update(started=len(agents), done=sum(1 for a in agents if a["done"]),
               failed=sum(1 for a in agents if a["failed"]))
    titles = list(declared)
    for agent in agents:
        if agent["phase"] and agent["phase"] not in titles:
            titles.append(agent["phase"])
    phases = []
    for title in titles[:PHASES_KEEP]:
        members = [a for a in agents if a["phase"] == title]
        phase = {"title": title, "started": len(members),
                 "done": sum(1 for a in members if a["done"])}
        failed = sum(1 for a in members if a["failed"])
        if failed:
            phase["failed"] = failed
        phases.append(phase)
    if phases:
        out["phases"] = phases
    # The phase it is in: the one its latest-started agent belongs to
    current = next((a["phase"] for a in reversed(agents) if a["phase"]), None)
    if current is not None and current in titles[:PHASES_KEEP]:
        out["phase"] = current
        if declared and current in declared:
            out["phase_index"] = declared.index(current) + 1
    if declared:
        out["phase_count"] = len(declared)
    return {k: v for k, v in out.items() if v is not None}


def _hook_counts(hook_state: dict, runs: dict) -> Dict[str, tuple]:
    """(started, finished) per task from hooks alone: a finished workflow
    agent names its run (``done[*].run``, from its transcript's path); a
    live one does not, and goes to the newest running run."""
    from . import swarm

    by_run_id = {r.get("run_id"): t for t, r in runs.items() if r.get("run_id")}
    counts = {t: [0, 0] for t in runs}
    sw = hook_state.get("swarm") if isinstance(hook_state.get("swarm"), dict) else {}
    for entry in (sw.get("done") or {}).values():
        if isinstance(entry, dict) and entry.get("kind") == swarm.KIND_WORKFLOW:
            task = by_run_id.get(entry.get("run"))
            if task is None and len(runs) == 1:
                task = next(iter(runs))
            if task is not None:
                counts[task][0] += 1
                counts[task][1] += 1
    running = [t for t, r in runs.items() if r.get("status") == "running"]
    for entry in (hook_state.get("subagents") or {}).values():
        if isinstance(entry, dict) and entry.get("kind") == swarm.KIND_WORKFLOW and running:
            counts[running[-1]][0] += 1
    return {t: (c[0], c[1]) for t, c in counts.items()}


def shown_runs(runs: dict) -> List[str]:
    """The tasks to publish: every running run and the newest finished ones, oldest first."""
    tasks = list(runs)
    finished = [t for t in tasks if runs[t].get("status") != "running"][-RECENT_RUNS:]
    return [t for t in tasks if runs[t].get("status") == "running" or t in finished]


def progress(hook_state: Optional[dict], journal: Callable[[str], Optional[list]] = lambda r: None,
             final: Callable[[str], Optional[dict]] = lambda r: None,
             script: Callable[[str], Optional[dict]] = lambda p: None) -> Optional[list]:
    """Every shown run's progress, oldest first, or None without workflow runs.

    ``journal(run_id)``, ``final(run_id)`` and ``script(path)`` read the
    files (see ``WorkflowFiles``); the defaults read nothing (hooks only).
    """
    if not isinstance(hook_state, dict):
        return None
    sw = hook_state.get("swarm") if isinstance(hook_state.get("swarm"), dict) else {}
    runs = {t: r for t, r in (sw.get("workflows") or {}).items() if isinstance(r, dict)}
    if not runs:
        return None
    hooks = _hook_counts(hook_state, runs)
    out = []
    for task in shown_runs(runs):
        run = runs[task]
        run_id = run.get("run_id")
        finished = run.get("status") != "running"
        script_meta = None
        if not run.get("phases") and run.get("script_path"):
            script_meta = script(run["script_path"])
        out.append(run_progress(
            run,
            journal=journal(run_id) if run_id else None,
            final=final(run_id) if run_id and finished else None,
            script_meta=script_meta,
            hook_counts=hooks.get(task)))
    return out or None


def active_run(runs) -> Optional[dict]:
    """The run a row shows: the newest running one."""
    running = [r for r in runs or () if isinstance(r, dict) and r.get("status") == "running"]
    return running[-1] if running else None


# ---------------------------------------------------------------------------
# The files, for the engine
# ---------------------------------------------------------------------------

class WorkflowFiles:
    """The engine's reads for every agent's workflow progress: journals
    (incremental, on change only), final records and saved scripts (once
    each). ``changed()`` says which agents' running journals moved since
    they were last read: one stat per running run."""

    def __init__(self, files=None) -> None:
        from .swarm_files import SwarmFiles

        self.files = files if files is not None else SwarmFiles()
        self._scripts: Dict[str, Optional[dict]] = {}
        # agent id -> {journal path of a running run: its (mtime, size)
        # when last read or reported, None while it is not there}
        self._watched: Dict[str, Dict[str, Optional[tuple]]] = {}

    def _script(self, path: str) -> Optional[dict]:
        path = os.path.expanduser(path)
        if path in self._scripts:
            return self._scripts[path]
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                self.files.reads += 1
                meta = parse_meta(f.read(SCRIPT_HEAD_CHARS))
        except OSError:
            return None  # not there (yet): asked again next time
        if len(self._scripts) >= SCRIPT_CACHE_MAX:
            self._scripts.clear()
        self._scripts[path] = meta
        return meta

    def of(self, agent_id: str, hook_state: Optional[dict]) -> Optional[list]:
        """Agent ``agent_id``'s workflow progress from its hook state."""
        sw = (hook_state or {}).get("swarm") if isinstance(hook_state, dict) else None
        if not isinstance(sw, dict) or not sw.get("workflows"):
            self._watched.pop(agent_id, None)
            return None
        details = self.files.details(sw.get("transcript"))
        base = details.base
        watched: Dict[str, Optional[tuple]] = {}

        def journal(run_id: str) -> Optional[list]:
            events = details.journal(run_id)
            run = next((r for r in sw["workflows"].values()
                        if isinstance(r, dict) and r.get("run_id") == run_id), {})
            if base is not None and run.get("status") == "running":
                path = os.path.join(base, "subagents", "workflows", run_id, "journal.jsonl")
                watched[path] = self.files.journal_signature(path) if events is not None else None
            return events

        def final(run_id: str) -> Optional[dict]:
            if base is None:
                return None
            return self.files.meta(os.path.join(base, "workflows", f"{run_id}.json"))

        out = progress(hook_state, journal, final, self._script)
        if watched:
            self._watched[agent_id] = watched
        else:
            self._watched.pop(agent_id, None)
        return out

    def changed(self) -> Set[str]:
        """Agents whose running runs' journals moved since they were read
        (one stat each). Each move is reported once, so an agent whose
        re-detection failed is not asked for again at every wake step."""
        from .swarm_files import _signature

        out = set()
        for agent, paths in self._watched.items():
            for path, seen in paths.items():
                sig = _signature(path)
                if sig != seen:
                    paths[path] = sig
                    out.add(agent)
        return out

    def forget(self, keep: Iterable[str]) -> None:
        """Stop watching agents not in ``keep``."""
        keep = set(keep)
        for agent in [a for a in self._watched if a not in keep]:
            del self._watched[agent]


# ---------------------------------------------------------------------------
# Words, for the views
# ---------------------------------------------------------------------------

def phase_fraction(run: dict) -> Optional[str]:
    """``2/3`` (phase 2 of 3 declared), the phase's title when none are
    declared, or None before any phase is known."""
    index, count = run.get("phase_index"), run.get("phase_count")
    if index and count:
        return f"{index}/{count}"
    return run.get("phase")


def describe_run(run: dict, done_glyph: str = "✅", failed_glyph: str = "❌") -> List[str]:
    """A run's detail lines, for hovers and ``overcode show``:
    ``review-changes · running · phase 2/3 Verify · 7/12 agents done``
    then one line per phase, ``  Verify  4/9 done``."""
    status = run.get("status") or "running"
    words = {"running": "running", "ok": f"{done_glyph} done", "failed": f"{failed_glyph} failed"}
    head = [run.get("name") or "workflow", words.get(status, status)]
    phase = run.get("phase")
    if phase and status == "running":
        fraction = (f"{run['phase_index']}/{run['phase_count']} "
                    if run.get("phase_index") and run.get("phase_count") else "")
        head.append(f"phase {fraction}{phase}")
    elif run.get("phase_count") and not run.get("phases"):
        head.append(f"{run['phase_count']} phases")
    started, done = run.get("started") or 0, run.get("done") or 0
    agents = f"{done}/{started} agent{'' if started == 1 else 's'} finished"
    if run.get("failed"):
        agents += f", {run['failed']} failed"
    head.append(agents)
    lines = [" · ".join(head)]
    for ph in run.get("phases") or ():
        if not ph.get("started"):
            lines.append(f"  {ph['title']}  not started")
            continue
        text = f"  {ph['title']}  {ph.get('done', 0)}/{ph['started']} finished"
        if ph.get("failed"):
            text += f", {ph['failed']} failed"
        lines.append(text)
    if run.get("source") == SOURCE_HOOKS and status == "running":
        lines.append("  (from hooks only: no per-phase counts)")
    return lines
