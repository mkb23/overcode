"""
Declarative summary column definitions for the TUI.

Each column is a SummaryColumn with an ID, group, detail-level gate,
and a render function that produces styled text segments. The render()
loop in SessionSummary iterates SUMMARY_COLUMNS in order, skipping
columns whose detail level or group is not active.

Plain-text rendering for CLI: each column can optionally provide a
``label`` and ``render_plain`` function for use by ``overcode show``.
"""

import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, List, Optional, Tuple

from .status_constants import (
    ALL_STATUSES,
    BADGE_KINDS,
    STATUS_COLOR_GREEN,
    STATUS_COLOR_ORANGE,
    STATUS_COLOR_RED,
    STATUS_COLOR_YELLOW,
    StatusDetail,
    get_permissiveness_emoji,
)
from .status_patterns import extract_sleep_duration
from .tui_helpers import (
    format_cost,
    format_duration,
    format_joules,
    format_line_count,
    format_tokens,
    calculate_uptime,
    get_current_state_times,
    get_status_symbol,
    usd_to_joules,
)


# ---------------------------------------------------------------------------
# Tool name → emoji registry for allowed-tools column
# ---------------------------------------------------------------------------
TOOL_EMOJI: dict[str, str] = {
    "Bash": "🖥️",
    "Read": "📖",
    "Write": "✏️",
    "Edit": "\U0001fa9b",  # 🪛 screwdriver — moved from 🔧 (now the DTL `tool` badge)
    "Glob": "🔍",
    "Grep": "🔎",
    "WebFetch": "🌐",
    "WebSearch": "🕵️",
    "Task": "🧵",
    "Notebook": "📓",
    "NotebookEdit": "📓",
    "TodoRead": "📋",
    "TodoWrite": "📝",
}
TOOL_EMOJI_DEFAULT = "🔹"  # Fallback for unknown tools
MAX_TOOL_EMOJI = 10  # Configurable cap

# ---------------------------------------------------------------------------
# Skill name → emoji registry for loaded-skills column (#252)
# Defaults below; user overrides via config.yaml `skill_emoji:` section.
# ---------------------------------------------------------------------------
_SKILL_EMOJI_DEFAULTS: dict[str, str] = {
    "overcode": "🐙",
    "delegating-to-agents": "👥",
    "claude-api": "🔌",
    "simplify": "✨",
    "commit": "📦",
    "review-pr": "🔍",
    "reset": "🔄",
    "loop": "🔁",
    "schedule": "📅",
}
SKILL_EMOJI_DEFAULT = "🧩"  # Fallback for unknown skills

# ---------------------------------------------------------------------------
# Wrapper name → emoji registry for wrapper column (#437)
# Defaults below; user overrides via config.yaml `wrapper_emoji:` section.
# ---------------------------------------------------------------------------
_WRAPPER_EMOJI_DEFAULTS: dict[str, str] = {
    "devcontainer": "🐳",
    "passthrough": "🔗",
}
WRAPPER_EMOJI_DEFAULT = "🎁"  # Fallback for unknown wrappers
SANDBOX_EMOJI = "🏖️"  # Badge when Claude /sandbox is detected as ON (#451)


def get_skill_emoji() -> dict[str, str]:
    """Return merged skill emoji registry (defaults + user config overrides)."""
    from .settings import get_user_config
    merged = dict(_SKILL_EMOJI_DEFAULTS)
    merged.update(get_user_config().skill_emoji)
    return merged


def get_wrapper_emoji() -> dict[str, str]:
    """Return merged wrapper emoji registry (defaults + user config overrides)."""
    from .settings import get_user_config
    merged = dict(_WRAPPER_EMOJI_DEFAULTS)
    merged.update(getattr(get_user_config(), "wrapper_emoji", {}))
    return merged


def _wrapper_name(wrapper_path: str) -> str:
    """Extract display name from a wrapper path (e.g. '/path/foo.sh' → 'foo')."""
    from pathlib import Path
    return Path(wrapper_path).stem or wrapper_path


def _tool_emojis(allowed_tools: Optional[str], max_n: int = MAX_TOOL_EMOJI, emoji_free: bool = False) -> str:
    """Convert comma-separated tool names to emoji string."""
    if not allowed_tools:
        return ""
    from .status_constants import emoji_or_ascii
    tools = [t.strip() for t in allowed_tools.split(",") if t.strip()]
    emojis = [emoji_or_ascii(TOOL_EMOJI.get(t, TOOL_EMOJI_DEFAULT), emoji_free) for t in tools[:max_n]]
    sep = " " if emoji_free else ""
    suffix = "…" if len(tools) > max_n else ""
    return sep.join(emojis) + suffix


# ---------------------------------------------------------------------------
# Type alias for column output: list of (text, style) segments, or None to skip
# ---------------------------------------------------------------------------
ColumnOutput = Optional[List[Tuple[str, str]]]


# ---------------------------------------------------------------------------
# Detail-level convenience sets
# ---------------------------------------------------------------------------
ALL = {"low", "med", "high", "full"}
MED_PLUS = {"med", "high", "full"}
HIGH_PLUS = {"high", "full"}
# Backward-compat alias
FULL_PLUS = HIGH_PLUS


# ---------------------------------------------------------------------------
# ColumnContext: all pre-computed data a column needs
# ---------------------------------------------------------------------------
@dataclass
class ColumnContext:
    """Pre-computed data passed to every column render function."""

    # Session and stats
    session: object  # Session
    stats: object  # SessionStats
    claude_stats: object  # Optional[AgentSessionStats]
    git_diff_stats: Optional[tuple]  # (files, insertions, deletions) or None

    # Pre-computed values
    status_symbol: str
    status_color: str
    bg: str  # background style suffix, e.g. " on #0d2137" or ""
    monochrome: bool
    emoji_free: bool
    summary_detail: str
    show_cost: str  # "tokens", "cost", "joules"
    any_has_budget: bool  # True if any agent has a cost budget (#173)
    expand_icon: str
    is_list_mode: bool
    is_compact_mode: bool
    has_focus: bool
    is_unvisited_stalled: bool

    # Time values
    uptime: str
    green_time: float
    non_green_time: float
    sleep_time: float
    median_work: float

    # Pre-computed strings
    repo_name: str
    branch: str
    display_name: str
    perm_emoji: str

    # Repo column visibility
    all_names_match_repos: bool  # True → hide repo column (redundant with agent name)

    # Live counts
    live_subagent_count: int
    background_bash_count: int
    child_count: int  # Agent hierarchy child count (#244)

    # Time-in-state
    status_changed_at: Optional[datetime]

    # App-level alignment widths
    max_name_width: int
    max_repo_width: int
    max_branch_width: int

    # Oversight countdown
    any_has_oversight_timeout: bool = False
    oversight_deadline: Optional[str] = None

    # Sleep countdown (#289)
    any_is_sleeping: bool = False  # True if any agent is busy_sleeping
    sleep_wake_estimate: Optional[datetime] = None  # Estimated wake time

    # Two-column status model (#TBD). When populated, the ⏰ column renders
    # emoji-stacked badges from the hook reducer instead of just the sleep
    # countdown. None → legacy sleep countdown rendering.
    status_detail: Optional["StatusDetail"] = None  # noqa: F821 (forward ref)
    # App-wide flag — True if any agent has a populated status_detail, used
    # by the column's visibility predicate so the column stays present when
    # someone has a non-sleep badge.
    any_has_status_detail: bool = False

    # Subtree cost (parent + all descendants)
    subtree_cost_usd: float = 0.0
    any_has_subtree_cost: bool = False

    # PR number (widget var, not session — survives session replacement)
    pr_number: Optional[int] = None
    any_has_pr: bool = False

    # Model
    model: str = ""  # Model short name or full name
    any_has_model: bool = False  # True if any agent has a model set

    # Provider
    any_has_provider: bool = False  # True if any agent uses non-web provider

    # Agent CLI backend. The column only appears once a fleet is mixed —
    # a Claude-only user should see no change from opencode support landing.
    mixed_backends: bool = False

    # Resource usage (set when at least one agent has a non-zero reading)
    any_has_cpu: bool = False
    any_has_ram: bool = False

    # Sister integration (#245)
    source_host: str = ""
    is_remote: bool = False
    has_sisters: bool = False
    local_hostname: str = ""

    # Untracked file count (#455). None when unknown (e.g. not a git repo).
    git_untracked_count: Optional[int] = None

    # In-session auto-accept-edits mode detected from pane scrape (#444).
    # Overlay over normal launch mode; ignored when launch mode is permissive/bypass.
    auto_accept_mode: bool = False

    # Window-scoped burn rate for this session (#174). None when the burn
    # column isn't enabled or the session has no recent token activity.
    window_burn: Optional[object] = None  # Optional[WindowBurnStats]
    any_has_burn: bool = False  # True if any agent has a non-zero burn rate

    def mono(self, colored: str, simple: str = "bold") -> str:
        """Return colored style (monochrome only applies to preview pane, not summaries)."""
        return colored

    def e(self, char: str) -> str:
        """Return ASCII fallback if emoji_free mode is active (#315),
        otherwise route through the VS16-stripping safety net so
        Windows Terminal / Konsole don't drift on variation-selector
        emoji clusters (#174-followup)."""
        if self.emoji_free:
            from .status_constants import EMOJI_ASCII
            return EMOJI_ASCII.get(char, char)
        from .status_constants import _safe_emoji
        return _safe_emoji(char)


