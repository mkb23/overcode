"""
Hover popups that hold still.

Textual hides a showing tooltip on any mouse move over the same widget and
shows it again half a second after the next one, so a hand resting on an
agent row's cell (terminals report the mouse in pixels) flickered the popup
on and off. An agent row is one widget whose popup depends on the column
under the pointer: the row swaps or clears its ``tooltip`` itself as the
pointer crosses columns (``SessionSummary.on_mouse_move``), and Textual
updates a showing popup in place when it does. So here a move that stays on
the same widget keeps the popup up; leaving the widget still clears it.

This leans on Screen internals (``_handle_mouse_move``, ``_tooltip_widget``);
if a Textual release changes them it falls back to Textual's own behaviour.
"""

from __future__ import annotations

from textual import events
from textual.screen import Screen


class SteadyTooltipScreen(Screen):
    """The app's default screen: a popup stays up while the mouse stays on its widget."""

    def _handle_mouse_move(self, event: events.MouseMove) -> None:
        try:
            from textual.widgets._tooltip import Tooltip
            tooltip = self.get_child_by_type(Tooltip)
            shown = bool(tooltip.display)
            before = self._tooltip_widget
        except Exception:
            super()._handle_mouse_move(event)
            return
        super()._handle_mouse_move(event)
        try:
            if (shown and not tooltip.display and before is not None
                    and self._tooltip_widget is before
                    and self.app.mouse_over is before
                    and before.tooltip is not None):
                tooltip.display = True
        except Exception:
            pass
