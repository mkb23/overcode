"""Freeing a child from its parent (#520, ``overcode release``).

The child becomes a root agent: out of the parent's tree, its oversight
and report duty gone, no budget reclaim. What it had spent until then
stays in the parent's subtree total.
"""

from collections import deque
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from overcode.monitor_daemon import MonitorDaemon
from overcode.monitor_daemon_state import SessionDaemonState
from overcode.session_manager import Session, SessionManager
from overcode.status_constants import STATUS_WAITING_OVERSIGHT

pytestmark = pytest.mark.unit

RESET = "2026-10-20T12:00:00+00:00"


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
    """This test's own state dir: a SessionManager() built elsewhere reads it too."""
    monkeypatch.setenv("OVERCODE_DIR", str(tmp_path / "oc"))
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "oc" / "sessions"))
    return SessionManager(skip_git_detection=True)


@pytest.fixture
def tree(sm):
    """parent ($4) -> child ($2) -> grandchild ($1); child on a 30 min timeout."""
    parent = _agent(sm, "parent", cost=4.0)
    child = _agent(sm, "child", parent=parent.id, cost=2.0, oversight_policy="timeout",
                   oversight_timeout_seconds=1800.0, oversight_deadline="2026-10-26T12:00:00",
                   oversight_deadline_at=1.0, cost_budget_usd=5.0)
    grandchild = _agent(sm, "grandchild", parent=child.id, cost=1.0)
    return parent, child, grandchild


def _rollup(sm, costs=None):
    """The daemon's SUB$ over the live records: {name: (subtree, trip subtree)}."""
    index = sm.index()
    states = []
    for s in sm.list_sessions():
        trip = None
        if s.trip_base:
            trip = {"estimated_cost_usd": max(
                0.0, s.stats.estimated_cost_usd - s.trip_base["estimated_cost_usd"])}
        states.append(SessionDaemonState(session_id=s.id, name=s.name,
                                         estimated_cost_usd=s.stats.estimated_cost_usd,
                                         trip=trip))
    MonitorDaemon._compute_subtree_costs(None, states, index)
    return {st.name: (st.subtree_cost_usd, st.subtree_trip_cost_usd) for st in states}


# -- the record -----------------------------------------------------------

class TestReleaseRecord:
    def test_the_child_becomes_a_root_with_a_trace(self, sm, tree):
        parent, child, grandchild = tree
        out = sm.release_child(child.id)
        assert out["parent_id"] == parent.id and out["parent_name"] == "parent"
        assert out["cost_usd"] == pytest.approx(3.0)  # the child and its grandchild
        freed = sm.get_session(child.id)
        assert freed.parent_session_id is None
        assert freed.released_from_session_id == parent.id
        assert freed.released_from_name == "parent"
        assert freed.released_at == out["released_at"]
        assert freed.cost_at_release_usd == pytest.approx(3.0)
        # Its own subtree goes with it
        assert sm.get_session(grandchild.id).parent_session_id == child.id
        assert [d.name for d in sm.get_descendants(parent.id)] == []

    def test_oversight_ends(self, sm, tree):
        _, child, _ = tree
        sm.update_session_status(child.id, STATUS_WAITING_OVERSIGHT)
        sm.release_child(child.id)
        freed = sm.get_session(child.id)
        assert freed.oversight_policy == "wait" and freed.oversight_timeout_seconds == 0.0
        assert freed.oversight_deadline is None and freed.oversight_deadline_at is None
        assert freed.status == "running"  # the daemon reads it afresh

    def test_budget_and_preset_stay(self, sm, tree):
        _, child, _ = tree
        sm.update_session(child.id, archive_after_seconds=600.0)
        sm.release_child(child.id)
        freed = sm.get_session(child.id)
        assert freed.cost_budget_usd == 5.0 and freed.archive_after_seconds == 600.0

    def test_a_root_or_unknown_agent_is_not_released(self, sm, tree):
        parent, _, _ = tree
        assert sm.release_child(parent.id) is None
        assert sm.release_child("nope") is None
        assert sm.get_session(parent.id).released_children_cost_usd == 0.0

    def test_the_billing_code_is_unchanged(self, sm, tree):
        _, child, _ = tree
        sm.update_session(child.id, billing_code_id="c_abc", billing_code="acme")
        sm.release_child(child.id)
        freed = sm.get_session(child.id)
        assert (freed.billing_code_id, freed.billing_code) == ("c_abc", "acme")

    def test_a_codeless_child_keeps_the_code_it_inherited(self, sm):
        root = _agent(sm, "root")
        sm.update_session(root.id, billing_code_id="c_root", billing_code="client-x")
        # As an older overcode launched it: no code of its own
        child = _agent(sm, "kid", parent=root.id, billing_code_id=None, billing_code=None)
        with patch.object(SessionManager, "_inherited_codes",
                          return_value={child.id: ("c_root", "client-x")}):
            sm.release_child(child.id)
        freed = sm.get_session(child.id)
        assert (freed.billing_code_id, freed.billing_code) == ("c_root", "client-x")

    def test_old_records_load_without_the_new_fields(self):
        s = Session.from_dict({"id": "a", "name": "a", "tmux_session": "t", "tmux_window": "w",
                               "command": ["claude"], "start_directory": "/tmp",
                               "start_time": RESET, "stats": {}})
        assert s.released_from_session_id is None and s.released_at is None
        assert s.released_children_cost_usd == 0.0 and s.cost_at_release_usd is None


