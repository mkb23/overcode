"""The TUI does bounded work: counted calls, never wall-clock time (quality review item 37).

A view of the engine should do nothing while nothing changes, and for one
delta only the work that delta needs. These tests wrap the real methods
that do the work and count the calls:

- ``SessionSummary.apply_engine``: a row taking its agent's view;
- ``SessionSummary._full_builds``: a row's cells built again from scratch;
- ``SessionSummary._compose_line``: a row's line padded and joined again;
- ``Widget.refresh`` on a row or on the status bar: Textual repaints it;
- ``app._repaint``: the column-aligning repaint of changed rows;
- ``tui_engine.monitor_state``: the status bar's fleet state rebuilt.

The clock is frozen (``tui_helpers.datetime`` for the rows'
durations, ``daemon_status_bar.datetime`` for the bar's "queried Xs
ago"), so a ticking value moving mid-test never counts as work: what is
counted is work done while nothing the screen shows has changed.
"""

from datetime import datetime

import pytest

from overcode.engine_protocol import Snapshot
from tests.tui_harness import NO_WORK, CountedWork, FakeEngine, ready, three_agents, until

pytestmark = pytest.mark.unit

FROZEN_NOW = datetime(2026, 10, 7, 12, 0, 0)


def _view(name, i, **fields):
    view = dict(
        session_id=f"id-{name}", name=name, current_status="running",
        current_activity=f"{name} working", status_since="2026-10-07T11:00:00",
        live_colour="green", episode_colour="green", episode_start=1_791_370_000.0 + i,
        live_since=1_791_370_000.0 + i, stats_available=True, input_tokens=1000,
        output_tokens=500, interaction_count=3, estimated_cost_usd=0.5,
        backend="claude-code",
    )
    view.update(fields)
    return view


def _snapshot(**per_agent):
    agents = {}
    for i, name in enumerate(("alpha", "bravo", "charlie")):
        view = _view(name, i, **per_agent.get(name, {}))
        agents[view["session_id"]] = view
    return Snapshot(agents=agents, fleet={"status": "active", "current_interval": 2,
                                          "interval_mode": "attended"})


@pytest.fixture
def frozen_clock(monkeypatch):
    """Rows read the time through tui_helpers, the status bar's "queried
    Xs ago" through its own module; hold both still."""
    from overcode import tui_helpers
    from overcode.tui_widgets import daemon_status_bar

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return FROZEN_NOW if tz is None else FROZEN_NOW.replace(tzinfo=tz)

    monkeypatch.setattr(tui_helpers, "datetime", Frozen)
    monkeypatch.setattr(daemon_status_bar, "datetime", Frozen)


@pytest.fixture
def engine():
    engine = FakeEngine()
    engine.publish(_snapshot())
    return engine


@pytest.fixture
def app(make_tui, engine, frozen_clock):
    return make_tui(three_agents(), engine=engine, size=(220, 40))


async def _settled(pilot):
    await ready(pilot)
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


async def _focused_tick(pilot):
    """One 250 ms tick, its capture applied (the capture thread posts back)."""
    app = pilot.app
    app._focused_pane_tick()
    assert await until(pilot, lambda: not app._focused_capture_in_flight, timeout=2)
    await pilot.pause()


@pytest.mark.asyncio
class TestAQuietFleet:

    async def test_quiet_ticks_rebuild_and_repaint_nothing(self, app, monkeypatch):
        """40 row-clock/capture ticks (10 s of the 250 ms timer) and 10
        status-bar ticks over a fleet where nothing happens: no row is
        applied, rebuilt, re-padded or repainted, and the bar is not
        rebuilt or repainted."""
        async with app.run_test() as pilot:
            await _settled(pilot)
            for _ in range(2):  # the first ticks may bring news (usage, local state)
                await _focused_tick(pilot)
                app.update_daemon_status()
                await app.workers.wait_for_complete()
                await pilot.pause()
            work = CountedWork(app, monkeypatch)
            for i in range(40):
                await _focused_tick(pilot)
                if i % 4 == 0:
                    app.update_daemon_status()
                    await app.workers.wait_for_complete()
                    await pilot.pause()
            await pilot.pause()
            assert work.total() == NO_WORK

    async def test_republishing_the_same_view_applies_nothing(self, app, engine, monkeypatch):
        """The engine publishing what the TUI already shows (10 times) is
        no row apply, no rebuild, no repaint and no status-bar rebuild."""
        async with app.run_test() as pilot:
            await _settled(pilot)
            work = CountedWork(app, monkeypatch)
            for _ in range(10):
                engine.publish(_snapshot())
            await pilot.pause()
            assert work.total() == NO_WORK


    async def test_the_running_clock_rebuilds_no_row(self, make_tui, engine, monkeypatch):
        """With the real clock, 40 ticks may redraw a duration (the ticking
        cells alone), but never build a row from scratch or apply a view."""
        app = make_tui(three_agents(), engine=engine, size=(220, 40))
        async with app.run_test() as pilot:
            await _settled(pilot)
            await _focused_tick(pilot)
            work = CountedWork(app, monkeypatch)
            for _ in range(40):
                await _focused_tick(pilot)
            assert work.full_builds() == {}
            assert work.applies == {} and work.bar_builds == 0
            # A row repaints only when its line moved: these durations are
            # days and weeks (0.1 d steps), so at most one step in the test
            assert all(n <= 1 for n in work.refreshes.values()), work.refreshes


