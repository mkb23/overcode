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

    def render(self) -> Text:
        text = Text()
        text.append("Passthru Keys\n", style="bold cyan")
        text.append(
            "j/k:move  space/enter:toggle  w:save  q:cancel\n",
            style="dim",
        )
        text.append(
            "(advanced remaps and extra slots: edit ~/.overcode/config.yaml)\n\n",
            style="dim",
        )

        for i, (slot, default_target) in enumerate(self._slots):
            is_selected = i == self.selected_index
            is_enabled = slot in self._working
            current = self._working.get(slot, default_target)
            remapped = is_enabled and current != default_target

            prefix = "> " if is_selected else "  "
            text.append(prefix, style="bold cyan" if is_selected else "")
            mark = "☒" if is_enabled else "☐"
            mark_style = "bold green" if is_enabled else "dim"
            text.append(f"{mark} ", style=mark_style)
            row_style = "bold" if is_selected else ""
            text.append(f"{slot}", style=row_style)
            if remapped:
                text.append(f"  → {current}", style="yellow")
            elif is_enabled:
                text.append(f"  → {current}", style="dim")
            else:
                text.append("  (disabled)", style="dim")
            text.append("\n", style="")

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
        self.selected_index = 0
        self._save_focus(app_ref)
        self.refresh()
        self.add_class("visible")
        try:
            self.focus()
        except Exception:
            pass
