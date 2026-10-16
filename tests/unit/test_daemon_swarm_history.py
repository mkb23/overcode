"""The daemon writes swarm_history.csv on change, and 🌙 rows' status history (phase 4).

Whole ticks through ``tests/daemon_tick_harness.py``: a parent's delegated
work gets rows when a hook event moves its counts, and none on the ticks in
between, however many. Background sessions get ordinary status history
rows, so the timeline draws a row for each.
"""

import csv
from datetime import datetime
from types import SimpleNamespace

import pytest

from overcode import swarm
from overcode.session_manager import SessionManager
from overcode.swarm_history import SWARM_HISTORY_FILE
from tests.daemon_tick_harness import (
    FrozenClock,
    ScriptedDetector,
    make_daemon,
    run_ticks,
    seed_sessions,
    seed_steady_state,
    set_sync_stamps,
)

pytestmark = pytest.mark.unit

START = datetime(2026, 9, 23, 12, 0, 0)


@pytest.fixture
def root(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".overcode" / "sessions").mkdir(parents=True)
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(home / ".overcode" / "sessions"))
    return tmp_path


def _hook_state(tick):
    """agent-00 delegates two agents at tick 2; one finishes at tick 6."""
    ts = START.timestamp() + 2 * min(tick, 6)
    live = {"a1": {"kind": "subagent"}}
    done = {}
    if tick < 6:
        live["a2"] = {"kind": "subagent"}
    else:
        done["a2"] = {"kind": "subagent", "result": "ok", "ended_at": ts}
    return {"event": "PreToolUse", "timestamp": ts, "subagents": live,
            "swarm": {"bg_at": ts, "done": done}}


class SwarmDetector(ScriptedDetector):
    def _state(self, name):
        if name != "agent-00" or self.tick < 2:
            return None
        return _hook_state(self.tick)

    def get_swarm_state(self, name):
        return self._state(name)

    def get_swarm_rollup(self, name):
        state = self._state(name)
        return swarm.rollup(state, START.timestamp()) if state else None


def _rows(path):
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def test_rows_on_change_only(root):
    sm = SessionManager(state_dir=root / "home" / ".overcode" / "sessions",
                        skip_git_detection=True)
    sessions = seed_sessions(sm, 3, "agents", root / "work", START)
    detector = SwarmDetector(lambda tick, s: ("running", "Working", "pane"))
    daemon = make_daemon(root / "home" / ".overcode", "agents", detector, session_manager=sm)
    seed_steady_state(daemon, sessions, START)
    path = daemon.history_path.with_name(SWARM_HISTORY_FILE)
    assert daemon.swarm_history.path == path
    with FrozenClock(START).installed() as clock:
        run_ticks(daemon, detector, clock, 30, between_ticks=lambda _t: set_sync_stamps(daemon, clock.now))
    rows = _rows(path)
    # tick 2: the swarm and its group; tick 6: both again (a2 finished); nothing else
    assert [(r["agent"], r["group"], r["running"], r["ok"]) for r in rows] == [
        ("agent-00", "*", "2", "0"), ("agent-00", "sa", "2", "0"),
        ("agent-00", "*", "1", "1"), ("agent-00", "sa", "1", "1"),
    ]
    # Counted once per hook event (ticks 2-6 each carry a new hook
    # timestamp), never on the 23 quiet ticks after
    assert daemon.swarm_history.computes == 5


def test_background_sessions_get_status_history_rows(tmp_path):
    from overcode.monitor_daemon import MonitorDaemon
    from overcode.monitor_daemon_state import SessionDaemonState
    from overcode.status_history import read_agent_status_history

    daemon = MonitorDaemon.for_test(tmp_path)

    def row(status, activity):
        return SimpleNamespace(state=SessionDaemonState(
            session_id="claude-bg:s1", name="bg-a3f9c1e2", current_status=status,
            current_activity=activity))

    daemon._log_claude_bg_history([row("running", "Read x")])
    daemon._log_claude_bg_history([row("running", "Read x")])  # unchanged: no row
    daemon._log_claude_bg_history([row("waiting_user", "Needs approval")])
    daemon._log_claude_bg_history([row("done", "ran migration")])
    daemon._log_claude_bg_history([row("done", "ran migration")])
    got = read_agent_status_history(hours=1, history_file=daemon.history_path)
    assert [(r[1], r[2], r[4]) for r in got] == [
        ("bg-a3f9c1e2", "running", "claude-bg:s1"),
        ("bg-a3f9c1e2", "waiting_user", "claude-bg:s1"),
        ("bg-a3f9c1e2", "done", "claude-bg:s1"),
    ]
    daemon._log_claude_bg_history([])  # gone: forgotten
    assert daemon._bg_logged == {}
