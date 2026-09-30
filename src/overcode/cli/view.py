"""
View commands: change what the running TUI shows (#484).

Each command is queued for the TUI, applied through the same code as its
own keys and dialogs, and acknowledged; the result or the error is printed.
`--json` prints the raw ack for agents.

    overcode view state
    overcode view columns list [--level med]
    overcode view columns show|hide|reset <column>... [--level med]
    overcode view sort <column|tree> [--desc|--asc]
    overcode view detail low|med|high|full
    overcode view filter <tag> | --clear
    overcode view focus <agent>
    overcode view toggle <palette action>
    overcode view notify "<text>"
    overcode view point <palette action>
"""

import json
from typing import Annotated, List, Optional

import typer

from ._shared import view_app

SessionOpt = Annotated[str, typer.Option(
    "--session", hidden=True, envvar="OVERCODE_TMUX_SESSION",
    help="Tmux session (default: the one this agent runs in, else 'agents')")]
JsonOpt = Annotated[bool, typer.Option("--json", help="Print the raw ack as JSON")]


def _send(session: str, verb: str, args: dict, as_json: bool) -> dict:
    from ..view_control import read_view_state, send_command, tui_is_live
    if not tui_is_live(read_view_state(session)):
        msg = f"No TUI is running for '{session}' (start one with `overcode` or `overcode tmux`)."
        if as_json:
            typer.echo(json.dumps({"ok": False, "error": msg}))
        else:
            typer.echo(msg, err=True)
        raise typer.Exit(code=2)
    ack = send_command(session, verb, args)
    if as_json:
        typer.echo(json.dumps(ack, indent=2, ensure_ascii=False))
    elif not ack.get("ok"):
        typer.echo(f"✗ {ack.get('error', 'failed')}", err=True)
    if not ack.get("ok"):
        raise typer.Exit(code=1)
    return ack.get("result") or {}


@view_app.command("state")
def view_state(session: SessionOpt = "agents"):
    """What the TUI is showing now, as JSON: focused agent, level, columns, sort, filters, dialog."""
    from ..view_control import read_view_state, tui_is_live
    state = read_view_state(session)
    if state is None:
        typer.echo(json.dumps({"running": False}))
        raise typer.Exit(code=2)
    typer.echo(json.dumps({"running": tui_is_live(state), **state}, indent=2, ensure_ascii=False))


@view_app.command("columns")
def view_columns(
    op: Annotated[str, typer.Argument(help="list, show, hide or reset")],
    columns: Annotated[Optional[List[str]], typer.Argument(help="Column ids, names or headers")] = None,
    level: Annotated[Optional[str], typer.Option("--level", help="low, med, high or full (default: current)")] = None,
    session: SessionOpt = "agents",
    as_json: JsonOpt = False,
):
    """List columns, or show / hide / reset them at a detail level."""
    if op == "list":
        _columns_list(session, level, as_json)
        return
    result = _send(session, "columns", {"op": op, "ids": columns or [], "level": level}, as_json)
    if not as_json:
        changed = ", ".join(result.get("changed", [])) or "all overrides"
        typer.echo(f"✓ {op} {changed} at {result.get('level')}: {len(result.get('visible', []))} columns visible")


def _columns_list(session: str, level: Optional[str], as_json: bool) -> None:
    """Every column with its description and whether it shows, from the TUI's state or prefs."""
    from ..settings import TUIPreferences
    from ..summary_columns import SUMMARY_COLUMNS, resolve_column_visible
    from ..view_control import read_view_state, tui_is_live

    state = read_view_state(session)
    live = tui_is_live(state)
    lvl = level or (state or {}).get("level") or TUIPreferences.load(session).summary_detail or "low"
    if live and lvl == state.get("level"):
        visible = set(state.get("visible_columns", []))
        uniform = state.get("uniform_columns", {})
    else:
        overrides = TUIPreferences.load(session).column_config.get(lvl, {})
        visible = {c.id for c in SUMMARY_COLUMNS if resolve_column_visible(c, lvl, overrides)}
        uniform = {}
    rows = [{
        "id": c.id, "name": c.name, "header": c.header.strip(), "group": c.group,
        "description": c.description, "default_levels": sorted(c.detail_levels),
        "visible": c.id in visible,
        "hidden_because_uniform": c.id in uniform and c.id not in visible,
        "sortable": c.sort_key is not None,
    } for c in SUMMARY_COLUMNS if not c.cli_only]
    if as_json:
        typer.echo(json.dumps({"level": lvl, "tui_running": live, "columns": rows}, indent=2))
        return
    typer.echo(f"Columns at level '{lvl}' ({'from the running TUI' if live else 'from saved prefs'}):")
    for r in rows:
        mark = "●" if r["visible"] else ("◌" if r["hidden_because_uniform"] else "·")
        typer.echo(f"  {mark} {r['id']:<20} {r['header']:<6} {r['name']:<22} {r['description'][:70]}")
    typer.echo("  ● shown  ◌ hidden: same value on every row  · hidden")


