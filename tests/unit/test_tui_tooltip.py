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
