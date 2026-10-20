"""The warning an agent carries when a conversation overcode never saw ran in its name.

The hook handler writes ``conversation_warning`` into the agent's hook state
(``hook_handler._place``): ``{"kind", "session_id", "at", "previous"?,
"owner"?}``. ``kind`` is

* ``adopted``  — the agent's own CLI moved to a conversation overcode did not
  start (``/resume`` into it): it is the agent, so overcode now tracks it and
  ``restart`` resumes it;
* ``shared``   — the same, into a conversation another live agent owns: the
  agent's status follows it, but the record stays where it was;
* ``unplaced`` — a CLI overcode can't place sent events in the agent's name:
  they are ignored.

The detector shows it as a status-detail badge for ``SHOW_SECONDS`` after
it was written (a value that holds still, so a quiet fleet sends no
delta), and ``overcode doctor`` lists the recent ones.

Not a warning, but shown the same way: ``bg_worker`` in hook state,
``{"session_id", "pane_pid", "at"}``, while the agent's own conversation
runs in Claude's background worker (``hook_handler``, ``FOREIGN_CONTINUED``):
a 🌙 badge for as long as it is there.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

# How long the badge shows after the warning was (last) written
SHOW_SECONDS = 30 * 60
# How far back ``overcode doctor`` looks
DOCTOR_SECONDS = 24 * 60 * 60

ADOPTED = "adopted"
SHARED = "shared"
UNPLACED = "unplaced"

# Status-detail badge kind per warning kind (status_constants.BADGE_KINDS)
BADGE_KIND = {ADOPTED: "conv_adopted", SHARED: "conv_shared", UNPLACED: "conv_unplaced"}


def current(hook_state: Any, now: float, within: float = SHOW_SECONDS) -> Optional[dict]:
    """The agent's warning if it was written in the last ``within`` seconds, else None."""
    if not isinstance(hook_state, Mapping):
        return None
    warning = hook_state.get("conversation_warning")
    if not isinstance(warning, Mapping) or warning.get("kind") not in BADGE_KIND:
        return None
    try:
        at = float(warning.get("at") or 0.0)
    except (TypeError, ValueError):
        return None
    if now - at >= within or not isinstance(warning.get("session_id"), str):
        return None
    return dict(warning)


def short(session_id: Any) -> str:
    return str(session_id or "")[:8]


def text(warning: Mapping[str, Any]) -> str:
    """One line for a person: what happened and what overcode did about it."""
    kind = warning.get("kind")
    sid = short(warning.get("session_id"))
    previous = short(warning.get("previous"))
    if kind == ADOPTED:
        was = f" (was {previous})" if previous else ""
        return (f"resumed conversation {sid}, which overcode did not start: "
                f"now tracked as this agent's{was}")
    if kind == SHARED:
        back = f"; restart returns to {previous}" if previous else ""
        return (f"in conversation {sid}, which belongs to agent {warning.get('owner')}: "
                f"not adopted{back}")
    return (f"conversation {sid} ran under this agent's name, but overcode can't tell "
            "whose it is: its events are ignored")


def badge(warning: Mapping[str, Any]):
    """The status-detail badge for ``warning``."""
    from .status_constants import StatusBadge

    label = short(warning.get("session_id"))
    if warning.get("kind") == SHARED and warning.get("owner"):
        label = f"{warning['owner']}'s {label}"
    return StatusBadge(BADGE_KIND[warning["kind"]], label=label)


BG_WORKER_KIND = "bg_worker"


def bg_worker(hook_state: Any) -> Optional[dict]:
    """The agent's conversation in Claude's background worker, while it is its current one."""
    if not isinstance(hook_state, Mapping):
        return None
    note = hook_state.get("bg_worker")
    if not isinstance(note, Mapping) or not isinstance(note.get("session_id"), str):
        return None
    if note["session_id"] != hook_state.get("agent_session_id"):
        return None
    return dict(note)


def bg_worker_badge(note: Mapping[str, Any]):
    """The status-detail badge for a ``bg_worker`` note."""
    from .status_constants import StatusBadge

    return StatusBadge(BG_WORKER_KIND, label=short(note.get("session_id")))
