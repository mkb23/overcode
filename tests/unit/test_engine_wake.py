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

    d = MonitorDaemon.__new__(MonitorDaemon)
    d.state_path = tmp_path / "monitor_daemon_state.json"
    d.state = MonitorDaemonState(pid=1)
    d.log = MagicMock()
    d._recorders = {}
    d._recorders_dirty = False
    d._hook_signatures = {}
    d._last_state_save = 0.0
    d._engine = None
    d._shutdown = False
    d.tmux_session = "agents"
    return d


def _touch(tmp_path, name, text):
    (tmp_path / f"hook_state_{name}.json").write_text(text)


class TestHookChanges:
    def test_first_sight_is_recorded_not_reported(self, tmp_path):
        d = _daemon(tmp_path)
        d.state.sessions = [SessionDaemonState(session_id="s1", name="a")]
        _touch(tmp_path, "a", "{}")
        assert d._hook_changes() == set()

    def test_a_rewritten_hook_file_is_reported_once(self, tmp_path):
        d = _daemon(tmp_path)
        d.state.sessions = [SessionDaemonState(session_id="s1", name="a"),
                            SessionDaemonState(session_id="s2", name="b")]
        _touch(tmp_path, "a", "{}")
        _touch(tmp_path, "b", "{}")
        d._hook_changes()
        _touch(tmp_path, "a", '{"event": "Stop"}')
        assert d._hook_changes() == {"a"}
        assert d._hook_changes() == set()

    def test_a_hook_file_appearing_or_vanishing_counts(self, tmp_path):
        d = _daemon(tmp_path)
        d.state.sessions = [SessionDaemonState(session_id="s1", name="a")]
        d._hook_changes()  # no file yet
        _touch(tmp_path, "a", "{}")
        assert d._hook_changes() == {"a"}
        os.unlink(tmp_path / "hook_state_a.json")
        assert d._hook_changes() == {"a"}

    def test_departed_agents_are_forgotten(self, tmp_path):
        d = _daemon(tmp_path)
        d.state.sessions = [SessionDaemonState(session_id="s1", name="a")]
        d._hook_changes()
        d.state.sessions = []
        d._hook_changes()
        assert d._hook_signatures == {}


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
        d._quick_tick({"b"}, datetime.now())
        assert detected == ["b"]
        states, save = published[0]
        assert [(s.name, s.current_status) for s in states] == [("a", "running"), ("b", "waiting_user")]
        d._flush_pending_writes.assert_called_once()

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
        d._quick_tick({"a"}, datetime.now())
        d._last_state_save = time.monotonic() - 5
        d._quick_tick({"a"}, datetime.now())
        assert saves == [False, True]

    def test_the_sleep_runs_quick_ticks_and_keeps_its_length(self, tmp_path):
        from overcode import monitor_daemon

        d = _daemon(tmp_path)
        d.state.interval_mode = "attended"
        changes = iter([set(), {"a"}] + [set()] * 100)
        d._hook_changes = lambda: next(changes)
        d._quick_tick = MagicMock()
        slept = []
        with patch.object(monitor_daemon.time, "sleep", slept.append), \
                patch.object(monitor_daemon, "check_activity_signal", return_value=False):
            d._interruptible_sleep(2)
        assert sum(slept) == pytest.approx(2)
        assert set(slept) == {monitor_daemon.WAKE_SCAN_ATTENDED_SECONDS}
        d._quick_tick.assert_called_once()

    def test_unattended_scans_every_two_seconds_but_listens_every_second(self, tmp_path):
        from overcode import monitor_daemon

        d = _daemon(tmp_path)
        d.state.interval_mode = "unattended"
        scans = []
        d._hook_changes = lambda: scans.append(1) or set()
        slept = []
        with patch.object(monitor_daemon.time, "sleep", slept.append), \
                patch.object(monitor_daemon, "check_activity_signal", return_value=False):
            d._interruptible_sleep(10)
        assert slept == [1.0] * 10
        assert len(scans) == 5

    def test_a_failed_quick_tick_never_ends_the_sleep_early(self, tmp_path):
        from overcode import monitor_daemon

        d = _daemon(tmp_path)
        d.state.interval_mode = "attended"
        d._hook_changes = lambda: {"a"}
        d._quick_tick = MagicMock(side_effect=RuntimeError("boom"))
        slept = []
        with patch.object(monitor_daemon.time, "sleep", slept.append), \
                patch.object(monitor_daemon, "check_activity_signal", return_value=False):
            d._interruptible_sleep(1)
        assert sum(slept) == pytest.approx(1)


