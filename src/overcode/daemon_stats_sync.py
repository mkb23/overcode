"""Transcript stats for the monitor daemon: tokens, cost, energy, model, PR.

``StatsSync`` reads each agent's transcripts through its backend's stats
reader and stages what changed (tokens, cost and energy, the model,
provider, effort and persona, the PR number) on the tick's
``PendingUpdates``. It also binds agent session ids after ``/clear`` and
keeps the transcript-derived values views render beside the token columns.
"""

from datetime import datetime
from typing import Dict, Optional

from .backends import BackendCapability, get_backend, session_backend_name
from .claude_pid import is_session_id_owned_by_others
from .clock import SYSTEM_CLOCK, Clock, stamp
from .energy import estimate_energy_joules
from . import conversation_spend as spend
from .session_manager import PendingUpdates
from .stats_reader import AgentSessionStats, stats_reader_for_session


class StatsSync:
    """One daemon's transcript-stats sync.

    ``stats_views`` (by session id) holds what the last sync read for the
    views: stats availability, median work time, context window, subagent
    count. ``last_commands`` holds each agent's last prompt, and
    ``transcript_prs`` the last pr-link PR number seen (the pane-text PR
    extraction stands back for those agents).
    """

    def __init__(self, *, session_manager, log, clock: Optional[Clock] = None):
        self.session_manager = session_manager
        self.log = log
        self.clock: Clock = clock if clock is not None else SYSTEM_CLOCK
        self.stats_views: Dict[str, dict] = {}
        self.last_commands: Dict[str, str] = {}  # session_id → last user prompt
        # Last pr-link PR number seen per session id (see sync_pr)
        self.transcript_prs: Dict[str, int] = {}

    def sync_session_id(self, session) -> None:
        """Detect and bind Claude session IDs (#116, #373).

        For newly launched agents, the launcher prescribes --session-id at
        launch and immediately binds it. This method handles two scenarios:

        1. Post-/clear detection: after /clear, Claude restarts with a new
           sessionId that appears in history.jsonl.
        2. Unmatched prescribed IDs: when Claude Code doesn't honor
           --session-id (e.g. after restart), the actual sessionIds need
           to be discovered from history.jsonl.

        Uses an ownership guard to prevent cross-contamination when multiple
        agents share the same working directory — a sessionId already owned
        by another agent is never stolen.

        Runs every 10 seconds.
        """
        if not session.start_directory:
            return

        reader = stats_reader_for_session(session)
        try:
            session_start = datetime.fromisoformat(session.start_time)

            # Fast path: discover the most recent sessionId for this directory.
            # Handles post-/clear detection.
            # Every tmux session's agents: one in another session can be
            # working in the same directory. A reader's directory fallback
            # skips their conversations; the check below is the backstop.
            all_sessions = self.session_manager.list_sessions()
            current_id = reader.get_current_session_id(
                session, session_start, all_sessions=all_sessions
            )
            if current_id:
                if not is_session_id_owned_by_others(current_id, session.id, all_sessions):
                    self.session_manager.add_agent_session_id(session.id, current_id)
                    self.session_manager.set_active_agent_session_id(session.id, current_id)

            # Slow path: if the agent has owned session IDs but they produced
            # zero tokens in the last stats sync, scan history.jsonl for ALL
            # unowned sessionIds in this directory and adopt them.  This
            # recovers from --session-id not being honored by Claude Code.
            owned_ids = session.agent_session_ids or []
            stats = session.stats
            has_zero_tokens = (
                owned_ids
                and stats.input_tokens == 0
                and stats.output_tokens == 0
                and stats.last_stats_update is not None  # at least one sync happened
            )
            if has_zero_tokens:
                self.discover_all_session_ids(session, session_start, reader)
        except (ValueError, TypeError):
            pass

    def discover_all_session_ids(
        self, session, session_start: datetime, reader=None
    ) -> None:
        """Adopt unowned agent session ids the backend can see on disk.

        When the prescribed --session-id wasn't honored by Claude Code,
        the agent's actual sessionIds are unknown.  The reader scans for
        ids matching this agent's directory+timestamp that no other agent
        owns; this method persists what it finds.
        """
        reader = reader or stats_reader_for_session(session)
        all_sessions = self.session_manager.list_sessions()

        discovered = reader.discover_session_ids(session, session_start, all_sessions)

        for sid in discovered.ids:
            self.session_manager.add_agent_session_id(session.id, sid)
            self.log.info(f"[{session.name}] Discovered unowned sessionId: {sid[:8]}...")

        if discovered.latest:
            self.session_manager.set_active_agent_session_id(
                session.id, discovered.latest
            )

    def sync_model(self, session, detected: Optional[str], pending: PendingUpdates) -> None:
        """Follow a model change seen in the transcript (e.g. /model).

        Only a change counts: after `restart --model`, the resumed
        transcript still ends on the old model until the agent replies,
        and must not undo the new choice (#505).
        """
        if detected and detected != session.transcript_model:
            pending.update_session(
                session.id, model=detected, transcript_model=detected
            )

    def sync_pr(self, session, pr_number: Optional[int], pending: PendingUpdates) -> None:
        """Take the PR from the transcript's pr-link records (#489).

        That is the number Claude Code's status line shows, and it is
        there even when no PR URL is on screen. Applied only when it
        changes, so a branch switch that cleared the PR is not undone.
        """
        if pr_number is None:
            return
        if self.transcript_prs.get(session.id) != pr_number:
            self.transcript_prs[session.id] = pr_number
            if pr_number != session.pr_number:
                pending.update_session(
                    session.id, pr_number=pr_number, pr_branch=session.branch
                )

    def apply_container_stats(
        self, session, stats: AgentSessionStats, pending: PendingUpdates
    ) -> None:
        """Persist stats read from inside a container agent's filesystem."""
        detected_model = stats.model
        detected_provider = stats.provider
        self.note_stats_view(session, stats)

        # Update model/provider if detected
        self.sync_model(session, detected_model, pending)
        if detected_provider and detected_provider != session.provider:
            pending.update_session(session.id, provider=detected_provider)
        if stats.effort and stats.effort != session.effort:
            pending.update_session(session.id, effort=stats.effort)

        self._stage_usage(session, stats, pending)

    def sync_agent_stats(self, session, pending: PendingUpdates) -> None:
        """Sync token/interaction stats from the backend's transcripts.

        Backends without readable transcripts get a NullStatsReader, which
        answers "unknown" for everything — nothing is written, so their
        columns render placeholders instead of zeros.
        """
        reader = stats_reader_for_session(session)
        try:
            # Container agents: read stats via docker exec
            if session.wrapper:
                container_stats = reader.get_container_stats(session)
                if container_stats is not None:
                    self.apply_container_stats(session, container_stats, pending)
                    return

            # Session ID detection also runs here for the first sync
            self.sync_session_id(session)

            stats = reader.get_stats(session)
            self.note_stats_view(session, stats)
            if stats is None:
                return

            # Update session-level model and provider from history files.
            # Provider is detected from the assistant message ID prefix
            # ("msg_bdrk_" = bedrock, "msg_" = web), which stays correct
            # across /clear (fixing cases where a bedrock agent switches
            # to Claude Max and vice versa).
            self.sync_model(session, stats.model, pending)
            self.sync_pr(session, stats.pr_number, pending)
            if stats.provider and stats.provider != session.provider:
                pending.update_session(session.id, provider=stats.provider)
            if stats.effort and stats.effort != session.effort:
                pending.update_session(session.id, effort=stats.effort)

            # Cache last command for daemon state publishing
            if stats.last_command:
                self.last_commands[session.id] = stats.last_command

            # Some backends record what the provider actually charged (opencode
            # keeps a per-session `cost`). That beats a pricing-table estimate,
            # so it wins when present; a zero/absent figure falls back to it.
            # A reader that splits by conversation reports it per conversation.
            stored_cost = None
            stored_cost_reader = getattr(reader, "get_stored_cost", None)
            if stored_cost_reader is not None and not isinstance(
                    getattr(stats, "per_conversation", None), dict):
                stored_cost = stored_cost_reader(session)

            # The stats object's fallback recovers the persona the agent is
            # actually running. For backends that honor launch-time --agent,
            # only fill an empty persona — never clobber the launcher's
            # choice. For backends without AGENT_INJECTION (opencode2, codex),
            # a stored persona was never applied to the CLI, so the detected
            # persona is the truth and replaces it.
            detected_agent = getattr(stats, "agent", None)
            if detected_agent:
                honors_launch_agent = bool(
                    get_backend(session_backend_name(session)).capabilities
                    & BackendCapability.AGENT_INJECTION
                )
                if honors_launch_agent:
                    if not getattr(session, "agent_persona", None):
                        pending.update_session(
                            session.id, agent_persona=detected_agent
                        )
                else:
                    pending.update_session(
                        session.id, agent_persona=detected_agent
                    )

            self._stage_usage(
                session, stats, pending, stored_cost=stored_cost,
                interaction_count=stats.interaction_count,
            )
        except Exception as e:
            self.log.warn(f"Failed to sync stats for {session.name}: {e}")

    def _stage_usage(
        self, session, stats: AgentSessionStats, pending: PendingUpdates,
        stored_cost: Optional[float] = None, **extra,
    ) -> None:
        """Stage tokens, cost and energy from one read of ``stats``.

        The spend is kept per conversation (``conversation_spend``): the
        conversations the read covers are updated, never lowered, and the
        totals are their sum, so a conversation the reader no longer
        covers keeps its spend and a briefly low read changes nothing. New
        tokens are priced at the model the conversation was read at (else
        the session's; unknown models fall back to the global config),
        unless the backend recorded a non-zero cost. A reader that does not
        split its read by conversation (``per_conversation`` None) is one
        pseudo conversation, with ``stored_cost`` as its recorded cost.
        ``extra`` stats fields are staged with them.
        """
        from .settings import get_user_config, get_model_pricing

        config = get_user_config()
        provider = stats.provider or session.provider
        default_model = stats.model or session.model

        def prices(model):
            mp = get_model_pricing(model, config, provider=provider)
            return (mp.input, mp.output, mp.cache_write, mp.cache_read)

        def energy(model, tokens):
            return estimate_energy_joules(
                model, tokens["in"], tokens["out"], tokens["cw"], tokens["cr"])

        reads = getattr(stats, "per_conversation", None)
        if not isinstance(reads, dict):
            reads = {spend.TOTAL_KEY: spend.usage(
                stats.input_tokens, stats.output_tokens,
                stats.cache_creation_tokens, stats.cache_read_tokens,
                model=stats.model, cost=stored_cost)}
        recorded = session.stats
        entries = spend.merge(
            getattr(recorded, "conversations", None), reads,
            prices=prices, energy=energy, default_model=default_model,
            recorded=spend.recorded_totals(recorded),
            owned_ids=_owned_conversations(session),
            seg=spend.count(getattr(session, "billing_seg", 0)),
        )
        usage = dict(
            extra,
            **spend.totals(entries),
            conversations=entries,
            current_context_tokens=stats.current_context_tokens,
            last_stats_update=stamp(self.clock.wall()),
        )
        pending.update_stats(session.id, **usage)

    def note_stats_view(self, session, stats: Optional[AgentSessionStats]) -> None:
        """Keep the transcript-derived values views render, from one sync.

        ``stats`` None means the backend's reader reports nothing (its
        columns render a dash), not a failed read: failures raise and leave
        the previous values in place.
        """
        if stats is None:
            self.stats_views[session.id] = {"stats_available": False}
            return
        def number(value, default):
            return value if isinstance(value, (int, float)) else default

        self.stats_views[session.id] = {
            "stats_available": True,
            "work_median_seconds": round(number(stats.median_work_time, 0.0), 1),
            "context_window": number(stats.max_context_tokens, None),
            "file_subagent_count": number(stats.live_subagent_count, 0),
        }


def _owned_conversations(session) -> list:
    """The agent conversation ids the record holds, the active one last."""
    held = getattr(session, "agent_session_ids", None)
    ids = [sid for sid in (held if isinstance(held, (list, tuple)) else ())
           if isinstance(sid, str) and sid]
    active = getattr(session, "active_agent_session_id", None)
    if isinstance(active, str) and active and active not in ids:
        ids.append(active)
    return ids
