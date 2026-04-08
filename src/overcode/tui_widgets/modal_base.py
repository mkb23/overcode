"""
Base class for keyboard-navigable modal dialogs.

Extracts shared show/hide, focus save/restore, and j/k navigation
patterns common to all 5 modal widgets.
"""

import logging
from typing import Optional, Any

from textual.widgets import Static
from textual import events

logger = logging.getLogger(__name__)


class ModalBase(Static, can_focus=True):
    """Base class for modal dialogs with keyboard navigation.

    Subclasses must implement:
        - render() -> Text
        - on_key() for modal-specific key bindings (call super for navigation)

    Provides:
        - show()/hide with focus save/restore
        - j/k/up/down navigation with selected_index
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.selected_index: int = 0
        self._app_ref: Optional[Any] = None
        self._previous_focus: Optional[Any] = None

    def _save_focus(self, app_ref: Optional[Any]) -> None:
        """Save current focus for later restoration."""
        self._app_ref = app_ref
        self._previous_focus = None
        if app_ref:
            try:
                self._previous_focus = app_ref.focused
            except (AttributeError, Exception) as e:
                logger.debug("Failed to save focus: %s", e)

    def _show(self) -> None:
        """Common show logic: reset index, refresh, add visible class, focus."""
        self.selected_index = 0
        self.refresh()
        self.add_class("visible")
        try:
            self.focus()
        except (AttributeError, Exception) as e:
            logger.debug("Failed to focus modal: %s", e)

    def _hide(self) -> None:
        """Hide the modal and restore previous focus."""
        self.remove_class("visible")
        if self._previous_focus is not None:
            try:
                self._previous_focus.focus()
            except (AttributeError, Exception) as e:
                logger.debug("Failed to restore focus: %s", e)
        self._previous_focus = None

    def _navigate(self, event: events.Key, total: int) -> bool:
        """Handle j/k/up/down navigation. Returns True if handled."""
        key = event.key
        if key in ("j", "down"):
            self.selected_index = (self.selected_index + 1) % total
            self.refresh()
            event.stop()
            return True
        elif key in ("k", "up"):
            self.selected_index = (self.selected_index - 1) % total
            self.refresh()
            event.stop()
            return True
        return False
