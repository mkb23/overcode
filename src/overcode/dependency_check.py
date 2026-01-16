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


def get_dependency_status() -> dict:
    """Get status of all dependencies.

    Returns:
        Dict with dependency info:
        {
            "tmux": {"available": bool, "path": str, "version": str},
            "claude": {"available": bool, "path": str, "version": str},
        }
    """
    tmux_ok, tmux_path, tmux_ver = check_tmux()
    claude_ok, claude_path, claude_ver = check_claude()

    return {
        "tmux": {
            "available": tmux_ok,
            "path": tmux_path,
            "version": tmux_ver,
        },
        "claude": {
            "available": claude_ok,
            "path": claude_path,
            "version": claude_ver,
        },
    }


def print_dependency_status():
    """Print dependency status to console."""
    status = get_dependency_status()

    print("Dependency Status:")
    print("-" * 40)

    for name, info in status.items():
        if info["available"]:
            version = info["version"] or "unknown version"
            print(f"  {name}: ✓ {version}")
            print(f"         Path: {info['path']}")
        else:
            print(f"  {name}: ✗ Not found")

    print("-" * 40)


class DependencyContext:
    """Context manager that checks dependencies before use.

    Example:
        with DependencyContext(require_tmux=True, require_claude=True):
            # Code that needs both tmux and claude
            pass

        # With graceful handling:
        with DependencyContext(require_tmux=True, on_missing="warn"):
            # Will warn but continue if tmux missing
            pass
    """

    def __init__(
        self,
        require_tmux: bool = False,
        require_claude: bool = False,
        on_missing: str = "raise"
    ):
        """Initialize the dependency context.

        Args:
            require_tmux: Whether tmux is required
            require_claude: Whether claude is required
            on_missing: What to do if dependency missing:
                        "raise" (default), "warn", "ignore"
        """
        self.require_tmux = require_tmux
        self.require_claude = require_claude
        self.on_missing = on_missing
        self._missing = []

    def __enter__(self):
        if self.require_tmux:
            try:
                require_tmux()
            except TmuxNotFoundError as e:
                self._handle_missing("tmux", e)

        if self.require_claude:
            try:
                require_claude()
            except ClaudeNotFoundError as e:
                self._handle_missing("claude", e)

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def _handle_missing(self, name: str, error: Exception):
        """Handle a missing dependency based on on_missing setting."""
        self._missing.append(name)

        if self.on_missing == "raise":
            raise error
        elif self.on_missing == "warn":
            import warnings
            warnings.warn(str(error), UserWarning)
        # "ignore" does nothing

    @property
    def missing_dependencies(self) -> list:
        """List of missing dependencies."""
        return self._missing.copy()

    @property
    def all_available(self) -> bool:
        """Whether all required dependencies are available."""
        return len(self._missing) == 0
