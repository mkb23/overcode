"""
Hooks commands: install (deprecated), uninstall, status.
"""

from typing import Annotated

import typer
from rich import print as rprint

from ._shared import hooks_app


DEPRECATION_NOTE = (
    "[yellow]Note:[/yellow] Manual hook installation is deprecated.\n"
    "  Hooks are now injected automatically when agents are launched via 'overcode launch'.\n"
    "  Use 'overcode hooks uninstall' to remove legacy hooks from your settings."
)


@hooks_app.command("install", deprecated=True)
def hooks_install(
    project: Annotated[
        bool,
        typer.Option("--project", "-p", help="Install to project-level .claude/settings.json instead of user-level"),
    ] = False,
):
    """[Deprecated] Install overcode hooks into Claude Code settings.

    Hooks are now injected automatically at launch time via --settings.
    This command is no longer needed for overcode-launched agents.
    """
    rprint(DEPRECATION_NOTE)
    raise typer.Exit(0)


@hooks_app.command("uninstall")
def hooks_uninstall(
    project: Annotated[
        bool,
        typer.Option("--project", "-p", help="Uninstall from project-level .claude/settings.json instead of user-level"),
    ] = False,
):
    """Remove all overcode hooks from Claude Code settings."""
    from ..claude_config import ClaudeConfigEditor
    from ..hook_handler import OVERCODE_HOOKS

    if project:
        editor = ClaudeConfigEditor.project_level()
        level = "project"
    else:
        editor = ClaudeConfigEditor.user_level()
        level = "user"

    try:
        editor.load()
    except ValueError as e:
        rprint(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    removed = 0
    for event, command in OVERCODE_HOOKS:
        if editor.remove_hook(event, command):
            removed += 1

    if removed > 0:
        rprint(f"[green]\u2713[/green] Removed {removed} hook(s) from {level} settings")
    else:
        rprint(f"[dim]No overcode hooks found in {level} settings[/dim]")


@hooks_app.command("status")
def hooks_status():
    """Show which overcode hooks are installed."""
    from ..claude_config import ClaudeConfigEditor
    from ..hook_handler import OVERCODE_HOOKS

    rprint(f"\n{DEPRECATION_NOTE}\n")

    for level_name, editor in [
        ("User-level", ClaudeConfigEditor.user_level()),
        ("Project-level", ClaudeConfigEditor.project_level()),
    ]:
        try:
            editor.load()
        except ValueError:
            rprint(f"\n{level_name} ({editor.path}):")
            rprint("  [red](invalid JSON)[/red]")
            continue

        if not editor.path.exists():
            rprint(f"\n{level_name} ({editor.path}):")
            rprint("  [dim](no settings file)[/dim]")
            continue

        rprint(f"\n{level_name} ({editor.path}):")

        for event, command in OVERCODE_HOOKS:
            if editor.has_hook(event, command):
                rprint(f"  {event:<20} {command}  [green]\u2713[/green]")
            else:
                rprint(f"  {event:<20} [dim]not installed[/dim]")
