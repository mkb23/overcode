"""
Summary line group definitions for the TUI.

Defines which fields belong to each group and their default visibility.
"""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class SummaryGroup:
    """Definition of a summary line column group."""

    id: str
    name: str
    fields: List[str]
    default_enabled: bool = True
    always_visible: bool = False  # identity group is always visible


# Group definitions - order matters for display in configurator
SUMMARY_GROUPS: List[SummaryGroup] = [
    SummaryGroup(
        id="identity",
        name="Identity",
        fields=["status_symbol", "agent_name", "expand_icon", "unvisited_alert"],
        default_enabled=True,
        always_visible=True,  # Cannot be toggled off
    ),
    SummaryGroup(
        id="sisters",
        name="Sisters",
        fields=["host"],
        default_enabled=True,
    ),
    SummaryGroup(
        id="time",
        name="Time",
        fields=["time_in_state", "sleep_countdown", "uptime", "running_time", "stalled_time", "sleep_time", "active_pct"],
        default_enabled=True,
    ),
    SummaryGroup(
        id="llm_usage",
        name="Budget",
        fields=["token_count", "cost", "budget"],
        default_enabled=True,
    ),
    SummaryGroup(
        id="context",
        name="Context",
        fields=["context_usage"],
        default_enabled=True,
    ),
    SummaryGroup(
        id="git",
        name="Git",
        fields=["repo_branch", "files_changed", "insertions", "deletions"],
        default_enabled=True,
    ),
    SummaryGroup(
        id="supervision",
        name="Supervision",
        fields=["permission_mode", "allowed_tools", "time_context", "human_count", "robot_count", "standing_orders", "heartbeat"],
        default_enabled=True,
    ),
    SummaryGroup(
        id="priority",
        name="Priority",
        fields=["agent_value", "priority_chevrons"],
        default_enabled=True,
    ),
    SummaryGroup(
        id="performance",
        name="Performance",
        fields=["median_work_time"],
        default_enabled=True,
    ),
    SummaryGroup(
        id="subprocesses",
        name="Subprocesses",
        fields=["subagent_count", "bash_count"],
        default_enabled=True,
    ),
]


# Quick lookup by group ID
SUMMARY_GROUPS_BY_ID: Dict[str, SummaryGroup] = {g.id: g for g in SUMMARY_GROUPS}


# Presets for common configurations
PRESETS: Dict[str, Dict[str, bool]] = {
    "minimal": {
        "sisters": True,
        "time": False,
        "llm_usage": True,
        "context": True,
        "git": False,
        "supervision": False,
        "priority": False,
        "performance": False,
        "subprocesses": False,
    },
    "standard": {
        "sisters": True,
        "time": True,
        "llm_usage": True,
        "context": True,
        "git": False,
        "supervision": True,
        "priority": True,
        "performance": False,
        "subprocesses": True,
    },
    "full": {
        "sisters": True,
        "time": True,
        "llm_usage": True,
        "context": True,
        "git": True,
        "supervision": True,
        "priority": True,
        "performance": True,
        "subprocesses": True,
    },
}


def get_default_group_visibility() -> Dict[str, bool]:
    """Get the default visibility settings for all toggleable groups."""
    return {
        group.id: group.default_enabled
        for group in SUMMARY_GROUPS
        if not group.always_visible
    }


def get_toggleable_groups() -> List[SummaryGroup]:
    """Get list of groups that can be toggled (excludes always_visible groups)."""
    return [g for g in SUMMARY_GROUPS if not g.always_visible]
