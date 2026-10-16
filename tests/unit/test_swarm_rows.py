"""An expanded parent's delegated work as rows (0.6.1 observability, phase 2).

docs/design/agent-observability-0.6.1.md §3–§4. ``swarm.virtual_rows``
turns the parent's hook state (plus the meta files and workflow journals
beside its transcript, ``swarm_files``) into the virtual rows the engine
publishes for parents a view expanded. The capture replays run the 2.1.300
spike's verbatim hook payloads through the real hook handler and detector.
"""

import json

import pytest

from overcode import swarm
from overcode.swarm_files import SwarmFiles
from tests.status_replay import CAPTURES, Scenario, capture_steps, replay

pytestmark = pytest.mark.unit


class CaptureFiles:
    """``Details`` over a capture's ``files/`` (paths flattened with ``__``)."""

    def __init__(self, name):
        self.dir = CAPTURES / name / "files"

    def meta(self, agent_id, run_id):
        for f in self.dir.glob(f"*agent-{agent_id}.meta.json"):
            return json.loads(f.read_text())
        return None

    def journal(self, run_id):
        for f in self.dir.glob(f"*{run_id}__journal.jsonl"):
            return [json.loads(line) for line in f.read_text().splitlines()]
        return None


def _steps(name):
    """A capture's hook events without its SessionEnd (which drops the
    record), so what happens after the last turn can be watched."""
    return [s for s in capture_steps(name) if s.payload.get("hook_event_name") != "SessionEnd"]


def _replay(name, tmp_path, tail=40.0):
    steps = _steps(name)
    end = max(s.t for s in steps) + tail
    return replay(Scenario(name, steps=steps, expect=[(0.0, end, None)]), tmp_path, cadence=0.5)


def _rows(sample, name=None, keep=30.0):
    details = CaptureFiles(name) if name else None
    return swarm.virtual_rows("P", sample.swarm_state, sample.now, keep, details)[0]


def _state(subagents=None, **sw):
    sw.setdefault("bg_at", 0.0)
    return {"event": "Stop", "timestamp": 0.0, "subagents": subagents or {}, "swarm": sw}


# ---------------------------------------------------------------------------
# Hook side: what the rows need that phase 1 did not keep
# ---------------------------------------------------------------------------

class TestHookSide:
    def test_a_member_s_tool_call_is_its_activity(self):
        subs, sw = swarm.apply_subagent_event({}, {}, "SubagentStart", "a1",
                                              {"agent_type": "Explore"}, 1.0)
        subs, sw = swarm.apply_subagent_event(
            subs, sw, "PreToolUse", "a1",
            {"tool_name": "Read", "tool_input": {"file_path": "src/x.py"}}, 2.0)
        assert subs["a1"]["activity"] == "Read src/x.py"

    def test_activity_is_one_short_line(self):
        text = swarm.tool_activity("Bash", {"command": "echo " + "x" * 200 + "\nls"})
        assert len(text) == swarm.ACTIVITY_CHARS and "\n" not in text
        assert swarm.tool_activity("TodoWrite", {"todos": []}) == "TodoWrite"
        assert swarm.tool_activity(None, {}) is None

    def test_a_workflow_agent_s_stop_names_its_run(self):
        subs, sw = swarm.apply_subagent_event({}, {}, "SubagentStart", "w1",
                                              {"agent_type": "workflow-subagent"}, 1.0)
        subs, sw = swarm.apply_subagent_event(
            subs, sw, "SubagentStop", "w1",
            {"agent_transcript_path": "/p/s/subagents/workflows/wf_9/agent-w1.jsonl"}, 2.0)
        assert sw["done"]["w1"]["run"] == "wf_9"
        assert sw["done"]["w1"]["started_at"] == 1.0

    def test_the_parent_s_transcript_is_noted_once_it_delegates(self):
        data = {"transcript_path": "/p/sid.jsonl"}
        assert swarm.apply_parent_event({}, {}, "PreToolUse", "Bash", {}, data, 1.0) == ({}, {})
        _, sw = swarm.apply_subagent_event({}, {}, "SubagentStart", "a1", data, 1.0)
        assert sw["transcript"] == "/p/sid.jsonl"

    def test_a_run_s_end_is_timed(self):
        sw = {"workflows": {"t1": {"status": "running", "run_id": "wf"}}}
        _, sw = swarm.apply_parent_event(
            {}, sw, "UserPromptSubmit", None, None,
            {"prompt": "<task-notification><task-id>t1</task-id><status>completed</status>"},
            7.0)
        assert sw["workflows"]["t1"]["ended_at"] == 7.0


