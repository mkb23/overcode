"""
Hover popups hold still (tui_tooltip.SteadyTooltipScreen).

Textual hides a showing tooltip on any mouse move over its widget, so a
resting hand flickered an agent row's popup. On the steady screen a move
within the widget keeps it; leaving the widget still clears it.
"""

import pytest
from textual.app import App
from textual.screen import Screen
from textual.widgets import Static
from textual.widgets._tooltip import Tooltip

from overcode.tui_tooltip import SteadyTooltipScreen


def _app(screen_class):
    class Hover(App):
        TOOLTIP_DELAY = 0.05
        CSS = "Static { height: 1; width: 30; }"

        def get_default_screen(self):
            return screen_class(id="_default")

        def compose(self):
            yield Static("x" * 30, id="tipped").with_tooltip("what the cell means")
            yield Static("plain", id="plain")

    return Hover()


async def _shown_after_a_small_move(screen_class):
    app = _app(screen_class)
    async with app.run_test(size=(60, 12), tooltips=True) as pilot:
        await pilot.hover("#tipped", offset=(3, 0))
        await pilot.pause(0.2)
        tooltip = app.screen.get_child_by_type(Tooltip)
        assert tooltip.display
        await pilot.hover("#tipped", offset=(4, 0))
        await pilot.pause()
        shown = bool(tooltip.display)
        await pilot.hover("#plain", offset=(1, 0))
        await pilot.pause(0.2)
        return shown, bool(tooltip.display)


@pytest.mark.asyncio
async def test_a_move_within_the_widget_keeps_the_popup():
    shown, after_leaving = await _shown_after_a_small_move(SteadyTooltipScreen)
    assert shown
    assert not after_leaving


@pytest.mark.asyncio
async def test_textuals_own_screen_hides_it_which_is_the_flicker():
    shown, _ = await _shown_after_a_small_move(Screen)
    assert not shown


def test_the_tui_uses_it():
    from overcode.tui import SupervisorTUI
    assert SupervisorTUI.get_default_screen is not App.get_default_screen


TALL = "\n".join(f"line {i}" for i in range(16))


def _tall_app(screen_class):
    """The split's top pane: 17 rows, a row at y=9 with a 16-line popup."""
    class Tall(App):
        TOOLTIP_DELAY = 0.05
        CSS = ("Static { height: 1; width: 30; } #pad { height: 9; } "
               "Tooltip { padding: 0 1; max-width: 48; }")

        def get_default_screen(self):
            return screen_class(id="_default")

        def compose(self):
            yield Static("", id="pad")
            yield Static("x" * 30, id="tipped").with_tooltip(TALL)

    return Tall()


async def _tall_popup_after_a_layout(screen_class):
    app = _tall_app(screen_class)
    async with app.run_test(size=(80, 17), tooltips=True) as pilot:
        await pilot.hover("#tipped", offset=(3, 0))
        await pilot.pause(0.2)
        app.screen.refresh(layout=True)
        await pilot.pause()
        await pilot.pause()
        tooltip = app.screen.get_child_by_type(Tooltip)
        covers = tooltip.region.contains(*app.mouse_position)
        return bool(tooltip.display), covers


@pytest.mark.asyncio
async def test_a_popup_too_tall_for_the_pane_stays_beside_the_pointer():
    """A popup taller than the room above and below the pointer is held to
    the taller side, so it never lands on the pointer (where Textual's next
    layout refresh would clear it)."""
    shown, covers = await _tall_popup_after_a_layout(SteadyTooltipScreen)
    assert shown and not covers


@pytest.mark.asyncio
async def test_textuals_own_screen_loses_a_tall_popup_which_is_the_flicker():
    shown, _ = await _tall_popup_after_a_layout(Screen)
    assert not shown


def test_tooltip_room():
    from overcode.tui_tooltip import tooltip_room
    assert tooltip_room(9, 17) == 8   # rows 0-7 above (row 8 is the margin)
    assert tooltip_room(2, 17) == 13  # below wins near the top
    assert tooltip_room(0, 1) == 1
