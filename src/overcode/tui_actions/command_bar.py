"""
Command bar handlers for the TUI: what the bar sends to an agent.

Instructions, standing orders, value, budget, annotation, heartbeat and
fork, each for a local agent (session manager / launcher) or a remote one
(through the SisterController).
"""

from __future__ import annotations

from textual.css.query import NoMatches
from textual.widgets import Input, TextArea

from ..launcher import AgentLauncher
from ..settings import signal_activity
from ..tui_helpers import format_duration
from ..tui_widgets import CommandBar, SessionSummary


class CommandBarMixin:
    """Mixin handling the command bar's messages for SupervisorTUI."""

    def _find_any_session_by_name(self, name: str):
        """Find a session by name, including remote sessions.

        Deprecated: prefer _find_any_session_by_id() for unambiguous routing.
        """
        # Check local sessions first
        session = self.session_manager.resolve_session_name(name)
        if session:
            return session
        # Check remote sessions in self.sessions
        for s in self.sessions:
            if s.name == name and getattr(s, 'is_remote', False):
                return s
        return None

    def _find_any_session_by_id(self, session_id: str):
        """Find a session by ID, including remote sessions."""
        if not session_id:
            return None
        session = self.session_manager.get_session(session_id)
        if session:
            return session
        for s in self.sessions:
            if s.id == session_id:
                return s
        return None

    def _resolve_command_bar_session(self, session_id: str, session_name: str):
        """Resolve session from command bar message, preferring ID over name."""
        if session_id:
            return self._find_any_session_by_id(session_id)
        return self._find_any_session_by_name(session_name)

    def on_command_bar_send_requested(self, message: CommandBar.SendRequested) -> None:
        """Handle send request from command bar."""
        session = self._resolve_command_bar_session(message.session_id, message.session_name)

        # Remote agent — dispatch through sister controller
        if session and getattr(session, 'is_remote', False):
            if self._guard_remote(session):
                return
            result = self._sister_controller.send_instruction(
                session.source_url, session.source_api_key,
                session.name, text=message.text,
            )
            if result.ok:
                self._record_instruction(message.text, message.session_name)
                self.notify(f"Sent to remote agent {message.session_name}")
            else:
                self.notify(f"Remote error: {result.error}", severity="error")
            return

        # Local agent — auto-wake sleeping agent if needed (#168)
        if session and session.is_asleep:
            self.session_manager.update_session(session.id, is_asleep=False)
            # Update widget display immediately
            for widget in self.query(SessionSummary):
                if widget.session.id == session.id:
                    widget.session.is_asleep = False
                    if widget.detected_status == "asleep":
                        widget.detected_status = "running"
                    widget.refresh()
                    break
            self.notify(f"Woke agent '{message.session_name}' to send command", severity="information")

        launcher = AgentLauncher(
            tmux_session=self.tmux_session,
            session_manager=self.session_manager
        )
        success = launcher.send_to_session_by_id(session.id, message.text) if session else False
        if success:
            self._record_instruction(message.text, message.session_name)
            self.notify(f"Sent to {message.session_name}")
        else:
            self.notify(f"Failed to send to {message.session_name}", severity="error")

    def on_command_bar_standing_order_requested(self, message: CommandBar.StandingOrderRequested) -> None:
        """Handle standing order request from command bar."""
        session = self._resolve_command_bar_session(message.session_id, message.session_name)
        if not session:
            self.notify(f"Session '{message.session_name}' not found", severity="error")
            return

        if getattr(session, 'is_remote', False):
            if self._guard_remote(session):
                return
            if message.text:
                result = self._sister_controller.set_standing_orders(
                    session.source_url, session.source_api_key,
                    session.name, text=message.text,
                )
            else:
                result = self._sister_controller.clear_standing_orders(
                    session.source_url, session.source_api_key, session.name,
                )
            if result.ok:
                action = "set" if message.text else "cleared"
                self.notify(f"Standing order {action} for remote {message.session_name}")
                self._optimistic_update_remote(session.id, standing_instructions=message.text)
            else:
                self.notify(f"Remote error: {result.error}", severity="error")
            return

        self.session_manager.set_standing_instructions(session.id, message.text)
        if message.text:
            self.notify(f"Standing order set for {message.session_name}")
        else:
            self.notify(f"Standing order cleared for {message.session_name}")
        self.refresh_sessions()

    def on_command_bar_value_updated(self, message: CommandBar.ValueUpdated) -> None:
        """Handle agent value update from command bar (#61)."""
        session = self._resolve_command_bar_session(message.session_id, message.session_name)
        if not session:
            self.notify(f"Session '{message.session_name}' not found", severity="error")
            return

        if getattr(session, 'is_remote', False):
            if self._guard_remote(session):
                return
            result = self._sister_controller.set_value(
                session.source_url, session.source_api_key,
                session.name, value=message.value,
            )
            if result.ok:
                self.notify(f"Value set to {message.value} for remote {message.session_name}")
                self._optimistic_update_remote(session.id, agent_value=message.value)
            else:
                self.notify(f"Remote error: {result.error}", severity="error")
            return

        self.session_manager.set_agent_value(session.id, message.value)
        self.notify(f"Value set to {message.value} for {message.session_name}")
        self.refresh_sessions()

    def on_command_bar_budget_updated(self, message: CommandBar.BudgetUpdated) -> None:
        """Handle cost budget update from command bar (#173)."""
        session = self._resolve_command_bar_session(message.session_id, message.session_name)
        if not session:
            self.notify(f"Session '{message.session_name}' not found", severity="error")
            return

        if getattr(session, 'is_remote', False):
            if self._guard_remote(session):
                return
            result = self._sister_controller.set_budget(
                session.source_url, session.source_api_key,
                session.name, usd=message.budget_usd,
            )
            if result.ok:
                if message.budget_usd > 0:
                    self.notify(f"Budget set to ${message.budget_usd:.2f} for remote {message.session_name}")
                else:
                    self.notify(f"Budget cleared for remote {message.session_name}")
                self._optimistic_update_remote(session.id, cost_budget_usd=message.budget_usd)
            else:
                self.notify(f"Remote error: {result.error}", severity="error")
            return

        self.session_manager.set_cost_budget(session.id, message.budget_usd)
        if message.budget_usd > 0:
            self.notify(f"Budget set to ${message.budget_usd:.2f} for {message.session_name}")
        else:
            self.notify(f"Budget cleared for {message.session_name}")
        self.refresh_sessions()

    def on_command_bar_annotation_updated(self, message: CommandBar.AnnotationUpdated) -> None:
        """Handle human annotation update from command bar (#74)."""
        session = self._resolve_command_bar_session(message.session_id, message.session_name)
        if not session:
            self.notify(f"Session '{message.session_name}' not found", severity="error")
            return

        if getattr(session, 'is_remote', False):
            if self._guard_remote(session):
                return
            result = self._sister_controller.set_annotation(
                session.source_url, session.source_api_key,
                session.name, text=message.annotation,
            )
            if result.ok:
                action = "set" if message.annotation else "cleared"
                self.notify(f"Annotation {action} for remote {message.session_name}")
                self._optimistic_update_remote(session.id, human_annotation=message.annotation)
            else:
                self.notify(f"Remote error: {result.error}", severity="error")
            return

        self.session_manager.set_human_annotation(session.id, message.annotation)
        if message.annotation:
            self.notify(f"Annotation set for {message.session_name}")
        else:
            self.notify(f"Annotation cleared for {message.session_name}")
        self.refresh_sessions()

    def on_command_bar_heartbeat_updated(self, message: CommandBar.HeartbeatUpdated) -> None:
        """Handle heartbeat configuration update from command bar (#171)."""
        session = self._resolve_command_bar_session(message.session_id, message.session_name)
        if not session:
            self.notify(f"Session not found: {message.session_name}", severity="error")
            return

        if getattr(session, 'is_remote', False):
            if self._guard_remote(session):
                return
            result = self._sister_controller.configure_heartbeat(
                session.source_url, session.source_api_key,
                session.name,
                enabled=message.enabled,
                frequency=str(message.frequency),
                instruction=message.instruction,
            )
            if result.ok:
                if message.enabled:
                    freq_str = format_duration(message.frequency)
                    self.notify(f"Heartbeat enabled: every {freq_str} (remote)", severity="information")
                else:
                    self.notify("Heartbeat disabled (remote)", severity="information")
                self._optimistic_update_remote(
                    session.id,
                    heartbeat_enabled=message.enabled,
                    heartbeat_frequency_seconds=message.frequency,
                    heartbeat_instruction=message.instruction,
                )
            else:
                self.notify(f"Remote error: {result.error}", severity="error")
            return

        self.session_manager.update_session(
            session.id,
            heartbeat_enabled=message.enabled,
            heartbeat_frequency_seconds=message.frequency,
            heartbeat_instruction=message.instruction,
        )

        # Wake daemon so status updates immediately (#212)
        signal_activity(self.tmux_session)

        if message.enabled:
            freq_str = format_duration(message.frequency)
            self.notify(f"Heartbeat enabled: every {freq_str}", severity="information")
        else:
            self.notify("Heartbeat disabled", severity="information")

        # Refresh session list to show updated heartbeat config
        self.refresh_sessions()

    def on_command_bar_clear_requested(self, message: CommandBar.ClearRequested) -> None:
        """Handle clear request - hide and unfocus command bar."""
        try:
            # Disable and hide the command bar
            cmd_bar = self.query_one("#command-bar", CommandBar)
            target_session_id = cmd_bar.target_session_id  # Remember before disabling
            target_session_name = cmd_bar.target_session  # Fallback for name match
            cmd_bar.query_one("#cmd-input", Input).disabled = True
            cmd_bar.query_one("#cmd-textarea", TextArea).disabled = True
            cmd_bar.remove_class("visible")

            # Focus the targeted session (not first session) to keep preview on it
            if self.sessions:
                widgets = self._get_widgets_in_session_order()
                if widgets:
                    # Find widget matching target session by ID, fall back to name
                    target_widget = None
                    for i, w in enumerate(widgets):
                        if target_session_id and w.session.id == target_session_id:
                            target_widget = w
                            self._user_navigated = True
                            self.focused_session_index = i
                            break
                        elif not target_session_id and w.session.name == target_session_name:
                            target_widget = w
                            self._user_navigated = True
                            self.focused_session_index = i
                            break
                    if target_widget:
                        target_widget.focus()
                    else:
                        # The target went away: back to the chosen agent, by id
                        self._restore_focus_in_update(self._chosen_session_id(), True)
                        widgets = self._get_widgets_in_session_order()
                        if widgets:
                            widgets[min(self.focused_session_index, len(widgets) - 1)].focus()
                    if self.preview_visible:
                        self._update_preview()
        except NoMatches:
            pass

    def on_command_bar_fork_requested(self, message: CommandBar.ForkRequested) -> None:
        """Handle fork agent request (#347)."""
        source_session = message.source_session
        fork_name = message.fork_name

        # Validate name
        if not fork_name or len(fork_name) > 50:
            self.notify("Invalid fork name", severity="error")
            return
        if ' ' in fork_name:
            self.notify("Fork name cannot contain spaces", severity="error")
            return

        # Remote agents: fork via sister controller (#368)
        if getattr(source_session, 'is_remote', False):
            try:
                result = self._sister_controller.fork_agent(
                    source_session.source_url,
                    source_session.source_api_key,
                    source_session.name,
                    fork_name,
                )
                if result.ok:
                    self.notify(f"Forked remote '{source_session.name}' → '{fork_name}'", severity="information")
                    self.refresh_sessions()
                else:
                    self.notify(f"Remote fork failed: {result.error}", severity="error")
            except Exception as e:
                self.notify(f"Failed to fork remote agent: {e}", severity="error")
            return

        from ..exceptions import LaunchError
        launcher = AgentLauncher(
            tmux_session=self.tmux_session,
            session_manager=self.session_manager
        )

        try:
            launcher.launch_fork(
                name=fork_name,
                source_session=source_session,
            )
            self.notify(f"Forked '{source_session.name}' → '{fork_name}'", severity="information")
            self.refresh_sessions()
        except LaunchError as e:
            self.notify(str(e), severity="error")
        except Exception as e:
            self.notify(f"Failed to fork: {e}", severity="error")

    def on_command_bar_worktree_requested(self, message: CommandBar.WorktreeRequested) -> None:
        """A fresh agent in a new worktree: off the UI thread (git checks out every file)."""
        source_session = message.source_session
        name = message.name
        if source_session is None:
            return
        self.notify(f"Making a worktree for '{name}'…", severity="information")

        def work() -> None:
            from ..exceptions import LaunchError
            launcher = AgentLauncher(
                tmux_session=self.tmux_session,
                session_manager=self.session_manager,
            )
            try:
                _, worktree = launcher.launch_in_worktree(name, source_session)
            except LaunchError as e:
                self.call_from_thread(self.notify, str(e), severity="error")
                return
            except Exception as e:
                self.call_from_thread(self.notify, f"Failed to create agent: {e}", severity="error")
                return
            self.call_from_thread(
                self.notify,
                f"Created '{name}' on branch {worktree.branch} in {worktree.path}",
                severity="information",
            )
            self.call_from_thread(self.refresh_sessions)

        self.run_worker(work, thread=True, group="worktree_launch", exclusive=False)
