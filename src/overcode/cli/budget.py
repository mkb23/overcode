"""
Budget commands: set, transfer, show.
"""

from typing import Annotated, Optional

import typer
from rich import print as rprint

from ._shared import app, budget_app, SessionOption, find_agent


@budget_app.command("set")
def budget_set(
    name: Annotated[str, typer.Argument(help="Name of agent")],
    amount: Annotated[float, typer.Argument(help="Budget in USD (0 to clear)")],
    session: SessionOption = "agents",
):
    """Set cost budget for an agent.

    When an agent's estimated cost reaches the budget, heartbeats are
    disabled and supervision is skipped.

    Examples:
        overcode budget set my-agent 5.00    # $5 budget
        overcode budget set my-agent 0       # Clear budget
    """
    from ..session_manager import SessionManager

    manager = SessionManager()
    agent = find_agent(manager, name)
    if not agent:
        rprint(f"[red]Error: Agent '{name}' not found[/red]")
        raise typer.Exit(code=1)

    if amount < 0:
        rprint("[red]Error: Budget cannot be negative[/red]")
        raise typer.Exit(code=1)

    # If agent has a parent with a budget, require transfer instead (#364)
    if agent.parent_session_id:
        parent = manager.get_session(agent.parent_session_id)
        if parent and parent.cost_budget_usd > 0:
            rprint(
                f"[red]Error: Cannot set budget directly — parent '{parent.name}' has a budget (${parent.cost_budget_usd:.2f})[/red]\n"
                f"Use [bold]overcode budget transfer {parent.name} {name} <amount>[/bold] instead."
            )
            raise typer.Exit(code=1)

    manager.set_cost_budget(agent.id, amount)
    if amount > 0:
        rprint(f"[green]✓ Set {name} budget to ${amount:.2f}[/green]")
    else:
        rprint(f"[green]✓ Cleared budget for {name}[/green]")


@budget_app.command("transfer")
def budget_transfer(
    source: Annotated[str, typer.Argument(help="Source agent name (must be ancestor)")],
    target: Annotated[str, typer.Argument(help="Target agent name")],
    amount: Annotated[float, typer.Argument(help="Amount in USD to transfer")],
    session: SessionOption = "agents",
):
    """Transfer budget from parent to child agent (#244).

    Source must be an ancestor of target in the agent hierarchy.
    If source has unlimited budget (0), the target's budget is simply set.

    Examples:
        overcode budget transfer parent-agent child-agent 2.00
    """
    from ..session_manager import SessionManager

    manager = SessionManager()

    source_agent = find_agent(manager, source)
    if not source_agent:
        rprint(f"[red]Error: Source agent '{source}' not found[/red]")
        raise typer.Exit(code=1)

    target_agent = find_agent(manager, target)
    if not target_agent:
        rprint(f"[red]Error: Target agent '{target}' not found[/red]")
        raise typer.Exit(code=1)

    if amount <= 0:
        rprint("[red]Error: Transfer amount must be positive[/red]")
        raise typer.Exit(code=1)

    if not manager.is_ancestor(source_agent.id, target_agent.id):
        rprint(f"[red]Error: '{source}' is not an ancestor of '{target}'[/red]")
        raise typer.Exit(code=1)

    success = manager.transfer_budget(source_agent.id, target_agent.id, amount)
    if success:
        rprint(f"[green]✓ Transferred ${amount:.2f} from {source} to {target}[/green]")
    else:
        rprint(f"[red]Transfer failed: insufficient budget on '{source}'[/red]")
        raise typer.Exit(code=1)


@budget_app.command("reclaim")
def budget_reclaim(
    name: Annotated[str, typer.Argument(help="Name of the child agent to reclaim from")],
    session: SessionOption = "agents",
):
    """Refund a child agent's unused budget back to its parent (#432).

    Use this for agents that died or crashed without posting a result. For
    successful child completions the refund happens automatically when the
    report is processed.

    The refund is idempotent: a second call returns 0.

    Examples:
        overcode budget reclaim my-dead-child
    """
    from ..session_manager import SessionManager

    manager = SessionManager()
    agent = find_agent(manager, name)
    if not agent:
        rprint(f"[red]Error: Agent '{name}' not found[/red]")
        raise typer.Exit(code=1)

    if not agent.parent_session_id:
        rprint(f"[red]Error: '{name}' has no parent — nothing to reclaim to[/red]")
        raise typer.Exit(code=1)

    refunded = manager.reclaim_budget(agent.id)
    if refunded is None:
        rprint(f"[yellow]Nothing to reclaim from '{name}' (unlimited or missing parent)[/yellow]")
        return
    if refunded <= 0:
        rprint(f"[yellow]'{name}' has spent its full budget — nothing to refund[/yellow]")
        return

    parent = manager.get_session(agent.parent_session_id)
    parent_name = parent.name if parent else "(parent)"
    rprint(f"[green]✓ Refunded ${refunded:.4f} from {name} to {parent_name}[/green]")


