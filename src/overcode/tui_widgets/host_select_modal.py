"""
Host selection modal for TUI.

Keyboard-navigable list to pick where to launch a new agent:
local machine or a remote sister.
"""

from typing import List, Optional, Any

from textual.message import Message
from textual import events
from rich.text import Text

from .modal_base import ModalBase


class HostSelectModal(ModalBase):
    """Modal dialog for selecting a host (local or sister).

    Navigate with j/k or up/down arrows.
    Press Enter to select, q/Esc to cancel.
    """

    class HostSelected(Message):
        """Message sent when a host is selected."""

        def __init__(self, host_name: str, is_local: bool) -> None:
            super().__init__()
            self.host_name = host_name
            self.is_local = is_local

    class HostSelectCancelled(Message):
        """Message sent when host selection is cancelled."""
        pass

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._local_hostname: str = ""
        self._sister_names: List[str] = []

    def render(self) -> Text:
        text = Text()
        text.append("Select Host\n", style="bold cyan")
        text.append("j/k:move  enter:select  q:cancel\n\n", style="dim")

        options = self._build_options()
        for i, (label, dim_suffix) in enumerate(options):
            is_selected = i == self.selected_index
            if is_selected:
                text.append("> ", style="bold cyan")
            else:
                text.append("  ", style="")
            text.append(label, style="bold" if is_selected else "")
            if dim_suffix:
                text.append(dim_suffix, style="dim")
            text.append("\n")

        return text

    def _build_options(self):
        """Build list of (label, dim_suffix) tuples."""
        options = [(self._local_hostname, " (local)")]
        for name in self._sister_names:
            options.append((name, " (remote)"))
        return options

    def on_key(self, event: events.Key) -> None:
        total = 1 + len(self._sister_names)
        if self._navigate(event, total):
            return

        key = event.key
        if key == "enter":
            self._select()
            event.stop()
        elif key in ("escape", "q", "Q"):
            self._cancel()
            event.stop()

    def _select(self) -> None:
        if self.selected_index == 0:
            self.post_message(self.HostSelected(self._local_hostname, is_local=True))
        else:
            name = self._sister_names[self.selected_index - 1]
            self.post_message(self.HostSelected(name, is_local=False))
        self._hide()

    def _cancel(self) -> None:
        self.post_message(self.HostSelectCancelled())
        self._hide()

    def show(self, local_hostname: str, sister_names: List[str],
             app_ref: Optional[Any] = None) -> None:
        """Display the modal with available hosts."""
        self._local_hostname = local_hostname
        self._sister_names = list(sister_names)
        self._save_focus(app_ref)
        self._show()
