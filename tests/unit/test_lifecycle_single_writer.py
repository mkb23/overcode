"""One writer of lifecycle state: the monitor daemon (quality review item 9).

- ``AgentLauncher.list_sessions`` is read-only: no status, no refund, no
  output, whatever the windows and hook files say.
- The daemon tick settles terminated, a child's done / waiting_oversight
  and the #432 budget refund, each exactly once.
- The TUI, which no longer reaches the writing path, picks up a new agent
  in sessions.json within one focused-pane tick, not the 10 s refresh.
"""

import json
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from overcode.session_manager import PendingUpdates, SessionManager
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


@pytest.fixture
def root(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".overcode" / "sessions").mkdir(parents=True)
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(home / ".overcode" / "sessions"))
    return tmp_path


def _hook_stop(name):
    path = get_session_dir("agents") / f"hook_state_{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"event": "Stop", "timestamp": 1.0}))


def _report(name, status="success"):
    path = get_session_dir("agents") / f"report_{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"status": status, "reason": ""}))


# ── list_sessions writes nothing ─────────────────────────────────────────


class TestListSessionsIsReadOnly:
    def test_no_write_with_gone_windows_and_stopped_children(self, root, capsys):
        from overcode.launcher import AgentLauncher
        from overcode.mocks import MockTmux
        from overcode.tmux_manager import TmuxManager

        sm = SessionManager(state_dir=root / "home" / ".overcode" / "sessions",
                            skip_git_detection=True)
        sessions = seed_sessions(sm, 12, "agents", root / "work", START)
        child = sessions[11]
        sm.update_session(child.id, cost_budget_usd=5.0)
        _hook_stop(child.name)
        _report(child.name)
        # No tmux windows at all: every agent's window is "gone"
        launcher = AgentLauncher(tmux_session="agents",
                                 tmux_manager=TmuxManager("agents", tmux=MockTmux()),
                                 session_manager=sm)
        before = sm.state_file.read_bytes()

        with patch.object(sm, "update_session_status") as status, \
                patch.object(sm, "update_session") as update, \
                patch.object(sm, "reclaim_budget") as refund, \
                patch.object(launcher, "_list_tmux_windows_cheap") as windows:
            listed = launcher.list_sessions()

        assert len(listed) == 12
        assert all(s.status == "running" for s in listed)
        status.assert_not_called()
        update.assert_not_called()
        refund.assert_not_called()
        windows.assert_not_called()  # not even a tmux call
        assert sm.state_file.read_bytes() == before
        assert capsys.readouterr().out == ""


# ── the daemon settles each, once ────────────────────────────────────────


class _StatusWrites:
    """Counts staged status writes per session id across ticks."""

    def __init__(self):
        self.calls = []

    def __enter__(self):
        original = PendingUpdates.update_session_status
        calls = self.calls

        def spy(pending, session_id, status):
            calls.append((session_id, status))
            return original(pending, session_id, status)

        self._patch = patch.object(PendingUpdates, "update_session_status", spy)
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()

    def of(self, session_id):
        return [s for i, s in self.calls if i == session_id]


def _daemon(root, gone=()):
    sm = SessionManager(state_dir=root / "home" / ".overcode" / "sessions",
                        skip_git_detection=True)
    sessions = seed_sessions(sm, 12, "agents", root / "work", START)
    parent, child = sessions[1], sessions[11]  # 11 is a child of 1
    sm.update_session(parent.id, cost_budget_usd=10.0)
    sm.update_session(child.id, cost_budget_usd=5.0)  # spent 2.75

    def script(tick, s):
        if s.name in gone:
            return "terminated", "Window no longer exists", ""
        return "waiting_user", "Waiting for input", "pane"

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


