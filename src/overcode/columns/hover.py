"""Hover popups: what a row's emoji stand for."""

from typing import List, Optional, TYPE_CHECKING, Tuple

from ..status_constants import BADGE_MEANINGS
from ..tui_helpers import format_duration
from .context import ColumnContext
from .emoji import (
    SANDBOX_EMOJI,
    SKILL_EMOJI_DEFAULT,
    TOOL_EMOJI,
    TOOL_EMOJI_DEFAULT,
    WRAPPER_EMOJI_DEFAULT,
    _wrapper_name,
    get_skill_emoji,
    get_wrapper_emoji,
)
from .render_identity import _badge_glyph
from .render_supervision import _skill_profile

if TYPE_CHECKING:
    from rich.text import Text


def _emoji_legend(title: str, rows: List[Tuple[str, str]], footer: str = "") -> "Text":  # noqa: F821
    """A popup: a bold title, then one ``emoji  name`` line per row."""
    from rich.text import Text
    text = Text(title, style="bold")
    for emoji, name in rows:
        text.append(f"\n{emoji}  {name}")
    if footer:
        text.append(f"\n{footer}", style="dim italic")
    return text


def _skills_legend(ctx: ColumnContext, title: str, skills: Optional[List[str]]) -> "Optional[Text]":  # noqa: F821
    if not skills:
        return None
    emoji = get_skill_emoji()
    return _emoji_legend(title, [(ctx.e(emoji.get(s, SKILL_EMOJI_DEFAULT)), s) for s in skills])


def hover_loaded_skills(ctx: ColumnContext):
    return _skills_legend(ctx, "Skills used", ctx.session.loaded_skills)


def hover_available_skills(ctx: ColumnContext):
    return _skills_legend(ctx, "Skills available", ctx.session.available_skills)


def hover_skill_profile(ctx: ColumnContext):
    profile = _skill_profile(ctx)
    if not profile:
        return None
    from ..skill_library import get_profile_emojis, get_profiles, profile_emoji
    emoji = get_skill_emoji()
    skills = get_profiles().get(profile, [])
    footer = (
        "" if profile in get_profile_emojis()
        else f"Set its emoji: overcode skills profile emoji {profile} <emoji>"
    )
    return _emoji_legend(
        f"Skill profile {ctx.e(profile_emoji(profile))} {profile}",
        [(ctx.e(emoji.get(s, SKILL_EMOJI_DEFAULT)), s) for s in skills],
        footer,
    )


def hover_allowed_tools(ctx: ColumnContext):
    tools = [t.strip() for t in (ctx.session.allowed_tools or "").split(",") if t.strip()]
    if not tools:
        return None
    return _emoji_legend("Allowed tools", [(ctx.e(TOOL_EMOJI.get(t, TOOL_EMOJI_DEFAULT)), t) for t in tools])


def hover_wrapper(ctx: ColumnContext):
    rows = []
    if ctx.session.wrapper:
        name = _wrapper_name(ctx.session.wrapper)
        rows.append((ctx.e(get_wrapper_emoji().get(name, WRAPPER_EMOJI_DEFAULT)), f"wrapper: {name}"))
    if getattr(ctx.session, "sandbox_enabled", None) is True:
        rows.append((ctx.e(SANDBOX_EMOJI), "Claude /sandbox is on"))
    return _emoji_legend("Wrapper", rows) if rows else None


def hover_permission_mode(ctx: ColumnContext):
    """The MOD emoji's mode, and the launch mode under an in-session one."""
    from ..status_constants import PERMISSIVENESS_EMOJIS, PERMISSIVENESS_MEANINGS
    launch = ctx.session.permissiveness_mode
    mode = ctx.effective_permission_mode
    rows = [(ctx.e(PERMISSIVENESS_EMOJIS.get(mode, "👮")), PERMISSIVENESS_MEANINGS.get(mode, mode))]
    footer = ""
    if mode != launch:
        rows.append((ctx.e(PERMISSIVENESS_EMOJIS.get(launch, "👮")),
                     f"launched {PERMISSIVENESS_MEANINGS.get(launch, launch).split(':')[0]}"))
        footer = "Switched in the session (shift+tab)"
    return _emoji_legend("Permission mode", rows, footer)


