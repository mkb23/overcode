"""Columns hidden while every agent shares a value, and the header's
"all: …" note, follow the fleet when a value changes without an agent
being added or removed (a rebill, a /model switch). Synthetic records only.
"""

import dataclasses

import pytest

from overcode.engine_protocol import Snapshot
from overcode.tui_widgets import SessionSummary
from tests.tui_harness import FakeEngine, ready, three_agents, until

pytestmark = pytest.mark.unit


def _agents(bravo_code="acme", bravo_model="opus"):
    out = []
    for s in three_agents():
        bravo = s.name == "bravo"
        out.append(dataclasses.replace(
            s, billing_code=bravo_code if bravo else "acme",
            model=bravo_model if bravo else "opus"))
    return out


def _snapshot(agents):
    return Snapshot(agents={s.id: {"current_status": "running", "billing_code": s.billing_code,
                                   "model": s.model} for s in agents})


def _header(app):
    return str(app.query_one("#column-headers").render())


def _shown(app, name, column):
    row = next(w for w in app.query(SessionSummary) if w.session.name == name)
    return column in [c.id for c in row.visible_columns()]


@pytest.mark.asyncio
class TestUniformColumnsFollowTheFleet:

    async def test_an_engine_delta_shows_the_column_again(self, make_tui):
        engine = FakeEngine()
        app = make_tui(_agents(), engine=engine, size=(250, 40))
        async with app.run_test(size=(250, 40)) as pilot:
            await ready(pilot)
            engine.publish(_snapshot(_agents()))
            assert await until(pilot, lambda: app.uniform_columns.get("billing_code") == "acme")
            assert not _shown(app, "bravo", "model")
            # bravo is rebilled and moves to sonnet: no agent added or removed
            engine.publish(_snapshot(_agents("other", "sonnet")))
            await pilot.pause()
            assert "billing_code" not in app.uniform_columns
            assert "model" not in app.uniform_columns
            assert _shown(app, "bravo", "model") and _shown(app, "alpha", "model")
            assert "acme" not in _header(app)

    async def test_a_session_sync_shows_the_column_again(self, make_tui):
        engine = FakeEngine()
        state = {"agents": _agents()}
        app = make_tui(lambda: list(state["agents"]), engine=engine, size=(250, 40))
        async with app.run_test(size=(250, 40)) as pilot:
            await ready(pilot)
            assert await until(pilot, lambda: app.uniform_columns.get("model") is not None)
            state["agents"] = _agents("other", "sonnet")
            app._apply_sessions(state["agents"])
            await pilot.pause()
            assert "billing_code" not in app.uniform_columns
            assert "model" not in app.uniform_columns

    async def test_values_becoming_shared_hide_the_column(self, make_tui):
        engine = FakeEngine()
        app = make_tui(_agents("other", "sonnet"), engine=engine, size=(250, 40))
        async with app.run_test(size=(250, 40)) as pilot:
            await ready(pilot)
            engine.publish(_snapshot(_agents("other", "sonnet")))
            await pilot.pause()
            assert "model" not in app.uniform_columns
            engine.publish(_snapshot(_agents()))
            await pilot.pause()
            assert app.uniform_columns.get("billing_code") == "acme"
            assert not _shown(app, "bravo", "model")
