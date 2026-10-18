"""The engine publishes virtual rows for expanded parents only (agent-observability §6.2).

A view subscribes to ``swarm:<id>``; the daemon's ``SwarmPublisher`` builds
those parents' rows into the snapshot's ``virtual`` section on every
publish, and between ticks when a subscription, a finished row's deadline
or a workflow journal changes. Rows never reach the state file, and a quiet
swarm sends no delta.
"""

import json
from unittest.mock import MagicMock

import pytest

from overcode.engine_socket import EngineServer
from overcode.monitor_daemon_state import SessionDaemonState
from overcode.swarm_publisher import SwarmPublisher

pytestmark = pytest.mark.unit

NOW = 1_800_000_000.0


class Engine(EngineServer):
    """A real EngineServer (diff, seq) that never binds, with chosen subscriptions."""

    def __init__(self, path):
        super().__init__(path)
        self.subs = frozenset()
        self.deltas = []

    @property
    def swarm_subscriptions(self):
        return self.subs

    def publish(self, snapshot):
        delta = super().publish(snapshot)
        self.deltas.append(delta)
        return delta


def _hook_state(done_at=NOW - 5):
    return {"event": "Stop", "timestamp": NOW - 10,
            "subagents": {"a1": {"kind": "subagent", "type": "Explore", "started_at": NOW - 20,
                                 "last_seen": NOW - 1}},
            "swarm": {"bg_at": NOW - 10,
                      "done": {"a2": {"kind": "subagent", "result": "ok", "ended_at": done_at}}}}


@pytest.fixture
def daemon(tmp_path, monkeypatch):
    from overcode.monitor_daemon import MonitorDaemon

    monkeypatch.setattr("overcode.monitor_daemon.ensure_session_dir", lambda x: tmp_path)
    monkeypatch.setattr("overcode.monitor_daemon.get_monitor_daemon_pid_path",
                        lambda x: tmp_path / "pid")
    monkeypatch.setattr("overcode.monitor_daemon.get_monitor_daemon_state_path",
                        lambda x: tmp_path / "state.json")
    monkeypatch.setattr("overcode.monitor_daemon.get_agent_history_path",
                        lambda x: tmp_path / "history.csv")
    d = MonitorDaemon(tmux_session="test", session_manager=MagicMock(), tmux=MagicMock())
    d.presence = MagicMock()
    d.presence.get_current_state.return_value = (None, None, None)
    d.presence.available = False
    states = {"one": _hook_state(), "two": _hook_state()}
    d.swarm_publisher = SwarmPublisher(states.get, lambda sid: None,
                                       keep_seconds=lambda: 30.0, clock=lambda: d.now)
    d.now = NOW
    d.hook_states = states
    d._engine = Engine(tmp_path / "engine.sock")
    d.state.sessions = [SessionDaemonState(session_id="s1", name="one"),
                        SessionDaemonState(session_id="s2", name="two")]
    return d


def _virtual(d):
    return d._engine._snapshot.virtual


class TestSubscriptions:
    def test_a_collapsed_swarm_builds_and_publishes_no_rows(self, daemon):
        daemon._publish_state(daemon.state.sessions, save=False)
        assert _virtual(daemon) == {}
        assert daemon.swarm_publisher.builds == 0

    def test_only_the_expanded_parent_s_rows_are_published(self, daemon):
        daemon._engine.subs = frozenset({"s1"})
        daemon._publish_state(daemon.state.sessions, save=False)
        rows = _virtual(daemon)
        assert set(rows) == {"s1/sa", "s1/sa/a1", "s1/sa/a2"}
        assert daemon.swarm_publisher.builds == 1

    def test_rows_never_reach_the_state_file(self, daemon, tmp_path):
        daemon._engine.subs = frozenset({"s1"})
        daemon._publish_state(daemon.state.sessions, save=True)
        assert _virtual(daemon)
        assert "s1/sa/a1" not in (tmp_path / "state.json").read_text()

    def test_a_quiet_swarm_sends_no_delta(self, daemon):
        daemon._engine.subs = frozenset({"s1"})
        daemon._publish_state(daemon.state.sessions, save=False)
        daemon._engine.deltas.clear()
        for step in range(1, 9):  # 8 publishes over 20 s, nothing happening
            daemon.now = NOW + step * 2.5 - 5
            daemon._publish_state(daemon.state.sessions, save=False)
        assert all(d.empty for d in daemon._engine.deltas)


