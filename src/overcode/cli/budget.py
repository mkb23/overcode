"""
Budget commands: set, transfer, show.
"""

from typing import Annotated, Optional

import typer
from rich import print as rprint

from ._shared import budget_app, SessionOption


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
    agent = manager.get_session_by_name(name)
    if not agent:
        rprint(f"[red]Error: Agent '{name}' not found[/red]")
        raise typer.Exit(code=1)

    if amount < 0:
        rprint("[red]Error: Budget cannot be negative[/red]")
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

    source_agent = manager.get_session_by_name(source)
    if not source_agent:
        rprint(f"[red]Error: Source agent '{source}' not found[/red]")
        raise typer.Exit(code=1)

    target_agent = manager.get_session_by_name(target)
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
        agent = manager.get_session_by_name(name)
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