# -- spend: what was spent under the parent stays with it --------------------

class TestSubtreeRollup:
    def test_before_and_after(self, sm, tree):
        parent, child, grandchild = tree
        assert _rollup(sm)["parent"][0] == pytest.approx(7.0)
        sm.release_child(child.id)
        assert sm.get_session(parent.id).released_children_cost_usd == pytest.approx(3.0)
        # The child and grandchild spend more after the release
        sm.update_stats(child.id, estimated_cost_usd=10.0)
        sm.update_stats(grandchild.id, estimated_cost_usd=4.0)
        rows = _rollup(sm)
        assert rows["parent"][0] == pytest.approx(7.0)  # $4 own + $3 at release
        assert rows["child"][0] == pytest.approx(14.0)  # its whole subtree, its own now

    def test_a_parent_with_no_children_left_still_shows_its_sub(self, sm):
        parent = _agent(sm, "parent", cost=1.0)
        kid = _agent(sm, "kid", parent=parent.id, cost=2.0)
        sm.release_child(kid.id)
        assert _rollup(sm)["parent"][0] == pytest.approx(3.0)
        assert _rollup(sm)["kid"][0] == 0.0  # a leaf: no SUB$

    def test_the_carry_rolls_up_to_the_grandparent(self, sm, tree):
        parent, child, grandchild = tree
        sm.release_child(grandchild.id)
        rows = _rollup(sm)
        assert rows["child"][0] == pytest.approx(3.0)
        assert rows["parent"][0] == pytest.approx(7.0)

    def test_released_twice_is_counted_once(self, sm, tree):
        parent, child, _ = tree
        sm.release_child(child.id)
        sm.release_child(child.id)
        assert sm.get_session(parent.id).released_children_cost_usd == pytest.approx(3.0)

    def test_a_trip_counts_only_what_was_released_after_its_reset(self, sm):
        parent = _agent(sm, "parent", cost=4.0)
        early = _agent(sm, "early", parent=parent.id, cost=1.0)
        late = _agent(sm, "late", parent=parent.id, cost=2.0)
        sm.release_child(early.id)
        sm.reset_trips([parent.id, late.id], RESET)
        assert sm.get_session(parent.id).trip_base["released_children_cost_usd"] == 1.0
        sm.update_stats(late.id, estimated_cost_usd=2.5)  # $0.50 since the reset
        sm.release_child(late.id)
        sub, trip = _rollup(sm)["parent"]
        assert sub == pytest.approx(7.5)  # $4 + $1 + $2.50
        assert trip == pytest.approx(2.5)  # only the late child's $2.50 is since the reset

    def test_budget_show_counts_the_carry(self, sm, tree, monkeypatch):
        from overcode.cli import app
        parent, child, _ = tree
        sm.release_child(child.id)
        result = CliRunner().invoke(app, ["budget", "show", "parent"])
        assert result.exit_code == 0, result.output
        assert "(subtree: $7.0000)" in result.output


# -- no reclaim, no oversight, no report ---------------------------------------

