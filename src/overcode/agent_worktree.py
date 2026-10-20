"""
A git worktree of its own for a new agent (the TUI's ``g`` key).

The new agent works on the highlighted agent's repository, starting from
the commit that agent has checked out, on a branch named after itself:

    <main checkout's parent>/<main checkout's name>-wt/<agent name>

The same layout for every worktree of a repository, whichever worktree the
source agent is in, so ``overcode-main`` grows ``overcode-main-wt/<name>``
beside it. A source agent started in a subdirectory of its checkout gets a
sibling started in the same subdirectory of the new worktree.

Uncommitted changes stay where they are: the worktree has the commit only.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

# git worktree add checks out every file: seconds on a big repository.
GIT_TIMEOUT = 60.0


class WorktreeError(Exception):
    """The worktree could not be made; ``str()`` says why, for a person."""


@dataclass(frozen=True)
class AgentWorktree:
    path: str             # the worktree's root
    repository: str       # the main checkout
    branch: str           # the branch checked out there (new)
    base: str             # the commit it starts from
    start_directory: str  # where the agent starts: path, or the same subdirectory


def _git(cwd: str, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True, text=True, timeout=GIT_TIMEOUT,
        )
    except FileNotFoundError as e:
        raise WorktreeError("git is not installed") from e
    except subprocess.TimeoutExpired as e:
        raise WorktreeError(f"git {args[0]} took longer than {GIT_TIMEOUT:.0f}s") from e


def _git_out(cwd: str, *args: str) -> str:
    result = _git(cwd, *args)
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip().splitlines()
        raise WorktreeError(message[-1] if message else f"git {args[0]} failed")
    return result.stdout.strip()


def main_checkout(directory: str) -> Path:
    """The repository's main worktree (the first ``git worktree list`` shows)."""
    for line in _git_out(directory, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            return Path(line[len("worktree "):])
    raise WorktreeError("git worktree list printed no worktrees")


def worktrees_root(main: Path) -> Path:
    """Where a repository's agent worktrees go: ``<parent>/<name>-wt``."""
    return main.parent / f"{main.name}-wt"


def _branch_exists(directory: str, branch: str) -> bool:
    return _git(directory, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0


def create_agent_worktree(source_directory: str, name: str) -> AgentWorktree:
    """Make a worktree for an agent called ``name`` beside ``source_directory``.

    The branch and directory are ``name``, or ``name-2``, ``name-3``… when
    a branch or directory of that name exists already.

    Raises:
        WorktreeError: not a git repository, no commit to start from, or
            git refused.
    """
    if not source_directory or not os.path.isdir(source_directory):
        raise WorktreeError(f"directory '{source_directory}' does not exist")
    inside = _git(source_directory, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise WorktreeError(f"'{source_directory}' is not in a git repository")

    toplevel = _git_out(source_directory, "rev-parse", "--show-toplevel")
    base = _git(source_directory, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    if base.returncode != 0:
        raise WorktreeError("the repository has no commits yet")
    base_commit = base.stdout.strip()

    relative = os.path.relpath(os.path.realpath(source_directory), os.path.realpath(toplevel))
    main = main_checkout(source_directory)
    root = worktrees_root(main)

    branch, n = name, 2
    while _branch_exists(source_directory, branch) or (root / branch).exists():
        branch, n = f"{name}-{n}", n + 1
    path = root / branch

    root.mkdir(parents=True, exist_ok=True)
    _git_out(source_directory, "worktree", "add", "-b", branch, str(path), base_commit)

    start = path if relative == "." else path / relative
    return AgentWorktree(
        path=str(path),
        repository=str(main),
        branch=branch,
        base=base_commit,
        start_directory=str(start if start.is_dir() else path),
    )


def remove_agent_worktree(worktree: AgentWorktree) -> None:
    """Undo ``create_agent_worktree`` (the agent never started): best effort."""
    _git(worktree.repository, "worktree", "remove", "--force", worktree.path)
    _git(worktree.repository, "branch", "-D", worktree.branch)
