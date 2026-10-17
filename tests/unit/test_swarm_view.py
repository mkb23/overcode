"""How an expanded parent's virtual rows are arranged (agent-observability §4.1).

``swarm_view`` is pure: rows in (as the engine publishes them), lines out.
Flat: the members sit right under the agent's row, then one done line.
"""

import pytest

from overcode import swarm, swarm_view

pytestmark = pytest.mark.unit


def _workflow(n_live, orange=(), phases=None, done=0, yellow=()):
    subs = {f"w{i}": {"kind": "workflow", "started_at": float(i),
                      **({"waiting": True} if i in orange else {}),
                      **({"own_work": True} if i in yellow else {})} for i in range(n_live)}
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


def _loose(n_live, n_done, orange=(), types=None):
    subs = {f"a{i}": {"kind": "subagent", "started_at": 50.0 + i,
                      "type": (types or {}).get(i, "general-purpose"),
                      **({"waiting": True} if i in orange else {})} for i in range(n_live)}
    done = {f"d{i}": {"kind": "subagent", "result": "failed" if i == 1 else "ok",
                      "started_at": 1.0, "ended_at": 10.0 + i} for i in range(n_done)}
    return {"subagents": subs, "swarm": {"bg_at": 0.0, "done": done}}


def _kinds(lines):
    return [ln.kind for ln in lines]


class TestFlat:
    def test_members_sit_right_under_the_agent_with_no_group_lines(self):
        lines = swarm_view.layout("p", _workflow(3))
        assert _kinds(lines) == ["member", "member", "member"]
        assert all(ln.depth == 1 and ln.owner == "" for ln in lines)
        assert lines[0].marker == "🧩" and lines[0].label == "w0"

    def test_every_group_s_members_in_one_list_orange_first_then_by_start(self):
        rows = _workflow(2, orange={1})
        rows.update(swarm.virtual_rows("p", _loose(2, 0), 100.0, 30)[0])
        lines = swarm_view.layout("p", rows)
        assert [ln.key for ln in lines] == ["p/wf/wf/w1", "p/wf/wf/w0", "p/sa/a0", "p/sa/a1"]

    def test_tree_connectors_are_indented_below_the_parent(self):
        lines = swarm_view.layout("p", _workflow(2, done=1), base_depth=1)
        assert [ln.connector for ln in lines] == ["  ├─", "  └─"]
        assert _kinds(lines) == ["member", "member"]

    def test_emoji_free_markers(self):
        assert swarm_view.marker_glyph("🧩", emoji_free=True) == "Wk"
        assert swarm_view.marker_glyph("👥", emoji_free=True) == "Sa"
        assert swarm_view.light_glyph({"light": "orange"}, emoji_free=True)[0] == "[A]"


class TestType:
    def test_a_subagent_s_type_fork_and_teammate(self):
        assert swarm_view.type_label({"kind": "subagent", "agent_type": "Explore"}) == \
            ("Explore", False)
        assert swarm_view.type_label({"kind": "subagent"}) == ("subagent", False)
        assert swarm_view.type_label({"kind": "fork", "agent_type": "fork"}) == ("fork", False)
        assert swarm_view.type_label({"kind": "teammate", "agent_type": "alice"}) == \
            ("teammate", False)

    def test_a_workflow_agent_shows_its_workflow_and_phase_when_it_fits(self):
        row = {"kind": "workflow", "workflow": "review-060", "phase_title": "Verify"}
        assert swarm_view.type_label(row) == ("review-060 · Verify", True)
        long = dict(row, phase_title="Cross-check every finding")
        assert swarm_view.type_label(long) == ("review-060", False)

    def test_a_phase_that_does_not_fit_goes_into_the_summary(self):
        rows = _workflow(1, phases={0: "Cross-check every finding twice"})
        member = swarm_view.layout("p", rows)[0]
        assert member.type_label == "review"
        assert member.summary.startswith("Cross-check every finding twice")
        assert "phase Cross-check" in member.context

    def test_types_of_counts_distinct_member_types(self):
        rows = swarm.virtual_rows("p", _loose(3, 0, types={2: "Explore"}), 100.0, 30)[0]
        assert swarm_view.types_of(swarm_view.layout("p", rows)) == {"general-purpose",
                                                                     "Explore"}
        single = swarm.virtual_rows("p", _loose(3, 0), 100.0, 30)[0]
        assert swarm_view.types_of(swarm_view.layout("p", single)) == {"general-purpose"}


