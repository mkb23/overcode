"""The pilot harness itself (tests/tui_harness.py): what every TUI test leans on."""

import pytest

from overcode.engine_protocol import Snapshot
from tests.tui_harness import FakeEngine, ready, three_agents


def _view(sid, **fields):
    return dict(session_id=sid, name=sid, current_status="running", live_colour="green",
                episode_colour="green", **fields)


@pytest.mark.asyncio
class TestHarness:

    async def test_no_timer_runs_and_no_banner_ever_shows(self, make_tui):
        from overcode.tui import ENGINE_BANNER_GRACE_SECONDS

        app = make_tui(three_agents())
        async with app.run_test() as pilot:
            await ready(pilot)
            assert app._periodic_timers == {}
            assert app._engine_connected
            # Long past the grace, the watch still finds the engine there
            app._engine_down_since -= 10 * ENGINE_BANNER_GRACE_SECONDS
            app.update_daemon_status()
            await pilot.pause()
            assert not app.query_one("#engine-banner").has_class("visible")

    async def test_timers_true_starts_them(self, make_tui):
        app = make_tui(three_agents(), timers=True)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert "focused_pane" in app._periodic_timers

    async def test_publish_reaches_the_rows_at_once(self, make_tui):
        engine = FakeEngine()
        app = make_tui(three_agents(), engine=engine)
        async with app.run_test() as pilot:
            await ready(pilot)
            engine.publish(Snapshot(agents={"id-bravo": _view(
                "id-bravo", current_activity="Reading files")}))
            bravo = next(w for w in app._get_widgets_in_session_order()
                         if w.session.name == "bravo")
            assert bravo.current_activity == "Reading files"  # no pause needed
            assert engine.focused_agents == {"id-alpha"} and engine.attended

    async def test_a_dropped_engine_shows_the_banner(self, make_tui):
        app = make_tui(three_agents())
        async with app.run_test() as pilot:
            await ready(pilot)
            app.engine.drop()
            await pilot.pause()
            assert app.query_one("#engine-banner").has_class("visible")
            assert app.daemon_starts.called
            app.engine.come_back()
            await pilot.pause()
            assert not app.query_one("#engine-banner").has_class("visible")
