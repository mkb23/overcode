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


@pytest.mark.parametrize("width", [120, 200])
@pytest.mark.parametrize("preset", ["calm", "colorblind", "shapes"])
async def test_layout_holds_in_every_status_theme(make_tui, width, preset):
    """A theme's glyph keeps the status cell two wide, one-cell shapes included,
    at every detail level, with and without emoji."""
    from overcode.status_theme import StatusTheme, save_theme
    save_theme(StatusTheme(preset))
    engine = GridEngine()
    engine.publish(engine.snapshot_now())
    app = make_tui(_named_agents(), engine=engine, size=(width, 40))
    async with app.run_test(size=(width, 40)) as pilot:
        await _open(pilot, engine)
        for emoji_free in (False, True):
            if emoji_free:
                app.action_toggle_emoji_free()
            for level in app.SUMMARY_LEVELS:
                app._set_summary_level(level)
                await _settle(pilot)
                try:
                    assert_layout_invariants(app)
                except AssertionError as e:
                    raise AssertionError(f"{preset}, {width} cols, {level}, "
                                         f"emoji-free {emoji_free}: {e}") from e
                _shot(app, f"layout-{preset}-{width}-{level}-{'ascii' if emoji_free else 'emoji'}")
