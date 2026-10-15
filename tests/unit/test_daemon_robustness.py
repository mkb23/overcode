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
from unittest.mock import MagicMock, patch

import pytest

from overcode.implementations import RealTmux, _command_timeout
from tests.tmux_doubles import MockTmux
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
        # `tmux -V` never talks to the server, so a wedged one still answers
        # it (libtmux 0.55+ asks before listing anything)
        'if [ "$1" = "-V" ]; then echo "tmux 3.5a"; exit 0; fi\n'
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
        from overcode import daemon_presence, monitor_daemon
        from overcode.implementations import TMUX_COMMAND_TIMEOUT_SECONDS

        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setattr(daemon_presence, "PresenceLogger", None)
        daemon = monitor_daemon.MonitorDaemon(tmux_session="robust", session_manager=MagicMock())
        assert daemon._tmux._command_timeout == TMUX_COMMAND_TIMEOUT_SECONDS

    def test_a_wedged_server_fails_a_write_instead_of_hanging(self, fake_tmux):
        fake_tmux.hang.touch()
        tmux = RealTmux(socket_name="oc-unit-nope", command_timeout=0.3)
        started = time.monotonic()
        assert tmux.send_keys("agents", "w", "hello") is False
        # the rest fail at once: the server just timed out
        assert tmux.new_window("agents", "w2") is None
        assert tmux.kill_window("agents", "w") is False
        assert tmux.kill_windows("agents", lambda name: True) == []
        assert tmux.select_window("agents", "w") is False
        assert tmux.resize_window("agents", "w", 80, 24) is False
        assert tmux.rename_window("agents", "w", "x") is False
        assert tmux.new_session("agents") is False
        assert tmux.kill_session("agents") is False
        assert time.monotonic() - started < 5

    def test_every_tmux_manager_call_is_bounded(self, fake_tmux):
        from overcode.implementations import TMUX_COMMAND_TIMEOUT_SECONDS
        from overcode.tmux_manager import TmuxManager

        manager = TmuxManager("agents", socket="oc-unit-nope")
        assert manager.client._command_timeout == TMUX_COMMAND_TIMEOUT_SECONDS
        manager.client._command_timeout = 0.3
        fake_tmux.hang.touch()
        started = time.monotonic()
        # "gone" is never the answer to a read the server did not give
        with pytest.raises(TmuxTimeoutError):
            manager.window_exists("w")
        with pytest.raises(TmuxTimeoutError):
            manager.session_exists()
        assert manager.send_keys("w", "hello") is False
        assert manager.kill_window("w") is False
        assert time.monotonic() - started < 5

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
            for i in range(2):
                clock.now += timedelta(seconds=daemon._unobserved_gap_seconds() + 1)
                if i == 0:
                    first_failure = clock.now
                daemon._tick(clock.now)
            unknown = {s.session_id: s for s in daemon.state.sessions}[victim.id]
            assert unknown.current_status == "unknown"
            # unknown since detection first failed: its time in state
            assert unknown.status_since == first_failure.isoformat()
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

        daemon = MonitorDaemon.for_test(tmp_path, log=MagicMock())
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


class TestStateFileFailure:
    """A state file that can't be written (a full disk) must not freeze the views."""

    def _daemon(self, tmp_path):
        from overcode.monitor_daemon import MonitorDaemon
        from overcode.monitor_daemon_state import MonitorDaemonState

        presence = MagicMock(available=False)
        presence.get_current_state.return_value = (1, 0.0, None)
        daemon = MonitorDaemon.for_test(tmp_path, log=MagicMock(), presence=presence)
        daemon.state = MonitorDaemonState()
        daemon._engine = MagicMock()
        return daemon

    def test_the_engine_still_gets_the_update_with_the_error(self, tmp_path):
        daemon = self._daemon(tmp_path)
        with patch.object(type(daemon.state), "save", side_effect=OSError(28, "No space left on device")):
            daemon._publish_state([])
        daemon._engine.publish.assert_called_once()
        assert "No space left" in daemon.state.state_file_error
        snapshot = daemon._engine.publish.call_args.args[0]
        assert "No space left" in snapshot.fleet["state_file_error"]
        assert daemon.log.error.call_count == 1

    def test_the_same_error_is_logged_once_and_recovery_clears_it(self, tmp_path):
        daemon = self._daemon(tmp_path)
        with patch.object(type(daemon.state), "save", side_effect=OSError(28, "full")):
            daemon._publish_state([])
            daemon._publish_state([])
        assert daemon.log.error.call_count == 1
        daemon._publish_state([])  # the disk has room again
        assert daemon.state.state_file_error is None
        assert (tmp_path / "monitor_daemon_state.json").exists()
        assert "saved again" in daemon.log.info.call_args.args[0]


