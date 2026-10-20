"""Engine: the wake scan, quick ticks, visits, and recorder persistence.

docs/design/engine-0.6.md: between full ticks the daemon stats each agent's
hook state file and re-detects only agents whose file changed; views report
visits; recorder state survives a restart so it neither forgets nor re-rings.
"""

import os
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from overcode.episodes import EpisodeRecorder
from overcode.monitor_daemon_state import SessionDaemonState
from overcode.status_constants import STATUS_COLOR_GREEN as G_, STATUS_COLOR_RED as R_

pytestmark = pytest.mark.unit


def _daemon(tmp_path):
    from overcode.monitor_daemon import MonitorDaemon
    from overcode.monitor_daemon_state import MonitorDaemonState

    d = MonitorDaemon.for_test(tmp_path, log=MagicMock(), detector=SimpleNamespace(mode="auto"))
    d.state = MonitorDaemonState(pid=1)
    d._refresh_detection_mode = lambda: None  # never reads a real state dir
    return d


@pytest.fixture
def root_for_ticks(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".overcode" / "sessions").mkdir(parents=True)
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(home / ".overcode" / "sessions"))
    return tmp_path


def _touch(tmp_path, name, text):
    (tmp_path / f"hook_state_{name}.json").write_text(text)


class TestHookChanges:
    def test_first_sight_is_recorded_not_reported(self, tmp_path):
        d = _daemon(tmp_path)
        d.state.sessions = [SessionDaemonState(session_id="s1", name="a")]
        _touch(tmp_path, "a", "{}")
        assert d.wake.changes() == set()

    def test_a_rewritten_hook_file_is_reported_once(self, tmp_path):
        d = _daemon(tmp_path)
        d.state.sessions = [SessionDaemonState(session_id="s1", name="a"),
                            SessionDaemonState(session_id="s2", name="b")]
        _touch(tmp_path, "a", "{}")
        _touch(tmp_path, "b", "{}")
        d.wake.changes()
        _touch(tmp_path, "a", '{"event": "Stop"}')
        assert d.wake.changes() == {"s1"}
        assert d.wake.changes() == set()

    def test_a_hook_file_appearing_or_vanishing_counts(self, tmp_path):
        d = _daemon(tmp_path)
        d.state.sessions = [SessionDaemonState(session_id="s1", name="a")]
        d.wake.changes()  # no file yet
        _touch(tmp_path, "a", "{}")
        assert d.wake.changes() == {"s1"}
        os.unlink(tmp_path / "hook_state_a.json")
        assert d.wake.changes() == {"s1"}

    def test_an_agent_with_its_own_directory_is_scanned_there(self, tmp_path):
        """Item 35: a stale name-keyed file of a dead namesake is never scanned."""
        d = _daemon(tmp_path)
        own = tmp_path / "agents" / "s1"
        own.mkdir(parents=True)
        d.state.sessions = [SessionDaemonState(session_id="s1", name="a")]
        d.wake.changes()
        _touch(tmp_path, "a", '{"event": "Stop"}')  # the dead namesake's file
        assert d.wake.changes() == set()
        (own / "hook_state.json").write_text('{"event": "Stop"}')
        assert d.wake.changes() == {"s1"}

    def test_departed_agents_are_forgotten(self, tmp_path):
        d = _daemon(tmp_path)
        d.state.sessions = [SessionDaemonState(session_id="s1", name="a")]
        d.wake.changes()
        d.state.sessions = []
        d.wake.changes()
        assert d.wake.scan.signatures == {}


