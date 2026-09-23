"""The monitor daemon's unattended low-power mode.

When nobody is watching — no client attached to the agents tmux session,
no fresh TUI keypress heartbeat, no TUI touching its attended file — the
loop stretches from ``interval_fast`` to ``DAEMON.interval_unattended``
(10 s) and says so in the published state. Everything that used to count
loops is wall-clock, so heartbeats, oversight timeouts and the every-two-
minutes housekeeping keep their cadence at the coarser tick, and the loop
is back on the fast interval within one tick of a client attaching, a TUI
touch, or the activity signal.
"""

import contextlib
import csv
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from overcode import config
from overcode.monitor_daemon import (
    HOUSEKEEPING_INTERVAL_SECONDS,
    INTERVAL_FAST,
    INTERVAL_UNATTENDED,
    TUI_ATTENDED_FRESHNESS,
    MonitorDaemon,
)
from overcode.monitor_daemon_state import MonitorDaemonState
from overcode.settings import DAEMON, touch_tui_attended, write_tui_heartbeat

START = datetime(2026, 9, 23, 12, 0, 0)


@pytest.fixture
def root(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".overcode" / "sessions").mkdir(parents=True)
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(home / ".overcode" / "sessions"))
    config._clear_config_cache()
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.yaml")
    yield tmp_path
    config._clear_config_cache()


def _daemon(root, tmux_session="agents"):
    """A MonitorDaemon whose presence logger is not started and whose tmux is a double."""
    from overcode import monitor_daemon

    original = monitor_daemon.PresenceLogger
    monitor_daemon.PresenceLogger = None
    try:
        with (
            patch("overcode.monitor_daemon.SessionManager"),
            patch("overcode.monitor_daemon.StatusDetector"),
        ):
            daemon = MonitorDaemon(tmux_session=tmux_session, tmux=MagicMock())
    finally:
        monitor_daemon.PresenceLogger = original
    daemon._relay_config = None
    return daemon


class TestSetting:
    def test_default_is_ten_seconds(self):
        assert DAEMON.interval_unattended == 10
        assert INTERVAL_UNATTENDED == 10
        assert INTERVAL_UNATTENDED > INTERVAL_FAST

    def test_attended_freshness_is_three_touches(self):
        from overcode.settings import TUI_ATTENDED_TOUCH_SECONDS

        assert TUI_ATTENDED_FRESHNESS == 3 * TUI_ATTENDED_TOUCH_SECONDS == 15

    def test_housekeeping_interval_is_what_sixty_fast_loops_were(self):
        assert HOUSEKEEPING_INTERVAL_SECONDS == 60 * INTERVAL_FAST == 120