# ---------------------------------------------------------------------------
# SummaryColumn definition
# ---------------------------------------------------------------------------
@dataclass
class SummaryColumn:
    """Declarative definition of a summary line column."""

    id: str
    group: str  # Group ID from summary_groups.py
    detail_levels: set  # Which detail levels show this column
    render: Callable[[ColumnContext], ColumnOutput]
    label: str = ""  # Human-readable label for CLI (e.g., "Status", "Uptime")
    render_plain: Optional[Callable[[ColumnContext], Optional[str]]] = None
    placeholder_width: int = 0  # When visible but render returns None, pad with N spaces
    visible: Optional[Callable[[ColumnContext], bool]] = None  # App-level visibility gate
    header: str = ""  # Short header label for column header row (e.g., "UPT", "TOK")
    name: str = ""  # Human-readable display name for config modal
    cli_only: bool = False  # True for synthetic aggregate lines used only by the CLI


# ---------------------------------------------------------------------------
# Render function factories
# ---------------------------------------------------------------------------

def _get_field(ctx: ColumnContext, field: str):
    """Resolve a dotted field path, e.g. 'stats.steers_count'."""
    obj = ctx
    for part in field.split("."):
        obj = getattr(obj, part)
    return obj


def _make_simple_render(
    field: str,
    formatter: Optional[Callable] = None,
    format_str: str = "{v}",
    colored_style: str = "bold",
    mono_style: str = "bold",
) -> Callable[[ColumnContext], ColumnOutput]:
    """Factory for single-segment render functions.

    Args:
        field: Attribute path on ColumnContext (dot-notation for nested)
        formatter: Optional callable to transform the raw value
        format_str: Format string with {v} placeholder for the formatted value
        colored_style: Rich style when not monochrome (bg suffix added automatically)
        mono_style: Rich style when monochrome
    """
    def render(ctx: ColumnContext) -> ColumnOutput:
        val = _get_field(ctx, field)
        if formatter is not None:
            val = formatter(val)
        return [(format_str.format(v=val), ctx.mono(f"{colored_style}{ctx.bg}", mono_style))]
    return render


def _make_simple_render_plain(
    field: str,
    formatter: Optional[Callable] = None,
    template: str = "{v}",
) -> Callable[[ColumnContext], Optional[str]]:
    """Factory for simple plain-text render functions.

    Args:
        field: Attribute path on ColumnContext (dot-notation for nested)
        formatter: Optional callable to transform the raw value
        template: Format string with {v} placeholder
    """
    def render(ctx: ColumnContext) -> Optional[str]:
        val = _get_field(ctx, field)
        if formatter is not None:
            val = formatter(val)
        return template.format(v=val)
    return render


# ---------------------------------------------------------------------------
# Render functions — each returns list of (text, style) or None
# ---------------------------------------------------------------------------

def render_status_symbol(ctx: ColumnContext) -> ColumnOutput:
    # Most status emojis are 2 cells wide; some (☑️) are 1 cell.
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
        return [(ctx.e("🔔"), ctx.mono(f"bold blink red{ctx.bg}", "bold"))]
    else:
        return [("  ", ctx.mono(f"dim{ctx.bg}", "dim"))]


def render_time_in_state(ctx: ColumnContext) -> ColumnOutput:
    state_start = ctx.status_changed_at
    if state_start is None and ctx.stats.state_since:
        try:
            state_start = datetime.fromisoformat(ctx.stats.state_since)
        except (ValueError, TypeError):
            pass
    if state_start:
        elapsed = (datetime.now() - state_start).total_seconds()
        return [(f"{format_duration(elapsed):>5} ", ctx.status_color)]
    else:
        return [("    - ", ctx.mono(f"dim{ctx.bg}", "dim"))]


_STATUS_COLOR_TO_STYLE: dict[str, str] = {
    STATUS_COLOR_GREEN:  "green",
    STATUS_COLOR_ORANGE: "orange1",
    STATUS_COLOR_YELLOW: "yellow",
    STATUS_COLOR_RED:    "red",
}

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
    color = _STATUS_COLOR_TO_STYLE.get(detail.color, "yellow")
    style = ctx.mono(f"{color}{ctx.bg}", "bold")

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
        remaining = max(0, (ctx.sleep_wake_estimate - datetime.now()).total_seconds())
        return [(f" {ctx.e('⏰')}{format_duration(remaining):>5} ", ctx.mono(f"yellow{ctx.bg}", "bold"))]
    return None


def render_expand_icon(ctx: ColumnContext) -> ColumnOutput:
    if ctx.has_focus:
        return [("→ ", ctx.status_color)]
    else:
        return [("  ", ctx.status_color)]


render_agent_name = _make_simple_render("display_name", colored_style="bold cyan")


def render_repo_name(ctx: ColumnContext) -> ColumnOutput:
    if ctx.all_names_match_repos:
        return None
    w = ctx.max_repo_width
    return [(f" {ctx.repo_name:<{w}}", ctx.mono(f"bold dim{ctx.bg}", "dim"))]


def render_branch(ctx: ColumnContext) -> ColumnOutput:
    w = ctx.max_branch_width
    sep = ":" if not ctx.all_names_match_repos else " "
    return [(f"{sep}{ctx.branch:<{w}} ", ctx.mono(f"bold dim{ctx.bg}", "dim"))]


render_uptime = _make_simple_render("uptime", format_str=" ↑{v:>5}", colored_style="bold white")


render_running_time = _make_simple_render("green_time", format_duration, " ▶{v:>5}", "bold green")


render_stalled_time = _make_simple_render("non_green_time", format_duration, " ⏸{v:>5}", "bold red", "dim")


def render_sleep_time(ctx: ColumnContext) -> ColumnOutput:
    sleep_str = format_duration(ctx.sleep_time) if ctx.sleep_time > 0 else "-"
    sleep_col = f" 💤{sleep_str:>5}"
    style = ctx.mono(f"bold cyan{ctx.bg}", "bold") if ctx.sleep_time > 0 else ctx.mono(f"dim cyan{ctx.bg}", "dim")
    return [(sleep_col, style)]


def render_active_pct(ctx: ColumnContext) -> ColumnOutput:
    active_time = ctx.green_time + ctx.non_green_time
    pct = (ctx.green_time / active_time * 100) if active_time > 0 else 0
    return [(f" {pct:>3.0f}%", ctx.mono(f"bold green{ctx.bg}" if pct >= 50 else f"bold red{ctx.bg}", "bold"))]


def render_token_count(ctx: ColumnContext) -> ColumnOutput:
    """Token count (Σ123K)."""
    if ctx.claude_stats is not None:
        return [(f" Σ{format_tokens(ctx.claude_stats.total_tokens):>6}", ctx.mono(f"bold orange1{ctx.bg}", "bold"))]
    else:
        return [("       -", ctx.mono(f"dim orange1{ctx.bg}", "dim"))]


def render_model(ctx: ColumnContext) -> ColumnOutput:
    """Model name. Only visible when any agent has a model set."""
    if not ctx.model:
        return [("     -", ctx.mono(f"dim{ctx.bg}", "dim"))]
    from .history_reader import model_short_name
    display = model_short_name(ctx.model)[:6]
    return [(f" {display:>5}", ctx.mono(f"bold magenta{ctx.bg}", "bold"))]