# ── recorder persistence ─────────────────────────────────────────────────


class TestRecorderSaves:
    def _daemon(self, tmp_path):
        from overcode.episodes import EpisodeRecorder
        from overcode.monitor_daemon import MonitorDaemon
        from overcode.status_constants import STATUS_COLOR_GREEN

        daemon = MonitorDaemon.for_test(tmp_path, log=MagicMock())
        rec = EpisodeRecorder()
        rec.observe(STATUS_COLOR_GREEN, 100.0)
        daemon.recorder_store.recorders = {"s1": rec}
        daemon.recorder_store.saved_at = time.monotonic()
        return daemon

    def _saved(self, daemon):
        import json

        try:
            return json.loads(daemon.recorder_store.path.read_text())["recorders"]
        except FileNotFoundError:
            return None

    def test_last_seen_is_written_periodically_without_a_change(self, tmp_path):
        from overcode.engine_recorders import RECORDER_SEEN_SAVE_SECONDS

        daemon = self._daemon(tmp_path)
        daemon.recorder_store.save({"s1"})
        assert self._saved(daemon) is None  # nothing changed, recently saved
        daemon.recorder_store.saved_at -= RECORDER_SEEN_SAVE_SECONDS
        daemon.recorder_store.save({"s1"})
        assert self._saved(daemon)["s1"]["last_seen"] == 100.0

    def test_shutdown_saves_whatever_the_state(self, tmp_path):
        daemon = self._daemon(tmp_path)
        daemon.recorder_store.save(force=True)
        assert self._saved(daemon)["s1"]["last_seen"] == 100.0

    def test_an_empty_session_index_forgets_nobody(self, tmp_path):
        daemon = self._daemon(tmp_path)
        daemon.recorder_store.dirty = True
        daemon.recorder_store.save(set())
        assert "s1" in daemon.recorder_store.recorders and "s1" in self._saved(daemon)
        daemon.recorder_store.dirty = True
        daemon.recorder_store.save({"s2"})
        assert daemon.recorder_store.recorders == {}


# ── time totals across a gap ─────────────────────────────────────────────


