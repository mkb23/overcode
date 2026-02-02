"""
Daemon status bar widget for TUI.

Shows Monitor Daemon, Supervisor Daemon, AI, spin stats, and presence status.
"""

from datetime import datetime
from typing import Optional, TYPE_CHECKING

from textual.widgets import Static
from rich.text import Text

from ..monitor_daemon_state import MonitorDaemonState, get_monitor_daemon_state
from ..supervisor_daemon import is_supervisor_daemon_running
from ..summarizer_client import SummarizerClient
from ..web_server import is_web_server_running, get_web_server_url
from ..settings import DAEMON_VERSION, get_agent_history_path
from ..status_history import read_agent_status_history
from ..tui_logic import calculate_mean_spin_from_history
from ..tui_helpers import (
    format_interval,
    format_duration,
    format_tokens,
    format_cost,
    get_daemon_status_style,
    calculate_safe_break_duration,
)

if TYPE_CHECKING:
    from ..session_manager import SessionManager


class DaemonStatusBar(Static):
    """Widget displaying daemon status.

    Shows Monitor Daemon and Supervisor Daemon status explicitly.
    Presence is shown only when available (macOS with monitor daemon running).
    """

    def __init__(self, tmux_session: str = "agents", session_manager: Optional["SessionManager"] = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tmux_session = tmux_session
        self.monitor_state: Optional[MonitorDaemonState] = None
        self._session_manager = session_manager
        self._asleep_session_ids: set = set()  # Cache of asleep session IDs
        self.show_cost: bool = False  # Show $ cost instead of token counts

    def update_status(self) -> None:
        """Refresh daemon state from file"""
        self.monitor_state = get_monitor_daemon_state(self.tmux_session)
        # Update cache of asleep session IDs from session manager
        if self._session_manager:
            self._asleep_session_ids = {
                s.id for s in self._session_manager.list_sessions() if s.is_asleep
            }
        self.refresh()

    def render(self) -> Text:
        """Render daemon status bar.

        Shows Monitor Daemon and Supervisor Daemon status explicitly.
        """
        content = Text()

        # Monitor Daemon status
        content.append("Monitor: ", style="bold")
        monitor_running = self.monitor_state and not self.monitor_state.is_stale()

        if monitor_running:
            state = self.monitor_state
            symbol, style = get_daemon_status_style(state.status)
            content.append(f"{symbol} ", style=style)
            content.append(f"#{state.loop_count}", style="cyan")
            content.append(f" @{format_interval(state.current_interval)}", style="dim")
            # Version mismatch warning
            if state.daemon_version != DAEMON_VERSION:
                content.append(f" ⚠v{state.daemon_version}→{DAEMON_VERSION}", style="bold yellow")
        else:
            content.append("○ ", style="red")
            content.append("stopped", style="red")

        content.append(" │ ", style="dim")

        # Supervisor Daemon status
        content.append("Supervisor: ", style="bold")
        supervisor_running = is_supervisor_daemon_running(self.tmux_session)

        if supervisor_running:
            content.append("● ", style="green")
            # Show if daemon Claude is currently running
            if monitor_running and self.monitor_state.supervisor_claude_running:
                # Calculate current run duration
                run_duration = ""
                if self.monitor_state.supervisor_claude_started_at:
                    try:
                        started = datetime.fromisoformat(self.monitor_state.supervisor_claude_started_at)
                        elapsed = (datetime.now() - started).total_seconds()
                        run_duration = format_duration(elapsed)
                    except (ValueError, TypeError):
                        run_duration = "?"
                content.append(f"🤖 RUNNING {run_duration}", style="bold yellow")
            # Show supervision stats if available from monitor state
            elif monitor_running and self.monitor_state.total_supervisions > 0:
                content.append(f"sup:{self.monitor_state.total_supervisions}", style="magenta")
                if self.monitor_state.supervisor_tokens > 0:
                    content.append(f" {format_tokens(self.monitor_state.supervisor_tokens)}", style="blue")
                # Show cumulative daemon Claude run time
                if self.monitor_state.supervisor_claude_total_run_seconds > 0:
                    total_run = format_duration(self.monitor_state.supervisor_claude_total_run_seconds)
                    content.append(f" ⏱{total_run}", style="dim")
            else:
                content.append("ready", style="green")
        else:
            content.append("○ ", style="red")
            content.append("stopped", style="red")

        # AI Summarizer status (from TUI's local summarizer, not daemon)
        content.append(" │ ", style="dim")
        content.append("AI: ", style="bold")
        # Get summarizer state from parent app
        summarizer_available = SummarizerClient.is_available()
        summarizer_enabled = False
        summarizer_calls = 0
        if hasattr(self.app, '_summarizer'):
            summarizer_enabled = self.app._summarizer.enabled
            summarizer_calls = self.app._summarizer.total_calls
        if summarizer_available:
            if summarizer_enabled:
                content.append("● ", style="green")
                if summarizer_calls > 0:
                    content.append(f"{summarizer_calls}", style="cyan")
                else:
                    content.append("on", style="green")
            else:
                content.append("○ ", style="dim")
                content.append("off", style="dim")
        else:
            content.append("○ ", style="red")
            content.append("n/a", style="red dim")

        # Spin rate stats (only when monitor running with sessions)
        if monitor_running and self.monitor_state.sessions:
            content.append(" │ ", style="dim")
            # Filter out sleeping agents from stats
            all_sessions = self.monitor_state.sessions
            active_sessions = [s for s in all_sessions if s.session_id not in self._asleep_session_ids]
            sleeping_count = len(all_sessions) - len(active_sessions)

            total_agents = len(active_sessions)
            # Recalculate green_now excluding sleeping agents
            green_now = sum(1 for s in active_sessions if s.current_status == "running")

            content.append("Spin: ", style="bold")
            content.append(f"{green_now}", style="bold green" if green_now > 0 else "dim")
            content.append(f"/{total_agents}", style="dim")
            if sleeping_count > 0:
                content.append(f" 💤{sleeping_count}", style="dim")  # Show sleeping count

            # Mean spin rate - use history-based calculation if baseline > 0
            baseline_minutes = getattr(self.app, 'baseline_minutes', 0)
            if baseline_minutes > 0:
                # History-based calculation for time window
                history = read_agent_status_history(
                    hours=baseline_minutes / 60.0 + 0.1,  # slight buffer
                    history_file=get_agent_history_path(self.tmux_session)
                )
                agent_names = [s.name for s in active_sessions]
                mean_spin, sample_count = calculate_mean_spin_from_history(
                    history, agent_names, baseline_minutes
                )

                if sample_count > 0:
                    # Format window label: "15m", "1h", "1h30m"
                    if baseline_minutes < 60:
                        window_label = f"{baseline_minutes}m"
                    else:
                        hours = baseline_minutes // 60
                        mins = baseline_minutes % 60
                        window_label = f"{hours}h" if mins == 0 else f"{hours}h{mins}m"
                    content.append(f" μ{mean_spin:.1f} ({window_label})", style="cyan")
                else:
                    content.append(" μ-- (no data)", style="dim")
            else:
                # Instantaneous: show current running count as the mean
                content.append(f" μ{green_now}", style="cyan")

            # Total tokens/cost across all sessions (include sleeping agents - they used tokens too)
            if self.show_cost:
                total_cost = sum(s.estimated_cost_usd for s in all_sessions)
                if total_cost > 0:
                    content.append(f" {format_cost(total_cost)}", style="orange1")
            else:
                total_tokens = sum(s.input_tokens + s.output_tokens for s in all_sessions)
                if total_tokens > 0:
                    content.append(f" Σ{format_tokens(total_tokens)}", style="orange1")

            # Safe break duration (time until 50%+ agents need attention) - exclude sleeping
            safe_break = calculate_safe_break_duration(active_sessions)
            if safe_break is not None:
                content.append(" │ ", style="dim")
                content.append("☕", style="bold")
                if safe_break < 60:
                    content.append(f" <1m", style="bold red")
                elif safe_break < 300:  # < 5 min
                    content.append(f" {format_duration(safe_break)}", style="bold yellow")
                else:
                    content.append(f" {format_duration(safe_break)}", style="bold green")

        # Presence status (only show if available via monitor daemon on macOS)
        if monitor_running and self.monitor_state.presence_available:
            content.append(" │ ", style="dim")
            state = self.monitor_state.presence_state
            idle = self.monitor_state.presence_idle_seconds or 0

            state_names = {1: "🔒", 2: "💤", 3: "👤"}
            state_colors = {1: "red", 2: "yellow", 3: "green"}

            icon = state_names.get(state, "?")
            color = state_colors.get(state, "dim")
            content.append(f"{icon}", style=color)
            content.append(f" {int(idle)}s", style="dim")

        # Relay status (small indicator)
        if monitor_running and self.monitor_state.relay_enabled:
            content.append(" │ ", style="dim")
            relay_status = self.monitor_state.relay_last_status
            if relay_status == "ok":
                content.append("📡", style="green")
            elif relay_status == "error":
                content.append("📡", style="red")
            else:
                content.append("📡", style="dim")

        # Web server status
        web_running = is_web_server_running(self.tmux_session)
        if web_running:
            content.append(" │ ", style="dim")
            url = get_web_server_url(self.tmux_session)
            content.append("🌐", style="green")
            if url:
                # Just show port
                port = url.split(":")[-1] if url else ""
                content.append(f":{port}", style="cyan")

        return content