class TestDutiesEnd:
    def test_no_budget_reclaim_to_the_former_parent(self, sm, tree):
        parent, child, _ = tree
        sm.set_cost_budget(parent.id, 20.0)
        sm.release_child(child.id)
        assert sm.reclaim_budget(child.id) is None
        assert sm.auto_reclaim_budget(child.id) is None
        assert sm.get_session(parent.id).cost_budget_usd == 20.0
        assert sm.get_session(child.id).cost_budget_usd == 5.0

    def test_no_report_is_expected(self, sm, tree):
        from overcode.child_lifecycle import settled_child_status, task_result
        _, child, _ = tree
        sm.update_session_status(child.id, STATUS_WAITING_OVERSIGHT)
        sm.release_child(child.id)
        freed = sm.get_session(child.id)
        assert settled_child_status(freed, stopped=True) is None
        assert task_result(freed, "waiting_user") == (None, "")

    def test_no_oversight_timeout(self, sm, tree):
        from datetime import datetime, timezone
        from overcode.monitor_daemon_core import should_enforce_oversight_timeout
        _, child, _ = tree
        sm.update_session_status(child.id, STATUS_WAITING_OVERSIGHT)
        sm.release_child(child.id)
        freed = sm.get_session(child.id)
        assert not should_enforce_oversight_timeout(
            freed.status, freed.oversight_policy, freed.oversight_deadline,
            datetime(2030, 1, 1, tzinfo=timezone.utc), deadline_at=freed.oversight_deadline_at)

    def test_kill_no_longer_cascades_to_it(self, sm, tree):
        parent, child, grandchild = tree
        sm.release_child(child.id)
        assert sm.get_descendants(parent.id) == []
        assert [d.id for d in sm.get_descendants(child.id)] == [grandchild.id]


# -- a parent's follow ends cleanly ------------------------------------------

def _released_session(parent_set: bool):
    return SimpleNamespace(
        id="c1", name="child", tmux_window="child-w", status="running",
        parent_session_id="p1" if parent_set else None,
        released_from_session_id=None if parent_set else "p1",
        released_from_name=None if parent_set else "parent",
        oversight_policy="wait", oversight_timeout_seconds=0.0, oversight_deadline=None)


class TestFollow:
    def test_follow_ends_with_0_when_the_child_is_released(self, capsys):
        from overcode import follow_mode
        sessions = MagicMock()
        sessions.resolve_session_name.return_value = _released_session(True)
        sessions.get_session.return_value = _released_session(False)
        with patch("overcode.follow_mode.SessionManager", return_value=sessions), \
             patch("overcode.follow_mode._capture_pane", return_value="out"), \
             patch("overcode.follow_mode._check_hook_stop", return_value=False), \
             patch("overcode.follow_mode.time.sleep"):
            assert follow_mode.follow_agent("child", "agents") == 0
        assert "released from 'parent'" in capsys.readouterr().err

    def test_waiting_for_a_report_ends_with_0_when_released(self, capsys):
        from overcode.follow_mode import _poll_for_report
        sessions = MagicMock()
        sessions.get_session.return_value = _released_session(False)
        with patch("overcode.follow_mode._check_report", return_value=None), \
             patch("overcode.follow_mode.time.sleep"):
            code = _poll_for_report("child", "agents", sessions, "child-w", "timeout", 60.0,
                                    0.1, deque(), session_id="c1", watch_release=True)
        assert code == 0
        assert "no longer reports" in capsys.readouterr().err

    def test_an_agent_released_before_the_follow_is_followed_as_before(self):
        from overcode import follow_mode
        sessions = MagicMock()
        sessions.resolve_session_name.return_value = _released_session(False)
        sessions.get_session.return_value = _released_session(False)
        with patch("overcode.follow_mode.SessionManager", return_value=sessions), \
             patch("overcode.follow_mode._capture_pane", return_value=None), \
             patch("overcode.follow_mode._check_session_terminated", return_value=True):
            assert follow_mode.follow_agent("child", "agents") == 1  # terminated, as before


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


