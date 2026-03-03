"""
Scan for Claude Code agent definitions (.md files in .claude/agents/).
"""

from pathlib import Path
from typing import List


def scan_agents(directory: str) -> List[str]:
    """Scan for available Claude agent definitions.

    Checks both project-level (.claude/agents/) and user-level (~/.claude/agents/)
    directories for .md files. Returns deduplicated, sorted list of agent names
    (filenames without .md extension).

    Args:
        directory: Project directory to scan for project-level agents.

    Returns:
        Sorted list of agent names. Empty list if none found.
    """
    names: set = set()

    # Project-level agents
    project_dir = Path(directory) / ".claude" / "agents"
    if project_dir.is_dir():
        for f in project_dir.iterdir():
            if f.is_file() and f.suffix == ".md":
                names.add(f.stem)

    # User-level agents
    user_dir = Path.home() / ".claude" / "agents"
    if user_dir.is_dir():
        for f in user_dir.iterdir():
            if f.is_file() and f.suffix == ".md":
                names.add(f.stem)

    return sorted(names)
