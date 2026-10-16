"""Swarm counts history: the timeline's density rows (0.6.1 observability, phase 4).

docs/design/agent-observability-0.6.1.md §5. The monitor daemon appends a
row to ``swarm_history.csv`` (beside ``agent_status_history.csv``, one file
per tmux session) when a parent's delegated-work counts change: one row for
the whole swarm (group ``*``) and one per group (``wf/<run>``, ``tm``,
``sa``, the virtual-row keys of §3.2 without the parent). Counts come from
the hook state behind the 👥 rollup, so a row goes out only when a hook
event moved them, never per tick:

* a quiet swarm (nothing live) writes nothing;
* a swarm with live members that hold still writes one keepalive row per
  group every ``KEEPALIVE_SECONDS``, so a reader knows its state at a
  window's left edge (the *carry*) and a row from a daemon that stopped is
  not read as live for ever (``STALE_AFTER_SECONDS``);
* a group that goes away while live (the parent's record cleared, the agent
  gone) gets one closing row with nothing live.

The file is rotated and compressed with the status history
(``status_history.rotate_and_retain``, from the daemon's hourly
housekeeping). Timestamps are naive local ISO (``clock.stamp``), as there.

The reader (``read_swarm_history``) is incremental (mtime/size cache, tail
reads) and keyed by (agent session id, group), so a renamed agent keeps its
strips. ``density`` turns one key's points into the timeline's slots: the
time-weighted **mean** of live members per slot (never a median), the worst
light seen in it, and whether a member failed in it.
"""

from __future__ import annotations

import csv
import math
import sys
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Deque, Dict, Iterable, List, Mapping, Optional, Tuple

from . import swarm
from .clock import from_iso, stamp

SWARM_HISTORY_FILE = "swarm_history.csv"
TOTAL = "*"  # the whole swarm's row

HEADER = ["timestamp", "agent", "session_id", "group", "kind", "title",
          "running", "yellow", "waiting", "idle", "ok", "failed", "workflows"]

# running, yellow, waiting, idle, ok, failed, workflows (running runs; total only)
COUNT_FIELDS = HEADER[6:]
LIVE_INDEXES = (0, 1, 2, 3)

KEEPALIVE_SECONDS = 300.0
# A live row stands until the next row of its group or this long, whichever
# comes first; a reader's carry looks back as far
STALE_AFTER_SECONDS = 2 * KEEPALIVE_SECONDS

Counts = Tuple[int, int, int, int, int, int, int]
ZERO: Counts = (0, 0, 0, 0, 0, 0, 0)


def live_of(counts: Counts) -> int:
    return sum(counts[i] for i in LIVE_INDEXES)


def _counts(d: Mapping, workflows: int = 0) -> Counts:
    return (int(d.get("running") or 0), int(d.get("yellow") or 0), int(d.get("waiting") or 0),
            int(d.get("idle") or 0), int(d.get("ok") or 0), int(d.get("failed") or 0),
            int(workflows or 0))


def group_counts(hook_state: Optional[dict], rollup: Optional[dict],
                 now: float) -> Dict[str, Tuple[str, str, Counts]]:
    """``{group: (kind, title, counts)}`` for one parent, plus ``*`` for the whole swarm.

    Groups are ``swarm.virtual_rows``'s, built from the hook state alone (no
    journals: a workflow agent goes to the run its SubagentStop named, else
    the newest running run), with no finished rows kept. ``*`` sums the
    groups' live counts and takes ✅ / ❌ and running workflows from the
    rollup (which also counts what was folded away).
    """
    if not isinstance(hook_state, dict) or not isinstance(rollup, dict):
        return {}
    rows, _ = swarm.virtual_rows("", hook_state, now, 0.0)
    out: Dict[str, Tuple[str, str, Counts]] = {}
    live = [0, 0, 0, 0]
    for key, row in rows.items():
        if row.get("row") != swarm.ROW_GROUP:
            continue
        counts = _counts(row.get("counts") or {})
        for i in LIVE_INDEXES:
            live[i] += counts[i]
        out[key.lstrip("/")] = (str(row.get("kind") or ""), str(row.get("title") or ""), counts)
    total = (live[0], live[1], live[2], live[3], int(rollup.get("ok") or 0),
             int(rollup.get("failed") or 0), int(rollup.get("workflows_running") or 0))
    out[TOTAL] = ("swarm", "", total)
    return out


