"""
Jump-to-agent candidates and their filter (#420).

The picker itself is the command palette (command_palette.py), which
shows these in its agent and tag modes.
"""

from dataclasses import dataclass
from typing import List


@dataclass
class JumpCandidate:
    """One agent (or tag) row the palette can show."""
    session_id: str
    name: str
    repo: str = ""
    branch: str = ""
    status: str = ""        # status symbol drawn before the name
    status_style: str = ""


def filter_candidates(candidates: List[JumpCandidate], query: str) -> List[JumpCandidate]:
    """Substring-filter candidates by query, case-insensitively.

    Matches against name, repo, and branch. Results are ordered so that
    name matches come first, then repo/branch. Pure function — easy to
    unit-test without a textual app.
    """
    if not query:
        return list(candidates)
    q = query.lower()
    name_hits: List[JumpCandidate] = []
    other_hits: List[JumpCandidate] = []
    for c in candidates:
        if q in c.name.lower():
            name_hits.append(c)
        elif q in c.repo.lower() or q in c.branch.lower():
            other_hits.append(c)
    return name_hits + other_hits
