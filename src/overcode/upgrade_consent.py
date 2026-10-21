"""
Asking before 0.6 converts what 0.5 left on disk.

The first 0.6 daemon converts a 0.5 state directory in place, and some of it
is one-way (docs/release-notes-0.6.1.md, "Upgrading"): the old archive is
split into month files and deleted, name-keyed agent files are packed into
day bundles (tails only), archived spend is restated. So nothing converts
until the person has said yes once:

- ``overcode`` / ``overcode tmux`` / ``overcode monitor`` (``cli.split.open_split``)
  explain it, suggest a backup and ask (``confirm_or_exit``);
- the daemon does not start while consent is outstanding
  (``MonitorDaemon.run``), and the lazy archive conversion a CLI command
  would trigger is skipped (``SessionArchive``).

A fresh install has nothing to convert and is never asked. Consent is the
file ``.upgrade-0.6`` in the registry directory, beside the archive.
"""

import os
from pathlib import Path
from typing import List, Optional

from . import paths

MARKER = ".upgrade-0.6"
NOTES_URL = "https://github.com/mkb23/overcode/blob/main/docs/release-notes-0.6.1.md"


def _registry(registry_dir: Optional[Path]) -> Path:
    return Path(registry_dir) if registry_dir is not None else paths.sessions_registry_dir()


def unconverted(registry_dir: Optional[Path] = None) -> List[str]:
    """What 0.5 left that 0.6 would convert (empty: nothing to ask about).

    The single-file archives beside ``sessions.json``, and any tmux session
    directory holding name-keyed agent files that is not on layout 2.
    ``registry_dir`` given (a ``SessionManager``'s own) checks the archive only.
    """
    found = []
    registry = _registry(registry_dir)
    for name in ("archive.json", "archive.jsonl"):
        if (registry / name).exists():
            found.append(str(registry / name))
    if registry_dir is not None:
        return found
    from .layout_migration import LAYOUT_VERSION, layout_version, legacy_files
    try:
        entries = list(os.scandir(paths.sessions_root()))
    except OSError:
        return found
    for entry in entries:
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if layout_version(entry.name) != LAYOUT_VERSION and legacy_files(entry.name):
            found.append(entry.path)
    return found


def granted(registry_dir: Optional[Path] = None) -> bool:
    return (_registry(registry_dir) / MARKER).exists()


def needs_consent(registry_dir: Optional[Path] = None) -> bool:
    """Whether 0.5 data is waiting to be converted and nobody has agreed yet."""
    try:
        return not granted(registry_dir) and bool(unconverted(registry_dir))
    except OSError:
        return False


def grant(registry_dir: Optional[Path] = None) -> None:
    registry = _registry(registry_dir)
    registry.mkdir(parents=True, exist_ok=True)
    (registry / MARKER).write_text("converted with the person's consent\n")


def _home(path: Path) -> str:
    s, home = str(path), str(Path.home())
    return "~" + s[len(home):] if s.startswith(home) else s


def confirm_or_exit(echo, ask=input, assume_yes: bool = False) -> None:
    """Explain the conversion, suggest a backup and ask; exit 0 on anything but yes.

    Does nothing when there is nothing to convert or consent was given.
    ``assume_yes`` (``--yes``) explains and converts without asking; without
    a terminal to ask on it explains and exits 1.
    """
    import sys
    if not needs_consent():
        return
    data = _home(paths.overcode_dir())
    state = paths.sessions_root()
    extra = "" if str(state).startswith(str(paths.overcode_dir())) else f" and {_home(state)}"
    echo(f"[bold]overcode 0.6 will convert the data overcode 0.5 left in {data}{extra}.[/bold]\n")
    echo("  - The agent archive moves to a new format and the old files are deleted.")
    echo("    Archived agents lose their launch command, instructions and")
    echo("    conversation ids.")
    echo("  - Old per-agent files are packed into compressed bundles; only their")
    echo("    last part is kept.")
    echo("  - Past spend is recalculated with corrected counting, so totals go down.")
    echo("  - Finished jobs older than 24 hours are removed.")
    echo("\n  overcode 0.5 cannot read the converted data, so [bold]make a backup first[/bold]:")
    echo("  stop any running 0.5 overcode, then")
    echo(f"\n      cp -a {data} {data}.bak-0.5")
    echo(f"\n  Details: {NOTES_URL}\n")
    if assume_yes:
        grant()
        return
    if not sys.stdin.isatty():
        echo("  Nothing was converted. Run [bold]overcode[/bold] in a terminal to answer "
             "(or [bold]overcode tmux --yes[/bold] to convert without asking).")
        raise SystemExit(1)
    try:
        answer = ask("  Convert now? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer not in ("y", "yes"):
        echo("[dim]Nothing converted. Run overcode again when you are ready.[/dim]")
        raise SystemExit(0)
    grant()
    echo("")
