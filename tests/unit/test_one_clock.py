"""One clock (quality review item 34): the injected Clock, and mixed timestamps.

The daemon, the detectors and history read the time from one ``Clock``, so
a test freezes and steps every reading together. Timestamps written now are
timezone-aware; files written before hold naive local times, and every
reader must take both, alone or mixed, and compare them correctly.
"""

import csv
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from overcode.clock import (
    SYSTEM_CLOCK,
    Clock,
    aware,
    from_iso,
    local_naive,
    parse_time,
    to_epoch,
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

START = datetime(2026, 10, 7, 12, 0, 0)  # naive: local time, as old files hold
TOKYO = timezone(timedelta(hours=9))


# ── the clock and its helpers ────────────────────────────────────────────


class TestTheClock:
    def test_the_system_clock_is_a_clock_and_its_wall_time_is_aware(self):
        assert isinstance(SYSTEM_CLOCK, Clock)
        wall = SYSTEM_CLOCK.wall()
        assert wall.tzinfo is not None
        assert abs(wall.timestamp() - SYSTEM_CLOCK.time()) < 5

    def test_the_harness_clock_freezes_every_reading_together(self):
        clock = FrozenClock(START)
        assert isinstance(clock, Clock)
        assert clock.wall() == aware(START)
        assert clock.time() == START.timestamp()
        before = clock.monotonic()
        clock.now += timedelta(seconds=7)
        assert clock.monotonic() - before == pytest.approx(7)
        assert clock.time() - START.timestamp() == pytest.approx(7)


class TestHelpers:
    def test_naive_is_local_time(self):
        assert to_epoch(START) == START.timestamp()
        assert to_epoch(START.isoformat()) == START.timestamp()
        assert parse_time(START.isoformat()) == aware(START)

    def test_an_aware_stamp_keeps_its_offset(self):
        tokyo = datetime(2026, 10, 7, 21, 0, 0, tzinfo=TOKYO)  # 12:00 UTC
        utc = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
        assert to_epoch(tokyo.isoformat()) == to_epoch(utc) == utc.timestamp()

    def test_from_iso_gives_naive_local_time_for_both(self):
        local = aware(START)
        assert from_iso(START.isoformat()) == START
        assert from_iso(local.isoformat()) == START
        tokyo = local.astimezone(TOKYO)
        assert from_iso(tokyo.isoformat()) == START  # converted, not just stripped
        assert local_naive(tokyo) == START

    def test_from_iso_raises_like_fromisoformat(self):
        with pytest.raises(ValueError):
            from_iso("not a time")
        with pytest.raises(TypeError):
            from_iso(None)

    def test_unreadable_values_are_none(self):
        assert to_epoch(None) is None
        assert to_epoch("") is None
        assert to_epoch("yesterday") is None
        assert parse_time(42) is not None  # epoch seconds
        assert to_epoch(42) == 42.0


# ── the daemon's rules over old (naive) and new (aware) stamps ───────────


class TestDaemonRulesTakeBoth:
    @pytest.mark.parametrize("now_aware", [False, True])
    @pytest.mark.parametrize("stamp_aware", [False, True])
    def test_oversight_deadline(self, now_aware, stamp_aware):
        from overcode.monitor_daemon_core import should_enforce_oversight_timeout

        deadline = START + timedelta(minutes=5)
        stamp = (aware(deadline) if stamp_aware else deadline).isoformat()

        def at(minutes):
            now = START + timedelta(minutes=minutes)
            return aware(now) if now_aware else now

        assert not should_enforce_oversight_timeout("waiting_oversight", "timeout", stamp, at(4))
        assert should_enforce_oversight_timeout("waiting_oversight", "timeout", stamp, at(6))

    @pytest.mark.parametrize("now_aware", [False, True])
    @pytest.mark.parametrize("stamp_aware", [False, True])
    def test_auto_archive_and_heartbeat(self, now_aware, stamp_aware):
        from overcode.monitor_daemon_core import is_heartbeat_due, should_auto_archive

        since = (aware(START) if stamp_aware else START).isoformat()
        later = START + timedelta(hours=2)
        now = aware(later) if now_aware else later
        assert should_auto_archive("done", since, now, timeout_seconds=3600)
        assert not should_auto_archive("done", since, now, timeout_seconds=3 * 3600)
        assert is_heartbeat_due(since, None, 3600, now)
        assert not is_heartbeat_due(since, None, 3 * 3600, now)

    def test_time_accumulation_caps_against_an_aware_start(self):
        from overcode.monitor_daemon_core import calculate_time_accumulation

        result = calculate_time_accumulation(
            current_status="running", previous_status="running", elapsed_seconds=2.0,
            current_green=1000.0, current_non_green=0.0, current_sleep=0.0,
            session_start=aware(START), now=START + timedelta(seconds=100),
        )
        assert result.was_capped and result.green_seconds == pytest.approx(100.0)

    def test_parsed_stamps_are_aware(self):
        from overcode.monitor_daemon_core import parse_datetime_safe

        assert parse_datetime_safe(START.isoformat()) == aware(START)
        assert parse_datetime_safe(aware(START).isoformat()).tzinfo is not None
        assert parse_datetime_safe(None) is None

    @pytest.mark.parametrize("stamp_aware", [False, True])
    def test_monitor_state_staleness(self, stamp_aware):
        from overcode.monitor_daemon_state import MonitorDaemonState

        stamp = aware(START) if stamp_aware else START
        state = MonitorDaemonState(last_loop_time=stamp.isoformat(), current_interval=2)
        assert not state.is_stale(buffer_seconds=5.0, now=START.timestamp() + 6)
        assert state.is_stale(buffer_seconds=5.0, now=START.timestamp() + 8)


# ── history: one file, both kinds of row ─────────────────────────────────


def _rows(path, stamps):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "agent", "status", "activity", "session_id", "hostname"])
        for i, ts in enumerate(stamps):
            w.writerow([ts.isoformat(), "a", f"s{i}", "", "sid", "h"])


