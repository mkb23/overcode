"""
Spend commands: record what an agent's evals and sims spent (experiment billing).
"""

from typing import Annotated, Optional

import typer
from rich import print as rprint

from ._shared import spend_app


def _parse(fields: dict, defaults: dict):
    """The ``Spend`` and the billing arguments of one record (validated, nothing written)."""
    from ..external_spend import Spend

    def num(key):
        value = fields.get(key) or 0
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or (isinstance(value, float) and not value.is_integer())):
            raise ValueError(f"{key} must be a whole number of tokens, not {value!r}")
        return int(value)
    usd = fields.get("usd")
    if usd is not None and (isinstance(usd, bool) or not isinstance(usd, (int, float))):
        raise ValueError(f"usd must be a number, not {usd!r}")
    spend = Spend(model=fields.get("model") or defaults.get("model"),
                  tok_in=num("in"), tok_out=num("out"), tok_cw=num("cache_write"),
                  tok_cr=num("cache_read"),
                  usd=float(usd) if usd is not None else None,
                  provider=fields.get("provider") or defaults.get("provider"))
    spend.validate()
    return spend, {key: fields.get(key) or defaults.get(key)
                   for key in ("agent", "code", "run", "label")}


@spend_app.command("record")
def spend_record(
    model: Annotated[Optional[str], typer.Option("--model", "-m", help="Model the tokens were spent on")] = None,
    tok_in: Annotated[int, typer.Option("--in", help="Input tokens")] = 0,
    tok_out: Annotated[int, typer.Option("--out", help="Output tokens")] = 0,
    cache_write: Annotated[int, typer.Option("--cache-write", help="Cache write tokens")] = 0,
    cache_read: Annotated[int, typer.Option("--cache-read", help="Cache read tokens")] = 0,
    usd: Annotated[Optional[float], typer.Option("--usd", help="The cost, when known (overrides token pricing)")] = None,
    provider: Annotated[Optional[str], typer.Option("--provider", help="anthropic, bedrock... (default: the agent's)")] = None,
    agent: Annotated[Optional[str], typer.Option("--agent", "-a", help="Agent to bill (default: this process's agent or job owner)")] = None,
    code: Annotated[Optional[str], typer.Option("--code", help="Billing code to bill (default: the agent's)")] = None,
    run: Annotated[Optional[str], typer.Option("--run", help="Run id: records sharing one add up to one run")] = None,
    label: Annotated[Optional[str], typer.Option("--label", help="What the run is called in reports")] = None,
    stdin: Annotated[bool, typer.Option("--stdin", help="Read records as JSON lines from stdin")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print what was recorded as JSON")] = False,
):
    """Record spend made outside an agent's conversation (an eval, a sim).

    Billed to the agent this runs in (OVERCODE_BILL_TO, an `overcode bash`
    job's owner, OVERCODE_SESSION_ID), under its billing code, and charged
    to its budget. Tokens are priced at --model's price; --usd gives the
    cost instead. Records with one --run id add up to one run.

    With --stdin, each line is a JSON object with any of: model, in, out,
    cache_write, cache_read, usd, provider, agent, code, run, label (the
    flags are the defaults). The Python helper (overcode.spend_client)
    uses this.

    Examples:
        overcode spend record --model claude-sonnet-4-5 --in 120000 --out 8000 --label sweep-a
        overcode spend record --usd 3.20 --label "sim batch 7"
    """
    import json
    import sys

    from ..external_spend import RecordError, default_source, record
    from ..session_manager import SessionManager

    manager = SessionManager()
    defaults = {"model": model, "provider": provider, "agent": agent, "code": code,
                "run": run, "label": label}
    source = default_source()
    if stdin:
        batch = []
        for n, line in enumerate(sys.stdin, 1):
            if not line.strip():
                continue
            try:
                fields = json.loads(line)
            except ValueError as e:
                rprint(f"[red]Error: line {n}: not JSON ({e})[/red]")
                raise typer.Exit(code=1)
            if not isinstance(fields, dict):
                rprint(f"[red]Error: line {n}: not a JSON object[/red]")
                raise typer.Exit(code=1)
            batch.append(fields)
    else:
        batch = [{"in": tok_in, "out": tok_out, "cache_write": cache_write,
                  "cache_read": cache_read, "usd": usd}]
    results = []
    try:
        # Every line is checked before any is recorded: a caller that retries
        # a refused batch must not count its first lines twice
        parsed = [_parse(fields, defaults) for fields in batch]
        for spend, where in parsed:
            results.append(record(manager, spend, source=source, **where))
    except (RecordError, ValueError) as e:
        if as_json:
            print(json.dumps({"error": str(e), "recorded": results}, default=str))
        else:
            rprint(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)
    if as_json:
        print(json.dumps(results[-1] if len(results) == 1 else results, default=str))
        return
    for r in results:
        who = f"'{r['agent_name']}'" if r.get("agent_name") else "no agent"
        note = " [yellow](model not in the price table: fallback rates)[/yellow]" \
            if r["priced"] == "fallback" else ""
        rprint(f"[green]✓[/green] ${r['cost']:.4f} to run {r['run']} "
               f"(${r['run_cost']:.4f} so far), {who}, code [bold]{r['code_name']}[/bold]{note}")
    last = results[-1] if results else None
    if last and last.get("over_budget"):
        rprint(f"[red]'{last['agent_name']}' is over its budget "
               f"(${last['charged']:.2f} of ${last['budget']:.2f}, evals included)[/red]")


@spend_app.command("helper-path")
def spend_helper_path():
    """Print the path of the stdlib-only Python helper (copy it into an eval's tree).

    The helper (`SpendMeter`) batches usage and records it with `overcode
    spend record --stdin`; it needs no overcode import, only `overcode` on
    PATH. Inside overcode's own environment: from overcode.spend_client
    import SpendMeter.
    """
    from .. import spend_client

    print(spend_client.__file__)
