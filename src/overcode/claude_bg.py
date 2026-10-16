"""Claude background sessions as fleet rows (0.6.1 observability, phase 3).

docs/design/agent-observability-0.6.1.md §1 (F14, F15), §3 and §6. A Claude
background session (``claude --bg``, ``/bg``, or one dispatched from
``claude agents``) is supervised by Claude's own daemon and registered in
``~/.claude/jobs/<short id>/state.json``::

    {"state": "working" | "blocked" | "done" | "failed" | "stopped" | ...,
     "tempo": "active" | "idle" | "blocked", "detail": "<one line>",
     "needs": "<the pending question>", "inFlight": {"tasks", "queued"},
     "output": {"result"}, "sessionId", "resumeSessionId", "daemonShort",
     "name", "cwd", "linkScanPath": "<transcript>", "respawnFlags": [...],
     "createdAt", "updatedAt", "lastTerminalAt", ...}

The engine reads those files (one ``stat`` per job per scan, a read only
when one changed) and publishes each session that belongs to the fleet as
an ordinary agent view with ``kind: "claude_bg"``: owner decision 1 shows a
background session only when its ``cwd`` is inside the directory of one of
this tmux session's live agents. Each is nested under the agent it came
from when that is known, else the agent whose directory holds it
(``attribute``).

Everything here is pure except ``JobsScanner`` (the jobs directory),
``find_job`` (one job, for the hook handler) and ``BgSpend`` (transcripts).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Tuple

from .session_manager import Session
from .status_constants import (
    STATUS_BUSY_SLEEPING,
    STATUS_COLOR_GREEN,
    STATUS_COLOR_RED,
    STATUS_COLOR_REST,
    STATUS_COLOR_YELLOW,
    STATUS_DONE,
    STATUS_RUNNING,
    STATUS_UNKNOWN,
    STATUS_WAITING_USER,
    StatusBadge,
    StatusDetail,
)

# The agent view's ``kind`` (SessionDaemonState.kind) for these rows
KIND = "claude_bg"
# Row identity (§3.2): never a path component, never a swarm parent prefix
ID_PREFIX = "claude-bg:"
# Shown before NAME (§4.0), and its emoji-free stand-in
MARKER = "🌙"
MARKER_ASCII = "Bs"

# Job states (Claude Code 2.1.30x): working/blocked are live; done, failed,
# stopped and crashed are over; starting/resuming/adopted are on the way up
_LIVE_STARTING = frozenset({"starting", "resuming", "adopted", "warming"})
_TERMINAL = {"done": "success", "failed": "failure", "crashed": "failure",
             "stopped": "unreported", "error": "failure"}

# Attribution, strongest first (``attribute``)
VIA_HOOKS = "hooks"                # overcode's hooks fired in it under the agent's name
VIA_CONVERSATION = "conversation"  # it continues one of the agent's conversations
VIA_DIRECTORY = "directory"        # only its directory says so


def claude_config_dir() -> Path:
    """Claude Code's own directory: ``$CLAUDE_CONFIG_DIR``, else ``~/.claude``."""
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured) if configured else Path.home() / ".claude"


def jobs_dir() -> Path:
    """Where Claude registers its background sessions (F14)."""
    return claude_config_dir() / "jobs"


def row_id(session_id: str) -> str:
    return f"{ID_PREFIX}{session_id}"


def is_row_id(value: Optional[str]) -> bool:
    return isinstance(value, str) and value.startswith(ID_PREFIX)


def _read_json(path: Path) -> Optional[dict]:
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def find_job(session_id: str, directory: Optional[Path] = None,
             scan_all: bool = True) -> Optional[dict]:
    """The registered background session whose ``sessionId`` is ``session_id``, or None.

    Claude names a job's directory after the first eight characters of its
    session id (``daemonShort``), so that file is tried first (one read);
    ``scan_all`` then reads every job, for a session started as a copy
    under another id.
    """
    if not isinstance(session_id, str) or not session_id:
        return None
    base = directory if directory is not None else jobs_dir()
    job = _read_json(base / session_id[:8] / "state.json")
    if job is not None and job.get("sessionId") == session_id:
        return job
    if not scan_all:
        return None
    try:
        entries = os.listdir(base)
    except OSError:
        return None
    for entry in entries:
        if entry == session_id[:8]:
            continue
        job = _read_json(base / entry / "state.json")
        if job is not None and job.get("sessionId") == session_id:
            return job
    return None