class TestConfigOverride:
    @pytest.fixture(autouse=True)
    def _fresh(self):
        config._clear_config_cache()
        yield
        config._clear_config_cache()

    def test_default_is_the_daemon_setting(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nonexistent.yaml")
        assert config.get_monitor_daemon_config() == {"interval_unattended": 10}

    def test_override(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("monitor_daemon:\n  interval_unattended_seconds: 30\n")
        monkeypatch.setattr(config, "CONFIG_PATH", cfg)
        assert config.get_monitor_daemon_config()["interval_unattended"] == 30

    def test_invalid_or_faster_than_fast_falls_back(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config.yaml"
        monkeypatch.setattr(config, "CONFIG_PATH", cfg)
        for text in (
            "monitor_daemon:\n  interval_unattended_seconds: soon\n",
            "monitor_daemon:\n  interval_unattended_seconds: 1\n",
            "monitor_daemon: 12\n",
        ):
            config._clear_config_cache()
            cfg.write_text(text)
            assert config.get_monitor_daemon_config()["interval_unattended"] == 10, text

    def test_daemon_reads_it_at_construction(self, root):
        (root / "config.yaml").write_text("monitor_daemon:\n  interval_unattended_seconds: 20\n")
        daemon = _daemon(root)
        assert daemon._interval_unattended == 20
        assert daemon.calculate_interval([], True, unattended=True) == 20


class TestAttendance:
    """Unattended only when all three signals say nobody is watching."""

    def test_attached_client_is_attended(self, root):
        daemon = _daemon(root)
        daemon.session_attached = 1
        assert daemon.attendance() == "attended"

    def test_unknown_attached_count_is_attended(self, root):
        """A failed listing (tmux down) never slows the loop."""
        daemon = _daemon(root)
        daemon.session_attached = None
        assert daemon.attendance() == "attended"

    def test_nobody_is_unattended(self, root):
        daemon = _daemon(root)
        daemon.session_attached = 0
        assert daemon.attendance() == "unattended"

    def test_fresh_keypress_heartbeat_is_attended(self, root):
        daemon = _daemon(root)
        daemon.session_attached = 0
        write_tui_heartbeat("agents")
        assert daemon.attendance() == "attended"

    def test_stale_keypress_heartbeat_is_not(self, root):
        from overcode.settings import get_tui_heartbeat_path

        daemon = _daemon(root)
        daemon.session_attached = 0
        path = get_tui_heartbeat_path("agents")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((datetime.now() - timedelta(seconds=120)).isoformat())
        assert daemon.attendance() == "unattended"

    def test_fresh_tui_touch_is_attended(self, root):
        """A TUI in another tmux session, or a plain terminal: only the touch sees it."""
        daemon = _daemon(root)
        daemon.session_attached = 0
        touch_tui_attended("agents")
        assert daemon.attendance() == "attended"

    def test_touch_older_than_three_periods_is_not(self, root):
        from overcode.settings import get_tui_attended_path

        daemon = _daemon(root)
        daemon.session_attached = 0
        touch_tui_attended("agents")
        path = get_tui_attended_path("agents")
        st = path.stat()
        old = st.st_mtime - (TUI_ATTENDED_FRESHNESS + 1)
        os.utime(path, (old, old))
        assert daemon.attendance() == "unattended"
        os.utime(path, (st.st_mtime - (TUI_ATTENDED_FRESHNESS - 1),) * 2)
        assert daemon.attendance() == "attended"


class TestCalculateInterval:
    def test_fast_unless_unattended(self, root):
        daemon = _daemon(root)
        assert daemon.calculate_interval([], True) == INTERVAL_FAST
        assert daemon.calculate_interval([MagicMock()], False) == INTERVAL_FAST
        assert daemon.calculate_interval([], True, unattended=False) == INTERVAL_FAST
        assert (
            daemon.calculate_interval([MagicMock()], False, unattended=True) == INTERVAL_UNATTENDED
        )


class TestPublishedMode:
    def _publish(self, daemon, now=START):
        daemon.state.save = MagicMock()
        daemon._enforce_oversight_timeouts = MagicMock()
        daemon._auto_archive_done_agents = MagicMock()
        daemon._count_untracked_windows = MagicMock(return_value=0)
        daemon._archive_terminated_sessions = MagicMock()
        with patch.object(daemon, "_publish_state") as publish:
            daemon._publish_and_enforce([], [], True, None, now)
        return publish

    def test_attended_publishes_fast_and_says_so(self, root):
        daemon = _daemon(root)
        daemon.session_attached = 2
        self._publish(daemon)
        assert (daemon.state.current_interval, daemon.state.interval_mode) == (
            INTERVAL_FAST,
            "attended",
        )

    def test_unattended_publishes_the_long_interval_before_sleeping_on_it(self, root):
        """The mode and interval are set before _publish_state, so the file a
        consumer reads describes the sleep that follows."""
        daemon = _daemon(root)
        daemon.session_attached = 0
        seen = {}
        daemon._publish_state = lambda states: seen.update(
            interval=daemon.state.current_interval, mode=daemon.state.interval_mode
        )
        daemon._enforce_oversight_timeouts = MagicMock()
        daemon._publish_and_enforce([], [], True, None, START)
        assert seen == {"interval": INTERVAL_UNATTENDED, "mode": "unattended"}

    def test_back_to_fast_within_one_loop_of_a_client(self, root):
        daemon = _daemon(root)
        daemon.session_attached = 0
        self._publish(daemon, START)
        assert daemon.state.interval_mode == "unattended"
        daemon.session_attached = 1  # the next tick's listing sees the client
        self._publish(daemon, START + timedelta(seconds=10))
        assert (daemon.state.current_interval, daemon.state.interval_mode) == (
            INTERVAL_FAST,
            "attended",
        )

    def test_activity_signal_ends_the_sleep_on_the_fast_interval(self, root):
        daemon = _daemon(root)
        daemon.state.current_interval = INTERVAL_UNATTENDED
        daemon.state.interval_mode = "unattended"
        daemon.state.save = MagicMock()
        slept = []
        with (
            patch("overcode.monitor_daemon.time.sleep", slept.append),
            patch("overcode.monitor_daemon.check_activity_signal", return_value=True),
        ):
            daemon._interruptible_sleep(INTERVAL_UNATTENDED)
        assert slept == [1]  # one chunk, then awake
        assert (daemon.state.current_interval, daemon.state.interval_mode) == (
            INTERVAL_FAST,
            "attended",
        )
        daemon.state.save.assert_called_once()

    def test_state_round_trips_the_mode(self, tmp_path):
        state = MonitorDaemonState(pid=1, interval_mode="unattended", current_interval=10)
        path = tmp_path / "state.json"
        state.save(path)
        loaded = MonitorDaemonState.load(path)
        assert loaded.interval_mode == "unattended" and loaded.current_interval == 10
        # An older daemon's file has no field: attended
        assert MonitorDaemonState.from_dict({"pid": 1}).interval_mode == "attended"

    def test_unattended_state_is_fresh_for_its_own_interval(self):
        """is_stale sizes its window on the published interval, so a 10 s loop is
        not a dead daemon to the TUI's 5 s-buffer fast path."""
        state = MonitorDaemonState(
            pid=1,
            interval_mode="unattended",
            current_interval=INTERVAL_UNATTENDED,
            last_loop_time=(datetime.now() - timedelta(seconds=12)).isoformat(),
        )
        assert state.is_stale(buffer_seconds=5.0) is False


class TestHousekeepingIsWallClock:
    def _daemon(self, root):
        daemon = _daemon(root)
        daemon.state.save = MagicMock()
        daemon._publish_state = MagicMock()
        daemon._enforce_oversight_timeouts = MagicMock()
        daemon._auto_archive_done_agents = MagicMock()
        daemon._count_untracked_windows = MagicMock(return_value=3)
        daemon._archive_terminated_sessions = MagicMock()
        daemon.session_attached = 1
        return daemon

    def _passes(self, daemon):
        return daemon._auto_archive_done_agents.call_count

    def test_first_pass_two_minutes_in_then_every_two_minutes_at_the_fast_loop(self, root):
        daemon = self._daemon(root)
        when = []
        for k in range(0, 250, INTERVAL_FAST):  # 2 s loops for 250 s
            now = START + timedelta(seconds=k)
            daemon._publish_and_enforce([], [], True, None, now)
            if self._passes(daemon) > len(when):
                when.append(k)
        assert when == [120, 240]
        assert daemon.state.untracked_window_count == 3
        assert daemon._archive_terminated_sessions.call_args.args[1] == START + timedelta(
            seconds=240
        )

    def test_same_cadence_at_the_unattended_loop(self, root):
        """Loop counts would have stretched it to 10 minutes; wall clock keeps 2."""
        daemon = self._daemon(root)
        daemon.session_attached = 0
        when = []
        for k in range(0, 250, INTERVAL_UNATTENDED):  # 10 s loops
            now = START + timedelta(seconds=k)
            daemon._publish_and_enforce([], [], True, None, now)
            if self._passes(daemon) > len(when):
                when.append(k)
        assert when == [120, 240]

    def test_loop_count_no_longer_triggers_it(self, root):
        daemon = self._daemon(root)
        daemon.state.loop_count = 60
        daemon._publish_and_enforce([], [], True, None, START)
        daemon.state.loop_count = 120
        daemon._publish_and_enforce([], [], True, None, START + timedelta(seconds=2))
        assert self._passes(daemon) == 0

    def test_a_test_can_force_a_pass_by_moving_the_stamp_back(self, root):
        daemon = self._daemon(root)
        daemon._last_housekeeping = START - timedelta(seconds=HOUSEKEEPING_INTERVAL_SECONDS)
        daemon._publish_and_enforce([], [], True, None, START)
        assert self._passes(daemon) == 1


class TestWallClockArithmetic:
    """The per-tick enforcement is wall-clock, so a 10 s tick changes nothing."""

    def test_heartbeat_due_uses_the_clock_not_the_loop(self):
        from overcode.monitor_daemon_core import is_heartbeat_due

        last = START.isoformat()
        assert not is_heartbeat_due(last, START.isoformat(), 300, START + timedelta(seconds=299))
        assert is_heartbeat_due(last, START.isoformat(), 300, START + timedelta(seconds=300))

    def test_oversight_timeout_uses_the_deadline(self):
        from overcode.monitor_daemon_core import should_enforce_oversight_timeout

        deadline = (START + timedelta(seconds=30)).isoformat()
        assert not should_enforce_oversight_timeout(
            "waiting_oversight", "timeout", deadline, START + timedelta(seconds=29)
        )
        assert should_enforce_oversight_timeout(
            "waiting_oversight", "timeout", deadline, START + timedelta(seconds=31)
        )

    def test_no_loop_count_cadence_remains_in_the_daemon(self):
        import inspect

        from overcode import monitor_daemon

        src = inspect.getsource(monitor_daemon)
        assert "if self.state.loop_count %" not in src


class TestStatusBarShowsTheMode:
    def _bar(self, state):
        from tests.unit.test_daemon_status_bar import _make_bare_status_bar, _make_monitor_state

        return _make_bare_status_bar(monitor_state=_make_monitor_state(**state))

    def test_unattended_is_shown_next_to_the_interval(self):
        plain = self._bar(dict(current_interval=10, interval_mode="unattended")).render().plain
        assert "@10s (unattended)" in plain

    def test_attended_shows_nothing_extra(self):
        plain = self._bar(dict(current_interval=2, interval_mode="attended")).render().plain
        assert "unattended" not in plain

    def test_state_from_an_older_daemon_shows_nothing_extra(self):
        from tests.unit.test_daemon_status_bar import _make_bare_status_bar, _make_monitor_state

        state = _make_monitor_state(current_interval=2)
        del state.interval_mode
        assert "unattended" not in _make_bare_status_bar(monitor_state=state).render().plain


class TestUnattendedTimelineHasNoHoles:
    """detach -> 10 unattended loops -> attach: the timeline the user returns to is gap-free.

    The maintainer's reason the daemon stays alive while unattended. Whole
    ticks through the daemon tick harness over three sessions, the clock
    frozen for the daemon *and* the history writer/reader so rows carry the
    scripted times: 15 attended ticks at 2 s, then the tmux client goes
    away and the next ten loops run at the unattended interval, then a
    client is back. Status changes land in every phase. Read the history
    the way the timeline widget does (carry=True) and bucket it into 10 s
    slots: every slot of every agent has a state, and the states are the
    scripted truth.
    """

    N = 3

    @staticmethod
    @contextlib.contextmanager
    def _frozen_history_clock(clock):
        from overcode import status_history

        class _Frozen(datetime):
            @classmethod
            def now(cls, tz=None):  # noqa: D401 - datetime API
                return clock.now

        original = status_history.datetime
        status_history.datetime = _Frozen
        try:
            yield
        finally:
            status_history.datetime = original

    @staticmethod
    def _truth(t: float, name: str) -> str:
        """Scripted status by seconds since START."""
        if name == "agent-01":
            return "waiting_user" if 10 <= t < 60 else "running"
        if name == "agent-02":
            return "waiting_user" if 100 <= t < 140 else "running"
        return "running"

    def test_walk(self, root):
        from overcode.session_manager import SessionManager
        from overcode.status_history import read_agent_status_history
        from overcode.tui_helpers import build_timeline_slots
        from tests.daemon_tick_harness import (
            FakeTmux,
            FrozenClock,
            ScriptedDetector,
            make_daemon,
            run_ticks,
            seed_sessions,
            seed_steady_state,
        )

        state_dir = root / "home" / ".overcode"
        sm = SessionManager(state_dir=state_dir / "sessions", skip_git_detection=True)
        sessions = seed_sessions(sm, self.N, "agents", root / "work", START)
        tmux = FakeTmux("agents", {s.tmux_window: "pane" for s in sessions})
        tmux.attached = 1
        clock = FrozenClock(START)

        def script(tick, session):
            t = (clock.now - START).total_seconds()
            status = self._truth(t, session.name)
            return status, ("Working" if status == "running" else "Waiting"), "pane"

        detector = ScriptedDetector(script)
        daemon = make_daemon(state_dir, "agents", detector, session_manager=sm, tmux=tmux)
        seed_steady_state(daemon, sessions, START)
        sync_stamps = (
            "_last_stats_sync",
            "_last_session_id_sync",
            "_last_skills_sync",
            "_last_sandbox_sync",
            "_last_resources_sync",
            "_last_history_rotation_check",
            "_last_model_metadata_check",
        )
        intervals = []  # (seconds since START, current_interval, interval_mode) per tick

        def after_tick(_i):
            for name in sync_stamps:
                setattr(daemon, name, clock.now)
            intervals.append(
                (
                    (
                        daemon.state.tick_started_at
                        and (
                            datetime.fromisoformat(daemon.state.tick_started_at) - START
                        ).total_seconds()
                    ),
                    daemon.state.current_interval,
                    daemon.state.interval_mode,
                )
            )

        with clock.installed(), self._frozen_history_clock(clock):
            # Attended: 15 ticks at 2 s (t = 0 .. 28)
            run_ticks(daemon, detector, clock, 15, step_seconds=2, between_ticks=after_tick)
            assert intervals[-1][1:] == (INTERVAL_FAST, "attended")
            rows_attended = len(_history_rows(daemon.history_path))
            # The client detaches: the daemon sees it on its next tick and
            # sleeps the unattended interval from then on (t = 30 .. 120)
            tmux.attached = 0
            run_ticks(daemon, detector, clock, 10, step_seconds=10, between_ticks=after_tick)
            rows_unattended = len(_history_rows(daemon.history_path)) - rows_attended
            # A client is back: the very next tick is on the fast interval (t = 130 ..)
            tmux.attached = 1
            run_ticks(daemon, detector, clock, 10, step_seconds=2, between_ticks=after_tick)
            end = clock.now  # START + 150 s

            # The interval trajectory: fast, ten unattended loops, fast within one loop
            unattended = [t for t, iv, mode in intervals if mode == "unattended"]
            assert unattended == [30 + 10 * k for k in range(10)]
            assert all(iv == INTERVAL_UNATTENDED for t, iv, _ in intervals if t in unattended)
            assert intervals[15 + 10][1:] == (INTERVAL_FAST, "attended")  # t = 130
            # Change-only logging: the ten unattended loops wrote the two
            # changes plus keepalives, not ten rows per agent
            assert 2 <= rows_unattended < 10 * self.N

            # What the timeline widget reads and buckets
            span = (end - START).total_seconds()
            history = read_agent_status_history(
                hours=span / 3600, history_file=daemon.history_path, carry=True
            )
        by_agent = {}
        for ts, agent, status, *_ in history:
            by_agent.setdefault(agent, []).append((ts, status))
        assert set(by_agent) == {s.name for s in sessions}
        width = int(span / 10)  # 10 s slots
        for name, agent_history in by_agent.items():
            slots = build_timeline_slots(agent_history, width, span / 3600, now=end)
            assert sorted(slots) == list(range(width)), (
                name,
                "holes at",
                sorted(set(range(width)) - set(slots)),
            )
            for i in range(width):
                assert slots[i] == self._truth(i * 10, name), (name, i, slots[i])


def _history_rows(path):
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.reader(f))[1:]
