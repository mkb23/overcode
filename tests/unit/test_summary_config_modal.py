"""Tests for SummaryConfigModal widget."""

import pytest

from overcode.tui_widgets.summary_config_modal import SummaryConfigModal
from overcode.summary_groups import SUMMARY_GROUPS


class TestSummaryConfigModal:
    """Tests for SummaryConfigModal widget."""

    def test_init_defaults(self):
        """Modal should initialize with default state."""
        modal = SummaryConfigModal()
        assert modal.level == "med"
        assert modal.overrides == {}
        assert modal.cursor_pos == 0

    def test_col_effective_default(self):
        """Column effective visibility follows detail_levels when no overrides."""
        modal = SummaryConfigModal()
        modal.level = "low"
        modal.overrides = {}
        # status_symbol is in ALL — visible at low
        assert modal._col_effective("status_symbol") is True
        # uptime is in MED_PLUS — not visible at low
        assert modal._col_effective("uptime") is False

    def test_col_effective_with_override(self):
        """Override should change effective visibility."""
        modal = SummaryConfigModal()
        modal.level = "low"
        modal.overrides = {"uptime": True}
        assert modal._col_effective("uptime") is True

    def test_col_default(self):
        """Default should reflect detail_levels without overrides."""
        modal = SummaryConfigModal()
        modal.level = "med"
        assert modal._col_default("uptime") is True  # uptime is MED_PLUS
        assert modal._col_default("active_pct") is False  # active_pct is HIGH_PLUS

    def test_group_state_all(self):
        """Group state 'all' when all columns are visible."""
        modal = SummaryConfigModal()
        modal.level = "low"
        modal.overrides = {}
        # Identity group — all columns are ALL, so all visible at low
        assert modal._group_state("identity") == "all"

    def test_group_state_none(self):
        """Group state 'none' when all columns are hidden."""
        modal = SummaryConfigModal()
        modal.level = "low"
        # Override all time columns to False
        from overcode.summary_columns import SUMMARY_COLUMNS
        time_cols = [c for c in SUMMARY_COLUMNS if c.group == "time"]
        modal.overrides = {c.id: False for c in time_cols}
        assert modal._group_state("time") == "none"

    def test_flat_rows_includes_groups_and_columns(self):
        """All groups and their columns are shown from the start."""
        modal = SummaryConfigModal()
        group_rows = [r for r in modal._flat_rows if r[0] == "group"]
        col_rows = [r for r in modal._flat_rows if r[0] == "column"]
        assert len(group_rows) == len(SUMMARY_GROUPS)
        assert len(col_rows) > 0
        assert len(modal._flat_rows) == len(group_rows) + len(col_rows)

    def test_show_sets_level_and_overrides(self):
        """show() should set level and overrides."""
        modal = SummaryConfigModal()
        overrides = {"uptime": True, "cost": False}
        modal.show("high", overrides)
        assert modal.level == "high"
        assert modal.overrides == overrides
        assert modal.original_overrides == overrides

    def test_cancel_restores_overrides(self):
        """Cancel should restore original overrides."""
        modal = SummaryConfigModal()
        original = {"uptime": True}
        modal.show("med", original)
        # Modify overrides
        modal.overrides["uptime"] = False
        modal.overrides["cost"] = True
        # Cancel
        modal._cancel()
        assert modal.overrides == original

    def test_apply_cleans_default_overrides(self):
        """Apply should remove overrides that match defaults."""
        modal = SummaryConfigModal()
        modal.level = "med"
        # uptime default at med is True — setting override to True is redundant
        modal.overrides = {"uptime": True, "active_pct": True}  # active_pct default at med is False
        # Test the cleaning logic directly
        cleaned = {}
        for col_id, val in modal.overrides.items():
            if val != modal._col_default(col_id):
                cleaned[col_id] = val
        # uptime=True matches default at med, should be cleaned
        assert "uptime" not in cleaned
        # active_pct=True doesn't match default (False) at med, should remain
        assert cleaned["active_pct"] is True


MODAL_TEST_CSS = """
SummaryConfigModal {
    display: none;
    layer: above;
    offset: 30 8;
    width: auto;
    height: auto;
    background: #1a1a2e;
    border: thick #5588aa;
    padding: 1 2;
}
SummaryConfigModal.visible {
    display: block;
}
"""


class TestModalVisibility:
    """Tests for modal visibility."""

    @pytest.mark.asyncio
    async def test_modal_shows_and_hides(self):
        """Modal should show when show() is called and hide on cancel."""
        from textual.app import App, ComposeResult
        from textual.widgets import Static

        class TestApp(App):
            CSS = MODAL_TEST_CSS

            def compose(self) -> ComposeResult:
                yield Static('Background')
                yield SummaryConfigModal(id='modal')

            def key_c(self):
                self.query_one('#modal').show("med", {})

        app = TestApp()
        async with app.run_test(size=(80, 24)) as pilot:
            modal = app.query_one('#modal')

            # Initially hidden
            assert "visible" not in modal.classes

            # Show modal
            await pilot.press('c')
            await pilot.pause()
            assert "visible" in modal.classes

            # Hide modal with 'q'
            await pilot.press('q')
            await pilot.pause()
            assert "visible" not in modal.classes


class TestModalNavigation:
    """Tests for keyboard navigation in the modal."""

    def test_navigation_wraps_around_forward(self):
        """Moving cursor past last item should wrap to first."""
        modal = SummaryConfigModal()
        num_rows = len(modal._flat_rows)
        modal.cursor_pos = num_rows - 1

        modal.cursor_pos = (modal.cursor_pos + 1) % num_rows
        assert modal.cursor_pos == 0

    def test_navigation_wraps_around_backward(self):
        """Moving cursor before first item should wrap to last."""
        modal = SummaryConfigModal()
        num_rows = len(modal._flat_rows)
        modal.cursor_pos = 0

        modal.cursor_pos = (modal.cursor_pos - 1) % num_rows
        assert modal.cursor_pos == num_rows - 1

    def test_toggle_group_sets_all_columns(self):
        """Toggling a group should set overrides for all its columns."""
        modal = SummaryConfigModal()
        modal.level = "low"
        modal.overrides = {}
        # Find the time group row
        for i, (rt, rid) in enumerate(modal._flat_rows):
            if rt == "group" and rid == "time":
                modal.cursor_pos = i
                break
        # At low, all time columns are off (MED_PLUS/HIGH_PLUS).
        # Toggle should set all ON (because state is "none").
        modal._toggle_current()
        from overcode.summary_columns import SUMMARY_COLUMNS
        time_cols = [c for c in SUMMARY_COLUMNS if c.group == "time"]
        for col in time_cols:
            assert modal.overrides.get(col.id) is True
        # Toggle again — all are now on, so should turn all OFF
        modal._toggle_current()
        for col in time_cols:
            assert modal.overrides.get(col.id) is False
