"""
Dependency checking and graceful degradation utilities.

Provides functions to check for required external dependencies (tmux, claude)
and handle graceful degradation when they're missing.
"""

import shutil
import subprocess
from typing import Callable, Optional, Tuple, Type

from .exceptions import TmuxNotFoundError, AgentCliNotFoundError


def find_executable(name: str) -> Optional[str]:
    """Find the path to an executable.

    Checks PATH first, then common install locations that may not be on PATH
    in non-login shells (e.g., web server subprocesses, SSH non-interactive).

    Args:
        name: Name of the executable

    Returns:
        Full path to executable, or None if not found
    """
    import os
    from pathlib import Path

    path = shutil.which(name)
    if path:
        return path

    # Check common locations not always on PATH in non-login shells
    return _find_in_fallback_dirs(name)


def _find_in_fallback_dirs(name: str) -> Optional[str]:
    """Check common install directories for an executable."""
    import os
    from pathlib import Path

    home = Path.home()
    fallback_dirs = [
        home / ".local" / "bin",           # pip/pipx, claude CLI
        home / ".npm-global" / "bin",      # npm global
        home / ".nvm" / "current" / "bin", # nvm
        Path("/usr/local/bin"),
        Path("/opt/homebrew/bin"),          # macOS ARM homebrew
    ]
    for d in fallback_dirs:
        candidate = d / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    return None


def _check_executable(
    name: str,
    version_args: list[str],
    timeout: int = 5,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Check if an executable is available and get its version.

    Args:
        name: Name of the executable
        version_args: Command-line args to get version (e.g. ["-V"])
        timeout: Subprocess timeout in seconds

    Returns:
        Tuple of (is_available, path, version)
    """
    path = find_executable(name)
    if not path:
        return False, None, None

    try:
        result = subprocess.run(
            [name] + version_args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        version = result.stdout.strip() if result.returncode == 0 else None
        return True, path, version
    except (subprocess.SubprocessError, OSError):
        return True, path, None


def _require_executable(
    check_fn: Callable[[], Tuple[bool, Optional[str], Optional[str]]],
    error_class: Type[Exception],
    install_hint: str,
) -> str:
    """Ensure an executable is available, raise if not.

    Args:
        check_fn: Function that checks availability (e.g. check_tmux)
        error_class: Exception class to raise if not found
        install_hint: Human-readable install instructions

    Returns:
        Path to the executable

    Raises:
        error_class: If the executable is not found
    """
    available, path, _ = check_fn()
    if not available:
        raise error_class(install_hint)
    return path


def check_tmux() -> Tuple[bool, Optional[str], Optional[str]]:
    """Check if tmux is available and get its version.

    Returns:
        Tuple of (is_available, path, version)
    """
    return _check_executable("tmux", ["-V"], timeout=5)


def _resolve_backend(backend):
    """Accept a backend object or a backend name."""
    if isinstance(backend, str):
        from .backends import get_backend
        return get_backend(backend)
    return backend


def check_agent_cli(
    backend, *, respect_override: bool = True
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Check if a backend's agent CLI is available and get its version.

    Args:
        backend: An AgentBackend or a registered backend name
        respect_override: When True (the default), probes
            ``resolved.executable()`` — which honors the backend's
            launch-time override env var (CLAUDE_COMMAND / OPENCODE_COMMAND /
            CODEX_COMMAND / GROK_COMMAND) when one is set, falling back to
            ``resolved.binary`` otherwise. This is what a pre-launch check
            wants: a bad override should fail here with a friendly error
            instead of dying silently once the pane launches, and the e2e
            mock harness's override to a mock script is genuinely validated
            rather than always requiring the real CLI on PATH regardless of
            the override. Pass False for a "what's actually installed on
            this machine" probe (e.g. a backend's ``installed_version()``
            doctor helper), which must never be satisfied by a dev/test
            override.

    Returns:
        Tuple of (is_available, path, version)
    """
    resolved = _resolve_backend(backend)
    name = resolved.executable() if respect_override else resolved.binary
    return _check_executable(name, list(resolved.version_args), timeout=10)


def require_agent_cli(backend) -> str:
    """Ensure a backend's agent CLI is available, raise if not.

    Args:
        backend: An AgentBackend or a registered backend name

    Returns:
        Path to the agent CLI executable

    Raises:
        The backend's not_found_error: If the CLI is not found
    """
    resolved = _resolve_backend(backend)
    return _require_executable(
        lambda: check_agent_cli(resolved),
        resolved.not_found_error,
        resolved.install_hint,
    )


def check_claude() -> Tuple[bool, Optional[str], Optional[str]]:
    """Check if Claude Code CLI is available and get its version.

    Returns:
        Tuple of (is_available, path, version)
    """
    return check_agent_cli("claude-code")


def require_tmux() -> str:
    """Ensure tmux is available, raise if not.

    Returns:
        Path to tmux executable

    Raises:
        TmuxNotFoundError: If tmux is not found
    """
    return _require_executable(
        check_tmux,
        TmuxNotFoundError,
        "tmux is required but not found. "
        "Install it with: brew install tmux (macOS) or apt install tmux (Linux)",
    )


def require_claude() -> str:
    """Ensure Claude Code CLI is available, raise if not.

    Returns:
        Path to claude executable

    Raises:
        AgentCliNotFoundError: If claude is not found
    """
    return require_agent_cli("claude-code")
