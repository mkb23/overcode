"""
Hooks commands: install (deprecated), uninstall, uninstall-backend, status.
"""

from typing import Annotated, Optional

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


@hooks_app.command("uninstall-backend")
def hooks_uninstall_backend(
    backend: Annotated[
        str,
        typer.Argument(help="Backend name: claude-code, opencode, codex, or grok"),
    ],
    dir: Annotated[
        Optional[str],
        typer.Option("--dir", help="Project directory (required for opencode — its plugin is project-scoped)"),
    ] = None,
):
    """Remove a backend's on-disk telemetry footprint, if any.

    Claude Code's hooks and codex's hooks both ride per-launch CLI flags
    with zero files written, so there is nothing to remove for either.
    opencode's telemetry plugin lives at
    ``<project>/.opencode/plugins/overcode-telemetry.js``; grok's hooks file
    is global at ``~/.grok/hooks/overcode.json``. Both are removed only if
    they still carry overcode's marker — a file you've since edited
    yourself is left alone. Pair this with setting
    ``backend_telemetry: {<backend>: off}`` in config.yaml so a future
    launch doesn't just reinstall it.
    """
    if backend in ("codex", "claude-code"):
        rprint(f"[dim]nothing installed on disk for this backend ({backend})[/dim]")
        return

    if backend == "grok":
        import json
        from ..backends.grok import hooks_file_path, GROK_HOOKS_MARKER

        path = hooks_file_path()
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except OSError:
            rprint(f"[dim]No grok hooks file found at {path}[/dim]")
            return
        except (ValueError, TypeError):
            rprint(f"[yellow]{path} is not valid JSON — leaving it alone[/yellow]")
            raise typer.Exit(1)
        if GROK_HOOKS_MARKER not in str(existing.get("description", "")):
            rprint(f"[yellow]{path} exists but is not overcode-managed — leaving it alone[/yellow]")
            raise typer.Exit(1)
        path.unlink()
        rprint(f"[green]✓[/green] Removed {path}")
        return

    if backend == "opencode":
        if not dir:
            rprint(
                "[red]Error:[/red] --dir is required for opencode — its "
                "telemetry plugin is installed per-project, not globally"
            )
            raise typer.Exit(1)
        from ..backends.opencode import project_plugin_path, PLUGIN_MARKER

        path = project_plugin_path(dir)
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError:
            rprint(f"[dim]No opencode telemetry plugin found at {path}[/dim]")
            return
        if PLUGIN_MARKER not in existing:
            rprint(f"[yellow]{path} exists but is not overcode-managed — leaving it alone[/yellow]")
            raise typer.Exit(1)
        path.unlink()
        rprint(f"[green]✓[/green] Removed {path}")
        return

    rprint(f"[red]Error:[/red] unknown backend '{backend}'")
    raise typer.Exit(1)


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
