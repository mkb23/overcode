"""
Sister selection modal for TUI.

Allows toggling visibility of each configured sister instance.
Disabled sisters' agents are hidden from the agent list.
"""

from typing import Dict, List, Optional, Any, Set

from textual.widgets import Static
from textual.message import Message
from textual import events
from rich.text import Text


class SisterSelectionModal(Static, can_focus=True):
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
        self.selected_index: int = 0
        self._app_ref: Optional[Any] = None
        self._previous_focus: Optional[Any] = None

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

        if key in ("j", "down"):
            self.selected_index = (self.selected_index + 1) % len(self._sisters)
            self.refresh()
            event.stop()

        elif key in ("k", "up"):
            self.selected_index = (self.selected_index - 1) % len(self._sisters)
            self.refresh()
            event.stop()

        elif key in ("space", "enter"):
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

    def _hide(self) -> None:
        self.remove_class("visible")
        if self._previous_focus is not None:
            try:
                self._previous_focus.focus()
            except Exception:
                pass
        self._previous_focus = None

    def _apply(self) -> None:
        self.post_message(self.SelectionChanged(set(self._disabled)))
        self._hide()

    def _cancel(self) -> None:
        self._disabled = set(self._original_disabled)
        self.post_message(self.Cancelled())
        self._hide()

    def show(self, sisters: List[Dict[str, str]], disabled: Set[str],
             app_ref: Optional[Any] = None) -> None:
        """Display the modal with configured sisters.

        Args:
            sisters: List of dicts with 'name' and 'url' keys.
            disabled: Set of sister names currently disabled.
            app_ref: Reference to the app for focus management.
        """
        self._sisters = list(sisters)
        self._disabled = set(disabled)
        self._original_disabled = set(disabled)
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