# ---------------------------------------------------------------------------
# Daemon side: virtual_rows
# ---------------------------------------------------------------------------

class TestRows:
    def test_never_delegated_has_no_rows(self):
        assert swarm.virtual_rows("P", None, 0.0, 30) == ({}, None)
        assert swarm.virtual_rows("P", {"event": "Stop"}, 0.0, 30) == ({}, None)

    def test_groups_then_members_keyed_by_identity(self):
        state = _state(
            {"a1": {"kind": "subagent", "type": "Explore", "started_at": 1.0},
             "aalice-1234abcd": {"kind": "teammate", "type": "alice", "idle": True},
             "w1": {"kind": "workflow", "waiting": True}},
            workflows={"t1": {"status": "running", "name": "review", "run_id": "wf_1"}},
            teammates={"aalice-1234abcd": {"name": "alice"}})
        rows, deadline = swarm.virtual_rows("P", state, 10.0, 30)
        assert deadline is None
        assert set(rows) == {"P/sa", "P/sa/a1", "P/tm", "P/tm/alice", "P/wf/wf_1", "P/wf/wf_1/w1"}
        assert [rows[k]["order"] for k in ("P/wf/wf_1", "P/tm", "P/sa")] == [0, 1000, 2000]
        assert rows["P/sa/a1"]["light"] == "green" and rows["P/sa/a1"]["title"] == "Explore"
        assert rows["P/tm/alice"]["light"] == "idle"
        assert rows["P/wf/wf_1"]["light"] == "orange"
        assert rows["P/wf/wf_1"]["counts"] == {"waiting": 1}
        assert all(r["parent"] == "P" for r in rows.values())

    def test_a_finished_member_keeps_its_row_for_the_configured_time(self):
        state = _state(done={"a2": {"kind": "subagent", "result": "failed", "ended_at": 100.0}})
        rows, deadline = swarm.virtual_rows("P", state, 110.0, 30)
        assert rows["P/sa/a2"]["light"] == "done" and rows["P/sa/a2"]["result"] == "failed"
        assert deadline == 130.0
        rows, deadline = swarm.virtual_rows("P", state, 130.0, 30)
        assert "P/sa/a2" not in rows and deadline is None
        assert rows["P/sa"]["counts"] == {"failed": 1}  # folded into the group's counts
        assert "P/sa/a2" not in swarm.virtual_rows("P", state, 100.0, 0)[0]

    def test_a_finished_run_s_group_goes_with_its_agents(self):
        state = _state(workflows={"t1": {"status": "ok", "run_id": "wf", "ended_at": 50.0}},
                       done={"w1": {"kind": "workflow", "result": "ok", "ended_at": 49.0,
                                    "run": "wf"}})
        rows, deadline = swarm.virtual_rows("P", state, 60.0, 30)
        assert {"P/wf/wf", "P/wf/wf/w1"} <= set(rows) and deadline == 79.0
        rows, _ = swarm.virtual_rows("P", state, 81.0, 30)
        assert not any(k.startswith("P/wf") for k in rows)

    def test_rows_hold_still_while_nothing_happens(self):
        state = _state({"a1": {"kind": "subagent", "started_at": 1.0, "last_seen": 2.0,
                               "activity": "Bash ls"}},
                       done={"a2": {"kind": "subagent", "result": "ok", "ended_at": 5.0}})
        frames = [swarm.virtual_rows("P", state, t, 30)[0] for t in (6.0, 12.0, 30.0)]
        assert frames[0] == frames[1] == frames[2]
        assert "last_seen" not in json.dumps(frames[0])

    def test_each_row_shows_its_own_share_of_the_spend(self):
        state = _state({"a1": {"kind": "subagent"}, "w1": {"kind": "workflow"}},
                       workflows={"t1": {"status": "running", "run_id": "wf_1"}})
        conversations = {"sid": {"cost": 4.0},
                         "sid/agent-a1": {"cost": 0.5, "in": 10, "out": 5},
                         "sid/workflows/wf_1/agent-w1": {"cost": 1.25},
                         "_legacy": {"cost": 9.0}}
        rows, _ = swarm.virtual_rows("P", state, 0.0, 30, conversations=conversations)
        assert rows["P/sa/a1"]["cost_usd"] == 0.5 and rows["P/sa/a1"]["tokens"] == 15
        assert rows["P/sa"]["cost_usd"] == 0.5
        assert rows["P/wf/wf_1"]["cost_usd"] == 1.25
        assert swarm.subagent_cost(conversations) == 1.75
        assert swarm.subagent_cost({"sid": {"cost": 4.0}}) is None

    def test_meta_files_name_the_members(self, tmp_path):
        base = tmp_path / "sid"
        (base / "subagents").mkdir(parents=True)
        (base / "subagents" / "agent-a1.meta.json").write_text(json.dumps(
            {"agentType": "general-purpose", "description": "map engine", "model": "haiku"}))
        state = _state({"a1": {"kind": "subagent"}}, transcript=str(base) + ".jsonl")
        files = SwarmFiles()
        details = files.details(state["swarm"]["transcript"])
        rows, _ = swarm.virtual_rows("P", state, 0.0, 30, details)
        assert rows["P/sa/a1"]["title"] == "map engine" and rows["P/sa/a1"]["model"] == "haiku"
        swarm.virtual_rows("P", state, 0.0, 30, files.details(state["swarm"]["transcript"]))
        assert files.reads == 1  # read once per agent

    def test_a_missing_meta_file_is_looked_for_again_later(self, tmp_path):
        now = [0.0]
        files = SwarmFiles(clock=lambda: now[0])
        path = str(tmp_path / "agent-a.meta.json")
        assert files.meta(path) is None
        (tmp_path / "agent-a.meta.json").write_text('{"description": "d"}')
        assert files.meta(path) is None  # inside the retry window
        now[0] = 5.0
        assert files.meta(path) == {"description": "d"}

    def test_a_journal_is_reread_only_when_it_moves(self, tmp_path):
        path = tmp_path / "journal.jsonl"
        path.write_text('{"type":"started","agentId":"a","label":"x","phase":"P1"}\n')
        files = SwarmFiles()
        assert len(files.journal(str(path))) == 1
        assert files.journal(str(path)) is not None and files.reads == 1
        assert not files.journals_changed()
        with open(path, "a") as f:
            f.write('{"type":"result","agentId":"a","result":"r"}\n')
        assert files.journals_changed()
        assert len(files.journal(str(path))) == 2 and files.reads == 2


