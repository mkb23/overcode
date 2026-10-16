"""Checking overcode's Claude spend against Claude Code's own (cost model 0.6.1, §6.5).

When a Claude Code session ends it writes a ``cost-state`` line to the
conversation's transcript: per model, the tokens it counted and what it
charged (``modelUsage.<model>.costUSD``, ``totalCostUSD``), subagents
included. Phase 0 showed overcode's per-message read matches its token
counts exactly (``tests/fixtures/spike_spend``); what is left to check is
the price: overcode's estimate of the same tokens, at its list prices,
against Claude Code's charge. A drift past ``DRIFT_RELATIVE`` (and past
``DRIFT_ABSOLUTE``, which absorbs the side calls Claude Code never writes
to a transcript, such as session naming) means a price row is wrong, and
``overcode doctor`` says which model.

The check only compares a model when the conversation's cache tokens match
Claude Code's within ``TOKEN_MATCH``: a conversation begun before the agent
started (overcode reads from its start) or resumed after its cost-state
reads different tokens, and its cost says nothing about prices. Bedrock
premiums and discounts are left out on purpose: the comparison is of list
prices (Claude Code's own estimate), not of the bill.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

DRIFT_RELATIVE = 0.02
DRIFT_ABSOLUTE = 0.05
TOKEN_MATCH = 0.01
# A transcript is read backwards this far for its last cost-state line
TAIL_BYTES = 256 * 1024


@dataclass(frozen=True)
class Drift:
    """One conversation's model whose estimate is off Claude Code's charge."""

    conversation: str
    model: str
    ours: float
    theirs: float

    @property
    def ratio(self) -> float:
        return self.ours / self.theirs if self.theirs else float("inf")


def latest_cost_state(path: Path) -> Optional[dict]:
    """The last ``cost-state`` record in a transcript, or None."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            tail = f.read()
    except OSError:
        return None
    for raw in reversed(tail.splitlines()):
        if b'"cost-state"' not in raw:
            continue
        try:
            record = json.loads(raw)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("type") == "cost-state":
            return record
    return None


def _model_key(model: Optional[str]) -> str:
    from .price_table import price_key

    return price_key(model) or (model or "?")


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(1.0, TOKEN_MATCH * max(abs(a), abs(b)))


def conversation_drift(conversation: str, reads: Mapping[str, Mapping[str, Any]],
                       cost_state: Mapping[str, Any], prices) -> List[Drift]:
    """The models of one conversation whose estimate drifts from its cost-state.

    ``reads`` are the conversation's reads (``conversation_spend.usage``:
    the conversation and its subagents, each at its model); ``prices(model)``
    the price row overcode prices at.
    """
    from .conversation_spend import price

    ours: Dict[str, Dict[str, float]] = {}
    for read in reads.values():
        key = _model_key(read.get("model"))
        group = ours.setdefault(key, {"cw": 0, "cr": 0, "cost": 0.0})
        group["cw"] += int(read.get("cw", 0) or 0)
        group["cr"] += int(read.get("cr", 0) or 0)
        try:
            group["cost"] += price(prices(read.get("model")), read)
        except Exception:
            group["cost"] = float("nan")
    found: List[Drift] = []
    usage = cost_state.get("modelUsage")
    for model, theirs in (usage.items() if isinstance(usage, Mapping) else ()):
        if not isinstance(theirs, Mapping):
            continue
        mine = ours.get(_model_key(model))
        charged = theirs.get("costUSD")
        if mine is None or not isinstance(charged, (int, float)) or mine["cost"] != mine["cost"]:
            continue
        if not (_close(mine["cw"], float(theirs.get("cacheCreationInputTokens") or 0))
                and _close(mine["cr"], float(theirs.get("cacheReadInputTokens") or 0))):
            continue
        if abs(mine["cost"] - charged) > max(DRIFT_RELATIVE * charged, DRIFT_ABSOLUTE):
            found.append(Drift(conversation, model, mine["cost"], float(charged)))
    return found


def transcript_path(session: Any, sid: str, projects_path: Path) -> Optional[Path]:
    """The transcript of one of a Claude agent's conversations, or None."""
    from .history_reader import get_session_file_path

    start = getattr(session, "start_directory", None)
    if start:
        path = get_session_file_path(start, sid, projects_path)
        if path.exists():
            return path
    try:
        return next(iter(projects_path.glob(f"*/{sid}.jsonl")), None)
    except OSError:
        return None


def spend_drift(session: Any, per_conversation: Mapping[str, Mapping[str, Any]],
                prices, projects_path: Optional[Path] = None) -> List[Drift]:
    """Every drift of a Claude agent's ended conversations (module docstring)."""
    from . import history_reader

    projects = projects_path if projects_path is not None else history_reader.CLAUDE_PROJECTS_PATH
    by_conversation: Dict[str, Dict[str, Mapping[str, Any]]] = {}
    for cid, read in per_conversation.items():
        if isinstance(read, Mapping) and not cid.startswith("_"):
            by_conversation.setdefault(cid.split("/", 1)[0], {})[cid] = read
    found: List[Drift] = []
    for sid, reads in by_conversation.items():
        path = transcript_path(session, sid, projects)
        record = latest_cost_state(path) if path is not None else None
        if record is not None:
            found.extend(conversation_drift(sid, reads, record, prices))
    return found


def drift_messages(drifts: Iterable[Drift]) -> List[str]:
    """One line per drift, for ``overcode doctor``."""
    return [
        f"conversation {d.conversation[:8]}: overcode prices {d.model} at ${d.ours:.4f}, "
        f"Claude Code charged ${d.theirs:.4f} (x{d.ratio:.2f}): its price row may be wrong"
        for d in drifts
    ]


__all__ = ["DRIFT_ABSOLUTE", "DRIFT_RELATIVE", "Drift", "TOKEN_MATCH", "conversation_drift",
           "drift_messages", "latest_cost_state", "spend_drift", "transcript_path"]
