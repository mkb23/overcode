"""The episode recorder, the bell and the hook time, through whole daemon ticks.

``ScriptedDetector`` answers ``get_status_detail`` and ``get_last_hook_at``
as the dispatcher does, so a harness tick runs the same path a live tick
does: detect, colour, record, ring, publish. These tests count what that
path does (episodes closed, bells rung, engine_state.json writes), never
how long it takes (quality review item 37).
"""

from datetime import datetime

import pytest

from overcode.status_constants import (
    STATUS_COLOR_GREEN as GREEN,
    STATUS_COLOR_ORANGE as ORANGE,
    STATUS_COLOR_RED as RED,
)
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


class RingingEngine:
    """What the daemon reaches on engine.sock during a tick; records the bells."""

    attended = True
    burn_windows: set = set()

    def __init__(self):
        self.bells = []
        self.published = 0

    def ring(self, agent, episode):
        self.bells.append((agent, episode["colour"]))

    def publish(self, snapshot):
        self.published += 1

    def take_visits(self):
        return []

    def beat(self):
        pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state" / "sessions"))
    (tmp_path / "state" / "sessions").mkdir(parents=True)
    return tmp_path


def _fleet(env, script, n=2, **detector_kwargs):
    from overcode.session_manager import SessionManager

    sm = SessionManager(state_dir=env / "state" / "sessions", skip_git_detection=True)
    sessions = seed_sessions(sm, n, "agents", env / "work", START)
    detector = ScriptedDetector(script, **detector_kwargs)
    daemon = make_daemon(env / "state", "agents", detector, session_manager=sm)
    seed_steady_state(daemon, sessions, START)
    daemon._engine = RingingEngine()
    return daemon, detector, sessions, FrozenClock(START)


@pytest.fixture
def counted(monkeypatch):
    """Counts of the recorder's episode-log appends and engine_state.json writes."""
    from overcode import engine_recorders

    counts = {"episodes": [], "saves": 0}
    real_append = engine_recorders.append_episode
    real_replace = engine_recorders.os.replace

    def append(directory, name, sid, episode):
        counts["episodes"].append((name, episode.colour))
        return real_append(directory, name, sid, episode)

    def replace(src, dst):
        if str(dst).endswith("engine_state.json"):
            counts["saves"] += 1
        return real_replace(src, dst)

    monkeypatch.setattr(engine_recorders, "append_episode", append)
    monkeypatch.setattr(engine_recorders.os, "replace", replace)
    return counts


class TestTheHarnessColoursEachTick:

    def test_every_agent_is_coloured_once_per_tick(self, env):
        daemon, detector, sessions, clock = _fleet(
            env, lambda t, s: ("running", "Using Read", ""), n=3)
        with clock.installed():
            run_ticks(daemon, detector, clock, 4)
        assert detector.calls["get_status_detail"] == 3 * 4
        assert detector.calls["get_last_hook_at"] == 3 * 4
        for state in daemon.state.sessions:
            assert state.live_colour == GREEN and state.episode_colour == GREEN
            assert state.status_detail is not None


class TestAQuietFleetRecordsNothing:

    def test_quiet_ticks_close_no_episode_ring_no_bell_and_save_once(self, env, counted):
        daemon, detector, _, clock = _fleet(env, lambda t, s: ("waiting_user", "Waiting", ""))
        with clock.installed():
            run_ticks(daemon, detector, clock, 1)  # first sight: the recorders are made
            first_saves = counted["saves"]
            run_ticks(daemon, detector, clock, 20)
        assert first_saves == 1
        assert counted["episodes"] == []
        assert daemon._engine.bells == []  # red from first sight: began unseen, never rings
        # The seen-time is written at most every RECORDER_SEEN_SAVE_SECONDS
        # of the daemon's (frozen, stepped) monotonic clock: once in 40 s
        assert counted["saves"] == first_saves + 1