class JobsScanner:
    """The jobs directory, re-read only where a ``state.json`` changed.

    ``scan`` lists the directory and stats each job's state file (its
    ``(mtime_ns, size)``); a changed file is read again, a vanished job
    (``claude rm``) is dropped. ``jobs`` maps the short id to the parsed
    state; an unreadable file keeps the job with ``None`` so it shows as
    unknown rather than vanishing mid-write.
    """

    def __init__(self, directory: Optional[Path] = None) -> None:
        self._directory = directory
        self.jobs: Dict[str, Optional[dict]] = {}
        self._signatures: Dict[str, Tuple[int, int]] = {}
        self.reads = 0  # state files parsed (tests: count the work)

    @property
    def directory(self) -> Path:
        return self._directory if self._directory is not None else jobs_dir()

    def scan(self) -> bool:
        """Bring ``jobs`` up to date; True if any job appeared, changed or went."""
        base = self.directory
        try:
            entries = os.listdir(base)
        except OSError:
            entries = []
        changed = False
        seen = set()
        for entry in entries:
            path = base / entry / "state.json"
            try:
                st = os.stat(path)
            except OSError:
                continue  # not a job (pins.json), or mid-creation
            seen.add(entry)
            sig = (st.st_mtime_ns, st.st_size)
            if self._signatures.get(entry) == sig and entry in self.jobs:
                continue
            self._signatures[entry] = sig
            self.reads += 1
            job = _read_json(path)
            if job is None and self.jobs.get(entry) is not None:
                continue  # caught mid-write: keep the last good read, retry next scan
            if self.jobs.get(entry, ...) != job:
                self.jobs[entry] = job
                changed = True
        for entry in [e for e in self.jobs if e not in seen]:
            del self.jobs[entry]
            self._signatures.pop(entry, None)
            changed = True
        return changed


# ── Attribution ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FleetAgent:
    """A live overcode agent a background session may belong to."""

    id: str
    name: str
    directory: Optional[str] = None
    conversations: FrozenSet[str] = frozenset()   # its own agent session ids
    foreign: FrozenSet[str] = frozenset()         # other conversations its hooks saw
    repo_name: Optional[str] = None
    branch: Optional[str] = None


@lru_cache(maxsize=1024)
def _real(path: str) -> str:
    """``path`` resolved (``~``, symlinks); remembered: rows are rebuilt often."""
    try:
        return os.path.realpath(os.path.expanduser(path))
    except (OSError, ValueError):
        return path


def _inside(path: str, directory: str) -> bool:
    p, d = _real(path), _real(directory)
    return p == d or p.startswith(d.rstrip(os.sep) + os.sep)


def attribute(job: Mapping[str, Any], agents: Iterable[FleetAgent]
              ) -> Optional[Tuple[FleetAgent, str]]:
    """The fleet agent ``job`` came from, and how that is known; None: not the fleet's.

    1. Its hooks fired under that agent's name (it inherited the agent's
       overcode environment, as ``/bg`` from the agent's Claude does): the
       agent's hook state lists it as a foreign conversation.
    2. It continues one of the agent's own conversations (``sessionId`` or
       ``resumeSessionId``).
    3. Its ``cwd`` is inside the agent's directory (the deepest one wins).

    A session that matches none of these is not under a fleet repo and is
    not shown (owner decision 1).
    """
    agents = list(agents)
    sid = job.get("sessionId")
    resumed = job.get("resumeSessionId")
    cwd = job.get("cwd")
    for agent in agents:
        if sid and sid in agent.foreign:
            return agent, VIA_HOOKS
    for agent in agents:
        if (sid and sid in agent.conversations) or (resumed and resumed in agent.conversations):
            return agent, VIA_CONVERSATION
    if not isinstance(cwd, str) or not cwd:
        return None
    best: Optional[FleetAgent] = None
    best_len = -1
    for agent in agents:
        if agent.directory and _inside(cwd, agent.directory):
            depth = len(_real(agent.directory))
            if depth > best_len:
                best, best_len = agent, depth
    return (best, VIA_DIRECTORY) if best is not None else None


# ── State mapping (§3.4) ───────────────────────────────────────────────


