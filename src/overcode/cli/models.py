"""
Model metadata commands: info, lookup, refresh (#473).

The catalog behind the CTX% and $ columns — see docs/configuration.md,
"Model metadata (context windows and pricing)".
"""

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich import print as rprint
from rich.markup import escape

from ._shared import models_app


@models_app.command("info")
def models_info():
    """Show which catalog lookups resolve against, and how old it is."""
    from ..model_metadata import (
        DEFAULT_STALE_AFTER_DAYS,
        local_cache_age_days,
        local_cache_path,
        opencode_models_cache_path,
        snapshot_info,
        staleness_findings,
    )

    info = snapshot_info()
    rprint("[bold]Active model metadata catalog[/bold]")
    rprint(f"  tier:       {info['tier']}")
    rprint(f"  path:       {info['path']}")
    rprint(f"  source:     {info['source']}")
    rprint(f"  fetched:    {info['fetched_at']}")
    rprint(f"  models:     {info['model_count']}")
    rprint()
    rprint("[bold]Local tiers[/bold] (newest wins; bundled snapshot is the fallback)")
    age = local_cache_age_days()
    local = local_cache_path()
    if age is None:
        rprint(f"  refreshed cache: [dim]absent[/dim] ({local})")
    else:
        rprint(f"  refreshed cache: {age:.1f} days old ({local})")
    oc = opencode_models_cache_path()
    rprint(f"  opencode cache:  {'present' if oc.exists() else '[dim]absent[/dim]'} ({oc})")
    for finding in staleness_findings(DEFAULT_STALE_AFTER_DAYS):
        rprint(f"[yellow]⚠[/yellow] {finding}")
    rprint()
    _print_price_table()


def _print_price_table() -> None:
    """Which Claude price table prices are read from, its date, source and age."""
    from .. import price_table

    rows, header = price_table.table()
    max_age = price_table_max_age_days()
    rprint("[bold]Claude price table[/bold] (config model_pricing: rows win over it)")
    tier = header.get("tier", "vendored")
    label = {"local": "local override (overcode models refresh --prices)",
             "vendored": "shipped with this overcode"}.get(tier, tier)
    rprint(f"  in use:     {label}")
    rprint(f"  path:       {header.get('origin')}")
    age = price_table.age_days(header)
    age_text = "" if age is None else f" ({age} days old)"
    rprint(f"  as of:      {header.get('as_of') or 'unknown'}{age_text}")
    rprint(f"  source:     {escape(_table_source(header, rows))}")
    rprint(f"  models:     {len(rows)}")
    if tier == "local":
        rprint(f"  shipped:    as of {header.get('vendored_as_of')} (rows the override lacks)")
    ignored = header.get("ignored_local")
    if ignored:
        rprint(f"  [dim]override {ignored['origin']} (as of {ignored['as_of']}) is ignored: "
               f"the shipped table is as new or newer[/dim]")
    elif tier != "local":
        rprint(f"  [dim]local override: absent ({price_table.local_override_path()})[/dim]")
    for finding in price_table.staleness_findings(max_age):
        rprint(f"[yellow]⚠[/yellow] {escape(finding)}")


def _table_source(header: dict, rows: dict) -> str:
    if header.get("source"):
        return str(header["source"])
    # The vendored file names its source per row; the first clause is the list it came from
    first = next(iter(rows.values()), None)
    if first is not None and first.source:
        return first.source.split(":")[0]
    return str(header.get("generated_by") or "unknown")


def price_table_max_age_days() -> float:
    """``pricing.table_max_age_days`` in config.yaml, else the default."""
    from ..price_table import max_age_days_from
    try:
        from ..config import _cached_config
        return max_age_days_from(_cached_config())
    except Exception:
        return max_age_days_from({})


@models_app.command("lookup")
def models_lookup(
    model: Annotated[str, typer.Argument(help="Model id as a backend reports it, e.g. zai/glm-4.6")],
):
    """Show what overcode resolves for a model id, and which tier answered."""
    from ..history_reader import (
        _bare_model_id,
        curated_context_window,
        model_context_window,
        model_short_name,
    )
    from ..model_metadata import lookup, snapshot_info
    from ..pricing import lookup_pricing, priced_by_family

    bare = _bare_model_id(model)
    meta = lookup(model)
    window = model_context_window(model)
    pricing = lookup_pricing(model)

    curated = curated_context_window(model)
    if curated is not None:
        window_tier = "curated" if curated[0] == bare else f"curated, family {curated[0]}"
    elif meta and meta.context_window:
        window_tier = f"catalog ({snapshot_info()['tier']})"
    else:
        window_tier = "unknown"
    if pricing.source.startswith("price table"):
        price_tier = pricing.source
    elif pricing.source.startswith("family"):
        price_tier = f"curated {pricing.source}"
        if priced_by_family(model):
            price_tier += "; no price-table row for this model"
    elif meta and meta.has_pricing:
        price_tier = f"catalog ({snapshot_info()['tier']})"
    else:
        price_tier = "unknown (configured default rates apply)"

    rprint(f"[bold]{model}[/bold]  (bare id: {bare}, MDL: {model_short_name(model)})")
    rprint(f"  context window: {f'{window:,}' if window else '—'}  {escape(f'[{window_tier}]')}")
    if pricing.input or pricing.output:
        rprint(
            f"  list price:     ${pricing.input:g} in / ${pricing.output:g} out per MTok"
            f" (cache read ${pricing.cache_read:g}, write ${pricing.cache_write:g})  {escape(f'[{price_tier}]')}"
        )
        if pricing.long is not None:
            rprint(f"  long context:   ${pricing.long.input:g} in / ${pricing.long.output:g} out"
                   f" per MTok on prompts over {pricing.long_above:,} tokens")
        if pricing.regional != 1.0:
            rprint(f"  bedrock:        x{pricing.regional:g} on geographic / in-region profiles"
                   " (global. is list price)")
    else:
        rprint(f"  list price:     —  {escape(f'[{price_tier}]')}")
    if meta:
        rprint(f"  catalog entry:  {meta.name} via {meta.provider}"
               f"{' (open weights)' if meta.open_weights else ''}")
    if window is None and meta is None:
        rprint("[dim]  Not in the curated tables or the catalog. Try `overcode models refresh`, "
               "or see docs/design/model-alias-resolution.md.[/dim]")