class TestAStallRingsOnceThroughTheTick:

    def test_a_confirmed_stall_rings_once_and_closes_one_episode(self, env, counted):
        from overcode.episodes import EPISODE_MERGE_SECONDS

        step = 2.0  # run_ticks' default
        stalled = [False]  # run_ticks numbers each call's ticks from 0: script by phase

        def script(tick, s):
            if s.name == "agent-00" and stalled[0]:
                return ("waiting_user", "Waiting", "")
            return ("running", "Using Bash", "")

        daemon, detector, sessions, clock = _fleet(env, script)
        sid = sessions[0].id
        with clock.installed():
            run_ticks(daemon, detector, clock, 3)
            stalled[0] = True
            stall_start = clock.now.timestamp()
            # Pending until it has lasted G seconds
            run_ticks(daemon, detector, clock, int(EPISODE_MERGE_SECONDS / step))
            assert daemon._engine.bells == []
            run_ticks(daemon, detector, clock, 1)
            assert daemon._engine.bells == [(sid, RED)]
            # Still stalled: one stretch, one bell
            run_ticks(daemon, detector, clock, 10)
        assert daemon._engine.bells == [(sid, RED)]
        assert counted["episodes"] == [("agent-00", GREEN)]
        state = next(s for s in daemon.state.sessions if s.session_id == sid)
        assert state.episode_colour == RED and state.episode_start == stall_start
        assert state.input_needed_since == stall_start

    def test_a_new_agent_red_from_first_sight_rings(self, env, counted):
        """An agent launched while the engine runs and first seen red (a
        launch dialog, a turn over before the first tick) rings after G;
        the fleet that was already red when the engine started does not."""
        from datetime import timedelta

        from overcode.episodes import EPISODE_MERGE_SECONDS

        def script(tick, s):
            return ("waiting_user", "Waiting", "")

        daemon, detector, sessions, clock = _fleet(env, script)
        with clock.installed():
            run_ticks(daemon, detector, clock, 3)
            sm = daemon.session_manager
            new = sm.create_session("late", "agents", "late-0001", ["claude"],
                                    start_directory=str(env / "work"))
            sm.update_session(new.id, start_time=(clock.now - timedelta(seconds=1)).isoformat())
            run_ticks(daemon, detector, clock, int(EPISODE_MERGE_SECONDS / 2.0) + 2)
        assert daemon._engine.bells == [(new.id, RED)]

    def test_a_brief_stall_merges_and_never_rings(self, env, counted):
        def script(tick, s):
            if s.name == "agent-00" and 3 <= tick < 6:  # 6 s of red, well inside G
                return ("waiting_user", "Waiting", "")
            return ("running", "Using Bash", "")

        daemon, detector, sessions, clock = _fleet(env, script)
        with clock.installed():
            run_ticks(daemon, detector, clock, 30)
        assert daemon._engine.bells == []
        assert counted["episodes"] == []
        assert daemon.recorder_store.recorders[sessions[0].id].episode.blips

    def test_a_scripted_detail_drives_the_colour(self, env, counted):
        """``details`` scripts the hook detector's colour apart from the
        legacy status: a running agent whose detail says orange (a
        permission prompt) rings."""
        from overcode.status_constants import StatusDetail

        def details(tick, name):
            colour = ORANGE if name == "agent-01" and tick >= 2 else GREEN
            return StatusDetail(colour, [], "running")

        daemon, detector, sessions, clock = _fleet(
            env, lambda t, s: ("running", "", ""), details=details)
        with clock.installed():
            run_ticks(daemon, detector, clock, 20)
        assert daemon._engine.bells == [(sessions[1].id, ORANGE)]


class TestTheHookTime:

    def test_the_hook_time_is_published_and_holds_still(self, env):
        from overcode.engine_protocol import diff

        hooks = {"agent-00": 1_791_380_000.0}
        daemon, detector, sessions, clock = _fleet(
            env, lambda t, s: ("waiting_user", "Waiting", ""),
            hook_at=lambda tick, name: hooks.get(name))
        with clock.installed():
            run_ticks(daemon, detector, clock, 4)
            before = daemon.state.to_engine_snapshot(clock.now.timestamp())
            run_ticks(daemon, detector, clock, 6)
            after = daemon.state.to_engine_snapshot(clock.now.timestamp())
            agents = after.agents
            assert agents[sessions[0].id]["last_hook_at"] == 1_791_380_000.0
            assert agents[sessions[1].id].get("last_hook_at") is None
            assert diff(before, after).empty
            # A new hook is news: one field of one agent moves
            hooks["agent-00"] = 1_791_380_050.0
            run_ticks(daemon, detector, clock, 1)
            moved = daemon.state.to_engine_snapshot(clock.now.timestamp())
        delta = diff(after, moved)
        assert set(delta.set) == {sessions[0].id}
        assert delta.set[sessions[0].id]["last_hook_at"] == 1_791_380_050.0
