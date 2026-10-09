"""The TUI is a view of the engine (docs/design/engine-0.6.md, step 3).

A real ``EngineServer`` on a short /tmp socket publishes snapshots and
deltas; a real TUI (Textual pilot) subscribes the way it does in the split.
The TUI must render what is published, repaint only what changed, ring on
the engine's bell, tell the engine what it is (visible, focus, visits, burn
window), and when the engine is missing show it and start it, never
falling back to computing anything itself.
"""

import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from overcode.engine_protocol import Snapshot
from overcode.engine_socket import EngineServer

pytestmark = pytest.mark.unit

AGENTS = ("alpha", "bravo", "charlie")


def _sessions():
    from overcode.session_manager import Session

    return [Session(id=f"id-{n}", name=n, tmux_session="test", tmux_window=f"w{i}",
                    command=["claude"], start_directory="/tmp",
                    start_time="2026-10-07T10:00:00", repo_name=f"repo-{n}", branch="main")
            for i, n in enumerate(AGENTS)]


def _view(name, **fields):
    view = dict(
        session_id=f"id-{name}", name=name, current_status="running",
        current_activity=f"{name} working", status_since="2026-10-07T11:00:00",
        live_colour="green", episode_colour="green", episode_start=1_791_370_000.0,
        live_since=1_791_370_000.0, stats_available=True, input_tokens=1000,
        output_tokens=500, interaction_count=3, estimated_cost_usd=0.5,
        backend="claude-code",
    )
    view.update(fields)
    return view


def _snapshot(**per_agent):
    agents = {}
    for name in AGENTS:
        view = _view(name, **per_agent.get(name, {}))
        agents[view["session_id"]] = view
    return Snapshot(agents=agents, fleet={"status": "active", "current_interval": 2,
                                          "interval_mode": "attended"})


