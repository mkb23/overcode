"""
Instruction history modal for TUI.

Shows the last N instructions sent to any agent, allowing the user
to select one and reinject it into the currently focused agent (#376).
"""

import time
from dataclasses import dataclass, field
from typing import List, Optional, Any

from textual.message import Message
from textual import events
from rich.text import Text

from . import dialog_style as ds
from .modal_base import ModalBase

MAX_HISTORY = 10


@dataclass
class HistoryEntry:
    """A single instruction sent to an agent."""

    text: str
    agent_name: str
    timestamp: float = field(default_factory=time.time)

    @property
    def age(self) -> str:
        """Human-readable age string."""
        delta = int(time.time() - self.timestamp)
        if delta < 60:
            return f"{delta}s ago"
        elif delta < 3600:
            return f"{delta // 60}m ago"
        else:
            return f"{delta // 3600}h ago"


class InstructionHistoryModal(ModalBase):
    """Modal showing recent instructions sent to agents.

    Navigate with j/k or up/down arrows.
    Press Enter to reinject the selected instruction to the focused agent.
    Press q/Esc to dismiss.
    """

    class ReinjectRequested(Message):
        """Message sent when the user selects an instruction to reinject."""

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class Cancelled(Message):
        """Message sent when the modal is dismissed."""
        pass

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._entries: List[HistoryEntry] = []

    TITLE = "Instruction history"
    WIDTH = 96
    _TIP_LINES = 2

    def hints(self) -> str:
        return ds.hints(("↵", "send to focused agent"), ("esc", "close"))

    def render(self) -> Text:
        w = self.inner_width
        text = Text(no_wrap=True, overflow="crop")

        if not self._entries:
            text.append_text(ds.finish(Text("  No instructions sent yet", style=f"italic {ds.MUTED}"), w))
            return text

        name_w = min(20, max(len(e.agent_name) for e in self._entries) + 2)
        for i, entry in enumerate(self._entries):
            sel = i == self.selected_index
            line = ds.item(sel)
            line.append_text(ds.fit(Text(entry.agent_name, style=f"bold {ds.ACCENT}" if sel else ds.ACCENT), name_w))
            line.append(f"{entry.age:>8}  ", style=ds.MUTED)
            line.append(entry.text.replace("\n", " ↵ "), style="bold" if sel else ds.TEXT)
            text.append_text(ds.finish(line, w))
            text.append("\n")

        # The whole of the highlighted instruction, which the row cuts short
        text.append_text(ds.rule(w))
        full = self._entries[self.selected_index].text.replace("\n", " ↵ ")
        for line in ds.wrap(full, w, self._TIP_LINES, style=ds.MUTED):
            text.append("\n")
            text.append_text(line)
        return text

    def on_key(self, event: events.Key) -> None:
        total = len(self._entries)

        if not total:
            if event.key in ("escape", "q", "Q"):
                self._dismiss()
                event.stop()
            return

        if self._navigate(event, total):
            return

        key = event.key
        if key == "enter":
            entry = self._entries[self.selected_index]
            self.post_message(self.ReinjectRequested(entry.text))
            self._dismiss()
            event.stop()
        elif key in ("escape", "q", "Q"):
            self._dismiss()
            event.stop()

    def _dismiss(self) -> None:
        self.post_message(self.Cancelled())
        self._hide()

    def show(self, entries: List[HistoryEntry], app_ref: Optional[Any] = None) -> None:
        """Display the modal with instruction history."""
        self._entries = list(entries)
        self._save_focus(app_ref)
        self._show()