# The CLI refresh gives up on models.dev after this long in all (DNS included):
# an air-gapped host should hear "offline" in seconds, not wait on a resolver
CLI_FETCH_DEADLINE_SECONDS = 10.0
CLI_FETCH_TIMEOUT_SECONDS = 8.0

OFFLINE_HINT = ("The Claude price table does not need the network: it ships with each "
                "release, and `overcode models refresh --prices <file>` rebuilds it from an AWS "
                "Price List file copied in by hand (docs/configuration.md, "
                "\"Prices on an air-gapped host\").")


@models_app.command("refresh")
def models_refresh(
    check: Annotated[
        bool, typer.Option("--check", help="Report only; fetch and write nothing "
                                           "(with --prices: show what would change)")
    ] = False,
    prices: Annotated[
        Optional[Path], typer.Option(
            "--prices", metavar="FILE",
            help="Rebuild the Claude price table from an AWS Price List file "
                 "(AmazonBedrockFoundationModels offer, JSON or .gz) instead of fetching "
                 "models.dev. No network; writes ~/.overcode/cache/claude_prices.json"),
    ] = None,
):
    """Fetch models.dev and rewrite the local cache (~/.overcode/cache/model_metadata.json),
    or, with --prices FILE, rebuild the Claude price table offline.

    This is the on-demand path; nothing is fetched unattended unless
    config.yaml sets model_metadata.auto_refresh: true.
    """
    if prices is not None:
        _refresh_prices(prices, check=check)
        return
    from ..model_metadata import fetch_models_dev, refresh_local_cache, snapshot_info

    before = snapshot_info()
    rprint(f"[dim]before: {before['tier']} catalog, {before['model_count']} models, "
           f"fetched {before['fetched_at']}[/dim]")
    if check:
        return
    try:
        info = refresh_local_cache(fetch=lambda: _bounded(
            lambda: fetch_models_dev(timeout=CLI_FETCH_TIMEOUT_SECONDS),
            CLI_FETCH_DEADLINE_SECONDS))
    except Exception as e:
        if _is_network_error(e):
            rprint(f"[red]Refresh failed:[/red] cannot reach models.dev "
                   f"({escape(str(e) or type(e).__name__)}); offline or blocked?")
        else:
            rprint(f"[red]Refresh failed:[/red] {escape(str(e))}")
        rprint("[dim]Lookups keep using the previous catalog.[/dim]")
        rprint(f"[dim]{escape(OFFLINE_HINT)}[/dim]")
        raise typer.Exit(code=1)
    rprint(f"[green]✓[/green] {info['model_count']} models from {info['source']} "
           f"(fetched {info['fetched_at']}) -> {info['path']}")


def _bounded(fetch, seconds: float):
    """``fetch()`` on a daemon thread, abandoned after ``seconds`` (TimeoutError):
    urlopen's timeout does not cover DNS resolution."""
    import threading

    box: dict = {}

    def run():
        try:
            box["value"] = fetch()
        except BaseException as e:  # noqa: BLE001 — re-raised on the caller's thread
            box["error"] = e

    worker = threading.Thread(target=run, name="models-refresh-fetch", daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        raise TimeoutError(f"no answer in {seconds:g}s")
    if "error" in box:
        raise box["error"]
    return box["value"]


def _is_network_error(error: BaseException) -> bool:
    # urllib.error.URLError, socket errors and TimeoutError are all OSError
    return isinstance(error, OSError)


def _refresh_prices(path: Path, *, check: bool) -> None:
    from ..price_table import PriceListError, refresh_local_override

    try:
        result = refresh_local_override(path, write=not check)
    except PriceListError as e:
        rprint(f"[red]Price table not rebuilt:[/red] {escape(str(e))}")
        raise typer.Exit(code=1)
    doc = result["doc"]
    rprint(f"[dim]{escape(path.name)}: {len(doc['rows'])} Claude rows, "
           f"published {doc['as_of']}[/dim]")
    for line in result["diff"]:
        rprint(f"  {escape(line)}")
    if not result["diff"]:
        rprint("  [dim]no price changes against the table in use[/dim]")
    if not result["newer"]:
        rprint(f"[yellow]⚠[/yellow] the price table shipped with this overcode is as of "
               f"{result['vendored_as_of']}, as new as this file or newer: nothing written "
               f"(an older override would be ignored)")
        raise typer.Exit(code=1)
    if check:
        rprint(f"[dim]--check: nothing written (would write {result['path']})[/dim]")
        return
    rprint(f"[green]✓[/green] price table as of {doc['as_of']} -> {result['path']} "
           f"(used ahead of the shipped table, as of {result['vendored_as_of']}; "
           f"running daemons pick it up on their next price lookup)")
