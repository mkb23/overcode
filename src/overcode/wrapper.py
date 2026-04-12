"""
Wrapper resolution for agent launch.

A wrapper is a user-provided executable that wraps the claude CLI invocation.
Instead of running `claude --session-id xyz` directly, overcode runs
`wrapper.sh claude --session-id xyz`. The wrapper receives the full claude
command as arguments ($@) and all OVERCODE_* environment variables, plus
OVERCODE_WRAPPER_DIR set to the agent's working directory.

Wrappers are resolved by name or path:
  1. Absolute path — used directly
  2. Relative path (contains /) — resolved from cwd
  3. Bare name — looked up in ~/.overcode/wrappers/ (with or without extension)
"""

import os
from pathlib import Path
from typing import Optional


def _wrappers_dir() -> Path:
    """Return the global wrappers directory (~/.overcode/wrappers/)."""
    base = os.environ.get("OVERCODE_DIR", str(Path.home() / ".overcode"))
    return Path(base) / "wrappers"


def resolve_wrapper(wrapper: str) -> Optional[str]:
    """Resolve a wrapper specification to an absolute executable path.

    Args:
        wrapper: An absolute path, relative path, or bare name.

    Returns:
        Absolute path to the executable wrapper, or None if not found/not executable.
    """
    if not wrapper or not wrapper.strip():
        return None

    wrapper = wrapper.strip()
    path = Path(wrapper)

    # Absolute path
    if path.is_absolute():
        return str(path) if _is_executable(path) else None

    # Relative path (contains a slash)
    if "/" in wrapper:
        resolved = Path(wrapper).resolve()
        return str(resolved) if _is_executable(resolved) else None

    # Bare name — search ~/.overcode/wrappers/
    search_dir = _wrappers_dir()
    if not search_dir.is_dir():
        return None

    # Try exact name, then common extensions
    for suffix in ("", ".sh", ".bash", ".py", ".zsh"):
        candidate = search_dir / f"{wrapper}{suffix}"
        if _is_executable(candidate):
            return str(candidate)

    return None


def _is_executable(path: Path) -> bool:
    """Check that path exists, is a file, and is executable."""
    return path.is_file() and os.access(str(path), os.X_OK)


def list_available_wrappers() -> list[tuple[str, str]]:
    """List wrappers available in ~/.overcode/wrappers/.

    Returns:
        List of (name, path) tuples for each executable file found.
    """
    search_dir = _wrappers_dir()
    if not search_dir.is_dir():
        return []

    results = []
    for entry in sorted(search_dir.iterdir()):
        if _is_executable(entry):
            results.append((entry.stem, str(entry)))
    return results
