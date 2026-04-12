"""
New-agent defaults configuration modal for TUI.

Keyboard-navigable checkbox list to toggle bypass_permissions and agent_teams.
Persists to ~/.overcode/config.yaml via config helpers.
"""

from typing import Optional, Any

from textual.message import Message
from textual import events
from rich.text import Text

from .modal_base import ModalBase


# (label, dict key)
_OPTIONS = [
    ("Bypass permissions \U0001f525", "bypass_permissions"),
    ("Agent teams \U0001f91d", "agent_teams"),
]


class NewAgentDefaultsModal(ModalBase):
    """Modal dialog for configuring new-agent defaults.

    Navigate with j/k or up/down arrows, toggle with space/enter.
    Press 'a' to apply, 'q'/Esc to cancel.
    """

    class DefaultsChanged(Message):
        """Message sent when defaults are applied."""

        def __init__(self, defaults: dict) -> None:
            super().__init__()
            self.defaults = defaults

    class Cancelled(Message):
        """Message sent when modal is cancelled."""
        pass

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.defaults: dict = {"bypass_permissions": False, "agent_teams": False}

    def render(self) -> Text:
        text = Text()
        text.append("New Agent Defaults\n", style="bold cyan")
        text.append("j/k:move  space:toggle  a:apply  q:cancel\n\n", style="dim")

        for i, (label, key) in enumerate(_OPTIONS):
            is_selected = i == self.selected_index
            is_enabled = self.defaults.get(key, False)

            if is_selected:
                text.append("> ", style="bold cyan")
            else:
                text.append("  ", style="")

            if is_enabled:
                text.append("[x] ", style="bold green")
            else:
                text.append("[ ] ", style="dim")

            style = "bold" if is_selected else ""
            text.append(f"{label}\n", style=style)

        return text

    def on_key(self, event: events.Key) -> None:
        if self._navigate(event, len(_OPTIONS)):
            return

        key = event.key
        if key in ("space", "enter"):
            _, dict_key = _OPTIONS[self.selected_index]
            self.defaults[dict_key] = not self.defaults.get(dict_key, False)
            self.refresh()
            event.stop()
        elif key in ("a", "A"):
            self._apply()
            event.stop()
        elif key in ("escape", "q", "Q"):
            self._cancel()
            event.stop()

    def _apply(self) -> None:
        self.post_message(self.DefaultsChanged(dict(self.defaults)))
        self._hide()

    def _cancel(self) -> None:
        self.post_message(self.Cancelled())
        self._hide()

    def show(self, defaults: dict, app_ref: Optional[Any] = None) -> None:
        self.defaults = dict(defaults)
        self._save_focus(app_ref)
        self._show()
