"""
Budget commands: set, transfer, show.
"""

from typing import Annotated, Optional

import typer
from rich import print as rprint

from ._shared import app, billing_app, budget_app, SessionOption, find_agent


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
    # The code the name resolved to (a previous name reads as the current one)
    after = manager.get_session(agent.id)
    new = getattr(after, "billing_code", None) or code.strip()
    if old == new:
        rprint(f"'{agent.name}' is already billed to {old}")
        return
    rprint(f"[green]✓[/green] '{agent.name}' billed to [bold]{new}[/bold] from now on "
           f"(its spend so far stays on {old})")


@billing_app.callback(invoke_without_command=True)
def billing(
    ctx: typer.Context,
    since: Annotated[
        Optional[str],
        typer.Option("--since", help="Spend since: 30d, 2w, 12h, 2026-09, 2026-09-15"),
    ] = None,
    month: Annotated[
        Optional[str], typer.Option("--month", help="Spend in one month: 2026-09"),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON")] = False,
    ids: Annotated[bool, typer.Option("--ids", help="Show each code's id")] = False,
):
    """Spend per billing code, from the ledger.

    Every agent is billed to a code: --billing-code if given, else per
    billing.new_agent_code (its name by default, its folder, or
    billing.default_code), and always its parent's for a child. Launches
    under one name share one code. A code has an id and a
    name; rename and merge change how past spend reports, never the
    ledger. With no filter, everything ever recorded; --since and --month
    narrow it to a period.
    """
    if ctx.invoked_subcommand is not None:
        return
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

    # Subscription quota (cost model Phase B): predicted points of the
    # weekly quota per code, from the quota token stream. With no filter,
    # this week's (the quota is weekly); for a period, the period's.
    quota = _quota_by_code(
        since=(start if month else (parse_since(since) if since else None)),
        until=(end if month else None))
    codes = ledger.codes
    if quota is not None:
        # The quota stream's keys are code keys too (an id, else a legacy string)
        by_code: dict = {}
        for key, pp in quota["codes"].items():
            code = codes.canonical(key)
            by_code[code] = by_code.get(code, 0.0) + pp
        quota["codes"] = by_code
        for code, pp in quota["codes"].items():
            report.setdefault(code, {"cost": 0.0, "tokens": 0, "agents": 0, "running": 0})
        for code, entry in report.items():
            entry["quota_pp"] = round(quota["codes"].get(code, 0.0), 2)
    for code, entry in report.items():
        entry["name"] = codes.name_of(code)

    if as_json:
        out = {"scope": scope, "codes": report}
        if quota is not None:
            out["quota"] = {k: v for k, v in quota.items() if k != "codes"}
        print(json.dumps(out, indent=2, sort_keys=True))
        return
    if not report:
        rprint(f"[dim]No spend recorded ({scope})[/dim]")
        return
    from ..codes import is_code_id
    from ..quota_model import format_pp
    from ..tui_helpers import format_tokens

    rprint(f"\n[bold]Spend by billing code ({scope}):[/bold]\n")
    rows = sorted(report.items(), key=lambda kv: (-kv[1]["cost"], kv[1]["name"]))
    width = max(12, *(len(entry["name"]) for _, entry in rows))
    # Claude background sessions billed to a code are among its agents
    background = any(entry.get("background") for _, entry in rows)
    bg_head = "  background" if background else ""
    q_head = f"  {quota['label']:>9}" if quota is not None else ""
    id_head = f"  {'id':<34}" if ids else ""
    print(f"  {'code':<{width}}  {'cost':>10}  {'tokens':>8}{q_head}  {'agents':>6}  running"
          f"{bg_head}{id_head}")
    for code, entry in rows:
        bg_cell = f"  {entry.get('background', 0):>10}" if background else ""
        q_cell = f"  {format_pp(entry.get('quota_pp', 0.0)):>9}" if quota is not None else ""
        id_cell = f"  {code if is_code_id(code) else '(no id)':<34}" if ids else ""
        print(f"  {entry['name']:<{width}}  ${entry['cost']:>9.2f}  {format_tokens(entry['tokens']):>8}"
              f"{q_cell}  {entry['agents']:>6}  {entry['running']:>7}{bg_cell}{id_cell}")
    if quota is not None and quota.get("unattributed") is not None:
        print(f"  {'unattributed':<{width}}  {'':>10}  {'':>8}  {format_pp(quota['unattributed']):>9}")
    total = sum(e["cost"] for _, e in rows)
    q_total = f"  {format_pp(sum(quota['codes'].values())):>9}" if quota is not None else ""
    print(f"  {'total':<{width}}  ${total:>9.2f}  {'':>8}{q_total}")
    if quota is not None:
        rprint(f"\n  [dim]{quota['label'].strip()}: predicted points of the weekly subscription "
               f"quota (estimate, model {quota['confidence']}; overcode usage model)[/dim]")


@billing_app.command("rename")
def billing_rename(
    old: Annotated[str, typer.Argument(help="The code: its name, a previous name, or its id")],
    new: Annotated[str, typer.Argument(help="Its new name")],
):
    """Rename a billing code: every report, past and future, shows the new name.

    The ledger is not touched. The old name still resolves (a launch or a
    rebill to it bills to this code) until another code takes it. Refuses
    a name another code has (merge folds two codes into one).

    Examples:
        overcode billing rename overcode-main2 billing-ids
    """
    from ..codes import CodeError
    from ..session_manager import SessionManager

    manager = SessionManager()
    try:
        code_id, was = manager.ledger.codes.rename(old, new)
    except CodeError as e:
        rprint(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)
    _refresh_names(manager)
    if was == new.strip():
        rprint(f"Billing code {was} is already called that")
        return
    rprint(f"[green]✓[/green] Billing code {was} is now [bold]{new.strip()}[/bold] [dim]({code_id})[/dim]")


@billing_app.command("merge")
def billing_merge(
    codes_: Annotated[
        list[str],
        typer.Argument(metavar="CODE...", help="Codes to fold away: names, previous names, ids, "
                       "or quoted patterns ('shirka*')"),
    ],
    into: Annotated[Optional[str], typer.Option("--into", help="The code they fold into "
                                                "(it must exist)")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="List what would merge; change nothing")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask before merging codes a pattern picked")] = False,
):
    """Fold billing codes into another: their spend, past and future, reports under it.

    For codes that should have been one once they have spend. Recorded in
    the code registry (one write), never by rewriting the ledger; the folded
    codes' names become previous names of the other, and their agents bill
    to it from now on. A pattern (*, ?, [..]; quote it) matches current
    names of unmerged codes, never the target; it asks first unless -y.
    `merge A B` (no --into) folds A into B. `overcode billing unmerge`
    undoes one.

    Examples:
        overcode billing merge api-v2 api
        overcode billing merge 'shirka*' --into shirka --dry-run
        overcode billing merge shirka2 shirka-fork1 --into shirka
    """
    from fnmatch import fnmatchcase

    from ..codes import CodeError
    from ..session_manager import SessionManager

    if into is None:
        if len(codes_) != 2:
            rprint("[red]Error: name the code to merge into with --into[/red]")
            raise typer.Exit(code=1)
        codes_, into = codes_[:1], codes_[1]
    manager = SessionManager()
    ledger = manager.ledger
    codes = ledger.codes
    target = codes.find(into)
    if target is None:
        rprint(f"[red]Error: no billing code '{into}' (merge never makes one; "
               f"`overcode billing rename` renames)[/red]")
        raise typer.Exit(code=1)
    live = {cid: e for cid, e in codes.codes().items() if not e.get("merged_into")}
    picked: list = []
    patterned = False
    for what in codes_:
        if any(ch in what for ch in "*?["):
            patterned = True
            hits = sorted((cid for cid, e in live.items()
                           if cid != target and fnmatchcase(str(e.get("name", "")), what)),
                          key=lambda cid: live[cid]["name"])
            if not hits:
                rprint(f"[red]Error: no billing code matches '{what}'[/red]")
                raise typer.Exit(code=1)
        else:
            code_id = codes.find(what)
            if code_id is None:
                rprint(f"[red]Error: no billing code '{what}'[/red]")
                raise typer.Exit(code=1)
            if code_id == target:
                rprint(f"[red]Error: '{what}' and '{into}' are the same billing code[/red]")
                raise typer.Exit(code=1)
            hits = [code_id]
        picked += [cid for cid in hits if cid not in picked]
    totals = ledger.totals()
    spend = {cid: totals.get(cid) or {} for cid in picked}
    cost = sum(float(e.get("cost", 0) or 0) for e in spend.values())
    agents = sum(int(e.get("agents", 0) or 0) for e in spend.values())
    into_name = codes.name_of(target)
    if len(picked) > 1 or patterned or dry_run:
        width = max(12, *(len(codes.name_of(c)) for c in picked))
        rprint(f"{'Would merge' if dry_run else 'Merging'} {len(picked)} "
               f"code{'s' if len(picked) != 1 else ''} (${cost:,.2f}, {agents} "
               f"agent{'s' if agents != 1 else ''}) into [bold]{into_name}[/bold]:")
        for cid in picked:
            e = spend[cid]
            print(f"  {codes.name_of(cid):<{width}}  ${float(e.get('cost', 0) or 0):>9.2f}"
                  f"  {int(e.get('agents', 0) or 0):>4} agents")
    if dry_run:
        return
    if patterned and not yes and not typer.confirm("Merge them?"):
        raise typer.Exit(code=1)
    try:
        _, folded = codes.merge_many(picked, target)
    except CodeError as e:
        rprint(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)
    _refresh_names(manager)
    what = f"Billing code {folded[0][1]}" if len(folded) == 1 else f"{len(folded)} billing codes"
    rprint(f"[green]✓[/green] {what} folded into [bold]{into_name}[/bold] "
           f"[dim](undo one: overcode billing unmerge <code>)[/dim]")


def _idle_days(text: str) -> float:
    """``--idle``: ``30d``, ``2w``, ``12h`` or a bare number of days."""
    from datetime import datetime

    from ..session_archive import parse_since

    text = text.strip()
    if text.replace(".", "", 1).isdigit():
        return float(text)
    if text[-1:].lower() not in "dwh" or not text[:-1].isdigit():
        raise ValueError(f"not a duration (30d, 2w, 12h): {text!r}")
    now = datetime.now()
    return (now - parse_since(text, now)).total_seconds() / 86400


@billing_app.command("collapse")
def billing_collapse(
    codes_: Annotated[
        Optional[list[str]],
        typer.Argument(metavar="[CODE]...", help="Codes to collapse (default: every code the filters pick)"),
    ] = None,
    under: Annotated[
        Optional[float], typer.Option("--under", help="Only codes with less than this spend in total ($)"),
    ] = None,
    idle: Annotated[
        Optional[str], typer.Option("--idle", help="Only codes with no running agent and no spend for this long: 30d, 2w"),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="List what would collapse; change nothing")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask before collapsing codes the filters picked")] = False,
):
    """Collapse codes into the default code (billing.default_code, "general").

    A merge into the default code: the codes' spend, past and future,
    reports under it, their names resolve to it and their live agents bill
    to it. The ledger is not touched; `overcode billing unmerge` undoes one
    (its live agents stay on the default code). With no codes, every code
    the filters pick; given codes are filtered too. The default code itself
    never collapses.

    Examples:
        overcode billing collapse scratch try-1
        overcode billing collapse --under 1.00 --idle 30d --dry-run
    """
    from datetime import datetime

    from ..codes import CodeError
    from ..config import get_billing_defaults
    from ..ledger import collapsible
    from ..session_manager import SessionManager

    if not codes_ and under is None and idle is None:
        rprint("[red]Error: name codes to collapse, or pick them with --under / --idle[/red]")
        raise typer.Exit(code=1)
    try:
        idle_days = _idle_days(idle) if idle is not None else None
    except ValueError as e:
        rprint(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)
    settings = get_billing_defaults()
    for warning in settings["warnings"]:
        rprint(f"[yellow]config.yaml: {warning}[/yellow]")
    default = settings["default_code"]
    manager = SessionManager()
    ledger = manager.ledger
    codes = ledger.codes
    default_id = codes.find(default)
    if codes_:
        picked = []
        for what in codes_:
            code_id = codes.find(what)
            if code_id is None:
                rprint(f"[red]Error: no billing code '{what}'[/red]")
                raise typer.Exit(code=1)
            if code_id == default_id:
                rprint(f"[red]Error: '{what}' is the default billing code; it cannot be collapsed[/red]")
                raise typer.Exit(code=1)
            if code_id not in picked:
                picked.append(code_id)
    else:
        picked = [cid for cid, e in codes.codes().items()
                  if not e.get("merged_into") and cid != default_id]
    now = datetime.now()
    rows = list(ledger.iter_rows())
    totals = ledger.totals(now=now)
    last = ledger.last_activity(rows) if idle_days is not None else {}
    chosen = []
    for code_id in picked:
        spent = totals.get(code_id) or {}
        if collapsible(float(spent.get("cost", 0) or 0), int(spent.get("running", 0) or 0),
                       last.get(code_id), now, under=under, idle_days=idle_days):
            chosen.append((code_id, float(spent.get("cost", 0) or 0)))
    if not chosen:
        rprint("[dim]No billing code to collapse[/dim]")
        return
    width = max(12, *(len(codes.name_of(c)) for c, _ in chosen))
    verb = "Would collapse" if dry_run else "Collapsing"
    total = sum(cost for _, cost in chosen)
    rprint(f"{verb} {len(chosen)} code{'s' if len(chosen) != 1 else ''} "
           f"(${total:,.2f}) into [bold]{default}[/bold]:")
    for code_id, cost in chosen:
        print(f"  {codes.name_of(code_id):<{width}}  ${cost:>9.2f}")
    if dry_run:
        return
    if not codes_ and not yes and not typer.confirm("Collapse them?"):
        raise typer.Exit(code=1)
    try:
        _, folded = codes.collapse([c for c, _ in chosen], default)
    except CodeError as e:
        rprint(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)
    _refresh_names(manager)
    rprint(f"[green]✓[/green] {len(folded)} code{'s' if len(folded) != 1 else ''} collapsed into "
           f"[bold]{default}[/bold] [dim](undo one: overcode billing unmerge <code>)[/dim]")


@billing_app.command("unmerge")
def billing_unmerge(
    code: Annotated[str, typer.Argument(help="The merged code: its name, a previous name, or its id")],
):
    """Undo a merge or collapse: the code reports its own spend again.

    Registry only, like the merge: its rows report under it again, past and
    future, and its names resolve to it. Live agents moved to the other code
    at the merge stay there (`overcode rebill` moves one back).

    Examples:
        overcode billing unmerge api-v2
    """
    from ..codes import CodeError
    from ..session_manager import SessionManager

    manager = SessionManager()
    try:
        code_id, name, into = manager.ledger.codes.unmerge(code)
    except CodeError as e:
        rprint(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)
    _refresh_names(manager)
    rprint(f"[green]✓[/green] Billing code [bold]{name}[/bold] reports its own spend again "
           f"(no longer merged into {into}) [dim]({code_id})[/dim]")
    rprint(f"  [dim]Agents moved to {into} at the merge stay on it[/dim]")


@billing_app.command("codes")
def billing_codes(
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON")] = False,
    merged: Annotated[bool, typer.Option("--merged", help="Also list merged codes (and what into)")] = False,
):
    """List billing codes: name, id, source, agents, spend, previous names.

    source: default (named after a launch) or human (given with
    --billing-code, a rebill, a rename, billing.new_agent_code folder or
    default). --merged adds merged codes, which `billing unmerge` restores.
    """
    import json

    from ..session_manager import SessionManager

    manager = SessionManager()
    codes = manager.ledger.codes
    totals = manager.ledger.totals()
    live: dict = {}
    for session in manager.list_sessions():
        if session.billing_code_id:
            key = codes.canonical(session.billing_code_id)
            live[key] = live.get(key, 0) + 1
    out = []
    for code_id, entry in codes.codes().items():
        if entry.get("merged_into") and not merged:
            continue
        spent = totals.get(code_id) or {}
        out.append({"id": code_id, "name": entry.get("name"), "source": entry.get("source"),
                    "created": entry.get("created"), "live": live.get(code_id, 0),
                    "agents": spent.get("agents", 0), "cost": spent.get("cost", 0.0),
                    "previous_names": list(entry.get("previous_names") or ())})
        if entry.get("merged_into"):
            out[-1]["merged_into"] = codes.name_of(entry["merged_into"])
    out.sort(key=lambda c: (-c["cost"], str(c["name"])))
    if as_json:
        print(json.dumps(out, indent=2))
        return
    if not out:
        rprint("[dim]No billing codes yet[/dim]")
        return
    width = max(12, *(len(str(c["name"])) for c in out))
    print(f"  {'code':<{width}}  {'id':<34}  {'source':<7}  {'live':>4}  {'agents':>6}  {'cost':>10}"
          "  previous names")
    for c in out:
        print(f"  {c['name']:<{width}}  {c['id']:<34}  {c['source'] or '-':<7}  {c['live']:>4}"
              f"  {c['agents']:>6}  ${c['cost']:>9.2f}  {', '.join(c['previous_names']) or '-'}"
              + (f"  (merged into {c['merged_into']})" if c.get("merged_into") else ""))


def _refresh_names(manager) -> None:
    """Live agents' billing code name copies, after a rename or merge (the daemon would, later)."""
    try:
        from ..layout_migration import fill_billing_codes

        fill_billing_codes(manager)
    except Exception as e:  # the registry is right; the daemon catches the copies up
        rprint(f"[dim]Agents' billing names not refreshed yet: {e}[/dim]")


def _quota_by_code(since=None, until=None) -> Optional[dict]:
    """Predicted weekly-quota points per billing code, None for a non-subscriber.

    ``since`` None: this week's, with the unattributed rest of the observed
    weekly figure. Never raises (the spend report must print regardless).
    """
    import time

    try:
        from ..config import get_quota_config
        from ..quota_engine import UsageStore, default_account, quota_by_code, quota_status

        cfg = get_quota_config()
        if not cfg["enabled"]:
            return None
        store = UsageStore()
        account = default_account()
        status = quota_status(store, account, cfg, now=time.time())
        if status["latest"] is None:
            return None
        model = status["model"]
        if since is None:
            return {"label": "quota wk", "codes": status["codes"],
                    "unattributed": status["unattributed"], "observed": status["observed"],
                    "confidence": model.confidence}
        codes = quota_by_code(store, model, since=since.timestamp(),
                              until=until.timestamp() if until else None, account=account)
        return {"label": "quota", "codes": codes, "unattributed": None,
                "observed": None, "confidence": model.confidence}
    except Exception:
        return None
