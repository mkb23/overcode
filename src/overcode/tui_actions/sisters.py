"""
Sister integration for the TUI (#245): remote agents from other machines.

Polling sisters (every 10 s, the focused remote agent every 1.5 s),
merging their agents into the list, the sister view (zoom + preview for a
remote agent without SSH), optimistic updates after a remote control call,
SSH provisioning and the sister selection dialog (#323).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, List

from textual import work
from textual.css.query import NoMatches

from ..sister_poller import SisterState
from ..tui_widgets import SessionSummary, SisterSelectionModal
from ..worker_guard import single_flight, worker_cancelled

if TYPE_CHECKING:  # pragma: no cover
    from ..session_manager import Session


class SistersMixin:
    """Mixin providing sister (remote agent) integration for SupervisorTUI."""

    def _enter_sister_view(self) -> None:
        """Zoom TUI pane and show preview for viewing a sister agent.

        Called automatically when navigating to a remote agent without SSH. The preview pane shows the sister's polled
        pane_content. The bottom terminal pane is hidden via tmux zoom.
        """
        if self._sister_zoom_active:
            return
        self._sister_zoom_active = True
        # Zoom the TUI pane (same as dialog zoom)
        self._dialog_will_open()
        # Show preview pane for sister content
        self.preview_visible = True

    def _exit_sister_view(self) -> None:
        """Restore the split layout when navigating back to a local agent.

        Reverses _enter_sister_view(): hides preview pane and
        unzooms the TUI pane to reveal the bottom terminal pane.
        """
        if not self._sister_zoom_active:
            return
        self._sister_zoom_active = False
        # Hide the preview pane (local agents show in the bottom pane)
        self.preview_visible = False
        # Unzoom — but only if no dialog is holding the zoom open
        if not self._any_dialog_visible():
            self.split.unzoom()

    def _apply_remote(self, widget: "SessionSummary") -> bool:
        """A sister's agent from its polled data; a new stretch needing input notifies.

        A 0.6 sister publishes input_needed_since in its forwarded state;
        the poller fills it in for an older sister's waiting_user agent.
        The first sight of an agent never notifies (as the engine's bell
        never rings on first sight); a stretch that starts later does.
        """
        changed = widget.apply_remote(self._visited_here.get(widget.session.id))
        sid = widget.session.id
        since = (widget.session.remote_daemon_state or {}).get("input_needed_since")
        if sid in self._remote_stretch_seen and since is not None \
                and since != self._remote_stretch_seen[sid] and widget.is_unvisited_stalled:
            self._notifier.queue(widget.session.name, widget.current_activity)
            self._notifier.flush()
        self._remote_stretch_seen[sid] = since
        return changed

    def _resolve_remote_parents(self) -> None:
        """Resolve parent_session_id for remote children whose parents are local or on other sisters.

        After merging local + remote sessions, remote children may have parent_name set
        but parent_session_id unset (because the parent wasn't available when the sister
        was polled). This function fixes cross-machine parent relationships by matching
        parent_name to session names in the combined list.

        Fixes flipping behavior where remote children appeared/disappeared from their
        parent's subtree (#245).
        """
        # Build name -> session mapping for fast lookup
        name_to_session = {s.name: s for s in self.sessions}

        for session in self.sessions:
            # Skip if already has parent_session_id set
            if session.parent_session_id:
                continue
            # Only process remote sessions that don't have a parent yet
            if not getattr(session, 'is_remote', False):
                continue

            # Extract parent_name from remote_daemon_state (contains parent_name from the sister)
            parent_name = None
            remote_daemon = getattr(session, 'remote_daemon_state', None)
            if remote_daemon and isinstance(remote_daemon, dict):
                parent_name = remote_daemon.get('parent_name')

            # Try to resolve parent by name in the merged session list
            if parent_name and parent_name in name_to_session:
                parent_session = name_to_session[parent_name]
                session.parent_session_id = parent_session.id

    def _poll_sisters(self) -> None:
        """Kick off sister polling in background thread."""
        self._poll_sisters_async()

    @work(thread=True, group="sister_poll")
    @single_flight("sister_poll")
    def _poll_sisters_async(self) -> None:
        """Fetch remote sessions from all sisters."""
        remote = self._sister_poller.poll_all()
        self.call_from_thread(self._apply_remote_sessions, remote)

    def _visible_remote_sessions(self) -> List[Session]:
        """Return remote sessions excluding disabled sisters (#323)."""
        disabled = self._prefs.disabled_sisters
        if not disabled:
            return list(self._remote_sessions)
        disabled_urls = {
            s.url for s in self._sister_poller.get_sister_states()
            if s.name in disabled
        }
        return [s for s in self._remote_sessions if s.source_url not in disabled_urls]

    def _apply_remote_sessions(self, remote_sessions: List[Session]) -> None:
        """Store remote sessions and rebuild widget list."""
        self._mark_event("apply_sisters_start")
        had_remote = len(self._remote_sessions) > 0
        self._remote_sessions = remote_sessions
        self.refresh_sessions()
        # On first remote arrival, kick off timeline update so remote
        # agent timelines appear immediately instead of waiting for the
        # 30s timeline cycle (#296).
        if not had_remote and remote_sessions:
            self.update_timeline()
        self._mark_event("apply_sisters_end")

    def _poll_focused_sister(self) -> None:
        """Fast-poll the focused remote agent (1.5s) for responsive preview."""
        focused = self._get_focused_widget()
        if focused is None or not focused.session.is_remote:
            return
        session = focused.session
        if not session.source_url or not session.name:
            return
        self._poll_focused_sister_async(
            session.source_url, session.source_api_key, session.name, session.id
        )

    @work(thread=True, group="focused_sister_poll")
    @single_flight("focused_sister_poll")
    def _poll_focused_sister_async(
        self, source_url: str, source_api_key: str, agent_name: str, session_id: str
    ) -> None:
        """Fetch single remote agent status in background thread."""
        updated = self._sister_poller.poll_single_agent(source_url, source_api_key, agent_name)
        if updated is not None:
            self.call_from_thread(self._apply_focused_sister, updated, session_id)

    def _apply_focused_sister(self, updated_session: "Session", session_id: str) -> None:
        """Apply fast-polled remote session data to the matching widget."""
        # Update the session in _remote_sessions so the fast path picks it up
        for i, rs in enumerate(self._remote_sessions):
            if rs.id == session_id:
                self._remote_sessions[i] = updated_session
                break

        # Update the widget directly for immediate feedback
        for widget in self.query(SessionSummary):
            if widget.session.id == session_id:
                widget.session = updated_session
                # Sync pr_number from session (propagates both detection and clearing)
                widget.pr_number = updated_session.pr_number
                self._apply_remote(widget)
                widget.refresh()
                break

        # Refresh preview pane if in list_preview mode
        if self.preview_visible:
            self._update_preview()

    def _optimistic_update_remote(self, session_id: str, **fields) -> None:
        """Optimistically update a remote session's local copy for instant UI feedback.

        After a successful remote API call, update the in-memory session and
        widget immediately instead of waiting for the next polling cycle (#305).
        """
        from dataclasses import replace
        for i, rs in enumerate(self._remote_sessions):
            if rs.id == session_id:
                self._remote_sessions[i] = replace(rs, **fields)
                break
        for widget in self.query(SessionSummary):
            if widget.session.id == session_id:
                widget.session = replace(widget.session, **fields)
                widget.refresh()
                break
        if self.preview_visible:
            self._update_preview()

    def _stale_banner_for(self, session) -> str:
        """Build a "sister unreachable" banner for a remote session (#385).

        Returns an empty string for local sessions or when the source sister
        is still reachable — the preview pane then renders normally.
        """
        if not getattr(session, 'is_remote', False):
            return ""
        source_url = getattr(session, 'source_url', '')
        if not source_url:
            return ""
        for sister in self._sister_poller.get_sister_states():
            if sister.url != source_url:
                continue
            if sister.reachable:
                return ""
            age = self._sister_last_fetch_age(sister)
            host = sister.name or "sister"
            return f"{host} unreachable — last updated {age}"
        return ""

    @staticmethod
    def _sister_last_fetch_age(sister) -> str:
        """Format how long ago the sister last succeeded, for the stale banner."""
        last_fetch = getattr(sister, 'last_fetch', None)
        if not last_fetch:
            return "never"
        try:
            ts = datetime.fromisoformat(last_fetch)
        except (TypeError, ValueError):
            return "unknown"
        secs = (datetime.now() - ts).total_seconds()
        if secs < 60:
            return f"{int(secs)}s ago"
        if secs < 3600:
            return f"{int(secs / 60)}m ago"
        return f"{int(secs / 3600)}h ago"

    @work(thread=True, group="ssh_provision")
    @single_flight("ssh_provision")
    def _provision_ssh_sisters(self) -> None:
        """Provision SSH-configured sisters in a background thread.

        Checks each sister with SSH configured to ensure overcode is running
        and version-matched. Bootstraps or upgrades via uvx as needed.
        """
        from ..ssh_provisioner import ensure_remote_ready
        from ..config import get_web_host

        sisters_with_ssh: list[SisterState] = [
            s for s in self._sister_poller.get_sister_states() if s.ssh
        ]
        if not sisters_with_ssh:
            return

        for sister in sisters_with_ssh:
            if worker_cancelled():
                return
            try:
                # Extract port from sister URL
                from urllib.parse import urlparse
                parsed = urlparse(sister.url)
                port = parsed.port or 8080
                host = get_web_host()

                result = ensure_remote_ready(
                    ssh_target=sister.ssh,
                    sister_url=sister.url,
                    api_key=sister.api_key,
                    port=port,
                    host=host,
                )
                if result.ok:
                    if result.action == "bootstrapped":
                        self.call_from_thread(
                            self.notify,
                            f"[b]{sister.name}[/b]: overcode started via uvx",
                            severity="information",
                        )
                    elif result.action == "upgraded":
                        self.call_from_thread(
                            self.notify,
                            f"[b]{sister.name}[/b]: upgraded to v{result.remote_version}",
                            severity="information",
                        )
                else:
                    self.call_from_thread(
                        self.notify,
                        f"[b]{sister.name}[/b]: provision failed — {result.error}",
                        severity="warning",
                    )
            except Exception as e:
                self.call_from_thread(
                    self.notify,
                    f"[b]{sister.name}[/b]: SSH provision error — {e}",
                    severity="warning",
                )

    def action_open_sister_selection(self) -> None:
        """Open the sister management modal (#323)."""
        sisters = [
            {
                "name": s.name, "url": s.url, "api_key": s.api_key,
                "version": s.version, "reachable": s.reachable,
                "daemon_running": s.daemon_running,
                "green_agents": s.green_agents, "total_agents": s.total_agents,
                "last_error": s.last_error,
            }
            for s in self._sister_poller.get_sister_states()
        ]
        if not sisters:
            self.notify("No sisters configured", severity="warning")
            return
        try:
            modal = self.query_one("#sister-selection-modal", SisterSelectionModal)
            self._dialog_will_open()
            modal.show(sisters, self._prefs.disabled_sisters, self)
        except NoMatches:
            pass

    def on_sister_selection_modal_selection_changed(self, message: SisterSelectionModal.SelectionChanged) -> None:
        """Handle sister visibility changes from modal (#323)."""
        self._prefs.disabled_sisters = message.disabled_sisters
        self._save_prefs()
        # Re-filter remote sessions and rebuild widget list
        self.refresh_sessions()
        count = len(message.disabled_sisters)
        if count:
            self.notify(f"{count} sister(s) hidden", severity="information")
        else:
            self.notify("All sisters visible", severity="information")
        self._dialog_did_close()

    def on_sister_selection_modal_cancelled(self, message: SisterSelectionModal.Cancelled) -> None:
        """Handle sister selection modal cancellation (#323)."""
        self._dialog_did_close()

    def on_sister_selection_modal_restart_daemon(self, message: SisterSelectionModal.RestartDaemon) -> None:
        """Handle daemon restart request from sister management modal."""
        self.notify(f"Restarting daemon on {message.sister_name}...", severity="information")
        result = self._sister_controller.restart_monitor(message.sister_url, message.api_key)
        if result.ok:
            self.notify(f"Daemon restarted on {message.sister_name}", severity="information")
        else:
            self.notify(f"Restart failed: {result.error}", severity="error")
