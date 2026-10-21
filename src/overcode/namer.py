"""
The auto-namer (#542, docs/design/auto-naming.md §3-5, §7-8).

The daemon names agents, because it owns per-agent values (AGENTS.md). It
works from turn ends (an agent going from a green status to waiting), adds
no polling, and calls the model on one worker thread, never in the tick:

    Namer.on_tick(sessions, states, now)    # each tick: turn ends, Claude titles
      due(...)                              # §3: is a name due at this Stop?
      worker: gather_inputs -> suggest -> naming_policy.choose -> rename(source="auto")

``suggest`` is the same path for one agent without the rename
(``overcode name suggest``; the naming lab builds on it).

Off unless ``naming.enabled``. A name whose source is ``human`` or
``parent`` is never renamed. Spend goes in the ledger under the billing
code ``overcode``, and stops for the day at ``naming.cost_cap_per_day``
(kept per tmux session, in ``namer_spend.json`` beside the daemon state).

Claude Code keeps a session name of its own (§7): the launch passes
``--name``, a rename is typed in as ``/rename <new>`` when the agent is idle
with an empty input line (at its next Stop, or on a tick while it sits idle
after a rename from the TUI or CLI; never forced), and a
``/rename`` typed in Claude (a new ``custom-title`` in its transcript) is
taken as a human rename. That part runs whether or not the namer is on
(``naming.cli_title``, default on).

Per agent, ``agents/<id>/namer.json`` keeps the turn count, the renames
done, and what Claude's title is believed to be.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .name_source import AUTO, DEFAULT, HUMAN, is_locked
from .status_constants import (
    GREEN_STATUSES,
    STATUS_ASLEEP,
    STATUS_DONE,
    STATUS_WAITING_APPROVAL,
    STATUS_WAITING_HEARTBEAT,
    STATUS_WAITING_OVERSIGHT,
    STATUS_WAITING_USER,
)

# A turn has ended when an agent goes from green to one of these
# (waiting_approval is a turn still in progress: never a Stop)
TURN_END_STATUSES = frozenset({STATUS_WAITING_USER, STATUS_WAITING_HEARTBEAT,
                               STATUS_WAITING_OVERSIGHT, STATUS_DONE})
# An agent at its prompt, between turns: its lagging title is typed in on a
# tick, not only at a Stop (asleep: snoozed, or the last idle stage)
IDLE_STATUSES = TURN_END_STATUSES | {STATUS_ASLEEP}

MIN_RENAME_GAP_SECONDS = 600     # auto names change at least this far apart
RETRY_GAP_SECONDS = 600          # after a call that failed or was skipped
REFINE_FACTOR = 10               # the refinement comes at 10x the first's work
HUMAN_QUIET_SECONDS = 30         # no rename this soon after a TUI keypress
TITLE_RETRY_SECONDS = 10         # an idle agent's lagging title is tried again this often
FIRST_AFTER_MINUTES = 5.0        # the first name, this long after the first prompt
FIRST_TRIES = 3                  # first-name calls a "keep" answer may use up
FIRST_RECHECK_SECONDS = 60       # an agent waiting for its first name is looked at this often
# The one refinement: this much work (turns, and green minutes or output tokens)
REFINE_AFTER = {"turns": 20, "green_minutes": 30, "output_tokens": 150000}
ARCHIVED_HOURS = 24              # archived agents the model sees
FLEET_LINES = 120                # at most this many fleet lines in the prompt
PANE_LINES = 60                  # a hookless agent's fallback input
TOP_FILES = 6
CALL_TIMEOUT = 20.0
MAX_TOKENS = 250

# The billing code namer spend goes under (codes registry, by name)
NAMER_BILLING_CODE = "overcode"

_EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit", "edit", "write", "patch"})


# ── per-agent state ───────────────────────────────────────────────────────


@dataclass
class NamerState:
    """``agents/<id>/namer.json``: what the namer has done for one agent."""

    turns: int = 0                      # turn ends seen
    renames: int = 0                    # auto renames made
    first_done: bool = False
    refine_done: bool = False
    last_rename_at: float = 0.0
    attempted_at: float = 0.0           # the last model call (or a skipped one)
    first_tries: int = 0                # first-name calls the model answered "keep"
    cli_title: Optional[str] = None     # what the CLI's own session name is
    seen_title: Optional[str] = None    # the last custom-title acted on

    @classmethod
    def from_dict(cls, data: Mapping) -> "NamerState":
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in data.items() if k in known})


def _state_path(tmux_session: str, session_id: str) -> Path:
    from .paths import agent_file
    return agent_file(tmux_session, session_id, "namer")


def load_state(tmux_session: str, session_id: str) -> NamerState:
    try:
        data = json.loads(_state_path(tmux_session, session_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return NamerState()
    return NamerState.from_dict(data) if isinstance(data, dict) else NamerState()


def save_state(tmux_session: str, session_id: str, state: NamerState) -> None:
    """Write the state atomically; only into an agent directory that exists."""
    path = _state_path(tmux_session, session_id)
    if not path.parent.is_dir():
        return
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(asdict(state)), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)


# ── when to name (§3) ─────────────────────────────────────────────────────


def first_due(state: NamerState, *, source: Optional[str], task_since: Optional[float],
              green_seconds: float, cfg: Mapping, now: float) -> bool:
    """Whether an agent's first name is due: ``first_after.minutes`` after its
    first prompt (``task_since``), once it has worked at all.

    An agent never given a task, or one that has not started on it, is not
    named; neither is a locked name, nor one named already. A call that
    failed, or that the model answered "keep" (``FIRST_TRIES`` at most), is
    tried again ``RETRY_GAP_SECONDS`` later.
    """
    if is_locked(source) or state.first_done or state.renames:
        return False
    if task_since is None or green_seconds <= 0:
        return False
    if state.attempted_at and now - state.attempted_at < RETRY_GAP_SECONDS:
        return False
    minutes = float((cfg.get("first_after") or {}).get("minutes", FIRST_AFTER_MINUTES) or 0)
    return now >= task_since + minutes * 60


def due(state: NamerState, *, source: Optional[str], turns: int, green_seconds: float,
        output_tokens: int, cfg: Mapping, now: float) -> Optional[str]:
    """``"refine"`` when the one refinement is due at this Stop, else None.

    After the first name (``first_due``), once the agent has done
    ``REFINE_AFTER`` work (turns, and green minutes or output tokens); at
    most ``max_renames`` auto renames, ``MIN_RENAME_GAP`` apart; never a
    locked name.
    """
    if is_locked(source) or not state.first_done:
        return None
    if state.renames >= cfg.get("max_renames", 2) or state.refine_done:
        return None
    if state.last_rename_at and now - state.last_rename_at < MIN_RENAME_GAP_SECONDS:
        return None
    if state.attempted_at and now - state.attempted_at < RETRY_GAP_SECONDS:
        return None
    if turns < REFINE_AFTER["turns"]:
        return None
    if (green_seconds >= REFINE_AFTER["green_minutes"] * 60
            or output_tokens >= REFINE_AFTER["output_tokens"]):
        return "refine"
    return None


def first_task_at(session, tmux_session: str) -> Optional[float]:
    """When the agent was given its task (epoch seconds), or None if never.

    Its first recorded prompt (``prompts.jsonl``); else, for an agent
    launched with a prompt (a ``brief``), its start.
    """
    files = live_files(tmux_session, session.id)
    for line in files("prompts").splitlines():
        try:
            t = json.loads(line).get("t")
        except (ValueError, AttributeError):
            continue
        if isinstance(t, (int, float)):
            return float(t)
    if files("brief").strip():
        from .clock import to_epoch
        return to_epoch(getattr(session, "start_time", None))
    return None


def hand_to_namer(session_manager, session, tmux_session: str) -> None:
    """"Let overcode name it": the name becomes a placeholder overcode may
    replace (``default``: no ``~``, since overcode did not choose it), and
    the agent's naming history is cleared so it gets a first name again."""
    session_manager.update_session(session.id, name_source=DEFAULT)
    tmux_session = session.tmux_session or tmux_session
    state = load_state(tmux_session, session.id)
    state.first_done = state.refine_done = False
    state.renames = state.first_tries = 0
    state.attempted_at = state.last_rename_at = 0.0
    save_state(tmux_session, session.id, state)


