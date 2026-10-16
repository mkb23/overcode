"""swarm_history.csv: the timeline's density rows (0.6.1 observability, phase 4).

docs/design/agent-observability-0.6.1.md §5. The writer appends a row per
group when a parent's delegated-work counts change (never per tick), the
reader is incremental with a carry at the window's left edge, and
``density`` turns points into slots (time-weighted mean, worst light,
failures).
"""

import csv
from datetime import datetime, timedelta

import pytest

from overcode import swarm_history as sh
from overcode.swarm_history import Point, SwarmHistoryWriter

pytestmark = pytest.mark.unit

T0 = 1_791_370_000.0


def _state(ts=T0, live=None, done=None, workflows=None, teammates=None):
    return {"event": "PreToolUse", "timestamp": ts,
            "subagents": live if live is not None else {
                "a1": {"kind": "subagent", "started_at": T0 - 10},
                "a2": {"kind": "subagent", "started_at": T0 - 5, "waiting": True}},
            "swarm": {"bg_at": T0, "done": done or {}, "workflows": workflows or {},
                      "teammates": teammates or {}}}


def _rollup(state, now=T0):
    from overcode import swarm
    return swarm.rollup(state, now)


def _rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture
def writer(tmp_path):
    return SwarmHistoryWriter(tmp_path / sh.SWARM_HISTORY_FILE)


class TestGroupCounts:
    def test_groups_by_kind_with_the_whole_swarm_first(self):
        state = _state(live={
            "a1": {"kind": "subagent"},
            "w1": {"kind": "workflow", "waiting": True},
            "atm-0123abcd": {"kind": "teammate", "idle": True},
        }, workflows={"t1": {"status": "running", "name": "review", "run_id": "wf_1"}},
            teammates={"atm-0123abcd": {"name": "tm"}})
        groups = sh.group_counts(state, _rollup(state), T0)
        assert set(groups) == {"*", "wf/wf_1", "tm", "sa"}
        assert groups["wf/wf_1"][:2] == ("workflow", "review")
        assert groups["wf/wf_1"][2][2] == 1           # waiting
        assert groups["tm"][2][3] == 1                # idle teammate
        assert groups["*"][2][:4] == (1, 0, 1, 1)     # live summed over groups
        assert groups["*"][2][6] == 1                 # one workflow running

    def test_no_rollup_no_groups(self):
        assert sh.group_counts(_state(), None, T0) == {}


