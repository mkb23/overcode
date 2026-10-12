"""
Git context of an agent: repo name and branch, and the focal repo (#170).

An agent's ``repo_name`` / ``branch`` come from its start directory, or
from the focal repo, one of the git repos directly under the start
directory of a multi-repo workspace. The module functions read the disk
(and fall back to ``git``); ``GitContextMixin`` is the part of
``SessionManager`` that stores the answers on a session.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from .session_manager import Session

logger = logging.getLogger(__name__)


_HEADS_PREFIX = "ref: refs/heads/"


def _looks_like_object_id(text: str) -> bool:
    return len(text) in (40, 64) and all(c in "0123456789abcdef" for c in text)


def read_git_context_from_disk(directory: str) -> Optional[tuple[Optional[str], Optional[str]]]:
    """Return ``(repo_name, branch)`` for ``directory`` by reading ``.git`` files.

    A subprocess-free equivalent of ``git rev-parse --show-toplevel`` plus
    ``git branch --show-current``, for the monitor daemon's per-agent
    per-tick refresh:

    - walks up from ``directory`` to the nearest ``.git`` — a directory, or
      the ``gitdir: <path>`` file that worktrees and submodules use;
    - ``repo_name`` is the name of the directory holding that ``.git``
      (the worktree root, exactly what ``--show-toplevel`` reports);
    - ``branch`` is the name behind ``ref: refs/heads/``, or ``""`` for a
      detached HEAD / non-branch ref (``--show-current`` prints nothing).

    Returns ``(None, None)`` when no repository encloses ``directory`` and
    ``None`` when the layout is unrecognised or unreadable — the caller
    then falls back to asking git itself.
    """
    try:
        path = Path(directory).resolve()
    except OSError:
        return None

    for candidate in (path, *path.parents):
        dot_git = candidate / ".git"
        try:
            if dot_git.is_dir():
                git_dir = dot_git
            elif dot_git.is_file():
                text = dot_git.read_text().strip()
                if not text.startswith("gitdir:"):
                    return None
                git_dir = Path(text[len("gitdir:"):].strip())
                if not git_dir.is_absolute():
                    git_dir = (candidate / git_dir).resolve()
                if not git_dir.is_dir():
                    return None
            else:
                continue
            content = (git_dir / "HEAD").read_text().strip()
        except OSError:
            return None

        if content.startswith(_HEADS_PREFIX):
            branch = content[len(_HEADS_PREFIX):]
        elif content.startswith("ref: ") or _looks_like_object_id(content):
            branch = ""
        else:
            return None
        return candidate.name, branch

    return None, None


def detect_focal_repo_candidates(start_directory: Optional[str]) -> List[str]:
    """Return one-layer-deep git-repo subdir names under ``start_directory`` (#170).

    Multi-repo workspaces are detected by scanning the immediate
    children of ``start_directory`` for entries that contain ``.git``.
    Returns the list of subdir *names* (relative to start_directory),
    sorted alphabetically.

    If ``start_directory`` is itself a git repo, returns an empty list:
    the user is in a single-repo situation and there's nothing to cycle.
    Same for missing / unreadable directories.
    """
    if not start_directory:
        return []
    try:
        if not os.path.isdir(start_directory):
            return []
        # If start_directory itself is a repo, this is single-repo.
        if os.path.isdir(os.path.join(start_directory, ".git")) or \
                os.path.isfile(os.path.join(start_directory, ".git")):
            return []
        candidates: List[str] = []
        for entry in os.listdir(start_directory):
            if entry.startswith("."):
                continue
            child = os.path.join(start_directory, entry)
            if not os.path.isdir(child):
                continue
            git_marker = os.path.join(child, ".git")
            # Either a directory (normal clone) or a file (worktree / submodule)
            if os.path.isdir(git_marker) or os.path.isfile(git_marker):
                candidates.append(entry)
        return sorted(candidates)
    except OSError:
        return []


def resolve_focal_directory(
    start_directory: Optional[str], focal_subdir: Optional[str]
) -> Optional[str]:
    """Return the effective directory for git-stat reads (#170).

    When ``focal_subdir`` is set and exists under ``start_directory``,
    returns the joined path. Otherwise falls back to start_directory.
    """
    if not start_directory:
        return None
    if not focal_subdir:
        return start_directory
    candidate = os.path.join(start_directory, focal_subdir)
    if os.path.isdir(candidate):
        return candidate
    return start_directory


def detect_git_context(directory: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Detect git repo and branch from directory.

    Reads ``.git/HEAD`` directly when it can (no subprocess — the monitor
    daemon calls this for every agent every 2s) and only shells out to
    git when the on-disk layout is something it doesn't understand.
    """
    if not directory:
        return None, None

    # Check directory exists
    if not os.path.isdir(directory):
        return None, None

    fast = read_git_context_from_disk(directory)
    if fast is not None:
        return fast

    try:
        # Get repo name
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=2
        )
        repo_path = result.stdout.strip() if result.returncode == 0 else None
        repo_name = Path(repo_path).name if repo_path else None

        # Get branch
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=2
        )
        branch = result.stdout.strip() if result.returncode == 0 else None

        return repo_name, branch
    except subprocess.TimeoutExpired:
        logger.warning("git command timed out in %s", directory)
        return None, None
    except subprocess.CalledProcessError as e:
        logger.warning("git command failed: %s", e)
        return None, None
    except (OSError, IOError) as e:
        logger.warning("could not detect git context: %s", e)
        return None, None