class TestReleaseCommand:
    def _run(self, *args):
        from overcode.cli import app
        with patch("overcode.cli.split.open_split") as open_split:
            result = CliRunner().invoke(app, ["release", *args])
        open_split.assert_not_called()
        return result

    def test_release_frees_and_tells_the_child(self, fleet):
        parent = fleet.add("parent", stats={"estimated_cost_usd": 1.0})
        fleet.add("kid", parent_session_id=parent.id, stats={"estimated_cost_usd": 0.25})
        result = self._run("kid")
        assert result.exit_code == 0, result.output
        assert "Released 'kid' from 'parent'" in result.output
        assert "$0.2500" in result.output and "Told 'kid'" in result.output
        assert fleet.get("kid").parent_session_id is None
        typed = [keys for (_s, window, keys, _e) in fleet.tmux.sent_keys if window == "kid-w"]
        assert typed and "released from your parent agent 'parent'" in typed[-1]
        assert "overcode report" in typed[-1]

    def test_no_notify_types_nothing(self, fleet):
        parent = fleet.add("parent")
        fleet.add("kid", parent_session_id=parent.id)
        result = self._run("kid", "--no-notify")
        assert result.exit_code == 0, result.output
        assert fleet.get("kid").released_from_session_id == parent.id
        assert not [k for k in fleet.tmux.sent_keys if k[1] == "kid-w"]

    def test_a_finished_child_is_released_but_not_told(self, fleet):
        parent = fleet.add("parent")
        fleet.add("kid", parent_session_id=parent.id, status="done")
        result = self._run("kid")
        assert result.exit_code == 0, result.output
        assert "not told: it is done" in result.output
        assert not [k for k in fleet.tmux.sent_keys if k[1] == "kid-w"]

    def test_a_root_agent_is_refused(self, fleet):
        fleet.add("solo")
        result = self._run("solo")
        assert result.exit_code == 1 and "has no parent" in result.output

    def test_unknown_agent(self, fleet):
        result = self._run("ghost")
        assert result.exit_code == 1 and "not found" in result.output

    def test_show_has_the_trace(self, fleet):
        from overcode.cli.agent import _release_lines
        parent = fleet.add("parent", stats={"estimated_cost_usd": 1.0})
        fleet.add("kid", parent_session_id=parent.id, stats={"estimated_cost_usd": 0.5})
        fleet.sm.release_child(fleet.get("kid").id)
        (label, value), = _release_lines(fleet.get("kid"))
        assert label == "Released" and value.startswith("from parent at ")
        assert value.endswith("$0.5000 spent under it")
        (label, value), = _release_lines(fleet.get("parent"))
        assert label == "Released children" and "$0.5000" in value
        assert _release_lines(SimpleNamespace()) == []


# -- the TUI and the web API -------------------------------------------------------

class TestTuiAndApi:
    def test_the_palette_has_it_for_a_focused_agent(self):
        from overcode.command_palette import COMMANDS
        entry = {c.action: c for c in COMMANDS}["release_focused"]
        assert entry.agent and entry.title == "Release from parent" and entry.category == "Agent"

    def _app(self, session):
        app = MagicMock()
        app._pending_confirmations = {}
        app._is_remote.return_value = False
        app.focused = None
        return app

    def test_the_action_asks_for_confirmation(self):
        from overcode.tui_actions import session as actions
        app = self._app(None)
        child = SimpleNamespace(name="kid", parent_session_id="p1", is_remote=False)
        with patch.object(actions, "_get_focused_session",
                          return_value=SimpleNamespace(session=child)):
            actions.SessionActionsMixin.action_release_focused(app)
        key, message = app._confirm_double_press.call_args.args[:2]
        assert key == "release" and "Release 'kid'" in message

    def test_a_root_agent_is_not_offered_a_release(self):
        from overcode.tui_actions import session as actions
        app = self._app(None)
        root = SimpleNamespace(name="solo", parent_session_id=None, is_remote=False)
        with patch.object(actions, "_get_focused_session",
                          return_value=SimpleNamespace(session=root)):
            actions.SessionActionsMixin.action_release_focused(app)
        app._confirm_double_press.assert_not_called()
        assert "no parent" in app.notify.call_args.args[0]

    def test_executing_releases_and_refreshes(self, sm):
        from overcode.tui_actions import session as actions
        parent = _agent(sm, "parent")
        kid = _agent(sm, "kid", parent=parent.id)
        app = self._app(None)
        app.session_manager = sm
        app.tmux_session = "agents"
        launcher = MagicMock()
        launcher.for_agent.return_value = launcher
        launcher.sessions = sm
        launcher.send_to_session_by_id.return_value = True
        with patch("overcode.launcher.AgentLauncher", return_value=launcher):
            actions.SessionActionsMixin._execute_release(app, kid)
        assert sm.get_session(kid.id).parent_session_id is None
        assert "Released 'kid' from 'parent'" in app.notify.call_args.args[0]
        app.refresh_sessions.assert_called_once()

    def test_web_control_api(self, sm):
        from overcode import web_control_api
        parent = _agent(sm, "parent")
        _agent(sm, "kid", parent=parent.id, cost=0.75)
        with patch("overcode.launcher.AgentLauncher") as launcher_cls:
            launcher_cls.return_value.sessions = SessionManager(skip_git_detection=True)
            out = web_control_api.release_agent("agents", "kid", notify=False)
        assert out == {"ok": True, "parent": "parent", "cost_usd": 0.75, "notified": False}
        with pytest.raises(web_control_api.ControlError, match="no parent"):
            with patch("overcode.launcher.AgentLauncher") as launcher_cls:
                launcher_cls.return_value.sessions = SessionManager(skip_git_detection=True)
                web_control_api.release_agent("agents", "kid", notify=False)
