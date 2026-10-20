"""The agent table's layout at real terminal widths (the layout matrix).

Pilot tests elsewhere run at 420 columns, where nothing is ever cropped,
so a row that wraps at 120 columns passes them all. This sweeps realistic
widths x every detail level x every summary content mode, with an
expanded parent's delegated-work rows (and a member's detail line) open,
and checks ``assert_layout_invariants`` after every change: one screen row
per line, nothing past the edge, the cells of every row on one grid.

``OVERCODE_LAYOUT_SHOTS=<dir>`` also saves an SVG screenshot of each
state into <dir>, to look at. Nothing is written by default.
"""

import os
import re

import pytest

from tests.tui_harness import assert_layout_invariants, three_agents
from tests.unit.test_swarm_grid_tui import GridEngine, _open

pytestmark = pytest.mark.unit

WIDTHS = [120, 160, 200, 260]


def _shot(app, name: str) -> None:
    shots = os.environ.get("OVERCODE_LAYOUT_SHOTS")
    if shots:
        os.makedirs(shots, exist_ok=True)
        app.save_screenshot(f"{name}.svg", path=shots)


async def _settle(pilot) -> None:
    await pilot.pause()
    await pilot.pause()


def _named_agents():
    """three_agents with every kind of name: dim default, "~" auto, plain human (#542)."""
    agents = three_agents()
    for session, source in zip(agents, ("default", "auto", "human")):
        session.name_source = source
    return agents


@pytest.mark.parametrize("width", WIDTHS)
async def test_layout_holds_at_every_detail_level_and_mode(make_tui, width):
    engine = GridEngine()
    engine.publish(engine.snapshot_now())
    app = make_tui(_named_agents(), engine=engine, size=(width, 40))
    async with app.run_test(size=(width, 40)) as pilot:
        rows = await _open(pilot, engine)
        # A member's detail line too
        app.swarm_cursor = "id-bravo/sa/a1"
        app._sync_swarm_rows()
        await pilot.press("right")
        await _settle(pilot)
        assert any(ln.kind == "detail" for ln in rows.lines)
        assert_layout_invariants(app)

        for level in app.SUMMARY_LEVELS:
            app._set_summary_level(level)
            await _settle(pilot)
            for _ in app.SUMMARY_CONTENT_MODES:
                mode = app.summary_content_mode
                try:
                    assert_layout_invariants(app)
                except AssertionError as e:
                    raise AssertionError(f"{width} cols, {level}, {mode}: {e}") from e
                _shot(app, f"layout-{width}-{level}-{mode}")
                app.action_cycle_summary_content()
                await _settle(pilot)

        # Emoji-free at the widest detail
        app.action_toggle_emoji_free()
        await _settle(pilot)
        try:
            assert_layout_invariants(app)
        except AssertionError as e:
            raise AssertionError(f"{width} cols, emoji-free: {e}") from e
        _shot(app, f"layout-{width}-emoji-free")


async def test_layout_follows_a_resize(make_tui):
    """Narrowing the terminal re-crops every row; widening it again restores them."""
    engine = GridEngine()
    engine.publish(engine.snapshot_now())
    app = make_tui(_named_agents(), engine=engine, size=(260, 40))
    async with app.run_test(size=(260, 40)) as pilot:
        await _open(pilot, engine)
        app._set_summary_level("full")
        await _settle(pilot)
        for width in (120, 200, 260):
            await pilot.resize_terminal(width, 40)
            await _settle(pilot)
            try:
                assert_layout_invariants(app)
            except AssertionError as e:
                raise AssertionError(f"resized to {width}: {e}") from e


class TripEngine(GridEngine):
    """GridEngine plus every spend cell's variants: alpha's trip reset (↺ on
    TOK / ENRG / $ / Q%), bravo's SUB$ on trips, quotas and a window burn
    with its quota points (the burn's Q% mode)."""

    def snapshot_now(self):
        snap = super().snapshot_now()
        burn = {str(float(h)): {"input_tokens": 9000, "output_tokens": 3000, "cost_usd": 1.2,
                                "energy_j": 900.0, "quota_pp": 0.84}
                for h in (1, 3, 6, 12, 24)}
        for view in snap.agents.values():
            view.update(quota={"pp7d": 12.34, "pp5h": 3.1, "w7": 40, "w5": 10, "conf": "low"},
                        burn=burn, estimated_energy_j=5.0e5)
        snap.agents["id-alpha"]["trip"] = {
            "reset_at": "2026-10-07T10:00:00+00:00", "estimated_cost_usd": 0.02,
            "estimated_energy_j": 900.0, "input_tokens": 10, "output_tokens": 5,
            "cache_creation_tokens": 0, "cache_read_tokens": 0, "pp7d": 0.01}
        snap.agents["id-bravo"].update(subtree_cost_usd=3.0, subtree_trip_cost_usd=1.0)
        return snap


def _trip_agents():
    agents = _named_agents()
    agents[0].cost_budget_usd = 5.0  # BDG shows in the $ modes
    return agents


@pytest.mark.parametrize("width", WIDTHS)
async def test_layout_holds_with_trip_counters_in_every_cost_mode(make_tui, width):
    """A reset row (↺) and the burn's quota mode keep the grid, in every $ preset."""
    engine = TripEngine()
    engine.publish(engine.snapshot_now())
    app = make_tui(_trip_agents(), engine=engine, size=(width, 40))
    async with app.run_test(size=(width, 40)) as pilot:
        await _open(pilot, engine)
        marked = set()
        for level in app.SUMMARY_LEVELS:
            app._set_summary_level(level)
            await _settle(pilot)
            for _ in app._COST_PRESETS:
                app.action_toggle_cost_display()
                await _settle(pilot)
                mode = app.show_cost
                try:
                    assert_layout_invariants(app)
                except AssertionError as e:
                    raise AssertionError(f"{width} cols, {level}, {mode}: {e}") from e
                _shot(app, f"layout-trip-{width}-{level}-{mode}")
                alpha = next(w for w in app._get_widgets_in_session_order()
                             if w.session.name == "alpha")
                line = alpha.render().plain
                if "↺" in line:
                    marked.add(mode)
                if mode == "quota" and level in ("high", "full") and width >= 200:
                    # 0.84 points over the burn window, in points per hour
                    assert re.search(r"🔥\d+\.\d\d%/h", line), line
        assert {"tokens", "cost", "joules", "quota"} <= marked

        app.action_toggle_emoji_free()
        await _settle(pilot)
        try:
            assert_layout_invariants(app)
        except AssertionError as e:
            raise AssertionError(f"{width} cols, emoji-free: {e}") from e
