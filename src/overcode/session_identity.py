"""
Which backend conversation an agent is in: its session-id history.

A backend mints a new session id whenever an agent resets its context
(``/clear``, auto-compaction); overcode records every id the agent has
owned (``agent_session_ids``) and the one it is in now
(``active_agent_session_id``), which ``restart`` resumes. Hook events
arrive concurrently and out of order, so moving the active id forward is
a decision (``advance_entry``) made inside one locked transaction.

``SessionIdentityMixin`` is the part of ``SessionManager`` that writes
these fields. It uses the manager's ``get_session``, ``_locked_state`` and
``_state_transaction``.
"""

from __future__ import annotations


def advance_entry(
    entry: dict, agent_session_id: str, ordinal: float | None = None,
    *, allow_new: bool = True, allow_first: bool = False, allow_return: bool = False,
) -> tuple[str, str | None, bool]:
    """The decision of ``advance_active_agent_session_id`` on one sessions.json entry.

    Mutates ``entry`` in place when the move is taken. Returns
    ``(outcome, active_agent_session_id, changed)``: the outcome and id the
    method returns, and whether ``entry`` was changed (and so must be
    written). Never returns "unknown"; a missing entry is the caller's.
    """
    ids = entry.get('agent_session_ids')
    if ids is None:
        ids = list(entry.get('claude_session_ids') or [])
    active = (
        entry.get('active_agent_session_id')
        or entry.get('active_claude_session_id')
    )

    unordered = bool(entry.get('active_agent_session_unordered'))

    if active == agent_session_id:
        # Re-evaluation of an earlier unordered advance: the same id
        # arriving with an age attached is what settles it, which is
        # what makes "unconfirmed" recoverable rather than permanent.
        if unordered and ordinal is not None:
            entry['active_agent_session_ordinal'] = ordinal
            entry['active_agent_session_unordered'] = False
            return "advanced", active, True
        if unordered:
            return "current_unordered", active, False
        return "current", active, False

    known = agent_session_id in ids
    if known and allow_return:
        # An explicit resume of a conversation this agent already
        # owns: history order says nothing about which one is live.
        entry['active_agent_session_id'] = agent_session_id
        entry.pop('active_claude_session_id', None)  # a pre-0.6 twin
        if ordinal is not None:
            entry['active_agent_session_ordinal'] = ordinal
        else:
            entry.pop('active_agent_session_ordinal', None)
        entry['active_agent_session_unordered'] = ordinal is None
        return ("advanced" if ordinal is not None else "advanced_unordered"), agent_session_id, True

    # Ids are appended the first time they are seen, so one that is
    # already recorded and is no longer the newest belongs to a
    # conversation already left behind — a hook event in flight across
    # a reset carries exactly that.
    if known and ids[-1] != agent_session_id:
        return "superseded", active, False

    # ``allow_first`` admits the conversation a launch starts: the agent's
    # first, or the one after a fresh relaunch of a backend that mints its
    # own ids, whose record still names the conversation it discarded.
    fresh = not active or bool(entry.get('agent_session_fresh_pending'))
    if not known and not (allow_new or (allow_first and fresh)):
        return "rejected", active, False

    # Age check, for a delayed hook whose id has never been seen: two
    # resets in quick succession can have the newer id take the lock
    # first, and history ordering alone would then accept the older id
    # as new. Comparing recorded ages catches it whichever order the
    # locks are acquired in.
    active_ordinal = entry.get('active_agent_session_ordinal')
    has_active_ordinal = isinstance(active_ordinal, (int, float))
    comparable = ordinal is not None and has_active_ordinal
    if comparable and ordinal < active_ordinal:
        return "superseded", active, False
    # Equal ages order nothing: two different conversations with the
    # same transcript mtime cannot be told apart, so accept the move
    # but do not claim it was confirmed.
    ages_tied = comparable and ordinal == active_ordinal

    # Keep the outgoing id in the history so the ordering check above
    # can recognise it as superseded next time. A fresh launch only
    # sets it active, so it is not necessarily there yet.
    if active and active not in ids:
        ids.append(active)
    if agent_session_id not in ids:
        ids.append(agent_session_id)

    entry['agent_session_ids'] = ids
    entry.pop('claude_session_ids', None)  # a pre-0.6 twin
    entry.pop('agent_session_fresh_pending', None)
    entry['active_agent_session_id'] = agent_session_id
    entry.pop('active_claude_session_id', None)  # a pre-0.6 twin
    confirmed = ordinal is not None and not ages_tied
    if confirmed:
        entry['active_agent_session_ordinal'] = ordinal
    else:
        # Nothing to order the next event against. Drop any inherited
        # age rather than comparing a new conversation against an older
        # one's, and flag the record so a later event carrying an age
        # re-evaluates instead of inheriting this uncertainty forever.
        entry.pop('active_agent_session_ordinal', None)
    entry['active_agent_session_unordered'] = not confirmed

    if not confirmed:
        return "advanced_unordered", agent_session_id, True
    return "advanced", agent_session_id, True


