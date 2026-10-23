"""
Naming commands and the launch-time name check (#542, auto-naming.md §2a).

``overcode name check <name>`` and ``overcode name policy``, and the check
``launch`` / ``fork`` run on a ``-n`` given from inside an agent.
"""

import json
from typing import Annotated, Optional, Tuple

import typer
from rich.console import Console
from rich.markup import escape

from ._shared import app, SessionOption

# Never hard-wrapped: an agent reads these lines
_out = Console(soft_wrap=True)
err_console = Console(stderr=True, soft_wrap=True)
rprint = _out.print

# What a refused name exits with (``launch``, ``fork``, ``name check``)
EXIT_NAME_REFUSED = 3

name_app = typer.Typer(
    help="Naming policy: check a name, print the rules and advice (#542)",
    no_args_is_help=True,
)
app.add_typer(name_app, name="name")


def _naming(directory: Optional[str] = None):
    """(config, rules, advice) for a name chosen in ``directory``."""
    from ..config import get_naming_config
    from ..naming_policy import NamingRules, load_advice

    cfg = get_naming_config()
    return cfg, NamingRules.from_config(cfg["rules"]), load_advice(cfg["advice"], directory)


def naming_context(rules, directory: Optional[str], self_id: Optional[str] = None,
                   sessions=None, names_taken: bool = True):
    """The fleet ``name`` is checked against: live agents, aliases, recent archive."""
    from ..git_context import read_git_context_from_disk
    from ..naming_policy import NamingContext
    from ..session_manager import SessionManager

    sm = sessions or SessionManager()
    archived = ()
    if rules.recent_archived_hours > 0:
        from datetime import datetime, timedelta
        since = datetime.now() - timedelta(hours=rules.recent_archived_hours)
        try:
            archived = list(sm.iter_archived_records(since, newest_first=True))
        except Exception:
            archived = ()
    repo = None
    if directory:
        try:
            repo = read_git_context_from_disk(directory)[0]
        except Exception:
            repo = None
    return NamingContext.from_fleet(rules, sm.list_sessions(), archived=archived,
                                    repo=repo, self_id=self_id, names_taken=names_taken)


def evaluate(name: str, directory: Optional[str], *, use_model: bool = True,
             sessions=None, names_taken: bool = True):
    """(verdict, advice): the deterministic rules, then ``llm_check`` if they pass."""
    from ..naming_policy import check, llm_check, suggest

    _, rules, advice = _naming(directory)
    ctx = naming_context(rules, directory, sessions=sessions, names_taken=names_taken)
    verdict = check(name, ctx)
    if verdict.ok and use_model and rules.llm_check:
        found = llm_check(name, advice)
        if found is not None:
            failure, model_pick = found
            verdict.failures.append(failure)
            if model_pick and not check(model_pick, ctx).failures:
                verdict.suggestion = model_pick
            else:
                verdict.suggestion = suggest(name, ctx)
    return verdict, advice


def _refuse(verdict, advice: str, as_json: bool) -> None:
    err_console.print(escape(verdict.message(advice)), style="red", highlight=False)
    if as_json:
        from .agent import _echo_json
        _echo_json(json.dumps({"error": "naming_policy", "advice": advice, **verdict.to_dict()}))
    raise typer.Exit(code=EXIT_NAME_REFUSED)


def check_launch_name(name: str, directory: Optional[str], force_name: bool,
                      as_json: bool = False, sessions=None) -> Tuple[str, bool]:
    """Check a ``-n`` as ``launch`` / ``fork`` take it; (name_source, name_forced).

    From inside an agent the name is checked and a failure refused (exit
    3) unless ``force_name``, which gets past the policy but never past a
    hard rule. From a human shell nothing is refused: a failure is only
    warned about, with ``naming.rules.warn_humans``.
    """
    from ..name_source import HUMAN, PARENT, launched_from_agent

    if not launched_from_agent():
        _, rules, _ = _naming(directory)
        if rules.warn_humans:
            verdict, advice = evaluate(name, directory, sessions=sessions, names_taken=False)
            if not verdict.ok:
                err_console.print(f"Warning: {escape(verdict.message(advice))}",
                                  style="yellow", highlight=False)
        return HUMAN, False

    # An existing agent's name is the launcher's to settle (see from_fleet)
    verdict, advice = evaluate(name, directory, use_model=not force_name, sessions=sessions,
                               names_taken=False)
    if verdict.ok:
        return PARENT, False
    if verdict.hard or not force_name:
        _refuse(verdict, advice, as_json)
    err_console.print(f"note: --force-name: {escape(verdict.message())}", style="yellow",
                      highlight=False)
    return PARENT, True


