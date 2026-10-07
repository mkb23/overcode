"""What the engine publishes so views compute nothing (0.6.0, step 3).

The TUI renders every per-agent column from engine.sock: the
transcript-derived values beside the token columns, the pane-derived
counts, the attention fields behind the 🔔 highlight, and time accounting
that holds still between status changes. Each field must keep its value
while nothing happens, so a quiet fleet publishes no deltas.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from overcode.episodes import EpisodeRecorder
from overcode.monitor_daemon_state import MonitorDaemonState, SessionDaemonState
from overcode.status_constants import STATUS_COLOR_GREEN as G_, STATUS_COLOR_RED as R_
from tests.daemon_tick_harness import (
    FrozenClock,
    ScriptedDetector,
    make_daemon,
    run_ticks,
    seed_sessions,
    seed_steady_state,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state" / "sessions"))
    (tmp_path / "state" / "sessions").mkdir(parents=True)
    return tmp_path


def _fleet(env, n=4, script=None, start=datetime(2026, 10, 7, 12, 0, 0)):
    from overcode.session_manager import SessionManager

    sm = SessionManager(state_dir=env / "state" / "sessions", skip_git_detection=True)
    sessions = seed_sessions(sm, n, "agents", env / "work", start)
    detector = ScriptedDetector(script or (lambda tick, s: ("waiting_user", "Waiting", "")))
    daemon = make_daemon(env / "state", "agents", detector, session_manager=sm)
    seed_steady_state(daemon, sessions, start)
    return daemon, detector, sessions, FrozenClock(start)


class TestAQuietFleetPublishesNothing:
    def test_consecutive_quiet_ticks_produce_an_empty_delta(self, env):
        from overcode.engine_protocol import diff

        daemon, detector, _, clock = _fleet(env)
        snaps = []
        with clock.installed():
            # past the first stats sync (real news), then two quiet syncs' worth
            run_ticks(daemon, detector, clock, 4)
            snaps.append(daemon.state.to_engine_snapshot())
            run_ticks(daemon, detector, clock, 6)
            snaps.append(daemon.state.to_engine_snapshot())
        delta = diff(snaps[0], snaps[1])
        assert delta.empty, (delta.set, delta.fleet_set)

    def test_the_growing_accumulators_are_not_published(self, env):
        daemon, detector, _, clock = _fleet(env, n=1)
        with clock.installed():
            run_ticks(daemon, detector, clock, 2)
        agent = next(iter(daemon.state.to_engine_snapshot().agents.values()))
        for name in MonitorDaemonState.ENGINE_TICK_AGENT_FIELDS:
            assert name not in agent
        assert agent["time_base"] is not None and agent["time_base_at"] is not None


class TestTimeBase:
    def test_rebased_only_when_the_status_changes(self, env):
        status = ["running"]
        daemon, detector, sessions, clock = _fleet(
            env, n=1, script=lambda tick, s: (status[0], "", ""))
        sid = sessions[0].id
        bases = []
        with clock.installed():
            for now in ["running", "running", "running", "waiting_user", "waiting_user"]:
                status[0] = now
                run_ticks(daemon, detector, clock, 1)
                bases.append(daemon._time_bases[sid])
        # running x3 holds one base; the change to waiting re-bases once
        assert bases[0] == bases[1] == bases[2]
        assert bases[3] != bases[2]
        assert bases[3] == bases[4]

    def test_the_base_plus_elapsed_is_what_the_accumulators_say(self, env):
        """A view extrapolating from the base sees what the daemon accumulated."""
        from overcode.tui_helpers import get_current_state_times

        daemon, detector, sessions, clock = _fleet(
            env, n=1, script=lambda t, s: ("running", "", ""))
        sid = sessions[0].id
        with clock.installed():
            run_ticks(daemon, detector, clock, 5)
        state = daemon.state.sessions[0]
        base, base_at = state.time_base, state.time_base_at
        stats = SimpleNamespace(
            green_time_seconds=base[0], non_green_time_seconds=base[1], sleep_time_seconds=base[2],
            last_time_accumulation=datetime.fromtimestamp(base_at).isoformat(),
            state_since=None, current_state="running",
        )
        on_disk = daemon.session_manager.get_session(sid).stats
        accumulated_at = datetime.fromisoformat(on_disk.last_time_accumulation)
        green, non_green, _ = get_current_state_times(stats, now=accumulated_at)
        assert green == pytest.approx(on_disk.green_time_seconds)
        assert non_green == pytest.approx(on_disk.non_green_time_seconds)


class TestStatsView:
    def test_a_backend_without_stats_says_so(self):
        from overcode.monitor_daemon import MonitorDaemon

        d = MonitorDaemon.__new__(MonitorDaemon)
        d._stats_views = {}
        d._note_stats_view(SimpleNamespace(id="s1"), None)
        assert d._stats_views["s1"] == {"stats_available": False}

    def test_transcript_values_are_kept_for_the_state(self):
        from overcode.history_reader import AgentSessionStats
        from overcode.monitor_daemon import MonitorDaemon

        d = MonitorDaemon.__new__(MonitorDaemon)
        d._stats_views = {}
        stats = AgentSessionStats(
            interaction_count=3, input_tokens=10, output_tokens=5,
            cache_creation_tokens=0, cache_read_tokens=0, work_times=[10.0, 30.0, 20.0],
            current_context_tokens=1000, live_subagent_count=2, model="claude-sonnet-4-5",
        )
        d._note_stats_view(SimpleNamespace(id="s1"), stats)
        assert d._stats_views["s1"] == {
            "stats_available": True,
            "work_median_seconds": 20.0,
            "context_window": stats.max_context_tokens,
            "file_subagent_count": 2,
        }
        assert stats.max_context_tokens  # the bundled table knows sonnet

    def test_a_tick_publishes_the_stats_view(self, env):
        daemon, detector, sessions, clock = _fleet(env, n=1)
        daemon._stats_views[sessions[0].id] = {"stats_available": True, "work_median_seconds": 42.0,
                                                "context_window": 200000, "file_subagent_count": 1}
        with clock.installed():
            run_ticks(daemon, detector, clock, 1)
        state = daemon.state.sessions[0]
        assert (state.stats_available, state.work_median_seconds, state.context_window,
                state.file_subagent_count) == (True, 42.0, 200000, 1)


class TestPaneView:
    PANE = "⏵⏵ accept edits on (shift+tab to cycle)\n· 2 background tasks"

    def test_a_tick_publishes_the_pane_derived_counts(self, env):
        daemon, detector, _, clock = _fleet(
            env, n=1, script=lambda t, s: ("running", "", TestPaneView.PANE))
        with clock.installed():
            run_ticks(daemon, detector, clock, 1)
        from overcode.status_patterns import extract_from_pane

        expected = extract_from_pane(self.PANE)
        state = daemon.state.sessions[0]
        assert state.auto_accept_mode == expected.auto_accept_mode
        assert state.background_bash_count == expected.background_bash_count
        assert state.live_subagent_count == expected.live_subagent_count

    def test_unchanged_pane_text_is_parsed_once(self, env):
        daemon, detector, _, clock = _fleet(
            env, n=1, script=lambda t, s: ("running", "", TestPaneView.PANE))
        from overcode import status_patterns

        with patch.object(status_patterns, "extract_from_pane",
                          wraps=status_patterns.extract_from_pane) as spy, clock.installed():
            run_ticks(daemon, detector, clock, 3)
        assert spy.call_count == 1

    def test_no_pane_text_means_zero_counts(self, env):
        daemon, detector, _, clock = _fleet(env, n=1)
        with clock.installed():
            run_ticks(daemon, detector, clock, 1)
        state = daemon.state.sessions[0]
        assert (state.background_bash_count, state.live_subagent_count,
                state.auto_accept_mode) == (0, 0, False)

    def test_departed_sessions_are_forgotten(self):
        from overcode.monitor_daemon import MonitorDaemon

        d = MonitorDaemon.__new__(MonitorDaemon)
        d.operation_start_times, d.previous_states = {}, {}
        d._last_logged, d._last_keepalive = {}, {}
        d._time_bases = {"gone": ([0, 0, 0], 1.0)}
        d._stats_views = {"gone": {}}
        d._pane_views = {"gone": ("", {})}
        d._pane_tracker, d._capture_gate = MagicMock(), MagicMock()
        d._cleanup_stale([])
        assert not (d._time_bases or d._stats_views or d._pane_views)


class TestAttentionFields:
    def _daemon(self, tmp_path):
        from overcode.monitor_daemon import MonitorDaemon

        d = MonitorDaemon.__new__(MonitorDaemon)
        d._recorders = {}
        d._recorders_dirty = False
        d._engine = None
        d.state_path = tmp_path / "monitor_daemon_state.json"
        d.log = MagicMock()
        return d

    def test_a_red_stretch_and_the_last_visit_are_published(self, tmp_path):
        d = self._daemon(tmp_path)
        rec = d._recorders["s1"] = EpisodeRecorder(visited_at=50.0)
        rec.observe(G_, 100.0)
        rec.observe(R_, 200.0)
        rec.observe(R_, 230.0)  # confirmed after G
        state = SessionDaemonState(session_id="s1")
        detail = SimpleNamespace(color=R_, badges=[], legacy_status="waiting_user")
        d.detector = MagicMock()
        d.detector.get_status_detail.return_value = detail
        session = SimpleNamespace(id="s1", name="a")
        d._record_episode(session, state, "waiting_user", datetime.fromtimestamp(240.0))
        assert state.input_needed_since == 200.0
        assert state.visited_at == 50.0

    def test_a_green_agent_needs_nothing(self, tmp_path):
        d = self._daemon(tmp_path)
        state = SessionDaemonState(session_id="s1")
        d.detector = MagicMock()
        d.detector.get_status_detail.return_value = SimpleNamespace(
            color=G_, badges=[], legacy_status="running")
        d._record_episode(SimpleNamespace(id="s1", name="a"), state, "running",
                          datetime.fromtimestamp(100.0))
        assert state.input_needed_since is None


class TestSlowTick:
    def _daemon(self, tmp_path, duration, interval):
        from overcode.monitor_daemon import MonitorDaemon

        d = MonitorDaemon.__new__(MonitorDaemon)
        d.state = MonitorDaemonState(pid=1, current_interval=interval)
        d._last_tick_duration_seconds = duration
        d.presence = MagicMock()
        d.presence.get_current_state.return_value = (None, None, None)
        d.presence.available = False
        d.tmux_session = "agents"
        d.state_path = tmp_path / "state.json"
        d._engine = None
        return d

    def test_published_only_while_ticks_overrun(self, tmp_path):
        d = self._daemon(tmp_path, 3.04, 2)
        with patch("overcode.monitor_daemon.get_supervisor_stats_path",
                   return_value=tmp_path / "none.json"):
            d._publish_state([], save=False)
            assert d.state.slow_tick_seconds == 3.0
            d._last_tick_duration_seconds = 0.4
            d._publish_state([], save=False)
        assert d.state.slow_tick_seconds is None


class TestWakeOnVisibleView:
    def test_an_unattended_sleep_ends_when_a_view_becomes_visible(self, tmp_path):
        from overcode import monitor_daemon
        from overcode.monitor_daemon import MonitorDaemon

        d = MonitorDaemon.__new__(MonitorDaemon)
        d.state = MonitorDaemonState(pid=1, interval_mode="unattended")
        d.tmux_session = "agents"
        d.log = MagicMock()
        d._shutdown = False
        d._engine = SimpleNamespace(attended=False)
        d._hook_changes = lambda: set()
        slept = []

        def sleep(seconds):
            slept.append(seconds)
            if len(slept) == 3:
                d._engine.attended = True

        with patch.object(monitor_daemon.time, "sleep", sleep), \
                patch.object(monitor_daemon, "check_activity_signal", return_value=False):
            d._interruptible_sleep(60)
        assert len(slept) == 3
