"""Claude background sessions' spend in the billing ledger (0.6.1 follow-ups).

A background session the engine adopted (a 🌙 row, ``claude_bg``) is its own
conversation: its spend is read from its own transcript (``BgSpend``) and was
never part of the agent it nests under. Owner decision: it is billed to that
agent's billing code (children inherit, codes are sticky), counted once.

Each session is a ledger "agent" of its own (``sid`` = its row id,
``claude-bg:<sessionId>``; ``parent`` = the agent it nests under) whose rows
carry its cumulative spend, like an agent's:

* ``checkpoint`` — while it runs: the first as soon as it has spent anything,
  then at most every ``ledger.checkpoint_minutes``, only when it moved;
* ``final`` — when it finishes, or when its row goes away unfinished (its
  agent ended, ``claude rm``): the segment closes;
* ``open`` — it spends again after a final (a finished session resumed) or
  its agent was rebilled: a new segment whose base is what the last one
  closed at, so nothing is counted twice (and a ``close`` row ends the old
  code's segment on a rebill);
* ``correction`` — it reads lower than its segment was billed (a reader
  fix: a session forked from a conversation no longer counts the history
  it copied, ``claude_bg.BgSpend``): the segment is scaled down by a
  ``ledger.correction_rows`` row, open or closed, never by a lower
  cumulative row.

What each session's ledger segment stands at is kept in
``ledger/claude_bg.json`` (seg, closed, base, last values, the code, which
engine wrote it), so a daemon restart neither re-bills nor forgets a closed
segment. A session whose ``sessionId`` is one of its agent's own
conversations is skipped: the agent's own spend already reads that
transcript.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional

from . import ledger as ledger_mod

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:  # pragma: no cover - Windows
    HAS_FCNTL = False

logger = logging.getLogger(__name__)

STATE_NAME = "claude_bg.json"
# A closed segment's entry is forgotten this long after it closed (its rows
# stay in the ledger for ever; a session resumed later starts at seg 0 of a
# fresh entry only if the ledger forgot it too, CLOSED_KEEP_DAYS)
FORGET_SECONDS = ledger_mod.CLOSED_KEEP_DAYS * 86400
_AMOUNTS = ledger_mod.AMOUNTS


def _record(row, code: str, name: Optional[str] = None) -> dict:
    """The ledger record (``ledger.make_row``'s input) of a 🌙 row's agent state.

    Billed to ``code`` (its agent's code id, else a legacy string) named ``name``.
    """
    from .codes import is_code_id

    st = row.state
    stats = {
        "estimated_cost_usd": float(st.estimated_cost_usd or 0.0),
        "input_tokens": int(st.input_tokens or 0),
        "output_tokens": int(st.output_tokens or 0),
        "cache_creation_tokens": int(st.cache_creation_tokens or 0),
        "cache_read_tokens": int(st.cache_read_tokens or 0),
    }
    stats["total_tokens"] = (stats["input_tokens"] + stats["output_tokens"]
                             + stats["cache_creation_tokens"] + stats["cache_read_tokens"])
    info = st.claude_bg or {}
    return {"id": st.session_id, "name": st.name, "parent_session_id": info.get("parent_id"),
            "billing_code_id": code if is_code_id(code) else None,
            "billing_code": name or code, "model": st.model, "stats": stats}


def _stats_of(spend: Mapping[str, object]) -> dict:
    """A record's ``stats`` from a ``claude_bg.read_spend`` value."""
    stats = {"estimated_cost_usd": float(spend.get("cost_usd") or 0.0)}
    for key in ("input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens"):
        stats[key] = int(spend.get(key) or 0)
    stats["total_tokens"] = (stats["input_tokens"] + stats["output_tokens"]
                             + stats["cache_creation_tokens"] + stats["cache_read_tokens"])
    return stats


def _moved(amounts: Mapping[str, float], last: Optional[Mapping[str, float]]) -> bool:
    if not last:
        return True
    return any(abs(float(amounts.get(k, 0) or 0) - float(last.get(k, 0) or 0)) > 1e-9
               for k in _AMOUNTS)


def _lowered(amounts: Mapping[str, float], last: Optional[Mapping[str, float]]) -> bool:
    """Whether a session's spend reads lower than its ledger holds (a reader fix)."""
    if not last:
        return False
    return (float(amounts.get("cost", 0) or 0) < float(last.get("cost", 0) or 0) - 1e-9
            or int(amounts.get("tokens", 0) or 0) < int(last.get("tokens", 0) or 0))


def _grew(amounts: Mapping[str, float], last: Optional[Mapping[str, float]]) -> bool:
    if not last:
        return True
    return (float(amounts.get("cost", 0) or 0) > float(last.get("cost", 0) or 0) + 1e-9
            or int(amounts.get("tokens", 0) or 0) > int(last.get("tokens", 0) or 0))


class BgLedger:
    """Writes the adopted background sessions' ledger rows (see the module docstring)."""

    def __init__(self, directory: Callable[[], Optional[Path]], owner: str,
                 checkpoint_seconds: Callable[[], float] = None) -> None:
        self._directory = directory
        self.owner = owner  # this engine's tmux session: only it closes what it opened
        self._every = checkpoint_seconds or _checkpoint_seconds
        self._seen: Dict[str, dict] = {}   # row id -> last record observed (this process)
        self._cache = None                 # (file signature, state) as last read
        self._codes = None                 # the ledger's code registry, this pass
        self.writes = 0                    # rows written (tests)

    @property
    def observing(self) -> bool:
        """Whether the last pass saw a session spend (one that goes must close)."""
        return bool(self._seen)

    # ── the state file ─────────────────────────────────────────────────

    def _path(self) -> Optional[Path]:
        directory = self._directory()
        return Path(directory) / STATE_NAME if directory is not None else None

    @contextmanager
    def _locked(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        if not HAS_FCNTL:
            yield
            return
        fd = os.open(str(path) + ".lock", os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    @staticmethod
    def _load(path: Path) -> Dict[str, dict]:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return {}
        sessions = data.get("sessions") if isinstance(data, dict) else None
        return {k: v for k, v in (sessions or {}).items() if isinstance(v, dict)}

    @staticmethod
    def _save(path: Path, sessions: Dict[str, dict]) -> None:
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"v": 1, "sessions": sessions}))
        os.replace(tmp, path)

    # ── one pass ───────────────────────────────────────────────────────

    def sync(self, rows: Iterable, agents: Iterable, ledger, now: float,
             codes: Optional[Mapping[str, str]] = None,
             reread: Optional[Callable[[str], Optional[dict]]] = None) -> int:
        """Bring the ledger up to date with ``rows`` (the source's, as built); rows written.

        ``agents`` are the source's FleetAgents (their own conversations are
        skipped); ``codes`` maps an agent id to its billing code key
        (default: each FleetAgent's ``billing_code_id``, else its
        ``billing_code``, else its name). A segment's code is compared by
        the code it reports under (``ledger.codes``), so a legacy string
        and its code's id are one code, not a rebill. ``reread``
        answers a session id's spend now (``ClaudeBgSource.read_spend_of``),
        so a segment closed for a row that is gone closes at what the
        session really spent, not at its last checkpoint.

        Planned against the state as last read (one ``stat`` to know it is
        current); the state file is locked and read again only when a row
        is to be written.
        """
        path = self._path()
        if path is None or ledger is None:
            return 0
        by_id = {a.id: a for a in agents}
        code_of = dict(codes or {})
        self._codes = getattr(ledger, "codes", None)
        observed: Dict[str, dict] = {}
        finished: Dict[str, bool] = {}
        for row in rows:
            info = row.state.claude_bg or {}
            parent = by_id.get(info.get("parent_id"))
            if parent is None or info.get("session_id") in parent.conversations:
                continue  # the agent's own conversation: its own spend counts it
            name = getattr(parent, "billing_code", None) or parent.name
            code = (code_of.get(parent.id) or getattr(parent, "billing_code_id", None)
                    or name)
            record = _record(row, code, name)
            if record["stats"]["estimated_cost_usd"] <= 0 and record["stats"]["total_tokens"] <= 0:
                continue  # nothing spent (yet), or no transcript read
            observed[row.state.session_id] = record
            finished[row.state.session_id] = row.light.ended_at is not None
        gone = {rid: rec for rid, rec in self._seen.items() if rid not in observed}
        self._seen = observed
        now_dt = datetime.fromtimestamp(now)
        out, _, changed = self._plan(self._cached(path), observed, finished, gone, now, now_dt,
                                     reread=None)
        if not out and not changed:
            return 0
        with self._locked(path):
            state = self._load(path)
            out, state, changed = self._plan(state, observed, finished, gone, now, now_dt,
                                             reread=reread)
            if out:
                try:
                    ledger.append(out, now=now_dt)
                except Exception as e:  # never fail the engine over the ledger
                    logger.warning("background-session spend not written: %s", e)
                    return 0
                self.writes += len(out)
            if changed:
                try:
                    self._save(path, state)
                except OSError as e:
                    logger.warning("background-session ledger state not saved: %s", e)
            self._cache = (self._signature(path), state)
        return len(out)

    @staticmethod
    def _signature(path: Path):
        try:
            st = os.stat(path)
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def _cached(self, path: Path) -> Dict[str, dict]:
        signature = self._signature(path)
        if self._cache is None or self._cache[0] != signature:
            self._cache = (signature, self._load(path))
        return self._cache[1]

    def _plan(self, state: Dict[str, dict], observed: Dict[str, dict],
              finished: Dict[str, bool], gone: Dict[str, dict], now: float,
              now_dt: datetime, reread) -> tuple:
        """(rows to write, the new state, whether it changed) from ``state``."""
        state = dict(state)
        out: List[dict] = []
        changed = False
        for rid, record in observed.items():
            rows_for, entry = self._rows_for(record, finished[rid], state.get(rid), now, now_dt)
            if entry is not None:
                state[rid] = entry
                changed = True
            out.extend(rows_for)
        # Rows gone unfinished (their agent ended, `claude rm`, its finished
        # row's time up), and, after a restart, segments this engine left
        # open: closed at what the session spent
        for rid, entry in list(state.items()):
            if rid in observed or entry.get("closed") or entry.get("owner") != self.owner:
                continue
            last = gone.get(rid) or entry.get("record")
            if not isinstance(last, dict):
                continue
            if reread is not None:
                fresh = None
                try:
                    fresh = reread(rid)
                except Exception:  # noqa: BLE001 — the last values still close it
                    fresh = None
                if isinstance(fresh, dict):
                    last = dict(last, stats=_stats_of(fresh), model=fresh.get("model")
                                or last.get("model"))
            out.append(self._row(last, "final", entry, now_dt))
            state[rid] = dict(entry, closed=True, at=now, last=ledger_mod.spend_of(last),
                              record=last)
            changed = True
        for rid in [r for r, e in state.items()
                    if e.get("closed") and now - float(e.get("at") or 0) > FORGET_SECONDS]:
            del state[rid]
            changed = True
        return out, state, changed

    def _canonical(self, key: Optional[str]) -> Optional[str]:
        return self._codes.canonical(key) if self._codes is not None else key

    def _name(self, key: str) -> str:
        """The name a row billed to ``key`` carries: an id's current name; a legacy string as it is."""
        from .codes import is_code_id

        return self._codes.name_of(key) if self._codes is not None and is_code_id(key) else key

    def _row(self, record: dict, kind: str, entry: dict, now_dt: datetime,
             code: Optional[str] = None, seg: Optional[int] = None,
             base: Optional[dict] = None) -> dict:
        base = base if base is not None else entry.get("base") or {}
        code = code or entry.get("code") or ledger_mod.billing_code_of(record)
        row = ledger_mod.make_row(
            record, kind, now=now_dt, code=code, billing_code=self._name(code),
            seg=int(entry.get("seg", 0) if seg is None else seg),
            base={"cost": float(base.get("cost", 0.0) or 0.0),
                  "tokens": int(base.get("tokens", 0) or 0), "usage": None})
        row["px"] = None  # a recorded cost: each file priced at its own model
        row["claude_bg"] = True
        return row

    def _correction(self, before: dict, after: dict, entry: dict, now_dt: datetime) -> dict:
        """The correction row restating ``entry``'s segment from ``before`` to ``after``.

        ``ledger.correction_rows``: the segment's cumulative values and its
        base are scaled by new/old; ``entry``'s base takes the scaled one.
        """
        from .codes import is_code_id

        base = entry.get("base") or {}
        fields = {"billing_seg": int(entry.get("seg", 0) or 0),
                  "billing_base_cost": float(base.get("cost", 0.0) or 0.0),
                  "billing_base_tokens": int(base.get("tokens", 0) or 0),
                  "billing_base_usage": None,
                  "billing_code_id": None, "billing_code": None}
        code = entry.get("code") or ledger_mod.billing_code_of(after)
        if is_code_id(code):
            fields.update(billing_code_id=code, billing_code=self._name(code))
        else:
            fields["billing_code"] = code
        rows, new_base = ledger_mod.correction_rows(
            dict(before, **fields), dict(after, **fields),
            closed=bool(entry.get("closed")), now=now_dt)
        row = rows[0]
        row["px"] = None
        row["claude_bg"] = True
        entry["base"] = dict(base, cost=new_base["billing_base_cost"],
                             tokens=new_base["billing_base_tokens"])
        return row

    def _rows_for(self, record: dict, done: bool, entry: Optional[dict],
                  now: float, now_dt: datetime):
        """(rows to write, the new state entry or None) for one observed session."""
        amounts = ledger_mod.spend_of(record)
        code = ledger_mod.billing_code_of(record)
        every = self._every()
        if entry is None:
            entry = {"seg": 0, "closed": False, "base": dict.fromkeys(_AMOUNTS, 0),
                     "code": code, "last": None, "at": None}
        previous = entry.get("record")
        entry = dict(entry, owner=self.owner, record=record)
        if entry.get("code") and entry["code"] != code and (
                self._canonical(entry["code"]) == self._canonical(code)):
            # The same code by another key (a legacy string, an id merged
            # since): its rows carry the current key from now on
            entry = dict(entry, code=code)
        rows: List[dict] = []
        if (_lowered(amounts, entry.get("last")) and isinstance(previous, dict)
                and (entry.get("closed") or entry.get("code") == code)):
            # Read lower than billed: a reader fix (a fork's copied history
            # is no longer its spend). The segment is restated by a
            # correction row, never by a lower cumulative one
            rows.append(self._correction(previous, record, entry, now_dt))
            entry.update(last=amounts, at=now)
            return rows, entry
        if entry.get("closed"):
            if not _grew(amounts, entry.get("last")):
                return rows, None  # still as it closed (a restart, a finished row on show)
            # It spends again (resumed): a new segment from where the last closed
            entry.update(seg=int(entry.get("seg", 0)) + 1, closed=False, code=code,
                         base=dict(entry.get("last") or {}))
            kind = "final" if done else "open"
            rows.append(self._row(record, kind, entry, now_dt))
            entry.update(closed=done, last=amounts, at=now)
            return rows, entry
        if entry.get("code") and entry["code"] != code:
            # Its agent was rebilled: the old code keeps what it spent so far
            rows.append(self._row(record, "close", entry, now_dt))
            entry.update(seg=int(entry.get("seg", 0)) + 1, code=code, base=dict(amounts))
            rows.append(self._row(record, "final" if done else "open", entry, now_dt))
            entry.update(closed=done, last=amounts, at=now)
            return rows, entry
        if done:
            rows.append(self._row(record, "final", entry, now_dt))
            entry.update(closed=True, last=amounts, at=now)
            return rows, entry
        due = entry.get("at") is None or now - float(entry.get("at") or 0) >= every
        if _moved(amounts, entry.get("last")) and due:
            rows.append(self._row(record, "checkpoint", entry, now_dt))
            entry.update(last=amounts, at=now)
            return rows, entry
        return rows, None


def _checkpoint_seconds() -> float:
    from .config import get_ledger_config

    return float(get_ledger_config()["checkpoint_minutes"]) * 60.0
