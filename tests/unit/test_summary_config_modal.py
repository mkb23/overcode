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

    def test_column_breakpoint_balances_rows(self):
        """Two-column layout breakpoint should fall near the middle (#443)."""
        modal = SummaryConfigModal()
        total = len(modal._flat_rows)
        breakpoint = modal._column_breakpoint
        # Breakpoint must leave both columns non-empty and at most one group apart.
        assert 0 < breakpoint < total
        left = breakpoint
        right = total - breakpoint
        # Columns should be roughly balanced — the larger side shouldn't be
        # more than ~2x the smaller side (groups vary in size).
        assert max(left, right) <= 2 * min(left, right)

    def test_column_breakpoint_on_group_boundary(self):
        """Breakpoint must fall immediately before a 'group' row (#443).

        Otherwise a group header would appear in one column while its
        columns appear in the other.
        """
        modal = SummaryConfigModal()
        breakpoint = modal._column_breakpoint
        # Row 0 is always a group (Identity)
        # Row at breakpoint must also be a group header
        assert modal._flat_rows[breakpoint][0] == "group"

    def test_render_produces_two_column_layout(self):
        """render() should place one column's rows on each visible line (#443)."""
        modal = SummaryConfigModal()
        modal.level = "med"
        modal.overrides = {}
        rendered = modal.render()
        plain = rendered.plain
        lines = plain.split("\n")
        # Skip title + help line + blank = 3 header lines
        body_lines = [l for l in lines[3:] if l.strip()]
        # Body should be at most the size of the larger half of rows
        total = len(modal._flat_rows)
        breakpoint = modal._column_breakpoint
        max_col = max(breakpoint, total - breakpoint)
        assert len(body_lines) <= max_col
        # Body should be significantly shorter than total row count
        assert len(body_lines) < total

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


class _FakeApp:
    """Minimal app stand-in for testing live override plumbing (#449)."""
    def __init__(self):
        self._live_column_overrides = None
        self._column_widths_dirty = False
        self._recompute_calls = 0

    def query(self, _cls):
        return []

    def _recompute_cell_column_widths(self):
        self._recompute_calls += 1


class TestLiveOverridePlumbing:
    """#449 — modal publishes its in-flight overrides to the app."""

    def test_update_live_summaries_publishes_overrides(self):
        modal = SummaryConfigModal()
        app = _FakeApp()
        modal._app_ref = app
        modal.overrides = {"uptime": False, "cost": True}

        modal._update_live_summaries()

        assert app._live_column_overrides == {"uptime": False, "cost": True}
        # Must be a copy, not a live alias — mutating modal state shouldn't
        # silently change what the app has already read.
        assert app._live_column_overrides is not modal.overrides
        assert app._column_widths_dirty is True
        assert app._recompute_calls == 1

    def test_update_live_summaries_republishes_on_each_toggle(self):
        modal = SummaryConfigModal()
        app = _FakeApp()
        modal._app_ref = app

        modal.overrides = {"uptime": True}
        modal._update_live_summaries()
        assert app._live_column_overrides == {"uptime": True}

        modal.overrides = {"uptime": False, "cost": True}
        modal._update_live_summaries()
        assert app._live_column_overrides == {"uptime": False, "cost": True}

    def test_cancel_restore_publishes_original(self):
        """Cancel restores modal.overrides to original — and the next live
        update should propagate that restoration to the app."""
        modal = SummaryConfigModal()
        app = _FakeApp()
        modal._app_ref = app
        modal.show("med", {"uptime": True}, app)
        # Simulate edits then cancel
        modal.overrides["cost"] = True
        modal._update_live_summaries()
        assert app._live_column_overrides == {"uptime": True, "cost": True}
        modal._cancel()
        # _cancel triggers one last _update_live_summaries with the restored
        # overrides — app sees the original config again.
        assert app._live_column_overrides == {"uptime": True}


