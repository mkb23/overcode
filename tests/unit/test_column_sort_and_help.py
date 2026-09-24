"""Column header tooltips and help (#477), sort by any column (#487)."""

import pytest
from rich.cells import cell_len

from overcode.summary_columns import (
    COLUMN_HELP, COLUMN_SORT, COLUMNS_BY_ID, SUMMARY_COLUMNS, column_at, render_header_cells,
)
from tests.unit.test_command_palette import _palette, _ready, tui  # noqa: F401 (fixture)


class TestRegistry:

    def test_every_tui_column_is_described(self):
        missing = [c.id for c in SUMMARY_COLUMNS if not c.cli_only and not c.description]
        assert missing == []

    def test_help_and_sort_tables_name_real_columns(self):
        assert set(COLUMN_HELP) - set(COLUMNS_BY_ID) == set()
        assert set(COLUMN_SORT) - set(COLUMNS_BY_ID) == set()

    def test_presets_are_sortable_columns(self):
        from overcode.tui_logic import PRESET_FOR_COLUMN
        assert all(COLUMNS_BY_ID[c].sort_key is not None for c in PRESET_FOR_COLUMN)


class TestColumnAt:

    def test_hits_by_cumulative_width(self):
        ids, widths = ["a", "b", "c"], [3, 0, 4]
        assert [column_at(x, ids, widths) for x in range(8)] == ["a"] * 3 + ["c"] * 4 + [None]

    def test_negative_is_nothing(self):
        assert column_at(-1, ["a"], [3]) is None


class TestHeaderArrow:

    @staticmethod
    def _only(*ids):
        return lambda c: c.id in ids

    def test_sorted_column_gets_arrow_within_its_width(self):
        line = render_header_cells(self._only("uptime", "cpu_pct"), [6, 6],
                                   sort_column="cpu_pct", sort_descending=True)
        assert line.plain == " UPT   CPU▼ "
        assert cell_len(line.plain) == 12

    def test_arrow_survives_truncation(self):
        line = render_header_cells(self._only("cpu_pct"), [3], sort_column="cpu_pct")
        assert line.plain == " C▲"

    def test_unsorted_header_unchanged(self):
        line = render_header_cells(self._only("cpu_pct"), [6])
        assert line.plain == " CPU  "


async def _until(pilot, cond, timeout=3.0):
    """Pause until cond() holds — clicks land a frame or two late under load."""
    for _ in range(int(timeout / 0.05)):
        if cond():
            return
        await pilot.pause(0.05)
    assert cond()


def _header(app):
    from overcode.tui_widgets import ColumnHeader
    return app.query_one("#column-headers", ColumnHeader)


def _x_of(header, col_id):
    """Content x of the first cell of `col_id` in the header."""
    x = 0
    for cid, w in zip(header.column_ids, header.column_widths):
        if cid == col_id:
            return x
        x += w
    raise AssertionError(f"{col_id} not in header")


@pytest.mark.asyncio
class TestHeaderInTUI:

    async def test_header_knows_its_columns(self, tui):
        async with tui.run_test(size=(200, 40)) as pilot:
            await _ready(pilot)
            header = _header(tui)
            assert "agent_name" in header.column_ids
            assert len(header.column_ids) == len(header.column_widths) == len(tui.column_widths)
            assert header.sort_column == "agent_name" and not header.sort_descending

    async def test_tooltip_describes_the_column(self, tui):
        async with tui.run_test(size=(200, 40)) as pilot:
            await _ready(pilot)
            header = _header(tui)
            tip = header.tooltip_for("agent_name").plain
            assert "Agent Name" in tip and "Sorted ▲" in tip
            tip = header.tooltip_for("uptime").plain
            assert COLUMN_HELP["uptime"] in tip and "Click to sort" in tip
            assert "Not sortable" in header.tooltip_for("allowed_tools").plain

    async def test_hover_sets_tooltip(self, tui):
        async with tui.run_test(size=(200, 40)) as pilot:
            await _ready(pilot)
            header = _header(tui)
            # +1 for the header's left padding
            await pilot.hover("#column-headers", offset=(_x_of(header, "uptime") + 2, 0))
            assert header.tooltip is not None and "Uptime" in header.tooltip.plain

    async def test_click_sorts_and_click_again_reverses(self, tui):
        async with tui.run_test(size=(200, 40)) as pilot:
            await _ready(pilot)
            header = _header(tui)
            x = _x_of(header, "uptime") + 2
            await pilot.click("#column-headers", offset=(x, 0))
            await _until(pilot, lambda: tui._prefs.sort_mode == "col:uptime")
            assert not tui._prefs.sort_reversed
            assert header.sort_column == "uptime" and header.sort_descending
            await pilot.pause(0.6)  # past the double-click window
            await pilot.click("#column-headers", offset=(x, 0))
            await _until(pilot, lambda: tui._prefs.sort_reversed)
            assert not header.sort_descending

    async def test_click_name_selects_alphabetical_preset(self, tui):
        async with tui.run_test(size=(200, 40)) as pilot:
            await _ready(pilot)
            tui.set_sort_mode("by_tree")
            await pilot.pause()
            header = _header(tui)
            await pilot.click("#column-headers", offset=(_x_of(header, "agent_name") + 2, 0))
            await pilot.pause()
            assert tui._prefs.sort_mode == "alphabetical"


