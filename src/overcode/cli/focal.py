"""
`overcode focal-repo` — show or set the focal repo for a multi-repo agent (#170).
"""

from typing import Annotated, Optional

import typer
from rich import print as rprint

from ._shared import app, SessionOption


@app.command("focal-repo")
def focal_repo(
    name: Annotated[str, typer.Argument(help="Agent name")],
    subdir: Annotated[
        Optional[str],
        typer.Argument(
            help="Subdir to focus on. Pass `-` to clear. Omit to list candidates.",
        ),
    ] = None,
    session: SessionOption = "agents",
):
    """Show or set the focal repo for a multi-repo workspace agent (#170).

    With no subdir argument: lists the detected candidate repos and which
    one (if any) is currently focal.

    With a subdir: sets that subdir as the focal repo. Pass `-` to clear
    the focal back to the workspace root.

    For agents whose start_directory is itself a single git repo, this
    command reports that there's nothing to cycle through.

    Examples:
        overcode focal-repo my-agent             # list
        overcode focal-repo my-agent backend     # set focal
        overcode focal-repo my-agent -           # clear focal
    """
    from ..session_manager import SessionManager

    manager = SessionManager()
    agent = manager.get_session_by_name(name)
    if not agent:
        rprint(f"[red]Error: Agent '{name}' not found[/red]")
        raise typer.Exit(code=1)

    candidates = manager.detect_focal_repo_candidates(agent.start_directory)
    if not candidates:
        rprint(
            f"[yellow]'{name}' is a single-repo workspace at "
            f"{agent.start_directory or '(no start dir)'} — nothing to focus.[/yellow]"
        )
        return

    if subdir is None:
        # List mode
        rprint(f"[bold]{name}[/bold] focal candidates:")
        for c in candidates:
            marker = "→" if c == agent.focal_repo_subdir else " "
            rprint(f"  {marker} {c}")
        if not agent.focal_repo_subdir:
            rprint("  [dim](no focal set — sampling start_directory directly)[/dim]")
        return

    # Set mode
    if subdir == "-":
        manager.set_focal_repo(agent.id, None)
        rprint(f"[green]✓ Cleared focal repo for {name}[/green]")
        return
    try:
        manager.set_focal_repo(agent.id, subdir)
    except ValueError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(code=1)
    rprint(f"[green]✓ {name} focal repo: {subdir}[/green]")
