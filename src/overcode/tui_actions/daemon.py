"""
Daemon action methods for TUI.

Handles Monitor Daemon, Supervisor Daemon, and API Server controls.
"""

import sys

from textual.css.query import NoMatches


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

    def action_open_summary_prompt_lab(self) -> None:
        """Open the summary prompt editor with live results (#491)."""
        from ..prompt_lab import LabSample, PromptLabRunner, pick_samples
        from ..summarizer_client import SummarizerClient
        from ..tui_widgets import SummaryPromptLab

        try:
            lab = self.query_one("#summary-prompt-lab", SummaryPromptLab)
        except NoMatches:
            return
        summarizer = self._summarizer

        if getattr(self, "_prompt_lab_runner", None) is None:
            def add_cost(usd: float) -> None:
                # Lab calls are real spend: they count toward the cap
                summarizer.total_cost_usd += usd

            self._prompt_lab_runner = PromptLabRunner(
                capture=summarizer._capture_pane,
                make_client=SummarizerClient,
                lines=summarizer.config.lines,
                on_cost=add_cost,
            )

        def blocked():
            cap = summarizer.config.cost_cap
            if summarizer.cost_cap_hit or (cap > 0 and summarizer.total_cost_usd >= cap):
                return f"summarizer cost cap (${cap:.2f}) reached — restart the TUI to reset"
            return None

        def samples(mode: str) -> list:
            sessions = [
                w.session for w in self._get_widgets_in_session_order()
                if not self._is_remote(w.session)
            ]
            focused = self._get_focused_widget()
            focused_id = focused.session.id if focused is not None else None
            out = []
            for s in pick_samples(sessions, focused_id):
                live = self._summaries.get(s.id)
                out.append(LabSample(
                    session_id=s.id,
                    name=s.name,
                    window=s.tmux_window,
                    status=getattr(s.stats, "current_state", "unknown") or "unknown",
                    live=(live.context if mode == "context" else live.text) if live else "",
                ))
            return out

        self._dialog_will_open()
        lab.show(
            runner=self._prompt_lab_runner,
            samples=samples,
            available=SummarizerClient.is_available(),
            blocked=blocked,
            app_ref=self,
        )

    def action_toggle_summarizer(self) -> None:
        """Toggle the AI Summarizer on/off."""
        from ..summarizer_client import SummarizerClient
        from ..tui_widgets import SessionSummary

        # Check if summarizer is available (OPENAI_API_KEY set)
        if not SummarizerClient.is_available():
            self.notify("AI Summarizer unavailable - set OPENAI_API_KEY", severity="warning")
            return

        # Block re-enable if cost cap was hit (requires TUI restart)
        if self._summarizer.cost_cap_hit and not self._summarizer.config.enabled:
            from ..tui_helpers import format_cost
            cap = self._summarizer.config.cost_cap
            self.notify(
                f"AI Summarizer HALTED — cost cap {format_cost(cap)} reached. Restart TUI to reset.",
                severity="error",
            )
            return

        # Block enable if model has no pricing data
        if not self._summarizer.config.enabled:
            from ..pricing import lookup_pricing
            from ..config import get_summarizer_config
            model = get_summarizer_config()["model"]
            pricing = lookup_pricing(model)
            if pricing.input == 0.0 and pricing.output == 0.0:
                self.notify(
                    f"AI Summarizer blocked — no cost data for model '{model}'",
                    severity="error",
                )
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

    def action_toggle_budget_reclaim(self) -> None:
        """Budget reclaim auto <-> manual (children.budget_reclaim in config.yaml, #546)."""
        from ..config import get_budget_reclaim_mode, save_budget_reclaim_mode
        mode = "manual" if get_budget_reclaim_mode() == "auto" else "auto"
        try:
            save_budget_reclaim_mode(mode)
        except Exception as e:
            self.notify(f"Budget reclaim not changed: {e}", severity="error")
            return
        if mode == "auto":
            message = "Budget reclaim auto: a finished child's unused budget goes back to its parent"
        else:
            message = ("Budget reclaim manual: a finished child keeps its budget until its parent "
                       "runs overcode budget reclaim <child>")
        self.notify(message, severity="information")

    def action_toggle_prefer_claude_messaging(self) -> None:
        """send.prefer_claude_messaging on <-> off in config.yaml (#553)."""
        from ..config import get_prefer_claude_messaging, save_prefer_claude_messaging
        on = not get_prefer_claude_messaging()
        try:
            save_prefer_claude_messaging(on)
        except Exception as e:
            self.notify(f"Claude messaging setting not changed: {e}", severity="error")
            return
        if on:
            message = ("Claude agents use SendMessage: overcode send between two Claude Code "
                       "agents is refused unless --force")
        else:
            message = "overcode send between Claude Code agents goes through again"
        self.notify(message, severity="information")

    def action_toggle_transcript_archive(self) -> None:
        """archive.transcripts.enabled on <-> off in config.yaml (the daemon and hooks read it)."""
        from ..config import get_transcript_archive_config, save_transcript_archive_enabled
        on = not get_transcript_archive_config()["enabled"]
        try:
            save_transcript_archive_enabled(on)
        except Exception as e:
            self.notify(f"Transcript archive not changed: {e}", severity="error")
            return
        if on:
            message = "Transcript archive on: Claude transcripts are copied to ~/.overcode/transcripts"
        else:
            message = "Transcript archive off: nothing new is captured; what is kept stays readable"
        self.notify(message, severity="information")

    def action_toggle_auto_naming(self) -> None:
        """Turn auto-naming on or off (naming.enabled in config.yaml; the daemon reads it each tick)."""
        from ..config import describe_auto_naming, get_naming_config, save_naming_enabled
        on = not get_naming_config()["enabled"]
        try:
            save_naming_enabled(on)
        except Exception as e:
            self.notify(f"Auto-naming not changed: {e}", severity="error")
            return
        cfg = get_naming_config()
        message, severity = f"Auto-naming {describe_auto_naming(cfg)}", "information"
        if on:
            from ..namer import make_client
            try:
                available = bool(make_client(cfg.get("provider")).available)
            except Exception:
                available = False
            if not available:
                message += (" — but no model is set up (summarizer: or naming.provider, "
                            "and its API key)")
                severity = "warning"
        self.notify(message, severity=severity)
        _log_to_daemon_panel(self, f">>> Auto-naming {'on' if on else 'off'}")

    def action_toggle_web_server(self) -> None:
        """Toggle the API server (the sister API) on/off."""
        from ..web_server import toggle_web_server, get_web_server_url

        is_running, msg = toggle_web_server(self.tmux_session)

        if is_running:
            url = get_web_server_url(self.tmux_session)
            self.notify(f"API server: {url}", severity="information")
            _log_to_daemon_panel(self, f">>> API server started: {url}")
        else:
            self.notify(f"API server: {msg}", severity="information")
            _log_to_daemon_panel(self, f">>> API server: {msg}")

        self.update_daemon_status()
