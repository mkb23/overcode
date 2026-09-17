"""
Model metadata commands: info, lookup, refresh (#473).

The catalog behind the CTX% and $ columns — see docs/configuration.md,
"Model metadata (context windows and pricing)".
"""

from typing import Annotated

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


@models_app.command("lookup")
def models_lookup(
    model: Annotated[str, typer.Argument(help="Model id as a backend reports it, e.g. zai/glm-4.6")],
):
    """Show what overcode resolves for a model id, and which tier answered."""
    from ..history_reader import (
        MODEL_CONTEXT_WINDOWS,
        _bare_model_id,
        model_context_window,
        model_short_name,
    )
    from ..model_metadata import lookup, snapshot_info
    from ..pricing import MODEL_PRICING, lookup_pricing

    bare = _bare_model_id(model)
    meta = lookup(model)
    window = model_context_window(model)
    pricing = lookup_pricing(model)

    if bare in MODEL_CONTEXT_WINDOWS:
        window_tier = "curated"
    elif meta and meta.context_window:
        window_tier = f"catalog ({snapshot_info()['tier']})"
    else:
        window_tier = "unknown"
    if any(key in model.lower() for key in MODEL_PRICING):
        price_tier = "curated"
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
    else:
        rprint(f"  list price:     —  {escape(f'[{price_tier}]')}")
    if meta:
        rprint(f"  catalog entry:  {meta.name} via {meta.provider}"
               f"{' (open weights)' if meta.open_weights else ''}")
    if window is None and meta is None:
        rprint("[dim]  Not in the curated tables or the catalog. Try `overcode models refresh`, "
               "or see docs/design/model-alias-resolution.md.[/dim]")


@models_app.command("refresh")
def models_refresh(
    check: Annotated[
        bool, typer.Option("--check", help="Only report the active catalog's age; fetch nothing")
    ] = False,
):
    """Fetch models.dev and rewrite the local cache (~/.overcode/cache/model_metadata.json).

    This is the on-demand path; nothing is fetched unattended unless
    config.yaml sets model_metadata.auto_refresh: true.
    """
    from ..model_metadata import refresh_local_cache, snapshot_info

    before = snapshot_info()
    rprint(f"[dim]before: {before['tier']} catalog, {before['model_count']} models, "
           f"fetched {before['fetched_at']}[/dim]")
    if check:
        return
    try:
        info = refresh_local_cache()
    except Exception as e:
        rprint(f"[red]Refresh failed:[/red] {e}")
        rprint("[dim]Lookups keep using the previous catalog.[/dim]")
        raise typer.Exit(code=1)
    rprint(f"[green]✓[/green] {info['model_count']} models from {info['source']} "
           f"(fetched {info['fetched_at']}) -> {info['path']}")
