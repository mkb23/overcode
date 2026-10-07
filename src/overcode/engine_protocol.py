"""The engine's publish protocol: snapshots, deltas, and how views apply them.

See docs/design/engine-0.6.md. The engine (monitor daemon) holds the fleet as
a ``Snapshot``: per agent a flat mapping of JSON-serialisable fields, plus
fleet-wide fields. Views receive one snapshot on connect and then deltas that
carry only what changed. Applying every delta to a snapshot reproduces the
engine's next snapshot exactly; ``tests/unit/test_engine_protocol.py`` holds
that as a property.

Wire format: one JSON object per line, UTF-8, each with a type ``t``:

    engine -> view   hello, snapshot, delta, ping, bell
    view -> engine   subscribe, visible, focus

Fields are kept flat on purpose: a delta replaces whole values, and a field
that disappears is listed in ``unset``, so applying never needs a deep merge.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

PROTOCOL_VERSION = 1

AgentFields = Dict[str, Any]


@dataclass
class Snapshot:
    seq: int = 0
    agents: Dict[str, AgentFields] = field(default_factory=dict)
    fleet: Dict[str, Any] = field(default_factory=dict)

    def copy(self) -> "Snapshot":
        return Snapshot(
            self.seq,
            {aid: dict(fields) for aid, fields in self.agents.items()},
            dict(self.fleet),
        )


@dataclass
class Delta:
    seq: int
    # Per agent: fields set (new or changed) and fields removed
    set: Dict[str, AgentFields] = field(default_factory=dict)
    unset: Dict[str, List[str]] = field(default_factory=dict)
    removed: List[str] = field(default_factory=list)
    fleet_set: Dict[str, Any] = field(default_factory=dict)
    fleet_unset: List[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.set or self.unset or self.removed
                    or self.fleet_set or self.fleet_unset)


def _diff_fields(old: Dict[str, Any], new: Dict[str, Any]):
    changed = {k: v for k, v in new.items() if k not in old or old[k] != v}
    gone = sorted(k for k in old if k not in new)
    return changed, gone


def diff(old: Snapshot, new: Snapshot) -> Delta:
    """The delta that turns ``old`` into ``new`` (``new.seq`` is its seq)."""
    delta = Delta(seq=new.seq)
    for aid, fields in new.agents.items():
        before = old.agents.get(aid)
        if before is None:
            delta.set[aid] = dict(fields)
            continue
        changed, gone = _diff_fields(before, fields)
        if changed:
            delta.set[aid] = changed
        if gone:
            delta.unset[aid] = gone
    delta.removed = sorted(aid for aid in old.agents if aid not in new.agents)
    delta.fleet_set, delta.fleet_unset = _diff_fields(old.fleet, new.fleet)
    return delta


def apply(snapshot: Snapshot, delta: Delta) -> Snapshot:
    """``snapshot`` advanced by ``delta``, as a new object."""
    out = snapshot.copy()
    for aid in delta.removed:
        out.agents.pop(aid, None)
    for aid, fields in delta.set.items():
        out.agents.setdefault(aid, {}).update(fields)
    for aid, names in delta.unset.items():
        agent = out.agents.get(aid)
        if agent is not None:
            for name in names:
                agent.pop(name, None)
    out.fleet.update(delta.fleet_set)
    for name in delta.fleet_unset:
        out.fleet.pop(name, None)
    out.seq = delta.seq
    return out


# ── wire encoding ─────────────────────────────────────────────────────────


def encode(message: Dict[str, Any]) -> bytes:
    return (json.dumps(message, separators=(",", ":"), default=str) + "\n").encode()


def hello(seq: int) -> bytes:
    return encode({"t": "hello", "version": PROTOCOL_VERSION, "seq": seq})


def snapshot_message(snapshot: Snapshot) -> bytes:
    return encode({"t": "snapshot", "seq": snapshot.seq,
                   "agents": snapshot.agents, "fleet": snapshot.fleet})


def delta_message(delta: Delta) -> bytes:
    msg: Dict[str, Any] = {"t": "delta", "seq": delta.seq}
    if delta.set:
        msg["set"] = delta.set
    if delta.unset:
        msg["unset"] = delta.unset
    if delta.removed:
        msg["removed"] = delta.removed
    if delta.fleet_set:
        msg["fleet_set"] = delta.fleet_set
    if delta.fleet_unset:
        msg["fleet_unset"] = delta.fleet_unset
    return encode(msg)


def ping(seq: int) -> bytes:
    return encode({"t": "ping", "seq": seq})


def bell(seq: int, agent: str, episode: Dict[str, Any]) -> bytes:
    return encode({"t": "bell", "seq": seq, "agent": agent, "episode": episode})


def decode_lines(buffer: bytearray) -> Iterable[Dict[str, Any]]:
    """Pop every complete line off ``buffer`` and yield the decoded messages.

    A partial trailing line stays in the buffer for the next read. A line
    that isn't a JSON object is skipped, never raised: a view must survive a
    newer engine's message types.
    """
    while True:
        end = buffer.find(b"\n")
        if end < 0:
            return
        line = bytes(buffer[:end])
        del buffer[: end + 1]
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict) and "t" in message:
            yield message


def delta_from_message(message: Dict[str, Any]) -> Delta:
    return Delta(
        seq=int(message.get("seq", 0)),
        set=message.get("set") or {},
        unset=message.get("unset") or {},
        removed=message.get("removed") or [],
        fleet_set=message.get("fleet_set") or {},
        fleet_unset=message.get("fleet_unset") or [],
    )


def snapshot_from_message(message: Dict[str, Any]) -> Snapshot:
    return Snapshot(
        seq=int(message.get("seq", 0)),
        agents=message.get("agents") or {},
        fleet=message.get("fleet") or {},
    )


class ViewState:
    """A view's copy of the engine's snapshot, kept current from messages.

    ``feed`` returns True when the snapshot changed. A delta that does not
    follow the snapshot it was built against (a gap in ``seq``) can't be
    applied safely, so the view reports ``needs_resync`` and the client
    reconnects for a fresh snapshot.
    """

    def __init__(self) -> None:
        self.snapshot: Optional[Snapshot] = None
        self.needs_resync = False
        self.version: Optional[int] = None

    def feed(self, message: Dict[str, Any]) -> bool:
        kind = message.get("t")
        if kind == "hello":
            self.version = message.get("version")
            return False
        if kind == "snapshot":
            self.snapshot = snapshot_from_message(message)
            self.needs_resync = False
            return True
        if kind == "delta":
            if self.snapshot is None:
                self.needs_resync = True
                return False
            delta = delta_from_message(message)
            if delta.seq != self.snapshot.seq + 1:
                self.needs_resync = True
                return False
            self.snapshot = apply(self.snapshot, delta)
            return not delta.empty
        if kind == "ping" and self.snapshot is not None:
            if int(message.get("seq", -1)) != self.snapshot.seq:
                self.needs_resync = True
        return False
