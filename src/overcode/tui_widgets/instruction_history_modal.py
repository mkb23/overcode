"""
Instruction history modal for TUI.

Shows the last N instructions sent to any agent, allowing the user
to select one and reinject it into the currently focused agent (#376).
"""

import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional, Any

from textual.widgets import Static
from textual.message import Message
from textual import events
from rich.text import Text

logger = logging.getLogger(__name__)

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
        oneline = self.text.replace("\n", " ↵ ")
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


class InstructionHistoryModal(Static, can_focus=True):
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
        self.selected_index: int = 0
        self._previous_focus: Optional[Any] = None

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
        key = event.key
        total = len(self._entries)

        if not total:
            if key in ("escape", "q", "Q"):
                self._dismiss()
                event.stop()
            return

        if key in ("j", "down"):
            self.selected_index = (self.selected_index + 1) % total
            self.refresh()
            event.stop()

        elif key in ("k", "up"):
            self.selected_index = (self.selected_index - 1) % total
            self.refresh()
            event.stop()

        elif key == "enter":
            entry = self._entries[self.selected_index]
            self.post_message(self.ReinjectRequested(entry.text))
            self._dismiss()
            event.stop()

        elif key in ("escape", "q", "Q"):
            self._dismiss()
            event.stop()

    def _dismiss(self) -> None:
        self.remove_class("visible")
        self.post_message(self.Cancelled())
        if self._previous_focus is not None:
            try:
                self._previous_focus.focus()
            except (AttributeError, Exception) as e:
                logger.debug("Failed to restore focus: %s", e)
        self._previous_focus = None

    def show(self, entries: List[HistoryEntry], app_ref: Optional[Any] = None) -> None:
        """Display the modal with instruction history.

        Args:
            entries: List of HistoryEntry objects (most recent first).
            app_ref: Reference to the app for focus management.
        """
        self._entries = list(entries)
        self._previous_focus = None
        if app_ref:
            try:
                self._previous_focus = app_ref.focused
            except (AttributeError, Exception) as e:
                logger.debug("Failed to save focus: %s", e)
        self.selected_index = 0
        self.refresh()
        self.add_class("visible")
        try:
            self.focus()
        except (AttributeError, Exception) as e:
            logger.debug("Failed to focus modal: %s", e)