class TestHistoryTakesBoth:
    def _mixed(self, now):
        # An old daemon's naive rows, then the new daemon's aware ones
        old = [now - timedelta(minutes=m) for m in (50, 40, 30)]
        new = [aware(now - timedelta(minutes=m)) for m in (20, 10)]
        return old + new

    def test_a_mixed_file_reads_in_order_as_local_times(self, tmp_path):
        from overcode.status_history import StatusHistoryFile

        now = datetime.now().replace(microsecond=0)
        path = tmp_path / "agent_status_history.csv"
        _rows(path, self._mixed(now))
        rows = StatusHistoryFile(path).read(hours=1.0)
        assert [r[2] for r in rows] == ["s0", "s1", "s2", "s3", "s4"]
        assert all(r[0].tzinfo is None for r in rows)
        assert rows[3][0] == now - timedelta(minutes=20)

    def test_the_window_cuts_and_carries_across_both_kinds(self, tmp_path):
        from overcode.status_history import StatusHistoryFile

        now = datetime.now().replace(microsecond=0)
        path = tmp_path / "agent_status_history.csv"
        _rows(path, self._mixed(now))
        # 26 min: s3 and s4 inside (aware), s2 carried (naive, 4 min before)
        rows = StatusHistoryFile(path).read(hours=26 / 60, carry=True)
        assert [r[2] for r in rows] == ["s2", "s3", "s4"]

    def test_rotation_classifies_both_kinds(self, tmp_path):
        from overcode.status_history import rotate_status_history

        now = datetime(2026, 9, 17, 12, 0, 0)
        path = tmp_path / "agent_status_history.csv"
        stamps = [now - timedelta(hours=40), aware(now - timedelta(hours=35)),
                  now - timedelta(hours=1), aware(now - timedelta(minutes=5))]
        _rows(path, stamps)
        archive = rotate_status_history(path, rotate_mb=0.0, keep_hours=30, now=aware(now))
        assert archive is not None
        kept = list(csv.reader(open(path, newline="")))[1:]
        assert [r[2] for r in kept] == ["s2", "s3"]

    def test_rows_are_written_aware_at_the_given_time(self, tmp_path):
        from overcode.status_history import log_agent_status

        path = tmp_path / "agent_status_history.csv"
        log_agent_status("a", "running", history_file=path, now=START)
        stamp = list(csv.reader(open(path, newline="")))[1][0]
        assert stamp == aware(START).isoformat()

    def test_the_keepalive_rule_takes_both(self):
        from overcode.status_history import status_row_due

        assert not status_row_due(("running", "x"), START, "running", "x",
                                  aware(START + timedelta(seconds=59)), 60)
        assert status_row_due(("running", "x"), aware(START), "running", "x",
                              START + timedelta(seconds=60), 60)


# ── views over aware stamps ──────────────────────────────────────────────


class TestViewsTakeBoth:
    @pytest.mark.parametrize("anchor_aware", [False, True])
    def test_time_in_state_counts_on_from_either_anchor(self, anchor_aware):
        from overcode.tui_helpers import get_current_state_times

        anchor = aware(START) if anchor_aware else START
        stats = SimpleNamespace(
            green_time_seconds=10.0, non_green_time_seconds=0.0, sleep_time_seconds=0.0,
            last_time_accumulation=anchor.isoformat(), state_since=None,
            current_state="running",
        )
        green, _, _ = get_current_state_times(stats, now=START + timedelta(seconds=5))
        assert green == pytest.approx(15.0)

    def test_status_since_renders_as_local_time(self):
        from overcode.tui_engine import status_changed_at

        view = {"current_status": "done", "status_since": aware(START).astimezone(TOKYO).isoformat()}
        assert status_changed_at(view) == START