class TestStickyRecheck:
    """A Stop inside the detector's sticky-green window (#448) reads as working
    until the window passes; nothing is written then, so the scan re-detects
    the agent once, and the Stop shows when the window ends rather than at
    the next full tick."""

    def test_a_changed_agent_is_reported_again_when_the_window_passes(self, tmp_path):
        from overcode import engine_wake

        d = _daemon(tmp_path)
        d.state.sessions = [SessionDaemonState(session_id="s1", name="a")]
        _touch(tmp_path, "a", "{}")
        clock = [100.0]
        with patch.object(engine_wake.time, "monotonic", lambda: clock[0]):
            d.wake.changes()
            _touch(tmp_path, "a", '{"event": "Stop"}')
            assert d.wake.changes() == {"s1"}
            clock[0] += 0.5
            assert d.wake.changes() == set()  # still inside the window
            clock[0] += engine_wake.sticky_recheck_seconds()
            assert d.wake.changes() == {"s1"}  # once, at the window's end
            clock[0] += 5
            assert d.wake.changes() == set()

    def test_the_recheck_follows_the_detectors_window(self):
        from overcode import engine_wake
        from overcode.hook_status_detector import _RECENT_ACTIVITY_WINDOW_SECONDS

        assert engine_wake.sticky_recheck_seconds() > _RECENT_ACTIVITY_WINDOW_SECONDS
        assert engine_wake.sticky_recheck_seconds() <= _RECENT_ACTIVITY_WINDOW_SECONDS + 0.5

    def test_a_quick_tick_rereads_the_detection_mode(self, tmp_path):
        """An "auto" fleet's first hook event is read as one at once, not
        after the next full tick flips the mode."""
        d = _daemon(tmp_path)
        calls = []
        d._refresh_detection_mode = lambda: calls.append(1)
        d._session_index = lambda: SimpleNamespace(by_id={})
        d.wake.quick_tick({"s1"}, datetime.now())
        assert calls == [1]
        # Once the fleet is on hooks, a quick tick does not re-read it (the
        # full tick still does, for a mode the user changes by hand)
        d.detector.mode = "hooks"
        d.wake.quick_tick({"s1"}, datetime.now())
        assert calls == [1]

    def test_the_resolved_mode_is_published_in_the_fleet(self, tmp_path):
        """Views (the `d` panel) show the fleet mode without resolving it."""
        from overcode.monitor_daemon import MonitorDaemon

        d = _daemon(tmp_path)
        with patch("overcode.settings.resolve_detection_mode", lambda session, *a: "hooks"):
            MonitorDaemon._refresh_detection_mode(d)
        assert d.detector.mode == "hooks"
        assert d.state.to_engine_snapshot().fleet["detection_mode"] == "hooks"

    def test_only_a_stop_is_rechecked(self, tmp_path):
        """The sticky window only holds a Stop; other events need no second look."""
        from overcode import engine_wake

        d = _daemon(tmp_path)
        d.state.sessions = [SessionDaemonState(session_id="s1", name="a")]
        _touch(tmp_path, "a", "{}")
        clock = [100.0]
        with patch.object(engine_wake.time, "monotonic", lambda: clock[0]):
            d.wake.changes()
            _touch(tmp_path, "a", '{"event": "PostToolUse", "tool_name": "Read"}')
            assert d.wake.changes() == {"s1"}
            clock[0] += 5
            assert d.wake.changes() == set()


