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

from .modal_base import ModalBase

MAX_HISTORY = 10


@dataclass
class HistoryEntry:
    """A single instruction sent to an agent."""

    text: str
    agent_name: str
    timestamp: float = field(default_factory=time.time)

    @property
    def preview(self) -> str:
        """Single-line preview, truncated to 60 chars."""
        oneline = self.text.replace("\n", " \u21b5 ")
        if len(oneline) > 60:
            return oneline[:57] + "..."
        return oneline

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

    def render(self) -> Text:
        text = Text()
        text.append("Instruction History\n", style="bold cyan")
        text.append("j/k:move  enter:reinject  q:close\n\n", style="dim")

        if not self._entries:
            text.append("  (no instructions sent yet)\n", style="dim italic")
            return text

        for i, entry in enumerate(self._entries):
            is_selected = i == self.selected_index

            if is_selected:
                text.append("> ", style="bold cyan")
            else:
                text.append("  ", style="")

            # Agent name + age
            text.append(f"{entry.agent_name}", style="bold magenta" if is_selected else "magenta")
            text.append(f"  {entry.age}\n", style="dim")

            # Instruction preview (indented)
            indent = "    " if is_selected else "    "
            style = "bold" if is_selected else ""
            text.append(f"{indent}{entry.preview}\n", style=style)

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
