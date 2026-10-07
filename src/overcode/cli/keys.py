"""
`overcode keys` — the TUI's keymap and where each key comes from (#510).

    overcode keys                    effective keys, grouped, with their source
    overcode keys --preset vscode    what a preset would give you
    overcode keys --conflicts        only the warnings
    overcode keys --use vscode       switch preset (writes keys.preset)
    overcode keys --presets          list the shipped presets
"""

import json
from typing import Annotated, Optional

import typer
from rich import print as rprint
from rich.markup import escape

from ._shared import app


def _source_markup(source: str) -> str:
    if source == "default":
        return "[dim]default[/dim]"
    if source == "override":
        return "[bold yellow]override[/bold yellow]"
    return f"[cyan]{escape(source)}[/cyan]"


def _rows(km, scope: str, show_unbound: bool):
    """(group, action, keys, title, source) rows for one scope."""
    from ..command_palette import COMMANDS, CATEGORIES, key_label
    rows = []
    if scope == "app":
        seen = set()
        order = {c: i for i, c in enumerate(CATEGORIES)}
        for cmd in COMMANDS:
            keys = km.keys_for(cmd.action)
            seen.add(cmd.action)
            if not keys and not show_unbound:
                continue
            rows.append((cmd.category, cmd.action, [key_label(k) for k in keys], cmd.title,
                         km.source_of(cmd.action) or "unbound"))
        for b in km.bindings("app"):
            if b.action in seen:
                continue
            seen.add(b.action)
            rows.append(("App", b.action, km.labels_for(b.action), b.description, b.source))
        rows.sort(key=lambda r: order.get(r[0], len(order)))
        return rows
    seen = set()
    for b in km.bindings(scope):
        if b.action in seen:
            continue
        seen.add(b.action)
        rows.append((scope, b.action, km.labels_for(b.action, scope), b.description, b.source))
    return rows


@app.command("keys")
def keys(
    preset: Annotated[Optional[str], typer.Option(
        "--preset", help="Show this preset (plus your overrides) instead of the configured one")] = None,
    conflicts: Annotated[bool, typer.Option(
        "--conflicts", help="Only list warnings: unknown actions, duplicate keys, "
                            "swallowed keys, shadowed passthru keys")] = False,
    use: Annotated[Optional[str], typer.Option(
        "--use", help="Switch the TUI to this preset (writes keys.preset in config.yaml)")] = None,
    presets: Annotated[bool, typer.Option("--presets", help="List the shipped presets")] = False,
    scope: Annotated[Optional[str], typer.Option(
        "--scope", help="Only this scope: app, command_bar, command_palette, "
                        "summary_prompt_lab")] = None,
    show_all: Annotated[bool, typer.Option(
        "--all", help="Also list palette commands that have no key")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output")] = False,
):
    """Show the TUI's keys and where each comes from (default, preset or override).

    Keys are configured under `keys:` in ~/.overcode/config.yaml:

    \b
      keys:
        preset: vscode
        overrides:
          jump_to_agent: [ctrl+j, J]
          toggle_monochrome: null
        scopes:
          command_palette:
            cursor_down: [down, ctrl+n]

    A running TUI picks up --use at its next start; the palette's
    "Key preset" command switches live.
    """
    from ..keymap import (
        SCOPES, configured_preset_name, effective_keymap, list_presets, load_preset,
        set_configured_preset,
    )

    if presets:
        current = configured_preset_name()
        for name in list_presets():
            p = load_preset(name)
            mark = "*" if name == current else " "
            typer.echo(f"{mark} {name:<10} {p.description}")
        return

    if use is not None:
        if use not in list_presets():
            rprint(f"[red]Unknown preset '{escape(use)}'.[/red] Known: {', '.join(list_presets())}")
            raise typer.Exit(1)
        set_configured_preset(use)
        km = effective_keymap(preset=use)
        changed = [b for s in SCOPES for b in km.bindings(s) if b.source == use]
        rprint(f"[green]✓[/green] key preset: [bold]{escape(use)}[/bold]"
               + (f" ({len(changed)} keys differ from default)" if changed else ""))
        rprint("[dim]Restart the TUI to pick it up, or use the palette's 'Key preset' command.[/dim]")
        if km.tmux_toggle_key:
            from ..config import get_tmux_toggle_key
            current_toggle = get_tmux_toggle_key()
            p = load_preset(use)
            if current_toggle in p.tmux_toggle_avoid:
                rprint(f"[yellow]⚠[/yellow] your tmux toggle key {escape(current_toggle)} does not "
                       f"work well here — try {escape(km.tmux_toggle_key)} (`overcode tmux` → "
                       "toggle key). Not changed automatically.")
        for w in km.warnings:
            rprint(f"[yellow]⚠[/yellow] {escape(w)}")
        return

    if preset is not None and preset not in list_presets():
        rprint(f"[red]Unknown preset '{escape(preset)}'.[/red] Known: {', '.join(list_presets())}")
        raise typer.Exit(1)
    if scope is not None and scope not in SCOPES:
        rprint(f"[red]Unknown scope '{escape(scope)}'.[/red] Known: {', '.join(SCOPES)}")
        raise typer.Exit(1)

    km = effective_keymap(preset=preset)

    if conflicts:
        if as_json:
            typer.echo(json.dumps({"preset": km.preset, "warnings": km.warnings}, indent=2))
            return
        if not km.warnings:
            rprint(f"[green]✓[/green] no key conflicts (preset: {escape(km.preset)})")
            return
        for w in km.warnings:
            rprint(f"[yellow]⚠[/yellow] {escape(w)}")
        return

    scopes = [scope] if scope else list(SCOPES)
    if as_json:
        out = {"preset": km.preset, "warnings": km.warnings, "scopes": {}}
        for s in scopes:
            out["scopes"][s] = [
                {"group": g, "action": a, "keys": k, "title": t, "source": src}
                for g, a, k, t, src in _rows(km, s, show_all)
            ]
        typer.echo(json.dumps(out, indent=2, ensure_ascii=False))
        return

    from rich.console import Console
    console = Console()
    rprint(f"[bold]Key preset:[/bold] {escape(km.preset)}")
    rprint("[dim]source: default = in code · preset name · override = your config[/dim]")
    for s in scopes:
        rows = _rows(km, s, show_all)
        if not rows:
            continue
        rprint(f"\n[bold]{escape(SCOPES[s])}[/bold]  [dim]({s})[/dim]")
        group = None
        for g, action, labels, title, source in rows:
            if s == "app" and g != group:
                group = g
                rprint(f"  [bold dim]{escape(g)}[/bold dim]")
            keys_txt = " ".join(labels) or "—"
            console.print(f"    [bold]{escape(keys_txt):<10}[/bold] {escape(title):<30} "
                          f"[dim]{escape(action):<26}[/dim] {_source_markup(source)}",
                          soft_wrap=True)
    if km.warnings:
        rprint(f"\n[yellow]⚠[/yellow] {len(km.warnings)} warning(s) — overcode keys --conflicts")