def render_model_plain(ctx: ColumnContext) -> Optional[str]:
    if not ctx.model:
        return None
    from .history_reader import model_short_name
    return model_short_name(ctx.model)


def render_context_usage(ctx: ColumnContext) -> ColumnOutput:
    """Context window usage (📚XX%). Always visible.

    Renders a dash when the model's context window is unknown (#469) —
    never assumes a default window size, since that produces a plausible-
    looking but wrong percentage (the original bug: an unrecognized model
    silently priced against a 200K default that had nothing to do with the
    real model in use).
    """
    if ctx.claude_stats is not None and ctx.claude_stats.current_context_tokens > 0:
        max_context = ctx.claude_stats.max_context_tokens
        if max_context:
            ctx_pct = min(100, ctx.claude_stats.current_context_tokens / max_context * 100)
            return [(f" 📚{ctx_pct:>3.0f}%", ctx.mono(f"bold orange1{ctx.bg}", "bold"))]
    return [(" 📚  -%", ctx.mono(f"dim orange1{ctx.bg}", "dim"))]


def render_cost(ctx: ColumnContext) -> ColumnOutput:
    """Dollar cost."""
    s = ctx.session
    if ctx.claude_stats is not None:
        cost = s.stats.estimated_cost_usd
        budget = s.cost_budget_usd
        if budget > 0:
            if cost >= budget:
                style = ctx.mono(f"bold red{ctx.bg}", "bold")
            elif cost >= budget * 0.8:
                style = ctx.mono(f"bold yellow{ctx.bg}", "bold")
            else:
                style = ctx.mono(f"bold orange1{ctx.bg}", "bold")
        else:
            style = ctx.mono(f"bold orange1{ctx.bg}", "bold")
        return [(f" {format_cost(cost):>6}", style)]
    else:
        return [("      -", ctx.mono(f"dim orange1{ctx.bg}", "dim"))]


def render_joules(ctx: ColumnContext) -> ColumnOutput:
    """Energy in joules."""
    s = ctx.session
    if ctx.claude_stats is not None:
        cost = s.stats.estimated_cost_usd
        joules = usd_to_joules(cost)
        style = ctx.mono(f"bold orange1{ctx.bg}", "bold")
        return [(f" ⚡{format_joules(joules)}", style)]
    else:
        return [("      -", ctx.mono(f"dim orange1{ctx.bg}", "dim"))]


def render_budget(ctx: ColumnContext) -> ColumnOutput:
    """Budget amount. Visibility gated by column's visible callback."""
    s = ctx.session
    if s.cost_budget_usd > 0:
        return [(f"/{format_cost(s.cost_budget_usd):>6}", ctx.mono(f"dim orange1{ctx.bg}", "dim"))]
    return None


def render_subtree_cost(ctx: ColumnContext) -> ColumnOutput:
    """Subtree cost (self + descendants). Visibility gated by column's visible callback."""
    if ctx.subtree_cost_usd > 0:
        return [(f" Σ{format_cost(ctx.subtree_cost_usd):>6}", ctx.mono(f"dim orange1{ctx.bg}", "dim"))]
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
    from .settings import get_user_config
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
        if burn.cost_per_hour <= 0:
            return None
        return f"{format_joules(usd_to_joules(burn.cost_per_hour))}/h"
    else:
        return f"{format_tokens(int(burn.tokens_per_hour))}/h" if burn.tokens_per_hour > 0 else None


def render_burn_rate(ctx: ColumnContext) -> ColumnOutput:
    """Burn rate (🔥123K/h) over the timeline window (#174).

    Switches units with show_cost (tokens / cost / joules), like the
    aggregate burn in the spin stats line. Color always tracks the $/hr
    rate (per burn_thresholds in user config), regardless of which unit
    is on screen — burn intensity is what matters, the unit just changes
    the label.
    """
    burn = ctx.window_burn
    if burn is None or burn.window_hours <= 0:
        return [("        -", ctx.mono(f"dim red{ctx.bg}", "dim"))]

    color = burn_color_for_rate(burn.cost_per_hour)
    if ctx.show_cost == "cost":
        rate = burn.cost_per_hour
        if rate <= 0:
            return [("        -", ctx.mono(f"dim red{ctx.bg}", "dim"))]
        return [(f" 🔥{format_cost(rate):>5}/h", ctx.mono(f"bold {color}{ctx.bg}", "bold"))]
    elif ctx.show_cost == "joules":
        rate = burn.cost_per_hour
        if rate <= 0:
            return [("        -", ctx.mono(f"dim red{ctx.bg}", "dim"))]
        return [(f" 🔥{format_joules(usd_to_joules(rate))}/h", ctx.mono(f"bold {color}{ctx.bg}", "bold"))]
    else:
        rate = burn.tokens_per_hour
        if rate <= 0:
            return [("        -", ctx.mono(f"dim red{ctx.bg}", "dim"))]
        return [(f" 🔥{format_tokens(int(rate)):>5}/h", ctx.mono(f"bold {color}{ctx.bg}", "bold"))]


# Backward-compat alias
render_tokens = render_token_count


def render_git_diff(ctx: ColumnContext) -> ColumnOutput:
    if ctx.git_diff_stats:
        files, ins, dels = ctx.git_diff_stats
        if ctx.summary_detail in ("full", "high"):
            return [
                (f" Δ{files:>2}", ctx.mono(f"bold magenta{ctx.bg}", "bold")),
                (f" +{format_line_count(ins):>5}", ctx.mono(f"bold green{ctx.bg}", "bold")),
                (f" -{format_line_count(dels):>5}", ctx.mono(f"bold red{ctx.bg}", "dim")),
            ]
        else:
            has_changes = files > 0
            return [(f" Δ{files:>2}", ctx.mono(f"bold magenta{ctx.bg}" if has_changes else f"dim{ctx.bg}", "bold" if has_changes else "dim"))]
    else:
        if ctx.summary_detail in ("full", "high"):
            return [(" Δ - +    - -    -", ctx.mono(f"dim{ctx.bg}", "dim"))]
        else:
            return [(" Δ -", ctx.mono(f"dim{ctx.bg}", "dim"))]


def render_git_untracked(ctx: ColumnContext) -> ColumnOutput:
    """Untracked file count (#455). Highlights when there are untracked files,
    so a 0-line diff doesn't hide work an agent has yet to commit."""
    n = ctx.git_untracked_count
    if n is None:
        return [(" U  -", ctx.mono(f"dim{ctx.bg}", "dim"))]
    if n > 0:
        return [(f" U{n:>3}", ctx.mono(f"bold yellow{ctx.bg}", "bold"))]
    return [(" U  0", ctx.mono(f"dim{ctx.bg}", "dim"))]


def render_git_untracked_plain(ctx: ColumnContext) -> Optional[str]:
    n = ctx.git_untracked_count
    if n is None or n == 0:
        return None
    return f"U{n}"


def render_pr_number(ctx: ColumnContext) -> ColumnOutput:
    pr = ctx.pr_number
    if pr is not None:
        return [(f" PR#{pr}", ctx.mono(f"bold cyan{ctx.bg}", "bold"))]
    return None


def render_pr_number_plain(ctx: ColumnContext) -> Optional[str]:
    pr = ctx.pr_number
    if pr is not None:
        return f"PR#{pr}"
    return None


def render_median_work_time(ctx: ColumnContext) -> ColumnOutput:
    """Median work-cycle time. Dashed when the backend reports no stats."""
    if ctx.claude_stats is None:
        return [(f" ⏱{'-':>5}", ctx.mono(f"dim blue{ctx.bg}", "dim"))]
    return [(
        f" ⏱{format_duration(ctx.median_work):>5}",
        ctx.mono(f"bold blue{ctx.bg}", "bold"),
    )]


def _format_rss(rss_bytes: int) -> str:
    """Format resident-set-size as a short human string (e.g. 842M, 1.4G)."""
    if rss_bytes <= 0:
        return "    -"
    mib = rss_bytes / (1024 * 1024)
    if mib < 1024:
        return f"{mib:>4.0f}M"
    return f"{mib / 1024:>4.1f}G"