# ---------------------------------------------------------------------------
# Replays of the spike captures
# ---------------------------------------------------------------------------

class TestCaptures:
    def test_a_workflow_shows_its_phases_and_their_progress(self, tmp_path):
        name = "06-workflow-slow-live-watch"
        samples = _replay(name, tmp_path)
        group = "P/wf/wf_7dd2f42b-ff0"
        mid = next(s for s in samples if s.t >= 8.0)
        rows = _rows(mid, name)
        assert rows[group]["title"] == "spike-wf-slow"
        assert rows[group]["counts"].get("running") == 3
        labels = {r["title"] for k, r in rows.items() if r.get("row") == "member"}
        assert labels == {"sleeper-1", "sleeper-2", "sleeper-3"}
        # The journal is read whole (the run's end state): two phases
        assert [rows[f"{group}/ph/{i}"]["title"] for i in (0, 1)] == ["Sleep", "Join"]
        member = rows[f"{group}/a2ae5f43fc5d905d5"]
        assert member["phase"] == f"{group}/ph/0"
        # After the run: its rows stay 30 s, then go
        last_event = max(s.t for s in _steps(name))
        after = next(s for s in samples if s.t >= last_event + 1)
        assert group in _rows(after, name)
        gone = next(s for s in samples if s.t >= last_event + 32)
        assert not any(k.startswith("P/wf") for k in _rows(gone, name))

    def test_a_background_subagent_is_named_from_its_meta_file(self, tmp_path):
        name = "03-background-subagent-with-SendMessage"
        samples = _replay(name, tmp_path)
        seen = set()
        for s in samples:
            for row in _rows(s, name).values():
                if row.get("row") == "member":
                    seen.add((row["title"], row["kind"]))
        assert ("slow waiter", "subagent") in seen

    def test_teammates_are_a_group_keyed_by_name(self, tmp_path):
        name = "08-team-in-process"
        samples = _replay(name, tmp_path)
        keys = set()
        for s in samples:
            keys |= set(_rows(s, name))
        assert {"P/tm", "P/tm/alice", "P/tm/bob"} <= keys

    def test_rows_change_only_when_an_event_lands(self, tmp_path):
        """Between hook events the rows hold still, except a finished row
        leaving at its deadline: 30 s after the event that finished it."""
        name = "06-workflow-slow-live-watch"
        times = sorted({s.t for s in _steps(name)})
        samples = _replay(name, tmp_path)
        prev, last_t, quiet_changes = None, 0.0, []
        for s in samples:
            rows, _ = swarm.virtual_rows("P", s.swarm_state, s.now, 30, CaptureFiles(name))
            if prev is not None and rows != prev and not any(last_t < t <= s.t for t in times):
                quiet_changes.append(s.t)
            prev, last_t = rows, s.t
        assert quiet_changes
        for t in quiet_changes:
            assert any(t - 0.5 < e + 30 <= t for e in times), t


