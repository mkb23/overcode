"""
Activity commands: what the usage log recorded (#483).

    overcode activity summary [--since 7d] [--json]
    overcode activity keys    [--since 1h] [--limit 200]
    overcode activity path
"""

import json
from typing import Annotated, Optional

import typer

from ._shared import activity_app


def _tui_keymaps() -> tuple[frozenset[str], dict[str, list[str]]]:
    """The TUI's bound keys, and keys by action, straight from its BINDINGS."""
    from ..tui import SupervisorTUI
    keys_by_action: dict[str, list[str]] = {}
    for binding in SupervisorTUI.BINDINGS:
        key, action = binding[0], binding[1]
        keys_by_action.setdefault(action, []).append(key)
    bound = frozenset(k for keys in keys_by_action.values() for k in keys)
    return bound, keys_by_action


def _window(since: Optional[str]) -> Optional[float]:
    from ..usage_analytics import since_ms
    if not since or since == "all":
        return None
    try:
        return since_ms(since)
    except ValueError as e:
        raise typer.BadParameter(str(e))


@activity_app.command("summary")
def activity_summary(
    since: Annotated[str, typer.Option("--since", help="Window: 7d, 24h, 30m, or 'all'")] = "7d",
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output")] = False,
):
    """How you use overcode: most-used actions and how you reach them, plus experimental signals."""
    from ..activity_log import iter_records
    from ..usage_analytics import render_summary, summarize

    start = _window(since)
    bound, keys_by_action = _tui_keymaps()
    s = summarize(iter_records(since_ms=start), bound, keys_by_action, since=start)
    if as_json:
        typer.echo(json.dumps(s.to_dict(), indent=2))
    else:
        typer.echo(render_summary(s))


@activity_app.command("keys")
def activity_keys(
    since: Annotated[str, typer.Option("--since", help="Window: 1h, 30m, 7d, or 'all'")] = "1h",
    limit: Annotated[int, typer.Option("--limit", help="Most recent N records")] = 200,
    kinds: Annotated[Optional[str], typer.Option("--kinds", help="Comma list, e.g. key,action")] = None,
):
    """The raw records, newest last, as JSON lines."""
    from collections import deque
    from ..activity_log import iter_records

    wanted = set(kinds.split(",")) if kinds else None
    tail: deque = deque(maxlen=max(1, limit))
    for rec in iter_records(since_ms=_window(since)):
        if wanted is None or rec.get("kind") in wanted:
            tail.append(rec)
    for rec in tail:
        typer.echo(json.dumps(rec, ensure_ascii=False))


@activity_app.command("path")
def activity_path():
    """Where the usage log lives."""
    from ..activity_log import get_activity_dir, recording_enabled
    typer.echo(str(get_activity_dir()))
    if not recording_enabled():
        typer.echo("(recording is off)", err=True)


from ._shared import app  # noqa: E402


@app.command("journey")
def journey(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output")] = False,
):
    """Your learning journey: what you've found in overcode and what's next (#483)."""
    from ..journey import load_journey, render_journey
    bound, keys_by_action = _tui_keymaps()
    j = load_journey(keys_by_action, bound)
    if as_json:
        typer.echo(json.dumps(j.to_dict(), indent=2, ensure_ascii=False))
    else:
        typer.echo(render_journey(j))


@activity_app.command("stream")
def activity_stream(
    detail: Annotated[str, typer.Option("--detail", help="rollup | significant | verbose")] = "rollup",
    interval: Annotated[str, typer.Option("--rollup-interval", help="Digest every… (15m, 1h)")] = "15m",
):
    """Follow the usage log live, as JSON lines (for an agent's Monitor tool).

    rollup (default) prints one digest per interval, and nothing while idle.
    significant adds actions, dialogs cancelled, nudges and CLI calls as they
    happen; verbose adds every key and click.
    """
    import time as _time
    from ..activity_log import activity_path_for, get_activity_dir, parse_record
    from ..usage_analytics import digest_line, is_significant, parse_since

    if detail not in ("rollup", "significant", "verbose"):
        raise typer.BadParameter("detail is rollup, significant or verbose")
    try:
        every = parse_since(interval)
    except ValueError as e:
        raise typer.BadParameter(str(e))

    def emit(obj: dict) -> None:
        typer.echo(json.dumps(obj, ensure_ascii=False))
        import sys
        sys.stdout.flush()

    bound, _ = _tui_keymaps()
    emit({"type": "start", "at": int(_time.time() * 1000), "detail": detail,
          "description": f"Watching {get_activity_dir()} ({detail}, digest every {interval})"})
    path = activity_path_for(_time.time() * 1000)
    offset = path.stat().st_size if path.exists() else 0  # from now on, no history
    window: list = []
    next_digest = _time.monotonic() + every
    try:
        while True:
            current = activity_path_for(_time.time() * 1000)
            if current != path:  # a new month
                path, offset = current, 0
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            if size < offset:
                offset = 0
            if size > offset:
                with open(path, "rb") as f:
                    f.seek(offset)
                    data = f.read()
                end = data.rfind(b"\n") + 1
                offset += end
                for line in data[:end].splitlines():
                    rec = parse_record(line)
                    if rec is None:
                        continue
                    window.append(rec)
                    if detail == "verbose" or (detail == "significant" and is_significant(rec)):
                        emit({"type": "event", **rec})
            if _time.monotonic() >= next_digest:
                if window:
                    emit(digest_line(window, every, bound))
                window = []
                next_digest = _time.monotonic() + every
            _time.sleep(1.0)
    except KeyboardInterrupt:
        pass
