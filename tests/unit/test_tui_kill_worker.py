"""The TUI's kill and clean-up run off the main thread (#528 item 7).

The confirmed action hands the work (tmux, the archive commit) to a thread
worker; the row leaves, the notification shows and the next agent is
chosen once it is done, on the main thread.
"""

import threading

import pytest

from overcode.launcher import AgentLauncher
from overcode.session_manager import SessionManager
from tests.tui_harness import ready, three_agents, until


def _row(app, name):
    return next(w for w in app._get_widgets_in_session_order() if w.session.name == name)


def _names(app):
    return [w.session.name for w in app._get_widgets_in_session_order()]


def _choose(app, name):
    app._user_navigated = True
    app.focused_session_index = _names(app).index(name)


@pytest.fixture
def notes(monkeypatch):
    shown = []
    from overcode.tui import SupervisorTUI

    monkeypatch.setattr(SupervisorTUI, "notify",
                        lambda self, message, severity="information", **kw: shown.append((message, severity)))
    return shown


class _Gate:
    """Hold the worker until the test lets it go; record its thread."""

    def __init__(self, result=True):
        self.go = threading.Event()
        self.threads = []
        self.result = result

    def __call__(self, *args, **kwargs):
        self.threads.append(threading.current_thread())
        assert self.go.wait(5)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


async def test_kill_runs_in_a_worker_and_removes_the_row(make_tui, monkeypatch, notes):
    gate = _Gate()
    monkeypatch.setattr(AgentLauncher, "kill_session", lambda self, name: gate(name))
    app = make_tui(three_agents(), size=(160, 30))
    async with app.run_test(size=(160, 30)) as pilot:
        await ready(pilot)
        _choose(app, "bravo")
        await pilot.pause()
        app._execute_kill(_row(app, "bravo"), "bravo", "id-bravo")
        await pilot.pause()
        # The main thread is free while the kill runs: the row is still there
        assert "bravo" in _names(app)
        assert notes == []
        gate.go.set()
        await app.workers.wait_for_complete()
        assert await until(pilot, lambda: "bravo" not in _names(app))
        assert gate.threads and gate.threads[0] is not threading.main_thread()
        assert ("Killed agent: bravo", "information") in notes
        assert "id-bravo" in app._terminated_sessions
        assert app._terminated_sessions["id-bravo"].status == "terminated"
        # The row now in its place is chosen
        assert _names(app)[app.focused_session_index] == "charlie"


async def test_failed_kill_keeps_the_row(make_tui, monkeypatch, notes):
    gate = _Gate(result=RuntimeError("tmux exploded"))
    gate.go.set()
    monkeypatch.setattr(AgentLauncher, "kill_session", lambda self, name: gate(name))
    app = make_tui(three_agents(), size=(160, 30))
    async with app.run_test(size=(160, 30)) as pilot:
        await ready(pilot)
        app._execute_kill(_row(app, "bravo"), "bravo", "id-bravo")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "bravo" in _names(app)
        assert ("Failed to kill agent: bravo", "error") in notes
        assert app.is_running


async def test_a_choice_made_while_the_kill_runs_is_kept(make_tui, monkeypatch, notes):
    gate = _Gate()
    monkeypatch.setattr(AgentLauncher, "kill_session", lambda self, name: gate(name))
    app = make_tui(three_agents(), size=(160, 30))
    async with app.run_test(size=(160, 30)) as pilot:
        await ready(pilot)
        _choose(app, "alpha")
        await pilot.pause()
        app._execute_kill(_row(app, "alpha"), "alpha", "id-alpha")
        _choose(app, "charlie")
        await pilot.pause()
        gate.go.set()
        await app.workers.wait_for_complete()
        assert await until(pilot, lambda: "alpha" not in _names(app))
        assert app._chosen_session_id() == "id-charlie"
        assert _names(app)[app.focused_session_index] == "charlie"


async def test_second_confirmation_while_running_is_ignored(make_tui, monkeypatch, notes):
    gate = _Gate()
    monkeypatch.setattr(AgentLauncher, "kill_session", lambda self, name: gate(name))
    app = make_tui(three_agents(), size=(160, 30))
    async with app.run_test(size=(160, 30)) as pilot:
        await ready(pilot)
        row = _row(app, "bravo")
        app._execute_kill(row, "bravo", "id-bravo")
        app._execute_kill(row, "bravo", "id-bravo")
        gate.go.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert len(gate.threads) == 1
        assert notes == [("Killed agent: bravo", "information")]


async def test_cleanup_runs_in_a_worker_and_removes_the_row(make_tui, monkeypatch, notes):
    gate = _Gate(result=None)
    monkeypatch.setattr(SessionManager, "delete_session", lambda self, sid: gate(sid))
    app = make_tui(three_agents(), size=(160, 30))
    async with app.run_test(size=(160, 30)) as pilot:
        await ready(pilot)
        _choose(app, "bravo")
        await pilot.pause()
        app._execute_cleanup(_row(app, "bravo"), "bravo", "id-bravo")
        await pilot.pause()
        assert "bravo" in _names(app)
        gate.go.set()
        await app.workers.wait_for_complete()
        assert await until(pilot, lambda: "bravo" not in _names(app))
        assert gate.threads[0] is not threading.main_thread()
        assert ("Cleaned up agent: bravo", "information") in notes
        assert _names(app)[app.focused_session_index] == "charlie"
