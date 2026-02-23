"""
Summary line configuration modal for TUI.

Simple keyboard-navigable list to toggle column group visibility.
Creates a "custom" summary detail level alongside low/med/full.
Updates live summary lines as you toggle groups.
"""

from typing import Dict, Optional, Any

from textual.widgets import Static
from textual.message import Message
from textual import events
from rich.text import Text

from ..summary_groups import get_toggleable_groups


class SummaryConfigModal(Static, can_focus=True):
    """Modal dialog for configuring summary line column visibility.

    Navigate with j/k or up/down arrows, toggle with space/enter.
    Updates live summary lines as you make changes.
    """

    class ConfigChanged(Message):
        """Message sent when configuration is applied."""

        def __init__(self, summary_groups: Dict[str, bool]) -> None:
            super().__init__()
            self.summary_groups = summary_groups

    class Cancelled(Message):
        """Message sent when modal is cancelled."""
        pass

    def __init__(self, current_config: Dict[str, bool], *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.groups = get_toggleable_groups()
        self.config = dict(current_config)
        self.original_config: Dict[str, bool] = {}
        for group in self.groups:
            if group.id not in self.config:
                self.config[group.id] = group.default_enabled
        self.selected_index = 0
        self._app_ref: Optional[Any] = None
        self._previous_focus: Optional[Any] = None

    def render(self) -> Text:
        """Render the modal content."""
        return self._build_list_text()

    def _build_list_text(self) -> Text:
        """Build the list of groups with checkboxes."""
        text = Text()
        text.append("Column Configuration\n", style="bold cyan")
        text.append("j/k:move  space:toggle  a:accept  q:cancel\n\n", style="dim")

        for i, group in enumerate(self.groups):
            is_selected = i == self.selected_index
            is_enabled = self.config.get(group.id, True)

            if is_selected:
                text.append("> ", style="bold cyan")
            else:
                text.append("  ", style="")

            if is_enabled:
                text.append("[x] ", style="bold green")
            else:
                text.append("[ ] ", style="dim")

            style = "bold" if is_selected else ""
            text.append(f"{group.name}\n", style=style)

        return text

    def _update_live_summaries(self) -> None:
        """Update the live summary lines with current config."""
        if self._app_ref is None:
            return
        try:
            from .session_summary import SessionSummary
            for widget in self._app_ref.query(SessionSummary):
                widget.summary_groups = self.config
                widget.refresh()
        except Exception:
            pass

    def on_key(self, event: events.Key) -> None:
        """Handle keyboard navigation."""
        key = event.key

        if key in ("j", "down"):
            self.selected_index = (self.selected_index + 1) % len(self.groups)
            self.refresh()
            event.stop()

        elif key in ("k", "up"):
            self.selected_index = (self.selected_index - 1) % len(self.groups)
            self.refresh()
            event.stop()

        elif key in ("space", "enter"):
            group_id = self.groups[self.selected_index].id
            self.config[group_id] = not self.config.get(group_id, True)
            self.refresh()
            self._update_live_summaries()
            event.stop()

        elif key in ("a", "A"):
            self._apply_config()
            event.stop()

        elif key in ("escape", "q", "Q"):
            self._cancel()
            event.stop()

    def _hide(self) -> None:
        """Hide the modal and restore focus."""
        self.remove_class("visible")
        # Restore focus to previously focused widget
        if self._previous_focus is not None:
            try:
                self._previous_focus.focus()
            except Exception:
                pass
        self._previous_focus = None

    def _apply_config(self) -> None:
        """Apply the current configuration."""
        self.post_message(self.ConfigChanged(self.config))
        self._hide()

    def _cancel(self) -> None:
        """Cancel and restore original config."""
        self.config = dict(self.original_config)
        self._update_live_summaries()
        self.post_message(self.Cancelled())
        self._hide()

    def show(self, current_config: Dict[str, bool], app_ref: Optional[Any] = None) -> None:
        """Show the modal with the given configuration."""
        self.config = dict(current_config)
        self.original_config = dict(current_config)
        self._app_ref = app_ref
        # Store the currently focused widget to restore later
        self._previous_focus = None
        if app_ref:
            try:
                self._previous_focus = app_ref.focused
            except Exception:
                pass
        for group in self.groups:
            if group.id not in self.config:
                self.config[group.id] = group.default_enabled
        self.selected_index = 0
        self.refresh()
        self.add_class("visible")
        # Position is set via CSS offset
        try:
            self.focus()
        except Exception:
            pass
