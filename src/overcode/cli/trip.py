"""
Trip counter commands: reset, show (trip.py).
"""

import json
from typing import Annotated, Optional

import typer
from rich import print as rprint

from ._shared import app, SessionOption, find_agent

trip_app = typer.Typer(
    name="trip",
    help="Trip counters: each agent's spend since you last reset it. "
         "Totals, billing and budgets keep counting.",
    no_args_is_help=True,
)
app.add_typer(trip_app, name="trip")


@trip_app.command("reset")
def trip_reset(
    name: Annotated[Optional[str], typer.Argument(help="Name of agent")] = None,
    tree: Annotated[
        bool, typer.Option("--tree", help="Also every agent under it (its children, theirs…)")
    ] = False,
    all_: Annotated[bool, typer.Option("--all", help="Every agent in this tmux session")] = False,
    session: SessionOption = "agents",
):
    """Zero an agent's trip counter: its row's spend (tokens, $, energy, Q%) starts again.

    The total keeps counting: the ledger, billing codes, `overcode billing`
    and budgets see all spend, so a reset never gives an agent more budget.

    Examples:
        overcode trip reset my-agent           # this agent
        overcode trip reset my-agent --tree    # it and its children
        overcode trip reset --all              # every agent
    """
    from ..session_manager import SessionManager
    from .. import trip

    if all_ == bool(name):
        rprint("[red]Error: name an agent, or pass --all[/red]")
        raise typer.Exit(code=1)
    manager = SessionManager()
    if all_:
        ids = [s.id for s in manager.list_sessions()
               if s.tmux_session == session and not s.is_remote]
    else:
        agent = find_agent(manager, name)
        if not agent:
            rprint(f"[red]Error: Agent '{name}' not found[/red]")
            raise typer.Exit(code=1)
        ids = trip.tree_ids(manager, agent.id) if tree else [agent.id]
    done = trip.reset(manager, ids, session)
    if not done:
        rprint("[dim]No agents to reset[/dim]")
        return
    names = [s.name for s in (manager.get_session(sid) for sid in done) if s]
    rprint(f"[green]✓ Trip reset: {', '.join(names)}[/green]")
    rprint("[dim]Totals, billing and budgets keep counting.[/dim]")


def trip_report(sess, daemon_state=None) -> dict:
    """An agent's trip and total spend, as `trip show --json` and `list --json` print them.

    The engine's published values when it is running, else from the record.
    """
    from .. import trip

    ds = daemon_state.get_session(sess.id) if daemon_state is not None else None
    totals = ds if ds is not None else sess.stats
    total = {name: getattr(totals, name, 0) for name in trip.TRIP_FIELDS}
    quota = getattr(ds, "quota", None) if ds is not None else None
    if isinstance(quota, dict) and quota.get("pp7d") is not None:
        total["pp7d"] = quota["pp7d"]
    if ds is not None:
        view = ds.trip
    else:
        view = trip.trip_view(sess, sess.stats)
    return {"total": total, "trip": view,
            "trip_reset_at": getattr(sess, "trip_reset_at", None)}


@trip_app.command("show")
def trip_show(
    name: Annotated[str, typer.Argument(help="Name of agent")],
    as_json: Annotated[bool, typer.Option("--json", help="Print trip and total as JSON")] = False,
    session: SessionOption = "agents",
):
    """Show an agent's trip counter beside its total.

    Examples:
        overcode trip show my-agent
    """
    from ..monitor_daemon_state import get_monitor_daemon_state
    from ..quota_model import format_pp
    from ..session_manager import SessionManager
    from ..trip import describe, trip_tokens
    from ..tui_helpers import format_cost, format_joules, format_tokens

    manager = SessionManager()
    agent = find_agent(manager, name)
    if not agent:
        rprint(f"[red]Error: Agent '{name}' not found[/red]")
        raise typer.Exit(code=1)
    state = get_monitor_daemon_state(session)
    if state is not None and state.is_stale():
        state = None
    report = trip_report(agent, state)
    if as_json:
        typer.echo(json.dumps({"name": agent.name, "id": agent.id, **report}, indent=2))
        return

    def line(values: dict) -> str:
        text = (f"{format_cost(values.get('estimated_cost_usd') or 0.0)}  "
                f"{format_tokens(trip_tokens(values))} tokens  "
                f"{format_joules(values.get('estimated_energy_j') or 0.0).strip()}")
        if values.get("pp7d") is not None:
            text += f"  {format_pp(values['pp7d'])} of the weekly quota"
        return text

    rprint(f"[bold]{agent.name}[/bold]")
    view = report["trip"]
    if view:
        rprint(f"  ↺ Trip   {line(view)}  [dim]{describe(view)}[/dim]")
    else:
        rprint("  ↺ Trip   [dim]never reset: the row shows the total[/dim]")
    rprint(f"  Σ Total  {line(report['total'])}")
    rprint("[dim]Budgets count the total: a trip reset never adds budget.[/dim]")
