"""Agent backend registry.

``get_backend(name)`` resolves a ``Session.backend`` discriminator to the
adapter that owns that CLI's argv grammar and gestures.
"""

from typing import Any, Dict, List

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
    _invalidate_derived_caches()


def unregister_backend(name: str) -> None:
    """Remove a backend. Silent when it isn't registered."""
    _BACKENDS.pop(name, None)
    _invalidate_derived_caches()


def _invalidate_derived_caches() -> None:
    """Drop per-backend objects other modules cache by backend name."""
    from ..stats_reader import clear_reader_cache
    from ..status_patterns import clear_patterns_cache
    clear_reader_cache()
    clear_patterns_cache()


def session_backend_name(session: Any) -> str:
    """Backend name recorded on a session, defaulting for legacy sessions."""
    name = getattr(session, "backend", None)
    if isinstance(name, str) and name:
        return name
    return DEFAULT_BACKEND


def session_supports(session: Any, capability: BackendCapability) -> bool:
    """True when the session's backend declares ``capability``.

    An unknown backend name answers False — an adapter overcode doesn't
    have can't be assumed to support anything.
    """
    try:
        backend = get_backend(session_backend_name(session))
    except UnknownBackendError:
        return False
    return supports(backend, capability)


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
    "session_backend_name",
    "session_supports",
    "unregister_backend",
    "supports",
]
