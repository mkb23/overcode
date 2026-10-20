"""LLM usage columns: tokens, energy, cost, budget, subtree cost, burn rate."""

from typing import Optional

from ..energy import format_watts
from ..tui_helpers import format_cost, format_joules, format_tokens
from .context import ColumnContext, ColumnOutput


def trip_mark(ctx: ColumnContext) -> str:
    """The cell's lead: ↺ on a reset trip counter (trip.py), else the usual space.

    One cell either way, so a reset moves nothing.
    """
    return ctx.e("↺") if ctx.trip else " "


def shown_tokens(ctx: ColumnContext) -> int:
    """TOK as the row shows it: since the trip reset, else the total."""
    from ..trip import trip_tokens
    return trip_tokens(ctx.trip) if ctx.trip else ctx.claude_stats.total_tokens


def shown_cost(ctx: ColumnContext) -> float:
    """$ as the row shows it: since the trip reset, else the total."""
    if ctx.trip:
        return ctx.trip.get("estimated_cost_usd") or 0.0
    return ctx.session.stats.estimated_cost_usd


def shown_energy(ctx: ColumnContext) -> float:
    """ENRG as the row shows it: since the trip reset, else the total."""
    if ctx.trip:
        return ctx.trip.get("estimated_energy_j") or 0.0
    return ctx.session.stats.estimated_energy_j


def render_token_count(ctx: ColumnContext) -> ColumnOutput:
    """Token count (Σ123K)."""
    if ctx.claude_stats is not None:
        return [(f"{trip_mark(ctx)}Σ{format_tokens(shown_tokens(ctx)):>6}", f"bold orange1{ctx.bg}")]
    else:
        return [("       -", f"dim orange1{ctx.bg}")]


def render_cost(ctx: ColumnContext) -> ColumnOutput:
    """Dollar cost. The budget colours follow the total: a trip reset never adds budget."""
    s = ctx.session
    if ctx.claude_stats is not None:
        total = s.stats.estimated_cost_usd
        budget = s.cost_budget_usd
        if budget > 0:
            if total >= budget:
                style = f"bold red{ctx.bg}"
            elif total >= budget * 0.8:
                style = f"bold yellow{ctx.bg}"
            else:
                style = f"bold orange1{ctx.bg}"
        else:
            style = f"bold orange1{ctx.bg}"
        return [(f"{trip_mark(ctx)}{format_cost(shown_cost(ctx)):>6}", style)]
    else:
        return [("      -", f"dim orange1{ctx.bg}")]


def render_joules(ctx: ColumnContext) -> ColumnOutput:
    """Estimated energy in joules (#522; see energy.py)."""
    if ctx.claude_stats is not None:
        style = f"bold orange1{ctx.bg}"
        return [(f"{trip_mark(ctx)}⚡{format_joules(shown_energy(ctx))}", style)]
    else:
        return [("      -", f"dim orange1{ctx.bg}")]


def render_budget(ctx: ColumnContext) -> ColumnOutput:
    """Budget amount. Visibility gated by column's visible callback."""
    s = ctx.session
    if s.cost_budget_usd > 0:
        return [(f"/{format_cost(s.cost_budget_usd):>6}", f"dim orange1{ctx.bg}")]
    return None


def shown_subtree_cost(ctx: ColumnContext) -> float:
    """SUB$ as the row shows it: the subtree's rows' $ (trips where reset), else the total."""
    if ctx.subtree_trip_cost_usd is not None:
        return ctx.subtree_trip_cost_usd
    return ctx.subtree_cost_usd


def render_subtree_cost(ctx: ColumnContext) -> ColumnOutput:
    """Subtree cost (self + descendants). Visibility gated by column's visible callback.

    ↺ while a member's trip was reset: the sum of what the rows show.
    """
    if ctx.subtree_cost_usd > 0:
        mark = ctx.e("↺") if ctx.subtree_trip_cost_usd is not None else " "
        return [(f"{mark}Σ{format_cost(shown_subtree_cost(ctx)):>6}", f"dim orange1{ctx.bg}")]
    return None