@view_app.command("sort")
def view_sort(
    column: Annotated[str, typer.Argument(help="Column id/name, or 'tree'")],
    desc: Annotated[Optional[bool], typer.Option("--desc/--asc", help="Direction (default: the column's own)")] = None,
    session: SessionOpt = "agents",
    as_json: JsonOpt = False,
):
    """Sort the agent list by a column, or tree order."""
    r = _send(session, "sort", {"column": column, "descending": desc}, as_json)
    if not as_json:
        typer.echo(f"✓ sort {r.get('mode')} {'▼' if r.get('descending') else '▲'}")


@view_app.command("detail")
def view_detail(
    level: Annotated[str, typer.Argument(help="low, med, high or full")],
    session: SessionOpt = "agents",
    as_json: JsonOpt = False,
):
    """Set the rows' detail level (the s key)."""
    r = _send(session, "detail", {"level": level}, as_json)
    if not as_json:
        typer.echo(f"✓ detail {r.get('level')}")


@view_app.command("filter")
def view_filter(
    tag: Annotated[Optional[str], typer.Argument(help="Show only agents with this tag")] = None,
    clear: Annotated[bool, typer.Option("--clear", help="Show every agent again")] = False,
    session: SessionOpt = "agents",
    as_json: JsonOpt = False,
):
    """Filter the list by tag, or clear the filter."""
    if not tag and not clear:
        raise typer.BadParameter("give a tag, or --clear")
    r = _send(session, "filter", {"tag": None if clear else tag}, as_json)
    if not as_json:
        typer.echo(f"✓ filter {r.get('tag') or 'cleared'}")


@view_app.command("focus")
def view_focus(
    agent: Annotated[str, typer.Argument(help="Agent name or id")],
    session: SessionOpt = "agents",
    as_json: JsonOpt = False,
):
    """Move the TUI's focus to an agent."""
    r = _send(session, "focus", {"agent": agent}, as_json)
    if not as_json:
        typer.echo(f"✓ focused {r.get('agent')}")


@view_app.command("toggle")
def view_toggle(
    action: Annotated[str, typer.Argument(help="A view palette action id, e.g. toggle_timeline")],
    session: SessionOpt = "agents",
    as_json: JsonOpt = False,
):
    """Run a view palette action by its id (see `overcode view actions`).

    Only actions that change the view or open a dialog: acting on agents,
    sending them keys, daemons and quit are refused (#502).
    """
    r = _send(session, "toggle", {"action": action}, as_json)
    if not as_json:
        state = f": {r['state']}" if r.get("state") else ""
        typer.echo(f"✓ {r.get('title')}{state}")


@view_app.command("actions")
def view_actions(as_json: JsonOpt = False):
    """Every palette action id, with its title, category and keys."""
    from ..command_palette import COMMANDS
    from ..keymap import effective_keymap
    from ..view_control import toggle_refusal
    keys = effective_keymap().keys_by_action()
    rows = [{"action": c.action, "title": c.title, "category": c.category,
             "keys": list(keys.get(c.action, [])),
             "acts_on_focused_agent": c.agent, "toggle": c.state is not None,
             "view_toggle": toggle_refusal(c.category) is None}
            for c in COMMANDS]
    if as_json:
        typer.echo(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    for r in rows:
        typer.echo(f"  {r['action']:<32} {' '.join(r['keys']):<8} {r['category']:<14} {r['title']}")


@view_app.command("notify")
def view_notify(
    text: Annotated[str, typer.Argument(help="Message to show in the TUI")],
    severity: Annotated[str, typer.Option("--severity", help="information, warning or error")] = "information",
    session: SessionOpt = "agents",
    as_json: JsonOpt = False,
):
    """Show a message in the TUI."""
    _send(session, "notify", {"text": text, "severity": severity}, as_json)
    if not as_json:
        typer.echo("✓ shown")


@view_app.command("point")
def view_point(
    action: Annotated[str, typer.Argument(help="A palette action id")],
    session: SessionOpt = "agents",
    as_json: JsonOpt = False,
):
    """Show the user how to reach an action: its key, or how to find it in the palette."""
    r = _send(session, "point", {"action": action}, as_json)
    if not as_json:
        keys = ", ".join(r.get("keys", [])) or "no key; palette only"
        typer.echo(f"✓ pointed at {action} ({keys})")


from ._shared import app  # noqa: E402


@app.command("docs")
def docs(
    what: Annotated[str, typer.Argument(
        help="'path': where the docs are; 'code': where overcode's own source is")] = "path",
):
    """Where overcode's docs are (they ship with it), or its installed source code."""
    from ..overagent import code_location, docs_location
    if what == "path":
        typer.echo(docs_location())
    elif what == "code":
        typer.echo(code_location())
    else:
        raise typer.BadParameter("'path' or 'code'")