@name_app.command("check")
def name_check(
    name: Annotated[str, typer.Argument(help="The name to check")],
    directory: Annotated[
        Optional[str], typer.Option("--directory", "-d", help="Where the agent would work (default: here)")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the verdict as JSON")] = False,
    session: SessionOption = "agents",
):
    """Check a name against the naming policy without launching (exit 3 if it fails).

    The same check `overcode launch -n` runs from inside an agent: the
    deterministic rules, then the model check if `naming.rules.llm_check`.
    """
    import os
    directory = directory or os.getcwd()
    verdict, advice = evaluate(name, directory)
    if as_json:
        typer.echo(json.dumps({"advice": advice, **verdict.to_dict()}))
    elif verdict.ok:
        rprint(f"[green]✓[/green] '{escape(name)}' passes the naming policy")
    else:
        err_console.print(escape(verdict.message(advice)), style="red", highlight=False)
    if not verdict.ok:
        raise typer.Exit(code=EXIT_NAME_REFUSED)


@name_app.command("policy")
def name_policy(
    directory: Annotated[
        Optional[str], typer.Option("--directory", "-d", help="Repo whose .overcode/naming.md applies (default: here)")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the policy as JSON")] = False,
):
    """Print the naming advice and rules. Read it before naming a child agent."""
    import os
    from ..naming_policy import project_advice_path
    from ..config import describe_auto_naming
    from ..summarizer_prompts import DEFAULT_NAMING_ADVICE, naming_prompt_customised, naming_prompt_path

    directory = directory or os.getcwd()
    cfg, rules, advice = _naming(directory)
    project = project_advice_path(directory)
    if as_json:
        typer.echo(json.dumps({
            "advice": advice or DEFAULT_NAMING_ADVICE,
            "advice_is_default": not advice,
            "project_advice": str(project) if project else None,
            "rules": cfg["rules"],
            "always": ["overcode's name pattern: letters, digits, - and _, at most 64",
                       "not taken by a live agent's name or old name"],
            "exit_code_on_refusal": EXIT_NAME_REFUSED,
            "enabled": cfg["enabled"],
            "auto_naming": describe_auto_naming(cfg),
            "first_after": cfg["first_after"],
            "max_length_target": cfg["max_length"],
            "warnings": cfg["warnings"],
        }))
        return

    rprint("[bold]Naming advice[/bold] (the policy: follow it when you name an agent)")
    rprint(escape(advice or DEFAULT_NAMING_ADVICE))
    if not advice:
        rprint("[dim](overcode's default; set naming.advice in config.yaml, or "
               "add .overcode/naming.md to the repo)[/dim]")
    elif project:
        rprint(f"[dim](includes {escape(str(project))})[/dim]")
    rprint(f"Aim for at most {cfg['max_length']} characters.")

    rprint(f"\n[bold]Auto-naming[/bold] {escape(describe_auto_naming(cfg))}")
    rprint("[dim](naming: in config.yaml, or \"Auto-naming settings…\" in the TUI palette: "
           "enabled, first_after.minutes, rename_every.minutes, max_renames)[/dim]")

    rprint("\n[bold]Always refused[/bold] (even with --force-name)")
    rprint("  a name outside letters, digits, - and _ (at most 64)")
    rprint("  a name taken by a live agent, or one of its old names")

    r = cfg["rules"]
    lines = []
    if r["pattern"]:
        lines.append(f"pattern: must match {r['pattern']}")
    if r["max_length"]:
        lines.append(f"max_length: at most {r['max_length']} characters")
    if r["forbid"]:
        lines.append(f"forbid: no word from {', '.join(r['forbid'])}")
    if r["require"]:
        lines.append(f"require: must match {r['require']}")
    if r["no_repo_prefix"]:
        lines.append("no_repo_prefix: don't start with the repo name while the repo column shows")
    if r["recent_archived_hours"]:
        lines.append(f"recent_archived_hours: not a name archived in the last "
                     f"{r['recent_archived_hours']:g} h")
    if r["llm_check"]:
        lines.append("llm_check: a model checks the name against the advice")
    rprint("\n[bold]Rules[/bold] (checked when an agent launches a child; --force-name overrides)")
    if lines:
        for line in lines:
            rprint(f"  {escape(line)}")
    else:
        rprint("  [dim]none set (naming.rules in config.yaml)[/dim]")
    rprint(f"\nA refused name exits {EXIT_NAME_REFUSED} with the failed rule and a suggestion; "
           "`overcode name check <name>` tries one without launching.")
    if naming_prompt_customised():
        rprint(f"[dim]Namer prompt: {escape(str(naming_prompt_path()))}[/dim]")
    for warning in cfg["warnings"]:
        err_console.print(f"Warning: {escape(warning)}", style="yellow", highlight=False)


@name_app.command("suggest")
def name_suggest(
    agent: Annotated[str, typer.Argument(help="The agent to name (a name, or an old name)")],
    advice_file: Annotated[
        Optional[str], typer.Option("--advice-file", help="Use this advice instead of the configured advice")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the result as JSON")] = False,
    session: SessionOption = "agents",
):
    """Ask the namer for one agent's name: the candidates, the pick and why. Renames nothing.

    One model call (the summarizer's, or naming.provider), billed to the
    `overcode` billing code. Works whether or not naming.enabled is on.
    """
    import time
    from pathlib import Path
    from ..config import get_naming_config
    from ..namer import (advice_for, gather_inputs, load_state, make_client, naming_context,
                         recent_archived, record_spend, suggest)
    from ..session_manager import SessionManager
    from ..summarizer_prompts import load_naming_prompt
    from ._shared import find_agent

    sm = SessionManager()
    sess = find_agent(sm, agent)
    if sess is None:
        err_console.print(f"Error: Agent '{escape(agent)}' not found", style="red", highlight=False)
        raise typer.Exit(code=1)
    cfg = get_naming_config()
    if advice_file:
        try:
            advice = Path(advice_file).expanduser().read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as e:
            err_console.print(f"Error: cannot read {escape(advice_file)}: {e}", style="red",
                              highlight=False)
            raise typer.Exit(code=1)
    else:
        advice = advice_for(cfg, sess.start_directory)
    tmux_session = sess.tmux_session or session

    def pane() -> str:
        from ..implementations import RealTmux
        return RealTmux().capture_pane(tmux_session, sess.tmux_window, lines=60) or ""

    sessions = sm.list_sessions()
    archived = recent_archived(sm)
    inputs = gather_inputs(sess, tmux_session=tmux_session, sessions=sessions, archived=archived,
                           max_length=cfg["max_length"], pane=pane,
                           state=load_state(tmux_session, sess.id), now=time.time())
    result = suggest(inputs, naming_context(cfg, sessions, archived, sess),
                     client=make_client(cfg.get("provider")), template=load_naming_prompt(),
                     advice=advice)
    try:
        record_spend(sm.ledger, result, sess)
    except Exception:
        pass  # the suggestion stands; the ledger row is bookkeeping
    if as_json:
        typer.echo(json.dumps({"agent": sess.name, "id": sess.id, "name_source": sess.name_source,
                               "advice": advice, **result.to_dict()}))
    else:
        rprint(f"[bold]{escape(sess.name)}[/bold] [dim]({escape(sess.name_source)})[/dim]")
        if result.error:
            err_console.print(f"Error: {escape(result.error)}", style="red", highlight=False)
        else:
            rprint(f"candidates: {escape(', '.join(result.candidates)) or '[dim]none[/dim]'}")
            pick = result.pick or "[dim]none passes[/dim]"
            rprint(f"pick:       {escape(pick) if result.pick else pick}"
                   + ("  [dim](the model would keep the current name)[/dim]" if result.keep else ""))
            if result.why:
                rprint(f"why:        {escape(result.why)}")
            rprint(f"[dim]{escape(result.model or '?')}, {result.tokens_in}+{result.tokens_out} "
                   f"tokens, ${result.cost_usd:.4f}[/dim]")
    if result.error:
        raise typer.Exit(code=1)


@name_app.command("lab")
def name_lab(
    archived: Annotated[
        Optional[int], typer.Option("--archived", help="Run on the last N archived agents (default: the live ones)")
    ] = None,
    advice_file: Annotated[
        Optional[str], typer.Option("--advice-file", help="The advice to try (default: the saved advice)")
    ] = None,
    vs: Annotated[
        Optional[str], typer.Option("--vs", help="A second advice file, compared side by side (B)")
    ] = None,
    csv_path: Annotated[Optional[str], typer.Option("--csv", help="Also write the rows to this CSV file")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the rows as JSON")] = False,
    session: SessionOption = "agents",
):
    """Try naming advice on the whole fleet without renaming anything (the naming lab, batch).

    One model call per agent and advice, in parallel, billed to the
    `overcode` billing code and counted against naming.cost_cap_per_day.
    A repo's .overcode/naming.md is still added after the advice, as the
    namer does. One row per agent: its name, the pick (A, and B with
    --vs), and the candidates passed over with why.
    """
    from ..config import get_naming_config
    from ..namer import make_client
    from ..naming_lab import (LabSpend, NamingLabRunner, collided, load_archived, load_live,
                              stats)
    from ..naming_policy import user_advice
    from ..session_manager import SessionManager
    from ..summarizer_prompts import DEFAULT_NAMING_ADVICE, load_naming_prompt

    def read(path: str) -> str:
        from pathlib import Path
        try:
            return Path(path).expanduser().read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as e:
            err_console.print(f"Error: cannot read {escape(path)}: {e}", style="red", highlight=False)
            raise typer.Exit(code=1)

    cfg = get_naming_config()
    drafts = [read(advice_file) if advice_file else (user_advice(cfg["advice"]) or DEFAULT_NAMING_ADVICE)]
    if vs:
        drafts.append(read(vs))
    client = make_client(cfg.get("provider"))
    if not getattr(client, "available", False):
        err_console.print("Error: no model: set summarizer: (or naming.provider) and its API key",
                          style="red", highlight=False)
        raise typer.Exit(code=1)
    sm = SessionManager()
    if archived:
        agents = load_archived(sm, session, cfg, archived)
    else:
        agents = load_live(sm, session, cfg)
    runner = NamingLabRunner(make_client=lambda: make_client(cfg.get("provider")),
                             spend=LabSpend(session, sm.ledger, cfg))
    template = load_naming_prompt()
    for advice in drafts:
        runner.run(agents, template, advice)
    if runner.blocked:
        err_console.print(f"Warning: {escape(runner.blocked)}", style="yellow", highlight=False)

    picks = [[runner.result(a, template, advice) for a in agents] for advice in drafts]
    clashes = [collided(agents, p) for p in picks]
    labels = ["a", "b"][:len(drafts)]
    rows = []
    for i, agent in enumerate(agents):
        row = {"agent": agent.name, "id": agent.id, "name_source": agent.source,
               "locked": agent.locked, "archived": agent.archived}
        for label, ps, clash in zip(labels, picks, clashes):
            p = ps[i]
            row[f"pick_{label}"] = p.pick if p is not None and not p.error else None
            row[f"keep_{label}"] = bool(p and p.keep)
            row[f"rejected_{label}"] = [{"name": n, "why": w} for n, w in (p.rejected if p else [])]
            row[f"why_{label}"] = p.why if p else ""
            row[f"error_{label}"] = (p.error if p else
                                     None if agent.has_inputs else "no recorded brief or prompts")
            row[f"collides_{label}"] = agent.id in clash
        rows.append(row)
    summary = {label: vars(stats(agents, ps)) for label, ps in zip(labels, picks)}

    if csv_path:
        import csv
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["agent", "name_source", "pick_a", "pick_b", "rejected"])
            for row in rows:
                rejected = "; ".join(f"{r['name']} ({r['why']})" for r in row["rejected_a"])
                writer.writerow([row["agent"], row["name_source"], row.get("pick_a") or "",
                                 row.get("pick_b") or "", rejected])
    if as_json:
        typer.echo(json.dumps({"rows": rows, "summary": summary, "calls": runner.calls,
                               "cost_usd": round(runner.spent_usd, 6)}))
        return

    from rich.table import Table
    table = Table(box=None, pad_edge=False, show_edge=False)
    table.add_column("agent")
    table.add_column("source", style="dim")
    for label in labels:
        table.add_column(f"pick {label.upper()}")
    table.add_column("rejected (A)", style="dim")
    for row in rows:
        cells = [escape(row["agent"]),
                 row["name_source"] + (" locked" if row["locked"] else "")]
        for label in labels:
            pick = row[f"pick_{label}"]
            if row[f"error_{label}"]:
                cell = f"[red]{escape(row[f'error_{label}'])}[/red]"
            elif row[f"keep_{label}"] or pick in (None, row["agent"]):
                cell = "[dim](keeps)[/dim]"
            elif row[f"collides_{label}"]:
                cell = f"[bold red]{escape(pick)}[/bold red] (collides)"
            else:
                cell = escape(pick)
            cells.append(cell)
        cells.append(escape(", ".join(f"{r['name']}: {r['why']}" for r in row["rejected_a"])))
        table.add_row(*cells)
    rprint(table)
    for label, s in summary.items():
        rprint(f"[bold]{label.upper()}[/bold]: {s['changed']} changed, {s['collisions']} collisions, "
               f"mean length {s['mean_length']:.1f}")
    rprint(f"[dim]{runner.calls} calls, ${runner.spent_usd:.4f} (billed to overcode)[/dim]")