def render_subtree_cost_plain(ctx: ColumnContext) -> Optional[str]:
    """Subtree cost for CLI."""
    if ctx.subtree_cost_usd > 0:
        mark = "↺" if ctx.subtree_trip_cost_usd is not None else ""
        return f"{mark}Σ{format_cost(shown_subtree_cost(ctx))}"
    return None


def burn_color_for_rate(cost_per_hour: float) -> str:
    """Pick a color for a $/hr burn rate using user-configured thresholds (#174).

    Thresholds are pairs of (color, max_usd_per_hour) sorted ascending;
    the first cutoff strictly greater than the rate wins. If the rate
    exceeds every cutoff, returns 'red'.
    """
    from ..settings import get_user_config
    try:
        thresholds = get_user_config().burn_thresholds
    except Exception:
        thresholds = [("green", 1.0), ("yellow", 10.0), ("orange1", 100.0)]
    for color, cutoff in thresholds:
        if cost_per_hour < cutoff:
            return _normalize_rich_color(color)
    return "red"


# Rich rejects "orange" outright (it uses orange1/orange3/orange4) and other
# common color synonyms have the same issue. Map the obvious aliases so a
# user typo in burn_thresholds doesn't crash the TUI on launch.
_RICH_COLOR_ALIASES = {
    "orange": "orange1",
    "purple": "purple4",
    "pink": "pink1",
    "gray": "grey50",
    "grey": "grey50",
}


def _normalize_rich_color(color: str) -> str:
    return _RICH_COLOR_ALIASES.get((color or "").lower(), color)


def format_pp_rate(pp_per_hour: float) -> str:
    """Quota points per hour in a 5-cell field: ``0.42%``, ``12.3%``, ``123%``."""
    if pp_per_hour < 9.995:
        return f"{pp_per_hour:.2f}%"
    if pp_per_hour < 99.95:
        return f"{pp_per_hour:.1f}%"
    return f"{pp_per_hour:.0f}%"


def render_burn_rate_plain(ctx: ColumnContext) -> Optional[str]:
    """Plain-text burn rate for the CLI (no styling, no emoji)."""
    burn = ctx.window_burn
    if burn is None or burn.window_hours <= 0:
        return None
    if ctx.show_cost == "quota":
        rate = burn.pp_per_hour
        return f"{format_pp_rate(rate)}/h" if rate else None
    if ctx.show_cost == "cost":
        return f"{format_cost(burn.cost_per_hour)}/h" if burn.cost_per_hour > 0 else None
    elif ctx.show_cost == "joules":
        if burn.watts <= 0:
            return None
        return format_watts(burn.watts).strip()
    else:
        return f"{format_tokens(int(burn.tokens_per_hour))}/h" if burn.tokens_per_hour > 0 else None


def render_burn_rate(ctx: ColumnContext) -> ColumnOutput:
    """Burn rate (🔥123K/h) over the timeline window (#174).

    Switches units with show_cost (tokens / cost / watts / weekly-quota
    points), like the aggregate burn in the spin stats line. Color always tracks the $/hr
    rate (per burn_thresholds in user config), regardless of which unit
    is on screen — burn intensity is what matters, the unit just changes
    the label.
    """
    burn = ctx.window_burn
    if burn is None or burn.window_hours <= 0:
        return [("        -", f"dim red{ctx.bg}")]

    color = burn_color_for_rate(burn.cost_per_hour)
    if ctx.show_cost == "quota":
        # Points of the weekly quota per hour (the Q% column's model)
        rate = burn.pp_per_hour
        if not rate or rate <= 0:
            return [("        -", f"dim red{ctx.bg}")]
        return [(f" 🔥{format_pp_rate(rate):>5}/h", f"bold {color}{ctx.bg}")]
    if ctx.show_cost == "cost":
        rate = burn.cost_per_hour
        if rate <= 0:
            return [("        -", f"dim red{ctx.bg}")]
        return [(f" 🔥{format_cost(rate):>5}/h", f"bold {color}{ctx.bg}")]
    elif ctx.show_cost == "joules":
        # Energy mode shows power: estimated watts over the window (#521)
        if burn.watts <= 0:
            return [("        -", f"dim red{ctx.bg}")]
        return [(f" 🔥{format_watts(burn.watts)}  ", f"bold {color}{ctx.bg}")]
    else:
        rate = burn.tokens_per_hour
        if rate <= 0:
            return [("        -", f"dim red{ctx.bg}")]
        return [(f" 🔥{format_tokens(int(rate)):>5}/h", f"bold {color}{ctx.bg}")]