class TestBetweenTicks:
    def test_expanding_republishes_at_the_next_wake_step(self, daemon):
        from overcode.engine_wake import WakeLoop

        loop = WakeLoop(daemon)
        daemon._publish_state(daemon.state.sessions, save=False)
        assert not loop.publish_swarm_if_due()
        daemon._engine.subs = frozenset({"s2"})
        assert loop.publish_swarm_if_due()
        assert "s2/sa/a1" in _virtual(daemon)
        assert not loop.publish_swarm_if_due()  # nothing new: no publish
        daemon._engine.subs = frozenset()
        assert loop.publish_swarm_if_due()
        assert _virtual(daemon) == {}

    def test_a_finished_row_leaves_on_time(self, daemon):
        from overcode.engine_wake import WakeLoop

        loop = WakeLoop(daemon)
        daemon._engine.subs = frozenset({"s1"})
        daemon._publish_state(daemon.state.sessions, save=False)
        assert daemon.swarm_publisher.deadline == NOW + 25
        daemon.now = NOW + 24.9
        assert not loop.publish_swarm_if_due()
        daemon.now = NOW + 25.0
        assert loop.publish_swarm_if_due()
        assert "s1/sa/a2" not in _virtual(daemon)
        assert _virtual(daemon)["s1/sa"]["counts"] == {"running": 1, "ok": 1}
        assert daemon.swarm_publisher.deadline is None

    def test_a_moving_journal_republishes(self, daemon, tmp_path):
        from overcode.engine_wake import WakeLoop

        base = tmp_path / "sid"
        run_dir = base / "subagents" / "workflows" / "wf_1"
        run_dir.mkdir(parents=True)
        journal = run_dir / "journal.jsonl"
        journal.write_text(json.dumps({"type": "started", "agentId": "w1",
                                       "label": "verify-a", "phase": "Verify"}) + "\n")
        daemon.hook_states["one"] = {
            "event": "Stop", "timestamp": NOW,
            "subagents": {"w1": {"kind": "workflow", "started_at": NOW}},
            "swarm": {"bg_at": NOW, "transcript": str(base) + ".jsonl",
                      "workflows": {"t1": {"status": "running", "run_id": "wf_1"}}}}
        loop = WakeLoop(daemon)
        daemon._engine.subs = frozenset({"s1"})
        daemon._publish_state(daemon.state.sessions, save=False)
        assert _virtual(daemon)["s1/wf/wf_1/ph/0"] == {
            "parent": "s1", "row": "phase", "group": "s1/wf/wf_1", "title": "Verify",
            "order": 0, "started": 1, "done": 0}
        assert not loop.publish_swarm_if_due()
        with open(journal, "a") as f:
            f.write(json.dumps({"type": "result", "agentId": "w1", "result": "ok"}) + "\n")
        assert loop.publish_swarm_if_due()
        assert _virtual(daemon)["s1/wf/wf_1/ph/0"]["done"] == 1


def test_the_parent_publishes_its_subagents_share_of_its_spend():
    """The $ hover splits own vs delegated spend (decision 6): the engine
    publishes the subagents' share; the TUI only renders it."""
    from overcode import swarm

    conversations = {"sid": {"cost": 3.0}, "sid/agent-a": {"cost": 0.75},
                     "sid/workflows/wf/agent-b": {"cost": 0.5}}
    assert swarm.subagent_cost(conversations) == 1.25
    assert "subagent_cost_usd" in SessionDaemonState().to_dict()


class TestOldSubscriptions:
    """A 0.6.1 beta TUI's ``<id>/done`` subscription asks for nothing."""

    def test_only_parent_ids_count(self):
        from overcode.swarm_publisher import subscribed_parents

        assert subscribed_parents({"s1", "s1/sa/done", "s2/done", "junk/x", 7}) == {"s1"}

    def test_a_done_key_alone_builds_nothing(self, daemon):
        daemon._engine.subs = frozenset({"s1/done"})
        daemon._publish_state(daemon.state.sessions, save=False)
        assert _virtual(daemon) == {} and daemon.swarm_publisher.builds == 0


