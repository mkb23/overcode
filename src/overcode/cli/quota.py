"""Quota model commands: `overcode usage model`, `overcode usage calibration`, `overcode statusline`."""

import json
import time
from datetime import datetime
from typing import Annotated, Optional

import typer
from rich import print as rprint

from ._shared import app
from .monitoring import usage_app


def _status(now: Optional[float] = None) -> dict:
    from ..config import get_quota_config
    from ..quota_engine import UsageStore, default_account, quota_status

    cfg = get_quota_config()
    return quota_status(UsageStore(), default_account(), cfg, now=now or time.time()), cfg


def _when(epoch) -> str:
    if not isinstance(epoch, (int, float)):
        return "-"
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")


@usage_app.command("model")
def usage_model(
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON")] = False,
    refit: Annotated[bool, typer.Option("--refit", help="Fit now on the kept history and save it")] = False,
):
    """The quota model: fitted weights against the price ratios, and how well it fits.

    The Q% column predicts each agent's share of the weekly quota from its
    tokens priced at list rates, times a weight per token type. The engine
    fits the weights hourly to the observed weekly percentages (whole
    percents, from the usage API and, with calibration on, statusline
    ticks); with little data they stay near the price ratios.
    """
    from ..quota_engine import UsageStore, fit_account
    from ..quota_model import FEATURES, format_pp, prior_model

    status, cfg = _status()
    if refit:
        store = UsageStore()
        prior = prior_model(status["plan"], cfg.get("weekly_usd"))
        model = fit_account(store, status["account"], prior, now=time.time(),
                            weeks=cfg["weeks"], breaks=cfg["breaks"])
        store.save_model(status["account"], model)
        status, cfg = _status()
    model = status["model"]
    if as_json:
        out = dict(status, model=model.to_dict(), confidence=model.confidence,
                   implied_weekly_usd=model.implied_weekly_usd())
        print(json.dumps(out, indent=2, sort_keys=True, default=str))
        return
    latest = status["latest"]
    rprint(f"\n[bold]Quota model[/bold] (account {status['account']}, plan "
           f"{status['plan'] or 'unknown'}, prior ${status['prior_weekly_usd']:,.0f}/week API-equivalent)\n")
    if latest is None:
        rprint("  [dim]No subscription usage recorded yet: the engine polls it while the "
               "monitor daemon runs and you are logged in to Claude Code with a subscription.[/dim]")
    state = (f"fitted {_when(model.fitted_at)}" if model.fitted else "prior only (not fitted yet)")
    rprint(f"  status:     {state}, confidence [bold]{model.confidence}[/bold]")
    print(f"\n  {'feature':<9} {'fitted':>9} {'± sd':>8} {'prior':>9} {'fit/prior':>9}   pp per $100 list")
    for f in FEATURES:
        theta, prior, sd = model.theta.get(f, 0.0), model.prior.get(f, 0.0), model.sd.get(f, 0.0)
        ratio = f"{theta / prior:9.2f}" if prior > 0 else f"{'-':>9}"
        print(f"  {f:<9} {theta:9.5f} {sd:8.5f} {prior:9.5f} {ratio}   {theta * 100:6.2f}")
    implied = model.implied_weekly_usd()
    if implied:
        print(f"\n  implied weekly budget: ${implied:,.0f} of list-price output")
    if model.fitted:
        cov = f", {model.coverage:.0%} of polls inside their cell" if model.coverage is not None else ""
        print(f"  fit: {model.n_obs} observations ({model.n_ticks} statusline ticks) in "
              f"{model.n_windows} window(s), {model.n_segments} segment(s); σ {model.sigma_pp}pp, "
              f"RMSE {model.rmse_pp}pp{cov}")
        print(f"  5-hour: {model.five_hour_ratio:.2f} points per weekly point "
              f"({model.five_hour_n} intervals)")
    samples = status["samples"]
    print(f"  samples (last {cfg['weeks']} weeks): {samples['api']} usage polls, "
          f"{samples['statusline']} statusline ticks"
          + ("" if cfg["calibration"] else " (calibration off: overcode usage calibration on)"))
    if latest is not None:
        print(f"\n  this week (since {_when(status['week_start'])}): observed "
              f"{status['observed']:.0f}% weekly, overcode agents {format_pp(status['attributed'])} "
              f"predicted, unattributed {format_pp(status['unattributed'])}")
        if status["unattributed"] is not None and status["unattributed"] < -2:
            rprint("  [yellow]The model predicts more than was observed: its weights are "
                   "too high (overcode doctor checks this).[/yellow]")
    for note in model.notes:
        print(f"  note: {note}")
    print()


@usage_app.command("calibration")
def usage_calibration(
    state: Annotated[Optional[str], typer.Argument(help="on | off (omit to show)")] = None,
):
    """Opt in or out of statusline calibration samples.

    On: agents overcode launches from now on run `overcode statusline`,
    which records each response's rate-limit figures (the exact moment the
    weekly percentage ticks over) for the quota model and then prints your
    own statusline, if you configured one. Off (the default): nothing is
    added to an agent's settings.
    """
    from ..config import get_quota_config, set_quota_calibration

    if state is None:
        on = get_quota_config()["calibration"]
        rprint(f"Quota calibration is [bold]{'on' if on else 'off'}[/bold]")
        return
    if state not in ("on", "off"):
        rprint("[red]Error: say on or off[/red]")
        raise typer.Exit(code=1)
    set_quota_calibration(state == "on")
    rprint(f"[green]✓[/green] Quota calibration {state}: applies to agents launched from now on")


@app.command("statusline", hidden=True)
def statusline_cmd():
    """Calibration statusline (internal; run by Claude Code, see `overcode usage calibration`)."""
    from ..statusline_tap import main

    main()
