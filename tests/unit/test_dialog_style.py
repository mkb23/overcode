"""Tests for the shared dialog look (#488) and the column guide (#490)."""

import pytest
from rich.cells import cell_len

from overcode.tui_widgets import dialog_style as ds
from overcode.tui_widgets.modal_base import ModalBase
from tests.unit.test_command_palette import _ready, tui  # noqa: F401 (fixture)


def _dialog_classes():
    import overcode.tui_widgets  # noqa: F401 — registers every dialog
    from overcode.tui_widgets import CommandPalette
    seen, todo = [], list(ModalBase.__subclasses__())
    while todo:
        cls = todo.pop()
        seen.append(cls)
        todo.extend(cls.__subclasses__())
    return [c for c in seen if c is not CommandPalette]


class TestHelpers:

    def test_options_lights_current(self):
        t = ds.options(("off", "on"), "on")
        assert t.plain == "off  on"
        assert ds.STATE_ON in str(t.spans[-1].style)

    def test_options_collapse_when_too_wide(self):
        opts = tuple(f"choice{i}" for i in range(10))
        t = ds.options(opts, "choice3", width=30)
        assert t.plain.startswith("choice3  4 of 10")

    def test_rows_fill_width(self):
        for sel in (True, False):
            line = ds.finish(ds.item(sel).append("x" * 200), 40)
            assert cell_len(line.plain) == 40
        assert cell_len(ds.section("Git", 40).plain) == 40

    def test_text_value_cursor(self):
        assert ds.text_value("", None).plain == "(none)"
        assert ds.text_value("abc", 1).plain == "abc"
        assert ds.text_value("abc", 3).plain == "abc "


class TestEveryDialog:

    @pytest.mark.parametrize("cls", _dialog_classes(), ids=lambda c: c.__name__)
    def test_has_title_and_key_hints(self, cls):
        modal = cls()
        modal.update_frame()
        assert modal.border_title
        assert "esc" in (modal.border_subtitle or "")

    @pytest.mark.parametrize("cls", _dialog_classes(), ids=lambda c: c.__name__)
    def test_rows_fit_inner_width(self, cls):
        modal = cls()
        for line in modal.render().plain.split("\n"):
            assert cell_len(line) <= modal.inner_width, (cls.__name__, line)


@pytest.mark.asyncio
class TestInTUI:

    async def test_column_guide_samples_focused_agent(self, tui):  # noqa: F811
        from overcode.tui_widgets import SummaryConfigModal
        async with tui.run_test(size=(140, 44)) as pilot:
            await _ready(pilot)
            tui.summary_level_index = 1
            focused = tui.focused.session.name
            tui.action_open_column_config()
            await pilot.pause()
            modal = tui.query_one("#summary-config-modal", SummaryConfigModal)
            assert modal.has_class("visible")
            assert modal._sample_agent == focused
            assert modal._samples["agent_name"].plain.endswith(focused)
            assert f"for {focused}" in modal.render().plain
            # Sits below the first agents so toggles can be watched live
            first_row = tui.query_one("#sessions-container").region.y
            assert modal.region.y >= first_row + modal._KEEP_AGENTS
            assert modal.region.bottom <= tui.size.height - 1

    async def test_dialog_recentres_on_resize(self, tui):  # noqa: F811
        from overcode.tui_widgets import TmuxConfigModal
        async with tui.run_test(size=(140, 44)) as pilot:
            await _ready(pilot)
            tui.action_open_tmux_config()
            await pilot.pause()
            modal = tui.query_one("#tmux-config-modal", TmuxConfigModal)
            assert modal.region.width == modal.WIDTH
            assert abs(modal.region.x - (140 - modal.WIDTH) // 2) <= 1
            await pilot.resize_terminal(50, 30)
            await pilot.pause()
            assert modal.region.width == 48
            assert modal.region.right <= 50