class TestDaemonSettlesLifecycleOnce:
    def test_gone_window_terminated_once(self, root):
        daemon, sm, sessions = _daemon(root, gone=("agent-02",))
        agent = sessions[2]
        with _StatusWrites() as writes:
            _ticks(daemon, 3)
        assert sm.get_session(agent.id).status == "terminated"
        assert writes.of(agent.id) == ["terminated"]

    def test_stopped_child_with_report_done_and_refunded_once(self, root):
        daemon, sm, sessions = _daemon(root)
        parent, child = sessions[1], sessions[11]
        _hook_stop(child.name)
        _report(child.name)
        reclaim = MagicMock(wraps=sm.reclaim_budget)
        with _StatusWrites() as writes, patch.object(sm, "reclaim_budget", reclaim):
            _ticks(daemon, 3)
        assert sm.get_session(child.id).status == "done"
        assert writes.of(child.id) == ["done"]
        reclaim.assert_called_once_with(child.id)
        # 5.00 budget, 2.75 spent: 2.25 back to the parent, child capped at its spend
        assert sm.get_session(child.id).cost_budget_usd == pytest.approx(2.75)
        assert sm.get_session(parent.id).cost_budget_usd == pytest.approx(12.25)
        published = {s.session_id: s for s in daemon.state.sessions}
        assert published[child.id].current_status == "done"

    def test_stopped_child_without_report_waits_then_done_on_report(self, root):
        daemon, sm, sessions = _daemon(root)
        parent, child = sessions[1], sessions[11]
        _hook_stop(child.name)
        reclaim = MagicMock(wraps=sm.reclaim_budget)
        with _StatusWrites() as writes, patch.object(sm, "reclaim_budget", reclaim):
            _ticks(daemon, 3)
            assert sm.get_session(child.id).status == "waiting_oversight"
            assert writes.of(child.id) == ["waiting_oversight"]
            reclaim.assert_not_called()

            _report(child.name)
            _ticks(daemon, 3, start=START + timedelta(seconds=10))

        assert sm.get_session(child.id).status == "done"
        assert writes.of(child.id) == ["waiting_oversight", "done"]
        reclaim.assert_called_once_with(child.id)
        # The child is capped at its spend; the rest of its 5.00 went back
        spent = sm.get_session(child.id).cost_budget_usd
        assert spent < 5.0
        assert sm.get_session(parent.id).cost_budget_usd == pytest.approx(10.0 + 5.0 - spent)

    def test_stopped_child_whose_window_is_gone_is_not_terminated(self, root):
        daemon, sm, sessions = _daemon(root, gone=("agent-11",))
        child = sessions[11]
        _hook_stop(child.name)
        with _StatusWrites() as writes:
            _ticks(daemon, 3)
        assert sm.get_session(child.id).status == "waiting_oversight"
        assert writes.of(child.id) == ["waiting_oversight"]

    def test_top_level_agent_stop_is_not_settled(self, root):
        daemon, sm, sessions = _daemon(root)
        agent = sessions[1]
        _hook_stop(agent.name)
        _report(agent.name)
        _ticks(daemon, 2)
        assert sm.get_session(agent.id).status == "running"


# ── one-shot reconcile for a CLI run with no daemon ──────────────────────


class TestReconcileLifecycle:
    def test_settles_once_and_reports(self, root):
        from overcode.launcher import AgentLauncher
        from overcode.mocks import MockTmux
        from overcode.tmux_manager import TmuxManager

        sm = SessionManager(state_dir=root / "home" / ".overcode" / "sessions",
                            skip_git_detection=True)
        sessions = seed_sessions(sm, 12, "agents", root / "work", START)
        parent, child, waiting = sessions[1], sessions[11], sessions[10]
        sm.update_session(parent.id, cost_budget_usd=10.0)
        sm.update_session(child.id, cost_budget_usd=5.0)
        _hook_stop(child.name)
        _report(child.name)
        _hook_stop(waiting.name)
        mock_tmux = MockTmux()
        mock_tmux.new_session("agents")
        for s in sessions[:5] + [child]:
            mock_tmux.new_window("agents", s.tmux_window)
        launcher = AgentLauncher(tmux_session="agents",
                                 tmux_manager=TmuxManager("agents", tmux=mock_tmux),
                                 session_manager=sm)

        result = launcher.reconcile_lifecycle()

        assert result.done == [child.name]
        assert result.waiting_oversight == [waiting.name]
        assert result.refunds == [(child.name, pytest.approx(2.25))]
        gone = {s.name for s in sessions[5:]} - {child.name, waiting.name}
        assert set(result.terminated) == gone
        again = launcher.reconcile_lifecycle()
        assert again.terminated == again.done == again.waiting_oversight == again.refunds == []
        assert sm.get_session(parent.id).cost_budget_usd == pytest.approx(12.25)


