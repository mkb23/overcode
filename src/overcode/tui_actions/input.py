"""
Input action methods for TUI.

Handles sending keys and commands to agents.
"""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..tui_widgets import SessionSummary


def _send_keys_to_focused(tui, keys: str, *, enter: bool = False, label: str | None = None, auto_wake: bool = True) -> bool:
    """Send keys to the focused agent via tmux.

    Handles the common pattern: get focused session, check remote,
    optionally auto-wake, create launcher, send, notify.

    Args:
        tui: The SupervisorTUI instance
        keys: The key(s) to send
        enter: Whether to send Enter after the keys
        label: Display label for notifications (defaults to keys)
        auto_wake: Whether to auto-wake sleeping agents

    Returns:
        True if keys were sent successfully, False otherwise.
        Returns False (with notification) if no agent is focused.
    """
    from ..tui_widgets import SessionSummary
    from ..launcher import ClaudeLauncher

    focused = tui.focused
    if not isinstance(focused, SessionSummary):
        tui.notify("No agent focused", severity="warning")
        return False

    session = focused.session
    if getattr(session, 'is_remote', False) is True:
        tui._send_remote_key(session, keys)
        return True

    session_name = session.name
    display = label or f"'{keys}'"

    if auto_wake:
        tui._auto_wake_if_sleeping(session, focused)

    launcher = ClaudeLauncher(
        tmux_session=tui.tmux_session,
        session_manager=tui.session_manager
    )

    send_kwargs = {"enter": True} if enter else {}
    if launcher.send_to_session_by_id(session.id, keys, **send_kwargs):
        tui.notify(f"Sent {display} to {session_name}", severity="information")
        return True
    else:
        tui.notify(f"Failed to send {display} to {session_name}", severity="error")
        return False


class InputActionsMixin:
    """Mixin providing input/send actions for SupervisorTUI."""

    def _auto_wake_if_sleeping(self, session, widget=None) -> bool:
        """Auto-wake a sleeping agent when sending a command (#168).

        Args:
            session: The session to potentially wake
            widget: Optional SessionSummary widget to update

        Returns:
            True if the agent was woken, False if it wasn't sleeping
        """
        # Check both the session object and widget's detected status
        is_sleeping = session.is_asleep
        if widget and widget.detected_status == "asleep":
            is_sleeping = True

        if not is_sleeping:
            return False

        # Wake the agent - persist to disk
        self.session_manager.update_session(session.id, is_asleep=False)
        session.is_asleep = False

        # Update widget display immediately (don't wait for next refresh cycle)
        if widget:
            widget.session.is_asleep = False
            # Clear the asleep status so it shows detected status on next refresh
            if widget.detected_status == "asleep":
                widget.detected_status = "running"  # Will be corrected on next status update
            widget.refresh()

        self.notify(f"Woke agent '{session.name}' to send command", severity="information")
        return True

    def _send_remote_key(self, session, key: str) -> bool:
        """Send a key to a remote agent via the sister controller.

        Returns True if the key was sent successfully.
        """
        result = self._sister_controller.send_key(
            session.source_url, session.source_api_key,
            session.name, key,
        )
        if result.ok:
            self.notify(f"Sent '{key}' to {session.name}", severity="information")
        else:
            self.notify(f"Remote error: {result.error}", severity="error")
        return result.ok

    def action_send_enter_to_focused(self) -> None:
        """Send Enter keypress to the focused agent (for approvals)."""
        _send_keys_to_focused(self, "enter", label="Enter")

    def action_send_escape_to_focused(self) -> None:
        """Send Escape keypress to the focused agent (for interrupting)."""
        _send_keys_to_focused(self, "escape", label="Escape", auto_wake=False)

    def _is_freetext_option(self, pane_content: str, key: str) -> bool:
        """Check if a numbered menu option is a free-text instruction option.

        Scans the pane content for patterns like "5. Tell Claude what to do"
        or "3) Give custom instructions" to determine if selecting this option
        should open the command bar for user input.

        Args:
            pane_content: The tmux pane content to scan
            key: The number key being pressed (e.g., "5")

        Returns:
            True if this option expects free-text input
        """
        # Claude Code v2.x only has one freetext option format:
        # "3. No, and tell Claude what to do differently (esc)"
        # This appears on all permission prompts (Bash, Read, Write, etc.)
        freetext_patterns = [
            r"tell\s+claude\s+what\s+to\s+do",
        ]

        # Look for the numbered option in the content
        # Match patterns like "5. text", "5) text", "5: text"
        option_pattern = rf"^\s*{key}[\.\)\:]\s*(.+)$"

        for line in pane_content.split('\n'):
            match = re.match(option_pattern, line.strip(), re.IGNORECASE)
            if match:
                option_text = match.group(1).lower()
                # Check if this option matches any freetext pattern
                for pattern in freetext_patterns:
                    if re.search(pattern, option_text):
                        return True
        return False

    def _send_key_to_focused(self, key: str) -> None:
        """Send a key to the focused agent.

        If the key selects a "free text instruction" menu option (detected by
        scanning the pane content), automatically opens the command bar (#72).

        Args:
            key: The key to send
        """
        from ..tui_widgets import SessionSummary

        # Check freetext before sending (need access to focused widget)
        focused = self.focused
        if isinstance(focused, SessionSummary):
            pane_content = self.detector.get_pane_content(focused.session.tmux_window) or ""
            is_freetext = self._is_freetext_option(pane_content, key)
        else:
            is_freetext = False

        if _send_keys_to_focused(self, key, enter=True) and is_freetext:
            # Open command bar if this was a free-text instruction option (#72)
            self.action_focus_command_bar()

    def action_send_1_to_focused(self) -> None:
        """Send '1' to focused agent."""
        self._send_key_to_focused("1")

    def action_send_2_to_focused(self) -> None:
        """Send '2' to focused agent."""
        self._send_key_to_focused("2")

    def action_send_3_to_focused(self) -> None:
        """Send '3' to focused agent."""
        self._send_key_to_focused("3")

    def action_send_4_to_focused(self) -> None:
        """Send '4' to focused agent."""
        self._send_key_to_focused("4")

    def action_send_5_to_focused(self) -> None:
        """Send '5' to focused agent."""
        self._send_key_to_focused("5")
