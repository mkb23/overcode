"""
New-agent defaults configuration modal for TUI.

Keyboard-navigable checkbox list to toggle bypass_permissions and
agent_teams, plus cycling Backend, Skill profile and Billing code rows and
a typed Default code. Persists to ~/.overcode/config.yaml via config
helpers (``new_agent_defaults``; ``billing.new_agent_code`` and
``billing.default_code``).
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

# billing.new_agent_code (config.NEW_AGENT_CODE_MODES)
BILLING_MODES = ("name", "folder", "default")


class NewAgentDefaultsModal(ModalBase):
    """Modal dialog for configuring new-agent defaults.

    Navigate with j/k or up/down arrows, toggle checkboxes / cycle the
    Backend row with space/enter. Press 'a' to apply, 'q'/Esc to cancel.
    """

    class DefaultsChanged(Message):
        """Message sent when defaults are applied."""

        def __init__(self, defaults: dict, billing: Optional[dict] = None) -> None:
            super().__init__()
            self.defaults = defaults
            self.billing = billing  # {"new_agent_code", "default_code"}

    class Cancelled(Message):
        """Message sent when modal is cancelled."""
        pass

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.defaults: dict = {"bypass_permissions": False, "agent_teams": False}
        self.backend_options: list[str] = [UNSET_BACKEND]
        self.backend_value: str = UNSET_BACKEND
        self.profile_options: list[str] = ["none"]
        self.profile_value: str = "none"
        self.billing_mode: str = "name"
        self.default_code: str = "general"
        self.editing = False   # typing the default code
        self.cursor = 0
        self._snapshot = ""

    # The Backend and Skill profile rows are synthetic entries appended after
    # the checkboxes.
    def _backend_row(self) -> int:
        return len(_OPTIONS)

    def _profile_row(self) -> int:
        return len(_OPTIONS) + 1

    def _billing_row(self) -> int:
        return len(_OPTIONS) + 2

    def _default_code_row(self) -> int:
        return len(_OPTIONS) + 3

    def _row_count(self) -> int:
        return len(_OPTIONS) + 4

    TITLE = "New agent defaults"
    WIDTH = 92
    _LABEL_W = 22

    _TIPS = [
        "New agents skip permission prompts",
        "New agents can run agent teams",
        "Agent CLI new agents launch with; (unset) uses the built-in default",
        "Skill profile for new agents when neither parent nor folder sets one",
        "A new agent's billing code: its name (follows auto names), its repo, or the default code",
        "The code 'default' bills to, and what collapse (m dialog) folds codes into",
    ]

    def hints(self) -> str:
        if self.editing:
            return ds.hints(("↵", "confirm"), ("esc", "undo edit"))
        return ds.hints(("space", "change"), ("a", "save"), ("esc", "cancel"))

    def render(self) -> Text:
        w = self.inner_width
        text = Text(no_wrap=True, overflow="crop")
        rows = [(label, ("off", "on"), "on" if self.defaults.get(key, False) else "off")
                for label, key in _OPTIONS]
        rows.append(("Backend", tuple(self.backend_options), self.backend_value))
        rows.append(("Skill profile", tuple(self.profile_options), self.profile_value))
        rows.append(("Billing code", BILLING_MODES, self.billing_mode))
        rows.append(("Default code", None, self.default_code))
        for i, (label, opts, current) in enumerate(rows):
            sel = i == self.selected_index
            line = ds.item(sel)
            line.append_text(ds.fit(Text(label, style="bold" if sel else ds.TEXT), self._LABEL_W))
            if opts is None:
                line.append_text(ds.text_value(current, self.cursor if sel and self.editing
                                               else None))
            else:
                line.append_text(ds.options(opts, current, w - 1 - self._LABEL_W))
            text.append_text(ds.finish(line, w))
            text.append("\n")
        tip = ("type the code's name · ← → home end move" if self.editing
               else self._TIPS[self.selected_index])
        text.append_text(ds.tip(w, Text(tip, style=ds.MUTED)))
        return text

    def on_key(self, event: events.Key) -> None:
        if self.editing:
            self._edit_key(event.key, event.character)
            event.stop()
            self.update_frame()
            self.refresh()
            return
        if self._navigate(event, self._row_count()):
            return

        key = event.key
        if key in ("space", "enter"):
            if self.selected_index == self._default_code_row():
                self.editing = True
                self._snapshot = self.default_code
                self.cursor = len(self.default_code)
                self.update_frame()
            elif self.selected_index == self._billing_row():
                i = BILLING_MODES.index(self.billing_mode) if self.billing_mode in BILLING_MODES else -1
                self.billing_mode = BILLING_MODES[(i + 1) % len(BILLING_MODES)]
            elif self.selected_index == self._backend_row():
                self._cycle_backend()
            elif self.selected_index == self._profile_row():
                opts = self.profile_options
                i = opts.index(self.profile_value) if self.profile_value in opts else -1
                self.profile_value = opts[(i + 1) % len(opts)]
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

    def _edit_key(self, key: str, character: Optional[str]) -> None:
        val, pos = self.default_code, self.cursor
        if key == "enter":
            self.default_code = val.strip() or self._snapshot
            self.editing = False
        elif key == "escape":
            self.default_code = self._snapshot
            self.editing = False
        elif key == "backspace":
            if pos > 0:
                self.default_code, self.cursor = val[:pos - 1] + val[pos:], pos - 1
        elif key == "delete":
            self.default_code = val[:pos] + val[pos + 1:]
        elif key == "left":
            self.cursor = max(0, pos - 1)
        elif key == "right":
            self.cursor = min(len(val), pos + 1)
        elif key == "home":
            self.cursor = 0
        elif key == "end":
            self.cursor = len(val)
        elif character and character.isprintable():
            self.default_code, self.cursor = val[:pos] + character + val[pos:], pos + 1

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
        result["skill_profile"] = None if self.profile_value == "none" else self.profile_value
        self.post_message(self.DefaultsChanged(result, {
            "new_agent_code": self.billing_mode, "default_code": self.default_code}))
        self._hide()

    def _cancel(self) -> None:
        self.post_message(self.Cancelled())
        self._hide()

    def show(self, defaults: dict, app_ref: Optional[Any] = None,
             billing: Optional[dict] = None, *, at_billing: bool = False) -> None:
        """``billing``: ``config.get_billing_defaults()``; ``at_billing`` opens on its row."""
        self.defaults = dict(defaults)
        billing = billing or {}
        self.billing_mode = billing.get("new_agent_code") or "name"
        self.default_code = billing.get("default_code") or "general"
        self.editing = False

        from ..backends import list_backends
        self.backend_options = [UNSET_BACKEND] + list_backends()
        if defaults.get("backend_explicit") and defaults.get("backend") in self.backend_options:
            self.backend_value = defaults["backend"]
        else:
            self.backend_value = UNSET_BACKEND

        from ..skill_library import get_profiles
        self.profile_options = ["none"] + list(get_profiles())
        current = defaults.get("skill_profile")
        self.profile_value = current if current in self.profile_options else "none"

        self._save_focus(app_ref)
        self._show(self._billing_row() if at_billing else 0)
