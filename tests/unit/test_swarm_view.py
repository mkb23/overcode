"""How an expanded parent's virtual rows are arranged (agent-observability §4.1).

``swarm_view`` is pure: rows in (as the engine publishes them), lines out.
"""

import pytest

from overcode import swarm, swarm_view

pytestmark = pytest.mark.unit


def _workflow(n_live, orange=(), phases=None, done=0):
    subs = {f"w{i}": {"kind": "workflow", "started_at": float(i),
                      **({"waiting": True} if i in orange else {})} for i in range(n_live)}
    state = {"subagents": subs,
             "swarm": {"bg_at": 0.0,
                       "workflows": {"t1": {"status": "running", "name": "review",
                                            "run_id": "wf"}},
                       "done": {f"d{i}": {"kind": "workflow", "result": "ok", "ended_at": 1.0,
                                          "run": "wf"} for i in range(done)}}}

    class Journal:
        def meta(self, agent_id, run_id):
            return None

        def journal(self, run_id):
            out = []
            for i in range(n_live):
                out.append({"type": "started", "agentId": f"w{i}", "label": f"verify-{i}",
                            "phase": (phases or {}).get(i, "Verify")})
            return out

    rows, _ = swarm.virtual_rows("p", state, 100.0, 30, Journal() if phases is not None else None)
    return rows


def _kinds(lines):
    return [ln.kind for ln in lines]


class TestGroups:
    def test_a_small_group_opens_with_every_member(self):
        lines = swarm_view.layout("p", _workflow(3))
        assert _kinds(lines) == ["group", "member", "member", "member"]
        assert "🧩 review ▾ 3 agents" in lines[0].text

    def test_above_eight_members_a_group_shows_its_top_three_needs_you(self):
        lines = swarm_view.layout("p", _workflow(12, orange={7}))
        assert _kinds(lines) == ["group", "member", "member", "member", "more"]
        assert "▸ 12 agents" in lines[0].text
        assert lines[1].key == "p/wf/wf/w7"  # 🟠 first
        assert [ln.key for ln in lines[2:4]] == ["p/wf/wf/w0", "p/wf/wf/w1"]  # then 🟢 oldest
        assert "+9 more" in lines[4].text

    def test_a_group_the_person_closed_shows_nothing_below(self):
        lines = swarm_view.layout("p", _workflow(3), opened={"p/wf/wf": False})
        assert _kinds(lines) == ["group"]
        assert "▸" in lines[0].text

    def test_finished_members_fold_into_a_done_line(self):
        lines = swarm_view.layout("p", _workflow(2, done=4))
        assert lines[-1].kind == "done" and "4 done" in lines[-1].text

    def test_emoji_free(self):
        lines = swarm_view.layout("p", _workflow(1, orange={0}), emoji_free=True)
        assert "🧩" not in "".join(ln.text for ln in lines)


class TestPhases:
    def test_a_workflow_shows_its_phases_each_opening_on_its_own(self):
        rows = _workflow(4, phases={0: "Find", 1: "Find", 2: "Verify", 3: "Verify"})
        lines = swarm_view.layout("p", rows)
        phases = [ln for ln in lines if ln.kind == "phase"]
        assert [p.text.split()[-2:] for p in phases] == [["Find", "0/2"], ["Verify", "0/2"]]
        closed = swarm_view.layout("p", rows, opened={"p/wf/wf/ph/0": False})
        assert [ln.kind for ln in closed].count("member") == 2

    def test_a_large_phase_opens_collapsed_with_its_top_three(self):
        rows = _workflow(10, phases={i: "Verify" for i in range(10)})
        lines = swarm_view.layout("p", rows, opened={"p/wf/wf": True})
        assert _kinds(lines) == ["group", "phase", "member", "member", "member", "more"]


class TestStep:
    def test_down_enters_up_leaves_and_detail_lines_are_skipped(self):
        rows = _workflow(2)
        lines = swarm_view.layout("p", rows, details={"p/wf/wf/w0"})
        assert swarm_view.step(lines, None, 1) == ("p/wf/wf", True)
        assert swarm_view.step(lines, "p/wf/wf/w0", 1) == ("p/wf/wf/w1", True)
        assert swarm_view.step(lines, "p/wf/wf/w1", 1) == (None, False)
        assert swarm_view.step(lines, "p/wf/wf", -1) == (None, True)
        assert swarm_view.step(lines, None, -1) == (None, False)
