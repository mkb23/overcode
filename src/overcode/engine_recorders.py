"""The engine's recorded colour per agent (#507): episodes, bells, visits.

``RecorderStore`` keeps one ``EpisodeRecorder`` per agent (by session id),
feeds it each detected colour, publishes the recorded fields on the agent's
state, appends closed episodes to the episode log and rings the bell on
engine.sock. It persists to ``engine_state.json`` beside the state file, so
a restart neither forgets an open episode nor rings again.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional

from .clock import SYSTEM_CLOCK, Clock
from .episodes import EpisodeRecorder, append_episode, status_detail_view
from .monitor_daemon_state import SessionDaemonState
from .status_constants import (
    AT_REST_STATUSES,
    STATUS_ASLEEP,
    STATUS_COLOR_REST,
    STATUS_TERMINATED,
    STATUS_UNKNOWN,
    StatusDetail,
)

# Recorder state (engine_state.json) is written when it changes, and at
# least this often while agents are observed: each recorder's last
# observation time must stay well inside UNOBSERVED_GAP_SECONDS on disk, or
# a quick restart would read as a gap and split every agent's episode.
RECORDER_SEEN_SAVE_SECONDS = 30.0


class RecorderStore:
    """Every agent's recorder, persisted in ``directory``/engine_state.json.

    ``gap_seconds`` answers the daemon's unobserved-gap length (asked when
    a recorder is made or loaded, as the configured loop can change it).
    """

    def __init__(self, directory: Path, log, gap_seconds: Callable[[], float],
                 clock: Optional[Clock] = None):
        self.directory = directory
        # The daemon's clock: its monotonic side paces the liveness saves
        self.clock: Clock = clock if clock is not None else SYSTEM_CLOCK
        self.log = log
        self._gap_seconds = gap_seconds
        # One recorded-colour history per agent (#507), by session id;
        # restored from the engine state file so a restart neither forgets
        # nor re-rings (see load)
        self.recorders: Dict[str, EpisodeRecorder] = {}
        self.dirty = False
        # monotonic time of the last engine_state.json write
        self.saved_at = 0.0

    @property
    def path(self) -> Path:
        return self.directory / "engine_state.json"

    def record(self, session, state: SessionDaemonState, effective_status: str,
               now: datetime, *, detector, engine=None) -> None:
        """Feed this tick's colour to the agent's recorder; publish, log, ring (#507)."""
        from .hook_status_detector import augment_with_legacy_heartbeat

        try:
            detail = detector.get_status_detail(session.name)
        except Exception:
            detail = None
        detail = augment_with_legacy_heartbeat(detail, effective_status)
        if effective_status in AT_REST_STATUSES:
            # A child at rest, reported or not. A done child is not
            # detected at all, so whatever detail is cached is stale.
            detail = StatusDetail(STATUS_COLOR_REST, [], effective_status)
        ts = now.timestamp()
        # Lifecycle states (and not knowing) hold the record where it was
        colour = None if effective_status in (STATUS_TERMINATED, STATUS_ASLEEP, STATUS_UNKNOWN) \
            else (detail.color if detail else None)
        recorder = self.recorders.get(session.id)
        if recorder is None:
            recorder = self.recorders[session.id] = EpisodeRecorder(
                gap_seconds=self._gap_seconds())
            self.dirty = True
        observed = recorder.observe(colour, ts)
        if observed.closed or observed.bell or observed.merged:
            self.dirty = True
        state.live_colour = recorder.live_colour
        state.live_since = recorder.live_since
        state.episode_colour = recorder.episode.colour if recorder.episode else None
        state.episode_start = recorder.episode.start if recorder.episode else None
        state.status_detail = status_detail_view(detail, ts) if colour else None
        state.input_needed_since = recorder.input_needed_since
        state.visited_at = recorder.visited_at
        for episode in observed.closed:
            append_episode(self.directory, session.name, session.id, episode)
        if observed.bell is not None:
            self.log.info(f"[{session.name}] bell: {observed.bell.colour} since "
                          f"{datetime.fromtimestamp(observed.bell.start).isoformat()}")
            if engine is not None:
                engine.ring(session.id, {"colour": observed.bell.colour,
                                         "start": observed.bell.start, "name": session.name})

    def load(self) -> None:
        """Restore each agent's recorded episode, rung stretch and last visit."""
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return
        recorders = data.get("recorders") if isinstance(data, dict) else None
        if isinstance(recorders, dict):
            gap = self._gap_seconds()
            self.recorders = {
                sid: EpisodeRecorder.from_dict(rec, gap_seconds=gap)
                for sid, rec in recorders.items()
            }

    def save(self, live_ids=None, force: bool = False) -> None:
        """Persist recorder state when it changed; forget agents that are gone.

        Each recorder's last observation time changes every tick without
        marking it dirty; it is written at least every
        RECORDER_SEEN_SAVE_SECONDS (and at shutdown, ``force``), so a
        restarted daemon can tell a short restart from a long gap. An
        empty ``live_ids`` (a session index that came back empty) forgets
        nobody.
        """
        stale = (bool(self.recorders) and self.clock.monotonic()
                 - self.saved_at >= RECORDER_SEEN_SAVE_SECONDS)
        if not (self.dirty or force or stale):
            return
        if live_ids:
            for sid in [s for s in self.recorders if s not in live_ids]:
                del self.recorders[sid]
        path = self.path
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(
                {"recorders": {sid: r.to_dict() for sid, r in self.recorders.items()}}))
            os.replace(tmp, path)
            self.dirty = False
            self.saved_at = self.clock.monotonic()
        except OSError as e:
            self.log.warn(f"engine state not saved: {e}")

    def apply_visits(self, engine: Optional[object]) -> None:
        """Record the visits views reported on engine.sock since the last call."""
        if engine is None:
            return
        for sid, ts in engine.take_visits():
            recorder = self.recorders.get(sid)
            if recorder is not None:
                recorder.visit(ts)
                self.dirty = True
