"""The recorded layer (#507): colour episodes, blip merging, and the bell.

See docs/design/engine-0.6.md, "Recorded layer". The live colour is whatever
the detector said this tick; the recorded colour is what history, timers,
the timeline and the bell use. They differ only while an excursion is
pending:

- The recorded episode has a colour and a start.
- When the live colour leaves it, an **excursion** begins. If the live
  colour comes back to the recorded colour within ``merge_seconds`` (G) of
  the excursion's start, the excursion is merged. The recorded episode
  carries on as if it never left, and the excursion is kept as a blip
  (e.g. for a 👤 tick on the timeline).
- If the excursion lasts G without returning, it is **confirmed**. The
  recorded episode closes at the excursion's start, and each colour the
  excursion passed through becomes its own episode with its real times.
- An excursion that settles on another colour still waits for G. A blip of
  red between green and yellow is history (a short red episode), not a
  merge, because merging only applies to a return to where it came from.

A **gap** — no coloured observation for longer than ``gap_seconds``
(the daemon frozen or stopped, the machine asleep, or the agent asleep or
terminated in between) — is not attributed to any colour. The open
episode closes when the agent was last seen, a pending excursion stands
as observed, and the colour after the gap opens a fresh episode by the
first-sight rule (no bell: when it began is unknown). A red or orange
stretch that is still red or orange after the gap carries on as the same
stretch, so a visited stall is not shown as unvisited again.

The **bell** rings once per stretch of input-needed colour (red or orange)
on the recorded layer. It rings when such a stretch is confirmed, and only
if it began after the person last visited the agent. orange → red inside
one stretch does not ring twice.

Everything is driven by ``observe(colour, now)``: no wall clock and no I/O,
so the replay harness and the restart replay feed it directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .monitor_daemon_core import UNOBSERVED_GAP_SECONDS
from .status_constants import (
    STATUS_COLOR_GREEN,
    STATUS_COLOR_ORANGE,
    STATUS_COLOR_RED,
    STATUS_COLOR_YELLOW,
)

# G: an excursion that returns within this many seconds is a blip (#507;
# 20 s merges 14% of red/orange episodes in three weeks of history).
EPISODE_MERGE_SECONDS = 20.0

INPUT_NEEDED = frozenset({STATUS_COLOR_RED, STATUS_COLOR_ORANGE})
COLOURS = frozenset({STATUS_COLOR_GREEN, STATUS_COLOR_YELLOW, STATUS_COLOR_ORANGE,
                     STATUS_COLOR_RED})


@dataclass(frozen=True)
class Episode:
    colour: str
    start: float
    end: Optional[float] = None  # None while it is the open episode
    # Excursions merged into this episode: (colour, start, end)
    blips: Tuple[Tuple[str, float, float], ...] = ()

    def to_dict(self) -> dict:
        return {"colour": self.colour, "start": self.start, "end": self.end,
                "blips": [list(b) for b in self.blips]}


@dataclass(frozen=True)
class Bell:
    colour: str
    start: float  # when the input-needed stretch began


@dataclass
class Observation:
    """What one ``observe`` produced."""
    closed: List[Episode] = field(default_factory=list)  # newly closed episodes, oldest first
    merged: Optional[Tuple[str, float, float]] = None  # an excursion just merged as a blip
    bell: Optional[Bell] = None


class EpisodeRecorder:
    """One agent's recorded colour history."""

    def __init__(self, merge_seconds: float = EPISODE_MERGE_SECONDS,
                 visited_at: Optional[float] = None,
                 gap_seconds: float = UNOBSERVED_GAP_SECONDS) -> None:
        self.merge_seconds = merge_seconds
        self.gap_seconds = gap_seconds
        self.visited_at = visited_at
        # When a colour was last observed (lifecycle states and unknowns
        # don't count): what a gap is measured from
        self.last_seen: Optional[float] = None
        self.episode: Optional[Episode] = None  # the open recorded episode
        # The pending excursion, as segments [(colour, start)], oldest first
        self._excursion: List[Tuple[str, float]] = []
        self._blips: List[Tuple[str, float, float]] = []
        # Start of the current recorded input-needed stretch, if in one
        self._input_needed_since: Optional[float] = None
        self._rang_for: Optional[float] = None

    # -- reading --------------------------------------------------------

    @property
    def live_colour(self) -> Optional[str]:
        if self._excursion:
            return self._excursion[-1][0]
        return self.episode.colour if self.episode else None

    @property
    def live_since(self) -> Optional[float]:
        """When the live colour began: what a time-in-state counter shows.

        During a pending excursion this is the excursion segment's own start.
        Once an excursion merges it snaps back to the recorded episode's.
        """
        if self._excursion:
            return self._excursion[-1][1]
        return self.episode.start if self.episode else None

    @property
    def pending(self) -> bool:
        return bool(self._excursion)

    @property
    def input_needed_since(self) -> Optional[float]:
        """Start of the recorded input-needed stretch the agent is in, if any.

        With ``visited_at`` it says whether the person has looked since the
        agent started needing them: the views' unvisited highlight.
        """
        return self._input_needed_since

    # -- persistence ------------------------------------------------------

    def to_dict(self) -> dict:
        """What survives an engine restart: the open episode, the pending
        excursion, the stretch being tracked, what already rang, the last
        visit, and when a colour was last seen (so a restart after a long
        downtime is read as a gap, not as the same episode carrying on)."""
        return {
            "episode": self.episode.to_dict() if self.episode else None,
            "excursion": [list(segment) for segment in self._excursion],
            "input_needed_since": self._input_needed_since,
            "rang_for": self._rang_for,
            "visited_at": self.visited_at,
            "last_seen": self.last_seen,
        }

    @classmethod
    def from_dict(cls, data: dict, merge_seconds: float = EPISODE_MERGE_SECONDS,
                  gap_seconds: float = UNOBSERVED_GAP_SECONDS) -> "EpisodeRecorder":
        rec = cls(merge_seconds=merge_seconds, gap_seconds=gap_seconds)
        if not isinstance(data, dict):
            return rec
        ep = data.get("episode")
        if isinstance(ep, dict) and ep.get("colour") in COLOURS and isinstance(
                ep.get("start"), (int, float)):
            blips = tuple(tuple(b) for b in ep.get("blips") or () if len(b) == 3)
            rec.episode = Episode(ep["colour"], float(ep["start"]), None, blips)
            rec._blips = list(blips)
            excursion = data.get("excursion")
            if isinstance(excursion, list):
                rec._excursion = [
                    (segment[0], float(segment[1])) for segment in excursion
                    if isinstance(segment, list) and len(segment) == 2
                    and segment[0] in COLOURS and isinstance(segment[1], (int, float))
                ]
        for name in ("input_needed_since", "rang_for", "visited_at", "last_seen"):
            value = data.get(name)
            if isinstance(value, (int, float)):
                setattr(rec, "_" + name if name in ("input_needed_since", "rang_for") else name,
                        float(value))
        return rec

    # -- writing --------------------------------------------------------

    def visit(self, now: float) -> None:
        """The person looked at this agent: later stretches may ring again."""
        self.visited_at = now

    def observe(self, colour: Optional[str], now: float) -> Observation:
        out = Observation()
        if colour not in COLOURS:
            # Lifecycle states (terminated, asleep) and unknowns carry no
            # colour: hold everything as it is.
            return out
        last_seen, self.last_seen = self.last_seen, now
        if (self.episode is not None and last_seen is not None
                and now - last_seen > self.gap_seconds):
            self._close_at_gap(out, last_seen)
        if self.episode is None:
            # First sight, or the first sight after a gap: when this colour
            # really began is unknown (the restart replay supplies it when
            # history exists), so it is recorded from now and never rings.
            # A restart must not ring every red agent's bell (d679fff).
            self.episode = Episode(colour, now)
            if colour in INPUT_NEEDED:
                if self._input_needed_since is None:
                    self._input_needed_since = now
                    self._rang_for = now
            else:
                self._input_needed_since = None
            return out

        recorded = self.episode.colour
        if not self._excursion:
            if colour != recorded:
                self._excursion.append((colour, now))
            return out

        # An excursion is pending
        start = self._excursion[0][1]
        if colour == recorded:
            if now - start <= self.merge_seconds:
                blip = (self._excursion[0][0], start, now)
                self._blips.append(blip)
                self.episode = Episode(recorded, self.episode.start, None, tuple(self._blips))
                self._excursion = []
                out.merged = blip
                return out
            # Back, but too late to be a blip: what happened stands (and is
            # already over, so it doesn't ring), and the return is an
            # episode of its own.
            self._confirm(out, current=False)
            self._begin(colour, now, out)
            return out

        if colour != self._excursion[-1][0]:
            self._excursion.append((colour, now))
        if now - start >= self.merge_seconds:
            self._confirm(out)
        return out

    # -- internals ------------------------------------------------------

    def _close_at_gap(self, out: Observation, last_seen: float) -> None:
        """Nothing was observed after ``last_seen``: close the record there.

        A pending excursion stands as far as it was seen (without ringing:
        it is over by the time anyone hears of it). The open episode then
        ends at ``last_seen``, and the next observation opens a fresh one.
        The input-needed stretch is left for the caller to carry on or end.
        """
        if self._excursion:
            self._confirm(out, current=False)
        ep = self.episode
        if ep is not None and last_seen > ep.start:
            out.closed.append(Episode(ep.colour, ep.start, last_seen, tuple(self._blips)))
        self._blips = []
        self.episode = None

    def _confirm(self, out: Observation, current: bool = True) -> None:
        """The excursion stood: its segments become episodes.

        The merge rule applies inside the excursion too: a colour that came
        and went between two stretches of the same colour within G (an
        orange prompt answered while the agent waits yellow) is a blip of
        that colour's episode, not an episode of its own. Only the colour
        the agent is in now (``current``) may ring: a red that was over
        before the excursion stood is history, not news.
        """
        segments = [[colour, start, []] for colour, start in self._excursion]
        self._excursion = []
        i = 1
        while i + 1 < len(segments):
            before, mid, after = segments[i - 1], segments[i], segments[i + 1]
            if before[0] == after[0] and after[1] - mid[1] <= self.merge_seconds:
                before[2] += mid[2] + [(mid[0], mid[1], after[1])] + after[2]
                del segments[i:i + 2]
                i = max(1, i - 1)
            else:
                i += 1
        for i, (colour, start, blips) in enumerate(segments):
            self._begin(colour, start, out,
                        announce=current and i == len(segments) - 1)
            if blips:
                self._blips = list(blips)
                self.episode = Episode(colour, start, None, tuple(blips))

    def _begin(self, colour: str, start: float, out: Observation, announce: bool = True) -> None:
        """Close the open episode at ``start`` and open ``colour`` from there."""
        ep = self.episode
        if ep is not None and start > ep.start:
            out.closed.append(Episode(ep.colour, ep.start, start, tuple(self._blips)))
        self._blips = []
        self.episode = Episode(colour, start)
        if colour in INPUT_NEEDED:
            if self._input_needed_since is None:
                self._input_needed_since = start
            stretch = self._input_needed_since
            if announce and self._rang_for != stretch and (
                self.visited_at is None or stretch > self.visited_at
            ):
                self._rang_for = stretch
                out.bell = Bell(colour, stretch)
        else:
            self._input_needed_since = None