class TestQuickTick:
    def test_only_changed_agents_are_redetected_and_merged_in_order(self, tmp_path):
        d = _daemon(tmp_path)
        d.state.sessions = [SessionDaemonState(session_id="s1", name="a", current_status="running"),
                            SessionDaemonState(session_id="s2", name="b", current_status="running")]
        sessions = [SimpleNamespace(id="s1", name="a", tmux_session="agents"),
                    SimpleNamespace(id="s2", name="b", tmux_session="agents")]
        d._session_index = lambda: SimpleNamespace(by_id={s.id: s for s in sessions})
        detected = []

        def detect(changed, now, index):
            detected.extend(s.name for s in changed)
            return [SessionDaemonState(session_id="s2", name="b", current_status="waiting_user")], False

        d._detect_and_enrich = detect
        d._flush_pending_writes = MagicMock()
        published = []
        d._publish_state = lambda states, save=True: published.append((states, save))
        d.wake.quick_tick({"s2"}, datetime.now())
        assert detected == ["b"]
        states, save = published[0]
        assert [(s.name, s.current_status) for s in states] == [("a", "running"), ("b", "waiting_user")]
        # sessions.json is the full tick's to write
        d._flush_pending_writes.assert_not_called()

    def test_quick_ticks_leave_sessions_json_to_the_full_tick(self, root_for_ticks):
        """An unchanged agent re-detected on every hook event stages its
        time accumulation, which the next full tick writes once: no write
        (and fsync) of sessions.json per quick tick."""
        from datetime import timedelta

        from tests.daemon_tick_harness import FrozenClock
        from tests.unit.test_child_results import START, _daemon as tick_daemon, _ticks

        daemon, sm, sessions = tick_daemon(root_for_ticks, {"agent-1": "running"})
        agent = sessions[1]
        _ticks(daemon, 2)
        writes = []
        commit = sm.commit_pending
        sm.commit_pending = lambda pending: writes.append(commit(pending)) or writes[-1]
        with FrozenClock(START + timedelta(seconds=4)).installed() as clock:
            for i in range(8):
                clock.now = START + timedelta(seconds=4, milliseconds=250 * i)
                daemon.wake.quick_tick({agent.id}, clock.now)
            assert not any(writes)
            accumulated = clock.now
            clock.now = START + timedelta(seconds=6)
            daemon._flush_pending_writes()  # what the full tick ends with
        assert writes == [True]
        from overcode.clock import to_epoch

        saved = sm.get_session(agent.id).stats.last_time_accumulation
        assert to_epoch(saved) == pytest.approx(to_epoch(accumulated.isoformat()))

    def test_a_redetected_parent_keeps_its_childrens_cost(self, tmp_path):
        """Only the parent changed: its subtree cost still counts the
        children it wasn't re-detected with (was published as 0)."""
        d = _daemon(tmp_path)
        d.state.sessions = [
            SessionDaemonState(session_id="p", name="parent", estimated_cost_usd=1.0,
                               subtree_cost_usd=3.0),
            SessionDaemonState(session_id="c", name="child", parent_name="parent",
                               estimated_cost_usd=2.0),
        ]
        d._session_index = lambda: SimpleNamespace(
            by_id={"p": SimpleNamespace(id="p", name="parent", tmux_session="agents")})
        d._detect_and_enrich = lambda c, n, i: (
            [SessionDaemonState(session_id="p", name="parent", estimated_cost_usd=1.5)], False)
        d._flush_pending_writes = MagicMock()
        published = []
        d._publish_state = lambda states, save=True: published.append(states)
        d.wake.quick_tick({"p"}, datetime.now())
        parent = next(s for s in published[0] if s.name == "parent")
        assert parent.subtree_cost_usd == 3.5

    def test_the_state_file_is_saved_at_most_once_a_second(self, tmp_path):
        import time

        d = _daemon(tmp_path)
        d.state.sessions = [SessionDaemonState(session_id="s1", name="a")]
        d._session_index = lambda: SimpleNamespace(
            by_id={"s1": SimpleNamespace(id="s1", name="a", tmux_session="agents")})
        d._detect_and_enrich = lambda c, n, i: ([SessionDaemonState(session_id="s1", name="a")], False)
        d._flush_pending_writes = MagicMock()
        saves = []
        d._publish_state = lambda states, save=True: saves.append(save)
        d._last_state_save = time.monotonic()
        d.wake.quick_tick({"s1"}, datetime.now())
        d._last_state_save = time.monotonic() - 5
        d.wake.quick_tick({"s1"}, datetime.now())
        assert saves == [False, True]

    def test_the_sleep_runs_quick_ticks_and_keeps_its_length(self, tmp_path):
        from overcode import engine_wake

        d = _daemon(tmp_path)
        d.state.interval_mode = "attended"
        changes = iter([set(), {"a"}] + [set()] * 100)
        d.wake.changes = lambda: next(changes)
        d.wake.quick_tick = MagicMock()
        slept = []
        with patch.object(engine_wake.time, "sleep", slept.append), \
                patch.object(engine_wake, "check_activity_signal", return_value=False):
            d.wake.sleep(2)
        assert sum(slept) == pytest.approx(2)
        assert set(slept) == {engine_wake.WAKE_SCAN_ATTENDED_SECONDS}
        d.wake.quick_tick.assert_called_once()

    def test_unattended_scans_every_two_seconds_but_listens_every_second(self, tmp_path):
        from overcode import engine_wake

        d = _daemon(tmp_path)
        d.state.interval_mode = "unattended"
        scans = []
        d.wake.changes = lambda: scans.append(1) or set()
        slept = []
        with patch.object(engine_wake.time, "sleep", slept.append), \
                patch.object(engine_wake, "check_activity_signal", return_value=False):
            d.wake.sleep(10)
        assert slept == [1.0] * 10
        assert len(scans) == 5

    def test_a_failed_quick_tick_never_ends_the_sleep_early(self, tmp_path):
        from overcode import engine_wake

        d = _daemon(tmp_path)
        d.state.interval_mode = "attended"
        d.wake.changes = lambda: {"a"}
        d.wake.quick_tick = MagicMock(side_effect=RuntimeError("boom"))
        slept = []
        with patch.object(engine_wake.time, "sleep", slept.append), \
                patch.object(engine_wake, "check_activity_signal", return_value=False):
            d.wake.sleep(1)
        assert sum(slept) == pytest.approx(1)

    def test_a_keypress_with_a_full_disk_never_ends_the_engine(self, tmp_path):
        """The activity signal's save is guarded like the tick's: the
        error is recorded, the sleep ends, the daemon keeps running."""
        import errno

        from overcode import engine_wake

        d = _daemon(tmp_path)
        d.state.interval_mode = "unattended"
        d.state.save = MagicMock(side_effect=OSError(errno.ENOSPC, "No space left on device"))
        slept = []
        with patch.object(engine_wake.time, "sleep", slept.append), \
                patch.object(engine_wake, "check_activity_signal", return_value=True):
            d.wake.sleep(10)
        assert slept == [1.0]
        assert d.state.interval_mode == "attended"
        assert "No space left" in d.state.state_file_error