@dataclass(frozen=True)
class JobLight:
    """What a job's state file says, in overcode's terms."""

    status: str                 # a STATUS_* the row's symbol comes from
    colour: Optional[str]       # STATUS_COLOR_*, None when unknown
    badges: Tuple[StatusBadge, ...] = ()
    activity: str = ""
    result: Optional[str] = None  # the Result column: success / failure / unreported
    reason: str = ""
    ended_at: Optional[float] = None  # epoch, for a finished job

    def detail(self) -> Optional[StatusDetail]:
        if self.colour is None:
            return None
        return StatusDetail(self.colour, list(self.badges), self.status)


def _epoch(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) / 1000.0 if value > 1e11 else float(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _one_line(value: Any, limit: int = 200) -> str:
    if not isinstance(value, str):
        return ""
    line = " ".join(value.split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _in_flight(job: Mapping[str, Any]) -> int:
    flight = job.get("inFlight")
    if not isinstance(flight, dict):
        return 0
    total = 0
    for key in ("tasks", "queued"):
        value = flight.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            total += value
    return total


def job_light(job: Optional[Mapping[str, Any]]) -> JobLight:
    """§3.4's background-session column: 🟢 working, 🟡 waiting on its own
    background work, 🔴 blocked (needs you), ⚪ finished with a Result, ❔
    an unreadable state file."""
    if not isinstance(job, Mapping):
        return JobLight(STATUS_UNKNOWN, None, activity="state file unreadable")
    state = job.get("state")
    tempo = job.get("tempo")
    detail = _one_line(job.get("detail"))
    if state in _TERMINAL:
        output = job.get("output") if isinstance(job.get("output"), dict) else {}
        result_text = _one_line(output.get("result")) or detail or str(state)
        ended = (_epoch(job.get("lastTerminalAt")) or _epoch(job.get("firstTerminalAt"))
                 or _epoch(job.get("updatedAt")))
        return JobLight(STATUS_DONE, STATUS_COLOR_REST,
                        activity=result_text if state == "done" else f"{state}: {result_text}",
                        result=_TERMINAL[state], reason=result_text, ended_at=ended)
    if state == "blocked" or tempo == "blocked":
        needs = _one_line(job.get("needs")) or detail or "Needs your input"
        return JobLight(STATUS_WAITING_USER, STATUS_COLOR_RED,
                        (StatusBadge("awaiting_input"),), activity=needs)
    if state == "working" or state in _LIVE_STARTING:
        if state in _LIVE_STARTING:
            return JobLight(STATUS_RUNNING, STATUS_COLOR_GREEN, activity=detail or f"{state}…")
        if tempo == "active" or tempo is None:
            return JobLight(STATUS_RUNNING, STATUS_COLOR_GREEN, activity=detail)
        waiting = _in_flight(job)
        badges = (StatusBadge("bg_task", count=waiting),) if waiting else ()
        what = f"Waiting on {waiting} background task{'s' if waiting != 1 else ''}" \
            if waiting else "Idle, will resume on its own"
        return JobLight(STATUS_BUSY_SLEEPING, STATUS_COLOR_YELLOW, badges,
                        activity=detail or what)
    return JobLight(STATUS_UNKNOWN, None, activity=detail or f"state {state!r}")


def _flag(flags: Any, name: str) -> Optional[str]:
    if not isinstance(flags, list):
        return None
    for i, value in enumerate(flags[:-1]):
        if value == name and isinstance(flags[i + 1], str):
            return flags[i + 1]
    return None


def job_name(job: Mapping[str, Any], short: str) -> str:
    name = job.get("name")
    return name if isinstance(name, str) and name.strip() else f"bg-{short}"


def transcript_path(job: Mapping[str, Any]) -> Optional[Path]:
    path = job.get("linkScanPath")
    if not isinstance(path, str) or not path:
        return None
    return Path(os.path.expanduser(path))


# ── Rows ───────────────────────────────────────────────────────────────


@dataclass
class BgRow:
    """One background session the engine publishes: its view, light and identity."""

    short: str
    state: Any  # SessionDaemonState
    light: JobLight
    started_at: Optional[float]


def build_rows(
    jobs: Mapping[str, Optional[dict]],
    agents: Iterable[FleetAgent],
    now: float,
    keep_seconds: float,
    since: Optional[Dict[str, Tuple[tuple, float]]] = None,
    spend: Optional[Mapping[str, dict]] = None,
) -> Tuple[List[BgRow], Optional[float]]:
    """The fleet's background sessions as agent states, and when they next change.

    ``since`` (the caller's, kept between calls) remembers when each row's
    light last changed, for ``status_since``: a quiet job builds the same
    state every time. ``spend`` is ``BgSpend.values`` (tokens, cost, model
    by session id). The deadline is the earliest time a finished row's
    ``keep_seconds`` runs out (None: no finished row on show).
    """
    from .clock import stamp
    from .monitor_daemon_state import SessionDaemonState

    agents = list(agents)
    since = since if since is not None else {}
    spend = spend or {}
    rows: List[BgRow] = []
    deadlines: List[float] = []
    seen: set = set()
    for short, job in sorted(jobs.items()):
        if not isinstance(job, dict):
            continue  # unreadable before its first good read: nothing to attribute
        sid = job.get("sessionId")
        if not isinstance(sid, str) or not sid:
            continue
        match = attribute(job, agents)
        if match is None:
            continue
        light = job_light(job)
        if light.ended_at is not None:
            until = light.ended_at + keep_seconds
            if now >= until:
                continue
            deadlines.append(until)
        parent, via = match
        rid = row_id(sid)
        seen.add(rid)
        key = (light.status, light.colour)
        prev = since.get(rid)
        if prev is None or prev[0] != key:
            first = prev is None
            at = (light.ended_at if light.ended_at is not None
                  else (_epoch(job.get("updatedAt")) if first else None)) or now
            since[rid] = (key, at)
        status_at = since[rid][1]
        started = _epoch(job.get("createdAt"))
        money = spend.get(sid) or {}
        model = money.get("model") or _flag(job.get("respawnFlags"), "--model")
        cwd = job.get("cwd") if isinstance(job.get("cwd"), str) else None
        same_dir = bool(cwd and parent.directory and _real(cwd) == _real(parent.directory))
        state = SessionDaemonState(
            session_id=rid,
            name=job_name(job, short),
            kind=KIND,
            current_status=light.status,
            current_activity=light.activity,
            status_since=stamp(datetime.fromtimestamp(status_at)),
            start_time=stamp(datetime.fromtimestamp(started)) if started else None,
            start_directory=cwd,
            repo_name=parent.repo_name if same_dir else (os.path.basename(cwd.rstrip(os.sep))
                                                         if cwd else None),
            branch=parent.branch if same_dir else None,
            parent_name=parent.name,
            depth=1,
            model=model,
            backend="claude-code",
            task_result=light.result,
            task_reason=light.reason,
            stats_available=True if money else None,
            input_tokens=int(money.get("input_tokens") or 0),
            output_tokens=int(money.get("output_tokens") or 0),
            cache_creation_tokens=int(money.get("cache_creation_tokens") or 0),
            cache_read_tokens=int(money.get("cache_read_tokens") or 0),
            estimated_cost_usd=float(money.get("cost_usd") or 0.0),
            claude_bg={"id": short, "session_id": sid, "parent_id": parent.id,
                       "via": via, "cwd": cwd},
        )
        rows.append(BgRow(short, state, light, started))
    for rid in [r for r in since if r not in seen]:
        del since[rid]
    return rows, (min(deadlines) if deadlines else None)


# ── Spend ──────────────────────────────────────────────────────────────


@dataclass
class BgSpend:
    """Each live background session's tokens and cost, from its own transcript.

    A background session is its own conversation (§3.5): it counts once,
    under its own row, deduplicated like an agent's (``read_tree_stats``
    over the transcript and its subagents). Read at the stats cadence while
    the session is live, once more when it finishes, then frozen.
    """

    values: Dict[str, dict] = field(default_factory=dict)  # session id -> spend
    _read_at: Dict[str, float] = field(default_factory=dict)
    _frozen: set = field(default_factory=set)
    reads: int = 0

    def due(self, sid: str, now: float, every: float, finished: bool) -> bool:
        if sid in self._frozen:
            return False
        last = self._read_at.get(sid)
        return last is None or finished or now - last >= every

    def sync(self, rows_jobs: Iterable[Tuple[str, dict, bool]], now: float, every: float) -> bool:
        """Read the sessions whose turn it is; True if any value changed.

        ``rows_jobs`` is (session id, job, finished) for each published row.
        """
        changed = False
        live = set()
        for sid, job, finished in rows_jobs:
            live.add(sid)
            if not self.due(sid, now, every, finished):
                continue
            self._read_at[sid] = now
            if finished:
                self._frozen.add(sid)
            value = read_spend(job)
            self.reads += 1
            if value is not None and value != self.values.get(sid):
                self.values[sid] = value
                changed = True
        for sid in [s for s in self._read_at if s not in live]:
            self._read_at.pop(sid, None)
            self.values.pop(sid, None)
            self._frozen.discard(sid)
        return changed


def read_spend(job: Mapping[str, Any]) -> Optional[dict]:
    """Tokens and cost of a background session's conversation, or None (no transcript)."""
    path = transcript_path(job)
    if path is None or not path.exists():
        return None
    try:
        from .history_reader import read_tree_stats, subagent_files
        from .pricing import calculate_cost_estimate, cost_of_1h_writes
        from .settings import get_model_pricing, get_user_config

        files = [path] + subagent_files(path.with_suffix("") / "subagents")
        results = read_tree_stats(files)
    except Exception:
        return None
    config = get_user_config()
    out = {"input_tokens": 0, "output_tokens": 0, "cache_creation_tokens": 0,
           "cache_read_tokens": 0, "cost_usd": 0.0, "model": None}
    fallback_model = _flag(job.get("respawnFlags"), "--model")
    for i, (totals, _work) in enumerate(results):
        model = totals.get("model") or out["model"] or fallback_model
        if i == 0 and totals.get("model"):
            out["model"] = totals["model"]
        mp = get_model_pricing(model, config)
        out["cost_usd"] += calculate_cost_estimate(
            totals.get("input_tokens", 0), totals.get("output_tokens", 0),
            totals.get("cache_creation_tokens", 0), totals.get("cache_read_tokens", 0),
            price_input=mp.input, price_output=mp.output,
            price_cache_write=mp.cache_write, price_cache_read=mp.cache_read,
        ) + cost_of_1h_writes(totals.get("cache_creation_1h_tokens", 0), mp)
        for key in ("input_tokens", "output_tokens", "cache_creation_tokens",
                    "cache_read_tokens"):
            out[key] += int(totals.get(key) or 0)
    out["cost_usd"] = round(out["cost_usd"], 6)
    return out


# ── The engine's source ────────────────────────────────────────────────

# How often the engine looks at the jobs directory (§6.1, §6.3: a
# background session's state reaches views within 2 s attended, 10 s
# unattended). A scan is a listdir and one stat per job.
SCAN_ATTENDED_SECONDS = 1.0
SCAN_UNATTENDED_SECONDS = 5.0


def finished_row_seconds() -> float:
    from .config import get_observability_config

    return get_observability_config()["finished_row_seconds"]


def enabled() -> bool:
    from .config import get_observability_config

    return bool(get_observability_config().get("background_sessions", True))


class ClaudeBgSource:
    """The background sessions the engine publishes, kept between scans.

    ``refresh`` scans the jobs directory, rebuilds the rows (``build_rows``)
    and reads the live ones' spend when due; ``due`` says when the wake
    loop should call it between ticks (the scan cadence, or a finished
    row's time running out). ``views`` is what the snapshot carries: the
    rows' agent views by row id, rebuilt only from a refresh, so a quiet
    jobs directory publishes the same dicts and sends no delta.
    """

    def __init__(self, directory: Optional[Path] = None, keep_seconds=finished_row_seconds,
                 is_enabled=enabled) -> None:
        self.scanner = JobsScanner(directory)
        self.spend = BgSpend()
        self.keep_seconds = keep_seconds
        self.is_enabled = is_enabled
        self.agents: Tuple[FleetAgent, ...] = ()
        self.rows: List[BgRow] = []
        self.deadline: Optional[float] = None
        self.scanned_at: Optional[float] = None  # monotonic
        self._since: Dict[str, Tuple[tuple, float]] = {}
        self._built_for: Optional[Tuple[FleetAgent, ...]] = None
        self.builds = 0

    def ids(self) -> set:
        return {row.state.session_id for row in self.rows}

    def set_agents(self, agents: Iterable[FleetAgent]) -> None:
        self.agents = tuple(agents)

    def due(self, monotonic_now: float, wall_now: float, attended: bool) -> bool:
        if self.scanned_at is None:
            return True
        every = SCAN_ATTENDED_SECONDS if attended else SCAN_UNATTENDED_SECONDS
        if monotonic_now - self.scanned_at >= every:
            return True
        return self.deadline is not None and wall_now >= self.deadline

    def refresh(self, monotonic_now: float, wall_now: float, stats_every: float) -> None:
        """Scan, rebuild the rows, read spend that is due."""
        self.scanned_at = monotonic_now
        if not self.is_enabled() or not self.agents:
            # No fleet agent: no fleet repo, so nothing could be shown
            self.rows, self.deadline, self._built_for = [], None, None
            self._since.clear()
            return
        jobs_moved = self.scanner.scan()
        stale = (jobs_moved or self._built_for != self.agents
                 or (self.deadline is not None and wall_now >= self.deadline)
                 or any(self.spend.due(row.state.claude_bg["session_id"], wall_now, stats_every,
                                       row.light.ended_at is not None) for row in self.rows))
        if not stale:
            return  # the rows as built stand: nothing to read, nothing to rebuild
        self._built_for = self.agents
        keep = self.keep_seconds()
        rows, deadline = build_rows(self.scanner.jobs, self.agents, wall_now, keep,
                                    self._since, self.spend.values)
        jobs_by_sid = {(job or {}).get("sessionId"): job for job in self.scanner.jobs.values()
                       if isinstance(job, dict)}
        wanted = [(row.state.claude_bg["session_id"],
                   jobs_by_sid.get(row.state.claude_bg["session_id"]) or {},
                   row.light.ended_at is not None) for row in rows]
        if self.spend.sync(wanted, wall_now, stats_every):
            rows, deadline = build_rows(self.scanner.jobs, self.agents, wall_now, keep,
                                        self._since, self.spend.values)
        self.builds += 1
        self.rows, self.deadline = rows, deadline


def fleet_agents(sessions: Iterable[Any], foreign_of=None) -> List[FleetAgent]:
    """This tmux session's live agents as attribution candidates.

    ``foreign_of`` answers an agent name's foreign conversations (the hook
    detector's ``get_foreign_sessions``). Terminated and done agents have
    no repo in use any more.
    """
    out = []
    for s in sessions:
        if getattr(s, "status", None) in ("terminated", "done"):
            continue
        foreign = foreign_of(s.name) if callable(foreign_of) else None
        out.append(FleetAgent(
            id=s.id, name=s.name,
            directory=getattr(s, "start_directory", None),
            conversations=frozenset(x for x in (list(getattr(s, "agent_session_ids", None) or ())
                                                + [getattr(s, "active_agent_session_id", None)])
                                    if isinstance(x, str) and x),
            foreign=frozenset(k for k in (foreign or {}) if isinstance(k, str)),
            repo_name=getattr(s, "repo_name", None),
            branch=getattr(s, "branch", None),
        ))
    return out


# ── The views' side ────────────────────────────────────────────────────


@dataclass(eq=True)
class BackgroundSession(Session):
    """A 🌙 row's Session: never in sessions.json, no tmux window of its own.

    ``claude_bg`` is the view's identity dict (``id`` is the short id the
    claude CLI takes); ``parent_session_id`` nests it under the agent it
    came from.
    """

    kind: str = KIND
    claude_bg: Optional[dict] = None

    @property
    def short_id(self) -> str:
        return str((self.claude_bg or {}).get("id") or "")


def is_background(session: Any) -> bool:
    """Whether ``session`` (a Session, a widget's) is an adopted background session."""
    return getattr(session, "kind", None) == KIND


def session_from_view(view: Mapping[str, Any]):
    """The Session a 🌙 row is drawn from, built from its published view."""
    info = view.get("claude_bg") if isinstance(view.get("claude_bg"), dict) else {}
    return BackgroundSession(
        id=view.get("session_id") or "",
        name=view.get("name") or f"bg-{info.get('id', '')}",
        tmux_session="",
        tmux_window="",
        command=[],
        start_directory=view.get("start_directory"),
        start_time=view.get("start_time") or "",
        repo_name=view.get("repo_name"),
        branch=view.get("branch"),
        model=view.get("model"),
        backend="claude-code",
        parent_session_id=info.get("parent_id"),
        claude_bg=dict(info),
    )


def adopted_key(agents: Mapping[str, Mapping[str, Any]]) -> tuple:
    """What decides the 🌙 rows a view draws: which, their names and parents."""
    out = []
    for sid, view in agents.items():
        if not isinstance(view, Mapping) or view.get("kind") != KIND:
            continue
        info = view.get("claude_bg") if isinstance(view.get("claude_bg"), dict) else {}
        out.append((sid, view.get("name"), info.get("parent_id"), info.get("id"),
                    view.get("start_directory"), view.get("repo_name"), view.get("branch")))
    return tuple(sorted(out, key=lambda t: t[0]))