class TestVisitsAndPersistence:
    def test_a_visit_from_a_view_reaches_the_recorder(self, tmp_path):
        d = _daemon(tmp_path)
        d._recorders["s1"] = EpisodeRecorder()
        d._engine = SimpleNamespace(take_visits=lambda: [("s1", 500.0), ("gone", 1.0)])
        d._apply_visits()
        assert d._recorders["s1"].visited_at == 500.0 and d._recorders_dirty

    def test_recorders_survive_a_restart_without_re_ringing(self, tmp_path):
        d = _daemon(tmp_path)
        rec = EpisodeRecorder()
        for t, c in [(0, G_), (10, R_), (31, R_)]:
            last = rec.observe(c, t)
        assert last.bell is not None  # rang before the restart
        rec.visit(40)
        d._recorders = {"s1": rec}
        d._recorders_dirty = True
        d._save_recorders({"s1"})

        d2 = _daemon(tmp_path)
        d2._load_recorders()
        again = d2._recorders["s1"]
        assert again.episode.colour == R_ and again.episode.start == 10
        assert again.visited_at == 40
        # Still the same stretch after the restart: no second bell
        assert again.observe(R_, 100).bell is None
        # A new stretch after the visit rings as usual
        bells = [again.observe(c, t).bell for t, c in [(110, G_), (140, R_), (161, R_)]]
        assert [b.start for b in bells if b] == [140]

    def test_gone_agents_are_dropped_and_clean_state_is_not_rewritten(self, tmp_path):
        d = _daemon(tmp_path)
        d._recorders = {"s1": EpisodeRecorder(), "old": EpisodeRecorder()}
        d._recorders_dirty = True
        d._save_recorders({"s1"})
        assert set(d._recorders) == {"s1"}
        path = d._engine_state_path()
        mtime = path.stat().st_mtime_ns
        d._save_recorders({"s1"})
        assert path.stat().st_mtime_ns == mtime

    def test_a_corrupt_engine_state_starts_fresh(self, tmp_path):
        d = _daemon(tmp_path)
        d._engine_state_path().write_text("{not json")
        d._load_recorders()
        assert d._recorders == {}
        d._engine_state_path().write_text('{"recorders": {"s1": {"episode": {"colour": "mauve"}}}}')
        d._load_recorders()
        assert d._recorders["s1"].episode is None


class TestEngineOwnsTheNumbers:
    """Stats, git and burn are computed by the engine on their contract cadences."""

    def test_stats_sync_every_five_seconds_attended_sixty_unattended(self, tmp_path):
        from datetime import timedelta

        d = _daemon(tmp_path)
        synced = []
        d.sync_agent_stats = synced.append
        t0 = datetime(2026, 10, 7, 12, 0, 0)
        d._last_stats_sync = t0
        d.state.interval_mode = "attended"
        d._sync_session_stats(["s"], t0 + timedelta(seconds=5))
        assert synced == ["s"]
        d.state.interval_mode = "unattended"
        d._sync_session_stats(["s"], t0 + timedelta(seconds=30))
        assert synced == ["s"]
        d._sync_session_stats(["s"], t0 + timedelta(seconds=66))
        assert synced == ["s", "s"]

    def test_git_is_read_once_per_directory_while_attended_only(self, tmp_path, monkeypatch):
        from datetime import timedelta

        from overcode import tui_helpers

        calls = []
        monkeypatch.setattr(tui_helpers, "get_git_diff_stats", lambda d: calls.append(d) or (2, 10, 3))
        monkeypatch.setattr(tui_helpers, "get_git_untracked_count", lambda d: 4)
        monkeypatch.setattr(tui_helpers, "effective_git_directory", lambda s: s.dir)
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
        from overcode import tui_logic
        from overcode.tui_logic import WindowBurnStats

        asked = []

        def fake_burn(sessions, asleep, hours, now=None):
            asked.append(hours)
            stats = WindowBurnStats(window_hours=hours)
            stats.per_session["s1"] = WindowBurnStats(window_hours=hours, input_tokens=100,
                                                      cost_usd=0.5, energy_j=7.0)
            return stats

        monkeypatch.setattr(tui_logic, "compute_window_burn", fake_burn)
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