class TestVisitsAndPersistence:
    def test_a_visit_from_a_view_reaches_the_recorder(self, tmp_path):
        d = _daemon(tmp_path)
        d.recorder_store.recorders["s1"] = EpisodeRecorder()
        d._engine = SimpleNamespace(take_visits=lambda: [("s1", 500.0), ("gone", 1.0)])
        d.recorder_store.apply_visits(d._engine)
        assert d.recorder_store.recorders["s1"].visited_at == 500.0 and d.recorder_store.dirty

    def test_recorders_survive_a_restart_without_re_ringing(self, tmp_path):
        d = _daemon(tmp_path)
        rec = EpisodeRecorder()
        for t, c in [(0, G_), (10, R_), (31, R_)]:
            last = rec.observe(c, t)
        assert last.bell is not None  # rang before the restart
        rec.visit(40)
        d.recorder_store.recorders = {"s1": rec}
        d.recorder_store.dirty = True
        d.recorder_store.save({"s1"})

        d2 = _daemon(tmp_path)
        d2.recorder_store.load()
        again = d2.recorder_store.recorders["s1"]
        assert again.episode.colour == R_ and again.episode.start == 10
        assert again.visited_at == 40
        # Still the same stretch after the restart: no second bell
        assert again.observe(R_, 100).bell is None
        # A new stretch after the visit rings as usual
        bells = [again.observe(c, t).bell for t, c in [(110, G_), (140, R_), (161, R_)]]
        assert [b.start for b in bells if b] == [140]

    def test_gone_agents_are_dropped_and_clean_state_is_not_rewritten(self, tmp_path):
        d = _daemon(tmp_path)
        d.recorder_store.recorders = {"s1": EpisodeRecorder(), "old": EpisodeRecorder()}
        d.recorder_store.dirty = True
        d.recorder_store.save({"s1"})
        assert set(d.recorder_store.recorders) == {"s1"}
        path = d.recorder_store.path
        mtime = path.stat().st_mtime_ns
        d.recorder_store.save({"s1"})
        assert path.stat().st_mtime_ns == mtime

    def test_a_corrupt_engine_state_starts_fresh(self, tmp_path):
        d = _daemon(tmp_path)
        d.recorder_store.path.write_text("{not json")
        d.recorder_store.load()
        assert d.recorder_store.recorders == {}
        d.recorder_store.path.write_text('{"recorders": {"s1": {"episode": {"colour": "mauve"}}}}')
        d.recorder_store.load()
        assert d.recorder_store.recorders["s1"].episode is None


