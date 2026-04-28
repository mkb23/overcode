"""Tests for the passthru key configuration modal (#446)."""

import pytest
from unittest.mock import MagicMock, patch

from overcode.tui_widgets.passthru_config_modal import PassthruConfigModal


class FakeEvent:
    def __init__(self, key):
        self.key = key
        self._stopped = False

    def stop(self):
        self._stopped = True


@pytest.fixture
def modal():
    m = PassthruConfigModal.__new__(PassthruConfigModal)
    m._slots = []
    m._working = {}
    m.selected_index = 0
    m._app_ref = None
    m._previous_focus = None
    m.refresh = MagicMock()
    m.add_class = MagicMock()
    m.remove_class = MagicMock()
    m.focus = MagicMock()
    m.post_message = MagicMock()
    return m


class TestPassthruConfigModal:
    def test_show_populates_defaults(self, modal):
        modal.show()
        # At least the 8 default slots
        slot_keys = [slot for slot, _ in modal._slots]
        for expected in ("enter", "escape", "1", "2", "3", "4", "5", "ctrl+o"):
            assert expected in slot_keys
        # Working copy starts as the active (default) set
        assert "enter" in modal._working
        assert modal._working["enter"] == "enter"

    def test_toggle_disables_then_reenables(self, modal):
        modal.show()
        slot, default_target = modal._slots[0]
        # Start enabled
        assert slot in modal._working
        modal.on_key(FakeEvent("space"))
        assert slot not in modal._working
        modal.on_key(FakeEvent("enter"))
        assert slot in modal._working
        assert modal._working[slot] == default_target

    def test_navigation(self, modal):
        modal.show()
        total = len(modal._slots)
        modal.on_key(FakeEvent("j"))
        assert modal.selected_index == 1
        modal.on_key(FakeEvent("k"))
        assert modal.selected_index == 0
        # Wraps up from 0
        modal.on_key(FakeEvent("k"))
        assert modal.selected_index == total - 1

    def test_cancel_does_not_save(self, modal):
        with patch("overcode.config.save_passthru_keys") as save_mock:
            modal.show()
            # disable first slot
            modal.on_key(FakeEvent("space"))
            modal.on_key(FakeEvent("escape"))
            save_mock.assert_not_called()
        # Cancelled message posted
        msgs = [call.args[0] for call in modal.post_message.call_args_list]
        assert any(isinstance(m, PassthruConfigModal.Cancelled) for m in msgs)

    def test_save_writes_to_config_and_posts_message(self, modal):
        with patch("overcode.config.save_passthru_keys") as save_mock:
            modal.show()
            # disable the first default slot
            slot, _ = modal._slots[0]
            modal.on_key(FakeEvent("space"))
            modal.on_key(FakeEvent("w"))
            save_mock.assert_called_once()
            saved_mapping = save_mock.call_args[0][0]
            assert slot not in saved_mapping
        msgs = [call.args[0] for call in modal.post_message.call_args_list]
        saved = [m for m in msgs if isinstance(m, PassthruConfigModal.Saved)]
        assert len(saved) == 1
        assert slot not in saved[0].mapping

    def test_show_surfaces_user_added_slots(self, modal):
        with patch(
            "overcode.config.get_passthru_keys",
            return_value={"enter": "enter", "f5": "f5"},
        ):
            modal.show()
        slot_keys = [slot for slot, _ in modal._slots]
        assert "f5" in slot_keys
        assert modal._working["f5"] == "f5"

    def test_escape_on_empty_slots_cancels(self, modal):
        modal._slots = []
        modal.on_key(FakeEvent("escape"))
        msgs = [call.args[0] for call in modal.post_message.call_args_list]
        assert any(isinstance(m, PassthruConfigModal.Cancelled) for m in msgs)