def render_cpu_pct(ctx: ColumnContext) -> ColumnOutput:
    """CPU % for the claude process tree. >100 = multi-core. Auto-hides
    via visible=any_has_cpu."""
    cpu = getattr(ctx.session, 'cpu_percent', 0.0) or 0.0
    if cpu < 1.0:
        return [("     -", ctx.mono(f"dim{ctx.bg}", "dim"))]
    if cpu >= 200.0:
        style = ctx.mono(f"bold red{ctx.bg}", "bold")
    elif cpu >= 100.0:
        style = ctx.mono(f"bold yellow{ctx.bg}", "bold")
    elif cpu >= 50.0:
        style = ctx.mono(f"bold{ctx.bg}", "bold")
    else:
        style = ctx.mono(f"dim{ctx.bg}", "dim")
    return [(f" {cpu:>4.0f}%", style)]


def render_cpu_pct_plain(ctx: ColumnContext) -> Optional[str]:
    cpu = getattr(ctx.session, 'cpu_percent', 0.0) or 0.0
    if cpu < 1.0:
        return None
    return f"{cpu:.0f}%"


def render_ram(ctx: ColumnContext) -> ColumnOutput:
    """Resident memory for the claude process tree, human-readable."""
    rss = getattr(ctx.session, 'rss_bytes', 0) or 0
    if rss <= 0:
        return [("     -", ctx.mono(f"dim{ctx.bg}", "dim"))]
    # Colour heavy consumers (>4 GB) for parity with CPU highlighting.
    gib = rss / (1024 * 1024 * 1024)
    if gib >= 8.0:
        style = ctx.mono(f"bold red{ctx.bg}", "bold")
    elif gib >= 4.0:
        style = ctx.mono(f"bold yellow{ctx.bg}", "bold")
    elif gib >= 1.0:
        style = ctx.mono(f"bold{ctx.bg}", "bold")
    else:
        style = ctx.mono(f"dim{ctx.bg}", "dim")
    return [(f" {_format_rss(rss)}", style)]


def render_ram_plain(ctx: ColumnContext) -> Optional[str]:
    rss = getattr(ctx.session, 'rss_bytes', 0) or 0
    if rss <= 0:
        return None
    return _format_rss(rss).strip()


def render_subagent_count(ctx: ColumnContext) -> ColumnOutput:
    count = ctx.live_subagent_count
    style = ctx.mono(f"bold purple{ctx.bg}", "bold") if count > 0 else ctx.mono(f"dim{ctx.bg}", "dim")
    return [(f" {ctx.e('🤿')}{count:>2}", style)]


def render_bash_count(ctx: ColumnContext) -> ColumnOutput:
    count = ctx.background_bash_count
    style = ctx.mono(f"bold yellow{ctx.bg}", "bold") if count > 0 else ctx.mono(f"dim{ctx.bg}", "dim")
    return [(f" {ctx.e('🐚')}{count:>2}", style)]


def render_child_count(ctx: ColumnContext) -> ColumnOutput:
    count = ctx.child_count
    if count == 0:
        return [(f" {ctx.e('👶')} 0", ctx.mono(f"dim{ctx.bg}", "dim"))]
    style = ctx.mono(f"bold cyan{ctx.bg}", "bold")
    return [(f" {ctx.e('👶')}{count:>2}", style)]


render_permission_mode = _make_simple_render("perm_emoji", format_str=" {v}", colored_style="bold white")


def render_agent_teams(ctx: ColumnContext) -> ColumnOutput:
    if ctx.session.agent_teams:
        return [(f" {ctx.e('🤝')}", ctx.mono(f"bold cyan{ctx.bg}", "bold"))]
    return None


def render_teams_plain(ctx: ColumnContext) -> Optional[str]:
    if ctx.session.agent_teams:
        return "teams"
    return None


def render_allowed_tools(ctx: ColumnContext) -> ColumnOutput:
    emojis = _tool_emojis(ctx.session.allowed_tools, emoji_free=ctx.emoji_free)
    if not emojis:
        return None
    return [(f" {emojis}", ctx.mono(f"white{ctx.bg}", ""))]


def render_loaded_skills(ctx: ColumnContext) -> ColumnOutput:
    skills = ctx.session.loaded_skills
    if not skills:
        return None
    from .status_constants import emoji_or_ascii
    emojis = [emoji_or_ascii(get_skill_emoji().get(s, SKILL_EMOJI_DEFAULT), ctx.emoji_free) for s in skills]
    sep = " " if ctx.emoji_free else ""
    text = sep.join(emojis)
    return [(f" {text}", ctx.mono(f"white{ctx.bg}", ""))]


def render_skills_plain(ctx: ColumnContext) -> Optional[str]:
    skills = ctx.session.loaded_skills
    if not skills:
        return None
    emojis = [get_skill_emoji().get(s, SKILL_EMOJI_DEFAULT) for s in skills]
    return f"{''.join(emojis)}  ({', '.join(skills)})"


def render_available_skills(ctx: ColumnContext) -> ColumnOutput:
    skills = ctx.session.available_skills
    if not skills:
        return None
    from .status_constants import emoji_or_ascii
    emojis = [emoji_or_ascii(get_skill_emoji().get(s, SKILL_EMOJI_DEFAULT), ctx.emoji_free) for s in skills]
    sep = " " if ctx.emoji_free else ""
    text = sep.join(emojis)
    return [(f" {text}", ctx.mono(f"dim white{ctx.bg}", "dim"))]


def render_available_skills_plain(ctx: ColumnContext) -> Optional[str]:
    skills = ctx.session.available_skills
    if not skills:
        return None
    emojis = [get_skill_emoji().get(s, SKILL_EMOJI_DEFAULT) for s in skills]
    return f"{''.join(emojis)}  ({', '.join(skills)})"


def render_enhanced_context(ctx: ColumnContext) -> ColumnOutput:
    if ctx.session.enhanced_context_enabled:
        return [(f" {ctx.e('🪝')}", ctx.mono(f"bold white{ctx.bg}", "bold"))]
    else:
        return [("  ·", ctx.mono(f"dim{ctx.bg}", "dim"))]


def render_wrapper(ctx: ColumnContext) -> ColumnOutput:
    """Wrapper badge + sandbox badge. None when neither is active."""
    wrapper = ctx.session.wrapper
    sandbox_on = getattr(ctx.session, "sandbox_enabled", None) is True
    if not wrapper and not sandbox_on:
        return None
    badges = ""
    if wrapper:
        name = _wrapper_name(wrapper)
        badges += ctx.e(get_wrapper_emoji().get(name, WRAPPER_EMOJI_DEFAULT))
    if sandbox_on:
        badges += ctx.e(SANDBOX_EMOJI)
    return [(f" {badges}", ctx.mono(f"bold yellow{ctx.bg}", "bold"))]


def render_wrapper_plain(ctx: ColumnContext) -> Optional[str]:
    wrapper = ctx.session.wrapper
    sandbox_on = getattr(ctx.session, "sandbox_enabled", None) is True
    parts = []
    if wrapper:
        parts.append(_wrapper_name(wrapper))
    if sandbox_on:
        parts.append("sandbox")
    return "+".join(parts) if parts else None


def render_human_count(ctx: ColumnContext) -> ColumnOutput:
    if ctx.claude_stats is not None:
        human_count = max(0, ctx.claude_stats.interaction_count - ctx.stats.steers_count)
        return [(f" {ctx.e('👤')}{human_count:>3}", ctx.mono(f"bold yellow{ctx.bg}", "bold"))]
    else:
        return [(f" {ctx.e('👤')}  -", ctx.mono(f"dim yellow{ctx.bg}", "dim"))]


def render_robot_count(ctx: ColumnContext) -> ColumnOutput:
    v = ctx.stats.steers_count
    return [(f" {ctx.e('🤖')}{v:>3}", ctx.mono(f"bold cyan{ctx.bg}", "bold"))]


def render_standing_orders(ctx: ColumnContext) -> ColumnOutput:
    s = ctx.session
    if s.standing_instructions:
        if s.standing_orders_complete:
            return [(f" {ctx.e('✓')}", ctx.mono(f"bold green{ctx.bg}", "bold"))]
        elif s.standing_instructions_preset:
            preset_display = f" {s.standing_instructions_preset[:8]}"
            return [(preset_display, ctx.mono(f"bold cyan{ctx.bg}", "bold"))]
        else:
            return [(f" {ctx.e('📋')}", ctx.mono(f"bold yellow{ctx.bg}", "bold"))]
    else:
        return [(" ➖", ctx.mono(f"bold dim{ctx.bg}", "dim"))]


