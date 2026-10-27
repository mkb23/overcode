"""The cost-budget gate on text typed into an agent (#530).

An agent whose estimated cost has reached its budget (``cost_budget_usd``)
takes no more prompts from overcode: ``overcode send``, the TUI's command
bar and reinject, the web API, the supervisor (which sends with
``overcode send``), standing instructions, a parent's messages and the
release notice all go through ``AgentIOMixin._send_to_resolved_session``,
which asks :func:`refusal` first. Heartbeats are skipped by the daemon
(``is_heartbeat_eligible``). Keys and gestures (Enter, Escape, approve,
reject, a menu number from the TUI, text without Enter) always go through:
they answer or stop the agent, they don't give it work.

The gate is in overcode, not in the agent, so it holds for every backend:
Claude's ``UserPromptSubmit`` hook also refuses (``hook_handler``), but the
opencode plugins have no way to, and a refusal here tells the sender why
before anything is typed.

A refused send stamps ``budget_rejected_at`` on the agent; while the
budget stays exceeded and the agent is not working, the daemon shows it red
with the "rejected" badge Claude's hook gives (``rejection_applies``), and
clears the stamp once the budget is raised or cleared.

A person can still send on purpose (``overcode send --over-budget``). The
send then leaves a short-lived permit in the agent's directory, which
Claude's hook takes (:func:`take_permit`) so its own check lets that one
prompt through too.

Stdlib only at import: the hook path imports :func:`take_permit`.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

# How long an --over-budget permit waits for the prompt it was written for
PERMIT_SECONDS = 120.0

REJECTED_ACTIVITY = "Prompt blocked: over its cost budget"


def is_budget_exceeded(session, stats=None) -> bool:
    """Whether ``session``'s estimated cost has reached its budget (#173).

    ``stats`` defaults to the session's own.
    """
    try:
        budget = session.cost_budget_usd
        cost = (stats if stats is not None else session.stats).estimated_cost_usd
        return isinstance(budget, (int, float)) and budget > 0 and cost >= budget
    except (AttributeError, TypeError):
        return False


def raise_hint(session, sessions=None) -> str:
    """The command that lets ``session`` spend more.

    A child whose parent has a budget gets its budget from the parent
    (``overcode budget set`` refuses it, #364).
    """
    parent_id = getattr(session, "parent_session_id", None)
    if parent_id and sessions is not None:
        try:
            parent = sessions.get_session(parent_id)
        except Exception:
            parent = None
        if parent is not None and (getattr(parent, "cost_budget_usd", 0) or 0) > 0:
            return f"overcode budget transfer {parent.name} {session.name} <amount>"
    return f"overcode budget set {session.name} <amount>"


def refusal(session, sessions=None) -> Optional[str]:
    """Why a prompt to ``session`` is refused, or None when it may be sent."""
    if getattr(session, "is_remote", False) is True:
        return None  # its own host's overcode gates it
    if not is_budget_exceeded(session):
        return None
    cost = session.stats.estimated_cost_usd
    budget = session.cost_budget_usd
    return (f"'{session.name}' is over its cost budget (${cost:.2f} of ${budget:.2f}): "
            f"not sent. Raise it with: {raise_hint(session, sessions)}")


def refusal_of(launcher) -> Optional[str]:
    """Why ``launcher``'s last send was refused for the budget, or None."""
    reason = getattr(launcher, "send_refusal", None)
    return reason if isinstance(reason, str) and reason else None


def is_prompt(text: str, enter: bool, gestures=()) -> bool:
    """Whether sending ``text`` (+ Enter when ``enter``) submits a prompt.

    Not a prompt: nothing submitted (no Enter), Enter alone, or a named
    key or gesture (``gestures``, lower case).
    """
    if not enter:
        return False
    stripped = text.strip()
    return bool(stripped) and stripped.lower() not in gestures


def refused_at(session) -> Optional[str]:
    """When a prompt to ``session`` was last refused (``budget_rejected_at``), or None."""
    stamp = getattr(session, "budget_rejected_at", None)
    return stamp if isinstance(stamp, str) and stamp else None


def rejection_applies(session, status: str) -> bool:
    """Whether the daemon shows ``session`` as rejected this tick.

    A refused send is on record, the budget is still exceeded and the
    agent is not working (a send made with --over-budget, or one typed into
    the pane, shows as work as usual).
    """
    from .status_constants import STATUS_ASLEEP, STATUS_TERMINATED, STATUS_UNKNOWN, is_green_status

    if not refused_at(session):
        return False
    if status in (STATUS_TERMINATED, STATUS_ASLEEP, STATUS_UNKNOWN) or is_green_status(status):
        return False
    return is_budget_exceeded(session)


def rejected_detail():
    """The status detail of a rejected prompt: red, "rejected" (as Claude's hook)."""
    from .status_constants import STATUS_COLOR_RED, STATUS_ERROR, StatusBadge, StatusDetail

    return StatusDetail(STATUS_COLOR_RED, [StatusBadge(kind="error", label="rejected")], STATUS_ERROR)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_permit(tmux_session: str, session_id: str, now: Optional[float] = None) -> None:
    """Let the next prompt past the agent's own budget check (``take_permit``)."""
    from .paths import agent_file

    path = agent_file(tmux_session, session_id, "budget_permit")
    path.parent.mkdir(parents=True, exist_ok=True)
    expires = (time.time() if now is None else now) + PERMIT_SECONDS
    path.write_text(f"{expires:.0f}\n", encoding="utf-8")


def take_permit(tmux_session: str, session_id: Optional[str], now: Optional[float] = None) -> bool:
    """Consume the agent's --over-budget permit; True when one was there and fresh."""
    if not session_id:
        return False
    from .paths import agent_file

    path = agent_file(tmux_session, session_id, "budget_permit")
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return False  # none (the common case)
    try:
        path.unlink()
    except OSError:
        pass
    try:
        expires = float(text)
    except ValueError:
        return False
    return (time.time() if now is None else now) <= expires
