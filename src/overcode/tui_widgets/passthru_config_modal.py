"""
Passthru key configuration modal (#446).

Lets the user toggle which of overcode's passthru-by-default hotkeys are
actually forwarded to the focused agent. Arbitrary remaps and fully
user-added slots are supported via config.yaml; this modal only exposes
on/off toggling of the default set to keep the UI simple.
"""

from typing import Any, Optional

from rich.text import Text
from textual import events
from textual.message import Message

from . import dialog_style as ds
from .modal_base import ModalBase


class PassthruConfigModal(ModalBase):
    """Modal dialog for toggling default passthru hotkeys on/off.

    Navigate with j/k or up/down. Space/enter toggles the selected key.
    Press 'w' to write changes to config.yaml, 'q'/Esc to cancel.
    """

    class Saved(Message):
        """Message sent when the user saves the updated passthru map."""

        def __init__(self, mapping: dict[str, str]) -> None:
            super().__init__()
            self.mapping = mapping

    class Cancelled(Message):
        """Message sent when the modal is cancelled."""
        pass

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Ordered list of (slot_key, default_target) to display
        self._slots: list[tuple[str, str]] = []
        # Working copy of {slot: target} the user is editing
        self._working: dict[str, str] = {}

    TITLE = "Passthru keys"
    WIDTH = 76

    def hints(self) -> str:
        return ds.hints(("space", "toggle"), ("w", "save"), ("esc", "cancel"))

    def render(self) -> Text:
        w = self.inner_width
        text = Text(no_wrap=True, overflow="crop")
        slot_w = max((len(s) for s, _ in self._slots), default=6) + 2
        for i, (slot, default_target) in enumerate(self._slots):
            sel = i == self.selected_index
            is_enabled = slot in self._working
            current = self._working.get(slot, default_target)
            line = ds.item(sel)
            line.append_text(ds.check(is_enabled))
            line.append(" ")
            line.append(f"{slot:<{slot_w}}", style=ds.KEY if is_enabled else ds.STATE_OTHER)
            if not is_enabled:
                line.append("not forwarded", style=f"italic {ds.STATE_OTHER}")
            else:
                remapped = current != default_target
                line.append("→ sends ", style=ds.MUTED)
                line.append(current, style=ds.WARN if remapped else ds.TEXT)
                if remapped:
                    line.append("  remapped", style=ds.MUTED)
            text.append_text(ds.finish(line, w))
            text.append("\n")
        text.append_text(ds.tip(w, Text("Ticked keys go to the focused agent · remaps: ~/.overcode/config.yaml",
                                        style=ds.MUTED)))
        return text

    def on_key(self, event: events.Key) -> None:
        if not self._slots:
            if event.key in ("escape", "q", "Q"):
                self._cancel()
                event.stop()
            return

        if self._navigate(event, len(self._slots)):
            return

        key = event.key
        if key in ("space", "enter"):
            self._toggle_selected()
            event.stop()
        elif key in ("w", "W"):
            self._save()
            event.stop()
        elif key in ("escape", "q", "Q"):
            self._cancel()
            event.stop()

    def _toggle_selected(self) -> None:
        slot, default_target = self._slots[self.selected_index]
        if slot in self._working:
            del self._working[slot]
        else:
            self._working[slot] = default_target
        self.refresh()

    def _save(self) -> None:
        from ..config import save_passthru_keys

        save_passthru_keys(self._working)
        self.post_message(self.Saved(dict(self._working)))
        self._hide()

    def _cancel(self) -> None:
        self.post_message(self.Cancelled())
        self._hide()

    def show(self, app_ref: Optional[Any] = None) -> None:
        from ..config import DEFAULT_PASSTHRU_KEYS, get_passthru_keys

        self._slots = list(DEFAULT_PASSTHRU_KEYS.items())
        # Include any user-added slots (beyond defaults) so they can be
        # toggled from the modal too, even though adding/remapping them
        # has to happen in config.yaml first.
        active = get_passthru_keys()
        for slot, target in active.items():
            if slot not in DEFAULT_PASSTHRU_KEYS:
                self._slots.append((slot, target))
        self._working = dict(active)
        self._save_focus(app_ref)
        self._show()
