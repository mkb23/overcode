"""A child's outcome and its parent's view of its children (the daemon's side).

- ``child_lifecycle.task_result``: the Result column's kind and reason.
- The daemon publishes it, backfills a report ``overcode report`` could not
  write onto the session, starts a policy-timeout child's oversight
  deadline without ``--follow`` (and clears it while the child works
  again), and rolls each parent's children up for the 👶 column.
- The children-working counts the hook detectors read come from the last
  published tick.
"""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from overcode.clock import to_epoch
from overcode.child_lifecycle import OVERSIGHT_TIMEOUT_REASON, task_result
from overcode.session_manager import SessionManager
from overcode.settings import get_session_dir
from tests.daemon_tick_harness import (
    FrozenClock,
    ScriptedDetector,
    make_daemon,
    seed_sessions,
    seed_steady_state,
)

pytestmark = pytest.mark.unit

START = datetime(2026, 10, 12, 12, 0, 0)


def _child(status="running", report_status=None, report_reason=""):
    return SimpleNamespace(parent_session_id="p", status=status,
                           report_status=report_status, report_reason=report_reason)


class TestTaskResult:
    def test_a_top_level_agent_has_none(self):
        top = SimpleNamespace(parent_session_id=None, status="done", report_status="success",
                              report_reason="")
        assert task_result(top, "done") == (None, "")

    def test_a_working_child_has_none(self):
        assert task_result(_child(), "running") == (None, "")

    @pytest.mark.parametrize("status", ["done", "waiting_oversight"])
    def test_a_child_at_rest_without_a_report_is_unreported(self, status):
        assert task_result(_child(status), status) == ("unreported", "")

    def test_a_resting_child_whose_window_went_is_still_unreported(self):
        assert task_result(_child("waiting_oversight"), "terminated") == ("unreported", "")

    def test_a_resting_child_prompted_again_is_working(self):
        assert task_result(_child("waiting_oversight"), "running") == (None, "")

    @pytest.mark.parametrize("kind", ["success", "failure"])
    def test_a_report_is_its_result_with_the_reason(self, kind):
        child = _child("done", kind, "all tests pass")
        assert task_result(child, "done") == (kind, "all tests pass")

    def test_an_expired_timeout_is_a_timeout(self):
        child = _child("done", "failure", OVERSIGHT_TIMEOUT_REASON)
        assert task_result(child, "done") == ("timeout", OVERSIGHT_TIMEOUT_REASON)


@pytest.fixture
def root(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".overcode" / "sessions").mkdir(parents=True)
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(home / ".overcode" / "sessions"))
    return tmp_path


def _hook(name, event):
    path = get_session_dir("agents") / f"hook_state_{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"event": event, "timestamp": 1.0}))


def _report(name, status, reason=""):
    path = get_session_dir("agents") / f"report_{name}.json"
    path.write_text(json.dumps({"status": status, "reason": reason}))


def _daemon(root, statuses):
    """12 agents (11 is a child of 1); ``statuses[name]`` scripts a detected status."""
    sm = SessionManager(state_dir=root / "home" / ".overcode" / "sessions",
                        skip_git_detection=True)
    sessions = seed_sessions(sm, 12, "agents", root / "work", START)

    def script(tick, s):
        return statuses.get(s.name, "waiting_user"), "activity", "pane"

    detector = ScriptedDetector(script)

    def read_hook_state(name):
        path = get_session_dir("agents") / f"hook_state_{name}.json"
        return json.loads(path.read_text()) if path.exists() else None

    detector.hooks._read_hook_state = read_hook_state
    daemon = make_daemon(root / "home" / ".overcode", "agents", detector, session_manager=sm)
    seed_steady_state(daemon, sessions, START)
    return daemon, sm, sessions


def _ticks(daemon, n, start=START):
    with FrozenClock(start).installed() as clock:
        for i in range(n):
            clock.now = start + timedelta(seconds=2 * i)
            daemon.detector.tick = i
            daemon._tick(clock.now)


def _published(daemon, session_id):
    return next(s for s in daemon.state.sessions if s.session_id == session_id)


