"""Agent backend registry.

``get_backend(name)`` resolves a ``Session.backend`` discriminator to the
adapter that owns that CLI's argv grammar and gestures.
"""

from typing import Dict, List

from .base import (
    AgentBackend,
    BackendCapability,
    DialogRule,
    KeyPress,
    LaunchSpec,
    supports,
)
from .claude_code import ClaudeCodeBackend

DEFAULT_BACKEND = "claude-code"

_BACKENDS: Dict[str, AgentBackend] = {
    ClaudeCodeBackend.name: ClaudeCodeBackend(),
}


class UnknownBackendError(ValueError):
    """Raised when a Session names a backend overcode doesn't have."""


def get_backend(name: str = DEFAULT_BACKEND) -> AgentBackend:
    """Resolve a backend by name.

    Empty/None resolves to the default so pre-backend sessions and
    partially-populated dicts keep working.
    """
    key = name or DEFAULT_BACKEND
    try:
        return _BACKENDS[key]
    except KeyError:
        known = ", ".join(sorted(_BACKENDS))
        raise UnknownBackendError(
            f"Unknown agent backend '{key}'. Known backends: {known}"
        ) from None


def list_backends() -> List[str]:
    """Names of every registered backend."""
    return sorted(_BACKENDS)


def register_backend(backend: AgentBackend) -> None:
    """Register (or replace) a backend. Used by tests to install doubles."""
    _BACKENDS[backend.name] = backend


def unregister_backend(name: str) -> None:
    """Remove a backend. Silent when it isn't registered."""
    _BACKENDS.pop(name, None)


__all__ = [
    "AgentBackend",
    "BackendCapability",
    "DEFAULT_BACKEND",
    "DialogRule",
    "KeyPress",
    "LaunchSpec",
    "UnknownBackendError",
    "get_backend",
    "list_backends",
    "register_backend",
    "unregister_backend",
    "supports",
]
