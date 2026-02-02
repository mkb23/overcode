"""
Session action methods for TUI.

Handles agent/session operations like kill, new, sleep, command bar focus.
"""

import time
from pathlib import Path
from typing import TYPE_CHECKING

from textual.css.query import NoMatches
from textual.widgets import Input

if TYPE_CHECKING:
    from ..tui_widgets import SessionSummary, CommandBar, PreviewPane


class SessionActionsMixin:
    """Mixin providing session/agent actions for SupervisorTUI."""

    def action_toggle_focused(self) -> None:
        """Toggle expansion of focused session (only in tree mode)."""
        from ..tui_widgets import SessionSummary
        if self.view_mode == "list_preview":
            return  # Don't toggle in list mode
        focused = self.focused
        if isinstance(focused, SessionSummary):
            focused.expanded = not focused.expanded

    def action_toggle_sleep(self) -> None:
        """Toggle sleep mode for the focused agent.

        Sleep mode marks an agent as 'asleep' (human doesn't want it to do anything).
        Sleeping agents are excluded from stats calculations.
        Press z again to wake the agent.
        """
        from ..tui_widgets import SessionSummary
        focused = self.focused
        if not isinstance(focused, SessionSummary):
            self.notify("No agent focused", severity="warning")
            return

        session = focused.session
        new_asleep_state = not session.is_asleep

        # Update the session in the session manager
        self.session_manager.update_session(session.id, is_asleep=new_asleep_state)

        # Update the local session object
        session.is_asleep = new_asleep_state

        # Update the widget's display status if sleeping
        if new_asleep_state:
            focused.detected_status = "asleep"
            self.notify(f"Agent '{session.name}' is now asleep (excluded from stats)", severity="information")
        else:
            # Wake up - status will be refreshed on next update cycle
            self.notify(f"Agent '{session.name}' is now awake", severity="information")

        # Force a refresh
        focused.refresh()

    def action_kill_focused(self) -> None:
        """Kill the currently focused agent (requires confirmation)."""
        from ..tui_widgets import SessionSummary
        focused = self.focused
        if not isinstance(focused, SessionSummary):
            self.notify("No agent focused", severity="warning")
            return

        session_name = focused.session.name
        session_id = focused.session.id
        now = time.time()

        # Check if this is a confirmation of a pending kill
        if self._pending_kill:
            pending_name, pending_time = self._pending_kill
            # Confirm if same session and within 3 second window
            if pending_name == session_name and (now - pending_time) < 3.0:
                self._pending_kill = None  # Clear pending state
                self._execute_kill(focused, session_name, session_id)
                return
            else:
                # Different session or expired - start new confirmation
                self._pending_kill = None

        # First press - request confirmation
        self._pending_kill = (session_name, now)
        self.notify(
            f"Press x again to kill '{session_name}'",
            severity="warning",
            timeout=3
        )

    def action_restart_focused(self) -> None:
        """Restart the currently focused agent (requires confirmation).

        Sends Ctrl-C to kill the current Claude process, then restarts it
        with the same configuration (directory, permissions).
        """
        from ..tui_widgets import SessionSummary
        focused = self.focused
        if not isinstance(focused, SessionSummary):
            self.notify("No agent focused", severity="warning")
            return

        session_name = focused.session.name
        now = time.time()

        # Check if this is a confirmation of a pending restart
        if self._pending_restart:
            pending_name, pending_time = self._pending_restart
            # Confirm if same session and within 3 second window
            if pending_name == session_name and (now - pending_time) < 3.0:
                self._pending_restart = None  # Clear pending state
                self._execute_restart(focused)
                return
            else:
                # Different session or expired - start new confirmation
                self._pending_restart = None

        # First press - request confirmation
        self._pending_restart = (session_name, now)
        self.notify(
            f"Press R again to restart '{session_name}'",
            severity="warning",
            timeout=3
        )

    def action_sync_to_main_and_clear(self) -> None:
        """Switch to main branch, pull, and clear agent context (requires confirmation).

        This action:
        1. Runs git checkout main && git pull via Claude's bash command
        2. Sends /clear to reset the conversation context
        """
        from ..tui_widgets import SessionSummary

        focused = self.focused
        if not isinstance(focused, SessionSummary):
            self.notify("No agent focused", severity="warning")
            return

        session_name = focused.session.name
        now = time.time()

        # Check if this is a confirmation of a pending sync
        if self._pending_sync:
            pending_name, pending_time = self._pending_sync
            # Confirm if same session and within 3 second window
            if pending_name == session_name and (now - pending_time) < 3.0:
                self._pending_sync = None  # Clear pending state
                self._execute_sync(focused)
                return
            else:
                # Different session or expired - start new confirmation
                self._pending_sync = None

        # First press - request confirmation
        self._pending_sync = (session_name, now)
        self.notify(
            f"Press c again to sync '{session_name}' to main",
            severity="warning",
            timeout=3
        )

    def _execute_sync(self, widget: "SessionSummary") -> None:
        """Execute the actual sync operation after confirmation."""
        from ..tmux_manager import TmuxManager

        session = widget.session
        session_name = session.name
        tmux = TmuxManager(self.tmux_session)

        self.notify(f"Syncing '{session_name}' to main...", severity="information")

        # Send git commands - Claude will execute and return to prompt
        git_commands = "!git checkout main && git pull"
        if not tmux.send_keys(session.tmux_window, git_commands, enter=True):
            self.notify(f"Failed to send git commands to '{session_name}'", severity="error")
            return

        # Send /clear - tmux queues this, Claude processes after git completes
        if tmux.send_keys(session.tmux_window, "/clear", enter=True):
            self.notify(f"Synced '{session_name}' to main with fresh context", severity="information")
            # Reset session stats for fresh start
            self.session_manager.update_stats(
                session.id,
                current_task="Synced to main"
            )
        else:
            self.notify(f"Failed to send /clear to '{session_name}'", severity="error")

    def action_new_agent(self) -> None:
        """Prompt for directory and name to create a new agent.

        Two-step flow:
        1. Enter working directory (or press Enter for current directory)
        2. Enter agent name (defaults to directory basename)
        """
        from ..tui_widgets import CommandBar

        try:
            command_bar = self.query_one("#command-bar", CommandBar)
            command_bar.add_class("visible")  # Must show the command bar first
            command_bar.set_mode("new_agent_dir")
            # Pre-fill with current working directory
            input_widget = command_bar.query_one("#cmd-input", Input)
            input_widget.value = str(Path.cwd())
            command_bar.focus_input()
        except NoMatches:
            self.notify("Command bar not found", severity="error")

    def action_focus_command_bar(self) -> None:
        """Focus the command bar for input."""
        from ..tui_widgets import SessionSummary, CommandBar

        try:
            cmd_bar = self.query_one("#command-bar", CommandBar)

            # Show the command bar
            cmd_bar.add_class("visible")

            # Get the currently focused session (if any)
            focused = self.focused
            if isinstance(focused, SessionSummary):
                cmd_bar.set_target(focused.session.name)
            elif not cmd_bar.target_session and self.sessions:
                # Default to first session if none focused
                cmd_bar.set_target(self.sessions[0].name)

            # Enable and focus the input
            cmd_input = cmd_bar.query_one("#cmd-input", Input)
            cmd_input.disabled = False
            cmd_input.focus()
        except NoMatches:
            pass

    def action_focus_standing_orders(self) -> None:
        """Focus the command bar for editing standing orders."""
        from ..tui_widgets import SessionSummary, CommandBar

        try:
            cmd_bar = self.query_one("#command-bar", CommandBar)

            # Show the command bar
            cmd_bar.add_class("visible")

            # Get the currently focused session (if any)
            focused = self.focused
            if isinstance(focused, SessionSummary):
                cmd_bar.set_target(focused.session.name)
                # Pre-fill with existing standing orders
                cmd_input = cmd_bar.query_one("#cmd-input", Input)
                cmd_input.value = focused.session.standing_instructions or ""
            elif not cmd_bar.target_session and self.sessions:
                # Default to first session if none focused
                cmd_bar.set_target(self.sessions[0].name)

            # Set mode to standing_orders
            cmd_bar.set_mode("standing_orders")

            # Enable and focus the input
            cmd_input = cmd_bar.query_one("#cmd-input", Input)
            cmd_input.disabled = False
            cmd_input.focus()
        except NoMatches:
            pass

    def action_focus_human_annotation(self) -> None:
        """Focus input for editing human annotation (#74)."""
        from ..tui_widgets import SessionSummary, CommandBar

        try:
            cmd_bar = self.query_one("#command-bar", CommandBar)

            # Show the command bar
            cmd_bar.add_class("visible")

            # Get the currently focused session (if any)
            focused = self.focused
            if isinstance(focused, SessionSummary):
                cmd_bar.set_target(focused.session.name)
                # Pre-fill with existing annotation
                cmd_input = cmd_bar.query_one("#cmd-input", Input)
                cmd_input.value = focused.session.human_annotation or ""
            elif not cmd_bar.target_session and self.sessions:
                # Default to first session if none focused
                cmd_bar.set_target(self.sessions[0].name)

            # Set mode to annotation editing
            cmd_bar.set_mode("annotation")

            # Enable and focus the input
            cmd_input = cmd_bar.query_one("#cmd-input", Input)
            cmd_input.disabled = False
            cmd_input.focus()
        except NoMatches:
            pass

    def action_edit_agent_value(self) -> None:
        """Focus the command bar for editing agent value (#61)."""
        from ..tui_widgets import SessionSummary, CommandBar

        try:
            cmd_bar = self.query_one("#command-bar", CommandBar)

            # Show the command bar
            cmd_bar.add_class("visible")

            # Get the currently focused session (if any)
            focused = self.focused
            if isinstance(focused, SessionSummary):
                cmd_bar.set_target(focused.session.name)
                # Pre-fill with existing value
                cmd_input = cmd_bar.query_one("#cmd-input", Input)
                cmd_input.value = str(focused.session.agent_value)
            elif not cmd_bar.target_session and self.sessions:
                # Default to first session if none focused
                cmd_bar.set_target(self.sessions[0].name)
                cmd_input = cmd_bar.query_one("#cmd-input", Input)
                cmd_input.value = "1000"

            # Set mode to value
            cmd_bar.set_mode("value")

            # Enable and focus the input
            cmd_input = cmd_bar.query_one("#cmd-input", Input)
            cmd_input.disabled = False
            cmd_input.focus()
        except NoMatches:
            pass
