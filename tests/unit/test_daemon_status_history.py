"""The daemon writes agent_status_history.csv on change, keepalive and first sight (audit R10).

Whole ticks over real sessions through ``tests/daemon_tick_harness.py``:
every session gets a row the first time the daemon sees it, an unchanged
tick writes nothing, a status or activity change writes that session's
row, a keepalive row goes out once the last one is a keepalive old, and a
session that leaves sessions.json is forgotten so it is logged again if
it returns.
"""

import csv
from datetime import datetime, timedelta

import pytest

from overcode.session_manager import SessionManager
from overcode.status_history import STATUS_HISTORY_KEEPALIVE_SECONDS
from tests.daemon_tick_harness import (
    FrozenClock,
    ScriptedDetector,
    make_daemon,
    run_ticks,
    seed_sessions,
    seed_steady_state,
)

START = datetime(2026, 9, 23, 12, 0, 0)
SYNC_STAMPS = (
    "_last_stats_sync",
    "_last_session_id_sync",
    "_last_skills_sync",
    "_last_sandbox_sync",
    "_last_resources_sync",
    "_last_history_rotation_check",
    "_last_model_metadata_check",
)


@pytest.fixture
def root(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".overcode" / "sessions").mkdir(parents=True)
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(home / ".overcode" / "sessions"))
    return tmp_path


def _rows(path):
    """(agent, status, activity) per row of the history CSV, in file order."""
    if not path.exists():
        return []
    with open(path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        assert header[:3] == ["timestamp", "agent", "status"]
        return [(row[1], row[2], row[3]) for row in reader]


class _Fleet:
    """Three sessions and a daemon whose periodic syncs never come due."""

    def __init__(self, root, script):
        self.sm = SessionManager(
            state_dir=root / "home" / ".overcode" / "sessions", skip_git_detection=True
        )
        self.sessions = seed_sessions(self.sm, 3, "agents", root / "work", START)
        self.detector = ScriptedDetector(script)
        self.daemon = make_daemon(
            root / "home" / ".overcode", "agents", self.detector, session_manager=self.sm
        )
        seed_steady_state(self.daemon, self.sessions, START)
        self.history = self.daemon.history_path

    def run(self, ticks, clock, first_tick=0):
        def keep_syncs_quiet(_tick):
            for name in SYNC_STAMPS:
                setattr(self.daemon, name, clock.now)

        self.detector.tick = first_tick
        run_ticks(self.daemon, self.detector, clock, ticks, between_ticks=keep_syncs_quiet)


def _steady(tick, session):
    return "running", "Working", "pane"


class TestChangeOnlyRows:
    def test_first_tick_logs_every_session_once(self, root):
        fleet = _Fleet(root, _steady)
        with FrozenClock(START).installed() as clock:
            fleet.run(1, clock)
        assert sorted(_rows(fleet.history)) == sorted(
            (s.name, "running", "Working") for s in fleet.sessions
        )

    def test_unchanged_ticks_log_nothing(self, root):
        fleet = _Fleet(root, _steady)
        with FrozenClock(START).installed() as clock:
            fleet.run(10, clock)  # 20 s of unchanged ticks
        assert len(_rows(fleet.history)) == 3

    def test_a_status_change_logs_that_session_only(self, root):
        def script(tick, session):
            if tick >= 2 and session.name == "agent-01":
                return "waiting_user", "Waiting for input", "pane"
            return "running", "Working", "pane"

        fleet = _Fleet(root, script)
        with FrozenClock(START).installed() as clock:
            fleet.run(5, clock)
        rows = _rows(fleet.history)
        assert len(rows) == 4
        assert rows[-1] == ("agent-01", "waiting_user", "Waiting for input")

    def test_an_activity_change_logs_a_row(self, root):
        def script(tick, session):
            if tick >= 3 and session.name == "agent-02":
                return "running", "Bash: pytest", "pane"
            return "running", "Working", "pane"

        fleet = _Fleet(root, script)
        with FrozenClock(START).installed() as clock:
            fleet.run(5, clock)
        rows = _rows(fleet.history)
        assert len(rows) == 4
        assert rows[-1] == ("agent-02", "running", "Bash: pytest")

    def test_keepalive_row_after_the_interval(self, root):
        fleet = _Fleet(root, _steady)
        ticks_to_keepalive = STATUS_HISTORY_KEEPALIVE_SECONDS // 2
        with FrozenClock(START).installed() as clock:
            fleet.run(ticks_to_keepalive, clock)  # last tick is 2 s short of a keepalive
            assert len(_rows(fleet.history)) == 3
            fleet.run(1, clock, first_tick=ticks_to_keepalive)  # exactly a keepalive later
        rows = _rows(fleet.history)
        assert len(rows) == 6
        assert sorted(rows[3:]) == sorted(rows[:3])

    def test_keepalive_clock_restarts_at_each_written_row(self, root):
        def script(tick, session):
            if tick >= 5 and session.name == "agent-01":
                return "waiting_user", "Waiting", "pane"
            return "running", "Working", "pane"

        fleet = _Fleet(root, script)
        keepalive_ticks = STATUS_HISTORY_KEEPALIVE_SECONDS // 2
        with FrozenClock(START).installed() as clock:
            fleet.run(keepalive_ticks + 1, clock)
        rows = _rows(fleet.history)
        # 3 first-sight rows, agent-01's change at tick 5, keepalives for the
        # two unchanged agents at tick 30 — agent-01's clock restarted at tick 5.
        assert len(rows) == 6
        assert [r[0] for r in rows[4:]] == ["agent-00", "agent-02"]

    def test_daemon_tracking_keys_are_pruned_and_a_returning_session_is_logged_again(self, root):
        fleet = _Fleet(root, _steady)
        gone = fleet.sessions[2]
        with FrozenClock(START).installed() as clock:
            fleet.run(2, clock)
            assert gone.id in fleet.daemon._last_logged
            assert gone.id in fleet.daemon._last_keepalive

            state = {s.id: s.to_dict() for s in fleet.sessions if s is not gone}
            fleet.sm._save_state(state)
            fleet.run(2, clock, first_tick=2)
            assert gone.id not in fleet.daemon._last_logged
            assert gone.id not in fleet.daemon._last_keepalive
            assert len(_rows(fleet.history)) == 3

            state[gone.id] = gone.to_dict()
            fleet.sm._save_state(state)
            fleet.run(1, clock, first_tick=4)
        rows = _rows(fleet.history)
        assert len(rows) == 4
        assert rows[-1] == (gone.name, "running", "Working")
