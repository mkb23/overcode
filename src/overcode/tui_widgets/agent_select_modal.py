"""
Agent selection modal for TUI.

Keyboard-navigable list to pick a Claude agent persona before launching.
"""

from typing import List, Optional, Any

from textual.message import Message
from textual import events
from rich.text import Text

from .modal_base import ModalBase


class AgentSelectModal(ModalBase):
    """Modal dialog for selecting a Claude agent.

    Navigate with j/k or up/down arrows.
    Press Enter to select, q/Esc to skip (default Claude).
    """

    class AgentSelected(Message):
        """Message sent when an agent is selected."""

        def __init__(self, agent_name: Optional[str]) -> None:
            super().__init__()
            self.agent_name = agent_name

    class AgentSelectSkipped(Message):
        """Message sent when agent selection is skipped."""
        pass

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._agents: List[str] = []

    def render(self) -> Text:
        text = Text()
        text.append("Select Agent\n", style="bold cyan")
        text.append("j/k:move  enter:select  q:skip\n\n", style="dim")

        # First option is always "(none) — default Claude"
        options = ["(none) \u2014 default Claude"] + self._agents

        for i, label in enumerate(options):
            is_selected = i == self.selected_index

            if is_selected:
                text.append("> ", style="bold cyan")
            else:
                text.append("  ", style="")

            style = "bold" if is_selected else ""
            text.append(f"{label}\n", style=style)

        return text

    def on_key(self, event: events.Key) -> None:
        total = 1 + len(self._agents)  # (none) + agent names
        if self._navigate(event, total):
            return

        key = event.key
        if key == "enter":
            self._select()
            event.stop()
        elif key in ("escape", "q", "Q"):
            self._skip()
            event.stop()

    def _select(self) -> None:
        if self.selected_index == 0:
            self.post_message(self.AgentSelected(None))
        else:
            agent_name = self._agents[self.selected_index - 1]
            self.post_message(self.AgentSelected(agent_name))
        self._hide()

    def _skip(self) -> None:
        self.post_message(self.AgentSelectSkipped())
        self._hide()

    def show(self, agents: List[str], app_ref: Optional[Any] = None) -> None:
        """Display the modal with available agents."""
        self._agents = list(agents)
        self._save_focus(app_ref)
        self._show()
