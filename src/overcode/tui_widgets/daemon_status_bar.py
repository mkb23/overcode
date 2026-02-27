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
from ..status_constants import is_green_status

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
        self._usage_snapshot = None  # UsageSnapshot from UsageMonitor
        # Cached I/O results (populated by fetch_volatile_state, read by render)
        self._supervisor_running: bool = False
        self._summarizer_available: bool = False
        self._web_running: bool = False
        self._web_url: Optional[str] = None
        self._mean_spin: float = 0.0
        self._spin_sample_count: int = 0
        self._spin_baseline_minutes: int = 0
        self._sister_states: list = []  # List of SisterState (#245)

    def fetch_volatile_state(self, baseline_minutes: int = 0, active_session_names: Optional[list] = None) -> None:
        """Fetch all I/O-dependent state. Call from a background thread, NOT main thread.

        This replaces the I/O that was previously done inside render().
        """
        self._supervisor_running = is_supervisor_daemon_running(self.tmux_session)
        self._summarizer_available = SummarizerClient.is_available()
        self._web_running = is_web_server_running(self.tmux_session)
        self._web_url = get_web_server_url(self.tmux_session) if self._web_running else None
        self._spin_baseline_minutes = baseline_minutes

        # Mean spin rate from history (expensive CSV parse)
        if baseline_minutes > 0 and active_session_names:
            history = read_agent_status_history(
                hours=baseline_minutes / 60.0 + 0.1,  # slight buffer
                history_file=get_agent_history_path(self.tmux_session)
            )
            self._mean_spin, self._spin_sample_count = calculate_mean_spin_from_history(
                history, active_session_names, baseline_minutes
            )
        else:
            self._mean_spin = 0.0
            self._spin_sample_count = 0

    def update_status(self) -> None:
        """Refresh daemon state from file.

        NOTE: This is the legacy entry point. The TUI now calls
        fetch_volatile_state() from a background worker and sets
        monitor_state / _asleep_session_ids directly. This method
        remains for backwards compatibility (e.g. diagnostics mode
        manual refresh).
        """
        self.monitor_state = get_monitor_daemon_state(self.tmux_session)
        if self._session_manager:
            self._asleep_session_ids = {
                s.id for s in self._session_manager.list_sessions()
                if s.is_asleep and s.tmux_session == self.tmux_session
            }
        self.fetch_volatile_state(
            baseline_minutes=getattr(self.app, 'baseline_minutes', 0),
            active_session_names=self._get_active_session_names(),
        )
        self.refresh()

    def _get_active_session_names(self) -> list:
        """Get active (non-sleeping) agent names from monitor state."""
        if not self.monitor_state or not self.monitor_state.sessions:
            return []
        return [
            s.name for s in self.monitor_state.sessions
            if s.session_id not in self._asleep_session_ids
        ]

    @staticmethod
    def _usage_pct_style(pct: float) -> str:
        """Return a Rich style string based on usage percentage."""
        if pct >= 90:
            return "bold red"
        elif pct >= 75:
            return "bold yellow"
        elif pct >= 50:
            return "yellow"
        return "green"

    def render(self) -> Text:
        """Render daemon status bar.

        Shows Monitor Daemon and Supervisor Daemon status explicitly.
        All data comes from cached instance variables — NO I/O here.
        """
        content = Text()

        # Usage section (prepended before Monitor)
        snap = self._usage_snapshot
        if snap is not None:
            content.append("Usage: ", style="bold")
            if snap.error:
                content.append("--", style="dim")
            else:
                content.append("5h:", style="dim")
                content.append(f"{snap.five_hour_pct:.0f}%", style=self._usage_pct_style(snap.five_hour_pct))
                content.append(" 7d:", style="dim")
                content.append(f"{snap.seven_day_pct:.0f}%", style=self._usage_pct_style(snap.seven_day_pct))
            content.append(" │ ", style="dim")

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

        # Supervisor Daemon status (cached)
        content.append("Supervisor: ", style="bold")

        if self._supervisor_running:
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
        # Get summarizer state from parent app (cheap attribute reads, no I/O)
        summarizer_enabled = False
        summarizer_calls = 0
        if hasattr(self.app, '_summarizer'):
            summarizer_enabled = self.app._summarizer.enabled
            summarizer_calls = self.app._summarizer.total_calls
        if self._summarizer_available:
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

        # Spin rate stats — aggregate local + sister agents
        local_sessions = self.monitor_state.sessions if monitor_running and self.monitor_state.sessions else []
        reachable_sisters = [s for s in self._sister_states if s.reachable]

        if local_sessions or reachable_sisters:
            content.append(" │ ", style="dim")
            # Local: filter out sleeping agents
            active_sessions = [s for s in local_sessions if s.session_id not in self._asleep_session_ids]
            sleeping_count = len(local_sessions) - len(active_sessions)
            local_green = sum(1 for s in active_sessions if is_green_status(s.current_status))
            local_total = len(active_sessions)

            # Sister: use pre-computed green/total from API (already excludes sleeping)
            sister_green = sum(s.green_agents for s in reachable_sisters)
            sister_total = sum(s.total_agents for s in reachable_sisters)

            green_now = local_green + sister_green
            total_agents = local_total + sister_total

            content.append("Spin: ", style="bold")
            content.append(f"{green_now}", style="bold green" if green_now > 0 else "dim")
            content.append(f"/{total_agents}", style="dim")
            if sleeping_count > 0:
                content.append(f" 💤{sleeping_count}", style="dim")  # Show sleeping count

            # Mean spin rate — use cached values from fetch_volatile_state()
            baseline_minutes = self._spin_baseline_minutes
            if baseline_minutes > 0:
                # Sister cumulative mean spin: sum of each agent's green fraction
                sister_mean_spin = 0.0
                for sister in reachable_sisters:
                    for sess in sister.sessions:
                        if sess.is_asleep:
                            continue
                        gt = sess.stats.green_time_seconds if sess.stats else 0.0
                        ngt = sess.stats.non_green_time_seconds if sess.stats else 0.0
                        if gt + ngt > 0:
                            sister_mean_spin += gt / (gt + ngt)

                combined_mean = self._mean_spin + sister_mean_spin
                combined_samples = self._spin_sample_count + (1 if reachable_sisters else 0)
                if combined_samples > 0:
                    # Format window label: "15m", "1h", "1h30m"
                    if baseline_minutes < 60:
                        window_label = f"{baseline_minutes}m"
                    else:
                        hours = baseline_minutes // 60
                        mins = baseline_minutes % 60
                        window_label = f"{hours}h" if mins == 0 else f"{hours}h{mins}m"
                    content.append(f" μ{combined_mean:.1f} ({window_label})", style="cyan")
                else:
                    content.append(" μ-- (no data)", style="dim")
            else:
                # Instantaneous: show current running count as the mean
                content.append(f" μ{green_now}", style="cyan")

            # Total tokens/cost across all sessions (include sleeping agents - they used tokens too)
            if self.show_cost:
                total_cost = sum(s.estimated_cost_usd for s in local_sessions)
                total_cost += sum(s.total_cost for s in reachable_sisters)
                if total_cost > 0:
                    content.append(f" {format_cost(total_cost)}", style="orange1")
            else:
                total_tokens = sum(s.input_tokens + s.output_tokens for s in local_sessions)
                for sister in reachable_sisters:
                    total_tokens += sum(
                        sess.stats.total_tokens for sess in sister.sessions
                        if sess.stats
                    )
                if total_tokens > 0:
                    content.append(f" Σ{format_tokens(total_tokens)}", style="orange1")

            # Safe break duration (time until 50%+ agents need attention) - exclude sleeping
            safe_break = calculate_safe_break_duration(active_sessions)
            if safe_break is not None:
                content.append(" │ ", style="dim")
                content.append("☕", style="bold")
                if safe_break < 60:
                    content.append(" <1m", style="bold red")
                elif safe_break < 300:  # < 5 min
                    content.append(f" {format_duration(safe_break)}", style="bold yellow")
                else:
                    content.append(f" {format_duration(safe_break)}", style="bold green")

        # Presence status (only show if available via monitor daemon on macOS)
        if monitor_running and self.monitor_state.presence_available:
            content.append(" │ ", style="dim")
            state = self.monitor_state.presence_state
            idle = self.monitor_state.presence_idle_seconds or 0

            state_names = {0: "⏻", 1: "🔒", 2: "🧘", 3: "🚶", 4: "🏃"}
            state_colors = {0: "#1a1a2e", 1: "red", 2: "orange1", 3: "yellow", 4: "green"}

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

        # Web server status (cached)
        if self._web_running:
            content.append(" │ ", style="dim")
            content.append("🌐", style="green")
            if self._web_url:
                # Just show port
                port = self._web_url.split(":")[-1] if self._web_url else ""
                content.append(f":{port}", style="cyan")

        # Sister status (#245)
        if self._sister_states:
            content.append(" │ ", style="dim")
            content.append("Sisters: ", style="bold")
            for i, sister in enumerate(self._sister_states):
                if i > 0:
                    content.append(" ", style="")
                if sister.reachable:
                    content.append(
                        f"{sister.name}({sister.green_agents}/{sister.total_agents})",
                        style="green",
                    )
                else:
                    content.append(f"{sister.name}(--)", style="dim red")

        return content
