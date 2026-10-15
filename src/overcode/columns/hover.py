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


def hover_task_result(ctx: ColumnContext):
    """The child's result in full: the kind, then the report's whole reason."""
    from ..status_constants import RESULT_KINDS
    spec = RESULT_KINDS.get(ctx.task_result or "")
    if spec is None:
        return None
    emoji, ascii_fb, _, meaning = spec
    reason = (ctx.task_reason or "").strip()
    return _emoji_legend("Result", [(ascii_fb if ctx.emoji_free else emoji,
                                     f"{meaning}: {reason}" if reason else meaning)])


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
            rows.append((ctx.e(glyph), f"{rollup[key]} {words}"))
    return _emoji_legend("Children", rows)


def hover_swarm(ctx: ColumnContext):
    """What the 👥 rollup's segments count."""
    from .render_performance import swarm_workflow_glyph
    rollup = ctx.swarm_rollup
    if not rollup:
        return None
    total = rollup.get("total") or 0
    rows = [(ctx.e("👥"), f"{total} agent{'' if total == 1 else 's'} delegated in this session "
                          "(subagents, forks, workflow agents, teammates)")]
    for key, glyph, words in (("running", "▶", "working"),
                              ("waiting", "🟠", "waiting for approval in this pane"),
                              ("idle", "⚪", "idle teammates, listening"),
                              ("ok", "✅", "finished"),
                              ("failed", "❌", "failed or stopped")):
        if rollup.get(key):
            rows.append((ctx.e(glyph).rstrip(), f"{rollup[key]} {words}"))
    runs = rollup.get("workflows") or 0
    if runs:
        running = rollup.get("workflows_running") or 0
        rows.append((swarm_workflow_glyph(ctx),
                     f"{runs} workflow run{'' if runs == 1 else 's'}"
                     + (f", {running} running" if running else "")))
    return _emoji_legend("Delegated work", rows)


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