class TestCliListReconcilesOnlyWithoutDaemon:
    def _run(self, daemon_running):
        from typer.testing import CliRunner

        from overcode.cli import app
        from overcode.launcher import LifecycleReconcile

        launcher = MagicMock()
        launcher.list_sessions.return_value = []
        launcher.reconcile_lifecycle.return_value = LifecycleReconcile(terminated=["gone"])
        with patch("overcode.cli.agent.AgentLauncher", return_value=launcher), \
                patch("overcode.monitor_daemon.is_monitor_daemon_running",
                      return_value=daemon_running):
            result = CliRunner().invoke(app, ["list"])
        assert result.exit_code == 0, result.output
        return launcher, result.output

    def test_no_daemon_reconciles_once(self):
        launcher, output = self._run(False)
        launcher.reconcile_lifecycle.assert_called_once_with()
        assert "Detected 1 terminated session(s): gone" in output

    def test_daemon_running_only_reads(self):
        launcher, output = self._run(True)
        launcher.reconcile_lifecycle.assert_not_called()
        assert "terminated" not in output


# ── the TUI: a new agent's row within one tick ───────────────────────────


@pytest.fixture
def short_state_dir(monkeypatch):
    """A short state dir: Unix socket paths are limited to ~104 bytes on macOS."""
    import os

    for k in list(os.environ):
        if k.startswith("OVERCODE_"):
            monkeypatch.delenv(k)
    d = Path(tempfile.mkdtemp(prefix="oc-lw-", dir="/tmp"))
    monkeypatch.setenv("OVERCODE_DIR", str(d))
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(d / "s"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _add(sm, name):
    return sm.create_session(name=name, tmux_session="test", tmux_window=f"{name}-w",
                             command=["claude"], start_directory="/tmp")


@pytest.mark.asyncio
class TestTuiRowsFollowSessionsJson:
    async def _app(self):
        from overcode.status_detector_factory import StatusDetectorDispatcher
        from overcode.tui import SupervisorTUI

        def no_detection(*a, **kw):
            raise AssertionError("the TUI must not detect status")

        with patch.object(SupervisorTUI, "_ensure_monitor_daemon", MagicMock()), \
                patch.object(StatusDetectorDispatcher, "detect_status", no_detection):
            tui = SupervisorTUI(tmux_session="test")
        tui._notifier = MagicMock()
        return tui

    @staticmethod
    def _names(app):
        from overcode.tui_widgets import SessionSummary

        return sorted(w.session.name for w in app.query(SessionSummary))

    async def test_new_agent_gets_a_row_within_one_tick(self, short_state_dir):
        from overcode.launcher import AgentLauncher

        sm = SessionManager(skip_git_detection=True)
        _add(sm, "alpha")
        app = await self._app()
        # Writing nothing anywhere: the 10 s refresh never runs in this test
        # (the row must come from the focused-pane tick), and the TUI never
        # reaches a writing path.
        with patch.object(type(app), "_fetch_sessions_async", lambda self: None), \
                patch.object(AgentLauncher, "reconcile_lifecycle",
                             side_effect=AssertionError("the TUI must not write lifecycle")), \
                patch.object(SessionManager, "update_session_status",
                             side_effect=AssertionError("the TUI must not write status")):
            async with app.run_test(size=(200, 30)) as pilot:
                await pilot.pause(0.3)
                assert self._names(app) == ["alpha"]

                _add(sm, "bravo")
                # One focused tick is 250 ms; allow one more for the worker
                for _ in range(10):
                    await pilot.pause(0.05)
                    if self._names(app) == ["alpha", "bravo"]:
                        break
                assert self._names(app) == ["alpha", "bravo"]

                sm.delete_session(sm.get_session_by_name("alpha").id)
                for _ in range(10):
                    await pilot.pause(0.05)
                    if self._names(app) == ["bravo"]:
                        break
                assert self._names(app) == ["bravo"]

    async def test_unchanged_table_does_not_reapply(self, short_state_dir):
        sm = SessionManager(skip_git_detection=True)
        _add(sm, "alpha")
        app = await self._app()
        async with app.run_test(size=(200, 30)) as pilot:
            await pilot.pause(0.3)
            applied = MagicMock(wraps=app._apply_sessions)
            with patch.object(app, "_apply_sessions", applied):
                await pilot.pause(1.0)  # four focused ticks
            applied.assert_not_called()
