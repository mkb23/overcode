"""
Sister selection modal for TUI.

Allows toggling visibility of each configured sister instance.
Disabled sisters' agents are hidden from the agent list.
"""

from typing import Dict, List, Optional, Any, Set

from textual.message import Message
from textual import events
from rich.text import Text

from .modal_base import ModalBase


class SisterSelectionModal(ModalBase):
    """Modal dialog for toggling sister visibility.

    Navigate with j/k, toggle with space/enter.
    Press a to apply, q/Esc to cancel.
    """

    class SelectionChanged(Message):
        """Message sent when sister selection is applied."""

        def __init__(self, disabled_sisters: Set[str]) -> None:
            super().__init__()
            self.disabled_sisters = disabled_sisters

    class Cancelled(Message):
        """Message sent when modal is cancelled."""
        pass

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._sisters: List[Dict[str, str]] = []  # [{name, url}, ...]
        self._disabled: Set[str] = set()
        self._original_disabled: Set[str] = set()

    def render(self) -> Text:
        text = Text()
        text.append("Sister Instances\n", style="bold cyan")
        text.append("j/k:move  space:toggle  a:apply  q:cancel\n\n", style="dim")

        if not self._sisters:
            text.append("  No sisters configured.\n", style="dim")
            text.append("  Add sisters in ~/.overcode/config.yaml\n", style="dim")
            return text

        for i, sister in enumerate(self._sisters):
            is_selected = i == self.selected_index
            is_enabled = sister["name"] not in self._disabled

            prefix = "> " if is_selected else "  "
            check = "[x]" if is_enabled else "[ ]"
            check_style = "bold green" if is_enabled else "dim"

            text.append(prefix, style="bold cyan" if is_selected else "")
            text.append(check, style=check_style)
            text.append(" ", style="")

            name_style = "bold" if is_selected else ""
            text.append(sister["name"], style=name_style)
            text.append(f"  {sister['url']}\n", style="dim")

        return text

    def on_key(self, event: events.Key) -> None:
        key = event.key
        if not self._sisters:
            if key in ("escape", "q", "Q"):
                self._cancel()
                event.stop()
            return

        if self._navigate(event, len(self._sisters)):
            return

        if key in ("space", "enter"):
            self._toggle_current()
            self.refresh()
            event.stop()
        elif key in ("a", "A"):
            self._apply()
            event.stop()
        elif key in ("escape", "q", "Q"):
            self._cancel()
            event.stop()

    def _toggle_current(self) -> None:
        if not self._sisters:
            return
        name = self._sisters[self.selected_index]["name"]
        if name in self._disabled:
            self._disabled.discard(name)
        else:
            self._disabled.add(name)

    def _apply(self) -> None:
        self.post_message(self.SelectionChanged(set(self._disabled)))
        self._hide()

    def _cancel(self) -> None:
        self._disabled = set(self._original_disabled)
        self.post_message(self.Cancelled())
        self._hide()

    def show(self, sisters: List[Dict[str, str]], disabled: Set[str],
             app_ref: Optional[Any] = None) -> None:
        """Display the modal with configured sisters."""
        self._sisters = list(sisters)
        self._disabled = set(disabled)
        self._original_disabled = set(disabled)
        self._save_focus(app_ref)
        self._show()