@pytest.fixture
def state_dir(monkeypatch):
    """A short state dir: Unix socket paths are limited to ~104 bytes on macOS."""
    import os

    for k in list(os.environ):
        if k.startswith("OVERCODE_"):
            monkeypatch.delenv(k)
    d = Path(tempfile.mkdtemp(prefix="oc-tv-", dir="/tmp"))
    monkeypatch.setenv("OVERCODE_DIR", str(d))
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(d / "s"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def engine(state_dir):
    """The engine's socket, served by a real EngineServer (no daemon behind it)."""
    from overcode import tui_engine

    server = EngineServer(tui_engine.engine_socket_path("test"))
    server.start()
    yield server
    server.stop()


@pytest.fixture
def app(state_dir):
    from overcode.status_detector_factory import StatusDetectorDispatcher
    from overcode.tui import SupervisorTUI

    def no_detection(*a, **kw):
        raise AssertionError("the TUI must not detect status (the engine does)")

    starts = MagicMock()
    with patch.object(SupervisorTUI, "_ensure_monitor_daemon", starts), \
            patch.object(StatusDetectorDispatcher, "detect_status", no_detection), \
            patch("overcode.launcher.AgentLauncher.list_sessions", lambda self: _sessions()):
        tui = SupervisorTUI(tmux_session="test")
        tui._notifier = MagicMock()
        tui.daemon_starts = starts
        yield tui


async def _until(pilot, predicate, timeout=5.0):
    for _ in range(int(timeout / 0.05)):
        if predicate():
            return True
        await pilot.pause(0.05)
    return predicate()


def _row(app, name):
    from overcode.tui_widgets import SessionSummary

    return next(w for w in app.query(SessionSummary) if w.session.name == name)


async def _connected(pilot, app):
    assert await _until(pilot, lambda: app._engine_connected and len(app._engine_agents) == 3)
    await pilot.pause(0.1)


@pytest.mark.asyncio
class TestRendersWhatTheEnginePublishes:

    async def test_status_colour_badges_stats_git_and_burn(self, app, engine):
        app._prefs.baseline_minutes = 60
        app.baseline_minutes = 60
        engine.publish(_snapshot(
            bravo=dict(current_status="waiting_user", current_activity="Waiting for input",
                       live_colour="red", episode_colour="red",
                       status_detail={"color": "yellow", "legacy_status": "waiting_user",
                                      "badges": [{"kind": "schedule_wakeup",
                                                  "eta_at": 4_000_000_000}]},
                       git_diff=[4, 120, 30], git_untracked=2,
                       burn={"1.0": {"input_tokens": 6000, "output_tokens": 1200,
                                     "cost_usd": 3.0, "energy_j": 3600.0}},
                       background_bash_count=2, auto_accept_mode=True),
        ))
        async with app.run_test(size=(220, 40)) as pilot:
            await _connected(pilot, app)
            row = _row(app, "bravo")
            assert row.detected_status == "waiting_user"
            assert row.current_activity == "Waiting for input"
            assert row.claude_stats.total_tokens == 1500
            assert row.git_diff_stats == (4, 120, 30) and row.git_untracked_count == 2
            assert row.window_burn.cost_per_hour == 3.0
            assert row.background_bash_count == 2 and row.auto_accept_mode is True
            assert row.status_detail.badges[0].eta_seconds > 0
            assert row.any_has_status_detail and row.any_has_burn
            plain = row.render().plain
            assert "🔴" in plain  # the status colour
            assert "Δ 4" in plain or "Δ4" in plain  # the git column
            # Every row's status comes from the engine
            assert _row(app, "alpha").detected_status == "running"
            # The status bar: fleet fields and the summed burn for the window
            from overcode.tui_widgets import DaemonStatusBar

            bar = app.query_one("#daemon-status", DaemonStatusBar)
            assert bar.engine_connected and bar.monitor_state.status == "active"
            assert bar._burn_stats.cost_usd == 3.0 and bar._burn_stats.window_hours == 1.0

    async def test_a_delta_updates_only_the_affected_row(self, app, engine):
        engine.publish(_snapshot())
        async with app.run_test(size=(200, 40)) as pilot:
            await _connected(pilot, app)
            applied = []
            for name in AGENTS:
                row = _row(app, name)
                original = row.apply_engine

                def spy(view, *a, _name=name, _orig=original, **kw):
                    applied.append(_name)
                    return _orig(view, *a, **kw)

                row.apply_engine = spy
            engine.publish(_snapshot(charlie=dict(current_status="waiting_user",
                                                  live_colour="red", episode_colour="red")))
            assert await _until(pilot, lambda: _row(app, "charlie").detected_status == "waiting_user")
            assert applied == ["charlie"]
            assert _row(app, "alpha").detected_status == "running"

    async def test_columns_realign_only_when_a_changed_row_needs_it(self, app, engine):
        engine.publish(_snapshot())
        async with app.run_test(size=(220, 40)) as pilot:
            await _connected(pilot, app)
            recomputes = []
            original = app._recompute_cell_column_widths
            app._recompute_cell_column_widths = lambda *a, **k: (recomputes.append(1),
                                                                  original(*a, **k))
            widths = list(app.column_widths)
            # Same widths: a new activity line lives in the content area
            engine.publish(_snapshot(bravo=dict(current_activity="Reading files")))
            assert await _until(pilot, lambda: _row(app, "bravo").current_activity
                                == "Reading files")
            await pilot.pause(0.1)
            assert recomputes == [] and app.column_widths == widths
            # A wider git cell: every row realigns to it
            engine.publish(_snapshot(bravo=dict(current_activity="Reading files",
                                                git_diff=[123, 99999, 9999])))
            assert await _until(pilot, lambda: app.column_widths != widths)
            assert recomputes

    async def test_a_quiet_engine_repaints_nothing(self, app, engine):
        engine.publish(_snapshot())
        async with app.run_test(size=(200, 40)) as pilot:
            await _connected(pilot, app)
            seq = app._engine_client.state.snapshot.seq
            engine.publish(_snapshot())  # nothing changed: no delta at all
            await pilot.pause(0.3)
            assert app._engine_client.state.snapshot.seq == seq


@pytest.mark.asyncio
class TestBells:

    async def test_the_engines_bell_notifies_and_lights_the_row(self, app, engine):
        engine.publish(_snapshot())
        async with app.run_test(size=(200, 40)) as pilot:
            await _connected(pilot, app)
            assert _row(app, "charlie").is_unvisited_stalled is False
            engine.ring("id-charlie", {"colour": "red", "start": 1.0, "name": "charlie"})
            assert await _until(pilot, lambda: app._notifier.queue.called)
            app._notifier.queue.assert_called_once_with("charlie", "charlie working")
            app._notifier.flush.assert_called()
            assert _row(app, "charlie").is_unvisited_stalled is True

    async def test_the_focused_agent_needing_a_look_counts_as_seen(self, app, engine, monkeypatch):
        """The focused agent is on screen (the split's bottom pane): its 🔔
        clears after a few seconds, and the engine is told of the visit."""
        monkeypatch.setattr("overcode.tui.BELL_SEEN_AFTER_SECONDS", 0.2)
        engine.publish(_snapshot(alpha=dict(input_needed_since=100.0)))
        async with app.run_test(size=(200, 40)) as pilot:
            await _connected(pilot, app)
            visits = []
            assert await _until(pilot, lambda: visits.extend(engine.take_visits()) or visits)
            assert [v[0] for v in visits] == ["id-alpha"]
            assert _row(app, "alpha").is_unvisited_stalled is False

    async def test_the_highlight_follows_the_attention_fields(self, app, engine):
        engine.publish(_snapshot(bravo=dict(input_needed_since=100.0, visited_at=50.0)))
        async with app.run_test(size=(200, 40)) as pilot:
            await _connected(pilot, app)
            assert _row(app, "bravo").is_unvisited_stalled is True
            # Someone visited (e.g. another view): the engine says so
            engine.publish(_snapshot(bravo=dict(input_needed_since=100.0, visited_at=150.0)))
            assert await _until(pilot, lambda: not _row(app, "bravo").is_unvisited_stalled)


@pytest.mark.asyncio
class TestTellsTheEngine:

    async def test_visible_focus_burn_window_and_visits(self, app, engine):
        engine.publish(_snapshot(bravo=dict(input_needed_since=100.0)))
        async with app.run_test(size=(200, 40)) as pilot:
            await _connected(pilot, app)
            # Visible (no tmux pane in a test: attended), the focused agent,
            # and the burn window (the spin baseline, prefs default 60 min)
            assert await _until(pilot, lambda: engine.attended)
            assert await _until(pilot, lambda: engine.focused_agents == {"id-alpha"})
            assert engine.burn_windows == {app.baseline_minutes / 60}
            # j moves focus to bravo, a 🔔 agent: focus and a visit reach the engine
            await pilot.press("j")
            assert await _until(pilot, lambda: engine.focused_agents == {"id-bravo"})
            visits = []
            assert await _until(pilot, lambda: visits.extend(engine.take_visits()) or visits)
            assert [v[0] for v in visits] == ["id-bravo"]
            assert _row(app, "bravo").is_unvisited_stalled is False  # cleared at once, locally
            # A new spin baseline is a new burn window
            app.action_baseline_back()
            assert await _until(pilot, lambda: engine.burn_windows == {app.baseline_minutes / 60})
            # Detached: the engine hears the view is not being looked at
            app._set_attended(False)
            assert await _until(pilot, lambda: not engine.attended)

    async def test_bells_still_reach_a_detached_view(self, app, engine):
        engine.publish(_snapshot())
        async with app.run_test(size=(200, 40)) as pilot:
            await _connected(pilot, app)
            app._set_attended(False)
            engine.ring("id-alpha", {"colour": "red", "start": 1.0, "name": "alpha"})
            assert await _until(pilot, lambda: app._notifier.queue.called)


class TestSisterAgents:
    """Sisters stay on HTTP polling (0.6.0): no bell message reaches this TUI
    for their agents, so a new input-needed stretch in a 0.6 sister's
    forwarded state notifies here; first sight never does."""

    def _app(self):
        from overcode.tui import SupervisorTUI

        app = SupervisorTUI.__new__(SupervisorTUI)
        app._visited_here = {}
        app._remote_stretch_seen = {}
        app._notifier = MagicMock()
        return app

    def _row(self, since, unvisited=True):
        row = MagicMock()
        row.session.id = "remote-1"
        row.session.name = "far"
        row.session.remote_daemon_state = {"input_needed_since": since}
        row.is_unvisited_stalled = unvisited
        row.current_activity = "Waiting"
        row.apply_remote.return_value = False
        return row

    def test_a_new_stretch_notifies_once(self):
        app = self._app()
        app._apply_remote(self._row(None, unvisited=False))  # first sight, working
        app._apply_remote(self._row(100.0))  # starts needing input
        app._apply_remote(self._row(100.0))  # same stretch, polled again
        app._notifier.queue.assert_called_once_with("far", "Waiting")

    def test_first_sight_of_a_stalled_agent_is_quiet(self):
        app = self._app()
        app._apply_remote(self._row(100.0))
        app._notifier.queue.assert_not_called()


@pytest.mark.asyncio
class TestEngineAbsent:

    async def test_no_socket_shows_the_banner_and_starts_the_daemon(self, app, state_dir):
        async with app.run_test(size=(200, 40)) as pilot:
            banner = app.query_one("#engine-banner")
            container = app.query_one("#sessions-container")
            assert await _until(pilot, lambda: banner.has_class("visible"), timeout=4)
            assert container.has_class("engine-stale")
            assert app.daemon_starts.called
            # Nothing was computed in its place: rows keep what sessions.json says
            assert all(w.engine == {} for w in app.query("SessionSummary"))

    async def test_a_dropped_engine_is_shown_restarted_and_resynced(self, app, engine):
        from overcode import tui_engine

        engine.publish(_snapshot())
        async with app.run_test(size=(200, 40)) as pilot:
            await _connected(pilot, app)
            banner = app.query_one("#engine-banner")
            assert not banner.has_class("visible")
            starts_before = app.daemon_starts.call_count
            engine.stop()  # the daemon died
            assert await _until(pilot, lambda: banner.has_class("visible"))
            assert app.daemon_starts.call_count > starts_before
            # The last snapshot stays on screen (dimmed), never recomputed
            assert _row(app, "alpha").detected_status == "running"
            # The daemon comes back with news: the view resyncs from its snapshot
            server = EngineServer(tui_engine.engine_socket_path("test"))
            server.start()
            try:
                server.publish(_snapshot(alpha=dict(current_status="waiting_user",
                                                    live_colour="red", episode_colour="red")))
                assert await _until(pilot, lambda: _row(app, "alpha").detected_status
                                    == "waiting_user", timeout=6)
                assert await _until(pilot, lambda: not banner.has_class("visible"))
                assert not app.query_one("#sessions-container").has_class("engine-stale")
            finally:
                server.stop()

    async def test_a_frozen_engine_is_shown_not_trusted(self, app, engine, monkeypatch):
        """Connected but its main loop stopped (2026-10-09: 26 h of frozen
        status looked live): the banner says frozen and the list dims."""
        from overcode import engine_socket

        monkeypatch.setattr(engine_socket, "PING_SECONDS", 0.05)
        monkeypatch.setattr(engine_socket, "STALL_SECONDS", 0.3)
        engine.publish(_snapshot())
        async with app.run_test(size=(200, 40)) as pilot:
            await _connected(pilot, app)
            banner = app.query_one("#engine-banner")
            container = app.query_one("#sessions-container")
            # Nothing beats the engine here: its pings soon carry a stale age
            assert await _until(pilot, lambda: banner.has_class("visible"), timeout=4)
            assert container.has_class("engine-stale")
            assert "frozen" in str(banner.render())
            assert app.query_one("#daemon-status").engine_stalled_for is not None
            assert app._engine_connected  # frozen, not gone: the daemon's watchdog acts
            # The loop beats again: the banner goes
            monkeypatch.setattr(engine_socket, "STALL_SECONDS", 1e9)
            assert await _until(pilot, lambda: not banner.has_class("visible"), timeout=4)
            assert not container.has_class("engine-stale")
            assert app.query_one("#daemon-status").engine_stalled_for is None
