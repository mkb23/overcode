"""Supervision and priority columns: mode, teams, wrapper, tools, skills, orders, heartbeat, oversight, value."""

from datetime import datetime, timedelta
from typing import Optional

from ..tui_helpers import format_duration
from .column import _make_simple_render, _make_simple_render_plain
from .context import ColumnContext, ColumnOutput
from .emoji import (
    SANDBOX_EMOJI,
    SKILL_EMOJI_DEFAULT,
    WRAPPER_EMOJI_DEFAULT,
    _tool_emojis,
    _wrapper_name,
    get_skill_emoji,
    get_wrapper_emoji,
)


render_permission_mode = _make_simple_render("perm_emoji", format_str=" {v}", colored_style="bold white")


def render_agent_teams(ctx: ColumnContext) -> ColumnOutput:
    if ctx.session.agent_teams:
        return [(f" {ctx.e('🤝')}", f"bold cyan{ctx.bg}")]
    return None


def render_teams_plain(ctx: ColumnContext) -> Optional[str]:
    if ctx.session.agent_teams:
        return "teams"
    return None


def render_allowed_tools(ctx: ColumnContext) -> ColumnOutput:
    emojis = _tool_emojis(ctx.session.allowed_tools, emoji_free=ctx.emoji_free)
    if not emojis:
        return None
    return [(f" {emojis}", f"white{ctx.bg}")]


def render_loaded_skills(ctx: ColumnContext) -> ColumnOutput:
    skills = ctx.session.loaded_skills
    if not skills:
        return None
    from ..status_constants import emoji_or_ascii
    emojis = [emoji_or_ascii(get_skill_emoji().get(s, SKILL_EMOJI_DEFAULT), ctx.emoji_free) for s in skills]
    sep = " " if ctx.emoji_free else ""
    text = sep.join(emojis)
    return [(f" {text}", f"white{ctx.bg}")]


def render_skills_plain(ctx: ColumnContext) -> Optional[str]:
    skills = ctx.session.loaded_skills
    if not skills:
        return None
    emojis = [get_skill_emoji().get(s, SKILL_EMOJI_DEFAULT) for s in skills]
    return f"{''.join(emojis)}  ({', '.join(skills)})"


def _skill_profile(ctx: ColumnContext) -> Optional[str]:
    profile = getattr(ctx.session, "skill_profile", None)
    return profile if isinstance(profile, str) and profile else None


def render_skill_profile(ctx: ColumnContext) -> ColumnOutput:
    """The skill profile the agent launched with (#499), as its emoji; None
    when it has none. Emoji-free terminals get the profile name."""
    profile = _skill_profile(ctx)
    if not profile:
        return None
    if ctx.emoji_free:
        return [(f" {profile}", f"cyan{ctx.bg}")]
    from ..skill_library import profile_emoji
    return [(f" {ctx.e(profile_emoji(profile))}", f"cyan{ctx.bg}")]


def render_skill_profile_plain(ctx: ColumnContext) -> Optional[str]:
    profile = _skill_profile(ctx)
    if not profile:
        return None
    from ..skill_library import profile_emoji
    return f"{profile_emoji(profile)} {profile}"


def render_available_skills(ctx: ColumnContext) -> ColumnOutput:
    skills = ctx.session.available_skills
    if not skills:
        return None
    from ..status_constants import emoji_or_ascii
    emojis = [emoji_or_ascii(get_skill_emoji().get(s, SKILL_EMOJI_DEFAULT), ctx.emoji_free) for s in skills]
    sep = " " if ctx.emoji_free else ""
    text = sep.join(emojis)
    return [(f" {text}", f"dim white{ctx.bg}")]


def render_available_skills_plain(ctx: ColumnContext) -> Optional[str]:
    skills = ctx.session.available_skills
    if not skills:
        return None
    emojis = [get_skill_emoji().get(s, SKILL_EMOJI_DEFAULT) for s in skills]
    return f"{''.join(emojis)}  ({', '.join(skills)})"


def render_enhanced_context(ctx: ColumnContext) -> ColumnOutput:
    if ctx.session.enhanced_context_enabled:
        return [(f" {ctx.e('🎣')}", f"bold white{ctx.bg}")]
    else:
        return [("  ·", f"dim{ctx.bg}")]


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
    return [(f" {badges}", f"bold yellow{ctx.bg}")]


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
        return [(f" {ctx.e('👤')}{human_count:>3}", f"bold yellow{ctx.bg}")]
    else:
        return [(f" {ctx.e('👤')}  -", f"dim yellow{ctx.bg}")]