class TestWriter:
    def test_first_sight_writes_the_swarm_and_its_groups(self, writer):
        state = _state()
        assert writer.record("id-p", "p", state, _rollup(state), T0) == 2
        rows = _rows(writer.path)
        assert [r["group"] for r in rows] == ["*", "sa"]
        assert rows[0]["running"] == "1" and rows[0]["waiting"] == "1"
        assert list(rows[0]) == sh.HEADER

    def test_never_delegated_writes_nothing_and_counts_nothing(self, writer):
        assert writer.record("id-p", "p", {"event": "Stop", "timestamp": T0}, None, T0) == 0
        assert not writer.path.exists() and writer.computes == 0

    def test_a_quiet_swarm_does_no_work_between_events(self, writer):
        state = _state()
        writer.record("id-p", "p", state, _rollup(state), T0)
        computes = writer.computes
        for i in range(1, 100):  # ticks with the same hook state, under the keepalive
            assert writer.record("id-p", "p", state, _rollup(state), T0 + i) == 0
        assert writer.computes == computes
        assert len(_rows(writer.path)) == 2

    def test_a_hook_event_that_moves_nothing_writes_nothing(self, writer):
        state = _state()
        writer.record("id-p", "p", state, _rollup(state), T0)
        moved = _state(ts=T0 + 3)  # a tool call: new timestamp, same counts
        assert writer.record("id-p", "p", moved, _rollup(moved), T0 + 3) == 0
        assert len(_rows(writer.path)) == 2

    def test_a_member_finishing_writes_its_group_and_the_swarm(self, writer):
        state = _state()
        writer.record("id-p", "p", state, _rollup(state), T0)
        later = _state(ts=T0 + 5, live={"a1": {"kind": "subagent"}},
                       done={"a2": {"kind": "subagent", "result": "failed", "ended_at": T0 + 5}})
        assert writer.record("id-p", "p", later, _rollup(later), T0 + 5) == 2
        last = _rows(writer.path)[-2:]
        assert [r["group"] for r in last] == ["*", "sa"]
        assert last[0]["failed"] == "1" and last[0]["waiting"] == "0"

    def test_live_groups_get_a_keepalive_quiet_ones_never(self, writer):
        state = _state()
        writer.record("id-p", "p", state, _rollup(state), T0)
        assert writer.record("id-p", "p", state, _rollup(state), T0 + sh.KEEPALIVE_SECONDS) == 2
        done = _state(ts=T0 + 400, live={},
                      done={"a1": {"result": "ok", "ended_at": T0 + 400}})
        writer.record("id-p", "p", done, _rollup(done), T0 + 400)
        n = len(_rows(writer.path))
        for t in (1000, 5000, 50_000):
            assert writer.record("id-p", "p", done, _rollup(done), T0 + t) == 0
        assert len(_rows(writer.path)) == n

    def test_a_cleared_record_closes_its_live_groups(self, writer):
        state = _state()
        writer.record("id-p", "p", state, _rollup(state), T0)
        cleared = {"event": "SessionEnd", "timestamp": T0 + 9}
        assert writer.record("id-p", "p", cleared, None, T0 + 9) == 2
        assert all(sh.live_of(tuple(int(r[k]) for k in sh.COUNT_FIELDS)) == 0
                   for r in _rows(writer.path)[-2:])
        # and then nothing more
        assert writer.record("id-p", "p", cleared, None, T0 + 900) == 0

    def test_an_agent_that_leaves_the_fleet_is_closed(self, writer):
        state = _state()
        writer.record("id-p", "p", state, _rollup(state), T0)
        assert writer.forget({"id-other"}, T0 + 20) == 2
        assert writer.forget({"id-other"}, T0 + 40) == 0


class TestReader:
    def _write(self, path, rows):
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(sh.HEADER)
            for ts, group, counts in rows:
                w.writerow([ts.isoformat(), "p", "id-p", group, "agents", "agents", *counts])

    def test_points_by_agent_and_group(self, tmp_path):
        now = datetime(2026, 10, 17, 12, 0, 0)
        path = tmp_path / sh.SWARM_HISTORY_FILE
        self._write(path, [(now - timedelta(minutes=30), "*", (2, 0, 0, 0, 0, 0, 0)),
                           (now - timedelta(minutes=30), "sa", (2, 0, 0, 0, 0, 0, 0)),
                           (now - timedelta(minutes=10), "*", (0, 0, 0, 0, 2, 0, 0))])
        out = sh.SwarmHistoryFile(path).read(1.0, now)
        assert [p.live for p in out[("id-p", "*")]] == [2, 0]
        assert len(out[("id-p", "sa")]) == 1

    def test_a_live_row_before_the_window_is_carried_to_its_edge(self, tmp_path):
        now = datetime(2026, 10, 17, 12, 0, 0)
        path = tmp_path / sh.SWARM_HISTORY_FILE
        self._write(path, [(now - timedelta(hours=1, minutes=3), "*", (3, 0, 0, 0, 0, 0, 0)),
                           (now - timedelta(hours=1, minutes=30), "sa", (3, 0, 0, 0, 0, 0, 0))])
        out = sh.SwarmHistoryFile(path).read(1.0, now)
        carried = out[("id-p", "*")][0]
        assert carried.carried and carried.ts == now - timedelta(hours=1) and carried.live == 3
        assert ("id-p", "sa") not in out  # older than STALE_AFTER: not live any more

    def test_cache_hit_and_tail_reads(self, tmp_path):
        now = datetime.now()
        path = tmp_path / sh.SWARM_HISTORY_FILE
        self._write(path, [(now - timedelta(minutes=5), "*", (1, 0, 0, 0, 0, 0, 0))])
        reader = sh.SwarmHistoryFile(path)
        reader.read(3.0)
        reader.read(3.0)
        assert (reader.full_reads, reader.tail_reads) == (1, 0)
        with open(path, "a", newline="") as f:
            csv.writer(f).writerow([now.isoformat(), "p", "id-p", "*", "swarm", "",
                                    0, 0, 0, 0, 1, 0, 0])
        out = reader.read(3.0)
        assert (reader.full_reads, reader.tail_reads) == (1, 1)
        assert [p.live for p in out[("id-p", "*")]] == [1, 0]

    def test_a_rotated_file_is_read_afresh(self, tmp_path):
        from overcode.status_history import rotate_and_retain

        now = datetime.now()
        path = tmp_path / sh.SWARM_HISTORY_FILE
        self._write(path, [(now - timedelta(days=9), "*", (5, 0, 0, 0, 0, 0, 0)),
                           (now - timedelta(minutes=5), "*", (1, 0, 0, 0, 0, 0, 0))])
        reader = sh.SwarmHistoryFile(path)
        assert len(reader.read(3.0)[("id-p", "*")]) == 1
        result = rotate_and_retain(path, now=now)
        assert result["archived"] is not None and result["archived"].name.startswith("swarm_history.")
        assert len(reader.read(3.0)[("id-p", "*")]) == 1
        assert reader.full_reads == 2

    def test_writer_and_reader_round_trip(self, tmp_path, writer):
        state = _state()
        now = datetime.now().timestamp()
        writer.record("id-p", "p", state, _rollup(state, now), now)
        out = sh.read_swarm_history(writer.path, 1.0)
        assert out["id-p"]["*"][-1].counts[:4] == (1, 0, 1, 0)


