"""
Pure render function for the AI summarizer section of the daemon status bar.

Takes data and returns Rich Text, so it is testable without Textual.
"""

from rich.text import Text

from .tui_helpers import format_cost


def _cost_style(cost: float, cap: float) -> str:
    """Return a Rich style for cost based on how close it is to the cap.

    Thresholds: <50% green, 50-80% yellow, 80-100% orange, >=100% red bold.
    """
    if cap <= 0:
        return "yellow"
    ratio = cost / cap
    if ratio >= 1.0:
        return "red bold"
    elif ratio >= 0.8:
        return "orange1"
    elif ratio >= 0.5:
        return "yellow"
    return "green"


def render_ai_summarizer_section(
    summarizer_available: bool,
    summarizer_enabled: bool,
    summarizer_calls: int,
    summarizer_cost_usd: float = 0.0,
    summarizer_cost_cap: float = 100.0,
    summarizer_cost_cap_hit: bool = False,
) -> Text:
    """Render the AI Summarizer section of the status bar.

    Args:
        summarizer_available: Whether API key is available
        summarizer_enabled: Whether summarizer is enabled
        summarizer_calls: Number of API calls made
        summarizer_cost_usd: Cumulative cost in USD
        summarizer_cost_cap: Cost cap in USD
        summarizer_cost_cap_hit: Whether cost cap has been hit

    Returns:
        Rich Text for the AI section
    """

    content = Text()
    content.append("AI: ", style="bold")

    if summarizer_available:
        if summarizer_enabled:
            content.append("● ", style="green")
            if summarizer_calls > 0:
                content.append(f"{summarizer_calls}", style="cyan")
                if summarizer_cost_usd > 0:
                    style = _cost_style(summarizer_cost_usd, summarizer_cost_cap)
                    content.append(f" {format_cost(summarizer_cost_usd)}", style=style)
            else:
                content.append("on", style="green")
        else:
            content.append("○ ", style="dim")
            if summarizer_cost_cap_hit:
                content.append("HALTED", style="red bold")
                content.append(f" {format_cost(summarizer_cost_usd)}", style="red bold")
            else:
                content.append("off", style="dim")
                if summarizer_cost_usd > 0:
                    style = _cost_style(summarizer_cost_usd, summarizer_cost_cap) + " dim"
                    content.append(f" {format_cost(summarizer_cost_usd)}", style=style)
    else:
        content.append("○ ", style="red")
        content.append("n/a", style="red dim")

    return content
