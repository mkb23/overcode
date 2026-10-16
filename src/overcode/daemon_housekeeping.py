"""The monitor daemon's slow chores, on their own wall-clock cadences.

Every HOUSEKEEPING_INTERVAL_SECONDS: auto-archive agents done for their delay,
count untracked tmux windows, move long-terminated sessions to the archive
and prune finished jobs. Hourly: rotate agent_status_history.csv and, when
opted in, refresh the models.dev catalog on a background thread. None of it
may fail a tick.
"""

import threading
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, TYPE_CHECKING

from .clock import SYSTEM_CLOCK, Clock, aware
from .daemon_cadence import Cadence
from .session_manager import PendingUpdates
from .status_constants import STATUS_TERMINATED
from .monitor_daemon_core import elapsed_seconds, should_archive_terminated, should_auto_archive
from .tmux_utils import untracked_window_names

if TYPE_CHECKING:
    from .protocols import TmuxInterface

# Finished jobs older than jobs.retention_hours are dropped from jobs.json
# this often (it otherwise grew without limit)
JOB_PRUNE_INTERVAL_SECONDS = 3600.0

# At most this many records are archived per housekeeping pass: each packs
# its agent's directory into the day bundle on the tick's thread. The rest
# wait for the next pass (their grace clock keeps running).
ARCHIVE_PER_PASS = 50

# The orphan sweep and bundle retention (SessionManager.tidy_agent_storage)
# run at daemon start and then this often
STORAGE_TIDY_INTERVAL_SECONDS = 3600.0