def render_robot_count(ctx: ColumnContext) -> ColumnOutput:
    v = ctx.stats.steers_count
    return [(f" {ctx.e('🤖')}{v:>3}", f"bold cyan{ctx.bg}")]


def render_standing_orders(ctx: ColumnContext) -> ColumnOutput:
    s = ctx.session
    if s.standing_instructions:
        if s.standing_orders_complete:
            return [(f" {ctx.e('✓')}", f"bold green{ctx.bg}")]
        elif s.standing_instructions_preset:
            preset_display = f" {s.standing_instructions_preset[:8]}"
            return [(preset_display, f"bold cyan{ctx.bg}")]
        else:
            return [(f" {ctx.e('📋')}", f"bold yellow{ctx.bg}")]
    else:
        return [(" ➖", f"bold dim{ctx.bg}")]


def render_oversight_countdown(ctx: ColumnContext) -> ColumnOutput:
    """Oversight countdown timer. Visibility gated by column's visible callback."""
    from ..status_constants import STATUS_WAITING_OVERSIGHT
    status = ctx.stats.current_state if hasattr(ctx.stats, 'current_state') else ""

    # Check session status as well (session object may have the status)
    session_status = getattr(ctx.session, 'status', '')
    is_oversight = session_status == STATUS_WAITING_OVERSIGHT or status == STATUS_WAITING_OVERSIGHT

    if not is_oversight:
        return None

    deadline_str = ctx.oversight_deadline
    if not deadline_str:
        hg = ctx.e("⏳")
        return [(f" {hg} --:--", f"yellow{ctx.bg}")]

    try:
        hg = ctx.e("⏳")
        deadline = datetime.fromisoformat(deadline_str)
        remaining = (deadline - datetime.now()).total_seconds()
        if remaining <= 0:
            return [(f" {hg} 0s  ", f"bold blink red{ctx.bg}")]

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
            style = f"bold blink red{ctx.bg}"
        elif remaining < 60:
            style = f"bold red{ctx.bg}"
        else:
            style = f"bold yellow{ctx.bg}"
        return [(text, style)]
    except (ValueError, TypeError):
        return [(f" {hg} --:--", f"dim{ctx.bg}")]


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
        segments = [(f" {hb}{freq_str:>5}", f"bold magenta{ctx.bg}")]
        next_time_str = _compute_next_heartbeat(s)
        if next_time_str:
            segments.append((f" @{next_time_str}", f"bold cyan{ctx.bg}"))
        else:
            segments.append((" @--:--", f"dim{ctx.bg}"))
        return segments
    elif s.heartbeat_enabled and s.heartbeat_paused:
        freq_str = format_duration(s.heartbeat_frequency_seconds)
        return [
            (f" {hb}{freq_str:>5}", f"dim{ctx.bg}"),
            ("     ⏸ ", f"bold yellow{ctx.bg}"),
        ]
    else:
        return [(f" {hb}    - @--:--", f"dim{ctx.bg}")]


def render_agent_value(ctx: ColumnContext) -> ColumnOutput:
    s = ctx.session
    if ctx.summary_detail in ("full", "high"):
        return [(f" {ctx.e('💰')}{s.agent_value:>4}", f"bold magenta{ctx.bg}")]
    else:
        if s.agent_value > 1000:
            return [(f" {ctx.e('⏫️')}", f"bold red{ctx.bg}")]
        elif s.agent_value < 1000:
            return [(f" {ctx.e('⏬️')}", f"bold blue{ctx.bg}")]
        else:
            return [(f" {ctx.e('⏹️')} ", f"dim{ctx.bg}")]


def render_mode_plain(ctx: ColumnContext) -> Optional[str]:
    from ..status_constants import PERMISSIVENESS_EMOJIS
    perm = ctx.session.permissiveness_mode
    if ctx.auto_accept_mode and perm == "normal":
        perm = "auto"
    emoji = PERMISSIVENESS_EMOJIS.get(perm, "👮")
    mode = f"{emoji} {perm}"
    ec = "🎣 enabled" if ctx.session.enhanced_context_enabled else "disabled"
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
