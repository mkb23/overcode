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

A popup taller than the room above and below the pointer (the split's top
pane is short) was pushed back inside the screen on top of the pointer,
and Textual clears a popup that is under the mouse on the next layout
refresh: it showed for a moment and went. Each popup is held to the taller
of the two, so it always fits on one side and is cut there instead.

This leans on Screen internals (``_handle_mouse_move``, ``_tooltip_widget``);
if a Textual release changes them it falls back to Textual's own behaviour.
"""

from __future__ import annotations

from textual import events
from textual.screen import Screen


# Tooltip's own vertical margin (margin: 1 0): rows kept clear on each side
TOOLTIP_MARGIN = 1


def tooltip_room(pointer_y: int, screen_height: int) -> int:
    """The tallest popup that fits above or below the pointer's row."""
    above = pointer_y - TOOLTIP_MARGIN
    below = screen_height - pointer_y - 1 - TOOLTIP_MARGIN
    return max(1, above, below)


class SteadyTooltipScreen(Screen):
    """The app's default screen: a popup stays up while the mouse stays on
    its widget, and is never taller than fits beside the pointer."""

    def _handle_tooltip_timer(self, widget) -> None:
        super()._handle_tooltip_timer(widget)
        try:
            from textual.widgets._tooltip import Tooltip
            tooltip = self.get_child_by_type(Tooltip)
            if tooltip.display:
                tooltip.styles.max_height = tooltip_room(self.app.mouse_position.y,
                                                         self.size.height)
        except Exception:
            pass

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