@pytest.mark.asyncio
class TestSortPicker:

    async def test_S_opens_sort_choices_with_current_lit(self, tui):
        async with tui.run_test(size=(120, 40)) as pilot:
            await _ready(pilot)
            await pilot.press("S")
            palette = _palette(tui)
            assert palette.has_class("visible") and palette.mode == "sort"
            active = [r.sort for r in palette._rows if r.sort and r.sort.active]
            assert [c.mode for c in active] == ["alphabetical"]
            assert palette.selected_row.sort.mode == "alphabetical"  # starts on current
            assert palette._rows[-1].sort.mode == "by_tree"

    async def test_filter_and_choose(self, tui):
        async with tui.run_test(size=(120, 40)) as pilot:
            await _ready(pilot)
            await pilot.press("S", *"cpu", "enter")
            await pilot.pause()
            assert tui._prefs.sort_mode == "col:cpu_pct"
            assert not _palette(tui).has_class("visible")

    async def test_tab_on_current_reverses_and_stays_open(self, tui):
        async with tui.run_test(size=(120, 40)) as pilot:
            await _ready(pilot)
            await pilot.press("S", *"uptime", "tab")
            await pilot.pause()
            palette = _palette(tui)
            assert tui._prefs.sort_mode == "col:uptime" and palette.has_class("visible")
            assert palette.selected_row.sort.active and palette.selected_row.sort.descending
            await pilot.press("tab")
            await pilot.pause()
            assert tui._prefs.sort_reversed
            assert not palette.selected_row.sort.descending

    async def test_reverse_sort_command(self, tui):
        async with tui.run_test(size=(120, 40)) as pilot:
            await _ready(pilot)
            await pilot.press("slash", *"reverse sort", "enter")
            await pilot.pause()
            assert tui._prefs.sort_mode == "alphabetical" and tui._prefs.sort_reversed
            names = [w.session.name for w in tui._get_widgets_in_session_order()]
            assert names == ["charlie", "bravo", "alpha"]

    async def test_sort_command_switches_list_in_place(self, tui):
        async with tui.run_test(size=(120, 40)) as pilot:
            await _ready(pilot)
            await pilot.press("slash", *"sort agents", "enter")
            assert _palette(tui).mode == "sort"


class TestConfigModalHelp:

    def test_cursor_column_description_shown(self):
        from overcode.tui_widgets.summary_config_modal import SummaryConfigModal
        modal = SummaryConfigModal()
        modal.level = "full"
        idx = modal._flat_rows.index(("column", "cpu_pct"))
        modal.cursor_pos = idx
        lines = modal._help_lines()
        assert len(lines) == modal._HELP_LINES
        assert lines[0].startswith("CPU: CPU used")

    def test_group_row_shows_blank_lines(self):
        from overcode.tui_widgets.summary_config_modal import SummaryConfigModal
        modal = SummaryConfigModal()
        modal.cursor_pos = 0  # first row is a group
        assert modal._help_lines() == [""] * modal._HELP_LINES