class TestBurnTakesTheTickTime:
    def test_an_aware_tick_time_gives_the_readers_a_local_naive_since(self, monkeypatch):
        from overcode import burn, stats_reader

        seen = []

        class Reader:
            def get_window_token_usage(self, session, since):
                seen.append(since)
                return {"input_tokens": 0, "output_tokens": 0,
                        "cache_creation_tokens": 0, "cache_read_tokens": 0}

        monkeypatch.setattr(stats_reader, "stats_reader_for_session", lambda s: Reader())
        burn.compute_window_burn([SimpleNamespace(id="s")], set(), 1.0,
                                 now=aware(START).astimezone(TOKYO))
        assert seen == [START - timedelta(hours=1)]


# ── one clock through the daemon ─────────────────────────────────────────


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state" / "sessions"))
    (tmp_path / "state" / "sessions").mkdir(parents=True)
    return tmp_path


def _fleet(env, n=2, script=None):
    from overcode.session_manager import SessionManager

    sm = SessionManager(state_dir=env / "state" / "sessions", skip_git_detection=True)
    sessions = seed_sessions(sm, n, "agents", env / "work", START)
    detector = ScriptedDetector(script or (lambda tick, s: ("waiting_user", "Waiting", "")))
    daemon = make_daemon(env / "state", "agents", detector, session_manager=sm)
    seed_steady_state(daemon, sessions, START)
    return daemon, detector, sessions, FrozenClock(START)


class TestOneClockThroughTheDaemon:
    def test_every_component_reads_the_daemon_clock(self, tmp_path):
        from overcode.monitor_daemon import MonitorDaemon

        clock = FrozenClock(START)
        daemon = MonitorDaemon.for_test(tmp_path / "state", clock=clock)
        assert daemon.clock is clock
        for component in (daemon.housekeeping, daemon.stats_sync, daemon.recorder_store,
                          daemon.wake.scan, daemon.detector.hooks):
            assert component.clock is clock
        assert daemon.state.started_at == aware(START).isoformat()

    def test_the_hook_detector_reads_its_clock(self, tmp_path):
        from overcode.hook_status_detector import HookStatusDetector

        from overcode.hook_status_detector import _STATUS_BAR_HOLD_SECONDS

        clock = FrozenClock(START)
        detector = HookStatusDetector("agents", state_dir=tmp_path, clock=clock)
        # Status-bar counts seen a second ago are held while the bar is
        # covered, for _STATUS_BAR_HOLD_SECONDS of the detector's clock
        detector._status_bar_counts["a"] = (START.timestamp() - 1, 2, 1)
        assert detector._hold_status_bar_counts("a", "", 0, 0) == (2, 1)
        clock.now += timedelta(seconds=_STATUS_BAR_HOLD_SECONDS)
        assert detector._hold_status_bar_counts("a", "", 0, 0) == (0, 0)

    def test_the_state_file_liveness_rewrite_follows_the_frozen_clock(self, env):
        """The flake: monotonic time ran on while ``datetime`` was frozen."""
        from overcode.monitor_daemon import STATE_FILE_LIVENESS_SECONDS

        daemon, detector, _, clock = _fleet(env)
        with clock.installed():
            run_ticks(daemon, detector, clock, 3)
            saved = daemon._saved_snapshot
            # The last write is stamped on the frozen clock (a tick's time)
            last = daemon._last_state_save
            assert (last - START.timestamp()) in (0.0, 2.0, 4.0)
            # An unchanged snapshot is rewritten only for liveness, on the
            # frozen clock however long the test really takes
            clock.now = START + timedelta(seconds=last - START.timestamp()
                                          + STATE_FILE_LIVENESS_SECONDS - 1)
            assert not daemon._state_file_due(saved)
            clock.now += timedelta(seconds=1)
            assert daemon._state_file_due(saved)

    def test_old_naive_stamps_keep_accumulating_and_new_ones_are_aware(self, env):
        daemon, detector, sessions, clock = _fleet(
            env, n=1, script=lambda tick, s: ("running", "Working", ""))
        before = sessions[0].stats.green_time_seconds  # seeded naive stamps
        with clock.installed():
            run_ticks(daemon, detector, clock, 3)  # t = 0, 2, 4
        stats = daemon.session_manager.get_session(sessions[0].id).stats
        assert stats.green_time_seconds == pytest.approx(before + 6)
        assert parse_time(stats.last_time_accumulation).tzinfo is not None
        assert to_epoch(stats.last_time_accumulation) == (START + timedelta(seconds=4)).timestamp()
        assert to_epoch(daemon.state.last_loop_time) == (START + timedelta(seconds=4)).timestamp()
