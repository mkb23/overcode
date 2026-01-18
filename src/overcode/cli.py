"""
CLI interface for Overcode using Typer.
"""

import sys
from pathlib import Path
from typing import Annotated, Optional, List

import typer
from rich import print as rprint
from rich.console import Console

from .launcher import ClaudeLauncher

# Main app
app = typer.Typer(
    name="overcode",
    help="Manage and supervise Claude Code agents",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


# Monitor daemon subcommand group
monitor_daemon_app = typer.Typer(
    name="monitor-daemon",
    help="Manage the Monitor Daemon (metrics/state tracking)",
    no_args_is_help=False,
    invoke_without_command=True,
)
app.add_typer(monitor_daemon_app, name="monitor-daemon")

# Supervisor daemon subcommand group
supervisor_daemon_app = typer.Typer(
    name="supervisor-daemon",
    help="Manage the Supervisor Daemon (Claude orchestration)",
    no_args_is_help=False,
    invoke_without_command=True,
)
app.add_typer(supervisor_daemon_app, name="supervisor-daemon")

# Console for rich output
console = Console()

# Global session option (hidden advanced usage)
SessionOption = Annotated[
    str,
    typer.Option(
        "--session",
        hidden=True,
        help="Tmux session name for agents",
    ),
]


# =============================================================================
# Agent Commands
# =============================================================================


@app.command()
def launch(
    name: Annotated[str, typer.Option("--name", "-n", help="Name for the agent")],
    directory: Annotated[
        Optional[str], typer.Option("--directory", "-d", help="Working directory")
    ] = None,
    prompt: Annotated[
        Optional[str], typer.Option("--prompt", "-p", help="Initial prompt to send")
    ] = None,
    skip_permissions: Annotated[
        bool,
        typer.Option(
            "--skip-permissions",
            help="Auto-deny permission prompts (--permission-mode dontAsk)",
        ),
    ] = False,
    bypass_permissions: Annotated[
        bool,
        typer.Option(
            "--bypass-permissions",
            help="Bypass all permission checks (--dangerously-skip-permissions)",
        ),
    ] = False,
    session: SessionOption = "agents",
):
    """Launch a new Claude agent."""
    import os

    # Default to current directory if not specified
    working_dir = directory if directory else os.getcwd()

    launcher = ClaudeLauncher(session)

    result = launcher.launch(
        name=name,
        start_directory=working_dir,
        initial_prompt=prompt,
        skip_permissions=skip_permissions,
        dangerously_skip_permissions=bypass_permissions,
    )

    if result:
        rprint(f"\n[green]✓[/green] Agent '[bold]{name}[/bold]' launched")
        if prompt:
            rprint("  Initial prompt sent")
        rprint("\nTo view: [bold]overcode attach[/bold]")


@app.command("list")
def list_agents(session: SessionOption = "agents"):
    """List running agents with status."""
    from .status_detector import StatusDetector
    from .history_reader import get_session_stats
    from .tui_helpers import (
        calculate_uptime, format_duration, format_tokens,
        get_current_state_times, get_status_symbol
    )

    launcher = ClaudeLauncher(session)
    sessions = launcher.list_sessions()

    if not sessions:
        rprint("[dim]No running agents[/dim]")
        return

    status_detector = StatusDetector(session)
    terminated_count = 0

    for sess in sessions:
        # For terminated sessions, use stored status; otherwise detect from tmux
        if sess.status == "terminated":
            status = "terminated"
            activity = "(tmux window no longer exists)"
            terminated_count += 1
        else:
            status, activity, _ = status_detector.detect_status(sess)

        symbol, _ = get_status_symbol(status)

        # Calculate uptime using shared helper
        uptime = calculate_uptime(sess.start_time) if sess.start_time else "?"

        # Get state times using shared helper
        green_time, non_green_time = get_current_state_times(sess.stats)

        # Get stats from Claude Code history and session files
        stats = get_session_stats(sess)
        if stats:
            stats_display = f"{stats.interaction_count:>2}i {format_tokens(stats.total_tokens):>5}"
        else:
            stats_display = " -i     -"

        print(
            f"{symbol} {sess.name:<16} ↑{uptime:>5}  "
            f"▶{format_duration(green_time):>5} ⏸{format_duration(non_green_time):>5}  "
            f"{stats_display}  {activity[:50]}"
        )

    if terminated_count > 0:
        rprint(f"\n[dim]{terminated_count} terminated session(s). Run 'overcode cleanup' to remove.[/dim]")


@app.command()
def attach(session: SessionOption = "agents"):
    """Attach to the tmux session to view agents."""
    launcher = ClaudeLauncher(session)
    rprint("[dim]Attaching to overcode...[/dim]")
    rprint("[dim](Ctrl-b d to detach, Ctrl-b <number> to switch agents)[/dim]")
    launcher.attach()


@app.command()
def kill(
    name: Annotated[str, typer.Argument(help="Name of agent to kill")],
    session: SessionOption = "agents",
):
    """Kill a running agent."""
    launcher = ClaudeLauncher(session)
    launcher.kill_session(name)


@app.command()
def cleanup(session: SessionOption = "agents"):
    """Remove terminated sessions from tracking.

    Terminated sessions are those whose tmux window no longer exists
    (e.g., after a machine reboot). Use 'overcode list' to see them.
    """
    launcher = ClaudeLauncher(session)
    count = launcher.cleanup_terminated_sessions()
    if count > 0:
        rprint(f"[green]✓ Cleaned up {count} terminated session(s)[/green]")
    else:
        rprint("[dim]No terminated sessions to clean up[/dim]")


@app.command()
def send(
    name: Annotated[str, typer.Argument(help="Name of agent")],
    text: Annotated[
        Optional[List[str]], typer.Argument(help="Text to send (or special key: enter, escape)")
    ] = None,
    no_enter: Annotated[
        bool, typer.Option("--no-enter", help="Don't press Enter after text")
    ] = False,
    session: SessionOption = "agents",
):
    """
    Send input to an agent.

    Special keys: enter, escape, tab, up, down, left, right

    Examples:
        overcode send my-agent "yes"           # Send "yes" + Enter
        overcode send my-agent enter           # Just press Enter (approve)
        overcode send my-agent escape          # Press Escape (reject)
        overcode send my-agent --no-enter "y"  # Send "y" without Enter
    """
    launcher = ClaudeLauncher(session)

    # Join all text parts if multiple were given
    text_str = " ".join(text) if text else ""
    enter = not no_enter

    if launcher.send_to_session(name, text_str, enter=enter):
        if text_str.lower() in ("enter", "escape", "esc"):
            rprint(f"[green]✓[/green] Sent {text_str.upper()} to '[bold]{name}[/bold]'")
        elif enter:
            display = text_str[:50] + "..." if len(text_str) > 50 else text_str
            rprint(f"[green]✓[/green] Sent to '[bold]{name}[/bold]': {display}")
        else:
            display = text_str[:50] + "..." if len(text_str) > 50 else text_str
            rprint(f"[green]✓[/green] Sent (no enter) to '[bold]{name}[/bold]': {display}")
    else:
        rprint(f"[red]✗[/red] Failed to send to '[bold]{name}[/bold]'")
        raise typer.Exit(1)


@app.command()
def show(
    name: Annotated[str, typer.Argument(help="Name of agent")],
    lines: Annotated[
        int, typer.Option("--lines", "-n", help="Number of lines to show")
    ] = 50,
    session: SessionOption = "agents",
):
    """Show recent output from an agent."""
    launcher = ClaudeLauncher(session)

    output = launcher.get_session_output(name, lines=lines)
    if output is not None:
        print(f"=== {name} (last {lines} lines) ===")
        print(output)
        print(f"=== end {name} ===")
    else:
        rprint(f"[red]✗[/red] Could not get output from '[bold]{name}[/bold]'")
        raise typer.Exit(1)


@app.command()
def instruct(
    name: Annotated[
        Optional[str], typer.Argument(help="Name of agent")
    ] = None,
    instructions: Annotated[
        Optional[List[str]],
        typer.Argument(help="Instructions or preset name (e.g., DO_NOTHING, STANDARD, CODING)"),
    ] = None,
    clear: Annotated[
        bool, typer.Option("--clear", "-c", help="Clear standing instructions")
    ] = False,
    list_presets: Annotated[
        bool, typer.Option("--list", "-l", help="List available presets")
    ] = False,
    session: SessionOption = "agents",
):
    """Set standing instructions for an agent.

    Use a preset name (DO_NOTHING, STANDARD, CODING, etc.) or provide custom instructions.
    Use --list to see all available presets.
    """
    from .session_manager import SessionManager
    from .standing_instructions import resolve_instructions, load_presets

    if list_presets:
        presets_dict = load_presets()
        rprint("\n[bold]Standing Instruction Presets:[/bold]\n")
        for preset_name in sorted(presets_dict.keys(), key=lambda x: (x != "DO_NOTHING", x)):
            preset = presets_dict[preset_name]
            rprint(f"  [cyan]{preset_name:12}[/cyan] {preset.description}")
        rprint("\n[dim]Usage: overcode instruct <agent> <PRESET>[/dim]")
        rprint("[dim]       overcode instruct <agent> \"custom instructions\"[/dim]")
        rprint("[dim]Config: ~/.overcode/presets.json[/dim]\n")
        return

    if not name:
        rprint("[red]Error:[/red] Agent name required")
        rprint("[dim]Usage: overcode instruct <agent> <PRESET or instructions>[/dim]")
        raise typer.Exit(1)

    sessions = SessionManager()
    sess = sessions.get_session_by_name(name)

    if sess is None:
        rprint(f"[red]✗[/red] Agent '[bold]{name}[/bold]' not found")
        raise typer.Exit(1)

    instructions_str = " ".join(instructions) if instructions else ""

    if clear:
        sessions.set_standing_instructions(sess.id, "", preset_name=None)
        rprint(f"[green]✓[/green] Cleared standing instructions for '[bold]{name}[/bold]'")
    elif instructions_str:
        # Resolve preset or use as custom instructions
        full_instructions, preset_name = resolve_instructions(instructions_str)
        sessions.set_standing_instructions(sess.id, full_instructions, preset_name=preset_name)

        if preset_name:
            rprint(f"[green]✓[/green] Set '[bold]{name}[/bold]' to [cyan]{preset_name}[/cyan] preset")
            rprint(f"  [dim]{full_instructions[:80]}...[/dim]" if len(full_instructions) > 80 else f"  [dim]{full_instructions}[/dim]")
        else:
            rprint(f"[green]✓[/green] Set standing instructions for '[bold]{name}[/bold]':")
            rprint(f'  "{instructions_str}"')
    else:
        # Show current instructions
        if sess.standing_instructions:
            if sess.standing_instructions_preset:
                rprint(f"Standing instructions for '[bold]{name}[/bold]': [cyan]{sess.standing_instructions_preset}[/cyan] preset")
            else:
                rprint(f"Standing instructions for '[bold]{name}[/bold]':")
            rprint(f'  "{sess.standing_instructions}"')
        else:
            rprint(f"[dim]No standing instructions set for '{name}'[/dim]")
            rprint(f"[dim]Tip: Use 'overcode presets' to see available presets[/dim]")


# =============================================================================
# Monitoring Commands
# =============================================================================


@app.command()
def monitor(
    session: SessionOption = "agents",
    diagnostics: Annotated[
        bool, typer.Option("--diagnostics", help="Diagnostic mode: disable all auto-refresh timers")
    ] = False,
):
    """Launch the standalone TUI monitor."""
    from .tui import run_tui

    run_tui(session, diagnostics=diagnostics)


@app.command()
def supervisor(
    restart: Annotated[
        bool, typer.Option("--restart", help="Restart if already running")
    ] = False,
    session: SessionOption = "agents",
):
    """Launch the TUI monitor with embedded controller Claude."""
    import subprocess
    import os

    if restart:
        rprint("[dim]Killing existing controller session...[/dim]")
        result = subprocess.run(
            ["tmux", "kill-session", "-t", "overcode-controller"],
            capture_output=True,
        )
        if result.returncode == 0:
            rprint("[green]✓[/green] Existing session killed")

    script_dir = Path(__file__).parent
    layout_script = script_dir / "supervisor_layout.sh"

    os.execvp("bash", ["bash", str(layout_script), session])


@app.command()
def serve(
    host: Annotated[
        str, typer.Option("--host", "-h", help="Host to bind to")
    ] = "0.0.0.0",
    port: Annotated[
        int, typer.Option("--port", "-p", help="Port to listen on")
    ] = 8080,
    session: SessionOption = "agents",
):
    """Start web dashboard server for remote monitoring.

    Provides a mobile-optimized read-only dashboard that displays
    agent status and timeline data. Auto-refreshes every 5 seconds.

    Access from your phone at http://<your-ip>:8080

    Examples:
        overcode serve                    # Listen on all interfaces, port 8080
        overcode serve --port 3000        # Custom port
        overcode serve --host 127.0.0.1   # Local only
    """
    from .web_server import run_server

    run_server(host=host, port=port, tmux_session=session)


@app.command()
def web(
    host: Annotated[
        str, typer.Option("--host", "-h", help="Host to bind to")
    ] = "127.0.0.1",
    port: Annotated[
        int, typer.Option("--port", "-p", help="Port to listen on")
    ] = 8080,
):
    """Launch analytics web dashboard for browsing historical data.

    A lightweight web app for exploring session history, timeline
    visualization, and efficiency metrics. Uses Chart.js for
    interactive charts with dark theme matching the TUI.

    Features:
        - Dashboard with summary stats and daily activity charts
        - Session browser with sortable table
        - Timeline view with agent status and user presence
        - Efficiency metrics with cost analysis

    Time range presets can be configured in ~/.overcode/config.yaml:

        web:
          time_presets:
            - name: "Morning"
              start: "09:00"
              end: "12:00"

    Examples:
        overcode web                    # Start on localhost:8080
        overcode web --port 3000        # Custom port
        overcode web --host 0.0.0.0     # Listen on all interfaces
    """
    from .web_server import run_analytics_server

    run_analytics_server(host=host, port=port)




@app.command()
def export(
    output: Annotated[
        str, typer.Argument(help="Output file path (.parquet)")
    ],
    include_archived: Annotated[
        bool, typer.Option("--archived", "-a", help="Include archived sessions")
    ] = True,
    include_timeline: Annotated[
        bool, typer.Option("--timeline", "-t", help="Include timeline data")
    ] = True,
    include_presence: Annotated[
        bool, typer.Option("--presence", "-p", help="Include presence data")
    ] = True,
):
    """Export session data to Parquet format for Jupyter analysis.

    Creates a parquet file with session stats, timeline history,
    and presence data suitable for pandas/jupyter analysis.
    """
    from .data_export import export_to_parquet

    try:
        result = export_to_parquet(
            output,
            include_archived=include_archived,
            include_timeline=include_timeline,
            include_presence=include_presence,
        )
        rprint(f"[green]✓[/green] Exported to [bold]{output}[/bold]")
        rprint(f"  Sessions: {result['sessions_count']}")
        if include_archived:
            rprint(f"  Archived: {result['archived_count']}")
        if include_timeline:
            rprint(f"  Timeline rows: {result['timeline_rows']}")
        if include_presence:
            rprint(f"  Presence rows: {result['presence_rows']}")
    except ImportError as e:
        rprint(f"[red]Error:[/red] {e}")
        rprint("[dim]Install pyarrow: pip install pyarrow[/dim]")
        raise typer.Exit(1)
    except Exception as e:
        rprint(f"[red]Export failed:[/red] {e}")
        raise typer.Exit(1)


@app.command()
def history(
    name: Annotated[
        Optional[str], typer.Argument(help="Agent name (omit for all archived)")
    ] = None,
):
    """Show archived session history."""
    from .session_manager import SessionManager
    from .tui_helpers import format_duration, format_tokens

    sessions = SessionManager()

    if name:
        # Show specific archived session
        archived = sessions.list_archived_sessions()
        session = next((s for s in archived if s.name == name), None)
        if not session:
            rprint(f"[red]✗[/red] No archived session named '[bold]{name}[/bold]'")
            raise typer.Exit(1)

        rprint(f"\n[bold]{session.name}[/bold]")
        rprint(f"  ID: {session.id}")
        rprint(f"  Started: {session.start_time}")
        end_time = getattr(session, '_end_time', None)
        if end_time:
            rprint(f"  Ended: {end_time}")
        rprint(f"  Directory: {session.start_directory or '-'}")
        rprint(f"  Repo: {session.repo_name or '-'} ({session.branch or '-'})")
        rprint(f"\n  [bold]Stats:[/bold]")
        stats = session.stats
        rprint(f"    Interactions: {stats.interaction_count}")
        rprint(f"    Tokens: {format_tokens(stats.total_tokens)}")
        rprint(f"    Cost: ${stats.estimated_cost_usd:.4f}")
        rprint(f"    Green time: {format_duration(stats.green_time_seconds)}")
        rprint(f"    Non-green time: {format_duration(stats.non_green_time_seconds)}")
        rprint(f"    Steers: {stats.steers_count}")
    else:
        # List all archived sessions
        archived = sessions.list_archived_sessions()
        if not archived:
            rprint("[dim]No archived sessions[/dim]")
            return

        rprint(f"\n[bold]Archived Sessions ({len(archived)}):[/bold]\n")
        for s in sorted(archived, key=lambda x: x.start_time, reverse=True):
            end_time = getattr(s, '_end_time', None)
            stats = s.stats
            duration = ""
            if end_time and s.start_time:
                try:
                    from datetime import datetime
                    start = datetime.fromisoformat(s.start_time)
                    end = datetime.fromisoformat(end_time)
                    dur_sec = (end - start).total_seconds()
                    duration = f" ({format_duration(dur_sec)})"
                except ValueError:
                    pass

            rprint(
                f"  {s.name:<16} {stats.interaction_count:>3}i "
                f"{format_tokens(stats.total_tokens):>6} "
                f"${stats.estimated_cost_usd:.2f}{duration}"
            )


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
    - User presence state (macOS only)
    """
    from .monitor_daemon import MonitorDaemon, is_monitor_daemon_running, get_monitor_daemon_pid

    if is_monitor_daemon_running(session):
        pid = get_monitor_daemon_pid(session)
        rprint(f"[yellow]Monitor Daemon already running[/yellow] (PID {pid}) for session '{session}'")
        raise typer.Exit(1)

    rprint(f"[dim]Starting Monitor Daemon for session '{session}' with interval {interval}s...[/dim]")
    daemon = MonitorDaemon(session)
    daemon.run(interval)


@monitor_daemon_app.command("stop")
def monitor_daemon_stop(session: SessionOption = "agents"):
    """Stop the running Monitor Daemon."""
    from .monitor_daemon import stop_monitor_daemon, is_monitor_daemon_running, get_monitor_daemon_pid

    if not is_monitor_daemon_running(session):
        rprint(f"[dim]Monitor Daemon is not running for session '{session}'[/dim]")
        return

    pid = get_monitor_daemon_pid(session)
    if stop_monitor_daemon(session):
        rprint(f"[green]✓[/green] Monitor Daemon stopped (was PID {pid}) for session '{session}'")
    else:
        rprint("[red]Failed to stop Monitor Daemon[/red]")
        raise typer.Exit(1)


@monitor_daemon_app.command("status")
def monitor_daemon_status_cmd(session: SessionOption = "agents"):
    """Show Monitor Daemon status."""
    _monitor_daemon_status(session)


def _monitor_daemon_status(session: str):
    """Internal function for showing monitor daemon status."""
    from .monitor_daemon import is_monitor_daemon_running, get_monitor_daemon_pid
    from .monitor_daemon_state import get_monitor_daemon_state
    from .settings import get_monitor_daemon_state_path

    state_path = get_monitor_daemon_state_path(session)

    if not is_monitor_daemon_running(session):
        rprint(f"[dim]Monitor Daemon ({session}):[/dim] ○ stopped")
        state = get_monitor_daemon_state(session)
        if state and state.last_loop_time:
            from .tui_helpers import format_ago
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
            from .tui_helpers import format_ago
            rprint(f"  Last loop: {format_ago(state.last_loop_time)}")
        if state.presence_available:
            rprint(f"  Presence: state={state.presence_state}, idle={state.presence_idle_seconds:.0f}s")


@monitor_daemon_app.command("watch")
def monitor_daemon_watch(session: SessionOption = "agents"):
    """Watch Monitor Daemon logs in real-time."""
    import subprocess
    from .settings import get_session_dir

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
    from .supervisor_daemon import SupervisorDaemon, is_supervisor_daemon_running, get_supervisor_daemon_pid

    if is_supervisor_daemon_running(session):
        pid = get_supervisor_daemon_pid(session)
        rprint(f"[yellow]Supervisor Daemon already running[/yellow] (PID {pid}) for session '{session}'")
        raise typer.Exit(1)

    rprint(f"[dim]Starting Supervisor Daemon for session '{session}' with interval {interval}s...[/dim]")
    daemon = SupervisorDaemon(session)
    daemon.run(interval)


@supervisor_daemon_app.command("stop")
def supervisor_daemon_stop(session: SessionOption = "agents"):
    """Stop the running Supervisor Daemon."""
    from .supervisor_daemon import stop_supervisor_daemon, is_supervisor_daemon_running, get_supervisor_daemon_pid

    if not is_supervisor_daemon_running(session):
        rprint(f"[dim]Supervisor Daemon is not running for session '{session}'[/dim]")
        return

    pid = get_supervisor_daemon_pid(session)
    if stop_supervisor_daemon(session):
        rprint(f"[green]✓[/green] Supervisor Daemon stopped (was PID {pid}) for session '{session}'")
    else:
        rprint("[red]Failed to stop Supervisor Daemon[/red]")
        raise typer.Exit(1)


@supervisor_daemon_app.command("status")
def supervisor_daemon_status_cmd(session: SessionOption = "agents"):
    """Show Supervisor Daemon status."""
    _supervisor_daemon_status(session)


def _supervisor_daemon_status(session: str):
    """Internal function for showing supervisor daemon status."""
    from .supervisor_daemon import is_supervisor_daemon_running, get_supervisor_daemon_pid

    if not is_supervisor_daemon_running(session):
        rprint(f"[dim]Supervisor Daemon ({session}):[/dim] ○ stopped")
        return

    pid = get_supervisor_daemon_pid(session)
    rprint(f"[green]Supervisor Daemon ({session}):[/green] ● running (PID {pid})")


@supervisor_daemon_app.command("watch")
def supervisor_daemon_watch(session: SessionOption = "agents"):
    """Watch Supervisor Daemon logs in real-time."""
    import subprocess
    from .settings import get_session_dir

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


# =============================================================================
# Summarizer Commands
# =============================================================================


@app.command()
def summarizer(
    action: Annotated[
        str, typer.Argument(help="Action: on, off, or status")
    ] = "status",
    session: SessionOption = "agents",
):
    """Control the agent activity summarizer.

    The summarizer uses GPT-4o-mini to generate human-readable summaries
    of what each agent has been doing. Requires OPENAI_API_KEY env var.

    Examples:
        overcode summarizer status  # Check current state
        overcode summarizer on      # Enable summarizer
        overcode summarizer off     # Disable summarizer
    """
    from .summarizer_component import (
        set_summarizer_enabled,
        is_summarizer_enabled,
        SummarizerClient,
    )
    from .monitor_daemon_state import get_monitor_daemon_state

    action = action.lower()

    if action == "status":
        # Check if API key is available
        api_available = SummarizerClient.is_available()
        enabled = is_summarizer_enabled(session)

        # Get stats from daemon state
        state = get_monitor_daemon_state(session)

        rprint(f"[bold]Summarizer Status ({session}):[/bold]")
        rprint(f"  API key: {'[green]available[/green]' if api_available else '[red]not set[/red] (export OPENAI_API_KEY=...)'}")
        rprint(f"  Enabled: {'[green]yes[/green]' if enabled else '[dim]no[/dim]'}")

        if state:
            rprint(f"  API calls: {state.summarizer_calls}")
            rprint(f"  Est. cost: ${state.summarizer_cost_usd:.4f}")

    elif action == "on":
        if not SummarizerClient.is_available():
            rprint("[red]Error:[/red] OPENAI_API_KEY environment variable not set")
            rprint("[dim]Export your API key: export OPENAI_API_KEY='sk-...'[/dim]")
            raise typer.Exit(1)

        set_summarizer_enabled(session, True)
        rprint(f"[green]✓[/green] Summarizer enabled for session '{session}'")
        rprint("[dim]Summaries will appear in the web dashboard and TUI[/dim]")

    elif action == "off":
        set_summarizer_enabled(session, False)
        rprint(f"[green]✓[/green] Summarizer disabled for session '{session}'")

    else:
        rprint(f"[red]Unknown action:[/red] {action}")
        rprint("[dim]Use: on, off, or status[/dim]")
        raise typer.Exit(1)


# =============================================================================
# Entry Point
# =============================================================================


def main():
    """Main entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
