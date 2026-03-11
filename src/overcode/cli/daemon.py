"""
Daemon commands: monitor-daemon and supervisor-daemon subcommands.
"""

from typing import Annotated, Callable

import typer
from rich import print as rprint

from ._shared import monitor_daemon_app, supervisor_daemon_app, SessionOption


def _daemon_control_start(
    daemon_name: str,
    session: str,
    interval: int,
    is_running_fn: Callable[[str], bool],
    get_pid_fn: Callable[[str], int],
    create_daemon_fn: Callable,
) -> None:
    """Shared start logic for daemon commands."""
    if is_running_fn(session):
        pid = get_pid_fn(session)
        rprint(f"[yellow]{daemon_name} already running[/yellow] (PID {pid}) for session '{session}'")
        raise typer.Exit(1)

    rprint(f"[dim]Starting {daemon_name} for session '{session}' with interval {interval}s...[/dim]")
    daemon = create_daemon_fn(session)
    daemon.run(interval)


def _daemon_control_stop(
    daemon_name: str,
    session: str,
    is_running_fn: Callable[[str], bool],
    get_pid_fn: Callable[[str], int],
    stop_fn: Callable[[str], bool],
) -> None:
    """Shared stop logic for daemon commands."""
    if not is_running_fn(session):
        rprint(f"[dim]{daemon_name} is not running for session '{session}'[/dim]")
        return

    pid = get_pid_fn(session)
    if stop_fn(session):
        rprint(f"[green]✓[/green] {daemon_name} stopped (was PID {pid}) for session '{session}'")
    else:
        rprint(f"[red]Failed to stop {daemon_name}[/red]")
        raise typer.Exit(1)


# =============================================================================
# Monitor Daemon Commands
# =============================================================================


@monitor_daemon_app.callback(invoke_without_command=True)
def monitor_daemon_default(ctx: typer.Context, session: SessionOption = "agents"):
    """Show monitor daemon status (default when no subcommand given)."""
    if ctx.invoked_subcommand is None:
        _monitor_daemon_status(session)


@monitor_daemon_app.command("start")
def monitor_daemon_start(
    interval: Annotated[
        int, typer.Option("--interval", "-i", help="Polling interval in seconds")
    ] = 10,
    session: SessionOption = "agents",
):
    """Start the Monitor Daemon.

    The Monitor Daemon tracks session state and metrics:
    - Status detection (running, waiting, etc.)
    - Time accumulation (green_time, non_green_time)
    - Claude Code stats (tokens, interactions)
    - User presence state
    """
    from ..monitor_daemon import MonitorDaemon, is_monitor_daemon_running, get_monitor_daemon_pid

    _daemon_control_start(
        "Monitor Daemon", session, interval,
        is_monitor_daemon_running, get_monitor_daemon_pid, MonitorDaemon,
    )


@monitor_daemon_app.command("stop")
def monitor_daemon_stop(session: SessionOption = "agents"):
    """Stop the running Monitor Daemon."""
    from ..monitor_daemon import stop_monitor_daemon, is_monitor_daemon_running, get_monitor_daemon_pid

    _daemon_control_stop(
        "Monitor Daemon", session,
        is_monitor_daemon_running, get_monitor_daemon_pid, stop_monitor_daemon,
    )


@monitor_daemon_app.command("status")
def monitor_daemon_status_cmd(session: SessionOption = "agents"):
    """Show Monitor Daemon status."""
    _monitor_daemon_status(session)


def _monitor_daemon_status(session: str):
    """Internal function for showing monitor daemon status."""
    from ..monitor_daemon import is_monitor_daemon_running, get_monitor_daemon_pid
    from ..monitor_daemon_state import get_monitor_daemon_state

    if not is_monitor_daemon_running(session):
        rprint(f"[dim]Monitor Daemon ({session}):[/dim] ○ stopped")
        state = get_monitor_daemon_state(session)
        if state and state.last_loop_time:
            from ..tui_helpers import format_ago
            rprint(f"  [dim]Last active: {format_ago(state.last_loop_time)}[/dim]")
        return

    pid = get_monitor_daemon_pid(session)
    state = get_monitor_daemon_state(session)

    rprint(f"[green]Monitor Daemon ({session}):[/green] ● running (PID {pid})")
    if state:
        rprint(f"  Status: {state.status}")
        rprint(f"  Loop count: {state.loop_count}")
        rprint(f"  Interval: {state.current_interval}s")
        rprint(f"  Sessions: {len(state.sessions)}")
        if state.last_loop_time:
            from ..tui_helpers import format_ago
            rprint(f"  Last loop: {format_ago(state.last_loop_time)}")
        if state.presence_available:
            rprint(f"  Presence: state={state.presence_state}, idle={state.presence_idle_seconds:.0f}s")