class Housekeeping:
    """Housekeeping for one daemon: its tmux client, history file and log.

    ``housekeeping``, ``rotation`` and ``model_metadata`` are the daemon's
    cadences for the every-2-minutes pass, the history rotation check and
    the models.dev check. Staged sessions.json changes go to the
    ``pending`` the caller passes, so they join the tick's single write.
    """

    def __init__(
        self,
        tmux_session: str,
        *,
        tmux: "TmuxInterface",
        history_path: Path,
        log,
        housekeeping: Cadence,
        rotation: Cadence,
        model_metadata: Cadence,
        clock: Optional[Clock] = None,
    ):
        self.tmux_session = tmux_session
        # The daemon's clock: wall time for the backoff, monotonic for the
        # hourly passes
        self.clock: Clock = clock if clock is not None else SYSTEM_CLOCK
        self.tmux = tmux
        self.history_path = history_path
        self.log = log
        self._cadence = housekeeping
        self._rotation = rotation
        self._model_metadata = model_metadata

        # When each terminated entry in sessions.json was first seen so by
        # this daemon (any tmux session); after the configured grace the
        # every-60-loops housekeeping moves it to the archive.
        self.terminated_since: Dict[str, datetime] = {}
        # The models.dev fetch runs on a background thread so a
        # packet-dropping network can never stall the loop, and a failure
        # backs off for hours.
        self.model_metadata_thread: Optional[threading.Thread] = None
        self.model_metadata_backoff_until: Optional[datetime] = None
        self.model_metadata_failure_backoff = 6 * 3600  # seconds
        # Finished-job pruning: monotonic time of the last pass (hourly)
        self.last_job_prune: Optional[float] = None
        # Agent storage tidy: monotonic time of the last pass (hourly)
        self.last_storage_tidy: Optional[float] = None
        # Each live agent's last ledger checkpoint: (when, cumulative cost)
        self.last_checkpoint: Dict[str, tuple] = {}
        # Whether this session's storage is on the 0.6 layout (layout_migration)
        self.layout_migrated = False
        # Whether the archive's spend is restated at the current reader
        # version (spend_recompute)
        self.spend_restated = False

    def due(self, now: datetime) -> bool:
        """Wall-clock replacement for ``loop_count % 60 == 0``.

        The first tick starts the clock (the loop-count rule first fired
        two minutes in, not on loop 1); after that every
        HOUSEKEEPING_INTERVAL_SECONDS, at the 2 s loop and the unattended
        one alike. Tests move ``cadence.housekeeping.last`` back to force a pass.
        """
        cadence = self._cadence
        if cadence.last is None:
            cadence.mark(now)
            return False
        if cadence.due(now):
            cadence.mark(now)
            return True
        return False

    def auto_archive_done_agents(self, sessions: list, pending: PendingUpdates,
                                 now: Optional[datetime] = None) -> None:
        """Auto-archive done agents once done for their archive delay (#244).

        The delay is the child's ``archive_after_seconds`` (its preset's,
        ``child_presets``; an hour by default, never when negative). Kills
        the tmux window and marks as terminated so cleanup can remove them.
        """
        from .child_presets import archive_after_of

        now = self.clock.wall() if now is None else now

        for session in sessions:
            if not should_auto_archive(
                session.status,
                session.stats.state_since,
                now,
                timeout_seconds=archive_after_of(session),
            ):
                continue

            # Archive: kill tmux window and mark terminated
            try:
                from .implementations import TMUX_COMMAND_TIMEOUT_SECONDS, RealTmux
                tmux = RealTmux(command_timeout=TMUX_COMMAND_TIMEOUT_SECONDS)
                tmux.kill_window(self.tmux_session, session.tmux_window)
            except Exception:
                pass  # Window may already be gone
            pending.update_session_status(session.id, "terminated")
            self.log.info(f"Auto-archived done agent: {session.name}")

    def count_untracked_windows(self, sessions: list, tmux=None) -> int:
        """Count tmux windows not tracked by any active session (#344).

        Uses the same predicate as ``overcode cleanup --untracked``
        (``tmux_utils.untracked_window_names``): window 0, live agents'
        windows and overcode's own windows (the dead-window placeholder,
        the supervisor daemon's claude window, SSH proxy windows) are not
        untracked, so the count never advertises a cleanup that would kill
        them.

        Args:
            sessions: Sessions from the current tick.
            tmux: A ``TmuxInterface``; the daemon's client when None.
                Injectable so the count can be tested against a fake tmux —
                the previous code called a ``session_exists`` method that
                ``RealTmux`` never had, so it raised on every run and the
                ``except`` below silently reported 0 untracked windows forever.
        """
        try:
            if tmux is None:
                tmux = self.tmux
            if not tmux.has_session(self.tmux_session):
                return 0
            tmux_windows = tmux.list_windows(self.tmux_session)
            active_sessions = [s for s in sessions if s.status != "terminated"]
            tracked_windows = {s.tmux_window for s in active_sessions}
            return len(untracked_window_names(tmux_windows, tracked_windows))
        except Exception:
            return 0

    def archive_terminated_sessions(self, all_sessions, now: datetime, pending: PendingUpdates,
                                    window_gone: frozenset = frozenset()) -> None:
        """Stage terminated entries that have outstayed the grace for the archive.

        ``window_gone`` are the ids of this tmux session's children kept
        waiting_oversight after their window went (published terminated,
        persisted waiting_oversight so the oversight timeout still applies:
        ``MonitorDaemon._is_waiting_on_oversight``). They can never report,
        so they go the same way: left alone, every child that stopped
        without a report stayed in sessions.json for ever, and a follower
        of it waited for ever.

        Runs from the every-60-loops housekeeping over every entry in
        sessions.json, whatever its tmux session: an entry left behind by
        a tmux session with no daemon would otherwise stay forever, and
        every TUI and daemon on the host parses the file whole. The clock
        starts when this daemon first sees the entry terminated (a restart
        starts it again, so an entry waits at most one extra grace) and is
        dropped for an entry that is revived or removed. An entry ``overcode
        shutdown`` stopped (``kept_for_revive``) is never archived: shutdown
        keeps it so ``revive --all`` can bring the agent back. The move itself is
        part of the tick's single commit, with the record ``overcode
        cleanup`` writes.
        """
        from .config import get_session_archive_config

        grace = get_session_archive_config()["terminated_grace_seconds"]
        seen = set()
        staged = 0
        for session in all_sessions:
            if session.status != STATUS_TERMINATED and session.id not in window_gone:
                continue
            if session.kept_for_revive:
                continue  # stopped by `overcode shutdown`, kept for `revive --all`
            seen.add(session.id)
            since = self.terminated_since.setdefault(session.id, now)
            if staged < ARCHIVE_PER_PASS and should_archive_terminated(since, now, grace):
                staged += 1
                pending.archive_session(session.id)
                del self.terminated_since[session.id]
                self.log.info(f"Archived terminated session: {session.name}")
        for session_id in [sid for sid in self.terminated_since if sid not in seen]:
            del self.terminated_since[session_id]

    def checkpoint_spend(self, sessions, ledger, now: datetime) -> int:
        """Ledger ``checkpoint`` rows for agents whose spend moved; returns how many.

        An agent gets one at most every ``ledger.checkpoint_minutes`` (60),
        and only when its spend changed since the last, so a long-running
        agent's spend reaches ``overcode billing`` without waiting for it
        to be archived. The first comes a full interval after this daemon
        first saw the agent (a restart starts the clock again; the rows are
        cumulative, so an extra one is harmless).
        """
        from .config import get_ledger_config
        from .ledger import make_row, spend_of

        every = timedelta(minutes=get_ledger_config()["checkpoint_minutes"])
        rows = []
        seen = set()
        for session in sessions:
            seen.add(session.id)
            cost = spend_of(session)["cost"]
            since, last_cost = self.last_checkpoint.setdefault(session.id, (now, None))
            if (elapsed_seconds(since, now) < every.total_seconds()
                    or cost == last_cost or cost <= 0):
                continue
            rows.append(make_row(session, "checkpoint", now=now))
            self.last_checkpoint[session.id] = (now, cost)
        for session_id in [sid for sid in self.last_checkpoint if sid not in seen]:
            del self.last_checkpoint[session_id]
        if rows:
            try:
                ledger.append(rows, now=now)
            except Exception as e:  # housekeeping must never fail the tick
                self.log.warn(f"spend checkpoint not written: {e}")
                return 0
        return len(rows)

    def tidy_storage(self, session_manager, *, force: bool = False) -> None:
        """Sweep orphaned agent directories and apply bundle retention, hourly.

        ``force`` runs it now (daemon start). Bounded per pass
        (``SessionManager.tidy_agent_storage``); never fails the tick.
        """
        last = self.last_storage_tidy
        if (not force and last is not None
                and self.clock.monotonic() - last < STORAGE_TIDY_INTERVAL_SECONDS):
            return
        self.last_storage_tidy = self.clock.monotonic()
        try:
            counts = session_manager.tidy_agent_storage(self.tmux_session)
        except Exception as e:  # housekeeping must never fail the tick
            self.log.warn(f"agent storage not tidied: {e}")
            return
        done = {k: v for k, v in (counts or {}).items() if v and k != "kept"}
        if done:
            self.log.info(f"Agent storage tidied: {done}")

    def migrate_storage(self, session_manager) -> None:
        """Move this session's storage to the 0.6 layout, one batch per pass.

        Runs at daemon start and on every housekeeping pass until done
        (``layout_migration``); then never again in this process.
        """
        if self.layout_migrated:
            return
        try:
            from .layout_migration import LAYOUT_VERSION, layout_version, migrate_layout

            # An older overcode's archive file (a downgrade, then this
            # upgrade) is taken in first: that resets the markers below
            session_manager._migrate_legacy_archive()
            if (layout_version(self.tmux_session) == LAYOUT_VERSION
                    and session_manager.ledger.backfilled()):
                self.layout_migrated = True
                return
            moved = migrate_layout(self.tmux_session, session_manager)
            self.layout_migrated = bool(moved.get("done"))
            self.log.info(f"Storage layout migration: {moved}")
        except Exception as e:  # housekeeping must never fail the tick
            self.log.warn(f"storage layout not migrated: {e}")

    def restate_archived_spend(self, session_manager) -> None:
        """Restate archived agents' spend after a reader fix, one batch per pass.

        ``spend_recompute``: bounded per call, idempotent, a marker when
        done; then never again in this process.
        """
        if self.spend_restated:
            return
        try:
            from .spend_recompute import recompute_archived_spend

            counts = recompute_archived_spend(session_manager)
        except Exception as e:  # housekeeping must never fail the tick
            self.log.warn(f"archived spend not restated: {e}")
            return
        self.spend_restated = bool(counts.get("done"))
        if counts.get("examined") or counts.get("restated"):
            self.log.info(f"Archived spend restated: {counts}")

    def fill_billing_codes(self, sessions, session_manager) -> list:
        """``sessions`` with a billing code each, giving one to any that has none.

        A record with none was written by an older overcode (a downgrade,
        or one still running after the upgrade, launching a child): it gets
        its parent's code, up to the root, else its name, as the storage
        migration gives them (``layout_migration.fill_billing_codes``).
        Checked every pass, after the migration too, since nothing else
        would; costs nothing when every record has one. The returned list
        carries the new codes, so this pass's checkpoints bill them.
        """
        if all(s.billing_code for s in sessions):
            return list(sessions)
        try:
            from .layout_migration import fill_billing_codes

            filled = fill_billing_codes(session_manager, sessions)
        except Exception as e:  # housekeeping must never fail the tick
            self.log.warn(f"billing codes not filled: {e}")
            return list(sessions)
        if filled:
            self.log.info(f"Billing codes given to {len(filled)} agent(s) an older overcode launched")
        return [replace(s, billing_code=filled[s.id]) if s.id in filled else s
                for s in sessions]

    def prune_finished_jobs(self) -> None:
        """Drop finished jobs past their retention, at most hourly."""
        last = self.last_job_prune
        if last is not None and self.clock.monotonic() - last < JOB_PRUNE_INTERVAL_SECONDS:
            return
        self.last_job_prune = self.clock.monotonic()
        try:
            from .config import get_jobs_retention_hours
            from .job_manager import JobManager

            JobManager().cleanup_completed(get_jobs_retention_hours())
        except Exception as e:  # housekeeping must never fail the tick
            self.log.warn(f"job retention not applied: {e}")

    def maybe_rotate_history(self, now: datetime) -> None:
        """Rotate/compress agent_status_history.csv and prune old archives (#465, #468).

        Runs at most hourly — a size/age stat check is cheap, but there's no
        reason to do even that on every 2-30s tick.
        """
        if not self._rotation.due(now):
            return
        self._rotation.mark(now)
        try:
            from .config import get_history_retention_config
            from .status_history import rotate_and_retain

            cfg = get_history_retention_config()
            result = rotate_and_retain(
                self.history_path,
                rotate_mb=cfg["status_history_rotate_mb"],
                retention_days=cfg["status_history_max_days"],
                now=now,
            )
            if result["archived"]:
                self.log.info(f"Rotated agent_status_history.csv -> {result['archived'].name}")
            if result["deleted"]:
                self.log.info(
                    f"Pruned {len(result['deleted'])} expired agent_status_history archive(s)"
                )
        except Exception as e:
            self.log.error(f"History rotation check failed: {e}")

    def maybe_refresh_model_metadata(self, now: datetime) -> None:
        """Refresh the local models.dev cache when opted in and stale (#473).

        Runs its stat() check at most hourly; the fetch only happens when
        ``model_metadata.auto_refresh`` is true *and* the local cache is
        missing or older than ``max_age_days``. The fetch itself runs on a
        daemon thread — the loop never waits on the network — and a failure
        backs off for ``model_metadata_failure_backoff`` seconds with one
        warning, so an air-gapped or packet-dropping host sees neither a
        stall nor an hourly log line. Lookups keep working off whatever
        catalog is already on disk throughout.
        """
        if not self._model_metadata.due(now):
            return
        self._model_metadata.mark(now)
        thread = self.model_metadata_thread
        if thread is not None and thread.is_alive():
            return
        backoff = self.model_metadata_backoff_until
        if backoff is not None and aware(now) < aware(backoff):
            return
        try:
            from .config import get_model_metadata_config
            from . import model_metadata

            cfg = get_model_metadata_config()
            if not cfg["auto_refresh"]:
                return
            age = model_metadata.local_cache_age_days()
            if age is not None and age < cfg["max_age_days"]:
                return
        except Exception as e:
            self.log.warn(f"Model metadata auto-refresh check failed: {e}")
            return

        thread = threading.Thread(
            target=self._run_model_metadata_refresh,
            name="model-metadata-refresh",
            daemon=True,
        )
        self.model_metadata_thread = thread
        thread.start()

    def _run_model_metadata_refresh(self) -> None:
        """Background body of the auto-refresh: fetch, write atomically, log once."""
        try:
            from . import model_metadata

            info = model_metadata.refresh_local_cache()
            self.model_metadata_backoff_until = None
            self.log.info(
                f"Refreshed model metadata catalog from models.dev: "
                f"{info['model_count']} models -> {info['path']}"
            )
        except Exception as e:
            self.model_metadata_backoff_until = self.clock.wall() + timedelta(
                seconds=self.model_metadata_failure_backoff
            )
            hours = self.model_metadata_failure_backoff / 3600
            self.log.warn(
                f"Model metadata auto-refresh failed; not retrying for {hours:g}h "
                f"(lookups keep using the catalog already on disk): {e}"
            )
