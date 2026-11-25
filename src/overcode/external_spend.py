"""Spend outside an agent's conversation: the evals and sims it runs.

``docs/design/experiment-billing.md``. A script an agent runs (an eval
calling an LLM API, a sim, a batch job) spends tokens overcode never reads.
``record`` puts that spend in the ledger, under the billing code of the
agent it is attributed to, and charges it to that agent's budget.

**Attribution.** The agent: ``agent`` (an id or name), else
``OVERCODE_BILL_TO`` (an ``overcode bash`` job an agent started exports
it), else ``OVERCODE_JOB_OWNER_ID``, else ``OVERCODE_SESSION_ID`` (every
process an agent runs inherits it). Or no agent and an explicit ``code``.
The code is the agent's *now*: a rebill moves later spend forward.

**Runs.** Spend is recorded to a *run* (an eval invocation; a fresh id
unless one is given). A run is a ledger pseudo-agent: ``sid`` ``ext:<run>``,
``parent`` the agent, rows with ``external: true``. Each record adds its
delta to the run's cumulative spend, kept in ``ledger/external.json``, and
appends the run's new cumulative row as ``final``: the newest closing row of
a segment replaces the last (``ledger._fold``), so a run is counted once and
a crashed eval up to its last record. The run state is saved *before* the
row is appended: a crash between the two is made good by the run's next
record. A rebill of its agent between records starts a new segment on the
new code, based at the cumulative so far.

**Pricing.** ``usd`` given: that cost (``priced: reported``). Else tokens at
``model``'s price (``settings.get_model_pricing``; the agent's provider by
default, Bedrock premiums and discounts applied), ``priced: estimate``, or
``fallback`` for a model the price table does not know.

**The agent.** Its ``stats.external_cost_usd`` / ``external_tokens`` grow in
the same write (sessions lock, then the ledger lock: the usual order), and
the answer says whether it is now over its budget
(``budget_gate.charged_cost``).
"""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Mapping, Optional

from . import ledger as ledger_mod
from .agent_identity import BILL_TO_VAR, JOB_OWNER_VAR
from .clock import stamp

STATE_NAME = "external.json"
RUN_PREFIX = "ext:"
# A run's state is forgotten this long after its last record (the totals
# forget its closed share then too, so a later record starts afresh)
FORGET_DAYS = ledger_mod.CLOSED_KEEP_DAYS
_USAGE_KEYS = ("in", "out", "cw", "cr")


class RecordError(ValueError):
    """A record that cannot be made: nobody to bill, no price, bad numbers."""


