"""The recorded layer (#507): episodes, blip merging at G, and the bell.

Unit cases drive EpisodeRecorder directly; the replay cases feed it the live
colours the real hook handler + detector produce for the #507 scenarios.
"""

import pytest

from overcode.episodes import EPISODE_MERGE_SECONDS, Bell, EpisodeRecorder
from overcode.status_constants import (
    STATUS_COLOR_GREEN as G_,
    STATUS_COLOR_ORANGE as O_,
    STATUS_COLOR_RED as R_,
    STATUS_COLOR_YELLOW as Y_,
)

pytestmark = pytest.mark.unit
G = EPISODE_MERGE_SECONDS


def run(steps, rec=None):
    """Feed (t, colour) steps; return the recorder, closed episodes, bells, merges."""
    rec = rec or EpisodeRecorder()
    closed, bells, merges = [], [], []
    for t, colour in steps:
        out = rec.observe(colour, t)
        closed += out.closed
        if out.bell:
            bells.append((t, out.bell))
        if out.merged:
            merges.append(out.merged)
    return rec, closed, bells, merges


def ticks(*runs, step=0.25):
    """[(colour, from, to)] sampled every ``step``, the way the engine ticks."""
    out = []
    for colour, lo, hi in runs:
        t = lo
        while t < hi - 1e-9:
            out.append((round(t, 3), colour))
            t += step
    return out


class TestEpisodes:
    def test_first_sight_opens_an_episode_and_never_rings(self):
        rec, closed, bells, _ = run([(0, R_), (100, R_)])
        assert rec.episode.colour == R_ and rec.episode.start == 0
        assert bells == [] and closed == []

    def test_a_stretch_seen_on_first_sight_never_rings_later_either(self):
        """Red at startup, then orange: still the stretch that was already
        there when the engine started, so it doesn't ring (d679fff)."""
        _, _, bells, _ = run(ticks((R_, 0, 10), (O_, 10, 60)))
        assert bells == []

    def test_a_stall_rings_once_when_confirmed(self):
        rec, closed, bells, _ = run(ticks((G_, 0, 8), (R_, 8, 60)))
        assert [(round(t, 2), b) for t, b in bells] == [(28.0, Bell(R_, 8.0))]
        assert [(e.colour, e.start, e.end) for e in closed] == [(G_, 0, 8.0)]
        assert rec.episode.colour == R_ and rec.episode.start == 8.0

    def test_a_brief_excursion_merges_and_time_in_state_snaps_back(self):
        rec, closed, bells, merges = run(ticks((G_, 0, 10), (R_, 10, 15), (G_, 15, 40)))
        assert closed == [] and bells == []
        assert merges == [(R_, 10.0, 15.0)]
        assert rec.episode.start == 0 and rec.live_since == 0
        assert rec.episode.blips == ((R_, 10.0, 15.0),)

    def test_live_colour_and_since_follow_a_pending_excursion(self):
        rec, *_ = run(ticks((G_, 0, 10), (R_, 10, 12)))
        assert rec.pending and rec.live_colour == R_ and rec.live_since == 10
        assert rec.episode.colour == G_

    def test_merge_boundary_is_inclusive_at_g(self):
        _, closed, _, merges = run([(0, G_), (10, R_), (10 + G, G_)])
        assert merges and not closed

    def test_a_return_after_g_is_history_not_a_blip(self):
        rec, closed, bells, merges = run([(0, G_), (10, R_), (10 + G + 1, G_)])
        assert merges == []
        assert [(e.colour, e.start, e.end) for e in closed] == [(G_, 0, 10), (R_, 10, 10 + G + 1)]
        assert rec.episode.colour == G_ and rec.episode.start == 10 + G + 1
        # It came back on the very sample that confirmed it: the red stretch
        # was already over, so nothing rings.
        assert bells == []

    def test_an_excursion_that_settles_elsewhere_keeps_every_colour(self):
        rec, closed, bells, _ = run(ticks((G_, 0, 10), (R_, 10, 12), (Y_, 12, 60)))
        assert [(e.colour, e.start, e.end) for e in closed] == [(G_, 0, 10), (R_, 10, 12)]
        assert rec.episode.colour == Y_ and rec.episode.start == 12
        assert bells == []  # the red was over before it stood

    def test_orange_then_red_is_one_stretch_one_bell(self):
        _, _, bells, _ = run(ticks((G_, 0, 5), (O_, 5, 30), (R_, 30, 80)))
        assert len(bells) == 1 and bells[0][1] == Bell(O_, 5.0)

    def test_a_new_stretch_after_a_visit_rings_again(self):
        rec = EpisodeRecorder()
        _, _, bells, _ = run(ticks((G_, 0, 5), (R_, 5, 40)), rec)
        assert len(bells) == 1
        rec.visit(41)
        # Green for longer than G, so it stands, then a new stall
        _, _, bells, _ = run(ticks((G_, 41, 70), (R_, 70, 110)), rec)
        assert [b for _, b in bells] == [Bell(R_, 70.0)]

    def test_a_short_return_to_work_merges_into_the_stall(self):
        """Green for under G inside a red stretch is a blip of the red."""
        rec = EpisodeRecorder()
        run(ticks((G_, 0, 5), (R_, 5, 40)), rec)
        rec.visit(41)
        _, _, bells, merges = run(ticks((G_, 41, 60), (R_, 60, 100)), rec)
        assert bells == [] and merges == [(G_, 41.0, 60.0)]

    def test_a_stretch_that_began_before_the_last_visit_does_not_ring(self):
        rec = EpisodeRecorder(visited_at=50)
        _, _, bells, _ = run(ticks((G_, 0, 30), (R_, 30, 100)), rec)
        assert bells == []

    def test_unvisited_stretches_each_ring(self):
        _, _, bells, _ = run(ticks((G_, 0, 5), (R_, 5, 40), (G_, 40, 70), (R_, 70, 100)))
        assert [b.start for _, b in bells] == [5.0, 70.0]

    def test_lifecycle_states_hold_the_record(self):
        rec, closed, bells, _ = run([(0, G_), (5, None), (6, "terminated"), (7, G_)])
        assert closed == [] and rec.episode.colour == G_ and not rec.pending

    def test_a_blip_inside_a_pending_excursion_merges_into_its_colour(self):
        rec, closed, bells, _ = run(ticks((G_, 0, 10), (Y_, 10, 14), (O_, 14, 18), (Y_, 18, 60)))
        assert [(e.colour, e.start, e.end) for e in closed] == [(G_, 0, 10)]
        assert rec.episode.colour == Y_ and rec.episode.start == 10
        assert rec.episode.blips == ((O_, 14.0, 18.0),)
        assert bells == []

    def test_blips_belong_to_the_episode_they_happened_in(self):
        _, closed, _, _ = run(ticks((G_, 0, 10), (R_, 10, 12), (G_, 12, 50), (Y_, 50, 100)))
        green = closed[0]
        assert green.colour == G_ and green.blips == ((R_, 10.0, 12.0),)

    @pytest.mark.parametrize("seed", range(300))
    def test_closed_episodes_tile_time_without_gaps_or_overlap(self, seed):
        import random

        rng = random.Random(seed)
        t, steps = 0.0, []
        for _ in range(400):
            t += rng.choice([0.25, 0.25, 0.5, 2, 7, 25])
            steps.append((t, rng.choice([G_, Y_, O_, R_, G_, G_])))
        rec, closed, bells, _ = run(steps)
        for a, b in zip(closed, closed[1:]):
            assert a.end == b.start
            assert a.colour != b.colour  # a same-colour neighbour would have been one episode
            assert a.start < a.end
        if closed:
            assert closed[-1].end == rec.episode.start
        # every bell is for an input-needed colour, one per stretch start
        starts = [b.start for _, b in bells]
        assert len(starts) == len(set(starts))
        assert all(b.colour in (R_, O_) for _, b in bells)


