"""The daemon tick writes sessions.json at most once and reads it at most twice.

Whole ticks over 50 real sessions through ``tests/daemon_tick_driver.py``:
the per-tick I/O budget (audit R5), same-tick visibility of staged values
in the published state, the waiting_oversight guard, the flush-on-failure
path, and — when ``OVERCODE_IDENTITY_BASE_SRC`` points at a frozen copy of
the pre-change source tree — a diff of the resulting sessions.json against
the old per-session-write path over the same scripted scenario.
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from overcode.session_manager import SessionManager
from tests import daemon_tick_driver as driver
from tests.daemon_tick_harness import (
    FrozenClock,
    IoCounters,
    ScriptedDetector,
    count_sessions_io,
    make_daemon,
    seed_sessions,
    seed_steady_state,
    state_without_timestamps,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def root(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".overcode" / "sessions").mkdir(parents=True)
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(home / ".overcode" / "sessions"))
    return tmp_path


def _by_index(state):
    return {
        int(e["name"].rsplit("-", 1)[1]): e
        for e in state.values()
        if e["name"].startswith("agent-")
    }


class TestPerTickBudget:
    def test_three_ticks_over_fifty_sessions(self, root):
        counters = IoCounters()
        sm = SessionManager(
            state_dir=root / "home" / ".overcode" / "sessions", skip_git_detection=True
        )
        with count_sessions_io(sm.state_file, counters):
            result = driver.run_scenario(root, ticks=3, counters=counters)
        assert len(counters.per_tick) == 3
        for reads, writes in counters.per_tick:
            assert writes <= 1, counters.per_tick
            assert reads <= 2, counters.per_tick
        # ...and each tick did have something to persist
        assert all(writes == 1 for _, writes in counters.per_tick), counters.per_tick

        by_i = _by_index(result["sessions"])
        # tick 2: agent-00 came back to running -> its 2 s operation was recorded
        assert by_i[0]["stats"]["current_task"] == "Active: Read(a.py)"
        assert by_i[0]["stats"]["operation_times"][-1] == 2.0
        # a PR link was picked up on tick 0 and cleared on tick 2 when the branch moved
        assert by_i[1]["branch"] == "feature"
        assert by_i[1]["pr_number"] is None and by_i[1]["pr_branch"] is None
        assert by_i[11]["pr_number"] is None
        # a window that vanished on tick 1 is persisted as terminated
        assert by_i[2]["status"] == "terminated"
        assert by_i[2]["stats"]["current_state"] == "terminated"
        # the done agent is left alone; the asleep one accumulates sleep time
        assert by_i[3]["status"] == "done" and by_i[3]["stats"]["current_task"] == "Completed"
        assert by_i[4]["stats"]["sleep_time_seconds"] > 0
        # the heartbeat stamp landed
        assert by_i[5]["last_heartbeat_time"] == driver.START.isoformat()
        # loaded skills changed on tick 2
        assert by_i[7]["loaded_skills"] == ["dataviz", "overcode"]
        assert by_i[17]["loaded_skills"] == ["dataviz", "overcode"]
        assert by_i[6]["loaded_skills"] == ["overcode"]
        # the 60 s stats sync (tick 0) landed tokens and the model
        assert by_i[9]["stats"]["input_tokens"] == 10_000 and by_i[9]["model"] == "claude-opus-4-6"
        # time accumulated across the ticks that followed the first observation
        assert by_i[8]["stats"]["non_green_time_seconds"] > 50.0 + 8

    def test_published_state_sees_values_staged_in_the_same_tick(self, root):
        result = driver.run_scenario(root, ticks=1)
        published = {s["name"]: s for s in result["daemon"]["sessions"]}
        # tokens/model/last command from the stats sync that ran earlier in the tick
        assert published["agent-09"]["input_tokens"] == 10_000
        assert published["agent-09"]["model"] == "claude-opus-4-6"
        assert published["agent-09"]["last_command"] == "do task 9"
        # the heartbeat sent earlier in the tick
        assert published["agent-05"]["last_heartbeat_time"] == driver.START.isoformat()
        assert published["agent-05"]["current_status"] == "heartbeat_start"
        # the git context read in the tick
        assert published["agent-01"]["branch"] == "main"


class TestOversightGuard:
    def _daemon(self, root, hook_state):
        start = datetime(2026, 9, 23, 12, 0, 0)
        sm = SessionManager(
            state_dir=root / "home" / ".overcode" / "sessions", skip_git_detection=True
        )
        sessions = seed_sessions(sm, 12, "agents", root / "work", start)
        child = sessions[11]  # child of sessions[1]
        sm.update_session_status(child.id, "waiting_oversight")

        def script(tick, s):
            if s.id == child.id:
                return "terminated", "Window no longer exists", ""
            return "waiting_user", "Waiting for input", "pane"

        detector = ScriptedDetector(script)
        detector.hooks._read_hook_state = lambda name: hook_state if name == child.name else None
        daemon = make_daemon(root / "home" / ".overcode", "agents", detector, session_manager=sm)
        seed_steady_state(daemon, sessions, start)
        return daemon, sm, child, start

    def test_child_with_stop_hook_keeps_waiting_oversight(self, root):
        daemon, sm, child, start = self._daemon(root, {"event": "Stop"})
        with FrozenClock(start).installed():
            daemon._tick(start)
        assert sm.get_session(child.id).status == "waiting_oversight"
        published = {s.session_id: s for s in daemon.state.sessions}
        assert published[child.id].current_status == "terminated"

    def test_child_without_stop_hook_is_persisted_terminated(self, root):
        daemon, sm, child, start = self._daemon(root, None)
        with FrozenClock(start).installed():
            daemon._tick(start)
        assert sm.get_session(child.id).status == "terminated"


class TestFlush:
    def test_staged_changes_land_even_when_a_later_phase_raises(self, root):
        start = datetime(2026, 9, 23, 12, 0, 0)
        sm = SessionManager(
            state_dir=root / "home" / ".overcode" / "sessions", skip_git_detection=True
        )
        sessions = seed_sessions(sm, 3, "agents", root / "work", start)
        detector = ScriptedDetector(lambda t, s: ("running", "Active: Bash(make)", "pane"))
        daemon = make_daemon(root / "home" / ".overcode", "agents", detector, session_manager=sm)
        seed_steady_state(daemon, sessions, start)
        with (
            FrozenClock(start).installed(),
            patch.object(daemon, "_publish_and_enforce", side_effect=RuntimeError("boom")),
        ):
            with pytest.raises(RuntimeError):
                daemon._tick(start)
        assert not daemon._pending
        raw = json.loads(sm.state_file.read_text())
        assert all(e["stats"]["current_task"] == "Active: Bash(make)" for e in raw.values())

    def test_flush_with_nothing_staged_does_not_touch_the_file(self, root):
        start = datetime(2026, 9, 23, 12, 0, 0)
        sm = SessionManager(
            state_dir=root / "home" / ".overcode" / "sessions", skip_git_detection=True
        )
        seed_sessions(sm, 2, "agents", root / "work", start)
        daemon = make_daemon(
            root / "home" / ".overcode",
            "agents",
            ScriptedDetector(lambda t, s: ("running", "", "")),
            session_manager=sm,
        )
        with count_sessions_io(sm.state_file) as io:
            daemon._flush_pending_writes()
        assert (io.reads, io.writes) == (0, 0)


BASE_SRC = os.environ.get("OVERCODE_IDENTITY_BASE_SRC")


@pytest.mark.skipif(
    not BASE_SRC,
    reason="set OVERCODE_IDENTITY_BASE_SRC=<frozen base tree>/src to diff against the old path",
)
class TestIdentityWithThePerSessionPath:
    """The same scenario through the old per-session writers and the batched tick."""

    def _run(self, tmp_path: Path, src: Path) -> dict:
        root = tmp_path / src.parent.name
        root.mkdir()
        out = root / "result.json"
        env = {k: v for k, v in os.environ.items() if not k.startswith("OVERCODE_")}
        env.update(
            {
                "PYTHONPATH": f"{src}{os.pathsep}{REPO_ROOT}",
                "OVERCODE_MODEL_METADATA_BUNDLED_ONLY": "1",
                "HOME": str(root / "home"),
            }
        )
        subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "tests" / "daemon_tick_driver.py"),
                "--root",
                str(root),
                "--out",
                str(out),
            ],
            check=True,
            env=env,
            cwd=str(REPO_ROOT),
            timeout=300,
        )
        # Each run has its own root, and start_directory embeds it
        return json.loads(out.read_text().replace(str(root), "<root>"))

    def test_sessions_json_is_identical(self, tmp_path):
        old = self._run(tmp_path, Path(BASE_SRC).resolve())
        new = self._run(tmp_path, REPO_ROOT / "src")
        assert state_without_timestamps(new["sessions"]) == state_without_timestamps(
            old["sessions"]
        )
        # With the clock frozen the stamps agree too
        assert new["sessions"] == old["sessions"]
        assert new["daemon"]["sessions"] == old["daemon"]["sessions"]
        assert len(new["sessions"]) == driver.N_SESSIONS + 5
