"""Time columns: uptime, running, stalled, asleep, active %."""

from typing import Optional

from ..tui_helpers import format_duration
from .column import _make_simple_render, _make_simple_render_plain
from .context import ColumnContext, ColumnOutput


render_uptime = _make_simple_render("uptime", format_str=" ↑{v:>5}", colored_style="bold white")


render_running_time = _make_simple_render("green_time", format_duration, " ▶{v:>5}", "bold green")


render_stalled_time = _make_simple_render("non_green_time", format_duration, " ⏸{v:>5}", "bold red")


def render_sleep_time(ctx: ColumnContext) -> ColumnOutput:
    sleep_str = format_duration(ctx.sleep_time) if ctx.sleep_time > 0 else "-"
    sleep_col = f" 💤{sleep_str:>5}"
    style = f"bold cyan{ctx.bg}" if ctx.sleep_time > 0 else f"dim cyan{ctx.bg}"
    return [(sleep_col, style)]


def render_active_pct(ctx: ColumnContext) -> ColumnOutput:
    active_time = ctx.green_time + ctx.non_green_time
    pct = (ctx.green_time / active_time * 100) if active_time > 0 else 0
    return [(f" {pct:>3.0f}%", f"bold green{ctx.bg}" if pct >= 50 else f"bold red{ctx.bg}")]


render_uptime_plain = _make_simple_render_plain("uptime", template="↑{v}")


def render_time_plain(ctx: ColumnContext) -> Optional[str]:
    """Combined time line: active, stalled, sleep, percent."""
    active_time = ctx.green_time + ctx.non_green_time
    pct = (ctx.green_time / active_time * 100) if active_time > 0 else 0
    sleep_str = f"  💤 {format_duration(ctx.sleep_time)}" if ctx.sleep_time > 0 else ""
    return (
        f"▶ {format_duration(ctx.green_time)} active  "
        f"⏸ {format_duration(ctx.non_green_time)} stalled"
        f"{sleep_str}  ({pct:.0f}%)"
    )
