"""engine_protocol: snapshots, deltas, and the view's copy (docs/design/engine-0.6.md).

The property that matters: a view that applies every delta to the snapshot it
was given holds exactly the engine's current snapshot, whatever changed.
"""

import json
import random

import pytest

from overcode import engine_protocol as proto

pytestmark = pytest.mark.unit

VALUES = [0, 1, 2.5, "green", "red", None, True, False, [1, 2], {"k": "v"}, "", "x" * 50]


def random_snapshot(rng, seq=0, agents=8, fields=6):
    snap = proto.Snapshot(seq=seq)
    for a in rng.sample(range(agents * 2), rng.randint(0, agents)):
        snap.agents[f"agent-{a}"] = {
            f"f{f}": rng.choice(VALUES) for f in rng.sample(range(fields * 2), rng.randint(0, fields))
        }
    snap.fleet = {f"g{f}": rng.choice(VALUES) for f in range(rng.randint(0, 4))}
    return snap


def mutate(rng, snap):
    """A plausible next tick: some agents change, appear, vanish or lose fields."""
    nxt = snap.copy()
    for aid in list(nxt.agents):
        roll = rng.random()
        if roll < 0.1:
            del nxt.agents[aid]
        elif roll < 0.5:
            fields = nxt.agents[aid]
            for name in list(fields):
                if rng.random() < 0.2:
                    del fields[name]
                elif rng.random() < 0.3:
                    fields[name] = rng.choice(VALUES)
            if rng.random() < 0.3:
                fields[f"f{rng.randint(0, 12)}"] = rng.choice(VALUES)
    if rng.random() < 0.3:
        nxt.agents[f"new-{rng.randint(0, 99)}"] = {"f0": rng.choice(VALUES)}
    if rng.random() < 0.3:
        nxt.fleet[f"g{rng.randint(0, 5)}"] = rng.choice(VALUES)
    if nxt.fleet and rng.random() < 0.2:
        nxt.fleet.pop(rng.choice(list(nxt.fleet)))
    nxt.seq = snap.seq + 1
    return nxt


class TestDiffApply:
    @pytest.mark.parametrize("seed", range(200))
    def test_applying_deltas_reproduces_every_snapshot(self, seed):
        rng = random.Random(seed)
        engine = random_snapshot(rng)
        view = engine.copy()
        for _ in range(25):
            nxt = mutate(rng, engine)
            delta = proto.diff(engine, nxt)
            view = proto.apply(view, delta)
            assert view == nxt
            engine = nxt

    @pytest.mark.parametrize("seed", range(50))
    def test_the_wire_round_trip_preserves_the_property(self, seed):
        """Through encode/decode, as a real view sees it (JSON, not objects)."""
        rng = random.Random(seed)
        engine = random_snapshot(rng)
        buf = bytearray(proto.snapshot_message(engine))
        state = proto.ViewState()
        for msg in proto.decode_lines(buf):
            state.feed(msg)
        for _ in range(20):
            nxt = mutate(rng, engine)
            buf += proto.delta_message(proto.diff(engine, nxt))
            for msg in proto.decode_lines(buf):
                state.feed(msg)
            assert not state.needs_resync
            assert state.snapshot == nxt
            engine = nxt

    def test_identical_snapshots_give_an_empty_delta(self):
        snap = proto.Snapshot(3, {"a": {"x": 1}}, {"g": 2})
        assert proto.diff(snap, snap.copy()).empty

    def test_delta_carries_only_what_changed(self):
        old = proto.Snapshot(1, {"a": {"x": 1, "y": 2}, "b": {"z": 3}})
        new = proto.Snapshot(2, {"a": {"x": 1, "y": 5}, "c": {"w": 0}})
        delta = proto.diff(old, new)
        assert delta.set == {"a": {"y": 5}, "c": {"w": 0}}
        assert delta.removed == ["b"]
        assert delta.unset == {}

    def test_removed_field_is_unset_not_nulled(self):
        old = proto.Snapshot(1, {"a": {"x": 1, "y": 2}})
        new = proto.Snapshot(2, {"a": {"x": 1}})
        delta = proto.diff(old, new)
        assert delta.unset == {"a": ["y"]}
        assert "y" not in proto.apply(old, delta).agents["a"]

    def test_apply_does_not_mutate_its_input(self):
        old = proto.Snapshot(1, {"a": {"x": 1}})
        before = old.copy()
        proto.apply(old, proto.Delta(seq=2, set={"a": {"x": 9}}, removed=["a"]))
        assert old == before


class TestWire:
    def test_partial_lines_wait_for_the_rest(self):
        msg = proto.ping(7)
        buf = bytearray(msg[:5])
        assert list(proto.decode_lines(buf)) == []
        buf += msg[5:]
        assert list(proto.decode_lines(buf)) == [{"t": "ping", "seq": 7}]
        assert buf == b""

    def test_garbage_and_unknown_types_are_skipped(self):
        buf = bytearray(b"not json\n[1,2]\n{\"no_type\":1}\n" + proto.ping(1))
        assert [m["t"] for m in proto.decode_lines(buf)] == ["ping"]
        state = proto.ViewState()
        assert state.feed({"t": "future_message_type", "seq": 1}) is False

    def test_messages_are_one_line_each(self):
        snap = proto.Snapshot(1, {"a": {"text": "line1\nline2"}})
        assert proto.snapshot_message(snap).count(b"\n") == 1


class TestViewState:
    def _connected(self):
        state = proto.ViewState()
        state.feed({"t": "hello", "version": proto.PROTOCOL_VERSION, "seq": 4})
        state.feed(json.loads(proto.snapshot_message(proto.Snapshot(4, {"a": {"x": 1}}))))
        return state

    def test_hello_then_snapshot(self):
        state = self._connected()
        assert state.version == proto.PROTOCOL_VERSION
        assert state.snapshot.agents == {"a": {"x": 1}}

    def test_a_gap_in_seq_asks_for_a_resync(self):
        state = self._connected()
        assert state.feed({"t": "delta", "seq": 6, "set": {"a": {"x": 2}}}) is False
        assert state.needs_resync
        assert state.snapshot.agents["a"]["x"] == 1  # never applied out of order

    def test_a_delta_before_any_snapshot_asks_for_a_resync(self):
        state = proto.ViewState()
        state.feed({"t": "delta", "seq": 1})
        assert state.needs_resync

    def test_a_ping_with_a_different_seq_asks_for_a_resync(self):
        state = self._connected()
        state.feed({"t": "ping", "seq": 4})
        assert not state.needs_resync
        state.feed({"t": "ping", "seq": 9})
        assert state.needs_resync

    def test_an_empty_delta_advances_seq_but_reports_no_change(self):
        state = self._connected()
        assert state.feed({"t": "delta", "seq": 5}) is False
        assert state.snapshot.seq == 5 and not state.needs_resync