# ---------------------------------------------------------------------------
# An open done line: the group's finished members, most recent first, capped
# ---------------------------------------------------------------------------


def _many_done(n, now=1000.0):
    """``n`` loose subagents finished ten minutes or so ago (oldest first)."""
    done = {f"d{i:03d}": {"kind": "subagent", "result": "failed" if i % 10 == 0 else "ok",
                          "started_at": now - 700 - i, "ended_at": now - 600 + i}
            for i in range(n)}
    return _state({"live": {"kind": "subagent", "started_at": now - 5}}, done=done)


class TestDoneLine:
    def test_finished_members_fold_into_counts_until_the_line_opens(self):
        rows, deadline = swarm.virtual_rows("P", _many_done(5), 1000.0, 30.0)
        assert sorted(k for k in rows if k.startswith("P/sa/")) == ["P/sa/live"]
        assert rows["P/sa"]["counts"] == {"running": 1, "ok": 4, "failed": 1}
        assert deadline is None

    def test_an_open_line_lists_the_most_recent_up_to_the_limit(self):
        rows, deadline = swarm.virtual_rows("P", _many_done(80), 1000.0, 30.0,
                                            done_open={"P/sa"}, done_limit=50)
        listed = [k for k, r in rows.items() if r.get("light") == "done"]
        assert set(listed) == {f"P/sa/d{i:03d}" for i in range(30, 80)}  # the newest 50
        assert rows["P/sa/d079"]["result"] == "ok" and rows["P/sa/d070"]["result"] == "failed"
        # Counts still cover every finished member; nothing here moves with the clock
        assert rows["P/sa"]["counts"] == {"running": 1, "ok": 72, "failed": 8}
        assert deadline is None
        later, _ = swarm.virtual_rows("P", _many_done(80), 5000.0, 30.0,
                                      done_open={"P/sa"}, done_limit=50)
        assert later == rows

    def test_members_still_kept_count_against_the_limit(self):
        state = _many_done(10)
        state["swarm"]["done"]["fresh"] = {"kind": "subagent", "result": "ok",
                                           "started_at": 980.0, "ended_at": 990.0}
        rows, deadline = swarm.virtual_rows("P", state, 1000.0, 30.0,
                                            done_open={"P/sa"}, done_limit=3)
        listed = sorted(k for k, r in rows.items() if r.get("light") == "done")
        assert listed == ["P/sa/d008", "P/sa/d009", "P/sa/fresh"]
        assert deadline == 1020.0  # the kept one's time still runs out on schedule

    def test_another_group_s_line_does_not_open(self):
        state = _many_done(3)
        state["subagents"]["t1"] = {"kind": "teammate", "started_at": 990.0}
        state["swarm"]["done"]["t0"] = {"kind": "teammate", "result": "ok", "ended_at": 100.0}
        rows, _ = swarm.virtual_rows("P", state, 1000.0, 30.0, done_open={"P/tm"})
        assert not any(k.startswith("P/sa/d") for k in rows)
        assert rows["P/tm/t0"]["light"] == "done"

    def test_a_zero_limit_lists_nothing(self):
        rows, _ = swarm.virtual_rows("P", _many_done(5), 1000.0, 30.0,
                                     done_open={"P/sa"}, done_limit=0)
        assert not any(r.get("light") == "done" for r in rows.values())
