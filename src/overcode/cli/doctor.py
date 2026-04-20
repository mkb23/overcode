"""
`overcode doctor` — diagnose agents with broken hook configuration (#435).

Identifies agents whose claude process was started without the --settings
injection (most commonly because the user manually relaunched claude inside
the tmux window, bypassing `overcode restart`). Optionally auto-relaunches
broken agents to reinject hooks.
"""

from typing import Annotated

import typer
from rich import print as rprint
from rich.table import Table

from ._shared import app, SessionOption


@app.command()
def doctor(
    session: SessionOption = "agents",
    fix: Annotated[
        bool,
        typer.Option("--fix", help="Restart agents whose claude process is missing --settings"),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Show full claude argv for each agent"),
    ] = False,
):
    """Diagnose which agents have broken hook configuration.

    An agent only emits hook-based state changes (PostToolUse, Stop, etc.)
    if its claude process was launched with `--settings`. This command
    inspects each live agent's claude process and flags ones that are
    missing the injection — typically because they were relaunched
    manually in the tmux pane.

    Use --fix to `overcode restart` broken agents, which re-injects --settings.
    """
    from ..launcher import ClaudeLauncher
    from ..doctor import (
        inspect_agent,
        snapshot_process_table,
        SEVERITY_ERROR,
        SEVERITY_WARNING,
        VERDICT_OK,
        VERDICT_MISSING_SETTINGS,
        VERDICT_NO_CLAUDE,
        VERDICT_WINDOW_GONE,
        VERDICT_REMOTE,
    )
    from ..history_reader import get_session_stats
    from ..monitor_daemon import is_monitor_daemon_running

    launcher = ClaudeLauncher(session)
    # detect_terminated=False — doctor inspects running state, doesn't mutate
    sessions = [s for s in launcher.list_sessions(detect_terminated=False)
                if s.status != "terminated"]

    if not sessions:
        rprint("[dim]No running agents in session '[bold]{}[/bold]'[/dim]".format(session))
        return

    # Single ps snapshot shared across all agents.
    children, argv_by_pid = snapshot_process_table()
    daemon_running = is_monitor_daemon_running(session)

    results = []
    for sess in sessions:
        pane_pid = None
        live_stats = None
        if not getattr(sess, "is_remote", False):
            pane_pid = launcher.tmux.get_pane_pid(sess.tmux_window)
            try:
                live_stats = get_session_stats(sess)
            except Exception:
                live_stats = None
        results.append(inspect_agent(
            sess, pane_pid, children, argv_by_pid,
            live_stats=live_stats,
            daemon_running=daemon_running,
        ))

    # Print table
    table = Table(title=f"Agent hook health — session '{session}'")
    table.add_column("Agent", style="bold")
    table.add_column("Verdict")
    table.add_column("Launcher")
    table.add_column("PID")
    table.add_column("Issues")
    table.add_column("Details")

    verdict_style = {
        VERDICT_OK: "[green]✓ ok[/green]",
        VERDICT_MISSING_SETTINGS: "[red]✗ no --settings[/red]",
        VERDICT_NO_CLAUDE: "[yellow]? no claude[/yellow]",
        VERDICT_WINDOW_GONE: "[dim]window gone[/dim]",
        VERDICT_REMOTE: "[dim]remote[/dim]",
    }

    def _issues_cell(findings):
        if not findings:
            return "[dim]—[/dim]"
        errors = sum(1 for f in findings if f.severity == SEVERITY_ERROR)
        warnings = sum(1 for f in findings if f.severity == SEVERITY_WARNING)
        parts = []
        if errors:
            parts.append(f"[red]{errors}✗[/red]")
        if warnings:
            parts.append(f"[yellow]{warnings}⚠[/yellow]")
        return " ".join(parts)

    for r in results:
        table.add_row(
            r.name,
            verdict_style.get(r.verdict, r.verdict),
            r.launcher_version or "[dim]—[/dim]",
            str(r.claude_pid) if r.claude_pid else "[dim]—[/dim]",
            _issues_cell(r.data_findings),
            r.details,
        )

    rprint(table)

    # Detail findings below the table
    flagged = [r for r in results if r.data_findings]
    if flagged:
        rprint()
        rprint("[bold]Data-quality findings:[/bold]")
        for r in flagged:
            rprint(f"  [bold]{r.name}[/bold]")
            for f in r.data_findings:
                badge = "[red]✗[/red]" if f.severity == SEVERITY_ERROR else "[yellow]⚠[/yellow]"
                rprint(f"    {badge} [dim]{f.code}[/dim]: {f.message}")

    if verbose:
        rprint()
        for r in results:
            if r.claude_argv:
                rprint(f"[bold]{r.name}[/bold] argv:")
                rprint(f"  [dim]{r.claude_argv}[/dim]")

    broken = [r for r in results if r.verdict == VERDICT_MISSING_SETTINGS]
    ok_count = sum(1 for r in results if r.verdict == VERDICT_OK)
    findings_count = sum(len(r.data_findings) for r in results)

    rprint()
    if broken:
        rprint(f"[red]✗[/red] {len(broken)} broken, [green]{ok_count}[/green] ok, "
               f"{len(results) - len(broken) - ok_count} other")
        if not fix:
            names = " ".join(r.name for r in broken)
            rprint(f"[dim]Fix with: overcode restart {names}[/dim]")
            rprint("[dim]Or: overcode doctor --fix[/dim]")
    else:
        rprint(f"[green]✓[/green] all {ok_count} agents have hooks injected")

    if findings_count:
        rprint(f"[yellow]⚠[/yellow] {findings_count} data-quality "
               f"finding{'s' if findings_count != 1 else ''} across "
               f"{len(flagged)} agent{'s' if len(flagged) != 1 else ''}")

    # Global (not per-agent): bundled skills drifted from what's installed.
    # Affects every agent, so it's surfaced once rather than duplicated per row.
    try:
        from ..bundled_skills import any_skills_stale
        if any_skills_stale():
            rprint("[yellow]⚠[/yellow] installed skills differ from bundled "
                   "versions — run [bold]overcode skills install[/bold]")
    except Exception:
        pass

    if fix and broken:
        rprint()
        rprint("[bold]Restarting broken agents...[/bold]")
        from ..session_manager import SessionManager
        sm = SessionManager()
        for r in broken:
            sess = sm.get_session_by_name(r.name)
            if sess is None:
                rprint(f"  [yellow]skip {r.name} — session vanished[/yellow]")
                continue
            # fresh=False → resume the existing Claude session so history is preserved;
            # the new shell line rebuilt by the launcher will include --settings.
            if launcher.restart(sess, fresh=False):
                rprint(f"  [green]✓[/green] restarted {r.name}")
            else:
                rprint(f"  [red]✗[/red] failed to restart {r.name}")