@pytest.mark.asyncio
class TestOneDelta:

    async def test_one_activity_delta_is_one_apply_of_one_row(self, app, engine, monkeypatch):
        async with app.run_test() as pilot:
            await _settled(pilot)
            # At full detail the summary runs past the row's edge, where a
            # change repaints nothing; at med it is on screen
            app._set_summary_level("med")
            await pilot.pause()
            work = CountedWork(app, monkeypatch)
            engine.publish(_snapshot(bravo=dict(current_activity="Reading files")))
            await pilot.pause()
            bravo = next(w for w in app._get_widgets_in_session_order()
                         if w.session.name == "bravo")
            assert "Reading files" in bravo.render().plain
            assert work.applies == {"bravo": 1}
            assert work.repaints == [["bravo"]]
            assert work.full_builds() == {"bravo": 1}
            assert work.refreshes == {"bravo": 1}  # the row only: no bar repaint
            assert work.bar_builds == 0
            assert set(work.composes) == {"bravo"}
            # Drawn once, then quiet again
            work.reset()
            for _ in range(8):
                await _focused_tick(pilot)
            assert work.total() == NO_WORK

    async def test_one_status_delta_repaints_one_row(self, app, engine, monkeypatch):
        """A status change is one row's work, and one bar rebuild (the
        bar counts the agents running)."""
        async with app.run_test() as pilot:
            await _settled(pilot)
            work = CountedWork(app, monkeypatch)
            engine.publish(_snapshot(charlie=dict(current_status="waiting_user",
                                                  current_activity="Waiting",
                                                  live_colour="red", episode_colour="red")))
            await pilot.pause()
            assert work.applies == {"charlie": 1}
            assert work.repaints == [["charlie"]]
            assert work.full_builds() == {"charlie": 1}
            assert work.bar_builds == 1
            assert work.refreshes == {"charlie": 1, "bar": 1}

    async def test_one_delegated_work_delta_repaints_one_row(self, make_tui, frozen_clock,
                                                             monkeypatch):
        """The 👥 counts (swarm_rollup) change for one agent: that row's
        work only; the same counts again are no work at all."""
        def swarm(running, ok):
            return {"total": running + ok, "running": running, "waiting": 0, "idle": 0,
                    "ok": ok, "failed": 0, "workflows": 0, "workflows_running": 0}

        engine = FakeEngine()
        engine.publish(_snapshot(bravo=dict(swarm_rollup=swarm(3, 0))))
        app = make_tui(three_agents(), engine=engine, size=(220, 40))
        async with app.run_test() as pilot:
            await _settled(pilot)
            from overcode.tui_widgets.session_summary import SessionSummary
            lines = {row.session.name: row.render().plain for row in app.query(SessionSummary)}
            assert "👥 ▶3" in lines["bravo"]
            assert "👥  -" in lines["alpha"]  # the column shows once any agent delegates
            work = CountedWork(app, monkeypatch)
            engine.publish(_snapshot(bravo=dict(swarm_rollup=swarm(2, 1))))
            await pilot.pause()
            assert work.applies == {"bravo": 1}
            assert work.repaints == [["bravo"]]
            assert work.full_builds() == {"bravo": 1}
            assert work.bar_builds == 0
            work.reset()
            for _ in range(5):
                engine.publish(_snapshot(bravo=dict(swarm_rollup=swarm(2, 1))))
            await pilot.pause()
            assert work.total() == NO_WORK

    async def test_a_bar_input_rebuilds_the_bar_once_and_no_row(self, app, engine, monkeypatch):
        async with app.run_test() as pilot:
            await _settled(pilot)
            work = CountedWork(app, monkeypatch)
            snap = _snapshot()
            snap.fleet["current_interval"] = 7
            engine.publish(snap)
            await pilot.pause()
            assert work.bar_builds == 1
            assert work.refreshes == {"bar": 1}
            assert work.applies == {} and work.repaints == [] and work.full_builds() == {}
