"""
Agent selection modal for TUI.

Keyboard-navigable list to pick an agent persona before launching.
"""

from typing import List, Optional, Any

from textual.message import Message
from textual import events
from rich.text import Text

from . import dialog_style as ds
from .modal_base import ModalBase


class AgentSelectModal(ModalBase):
    """Modal dialog for selecting an agent persona.

    Navigate with j/k or up/down arrows.
    Press Enter to select, q/Esc to skip (the backend's default agent).
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

    TITLE = "Agent persona"
    WIDTH = 56

    def hints(self) -> str:
        return ds.hints(("↵", "select"), ("esc", "skip"))

    def render(self) -> Text:
        w = self.inner_width
        text = Text(no_wrap=True, overflow="crop")
        # First option is always "(none)": the backend's default agent
        options = [("(none)", "backend default")] + [(a, "") for a in self._agents]
        for i, (label, note) in enumerate(options):
            sel = i == self.selected_index
            line = ds.item(sel)
            line.append(label, style="bold" if sel else ds.TEXT)
            if note:
                line.append(f"  {note}", style=ds.MUTED)
            text.append_text(ds.finish(line, w))
            text.append("\n")
        text.rstrip()
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