class TestTimeAcrossGaps:
    def _daemon(self, tmp_path):
        from overcode.monitor_daemon import MonitorDaemon

        daemon = MonitorDaemon.for_test(tmp_path, log=MagicMock())
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

    def test_a_restart_after_downtime_does_not_count_the_downtime(self, tmp_path):
        daemon = self._daemon(tmp_path)
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

    def test_a_quick_restart_still_counts_the_seconds_in_between(self, tmp_path):
        daemon = self._daemon(tmp_path)
        now = datetime(2026, 10, 10, 12, 0, 0)
        session = self._session(now - timedelta(seconds=10))
        daemon._update_state_time(session, "running", now)
        daemon._update_state_time(session, "running", now + timedelta(seconds=2))
        assert daemon._pending.stats["s1"]["green_time_seconds"] == pytest.approx(112.0)

    def test_a_machine_sleep_mid_run_is_not_counted(self, tmp_path):
        daemon = self._daemon(tmp_path)
        now = datetime(2026, 10, 10, 12, 0, 0)
        session = self._session(now - timedelta(seconds=2))
        daemon._update_state_time(session, "waiting_user", now)
        daemon._update_state_time(session, "waiting_user", now + timedelta(hours=3))
        staged = daemon._pending.stats["s1"]
        assert staged["non_green_time_seconds"] == 50.0
        assert staged["last_time_accumulation"] == (now + timedelta(hours=3)).isoformat()

    def test_the_gap_bound_follows_a_long_configured_loop(self, tmp_path):
        from overcode.monitor_daemon_core import UNOBSERVED_GAP_SECONDS

        daemon = self._daemon(tmp_path)
        assert daemon._unobserved_gap_seconds() == UNOBSERVED_GAP_SECONDS
        daemon._interval_unattended = 90
        assert daemon._unobserved_gap_seconds() == 270

    def test_core_counts_an_unobserved_span_as_nothing(self, tmp_path):
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

    # Persisted stamps are naive local ISO (clock.stamp), which cannot say
    # which pass of the repeated hour they mean: read alone, they take the
    # first. The daemon writes the epoch beside the two whose reading acts.

    def _daemon(self, tmp_path, now):
        from overcode.monitor_daemon import MonitorDaemon
        from overcode.monitor_daemon_state import MonitorDaemonState
        from tests.daemon_tick_harness import FrozenClock

        presence = MagicMock(available=False)
        presence.get_current_state.return_value = (1, 0.0, None)
        daemon = MonitorDaemon.for_test(tmp_path, log=MagicMock(), presence=presence,
                                        clock=FrozenClock(now))
        daemon.state = MonitorDaemonState()
        daemon._engine = MagicMock()
        return daemon

    def test_a_live_engine_in_the_second_pass_is_not_stale(self, london, tmp_path):
        from datetime import timezone

        from overcode.monitor_daemon_state import MonitorDaemonState

        now = datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc).astimezone()  # 01:30 GMT
        daemon = self._daemon(tmp_path, now)
        daemon._publish_state([])
        daemon._flush_pending_writes()
        state = MonitorDaemonState.load(tmp_path / "monitor_daemon_state.json")
        assert state is not None and state.last_loop_time == "2026-10-25T01:30:00"
        assert not state.is_stale(now=now.timestamp() + 5)

    def test_an_oversight_deadline_in_the_second_pass_does_not_fire_early(self, london, tmp_path):
        from datetime import timezone

        from overcode.monitor_daemon_core import should_enforce_oversight_timeout

        now = datetime(2026, 10, 25, 0, 20, tzinfo=timezone.utc).astimezone()  # 01:20 BST
        daemon = self._daemon(tmp_path, now)
        sm = daemon.session_manager
        child = sm.create_session("child", "agents", "child-0001", ["claude"],
                                  start_directory=str(tmp_path))
        sm.update_session(child.id, oversight_policy="timeout", oversight_timeout_seconds=3600.0)
        with patch.object(daemon, "_hook_stopped", return_value=True):
            daemon._stage_oversight_deadline(sm.get_session(child.id))
        daemon._flush_pending_writes()
        child = sm.get_session(child.id)
        assert child.oversight_deadline == "2026-10-25T01:20:00"  # 01:20 GMT, the second pass

        def fires(after):
            return should_enforce_oversight_timeout(
                "waiting_oversight", "timeout", child.oversight_deadline,
                now + timedelta(seconds=after), deadline_at=child.oversight_deadline_at)

        assert not fires(60)
        assert not fires(3599)
        assert fires(3600)

    def test_an_epoch_that_does_not_match_its_stamp_is_ignored(self, london):
        from overcode.clock import stamp_epoch

        first = datetime(2026, 10, 25, 1, 20)  # fold 0: BST
        assert stamp_epoch("2026-10-25T01:20:00", first.timestamp() + 3600) == first.timestamp() + 3600
        # A stamp rewritten by something that left the old epoch behind
        assert stamp_epoch("2026-10-25T01:40:00", first.timestamp() + 3600) == \
            datetime(2026, 10, 25, 1, 40).timestamp()
        assert stamp_epoch("2026-10-25T01:20:00", None) == first.timestamp()
        assert stamp_epoch(None, first.timestamp()) is None

    # Stamps are written naive (clock.stamp), which drops ``fold``: one
    # written in the second (GMT) 01:xx must not read as the first (BST)
    # pass, an hour earlier, against the daemon's aware now.

    @staticmethod
    def _second_pass_now():
        from datetime import timezone
        now = datetime(2026, 10, 25, 1, 10, tzinfo=timezone.utc).astimezone()
        assert now.utcoffset().total_seconds() == 0  # 01:10 GMT, the repeat
        return now

    def test_an_oversight_deadline_set_in_the_repeated_hour_is_not_already_past(self, london):
        from datetime import timedelta
        from overcode.clock import stamp
        from overcode.monitor_daemon_core import should_enforce_oversight_timeout

        now = self._second_pass_now()
        deadline = stamp(now + timedelta(minutes=5))
        assert not should_enforce_oversight_timeout("waiting_oversight", "timeout", deadline, now)
        later = now + timedelta(minutes=6)
        assert should_enforce_oversight_timeout("waiting_oversight", "timeout", deadline, later)

    def test_a_heartbeat_sent_in_the_repeated_hour_is_not_due_again_at_once(self, london):
        from datetime import timedelta
        from overcode.clock import stamp
        from overcode.monitor_daemon_core import is_heartbeat_due

        now = self._second_pass_now()
        sent = stamp(now)
        assert not is_heartbeat_due(sent, None, 1800, now + timedelta(seconds=10))
        assert is_heartbeat_due(sent, None, 1800, now + timedelta(minutes=31))

    def test_an_agent_done_in_the_repeated_hour_is_not_archived_at_once(self, london):
        from datetime import timedelta
        from overcode.clock import stamp
        from overcode.monitor_daemon_core import should_auto_archive

        now = self._second_pass_now()
        assert not should_auto_archive("done", stamp(now), now)
        assert should_auto_archive("done", stamp(now), now + timedelta(hours=1, seconds=1))

    def test_a_stamp_from_the_first_pass_still_reads_as_the_first_pass(self, london):
        from datetime import timedelta, timezone
        from overcode.clock import seconds_since, stamp, to_epoch

        first = datetime(2026, 10, 25, 0, 50, tzinfo=timezone.utc).astimezone()  # 01:50 BST
        assert first.utcoffset().total_seconds() == 3600
        now = first + timedelta(minutes=15)  # 01:05 GMT
        assert seconds_since(stamp(first), now.timestamp()) == pytest.approx(900)
        assert to_epoch(stamp(first), near=now) == pytest.approx(first.timestamp())



