"""Tests for the sister selection modal widget."""

import pytest
from unittest.mock import MagicMock

from overcode.tui_widgets.sister_selection_modal import SisterSelectionModal


class FakeEvent:
    """Minimal event for testing on_key."""
    def __init__(self, key):
        self.key = key
        self._stopped = False
    def stop(self):
        self._stopped = True


@pytest.fixture
def modal():
    """Create a SisterSelectionModal without mounting."""
    m = SisterSelectionModal.__new__(SisterSelectionModal)
    m._sisters = []
    m._disabled = set()
    m._original_disabled = set()
    m.selected_index = 0
    m._app_ref = None
    m._previous_focus = None
    # Stub Textual methods
    m.refresh = MagicMock()
    m.add_class = MagicMock()
    m.remove_class = MagicMock()
    m.focus = MagicMock()
    m.post_message = MagicMock()
    return m


SISTERS = [
    {"name": "macbook", "url": "http://macbook:15337", "reachable": True, "daemon_running": True, "api_key": "key1"},
    {"name": "desktop", "url": "http://desktop:15337", "reachable": True, "daemon_running": False, "api_key": "key2"},
    {"name": "server", "url": "http://server:15337", "reachable": False, "daemon_running": False, "api_key": ""},
]


class TestSisterSelectionModal:
    def test_show_populates_sisters(self, modal):
        modal.show(SISTERS, {"desktop"})
        assert len(modal._sisters) == 3
        assert modal._disabled == {"desktop"}
        assert modal._original_disabled == {"desktop"}
        assert modal.selected_index == 0
        modal.add_class.assert_called_with("visible")

    def test_navigate_down(self, modal):
        modal.show(SISTERS, set())
        modal.on_key(FakeEvent("j"))
        assert modal.selected_index == 1
        modal.on_key(FakeEvent("j"))
        assert modal.selected_index == 2
        # Wraps around
        modal.on_key(FakeEvent("j"))
        assert modal.selected_index == 0

    def test_navigate_up(self, modal):
        modal.show(SISTERS, set())
        # Wraps to last
        modal.on_key(FakeEvent("k"))
        assert modal.selected_index == 2
        modal.on_key(FakeEvent("k"))
        assert modal.selected_index == 1

    def test_toggle_disables_and_enables(self, modal):
        modal.show(SISTERS, set())
        # All enabled initially
        assert "macbook" not in modal._disabled

        # Toggle first — should disable
        modal.on_key(FakeEvent("space"))
        assert "macbook" in modal._disabled

        # Toggle again — should re-enable
        modal.on_key(FakeEvent("space"))
        assert "macbook" not in modal._disabled

    def test_toggle_specific_sister(self, modal):
        modal.show(SISTERS, set())
        # Navigate to "desktop" (index 1) and toggle
        modal.on_key(FakeEvent("j"))
        modal.on_key(FakeEvent("space"))
        assert "desktop" in modal._disabled
        assert "macbook" not in modal._disabled

    def test_apply_sends_message(self, modal):
        modal.show(SISTERS, set())
        modal.on_key(FakeEvent("space"))  # disable macbook
        modal.on_key(FakeEvent("a"))      # apply
        modal.post_message.assert_called()
        msg = modal.post_message.call_args[0][0]
        assert isinstance(msg, SisterSelectionModal.SelectionChanged)
        assert msg.disabled_sisters == {"macbook"}

    def test_cancel_restores_original(self, modal):
        modal.show(SISTERS, {"server"})
        # Change something
        modal.on_key(FakeEvent("space"))  # toggle macbook
        assert "macbook" in modal._disabled
        # Cancel
        modal.on_key(FakeEvent("q"))
        # Original should be restored
        assert modal._disabled == {"server"}
        modal.post_message.assert_called()
        msg = modal.post_message.call_args[0][0]
        assert isinstance(msg, SisterSelectionModal.Cancelled)

    def test_render_shows_sisters(self, modal):
        modal.show(SISTERS, {"desktop"})
        text = modal.render()
        plain = text.plain
        assert "macbook" in plain
        assert "desktop" in plain
        assert "server" in plain
        assert "[x]" in plain  # enabled sisters
        assert "[ ]" in plain  # disabled sister

    def test_render_empty_sisters(self, modal):
        modal.show([], set())
        text = modal.render()
        plain = text.plain
        assert "No sisters configured" in plain

    def test_escape_cancels(self, modal):
        modal.show(SISTERS, set())
        modal.on_key(FakeEvent("escape"))
        modal.post_message.assert_called()
        msg = modal.post_message.call_args[0][0]
        assert isinstance(msg, SisterSelectionModal.Cancelled)

    def test_enter_toggles(self, modal):
        modal.show(SISTERS, set())
        modal.on_key(FakeEvent("enter"))
        assert "macbook" in modal._disabled

    def test_restart_daemon_sends_message(self, modal):
        modal.show(SISTERS, set())
        # Navigate to desktop (index 1, reachable but daemon down)
        modal.on_key(FakeEvent("j"))
        modal.on_key(FakeEvent("r"))
        modal.post_message.assert_called()
        msg = modal.post_message.call_args[0][0]
        assert isinstance(msg, SisterSelectionModal.RestartDaemon)
        assert msg.sister_name == "desktop"
        assert msg.api_key == "key2"

    def test_restart_daemon_ignored_when_unreachable(self, modal):
        modal.show(SISTERS, set())
        # Navigate to server (index 2, unreachable)
        modal.on_key(FakeEvent("j"))
        modal.on_key(FakeEvent("j"))
        modal.on_key(FakeEvent("r"))
        # Should not post a RestartDaemon message
        calls = [c[0][0] for c in modal.post_message.call_args_list]
        assert not any(isinstance(c, SisterSelectionModal.RestartDaemon) for c in calls)

    def test_render_shows_daemon_status(self, modal):
        modal.show(SISTERS, set())
        text = modal.render()
        plain = text.plain
        assert "daemon:" in plain
        assert "unreachable" in plain  # server

    def test_empty_sisters_only_allows_cancel(self, modal):
        modal.show([], set())
        # j/k should do nothing (no crash)
        modal.on_key(FakeEvent("j"))
        modal.on_key(FakeEvent("k"))
        # q should cancel
        modal.on_key(FakeEvent("q"))
        modal.post_message.assert_called()
