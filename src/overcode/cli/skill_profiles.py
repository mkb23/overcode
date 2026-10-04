"""
Skill profile commands (#499): the library, profiles, folder pins.

    overcode skills list
    overcode skills profile list|show|set|add|remove|delete|emoji
    overcode skills pin <profile> [dir] / unpin [dir]
    overcode skills library add|remove <path>
    overcode skills adopt <skill>
"""

import shutil
from pathlib import Path
from typing import Annotated, List, Optional

import typer
from rich import print as rprint
from rich.markup import escape

from ._shared import skills_app

profile_app = typer.Typer(
    name="profile",
    help="Named sets of skills switched on per agent at launch.",
    no_args_is_help=True,
)
skills_app.add_typer(profile_app, name="profile")

library_app = typer.Typer(
    name="library",
    help="Folders the skill library is read from.",
    no_args_is_help=True,
)
skills_app.add_typer(library_app, name="library")


def _short(text: str, width: int = 60) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _require_profile(name: str) -> List[str]:
    from ..skill_library import get_profiles
    profiles = get_profiles()
    if name not in profiles:
        rprint(f"[red]No skill profile '{escape(name)}'.[/red] "
               f"Profiles: {', '.join(profiles) or '(none)'}")
        raise typer.Exit(1)
    return profiles[name]


def _warn_unknown(skills: List[str]) -> None:
    from ..skill_library import personal_skills, scan_library
    known = {n for s in scan_library() for n in s.names}
    for backend in ("claude-code", "opencode"):
        known.update(n for s in personal_skills(backend) for n in s.names)
    unknown = [s for s in skills if s not in known]
    if unknown:
        rprint(f"  [yellow]Not found in the library or personal skills:[/yellow] "
               f"{escape(', '.join(unknown))}")


@skills_app.command("list")
def skills_list():
    """Show the skill library, profiles, and each CLI's always-on skills."""
    from ..skill_library import (
        get_folder_pins, get_profiles, library_dir, library_duplicates,
        library_paths, overlapping_library_paths, personal_skills, scan_library,
    )

    profiles = get_profiles()
    in_profiles = {}
    for pname, skills in profiles.items():
        for s in skills:
            in_profiles.setdefault(s, []).append(pname)

    rprint("[bold]Library[/bold] [dim](installed, off until a profile uses them)[/dim]")
    rprint(f"  [dim]{library_dir()}[/dim]")
    for path in library_paths():
        rprint(f"  [dim]{escape(path)}[/dim]")
    library = scan_library()
    if not library:
        rprint("  [dim](empty — copy skill folders into the library, "
               "or `overcode skills library add <path>`)[/dim]")
    for skill in library:
        used = ", ".join(in_profiles.get(skill.name, [])) or "-"
        rprint(f"  {escape(skill.name):<28} [cyan]{escape(used):<16}[/cyan] "
               f"[dim]{escape(_short(skill.description))}[/dim]")
    for name, paths in library_duplicates().items():
        rprint(f"  [yellow]'{escape(name)}' is in {len(paths)} places; using {paths[0]}[/yellow]")
    for path in overlapping_library_paths():
        rprint(f"  [yellow]{escape(path)} is a folder an agent CLI already reads, "
               f"so its skills are always on[/yellow]")

    for backend, label in (("claude-code", "Claude Code"), ("opencode", "opencode")):
        personal = personal_skills(backend)
        rprint(f"\n[bold]Always on in {label}[/bold] "
               f"[dim](hidden when a profile doesn't include them)[/dim]")
        if not personal:
            rprint("  [dim](none)[/dim]")
        for skill in personal:
            rprint(f"  {escape(skill.name):<28} [dim]{escape(skill.source)}[/dim]")

    rprint("\n[bold]Profiles[/bold]")
    if not profiles:
        rprint("  [dim](none — `overcode skills profile set <name> <skill>...`)[/dim]")
    for pname, skills in profiles.items():
        rprint(f"  [cyan]{escape(pname):<16}[/cyan] {escape(', '.join(skills)) or '[dim](no skills)[/dim]'}")
    pins = get_folder_pins()
    if pins:
        rprint("\n[bold]Pinned folders[/bold]")
        for folder, pname in pins.items():
            rprint(f"  {escape(folder)} → [cyan]{escape(pname)}[/cyan]")


