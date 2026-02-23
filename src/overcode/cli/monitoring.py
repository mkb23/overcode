"""
Monitoring commands: monitor, supervisor, web, export, instruct, heartbeat,
history, hook_handler_cmd, usage.
"""

from pathlib import Path
from typing import Annotated, Optional, List

import typer
from rich import print as rprint

from ._shared import app, SessionOption


@app.command("hook-handler", hidden=True)
def hook_handler_cmd():
    """Handle Claude Code hook events (internal).

    Called by Claude Code hooks, not by users directly.
    Reads event JSON from stdin, writes state for status detection,
    and outputs time-context for UserPromptSubmit events.
    """
    from ..hook_handler import handle_hook_event

    handle_hook_event()


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
    from ..session_manager import SessionManager
    from ..standing_instructions import resolve_instructions, load_presets

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
            rprint("[dim]Tip: Use 'overcode presets' to see available presets[/dim]")


def _signal_heartbeat_change(session: str) -> None:
    """Wake the monitor daemon so heartbeat status updates immediately (#212)."""
    from ..settings import signal_activity
    signal_activity(session)


@app.command()
def heartbeat(
    name: Annotated[str, typer.Argument(help="Name of agent")],
    enable: Annotated[
        bool, typer.Option("--enable", "-e", help="Enable heartbeat")
    ] = False,
    disable: Annotated[
        bool, typer.Option("--disable", "-d", help="Disable heartbeat")
    ] = False,
    pause: Annotated[
        bool, typer.Option("--pause", help="Pause heartbeat (keep config)")
    ] = False,
    resume: Annotated[
        bool, typer.Option("--resume", help="Resume paused heartbeat")
    ] = False,
    frequency: Annotated[
        Optional[str], typer.Option("--frequency", "-f", help="Interval (e.g., 300, 5m, 1h)")
    ] = None,
    instruction: Annotated[
        Optional[str], typer.Option("--instruction", "-i", help="Instruction to send")
    ] = None,
    show: Annotated[
        bool, typer.Option("--show", "-s", help="Show current heartbeat config")
    ] = False,
    session: SessionOption = "agents",
):
    """Configure heartbeat for an agent (#171).

    Heartbeat sends a periodic instruction to keep agents active or provide
    regular status updates. The instruction is sent at the configured frequency.

    Examples:
        overcode heartbeat my-agent --show              # Show current config
        overcode heartbeat my-agent -e -f 5m -i "Status check"  # Enable
        overcode heartbeat my-agent --pause             # Temporarily pause
        overcode heartbeat my-agent --resume            # Resume
        overcode heartbeat my-agent --disable           # Disable completely
    """
    from ..session_manager import SessionManager
    from ..tui_helpers import format_duration

    manager = SessionManager()
    agent = manager.get_session_by_name(name)
    if not agent:
        rprint(f"[red]Error: Agent '{name}' not found[/red]")
        raise typer.Exit(code=1)

    # Parse frequency if provided
    freq_seconds = None
    if frequency:
        freq = frequency.strip().lower()
        try:
            if freq.endswith('s'):
                freq_seconds = int(freq[:-1])
            elif freq.endswith('m'):
                freq_seconds = int(freq[:-1]) * 60
            elif freq.endswith('h'):
                freq_seconds = int(freq[:-1]) * 3600
            else:
                freq_seconds = int(freq)
        except ValueError:
            rprint(f"[red]Error: Invalid frequency format '{frequency}'[/red]")
            rprint("[dim]Use: 300, 5m, or 1h[/dim]")
            raise typer.Exit(code=1)

        if freq_seconds < 30:
            rprint("[red]Error: Minimum heartbeat interval is 30 seconds[/red]")
            raise typer.Exit(code=1)

    # Show current config
    if show or (not enable and not disable and not pause and not resume
                and not frequency and not instruction):
        if agent.heartbeat_enabled:
            freq_str = format_duration(agent.heartbeat_frequency_seconds)
            status = "[yellow]paused[/yellow]" if agent.heartbeat_paused else "[green]enabled[/green]"
            rprint(f"Heartbeat for '[bold]{name}[/bold]': {status}")
            rprint(f"  Frequency: {freq_str}")
            rprint(f"  Instruction: {agent.heartbeat_instruction or '(none)'}")
            if agent.last_heartbeat_time:
                rprint(f"  Last sent: {agent.last_heartbeat_time}")
        else:
            rprint(f"Heartbeat for '[bold]{name}[/bold]': [dim]disabled[/dim]")
        return

    # Disable
    if disable:
        manager.update_session(
            agent.id,
            heartbeat_enabled=False,
            heartbeat_paused=False,
            heartbeat_instruction="",
        )
        _signal_heartbeat_change(session)
        rprint(f"[green]✓ Heartbeat disabled for {name}[/green]")
        return

    # Pause
    if pause:
        if not agent.heartbeat_enabled:
            rprint(f"[yellow]Heartbeat is not enabled for {name}[/yellow]")
            return
        manager.update_session(agent.id, heartbeat_paused=True)
        _signal_heartbeat_change(session)
        rprint(f"[green]✓ Heartbeat paused for {name}[/green]")
        return

    # Resume
    if resume:
        if not agent.heartbeat_enabled:
            rprint(f"[yellow]Heartbeat is not enabled for {name}[/yellow]")
            return
        manager.update_session(agent.id, heartbeat_paused=False)
        _signal_heartbeat_change(session)
        rprint(f"[green]✓ Heartbeat resumed for {name}[/green]")
        return

    # Enable with frequency and instruction
    if enable:
        if not instruction:
            rprint("[red]Error: --instruction required when enabling heartbeat[/red]")
            raise typer.Exit(code=1)

        final_freq = freq_seconds or 300  # Default 5 minutes
        manager.update_session(
            agent.id,
            heartbeat_enabled=True,
            heartbeat_paused=False,
            heartbeat_frequency_seconds=final_freq,
            heartbeat_instruction=instruction,
        )
        _signal_heartbeat_change(session)
        rprint(f"[green]✓ Heartbeat enabled for {name}[/green]")
        rprint(f"  Frequency: {format_duration(final_freq)}")
        rprint(f"  Instruction: {instruction}")
        return

    # Update frequency or instruction without full enable
    updates = {}
    if freq_seconds:
        updates['heartbeat_frequency_seconds'] = freq_seconds
    if instruction:
        updates['heartbeat_instruction'] = instruction

    if updates:
        manager.update_session(agent.id, **updates)
        _signal_heartbeat_change(session)
        rprint(f"[green]✓ Heartbeat config updated for {name}[/green]")
        if freq_seconds:
            rprint(f"  Frequency: {format_duration(freq_seconds)}")
        if instruction:
            rprint(f"  Instruction: {instruction}")


