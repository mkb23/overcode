"""
Skills commands: install, uninstall, status.
"""

from pathlib import Path
from typing import Annotated

import typer
from rich import print as rprint

from ._shared import skills_app


@skills_app.command("install")
def skills_install(
    project: Annotated[
        bool,
        typer.Option("--project", "-p", help="Install to project-level .claude/skills/ instead of user-level"),
    ] = False,
):
    """Install overcode skill files into Claude Code skills directory.

    By default installs to ~/.claude/skills/<name>/SKILL.md (user-level).
    Use --project to install to .claude/skills/<name>/SKILL.md instead.
    """
    import shutil
    from ..bundled_skills import OVERCODE_SKILLS, DEPRECATED_SKILL_NAMES

    if project:
        base = Path.cwd() / ".claude" / "skills"
        level = "project"
    else:
        base = Path.home() / ".claude" / "skills"
        level = "user"

    # Remove deprecated/renamed skills
    for old_name in DEPRECATED_SKILL_NAMES:
        old_dir = base / old_name
        if old_dir.exists():
            shutil.rmtree(old_dir)
            rprint(f"  [dim]Removed deprecated skill '{old_name}'[/dim]")

    installed = 0
    skipped = 0
    updated = 0

    for name, skill in OVERCODE_SKILLS.items():
        skill_dir = base / name
        skill_file = skill_dir / "SKILL.md"

        if skill_file.exists():
            existing = skill_file.read_text()
            if existing == skill["content"]:
                skipped += 1
                continue
            else:
                skill_file.write_text(skill["content"])
                updated += 1
                continue

        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file.write_text(skill["content"])
        installed += 1

    parts = []
    if installed > 0:
        parts.append(f"{installed} installed")
    if updated > 0:
        parts.append(f"{updated} updated")
    if skipped > 0:
        parts.append(f"{skipped} up-to-date")

    if installed > 0 or updated > 0:
        rprint(f"[green]\u2713[/green] Skills: {', '.join(parts)} ({level})")
        rprint(f"  [dim]{base}[/dim]")
    else:
        rprint(f"[green]\u2713[/green] All {skipped} skills already up-to-date ({level})")


@skills_app.command("uninstall")
def skills_uninstall(
    project: Annotated[
        bool,
        typer.Option("--project", "-p", help="Uninstall from project-level .claude/skills/ instead of user-level"),
    ] = False,
):
    """Remove overcode skill files from Claude Code skills directory.

    Only removes skills whose SKILL.md content matches bundled content
    (won't delete user-customized skills).
    """
    import shutil

    from ..bundled_skills import OVERCODE_SKILLS

    if project:
        base = Path.cwd() / ".claude" / "skills"
        level = "project"
    else:
        base = Path.home() / ".claude" / "skills"
        level = "user"

    removed = 0
    skipped_modified = 0

    for name, skill in OVERCODE_SKILLS.items():
        skill_dir = base / name
        skill_file = skill_dir / "SKILL.md"

        if not skill_file.exists():
            continue

        existing = skill_file.read_text()
        if existing != skill["content"]:
            skipped_modified += 1
            rprint(f"  [yellow]Skipped[/yellow] {name} (modified)")
            continue

        shutil.rmtree(skill_dir)
        removed += 1

    if removed > 0:
        rprint(f"[green]\u2713[/green] Removed {removed} skill(s) from {level}")
    elif skipped_modified > 0:
        rprint(f"[dim]No unmodified overcode skills to remove ({skipped_modified} modified)[/dim]")
    else:
        rprint(f"[dim]No overcode skills found in {level}[/dim]")


@skills_app.command("status")
def skills_status():
    """Show which overcode skills are installed."""
    from ..bundled_skills import OVERCODE_SKILLS

    for level_name, base in [
        ("User-level", Path.home() / ".claude" / "skills"),
        ("Project-level", Path.cwd() / ".claude" / "skills"),
    ]:
        rprint(f"\n{level_name} ({base}):")

        for name, skill in OVERCODE_SKILLS.items():
            skill_file = base / name / "SKILL.md"

            if not skill_file.exists():
                rprint(f"  {name:<20} [dim]not installed[/dim]")
            elif skill_file.read_text() == skill["content"]:
                rprint(f"  {name:<20} [green]\u2713 installed[/green]")
            else:
                rprint(f"  {name:<20} [yellow]\u26a0 modified[/yellow] — run: overcode skills install")
