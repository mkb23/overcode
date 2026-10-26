"""
Transcript archive commands: status, sweep, import, find, cat (transcript_archive).

``find`` and ``cat`` are what shirka reads: ``find`` prints manifest rows as
JSON lines, ``cat`` a conversation's transcript bytes. Keep both stable.
"""

import json
import sys
from datetime import timezone
from pathlib import Path
from typing import Annotated, List, Optional

import typer
from rich import print as rprint

from ._shared import app

archive_app = typer.Typer(
    name="archive",
    help="The transcript archive: every Claude Code transcript, compressed and kept "
         "under ~/.overcode/transcripts.",
    no_args_is_help=True,
)
app.add_typer(archive_app, name="archive")

# --root: the archive these commands use (default $OVERCODE_TRANSCRIPTS_DIR,
# else ~/.overcode/transcripts)
_root: Optional[Path] = None


@archive_app.callback()
def archive_options(
    root: Annotated[Optional[Path], typer.Option(
        "--root", help="Use this archive directory instead of ~/.overcode/transcripts "
                       "(e.g. a trial sweep or import into a scratch archive)")] = None,
):
    """The transcript archive: every Claude Code transcript, compressed and kept."""
    global _root
    _root = root.expanduser() if root is not None else None


def _archive():
    from ..config import get_transcript_archive_config
    from ..transcript_archive import TranscriptArchive

    return TranscriptArchive(_root, max_bytes=get_transcript_archive_config()["max_bytes"])


