"""Clicking an agent's row selects it like j/k, so the tmux pane follows."""

from unittest.mock import MagicMock

import pytest

from tests.unit.test_command_palette import _ready, tui  # noqa: F401 (fixture)


def _rows(session_ids):
    rows = []
    for sid in session_ids:
        w = MagicMock()
        w.session.id = sid
        rows.append(w)
    return rows


class TestClickHandler:

    def _app(self, focused=0):
        from overcode.tui import SupervisorTUI
        app = MagicMock()
        app._get_widgets_in_session_order.return_value = _rows(["a", "b", "c"])
        app.focused_session_index = focused
        app._user_navigated = False
        return app, SupervisorTUI.on_session_summary_clicked

    def test_clicking_another_row_navigates_to_it(self):
        app, handler = self._app(focused=0)
        handler(app, MagicMock(session_id="c"))
        assert app.focused_session_index == 2
        assert app._user_navigated is True  # the index watcher syncs tmux
        app._sync_tmux_window.assert_not_called()

    def test_clicking_the_selected_row_resyncs_the_pane(self):
        app, handler = self._app(focused=1)
        handler(app, MagicMock(session_id="b"))
        assert app.focused_session_index == 1
        [widget] = [w for w in app._get_widgets_in_session_order() if w.session.id == "b"]
        app._sync_tmux_window.assert_called_once_with(widget)

    def test_unknown_row_is_ignored(self):
        app, handler = self._app(focused=1)
        handler(app, MagicMock(session_id="zz"))
        assert app.focused_session_index == 1
        app._sync_tmux_window.assert_not_called()


@pytest.mark.asyncio
class TestClickInTUI:

    async def test_click_switches_the_tmux_pane_to_that_agent(self, tui):  # noqa: F811 (fixture)
        from overcode.tui_widgets import SessionSummary
        synced = []
        tui._sync_tmux_window = lambda widget=None: synced.append(widget.session.id)
        tui._fix_window_size_if_needed = lambda widget: None
        async with tui.run_test(size=(200, 40)) as pilot:
            await _ready(pilot)
            rows = tui._get_widgets_in_session_order()
            assert tui.focused_session_index == 0
            target = rows[2]
            await pilot.click(target, offset=(3, 0))
            await pilot.pause()
            assert tui.focused_session_index == 2
            assert tui.focused is target
            assert synced[-1] == target.session.id
            # j/k now continue from the clicked row
            await pilot.press("k")
            await pilot.pause()
            assert tui.focused_session_index == 1
            assert isinstance(tui.focused, SessionSummary)
