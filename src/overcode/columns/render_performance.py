"""Performance and subprocess columns: median work, CPU, RAM, counts."""

from typing import Optional

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


def render_subagent_count(ctx: ColumnContext) -> ColumnOutput:
    count = ctx.live_subagent_count
    style = f"bold purple{ctx.bg}" if count > 0 else f"dim{ctx.bg}"
    return [(f" {ctx.e('🤿')}{count:>2}", style)]


def render_bash_count(ctx: ColumnContext) -> ColumnOutput:
    count = ctx.background_bash_count
    style = f"bold yellow{ctx.bg}" if count > 0 else f"dim{ctx.bg}"
    return [(f" {ctx.e('🐚')}{count:>2}", style)]


# The 👶 rollup's segments, in order: (rollup key, glyph); zero ones are left out
_ROLLUP_SEGMENTS = (("working", "▶"), ("unreported", "📭"), ("success", "✅"), ("failure", "❌"))


def render_child_count(ctx: ColumnContext) -> ColumnOutput:
    """Children, then how they stand: " 👶 5 2▶ 1📭 1✅ 1❌" (non-zero only)."""
    count = ctx.child_count
    if count == 0:
        return [(f" {ctx.e('👶')} 0", f"dim{ctx.bg}")]
    style = f"bold cyan{ctx.bg}"
    text = f" {ctx.e('👶')}{count:>2}"
    rollup = ctx.child_rollup or {}
    for key, glyph in _ROLLUP_SEGMENTS:
        n = rollup.get(key) or 0
        if n:
            text += f" {n}{ctx.e(glyph).rstrip()}"
    return [(text, style)]


# The 👥 rollup's segments after the total, in order: (rollup key, glyph);
# zero ones are left out. Workflow runs (🧩) come last.
_SWARM_SEGMENTS = (("running", "▶"), ("waiting", "🟠"), ("ok", "✅"), ("failed", "❌"))

# 🧩 is also the unknown-skill emoji, whose emoji-free form is the glyph
# itself; workflows get their own letters here instead of an EMOJI_ASCII entry
WORKFLOW_GLYPH, WORKFLOW_ASCII = "🧩", "Wf"


def swarm_workflow_glyph(ctx: ColumnContext) -> str:
    return WORKFLOW_ASCII if ctx.emoji_free else ctx.e(WORKFLOW_GLYPH)


def render_swarm(ctx: ColumnContext) -> ColumnOutput:
    """Work the agent delegated in its session (subagents, workflows,
    teammates): " 👥 43 ▶15 🟠1 ✅27 ❌1 🧩1", non-zero counts only.

    The engine publishes the counts (swarm_rollup); a row without any shows
    " 👥  -", the same shape as a one-digit total.
    """
    rollup = ctx.swarm_rollup
    if not rollup:
        return [(f" {ctx.e('👥')} {'-':>2}", f"dim{ctx.bg}")]
    text = f" {ctx.e('👥')} {rollup.get('total') or 0:>2}"
    for key, glyph in _SWARM_SEGMENTS:
        n = rollup.get(key) or 0
        if n:
            text += f" {ctx.e(glyph).rstrip()}{n}"
    if rollup.get("workflows"):
        text += f" {swarm_workflow_glyph(ctx)}{rollup['workflows']}"
    active = rollup.get("running") or rollup.get("waiting")
    return [(text, f"bold magenta{ctx.bg}" if active else f"magenta{ctx.bg}")]


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
    return f"🤿 {ctx.live_subagent_count} subagents  🐚 {ctx.background_bash_count} background bashes"