class GitContextMixin:
    """Git context on a session (see module docstring).

    Uses the manager's ``get_session`` and ``update_session``.
    """

    detect_focal_repo_candidates = staticmethod(detect_focal_repo_candidates)
    resolve_focal_directory = staticmethod(resolve_focal_directory)

    def _detect_git_context(self, directory: Optional[str]) -> tuple[Optional[str], Optional[str]]:
        """``detect_git_context``; a method so a test can stub one manager's."""
        return detect_git_context(directory)

    def read_git_context(self, session: Session) -> tuple[Optional[str], Optional[str]]:
        """``(repo_name, branch)`` of ``session``'s focal repo, else its start directory, right now.

        The detection half of ``refresh_git_context`` with no write: the
        daemon compares the answer with the session it holds and stages
        the change for its per-tick commit. It must follow the focal repo,
        or every tick undoes ``set_focal_repo`` (#494).
        """
        return self._detect_git_context(self.resolve_focal_directory(
            session.start_directory, getattr(session, "focal_repo_subdir", None)))

    def refresh_git_context(self, session_id: str) -> bool:
        """Refresh git repo/branch info for a session.

        Detects current branch from the session's focal repo (else its
        start_directory) and updates the session if it has changed.

        Returns:
            True if git context was updated, False otherwise
        """
        session = self.get_session(session_id)
        if not session or not session.start_directory:
            return False

        repo_name, branch = self.read_git_context(session)

        # Only update if something changed
        if repo_name != session.repo_name or branch != session.branch:
            self.update_session(
                session_id,
                repo_name=repo_name,
                branch=branch
            )
            return True
        return False

    def set_focal_repo(self, session_id: str, focal_subdir: Optional[str]) -> Optional[str]:
        """Set the focal repo subdir for a multi-repo workspace agent (#170).

        - ``focal_subdir=None`` clears the focal (back to start_directory).
        - Otherwise the subdir must be one of the candidates returned by
          ``detect_focal_repo_candidates(start_directory)``; an unknown
          value is rejected with a ``ValueError``.

        Updates the session's ``repo_name`` and ``branch`` to reflect the
        new effective directory so downstream renders pick it up without
        plumbing extra context.

        Returns the new focal_subdir (or None on clear), or None if the
        session is missing.
        """
        session = self.get_session(session_id)
        if session is None:
            return None
        if focal_subdir is not None:
            candidates = self.detect_focal_repo_candidates(session.start_directory)
            if not candidates:
                raise ValueError(
                    f"'{session.name}' is a single-repo workspace — nothing to focus."
                )
            if focal_subdir not in candidates:
                raise ValueError(
                    f"'{focal_subdir}' is not one of the focal candidates: "
                    f"{', '.join(candidates)}"
                )
        new_dir = self.resolve_focal_directory(session.start_directory, focal_subdir)
        new_repo, new_branch = self._detect_git_context(new_dir)
        self.update_session(
            session_id,
            focal_repo_subdir=focal_subdir,
            repo_name=new_repo,
            branch=new_branch,
        )
        return focal_subdir

    def cycle_focal_repo(self, session_id: str) -> Optional[str]:
        """Advance the focal repo to the next candidate (#170).

        Returns the new focal subdir, or None when the agent is single-repo
        (nothing to cycle). Wraps around at the end of the list. If no
        focal is currently set, picks the first candidate.
        """
        session = self.get_session(session_id)
        if session is None:
            return None
        candidates = self.detect_focal_repo_candidates(session.start_directory)
        if not candidates:
            return None
        current = session.focal_repo_subdir
        if current in candidates:
            idx = (candidates.index(current) + 1) % len(candidates)
        else:
            idx = 0
        return self.set_focal_repo(session_id, candidates[idx])
