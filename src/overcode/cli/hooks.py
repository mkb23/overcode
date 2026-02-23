"""
Hooks commands: install, uninstall, status.
"""

from typing import Annotated

import typer
from rich import print as rprint

from ._shared import hooks_app


@hooks_app.command("install")
def hooks_install(
    project: Annotated[
        bool,
        typer.Option("--project", "-p", help="Install to project-level .claude/settings.json instead of user-level"),
    ] = False,
):
    """Install all overcode hooks into Claude Code settings.

    Installs hooks for: UserPromptSubmit, PostToolUse, Stop,
    PermissionRequest, SessionEnd. All use the unified 'overcode hook-handler'.
    """
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

    # Install all overcode hooks (idempotent)
    installed = 0
    already = 0
    for event, command in OVERCODE_HOOKS:
        if editor.add_hook(event, command):
            installed += 1
        else:
            already += 1

    if installed > 0:
        events = ", ".join(event for event, _ in OVERCODE_HOOKS)
        rprint(f"[green]\u2713[/green] Installed {installed} hook(s) in {level} settings")
        rprint(f"  [dim]{editor.path}[/dim]")
        rprint(f"\n  Events: {events}")
        rprint("  All hooks run 'overcode hook-handler' (reads event from stdin).")
    elif already == len(OVERCODE_HOOKS):
        rprint(f"[green]\u2713[/green] All {already} hooks already installed in {level} settings")


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
