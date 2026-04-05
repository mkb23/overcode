"""
Remote control toggle for agents.

Sends Claude Code's /remote-control slash command to an agent's tmux pane
to enable or disable remote connections from claude.ai/code.
"""

from typing import Annotated

import typer
from rich import print as rprint

from ._shared import app, SessionOption


@app.command("rc")
def rc(
    name: Annotated[str, typer.Argument(help="Name of agent")],
    action: Annotated[str, typer.Argument(help="'on' or 'off'")],
    session: SessionOption = "agents",
):
    """Toggle /remote-control on an agent.

    Enables or disables Claude Code's built-in remote control,
    which allows connections from claude.ai/code or the mobile app.

    Examples:
        overcode rc dispatch on    # Enable remote control
        overcode rc dispatch off   # Disable remote control
    """
    from ..launcher import ClaudeLauncher
    from ..session_manager import SessionManager

    if action not in ("on", "off"):
        rprint(f"[red]Error: action must be 'on' or 'off', got '{action}'[/red]")
        raise typer.Exit(1)

    sm = SessionManager()
    agent_session = sm.get_session_by_name(name)
    if not agent_session:
        rprint(f"[red]Error: agent '{name}' not found[/red]")
        raise typer.Exit(1)

    # Auto-wake sleeping agent
    if agent_session.is_asleep:
        sm.update_session(agent_session.id, is_asleep=False)
        rprint(f"[dim]Woke agent '{name}'[/dim]")

    launcher = ClaudeLauncher(session)

    if action == "on":
        success = launcher.send_to_session(name, "/remote-control")
        if success:
            rprint(f"[green]✓[/green] Sent /remote-control to '[bold]{name}[/bold]'")
        else:
            rprint(f"[red]✗[/red] Failed to send to '[bold]{name}[/bold]'")
            raise typer.Exit(1)
    else:
        # Send Escape to dismiss RC mode
        success = launcher.send_to_session(name, "escape", enter=False)
        if success:
            rprint(f"[green]✓[/green] Sent Escape to '[bold]{name}[/bold]' (RC off)")
        else:
            rprint(f"[red]✗[/red] Failed to send to '[bold]{name}[/bold]'")
            raise typer.Exit(1)