class TestStateFileWrittenOnChange:
    """engine-0.6.md: the state file is written when something changed, plus a
    liveness rewrite, not fsync'd on every 2 s tick (review item 24)."""

    def _daemon(self, tmp_path):
        d = TestStateFileFailure()._daemon(tmp_path)
        d._last_state_save = 0.0
        return d

    def test_a_quiet_fleet_is_not_rewritten_every_tick(self, tmp_path):
        from overcode.monitor_daemon_state import SessionDaemonState

        d = self._daemon(tmp_path)
        states = [SessionDaemonState(session_id="s1", name="a", current_status="running")]
        with patch.object(type(d.state), "save") as save:
            d._publish_state(states)
            d._publish_state(states)
            d._publish_state(states)
        assert save.call_count == 1  # the first; nothing changed since
        assert d._engine.publish.call_count == 3  # the views still get every tick

    def test_a_change_is_written_at_once(self, tmp_path):
        from overcode.monitor_daemon_state import SessionDaemonState

        d = self._daemon(tmp_path)
        with patch.object(type(d.state), "save") as save:
            d._publish_state([SessionDaemonState(session_id="s1", name="a", current_status="running")])
            d._publish_state([SessionDaemonState(session_id="s1", name="a", current_status="waiting_user")])
        assert save.call_count == 2

    def test_a_quiet_file_is_still_rewritten_for_liveness(self, tmp_path):
        from overcode import monitor_daemon

        d = self._daemon(tmp_path)
        with patch.object(type(d.state), "save") as save:
            d._publish_state([])
            d._last_state_save -= monitor_daemon.STATE_FILE_LIVENESS_SECONDS + 1
            d._publish_state([])
        assert save.call_count == 2


class TestJobRetention:
    """jobs.json grew without limit: retention was never applied (item 16)."""

    def test_finished_jobs_past_retention_are_pruned_hourly(self, tmp_path):
        from overcode.monitor_daemon import MonitorDaemon

        d = MonitorDaemon.for_test(tmp_path, log=MagicMock())
        with patch("overcode.job_manager.JobManager.cleanup_completed") as cleanup, \
                patch("overcode.config.get_jobs_retention_hours", return_value=12.0):
            d.housekeeping.prune_finished_jobs()
            d.housekeeping.prune_finished_jobs()  # within the hour: not again
        cleanup.assert_called_once_with(12.0)

    def test_a_failing_prune_is_logged_not_raised(self, tmp_path):
        from overcode.monitor_daemon import MonitorDaemon

        d = MonitorDaemon.for_test(tmp_path, log=MagicMock())
        with patch("overcode.job_manager.JobManager.cleanup_completed", side_effect=OSError("ro")):
            d.housekeeping.prune_finished_jobs()
        assert "job retention" in d.log.warn.call_args.args[0]