class TestReplayScenarios:
    """The #507 scenarios through the real hook handler and detector, recorded."""

    def _record(self, scenario, tmp_path, end=None):
        from tests.status_replay import Scenario, replay

        if end is not None:
            scenario = Scenario(scenario.name, scenario.steps, scenario.expect, end=end,
                                child=scenario.child, initial_pane=scenario.initial_pane)
        samples = replay(scenario, tmp_path)
        return run([(s.t, s.colour) for s in samples])

    def test_a_plain_turn_rings_once_for_the_stall(self, tmp_path):
        from tests.unit.test_status_replay import PLAIN_TURN

        rec, closed, bells, merges = self._record(PLAIN_TURN, tmp_path, end=60)
        assert len(bells) == 1 and bells[0][1].colour == R_
        assert 8.0 <= bells[0][1].start <= 8.25
        assert merges == []

    def test_a_quick_reply_then_reprompt_is_one_green_episode(self, tmp_path):
        """Stop, a 9 s red, then the person prompts again: a blip, no bell."""
        from tests.status_replay import Scenario, ev

        scenario = Scenario(
            "quick_reply_reprompt",
            steps=[ev(0.0, "UserPromptSubmit"), ev(0.5, "Stop"),
                   ev(10.0, "UserPromptSubmit"), ev(30.0, "Stop")],
            expect=[(0.0, 1.5, G_)], end=40,
        )
        rec, closed, bells, merges = self._record(scenario, tmp_path)
        assert [m[0] for m in merges] == [R_]
        assert closed == [] or closed[0].colour == G_
        assert bells == []

    def test_background_shell_wait_is_yellow_not_a_bell(self, tmp_path):
        from tests.unit.test_status_replay import BACKGROUND_SHELL

        rec, closed, bells, _ = self._record(BACKGROUND_SHELL, tmp_path, end=63)
        assert bells == []
        assert rec.episode.colour == Y_

    def test_a_brief_subagent_permission_prompt_merges(self, tmp_path):
        """Orange for ~5 s while the parent waits yellow: merged, no bell."""
        from tests.unit.test_status_replay import SUBAGENT_PERMISSION

        rec, closed, bells, merges = self._record(SUBAGENT_PERMISSION, tmp_path, end=29)
        colours = [e.colour for e in closed] + [rec.episode.colour]
        assert O_ not in colours
        yellow = rec.episode if rec.episode.colour == Y_ else next(e for e in closed if e.colour == Y_)
        assert [b[0] for b in yellow.blips] == [O_]
        assert bells == []


