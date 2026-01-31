"""
Daemon action methods for TUI.

Handles Monitor Daemon, Supervisor Daemon, and Web Server controls.
"""

import subprocess
import sys
from typing import TYPE_CHECKING

from textual.css.query import NoMatches

if TYPE_CHECKING:
    from ..tui_widgets import DaemonPanel


class DaemonActionsMixin:
    """Mixin providing daemon control actions for SupervisorTUI."""

    def action_toggle_daemon(self) -> None:
        """Toggle daemon panel visibility (like timeline)."""
        from ..tui_widgets import DaemonPanel
        try:
            daemon_panel = self.query_one("#daemon-panel", DaemonPanel)
            daemon_panel.display = not daemon_panel.display
            if daemon_panel.display:
                # Force immediate refresh when becoming visible
                daemon_panel._refresh_logs()
            # Save preference
            self._prefs.daemon_panel_visible = daemon_panel.display
            self._save_prefs()
            state = "shown" if daemon_panel.display else "hidden"
            self.notify(f"Daemon panel {state}", severity="information")
        except NoMatches:
            pass

    def action_supervisor_start(self) -> None:
        """Start the Supervisor Daemon (handles Claude orchestration)."""
        from ..monitor_daemon import is_monitor_daemon_running
        from ..supervisor_daemon import is_supervisor_daemon_running
        from ..tui_widgets import DaemonPanel
        import time

        # Ensure Monitor Daemon is running first (Supervisor depends on it)
        if not is_monitor_daemon_running(self.tmux_session):
            self._ensure_monitor_daemon()
            time.sleep(1.0)

        if is_supervisor_daemon_running(self.tmux_session):
            self.notify("Supervisor Daemon already running", severity="warning")
            return

        try:
            panel = self.query_one("#daemon-panel", DaemonPanel)
            panel.log_lines.append(">>> Starting Supervisor Daemon...")
        except NoMatches:
            pass

        try:
            subprocess.Popen(
                [sys.executable, "-m", "overcode.supervisor_daemon",
                 "--session", self.tmux_session],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.notify("Started Supervisor Daemon", severity="information")
            self.set_timer(1.0, self.update_daemon_status)
        except (OSError, subprocess.SubprocessError) as e:
            self.notify(f"Failed to start Supervisor Daemon: {e}", severity="error")

    def action_supervisor_stop(self) -> None:
        """Stop the Supervisor Daemon."""
        from ..supervisor_daemon import is_supervisor_daemon_running, stop_supervisor_daemon
        from ..tui_widgets import DaemonPanel

        if not is_supervisor_daemon_running(self.tmux_session):
            self.notify("Supervisor Daemon not running", severity="warning")
            return

        if stop_supervisor_daemon(self.tmux_session):
            self.notify("Stopped Supervisor Daemon", severity="information")
            try:
                panel = self.query_one("#daemon-panel", DaemonPanel)
                panel.log_lines.append(">>> Supervisor Daemon stopped")
            except NoMatches:
                pass
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
        from ..tui_widgets import DaemonPanel

        try:
            panel = self.query_one("#daemon-panel", DaemonPanel)
            panel.log_lines.append(">>> Restarting Monitor Daemon...")
        except NoMatches:
            pass

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
        from ..tui_widgets import DaemonPanel

        try:
            subprocess.Popen(
                [sys.executable, "-m", "overcode.monitor_daemon",
                 "--session", self.tmux_session],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            self.notify("Monitor Daemon restarted", severity="information")
            try:
                panel = self.query_one("#daemon-panel", DaemonPanel)
                panel.log_lines.append(">>> Monitor Daemon restarted")
            except NoMatches:
                pass
            self.set_timer(1.0, self.update_daemon_status)
        except (OSError, subprocess.SubprocessError) as e:
            self.notify(f"Failed to restart Monitor Daemon: {e}", severity="error")

    def action_toggle_web_server(self) -> None:
        """Toggle the web analytics dashboard server on/off."""
        from ..web_server import toggle_web_server, get_web_server_url
        from ..tui_widgets import DaemonPanel

        is_running, msg = toggle_web_server(self.tmux_session)

        if is_running:
            url = get_web_server_url(self.tmux_session)
            self.notify(f"Web server: {url}", severity="information")
            try:
                panel = self.query_one("#daemon-panel", DaemonPanel)
                panel.log_lines.append(f">>> Web server started: {url}")
            except NoMatches:
                pass
        else:
            self.notify(f"Web server: {msg}", severity="information")
            try:
                panel = self.query_one("#daemon-panel", DaemonPanel)
                panel.log_lines.append(f">>> Web server: {msg}")
            except NoMatches:
                pass

        self.update_daemon_status()
