"""Context columns: context window, model, effort, provider, backend."""

from typing import Optional

from .context import ColumnContext, ColumnOutput


def render_model(ctx: ColumnContext) -> ColumnOutput:
    """Model name. Only visible when any agent has a model set.

    Width 8 (space + 7): sized so every MODEL_SHORT_NAMES entry and the
    rule-based fallback's common shapes (Sn3.7, G5.6Sol, GLM4.6) fit
    untruncated across a four-backend fleet.
    """
    if not ctx.model:
        return [("       -", f"dim{ctx.bg}")]
    from ..history_reader import model_short_name
    display = model_short_name(ctx.model)[:7]
    return [(f" {display:>7}", f"bold magenta{ctx.bg}")]


def render_model_plain(ctx: ColumnContext) -> Optional[str]:
    if not ctx.model:
        return None
    from ..history_reader import model_short_name
    return model_short_name(ctx.model)


# Reasoning effort vocabulary across harnesses, weakest first (#497).
# Claude Code: low/medium/high/xhigh/max; codex adds minimal; hermes adds
# none; grok and opencode (as a model variant) use subsets. Unknown words
# still render (truncated) — they just sort below every known level.
EFFORT_LEVELS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultracode")
_EFFORT_SHORT = {"minimal": "min", "medium": "med", "ultracode": "ultra"}


def effort_short_name(effort: str) -> str:
    """Display form of an effort level, at most 5 chars ("medium" -> "med")."""
    effort = effort.strip().lower()
    return _EFFORT_SHORT.get(effort, effort)[:5]


def render_effort(ctx: ColumnContext) -> ColumnOutput:
    """Reasoning effort. Only visible when any agent has one detected.

    ALIGNMENT: width 6 (space + 5) — "xhigh", "ultra" are the widest.
    Brightness climbs with the level so a fleet's heavy thinkers stand out.
    """
    if not ctx.effort:
        return [("     -", f"dim{ctx.bg}")]
    level = ctx.effort.strip().lower()
    rank = EFFORT_LEVELS.index(level) if level in EFFORT_LEVELS else -1
    if rank >= EFFORT_LEVELS.index("xhigh"):
        style = f"bold yellow{ctx.bg}"
    elif rank == EFFORT_LEVELS.index("high"):
        style = f"bold cyan{ctx.bg}"
    elif rank == EFFORT_LEVELS.index("medium") or rank < 0:
        style = f"cyan{ctx.bg}"
    else:
        style = f"dim cyan{ctx.bg}"
    return [(f" {effort_short_name(ctx.effort):>5}", style)]


def render_effort_plain(ctx: ColumnContext) -> Optional[str]:
    return ctx.effort or None


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
            return [(f" 📚{ctx_pct:>3.0f}%", f"bold orange1{ctx.bg}")]
    return [(" 📚  -%", f"dim orange1{ctx.bg}")]


def render_provider(ctx: ColumnContext) -> ColumnOutput:
    """API provider: 🧱 bedrock, · web. Hidden when all agents use web."""
    provider = getattr(ctx.session, 'provider', 'web') or 'web'
    if provider == 'bedrock':
        return [(f" {ctx.e('🧱')}", f"bold cyan{ctx.bg}")]
    return [("  ·", f"dim{ctx.bg}")]


def render_provider_plain(ctx: ColumnContext) -> Optional[str]:
    if not ctx.fleet.any_has_provider:
        return None
    provider = getattr(ctx.session, 'provider', 'web') or 'web'
    return provider


# Short badges for the backend column — keeps the row width at 3 chars
# regardless of how long a backend's name is.
BACKEND_BADGES = {
    "claude-code": "cc",
    "opencode": "oc",
    "opencode2": "o2",
    "codex": "cx",
    "grok": "gk",
    "hermes": "hm",
    "shell": "sh",
    "overagent": "oa",
}


def _backend_badge(name: str) -> str:
    return BACKEND_BADGES.get(name, (name or "?")[:2])


def render_backend(ctx: ColumnContext) -> ColumnOutput:
    """Agent CLI badge (cc / oc). Hidden unless the fleet is mixed."""
    from ..backends import DEFAULT_BACKEND, session_backend_name
    name = session_backend_name(ctx.session)
    badge = _backend_badge(name)
    if name == DEFAULT_BACKEND:
        return [(f" {badge}", f"dim{ctx.bg}")]
    return [(f" {badge}", f"bold green{ctx.bg}")]


def render_backend_plain(ctx: ColumnContext) -> Optional[str]:
    # Mirrors the widget gate: silent for a single-backend fleet. `overcode
    # show` prints the backend unconditionally via its own line instead.
    if not ctx.fleet.mixed_backends:
        return None
    from ..backends import session_backend_name
    return session_backend_name(ctx.session)