def _plain_mark(ctx: ColumnContext) -> str:
    return "↺" if ctx.trip else ""


def render_token_count_plain(ctx: ColumnContext) -> Optional[str]:
    """Token count for CLI."""
    if ctx.claude_stats is None:
        return None
    return f"{_plain_mark(ctx)}Σ {format_tokens(shown_tokens(ctx))}"


def render_cost_plain(ctx: ColumnContext) -> Optional[str]:
    """Cost + budget for CLI (a trip's $ against the budget's, which counts the total)."""
    s = ctx.session
    if ctx.claude_stats is None:
        return None
    cost = f"{_plain_mark(ctx)}{format_cost(shown_cost(ctx))}"
    budget = s.cost_budget_usd
    if budget > 0:
        return f"{cost}/{format_cost(budget)}"
    return cost


# The quota cell's width (" 12.34%"): the "-" placeholder is padded to it
QUOTA_W = 7


def _pp(value) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def quota_week_pp(ctx: ColumnContext) -> Optional[float]:
    """The agent's predicted points of the weekly quota this week, None without one."""
    q = ctx.quota
    return _pp(q.get("pp7d") if isinstance(q, dict) else None)


def quota_pp(ctx: ColumnContext) -> Optional[float]:
    """Q% as the row shows it: since the trip reset (within this quota week), else this week's."""
    pp = quota_week_pp(ctx)
    if pp is not None and ctx.trip and _pp(ctx.trip.get("pp7d")) is not None:
        return _pp(ctx.trip.get("pp7d"))
    return pp


def render_quota(ctx: ColumnContext) -> ColumnOutput:
    """Share of the weekly subscription quota this week, in points (``1.37%``; an estimate).

    Orange like the token, cost and energy cells; not bold while the model
    is only its prior (not yet fitted to this account).
    """
    from ..quota_model import format_pp

    pp = quota_pp(ctx)
    if pp is None:
        return [("-".rjust(QUOTA_W), f"dim orange1{ctx.bg}")]
    prior = (ctx.quota or {}).get("conf") == "prior"
    style = "orange1" if prior else "bold orange1"
    return [(trip_mark(ctx) + format_pp(pp).rjust(QUOTA_W - 1), f"{style}{ctx.bg}")]


def render_quota_plain(ctx: ColumnContext) -> Optional[str]:
    from ..quota_model import format_pp

    pp = quota_pp(ctx)
    return None if pp is None else f"{_plain_mark(ctx)}{format_pp(pp)} wk"


# The billing-code cell's width: a code is cut to it (… marks the cut) and
# the "-" placeholder is padded to it, so the column holds still.
BILLING_CODE_W = 12


def _billing_code(ctx: ColumnContext) -> Optional[str]:
    code = getattr(ctx.session, "billing_code", None)
    return code if isinstance(code, str) and code else None


def render_billing_code(ctx: ColumnContext) -> ColumnOutput:
    """The code new spend is billed to; dim while it is the agent's own name
    (the default), bold once rebilled or launched with a code."""
    from rich.cells import cell_len, set_cell_size

    code = _billing_code(ctx)
    if code is None:
        return [(" " + "-".ljust(BILLING_CODE_W), f"dim{ctx.bg}")]
    style = "dim" if code == ctx.session.name else "bold cyan"
    if cell_len(code) > BILLING_CODE_W:
        code = set_cell_size(code, BILLING_CODE_W - 1) + "…"
    return [(" " + set_cell_size(code, BILLING_CODE_W), f"{style}{ctx.bg}")]


def render_billing_code_plain(ctx: ColumnContext) -> Optional[str]:
    return _billing_code(ctx)
