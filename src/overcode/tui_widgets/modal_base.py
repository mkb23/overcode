"""
Base class for keyboard-navigable modal dialogs.

Shared show/hide, focus save/restore and j/k navigation, plus the frame
every dialog shares with the command palette (#488): a width, centred
near the top of the screen, the title set into the top border and the
keys into the bottom one. The rows themselves are drawn with the helpers
in dialog_style.py.
"""

import logging
from typing import Optional, Any

from textual.widgets import Static
from textual import events

logger = logging.getLogger(__name__)


class ModalBase(Static, can_focus=True):
    """Base class for modal dialogs with keyboard navigation.

    Subclasses must implement:
        - render() -> Text, drawing rows `self.inner_width` cells wide
        - on_key() for modal-specific key bindings (call super for navigation)

    and may set TITLE (the top border), WIDTH (the widest the dialog
    gets; narrower terminals get less) and hints() (the bottom border).

    Provides:
        - show()/hide with focus save/restore
        - j/k/up/down navigation with selected_index
        - relayout(), called on show and on terminal resize
    """

    DEFAULT_CLASSES = "modal"
    TITLE: str = ""
    WIDTH: int = 64

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.selected_index: int = 0
        self._app_ref: Optional[Any] = None
        self._previous_focus: Optional[Any] = None
        self._inner_width: int = self.WIDTH - 4
        self._screen_height: int = 40

    @property
    def inner_width(self) -> int:
        """Cells available to a row: the width less border and padding."""
        return getattr(self, "_inner_width", self.WIDTH - 4)

    def hints(self) -> str:
        """Keys shown in the bottom border, e.g. "↵ select · esc cancel"."""
        return ""

    def relayout(self) -> None:
        """Size the dialog for the terminal and centre it, near the top
        like the palette; refresh the border text."""
        self.update_frame()
        try:
            screen_w, screen_h = self.app.size
        except Exception:
            return
        width = max(24, min(self.WIDTH, screen_w - 2))
        top = 1 if screen_h < 30 else 3
        self._inner_width = width - 4  # border + 1 col padding each side
        self._screen_height = screen_h
        self.styles.width = width
        self.styles.max_height = max(5, screen_h - top - 1)
        self.styles.offset = (max(0, (screen_w - width) // 2), top)
        self.refresh(layout=True)

    def update_frame(self) -> None:
        """Set the border title and key hints (after a mode change)."""
        self.border_title = self.TITLE or None
        self.border_subtitle = self.hints() or None

    def _save_focus(self, app_ref: Optional[Any]) -> None:
        """Save current focus for later restoration."""
        self._app_ref = app_ref
        self._previous_focus = None
        if app_ref:
            try:
                self._previous_focus = app_ref.focused
            except (AttributeError, Exception) as e:
                logger.debug("Failed to save focus: %s", e)

    def _show(self, index: int = 0) -> None:
        """Common show logic: set the index, lay out, add visible class, focus."""
        self.selected_index = index
        self.relayout()
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