def hover_child_count(ctx: ColumnContext):
    """What the 👶 rollup's segments count."""
    if not ctx.child_count:
        return None
    rollup = ctx.child_rollup or {}
    rows = [(ctx.e("👶"), f"{ctx.child_count} child agent{'' if ctx.child_count == 1 else 's'}")]
    for key, glyph, words in (("working", "▶", "still working"),
                              ("unreported", "📭", "stopped without a report"),
                              ("success", "✅", "reported success"),
                              ("failure", "❌", "reported failure or timed out")):
        if rollup.get(key):
            rows.append((ctx.e(glyph).rstrip(), f"{rollup[key]} {words}"))
    return _emoji_legend("Children", rows)


_SUBAGENT_WORDS = {"running": "working", "yellow": "waiting on background work of their own",
                   "waiting": "waiting for approval in this pane",
                   "idle": "idle teammates, listening", "ok": "finished",
                   "failed": "failed or stopped"}


def hover_subagents(ctx: ColumnContext):
    """What the SubAgents cell's segments count, and the total it leaves out."""
    from .render_performance import SUBAGENT_SEGMENTS, swarm_workflow_glyph
    rollup = ctx.swarm_rollup
    if not rollup:
        live = ctx.live_subagent_count
        if live <= 0:
            return None
        return _emoji_legend("SubAgents", [(ctx.e("▶").rstrip(), f"{live} running")],
                             "Seen from its transcript files: no hook counts for this agent")
    total = rollup.get("total") or 0
    rows = [(ctx.e("👥"), f"{total} agent{'' if total == 1 else 's'} delegated in this session "
                          "(subagents, forks, workflow agents, teammates)")]
    for key, glyph in SUBAGENT_SEGMENTS:
        if rollup.get(key):
            rows.append((ctx.e(glyph).rstrip(), f"{rollup[key]} {_SUBAGENT_WORDS[key]}"))
    runs = rollup.get("workflows") or 0
    if runs:
        running = rollup.get("workflows_running") or 0
        rows.append((swarm_workflow_glyph(ctx),
                     f"{runs} workflow run{'' if runs == 1 else 's'}"
                     + (f", {running} running" if running else "")))
    return _emoji_legend("SubAgents", rows)


TRIP_FOOTER = "Budgets count the total: a trip reset never adds budget."


def _spend_line(cost: float, tokens: int, joules: float) -> str:
    from ..tui_helpers import format_joules, format_tokens
    return f"${cost:.2f} · {format_tokens(tokens)} tokens · {format_joules(joules).strip()}"


def hover_spend(ctx: ColumnContext):
    """The agent's spend: its trip beside the total (trip.py), and the total
    split into its own and its delegated work's (decision 6)."""
    from ..trip import describe, trip_tokens
    sub = ctx.subagent_cost_usd
    stats = ctx.session.stats
    total = getattr(stats, "estimated_cost_usd", 0.0) or 0.0
    trip = ctx.trip
    if (not sub and not trip) or ctx.claude_stats is None:
        return None
    rows, notes = [], []
    if trip:
        since = _spend_line(trip.get("estimated_cost_usd") or 0.0, trip_tokens(trip),
                            trip.get("estimated_energy_j") or 0.0)
        in_all = _spend_line(total, ctx.claude_stats.total_tokens,
                             getattr(stats, "estimated_energy_j", 0.0) or 0.0)
        rows += [(ctx.e("↺"), f"{since} {describe(trip)}"), ("Σ", f"{in_all} in all")]
        notes.append(TRIP_FOOTER)
    else:
        rows.append(("$", f"${total:.2f} in all"))
    if sub:
        own = max(0.0, total - sub)
        rows += [("·", f"${own:.2f} its own turns"),
                 (ctx.e("👥"), f"${sub:.2f} its subagents, workflow agents and teammates")]
        notes.append("Delegated work is inside the total; never add it again.")
    return _emoji_legend("Spend", rows, "\n".join(notes))