class TestOversightDeadline:
    def test_a_timeout_child_gets_its_deadline_without_follow(self, root):
        daemon, sm, sessions = _daemon(root, {})
        child = sessions[11]
        sm.update_session(child.id, oversight_policy="timeout", oversight_timeout_seconds=600)
        _hook(child.name, "Stop")
        _ticks(daemon, 2)
        saved = sm.get_session(child.id)
        assert saved.status == "waiting_oversight"
        # Staged on the tick that saw the Stop (the clock is frozen at START)
        # Stored aware; the frozen START is naive local time
        assert to_epoch(saved.oversight_deadline) == (START + timedelta(seconds=600)).timestamp()
        assert _published(daemon, child.id).task_result == "unreported"

    def test_the_wait_policy_has_no_deadline(self, root):
        daemon, sm, sessions = _daemon(root, {})
        child = sessions[11]
        _hook(child.name, "Stop")
        _ticks(daemon, 2)
        assert sm.get_session(child.id).oversight_deadline is None

    def test_an_expired_deadline_is_a_timeout_result(self, root):
        daemon, sm, sessions = _daemon(root, {})
        child = sessions[11]
        past = (START - timedelta(seconds=5)).isoformat()
        sm.update_session(child.id, oversight_policy="timeout", oversight_timeout_seconds=1,
                          oversight_deadline=past, status="waiting_oversight")
        _hook(child.name, "Stop")
        _ticks(daemon, 2)
        saved = sm.get_session(child.id)
        assert (saved.status, saved.report_reason) == ("done", OVERSIGHT_TIMEOUT_REASON)
        published = _published(daemon, child.id)
        assert (published.task_result, published.task_reason) == ("timeout",
                                                                  OVERSIGHT_TIMEOUT_REASON)

    def test_a_child_working_again_loses_its_deadline(self, root):
        daemon, sm, sessions = _daemon(root, {"agent-11": "running"})
        child = sessions[11]
        future = (START + timedelta(seconds=60)).isoformat()
        sm.update_session(child.id, oversight_policy="timeout", oversight_timeout_seconds=60,
                          oversight_deadline=future, status="waiting_oversight")
        _hook(child.name, "UserPromptSubmit")
        _ticks(daemon, 1)
        assert sm.get_session(child.id).oversight_deadline is None

    def test_a_stopped_child_busy_with_background_work_is_not_restaged(self, root):
        """Stop + a background shell reads busy_sleeping (yellow): no
        deadline, and no deadline staged and cleared (and logged) each tick."""
        daemon, sm, sessions = _daemon(root, {"agent-11": "busy_sleeping"})
        child = sessions[11]
        sm.update_session(child.id, oversight_policy="timeout", oversight_timeout_seconds=600)
        _hook(child.name, "Stop")
        lines = []
        daemon.log.info = lambda message, *a, **k: lines.append(message)
        _ticks(daemon, 4)
        saved = sm.get_session(child.id)
        assert (saved.status, saved.oversight_deadline) == ("waiting_oversight", None)
        assert not [m for m in lines if "oversight timeout in" in m]
        # The background work ends: the child rests and its timeout starts
        daemon.detector.script = lambda tick, s: ("waiting_user", "activity", "pane")
        _ticks(daemon, 1)
        assert sm.get_session(child.id).oversight_deadline is not None


class TestReports:
    def test_a_report_file_lands_on_the_session_and_in_the_result(self, root):
        daemon, sm, sessions = _daemon(root, {})
        child = sessions[11]
        _hook(child.name, "Stop")
        _report(child.name, "failure", "3 tests still failing")
        _ticks(daemon, 2)
        saved = sm.get_session(child.id)
        assert (saved.status, saved.report_status, saved.report_reason) == (
            "done", "failure", "3 tests still failing")
        published = _published(daemon, child.id)
        assert (published.task_result, published.task_reason) == (
            "failure", "3 tests still failing")


class TestChildrenRollupAndCounts:
    def test_a_parent_rolls_up_its_children(self, root):
        # Agents 10-19 are children of 0-9 in the seeded tree; here 1 has 11
        daemon, sm, sessions = _daemon(root, {"agent-11": "running"})
        parent, child = sessions[1], sessions[11]
        _ticks(daemon, 2)
        assert _published(daemon, parent.id).children_rollup == {
            "working": 1, "unreported": 0, "success": 0, "failure": 0}
        assert _published(daemon, child.id).children_rollup is None
        # Counted from the last published tick, for this tick's detection
        assert daemon._children_working[parent.id] == 1

    def test_a_resting_child_is_not_working(self, root):
        daemon, sm, sessions = _daemon(root, {})
        parent, child = sessions[1], sessions[11]
        _hook(child.name, "Stop")
        _report(child.name, "success")
        _ticks(daemon, 3)
        assert _published(daemon, parent.id).children_rollup == {
            "working": 0, "unreported": 0, "success": 1, "failure": 0}
        assert parent.id not in daemon._children_working

    def test_the_counts_are_the_detectors_dict(self, tmp_path):
        from overcode.monitor_daemon import MonitorDaemon

        daemon = MonitorDaemon.for_test(tmp_path)
        hooks = getattr(daemon.detector, "hooks", None)
        if not hasattr(hooks, "children_working"):
            pytest.skip("the test daemon's detector has no hook detector")
        assert hooks.children_working is daemon._children_working