def _freeze(value) -> Optional[tuple]:
    return tuple(sorted(value.items())) if isinstance(value, dict) else None


@dataclass
class _Written:
    counts: Counts
    at: float      # epoch of the row
    kind: str
    title: str


class SwarmHistoryWriter:
    """The daemon's side: one row per group per change (see the module doc).

    ``record`` is called for each agent the tick detected. It does nothing
    (not even count the groups) unless the hook state's timestamp or the
    rollup moved, or a keepalive is due.
    """

    def __init__(self, path: Path, keepalive_seconds: float = KEEPALIVE_SECONDS) -> None:
        self.path = path
        self.keepalive = keepalive_seconds
        self._last: Dict[str, Dict[str, _Written]] = {}
        self._seen: Dict[str, tuple] = {}
        self._names: Dict[str, str] = {}
        self.computes = 0  # group counts built (tests: count the work)
        self.rows_written = 0

    def _keepalive_due(self, last: Mapping[str, _Written], now: float) -> bool:
        return any(live_of(w.counts) and now - w.at >= self.keepalive for w in last.values())

    def record(self, session_id: str, name: str, hook_state: Optional[dict],
               rollup: Optional[dict], now: float) -> int:
        """Append what changed for one agent; returns the rows written."""
        stamp_of_state = hook_state.get("timestamp") if isinstance(hook_state, dict) else None
        fingerprint = (stamp_of_state, _freeze(rollup))
        last = self._last.get(session_id) or {}
        if self._seen.get(session_id) == fingerprint and not self._keepalive_due(last, now):
            return 0
        self._seen[session_id] = fingerprint
        self._names[session_id] = name
        if rollup is None and not last:
            return 0  # never delegated (or nothing of it on record)
        self.computes += 1
        groups = group_counts(hook_state, rollup, now)
        out: List[Tuple[str, str, str, Counts]] = []
        kept: Dict[str, _Written] = {}
        for group, (kind, title, counts) in groups.items():
            prev = last.get(group)
            due = (prev is None or prev.counts != counts or (prev.kind, prev.title) != (kind, title)
                   or (live_of(counts) and now - prev.at >= self.keepalive))
            if due:
                out.append((group, kind, title, counts))
                kept[group] = _Written(counts, now, kind, title)
            else:
                kept[group] = prev
        for group, prev in last.items():
            if group not in groups and live_of(prev.counts):
                closing = (0, 0, 0, 0) + prev.counts[4:6] + (0,)
                out.append((group, prev.kind, prev.title, closing))
        if kept:
            self._last[session_id] = kept
        else:
            self._last.pop(session_id, None)
        self._write(session_id, name, out, now)
        return len(out)

    def forget(self, current_ids: Iterable[str], now: float) -> int:
        """Agents no longer in the fleet: close their live groups, drop their state."""
        current = set(current_ids)
        written = 0
        for sid in [s for s in self._last if s not in current]:
            last = self._last.pop(sid)
            out = [(g, w.kind, w.title, (0, 0, 0, 0) + w.counts[4:6] + (0,))
                   for g, w in last.items() if live_of(w.counts)]
            self._write(sid, self._names.get(sid, ""), out, now)
            written += len(out)
        for per in (self._seen, self._names):
            for sid in [s for s in per if s not in current]:
                del per[sid]
        return written

    def _write(self, session_id: str, name: str, rows, now: float) -> None:
        if not rows:
            return
        when = stamp(datetime.fromtimestamp(now))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists()
        # Order: the whole swarm first, then groups, so a reader sees one block
        rows = sorted(rows, key=lambda r: (r[0] != TOTAL, r[0]))
        with open(self.path, "a", newline="") as f:
            writer = csv.writer(f)
            if new:
                writer.writerow(HEADER)
            for group, kind, title, counts in rows:
                writer.writerow([when, name, session_id, group, kind, title[:60], *counts])
        self.rows_written += len(rows)


