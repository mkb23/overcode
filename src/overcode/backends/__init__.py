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
from .codex import CodexBackend
from .grok import GrokBackend
from .opencode import OpencodeBackend

DEFAULT_BACKEND = "claude-code"

_BACKENDS: Dict[str, AgentBackend] = {
    ClaudeCodeBackend.name: ClaudeCodeBackend(),
    OpencodeBackend.name: OpencodeBackend(),
    CodexBackend.name: CodexBackend(),
    GrokBackend.name: GrokBackend(),
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


def capability_names(capabilities: BackendCapability) -> List[str]:
    """Serialize a capability flag set to sorted member names.

    This is the wire form: ``SessionDaemonState.backend_capabilities`` and
    therefore the sister protocol carry these strings, so a newer sister can
    tell an older TUI what its backends can do.
    """
    return sorted(
        member.name
        for member in BackendCapability
        if member.name and member.value and (capabilities & member)
    )


def capabilities_from_names(names: Any) -> BackendCapability:
    """Parse serialized capability names back into a flag set.

    Unknown names are ignored: a sister running a newer overcode may report
    capabilities this build has never heard of.
    """
    result = BackendCapability.NONE
    if not isinstance(names, (list, tuple, set)):
        return result
    for name in names:
        member = getattr(BackendCapability, name, None) if isinstance(name, str) else None
        if isinstance(member, BackendCapability):
            result |= member
    return result


def session_capabilities(session: Any) -> BackendCapability:
    """Capabilities of the backend behind a session.

    Remote (sister) agents are answered from the capability list their own
    daemon published, so a sister can run a backend this host doesn't have.
    Sisters predating Phase 6 report nothing; they are assumed to be
    claude-code with the full capability set (design §3, consequence 5).
    """
    # Strict bool check: callers hand us duck-typed stand-ins whose every
    # attribute is truthy, and misreading one as remote would silently swap
    # in another host's capability answer.
    is_remote = getattr(session, "is_remote", False)
    if isinstance(is_remote, bool) and is_remote:
        remote_state = getattr(session, "remote_daemon_state", None)
        if isinstance(remote_state, dict) and remote_state.get("backend_capabilities"):
            return capabilities_from_names(remote_state["backend_capabilities"])
        return get_backend(DEFAULT_BACKEND).capabilities
    try:
        backend = get_backend(session_backend_name(session))
    except UnknownBackendError:
        return BackendCapability.NONE
    return backend.capabilities


def session_supports(session: Any, capability: BackendCapability) -> bool:
    """True when the session's backend declares ``capability``.

    An unknown backend name answers False — an adapter overcode doesn't
    have can't be assumed to support anything.
    """
    return bool(session_capabilities(session) & capability)


__all__ = [
    "AgentBackend",
    "BackendCapability",
    "ClaudeCodeBackend",
    "CodexBackend",
    "GrokBackend",
    "OpencodeBackend",
    "DEFAULT_BACKEND",
    "DialogRule",
    "KeyPress",
    "LaunchSpec",
    "UnknownBackendError",
    "capabilities_from_names",
    "capability_names",
    "get_backend",
    "list_backends",
    "register_backend",
    "session_backend_name",
    "session_capabilities",
    "session_supports",
    "unregister_backend",
    "supports",
]
