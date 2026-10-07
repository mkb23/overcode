"""
Tests for overcode.lifecycle: bulk revive (#481) and shutdown (#509),
plus the CLI commands and web route built on them.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from overcode.mocks import MockTmux
from overcode.launcher import AgentLauncher
from overcode.lifecycle import (
    ShutdownReport,
    find_dead_agents,
    resume_mode,
    revive_all,
    shutdown,
)
from overcode.session_manager import SessionManager
from overcode.tmux_manager import TmuxManager


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Isolate state dirs and strip OVERCODE_* vars leaking from a host agent."""
    for key in list(os.environ):
        if key.startswith("OVERCODE_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state"))
    with patch("overcode.launcher.require_tmux"), \
         patch("overcode.launcher.require_agent_cli"), \
         patch("overcode.launcher.time.sleep"):
        yield


@pytest.fixture
def fleet(tmp_path):
    """A launcher over MockTmux with a parent, its child and a grandchild."""
    mock_tmux = MockTmux()
    sm = SessionManager(state_dir=tmp_path / "sessions", skip_git_detection=True)
    launcher = AgentLauncher("agents", TmuxManager("agents", tmux=mock_tmux), sm)
    parent = launcher.launch(name="parent")
    child = launcher.launch(name="child")
    grandchild = launcher.launch(name="grandchild")
    sm.update_session(child.id, parent_session_id=parent.id)
    sm.update_session(grandchild.id, parent_session_id=child.id)
    sm.set_active_agent_session_id(parent.id, "sid-parent")
    sm.set_active_agent_session_id(child.id, "sid-child")
    sm.update_session(grandchild.id, active_agent_session_id=None)  # never bound one
    mock_tmux.sent_keys.clear()
    return launcher, mock_tmux, sm


def _kill_all_windows(launcher, sm):
    for s in sm.list_sessions():
        launcher.tmux.kill_window(s.tmux_window)


# ---------------------------------------------------------------------------
# Revive
# ---------------------------------------------------------------------------

class TestFindDeadAgents:
    def test_none_dead_while_windows_live(self, fleet):
        launcher, _, _ = fleet
        assert find_dead_agents(launcher) == []

    def test_parents_before_children(self, fleet):
        launcher, _, sm = fleet
        _kill_all_windows(launcher, sm)
        names = [s.name for s in find_dead_agents(launcher)]
        assert names == ["parent", "child", "grandchild"]

    def test_skips_done_and_reported_children(self, fleet):
        launcher, _, sm = fleet
        _kill_all_windows(launcher, sm)
        grandchild = sm.get_session_by_name("grandchild")
        sm.update_session_status(grandchild.id, "done")
        with patch("overcode.follow_mode._check_report",
                   side_effect=lambda ts, name: {"status": "success"} if name == "child" else None):
            names = [s.name for s in find_dead_agents(launcher)]
        assert names == ["parent"]


class TestResumeMode:
    def test_resume_when_id_recorded(self, fleet):
        _, _, sm = fleet
        assert resume_mode(sm.get_session_by_name("parent")) == ("resume", "")

    def test_fresh_without_recorded_conversation(self, fleet):
        _, _, sm = fleet
        mode, reason = resume_mode(sm.get_session_by_name("grandchild"))
        assert mode == "fresh" and "no conversation" in reason

    def test_fresh_flag_wins(self, fleet):
        _, _, sm = fleet
        assert resume_mode(sm.get_session_by_name("parent"), fresh=True)[0] == "fresh"

    def test_backend_without_resume(self, fleet):
        _, _, sm = fleet
        s = sm.get_session_by_name("parent")
        with patch("overcode.backends.session_supports", return_value=False):
            mode, reason = resume_mode(s)
        assert mode == "fresh" and "can't resume" in reason