class TestEngineOwnsTheNumbers:
    """Stats, git and burn are computed by the engine on their contract cadences."""

    def test_stats_sync_every_five_seconds_attended_sixty_unattended(self, tmp_path):
        from datetime import timedelta

        d = _daemon(tmp_path)
        synced = []
        d.sync_agent_stats = synced.append
        t0 = datetime(2026, 10, 7, 12, 0, 0)
        d.cadence.stats.last = t0
        s = SimpleNamespace(id="s", status="running")
        d.state.interval_mode = "attended"
        d._sync_session_stats([s], t0 + timedelta(seconds=5))
        assert synced == [s]
        d.state.interval_mode = "unattended"
        d._sync_session_stats([s], t0 + timedelta(seconds=30))
        assert synced == [s]
        d._sync_session_stats([s], t0 + timedelta(seconds=66))
        assert synced == [s, s]

    def test_a_finished_agent_is_read_once_more_then_never(self, tmp_path):
        """Records kept past their end must not cost every stats sync a read."""
        from datetime import timedelta

        d = _daemon(tmp_path)
        synced = []
        d.sync_agent_stats = lambda s: synced.append(s.id)
        d.state.interval_mode = "attended"
        t = datetime(2026, 10, 7, 12, 0, 0)
        live = SimpleNamespace(id="live", status="running")
        gone = SimpleNamespace(id="gone", status="terminated")
        for i in range(3):
            t += timedelta(seconds=6)
            d._sync_session_stats([live, gone], t)
        assert synced == ["live", "gone", "live", "live"]
        # Brought back (revive): read again
        gone.status = "running"
        d._sync_session_stats([live, gone], t + timedelta(seconds=6))
        assert synced[-2:] == ["live", "gone"]

    def test_git_is_read_once_per_directory_while_attended_only(self, tmp_path, monkeypatch):
        from datetime import timedelta

        from overcode import git_stats

        calls = []
        monkeypatch.setattr(git_stats, "get_git_diff_stats", lambda d: calls.append(d) or (2, 10, 3))
        monkeypatch.setattr(git_stats, "get_git_untracked_count", lambda d: 4)
        monkeypatch.setattr(git_stats, "effective_git_directory", lambda s: s.dir)
        d = _daemon(tmp_path)
        sessions = [SimpleNamespace(id="a", dir="/r1"), SimpleNamespace(id="b", dir="/r1"),
                    SimpleNamespace(id="c", dir="/r2")]
        t0 = datetime(2026, 10, 7, 12, 0, 0)
        d.state.interval_mode = "unattended"
        d._sync_git(sessions, t0)
        assert calls == []
        d.state.interval_mode = "attended"
        d._sync_git(sessions, t0)
        assert sorted(calls) == ["/r1", "/r2"]
        d._sync_git(sessions, t0 + timedelta(seconds=10))  # inside GIT_SYNC_SECONDS
        assert len(calls) == 2
        state = SessionDaemonState(session_id="b")
        d._attach_git_and_burn(sessions[1], state)
        assert (state.git_diff, state.git_untracked) == ([2, 10, 3], 4)

    def test_burn_is_computed_only_for_windows_visible_views_asked_for(self, tmp_path, monkeypatch):
        from overcode import burn
        from overcode.burn import WindowBurnStats

        asked = []

        def fake_burn(sessions, asleep, hours, now=None, quota_pp_of=None):
            asked.append(hours)
            stats = WindowBurnStats(window_hours=hours)
            stats.per_session["s1"] = WindowBurnStats(window_hours=hours, input_tokens=100,
                                                      cost_usd=0.5, energy_j=7.0)
            return stats

        monkeypatch.setattr(burn, "compute_window_burn", fake_burn)
        d = _daemon(tmp_path)
        d.state.interval_mode = "attended"
        d._engine = SimpleNamespace(burn_windows=set())
        d._sync_burn([SimpleNamespace(id="s1")], datetime.now())
        assert asked == []
        d._engine = SimpleNamespace(burn_windows={1.0, 3.0})
        d._sync_burn([SimpleNamespace(id="s1")], datetime.now())
        assert asked == [1.0, 3.0]
        state = SessionDaemonState(session_id="s1")
        d._attach_git_and_burn(SimpleNamespace(id="s1", dir=None), state)
        assert state.burn["1.0"]["input_tokens"] == 100 and state.burn["3.0"]["energy_j"] == 7.0
        d.state.interval_mode = "unattended"
        d._sync_burn([SimpleNamespace(id="s1")], datetime.now())
        assert d._burn_by_session == {}


