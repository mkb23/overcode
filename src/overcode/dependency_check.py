"""
Dependency checking and graceful degradation utilities.

Provides functions to check for required external dependencies (tmux, claude)
and handle graceful degradation when they're missing.
"""

import shutil
import subprocess
from typing import Callable, Optional, Tuple, Type

from .exceptions import TmuxNotFoundError, ClaudeNotFoundError


def find_executable(name: str) -> Optional[str]:
    """Find the path to an executable.

    Args:
        name: Name of the executable

    Returns:
        Full path to executable, or None if not found
    """
    return shutil.which(name)


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


def check_claude() -> Tuple[bool, Optional[str], Optional[str]]:
    """Check if Claude Code CLI is available and get its version.

    Returns:
        Tuple of (is_available, path, version)
    """
    return _check_executable("claude", ["--version"], timeout=10)


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
        ClaudeNotFoundError: If claude is not found
    """
    return _require_executable(
        check_claude,
        ClaudeNotFoundError,
        "Claude Code CLI is required but not found. "
        "Install it from: https://claude.ai/claude-code",
    )