def _size(n: Optional[int]) -> str:
    value = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _run_locked(archive, label: str, work):
    from ..transcript_archive import ArchiveBusy, ArchiveError

    try:
        try:
            with archive.locked(wait=False):
                return work()
        except ArchiveBusy:
            print(f"Waiting for another process using the transcript archive ({label})...",
                  file=sys.stderr)
            with archive.locked(wait=True):
                return work()
    except ArchiveError as e:
        rprint(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


def _print_result(result, as_json: bool, title: str) -> None:
    if as_json:
        print(json.dumps(result.as_dict()))
        return
    rprint(f"[bold]{title}[/bold]: {result.captured} captured "
           f"({result.appended} appended, {result.versioned} new versions), "
           f"{result.unchanged} unchanged, {result.deferred} deferred, "
           f"{result.failed} failed — {result.seen} files seen, {result.rows} manifest rows, "
           f"{_size(result.bytes)} → {_size(result.zst_bytes)}, "
           f"{result.secrets_flagged} key-like strings flagged, {result.seconds:.1f}s")


@archive_app.command("status")
def archive_status(
    as_json: Annotated[bool, typer.Option("--json", help="One JSON object")] = False,
):
    """Sessions, sizes, the last sweep, what is waiting and the flagged-secret count."""
    archive = _archive()
    archive.load()
    status = archive.status()
    if as_json:
        print(json.dumps(status))
        return
    rprint(f"[bold]Transcript archive[/bold] {status['root']}")
    rprint(f"  {status['sessions']} sessions, {status['files']} files "
           f"({status['manifest_rows']} manifest rows)")
    rprint(f"  {_size(status['bytes'])} of transcripts in {_size(status['zst_bytes'])}")
    if status["max_bytes"]:
        used = status["cap_used"] or 0
        colour = "red" if used >= 1 else "yellow" if used >= 0.8 else "dim"
        rprint(f"  [{colour}]{used:.0%} of archive.transcripts.max_bytes "
               f"({_size(status['max_bytes'])})[/{colour}]")
    sweep = status["last_sweep"]
    if sweep:
        rprint(f"  Last sweep {sweep.get('at')}: {sweep.get('captured', 0)} captured, "
               f"{sweep.get('deferred', 0)} deferred, {sweep.get('failed', 0)} failed")
    else:
        rprint("  [yellow]Never swept[/yellow] (overcode archive sweep)")
    rprint(f"  {status['pending']} files changed since their last capture, "
           f"{status['queued']} queued by hooks")
    rprint(f"  {status['secrets_flagged']} key-like strings in "
           f"{status['secrets_flagged_files']} files (counted, never redacted)")
    if not status["zstd"]:
        rprint("  [red]zstd is not installed: nothing can be captured or read[/red]")


@archive_app.command("sweep")
def archive_sweep(
    now: Annotated[bool, typer.Option(
        "--now", help="Also take appends to transcripts still being written")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Counts as one JSON object")] = False,
):
    """Archive every transcript that changed since the last sweep, in the foreground.

    The monitor daemon does this hourly (archive.transcripts.sweep), and
    drains what the SessionEnd / PreCompact hooks queued within a minute.
    """
    archive = _archive()

    def work():
        result = archive.drain_queue()
        return archive.sweep(now=now, result=result)

    _print_result(_run_locked(archive, "sweep", work), as_json, "Sweep")


@archive_app.command("import")
def archive_import(
    snapshot: Annotated[Path, typer.Argument(help="A tar.zst of ~/.claude (projects/, file-history/, history.jsonl)")],
    as_json: Annotated[bool, typer.Option("--json", help="Counts as one JSON object")] = False,
):
    """Import a snapshot of Claude's directory into the archive and its manifest.

    Every file becomes a manifest row; importing it again adds nothing.

    Example:
        overcode archive import ~/.overcode/transcripts/bootstrap-2026-10-25.tar.zst
    """
    if not snapshot.is_file():
        rprint(f"[red]Error:[/red] no such file: {snapshot}")
        raise typer.Exit(1)
    archive = _archive()
    result = _run_locked(archive, "import", lambda: archive.import_snapshot(snapshot))
    _print_result(result, as_json, "Import")
    if result.failed:
        raise typer.Exit(1)


@archive_app.command("find")
def archive_find(
    repo: Annotated[Optional[str], typer.Option(
        "--repo", help="A git remote (any spelling: git@host:o/r.git, https://host/o/r) "
                       "or a local checkout")] = None,
    since: Annotated[Optional[str], typer.Option(
        "--since", help="Active since: 30d, 2w, 12h, 2026-09 or 2026-09-15")] = None,
    agent: Annotated[Optional[str], typer.Option("--agent", help="Overcode agent name or id")] = None,
    billing: Annotated[Optional[str], typer.Option("--billing", help="Billing code (name or id)")] = None,
    session: Annotated[Optional[str], typer.Option("--session", help="A Claude session id")] = None,
    kind: Annotated[Optional[List[str]], typer.Option(
        "--kind", help="main, subagent, tool-result, session-file, file-history, ... (repeatable)")] = None,
):
    """Manifest rows (JSON lines): the current version of each archived file that matches."""
    from ..session_archive import parse_since

    since_iso = None
    if since:
        try:
            since_iso = parse_since(since).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            raise typer.Exit(2)
    rows = _archive().find(repo=repo, since=since_iso, agent=agent, billing=billing,
                           cc_session=session, kinds=kind)
    out = sys.stdout
    for row in rows:
        out.write(json.dumps(row, separators=(",", ":")) + "\n")
    out.flush()


@archive_app.command("cat")
def archive_cat(
    cc_session: Annotated[str, typer.Argument(help="A Claude session id")],
    subagents: Annotated[bool, typer.Option(
        "--subagents", help="Also its subagents' transcripts, after the main one")] = False,
    version: Annotated[Optional[int], typer.Option(
        "--version", help="This version of the main transcript (see find's 'version')")] = None,
):
    """A conversation's transcript, decompressed to stdout, byte for byte."""
    from ..transcript_archive import ArchiveError

    try:
        count = _archive().cat(cc_session, sys.stdout.buffer, subagents=subagents,
                               version=version)
    except ArchiveError as e:
        print(f"Error: {e}", file=sys.stderr)
        raise typer.Exit(1)
    except BrokenPipeError:
        return
    sys.stdout.buffer.flush()
    if not count:
        print(f"No archived transcript for {cc_session}", file=sys.stderr)
        raise typer.Exit(1)