def render_oversight_countdown(ctx: ColumnContext) -> ColumnOutput:
    """Oversight countdown timer. Visibility gated by column's visible callback."""
    from .status_constants import STATUS_WAITING_OVERSIGHT
    status = ctx.stats.current_state if hasattr(ctx.stats, 'current_state') else ""

    # Check session status as well (session object may have the status)
    session_status = getattr(ctx.session, 'status', '')
    is_oversight = session_status == STATUS_WAITING_OVERSIGHT or status == STATUS_WAITING_OVERSIGHT

    if not is_oversight:
        return None

    deadline_str = ctx.oversight_deadline
    if not deadline_str:
        hg = ctx.e("⏳")
        return [(f" {hg} --:--", ctx.mono(f"yellow{ctx.bg}", "dim"))]

    try:
        hg = ctx.e("⏳")
        deadline = datetime.fromisoformat(deadline_str)
        remaining = (deadline - datetime.now()).total_seconds()
        if remaining <= 0:
            return [(f" {hg} 0s  ", ctx.mono(f"bold blink red{ctx.bg}", "bold"))]

        if remaining < 60:
            text = f" {hg} {remaining:>3.0f}s"
        elif remaining < 3600:
            mins = int(remaining // 60)
            secs = int(remaining % 60)
            text = f" {hg}{mins:>2}m{secs:02d}s"
        else:
            hrs = int(remaining // 3600)
            mins = int((remaining % 3600) // 60)
            text = f" {hg}{hrs:>2}h{mins:02d}m"

        if remaining < 30:
            style = ctx.mono(f"bold blink red{ctx.bg}", "bold")
        elif remaining < 60:
            style = ctx.mono(f"bold red{ctx.bg}", "bold")
        else:
            style = ctx.mono(f"bold yellow{ctx.bg}", "bold")
        return [(text, style)]
    except (ValueError, TypeError):
        return [(f" {hg} --:--", ctx.mono(f"dim{ctx.bg}", "dim"))]


def _compute_next_heartbeat(session) -> Optional[str]:
    """Compute next heartbeat time as HH:MM string, or None."""
    now = datetime.now()
    if session.last_heartbeat_time:
        try:
            last_hb = datetime.fromisoformat(session.last_heartbeat_time)
            next_due = last_hb + timedelta(seconds=session.heartbeat_frequency_seconds)
            # If next due is in the past (stale timestamp), show "now"
            if next_due < now:
                return now.strftime("%H:%M")
            return next_due.strftime("%H:%M")
        except (ValueError, TypeError):
            pass
    if session.start_time:
        try:
            start = datetime.fromisoformat(session.start_time)
            next_due = start + timedelta(seconds=session.heartbeat_frequency_seconds)
            if next_due < now:
                return now.strftime("%H:%M")
            return next_due.strftime("%H:%M")
        except (ValueError, TypeError):
            pass
    return None


def render_heartbeat(ctx: ColumnContext) -> ColumnOutput:
    s = ctx.session
    hb = ctx.e("💓")
    if s.heartbeat_enabled and not s.heartbeat_paused:
        freq_str = format_duration(s.heartbeat_frequency_seconds)
        segments = [(f" {hb}{freq_str:>5}", ctx.mono(f"bold magenta{ctx.bg}", "bold"))]
        next_time_str = _compute_next_heartbeat(s)
        if next_time_str:
            segments.append((f" @{next_time_str}", ctx.mono(f"bold cyan{ctx.bg}", "bold")))
        else:
            segments.append((" @--:--", ctx.mono(f"dim{ctx.bg}", "dim")))
        return segments
    elif s.heartbeat_enabled and s.heartbeat_paused:
        freq_str = format_duration(s.heartbeat_frequency_seconds)
        return [
            (f" {hb}{freq_str:>5}", ctx.mono(f"dim{ctx.bg}", "dim")),
            ("     ⏸ ", ctx.mono(f"bold yellow{ctx.bg}", "bold")),
        ]
    else:
        return [(f" {hb}    - @--:--", ctx.mono(f"dim{ctx.bg}", "dim"))]


def render_agent_value(ctx: ColumnContext) -> ColumnOutput:
    s = ctx.session
    if ctx.summary_detail in ("full", "high"):
        return [(f" {ctx.e('💰')}{s.agent_value:>4}", ctx.mono(f"bold magenta{ctx.bg}", "bold"))]
    else:
        if s.agent_value > 1000:
            return [(f" {ctx.e('⏫️')}", ctx.mono(f"bold red{ctx.bg}", "bold"))]
        elif s.agent_value < 1000:
            return [(f" {ctx.e('⏬️')}", ctx.mono(f"bold blue{ctx.bg}", "bold"))]
        else:
            return [(f" {ctx.e('⏹️')} ", ctx.mono(f"dim{ctx.bg}", "dim"))]


# ---------------------------------------------------------------------------
# Plain-text render functions for CLI output
# ---------------------------------------------------------------------------

def render_status_plain(ctx: ColumnContext) -> Optional[str]:
    """Status with time-in-state."""
    # Determine status name from the symbol
    status = "unknown"
    # Reverse-lookup from status_symbol
    for candidate in ALL_STATUSES:
        sym, _ = get_status_symbol(candidate)
        if sym == ctx.status_symbol:
            status = candidate
            break

    state_start = ctx.status_changed_at
    if state_start is None and ctx.stats.state_since:
        try:
            state_start = datetime.fromisoformat(ctx.stats.state_since)
        except (ValueError, TypeError):
            pass

    time_str = ""
    if state_start:
        elapsed = (datetime.now() - state_start).total_seconds()
        time_str = f" ({format_duration(elapsed)})"

    return f"{ctx.status_symbol} {status}{time_str}"


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


def render_token_count_plain(ctx: ColumnContext) -> Optional[str]:
    """Token count for CLI."""
    if ctx.claude_stats is None:
        return None
    return f"Σ {format_tokens(ctx.claude_stats.total_tokens)}"


def render_context_usage_plain(ctx: ColumnContext) -> Optional[str]:
    """Context window usage for CLI.

    None when the model's context window is unknown (#469) — same "assume
    nothing" rule as render_context_usage above.
    """
    if ctx.claude_stats is None:
        return None
    if ctx.claude_stats.current_context_tokens > 0:
        max_context = ctx.claude_stats.max_context_tokens
        if max_context:
            ctx_pct = min(100, ctx.claude_stats.current_context_tokens / max_context * 100)
            return f"context {ctx_pct:.0f}%"
    return None


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


# Backward-compat alias
render_tokens_plain = render_token_count_plain


def render_git_diff_plain(ctx: ColumnContext) -> Optional[str]:
    if ctx.git_diff_stats:
        files, ins, dels = ctx.git_diff_stats
        return f"Δ{files} files +{format_line_count(ins)} -{format_line_count(dels)}"
    return None


def render_work_plain(ctx: ColumnContext) -> Optional[str]:
    if ctx.claude_stats is None:
        return None
    median_work = ctx.median_work
    work_str = format_duration(median_work) if median_work > 0 else "-"
    human_count = max(0, ctx.claude_stats.interaction_count - ctx.stats.steers_count)
    return f"⏱ {work_str} median  👤 {human_count} human 🤖 {ctx.stats.steers_count} robot"


def render_agents_plain(ctx: ColumnContext) -> Optional[str]:
    return f"🤿 {ctx.live_subagent_count} subagents  🐚 {ctx.background_bash_count} background bashes"


def render_repo_name_plain(ctx: ColumnContext) -> Optional[str]:
    if ctx.all_names_match_repos:
        return None
    return ctx.repo_name


render_branch_plain = _make_simple_render_plain("branch")


def render_mode_plain(ctx: ColumnContext) -> Optional[str]:
    from .status_constants import PERMISSIVENESS_EMOJIS
    perm = ctx.session.permissiveness_mode
    if ctx.auto_accept_mode and perm == "normal":
        perm = "auto"
    emoji = PERMISSIVENESS_EMOJIS.get(perm, "👮")
    mode = f"{emoji} {perm}"
    ec = "🪝 enabled" if ctx.session.enhanced_context_enabled else "disabled"
    return f"{mode}  Enh ctx: {ec}"


def render_tools_plain(ctx: ColumnContext) -> Optional[str]:
    if not ctx.session.allowed_tools:
        return None
    emojis = _tool_emojis(ctx.session.allowed_tools)
    return f"{emojis}  ({ctx.session.allowed_tools})"


def render_heartbeat_plain(ctx: ColumnContext) -> Optional[str]:
    s = ctx.session
    if not s.heartbeat_enabled:
        return "disabled"
    freq_str = format_duration(s.heartbeat_frequency_seconds)
    if s.heartbeat_paused:
        return f"💓 {freq_str} (paused)"
    next_time_str = _compute_next_heartbeat(s)
    time_part = f" @{next_time_str}" if next_time_str else ""
    return f"💓 {freq_str}{time_part}"


def render_orders_plain(ctx: ColumnContext) -> Optional[str]:
    s = ctx.session
    if not s.standing_instructions:
        return None
    prefix = "✓ " if s.standing_orders_complete else ""
    return f"📋 {prefix}{s.standing_instructions[:80]}"


render_value_plain = _make_simple_render_plain("session.agent_value", str)


def render_provider(ctx: ColumnContext) -> ColumnOutput:
    """API provider: 🪨 bedrock, · web. Hidden when all agents use web."""
    provider = getattr(ctx.session, 'provider', 'web') or 'web'
    if provider == 'bedrock':
        return [(f" {ctx.e('🪨')}", ctx.mono(f"bold cyan{ctx.bg}", "bold"))]
    return [("  ·", ctx.mono(f"dim{ctx.bg}", "dim"))]


def render_provider_plain(ctx: ColumnContext) -> Optional[str]:
    if not ctx.any_has_provider:
        return None
    provider = getattr(ctx.session, 'provider', 'web') or 'web'
    return provider


# Short badges for the backend column — keeps the row width at 3 chars
# regardless of how long a backend's name is.
BACKEND_BADGES = {
    "claude-code": "cc",
    "opencode": "oc",
    "codex": "cx",
    "grok": "gk",
}


def _backend_badge(name: str) -> str:
    return BACKEND_BADGES.get(name, (name or "?")[:2])


def render_backend(ctx: ColumnContext) -> ColumnOutput:
    """Agent CLI badge (cc / oc). Hidden unless the fleet is mixed."""
    from .backends import DEFAULT_BACKEND, session_backend_name
    name = session_backend_name(ctx.session)
    badge = _backend_badge(name)
    if name == DEFAULT_BACKEND:
        return [(f" {badge}", ctx.mono(f"dim{ctx.bg}", "dim"))]
    return [(f" {badge}", ctx.mono(f"bold green{ctx.bg}", "bold"))]


def render_backend_plain(ctx: ColumnContext) -> Optional[str]:
    # Mirrors the widget gate: silent for a single-backend fleet. `overcode
    # show` prints the backend unconditionally via its own line instead.
    if not ctx.mixed_backends:
        return None
    from .backends import session_backend_name
    return session_backend_name(ctx.session)


def render_host(ctx: ColumnContext) -> ColumnOutput:
    """Render host column — hidden when no sisters are configured."""
    if not ctx.has_sisters:
        return None
    host = ctx.source_host or ctx.local_hostname
    width = 14
    label = " " + host[:width].ljust(width)
    if ctx.is_remote:
        return [(label, ctx.mono(f"bold magenta{ctx.bg}", "bold"))]
    else:
        return [(label, ctx.mono(f"dim{ctx.bg}", "dim"))]


def render_host_plain(ctx: ColumnContext) -> Optional[str]:
    if not ctx.has_sisters:
        return None
    return ctx.source_host or ctx.local_hostname


# ---------------------------------------------------------------------------
# Ordered column list — reorder by moving items
# ---------------------------------------------------------------------------
SUMMARY_COLUMNS: List[SummaryColumn] = [
    # Identity group — always visible
    SummaryColumn(id="status_symbol", group="identity", detail_levels=ALL, render=render_status_symbol,
                  label="Status", render_plain=render_status_plain, name="Status"),
    SummaryColumn(id="unvisited_alert", group="identity", detail_levels=ALL, render=render_unvisited_alert,
                  name="Alert"),
    SummaryColumn(id="time_in_state", group="identity", detail_levels=ALL, render=render_time_in_state,
                  header="ST", name="Time in State"),
    SummaryColumn(id="sleep_countdown", group="identity", detail_levels=ALL, render=render_sleep_countdown,
                  visible=lambda ctx: ctx.any_is_sleeping or ctx.any_has_status_detail,
                  header="DTL", name="Status Detail"),
    SummaryColumn(id="expand_icon", group="identity", detail_levels=ALL, render=render_expand_icon,
                  name="Expand"),
    SummaryColumn(id="agent_name", group="identity", detail_levels=ALL, render=render_agent_name,
                  name="Agent Name"),
    SummaryColumn(id="host", group="sisters", detail_levels=ALL, render=render_host,
                  label="Host", render_plain=render_host_plain, header="HST", name="Host"),

    # Git group — repo, branch (high/full only), diff stats
    SummaryColumn(id="repo_name", group="git", detail_levels=HIGH_PLUS, render=render_repo_name,
                  label="Repo", render_plain=render_repo_name_plain, header="RPO", name="Repo Name"),
    SummaryColumn(id="branch", group="git", detail_levels=HIGH_PLUS, render=render_branch,
                  label="Branch", render_plain=render_branch_plain, header="BR", name="Branch"),
    SummaryColumn(id="git_diff", group="git", detail_levels=ALL, render=render_git_diff,
                  label="Git", render_plain=render_git_diff_plain, header="GIT", name="Git Diff"),
    SummaryColumn(id="git_untracked", group="git", detail_levels=HIGH_PLUS, render=render_git_untracked,
                  label="Untracked", render_plain=render_git_untracked_plain, header="UN",
                  name="Untracked Files"),
    SummaryColumn(id="pr_number", group="git", detail_levels=ALL, render=render_pr_number,
                  label="PR", render_plain=render_pr_number_plain,
                  visible=lambda ctx: ctx.any_has_pr, placeholder_width=8, header="PR", name="PR Number"),

    # Time group — uptime, running, stalled, sleep, active%
    SummaryColumn(id="uptime", group="time", detail_levels=MED_PLUS, render=render_uptime,
                  label="Uptime", render_plain=render_uptime_plain, header="UPT", name="Uptime"),
    SummaryColumn(id="running_time", group="time", detail_levels=MED_PLUS, render=render_running_time,
                  header="RUN", name="Running Time"),
    SummaryColumn(id="stalled_time", group="time", detail_levels=MED_PLUS, render=render_stalled_time,
                  header="STL", name="Stalled Time"),
    SummaryColumn(id="sleep_time", group="time", detail_levels=MED_PLUS, render=render_sleep_time,
                  header="ZZZ", name="Sleep Time"),
    SummaryColumn(id="active_pct", group="time", detail_levels=HIGH_PLUS, render=render_active_pct,
                  header="ACT", name="Active %"),
    # Synthetic CLI-only: combined time line
    SummaryColumn(id="time_combined", group="time", detail_levels=set(), render=lambda ctx: None,
                  label="Time", render_plain=render_time_plain, cli_only=True),

    # LLM usage group — TOK, ENRG, $, BDG, SUB$ (energy before cost for readability)
    SummaryColumn(id="token_count", group="llm_usage", detail_levels=ALL, render=render_token_count,
                  label="Tokens", render_plain=render_token_count_plain, header="TOK", name="Token Count"),
    SummaryColumn(id="joules", group="llm_usage", detail_levels=set(), render=render_joules,
                  header="ENRG", name="Energy (Joules)"),
    SummaryColumn(id="cost", group="llm_usage", detail_levels=set(), render=render_cost,
                  label="Cost", render_plain=render_cost_plain, header="$", name="Cost"),
    SummaryColumn(id="budget", group="llm_usage", detail_levels=set(), render=render_budget,
                  visible=lambda ctx: ctx.any_has_budget, placeholder_width=7,
                  header="BDG", name="Budget"),
    SummaryColumn(id="subtree_cost", group="llm_usage", detail_levels=set(),
                  render=render_subtree_cost, label="Subtree",
                  render_plain=render_subtree_cost_plain,
                  visible=lambda ctx: ctx.any_has_subtree_cost,
                  placeholder_width=8, header="SUB$", name="Subtree Cost"),
    # Burn rate over the timeline window (#174). Hidden until at least one
    # agent has window-scoped activity, so the column doesn't take width
    # when burn data isn't being computed (TUI not running, no recent tokens).
    SummaryColumn(id="burn_rate", group="llm_usage", detail_levels=HIGH_PLUS,
                  render=render_burn_rate, label="Burn",
                  render_plain=render_burn_rate_plain,
                  visible=lambda ctx: ctx.any_has_burn,
                  placeholder_width=9, header="BURN", name="Burn Rate"),

    # Context group — always visible, independent of $ toggle
    SummaryColumn(id="context_usage", group="context", detail_levels=ALL, render=render_context_usage,
                  header="CTX", name="Context Usage"),
    SummaryColumn(id="model", group="context", detail_levels=ALL, render=render_model,
                  label="Model", render_plain=render_model_plain,
                  visible=lambda ctx: ctx.any_has_model,
                  placeholder_width=6, header="MDL", name="Model"),
    SummaryColumn(id="provider", group="context", detail_levels=ALL, render=render_provider,
                  label="Provider", render_plain=render_provider_plain,
                  visible=lambda ctx: ctx.any_has_provider,
                  placeholder_width=3, header="PRV", name="Provider"),
    SummaryColumn(id="backend", group="context", detail_levels=ALL, render=render_backend,
                  label="Backend", render_plain=render_backend_plain,
                  visible=lambda ctx: ctx.mixed_backends,
                  placeholder_width=3, header="BKD", name="Agent Backend"),

    # Performance group — median work, CPU, RAM
    SummaryColumn(id="median_work_time", group="performance", detail_levels=MED_PLUS, render=render_median_work_time,
                  header="MED", name="Median Work Time"),
    SummaryColumn(id="cpu_pct", group="performance", detail_levels=HIGH_PLUS, render=render_cpu_pct,
                  label="CPU", render_plain=render_cpu_pct_plain,
                  visible=lambda ctx: ctx.any_has_cpu,
                  placeholder_width=6, header="CPU", name="CPU %"),
    SummaryColumn(id="ram", group="performance", detail_levels=HIGH_PLUS, render=render_ram,
                  label="RAM", render_plain=render_ram_plain,
                  visible=lambda ctx: ctx.any_has_ram,
                  placeholder_width=6, header="RAM", name="Memory (RSS)"),

    # Subprocesses group
    SummaryColumn(id="subagent_count", group="subprocesses", detail_levels=HIGH_PLUS, render=render_subagent_count,
                  header="SUB", name="Subagent Count"),
    SummaryColumn(id="bash_count", group="subprocesses", detail_levels=HIGH_PLUS, render=render_bash_count,
                  header="SH", name="Bash Count"),
    SummaryColumn(id="child_count", group="subprocesses", detail_levels=HIGH_PLUS, render=render_child_count,
                  header="CH", name="Child Count"),
    # Synthetic CLI-only: combined work + interactions line
    SummaryColumn(id="work_combined", group="performance", detail_levels=set(), render=lambda ctx: None,
                  label="Work", render_plain=render_work_plain, cli_only=True),
    # Synthetic CLI-only: combined agents line
    SummaryColumn(id="agents_combined", group="subprocesses", detail_levels=set(), render=lambda ctx: None,
                  label="Agents", render_plain=render_agents_plain, cli_only=True),

    # Supervision group
    SummaryColumn(id="permission_mode", group="supervision", detail_levels=ALL, render=render_permission_mode,
                  label="Mode", render_plain=render_mode_plain, header="MOD", name="Permission Mode"),
    SummaryColumn(id="agent_teams", group="supervision", detail_levels=ALL, render=render_agent_teams,
                  label="Teams", render_plain=render_teams_plain, header="TM", name="Teams"),
    SummaryColumn(id="wrapper", group="supervision", detail_levels=ALL, render=render_wrapper,
                  label="Wrapper", render_plain=render_wrapper_plain, header="WRP", name="Wrapper"),
    SummaryColumn(id="allowed_tools", group="supervision", detail_levels=ALL, render=render_allowed_tools,
                  label="Tools", render_plain=render_tools_plain, header="TLS", name="Allowed Tools"),
    SummaryColumn(id="loaded_skills", group="supervision", detail_levels=ALL, render=render_loaded_skills,
                  label="Loaded Skills", render_plain=render_skills_plain, header="SKL", name="Loaded Skills"),
    SummaryColumn(id="available_skills", group="supervision", detail_levels=ALL, render=render_available_skills,
                  label="Available Skills", render_plain=render_available_skills_plain, header="ASK", name="Available Skills"),
    SummaryColumn(id="enhanced_context", group="supervision", detail_levels=ALL, render=render_enhanced_context,
                  header="EC", name="Enhanced Context"),
    SummaryColumn(id="human_count", group="supervision", detail_levels=ALL, render=render_human_count,
                  header="H#", name="Human Count"),
    SummaryColumn(id="robot_count", group="supervision", detail_levels=ALL, render=render_robot_count,
                  header="R#", name="Robot Count"),
    SummaryColumn(id="standing_orders", group="supervision", detail_levels=ALL, render=render_standing_orders,
                  label="Orders", render_plain=render_orders_plain, header="ORD", name="Standing Orders"),
    SummaryColumn(id="heartbeat", group="supervision", detail_levels=ALL, render=render_heartbeat,
                  label="Heartbeat", render_plain=render_heartbeat_plain, header="HB", name="Heartbeat"),
    SummaryColumn(id="oversight_countdown", group="supervision", detail_levels=ALL, render=render_oversight_countdown,
                  visible=lambda ctx: ctx.any_has_oversight_timeout, placeholder_width=8,
                  header="OVR", name="Oversight Countdown"),

    # Priority group
    SummaryColumn(id="agent_value", group="priority", detail_levels=ALL, render=render_agent_value,
                  label="Value", render_plain=render_value_plain, header="VAL", name="Agent Value"),
]


# ---------------------------------------------------------------------------
# CLI rendering helpers
# ---------------------------------------------------------------------------

def build_cli_context(
    session, stats, claude_stats, git_diff_stats,
    status: str, bg_bash_count: int, live_sub_count: int,
    git_untracked_count: Optional[int] = None,
    auto_accept_mode: bool = False,
    any_has_budget: bool = False, child_count: int = 0, any_is_sleeping: bool = False,
    any_has_oversight_timeout: bool = False, oversight_deadline: Optional[str] = None,
    pr_number: Optional[int] = None, any_has_pr: bool = False,
    any_has_model: bool = False,
    any_has_provider: bool = False,
    mixed_backends: bool = False,
    any_has_cpu: bool = False, any_has_ram: bool = False,
    monochrome: bool = True, emoji_free: bool = False, summary_detail: str = "full",
    has_sisters: bool = False, local_hostname: str = "",
    max_name_width: int = 16, max_repo_width: int = 10,
    max_branch_width: int = 10, all_names_match_repos: bool = False,
    subtree_cost_usd: float = 0.0, any_has_subtree_cost: bool = False,
    status_detail: Optional[StatusDetail] = None,
    any_has_status_detail: bool = False,
    window_burn: Optional[object] = None,
    any_has_burn: bool = False,
) -> ColumnContext:
    """Build a ColumnContext from CLI data (no TUI widget needed)."""
    status_symbol, _ = get_status_symbol(status, emoji_free=emoji_free)
    uptime = calculate_uptime(session.start_time) if session.start_time else "-"
    green_time, non_green_time, sleep_time = get_current_state_times(
        stats, is_asleep=session.is_asleep
    )
    median_work = claude_stats.median_work_time if claude_stats else 0.0
    effective_perm = session.permissiveness_mode
    if auto_accept_mode and effective_perm == "normal":
        effective_perm = "auto"
    perm_emoji = get_permissiveness_emoji(effective_perm, emoji_free)

    # Parse state_since for time-in-state
    status_changed_at = None
    if stats.state_since:
        try:
            status_changed_at = datetime.fromisoformat(stats.state_since)
        except (ValueError, TypeError):
            pass

    # Compute sleep wake estimate (#289)
    sleep_wake_estimate = None
    if status == "busy_sleeping" and status_changed_at:
        dur = extract_sleep_duration(getattr(stats, 'current_task', '') or '')
        if dur:
            sleep_wake_estimate = status_changed_at + timedelta(seconds=dur)

    return ColumnContext(
        session=session,
        stats=stats,
        claude_stats=claude_stats,
        git_diff_stats=git_diff_stats,
        git_untracked_count=git_untracked_count,
        auto_accept_mode=auto_accept_mode,
        status_symbol=status_symbol,
        status_color="bold",
        bg="",
        monochrome=monochrome,
        emoji_free=emoji_free,
        summary_detail=summary_detail,
        show_cost="cost",
        any_has_budget=any_has_budget,
        expand_icon="",
        is_list_mode=False,
        is_compact_mode=False,
        has_focus=False,
        is_unvisited_stalled=False,
        uptime=uptime,
        green_time=green_time,
        non_green_time=non_green_time,
        sleep_time=sleep_time,
        median_work=median_work,
        repo_name=session.repo_name or "-",
        branch=session.branch or "-",
        display_name=session.name,
        perm_emoji=perm_emoji,
        all_names_match_repos=all_names_match_repos,
        live_subagent_count=live_sub_count,
        background_bash_count=bg_bash_count,
        child_count=child_count,
        status_changed_at=status_changed_at,
        max_name_width=max_name_width,
        max_repo_width=max_repo_width,
        max_branch_width=max_branch_width,
        any_has_oversight_timeout=any_has_oversight_timeout,
        oversight_deadline=oversight_deadline,
        any_is_sleeping=any_is_sleeping,
        sleep_wake_estimate=sleep_wake_estimate,
        status_detail=status_detail,
        any_has_status_detail=any_has_status_detail,
        pr_number=pr_number,
        any_has_pr=any_has_pr,
        model=getattr(session, 'model', '') or '',
        any_has_model=any_has_model,
        any_has_provider=any_has_provider,
        mixed_backends=mixed_backends,
        any_has_cpu=any_has_cpu,
        any_has_ram=any_has_ram,
        source_host=getattr(session, 'source_host', ''),
        is_remote=getattr(session, 'is_remote', False),
        has_sisters=has_sisters,
        local_hostname=local_hostname,
        subtree_cost_usd=subtree_cost_usd,
        any_has_subtree_cost=any_has_subtree_cost,
        window_burn=window_burn,
        any_has_burn=any_has_burn,
    )


def resolve_column_visible(col: SummaryColumn, level: str, overrides: dict) -> bool:
    """Determine if a column is visible at a given level with user overrides.

    - full: default True for all columns, but explicit False overrides still apply
    - low/med/high: start from col.detail_levels default, then apply overrides
    """
    base = True if level == "full" else level in col.detail_levels
    col_override = overrides.get(col.id)
    if col_override is not None:
        return col_override
    return base


def render_summary_cells(
    ctx: ColumnContext,
    column_filter: Optional[Callable[[SummaryColumn], bool]] = None,
    group_filter: Optional[Callable[[str], bool]] = None,
) -> "List[Text]":
    """Render each visible column as a separate Text cell.

    Returns one Text per visible column position (including placeholders).
    Used by the CLI for automatic cross-row alignment via align_summary_rows().
    The TUI calls render_summary_line() which concatenates these.

    Args:
        ctx: Pre-computed column context.
        column_filter: Optional callback to check column visibility. Takes a
            SummaryColumn and returns bool. When provided, replaces the default
            detail_levels check and group_filter.
        group_filter: Legacy callback to check group visibility. Ignored when
            column_filter is provided.
    """
    from rich.text import Text
    cells = []
    for col in SUMMARY_COLUMNS:
        if column_filter is not None:
            if not column_filter(col):
                continue
        else:
            if ctx.summary_detail not in col.detail_levels:
                continue
            if group_filter is not None and not group_filter(col.group):
                continue
        cell = Text()
        if col.visible is not None and not col.visible(ctx):
            if col.placeholder_width > 0:
                cell.append(" " * col.placeholder_width, style=ctx.mono(f"dim{ctx.bg}", "dim"))
            cells.append(cell)
            continue
        segments = col.render(ctx)
        if segments:
            for text, style in segments:
                cell.append(text, style=style)
        elif col.placeholder_width > 0:
            cell.append(" " * col.placeholder_width, style=ctx.mono(f"dim{ctx.bg}", "dim"))
        cells.append(cell)
    return cells


def render_summary_line(
    ctx: ColumnContext,
    column_filter: Optional[Callable[[SummaryColumn], bool]] = None,
    group_filter: Optional[Callable[[str], bool]] = None,
) -> "Text":
    """Render a single summary line from SUMMARY_COLUMNS.

    This is the canonical render loop — both TUI and CLI call this.
    Concatenates cells from render_summary_cells() into a single Text.
    """
    from rich.text import Text
    content = Text()
    for cell in render_summary_cells(ctx, column_filter=column_filter, group_filter=group_filter):
        content.append_text(cell)
    return content


def compute_column_widths(cell_rows: "List[List[Text]]") -> "List[int]":
    """Compute max visual width per column across all rows.

    Args:
        cell_rows: List of rows, each a list of Text cells from render_summary_cells().

    Returns:
        List of max visual widths, one per column position.
    """
    from rich.cells import cell_len
    if not cell_rows:
        return []
    n_cols = max(len(row) for row in cell_rows)
    max_widths = [0] * n_cols
    for row in cell_rows:
        for i, cell in enumerate(row):
            w = cell_len(cell.plain)
            if w > max_widths[i]:
                max_widths[i] = w
    return max_widths


def pad_and_join_cells(cells: "List[Text]", column_widths: "List[int]", pad_style: str = "") -> "Text":
    """Pad one row's cells to the given column widths and join into a single Text.

    This is the shared alignment function used by both TUI and CLI.

    Args:
        pad_style: Style applied to padding spaces (e.g. "on #0d2137" for TUI background).
    """
    from rich.cells import cell_len
    from rich.text import Text
    line = Text()
    for i, cell in enumerate(cells):
        line.append_text(cell)
        if i < len(column_widths):
            pad = column_widths[i] - cell_len(cell.plain)
            if pad > 0:
                line.append(" " * pad, style=pad_style)
    return line


def align_summary_rows(cell_rows: "List[List[Text]]") -> "List[Text]":
    """Convenience: compute column widths and pad all rows in one call.

    Used by the CLI which has all rows available at once.
    """
    if not cell_rows:
        return []
    widths = compute_column_widths(cell_rows)
    return [pad_and_join_cells(row, widths) for row in cell_rows]


def render_header_cells(
    column_filter: Optional[Callable[[SummaryColumn], bool]] = None,
    column_widths: Optional["List[int]"] = None,
) -> "Text":
    """Render a column header row using short header labels.

    Args:
        column_filter: Same filter used for data rows — ensures headers align.
        column_widths: Per-column widths from compute_column_widths(). Headers
            are truncated to fit but never contribute to width computation.

    Returns:
        A single Text line with dim-styled abbreviated headers.
    """
    from rich.text import Text
    from rich.cells import cell_len
    line = Text()
    col_idx = 0
    for col in SUMMARY_COLUMNS:
        if column_filter is not None:
            if not column_filter(col):
                continue
        header = " " + col.header if col.header else ""
        if column_widths and col_idx < len(column_widths):
            w = column_widths[col_idx]
            # Truncate header to column width
            if cell_len(header) > w:
                header = header[:w]
            # Pad to column width
            pad = w - cell_len(header)
            line.append(header, style="dim")
            if pad > 0:
                line.append(" " * pad, style="dim")
        else:
            line.append(header, style="dim")
        col_idx += 1
    return line


def render_cli_stats(ctx: ColumnContext) -> List[Tuple[str, str]]:
    """Render columns as CLI key-value lines from SUMMARY_COLUMNS.

    Iterates SUMMARY_COLUMNS. For each column with a render_plain,
    calls it and collects (label, value). Columns sharing a label
    are merged onto one line separated by "  ".

    Returns list of (label, value) tuples for print formatting.
    """
    result: List[Tuple[str, str]] = []
    seen_labels: dict = {}  # label -> index in result

    for col in SUMMARY_COLUMNS:
        if not col.label or col.render_plain is None:
            continue
        value = col.render_plain(ctx)
        if value is None:
            continue
        if col.label in seen_labels:
            idx = seen_labels[col.label]
            old_label, old_value = result[idx]
            result[idx] = (old_label, f"{old_value}  {value}")
        else:
            seen_labels[col.label] = len(result)
            result.append((col.label, value))

    return result
