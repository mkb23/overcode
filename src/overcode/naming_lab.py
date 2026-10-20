"""
Engine behind the naming lab (#542, docs/design/auto-naming.md §9).

The lab runs a draft of the naming advice (or of the namer's whole prompt)
over the fleet, so advice can be tuned by watching the names it gives: live
agents, or the last N archived ones, whose inputs come from their day-bundle
members. This module is UI-free; the TUI dialog
(``tui_widgets/naming_lab.py``) and ``overcode name lab`` both use it:

    agents = load_live(sm, tmux_session, cfg) | load_archived(sm, tmux_session, cfg, n)
    runner.run(agents, template, advice)       # parallel calls, only what changed
    runner.result(agent, template, advice)     # LabPick: the pick, rejected candidates
    stats(agents, picks)                       # changed, collisions, mean length

A call is keyed by the prompt it renders, so a settled draft costs nothing,
and a draft pinned as A keeps its results while B is edited. Each call's
spend is the namer's: a ledger row under ``overcode`` and the naming daily
cap (``LabSpend``).
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .name_source import is_locked

MAX_WORKERS = 8
DEFAULT_ARCHIVED = 20     # `overcode name lab --archived` without a number
ARCHIVED_IN_LAB = 30      # the TUI lab's archived view

# Short reasons for a rejected candidate, by rule (naming_policy)
_SHORT = {
    "base_pattern": "not a valid name",
    "reserved": "reserved",
    "pattern": "fails the pattern",
    "max_length": "too long",
    "forbid": "forbidden word",
    "require": "fails require",
    "no_repo_prefix": "repeats repo",
    "recent_archived": "recently used",
}


@dataclass
class LabAgent:
    """One agent the draft is tried on, with what its name is checked against."""

    session: Any                         # a Session (live or archived)
    inputs: Any                          # namer.NamerInputs
    ctx: Any                             # naming_policy.NamingContext
    archived: bool = False
    project_advice: str = ""             # the repo's .overcode/naming.md, read once

    @property
    def id(self) -> str:
        return self.session.id

    @property
    def name(self) -> str:
        return self.session.name

    @property
    def source(self) -> str:
        return getattr(self.session, "name_source", "human") or "human"

    @property
    def locked(self) -> bool:
        return is_locked(self.source)

    @property
    def has_inputs(self) -> bool:
        i = self.inputs
        return bool(i.brief or i.prompts or i.pane)

    @property
    def directory(self) -> Optional[str]:
        return self.session.start_directory


@dataclass
class LabPick:
    """One draft's answer for one agent."""

    pick: Optional[str] = None
    candidates: List[str] = field(default_factory=list)
    rejected: List[Tuple[str, str]] = field(default_factory=list)   # (candidate, why)
    keep: bool = False
    why: str = ""
    error: Optional[str] = None
    running: bool = False
    seconds: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0

    @property
    def done(self) -> bool:
        return not self.running and (self.error is not None or bool(self.candidates) or self.keep)

    def result_name(self, agent: LabAgent) -> str:
        """The name the agent would have: the pick, unless it is locked or kept."""
        if agent.locked or self.keep or not self.pick or self.error:
            return agent.name
        return self.pick


def short_reason(failure) -> str:
    if failure.rule == "taken":
        return failure.message           # "taken by agent 'x'"
    return _SHORT.get(failure.rule, failure.message)


def rejected(candidates: Sequence[str], pick: Optional[str], ctx) -> List[Tuple[str, str]]:
    """The candidates passed over before the pick, each with why."""
    from .naming_policy import failures, normalise

    out: List[Tuple[str, str]] = []
    for raw in candidates:
        name = normalise(raw)
        if not name:
            out.append((raw, "not a name"))
            continue
        if name == pick:
            break
        found = failures(name, ctx)
        out.append((name, short_reason(found[0]) if found else "passed over"))
    return out


# ── the agents ────────────────────────────────────────────────────────────


def load_live(session_manager, tmux_session: str, cfg: Mapping,
              pane: Optional[Callable[[Any], str]] = None) -> List[LabAgent]:
    """The live local agents, with the inputs and context the namer would use.

    ``pane(session)`` gives a hookless agent's last lines.
    """
    from .namer import gather_inputs, naming_context, recent_archived

    sessions = [s for s in session_manager.list_sessions() if not getattr(s, "is_remote", False)]
    archived = recent_archived(session_manager)
    out = []
    for s in sessions:
        inputs = gather_inputs(s, tmux_session=s.tmux_session or tmux_session, sessions=sessions,
                               archived=archived, max_length=cfg.get("max_length", 18),
                               pane=(lambda s=s: pane(s)) if pane else None)
        out.append(LabAgent(s, inputs, naming_context(cfg, sessions, archived, s),
                            project_advice=_project_advice(s.start_directory)))
    return out


