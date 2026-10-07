"""
Summary line group definitions for the TUI.

Group membership is declared per-column on SummaryColumn.group in
summary_columns.py. This file only defines the group *metadata* (display
name, default visibility, identity flag) and the group *ordering* used by
the column configurator.

Group ordering here matches the first-appearance order of each group in
SUMMARY_COLUMNS, so the configurator lists groups in the same order the
TUI renders them.
"""

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class SummaryGroup:
    """Definition of a summary line column group."""

    id: str
    name: str
    default_enabled: bool = True
    always_visible: bool = False  # identity group is always visible


# Group ordering — mirrors SUMMARY_COLUMNS first-appearance order.
SUMMARY_GROUPS: List[SummaryGroup] = [
    SummaryGroup(id="identity", name="Identity", always_visible=True),
    SummaryGroup(id="sisters", name="Sisters"),
    SummaryGroup(id="git", name="Git"),
    SummaryGroup(id="time", name="Time"),
    SummaryGroup(id="llm_usage", name="Budget"),
    SummaryGroup(id="context", name="Context"),
    SummaryGroup(id="performance", name="Performance"),
    SummaryGroup(id="subprocesses", name="Subprocesses"),
    SummaryGroup(id="supervision", name="Supervision"),
    SummaryGroup(id="priority", name="Priority"),
]


# Quick lookup by group ID
SUMMARY_GROUPS_BY_ID: Dict[str, SummaryGroup] = {g.id: g for g in SUMMARY_GROUPS}
