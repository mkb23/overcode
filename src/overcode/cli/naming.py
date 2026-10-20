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
                   sessions=None):
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
                                    repo=repo, self_id=self_id)


def evaluate(name: str, directory: Optional[str], *, use_model: bool = True,
             sessions=None):
    """(verdict, advice): the deterministic rules, then ``llm_check`` if they pass."""
    from ..naming_policy import check, llm_check, suggest

    _, rules, advice = _naming(directory)
    ctx = naming_context(rules, directory, sessions=sessions)
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
            verdict, advice = evaluate(name, directory, sessions=sessions)
            if not verdict.ok:
                err_console.print(f"Warning: {escape(verdict.message(advice))}",
                                  style="yellow", highlight=False)
        return HUMAN, False

    verdict, advice = evaluate(name, directory, use_model=not force_name, sessions=sessions)
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
