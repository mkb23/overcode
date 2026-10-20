"""Identity and sisters columns: status, time in state, status detail, name, host."""

import time
import unicodedata
from datetime import datetime
from typing import Optional

from ..clock import from_iso, seconds_since, to_epoch
from ..status_constants import BADGE_KINDS, StatusDetail
from ..tui_helpers import format_duration
from .column import _make_simple_render
from .context import ColumnContext, ColumnOutput


def render_status_symbol(ctx: ColumnContext) -> ColumnOutput:
    # Status emojis are 2 cells wide; an ASCII fallback may be narrower.
    # Pad narrow symbols with an extra space so columns stay aligned.
    symbol = ctx.status_symbol
    cell_width = sum(
        2 if unicodedata.east_asian_width(c) in ('W', 'F') else (0 if c == '\ufe0f' else 1)
        for c in symbol
    )
    pad = " " * (2 - cell_width) if cell_width < 2 else ""
    return [(f"{symbol}{pad} ", ctx.status_color)]


def render_unvisited_alert(ctx: ColumnContext) -> ColumnOutput:
    if ctx.is_unvisited_stalled:
        return [(ctx.e("🔔"), f"bold blink red{ctx.bg}")]
    else:
        return [("  ", f"dim{ctx.bg}")]


def render_time_in_state(ctx: ColumnContext) -> ColumnOutput:
    state_start = ctx.status_changed_at
    if state_start is None and ctx.stats.state_since:
        try:
            state_start = from_iso(ctx.stats.state_since)
        except (ValueError, TypeError):
            pass
    # Epoch arithmetic, so a state spanning a DST change reads true
    elapsed = seconds_since(state_start, datetime.now().timestamp()) if state_start else None
    if elapsed is not None:
        return [(f"{format_duration(elapsed):>5} ", ctx.status_color)]
    else:
        return [("    - ", f"dim{ctx.bg}")]


# Total display-cell budget for the column. Matches the legacy sleep-countdown
# layout so headers and other columns line up.
_STATUS_DETAIL_WIDTH = 9


def _display_width(text: str) -> int:
    """Terminal cell width, accounting for wide chars (emoji = 2)."""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def _badge_glyph(kind: str, emoji_free: bool) -> str:
    """Resolve a badge kind to its emoji (or ASCII fallback)."""
    spec = BADGE_KINDS.get(kind)
    if spec is None:
        return "?"
    emoji, _, ascii_fb = spec
    return ascii_fb if emoji_free else emoji


def _render_status_detail(ctx: "ColumnContext", detail: StatusDetail) -> ColumnOutput:
    """Render emoji-stacked badges for the column-2 status detail (#TBD).

    Width budget is _STATUS_DETAIL_WIDTH cells. Suffix priority per badge:
    ETA > count > label > nothing. When only one badge fits, it gets the
    full remaining width; multi-badge layouts drop labels to keep packing
    tight. Overflow is signalled with a trailing '+'.
    """
    # The badges take their light's colour in the active status theme
    from ..status_theme import BUCKET_LIGHTS, active_theme
    light = BUCKET_LIGHTS.get(detail.color)
    color = active_theme().colour(light) if light else "yellow"
    style = f"{color}{ctx.bg}"

    badges = detail.badges
    if not badges:
        return None

    # Reserve one leading space; the remainder is the badge budget.
    budget = _STATUS_DETAIL_WIDTH - 1
    parts: list[str] = []
    n = len(badges)
    for i, b in enumerate(badges):
        glyph = _badge_glyph(b.kind, ctx.emoji_free)
        glyph_w = _display_width(glyph)

        # Pick a suffix in priority order.
        suffix = ""
        if b.eta_seconds is not None and b.eta_seconds > 0:
            suffix = format_duration(int(b.eta_seconds))
        elif b.count > 1:
            suffix = f"×{b.count}"
        elif b.label and n == 1:
            # Single-badge case — let the label fill the remaining budget.
            suffix = b.label

        chunk_w = glyph_w + _display_width(suffix)
        remaining_badges = n - i - 1
        # Always reserve a cell for '+' if more badges won't fit.
        reserve = 1 if remaining_badges > 0 else 0
        if chunk_w > budget - reserve:
            # Try the glyph alone (drop the suffix).
            if glyph_w <= budget - reserve:
                parts.append(glyph)
                budget -= glyph_w
                if remaining_badges > 0 and budget >= 1:
                    parts.append("+")
                    budget -= 1
                break
            if remaining_badges > 0 and budget >= 1:
                parts.append("+")
                budget -= 1
            break
        parts.append(glyph + suffix)
        budget -= chunk_w

    body = "".join(parts)
    # Pad to the full width so columns to the right stay aligned.
    pad_cells = _STATUS_DETAIL_WIDTH - 1 - _display_width(body)
    text = " " + body + (" " * max(0, pad_cells))
    return [(text, style)]


