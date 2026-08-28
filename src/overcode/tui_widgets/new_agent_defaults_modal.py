"""
New-agent defaults configuration modal for TUI.

Keyboard-navigable checkbox list to toggle bypass_permissions and
agent_teams, plus a cycling Backend row. Persists to ~/.overcode/config.yaml
via config helpers.
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

# Sentinel shown/selected when no backend is pinned in config — new agents
# fall back to whatever get_new_agent_defaults()/list_backends() calls the
# built-in default (mirrors config.py's get_new_agent_defaults docstring).
UNSET_BACKEND = "(unset)"


class NewAgentDefaultsModal(ModalBase):
    """Modal dialog for configuring new-agent defaults.

    Navigate with j/k or up/down arrows, toggle checkboxes / cycle the
    Backend row with space/enter. Press 'a' to apply, 'q'/Esc to cancel.
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
        self.backend_options: list[str] = [UNSET_BACKEND]
        self.backend_value: str = UNSET_BACKEND

    # The Backend row is a synthetic entry appended after the checkboxes.
    def _backend_row(self) -> int:
        return len(_OPTIONS)

    def _row_count(self) -> int:
        return len(_OPTIONS) + 1

    def render(self) -> Text:
        text = Text()
        text.append("New Agent Defaults\n", style="bold cyan")
        text.append("j/k:move  space:toggle/cycle  a:apply  q:cancel\n\n", style="dim")

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

        is_selected = self._backend_row() == self.selected_index
        text.append("> " if is_selected else "  ", style="bold cyan" if is_selected else "")
        style = "bold" if is_selected else ""
        text.append(f"Backend: {self.backend_value}\n", style=style)

        return text

    def on_key(self, event: events.Key) -> None:
        if self._navigate(event, self._row_count()):
            return

        key = event.key
        if key in ("space", "enter"):
            if self.selected_index == self._backend_row():
                self._cycle_backend()
            else:
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

    def _cycle_backend(self) -> None:
        try:
            idx = self.backend_options.index(self.backend_value)
        except ValueError:
            idx = -1
        self.backend_value = self.backend_options[(idx + 1) % len(self.backend_options)]

    def _apply(self) -> None:
        result = dict(self.defaults)
        # backend_explicit is derived by config.get_new_agent_defaults() from
        # whatever "backend" ends up in config.yaml — it's not itself a
        # config key, so it isn't persisted.
        result.pop("backend_explicit", None)
        result["backend"] = None if self.backend_value == UNSET_BACKEND else self.backend_value
        self.post_message(self.DefaultsChanged(result))
        self._hide()

    def _cancel(self) -> None:
        self.post_message(self.Cancelled())
        self._hide()

    def show(self, defaults: dict, app_ref: Optional[Any] = None) -> None:
        self.defaults = dict(defaults)

        from ..backends import list_backends
        self.backend_options = [UNSET_BACKEND] + list_backends()
        if defaults.get("backend_explicit") and defaults.get("backend") in self.backend_options:
            self.backend_value = defaults["backend"]
        else:
            self.backend_value = UNSET_BACKEND

        self._save_focus(app_ref)
        self._show()