class SessionIdentityMixin:
    """Record and advance a session's backend session ids (see module docstring)."""

    def add_agent_session_id(self, session_id: str, agent_session_id: str) -> bool:
        """Add a backend sessionId to a session's owned list if not present.

        This tracks which backend sessionIds belong to this overcode agent,
        enabling accurate context window calculation when multiple agents
        run in the same directory (#119).

        Args:
            session_id: The overcode session ID
            agent_session_id: The backend's own sessionId to add

        Returns:
            True if the sessionId was added, False if already present or session not found
        """
        session = self.get_session(session_id)
        if not session or agent_session_id in session.agent_session_ids:
            return False

        with self._locked_state() as state:
            if session_id in state:
                entry = state[session_id]
                ids = entry.get('agent_session_ids')
                if ids is None:
                    ids = entry.get('claude_session_ids', [])
                if agent_session_id not in ids:
                    ids.append(agent_session_id)
                entry['agent_session_ids'] = ids
                entry.pop('claude_session_ids', None)  # a pre-0.6 twin
        return True

    def set_active_agent_session_id(self, session_id: str, agent_session_id: str):
        """Set the active backend session ID for context tracking (#116).

        Unlike add_agent_session_id which accumulates, this replaces the
        active session. After /clear the backend starts a new session and
        only that session's context window is relevant.

        Carries no ordering information, so any age recorded for the previous
        conversation is dropped rather than left attached to a different id —
        a relaunch sets the active id this way, and a stale age would then be
        compared against the new conversation.
        """
        session = self.get_session(session_id)
        if not session:
            return
        if session.active_agent_session_id == agent_session_id:
            # The daemon re-binds the current id every 10 s per agent; a
            # rebind to the id already on disk is not a write (audit R5).
            return

        with self._locked_state() as state:
            if session_id in state:
                state[session_id]['active_agent_session_id'] = agent_session_id
                state[session_id].pop('active_claude_session_id', None)  # a pre-0.6 twin
                state[session_id].pop('active_agent_session_ordinal', None)
                state[session_id]['active_agent_session_unordered'] = True
                # A fresh launch's conversation, once named, is the one
                # the launch was waiting for.
                state[session_id].pop('agent_session_fresh_pending', None)

    def advance_active_agent_session_id(
        self, session_id: str, agent_session_id: str, ordinal: float | None = None,
        *, allow_new: bool = True, allow_first: bool = False, allow_return: bool = False,
    ) -> tuple[str, str | None]:
        """Move the active backend session id forward, atomically.

        A backend mints a new session id whenever the agent resets its context
        (`/clear`, auto-compaction), and `restart` relaunches with
        `--resume <active_agent_session_id>`, so this field has to follow the
        conversation the agent is actually in.

        The ordering check, the history append and the active-id write all
        happen inside one `_locked_state` transaction, so the decision is made
        against the same state it is written to. Hook invocations for one agent
        can overlap, and doing this as separate locked updates let a stale one
        land after a newer one.

        Which moves are allowed is the caller's to say, from the event (#500):

        ``allow_new``    — may move to an id this record has never seen (an
                           explicit reset: SessionStart with source clear or
                           compact). False for ordinary events, so a nested
                           agent CLI inheriting this agent's env cannot take
                           the record over.
        ``allow_first``  — may take an unseen id when the record has no active
                           id yet (the first id of a backend with no
                           prescribed launch id, e.g. codex).
        ``allow_return`` — may move back to an id already in the history (an
                           explicit resume), which otherwise is refused as
                           superseded.

        ``ordinal`` is a caller-supplied age for the conversation behind
        ``agent_session_id`` — the modification time of its transcript. It is
        recorded alongside the active id and compared on the next call, which
        is what lets a delayed hook be recognised as stale even when its id has
        never been seen before and lock acquisition happens out of event order.
        Callers that cannot supply one degrade to the history-ordering check
        alone.

        Returns ``(outcome, active_agent_session_id)`` where the second element
        is the id the record holds when the transaction ends — read inside the
        lock, so callers never have to fall back on a pre-transaction snapshot
        that a concurrent hook may already have replaced. Outcomes:

            "advanced"           — the record now names ``agent_session_id``
            "advanced_unordered" — advanced, but its age could not be compared
                                   against the previous conversation's, so the
                                   record is flagged unordered and the caller
                                   should treat the sync as unconfirmed
            "current"            — it already did, with a known age
            "current_unordered"  — it already did, but the age is still
                                   unknown, so a later event carrying one can
                                   still settle it
            "superseded"         — ``agent_session_id`` is an older
                                   conversation the agent has left; ignored
            "rejected"           — an unseen id from an event not allowed to
                                   introduce one; ignored
            "unknown"            — no such session
        """
        # Written only when the entry changes (#501): the hook handler calls
        # this on events whose id may already be current, and a rewrite of
        # sessions.json per hook event is the audit's R5 shape.
        with self._state_transaction() as txn:
            entry = txn.state.get(session_id)
            if entry is None:
                return "unknown", None
            outcome, active, changed = advance_entry(
                entry, agent_session_id, ordinal,
                allow_new=allow_new, allow_first=allow_first, allow_return=allow_return,
            )
            if changed:
                txn.dirty = True
            return outcome, active