def _project_advice(directory: Optional[str]) -> str:
    from .naming_policy import load_advice
    return load_advice("", directory, user="")


def load_archived(session_manager, tmux_session: str, cfg: Mapping,
                  limit: int = DEFAULT_ARCHIVED) -> List[LabAgent]:
    """The last ``limit`` archived agents, newest first; inputs from their day bundles.

    They are checked against each other, as if they were the fleet: the lab
    asks how the advice would have named them side by side. An agent whose
    bundle is gone (or never had one) has no inputs, and is shown, not run.
    """
    from .agent_bundle import read_agent
    from .namer import bundle_files, gather_inputs
    from .naming_policy import NamingContext, NamingRules

    live_ids = {s.id for s in session_manager.list_sessions()}
    sessions = []
    try:
        for s in session_manager.iter_archived_sessions(newest_first=True):
            if len(sessions) >= limit:
                break
            if s.id in live_ids or getattr(s, "is_remote", False):
                continue
            sessions.append(s)
    except Exception:
        pass
    rules = NamingRules.from_config(cfg.get("rules") or {})
    out = []
    for s in sessions:
        tmux = s.tmux_session or tmux_session
        pointer = getattr(s, "_bundle", None)
        member = read_agent(tmux, pointer) if isinstance(pointer, dict) else None
        inputs = gather_inputs(s, tmux_session=tmux, sessions=sessions,
                               max_length=cfg.get("max_length", 18), files=bundle_files(member))
        ctx = NamingContext.from_fleet(rules, sessions, repo=s.repo_name, self_id=s.id)
        out.append(LabAgent(s, inputs, ctx, archived=True,
                            project_advice=_project_advice(s.start_directory)))
    return out


# ── spend ─────────────────────────────────────────────────────────────────


class LabSpend:
    """The namer's spend rules for lab calls: the naming daily cap and a ledger row each."""

    def __init__(self, tmux_session: str, ledger, cfg: Mapping,
                 clock: Optional[Callable[[], datetime]] = None):
        from .namer import DailySpend
        from .paths import session_dir
        self.daily = DailySpend(session_dir(tmux_session) / "namer_spend.json")
        self.ledger = ledger
        self.cap = float(cfg.get("cost_cap_per_day", 0.5) or 0.0)
        self._now = clock or datetime.now

    def blocked(self) -> Optional[str]:
        """Why no call may be made now (the cap), or None."""
        if self.daily.today(self._now()) >= self.cap:
            return f"naming cost cap (${self.cap:.2f} a day) reached — calls resume tomorrow"
        return None

    def add(self, suggestion, session) -> None:
        from .namer import record_spend
        now = self._now()
        if suggestion.cost_usd:
            self.daily.add(suggestion.cost_usd, now)
        try:
            record_spend(self.ledger, suggestion, session, now)
        except Exception:
            pass  # the result stands; the ledger row is bookkeeping


# ── the runner ────────────────────────────────────────────────────────────


def full_advice(agent: LabAgent, advice: str) -> str:
    """``advice`` for this agent: a repo's ``.overcode/naming.md`` still follows it."""
    from .summarizer_prompts import DEFAULT_NAMING_ADVICE
    parts = [p for p in (advice.strip(), agent.project_advice) if p]
    return "\n\n".join(parts) or DEFAULT_NAMING_ADVICE


