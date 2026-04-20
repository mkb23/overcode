"""
Permissions commands: install (deprecated), uninstall, status.
"""

from typing import Annotated

import typer
from rich import print as rprint

from ._shared import perms_app

OVERCODE_SAFE_PERMS = [
    "Bash(overcode report *)",
    "Bash(overcode show *)",
    "Bash(overcode list *)",
    "Bash(overcode follow *)",
    "Bash(overcode kill *)",
    "Bash(overcode budget *)",
]

OVERCODE_PUNCHY_PERMS = [
    "Bash(overcode launch *)",
    "Bash(overcode send *)",
    "Bash(overcode instruct *)",
]


DEPRECATION_NOTE = (
    "[yellow]Note:[/yellow] Manual permission installation is deprecated.\n"
    "  Permissions are now injected automatically when agents are launched via 'overcode launch'.\n"
    "  Use 'overcode perms uninstall' to remove legacy permissions from your settings."
)


@perms_app.command("install", deprecated=True)
def perms_install(
    project: Annotated[
        bool,
        typer.Option("--project", "-p", help="Install to project-level .claude/settings.json instead of user-level"),
    ] = False,
    all_perms: Annotated[
        bool,
        typer.Option("--all", help="Include punchy permissions (launch, send, instruct)"),
    ] = False,
):
    """[Deprecated] Install overcode permissions into Claude Code settings.

    Permissions are now injected automatically at launch time via --settings.
    This command is no longer needed for overcode-launched agents.
    """
    rprint(DEPRECATION_NOTE)
    raise typer.Exit(0)


@perms_app.command("uninstall")
def perms_uninstall(
    project: Annotated[
        bool,
        typer.Option("--project", "-p", help="Uninstall from project-level .claude/settings.json instead of user-level"),
    ] = False,
):
    """Remove all overcode permissions from Claude Code settings."""
    from ..claude_config import ClaudeConfigEditor

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

    all_perms = OVERCODE_SAFE_PERMS + OVERCODE_PUNCHY_PERMS
    removed = 0
    for perm in all_perms:
        if editor.remove_permission(perm):
            removed += 1

    if removed > 0:
        rprint(f"[green]\u2713[/green] Removed {removed} permission(s) from {level} settings")
    else:
        rprint(f"[dim]No overcode permissions found in {level} settings[/dim]")


@perms_app.command("status")
def perms_status():
    """Show which overcode permissions are installed."""
    from ..claude_config import ClaudeConfigEditor

    rprint(f"\n{DEPRECATION_NOTE}\n")

    all_perms = OVERCODE_SAFE_PERMS + OVERCODE_PUNCHY_PERMS

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

        installed = editor.list_permissions_matching("Bash(overcode ")
        for perm in all_perms:
            if perm in installed:
                rprint(f"  {perm}  [green]\u2713[/green]")
            else:
                rprint(f"  {perm:<30} [dim]not installed[/dim]")