# ── what the namer reads (§4) ─────────────────────────────────────────────


@dataclass
class FleetEntry:
    name: str
    source: str = "default"
    repo: Optional[str] = None
    parent: Optional[str] = None        # the parent's name
    parent_id: Optional[str] = None
    archived: bool = False


@dataclass
class NamerInputs:
    """Everything the prompt is built from, for one agent."""

    session_id: str
    name: str
    name_source: str
    brief: str = ""
    prompts: List[str] = field(default_factory=list)
    pane: str = ""                      # a hookless agent's last lines
    repo: Optional[str] = None
    branch: Optional[str] = None
    pr: Optional[int] = None
    files: List[str] = field(default_factory=list)
    parent: Optional[str] = None
    parent_id: Optional[str] = None
    fleet: List[FleetEntry] = field(default_factory=list)
    max_length: int = 18
    directory: Optional[str] = None

    @property
    def siblings(self) -> List[str]:
        if not self.parent_id:
            return []
        return [e.name for e in self.fleet if e.parent_id == self.parent_id and not e.archived]


def live_files(tmux_session: str, session_id: str) -> Callable[[str], str]:
    """``kind -> text`` of a live agent's files (``paths.AGENT_DIR_FILES``); "" if unreadable."""
    from .paths import agent_file

    def read(kind: str) -> str:
        try:
            return agent_file(tmux_session, session_id, kind).read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            return ""
    return read


def bundle_files(member: Optional[Mapping]) -> Callable[[str], str]:
    """``kind -> text`` of an archived agent's files, from its day-bundle member."""
    from .paths import AGENT_DIR_FILES
    files = (member or {}).get("files") or {}

    def read(kind: str) -> str:
        value = files.get(AGENT_DIR_FILES.get(kind, kind)) if isinstance(files, dict) else None
        return value if isinstance(value, str) else ""
    return read


def parse_prompts(text: str) -> List[str]:
    """The prompts in a ``prompts.jsonl`` text."""
    out: List[str] = []
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        prompt = entry.get("prompt") if isinstance(entry, dict) else None
        if isinstance(prompt, str) and prompt.strip():
            out.append(prompt.strip())
    return out


def read_prompts(tmux_session: str, session_id: str) -> List[str]:
    """The agent's first prompts (``prompts.jsonl``, written by the hook handler)."""
    return parse_prompts(live_files(tmux_session, session_id)("prompts"))


def edited_files(lines: Iterable[str], directory: Optional[str] = None,
                 limit: int = TOP_FILES) -> List[str]:
    """The files a hook event log shows the agent editing most, most first."""
    from collections import Counter
    counts: Counter = Counter()
    for line in lines:
        if '"file_path"' not in line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("tool_name") not in _EDIT_TOOLS:
            continue
        path = (entry.get("tool_input") or {}).get("file_path")
        if isinstance(path, str) and path:
            counts[path] += 1
    out = []
    for path, _ in counts.most_common(limit):
        if directory and path.startswith(directory.rstrip("/") + "/"):
            path = path[len(directory.rstrip("/")) + 1:]
        out.append(path)
    return out


