"""LLM usage columns: tokens, energy, cost, budget, subtree cost, burn rate."""

from typing import Optional

from ..energy import format_watts
from ..tui_helpers import format_cost, format_joules, format_tokens
from .context import ColumnContext, ColumnOutput


def render_token_count(ctx: ColumnContext) -> ColumnOutput:
    """Token count (Σ123K)."""
    if ctx.claude_stats is not None:
        return [(f" Σ{format_tokens(ctx.claude_stats.total_tokens):>6}", f"bold orange1{ctx.bg}")]
    else:
        return [("       -", f"dim orange1{ctx.bg}")]


def render_cost(ctx: ColumnContext) -> ColumnOutput:
    """Dollar cost."""
    s = ctx.session
    if ctx.claude_stats is not None:
        cost = s.stats.estimated_cost_usd
        budget = s.cost_budget_usd
        if budget > 0:
            if cost >= budget:
                style = f"bold red{ctx.bg}"
            elif cost >= budget * 0.8:
                style = f"bold yellow{ctx.bg}"
            else:
                style = f"bold orange1{ctx.bg}"
        else:
            style = f"bold orange1{ctx.bg}"
        return [(f" {format_cost(cost):>6}", style)]
    else:
        return [("      -", f"dim orange1{ctx.bg}")]


def render_joules(ctx: ColumnContext) -> ColumnOutput:
    """Estimated energy in joules (#522; see energy.py)."""
    s = ctx.session
    if ctx.claude_stats is not None:
        joules = s.stats.estimated_energy_j
        style = f"bold orange1{ctx.bg}"
        return [(f" ⚡{format_joules(joules)}", style)]
    else:
        return [("      -", f"dim orange1{ctx.bg}")]


def render_budget(ctx: ColumnContext) -> ColumnOutput:
    """Budget amount. Visibility gated by column's visible callback."""
    s = ctx.session
    if s.cost_budget_usd > 0:
        return [(f"/{format_cost(s.cost_budget_usd):>6}", f"dim orange1{ctx.bg}")]
    return None


def render_subtree_cost(ctx: ColumnContext) -> ColumnOutput:
    """Subtree cost (self + descendants). Visibility gated by column's visible callback."""
    if ctx.subtree_cost_usd > 0:
        return [(f" Σ{format_cost(ctx.subtree_cost_usd):>6}", f"dim orange1{ctx.bg}")]
    return None


def render_subtree_cost_plain(ctx: ColumnContext) -> Optional[str]:
    """Subtree cost for CLI."""
    if ctx.subtree_cost_usd > 0:
        return f"Σ{format_cost(ctx.subtree_cost_usd)}"
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


def render_burn_rate_plain(ctx: ColumnContext) -> Optional[str]:
    """Plain-text burn rate for the CLI (no styling, no emoji)."""
    burn = ctx.window_burn
    if burn is None or burn.window_hours <= 0:
        return None
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

    Switches units with show_cost (tokens / cost / watts), like the
    aggregate burn in the spin stats line. Color always tracks the $/hr
    rate (per burn_thresholds in user config), regardless of which unit
    is on screen — burn intensity is what matters, the unit just changes
    the label.
    """
    burn = ctx.window_burn
    if burn is None or burn.window_hours <= 0:
        return [("        -", f"dim red{ctx.bg}")]

    color = burn_color_for_rate(burn.cost_per_hour)
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


def render_token_count_plain(ctx: ColumnContext) -> Optional[str]:
    """Token count for CLI."""
    if ctx.claude_stats is None:
        return None
    return f"Σ {format_tokens(ctx.claude_stats.total_tokens)}"


def render_cost_plain(ctx: ColumnContext) -> Optional[str]:
    """Cost + budget for CLI."""
    s = ctx.session
    if ctx.claude_stats is None:
        return None
    cost = s.stats.estimated_cost_usd
    budget = s.cost_budget_usd
    if budget > 0:
        return f"{format_cost(cost)}/{format_cost(budget)}"
    return format_cost(cost)


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
