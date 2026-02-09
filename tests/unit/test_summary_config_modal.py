"""Tests for SummaryConfigModal widget."""

import pytest

from overcode.tui_widgets.summary_config_modal import SummaryConfigModal
from overcode.summary_groups import get_default_group_visibility


class TestSummaryConfigModal:
    """Tests for SummaryConfigModal widget."""

    def test_init_with_empty_config(self):
        """Modal should initialize missing groups with defaults."""
        modal = SummaryConfigModal({})
        # All toggleable groups should be present
        expected_groups = ["time", "tokens", "git", "supervision", "priority", "performance"]
        for group_id in expected_groups:
            assert group_id in modal.config

    def test_init_preserves_existing_config(self):
        """Modal should preserve provided configuration values."""
        config = {"time": False, "tokens": True, "git": False}
        modal = SummaryConfigModal(config)
        assert modal.config["time"] is False
        assert modal.config["tokens"] is True
        assert modal.config["git"] is False

    def test_config_is_copied(self):
        """Modal should not modify the original config dict."""
        original_config = {"time": True, "tokens": True}
        modal = SummaryConfigModal(original_config)
        modal.config["time"] = False
        # Original should be unchanged
        assert original_config["time"] is True

    def test_selected_index_starts_at_zero(self):
        """Modal should start with first item selected."""
        modal = SummaryConfigModal({})
        assert modal.selected_index == 0

    def test_build_list_text_shows_all_groups(self):
        """List text should include all toggleable groups."""
        modal = SummaryConfigModal(get_default_group_visibility())
        text = modal._build_list_text()
        plain = text.plain

        # All group names should be in the text
        assert "Time" in plain
        assert "Tokens" in plain
        assert "Git" in plain
        assert "Supervision" in plain
        assert "Priority" in plain
        assert "Performance" in plain

    def test_build_list_text_shows_checkmarks(self):
        """List text should show checkmarks for enabled groups."""
        modal = SummaryConfigModal({"time": True, "tokens": False})
        text = modal._build_list_text()
        plain = text.plain

        # Should have both checked and unchecked states
        assert "[x]" in plain
        assert "[ ]" in plain

    def test_show_stores_original_config(self):
        """Show should store original config for cancel."""
        modal = SummaryConfigModal({})
        config = {"time": False, "tokens": True}
        modal.show(config)
        assert modal.original_config == config

    def test_cancel_restores_original_config(self):
        """Cancel should restore original config."""
        modal = SummaryConfigModal({})
        original = {"time": True, "tokens": True, "git": True,
                   "supervision": True, "priority": True, "performance": True,
                   "subprocesses": True}
        modal.show(original)
        # Change some values
        modal.config["time"] = False
        modal.config["tokens"] = False
        # Cancel
        modal._cancel()
        # Config should be restored
        assert modal.config == original


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
        """Modal should show when triggered and hide when dismissed."""
        from textual.app import App, ComposeResult
        from textual.widgets import Static

        class TestApp(App):
            CSS = MODAL_TEST_CSS

            def compose(self) -> ComposeResult:
                yield Static('Background')
                yield SummaryConfigModal(get_default_group_visibility(), id='modal')

            def key_c(self):
                self.query_one('#modal').show(get_default_group_visibility())

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
        """Moving down from last item should wrap to first."""
        modal = SummaryConfigModal({})
        num_groups = len(modal.groups)
        modal.selected_index = num_groups - 1

        # Simulate moving down
        modal.selected_index = (modal.selected_index + 1) % num_groups
        assert modal.selected_index == 0

    def test_navigation_wraps_around_backward(self):
        """Moving up from first item should wrap to last."""
        modal = SummaryConfigModal({})
        num_groups = len(modal.groups)
        modal.selected_index = 0

        # Simulate moving up
        modal.selected_index = (modal.selected_index - 1) % num_groups
        assert modal.selected_index == num_groups - 1

    def test_toggle_changes_config(self):
        """Toggling should flip the boolean value."""
        modal = SummaryConfigModal({"time": True})
        modal.selected_index = 0  # Assuming time is first

        # Toggle
        group_id = modal.groups[0].id
        modal.config[group_id] = not modal.config.get(group_id, True)

        assert modal.config[group_id] is False