def most_edited_files(tmux_session: str, session_id: str, directory: Optional[str] = None,
                      limit: int = TOP_FILES) -> List[str]:
    """The files the agent's hook events show it editing most, most first."""
    from .paths import agent_file
    try:
        with open(agent_file(tmux_session, session_id, "hook_events"), encoding="utf-8",
                  errors="replace") as f:
            return edited_files(f, directory, limit)
    except OSError:
        return []


def recent_archived(session_manager, now: Optional[datetime] = None,
                    hours: float = ARCHIVED_HOURS) -> List[dict]:
    """Archive records of agents that ended in the last ``hours`` (newest first)."""
    now = now or datetime.now()
    cutoff = now - timedelta(hours=hours)
    out: List[dict] = []
    try:
        records = session_manager.iter_archived_records(cutoff, newest_first=True)
        stale = 0
        for record in records:
            when = record.get("end_time") or record.get("start_time")
            try:
                ended = datetime.fromisoformat(when) if isinstance(when, str) else None
            except ValueError:
                ended = None
            if ended is not None and ended.tzinfo is not None:
                ended = ended.astimezone().replace(tzinfo=None)
            if ended is None or ended < cutoff:
                stale += 1
                if stale > 50:   # filed by archive time: the rest are older
                    break
                continue
            stale = 0
            out.append(record)
    except Exception:
        return out
    return out


def fleet_entries(sessions: Iterable, archived: Iterable[Mapping] = (),
                  exclude_id: Optional[str] = None) -> List[FleetEntry]:
    """The fleet the model sees: live local agents, then recently archived ones.

    Sister agents are left out (no sister awareness).
    """
    live = [s for s in sessions if not getattr(s, "is_remote", False)]
    names = {s.id: s.name for s in live}
    archived = [r for r in archived if isinstance(r.get("name"), str)]
    for record in archived:
        names.setdefault(record.get("id"), record["name"])
    out: List[FleetEntry] = []
    for s in live:
        if s.id == exclude_id:
            continue
        out.append(FleetEntry(name=s.name, source=getattr(s, "name_source", "human"),
                              repo=s.repo_name, parent=names.get(s.parent_session_id),
                              parent_id=s.parent_session_id))
    live_ids = {s.id for s in live}
    for record in archived:
        if record.get("id") in live_ids or record.get("id") == exclude_id:
            continue
        out.append(FleetEntry(name=record["name"], source=record.get("name_source") or "human",
                              repo=record.get("repo_name"),
                              parent=names.get(record.get("parent_session_id")),
                              parent_id=record.get("parent_session_id"), archived=True))
    return out


def gather_inputs(session, *, tmux_session: str, sessions: Sequence, archived: Sequence = (),
                  max_length: int = 18, pane: Optional[Callable[[], str]] = None,
                  files: Optional[Callable[[str], str]] = None) -> NamerInputs:
    """The namer's inputs for ``session`` (a Session), from its files and the fleet.

    ``files`` reads the agent's files by kind (default: its live directory;
    ``bundle_files`` for an archived agent). ``pane`` gives the agent's last
    lines; read only when it has neither a brief nor a captured prompt (a
    hookless agent).
    """
    sid = session.id
    by_id = {s.id: s for s in sessions}
    parent = by_id.get(session.parent_session_id) if session.parent_session_id else None
    parent_name = parent.name if parent is not None else None
    if parent_name is None and session.parent_session_id:
        parent_name = next((r.get("name") for r in archived
                            if r.get("id") == session.parent_session_id), None)
    if files is None:
        from .agent_files import read_brief
        brief = read_brief(tmux_session, sid)
        prompts = read_prompts(tmux_session, sid)
        edited = most_edited_files(tmux_session, sid, session.start_directory)
    else:
        brief = files("brief")
        prompts = parse_prompts(files("prompts"))
        edited = edited_files(files("hook_events").splitlines(), session.start_directory)
    inputs = NamerInputs(
        session_id=sid, name=session.name,
        name_source=getattr(session, "name_source", "human"),
        brief=brief.strip(), prompts=prompts,
        repo=session.repo_name, branch=session.branch, pr=session.pr_number,
        files=edited,
        parent=parent_name, parent_id=session.parent_session_id,
        fleet=fleet_entries(sessions, archived, exclude_id=sid),
        max_length=max_length, directory=session.start_directory,
    )
    if not inputs.brief and not inputs.prompts and pane is not None:
        try:
            text = pane() or ""
        except Exception:
            text = ""
        inputs.pane = "\n".join(text.rstrip().splitlines()[-PANE_LINES:])
    return inputs


# ── the prompt and the reply (§5, §6) ─────────────────────────────────────


def _fleet_text(entries: Sequence[FleetEntry]) -> str:
    """One line per agent, siblings together (grouped by parent)."""
    ordered = sorted(entries, key=lambda e: (e.archived, e.parent or e.name, e.parent is not None,
                                             e.name))
    lines = []
    for e in ordered[:FLEET_LINES]:
        details = ([f"repo {e.repo}"] if e.repo else []) + (
            [f"child of {e.parent}"] if e.parent else [])
        line = f"- {e.name} [{e.source}]"
        if details:
            line += " " + ", ".join(details)
        lines.append(line + (" (ended)" if e.archived else ""))
    if len(ordered) > FLEET_LINES:
        lines.append(f"... and {len(ordered) - FLEET_LINES} more")
    return "\n".join(lines)


