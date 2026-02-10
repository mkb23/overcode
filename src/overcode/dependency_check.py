"""
Dependency checking and graceful degradation utilities.

Provides functions to check for required external dependencies (tmux, claude)
and handle graceful degradation when they're missing.
"""

import shutil
import subprocess
from typing import Optional, Tuple

from .exceptions import TmuxNotFoundError, ClaudeNotFoundError


def find_executable(name: str) -> Optional[str]:
    """Find the path to an executable.

    Args:
        name: Name of the executable

    Returns:
        Full path to executable, or None if not found
    """
    return shutil.which(name)


def check_tmux() -> Tuple[bool, Optional[str], Optional[str]]:
    """Check if tmux is available and get its version.

    Returns:
        Tuple of (is_available, path, version)
    """
    path = find_executable("tmux")
    if not path:
        return False, None, None

    try:
        result = subprocess.run(
            ["tmux", "-V"],
            capture_output=True,
            text=True,
            timeout=5
        )
        version = result.stdout.strip() if result.returncode == 0 else None
        return True, path, version
    except (subprocess.SubprocessError, OSError):
        return True, path, None


def check_claude() -> Tuple[bool, Optional[str], Optional[str]]:
    """Check if Claude Code CLI is available and get its version.

    Returns:
        Tuple of (is_available, path, version)
    """
    path = find_executable("claude")
    if not path:
        return False, None, None

    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            # Parse version from output like "Claude Code v2.0.75"
            version = result.stdout.strip()
            return True, path, version
        return True, path, None
    except (subprocess.SubprocessError, OSError):
        return True, path, None


def require_tmux() -> str:
    """Ensure tmux is available, raise if not.

    Returns:
        Path to tmux executable

    Raises:
        TmuxNotFoundError: If tmux is not found
    """
    available, path, _ = check_tmux()
    if not available:
        raise TmuxNotFoundError(
            "tmux is required but not found. "
            "Install it with: brew install tmux (macOS) or apt install tmux (Linux)"
        )
    return path


def require_claude() -> str:
    """Ensure Claude Code CLI is available, raise if not.

    Returns:
        Path to claude executable

    Raises:
        ClaudeNotFoundError: If claude is not found
    """
    available, path, _ = check_claude()
    if not available:
        raise ClaudeNotFoundError(
            "Claude Code CLI is required but not found. "
            "Install it from: https://claude.ai/claude-code"
        )
    return path