# ── Reading ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Point:
    ts: datetime
    counts: Counts
    kind: str
    title: str
    carried: bool = False

    @property
    def live(self) -> int:
        return live_of(self.counts)


Key = Tuple[str, str]  # (session id, group)
_Row = Tuple[datetime, str, str, str, str, Counts]  # ts, sid, group, kind, title, counts


def _parse(data: str) -> List[_Row]:
    rows: List[_Row] = []
    for row in csv.reader(data.splitlines()):
        if len(row) < len(HEADER) or row[0] == "timestamp":
            continue
        try:
            ts = from_iso(row[0])
            counts = tuple(int(v or 0) for v in row[6:13])
        except (ValueError, IndexError):
            continue
        rows.append((ts, sys.intern(row[2]), sys.intern(row[3]), sys.intern(row[4]),
                     sys.intern(row[5]), counts))  # type: ignore[arg-type]
    return rows


class SwarmHistoryFile:
    """Cached incremental reader for swarm_history.csv (as ``StatusHistoryFile``)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._mtime = 0.0
        self._size = 0
        self._hours = 0.0
        self._offset = 0
        self._rows: Deque[_Row] = deque()
        self._carry: Dict[Key, _Row] = {}
        self.full_reads = 0
        self.tail_reads = 0

    def read(self, hours: float, now: Optional[datetime] = None) -> Dict[Key, List[Point]]:
        try:
            st = self._path.stat()
        except OSError:
            return {}
        now = now or datetime.now()
        with self._lock:
            changed = st.st_mtime != self._mtime or st.st_size != self._size
            wider = hours > self._hours
            if changed or wider:
                if not wider and st.st_size > self._size and self._offset > 0:
                    self._tail(st)
                else:
                    self._full(st, now, hours)
            self._trim(now - timedelta(hours=self._hours))
            return self._filter(now, hours)

    def _full(self, st, now: datetime, hours: float) -> None:
        from .status_history import StatusHistoryFile

        lookback = now - timedelta(hours=hours, seconds=STALE_AFTER_SECONDS)
        try:
            with open(self._path, "rb") as f:
                start = StatusHistoryFile._seek_to_cutoff(f, lookback, st.st_size)
                f.seek(start)
                rows = _parse(f.read().decode("utf-8", errors="replace"))
        except OSError:
            rows = []
        self._rows = deque(rows)
        self._carry = {}
        self._mtime, self._size, self._offset, self._hours = st.st_mtime, st.st_size, st.st_size, hours
        self.full_reads += 1

    def _tail(self, st) -> None:
        try:
            with open(self._path, "rb") as f:
                f.seek(self._offset)
                rows = _parse(f.read().decode("utf-8", errors="replace"))
        except OSError:
            rows = []
        self._rows.extend(rows)
        self._mtime, self._size, self._offset = st.st_mtime, st.st_size, st.st_size
        self.tail_reads += 1

    def _trim(self, cutoff: datetime) -> None:
        rows, carry = self._rows, self._carry
        while rows and rows[0][0] < cutoff:
            row = rows.popleft()
            carry[(row[1], row[2])] = row
        stale = cutoff - timedelta(seconds=STALE_AFTER_SECONDS)
        for key in [k for k, r in carry.items() if r[0] < stale]:
            del carry[key]

    def _filter(self, now: datetime, hours: float) -> Dict[Key, List[Point]]:
        cutoff = now - timedelta(hours=hours)
        prior: Dict[Key, _Row] = dict(self._carry)
        out: Dict[Key, List[Point]] = {}
        for row in self._rows:
            key = (row[1], row[2])
            if row[0] < cutoff:
                prior[key] = row
                continue
            out.setdefault(key, []).append(Point(row[0], row[5], row[3], row[4]))
        lookback = cutoff - timedelta(seconds=STALE_AFTER_SECONDS)
        for key, row in prior.items():
            if row[0] >= lookback and live_of(row[5]):
                carried = Point(cutoff, row[5], row[3], row[4], carried=True)
                out.setdefault(key, []).insert(0, carried)
        return out


_readers: Dict[str, SwarmHistoryFile] = {}
_readers_lock = threading.Lock()


def read_swarm_history(path: Path, hours: float,
                       now: Optional[datetime] = None) -> Dict[str, Dict[str, List[Point]]]:
    """``{agent session id: {group: [Point, ...]}}`` over the last ``hours``, oldest first.

    Each group's first point may be its carry: its state at the window's
    left edge (``Point.carried``), when that was live.
    """
    with _readers_lock:
        reader = _readers.get(str(path))
        if reader is None:
            reader = _readers[str(path)] = SwarmHistoryFile(path)
    out: Dict[str, Dict[str, List[Point]]] = {}
    for (sid, group), points in reader.read(hours, now).items():
        out.setdefault(sid, {})[group] = points
    return out


# ── Slots ──────────────────────────────────────────────────────────────

LIGHT_RANK = {"orange": 3, "green": 2, "yellow": 1, "idle": 0}


def worst_light(counts: Counts) -> Optional[str]:
    """🟠 > 🟢 > 🟡 > idle (design §5), or None with nothing live."""
    if counts[2]:
        return "orange"
    if counts[0]:
        return "green"
    if counts[1]:
        return "yellow"
    if counts[3]:
        return "idle"
    return None


@dataclass
class Slot:
    mean: float               # time-weighted mean of live members over the covered part
    light: Optional[str]      # the worst light seen in the slot
    failed: bool              # a member failed in it


def density(points: List[Point], width: int, hours: float,
            now: datetime) -> List[Optional[Slot]]:
    """One group's points as ``width`` slots over the last ``hours`` (None: no data).

    A point stands until the next one, or for ``STALE_AFTER_SECONDS`` while
    live (a daemon that stopped writes no keepalive), and the last one until
    ``now``. Height is the mean, weighted by time, of live members across
    the slot's covered time; colour is the worst light in it.
    """
    if width <= 0 or not points:
        return [None] * max(width, 0)
    span = hours * 3600.0
    step = span / width
    start = now - timedelta(seconds=span)
    sums = [0.0] * width
    covered = [0.0] * width
    lights: List[Optional[str]] = [None] * width
    failed = [False] * width
    prev_failed: Optional[int] = None
    for i, p in enumerate(points):
        a = (p.ts - start).total_seconds()
        if i + 1 < len(points):
            b = (points[i + 1].ts - start).total_seconds()
        else:
            b = span
        if p.live:
            b = min(b, a + STALE_AFTER_SECONDS)
        if not p.carried and prev_failed is not None and p.counts[5] > prev_failed and 0 <= a < span:
            failed[min(width - 1, int(a / step))] = True
        prev_failed = p.counts[5]
        a, b = max(a, 0.0), min(b, span)
        if b <= a:
            continue
        light = worst_light(p.counts)
        first, last = int(a / step), min(width - 1, int(math.ceil(b / step)) - 1)
        for s in range(first, last + 1):
            lo, hi = max(a, s * step), min(b, (s + 1) * step)
            if hi <= lo:
                continue
            covered[s] += hi - lo
            sums[s] += p.live * (hi - lo)
            if light is not None and (lights[s] is None or LIGHT_RANK[light] > LIGHT_RANK[lights[s]]):
                lights[s] = light
    out: List[Optional[Slot]] = []
    for s in range(width):
        if covered[s] <= 0 and not failed[s]:
            out.append(None)
            continue
        mean = sums[s] / covered[s] if covered[s] > 0 else 0.0
        out.append(Slot(mean, lights[s], failed[s]))
    return out


LEVELS = "▁▂▃▄▅▆▇█"


def glyphs(slots: List[Optional[Slot]]) -> List[str]:
    """Each slot's glyph: ``▁``..``█`` scaled to the strip's peak mean, ``·`` none live, `` `` no data."""
    peak = max((s.mean for s in slots if s is not None), default=0.0)
    out = []
    for s in slots:
        if s is None:
            out.append(" ")
        elif s.mean <= 0 or peak <= 0:
            out.append("·")
        else:
            level = max(1, int(math.ceil(s.mean / peak * len(LEVELS))))
            out.append(LEVELS[min(level, len(LEVELS)) - 1])
    return out


def peak_live(points: List[Point]) -> int:
    return max((p.live for p in points), default=0)


def has_live(points: List[Point]) -> bool:
    return any(p.live for p in points)
