"""
Daemon panel widget for TUI.

Inline panel showing monitor daemon status and recent log output.
Toggled with 'd'. Shows the monitor daemon's log file, which records
status detection, stats tracking, and heartbeat activity.
"""

from datetime import datetime
from typing import Optional

from textual.widgets import Static
from textual import work
from rich.text import Text

from ..daemon_logging import follow_log
from ..monitor_daemon_state import MonitorDaemonState, get_monitor_daemon_state
from ..settings import get_session_dir
from ..tui_helpers import (
    format_interval,
    format_ago,
    format_duration,
    get_daemon_status_style,
)


class DaemonPanel(Static):
    """Inline panel showing monitor daemon status and log tail.

    While the TUI is connected to the engine, the status line is the
    engine's fleet snapshot (status, interval, steers, the detection mode)
    and its liveness is the engine client's (EngineClient.stalled_for):
    nothing is parsed. Only with no engine is the daemon's state file read,
    to say when it last ran. The 1 s refresh does nothing while hidden, and
    the log is a stat until it grows.
    """

    LOG_LINES_TO_SHOW = 16

    def __init__(self, tmux_session: str = "agents", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tmux_session = tmux_session
        self.log_lines: list[str] = []
        # The state file, read only while the engine is not connected
        self.monitor_state: Optional[MonitorDaemonState] = None
        # The fleet default detection mode, resolved in the worker when the
        # engine does not publish it
        self.detection_mode: Optional[str] = None
        self._log_file_pos = 0
        self._last_rendered: Optional[Text] = None

    def on_mount(self) -> None:
        """Start log tailing when mounted."""
        self.set_interval(1.0, self._refresh_logs)
        self._refresh_logs()

    def _engine(self) -> Optional[tuple]:
        """(fleet, stalled_for) while the app is connected to the engine, else None."""
        try:
            app = self.app
        except Exception:  # a bare widget (no running app)
            return None
        if not getattr(app, "_engine_connected", False):
            return None
        client = getattr(app, "_engine_client", None)
        stalled = client.stalled_for() if client is not None else None
        return getattr(app, "_engine_fleet", None) or {}, stalled

    def _refresh_logs(self) -> None:
        """Each second, while shown: the log (a stat until it grows) and what the engine lacks."""
        if not self.display:
            return
        engine = self._engine()
        fleet = engine[0] if engine is not None else {}
        self._fetch_logs_async(
            read_state=engine is None,
            resolve_mode=not fleet.get("detection_mode"),
        )

    @work(thread=True, exclusive=True, group="daemon_logs")
    def _fetch_logs_async(self, read_state: bool = True, resolve_mode: bool = True) -> None:
        """Worker: the log's new lines; the state file and detection mode only if asked."""
        monitor_state = get_monitor_daemon_state(self.tmux_session) if read_state else None
        mode = None
        if resolve_mode:
            from ..settings import resolve_detection_mode
            mode = resolve_detection_mode(self.tmux_session)

        log_file = get_session_dir(self.tmux_session) / "monitor_daemon.log"
        new_log_lines, new_file_pos = follow_log(log_file, self.log_lines, self._log_file_pos)

        self.app.call_from_thread(
            self._apply_logs, monitor_state, new_log_lines, new_file_pos, mode
        )

    def _apply_logs(self, monitor_state, new_log_lines, new_file_pos, mode=None) -> None:
        """Apply fetched log data on main thread; repaint only if it draws differently."""
        self.monitor_state = monitor_state
        self._log_file_pos = new_file_pos
        if new_log_lines is not None:
            self.log_lines = new_log_lines
        if mode is not None:
            self.detection_mode = mode
        self.refresh_if_changed()

    def refresh_if_changed(self) -> bool:
        if self._last_rendered is not None and self._render_panel() == self._last_rendered:
            return False
        self.refresh()
        return True

    def render(self) -> Text:
        """Render the daemon panel."""
        content = self._render_panel()
        self._last_rendered = content
        return content

    def _render_panel(self) -> Text:
        content = Text()

        # Header
        content.append("Monitor Daemon", style="bold")

        engine = self._engine()
        fleet = engine[0] if engine is not None else {}
        if engine is not None:
            stalled_for = engine[1]
            status = fleet.get("status") or "active"
            symbol, style = get_daemon_status_style(status)
            content.append("  ")
            content.append(f"{symbol} {status}", style=style)
            if stalled_for is not None:
                content.append(f"  ⚠ frozen {format_duration(stalled_for)}", style="bold red")
            interval = fleet.get("current_interval")
            if isinstance(interval, (int, float)):
                content.append(f"  @{format_interval(interval)}", style="dim")
            if fleet.get("interval_mode") == "unattended":
                content.append(" (unattended)", style="dim")
            steers = fleet.get("total_supervisions") or 0
            if steers > 0:
                content.append(f"  steers:{steers}", style="magenta")
        elif self.monitor_state and not self.monitor_state.is_stale():
            state = self.monitor_state
            symbol, style = get_daemon_status_style(state.status)

            content.append("  ")
            content.append(f"{symbol} {state.status}", style=style)

            # Loop count and interval
            content.append("  ", style="dim")
            content.append(f"loop #{state.loop_count}", style="cyan")
            content.append(f" @{format_interval(state.current_interval)}", style="dim")
            try:
                last_loop = datetime.fromisoformat(state.last_loop_time) if state.last_loop_time else None
            except (ValueError, TypeError):
                last_loop = None
            content.append(f" ({format_ago(last_loop)})", style="dim")
            if state.total_supervisions > 0:
                content.append(f"  steers:{state.total_supervisions}", style="magenta")
        else:
            content.append("  ")
            content.append("stopped", style="red")
            if self.monitor_state and self.monitor_state.last_loop_time:
                try:
                    last_time = datetime.fromisoformat(self.monitor_state.last_loop_time)
                    content.append(f" (last: {format_ago(last_time)})", style="dim")
                except ValueError:
                    pass

        # Fleet default detection mode (per-agent overrides win over this):
        # the engine's, else resolved by the worker
        mode = fleet.get("detection_mode") or self.detection_mode
        if mode:
            mode_style = "green" if mode == "hooks" else "yellow"
            content.append("  ")
            content.append(f"detect:{mode}(fleet)", style=mode_style)

        # Controls
        content.append("  ", style="dim")
        content.append("[", style="bold green")
        content.append(" start  ", style="dim")
        content.append("]", style="bold red")
        content.append(" stop  ", style="dim")
        content.append("\\", style="bold yellow")
        content.append(" restart  ", style="dim")
        content.append("K", style="bold cyan")
        content.append(" agent mode", style="dim")

        content.append("\n")

        # Log file path
        session_dir = get_session_dir(self.tmux_session)
        content.append(f"  {session_dir / 'monitor_daemon.log'}", style="dim italic")
        content.append("\n")

        # Log lines
        display_lines = self.log_lines[-self.LOG_LINES_TO_SHOW:] if self.log_lines else []

        if not display_lines:
            content.append("  (no log output yet)", style="dim italic")
            content.append("\n")
        else:
            for line in display_lines:
                content.append("  ")
                display_line = line[:140] if len(line) > 140 else line

                if "ERROR" in line or "error" in line:
                    style = "red"
                elif "WARNING" in line or "warning" in line:
                    style = "yellow"
                elif ">>>" in line:
                    style = "bold cyan"
                elif "supervising" in line.lower() or "steering" in line.lower():
                    style = "magenta"
                elif "Loop" in line:
                    style = "dim cyan"
                else:
                    style = "dim"

                content.append(display_line, style=style)
                content.append("\n")

        return content
