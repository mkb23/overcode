"""
Declarative summary column definitions for the TUI.

Each column is a SummaryColumn with an ID, group, detail-level gate,
and a render function that produces styled text segments. The render()
loop in SessionSummary iterates SUMMARY_COLUMNS in order, skipping
columns whose detail level or group is not active.

Plain-text rendering for CLI: each column can optionally provide a
``label`` and ``render_plain`` function for use by ``overcode show``.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, List, Optional, Tuple

from .tui_helpers import (
    format_cost,
    format_duration,
    format_line_count,
    format_tokens,
    format_budget,
    calculate_uptime,
    get_current_state_times,
    get_status_symbol,
)


# ---------------------------------------------------------------------------
# Type alias for column output: list of (text, style) segments, or None to skip
# ---------------------------------------------------------------------------
ColumnOutput = Optional[List[Tuple[str, str]]]


# ---------------------------------------------------------------------------
# Detail-level convenience sets
# ---------------------------------------------------------------------------
ALL = {"low", "med", "full", "custom"}
MED_PLUS = {"med", "full", "custom"}
FULL_PLUS = {"full", "custom"}


# ---------------------------------------------------------------------------
# ColumnContext: all pre-computed data a column needs
# ---------------------------------------------------------------------------
@dataclass
class ColumnContext:
    """Pre-computed data passed to every column render function."""

    # Session and stats
    session: object  # Session
    stats: object  # SessionStats
    claude_stats: object  # Optional[ClaudeSessionStats]
    git_diff_stats: Optional[tuple]  # (files, insertions, deletions) or None

    # Pre-computed values
    status_symbol: str
    status_color: str
    bg: str  # background style suffix, e.g. " on #0d2137" or ""
    monochrome: bool
    summary_detail: str
    show_cost: bool
    any_has_budget: bool  # True if any agent has a cost budget (#173)
    expand_icon: str
    is_list_mode: bool
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

    # Sister integration (#245)
    source_host: str = ""
    is_remote: bool = False
    has_sisters: bool = False
    local_hostname: str = ""

    def mono(self, colored: str, simple: str = "bold") -> str:
        """Return simplified style when monochrome is enabled."""
        return simple if self.monochrome else colored


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


# ---------------------------------------------------------------------------
# Render functions — each returns list of (text, style) or None
# ---------------------------------------------------------------------------

def render_status_symbol(ctx: ColumnContext) -> ColumnOutput:
    # Most status emojis are 2 cells wide; some (☑️) are 1 cell.
    # Pad narrow symbols with an extra space so columns stay aligned.
    import unicodedata
    symbol = ctx.status_symbol
    cell_width = sum(
        2 if unicodedata.east_asian_width(c) in ('W', 'F') else (0 if c == '\ufe0f' else 1)
        for c in symbol
    )
    pad = " " * (2 - cell_width) if cell_width < 2 else ""
    return [(f"{symbol}{pad} ", ctx.status_color)]


def render_unvisited_alert(ctx: ColumnContext) -> ColumnOutput:
    if ctx.is_unvisited_stalled:
        return [("🔔", ctx.mono(f"bold blink red{ctx.bg}", "bold"))]
    else:
        return [("  ", ctx.mono(f"dim{ctx.bg}", "dim"))]


def render_time_in_state(ctx: ColumnContext) -> ColumnOutput:
    state_start = ctx.status_changed_at
    if ctx.stats.state_since:
        try:
            daemon_state_start = datetime.fromisoformat(ctx.stats.state_since)
            if state_start is None or daemon_state_start > state_start:
                state_start = daemon_state_start
        except (ValueError, TypeError):
            pass
    if state_start:
        elapsed = (datetime.now() - state_start).total_seconds()
        return [(f"{format_duration(elapsed):>5} ", ctx.status_color)]
    else:
        return [("    - ", ctx.mono(f"dim{ctx.bg}", "dim"))]


def render_expand_icon(ctx: ColumnContext) -> ColumnOutput:
    if ctx.is_list_mode:
        if ctx.has_focus:
            return [("→ ", ctx.status_color)]
        else:
            return [("  ", ctx.status_color)]
    else:
        return [(f"{ctx.expand_icon} ", ctx.status_color)]


def render_agent_name(ctx: ColumnContext) -> ColumnOutput:
    return [(ctx.display_name, ctx.mono(f"bold cyan{ctx.bg}", "bold"))]


def render_repo_name(ctx: ColumnContext) -> ColumnOutput:
    if ctx.all_names_match_repos:
        return None
    w = ctx.max_repo_width
    return [(f" {ctx.repo_name:<{w}}", ctx.mono(f"bold dim{ctx.bg}", "dim"))]


def render_branch(ctx: ColumnContext) -> ColumnOutput:
    w = ctx.max_branch_width
    sep = ":" if not ctx.all_names_match_repos else " "
    return [(f"{sep}{ctx.branch:<{w}} ", ctx.mono(f"bold dim{ctx.bg}", "dim"))]


def render_uptime(ctx: ColumnContext) -> ColumnOutput:
    return [(f" ↑{ctx.uptime:>5}", ctx.mono(f"bold white{ctx.bg}", "bold"))]


def render_running_time(ctx: ColumnContext) -> ColumnOutput:
    return [(f" ▶{format_duration(ctx.green_time):>5}", ctx.mono(f"bold green{ctx.bg}", "bold"))]


def render_stalled_time(ctx: ColumnContext) -> ColumnOutput:
    return [(f" ⏸{format_duration(ctx.non_green_time):>5}", ctx.mono(f"bold red{ctx.bg}", "dim"))]


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
    """Token count (Σ123K). Hidden when show_cost=True."""
    if ctx.show_cost:
        return None
    if ctx.claude_stats is not None:
        return [(f" Σ{format_tokens(ctx.claude_stats.total_tokens):>6}", ctx.mono(f"bold orange1{ctx.bg}", "bold"))]
    else:
        return [("       -", ctx.mono(f"dim orange1{ctx.bg}", "dim"))]


def render_context_usage(ctx: ColumnContext) -> ColumnOutput:
    """Context window usage (📚XX%). Always visible."""
    if ctx.claude_stats is not None:
        if ctx.claude_stats.current_context_tokens > 0:
            max_context = 200_000
            ctx_pct = min(100, ctx.claude_stats.current_context_tokens / max_context * 100)
            return [(f" 📚{ctx_pct:>3.0f}%", ctx.mono(f"bold orange1{ctx.bg}", "bold"))]
        else:
            return [(" 📚  -%", ctx.mono(f"dim orange1{ctx.bg}", "dim"))]
    else:
        return [(" 📚  -%", ctx.mono(f"dim orange1{ctx.bg}", "dim"))]


def render_cost(ctx: ColumnContext) -> ColumnOutput:
    """Dollar cost. Hidden when show_cost=False."""
    if not ctx.show_cost:
        return None
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


def render_budget(ctx: ColumnContext) -> ColumnOutput:
    """Budget amount. Hidden when show_cost=False or no session has a budget."""
    if not ctx.show_cost:
        return None
    if not ctx.any_has_budget:
        return None
    s = ctx.session
    if s.cost_budget_usd > 0:
        return [(f"/{format_cost(s.cost_budget_usd):>6}", ctx.mono(f"dim orange1{ctx.bg}", "dim"))]
    else:
        return [("       ", ctx.mono(f"dim{ctx.bg}", "dim"))]


# Backward-compat alias
render_tokens = render_token_count


def render_git_diff(ctx: ColumnContext) -> ColumnOutput:
    if ctx.git_diff_stats:
        files, ins, dels = ctx.git_diff_stats
        if ctx.summary_detail in ("full", "custom"):
            return [
                (f" Δ{files:>2}", ctx.mono(f"bold magenta{ctx.bg}", "bold")),
                (f" +{format_line_count(ins):>5}", ctx.mono(f"bold green{ctx.bg}", "bold")),
                (f" -{format_line_count(dels):>5}", ctx.mono(f"bold red{ctx.bg}", "dim")),
            ]
        else:
            has_changes = files > 0
            return [(f" Δ{files:>2}", ctx.mono(f"bold magenta{ctx.bg}" if has_changes else f"dim{ctx.bg}", "bold" if has_changes else "dim"))]
    else:
        if ctx.summary_detail in ("full", "custom"):
            return [("  Δ-  +    -  -   ", ctx.mono(f"dim{ctx.bg}", "dim"))]
        else:
            return [("  Δ-", ctx.mono(f"dim{ctx.bg}", "dim"))]


def render_median_work_time(ctx: ColumnContext) -> ColumnOutput:
    work_str = format_duration(ctx.median_work) if ctx.median_work > 0 else "0s"
    return [(f" ⏱{work_str:>5}", ctx.mono(f"bold blue{ctx.bg}", "bold"))]


def render_subagent_count(ctx: ColumnContext) -> ColumnOutput:
    count = ctx.live_subagent_count
    style = ctx.mono(f"bold purple{ctx.bg}", "bold") if count > 0 else ctx.mono(f"dim{ctx.bg}", "dim")
    return [(f" 🤿{count:>2}", style)]


def render_bash_count(ctx: ColumnContext) -> ColumnOutput:
    count = ctx.background_bash_count
    style = ctx.mono(f"bold yellow{ctx.bg}", "bold") if count > 0 else ctx.mono(f"dim{ctx.bg}", "dim")
    return [(f" 🐚{count:>2}", style)]


def render_child_count(ctx: ColumnContext) -> ColumnOutput:
    count = ctx.child_count
    if count == 0:
        return [(f" 👶 0", ctx.mono(f"dim{ctx.bg}", "dim"))]
    style = ctx.mono(f"bold cyan{ctx.bg}", "bold")
    return [(f" 👶{count:>2}", style)]


def render_permission_mode(ctx: ColumnContext) -> ColumnOutput:
    return [(f" {ctx.perm_emoji}", ctx.mono(f"bold white{ctx.bg}", "bold"))]


def render_time_context(ctx: ColumnContext) -> ColumnOutput:
    if ctx.session.time_context_enabled:
        return [(" 🕐", ctx.mono(f"bold white{ctx.bg}", "bold"))]
    else:
        return [("  ·", ctx.mono(f"dim{ctx.bg}", "dim"))]


def render_human_count(ctx: ColumnContext) -> ColumnOutput:
    if ctx.claude_stats is not None:
        human_count = max(0, ctx.claude_stats.interaction_count - ctx.stats.steers_count)
        return [(f" 👤{human_count:>3}", ctx.mono(f"bold yellow{ctx.bg}", "bold"))]
    else:
        return [(" 👤  -", ctx.mono(f"dim yellow{ctx.bg}", "dim"))]


def render_robot_count(ctx: ColumnContext) -> ColumnOutput:
    return [(f" 🤖{ctx.stats.steers_count:>3}", ctx.mono(f"bold cyan{ctx.bg}", "bold"))]


def render_standing_orders(ctx: ColumnContext) -> ColumnOutput:
    s = ctx.session
    if s.standing_instructions:
        if s.standing_orders_complete:
            return [(" ✓", ctx.mono(f"bold green{ctx.bg}", "bold"))]
        elif s.standing_instructions_preset:
            preset_display = f" {s.standing_instructions_preset[:8]}"
            return [(preset_display, ctx.mono(f"bold cyan{ctx.bg}", "bold"))]
        else:
            return [(" 📋", ctx.mono(f"bold yellow{ctx.bg}", "bold"))]
    else:
        return [(" ➖", ctx.mono(f"bold dim{ctx.bg}", "dim"))]


def render_oversight_countdown(ctx: ColumnContext) -> ColumnOutput:
    """Oversight countdown timer. Hidden if no agent has a timeout."""
    if not ctx.any_has_oversight_timeout:
        return None

    from .status_constants import STATUS_WAITING_OVERSIGHT
    status = ctx.stats.current_state if hasattr(ctx.stats, 'current_state') else ""

    # Check session status as well (session object may have the status)
    session_status = getattr(ctx.session, 'status', '')
    is_oversight = session_status == STATUS_WAITING_OVERSIGHT or status == STATUS_WAITING_OVERSIGHT

    if not is_oversight:
        return [("        ", ctx.mono(f"dim{ctx.bg}", "dim"))]

    deadline_str = ctx.oversight_deadline
    if not deadline_str:
        return [(" ⏳ --:--", ctx.mono(f"yellow{ctx.bg}", "dim"))]

    try:
        deadline = datetime.fromisoformat(deadline_str)
        remaining = (deadline - datetime.now()).total_seconds()
        if remaining <= 0:
            return [(" ⏳ 0s  ", ctx.mono(f"bold blink red{ctx.bg}", "bold"))]

        if remaining < 60:
            text = f" ⏳ {remaining:>3.0f}s"
        elif remaining < 3600:
            mins = int(remaining // 60)
            secs = int(remaining % 60)
            text = f" ⏳{mins:>2}m{secs:02d}s"
        else:
            hrs = int(remaining // 3600)
            mins = int((remaining % 3600) // 60)
            text = f" ⏳{hrs:>2}h{mins:02d}m"

        if remaining < 30:
            style = ctx.mono(f"bold blink red{ctx.bg}", "bold")
        elif remaining < 60:
            style = ctx.mono(f"bold red{ctx.bg}", "bold")
        else:
            style = ctx.mono(f"bold yellow{ctx.bg}", "bold")
        return [(text, style)]
    except (ValueError, TypeError):
        return [(" ⏳ --:--", ctx.mono(f"dim{ctx.bg}", "dim"))]


def render_heartbeat(ctx: ColumnContext) -> ColumnOutput:
    s = ctx.session
    if s.heartbeat_enabled and not s.heartbeat_paused:
        freq_str = format_duration(s.heartbeat_frequency_seconds)
        segments = [(f" 💓{freq_str:>5}", ctx.mono(f"bold magenta{ctx.bg}", "bold"))]
        # Next heartbeat time in 24hr format
        next_time_str = None
        if s.last_heartbeat_time:
            try:
                last_hb = datetime.fromisoformat(s.last_heartbeat_time)
                next_due = last_hb + timedelta(seconds=s.heartbeat_frequency_seconds)
                next_time_str = next_due.strftime("%H:%M")
            except (ValueError, TypeError):
                pass
        if next_time_str is None and s.start_time:
            try:
                start = datetime.fromisoformat(s.start_time)
                next_due = start + timedelta(seconds=s.heartbeat_frequency_seconds)
                next_time_str = next_due.strftime("%H:%M")
            except (ValueError, TypeError):
                pass
        if next_time_str:
            segments.append((f" @{next_time_str}", ctx.mono(f"bold cyan{ctx.bg}", "bold")))
        else:
            segments.append((" @--:--", ctx.mono(f"dim{ctx.bg}", "dim")))
        return segments
    elif s.heartbeat_enabled and s.heartbeat_paused:
        freq_str = format_duration(s.heartbeat_frequency_seconds)
        return [
            (f" 💓{freq_str:>5}", ctx.mono(f"dim{ctx.bg}", "dim")),
            ("    ⏸ ", ctx.mono(f"bold yellow{ctx.bg}", "bold")),
        ]
    else:
        return [(" 💓    - @--:--", ctx.mono(f"dim{ctx.bg}", "dim"))]


def render_agent_value(ctx: ColumnContext) -> ColumnOutput:
    s = ctx.session
    if ctx.summary_detail in ("full", "custom"):
        return [(f" 💰{s.agent_value:>4}", ctx.mono(f"bold magenta{ctx.bg}", "bold"))]
    else:
        if s.agent_value > 1000:
            return [(" ⏫️", ctx.mono(f"bold red{ctx.bg}", "bold"))]
        elif s.agent_value < 1000:
            return [(" ⏬️", ctx.mono(f"bold blue{ctx.bg}", "bold"))]
        else:
            return [(" ⏹️ ", ctx.mono(f"dim{ctx.bg}", "dim"))]


# ---------------------------------------------------------------------------
# Plain-text render functions for CLI output
# ---------------------------------------------------------------------------

def render_status_plain(ctx: ColumnContext) -> Optional[str]:
    """Status with time-in-state."""
    # Determine status name from the symbol
    s = ctx.session
    status = "unknown"
    # Reverse-lookup from status_symbol
    for candidate in ["running", "waiting_user", "waiting_tool", "thinking",
                       "terminated", "asleep", "stalled"]:
        sym, _ = get_status_symbol(candidate)
        if sym == ctx.status_symbol:
            status = candidate
            break

    state_start = ctx.status_changed_at
    if ctx.stats.state_since:
        try:
            daemon_state_start = datetime.fromisoformat(ctx.stats.state_since)
            if state_start is None or daemon_state_start > state_start:
                state_start = daemon_state_start
        except (ValueError, TypeError):
            pass

    time_str = ""
    if state_start:
        elapsed = (datetime.now() - state_start).total_seconds()
        time_str = f" ({format_duration(elapsed)})"

    return f"{ctx.status_symbol} {status}{time_str}"


def render_uptime_plain(ctx: ColumnContext) -> Optional[str]:
    return f"↑{ctx.uptime}"


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
    """Context window usage for CLI."""
    if ctx.claude_stats is None:
        return None
    if ctx.claude_stats.current_context_tokens > 0:
        ctx_pct = min(100, ctx.claude_stats.current_context_tokens / 200_000 * 100)
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


def render_branch_plain(ctx: ColumnContext) -> Optional[str]:
    return ctx.branch


def render_mode_plain(ctx: ColumnContext) -> Optional[str]:
    perm_map = {"bypass": "🔥 bypass", "permissive": "🏃 permissive", "normal": "👮 normal"}
    mode = perm_map.get(ctx.session.permissiveness_mode, ctx.session.permissiveness_mode)
    tc = "🕐 enabled" if ctx.session.time_context_enabled else "disabled"
    return f"{mode}  Time ctx: {tc}"


def render_heartbeat_plain(ctx: ColumnContext) -> Optional[str]:
    s = ctx.session
    if not s.heartbeat_enabled:
        return "disabled"
    freq_str = format_duration(s.heartbeat_frequency_seconds)
    if s.heartbeat_paused:
        return f"💓 {freq_str} (paused)"
    next_time_str = None
    if s.last_heartbeat_time:
        try:
            last_hb = datetime.fromisoformat(s.last_heartbeat_time)
            next_due = last_hb + timedelta(seconds=s.heartbeat_frequency_seconds)
            next_time_str = next_due.strftime("%H:%M")
        except (ValueError, TypeError):
            pass
    if next_time_str is None and s.start_time:
        try:
            start = datetime.fromisoformat(s.start_time)
            next_due = start + timedelta(seconds=s.heartbeat_frequency_seconds)
            next_time_str = next_due.strftime("%H:%M")
        except (ValueError, TypeError):
            pass
    time_part = f" @{next_time_str}" if next_time_str else ""
    return f"💓 {freq_str}{time_part}"


def render_orders_plain(ctx: ColumnContext) -> Optional[str]:
    s = ctx.session
    if not s.standing_instructions:
        return None
    prefix = "✓ " if s.standing_orders_complete else ""
    return f"📋 {prefix}{s.standing_instructions[:80]}"


def render_value_plain(ctx: ColumnContext) -> Optional[str]:
    return str(ctx.session.agent_value)


def render_host(ctx: ColumnContext) -> ColumnOutput:
    """Render host column — hidden when no sisters are configured."""
    if not ctx.has_sisters:
        return None
    host = ctx.source_host or ctx.local_hostname
    width = 14
    label = host[:width].ljust(width)
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
                  label="Status", render_plain=render_status_plain),
    SummaryColumn(id="unvisited_alert", group="identity", detail_levels=ALL, render=render_unvisited_alert),
    SummaryColumn(id="time_in_state", group="identity", detail_levels=ALL, render=render_time_in_state),
    SummaryColumn(id="expand_icon", group="identity", detail_levels=ALL, render=render_expand_icon),
    SummaryColumn(id="agent_name", group="identity", detail_levels=ALL, render=render_agent_name),
    SummaryColumn(id="host", group="identity", detail_levels=ALL, render=render_host,
                  label="Host", render_plain=render_host_plain),

    # Git group — repo, branch (full/custom only), diff stats
    SummaryColumn(id="repo_name", group="git", detail_levels=FULL_PLUS, render=render_repo_name,
                  label="Repo", render_plain=render_repo_name_plain),
    SummaryColumn(id="branch", group="git", detail_levels=FULL_PLUS, render=render_branch,
                  label="Branch", render_plain=render_branch_plain),
    SummaryColumn(id="git_diff", group="git", detail_levels=ALL, render=render_git_diff,
                  label="Git", render_plain=render_git_diff_plain),

    # Time group — uptime, running, stalled, sleep, active%
    SummaryColumn(id="uptime", group="time", detail_levels=MED_PLUS, render=render_uptime,
                  label="Uptime", render_plain=render_uptime_plain),
    SummaryColumn(id="running_time", group="time", detail_levels=MED_PLUS, render=render_running_time),
    SummaryColumn(id="stalled_time", group="time", detail_levels=MED_PLUS, render=render_stalled_time),
    SummaryColumn(id="sleep_time", group="time", detail_levels=MED_PLUS, render=render_sleep_time),
    SummaryColumn(id="active_pct", group="time", detail_levels=FULL_PLUS, render=render_active_pct),
    # Synthetic CLI-only: combined time line
    SummaryColumn(id="time_combined", group="time", detail_levels=set(), render=lambda ctx: None,
                  label="Time", render_plain=render_time_plain),

    # Budget group — token count, cost, budget ($-toggled); context always visible
    SummaryColumn(id="token_count", group="llm_usage", detail_levels=ALL, render=render_token_count,
                  label="Tokens", render_plain=render_token_count_plain),
    SummaryColumn(id="cost", group="llm_usage", detail_levels=ALL, render=render_cost,
                  label="Cost", render_plain=render_cost_plain),
    SummaryColumn(id="budget", group="llm_usage", detail_levels=ALL, render=render_budget),

    # Context group — always visible, independent of $ toggle
    SummaryColumn(id="context_usage", group="context", detail_levels=ALL, render=render_context_usage),

    # Performance group
    SummaryColumn(id="median_work_time", group="performance", detail_levels=MED_PLUS, render=render_median_work_time),

    # Subprocesses group
    SummaryColumn(id="subagent_count", group="subprocesses", detail_levels=FULL_PLUS, render=render_subagent_count),
    SummaryColumn(id="bash_count", group="subprocesses", detail_levels=FULL_PLUS, render=render_bash_count),
    SummaryColumn(id="child_count", group="subprocesses", detail_levels=FULL_PLUS, render=render_child_count),
    # Synthetic CLI-only: combined work + interactions line
    SummaryColumn(id="work_combined", group="performance", detail_levels=set(), render=lambda ctx: None,
                  label="Work", render_plain=render_work_plain),
    # Synthetic CLI-only: combined agents line
    SummaryColumn(id="agents_combined", group="subprocesses", detail_levels=set(), render=lambda ctx: None,
                  label="Agents", render_plain=render_agents_plain),

    # Supervision group
    SummaryColumn(id="permission_mode", group="supervision", detail_levels=ALL, render=render_permission_mode,
                  label="Mode", render_plain=render_mode_plain),
    SummaryColumn(id="time_context", group="supervision", detail_levels=ALL, render=render_time_context),
    SummaryColumn(id="human_count", group="supervision", detail_levels=ALL, render=render_human_count),
    SummaryColumn(id="robot_count", group="supervision", detail_levels=ALL, render=render_robot_count),
    SummaryColumn(id="standing_orders", group="supervision", detail_levels=ALL, render=render_standing_orders,
                  label="Orders", render_plain=render_orders_plain),
    SummaryColumn(id="heartbeat", group="supervision", detail_levels=ALL, render=render_heartbeat,
                  label="Heartbeat", render_plain=render_heartbeat_plain),
    SummaryColumn(id="oversight_countdown", group="supervision", detail_levels=ALL, render=render_oversight_countdown),

    # Priority group
    SummaryColumn(id="agent_value", group="priority", detail_levels=ALL, render=render_agent_value,
                  label="Value", render_plain=render_value_plain),
]


# ---------------------------------------------------------------------------
# CLI rendering helpers
# ---------------------------------------------------------------------------

def build_cli_context(
    session, stats, claude_stats, git_diff_stats,
    status: str, bg_bash_count: int, live_sub_count: int,
    any_has_budget: bool = False, child_count: int = 0,
    any_has_oversight_timeout: bool = False, oversight_deadline: Optional[str] = None,
) -> ColumnContext:
    """Build a ColumnContext from CLI data (no TUI widget needed)."""
    status_symbol, _ = get_status_symbol(status)
    uptime = calculate_uptime(session.start_time) if session.start_time else "-"
    green_time, non_green_time, sleep_time = get_current_state_times(
        stats, is_asleep=session.is_asleep
    )
    median_work = claude_stats.median_work_time if claude_stats else 0.0

    # Permissiveness mode emoji
    if session.permissiveness_mode == "bypass":
        perm_emoji = "🔥"
    elif session.permissiveness_mode == "permissive":
        perm_emoji = "🏃"
    else:
        perm_emoji = "👮"

    # Parse state_since for time-in-state
    status_changed_at = None
    if stats.state_since:
        try:
            status_changed_at = datetime.fromisoformat(stats.state_since)
        except (ValueError, TypeError):
            pass

    return ColumnContext(
        session=session,
        stats=stats,
        claude_stats=claude_stats,
        git_diff_stats=git_diff_stats,
        status_symbol=status_symbol,
        status_color="bold",
        bg="",
        monochrome=True,
        summary_detail="full",
        show_cost=True,
        any_has_budget=any_has_budget,
        expand_icon="",
        is_list_mode=False,
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
        all_names_match_repos=False,
        live_subagent_count=live_sub_count,
        background_bash_count=bg_bash_count,
        child_count=child_count,
        status_changed_at=status_changed_at,
        max_name_width=16,
        max_repo_width=10,
        max_branch_width=10,
        any_has_oversight_timeout=any_has_oversight_timeout,
        oversight_deadline=oversight_deadline,
        source_host=getattr(session, 'source_host', ''),
        is_remote=getattr(session, 'is_remote', False),
    )


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