class TestReviveAll:
    def test_revives_in_order_with_modes(self, fleet):
        launcher, mock_tmux, sm = fleet
        _kill_all_windows(launcher, sm)
        seen = []
        results = revive_all(launcher, on_result=seen.append)
        assert [r.name for r in results] == ["parent", "child", "grandchild"]
        assert seen == results
        assert [r.mode for r in results] == ["resume", "resume", "fresh"]
        assert all(r.ok for r in results)
        for s in sm.list_sessions():
            assert launcher.tmux.window_exists(s.tmux_window)
            assert s.status == "running"
        sent = " ".join(k[2] for k in mock_tmux.sent_keys)
        assert "--resume sid-parent" in sent and "--resume sid-child" in sent

    def test_dry_run_changes_nothing(self, fleet):
        launcher, mock_tmux, sm = fleet
        _kill_all_windows(launcher, sm)
        results = revive_all(launcher, dry_run=True)
        assert [r.ok for r in results] == [None, None, None]
        assert mock_tmux.sent_keys == []
        assert all(s.status == "terminated" for s in sm.list_sessions())

    def test_fresh(self, fleet):
        launcher, mock_tmux, sm = fleet
        _kill_all_windows(launcher, sm)
        results = revive_all(launcher, fresh=True)
        assert {r.mode for r in results} == {"fresh"}
        assert "--resume" not in " ".join(k[2] for k in mock_tmux.sent_keys)

    def test_one_failure_does_not_stop_the_rest(self, fleet):
        launcher, _, sm = fleet
        _kill_all_windows(launcher, sm)
        real = launcher.revive

        def flaky(session, fresh=False):
            if session.name == "child":
                raise RuntimeError("boom")
            return real(session, fresh=fresh)

        with patch.object(launcher, "revive", side_effect=flaky):
            results = revive_all(launcher)
        assert [(r.name, r.ok) for r in results] == [
            ("parent", True), ("child", False), ("grandchild", True)]
        assert results[1].reason == "boom"


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------

class FakeOps:
    """SystemOps double: records everything, touches no real process."""

    def __init__(self, live_sessions, *, agents_exit=True, own_session=None,
                 running=None, own_pid=4242):
        self.live = list(live_sessions)
        self.killed_sessions = []
        self.stopped = []
        self.agents_exit = agents_exit
        self.own_session = own_session
        self.own_window = None
        self.running = dict(running or {})  # pid-file name -> pid
        self._own_pid = own_pid
        self.clock = 0.0

    def list_tmux_sessions(self):
        return list(self.live)

    def kill_tmux_session(self, name):
        self.killed_sessions.append(name)
        return True

    def current_tmux_location(self):
        return self.own_session, self.own_window

    def pane_commands(self, tmux_session):
        return self._commands

    def running_pid(self, pid_file):
        return self.running.get(Path(pid_file).name)

    def stop_pid_file(self, pid_file):
        self.stopped.append(Path(pid_file).name)
        return True

    def own_pid(self):
        return self._own_pid

    def sleep(self, seconds):
        self.clock += seconds

    def monotonic(self):
        return self.clock


def _run_shutdown(fleet, ops, **kwargs):
    launcher, mock_tmux, sm = fleet
    windows = {s.tmux_window: ("zsh" if ops.agents_exit else "claude")
               for s in sm.list_sessions()}
    ops._commands = windows
    lines = []
    report = shutdown("agents", ops=ops, session_manager=sm,
                      launcher_factory=lambda ts: launcher, echo=lines.append, **kwargs)
    return report, lines


