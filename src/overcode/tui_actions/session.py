"""
Session action methods for TUI.

Handles agent/session operations like kill, new, sleep, command bar focus.
"""

import time
from pathlib import Path
from typing import TYPE_CHECKING, List

from textual.css.query import NoMatches
from textual.widgets import Input

if TYPE_CHECKING:
    from ..tui_widgets import SessionSummary, CommandBar, PreviewPane
    from ..session_manager import Session


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

        Note: Cannot put a running agent to sleep (#158).
        """
        from ..tui_widgets import SessionSummary
        from ..status_constants import STATUS_RUNNING
        focused = self.focused
        if not isinstance(focused, SessionSummary):
            self.notify("No agent focused", severity="warning")
            return

        session = focused.session
        new_asleep_state = not session.is_asleep

        # Prevent putting a running agent to sleep (#158)
        if new_asleep_state and focused.detected_status == STATUS_RUNNING:
            self.notify("Cannot put a running agent to sleep", severity="warning")
            return

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

    def action_configure_heartbeat(self) -> None:
        """Open command bar for heartbeat configuration (H key) (#171).

        Two-step flow:
        1. Enter frequency (e.g., 300, 5m, 1h) or 'off' to disable
        2. Enter instruction to send at each heartbeat
        """
        from ..tui_widgets import SessionSummary, CommandBar

        try:
            cmd_bar = self.query_one("#command-bar", CommandBar)
            cmd_bar.add_class("visible")

            # Get the currently focused session (if any)
            focused = self.focused
            if isinstance(focused, SessionSummary):
                cmd_bar.set_target(focused.session.name)
                # Pre-fill with existing frequency if enabled
                cmd_input = cmd_bar.query_one("#cmd-input", Input)
                if focused.session.heartbeat_enabled:
                    cmd_input.value = str(focused.session.heartbeat_frequency_seconds)
                else:
                    cmd_input.value = "300"  # Default 5 min
            elif not cmd_bar.target_session and self.sessions:
                cmd_bar.set_target(self.sessions[0].name)

            cmd_bar.set_mode("heartbeat_freq")
            cmd_input = cmd_bar.query_one("#cmd-input", Input)
            cmd_input.disabled = False
            cmd_input.focus()
        except NoMatches:
            pass

    def action_transport_all(self) -> None:
        """Prepare all sessions for transport/handover (requires double-press confirmation).

        Sends instructions to all active (non-sleeping) agents to:
        - Create a new branch if on main/master
        - Commit their current changes
        - Push to their branch
        - Create a draft PR if none exists
        - Post handover summary as a PR comment

        Sleeping agents are excluded from handover.
        """
        now = time.time()

        # Get active sessions (exclude terminated and sleeping)
        active_sessions = [
            s for s in self.sessions
            if s.status != "terminated" and not s.is_asleep
        ]
        if not active_sessions:
            self.notify("No active sessions to prepare (sleeping sessions excluded)", severity="warning")
            return

        # Check if this is a confirmation of a pending transport
        if self._pending_transport is not None:
            if (now - self._pending_transport) < 3.0:
                self._pending_transport = None  # Clear pending state
                self._execute_transport_all(active_sessions)
                return
            else:
                # Expired - start new confirmation
                self._pending_transport = None

        # First press - request confirmation
        self._pending_transport = now
        count = len(active_sessions)
        self.notify(
            f"Press H again to send handover instructions to {count} agent(s)",
            severity="warning",
            timeout=3
        )

    def _execute_transport_all(self, sessions: List["Session"]) -> None:
        """Execute transport/handover instructions to all sessions."""
        from ..launcher import ClaudeLauncher

        # The handover instruction to send to each agent
        handover_instruction = (
            "Please prepare for handover. Follow these steps in order:\n\n"
            "1. Check your current branch with `git branch --show-current`\n"
            "   - If on main or master, create and switch to a new branch:\n"
            "     `git checkout -b handover/<brief-task-description>`\n"
            "   - Never push directly to main/master\n\n"
            "2. Commit all your current changes with a descriptive commit message\n\n"
            "3. Push to your branch: `git push -u origin <branch-name>`\n\n"
            "4. Check if a PR exists: `gh pr list --head $(git branch --show-current)`\n"
            "   - If no PR exists, create a draft PR:\n"
            "     `gh pr create --draft --title '<brief title>' --body 'WIP'`\n\n"
            "5. Post a handover comment on the PR using `gh pr comment` with:\n"
            "   - What you've accomplished\n"
            "   - Current state of the work\n"
            "   - Any pending tasks or next steps\n"
            "   - Known issues or blockers"
        )

        launcher = ClaudeLauncher(
            tmux_session=self.tmux_session,
            session_manager=self.session_manager
        )

        success_count = 0
        for session in sessions:
            if launcher.send_to_session(session.name, handover_instruction):
                success_count += 1

        if success_count == len(sessions):
            self.notify(
                f"Sent handover instructions to {success_count} agent(s)",
                severity="information"
            )
        else:
            failed = len(sessions) - success_count
            self.notify(
                f"Sent to {success_count}, failed {failed} agent(s)",
                severity="warning"
            )
