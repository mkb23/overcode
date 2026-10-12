"""Git diff and untracked-file counts for an agent's working directory.

The engine samples these on its git cadence (attended only) and publishes
them; ``overcode list``, ``show`` and the sister API read them too.
"""

import os
import re
import subprocess
from typing import Optional, Tuple


def effective_git_directory(obj) -> Optional[str]:
    """Return the directory git stat helpers should sample for ``obj`` (#170).

    ``obj`` is anything carrying ``start_directory`` and (optionally)
    ``focal_repo_subdir`` attributes — i.e. a Session or SessionDaemonState.
    When focal is set and resolves to an existing subdir, returns the
    joined path; otherwise returns ``start_directory`` (which may be None).
    """
    start = getattr(obj, 'start_directory', None)
    if not start:
        return None
    focal = getattr(obj, 'focal_repo_subdir', None)
    if not focal:
        return start
    candidate = os.path.join(start, focal)
    return candidate if os.path.isdir(candidate) else start


def get_git_untracked_count(directory: str) -> Optional[int]:
    """Count untracked files in a git repository (respecting .gitignore).

    Args:
        directory: Path to the git repository

    Returns:
        Untracked file count, or None if not a git repo / git is unavailable.
    """
    try:
        result = subprocess.run(
            # -C, not cwd=: a cwd forces fork+exec over posix_spawn (spawn.py)
            ["git", "-C", directory, "ls-files", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode != 0:
            return None
        out = result.stdout.strip()
        if not out:
            return 0
        return out.count("\n") + 1
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def get_git_diff_stats(directory: str) -> Optional[Tuple[int, int, int]]:
    """Get git diff stats for a directory.

    Args:
        directory: Path to the git repository

    Returns:
        Tuple of (files_changed, insertions, deletions) or None if not a git repo
    """
    try:
        result = subprocess.run(
            # -C, not cwd=: a cwd forces fork+exec over posix_spawn (spawn.py)
            ["git", "-C", directory, "diff", "--stat", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode != 0:
            return None

        # Parse the last line which looks like:
        # "3 files changed, 10 insertions(+), 5 deletions(-)"
        # or just "1 file changed, 2 insertions(+)"
        lines = result.stdout.strip().split('\n')
        if not lines or not lines[-1]:
            return (0, 0, 0)  # No changes

        summary = lines[-1]
        files = 0
        insertions = 0
        deletions = 0

        files_match = re.search(r'(\d+) files? changed', summary)
        ins_match = re.search(r'(\d+) insertions?', summary)
        del_match = re.search(r'(\d+) deletions?', summary)

        if files_match:
            files = int(files_match.group(1))
        if ins_match:
            insertions = int(ins_match.group(1))
        if del_match:
            deletions = int(del_match.group(1))

        return (files, insertions, deletions)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
