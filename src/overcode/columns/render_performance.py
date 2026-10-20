"""Performance and subprocess columns: median work, CPU, RAM, counts."""

from typing import Optional

from ..status_constants import WORKFLOW_ASCII, WORKFLOW_GLYPH
from ..swarm_view import count_glyph
from ..tui_helpers import format_duration
from .context import ColumnContext, ColumnOutput


def render_median_work_time(ctx: ColumnContext) -> ColumnOutput:
    """Median work-cycle time. Dashed when the backend reports no stats."""
    w = ctx.e("⌚")
    if ctx.claude_stats is None:
        return [(f" {w}{'-':>5}", f"dim blue{ctx.bg}")]
    return [(
        f" {w}{format_duration(ctx.median_work):>5}",
        f"bold blue{ctx.bg}",
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
    via visible=fleet.any_has_cpu."""
    cpu = getattr(ctx.session, 'cpu_percent', 0.0) or 0.0
    if cpu < 1.0:
        return [("     -", f"dim{ctx.bg}")]
    if cpu >= 200.0:
        style = f"bold red{ctx.bg}"
    elif cpu >= 100.0:
        style = f"bold yellow{ctx.bg}"
    elif cpu >= 50.0:
        style = f"bold{ctx.bg}"
    else:
        style = f"dim{ctx.bg}"
    return [(f" {cpu:>4.0f}%", style)]


def render_ram(ctx: ColumnContext) -> ColumnOutput:
    """Resident memory for the claude process tree, human-readable."""
    rss = getattr(ctx.session, 'rss_bytes', 0) or 0
    if rss <= 0:
        return [("     -", f"dim{ctx.bg}")]
    # Colour heavy consumers (>4 GB) for parity with CPU highlighting.
    gib = rss / (1024 * 1024 * 1024)
    if gib >= 8.0:
        style = f"bold red{ctx.bg}"
    elif gib >= 4.0:
        style = f"bold yellow{ctx.bg}"
    elif gib >= 1.0:
        style = f"bold{ctx.bg}"
    else:
        style = f"dim{ctx.bg}"
    return [(f" {_format_rss(rss)}", style)]


def render_bash_count(ctx: ColumnContext) -> ColumnOutput:
    count = ctx.background_bash_count
    style = f"bold yellow{ctx.bg}" if count > 0 else f"dim{ctx.bg}"
    return [(f" {ctx.e('🐚')}{count:>2}", style)]


# The 👶 rollup's segments after the total, in order: (rollup key, glyph);
# zero ones are left out. The same order, and glyph-before-count, as the
# SubAgents cell: still going, needs a look, succeeded, failed.
_ROLLUP_SEGMENTS = (("working", "▶"), ("unreported", "📭"), ("success", "✅"), ("failure", "❌"))


def render_child_count(ctx: ColumnContext) -> ColumnOutput:
    """Children, then how they stand: " 👶 5 ▶2 📭1 ✅1 ❌1" (non-zero only).

    The total stays: the states overlap (a child can work again after it
    reported) and miss some (a child waiting on you has no result yet).
    """
    count = ctx.child_count
    if count == 0:
        return [(f" {ctx.e('👶')} 0", f"dim{ctx.bg}")]
    style = f"bold cyan{ctx.bg}"
    text = f" {ctx.e('👶')}{count:>2}"
    rollup = ctx.child_rollup or {}
    for key, glyph in _ROLLUP_SEGMENTS:
        n = rollup.get(key) or 0
        if n:
            text += f" {ctx.e(glyph).rstrip()}{n}"
    return [(text, style)]


# The SubAgents cell's segments, in order: (rollup key, glyph); zero ones
# are left out. ▶ working, 🟡 waiting on a background shell of its own
# (its row's light), 🟠 waiting for approval (the light's glyph), ⚪ idle
# teammates (at rest, listening), ✅ finished, ❌ failed: the same glyphs
# and order as the summary lines under it (swarm_view.COUNT_GLYPHS). The
# lit ones are drawn in the active status theme (swarm_view.count_glyph).
# Workflow runs (🧩) come last. No total: the states partition it (the
# hover says it).
SUBAGENT_SEGMENTS = (("running", "▶"), ("yellow", "🟡"), ("waiting", "🟠"), ("idle", "⚪"),
                     ("ok", "✅"), ("failed", "❌"))


def swarm_workflow_glyph(ctx: ColumnContext) -> str:
    return WORKFLOW_ASCII if ctx.emoji_free else ctx.e(WORKFLOW_GLYPH)


def render_subagents(ctx: ColumnContext) -> ColumnOutput:
    """Work the agent delegated in its session (subagents, forks, workflow
    agents, teammates), each state's count, non-zero only:
    "▸👥 ▶15 🟡2 🟠1 ✅27 ❌1 🧩1".

    The engine publishes the counts (swarm_rollup). Without them, a live
    subagent count from the transcript files still shows (" 👥 ▶2", no
    fold marker: there are no rows to expand). Nothing at all shows
    " 👥  -", the same shape as " 👥 ▶1".
    """
    rollup = ctx.swarm_rollup
    if not rollup:
        live = ctx.live_subagent_count
        if live > 0:
            return [(f" {ctx.e('👥')} {ctx.e('▶').rstrip()}{live}", f"bold magenta{ctx.bg}")]
        return [(f" {ctx.e('👥')} {'-':>2}", f"dim{ctx.bg}")]
    # The fold marker in the leading space: → expands it into rows
    fold = ("▾" if ctx.swarm_expanded else "▸") if ctx.is_list_mode and not ctx.is_remote else " "
    text = f"{fold}{ctx.e('👥')}"
    parts = [f"{count_glyph(key, ctx.emoji_free).rstrip()}{rollup[key]}"
             for key, _ in SUBAGENT_SEGMENTS if rollup.get(key)]
    if rollup.get("workflows"):
        parts.append(f"{swarm_workflow_glyph(ctx)}{rollup['workflows']}")
    # Every count zero can't happen (a rollup has some work), but an odd
    # one from a sister keeps the placeholder's shape, fold marker and all
    text += " " + " ".join(parts) if parts else f" {'-':>2}"
    active = rollup.get("running") or rollup.get("waiting")
    return [(text, f"bold magenta{ctx.bg}" if active else f"magenta{ctx.bg}")]


def render_subagents_plain(ctx: ColumnContext) -> Optional[str]:
    """The cell plus the total, for the CLI's Agents line (``overcode show``)."""
    rollup = ctx.swarm_rollup
    if not rollup:
        live = ctx.live_subagent_count
        return f"👥 ▶{live} subagents running" if live > 0 else None
    parts = [f"{count_glyph(key)}{rollup[key]}" for key, _ in SUBAGENT_SEGMENTS if rollup.get(key)]
    if rollup.get("workflows"):
        parts.append(f"{WORKFLOW_GLYPH}{rollup['workflows']}")
    total = rollup.get("total") or 0
    return f"👥 {' '.join(parts)} ({total} delegated)"


def render_job_count(ctx: ColumnContext) -> ColumnOutput:
    count = ctx.job_count
    if count == 0:
        return [(f" {ctx.e('🚜')} 0", f"dim{ctx.bg}")]
    return [(f" {ctx.e('🚜')}{count:>2}", f"bold green{ctx.bg}")]


def render_work_plain(ctx: ColumnContext) -> Optional[str]:
    if ctx.claude_stats is None:
        return None
    median_work = ctx.median_work
    work_str = format_duration(median_work) if median_work > 0 else "-"
    human_count = max(0, ctx.claude_stats.interaction_count - ctx.stats.steers_count)
    return f"⌚ {work_str} median  👤 {human_count} human 🤖 {ctx.stats.steers_count} robot"


def render_agents_plain(ctx: ColumnContext) -> Optional[str]:
    return f"🐚 {ctx.background_bash_count} background bashes"
