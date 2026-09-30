"""
Fleet lifecycle commands: revive (#481) and shutdown (#509).
"""

from typing import Annotated, Optional

import typer
from rich import print as rprint

from ._shared import app, SessionOption, find_agent


def _revive_line(r) -> str:
    how = "resumed" if r.mode == "resume" else "fresh"
    if r.ok is None:
        how = "would resume" if r.mode == "resume" else "would start fresh"
    reason = f" ({r.reason})" if r.reason else ""
    if r.ok is False:
        return f"  [red]failed[/red]   {r.name} [{r.tmux_session}]{reason}"
    colour = "dim" if r.ok is None else "green"
    return f"  [{colour}]{how:<17}[/{colour}] {r.name} [{r.tmux_session}]{reason}"


@app.command()
def revive(
    name: Annotated[
        Optional[str], typer.Argument(help="Agent to revive (omit with --all)")
    ] = None,
    all_dead: Annotated[
        bool, typer.Option("--all", "-a", help="Revive every dead agent (parents before children)")
    ] = False,
    all_sessions: Annotated[
        bool, typer.Option("--all-sessions", help="With --all: every overcode tmux session, not just this one")
    ] = False,
    fresh: Annotated[
        bool, typer.Option("--fresh", help="Start new conversations instead of resuming")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", "-n", help="List what would be revived, change nothing")
    ] = False,
    session: SessionOption = "agents",
):
    """Bring back agents whose tmux window is gone (e.g. after a reboot).

    Relaunches each agent in a new window with its full launch context,
    resuming its prior conversation where one is recorded and the backend
    can resume. Done children, and children that filed a report, are
    skipped. Starts the monitor daemon if it isn't running.

    Examples:
        overcode revive my-agent
        overcode revive --all --dry-run
        overcode revive --all
    """
    from ..launcher import AgentLauncher
    from ..lifecycle import ensure_monitor_daemon, known_tmux_sessions, resume_mode, revive_all

    if all_sessions and not all_dead:
        rprint("[red]Error:[/red] --all-sessions needs --all")
        raise typer.Exit(1)
    if bool(name) == all_dead:
        rprint("[red]Error:[/red] give an agent name, or --all")
        raise typer.Exit(1)

    if name:
        launcher = AgentLauncher(session)
        sess = find_agent(launcher.sessions, name)
        if not sess:
            rprint(f"[red]Error: Agent '{name}' not found[/red]")
            raise typer.Exit(1)
        mode, reason = resume_mode(sess, fresh)
        how = "resume" if mode == "resume" else f"start fresh{f' ({reason})' if reason else ''}"
        if dry_run:
            rprint(f"[dim]Would {how}: {name}[/dim]")
            return
        if not launcher.revive(sess, fresh=fresh):
            rprint(f"[red]Failed to revive agent: {name}[/red]")
            raise typer.Exit(1)
        rprint(f"[green]Revived agent: {name} ({'resumed' if mode == 'resume' else 'fresh'})[/green]")
        tmux_sessions = [session]
        failures = 0
    else:
        if all_sessions:
            from ..session_manager import SessionManager
            tmux_sessions = known_tmux_sessions(SessionManager()) or [session]
        else:
            tmux_sessions = [session]
        results = []
        for ts in tmux_sessions:
            launcher = AgentLauncher(ts)
            results += revive_all(launcher, fresh=fresh, dry_run=dry_run,
                                  on_result=lambda r: rprint(_revive_line(r)))
        if not results:
            rprint("[dim]No dead agents to revive.[/dim]")
            return
        failures = sum(1 for r in results if r.ok is False)
        if dry_run:
            rprint(f"[dim]{len(results)} agent(s) would be revived. Run without --dry-run to revive.[/dim]")
            return
        ok = len(results) - failures
        rprint(f"Revived {ok} of {len(results)} agent(s).")
        tmux_sessions = sorted({r.tmux_session for r in results if r.ok})

    for ts in tmux_sessions:
        started = ensure_monitor_daemon(ts)
        if started:
            rprint(f"[dim]Started monitor daemon [{ts}][/dim]")
        elif started is False:
            rprint(f"[yellow]Could not start monitor daemon [{ts}][/yellow]")
    if failures:
        raise typer.Exit(1)


@app.command()
def shutdown(
    all_sessions: Annotated[
        bool, typer.Option("--all-sessions", "--all", help="Every overcode tmux session, not just this one")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", "-n", help="List what would be stopped, change nothing")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Kill agent windows without a graceful exit")
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Skip the confirmation")
    ] = False,
    keep_jobs: Annotated[
        bool, typer.Option("--keep-jobs", help="Leave the jobs tmux session running")
    ] = False,
    timeout: Annotated[
        float, typer.Option("--timeout", help="Seconds to wait for agents to exit before killing their windows")
    ] = 10.0,
    session: SessionOption = "agents",
):
    """Stop everything overcode is running: agents, jobs, daemons, tmux sessions.

    Order: supervisor daemon; agents, children first (graceful exit, then
    the window is killed); the jobs tmux session; the monitor daemon, web
    server and presence logger; then overcode's tmux sessions.

    Agent records are kept, so `overcode revive --all` brings them back
    later, resuming their conversations.

    Examples:
        overcode shutdown --dry-run
        overcode shutdown
        overcode shutdown --all --yes
    """
    from ..lifecycle import shutdown as do_shutdown

    kwargs = dict(all_sessions=all_sessions, force=force, keep_jobs=keep_jobs,
                  timeout=timeout)
    if dry_run or not yes:
        rprint("[bold]Shutdown plan[/bold]" + ("" if all_sessions else f" (tmux session '{session}')"))
        do_shutdown(session, dry_run=True, **kwargs)
        if dry_run:
            return
        if not typer.confirm("Shut all of this down?", default=False):
            rprint("[dim]Cancelled.[/dim]")
            raise typer.Exit(1)

    rprint("[bold]Shutting down overcode[/bold]")
    report = do_shutdown(session, **kwargs)
    if report.failed:
        rprint(f"[yellow]Done, {len(report.failed)} step(s) failed.[/yellow]")
        raise typer.Exit(1)
    rprint("[green]overcode is shut down.[/green] Bring agents back with: overcode revive --all")