class NamingLabRunner:
    """Runs a draft over agents; thread-safe, one pass at a time.

    ``make_client()`` returns a fresh client (one per call, so concurrent
    calls don't share its token counters); ``spend`` is a ``LabSpend`` (or
    None: nothing counted, nothing capped); ``suggest`` is ``namer.suggest``
    (tests pass a stub).
    """

    def __init__(self, make_client: Callable[[], Any], spend: Optional[LabSpend] = None,
                 suggest: Optional[Callable] = None) -> None:
        if suggest is None:
            from .namer import suggest
        self.make_client = make_client
        self._suggest = suggest
        self.spend = spend
        self._lock = threading.Lock()
        self._busy = False
        self._results: Dict[Tuple[str, int], LabPick] = {}
        self._keys: Dict[Tuple[str, str, str], Tuple[str, int]] = {}
        self.calls = 0
        self.spent_usd = 0.0
        self.blocked: Optional[str] = None

    @property
    def busy(self) -> bool:
        return self._busy

    def key(self, agent: LabAgent, template: str, advice: str) -> Tuple[str, int]:
        """The prompt this draft renders for the agent (memoised: drawn on every key)."""
        memo = (agent.id, template, advice)
        found = self._keys.get(memo)
        if found is None:
            from .namer import render
            found = agent.id, hash(render(agent.inputs, template, full_advice(agent, advice)))
            if len(self._keys) > 4096:
                self._keys.clear()
            self._keys[memo] = found
        return found

    def forget(self) -> None:
        """Drop every result (the agents were read again)."""
        with self._lock:
            self._results.clear()
            self._keys.clear()

    def result(self, agent: LabAgent, template: str, advice: str) -> Optional[LabPick]:
        with self._lock:
            return self._results.get(self.key(agent, template, advice))

    def pending(self, agents: Sequence[LabAgent], template: str, advice: str) -> List[LabAgent]:
        """The agents this draft has no result (or a failed one) for."""
        out = []
        with self._lock:
            for agent in agents:
                if not agent.has_inputs:
                    continue
                r = self._results.get(self.key(agent, template, advice))
                if r is None or (r.error and not r.running):
                    out.append(agent)
        return out

    def run(self, agents: Sequence[LabAgent], template: str, advice: str,
            on_update: Optional[Callable[[], None]] = None) -> int:
        """One pass: call the model for every agent whose prompt has no result yet,
        in parallel, calling ``on_update`` as each lands. Returns the calls made;
        0 (without waiting) when a pass is in flight. Blocking: call it from a thread."""
        with self._lock:
            if self._busy:
                return 0
            self._busy = True
        try:
            todo = self.pending(agents, template, advice)
            if not todo:
                return 0
            self.blocked = self.spend.blocked() if self.spend is not None else None
            if self.blocked:
                if on_update:
                    on_update()
                return 0
            with self._lock:
                for agent in todo:
                    self._results[self.key(agent, template, advice)] = LabPick(running=True)
            if on_update:
                on_update()
            with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(todo))) as pool:
                for agent in todo:
                    pool.submit(self._call, agent, template, advice, on_update)
            return len(todo)
        finally:
            with self._lock:
                self._busy = False

    def _call(self, agent: LabAgent, template: str, advice: str, on_update) -> None:
        key = self.key(agent, template, advice)
        start = time.monotonic()
        try:
            s = self._suggest(agent.inputs, agent.ctx, client=self.make_client(),
                              template=template, advice=full_advice(agent, advice))
        except Exception as e:  # a bug costs this row, never the lab
            from .namer import Suggestion
            s = Suggestion(error=f"lab call failed: {e}")
        pick = LabPick(
            pick=s.pick, candidates=list(s.candidates), keep=s.keep, why=s.why, error=s.error,
            rejected=rejected(s.candidates, s.pick, agent.ctx),
            seconds=time.monotonic() - start, tokens_in=s.tokens_in, tokens_out=s.tokens_out,
            cost_usd=s.cost_usd,
        )
        with self._lock:
            self.calls += 1
            self.spent_usd += s.cost_usd
            self._results[key] = pick
        if self.spend is not None:
            self.spend.add(s, agent.session)
        if on_update:
            on_update()


# ── comparing drafts ──────────────────────────────────────────────────────


@dataclass
class DraftStats:
    changed: int = 0          # agents the draft would rename (unlocked, a new pick)
    collisions: int = 0       # agents whose resulting name another also gets
    mean_length: float = 0.0  # of the names the draft would leave
    done: int = 0             # agents with an answer


def collided(agents: Sequence[LabAgent], picks: Sequence[Optional[LabPick]]) -> set:
    """Ids of agents whose pick another agent's pick matches (each was chosen alone)."""
    mine = [p.pick if p is not None and p.pick and not p.error else None for p in picks]
    counts = Counter(n for n in mine if n)
    return {a.id for a, n in zip(agents, mine) if n and counts[n] > 1}


def stats(agents: Sequence[LabAgent], picks: Sequence[Optional[LabPick]]) -> DraftStats:
    out = DraftStats()
    names = []
    for agent, p in zip(agents, picks):
        if p is not None and p.done:
            out.done += 1
        name = p.result_name(agent) if p is not None else agent.name
        if name != agent.name:
            out.changed += 1
        names.append(name)
    out.collisions = len(collided(agents, picks))
    out.mean_length = sum(len(n) for n in names) / len(names) if names else 0.0
    return out
