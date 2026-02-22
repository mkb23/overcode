"""
Sister commands: list, add, remove, allow-control.
"""

from typing import Annotated, Optional

import typer
from rich import print as rprint

from ._shared import sister_app


@sister_app.callback(invoke_without_command=True)
def sister_default(ctx: typer.Context):
    """List configured sister instances (default when no subcommand given)."""
    if ctx.invoked_subcommand is None:
        _sister_list()


@sister_app.command("list")
def sister_list():
    """List configured sister instances."""
    _sister_list()


def _sister_list():
    """Internal function to display configured sisters."""
    from ..config import get_sisters_config

    sisters = get_sisters_config()
    if not sisters:
        rprint("[dim]No sister instances configured.[/dim]")
        rprint("[dim]Use 'overcode sister add <name> <url>' to add one.[/dim]")
        return

    rprint(f"[bold]Sister instances[/bold] ({len(sisters)}):\n")
    for s in sisters:
        api_key = s.get("api_key")
        if api_key:
            masked = api_key[:4] + "..." if len(api_key) > 4 else "****"
            rprint(f"  [bold]{s['name']}[/bold]  {s['url']}  api_key={masked}")
        else:
            rprint(f"  [bold]{s['name']}[/bold]  {s['url']}")


@sister_app.command("add")
def sister_add(
    name: Annotated[str, typer.Argument(help="Name for this sister instance")],
    url: Annotated[str, typer.Argument(help="URL of the sister's web server")],
    api_key: Annotated[
        Optional[str], typer.Option("--api-key", help="API key for authentication")
    ] = None,
):
    """Add a sister instance for cross-machine monitoring.

    Examples:
        overcode sister add macbook http://localhost:15337
        overcode sister add desktop http://192.168.1.10:5337 --api-key secret
    """
    from ..config import load_config, save_config

    config = load_config()
    sisters = config.get("sisters", [])
    if not isinstance(sisters, list):
        sisters = []

    # Reject duplicate name
    for s in sisters:
        if isinstance(s, dict) and s.get("name") == name:
            rprint(f"[red]Error: Sister '{name}' already exists[/red]")
            raise typer.Exit(code=1)

    entry = {"name": name, "url": url.rstrip("/")}
    if api_key:
        entry["api_key"] = api_key

    sisters.append(entry)
    config["sisters"] = sisters
    save_config(config)
    rprint(f"[green]✓ Added sister '{name}' at {entry['url']}[/green]")


@sister_app.command("remove")
def sister_remove(
    name: Annotated[str, typer.Argument(help="Name of the sister instance to remove")],
):
    """Remove a sister instance.

    Examples:
        overcode sister remove desktop
    """
    from ..config import load_config, save_config

    config = load_config()
    sisters = config.get("sisters", [])
    if not isinstance(sisters, list):
        sisters = []

    new_sisters = [s for s in sisters if not (isinstance(s, dict) and s.get("name") == name)]
    if len(new_sisters) == len(sisters):
        rprint(f"[red]Error: Sister '{name}' not found[/red]")
        raise typer.Exit(code=1)

    config["sisters"] = new_sisters
    save_config(config)
    rprint(f"[green]✓ Removed sister '{name}'[/green]")


@sister_app.command("allow-control")
def sister_allow_control(
    on: Annotated[bool, typer.Option("--on", help="Enable remote control")] = False,
    off: Annotated[bool, typer.Option("--off", help="Disable remote control")] = False,
):
    """Show or toggle remote control for this machine's web server.

    When enabled, sister instances can send commands (kill, restart, send
    instructions, etc.) to agents on this machine via POST endpoints.

    Examples:
        overcode sister allow-control          # Show current status
        overcode sister allow-control --on     # Enable remote control
        overcode sister allow-control --off    # Disable remote control
    """
    from ..config import load_config, save_config, get_web_api_key

    config = load_config()
    web = config.setdefault("web", {})

    if on and off:
        rprint("[red]Error: Cannot use --on and --off together[/red]")
        raise typer.Exit(code=1)

    if on:
        api_key = get_web_api_key()
        web["allow_control"] = True
        save_config(config)
        rprint(f"[green]✓ Remote control enabled (web.allow_control = true)[/green]")
        if api_key:
            masked_key = api_key[:4] + "..."
            rprint(f"  API key: {masked_key}")
        else:
            rprint(f"  [yellow]Warning: web.api_key is not set — endpoints are unauthenticated[/yellow]")
            rprint(f"  [dim]This is fine if you're using SSH tunnels. Otherwise set it in ~/.overcode/config.yaml:[/dim]")
            rprint()
            rprint("  web:")
            rprint('    api_key: "your-secret-key"')
        rprint(f"  Restart web server for changes to take effect.")
    elif off:
        web["allow_control"] = False
        save_config(config)
        rprint(f"[green]✓ Remote control disabled (web.allow_control = false)[/green]")
    else:
        # Show current status
        enabled = web.get("allow_control", False)
        api_key = get_web_api_key()
        if enabled:
            masked_key = (api_key[:4] + "...") if api_key else "(not set)"
            rprint(f"Remote control: [green]enabled[/green]")
            rprint(f"  API key: {masked_key}")
        else:
            rprint(f"Remote control: [red]disabled[/red]")
            if not api_key:
                rprint(f"  [dim]Note: web.api_key is also not set[/dim]")