class TestWatchdog:
    """The socket thread's watchdog for a main loop that stopped beating."""

    def _armed(self, tmp_path):
        d = _daemon(tmp_path)
        d._stall_signalled_at = None
        d._main_thread_id = 12345
        return d

    def test_a_stall_logs_the_stack_once_and_signals_every_30s(self, tmp_path):
        import signal as _signal
        d = self._armed(tmp_path)
        with patch("overcode.monitor_daemon.signal.pthread_kill") as kill:
            for age in (31.0, 32.0, 60.0, 61.0):
                d._on_main_loop_stall(age)
        assert kill.call_count == 2  # at 31 s and 61 s
        kill.assert_called_with(12345, _signal.SIGUSR1)
        assert d.log.warn.call_count == 1
        assert "stalled" in d.log.warn.call_args.args[0]

    def test_a_beat_rearms_the_watchdog(self, tmp_path):
        d = self._armed(tmp_path)
        with patch("overcode.monitor_daemon.signal.pthread_kill") as kill:
            d._on_main_loop_stall(31.0)
            d._beat()
            d._on_main_loop_stall(31.0)
        assert kill.call_count == 2

    def test_still_stuck_long_after_exits_for_a_restart(self, tmp_path):
        from overcode import monitor_daemon
        d = self._armed(tmp_path)
        with patch("overcode.monitor_daemon.os._exit") as exit_, \
                patch("overcode.monitor_daemon.signal.pthread_kill"):
            d._on_main_loop_stall(monitor_daemon.WATCHDOG_EXIT_SECONDS)
        exit_.assert_called_once_with(1)

    def test_every_sleep_step_beats(self, tmp_path):
        d = self._armed(tmp_path)
        d._engine = MagicMock(attended=False)
        d.state.interval_mode = "attended"
        d.wake.changes = lambda: set()
        with patch("overcode.monitor_daemon.time.sleep"), \
                patch("overcode.engine_wake.check_activity_signal", return_value=False):
            d.wake.sleep(1)
        assert d._engine.beat.call_count == 4  # 1 s in 0.25 s steps