def render(inputs: NamerInputs, template: str, advice: str) -> str:
    """The model's prompt: ``template`` with every placeholder filled."""
    from .summarizer_prompts import render_naming_prompt

    prompts = "\n".join(f"{i}. {p}" for i, p in enumerate(inputs.prompts, 1))
    if not prompts and inputs.pane:
        prompts = f"(none recorded; the last lines of its terminal:)\n{inputs.pane}"
    branch = inputs.branch or ""
    if inputs.pr:
        branch = f"{branch} (PR #{inputs.pr})".strip()
    return render_naming_prompt(
        template,
        advice=advice,
        brief=inputs.brief,
        prompts=prompts,
        repo=inputs.repo,
        branch=branch,
        files=", ".join(inputs.files),
        current_name=inputs.name,
        name_source=inputs.name_source,
        parent=inputs.parent,
        fleet=_fleet_text(inputs.fleet),
        siblings=", ".join(inputs.siblings),
        max_length=str(inputs.max_length),
    )


@dataclass
class Reply:
    candidates: List[str]
    keep: bool = False
    why: str = ""


def parse_reply(text: Optional[str]) -> Optional[Reply]:
    """The model's ``{"candidates": [...], "keep": bool, "why": str}``; None if unreadable.

    Lenient: a code fence or prose around the object, a single string for
    ``candidates``, and ``"true"`` for ``keep`` are all accepted.
    """
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("candidates")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raw = []
    candidates = [c.strip() for c in raw if isinstance(c, str) and c.strip()][:8]
    keep = data.get("keep")
    keep = keep is True or (isinstance(keep, str) and keep.strip().lower() == "true")
    why = data.get("why") if isinstance(data.get("why"), str) else ""
    if not candidates and not keep:
        return None
    return Reply(candidates=candidates, keep=keep, why=why.strip()[:300])


@dataclass
class Suggestion:
    """One namer call: the model's reply, overcode's pick, and what it cost."""

    candidates: List[str] = field(default_factory=list)
    keep: bool = False
    why: str = ""
    pick: Optional[str] = None
    error: Optional[str] = None
    model: Optional[str] = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    prompt: str = ""

    def to_dict(self) -> dict:
        out = asdict(self)
        out.pop("prompt")
        return out


def suggest(inputs: NamerInputs, ctx, *, client, template: str, advice: str,
            timeout: float = CALL_TIMEOUT) -> Suggestion:
    """Ask the model for names and pick one with ``naming_policy.choose`` (no rename)."""
    from .naming_policy import choose

    prompt = render(inputs, template, advice)
    out = Suggestion(prompt=prompt, model=getattr(client, "model", None))
    if client is None or not getattr(client, "available", False):
        out.error = "no model: set summarizer: (or naming.provider) and its API key"
        return out
    try:
        text = client.complete(prompt, max_tokens=MAX_TOKENS, timeout=timeout)
    except Exception as e:  # a client bug costs this call, never the daemon
        text, out.error = None, f"model call failed: {e}"
    out.tokens_in = int(getattr(client, "last_input_tokens", 0) or 0)
    out.tokens_out = int(getattr(client, "last_output_tokens", 0) or 0)
    out.cost_usd = call_cost(out.model, out.tokens_in, out.tokens_out)
    if text is None:
        out.error = out.error or "no reply from the model"
        return out
    reply = parse_reply(text)
    if reply is None:
        out.error = "unreadable reply from the model"
        return out
    out.candidates, out.keep, out.why = reply.candidates, reply.keep, reply.why
    out.pick = choose(reply.candidates, ctx) if reply.candidates else None
    return out


def call_cost(model: Optional[str], tokens_in: int, tokens_out: int) -> float:
    if not model or not (tokens_in or tokens_out):
        return 0.0
    try:
        from .pricing import estimate_cost
        return float(estimate_cost(model, tokens_in, tokens_out))
    except Exception:
        return 0.0


def make_client(provider: Optional[Mapping] = None):
    """The model client: ``naming.provider`` over the ``summarizer:`` block."""
    from .summarizer_client import SummarizerClient
    return SummarizerClient(overrides=dict(provider) if provider else None)


def advice_for(cfg: Mapping, directory: Optional[str]) -> str:
    """The naming advice for an agent in ``directory`` (config, then the repo's file)."""
    from .naming_policy import load_advice
    from .summarizer_prompts import DEFAULT_NAMING_ADVICE
    return load_advice(cfg.get("advice") or "", directory) or DEFAULT_NAMING_ADVICE


def naming_context(cfg: Mapping, sessions: Sequence, archived: Sequence, session):
    """What a pick is checked against: the rules, live names and aliases, but not the agent's own."""
    from .naming_policy import NamingContext, NamingRules
    return NamingContext.from_fleet(NamingRules.from_config(cfg.get("rules") or {}), sessions,
                                    archived=archived, repo=session.repo_name, self_id=session.id)


# ── spend (§3) ────────────────────────────────────────────────────────────


def ledger_row(suggestion: Suggestion, session, code_id: Optional[str],
               now: Optional[datetime] = None) -> dict:
    """A ledger row for one call: a closed segment of its own, billed to ``overcode``."""
    import uuid
    from .ledger import make_row

    record = {
        "id": f"namer:{uuid.uuid4().hex[:12]}",
        "name": f"namer:{session.name}",
        "parent_session_id": session.id,
        "model": suggestion.model,
        "stats": {"estimated_cost_usd": suggestion.cost_usd,
                  "input_tokens": suggestion.tokens_in, "output_tokens": suggestion.tokens_out,
                  "total_tokens": suggestion.tokens_in + suggestion.tokens_out},
    }
    row = make_row(record, "final", now=now, code=code_id, billing_code=NAMER_BILLING_CODE)
    row["namer"] = True
    return row


