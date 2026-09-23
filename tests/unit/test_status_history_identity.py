"""Change-only status logging produces the same timeline and mean spin (audit R10).

One scripted status timeline — flapping and steady agents, an agent that
terminates, one that appears mid-window, one whose activity moves while its
status does not — is driven through the old logger (a row per agent per
2 s tick) and the new one (``status_row_due``: a row on change plus a
keepalive). The old reader and the old row-fraction mean over the old file
must agree with the new reader and the time-weighted mean over the new
file to within one sample interval, and the timeline slot arrays must be
equal.
"""

import csv
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from overcode.settings import DAEMON
from overcode.status_constants import is_green_status
from overcode.status_history import read_agent_status_history, status_row_due
from overcode.tui_helpers import build_timeline_slots
from overcode.tui_logic import calculate_mean_spin_from_history

TICK_SECONDS = 2
SCENARIO_MINUTES = 70
KEEPALIVE = DAEMON.status_history_keepalive_seconds


# ── the scripted fleet ──────────────────────────────────────────────────


def _scenario(now: datetime):
    """Yield ``(tick_time, {agent: (status, activity)})`` for every 2 s tick.

    Ticks sit on odd seconds relative to the window cutoffs, so no row lands
    inside the few milliseconds by which a reader's ``now`` trails the
    test's; the 31 s phase puts every window's cutoff mid-keepalive, so a
    quiet agent's first in-window row is well after the cutoff.
    """
    start = now - timedelta(minutes=SCENARIO_MINUTES) + timedelta(seconds=31)
    ticks = SCENARIO_MINUTES * 60 // TICK_SECONDS
    for k in range(ticks):
        t = start + timedelta(seconds=TICK_SECONDS * k)
        minute = TICK_SECONDS * k / 60
        fleet = {"steady": ("running", "Working")}
        # flapper: alternates every 90 s
        fleet["flapper"] = (
            ("running", "Working")
            if int(TICK_SECONDS * k // 90) % 2 == 0
            else ("waiting_user", "Waiting for input")
        )
        # chatty: always running, activity changes every 30 s
        fleet["chatty"] = ("running", f"Bash: step {int(TICK_SECONDS * k // 30)}")
        # terminator: running until minute 12, terminated until 20, then gone
        if minute < 12:
            fleet["terminator"] = ("running", "Working")
        elif minute < 20:
            fleet["terminator"] = ("terminated", "Window no longer exists")
        # latecomer: appears at minute 45
        if minute >= 45:
            fleet["latecomer"] = (
                ("running", "Working") if minute < 60 else ("waiting_user", "Waiting")
            )
        yield t, fleet


AGENTS = ["steady", "flapper", "chatty", "terminator", "latecomer"]
# The status bar's agent_names are the live (non-sleeping) sessions; the
# terminator has left. Its rows stopping is also the one place the two
# means differ: the old one stops counting at its last sample, the new one
# lets that row stand for up to two keepalives.
SPIN_NAMES = ["steady", "flapper", "chatty", "latecomer"]
HEADER = ["timestamp", "agent", "status", "activity", "session_id", "hostname"]


def _write(path: Path, rows) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for t, agent, status, activity in rows:
            w.writerow([t.isoformat(), agent, status, activity[:100], f"sid-{agent}", "host"])


def _old_logger_rows(now):
    for t, fleet in _scenario(now):
        for agent, (status, activity) in fleet.items():
            yield t, agent, status, activity


def _new_logger_rows(now):
    last_pair, last_written = {}, {}
    for t, fleet in _scenario(now):
        for agent, (status, activity) in fleet.items():
            if status_row_due(last_pair.get(agent), last_written.get(agent), status, activity, t):
                last_pair[agent] = (status, activity[:100] if activity else "")
                last_written[agent] = t
                yield t, agent, status, activity


# ── the old reader and mean, as they were ───────────────────────────────


def _old_read(path: Path, hours: float, now: datetime):
    """read_agent_status_history before the carry: rows with ts >= cutoff."""
    cutoff = now - timedelta(hours=hours)
    rows = []
    with open(path, newline="") as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            ts = datetime.fromisoformat(row[0])
            if ts >= cutoff:
                rows.append((ts, row[1], row[2], row[3], row[4], row[5]))
    return rows


def _old_mean_spin(history, agent_names, baseline_minutes, now):
    cutoff = now - timedelta(minutes=baseline_minutes)
    window = [
        (ts, agent, status)
        for ts, agent, status, *_ in history
        if cutoff <= ts <= now and agent in agent_names
    ]
    if not window:
        return 0.0, 0
    running = sum(1 for _, _, status in window if is_green_status(status))
    return running / len(window) * len(agent_names), len(window)


@pytest.fixture
def files(tmp_path):
    now = datetime.now()
    old = tmp_path / "old.csv"
    new = tmp_path / "new.csv"
    _write(old, _old_logger_rows(now))
    _write(new, _new_logger_rows(now))
    return now, old, new


class TestRowCount:
    def test_new_logger_writes_far_fewer_rows(self, files):
        now, old, new = files
        old_rows = sum(1 for _ in open(old)) - 1
        new_rows = sum(1 for _ in open(new)) - 1
        assert old_rows > 7_000
        assert new_rows * 10 < old_rows, (old_rows, new_rows)
        # ...but every agent still has a row within a keepalive of any instant it was logged
        by_agent = {}
        for t, agent, *_ in _new_logger_rows(now):
            by_agent.setdefault(agent, []).append(t)
        for agent, stamps in by_agent.items():
            gaps = [(b - a).total_seconds() for a, b in zip(stamps, stamps[1:])]
            assert max(gaps) <= KEEPALIVE + TICK_SECONDS, agent


class TestTimelineIdentity:
    @pytest.mark.parametrize("hours,width", [(1.0, 60), (1.0, 200), (0.5, 97)])
    def test_slot_arrays_are_equal(self, files, hours, width):
        now, old, new = files
        old_hist = {}
        for ts, agent, status, *_ in _old_read(old, hours, now):
            old_hist.setdefault(agent, []).append((ts, status))
        new_hist = {}
        for ts, agent, status, *_ in read_agent_status_history(
            hours=hours, history_file=new, carry=True
        ):
            new_hist.setdefault(agent, []).append((ts, status))

        assert set(old_hist) == set(new_hist)
        for agent in AGENTS:
            old_slots = build_timeline_slots(old_hist.get(agent, []), width, hours, now)
            new_slots = build_timeline_slots(new_hist.get(agent, []), width, hours, now)
            assert old_slots == new_slots, agent

    def test_without_the_carry_the_left_edge_would_differ(self, files):
        """The carry is what makes slot 0 agree for an agent quiet across the cutoff."""
        now, old, new = files
        hours, width = 1.0, 200  # 18 s slots; the first keepalive is 31 s in
        old_rows = [(ts, s) for ts, a, s, *_ in _old_read(old, hours, now) if a == "steady"]
        bare = [
            (ts, s)
            for ts, a, s, *_ in read_agent_status_history(hours=hours, history_file=new)
            if a == "steady"
        ]
        assert 0 in build_timeline_slots(old_rows, width, hours, now)
        assert 0 not in build_timeline_slots(bare, width, hours, now)


class TestMeanSpinIdentity:
    @pytest.mark.parametrize("baseline_minutes", [15, 30, 60])
    def test_means_agree_within_one_sample_interval(self, files, baseline_minutes):
        now, old, new = files
        hours = baseline_minutes / 60.0 + 0.1  # the status bar's read
        names = SPIN_NAMES
        old_mean, old_samples = _old_mean_spin(
            _old_read(old, hours, now), names, baseline_minutes, now
        )
        new_mean, new_samples = calculate_mean_spin_from_history(
            read_agent_status_history(hours=hours, history_file=new),
            names,
            baseline_minutes,
            now,
        )
        assert old_samples > 0 and new_samples > 0
        # One tick of coverage per agent, over the window
        tolerance = 2 * len(names) * TICK_SECONDS / (baseline_minutes * 60)
        assert new_mean == pytest.approx(old_mean, abs=tolerance), (old_mean, new_mean)

    def test_time_weighted_mean_over_the_old_file_is_the_row_fraction(self, files):
        now, old, new = files
        for baseline_minutes in (15, 30, 60):
            hours = baseline_minutes / 60.0 + 0.1
            rows = _old_read(old, hours, now)
            old_mean, _ = _old_mean_spin(rows, SPIN_NAMES, baseline_minutes, now)
            new_mean, _ = calculate_mean_spin_from_history(rows, SPIN_NAMES, baseline_minutes, now)
            tolerance = 2 * len(SPIN_NAMES) * TICK_SECONDS / (baseline_minutes * 60)
            assert new_mean == pytest.approx(old_mean, abs=tolerance)
