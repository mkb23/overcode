"""
Daemon action methods for TUI.

Handles Monitor Daemon, Supervisor Daemon, and Web Server controls.
"""

import sys
from typing import TYPE_CHECKING

from textual.css.query import NoMatches

if TYPE_CHECKING:
    from ..tui_widgets import DaemonPanel


def _log_to_daemon_panel(tui, message: str) -> None:
    """Log a message to the daemon panel if it exists."""
    from ..tui_widgets import DaemonPanel
    try:
        panel = tui.query_one("#daemon-panel", DaemonPanel)
        panel.log_lines.append(message)
    except NoMatches:
        pass


class DaemonActionsMixin:
    """Mixin providing daemon control actions for SupervisorTUI."""

    def action_toggle_daemon(self) -> None:
        """Toggle daemon panel visibility (like timeline)."""
        from ..tui_widgets import DaemonPanel
        from .view import _toggle_widget
        _toggle_widget(
            self, "daemon-panel", DaemonPanel, "daemon_panel_visible", "Daemon panel",
            on_show=lambda w: w._refresh_logs(),
        )

    def action_toggle_tui_log(self) -> None:
        """Toggle TUI diagnostic log panel visibility."""
        from ..tui_widgets import TuiLogPanel
        from .view import _toggle_widget
        _toggle_widget(
            self, "tui-log-panel", TuiLogPanel, "tui_log_panel_visible", "TUI log panel",
            on_show=lambda w: w._refresh_logs(),
        )

    def action_supervisor_start(self) -> None:
        """Start the Supervisor Daemon (requires double-press confirmation)."""
        self._confirm_double_press(
            "supervisor_start",
            "Press [ again to start Supervisor Daemon",
            self._do_supervisor_start,
        )

    def _do_supervisor_start(self) -> None:
        """Actually start the Supervisor Daemon."""
        from ..monitor_daemon import is_monitor_daemon_running
        from ..supervisor_daemon import is_supervisor_daemon_running
        import time

        # Ensure Monitor Daemon is running first (Supervisor depends on it)
        if not is_monitor_daemon_running(self.tmux_session):
            self._ensure_monitor_daemon()
            time.sleep(1.0)

        if is_supervisor_daemon_running(self.tmux_session):
            self.notify("Supervisor Daemon already running", severity="warning")
            return

        _log_to_daemon_panel(self, ">>> Starting Supervisor Daemon...")

        from ..pid_utils import spawn_daemon
        pid = spawn_daemon([
            sys.executable, "-m", "overcode.supervisor_daemon",
            "--session", self.tmux_session,
        ])
        if pid:
            self.notify("Started Supervisor Daemon", severity="information")
            self.set_timer(1.0, self.update_daemon_status)
        else:
            self.notify("Failed to start Supervisor Daemon", severity="error")

    def action_supervisor_stop(self) -> None:
        """Stop the Supervisor Daemon (requires double-press confirmation)."""
        self._confirm_double_press(
            "supervisor_stop",
            "Press ] again to stop Supervisor Daemon",
            self._do_supervisor_stop,
        )

    def _do_supervisor_stop(self) -> None:
        """Actually stop the Supervisor Daemon."""
        from ..supervisor_daemon import is_supervisor_daemon_running, stop_supervisor_daemon

        if not is_supervisor_daemon_running(self.tmux_session):
            self.notify("Supervisor Daemon not running", severity="warning")
            return

        if stop_supervisor_daemon(self.tmux_session):
            self.notify("Stopped Supervisor Daemon", severity="information")
            _log_to_daemon_panel(self, ">>> Supervisor Daemon stopped")
        else:
            self.notify("Failed to stop Supervisor Daemon", severity="error")

        self.update_daemon_status()

    def action_toggle_summarizer(self) -> None:
        """Toggle the AI Summarizer on/off."""
        from ..summarizer_client import SummarizerClient
        from ..tui_widgets import SessionSummary

        # Check if summarizer is available (OPENAI_API_KEY set)
        if not SummarizerClient.is_available():
            self.notify("AI Summarizer unavailable - set OPENAI_API_KEY", severity="warning")
            return

        # Toggle the state
        self._summarizer.config.enabled = not self._summarizer.config.enabled

        if self._summarizer.config.enabled:
            # Enable: create client if needed
            if not self._summarizer._client:
                self._summarizer._client = SummarizerClient()
            self.notify("AI Summarizer enabled", severity="information")
            # Update all widgets to show summarizer is enabled
            for widget in self.query(SessionSummary):
                widget.summarizer_enabled = True
            # Trigger an immediate update
            self._update_summaries_async()
        else:
            # Disable: close client to release resources
            if self._summarizer._client:
                self._summarizer._client.close()
                self._summarizer._client = None
            # Clear cached summaries
            self._summaries = {}
            # Update all widgets to clear summaries and show disabled state
            for widget in self.query(SessionSummary):
                widget.ai_summary_short = ""
                widget.ai_summary_context = ""
                widget.summarizer_enabled = False
                widget.refresh()
            self.notify("AI Summarizer disabled", severity="information")

        # Refresh status bar
        self.update_daemon_status()

    def action_monitor_restart(self) -> None:
        """Restart the Monitor Daemon (handles metrics/state tracking)."""
        from ..monitor_daemon import is_monitor_daemon_running, stop_monitor_daemon

        _log_to_daemon_panel(self, ">>> Restarting Monitor Daemon...")

        # Stop if running
        if is_monitor_daemon_running(self.tmux_session):
            stop_monitor_daemon(self.tmux_session)
            # Use non-blocking timer to wait before starting
            # (avoids blocking the event loop which caused double-press issue)
            self.set_timer(0.5, self._start_monitor_daemon)
        else:
            # Not running, start immediately
            self._start_monitor_daemon()

    def _start_monitor_daemon(self) -> None:
        """Start the monitor daemon (called by action_monitor_restart)."""
        from ..pid_utils import spawn_daemon

        pid = spawn_daemon([
            sys.executable, "-m", "overcode.monitor_daemon",
            "--session", self.tmux_session,
        ])
        if pid:
            self.notify("Monitor Daemon restarted", severity="information")
            _log_to_daemon_panel(self, ">>> Monitor Daemon restarted")
            self.set_timer(1.0, self.update_daemon_status)
        else:
            self.notify("Failed to restart Monitor Daemon", severity="error")

    def action_toggle_web_server(self) -> None:
        """Toggle the web analytics dashboard server on/off."""
        from ..web_server import toggle_web_server, get_web_server_url

        is_running, msg = toggle_web_server(self.tmux_session)

        if is_running:
            url = get_web_server_url(self.tmux_session)
            self.notify(f"Web server: {url}", severity="information")
            _log_to_daemon_panel(self, f">>> Web server started: {url}")
        else:
            self.notify(f"Web server: {msg}", severity="information")
            _log_to_daemon_panel(self, f">>> Web server: {msg}")

        self.update_daemon_status()
