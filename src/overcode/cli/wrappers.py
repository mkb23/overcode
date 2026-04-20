"""
Wrappers commands: list, install, reset.
"""

from typing import Annotated, Optional

import typer
from rich import print as rprint

from ._shared import wrappers_app


@wrappers_app.command("list")
def wrappers_list():
    """List installed and available wrappers.

    Shows wrappers in ~/.overcode/wrappers/ and indicates which are
    bundled (shipped with overcode) and whether they've been modified.
    """
    from ..wrapper import list_available_wrappers, BUNDLED_WRAPPERS, _wrappers_dir
    from pathlib import Path

    installed = list_available_wrappers()
    bundled_stems = {Path(f).stem for f in BUNDLED_WRAPPERS}

    if not installed and not BUNDLED_WRAPPERS:
        rprint("[dim]No wrappers available[/dim]")
        return

    rprint(f"[bold]Wrappers directory:[/bold] {_wrappers_dir()}\n")

    if installed:
        rprint("[bold]Installed:[/bold]")
        for name, path in installed:
            # Check if it's a bundled wrapper and if it's been modified
            p = Path(path)
            bundled_content = BUNDLED_WRAPPERS.get(p.name, "")
            if bundled_content:
                modified = p.read_text() != bundled_content
                tag = " [yellow](modified)[/yellow]" if modified else " [dim](bundled)[/dim]"
            else:
                tag = " [dim](custom)[/dim]"
            rprint(f"  {name}{tag}")
    else:
        rprint("[dim]No wrappers installed[/dim]")

    # Show uninstalled bundled wrappers
    installed_names = {name for name, _ in installed}
    not_installed = [Path(f).stem for f in BUNDLED_WRAPPERS if Path(f).stem not in installed_names]
    if not_installed:
        rprint(f"\n[bold]Available (auto-installs on first use):[/bold]")
        for name in not_installed:
            rprint(f"  {name}")


@wrappers_app.command("install")
def wrappers_install():
    """Install or update all bundled wrappers.

    Copies bundled wrapper scripts to ~/.overcode/wrappers/.
    Existing wrappers are only overwritten if they haven't been modified.
    """
    from ..wrapper import install_all_bundled

    results = install_all_bundled()

    for name, status in results:
        if status == "installed":
            rprint(f"  [green]Installed[/green] {name}")
        elif status == "updated":
            rprint(f"  [yellow]Updated[/yellow]  {name}")
        else:
            rprint(f"  [dim]Unchanged[/dim] {name}")

    rprint(f"\n[green]Done.[/green] {sum(1 for _, s in results if s != 'unchanged')} wrapper(s) installed/updated.")


@wrappers_app.command("reset")
def wrappers_reset(
    name: Annotated[
        Optional[str],
        typer.Argument(help="Wrapper name to reset (omit for all)"),
    ] = None,
):
    """Reset wrapper(s) to the bundled version.

    Overwrites the installed copy with the version shipped with overcode.
    Use this to undo local modifications.

    Examples:
        overcode wrappers reset              # Reset all bundled wrappers
        overcode wrappers reset devcontainer # Reset only devcontainer
    """
    from ..wrapper import reset_wrapper, BUNDLED_WRAPPERS
    from pathlib import Path

    if name:
        result = reset_wrapper(name)
        if result:
            rprint(f"[green]Restored[/green] {name} to bundled version")
        else:
            bundled_names = [Path(f).stem for f in BUNDLED_WRAPPERS]
            rprint(f"[red]Error:[/red] '{name}' is not a bundled wrapper")
            rprint(f"[dim]Bundled wrappers: {', '.join(bundled_names)}[/dim]")
            raise typer.Exit(code=1)
    else:
        from ..wrapper import install_all_bundled
        results = install_all_bundled()
        for fname, status in results:
            rprint(f"  [green]Restored[/green] {fname}")
        rprint(f"\n[green]Done.[/green] All bundled wrappers reset.")