def render_sleep_countdown(ctx: ColumnContext) -> ColumnOutput:
    """Column 2 of the two-column status model (#TBD).

    Renders emoji-stacked status badges from the hook reducer when
    ctx.status_detail is populated. Falls back to the legacy sleep-wake
    countdown (#289) when not — so callers that haven't been wired up to
    the new detector output still get the original behavior.

    Function name is kept (rather than renamed to render_status_detail) for
    backward compatibility with the column id `sleep_countdown` and the
    existing test suite.
    """
    if ctx.status_detail is not None and ctx.status_detail.badges:
        rendered = _render_status_detail(ctx, ctx.status_detail)
        if rendered is not None:
            return rendered

    # Legacy fallback (#289)
    if ctx.sleep_wake_estimate is not None:
        # Epoch arithmetic: the estimate may be aware (the engine's time base)
        remaining = max(0.0, (to_epoch(ctx.sleep_wake_estimate) or 0.0) - time.time())
        return [(f" {ctx.e('⏰')}{format_duration(remaining):>5} ", f"yellow{ctx.bg}")]
    return None


def render_kind(ctx: ColumnContext) -> ColumnOutput:
    """KIND (full detail): ``agent``, ``child`` or ``bg``, the same width, then a gap."""
    return [(f"{ctx.agent_kind:<5} ", f"dim{ctx.bg}")]


def render_agent_type(ctx: ColumnContext) -> ColumnOutput:
    """TYPE on an agent row: its own kind (dim), only while the expanded
    delegated rows show their types (``FleetFlags.swarm_types_shown``)."""
    if not ctx.fleet.swarm_types_shown:
        return None
    return [(f" {ctx.agent_kind}", f"dim{ctx.bg}")]


def render_expand_icon(ctx: ColumnContext) -> ColumnOutput:
    if ctx.has_focus:
        return [("→ ", ctx.status_color)]
    else:
        return [("  ", ctx.status_color)]


_render_plain_agent_name = _make_simple_render("display_name", colored_style="bold cyan")
# The overagent's name stands out from every agent's (#484): its own colour.
_render_overagent_name = _make_simple_render("display_name", colored_style="bold #ff87ff")
# A name overcode made up and nobody chose (name_source "default", #542): dim
_render_default_agent_name = _make_simple_render("display_name", colored_style="dim cyan")

def render_agent_name(ctx: ColumnContext) -> ColumnOutput:
    if getattr(ctx.session, "backend", None) == "overagent":
        return _render_overagent_name(ctx)
    if getattr(ctx.session, "name_source", None) == "default":
        return _render_default_agent_name(ctx)
    return _render_plain_agent_name(ctx)


def render_status_plain(ctx: ColumnContext) -> Optional[str]:
    """Status with time-in-state."""
    # The status itself: done and waiting_oversight share one symbol (⚪)
    status = ctx.status or getattr(ctx.stats, "current_state", "") or "unknown"

    state_start = ctx.status_changed_at
    if state_start is None and ctx.stats.state_since:
        try:
            state_start = from_iso(ctx.stats.state_since)
        except (ValueError, TypeError):
            pass

    time_str = ""
    elapsed = seconds_since(state_start, datetime.now().timestamp()) if state_start else None
    if elapsed is not None:
        time_str = f" ({format_duration(elapsed)})"

    return f"{ctx.status_symbol} {status}{time_str}"


def render_host(ctx: ColumnContext) -> ColumnOutput:
    """Render host column — hidden when no sisters are configured."""
    if not ctx.has_sisters:
        return None
    host = ctx.source_host or ctx.local_hostname
    width = 14
    label = " " + host[:width].ljust(width)
    if ctx.is_remote:
        return [(label, f"bold magenta{ctx.bg}")]
    else:
        return [(label, f"dim{ctx.bg}")]


def render_host_plain(ctx: ColumnContext) -> Optional[str]:
    if not ctx.has_sisters:
        return None
    return ctx.source_host or ctx.local_hostname
