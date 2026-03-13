"""Tests for the instruction history modal widget (#376)."""

import pytest
import time
from unittest.mock import MagicMock

from overcode.tui_widgets.instruction_history_modal import (
    InstructionHistoryModal,
    HistoryEntry,
    MAX_HISTORY,
)


class FakeEvent:
    """Minimal event for testing on_key."""
    def __init__(self, key):
        self.key = key
        self._stopped = False
    def stop(self):
        self._stopped = True


@pytest.fixture
def modal():
    """Create an InstructionHistoryModal without mounting."""
    m = InstructionHistoryModal.__new__(InstructionHistoryModal)
    m._entries = []
    m.selected_index = 0
    m._previous_focus = None
    # Stub Textual methods
    m.refresh = MagicMock()
    m.add_class = MagicMock()
    m.remove_class = MagicMock()
    m.focus = MagicMock()
    m.post_message = MagicMock()
    return m


def make_entries(n):
    """Create n sample history entries."""
    return [
        HistoryEntry(text=f"instruction {i}", agent_name=f"agent-{i}")
        for i in range(n)
    ]


class TestHistoryEntry:
    """Tests for the HistoryEntry dataclass."""

    def test_preview_short_text(self):
        e = HistoryEntry(text="hello world", agent_name="a")
        assert e.preview == "hello world"

    def test_preview_multiline(self):
        e = HistoryEntry(text="line1\nline2\nline3", agent_name="a")
        assert "↵" in e.preview
        assert "\n" not in e.preview

    def test_preview_truncates_long_text(self):
        e = HistoryEntry(text="x" * 100, agent_name="a")
        assert len(e.preview) <= 60
        assert e.preview.endswith("...")

    def test_age_seconds(self):
        e = HistoryEntry(text="t", agent_name="a", timestamp=time.time() - 30)
        assert "s ago" in e.age

    def test_age_minutes(self):
        e = HistoryEntry(text="t", agent_name="a", timestamp=time.time() - 120)
        assert "m ago" in e.age

    def test_age_hours(self):
        e = HistoryEntry(text="t", agent_name="a", timestamp=time.time() - 7200)
        assert "h ago" in e.age


class TestModalNavigation:
    """Tests for keyboard navigation."""

    def test_j_moves_down(self, modal):
        modal._entries = make_entries(3)
        modal.on_key(FakeEvent("j"))
        assert modal.selected_index == 1

    def test_k_moves_up_wraps(self, modal):
        modal._entries = make_entries(3)
        modal.on_key(FakeEvent("k"))
        assert modal.selected_index == 2

    def test_down_arrow_works(self, modal):
        modal._entries = make_entries(3)
        modal.on_key(FakeEvent("down"))
        assert modal.selected_index == 1

    def test_j_wraps_around(self, modal):
        modal._entries = make_entries(3)
        modal.selected_index = 2
        modal.on_key(FakeEvent("j"))
        assert modal.selected_index == 0


class TestModalActions:
    """Tests for enter/escape actions."""

    def test_enter_posts_reinject(self, modal):
        modal._entries = make_entries(3)
        modal.selected_index = 1
        modal.on_key(FakeEvent("enter"))
        # post_message is called twice: ReinjectRequested then Cancelled (from _dismiss)
        calls = [c[0][0] for c in modal.post_message.call_args_list]
        reinject_msgs = [m for m in calls if isinstance(m, InstructionHistoryModal.ReinjectRequested)]
        assert len(reinject_msgs) == 1
        assert reinject_msgs[0].text == "instruction 1"

    def test_escape_dismisses(self, modal):
        modal._entries = make_entries(3)
        modal.on_key(FakeEvent("escape"))
        modal.remove_class.assert_called_with("visible")

    def test_q_dismisses(self, modal):
        modal._entries = make_entries(3)
        modal.on_key(FakeEvent("q"))
        modal.remove_class.assert_called_with("visible")

    def test_escape_on_empty_works(self, modal):
        modal.on_key(FakeEvent("escape"))
        modal.remove_class.assert_called_with("visible")


class TestModalShow:
    """Tests for the show() method."""

    def test_show_sets_entries(self, modal):
        entries = make_entries(5)
        modal.show(entries)
        assert modal._entries == entries
        assert modal.selected_index == 0
        modal.add_class.assert_called_with("visible")

    def test_show_saves_focus(self, modal):
        app = MagicMock()
        app.focused = "some_widget"
        modal.show(make_entries(2), app_ref=app)
        assert modal._previous_focus == "some_widget"


class TestMaxHistory:
    def test_max_history_is_10(self):
        assert MAX_HISTORY == 10


class TestRender:
    """Tests for render output."""

    def test_render_empty(self, modal):
        text = modal.render()
        assert "no instructions sent yet" in text.plain

    def test_render_with_entries(self, modal):
        modal._entries = make_entries(2)
        text = modal.render()
        assert "agent-0" in text.plain
        assert "instruction 0" in text.plain
