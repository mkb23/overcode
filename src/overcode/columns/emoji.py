"""Emoji registries for the tools, skills and wrapper columns."""

from typing import Optional


# ---------------------------------------------------------------------------
# Tool name → emoji registry for allowed-tools column
# ---------------------------------------------------------------------------
TOOL_EMOJI: dict[str, str] = {
    "Bash": "🖥️",
    "Read": "📖",
    "Write": "✏️",
    "Edit": "🔨",  # 🔧 is the DTL `tool` badge; the screwdriver was too new for VSCode (#504)
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
    "delegating-to-agents": "👥",  # merged into overcode; kept for older sessions
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
    from ..settings import get_user_config
    merged = dict(_SKILL_EMOJI_DEFAULTS)
    merged.update(get_user_config().skill_emoji)
    return merged


def get_wrapper_emoji() -> dict[str, str]:
    """Return merged wrapper emoji registry (defaults + user config overrides)."""
    from ..settings import get_user_config
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
    from ..status_constants import emoji_or_ascii
    tools = [t.strip() for t in allowed_tools.split(",") if t.strip()]
    emojis = [emoji_or_ascii(TOOL_EMOJI.get(t, TOOL_EMOJI_DEFAULT), emoji_free) for t in tools[:max_n]]
    sep = " " if emoji_free else ""
    suffix = "…" if len(tools) > max_n else ""
    return sep.join(emojis) + suffix
