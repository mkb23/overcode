"""
Session action methods for TUI.

Handles agent/session operations like kill, new, sleep, command bar focus.
Supports both local and remote (sister) agents — remote actions are dispatched
through the SisterController HTTP client.
"""

import time
from pathlib import Path
from typing import TYPE_CHECKING, List

from textual.css.query import NoMatches
from textual.widgets import Input

if TYPE_CHECKING:
    from ..tui_widgets import SessionSummary, CommandBar, PreviewPane
    from ..session_manager import Session


def _get_focused_session(tui) -> "SessionSummary | None":
    """Return the focused widget if it's a SessionSummary, else None."""
    from ..tui_widgets import SessionSummary
    focused = tui.focused
    return focused if isinstance(focused, SessionSummary) else None


class SessionActionsMixin:
    """Mixin providing session/agent actions for SupervisorTUI."""

    def _is_remote(self, session) -> bool:
        """Return True if session is a remote (sister) agent."""
        return getattr(session, 'is_remote', False) is True

    def _sister_reachable(self, session) -> bool:
        """Check if the sister hosting this remote session is currently reachable."""
        for state in self._sister_poller.get_sister_states():
            if state.url == session.source_url:
                return state.reachable
        return False

    def _guard_remote(self, session) -> bool:
        """Block remote actions when the sister is unreachable. Returns True if blocked."""
        if self._is_remote(session) and not self._sister_reachable(session):
            self.notify(f"Sister '{session.source_host}' is unreachable", severity="warning")
            return True
        return False

    def _remote_notify(self, result, success_msg: str) -> None:
        """Show notification based on a SisterController result."""
        if result.ok:
            self.notify(success_msg, severity="information")
        else:
            self.notify(f"Remote error: {result.error}", severity="error")

    def _confirm_double_press(
        self,
        action_key: str,
        message: str,
        callback,
        session_name: str | None = None,
        timeout: float = 3.0,
    ) -> None:
        """Generic double-press confirmation pattern.

        First press shows a warning notification. Second press within timeout
        executes the callback. If the session_name changes, the confirmation
        resets.

        Args:
            action_key: Unique key for this action (e.g., "kill", "restart")
            message: Warning message shown on first press (e.g., "Press x again to kill 'agent'")
            callback: Callable to execute on confirmation
            session_name: Session name to match (None for global actions)
            timeout: Seconds before confirmation expires
        """
        now = time.time()
        pending = self._pending_confirmations.get(action_key)

        if pending is not None:
            pending_name, pending_time = pending
            if pending_name == session_name and (now - pending_time) < timeout:
                del self._pending_confirmations[action_key]
                callback()
                return
            # Different session or expired — reset
            del self._pending_confirmations[action_key]

        self._pending_confirmations[action_key] = (session_name, now)
        self.notify(message, severity="warning", timeout=int(timeout))

    def action_fork_focused(self) -> None:
        """Fork the focused agent's context into a new child agent.

        Uses Claude's --resume --fork-session to create a new agent with
        the source agent's full conversation history. The forked agent is
        registered as a child of the source.
        """
        from ..tui_widgets import CommandBar

        focused = _get_focused_session(self)
        if not focused:
            self.notify("No agent focused", severity="warning")
            return

        session = focused.session

        if session.status in ("terminated", "done"):
            self.notify("Cannot fork a terminated agent", severity="warning")
            return

        if not session.active_claude_session_id:
            self.notify("No Claude session ID detected yet — try again shortly", severity="warning")
            return

        try:
            command_bar = self.query_one("#command-bar", CommandBar)
            command_bar.add_class("visible")
            command_bar.fork_source_session = session
            command_bar.set_mode("fork_name")
            # Pre-fill with unique fork name
            input_widget = command_bar.query_one("#cmd-input", Input)
            default_name = command_bar._get_unique_agent_name(f"{session.name}-fork")
            input_widget.value = default_name
            command_bar.focus_input()
        except NoMatches:
            self.notify("Command bar not found", severity="error")

    def action_toggle_sleep(self) -> None:
        """Toggle sleep mode for the focused agent.

        Sleep mode marks an agent as 'asleep' (human doesn't want it to do anything).
        Sleeping agents are excluded from stats calculations.
        Press z again to wake the agent.

        Note: Cannot put a running agent to sleep (#158).
        """
        from ..status_constants import is_green_status
        focused = _get_focused_session(self)
        if not focused:
            self.notify("No agent focused", severity="warning")
            return

        session = focused.session

        if self._is_remote(session):
            if self._guard_remote(session):
                return
            new_asleep_state = not session.is_asleep
            result = self._sister_controller.set_sleep(
                session.source_url, session.source_api_key,
                session.name, asleep=new_asleep_state,
            )
            state_word = "asleep" if new_asleep_state else "awake"
            self._remote_notify(result, f"Agent '{session.name}' is now {state_word}")
            return

        # Only allow sleep toggle on root agents (not children)
        if session.parent_session_id is not None:
            self.notify("Sleep mode is only available for root agents", severity="warning")
            return

        new_asleep_state = not session.is_asleep

        # Prevent putting a running agent to sleep (#158)
        if new_asleep_state and is_green_status(focused.detected_status):
            self.notify("Cannot put a running agent to sleep", severity="warning")
            return

        # Prevent putting an agent with heartbeat enabled to sleep (#219)
        if new_asleep_state and session.heartbeat_enabled and not session.heartbeat_paused:
            self.notify("Cannot sleep agent with active heartbeat — disable heartbeat first", severity="warning")
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

    def action_toggle_heartbeat_pause(self) -> None:
        """Toggle heartbeat pause/resume for the focused agent (#265).

        Pauses an active heartbeat (keeps config) or resumes a paused one.
        Press P again to toggle back.
        """
        from ..settings import signal_activity

        focused = _get_focused_session(self)
        if not focused:
            self.notify("No agent focused", severity="warning")
            return

        session = focused.session

        if self._is_remote(session):
            if self._guard_remote(session):
                return
            if session.heartbeat_paused:
                result = self._sister_controller.resume_heartbeat(
                    session.source_url, session.source_api_key, session.name,
                )
                self._remote_notify(result, f"Heartbeat resumed for '{session.name}'")
            else:
                result = self._sister_controller.pause_heartbeat(
                    session.source_url, session.source_api_key, session.name,
                )
                self._remote_notify(result, f"Heartbeat paused for '{session.name}'")
            return

        # Only allow on root agents (not children)
        if session.parent_session_id is not None:
            self.notify("Heartbeat pause is only available for root agents", severity="warning")
            return

        if not session.heartbeat_enabled:
            self.notify("No heartbeat configured — press H to set up", severity="warning")
            return

        new_paused_state = not session.heartbeat_paused

        # Prevent resuming heartbeat on a sleeping agent (#265)
        if not new_paused_state and session.is_asleep:
            self.notify("Cannot resume heartbeat on sleeping agent — wake with z first", severity="warning")
            return

        # Update the session in the session manager
        update_kwargs = {"heartbeat_paused": new_paused_state}
        if not new_paused_state:
            # Resuming: clear last_heartbeat_time so next heartbeat is
            # computed from now, not from a stale timestamp (#392)
            update_kwargs["last_heartbeat_time"] = None
            session.last_heartbeat_time = None
        self.session_manager.update_session(session.id, **update_kwargs)

        # Update the local session object
        session.heartbeat_paused = new_paused_state

        # Wake daemon so it picks up the change immediately
        signal_activity(self.tmux_session)

        if new_paused_state:
            self.notify(f"Heartbeat paused for '{session.name}'", severity="information")
        else:
            self.notify(f"Heartbeat resumed for '{session.name}'", severity="information")

        # Force a refresh
        focused.refresh()

    def action_toggle_enhanced_context(self) -> None:
        """Toggle enhanced context hook for the focused agent.

        When enabled, the agent receives enhanced context (agent name, clock,
        uptime, presence) injected into every prompt via the UserPromptSubmit hook.
        Press ^T again to disable.
        """
        focused = _get_focused_session(self)
        if not focused:
            self.notify("No agent focused", severity="warning")
            return

        session = focused.session

        if self._is_remote(session):
            if self._guard_remote(session):
                return
            new_state = not session.enhanced_context_enabled
            result = self._sister_controller.set_enhanced_context(
                session.source_url, session.source_api_key,
                session.name, enabled=new_state,
            )
            state_word = "enabled" if new_state else "disabled"
            self._remote_notify(result, f"Enhanced context {state_word} for '{session.name}'")
            return

        new_state = not session.enhanced_context_enabled

        # Update the session in the session manager
        self.session_manager.update_session(session.id, enhanced_context_enabled=new_state)

        # Update the local session object
        session.enhanced_context_enabled = new_state

        if new_state:
            self.notify(f"Enhanced context enabled for '{session.name}'", severity="information")
        else:
            self.notify(f"Enhanced context disabled for '{session.name}'", severity="information")

        # Force a refresh
        focused.refresh()

    def action_toggle_hook_detection(self) -> None:
        """Toggle global detection mode between hooks and polling.

        Switches ALL agents between hook-based and polling-based status
        detection. The mode is persisted and shared with the monitor daemon.
        """
        from ..settings import write_detection_mode
        new_mode = "polling" if self.detector.mode == "hooks" else "hooks"
        if new_mode == "hooks":
            from ..claude_config import ClaudeConfigEditor
            if not ClaudeConfigEditor.are_overcode_hooks_installed():
                self.notify(
                    "Hooks not installed — run 'overcode hooks install' first",
                    severity="error",
                )
                return
        self.detector.mode = new_mode
        write_detection_mode(self.tmux_session, new_mode)
        self.notify(f"Detection mode: {new_mode} (all agents)", severity="information")

    def action_kill_focused(self) -> None:
        """Kill or clean up the currently focused agent/job (requires confirmation).

        Context-sensitive: if the agent is terminated/done, cleans it up
        (archives and removes from display). Otherwise kills it.
        In jobs mode, kills the focused job.
        """
        if getattr(self, 'tui_mode', 'agents') == 'jobs':
            self._action_kill_focused_job()
            return
        focused = _get_focused_session(self)
        if not focused:
            self.notify("No agent focused", severity="warning")
            return

        session = focused.session
        session_name = session.name

        if self._is_remote(session):
            if self._guard_remote(session):
                return
            self._confirm_double_press(
                "kill",
                f"Press x again to kill remote agent '{session_name}'",
                lambda: self._execute_remote_kill(session),
                session_name=session_name,
            )
            return

        session_id = session.id
        session_status = session.status

        # Terminated/done agents: clean up (archive + remove) instead of kill
        if session_status in ("terminated", "done"):
            self._confirm_double_press(
                "cleanup",
                f"Press x again to clean up '{session_name}'",
                lambda: self._execute_cleanup(focused, session_name, session_id),
                session_name=session_name,
            )
        else:
            self._confirm_double_press(
                "kill",
                f"Press x again to kill '{session_name}'",
                lambda: self._execute_kill(focused, session_name, session_id),
                session_name=session_name,
            )

    def _execute_remote_kill(self, session: "Session") -> None:
        """Kill a remote agent via the sister controller."""
        result = self._sister_controller.kill_agent(
            session.source_url, session.source_api_key, session.name,
        )
        self._remote_notify(result, f"Killed remote agent: {session.name}")

    def action_restart_focused(self) -> None:
        """Restart the currently focused agent (requires confirmation).

        Sends Ctrl-C to kill the current Claude process, then restarts it
        with the same configuration (directory, permissions).
        """
        focused = _get_focused_session(self)
        if not focused:
            self.notify("No agent focused", severity="warning")
            return

        session = focused.session
        session_name = session.name

        if self._is_remote(session):
            if self._guard_remote(session):
                return
            self._confirm_double_press(
                "restart",
                f"Press R again to restart remote agent '{session_name}'",
                lambda: self._execute_remote_restart(session),
                session_name=session_name,
            )
            return

        verb = "revive" if session.status in ("terminated", "done") else "restart"
        self._confirm_double_press(
            "restart",
            f"Press R again to {verb} '{session_name}'",
            lambda: self._execute_restart(focused),
            session_name=session_name,
        )

    def _execute_remote_restart(self, session: "Session") -> None:
        """Restart a remote agent via the sister controller."""
        result = self._sister_controller.restart_agent(
            session.source_url, session.source_api_key, session.name,
        )
        self._remote_notify(result, f"Restarted remote agent: {session.name}")

    def action_sync_to_main_and_clear(self) -> None:
        """Switch to main branch, pull, and clear agent context (requires confirmation).

        In jobs mode, clears completed jobs instead.

        This action:
        1. Runs git checkout main && git pull via Claude's bash command
        2. Sends /clear to reset the conversation context
        """
        if getattr(self, 'tui_mode', 'agents') == 'jobs':
            self._action_clear_completed_jobs()
            return
        focused = _get_focused_session(self)
        if not focused:
            self.notify("No agent focused", severity="warning")
            return

        session = focused.session

        if self._is_remote(session):
            if self._guard_remote(session):
                return
            # Sync requires multi-step tmux interaction — send as instruction
            self._confirm_double_press(
                "sync",
                f"Press c again to sync remote '{session.name}' to main",
                lambda: self._execute_remote_sync(session),
                session_name=session.name,
            )
            return

        session_name = session.name
        self._confirm_double_press(
            "sync",
            f"Press c again to sync '{session_name}' to main",
            lambda: self._execute_sync(focused),
            session_name=session_name,
        )

    def _execute_remote_sync(self, session: "Session") -> None:
        """Sync a remote agent to main via sister controller."""
        sync_text = "!git checkout main && git pull"
        result = self._sister_controller.send_instruction(
            session.source_url, session.source_api_key,
            session.name, text=sync_text,
        )
        if result.ok:
            # Follow up with /clear
            self._sister_controller.send_instruction(
                session.source_url, session.source_api_key,
                session.name, text="/clear",
            )
            self.notify(f"Synced remote '{session.name}' to main", severity="information")
        else:
            self.notify(f"Remote error: {result.error}", severity="error")

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
            # Clear PR number — agent is back on main, old PR is stale (#391)
            self.session_manager.update_session(
                session.id, pr_number=None, pr_branch=None
            )
        else:
            self.notify(f"Failed to send /clear to '{session_name}'", severity="error")

    def action_new_agent(self) -> None:
        """Prompt to create a new agent (local or remote).

        When sisters are configured, shows a host selection modal first.
        Otherwise goes straight to the directory step.
        """
        from ..tui_widgets import CommandBar

        try:
            command_bar = self.query_one("#command-bar", CommandBar)
            command_bar.add_class("visible")  # Must show the command bar first

            if self.has_sisters:
                # Show host selection modal (local + sisters)
                from ..config import get_sisters_config
                from ..tui_widgets.host_select_modal import HostSelectModal
                sister_names = [s["name"] for s in get_sisters_config()]
                try:
                    modal = self.query_one("#host-select-modal", HostSelectModal)
                    if hasattr(self, '_dialog_will_open'):
                        self._dialog_will_open()
                    modal.show(self.local_hostname, sister_names, self)
                except NoMatches:
                    # Fallback: go straight to directory step
                    command_bar.set_mode("new_agent_dir")
                    input_widget = command_bar.query_one("#cmd-input", Input)
                    input_widget.value = str(Path.cwd())
                    command_bar.focus_input()
            else:
                # No sisters: go straight to directory step
                command_bar.set_mode("new_agent_dir")
                input_widget = command_bar.query_one("#cmd-input", Input)
                input_widget.value = str(Path.cwd())
                command_bar.focus_input()
        except NoMatches:
            self.notify("Command bar not found", severity="error")

    def _open_command_bar(
        self,
        mode: str | None = None,
        get_prefill=None,
        fallback_prefill: str | None = None,
    ) -> None:
        """Open the command bar with optional mode and pre-fill.

        Handles the common pattern of showing the bar, targeting the focused
        session, pre-filling the input, and focusing it.

        Args:
            mode: Command bar mode to set (None keeps current mode)
            get_prefill: Optional callable(session) -> str to pre-fill input
            fallback_prefill: Pre-fill value when no agent is focused
        """
        from ..tui_widgets import CommandBar

        try:
            cmd_bar = self.query_one("#command-bar", CommandBar)
            cmd_bar.add_class("visible")

            # Use _get_focused_widget (our own index) not self.focused
            # (Textual's internal focus) which diverges during DOM reordering
            focused = self._get_focused_widget()
            if focused:
                cmd_bar.set_target(focused.session.name, session_id=focused.session.id)
                if get_prefill:
                    cmd_input = cmd_bar.query_one("#cmd-input", Input)
                    cmd_input.value = get_prefill(focused.session)
            elif not cmd_bar.target_session and self.sessions:
                cmd_bar.set_target(self.sessions[0].name, session_id=self.sessions[0].id)
                if fallback_prefill is not None:
                    cmd_input = cmd_bar.query_one("#cmd-input", Input)
                    cmd_input.value = fallback_prefill

            if mode:
                cmd_bar.set_mode(mode)

            cmd_input = cmd_bar.query_one("#cmd-input", Input)
            cmd_input.disabled = False
            cmd_input.focus()
        except NoMatches:
            pass

    def action_focus_command_bar(self) -> None:
        """Focus the command bar for input."""
        self._open_command_bar()

    def action_focus_standing_orders(self) -> None:
        """Focus the command bar for editing standing orders."""
        self._open_command_bar("standing_orders", lambda s: s.standing_instructions or "")

    def action_focus_human_annotation(self) -> None:
        """Focus input for editing human annotation (#74)."""
        self._open_command_bar("annotation", lambda s: s.human_annotation or "")

    def action_edit_agent_value(self) -> None:
        """Focus the command bar for editing agent value (#61)."""
        self._open_command_bar("value", lambda s: str(s.agent_value), fallback_prefill="1000")

    def action_edit_cost_budget(self) -> None:
        """Focus the command bar for editing cost budget (#173)."""
        self._open_command_bar(
            "cost_budget",
            lambda s: str(s.cost_budget_usd) if s.cost_budget_usd > 0 else "",
        )

    def action_configure_heartbeat(self) -> None:
        """Open command bar for heartbeat configuration (#171)."""
        self._open_command_bar(
            "heartbeat_freq",
            lambda s: str(s.heartbeat_frequency_seconds) if s.heartbeat_enabled else "300",
        )

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
        # Get active sessions (exclude terminated and sleeping)
        active_sessions = [
            s for s in self.sessions
            if s.status != "terminated" and not s.is_asleep
        ]
        if not active_sessions:
            self.notify("No active sessions to prepare (sleeping sessions excluded)", severity="warning")
            return

        count = len(active_sessions)
        self._confirm_double_press(
            "transport",
            f"Press H again to send handover instructions to {count} agent(s)",
            lambda: self._execute_transport_all(active_sessions),
        )

    def _execute_transport_all(self, sessions: List["Session"]) -> None:
        """Execute transport/handover instructions to all sessions."""
        from ..launcher import ClaudeLauncher

        from ..standing_instructions import HANDOVER_INSTRUCTION
        handover_instruction = HANDOVER_INSTRUCTION

        launcher = ClaudeLauncher(
            tmux_session=self.tmux_session,
            session_manager=self.session_manager
        )

        success_count = 0
        for session in sessions:
            if self._is_remote(session):
                if not self._sister_reachable(session):
                    continue
                # Send via sister controller for remote agents
                result = self._sister_controller.send_instruction(
                    session.source_url, session.source_api_key,
                    session.name, text=handover_instruction,
                )
                if result.ok:
                    success_count += 1
            else:
                if launcher.send_to_session_by_id(session.id, handover_instruction):
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
