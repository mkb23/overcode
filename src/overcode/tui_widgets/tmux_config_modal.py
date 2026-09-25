"""
Tmux pane-toggle key configuration modal.

Lets the user change the tmux toggle key from inside the TUI. On apply,
the key is saved to config and (if keybindings are installed) reinstalled.
"""

from typing import Any, Optional

from rich.text import Text
from textual import events
from textual.message import Message

from . import dialog_style as ds
from .modal_base import ModalBase


class TmuxConfigModal(ModalBase):
    """Modal dialog for changing the tmux pane-toggle key.

    Navigate with j/k or up/down. Press space/enter to select.
    Press 'q'/Esc to cancel.
    """

    class ToggleKeyChanged(Message):
        """Message sent when a new toggle key is chosen."""

        def __init__(self, key: str, label: str, reinstalled: bool) -> None:
            super().__init__()
            self.key = key
            self.label = label
            self.reinstalled = reinstalled

    class Cancelled(Message):
        """Message sent when the modal is cancelled."""
        pass

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.current_key: Optional[str] = None
        self._choices: list[tuple[str, str]] = []

    TITLE = "Tmux pane-toggle key"
    WIDTH = 68

    def hints(self) -> str:
        return ds.hints(("↵", "choose"), ("esc", "cancel"))

    def render(self) -> Text:
        w = self.inner_width
        text = Text(no_wrap=True, overflow="crop")
        for i, (label, key) in enumerate(self._choices):
            sel = i == self.selected_index
            is_current = key == self.current_key
            line = ds.item(sel)
            line.append("● " if is_current else "○ ", style=ds.STATE_ON if is_current else ds.STATE_OTHER)
            line.append(label, style="bold" if sel else ds.TEXT)
            right = Text("current ", style=ds.MUTED) if is_current else Text()
            text.append_text(ds.spread(line, right, w) if right.plain else ds.finish(line, w))
            text.append("\n")
        text.append_text(ds.tip(w, Text("Switches between the TUI and the agent terminal (split mode)",
                                        style=ds.MUTED)))
        return text

    def on_key(self, event: events.Key) -> None:
        if not self._choices:
            if event.key in ("escape", "q", "Q"):
                self._cancel()
                event.stop()
            return

        if self._navigate(event, len(self._choices)):
            return

        key = event.key
        if key in ("space", "enter"):
            self._apply()
            event.stop()
        elif key in ("escape", "q", "Q"):
            self._cancel()
            event.stop()

    def _apply(self) -> None:
        from ..cli.split import change_toggle_key

        label, tmux_key = self._choices[self.selected_index]
        reinstalled = change_toggle_key(tmux_key)
        self.post_message(self.ToggleKeyChanged(tmux_key, label, reinstalled))
        self._hide()

    def _cancel(self) -> None:
        self.post_message(self.Cancelled())
        self._hide()

    def show(self, app_ref: Optional[Any] = None) -> None:
        from ..cli.split import TOGGLE_KEY_CHOICES
        from ..config import get_tmux_toggle_key

        self._choices = list(TOGGLE_KEY_CHOICES)
        self.current_key = get_tmux_toggle_key()
        # Start cursor on current key if present
        self.selected_index = 0
        for i, (_, k) in enumerate(self._choices):
            if k == self.current_key:
                self.selected_index = i
                break
        self._save_focus(app_ref)
        self._show(self.selected_index)
