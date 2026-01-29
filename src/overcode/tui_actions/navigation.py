"""
Navigation action methods for TUI.

Handles moving between sessions in the list.
"""

from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from ..tui_widgets import SessionSummary


class NavigationActionsMixin:
    """Mixin providing navigation actions for SupervisorTUI."""

    def action_focus_next_session(self) -> None:
        """Focus the next session in the list."""
        widgets = self._get_widgets_in_session_order()
        if not widgets:
            return
        self.focused_session_index = (self.focused_session_index + 1) % len(widgets)
        target_widget = widgets[self.focused_session_index]
        target_widget.focus()
        if self.view_mode == "list_preview":
            self._update_preview()
        self._sync_tmux_window(target_widget)

    def action_focus_previous_session(self) -> None:
        """Focus the previous session in the list."""
        widgets = self._get_widgets_in_session_order()
        if not widgets:
            return
        self.focused_session_index = (self.focused_session_index - 1) % len(widgets)
        target_widget = widgets[self.focused_session_index]
        target_widget.focus()
        if self.view_mode == "list_preview":
            self._update_preview()
        self._sync_tmux_window(target_widget)

    def action_jump_to_attention(self) -> None:
        """Jump to next session needing attention.

        Cycles through sessions with waiting_user status first (red/bell),
        then through other non-green statuses (no_instructions, waiting_supervisor).
        """
        from ..status_constants import (
            STATUS_WAITING_USER,
            STATUS_NO_INSTRUCTIONS,
            STATUS_WAITING_SUPERVISOR,
            STATUS_RUNNING,
        )

        widgets = self._get_widgets_in_session_order()
        if not widgets:
            return

        # Build prioritized list of sessions needing attention
        # Priority: waiting_user (red) > no_instructions (yellow) > waiting_supervisor (orange)
        attention_sessions = []
        for i, widget in enumerate(widgets):
            status = getattr(widget, 'current_status', STATUS_RUNNING)
            if status == STATUS_WAITING_USER:
                attention_sessions.append((0, i, widget))  # Highest priority
            elif status == STATUS_NO_INSTRUCTIONS:
                attention_sessions.append((1, i, widget))
            elif status == STATUS_WAITING_SUPERVISOR:
                attention_sessions.append((2, i, widget))
            # Skip running, terminated, asleep

        if not attention_sessions:
            self.notify("No sessions need attention", severity="information")
            return

        # Sort by priority, then by original index
        attention_sessions.sort(key=lambda x: (x[0], x[1]))

        # Check if our cached list changed (sessions may have changed state)
        current_widget_ids = [id(w) for _, _, w in attention_sessions]
        cached_widget_ids = [id(w) for w in self._attention_jump_list]

        if current_widget_ids != cached_widget_ids:
            # List changed, reset index
            self._attention_jump_list = [w for _, _, w in attention_sessions]
            self._attention_jump_index = 0
        else:
            # Cycle to next
            self._attention_jump_index = (self._attention_jump_index + 1) % len(self._attention_jump_list)

        # Focus the target widget
        target_widget = self._attention_jump_list[self._attention_jump_index]
        # Find its index in the full widget list
        for i, w in enumerate(widgets):
            if w is target_widget:
                self.focused_session_index = i
                break

        target_widget.focus()
        if self.view_mode == "list_preview":
            self._update_preview()
        self._sync_tmux_window(target_widget)

        # Show position indicator
        pos = self._attention_jump_index + 1
        total = len(self._attention_jump_list)
        status = getattr(target_widget, 'current_status', 'unknown')
        self.notify(f"Attention {pos}/{total}: {target_widget.session.name} ({status})", severity="information")
