"""
New-agent defaults configuration modal for TUI.

Keyboard-navigable checkbox list to toggle bypass_permissions and agent_teams.
Persists to ~/.overcode/config.yaml via config helpers.
"""

from typing import Optional, Any

from textual.widgets import Static
from textual.message import Message
from textual import events
from rich.text import Text


# (label, dict key)
_OPTIONS = [
    ("Bypass permissions \U0001f525", "bypass_permissions"),
    ("Agent teams \U0001f91d", "agent_teams"),
]


class NewAgentDefaultsModal(Static, can_focus=True):
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
        self.selected_index: int = 0
        self._app_ref: Optional[Any] = None
        self._previous_focus: Optional[Any] = None

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
        key = event.key

        if key in ("j", "down"):
            self.selected_index = (self.selected_index + 1) % len(_OPTIONS)
            self.refresh()
            event.stop()

        elif key in ("k", "up"):
            self.selected_index = (self.selected_index - 1) % len(_OPTIONS)
            self.refresh()
            event.stop()

        elif key in ("space", "enter"):
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

    def _hide(self) -> None:
        self.remove_class("visible")
        if self._previous_focus is not None:
            try:
                self._previous_focus.focus()
            except Exception:
                pass
        self._previous_focus = None

    def _apply(self) -> None:
        self.post_message(self.DefaultsChanged(dict(self.defaults)))
        self._hide()

    def _cancel(self) -> None:
        self.post_message(self.Cancelled())
        self._hide()

    def show(self, defaults: dict, app_ref: Optional[Any] = None) -> None:
        self.defaults = dict(defaults)
        self._app_ref = app_ref
        self._previous_focus = None
        if app_ref:
            try:
                self._previous_focus = app_ref.focused
            except Exception:
                pass
        self.selected_index = 0
        self.refresh()
        self.add_class("visible")
        try:
            self.focus()
        except Exception:
            pass
