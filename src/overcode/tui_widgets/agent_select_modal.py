"""
Agent selection modal for TUI.

Keyboard-navigable list to pick a Claude agent persona before launching.
"""

import logging
from typing import List, Optional, Any

from textual.widgets import Static
from textual.message import Message
from textual import events
from rich.text import Text

logger = logging.getLogger(__name__)


class AgentSelectModal(Static, can_focus=True):
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
        self.selected_index: int = 0
        self._app_ref: Optional[Any] = None
        self._previous_focus: Optional[Any] = None

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
        key = event.key
        total = 1 + len(self._agents)  # (none) + agent names

        if key in ("j", "down"):
            self.selected_index = (self.selected_index + 1) % total
            self.refresh()
            event.stop()

        elif key in ("k", "up"):
            self.selected_index = (self.selected_index - 1) % total
            self.refresh()
            event.stop()

        elif key == "enter":
            self._select()
            event.stop()

        elif key in ("escape", "q", "Q"):
            self._skip()
            event.stop()

    def _hide(self) -> None:
        self.remove_class("visible")
        if self._previous_focus is not None:
            try:
                self._previous_focus.focus()
            except (AttributeError, Exception) as e:
                logger.debug("Failed to restore focus: %s", e)
        self._previous_focus = None

    def _select(self) -> None:
        if self.selected_index == 0:
            # "(none)" selected
            self.post_message(self.AgentSelected(None))
        else:
            agent_name = self._agents[self.selected_index - 1]
            self.post_message(self.AgentSelected(agent_name))
        self._hide()

    def _skip(self) -> None:
        self.post_message(self.AgentSelectSkipped())
        self._hide()

    def show(self, agents: List[str], app_ref: Optional[Any] = None) -> None:
        """Display the modal with available agents.

        Args:
            agents: List of agent names (from scan_agents).
            app_ref: Reference to the app for focus management.
        """
        self._agents = list(agents)
        self._app_ref = app_ref
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