class TestCurrentColumnOverrides:
    """#449 — the helper both the header path and the width path read from."""

    def _bare_app(self):
        """Construct a SupervisorTUI without running __init__ (which needs
        Textual's full init). We only exercise the pure-Python helper."""
        from overcode.tui import SupervisorTUI
        app = SupervisorTUI.__new__(SupervisorTUI)
        return app

    def test_returns_prefs_when_no_live_overrides(self):
        from unittest.mock import MagicMock
        app = self._bare_app()
        app._live_column_overrides = None
        app._prefs = MagicMock()
        app._prefs.column_config = {"med": {"uptime": False}}
        assert app._current_column_overrides("med") == {"uptime": False}

    def test_returns_empty_when_level_missing_from_prefs(self):
        from unittest.mock import MagicMock
        app = self._bare_app()
        app._live_column_overrides = None
        app._prefs = MagicMock()
        app._prefs.column_config = {}
        assert app._current_column_overrides("high") == {}

    def test_live_overrides_take_precedence(self):
        """Even when prefs have a config for the level, the live dict wins."""
        from unittest.mock import MagicMock
        app = self._bare_app()
        app._live_column_overrides = {"cost": True}
        app._prefs = MagicMock()
        app._prefs.column_config = {"med": {"uptime": False}}
        # Must be the live dict — not merged with prefs. The modal owns the
        # complete in-flight config while open.
        assert app._current_column_overrides("med") == {"cost": True}

    def test_empty_live_overrides_still_wins(self):
        """Empty-but-present live overrides (user cleared everything via `r`)
        must not fall back to prefs."""
        from unittest.mock import MagicMock
        app = self._bare_app()
        app._live_column_overrides = {}
        app._prefs = MagicMock()
        app._prefs.column_config = {"med": {"uptime": False}}
        assert app._current_column_overrides("med") == {}


class TestTUIHeaderSync:
    """#449 — the TUI header path reads live overrides when present."""

    def test_update_column_headers_uses_live_overrides(self, monkeypatch):
        """End-to-end: when _live_column_overrides is set, the header rendered
        by _update_column_headers reflects those overrides rather than prefs."""
        from unittest.mock import MagicMock
        from overcode.tui import SupervisorTUI

        captured = {}

        def fake_render_header_cells(column_filter, column_widths):
            from overcode.summary_columns import SUMMARY_COLUMNS
            captured["visible"] = [c.id for c in SUMMARY_COLUMNS if column_filter(c)]
            from rich.text import Text
            return Text("HEADER")

        # Patch where tui.py imports it from — tui.py does a local import
        # of render_header_cells inside _update_column_headers.
        import overcode.summary_columns as sc_mod
        monkeypatch.setattr(sc_mod, "render_header_cells", fake_render_header_cells)

        # Subclass shadows DOMNode.id (property), so reactive.__get__'s
        # `hasattr(obj, "id")` guard passes without touching Textual init.
        class _TestTUI(SupervisorTUI):
            id = "test-app"

        app = _TestTUI.__new__(_TestTUI)
        # tui_mode is a Textual reactive — bypass the descriptor's setter by
        # writing directly to the internal storage slot.
        app._reactive_tui_mode = "list"

        app._prefs = MagicMock()
        app._prefs.show_column_headers = True
        app._prefs.column_config = {"med": {}}  # prefs: no overrides at med
        app.SUMMARY_LEVELS = ["low", "med", "high", "full"]
        app.summary_level_index = 1
        app.column_widths = []

        header_widget = MagicMock()
        header_widget.display = True

        def fake_query_one(sel, cls):
            assert sel == "#column-headers"
            return header_widget
        app.query_one = fake_query_one

        # Baseline: no live overrides → header follows prefs (defaults at med).
        app._live_column_overrides = None
        app._update_column_headers()
        baseline = set(captured["visible"])
        assert "uptime" in baseline  # uptime is MED_PLUS → visible at med

        # Live override hides uptime — header MUST reflect it, even though
        # prefs still say "no overrides".
        app._live_column_overrides = {"uptime": False}
        app._update_column_headers()
        with_override = set(captured["visible"])
        assert "uptime" not in with_override
        assert with_override != baseline
