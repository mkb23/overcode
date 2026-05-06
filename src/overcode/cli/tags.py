"""
Tag commands: tag, untag, tags (list) — #356.

Tags group agents for filtering/sorting. They're free-form strings stored
on the Session, lower-cased on write, persisted in sessions.json.
"""

from typing import Annotated, List

import typer
from rich import print as rprint
from rich.table import Table

from ._shared import app, SessionOption


@app.command("tag")
def tag_add(
    name: Annotated[str, typer.Argument(help="Agent name")],
    tags: Annotated[List[str], typer.Argument(help="One or more tags to add")],
    session: SessionOption = "agents",
):
    """Add tags to an agent (#356).

    Tags are lower-cased and de-duplicated. Use space-separated values
    to add several at once.

    Examples:
        overcode tag my-agent backend hot-path
        overcode tag my-agent waiting-on-review
    """
    from ..session_manager import SessionManager

    manager = SessionManager()
    agent = manager.get_session_by_name(name)
    if not agent:
        rprint(f"[red]Error: Agent '{name}' not found[/red]")
        raise typer.Exit(code=1)

    new_tags = manager.add_tags(agent.id, tags)
    if not new_tags:
        rprint(f"[yellow]No tags applied[/yellow]")
        return
    rprint(f"[green]✓ {name} tags: {', '.join(new_tags)}[/green]")


@app.command("untag")
def tag_remove(
    name: Annotated[str, typer.Argument(help="Agent name")],
    tags: Annotated[
        List[str],
        typer.Argument(
            help="Tags to remove (omit to clear all)",
        ),
    ] = None,
    session: SessionOption = "agents",
):
    """Remove tags from an agent (#356).

    With no tags listed, all tags are cleared.

    Examples:
        overcode untag my-agent waiting-on-review
        overcode untag my-agent              # clear all
    """
    from ..session_manager import SessionManager

    manager = SessionManager()
    agent = manager.get_session_by_name(name)
    if not agent:
        rprint(f"[red]Error: Agent '{name}' not found[/red]")
        raise typer.Exit(code=1)

    remaining = manager.remove_tags(agent.id, tags or [])
    if remaining:
        rprint(f"[green]✓ {name} tags: {', '.join(remaining)}[/green]")
    else:
        rprint(f"[green]✓ {name} has no tags[/green]")


@app.command("tags")
def tags_list(
    name: Annotated[
        str, typer.Argument(help="Agent name (omit to list all agents' tags)")
    ] = None,
    session: SessionOption = "agents",
):
    """List tags for one agent or all agents (#356).

    Examples:
        overcode tags                # all agents and their tags
        overcode tags my-agent       # tags for just one agent
    """
    from ..session_manager import SessionManager

    manager = SessionManager()
    if name:
        agent = manager.get_session_by_name(name)
        if not agent:
            rprint(f"[red]Error: Agent '{name}' not found[/red]")
            raise typer.Exit(code=1)
        if agent.tags:
            rprint(f"{agent.name}: {', '.join(agent.tags)}")
        else:
            rprint(f"[dim]{agent.name}: (no tags)[/dim]")
        return

    agents = manager.list_sessions()
    if not agents:
        rprint("[dim]No agents[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Agent")
    table.add_column("Tags")
    for agent in agents:
        if agent.tags:
            table.add_row(agent.name, ", ".join(agent.tags))
        else:
            table.add_row(agent.name, "[dim]—[/dim]")
    rprint(table)
