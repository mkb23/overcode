"""
Permissions commands: install, uninstall, status.
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


@perms_app.command("install")
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
    """Install overcode tool permissions into Claude Code settings.

    By default installs safe (read-only) permissions. Use --all to also
    include punchy permissions that can spawn or control agents.

    Safe: report, show, list, follow, kill, budget
    Punchy (--all): launch, send, instruct
    """
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

    perms = OVERCODE_SAFE_PERMS + (OVERCODE_PUNCHY_PERMS if all_perms else [])

    installed = 0
    already = 0
    for perm in perms:
        if editor.add_permission(perm):
            installed += 1
        else:
            already += 1

    if installed > 0:
        tier = "safe + punchy" if all_perms else "safe"
        rprint(f"[green]\u2713[/green] Installed {installed} permission(s) in {level} settings ({tier})")
        rprint(f"  [dim]{editor.path}[/dim]")
        for perm in perms:
            rprint(f"  {perm}")
    elif already == len(perms):
        rprint(f"[green]\u2713[/green] All {already} permissions already installed in {level} settings")


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

    all_perms = OVERCODE_SAFE_PERMS + OVERCODE_PUNCHY_PERMS

    for level_name, editor in [
        ("User-level", ClaudeConfigEditor.user_level()),
        ("Project-level", ClaudeConfigEditor.project_level()),
    ]:
        try:
            editor.load()
        except ValueError:
            rprint(f"\n{level_name} ({editor.path}):")
            rprint(f"  [red](invalid JSON)[/red]")
            continue

        if not editor.path.exists():
            rprint(f"\n{level_name} ({editor.path}):")
            rprint(f"  [dim](no settings file)[/dim]")
            continue

        rprint(f"\n{level_name} ({editor.path}):")

        installed = editor.list_permissions_matching("Bash(overcode ")
        for perm in all_perms:
            if perm in installed:
                rprint(f"  {perm}  [green]\u2713[/green]")
            else:
                rprint(f"  {perm:<30} [dim]not installed[/dim]")
