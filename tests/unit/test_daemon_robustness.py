"""The monitor daemon keeps going, and keeps honest time, when things go wrong.

- a tmux server that stops answering costs a tick one bounded wait;
- a failing detection or tick is logged and the loop carries on;
- time nobody observed (a freeze, downtime, a sleeping machine) is counted
  as no status;
- interval checks run on the clock, not the calendar (DST).
"""

import os
import stat
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from overcode.implementations import RealTmux, _command_timeout
from overcode.mocks import MockTmux
from overcode.protocols import TmuxTimeoutError

pytestmark = pytest.mark.unit


# ── bounded tmux reads ───────────────────────────────────────────────────


@pytest.fixture
def fake_tmux(tmp_path, monkeypatch):
    """A ``tmux`` on PATH that answers, or hangs while ``hang`` exists.

    Every invocation is appended to ``calls``, so a test can tell a read
    that failed fast from one that waited out its timeout.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    hang = tmp_path / "hang"
    calls = tmp_path / "calls"
    script = bin_dir / "tmux"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{calls}"\n'
        f'if [ -e "{hang}" ]; then exec sleep 30; fi\n'
        'printf "one\\ntwo\\n\\n"\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return SimpleNamespace(hang=hang, calls=calls)


def _calls(fake) -> int:
    try:
        return len(fake.calls.read_text().splitlines())
    except FileNotFoundError:
        return 0


class TestBoundedTmux:
    def test_a_wedged_server_fails_a_read_fast_instead_of_hanging(self, fake_tmux):
        fake_tmux.hang.touch()
        tmux = RealTmux(socket_name="oc-unit-nope", command_timeout=0.3)
        started = time.monotonic()
        with pytest.raises(TmuxTimeoutError):
            tmux.has_session("agents")
        assert time.monotonic() - started < 5
        # the timeout is the call's own, not left on the thread
        assert getattr(_command_timeout, "seconds", None) is None

    def test_after_a_timeout_reads_fail_at_once_for_a_while(self, fake_tmux):
        fake_tmux.hang.touch()
        tmux = RealTmux(socket_name="oc-unit-nope", command_timeout=0.3)
        with pytest.raises(TmuxTimeoutError):
            tmux.capture_pane("agents", "w1")
        before = _calls(fake_tmux)
        started = time.monotonic()
        with pytest.raises(TmuxTimeoutError):
            tmux.capture_pane("agents", "w2")
        with pytest.raises(TmuxTimeoutError):
            tmux.list_windows("agents")
        assert time.monotonic() - started < 0.2
        assert _calls(fake_tmux) == before  # no tmux was started

    def test_list_panes_answers_none_and_keeps_the_cache(self, fake_tmux):
        fake_tmux.hang.touch()
        tmux = RealTmux(socket_name="oc-unit-nope", command_timeout=0.3)
        tmux._session_cache["agents"] = ("cached", time.time())
        assert tmux.list_panes("agents") is None
        assert "agents" in tmux._session_cache

    def test_a_server_that_answers_again_is_read_again(self, fake_tmux):
        fake_tmux.hang.touch()
        tmux = RealTmux(socket_name="oc-unit-nope", command_timeout=0.3)
        assert tmux.list_panes("agents") is None
        fake_tmux.hang.unlink()
        tmux._unresponsive_until = 0.0  # the hold has passed
        assert tmux.has_session("agents") is True

    def test_timed_and_untimed_commands_parse_output_alike(self, fake_tmux):
        from libtmux.common import tmux_cmd

        from overcode.implementations import _BoundedTmuxCmd

        plain = tmux_cmd("-Lx", "list-sessions")
        _command_timeout.seconds = 2.0
        try:
            timed = _BoundedTmuxCmd("-Lx", "list-sessions")
        finally:
            _command_timeout.seconds = None
        assert (timed.stdout, timed.stderr, timed.returncode) == (
            plain.stdout, plain.stderr, plain.returncode) == (["one", "two"], [], 0)

    def test_without_a_timeout_libtmux_waits_as_before(self, fake_tmux, monkeypatch):
        tmux = RealTmux(socket_name="oc-unit-nope")
        assert tmux._command_timeout is None
        assert tmux.has_session("agents") is True

    def test_the_daemon_process_default_applies_to_every_client(self, monkeypatch):
        monkeypatch.setattr(RealTmux, "default_command_timeout", 1.5)
        assert RealTmux()._command_timeout == 1.5
        assert RealTmux(command_timeout=0.2)._command_timeout == 0.2

    def test_the_daemon_bounds_its_own_client(self, tmp_path, monkeypatch):
        from overcode import monitor_daemon
        from overcode.implementations import TMUX_COMMAND_TIMEOUT_SECONDS

        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setattr(monitor_daemon, "PresenceLogger", None)
        daemon = monitor_daemon.MonitorDaemon(tmux_session="robust", session_manager=MagicMock())
        assert daemon._tmux._command_timeout == TMUX_COMMAND_TIMEOUT_SECONDS

    def test_mock_tmux_can_play_a_wedged_server(self):
        tmux = MockTmux()
        tmux.set_pane_content("agents", "w", "hi")
        tmux.unresponsive = True
        with pytest.raises(TmuxTimeoutError):
            tmux.capture_pane("agents", "w")
        with pytest.raises(TmuxTimeoutError):
            tmux.has_session("agents")
        assert tmux.list_panes("agents") is None


# ── a failing detection or tick ──────────────────────────────────────────


@pytest.fixture
def root(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".overcode" / "sessions").mkdir(parents=True)
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(home / ".overcode" / "sessions"))
    return tmp_path


class TestDetectionFailure:
    START = datetime(2026, 10, 10, 12, 0, 0)

    def _fleet(self, root, script):
        from overcode.session_manager import SessionManager
        from tests.daemon_tick_harness import (
            FrozenClock, ScriptedDetector, make_daemon, seed_sessions, seed_steady_state,
        )

        sm = SessionManager(state_dir=root / "home" / ".overcode" / "sessions",
                            skip_git_detection=True)
        sessions = seed_sessions(sm, 3, "agents", root / "work", self.START)
        detector = ScriptedDetector(script)
        daemon = make_daemon(root / "home" / ".overcode", "agents", detector, session_manager=sm)
        seed_steady_state(daemon, sessions, self.START)
        return daemon, detector, sessions, FrozenClock(self.START)

    def test_one_agent_unreadable_leaves_the_others_and_its_last_state(self, root):
        broken = set()

        def script(tick, s):
            if s.name in broken:
                raise TmuxTimeoutError("tmux did not answer in 3s")
            return ("running", "Working", "pane") if tick == 0 else (
                "waiting_user", "Waiting", "pane")

        daemon, detector, sessions, clock = self._fleet(root, script)
        victim = sessions[0]
        with clock.installed():
            daemon._tick(clock.now)
            broken.add(victim.name)
            detector.tick = 1
            clock.now += timedelta(seconds=2)
            daemon._tick(clock.now)
        published = {s.session_id: s for s in daemon.state.sessions}
        assert published[sessions[1].id].current_status == "waiting_user"
        # the unreadable one keeps what it last showed: one tick is not news
        assert published[victim.id].current_status == "running"
        assert published[victim.id].current_activity == "Working"

    def test_an_agent_unreadable_past_the_gap_is_published_unknown(self, root):
        broken = set()

        def script(tick, s):
            if s.name in broken:
                raise TmuxTimeoutError("tmux did not answer in 3s")
            return "running", "Working", "pane"

        daemon, detector, sessions, clock = self._fleet(root, script)
        victim = sessions[0]
        with clock.installed():
            daemon._tick(clock.now)
            broken.add(victim.name)
            for _ in range(2):
                clock.now += timedelta(seconds=daemon._unobserved_gap_seconds() + 1)
                daemon._tick(clock.now)
            unknown = {s.session_id: s for s in daemon.state.sessions}[victim.id]
            assert unknown.current_status == "unknown"
            assert unknown.live_colour is None and unknown.status_detail is None
            # one warning when it started, not one per tick
            log = daemon.log.console.file.getvalue()
            assert log.count("status not detected") == 1
            broken.clear()
            clock.now += timedelta(seconds=2)
            daemon._tick(clock.now)
        back = {s.session_id: s for s in daemon.state.sessions}[victim.id]
        assert back.current_status == "running"
        assert "status detected again" in daemon.log.console.file.getvalue()

    def test_a_detector_bug_is_logged_with_its_traceback(self, root):
        def script(tick, s):
            if s.name.endswith("00"):
                raise KeyError("boom")
            return "running", "Working", "pane"

        daemon, _, _, clock = self._fleet(root, script)
        with clock.installed():
            daemon._tick(clock.now)
        log = daemon.log.console.file.getvalue()
        assert "status detection failed: KeyError" in log and "Traceback" in log
        assert len(daemon.state.sessions) == 3  # a bare unknown row, not a missing agent


class TestFailingTick:
    def _daemon(self, tmp_path):
        from overcode.monitor_daemon import MonitorDaemon
        from overcode.monitor_daemon_state import MonitorDaemonState

        daemon = MonitorDaemon.__new__(MonitorDaemon)
        daemon.log = MagicMock()
        daemon.state = MonitorDaemonState()
        daemon._publish_state = MagicMock()
        return daemon

    def test_a_failing_tick_is_logged_published_and_survived(self, tmp_path):
        daemon = self._daemon(tmp_path)
        daemon._tick = MagicMock(side_effect=ValueError("corrupt sessions.json"))
        daemon._run_tick(datetime.now())  # does not raise
        message = daemon.log.error.call_args.args[0]
        assert "ValueError: corrupt sessions.json" in message and "Traceback" in message
        assert daemon.state.status == "error"
        daemon._publish_state.assert_called_once()

    def test_the_same_failure_again_is_not_a_new_traceback_each_tick(self, tmp_path):
        daemon = self._daemon(tmp_path)
        daemon._tick = MagicMock(side_effect=ValueError("same"))
        for _ in range(8):
            daemon._run_tick(datetime.now())
        # the first with its traceback, then at 2, 4 and 8 in a row
        assert daemon.log.error.call_count == 4
        assert daemon._tick_errors == 8

    def test_recovery_is_logged_and_resets_the_count(self, tmp_path):
        daemon = self._daemon(tmp_path)
        daemon._tick = MagicMock(side_effect=[ValueError("x"), None])
        daemon._run_tick(datetime.now())
        daemon._run_tick(datetime.now())
        assert daemon._tick_errors == 0 and daemon._last_tick_error is None
        assert "recovered" in daemon.log.info.call_args.args[0]

    def test_the_daemon_logger_has_warning(self, tmp_path):
        from overcode.daemon_logging import BaseDaemonLogger

        logger = BaseDaemonLogger(log_file=tmp_path / "d.log")
        logger.warning("legacy name")  # the three handlers that called it raised
        assert "legacy name" in (tmp_path / "d.log").read_text()


# ── recorder persistence ─────────────────────────────────────────────────


class TestRecorderSaves:
    def _daemon(self, tmp_path):
        from overcode.episodes import EpisodeRecorder
        from overcode.monitor_daemon import MonitorDaemon
        from overcode.status_constants import STATUS_COLOR_GREEN

        daemon = MonitorDaemon.__new__(MonitorDaemon)
        daemon.log = MagicMock()
        daemon.state_path = tmp_path / "monitor_daemon_state.json"
        rec = EpisodeRecorder()
        rec.observe(STATUS_COLOR_GREEN, 100.0)
        daemon._recorders = {"s1": rec}
        daemon._recorders_dirty = False
        daemon._recorders_saved_at = time.monotonic()
        return daemon

    def _saved(self, daemon):
        import json

        try:
            return json.loads(daemon._engine_state_path().read_text())["recorders"]
        except FileNotFoundError:
            return None

    def test_last_seen_is_written_periodically_without_a_change(self, tmp_path):
        from overcode.monitor_daemon import RECORDER_SEEN_SAVE_SECONDS

        daemon = self._daemon(tmp_path)
        daemon._save_recorders({"s1"})
        assert self._saved(daemon) is None  # nothing changed, recently saved
        daemon._recorders_saved_at -= RECORDER_SEEN_SAVE_SECONDS
        daemon._save_recorders({"s1"})
        assert self._saved(daemon)["s1"]["last_seen"] == 100.0

    def test_shutdown_saves_whatever_the_state(self, tmp_path):
        daemon = self._daemon(tmp_path)
        daemon._save_recorders(force=True)
        assert self._saved(daemon)["s1"]["last_seen"] == 100.0

    def test_an_empty_session_index_forgets_nobody(self, tmp_path):
        daemon = self._daemon(tmp_path)
        daemon._recorders_dirty = True
        daemon._save_recorders(set())
        assert "s1" in daemon._recorders and "s1" in self._saved(daemon)
        daemon._recorders_dirty = True
        daemon._save_recorders({"s2"})
        assert daemon._recorders == {}


# ── time totals across a gap ─────────────────────────────────────────────


class TestTimeAcrossGaps:
    def _daemon(self):
        from overcode.monitor_daemon import MonitorDaemon
        from overcode.session_manager import PendingUpdates

        daemon = MonitorDaemon.__new__(MonitorDaemon)
        daemon.log = MagicMock()
        daemon.last_state_times = {}
        daemon._time_bases = {}
        daemon.previous_states = {}
        daemon._pending = PendingUpdates()
        daemon._interval_unattended = 10
        return daemon

    def _session(self, last_accumulation: datetime, green: float = 100.0):
        stats = SimpleNamespace(
            green_time_seconds=green, non_green_time_seconds=50.0, sleep_time_seconds=0.0,
            last_time_accumulation=last_accumulation.isoformat(),
            state_since=last_accumulation.isoformat(),
        )
        start = last_accumulation - timedelta(days=3)
        return SimpleNamespace(id="s1", name="a1", stats=stats, start_time=start.isoformat())

    def test_a_restart_after_downtime_does_not_count_the_downtime(self):
        daemon = self._daemon()
        now = datetime(2026, 10, 9, 23, 49, 14)
        session = self._session(now - timedelta(hours=26))
        daemon._update_state_time(session, "running", now)
        # the views count on from now, not from before the downtime
        assert daemon._time_bases["s1"][1] == now.timestamp()
        daemon._update_state_time(session, "running", now + timedelta(seconds=2))
        staged = daemon._pending.stats["s1"]
        assert staged["green_time_seconds"] == 100.0
        assert daemon._time_bases["s1"] == ([100.0, 50.0, 0.0], (now + timedelta(seconds=2)).timestamp())
        # ...and normal ticks after it count again
        session.stats.last_time_accumulation = staged["last_time_accumulation"]
        daemon._update_state_time(session, "running", now + timedelta(seconds=4))
        assert daemon._pending.stats["s1"]["green_time_seconds"] == pytest.approx(102.0)

    def test_a_quick_restart_still_counts_the_seconds_in_between(self):
        daemon = self._daemon()
        now = datetime(2026, 10, 10, 12, 0, 0)
        session = self._session(now - timedelta(seconds=10))
        daemon._update_state_time(session, "running", now)
        daemon._update_state_time(session, "running", now + timedelta(seconds=2))
        assert daemon._pending.stats["s1"]["green_time_seconds"] == pytest.approx(112.0)

    def test_a_machine_sleep_mid_run_is_not_counted(self):
        daemon = self._daemon()
        now = datetime(2026, 10, 10, 12, 0, 0)
        session = self._session(now - timedelta(seconds=2))
        daemon._update_state_time(session, "waiting_user", now)
        daemon._update_state_time(session, "waiting_user", now + timedelta(hours=3))
        staged = daemon._pending.stats["s1"]
        assert staged["non_green_time_seconds"] == 50.0
        assert staged["last_time_accumulation"] == (now + timedelta(hours=3)).isoformat()

    def test_the_gap_bound_follows_a_long_configured_loop(self):
        from overcode.monitor_daemon_core import UNOBSERVED_GAP_SECONDS

        daemon = self._daemon()
        assert daemon._unobserved_gap_seconds() == UNOBSERVED_GAP_SECONDS
        daemon._interval_unattended = 90
        assert daemon._unobserved_gap_seconds() == 270

    def test_core_counts_an_unobserved_span_as_nothing(self):
        from overcode.monitor_daemon_core import calculate_time_accumulation

        now = datetime(2026, 10, 10, 12, 0, 0)
        result = calculate_time_accumulation(
            current_status="waiting_user", previous_status="running", elapsed_seconds=7200,
            current_green=10.0, current_non_green=5.0, current_sleep=0.0,
            session_start=now - timedelta(days=1), now=now, max_elapsed_seconds=120,
        )
        assert (result.green_seconds, result.non_green_seconds) == (10.0, 5.0)
        assert result.unobserved and result.state_changed


# ── DST ──────────────────────────────────────────────────────────────────


@pytest.fixture
def london(monkeypatch):
    if not hasattr(time, "tzset"):
        pytest.skip("needs time.tzset")
    monkeypatch.setenv("TZ", "Europe/London")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


class TestDaylightSavingEnds:
    """25 Oct 2026: 02:00 BST becomes 01:00 GMT; the naive clock goes back."""

    def test_elapsed_time_runs_forward_through_the_repeated_hour(self, london):
        from overcode.monitor_daemon_core import elapsed_seconds, should_sync_stats

        before = datetime(2026, 10, 25, 1, 59, 59)  # BST, first pass
        epoch_after = before.timestamp() + 2
        after = datetime.fromtimestamp(epoch_after)  # 01:00:01 GMT, as datetime.now() gives it
        assert after.hour == 1 and after.fold == 1
        assert (after - before).total_seconds() < 0  # what stalled every cadence
        assert elapsed_seconds(before, after) == pytest.approx(2.0)
        assert should_sync_stats(before, after, 1.0)

    def test_heartbeats_stay_due_through_the_repeated_hour(self, london):
        from overcode.monitor_daemon_core import is_heartbeat_due

        last = datetime(2026, 10, 25, 1, 55, 0)  # BST
        now = datetime.fromtimestamp(last.timestamp() + 600)  # 01:05 GMT
        assert is_heartbeat_due(last.isoformat(), None, 300, now)

