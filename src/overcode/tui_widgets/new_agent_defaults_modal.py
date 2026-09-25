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

from . import dialog_style as ds
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

    TITLE = "New agent defaults"
    WIDTH = 92
    _LABEL_W = 22

    _TIPS = [
        "New agents skip permission prompts",
        "New agents can run agent teams",
        "Agent CLI new agents launch with; (unset) uses the built-in default",
    ]

    def hints(self) -> str:
        return ds.hints(("space", "change"), ("a", "save"), ("esc", "cancel"))

    def render(self) -> Text:
        w = self.inner_width
        text = Text(no_wrap=True, overflow="crop")
        rows = [(label, ("off", "on"), "on" if self.defaults.get(key, False) else "off")
                for label, key in _OPTIONS]
        rows.append(("Backend", tuple(self.backend_options), self.backend_value))
        for i, (label, opts, current) in enumerate(rows):
            sel = i == self.selected_index
            line = ds.item(sel)
            line.append_text(ds.fit(Text(label, style="bold" if sel else ds.TEXT), self._LABEL_W))
            line.append_text(ds.options(opts, current, w - 1 - self._LABEL_W))
            text.append_text(ds.finish(line, w))
            text.append("\n")
        text.append_text(ds.tip(w, Text(self._TIPS[self.selected_index], style=ds.MUTED)))
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