@profile_app.command("list")
def profile_list():
    """List skill profiles."""
    from ..skill_library import get_profiles, profile_emoji
    profiles = get_profiles()
    if not profiles:
        rprint("[dim]No skill profiles. Create one: overcode skills profile set <name> <skill>...[/dim]")
        return
    for pname, skills in profiles.items():
        rprint(f"{profile_emoji(pname)} [cyan]{escape(pname):<16}[/cyan] {escape(', '.join(skills))}")


@profile_app.command("show")
def profile_show(name: Annotated[str, typer.Argument(help="Profile name")]):
    """Show what a profile adds and hides for each CLI."""
    from ..skill_library import personal_skills, scan_library
    skills = _require_profile(name)
    library = {s.name: s for s in scan_library()}
    rprint(f"[bold cyan]{escape(name)}[/bold cyan]")
    for skill in skills:
        found = library.get(skill)
        where = escape(str(found.path)) if found else "[yellow]not in the library[/yellow]"
        rprint(f"  {escape(skill):<28} [dim]{where}[/dim]")
    for backend, label in (("claude-code", "Claude Code"), ("opencode", "opencode")):
        hidden = [s.cli_name(backend) for s in personal_skills(backend)
                  if not (s.names & set(skills))]
        if hidden:
            rprint(f"  [dim]Hidden in {label}: {escape(', '.join(hidden))}[/dim]")


@profile_app.command("set")
def profile_set(
    name: Annotated[str, typer.Argument(help="Profile name (lowercase, dashes)")],
    skills: Annotated[Optional[List[str]], typer.Argument(help="Skill names")] = None,
):
    """Create a profile, or replace its skills."""
    from ..skill_library import save_profile
    try:
        save_profile(name, skills or [])
    except ValueError as e:
        rprint(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Profile [cyan]{escape(name)}[/cyan]: "
           f"{escape(', '.join(skills or [])) or '(no skills)'}")
    _warn_unknown(skills or [])


@profile_app.command("add")
def profile_add(
    name: Annotated[str, typer.Argument(help="Profile name")],
    skills: Annotated[List[str], typer.Argument(help="Skill names to add")],
):
    """Add skills to a profile."""
    from ..skill_library import save_profile
    current = _require_profile(name)
    save_profile(name, current + skills)
    rprint(f"[green]✓[/green] Profile [cyan]{escape(name)}[/cyan]: "
           f"{escape(', '.join(dict.fromkeys(current + skills)))}")
    _warn_unknown(skills)


@profile_app.command("remove")
def profile_remove(
    name: Annotated[str, typer.Argument(help="Profile name")],
    skills: Annotated[List[str], typer.Argument(help="Skill names to remove")],
):
    """Remove skills from a profile."""
    from ..skill_library import save_profile
    current = _require_profile(name)
    kept = [s for s in current if s not in skills]
    save_profile(name, kept)
    rprint(f"[green]✓[/green] Profile [cyan]{escape(name)}[/cyan]: "
           f"{escape(', '.join(kept)) or '(no skills)'}")


@profile_app.command("delete")
def profile_delete(name: Annotated[str, typer.Argument(help="Profile name")]):
    """Delete a profile (and any folder pins to it)."""
    from ..skill_library import delete_profile
    if not delete_profile(name):
        rprint(f"[red]No skill profile '{escape(name)}'[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Deleted profile [cyan]{escape(name)}[/cyan]")


@profile_app.command("emoji")
def profile_set_emoji(
    name: Annotated[str, typer.Argument(help="Profile name")],
    emoji: Annotated[Optional[str], typer.Argument(help="Emoji to show in the PRF column; omit to go back to the default")] = None,
):
    """Choose the emoji the PRF column shows for a profile."""
    from ..skill_library import PROFILE_EMOJI_DEFAULT, set_profile_emoji
    _require_profile(name)
    set_profile_emoji(name, emoji)
    rprint(f"[green]✓[/green] Profile [cyan]{escape(name)}[/cyan] shows as "
           f"{emoji or PROFILE_EMOJI_DEFAULT}{'' if emoji else ' (default)'}")