@monitor_daemon_app.command("watch")
def monitor_daemon_watch(session: SessionOption = "agents"):
    """Watch Monitor Daemon logs in real-time."""
    import subprocess
    from ..settings import get_session_dir

    log_file = get_session_dir(session) / "monitor_daemon.log"

    if not log_file.exists():
        rprint(f"[red]Log file not found:[/red] {log_file}")
        rprint("[dim]The Monitor Daemon may not have run yet.[/dim]")
        raise typer.Exit(1)

    rprint(f"[dim]Watching {log_file} (Ctrl-C to stop)[/dim]")
    print("-" * 60)

    try:
        subprocess.run(["tail", "-f", str(log_file)])
    except KeyboardInterrupt:
        print("\nStopped watching.")


# =============================================================================
# Supervisor Daemon Commands
# =============================================================================


@supervisor_daemon_app.callback(invoke_without_command=True)
def supervisor_daemon_default(ctx: typer.Context, session: SessionOption = "agents"):
    """Show supervisor daemon status (default when no subcommand given)."""
    if ctx.invoked_subcommand is None:
        _supervisor_daemon_status(session)


@supervisor_daemon_app.command("start")
def supervisor_daemon_start(
    interval: Annotated[
        int, typer.Option("--interval", "-i", help="Polling interval in seconds")
    ] = 10,
    session: SessionOption = "agents",
):
    """Start the Supervisor Daemon.

    The Supervisor Daemon handles Claude orchestration:
    - Launches daemon claude when sessions need attention
    - Waits for daemon claude to complete
    - Tracks interventions and steers

    Requires Monitor Daemon to be running (reads session state from it).
    """
    from ..supervisor_daemon import SupervisorDaemon, is_supervisor_daemon_running, get_supervisor_daemon_pid

    _daemon_control_start(
        "Supervisor Daemon", session, interval,
        is_supervisor_daemon_running, get_supervisor_daemon_pid, SupervisorDaemon,
    )


@supervisor_daemon_app.command("stop")
def supervisor_daemon_stop(session: SessionOption = "agents"):
    """Stop the running Supervisor Daemon."""
    from ..supervisor_daemon import stop_supervisor_daemon, is_supervisor_daemon_running, get_supervisor_daemon_pid

    _daemon_control_stop(
        "Supervisor Daemon", session,
        is_supervisor_daemon_running, get_supervisor_daemon_pid, stop_supervisor_daemon,
    )


@supervisor_daemon_app.command("status")
def supervisor_daemon_status_cmd(session: SessionOption = "agents"):
    """Show Supervisor Daemon status."""
    _supervisor_daemon_status(session)


def _supervisor_daemon_status(session: str):
    """Internal function for showing supervisor daemon status."""
    from ..supervisor_daemon import is_supervisor_daemon_running, get_supervisor_daemon_pid

    if not is_supervisor_daemon_running(session):
        rprint(f"[dim]Supervisor Daemon ({session}):[/dim] ○ stopped")
        return

    pid = get_supervisor_daemon_pid(session)
    rprint(f"[green]Supervisor Daemon ({session}):[/green] ● running (PID {pid})")


@supervisor_daemon_app.command("watch")
def supervisor_daemon_watch(session: SessionOption = "agents"):
    """Watch Supervisor Daemon logs in real-time."""
    import subprocess
    from ..settings import get_session_dir

    log_file = get_session_dir(session) / "supervisor_daemon.log"

    if not log_file.exists():
        rprint(f"[red]Log file not found:[/red] {log_file}")
        rprint("[dim]The Supervisor Daemon may not have run yet.[/dim]")
        raise typer.Exit(1)

    rprint(f"[dim]Watching {log_file} (Ctrl-C to stop)[/dim]")
    print("-" * 60)

    try:
        subprocess.run(["tail", "-f", str(log_file)])
    except KeyboardInterrupt:
        print("\nStopped watching.")