def _pt(now, minutes_ago, counts, carried=False):
    return Point(now - timedelta(minutes=minutes_ago), counts, "agents", "agents", carried)


class TestDensity:
    NOW = datetime(2026, 10, 17, 12, 0, 0)

    def test_height_is_the_time_weighted_mean(self):
        # 10 slots over an hour, 6 min each: 4 live for 3 min, then 0
        pts = [_pt(self.NOW, 60, (4, 0, 0, 0, 0, 0, 0)), _pt(self.NOW, 57, sh.ZERO)]
        slots = sh.density(pts, 10, 1.0, self.NOW)
        assert slots[0].mean == pytest.approx(2.0)
        assert slots[1].mean == 0 and slots[1].light is None

    def test_colour_is_the_worst_light_in_the_slot(self):
        pts = [_pt(self.NOW, 60, (3, 0, 0, 0, 0, 0, 0)), _pt(self.NOW, 59, (2, 0, 1, 0, 0, 0, 0)),
               _pt(self.NOW, 58, (0, 2, 0, 0, 0, 0, 0))]
        slots = sh.density(pts, 10, 1.0, self.NOW)
        assert slots[0].light == "orange"
        assert slots[1].light == "yellow"

    def test_a_failure_marks_its_slot(self):
        pts = [_pt(self.NOW, 60, (2, 0, 0, 0, 0, 0, 0)), _pt(self.NOW, 30, (1, 0, 0, 0, 0, 1, 0))]
        slots = sh.density(pts, 10, 1.0, self.NOW)
        assert [i for i, s in enumerate(slots) if s and s.failed] == [5]

    def test_a_carried_point_marks_no_failure(self):
        pts = [_pt(self.NOW, 60, (2, 0, 0, 0, 0, 3, 0), carried=True)]
        assert not any(s and s.failed for s in sh.density(pts, 10, 1.0, self.NOW))

    def test_a_live_row_with_no_follow_up_goes_stale(self):
        pts = [_pt(self.NOW, 60, (2, 0, 0, 0, 0, 0, 0))]
        slots = sh.density(pts, 60, 1.0, self.NOW)  # a slot a minute
        stale_minutes = int(sh.STALE_AFTER_SECONDS / 60)
        assert slots[stale_minutes - 1] is not None and slots[stale_minutes + 1] is None

    def test_glyphs_scale_to_the_peak(self):
        slots = [None, sh.Slot(0.0, None, False), sh.Slot(1.0, "green", False),
                 sh.Slot(8.0, "green", False)]
        assert sh.glyphs(slots) == [" ", "·", "▁", "█"]