@app.command()
def monitor(
    session: SessionOption = "agents",
    restart: Annotated[
        bool, typer.Option("--restart", help="Restart the monitor daemon before launching")
    ] = False,
    diagnostics: Annotated[
        bool, typer.Option("--diagnostics", help="Diagnostic mode: disable all auto-refresh timers")
    ] = False,
):
    """Launch the standalone TUI monitor."""
    if restart:
        from ..monitor_daemon import stop_monitor_daemon, is_monitor_daemon_running, get_monitor_daemon_pid
        from ..web_server import is_web_server_running, stop_web_server, start_web_server

        if is_monitor_daemon_running(session):
            pid = get_monitor_daemon_pid(session)
            if stop_monitor_daemon(session):
                rprint(f"[green]✓[/green] Monitor daemon stopped (was PID {pid})")
            else:
                rprint("[red]Failed to stop monitor daemon[/red]")
                raise typer.Exit(1)

        if is_web_server_running(session):
            ok, msg = stop_web_server(session)
            if ok:
                rprint("[green]✓[/green] Web server stopped")
                started, start_msg = start_web_server(session)
                if started:
                    rprint(f"[green]✓[/green] Web server restarted ({start_msg})")
                else:
                    rprint(f"[yellow]Warning: web server failed to restart: {start_msg}[/yellow]")

    from ..tui import run_tui

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

    script_dir = Path(__file__).parent.parent
    layout_script = script_dir / "supervisor_layout.sh"

    os.execvp("bash", ["bash", str(layout_script), session])


