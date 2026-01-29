"""
Input action methods for TUI.

Handles sending keys and commands to agents.
"""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..tui_widgets import SessionSummary


class InputActionsMixin:
    """Mixin providing input/send actions for SupervisorTUI."""

    def action_send_enter_to_focused(self) -> None:
        """Send Enter keypress to the focused agent (for approvals)."""
        from ..tui_widgets import SessionSummary
        from ..launcher import ClaudeLauncher

        focused = self.focused
        if not isinstance(focused, SessionSummary):
            self.notify("No agent focused", severity="warning")
            return

        session_name = focused.session.name
        launcher = ClaudeLauncher(
            tmux_session=self.tmux_session,
            session_manager=self.session_manager
        )

        # Send "enter" which the launcher handles as just pressing Enter
        if launcher.send_to_session(session_name, "enter"):
            self.notify(f"Sent Enter to {session_name}", severity="information")
        else:
            self.notify(f"Failed to send Enter to {session_name}", severity="error")

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
        from ..launcher import ClaudeLauncher

        focused = self.focused
        if not isinstance(focused, SessionSummary):
            self.notify("No agent focused", severity="warning")
            return

        session_name = focused.session.name
        launcher = ClaudeLauncher(
            tmux_session=self.tmux_session,
            session_manager=self.session_manager
        )

        # Check if this option is a free-text instruction option before sending
        pane_content = self.status_detector.get_pane_content(focused.session.tmux_window) or ""
        is_freetext = self._is_freetext_option(pane_content, key)

        # Send the key followed by Enter (to select the numbered option)
        if launcher.send_to_session(session_name, key, enter=True):
            self.notify(f"Sent '{key}' to {session_name}", severity="information")
            # Open command bar if this was a free-text instruction option (#72)
            if is_freetext:
                self.action_focus_command_bar()
        else:
            self.notify(f"Failed to send '{key}' to {session_name}", severity="error")

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
