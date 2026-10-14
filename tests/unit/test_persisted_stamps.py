"""Persisted timestamps stay naive local ISO, as 0.5.x wrote them.

An older overcode still running beside 0.6 after an upgrade (its supervisor
daemon, its API server) or one rolled back to reads the same files and
subtracts their stamps from a naive ``datetime.now()``. An aware stamp
makes that raise: the 0.5.8 supervisor saw the engine as stale for ever,
its API server's /api/status died, and its daemon crashed on its second
tick. In memory the clock stays aware; ``clock.stamp`` is what goes to disk.
"""

import csv
import json
from datetime import datetime, timedelta

import pytest

from overcode.clock import seconds_since, stamp, to_epoch
from tests.daemon_tick_harness import (
    FrozenClock,
    ScriptedDetector,
    make_daemon,
    run_ticks,
    seed_sessions,
    seed_steady_state,
)

pytestmark = pytest.mark.unit

START = datetime(2026, 10, 7, 12, 0, 0)


def _iso_strings(value, path=""):
    """Every (path, string) in a JSON value that reads as an ISO datetime."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _iso_strings(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _iso_strings(v, f"{path}[{i}]")
    elif isinstance(value, str) and len(value) >= 19 and value[4:5] == "-" and "T" in value:
        try:
            yield path, datetime.fromisoformat(value)
        except ValueError:
            pass


def _assert_058_readable(stamps):
    """What 0.5.8 does with a stamp: ``datetime.now() - fromisoformat(s)``."""
    assert stamps, "no stamps written"
    for where, parsed in stamps:
        assert parsed.tzinfo is None, f"{where} is aware: {parsed.isoformat()}"
        (datetime.now() - parsed).total_seconds()  # raised TypeError when aware


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state" / "sessions"))
    (tmp_path / "state" / "sessions").mkdir(parents=True)
    return tmp_path


def _flip(tick, session):
    return ("running", "Working", "") if tick % 2 else ("waiting_user", "Waiting", "")


class TestTheDaemonWritesNaiveStamps:
    def _run(self, env, ticks=4):
        from overcode.session_manager import SessionManager

        sm = SessionManager(state_dir=env / "state" / "sessions", skip_git_detection=True)
        sessions = seed_sessions(sm, 2, "agents", env / "work", START)
        detector = ScriptedDetector(_flip)
        daemon = make_daemon(env / "state", "agents", detector, session_manager=sm)
        seed_steady_state(daemon, sessions, START)
        clock = FrozenClock(START)
        with clock.installed():
            run_ticks(daemon, detector, clock, ticks)
            daemon._save_state_file()
        return daemon, sm

    def test_sessions_json_holds_only_naive_stamps(self, env):
        """#11: a rolled-back 0.5.8 daemon crashed on tick 2 on aware stats stamps."""
        _, sm = self._run(env)
        data = json.loads(sm.state_file.read_text())
        stamps = list(_iso_strings(data))
        names = {where.rsplit(".", 1)[-1] for where, _ in stamps}
        assert {"state_since", "last_time_accumulation"} <= names
        _assert_058_readable(stamps)

    def test_the_state_file_holds_only_naive_stamps(self, env):
        """#7/#22: a 0.5.8 supervisor or API server reads this file."""
        daemon, _ = self._run(env)
        data = json.loads(daemon.state_path.read_text())
        stamps = list(_iso_strings(data))
        names = {where.rsplit(".", 1)[-1] for where, _ in stamps}
        assert {"last_loop_time", "started_at", "status_since"} <= names
        _assert_058_readable(stamps)

    def test_a_058_supervisor_sees_the_engine_as_live(self, env):
        """#7: 0.5.8 is_stale(), verbatim, on the state the 0.6 engine wrote."""
        from overcode.monitor_daemon_state import MonitorDaemonState

        daemon, _ = self._run(env)
        state = MonitorDaemonState.load(daemon.state_path)
        # 0.5.8 monitor_daemon_state.is_stale; "now" is the engine's tick time
        now = START + timedelta(seconds=8)
        last_time = datetime.fromisoformat(state.last_loop_time)
        age = (now - last_time).total_seconds()
        assert age <= state.expected_publish_gap() + 30.0

    def test_the_status_history_holds_only_naive_stamps(self, env):
        """#12: a 0.5.8 TUI compared aware rows with its naive cutoff and died."""
        from overcode.settings import get_agent_history_path

        self._run(env)
        rows = list(csv.reader(open(get_agent_history_path("agents"), newline="")))[1:]
        assert rows
        _assert_058_readable([(f"row {i}", datetime.fromisoformat(r[0]))
                              for i, r in enumerate(rows)])
        cutoff = datetime.now() - timedelta(days=3650)
        assert all(datetime.fromisoformat(r[0]) >= cutoff for r in rows)


class TestStamp:
    def test_an_aware_value_is_written_as_its_local_naive_time(self):
        local = START.astimezone()
        assert stamp(local) == START.isoformat()

    def test_a_naive_value_is_written_as_is(self):
        assert stamp(START) == START.isoformat()

    def test_the_ledger_row_time_is_naive(self):
        from overcode.ledger import make_row

        row = make_row({"id": "s", "name": "a"}, "checkpoint", now=START.astimezone())
        assert row["t"] == START.isoformat()


class TestSecondsSince:
    def test_reads_naive_aware_strings_and_epochs(self):
        now = START.timestamp() + 90
        for value in (START, START.astimezone(), START.isoformat(),
                      START.astimezone().isoformat(), START.timestamp()):
            assert seconds_since(value, now) == pytest.approx(90)

    def test_unknown_is_none(self):
        assert seconds_since(None) is None
        assert seconds_since("not a time") is None

    def test_to_epoch_is_what_it_measures_from(self):
        assert seconds_since(START, to_epoch(START)) == 0