# ── publishing and the episode log ───────────────────────────────────────

# An agent's episode log is rotated to .1 past this size (one generation
# kept): a busy agent closes a few hundred episodes a day at ~120 bytes each.
EPISODE_LOG_MAX_BYTES = 2 << 20


def status_detail_view(detail, now: float) -> Optional[dict]:
    """A StatusDetail as the engine publishes it.

    A badge's countdown (``eta_seconds``) shrinks every tick; publishing its
    absolute ``eta_at`` instead keeps an unchanged agent's fields unchanged,
    so a waiting fleet produces no deltas. Views count down from it.
    """
    if detail is None:
        return None
    badges = []
    for badge in detail.badges:
        entry = {"kind": badge.kind}
        if badge.label:
            entry["label"] = badge.label
        if badge.count != 1:
            entry["count"] = badge.count
        if badge.eta_seconds is not None:
            entry["eta_at"] = round(now + badge.eta_seconds)
        badges.append(entry)
    return {"color": detail.color, "badges": badges, "legacy_status": detail.legacy_status}


def episode_log_path(state_dir, agent_name: str):
    from pathlib import Path

    return Path(state_dir) / f"episodes_{agent_name}.jsonl"


def append_episode(state_dir, agent_name: str, session_id: str, episode: Episode) -> None:
    """Append one closed episode to the agent's log; never raises."""
    import json
    import os

    path = episode_log_path(state_dir, agent_name)
    record = dict(episode.to_dict(), session_id=session_id)
    try:
        try:
            if path.stat().st_size > EPISODE_LOG_MAX_BYTES:
                os.replace(path, path.with_suffix(".jsonl.1"))
        except FileNotFoundError:
            pass
        with open(path, "a") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
    except OSError:
        pass


def read_episodes(state_dir, agent_name: str) -> List[dict]:
    """Every logged episode for an agent, oldest first (rotated file included)."""
    import json

    path = episode_log_path(state_dir, agent_name)
    out: List[dict] = []
    for candidate in (path.with_suffix(".jsonl.1"), path):
        try:
            with open(candidate) as f:
                for line in f:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            continue
    return out