class TestAutoCollapse:
    def test_above_eight_live_members_the_top_three_needs_you_and_more(self):
        lines = swarm_view.layout("p", _workflow(12, orange={7}))
        assert _kinds(lines) == ["member", "member", "member", "more"]
        assert lines[0].key == "p/wf/wf/w7"  # 🟠 first
        assert [ln.key for ln in lines[1:3]] == ["p/wf/wf/w0", "p/wf/wf/w1"]  # then oldest
        more = lines[3]
        assert more.label == "+9 more" and more.counts == (("more", 9),)
        assert more.summary == "→ to open" and more.owner == ""

    def test_counted_across_groups(self):
        rows = _workflow(5)
        rows.update(swarm.virtual_rows("p", _loose(5, 0), 100.0, 30)[0])
        assert _kinds(swarm_view.layout("p", rows))[-1] == "more"

    def test_members_waiting_on_their_own_work_still_show(self):
        lines = swarm_view.layout("p", _workflow(10, yellow=set(range(10))))
        assert _kinds(lines) == ["member", "member", "member", "more"]

    def test_opened_it_lists_every_live_member(self):
        lines = swarm_view.layout("p", _workflow(12), opened={"p/members": True})
        assert _kinds(lines) == ["member"] * 12
        assert swarm_view.members_key("p") == "p/members"

    def test_a_collapsed_list_ends_at_its_more_line(self):
        lines = swarm_view.layout("p", _workflow(12, done=3))
        assert _kinds(lines) == ["member", "member", "member", "more"]


class TestNoDoneLine:
    """Finished members are a count in the agent's SubAgents cell, not a
    line (owner, 2026-10-18: the done line repeated that cell)."""

    def test_long_finished_members_leave_no_line(self):
        rows, _ = swarm.virtual_rows("p", _loose(1, 5), 100.0, 30)
        assert _kinds(swarm_view.layout("p", rows)) == ["member"]

    def test_one_that_finished_moments_ago_keeps_its_row(self):
        state = _loose(1, 0)
        state["swarm"]["done"]["fresh"] = {"kind": "subagent", "result": "ok",
                                           "started_at": 90.0, "ended_at": 95.0}
        rows, _ = swarm.virtual_rows("p", state, 100.0, 30)
        assert [ln.key for ln in swarm_view.layout("p", rows)] == ["p/sa/a0", "p/sa/fresh"]

    def test_old_saved_keys_mean_nothing(self):
        rows, _ = swarm.virtual_rows("p", _loose(2, 2), 100.0, 30)
        lines = swarm_view.layout("p", rows, opened={"p/done": True, "p/sa": False,
                                                     "p/sa/done": True, "p/wf/x/ph/0": True})
        assert _kinds(lines) == ["member", "member"]


class TestNeedsYou:
    def test_only_the_orange_members_until_the_rest_are_opened(self):
        rows = _workflow(3, orange={1})
        rows.update(swarm.virtual_rows("p", _loose(2, 0), 100.0, 30)[0])
        lines = swarm_view.layout("p", rows, needs_you=True)
        assert [ln.key for ln in lines] == ["p/wf/wf/w1", "p/+more"]
        assert lines[-1].label == "+4 more"
        opened = swarm_view.layout("p", rows, opened={"p/members": True}, needs_you=True)
        assert len(opened) == 5

    def test_without_an_orange_member_it_lists_as_usual(self):
        rows = swarm.virtual_rows("p", _loose(2, 0), 100.0, 30)[0]
        assert _kinds(swarm_view.layout("p", rows, needs_you=True)) == ["member", "member"]


class TestStep:
    def test_down_enters_up_leaves_and_detail_lines_are_skipped(self):
        rows = _workflow(2)
        lines = swarm_view.layout("p", rows, details={"p/wf/wf/w0"})
        assert swarm_view.step(lines, None, 1) == ("p/wf/wf/w0", True)
        assert swarm_view.step(lines, "p/wf/wf/w0", 1) == ("p/wf/wf/w1", True)
        assert swarm_view.step(lines, "p/wf/wf/w1", 1) == (None, False)
        assert swarm_view.step(lines, "p/wf/wf/w0", -1) == (None, True)
        assert swarm_view.step(lines, None, -1) == (None, False)