class TestShutdown:
    def test_graceful_children_first_and_records_kept(self, fleet):
        launcher, mock_tmux, sm = fleet
        ops = FakeOps(["agents", "jobs"])
        report, _ = _run_shutdown(fleet, ops)

        exits = [k[1] for k in mock_tmux.sent_keys if k[2] == "/exit"]
        order = {s.tmux_window: s.name for s in sm.list_sessions()}
        assert [order[w] for w in exits] == ["grandchild", "child", "parent"]

        sessions = sm.list_sessions()
        assert len(sessions) == 3  # records kept, not archived
        assert all(s.status == "terminated" for s in sessions)
        assert sm.get_session_by_name("parent").active_agent_session_id == "sid-parent"
        assert not any(launcher.tmux.window_exists(s.tmux_window) for s in sessions)
        assert report.count("agents", "exited") == 3
        assert ops.killed_sessions == ["jobs", "agents"]

        # And revive brings them back.
        assert [r.ok for r in revive_all(launcher)] == [True, True, True]

    def test_stragglers_killed_after_timeout(self, fleet):
        ops = FakeOps(["agents"], agents_exit=False)
        report, _ = _run_shutdown(fleet, ops, timeout=2.0)
        assert report.count("agents", "killed") == 3
        assert ops.clock >= 2.0

    def test_force_skips_graceful_exit(self, fleet):
        _, mock_tmux, _ = fleet
        ops = FakeOps(["agents"], agents_exit=False)
        report, _ = _run_shutdown(fleet, ops, force=True)
        assert mock_tmux.sent_keys == []
        assert report.count("agents", "killed") == 3
        assert ops.clock == 0.0

    def test_dry_run_changes_nothing(self, fleet):
        launcher, mock_tmux, sm = fleet
        ops = FakeOps(["agents", "jobs", "oc-view-agents"],
                      running={"supervisor_daemon.pid": 11, "monitor_daemon.pid": 12})
        report, lines = _run_shutdown(fleet, ops, dry_run=True)
        assert mock_tmux.sent_keys == []
        assert ops.killed_sessions == [] and ops.stopped == []
        assert all(launcher.tmux.window_exists(s.tmux_window) for s in sm.list_sessions())
        assert report.count("agents", "would-stop") == 3
        assert any("supervisor daemon [agents]" in line for line in lines)

    def test_supervisor_stopped_before_agents_and_monitor_after(self, fleet):
        _, mock_tmux, _ = fleet
        ops = FakeOps(["agents"], running={"supervisor_daemon.pid": 11,
                                           "monitor_daemon.pid": 12, "web_server.pid": 13})
        report, _ = _run_shutdown(fleet, ops)
        phases = [s.phase for s in report.steps if s.outcome in ("stopped", "exited")]
        assert phases.index("supervisor") < phases.index("agents") < phases.index("daemons")
        assert ops.stopped[:3] == ["supervisor_daemon.pid", "monitor_daemon.pid", "web_server.pid"]

    def test_keep_jobs(self, fleet):
        ops = FakeOps(["agents", "jobs"])
        _run_shutdown(fleet, ops, keep_jobs=True)
        assert "jobs" not in ops.killed_sessions

    def test_own_tmux_session_killed_last(self, fleet):
        ops = FakeOps(["agents", "jobs", "oc-view-agents", "overcode"], own_session="agents")
        _run_shutdown(fleet, ops)
        assert ops.killed_sessions[-1] == "agents"
        assert set(ops.killed_sessions) == {"agents", "jobs", "oc-view-agents", "overcode"}

    def test_linked_own_session_deferred_with_its_group(self, fleet):
        ops = FakeOps(["agents", "jobs", "oc-view-agents", "overcode"], own_session="oc-view-agents")
        _run_shutdown(fleet, ops)
        assert set(ops.killed_sessions[-2:]) == {"agents", "oc-view-agents"}

    def test_calling_agent_is_not_exited_mid_run(self, fleet):
        launcher, mock_tmux, sm = fleet
        me = sm.get_session_by_name("child")
        ops = FakeOps(["agents"], own_session="agents")
        ops.own_window = me.tmux_window
        report, _ = _run_shutdown(fleet, ops)
        assert me.tmux_window not in [k[1] for k in mock_tmux.sent_keys]
        assert launcher.tmux.window_exists(me.tmux_window)  # dies with the session
        assert sm.get_session(me.id).status == "terminated"
        assert any(s.target == "child" and s.outcome == "deferred" for s in report.steps)
        assert ops.killed_sessions[-1] == "agents"

    def test_own_pid_is_deferred(self, fleet):
        ops = FakeOps(["agents"], running={"web_server.pid": 4242}, own_pid=4242)
        report, _ = _run_shutdown(fleet, ops)
        assert report.stop_self
        assert "web_server.pid" not in ops.stopped

    def test_shared_pieces_kept_while_another_session_runs(self, fleet):
        launcher, _, sm = fleet
        other = sm.create_session(name="elsewhere", tmux_session="work",
                                  tmux_window="elsewhere-1", command=["claude"])
        assert other
        ops = FakeOps(["agents", "work", "overcode"])
        report, _ = _run_shutdown(fleet, ops)
        assert "overcode" not in ops.killed_sessions
        assert "work" not in ops.killed_sessions
        assert any(s.target == "presence logger" and s.outcome == "skipped" for s in report.steps)

    def test_services_only_leaves_agents(self, fleet):
        launcher, mock_tmux, sm = fleet
        ops = FakeOps(["agents", "jobs"], running={"monitor_daemon.pid": 12})
        report, _ = _run_shutdown(fleet, ops, services_only=True)
        assert mock_tmux.sent_keys == []
        assert ops.killed_sessions == []
        assert ops.stopped == ["monitor_daemon.pid"]
        assert all(launcher.tmux.window_exists(s.tmux_window) for s in sm.list_sessions())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

