"""Making a root agent a child (``overcode adopt``): release's reverse.

The child and its subtree join the parent's tree (SUB$, cascade kill,
oversight), bill to the parent's code from then on, and keep a budget
that reclaim never hands to the parent.
"""

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from overcode.session_manager import SessionManager

pytestmark = pytest.mark.unit

MAX_DEPTH = 5


def _agent(sm, name, parent=None, cost=0.0, **fields):
    s = sm.create_session(name=name, tmux_session="agents", tmux_window=f"{name}-w",
                          command=["claude"], start_directory="/tmp")
    if parent:
        fields["parent_session_id"] = parent
    if fields:
        sm.update_session(s.id, **fields)
    if cost:
        sm.update_stats(s.id, estimated_cost_usd=cost)
    return sm.get_session(s.id)


@pytest.fixture
def sm(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERCODE_DIR", str(tmp_path / "oc"))
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "oc" / "sessions"))
    return SessionManager(skip_git_detection=True)


class TestAdoptRecord:
    def test_a_root_and_its_subtree_join_the_parent(self, sm):
        head = _agent(sm, "head", cost=4.0)
        stray = _agent(sm, "stray", cost=2.0)
        helper = _agent(sm, "helper", parent=stray.id, cost=1.0)
        out = sm.adopt_child(stray.id, head.id, MAX_DEPTH)
        assert out["cost_usd"] == pytest.approx(3.0)
        assert out["subtree"] == [stray.id, helper.id]
        adopted = sm.get_session(stray.id)
        assert adopted.parent_session_id == head.id
        assert adopted.adopted_by_session_id == head.id and adopted.adopted_by_name == "head"
        assert adopted.adopted_at
        assert sm._subtree_spend(head.id, sm._load_state()) == pytest.approx(7.0)

    def test_an_agent_with_a_parent_is_refused(self, sm):
        head = _agent(sm, "head")
        other = _agent(sm, "other")
        kid = _agent(sm, "kid", parent=other.id)
        with pytest.raises(ValueError, match="already has a parent .*'other'"):
            sm.adopt_child(kid.id, head.id, MAX_DEPTH)

    def test_no_cycles(self, sm):
        root = _agent(sm, "root")
        kid = _agent(sm, "kid", parent=root.id)
        with pytest.raises(ValueError, match="ancestor"):
            sm.adopt_child(root.id, kid.id, MAX_DEPTH)
        with pytest.raises(ValueError, match="itself"):
            sm.adopt_child(root.id, root.id, MAX_DEPTH)

    def test_the_depth_limit_counts_the_whole_subtree(self, sm):
        head = _agent(sm, "head")
        stray = _agent(sm, "stray")
        a = _agent(sm, "a", parent=stray.id)
        _agent(sm, "b", parent=a.id)
        # head(0) -> stray(1) -> a(2) -> b(3): fits under 4, not under 3
        with pytest.raises(ValueError, match="depth"):
            sm.adopt_child(stray.id, head.id, 3)
        sm.adopt_child(stray.id, head.id, 4)

    def test_reclaim_never_refunds_the_budget_it_came_with(self, sm):
        head = _agent(sm, "head", cost_budget_usd=10.0)
        stray = _agent(sm, "stray", cost=1.0, cost_budget_usd=5.0)
        sm.adopt_child(stray.id, head.id, MAX_DEPTH)
        assert sm.reclaim_budget(stray.id) == 0.0
        assert sm.get_session(head.id).cost_budget_usd == 10.0
        # What the parent gives it afterwards can come back
        sm.update_session(stray.id, cost_budget_usd=8.0)
        assert sm.reclaim_budget(stray.id) == pytest.approx(3.0)
        assert sm.get_session(head.id).cost_budget_usd == pytest.approx(13.0)


# -- the CLI -----------------------------------------------------------------

@pytest.fixture
def fleet(tmp_path, monkeypatch):
    from tests.unit.test_cli import Fleet
    monkeypatch.setenv("OVERCODE_DIR", str(tmp_path / "oc"))
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "oc" / "sessions"))
    f = Fleet()
    monkeypatch.setattr("overcode.cli.agent.AgentLauncher",
                        lambda tmux_session="agents", *a, **kw: f.launcher(tmux_session))
    return f


class TestAdoptCommand:
    def _run(self, *args):
        from overcode.cli import app
        with patch("overcode.cli.split.open_split") as open_split:
            result = CliRunner().invoke(app, ["adopt", *args])
        open_split.assert_not_called()
        return result

    def test_adopts_rebills_and_tells_each_agent(self, fleet):
        head = fleet.add("head")
        fleet.add("w1", stats={"estimated_cost_usd": 0.5})
        fleet.add("w2")
        result = self._run("w1", "w2", "--parent", "head")
        assert result.exit_code == 0, result.output
        assert "Adopted 'w1' under 'head'" in result.output
        assert "Adopted 'w2' under 'head'" in result.output
        for name in ("w1", "w2"):
            kid = fleet.get(name)
            assert kid.parent_session_id == head.id
            assert kid.billing_code_id == fleet.get("head").billing_code_id
            typed = [k for (_s, w, k, _e) in fleet.tmux.sent_keys if w == f"{name}-w"]
            assert typed and "now a child of the agent 'head'" in typed[-1]
            assert "overcode report" in typed[-1]

    def test_no_notify_types_nothing(self, fleet):
        fleet.add("head")
        fleet.add("w1")
        result = self._run("w1", "--parent", "head", "--no-notify")
        assert result.exit_code == 0, result.output
        assert not [k for k in fleet.tmux.sent_keys if k[1] == "w1-w"]

    def test_outside_an_agent_a_parent_is_required(self, fleet):
        fleet.add("w1")
        result = self._run("w1")
        assert result.exit_code == 1 and "--parent is required" in result.output

    def test_an_agent_adopts_into_itself_by_default(self, fleet, monkeypatch):
        head = fleet.add("head")
        fleet.add("w1")
        monkeypatch.setenv("OVERCODE_SESSION_ID", head.id)
        result = self._run("w1")
        assert result.exit_code == 0, result.output
        assert fleet.get("w1").parent_session_id == head.id

    def test_an_agent_cannot_adopt_into_an_agent_outside_its_tree(self, fleet, monkeypatch):
        head = fleet.add("head")
        fleet.add("someone-else")
        fleet.add("w1")
        monkeypatch.setenv("OVERCODE_SESSION_ID", head.id)
        result = self._run("w1", "--parent", "someone-else")
        assert result.exit_code == 1 and "only into itself" in result.output
        assert fleet.get("w1").parent_session_id is None

    def test_a_failure_does_not_stop_the_rest(self, fleet):
        head = fleet.add("head")
        other = fleet.add("other")
        fleet.add("taken", parent_session_id=other.id)
        fleet.add("w1")
        result = self._run("taken", "ghost", "w1", "--parent", "head")
        assert result.exit_code == 1
        assert "already has a parent" in result.output and "'ghost' not found" in result.output
        assert fleet.get("w1").parent_session_id == head.id

    def test_show_has_the_trace(self, fleet):
        from overcode.cli.agent import _release_lines
        head = fleet.add("head")
        w1 = fleet.add("w1")
        fleet.sm.adopt_child(w1.id, head.id, MAX_DEPTH)
        (label, value), = _release_lines(fleet.get("w1"))
        assert label == "Adopted" and value.startswith("by head at ")