@budget_app.command("show")
def budget_show(
    name: Annotated[
        Optional[str], typer.Argument(help="Agent name (omit for all)")
    ] = None,
    session: SessionOption = "agents",
):
    """Show budget status for agents.

    Shows per-agent budget, spent, remaining, and % used.
    For parents, includes subtree total spend.

    Examples:
        overcode budget show              # All agents
        overcode budget show my-agent     # Specific agent
    """
    from ..session_manager import SessionManager

    manager = SessionManager()

    if name:
        agents = []
        agent = find_agent(manager, name)
        if not agent:
            rprint(f"[red]Error: Agent '{name}' not found[/red]")
            raise typer.Exit(code=1)
        agents = [agent]
    else:
        agents = manager.list_sessions()
        if not agents:
            rprint("[dim]No running agents[/dim]")
            return

    for agent in agents:
        budget = agent.cost_budget_usd
        spent = agent.stats.estimated_cost_usd
        budget_str = f"${budget:.2f}" if budget > 0 else "unlimited"
        spent_str = f"${spent:.4f}"

        if budget > 0:
            remaining = max(0, budget - spent)
            pct = (spent / budget * 100) if budget > 0 else 0
            status = f"${remaining:.2f} remaining ({pct:.0f}% used)"
        else:
            status = "no limit"

        # Check for children's spend
        children = manager.get_descendants(agent.id)
        subtree_spend = spent + sum(c.stats.estimated_cost_usd for c in children)

        depth = manager.compute_depth(agent)
        indent = "  " * depth
        line = f"  {indent}{agent.name:<16} budget={budget_str:<10} spent={spent_str:<10} {status}"
        if children:
            line += f"  (subtree: ${subtree_spend:.4f})"
        print(line)


# ── Billing codes and the spend ledger ────────────────────────────────────


@app.command()
def rebill(
    name: Annotated[str, typer.Argument(help="Name of agent")],
    code: Annotated[str, typer.Argument(help="The new billing code")],
):
    """Bill an agent's spend to another code from now on.

    Forward only: what it spent so far stays on its old code. Children it
    launches from now on take the new code; children already running keep
    theirs (rebill them too if they should move).

    Examples:
        overcode rebill my-agent client-x
    """
    from ..session_manager import SessionManager

    manager = SessionManager()
    agent = find_agent(manager, name)
    if not agent:
        rprint(f"[red]Error: Agent '{name}' not found[/red]")
        raise typer.Exit(code=1)
    try:
        old = manager.rebill(agent.id, code)
    except ValueError as e:
        rprint(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)
    if old is None:
        rprint(f"[red]Error: Agent '{name}' not found[/red]")
        raise typer.Exit(code=1)
    if old == code.strip():
        rprint(f"'{agent.name}' is already billed to {old}")
        return
    rprint(f"[green]✓[/green] '{agent.name}' billed to [bold]{code.strip()}[/bold] from now on "
           f"(its spend so far stays on {old})")


@app.command()
def billing(
    since: Annotated[
        Optional[str],
        typer.Option("--since", help="Spend since: 30d, 2w, 12h, 2026-09, 2026-09-15"),
    ] = None,
    month: Annotated[
        Optional[str], typer.Option("--month", help="Spend in one month: 2026-09"),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON")] = False,
):
    """Spend per billing code, from the ledger.

    Every agent is billed to a code: its launch name unless launched with
    --billing-code, and always its parent's for a child. With no filter,
    everything ever recorded; --since and --month narrow it to a period.
    """
    import json
    from datetime import datetime

    from ..session_archive import parse_since
    from ..session_manager import SessionManager

    ledger = SessionManager().ledger
    try:
        if month:
            start = datetime.strptime(month, "%Y-%m")
            end = (start.replace(year=start.year + 1, month=1) if start.month == 12
                   else start.replace(month=start.month + 1))
            report = ledger.report(since=start, until=end)
            scope = f"in {month}"
        elif since:
            report = ledger.report(since=parse_since(since))
            scope = f"since {since}"
        else:
            report = ledger.totals()
            scope = "all time"
    except ValueError as e:
        rprint(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)

    if as_json:
        print(json.dumps({"scope": scope, "codes": report}, indent=2, sort_keys=True))
        return
    if not report:
        rprint(f"[dim]No spend recorded ({scope})[/dim]")
        return
    from ..tui_helpers import format_tokens

    rprint(f"\n[bold]Spend by billing code ({scope}):[/bold]\n")
    rows = sorted(report.items(), key=lambda kv: (-kv[1]["cost"], kv[0]))
    width = max(12, *(len(code) for code, _ in rows))
    print(f"  {'code':<{width}}  {'cost':>10}  {'tokens':>8}  {'agents':>6}  running")
    for code, entry in rows:
        print(f"  {code:<{width}}  ${entry['cost']:>9.2f}  {format_tokens(entry['tokens']):>8}  "
              f"{entry['agents']:>6}  {entry['running']:>7}")
    total = sum(e["cost"] for _, e in rows)
    print(f"  {'total':<{width}}  ${total:>9.2f}")