@app.command()
def web(
    host: Annotated[
        str, typer.Option("--host", "-h", help="Host to bind to")
    ] = "127.0.0.1",
    port: Annotated[
        int, typer.Option("--port", "-p", help="Port to listen on")
    ] = 8080,
    stop: Annotated[
        bool, typer.Option("--stop", help="Stop the running web server")
    ] = False,
    session: SessionOption = "agents",
):
    """Start or stop the web dashboard server (non-blocking).

    Starts the web server in the background and exits immediately.
    If the server is already running, shows the current URL.
    Use --stop to stop a running server.

    The server provides analytics (at /), live monitoring (at /dashboard),
    and the /api/status endpoint used by sister instances.

    Examples:
        overcode web                          # Start on localhost:8080
        overcode web --port 3000              # Custom port
        overcode web --host 0.0.0.0           # LAN access (needs api_key)
        overcode web --stop                   # Stop the server
    """
    from ..web_server import (
        start_web_server,
        stop_web_server,
        is_web_server_running,
        get_web_server_url,
    )

    if stop:
        success, msg = stop_web_server(session)
        if success:
            print("Web server stopped.")
        else:
            print(f"Web server: {msg}")
        return

    if is_web_server_running(session):
        url = get_web_server_url(session)
        print(f"Web server already running at {url}")
        return

    # Security: require API key when binding to non-localhost
    if host not in ("127.0.0.1", "localhost"):
        from ..config import get_web_api_key
        if not get_web_api_key():
            print("Error: Binding to non-localhost requires web.api_key in config.")
            print("Set it in ~/.overcode/config.yaml:")
            print()
            print("  web:")
            print('    api_key: "your-secret-key"')
            raise typer.Exit(1)

    success, msg = start_web_server(session, port, host)
    if success:
        print(f"Web server started: {msg}")
    else:
        print(f"Failed to start web server: {msg}")
        raise typer.Exit(1)


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
    from ..data_export import export_to_parquet

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
    from ..session_manager import SessionManager
    from ..tui_helpers import format_duration, format_tokens

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
        rprint("\n  [bold]Stats:[/bold]")
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

@app.command()
def usage():
    """Show Claude Code subscription usage (5h session + 7d weekly limits)."""
    from ..usage_monitor import UsageMonitor

    token = UsageMonitor._get_access_token()
    if token is None:
        rprint("[red]✗[/red] Could not retrieve Claude Code OAuth token from Keychain")
        raise typer.Exit(1)

    snap = UsageMonitor._fetch_usage(token)
    if snap.error:
        rprint(f"[red]✗[/red] API error: {snap.error}")
        raise typer.Exit(1)

    def _pct_style(pct: float) -> str:
        if pct >= 90:
            return "bold red"
        elif pct >= 75:
            return "bold yellow"
        elif pct >= 50:
            return "yellow"
        return "green"

    def _fmt_reset(reset_at: str | None) -> str:
        if not reset_at:
            return ""
        from datetime import datetime, timezone
        try:
            reset_dt = datetime.fromisoformat(reset_at)
            now = datetime.now(timezone.utc)
            if reset_dt.tzinfo is None:
                reset_dt = reset_dt.replace(tzinfo=timezone.utc)
            delta = reset_dt - now
            total_secs = int(delta.total_seconds())
            if total_secs <= 0:
                return " [dim](resetting now)[/dim]"
            hours, remainder = divmod(total_secs, 3600)
            minutes = remainder // 60
            if hours > 0:
                return f" [dim](resets in {hours}h {minutes}m)[/dim]"
            return f" [dim](resets in {minutes}m)[/dim]"
        except (ValueError, TypeError):
            return f" [dim](resets at {reset_at})[/dim]"

    five_h_style = _pct_style(snap.five_hour_pct)
    seven_d_style = _pct_style(snap.seven_day_pct)

    rprint("\n[bold]Claude Code Usage[/bold]\n")
    rprint(f"  5h session:  [{five_h_style}]{snap.five_hour_pct:.0f}%[/{five_h_style}]{_fmt_reset(snap.five_hour_resets_at)}")
    rprint(f"  7d weekly:   [{seven_d_style}]{snap.seven_day_pct:.0f}%[/{seven_d_style}]{_fmt_reset(snap.seven_day_resets_at)}")

    if snap.opus_pct is not None or snap.sonnet_pct is not None:
        rprint("\n  [bold]Model breakdown (7d):[/bold]")
        if snap.opus_pct is not None:
            opus_style = _pct_style(snap.opus_pct)
            rprint(f"    Opus:      [{opus_style}]{snap.opus_pct:.0f}%[/{opus_style}]")
        if snap.sonnet_pct is not None:
            sonnet_style = _pct_style(snap.sonnet_pct)
            rprint(f"    Sonnet:    [{sonnet_style}]{snap.sonnet_pct:.0f}%[/{sonnet_style}]")

    rprint(f"\n  [dim]Fetched at {snap.fetched_at:%H:%M:%S}[/dim]\n")