runner = CliRunner()


class TestCli:
    def test_restart_revives_when_window_gone(self):
        from overcode.cli import app
        sess = MagicMock(name="sess", active_agent_session_id="sid", backend="claude-code")
        launcher = MagicMock()
        launcher.tmux.window_exists.return_value = False
        launcher.revive.return_value = True
        with patch("overcode.launcher.AgentLauncher", return_value=launcher), \
             patch("overcode.cli.agent.find_agent", return_value=sess):
            result = runner.invoke(app, ["restart", "a1"])
        assert result.exit_code == 0, result.output
        launcher.revive.assert_called_once_with(sess, fresh=False)
        launcher.restart.assert_not_called()
        assert "Revived agent: a1 (resumed)" in result.output

    def test_revive_needs_name_or_all(self):
        from overcode.cli import app
        result = runner.invoke(app, ["revive"])
        assert result.exit_code == 1

    def test_revive_all_dry_run(self):
        from overcode.cli import app
        from overcode.lifecycle import ReviveResult

        def fake(launcher, fresh=False, dry_run=False, on_result=None):
            r = ReviveResult("a1", "agents", "resume", None)
            on_result(r)
            return [r]

        with patch("overcode.launcher.AgentLauncher"), \
             patch("overcode.lifecycle.revive_all", side_effect=fake) as ra, \
             patch("overcode.lifecycle.ensure_monitor_daemon") as ensure:
            result = runner.invoke(app, ["revive", "--all", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert ra.call_args.kwargs["dry_run"] is True
        assert "would resume" in result.output
        ensure.assert_not_called()

    def test_revive_all_reports_and_starts_monitor(self):
        from overcode.cli import app
        from overcode.lifecycle import ReviveResult
        results = [ReviveResult("a1", "agents", "resume", True),
                   ReviveResult("a2", "agents", "fresh", True, "no conversation recorded")]
        with patch("overcode.launcher.AgentLauncher"), \
             patch("overcode.lifecycle.revive_all", return_value=results), \
             patch("overcode.lifecycle.ensure_monitor_daemon", return_value=True) as ensure:
            result = runner.invoke(app, ["revive", "--all"])
        assert result.exit_code == 0, result.output
        assert "Revived 2 of 2" in result.output
        ensure.assert_called_once_with("agents")

    def test_shutdown_dry_run(self):
        from overcode.cli import app
        with patch("overcode.lifecycle.shutdown", return_value=ShutdownReport()) as sd:
            result = runner.invoke(app, ["shutdown", "--dry-run", "--all"])
        assert result.exit_code == 0, result.output
        sd.assert_called_once()
        assert sd.call_args.kwargs["dry_run"] is True
        assert sd.call_args.kwargs["all_sessions"] is True

    def test_shutdown_declined(self):
        from overcode.cli import app
        with patch("overcode.lifecycle.shutdown", return_value=ShutdownReport()) as sd:
            result = runner.invoke(app, ["shutdown"], input="n\n")
        assert result.exit_code == 1
        assert sd.call_count == 1  # the plan only

    def test_shutdown_yes(self):
        from overcode.cli import app
        with patch("overcode.lifecycle.shutdown", return_value=ShutdownReport()) as sd:
            result = runner.invoke(app, ["shutdown", "--yes", "--force", "--keep-jobs"])
        assert result.exit_code == 0, result.output
        sd.assert_called_once()
        kw = sd.call_args.kwargs
        assert kw["force"] and kw["keep_jobs"] and "dry_run" not in kw


# ---------------------------------------------------------------------------
# Web route
# ---------------------------------------------------------------------------

class TestWebShutdown:
    def test_route_registered(self):
        from overcode.web_server import _FIXED_CONTROL_ROUTES
        assert ("POST", "/api/shutdown") in _FIXED_CONTROL_ROUTES

    def test_default_scope_is_services(self):
        from overcode.web_server import _FIXED_CONTROL_ROUTES
        api = MagicMock()
        _FIXED_CONTROL_ROUTES[("POST", "/api/shutdown")](api, "agents", {})
        assert api.shutdown_overcode.call_args.kwargs["scope"] == "services"

    def test_bad_scope(self):
        from overcode.web_control_api import ControlError, shutdown_overcode
        with pytest.raises(ControlError):
            shutdown_overcode("agents", scope="everything")

    def test_dry_run_returns_plan(self):
        from overcode.lifecycle import ShutdownStep
        from overcode.web_control_api import shutdown_overcode
        report = ShutdownReport(steps=[ShutdownStep("daemons", "monitor daemon [agents]", "would-stop")])
        with patch("overcode.lifecycle.shutdown", return_value=report) as sd:
            out = shutdown_overcode("agents", scope="all", dry_run=True)
        assert out["steps"][0]["outcome"] == "would-stop"
        assert sd.call_args.kwargs["services_only"] is False

    def test_real_run_is_backgrounded(self):
        from overcode.web_control_api import shutdown_overcode
        with patch("threading.Thread") as thread, \
             patch("overcode.lifecycle.shutdown") as sd:
            out = shutdown_overcode("agents")
        assert out == {"ok": True, "scope": "services", "started": True}
        thread.return_value.start.assert_called_once()
        sd.assert_not_called()


# ---------------------------------------------------------------------------
# TUI actions (palette-only; confirmed by running twice)
# ---------------------------------------------------------------------------

class TestTuiActions:
    def _app(self):
        app = MagicMock()
        app.tmux_session = "agents"
        app._pending_confirmations = {}
        return app

    def test_revive_with_nothing_dead(self):
        from overcode.tui_actions.session import SessionActionsMixin
        app = self._app()
        with patch("overcode.launcher.AgentLauncher"), \
             patch("overcode.lifecycle.find_dead_agents", return_value=[]):
            SessionActionsMixin.action_revive_dead_agents(app)
        app._confirm_double_press.assert_not_called()
        assert "No dead agents" in app.notify.call_args.args[0]

    def test_revive_asks_for_confirmation(self):
        from overcode.tui_actions.session import SessionActionsMixin
        app = self._app()
        with patch("overcode.launcher.AgentLauncher"), \
             patch("overcode.lifecycle.find_dead_agents", return_value=[MagicMock(), MagicMock()]):
            SessionActionsMixin.action_revive_dead_agents(app)
        key, message = app._confirm_double_press.call_args.args[:2]
        assert key == "revive_all" and "Revive 2 dead agent(s)" in message

    def test_revive_done_clears_terminated_cache(self):
        from overcode.lifecycle import ReviveResult
        from overcode.tui_actions.session import SessionActionsMixin
        app = self._app()
        app._terminated_sessions = {"id1": object(), "id2": object()}
        app._terminated_times = {"id1": 1.0}
        results = [ReviveResult("a", "agents", "resume", True, session_id="id1"),
                   ReviveResult("b", "agents", "fresh", False, "x", session_id="id2")]
        SessionActionsMixin._revive_all_done(app, results)
        assert "id1" not in app._terminated_sessions and "id2" in app._terminated_sessions
        assert "failed: b" in app.notify.call_args.args[0]
        app._ensure_monitor_daemon.assert_called_once()

    def test_shutdown_asks_for_confirmation(self):
        from overcode.tui_actions.session import SessionActionsMixin
        app = self._app()
        SessionActionsMixin.action_shutdown_overcode(app)
        assert app._confirm_double_press.call_args.args[0] == "shutdown"