def record_spend(ledger, suggestion: Suggestion, session,
                 now: Optional[datetime] = None) -> Optional[dict]:
    """Write a call's spend to the ledger under ``overcode``; the row, or None (no spend)."""
    if ledger is None or not (suggestion.cost_usd or suggestion.tokens_in or suggestion.tokens_out):
        return None
    try:
        code_id = ledger.codes.resolve(NAMER_BILLING_CODE)
    except Exception:
        code_id = None
    row = ledger_row(suggestion, session, code_id, now)
    ledger.append([row], now=now)
    return row


class DailySpend:
    """The namer's spend today, in ``<session dir>/namer_spend.json`` (the cost cap)."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def _read(self, day: str) -> float:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0.0
        if not isinstance(data, dict) or data.get("day") != day:
            return 0.0
        try:
            return float(data.get("usd") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def today(self, now: datetime) -> float:
        return self._read(now.strftime("%Y-%m-%d"))

    def add(self, usd: float, now: datetime) -> float:
        day = now.strftime("%Y-%m-%d")
        with self._lock:
            total = self._read(day) + max(0.0, usd)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
                tmp.write_text(json.dumps({"day": day, "usd": round(total, 6)}), encoding="utf-8")
                os.replace(tmp, self.path)
            except OSError:
                pass
        return total


# ── Claude's own session name (§7) ────────────────────────────────────────


def input_line_empty(pane: str, backend) -> bool:
    """Whether the CLI's live input line is drawn empty (the launcher's ready-line check).

    The live input is the last line starting with a prompt glyph; earlier
    ones are scrollback.
    """
    from .status_patterns import strip_ansi

    glyphs = backend.prompt_ready_chars()
    last = None
    for raw in pane.splitlines():
        line = strip_ansi(raw).strip()
        if line and line[0] in glyphs:
            last = line
    return last is not None and backend.prompt_ready_line(last)


def title_to_adopt(title: Optional[str], session, state: NamerState) -> Optional[str]:
    """The name to adopt from a CLI title the user set (``/rename``), or None.

    Ignored: a title already acted on, the agent's name or one of its old
    names (overcode set those: ``--name``, an alignment), and the title
    overcode last typed. A title that is not a valid name is normalised.
    """
    from .naming_policy import normalise

    if not title or title == state.seen_title:
        return None
    if title == session.name or title in (session.previous_names or ()) or title == state.cli_title:
        return None
    name = normalise(title)
    if not name or name == session.name:
        return None
    return name


# ── the daemon's namer ────────────────────────────────────────────────────


def _tui_keypress_age(tmux_session: str) -> Optional[float]:
    """Seconds since the TUI last saw a key (its heartbeat file), None if never."""
    import time
    from .settings import get_tui_heartbeat_path
    try:
        return max(0.0, time.time() - get_tui_heartbeat_path(tmux_session).stat().st_mtime)
    except OSError:
        return None


class Namer:
    """The daemon's auto-namer and Claude-title keeper (see the module docstring).

    ``on_tick`` runs in the daemon's tick and only compares statuses and
    reads small per-agent files at a turn end; everything that calls a
    model, renames or touches a pane runs in ``submit`` (one worker thread
    by default, so two agents never rename at once from one daemon).
    Tests inject ``submit`` (run at once), ``rename``, ``client_factory``,
    ``config`` and ``keypress_age``.
    """

    def __init__(self, tmux_session: str, session_manager, *, tmux=None, log=None,
                 clock=None, titles: Optional[Callable[[], Mapping[str, str]]] = None,
                 rename: Optional[Callable] = None, client_factory: Optional[Callable] = None,
                 submit: Optional[Callable] = None, config: Optional[Callable[[], dict]] = None,
                 keypress_age: Optional[Callable[[], Optional[float]]] = None,
                 spend_path: Optional[Path] = None):
        from .clock import SYSTEM_CLOCK
        self.tmux_session = tmux_session
        self.session_manager = session_manager
        self.tmux = tmux
        self.log = log
        self.clock = clock if clock is not None else SYSTEM_CLOCK
        self._titles = titles or (lambda: {})
        self._rename = rename or self._launcher_rename
        self._client_factory = client_factory or make_client
        self._submit = submit or self._pool_submit
        self._config = config
        self._keypress_age = keypress_age or (lambda: _tui_keypress_age(tmux_session))
        if spend_path is None:
            from .paths import session_dir
            spend_path = session_dir(tmux_session) / "namer_spend.json"
        self.spend = DailySpend(spend_path)
        self._pool = None
        self._last_status: Dict[str, str] = {}
        self._title_seen: Dict[str, str] = {}   # the CLI title last looked at, per id
        # Per id, the name an idle agent's title was last checked against, and
        # when to check again (None: settled until the name changes)
        self._idle_title: Dict[str, Tuple[str, Optional[float]]] = {}
        # Per id, the name and the CLI title recorded for it (title_lag)
        self._titles_known: Dict[str, Tuple[str, Optional[str]]] = {}
        # Per id, when to look again at an agent for its first name, and
        # when it was given its task (first_task_at; it never changes)
        self._first_next: Dict[str, float] = {}
        self._task_at: Dict[str, float] = {}
        self._in_flight: set = set()
        self._lock = threading.Lock()
        self._cap_logged_day: Optional[str] = None
        self._no_client_logged = False

    # ── plumbing ──────────────────────────────────────────────────────

    def config(self) -> dict:
        if self._config is not None:
            return self._config()
        from .config import get_naming_config
        return get_naming_config()

    def _pool_submit(self, fn: Callable[[], None]) -> None:
        if self._pool is None:
            from concurrent.futures import ThreadPoolExecutor
            self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="namer")
        self._pool.submit(fn)

    def shutdown(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False)

    def _info(self, message: str) -> None:
        if self.log is not None:
            self.log.info(message)

    def _warn(self, message: str) -> None:
        if self.log is not None:
            self.log.warn(message)

    def _launcher_rename(self, session, new_name: str, source: str) -> bool:
        from .launcher import AgentLauncher
        from .tmux_manager import TmuxManager
        tmux_session = session.tmux_session or self.tmux_session
        manager = TmuxManager(tmux_session, tmux=self.tmux) if self.tmux is not None else None
        launcher = AgentLauncher(tmux_session, tmux_manager=manager,
                                 session_manager=self.session_manager)
        return launcher.rename(session, new_name, source=source)

    def _run(self, key: str, fn: Callable[[], None]) -> None:
        """Run ``fn`` on the worker unless a job for ``key`` is already queued."""
        with self._lock:
            if key in self._in_flight:
                return
            self._in_flight.add(key)

        def job():
            try:
                fn()
            except Exception as e:  # never kill the worker
                self._warn(f"namer: {key}: {type(e).__name__}: {e}")
            finally:
                with self._lock:
                    self._in_flight.discard(key)
        self._submit(job)

    def _human_typing(self) -> bool:
        age = self._keypress_age()
        return age is not None and age < HUMAN_QUIET_SECONDS

    # ── the tick ──────────────────────────────────────────────────────

    def on_tick(self, sessions: Sequence, states: Sequence, now: datetime) -> None:
        """Act on this tick's turn ends and CLI titles (cheap; the work goes to the worker)."""
        by_id = {s.id: s for s in sessions}
        titles = self._titles()
        cfg = None
        seen = set()
        for st in states:
            sid = getattr(st, "session_id", None)
            session = by_id.get(sid)
            if session is None or getattr(session, "is_remote", False):
                continue
            seen.add(sid)
            status = st.current_status
            previous = self._last_status.get(sid)
            self._last_status[sid] = status
            title = titles.get(sid)
            if title is not None and self._title_seen.get(sid) == title:
                title = None  # acted on already
            elif title is not None:
                self._title_seen[sid] = title
            turn_end = previous in GREEN_STATUSES and status in TURN_END_STATUSES
            if (not turn_end and status in IDLE_STATUSES
                    and self._idle_title_due(session, now.timestamp())):
                if cfg is None:
                    cfg = self.config()
                if cfg.get("cli_title", True) and self._is_claude(session):
                    self._run(f"align:{sid}", lambda sid=sid: self._align(sid))
            if float(getattr(st, "green_time_seconds", 0) or 0) > 0:
                if cfg is None:
                    cfg = self.config()
                if self._first_name_due(session, st, cfg, now):
                    self._run(f"name:{sid}", lambda sid=sid, cfg=cfg: self._name(sid, "first", cfg))
            if not turn_end and not title:
                continue
            if cfg is None:
                cfg = self.config()
            if title and cfg.get("cli_title", True):
                self._consider_title(session, title)
            if turn_end:
                self._on_stop(session, st, cfg, now)
        for sid in list(self._last_status):
            if sid not in seen:
                del self._last_status[sid]
                self._title_seen.pop(sid, None)
                self._idle_title.pop(sid, None)
                self._titles_known.pop(sid, None)
                self._first_next.pop(sid, None)
                self._task_at.pop(sid, None)

    def _first_name_due(self, session, st, cfg: dict, now: datetime) -> bool:
        """Whether an agent gets its first name on this tick (``first_due``).

        Checked on any tick, mid-turn or idle, for an agent that has worked
        at all. Its files are read at most every ``FIRST_RECHECK_SECONDS``
        (once its task time is known, not before the name is due), so a
        hand-back from the TUI or CLI (``hand_to_namer``) is seen within a
        minute.
        """
        if not cfg.get("enabled") or is_locked(getattr(session, "name_source", HUMAN)):
            return False
        ts = now.timestamp()
        if ts < self._first_next.get(session.id, 0.0):
            return False
        self._first_next[session.id] = ts + FIRST_RECHECK_SECONDS
        tmux_session = session.tmux_session or self.tmux_session
        state = load_state(tmux_session, session.id)
        if state.first_done or state.renames:
            return False
        since = self._task_at.get(session.id)
        if since is None:
            since = first_task_at(session, tmux_session)
            if since is None:
                return False
            self._task_at[session.id] = since
        if not first_due(state, source=getattr(session, "name_source", HUMAN), task_since=since,
                         green_seconds=float(getattr(st, "green_time_seconds", 0) or 0),
                         cfg=cfg, now=ts):
            minutes = float((cfg.get("first_after") or {}).get("minutes", FIRST_AFTER_MINUTES) or 0)
            due_at = since + minutes * 60
            if state.attempted_at:
                due_at = max(due_at, state.attempted_at + RETRY_GAP_SECONDS)
            self._first_next[session.id] = max(ts + 1, min(due_at, ts + FIRST_RECHECK_SECONDS))
            return False
        return not self._human_typing() and not self._over_cap(cfg, now)

    def title_lag(self, session) -> Optional[str]:
        """The title Claude still shows for an agent renamed since, else None.

        Published as ``cli_title_lag``: views mark the name until ``_align``
        types the new one in. Read from ``namer.json`` when the agent's name
        changes, kept in memory between.
        """
        if not self._is_claude(session) or not self.config().get("cli_title", True):
            return None
        known = self._titles_known.get(session.id)
        if known is None or known[0] != session.name:
            title = load_state(session.tmux_session or self.tmux_session, session.id).cli_title
            known = self._titles_known[session.id] = (session.name, title)
        title = known[1]
        return title if title is not None and title != session.name else None

    def _idle_title_due(self, session, now: float) -> bool:
        """Whether to check an idle agent's title against its name now (a rename made while it sat idle).

        Once per name, then every ``TITLE_RETRY_SECONDS`` while ``_align``
        could not type it in; ``_align`` settles it.
        """
        seen = self._idle_title.get(session.id)
        if seen is not None and seen[0] == session.name and (
                seen[1] is None or now < seen[1]):
            return False
        self._idle_title[session.id] = (session.name, now + TITLE_RETRY_SECONDS)
        return True

    def _on_stop(self, session, st, cfg: dict, now: datetime) -> None:
        tmux_session = session.tmux_session or self.tmux_session
        state = load_state(tmux_session, session.id)
        state.turns += 1
        if state.cli_title is None:
            state.cli_title = session.name  # launched with --name (or never titled)
        save_state(tmux_session, session.id, state)
        if cfg.get("cli_title", True) and self._is_claude(session) and state.cli_title != session.name:
            self._run(f"align:{session.id}", lambda: self._align(session.id))
        if not cfg.get("enabled"):
            return
        turns = max(state.turns, int(getattr(session.stats, "interaction_count", 0) or 0))
        stage = due(state, source=getattr(session, "name_source", HUMAN), turns=turns,
                    green_seconds=float(getattr(st, "green_time_seconds", 0) or 0),
                    output_tokens=int(getattr(st, "output_tokens", 0) or 0),
                    cfg=cfg, now=now.timestamp())
        if stage is None:
            return
        if st.current_status == STATUS_WAITING_APPROVAL or self._human_typing():
            return  # at the next Stop
        if self._over_cap(cfg, now):
            return
        self._run(f"name:{session.id}", lambda: self._name(session.id, stage, cfg))

    def _over_cap(self, cfg: dict, now: datetime) -> bool:
        cap = float(cfg.get("cost_cap_per_day", 0.5) or 0.0)
        if self.spend.today(now) < cap:
            return False
        day = now.strftime("%Y-%m-%d")
        if self._cap_logged_day != day:
            self._cap_logged_day = day
            self._info(f"namer: today's cost cap (${cap:.2f}) reached; naming resumes tomorrow")
        return True

    @staticmethod
    def _is_claude(session) -> bool:
        from .backends import session_backend_name
        return session_backend_name(session) == "claude-code"

    # ── the worker's jobs ─────────────────────────────────────────────

    def _name(self, session_id: str, stage: str, cfg: dict) -> None:
        """Name one agent: gather, ask, choose, rename (worker thread)."""
        sm = self.session_manager
        session = sm.get_session(session_id)
        if session is None or is_locked(getattr(session, "name_source", HUMAN)):
            return
        now = self.clock.wall().replace(tzinfo=None)
        if self._over_cap(cfg, now):
            return
        tmux_session = session.tmux_session or self.tmux_session
        state = load_state(tmux_session, session_id)
        state.attempted_at = now.timestamp()
        save_state(tmux_session, session_id, state)

        client = self._client_factory(cfg.get("provider"))
        if client is None or not getattr(client, "available", False):
            if not self._no_client_logged:
                self._no_client_logged = True
                self._warn("namer: naming.enabled is on but no model is configured "
                           "(summarizer: or naming.provider, and its API key)")
            return
        sessions = sm.list_sessions()
        archived = recent_archived(sm, now)
        inputs = gather_inputs(session, tmux_session=tmux_session, sessions=sessions,
                               archived=archived, max_length=cfg.get("max_length", 18),
                               pane=lambda: self._capture(session, PANE_LINES))
        from .summarizer_prompts import load_naming_prompt
        result = suggest(inputs, naming_context(cfg, sessions, archived, session), client=client,
                         template=load_naming_prompt(),
                         advice=advice_for(cfg, session.start_directory))
        if result.cost_usd:
            self.spend.add(result.cost_usd, now)
        try:
            record_spend(getattr(sm, "ledger", None), result, session, now)
        except Exception as e:
            self._warn(f"namer: spend not written to the ledger: {e}")
        if result.error:
            self._info(f"namer: [{session.name}] {result.error}")
            return

        state = load_state(tmux_session, session_id)
        pick = result.pick
        keeps = result.keep or not pick or pick == session.name
        if stage != "first":
            state.refine_done = True
        elif keeps and getattr(session, "name_source", HUMAN) == DEFAULT \
                and state.first_tries + 1 < FIRST_TRIES:
            state.first_tries += 1  # a placeholder: ask again later (first_due)
        else:
            state.first_done = True
        save_state(tmux_session, session_id, state)
        if keeps:
            self._info(f"namer: [{session.name}] keeps its name"
                       + (f" ({result.why})" if result.why else ""))
            return
        renamed = self._commit(session, pick, result, cfg, sessions, archived)
        if renamed is None:
            return
        state = load_state(tmux_session, session_id)
        state.renames += 1
        state.last_rename_at = now.timestamp()
        save_state(tmux_session, session_id, state)
        self._info(f"namer: auto-named '{session.name}' → '{renamed}'"
                   + (f" ({result.why})" if result.why else ""))
        if not session.parent_session_id:
            self._follow_code(session, renamed)
        if cfg.get("cli_title", True) and self._is_claude(session):
            self._align(session_id)

    def _commit(self, session, pick: str, result: Suggestion, cfg: dict, sessions,
                archived) -> Optional[str]:
        """Rename to ``pick``; on a lost race, choose again against the fleet as it is now."""
        from .naming_policy import choose
        for attempt in range(2):
            current = self.session_manager.get_session(session.id)
            if current is None or is_locked(getattr(current, "name_source", HUMAN)):
                return None  # a person took the name over meanwhile
            tmux_session = current.tmux_session or self.tmux_session
            state = load_state(tmux_session, current.id)
            if state.cli_title is None:  # still the launch's --name: keep it known
                state.cli_title = current.name
                save_state(tmux_session, current.id, state)
            try:
                if self._rename(current, pick, AUTO):
                    return pick
                self._info(f"namer: [{current.name}] window rename to '{pick}' refused")
                return None
            except ValueError as e:
                if attempt:
                    self._info(f"namer: [{current.name}] not renamed: {e}")
                    return None
                fresh = self.session_manager.list_sessions()
                pick = choose(result.candidates, naming_context(cfg, fresh, archived, current))
                if not pick or pick == current.name:
                    return None
        return None

    def _follow_code(self, session, name: str) -> None:
        """A billing code made just for this agent takes its auto name (owner decision 3).

        Each auto rename, while the code is still ``default`` and only this
        tree's (``follow_agent_name``): a folder or default code is ``human``.
        """
        code_id = getattr(session, "billing_code_id", None)
        ledger = getattr(self.session_manager, "ledger", None)
        if not code_id or ledger is None:
            return
        try:
            if ledger.codes.follow_agent_name(code_id, name):
                self._info(f"namer: billing code of '{name}' renamed to match")
        except Exception as e:
            self._warn(f"namer: billing code not renamed: {e}")

    def _capture(self, session, lines: int) -> str:
        if self.tmux is None:
            return ""
        return self.tmux.capture_pane(session.tmux_session or self.tmux_session,
                                      session.tmux_window, lines=lines) or ""

    def _align(self, session_id: str) -> None:
        """Type ``/rename <name>`` into a Claude agent whose title lags its name (worker).

        Only into an idle agent with an empty input line; otherwise the next
        Stop or idle retry tries again. Keys typed into the agent's pane go
        to tmux, not the TUI, so the empty line is the guard against typing
        over a draft, not the TUI's keypress age (that one keeps auto
        renames from moving rows under the cursor). A title never recorded
        is the launch's ``--name``.
        """
        from .backends import get_backend, session_backend_name
        session = self.session_manager.get_session(session_id)
        if session is None or self.tmux is None:
            return
        if self._last_status.get(session_id) not in IDLE_STATUSES:
            return  # a working Claude would take it as its next prompt
        tmux_session = session.tmux_session or self.tmux_session
        state = load_state(tmux_session, session_id)
        if state.cli_title is None:
            state.cli_title = session.name
            save_state(tmux_session, session_id, state)
        if state.cli_title == session.name:
            self._idle_title[session_id] = (session.name, None)
            self._titles_known[session_id] = (session.name, session.name)
            return
        backend = get_backend(session_backend_name(session))
        if not input_line_empty(self._capture(session, 15), backend):
            return
        if not self.tmux.send_keys(tmux_session, session.tmux_window,
                                   f"/rename {session.name}", enter=True):
            return
        state = load_state(tmux_session, session_id)
        state.cli_title = session.name
        save_state(tmux_session, session_id, state)
        self._idle_title[session_id] = (session.name, None)
        self._titles_known[session_id] = (session.name, session.name)

    def _consider_title(self, session, title: str) -> None:
        tmux_session = session.tmux_session or self.tmux_session
        state = load_state(tmux_session, session.id)
        if title == state.seen_title:
            return
        name = title_to_adopt(title, session, state)
        state.seen_title = title
        if name is None:
            if title == session.name:
                state.cli_title = title
                self._titles_known[session.id] = (session.name, title)
            save_state(tmux_session, session.id, state)
            return
        save_state(tmux_session, session.id, state)
        self._run(f"adopt:{session.id}", lambda: self._adopt(session.id, title, name))

    def _adopt(self, session_id: str, title: str, name: str) -> None:
        """Take a ``/rename`` typed in the CLI as a human rename of the agent (worker)."""
        from .naming_policy import NamingContext, NamingRules, failures
        session = self.session_manager.get_session(session_id)
        if session is None or session.name == name:
            return
        ctx = NamingContext.from_fleet(NamingRules(), self.session_manager.list_sessions(),
                                       self_id=session_id)
        found = failures(name, ctx)
        if found:
            self._info(f"namer: [{session.name}] Claude title '{title}' not adopted "
                       f"({found[0].message})")
            return
        try:
            if not self._rename(session, name, HUMAN):
                return
        except ValueError as e:
            self._info(f"namer: [{session.name}] Claude title '{title}' not adopted ({e})")
            return
        tmux_session = session.tmux_session or self.tmux_session
        state = load_state(tmux_session, session_id)
        # Claude already shows the title it was given; nothing to type back
        state.cli_title = name if name == title else title
        save_state(tmux_session, session_id, state)
        self._info(f"namer: '{session.name}' → '{name}' (renamed in Claude)")