@skills_app.command("pin")
def skills_pin(
    profile: Annotated[str, typer.Argument(help="Profile name, or 'none' for no profile")],
    directory: Annotated[Optional[str], typer.Argument(help="Folder (default: current)")] = None,
):
    """New agents started in this folder (or below it) get this profile."""
    from ..skill_library import NO_PROFILE, pin_folder
    if profile != NO_PROFILE:
        _require_profile(profile)
    folder = pin_folder(str(Path(directory or ".").resolve()), profile)
    rprint(f"[green]✓[/green] {escape(folder)} → [cyan]{escape(profile)}[/cyan] "
           f"[dim](recorded in ~/.overcode/config.yaml, nothing written to the folder)[/dim]")


@skills_app.command("unpin")
def skills_unpin(
    directory: Annotated[Optional[str], typer.Argument(help="Folder (default: current)")] = None,
):
    """Remove a folder's profile pin."""
    from ..skill_library import unpin_folder
    folder = str(Path(directory or ".").resolve())
    if not unpin_folder(folder):
        rprint(f"[dim]{escape(folder)} is not pinned[/dim]")
        return
    rprint(f"[green]✓[/green] Unpinned {escape(folder)}")


@library_app.command("add")
def library_add(path: Annotated[str, typer.Argument(help="Folder to read skills from")]):
    """Read skills from a folder too, e.g. a skills repo in your code root."""
    from ..skill_library import (
        _display_path, add_library_path, overlapping_library_paths, scan_dir,
    )
    folder = Path(path).expanduser().resolve()
    if not folder.is_dir():
        rprint(f"[red]Not a folder: {escape(str(folder))}[/red]")
        raise typer.Exit(1)
    recorded = _display_path(str(folder))
    add_library_path(recorded)
    count = len(scan_dir(folder, recorded))
    rprint(f"[green]✓[/green] Library folder {escape(recorded)} ({count} skills)")
    if recorded in overlapping_library_paths():
        rprint("  [yellow]An agent CLI already reads this folder, so these skills are "
               "always on regardless of profiles.[/yellow]")


@library_app.command("remove")
def library_remove(path: Annotated[str, typer.Argument(help="Library folder to stop reading")]):
    """Stop reading skills from a folder (the folder itself is untouched)."""
    from ..skill_library import remove_library_path
    if not remove_library_path(path) and not remove_library_path(str(Path(path).expanduser().resolve())):
        rprint(f"[red]{escape(path)} is not a library folder[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Removed library folder {escape(path)}")


@skills_app.command("adopt")
def skills_adopt(
    names: Annotated[List[str], typer.Argument(help="Personal skill names to move")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask")] = False,
):
    """Move always-on personal skills into the library, so only profiles switch them on."""
    from ..skill_library import library_dir, personal_skills
    personal = {}
    for backend in ("claude-code", "opencode"):
        for skill in personal_skills(backend):
            for n in skill.names:
                personal.setdefault(n, skill)
    moves = []
    for name in names:
        skill = personal.get(name)
        if skill is None:
            rprint(f"[red]No personal skill '{escape(name)}'[/red]")
            raise typer.Exit(1)
        dest = library_dir() / skill.path.name
        if dest.exists() or dest.is_symlink():
            rprint(f"[red]{escape(str(dest))} already exists[/red]")
            raise typer.Exit(1)
        moves.append((skill, dest))
    for skill, dest in moves:
        rprint(f"  {escape(str(skill.path))} → {escape(str(dest))}")
    if not yes and not typer.confirm("Move these into the library?"):
        raise typer.Exit(1)
    library_dir().mkdir(parents=True, exist_ok=True)
    for skill, dest in moves:
        shutil.move(str(skill.path), str(dest))
    rprint(f"[green]✓[/green] Moved {len(moves)} skill(s). They're off until a profile "
           f"includes them. Running agents keep them until restarted.")
