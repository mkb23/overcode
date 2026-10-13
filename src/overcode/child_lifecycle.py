"""How a child agent's finished turn settles its lifecycle status (#244, #432).

A child (an agent launched with a parent) whose Stop hook has fired is
either *done*, when it has filed a report (``overcode report``), or
*waiting_oversight* until it does. A child that turns done gets its unused
budget refunded to its parent (``SessionManager.reclaim_budget``, #432).

These are the read side and the rule; the writer is the monitor daemon's
tick (``MonitorDaemon._settle_children``). ``AgentLauncher.reconcile_lifecycle``
applies the same rule once for a CLI command run while no daemon is.

Either way the child is *at rest* (⚪): the light says it stopped, and
``task_result`` says how it went (the Result column).
"""

import json
from typing import Optional, Tuple

from .paths import agent_file_name
from .settings import get_session_dir
from .status_constants import (
    AT_REST_STATUSES,
    RESULT_FAILURE,
    RESULT_SUCCESS,
    RESULT_TIMEOUT,
    RESULT_UNREPORTED,
    STATUS_WAITING_APPROVAL,
    STATUS_WAITING_OVERSIGHT,
    is_green_status,
)

# Statuses a stopped child is settled from. "done" is final; anything else
# (a killed or archived record) is not an agent the rule should touch.
SETTLEABLE_STATUSES = frozenset({"running", "starting", "terminated", STATUS_WAITING_OVERSIGHT})

# The report reason an expired oversight timeout files (the daemon and
# follow mode both write it); the Result column shows it as a timeout.
OVERSIGHT_TIMEOUT_REASON = "Oversight timeout expired"


def hook_stopped(tmux_session: str, agent_name: str) -> bool:
    """True if the agent's hook state file shows a Stop event."""
    hook_state_file = get_session_dir(tmux_session) / agent_file_name(agent_name, "hook_state")
    if not hook_state_file.exists():
        return False
    try:
        with open(hook_state_file) as f:
            data = json.load(f)
        return isinstance(data, dict) and data.get("event") == "Stop"
    except (json.JSONDecodeError, OSError):
        return False


def read_report(tmux_session: str, agent_name: str) -> Optional[dict]:
    """The agent's filed report ({'status', 'reason', 'timestamp'}), or None."""
    report_file = get_session_dir(tmux_session) / agent_file_name(agent_name, "report")
    if not report_file.exists():
        return None
    try:
        with open(report_file) as f:
            data = json.load(f)
        if isinstance(data, dict) and "status" in data:
            return data
        return None
    except (json.JSONDecodeError, OSError):
        return None


def settled_child_status(session, stopped: Optional[bool] = None) -> Optional[str]:
    """The status a stopped child should now have, or None to leave it as is.

    "done" with a report, "waiting_oversight" without one; None for an agent
    with no parent, one whose Stop hook has not fired, one in a status the
    rule does not settle from, or one already in the status it would get.
    ``stopped`` is whether its Stop hook fired, when the caller has read
    that already (the daemon, through its detector); read here otherwise.
    """
    if session.parent_session_id is None or session.status not in SETTLEABLE_STATUSES:
        return None
    if stopped is None:
        stopped = hook_stopped(session.tmux_session, session.name)
    if not stopped:
        return None
    if read_report(session.tmux_session, session.name) is not None:
        status = "done"
    else:
        status = STATUS_WAITING_OVERSIGHT
    return status if status != session.status else None


def task_result(session, status: Optional[str] = None) -> Tuple[Optional[str], str]:
    """A child's task outcome for the Result column: (result kind, reason).

    A filed report is ``success`` / ``failure`` (an expired oversight
    timeout is ``timeout``); a child at rest without one is
    ``unreported``. ``status`` is the one published this tick (a child
    whose window has gone publishes terminated but is still at rest).
    (None, "") for a top-level agent and for a child still at work.
    """
    if getattr(session, "parent_session_id", None) is None:
        return None, ""
    reported = getattr(session, "report_status", None)
    reason = getattr(session, "report_reason", "") or ""
    if reported == "failure" and reason == OVERSIGHT_TIMEOUT_REASON:
        return RESULT_TIMEOUT, reason
    if reported in (RESULT_SUCCESS, RESULT_FAILURE):
        return reported, reason
    # Settled at rest and not working again (its window may be gone, or
    # the pane may say it waits)
    at_rest = status in AT_REST_STATUSES or (
        session.status in AT_REST_STATUSES
        and not is_green_status(status or "") and status != STATUS_WAITING_APPROVAL)
    return (RESULT_UNREPORTED, "") if at_rest else (None, "")