class TestPublishing:
    def test_badges_publish_an_absolute_eta(self):
        from overcode.episodes import status_detail_view
        from overcode.status_constants import StatusBadge, StatusDetail

        detail = StatusDetail(Y_, [StatusBadge("schedule_wakeup", eta_seconds=240.4),
                                   StatusBadge("monitor", count=2)], "busy_sleeping")
        a = status_detail_view(detail, 1000.0)
        assert a == {"color": Y_, "legacy_status": "busy_sleeping", "badges": [
            {"kind": "schedule_wakeup", "eta_at": 1240}, {"kind": "monitor", "count": 2}]}
        # Two ticks later the countdown moved, the published view did not
        later = StatusDetail(Y_, [StatusBadge("schedule_wakeup", eta_seconds=238.4),
                                  StatusBadge("monitor", count=2)], "busy_sleeping")
        assert status_detail_view(later, 1002.0) == a
        assert status_detail_view(None, 0) is None

    def test_episode_log_appends_rotates_and_reads_back(self, tmp_path, monkeypatch):
        from overcode import episodes
        from overcode.episodes import Episode, append_episode, read_episodes

        monkeypatch.setattr(episodes, "EPISODE_LOG_MAX_BYTES", 300)
        for i in range(10):
            append_episode(tmp_path, "agent", "sid", Episode(G_, float(i), float(i) + 1))
        got = read_episodes(tmp_path, "agent")
        assert (tmp_path / "episodes_agent.jsonl.1").exists()
        starts = [e["start"] for e in got]
        assert starts == sorted(starts) and starts[-1] == 9.0
        assert all(e["session_id"] == "sid" for e in got)

    def test_an_unwritable_log_never_raises(self, tmp_path):
        from overcode.episodes import Episode, append_episode

        append_episode(tmp_path / "missing" / "dir", "a", "s", Episode(G_, 0.0, 1.0))


class TestDaemonRecords:
    """The monitor daemon feeds each tick's colour to the agent's recorder."""

    def _daemon(self, tmp_path):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from overcode.monitor_daemon import MonitorDaemon

        daemon = MonitorDaemon.__new__(MonitorDaemon)
        daemon._recorders = {}
        daemon.state_path = tmp_path / "state.json"
        daemon.log = MagicMock()
        daemon._engine = MagicMock()
        daemon.detector = SimpleNamespace(detail=None)
        daemon.detector.get_status_detail = lambda name: daemon.detector.detail
        return daemon

    def _tick(self, daemon, colour, t, status="running"):
        from datetime import datetime
        from types import SimpleNamespace

        from overcode.monitor_daemon_state import SessionDaemonState
        from overcode.status_constants import StatusDetail

        daemon.detector.detail = StatusDetail(colour, [], status) if colour else None
        state = SessionDaemonState(session_id="s1", name="a1")
        daemon._record_episode(SimpleNamespace(id="s1", name="a1"), state, status,
                               datetime.fromtimestamp(t))
        return state

    def test_publishes_live_and_recorded_colour(self, tmp_path):
        daemon = self._daemon(tmp_path)
        self._tick(daemon, G_, 1000)
        state = self._tick(daemon, R_, 1010, "waiting_user")
        assert (state.live_colour, state.live_since) == (R_, 1010)
        assert (state.episode_colour, state.episode_start) == (G_, 1000)
        assert state.status_detail["color"] == R_

    def test_a_confirmed_stall_rings_the_engine_and_logs_the_episode(self, tmp_path):
        from overcode.episodes import read_episodes

        daemon = self._daemon(tmp_path)
        self._tick(daemon, G_, 1000)
        self._tick(daemon, R_, 1010, "waiting_user")
        daemon._engine.ring.assert_not_called()
        state = self._tick(daemon, R_, 1031, "waiting_user")
        daemon._engine.ring.assert_called_once()
        sid, bell = daemon._engine.ring.call_args.args
        assert sid == "s1" and bell["colour"] == R_ and bell["start"] == 1010
        assert state.episode_colour == R_
        assert [(e["colour"], e["start"], e["end"]) for e in read_episodes(tmp_path, "a1")] == [
            (G_, 1000, 1010)]

    def test_lifecycle_states_hold_the_record(self, tmp_path):
        daemon = self._daemon(tmp_path)
        self._tick(daemon, G_, 1000)
        state = self._tick(daemon, None, 1005, "terminated")
        assert state.episode_colour == G_ and state.status_detail is None
        assert not daemon._recorders["s1"].pending