@dataclass
class Spend:
    """One recorded amount: tokens (priced at ``model``) or a known cost."""
    model: Optional[str] = None
    tok_in: int = 0
    tok_out: int = 0
    tok_cw: int = 0
    tok_cr: int = 0
    usd: Optional[float] = None
    provider: Optional[str] = None

    def usage(self) -> Dict[str, int]:
        return {"in": int(self.tok_in), "out": int(self.tok_out),
                "cw": int(self.tok_cw), "cr": int(self.tok_cr)}

    def validate(self) -> None:
        for name in ("tok_in", "tok_out", "tok_cw", "tok_cr"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise RecordError(f"{name} must be a whole number >= 0, not {value!r}")
        if self.usd is not None and (not isinstance(self.usd, (int, float)) or self.usd < 0):
            raise RecordError(f"usd must be a number >= 0, not {self.usd!r}")
        if self.usd is None and not self.model:
            raise RecordError("give the model the tokens were spent on (--model), or the cost (--usd)")


@dataclass
class Target:
    """Who a record is billed to: an agent (live or archived) and/or a code."""
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None
    live: bool = False
    record: dict = field(default_factory=dict)
    code_name: Optional[str] = None  # an explicit --code


def _env_target(env: Mapping[str, str]) -> Optional[str]:
    for var in (BILL_TO_VAR, JOB_OWNER_VAR, "OVERCODE_SESSION_ID"):
        value = (env.get(var) or "").strip()
        if value:
            return value
    return None


def _find_agent(manager, key: str) -> Optional[dict]:
    """A live record (as a dict, ``_live: True``) or an archived one, by id or name."""
    session = manager.get_session(key) or manager.resolve_session_name(key)
    if session is not None:
        return {"id": session.id, "name": session.name, "_live": True}
    needle = key.encode("utf-8")
    for record in manager.iter_archived_records(newest_first=True, needle=needle):
        if record.get("id") == key or record.get("name") == key:
            return dict(record)
    return None


def resolve_target(manager, *, agent: Optional[str] = None, code: Optional[str] = None,
                   env: Optional[Mapping[str, str]] = None) -> Target:
    """Who a record bills to (see the module docstring); RecordError when nobody."""
    env = os.environ if env is None else env
    key = (agent or "").strip() or None
    if key is None and not (code or "").strip():
        key = _env_target(env)
    if key is None:
        if (code or "").strip():
            return Target(code_name=code.strip())
        raise RecordError(
            "nothing to bill: run inside an overcode agent or job, or give --agent or --code "
            f"(or set {BILL_TO_VAR})")
    found = _find_agent(manager, key)
    if found is None:
        raise RecordError(f"no agent '{key}' (live or archived)")
    return Target(agent_id=found["id"], agent_name=found.get("name"),
                  live=bool(found.get("_live")), record=found,
                  code_name=(code or "").strip() or None)


def price_spend(spend: Spend, provider: Optional[str] = None,
                routing: Optional[str] = None) -> tuple:
    """``(cost, priced, provider)`` of a spend (see the module docstring)."""
    if spend.usd is not None:
        return float(spend.usd), "reported", spend.provider or provider
    from .pricing import is_priced
    from .settings import get_model_pricing, get_user_config

    provider = spend.provider or provider
    mp = get_model_pricing(spend.model, get_user_config(), provider=provider, routing=routing)
    px = [float(mp.input), float(mp.output), float(mp.cache_write), float(mp.cache_read)]
    cost = ledger_mod.price(px, spend.usage())
    return cost, ("estimate" if is_priced(spend.model) else "fallback"), provider


def _state_path(ledger):
    return ledger.dir / STATE_NAME


def _load_state(ledger) -> dict:
    try:
        data = json.loads(_state_path(ledger).read_text())
    except (OSError, ValueError):
        return {"v": 1, "runs": {}}
    if not isinstance(data, dict) or not isinstance(data.get("runs"), dict):
        return {"v": 1, "runs": {}}
    return data


def _save_state(ledger, data: dict, now: datetime) -> None:
    cutoff = stamp(now - timedelta(days=FORGET_DAYS))
    data["runs"] = {k: v for k, v in data["runs"].items()
                    if isinstance(v, dict) and str(v.get("t", "")) >= cutoff}
    path = _state_path(ledger)
    tmp = path.with_name(f".{STATE_NAME}.{os.getpid()}.tmp")
    with open(tmp, "w") as f:
        f.write(json.dumps(data, sort_keys=True))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _empty_cum() -> dict:
    return {"cost": 0.0, "in": 0, "out": 0, "cw": 0, "cr": 0}


def _tokens(cum: Mapping) -> int:
    return sum(int(cum.get(k, 0) or 0) for k in _USAGE_KEYS)


def _row(run_id: str, run: dict, *, now: datetime, source: Optional[dict]) -> dict:
    cum, base = run["cum"], run.get("base") or _empty_cum()
    return {
        "t": stamp(now), "seq": time.time_ns(),
        "sid": RUN_PREFIX + run_id, "name": run.get("name") or run_id,
        "parent": run.get("agent"),
        **({"code": run["code"]} if run.get("code_is_id") else {}),
        "billing_code": run.get("code_name") or run.get("code"),
        "seg": int(run.get("seg", 0)), "model": run.get("model"),
        "cost": float(cum["cost"]), "tokens": _tokens(cum),
        "tok_in": int(cum["in"]), "tok_out": int(cum["out"]),
        "tok_cache": int(cum["cw"]) + int(cum["cr"]),
        "tok_cw": int(cum["cw"]), "tok_cr": int(cum["cr"]), "px": None,
        "base_cost": float(base["cost"]), "base_tokens": _tokens(base),
        "base_usage": {k: int(base.get(k, 0) or 0) for k in _USAGE_KEYS},
        "kind": "final", "external": True,
        "provider": run.get("provider"), "priced": run.get("priced"),
        **({"source": source} if source else {}),
    }


def default_source() -> dict:
    """Where a record came from: the recording process's parent (the eval), host."""
    return {"pid": os.getppid(), "host": socket.gethostname()}


def record(manager, spend: Spend, *, agent: Optional[str] = None, code: Optional[str] = None,
           run: Optional[str] = None, label: Optional[str] = None,
           source: Optional[dict] = None, env: Optional[Mapping[str, str]] = None,
           now: Optional[datetime] = None) -> dict:
    """Record ``spend`` (see the module docstring); returns what was recorded.

    ``{"run", "sid", "agent", "agent_name", "code", "code_name", "cost",
    "priced", "provider", "run_cost", "external_cost", "charged",
    "budget", "over_budget"}``.
    """
    from .budget_gate import charged_cost
    from .codes import HUMAN, is_code_id

    spend.validate()
    now = now or datetime.now()
    target = resolve_target(manager, agent=agent, code=code, env=env)
    run_id = (run or "").strip() or uuid.uuid4().hex[:12]
    ledger = manager.ledger
    explicit_code = (ledger.codes.resolve(target.code_name, source=HUMAN)
                     if target.code_name else None)
    out: dict = {"run": run_id, "sid": RUN_PREFIX + run_id, "agent": target.agent_id,
                 "agent_name": target.agent_name}
    with manager._state_transaction() as txn:
        state = txn.state
        record_ = state.get(target.agent_id) if target.agent_id else None
        if record_ is None and target.agent_id and not target.live:
            record_ = target.record  # archived: bill to the code it ended on
        provider = routing = None
        if record_ is not None:
            provider = record_.get("provider")
            routing = record_.get("bedrock_routing")
        # An agent on claude.ai (a subscription) still runs its evals on an API key
        provider = provider if provider == "bedrock" else "anthropic"
        cost, priced, provider = price_spend(spend, provider, routing)
        with ledger.locked():
            data = _load_state(ledger)
            existing = data["runs"].get(run_id)
            if explicit_code:
                code_key = explicit_code
            elif record_ is not None:
                code_key = ledger_mod.billing_code_of(record_)
            elif existing:
                code_key = existing.get("code")
            else:
                raise RecordError(f"agent '{target.agent_id}' has no billing code")
            code_name = ledger.codes.name_of(code_key)
            entry = dict(existing) if isinstance(existing, dict) else {
                "seg": 0, "cum": _empty_cum(), "base": _empty_cum()}
            if existing and existing.get("code") != code_key:
                # Its agent was rebilled since: later spend goes to the new code
                entry["seg"] = int(existing.get("seg", 0)) + 1
                entry["base"] = dict(existing["cum"])
            cum = dict(entry["cum"])
            cum["cost"] = float(cum.get("cost", 0.0)) + cost
            for k, v in spend.usage().items():
                cum[k] = int(cum.get(k, 0) or 0) + v
            entry.update(cum=cum, code=code_key, code_is_id=is_code_id(code_key),
                         code_name=code_name, agent=target.agent_id or entry.get("agent"),
                         name=label or entry.get("name") or run_id,
                         model=spend.model or entry.get("model"), provider=provider,
                         priced=priced if entry.get("priced") in (None, priced) else "mixed",
                         t=stamp(now))
            data["runs"][run_id] = entry
            # Run state first: a crash before the row is made good by the next record
            _save_state(ledger, data, now)
            ledger.append_locked([_row(run_id, entry, now=now, source=source)], now=now)
        live = state.get(target.agent_id) if target.agent_id else None
        if live is not None:
            stats = live.setdefault("stats", {})
            stats["external_cost_usd"] = float(stats.get("external_cost_usd", 0.0) or 0.0) + cost
            stats["external_tokens"] = int(stats.get("external_tokens", 0) or 0) + _tokens(
                spend.usage())
            txn.dirty = True
            budget = float(live.get("cost_budget_usd", 0.0) or 0.0)
            charged = charged_cost(stats)
            out.update(external_cost=stats["external_cost_usd"], charged=charged, budget=budget,
                       over_budget=budget > 0 and charged >= budget)
        else:
            out.update(external_cost=None, charged=None, budget=None, over_budget=False)
    out.update(code=code_key, code_name=code_name, cost=cost, priced=priced, provider=provider,
               run_cost=float(cum["cost"]) - float(entry["base"].get("cost", 0.0)))
    return out