def hover_subtree_cost(ctx: ColumnContext):
    """SUB$ on trips: the rows' sum beside the subtree's total."""
    shown = ctx.subtree_trip_cost_usd
    if shown is None or ctx.subtree_cost_usd <= 0:
        return None
    rows = [(ctx.e("↺"), f"${shown:.2f}: its rows' $ summed (trips where reset)"),
            ("Σ", f"${ctx.subtree_cost_usd:.2f} in all")]
    return _emoji_legend("Subtree cost", rows, TRIP_FOOTER)


_CONFIDENCE = {
    "prior": "not fitted yet: list-price ratios and the plan's weekly budget",
    "low": "fitted on little data",
    "medium": "fitted",
    "high": "fitted, tight",
}


def hover_quota(ctx: ColumnContext):
    """The quota estimate: this agent's weekly and 5-hour points, the account's readings."""
    from ..quota_model import format_pp

    q = ctx.quota
    if not isinstance(q, dict) or q.get("pp7d") is None:
        return None
    rows = [("7d", f"{format_pp(q.get('pp7d'))} of the weekly quota this week")]
    trip = ctx.trip
    if trip and trip.get("pp7d") is not None:
        from ..trip import describe
        rows.insert(0, (ctx.e("↺"), f"{format_pp(trip.get('pp7d'))} {describe(trip)} "
                                    "(this week's only, once the week rolls over)"))
    if q.get("pp5h") is not None:
        rows.append(("5h", f"{format_pp(q.get('pp5h'))} of the current 5-hour window"))
    w7, w5 = q.get("w7"), q.get("w5")
    if isinstance(w7, (int, float)):
        five = f", 5-hour {w5:.0f}%" if isinstance(w5, (int, float)) else ""
        rows.append(("Σ", f"account: weekly {w7:.0f}% used{five} (all surfaces)"))
    conf = q.get("conf") or "prior"
    rows.append(("±", f"model {conf}: {_CONFIDENCE.get(conf, conf)}"))
    return _emoji_legend("Quota (estimate)", rows,
                         "Predicted from tokens; the meter itself shows whole percents. "
                         "overcode usage model")


def hover_status_detail(ctx: ColumnContext):
    detail = ctx.status_detail
    if detail is None or not detail.badges:
        return None
    rows = []
    for b in detail.badges:
        meaning = BADGE_MEANINGS.get(b.kind, b.kind)
        if b.label:
            meaning += f" ({b.label})"
        if b.count > 1:
            meaning += f" ×{b.count}"
        if b.eta_seconds is not None and b.eta_seconds > 0:
            meaning += f", in {format_duration(int(b.eta_seconds))}"
        rows.append((_badge_glyph(b.kind, ctx.emoji_free), meaning))
    return _emoji_legend("Status detail", rows)


_NAME_SOURCE_TEXT = {
    "default": "a default name (its directory or fork); overcode may rename it",
    "parent": "named by its parent agent",
    "auto": "named by overcode; it may refine it",
    "human": "named by you; overcode never renames it",
}


def hover_agent_name(ctx: ColumnContext):
    """NAME: who chose the name (#542), and the names it had before."""
    from rich.text import Text
    session = ctx.session
    source = getattr(session, "name_source", None)
    if getattr(session, "is_remote", False) or source not in _NAME_SOURCE_TEXT:
        return None
    text = Text(session.name, style="bold")
    text.append(f"\n{_NAME_SOURCE_TEXT[source]}")
    if getattr(session, "name_forced", False):
        text.append("\nlaunched with --force-name past the naming policy", style="yellow")
    previous = getattr(session, "previous_names", None) or []
    if previous:
        text.append(f"\nwas: {', '.join(reversed(previous))}", style="dim")
    return text
