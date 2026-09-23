"""The TUI's attended state: timers pause while no client is attached, resume on return.

Nothing in the TUI was gated on anyone watching (scaling audit,
cross-cutting finding): every timer ran at full rate with the tmux client
detached. Now one ``attended`` flag on the app, written only through
``_set_attended`` and reacted to only in ``_on_attended_changed``, pauses
every timer in PAUSED_WHEN_UNATTENDED and resumes the one 2 s read of the
daemon's published state that keeps stall bells and notifications going.
These tests drive the real app methods on a bare instance with fake
timers and a fake attached/detached signal, and count what the workers
were asked to do.
"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.tui import (  # noqa: E402
    PAUSED_WHEN_UNATTENDED,
    TIMER_INTERVALS,
    TIMER_PHASE_OFFSETS,
    SupervisorTUI,
)


class FakeTimer:
    """A Textual Timer stand-in: fires its callback on schedule unless paused."""

    def __init__(self, interval, callback, pause=False):
        self.interval = interval
        self.callback = callback
        self.paused = pause
        self.fires = 0
        self.transitions = []  # ("pause" | "resume") in order

    def pause(self):
        self.paused = True
        self.transitions.append("pause")

    def resume(self):
        self.paused = False
        self.transitions.append("resume")


class Clock:
    """Simulated time: fake timers fire at their cadence, delayed starts happen."""

    def __init__(self, app):
        self.app = app
        self.now = 0.0
        self.pending = []  # (due, callback) from set_timer

    def set_timer(self, delay, callback):
        self.pending.append((self.now + delay, callback))
        return MagicMock()

    def set_interval(self, interval, callback, pause=False):
        return FakeTimer(interval, callback, pause=pause)

    def advance(self, seconds, step=0.05):
        """Fire everything due, in order, until ``seconds`` have passed."""
        end = self.now + seconds
        while self.now < end - 1e-9:
            self.now = round(self.now + step, 6)
            for due, cb in sorted(self.pending):
                if due <= self.now + 1e-9:
                    self.pending.remove((due, cb))
                    cb()
            for name, timer in list(self.app._periodic_timers.items()):
                if timer.paused:
                    continue
                # fire when a multiple of the interval has elapsed since 0
                n = int(round(self.now / timer.interval, 6))
                if n > timer.fires and abs(n * timer.interval - self.now) < 1e-6:
                    timer.fires = n
                    timer.callback()


def _bare_app(attended=True, in_tmux=True):
    app = SupervisorTUI.__new__(SupervisorTUI)
    app.tmux_session = "agents"
    app.attended = attended
    app._periodic_timers = {}
    app._tui_tmux_pane = "%3" if in_tmux else None
    app._tui_tmux_socket = "/tmp/tmux-1/default" if in_tmux else None
    app._listing_is_own_server = in_tmux
    app._tui_tmux_session = None
    app._attached_reading = None
    app._last_attended_touch = 0.0
    app._heartbeat_last = 0.0
    app._heartbeat_enabled = False
    app.has_sisters = False
    app.diagnostics = False
    app._prefs = MagicMock(status_change_logging=False)
    app._prefs.status_change_logging = False
    # Every worker the timers reach, as counters
    app.calls = {}

    def counter(name):
        def _call(*a, **kw):
            app.calls[name] = app.calls.get(name, 0) + 1

        return _call

    for name in (
        "refresh_sessions",
        "update_focused_status",
        "_update_stats_async",
        "update_daemon_status",
        "update_timeline",
        "_update_summaries_async",
        "_poll_sisters",
        "_poll_focused_sister",
        "_refresh_jobs",
        "_poll_focused_job_pane",
        "_periodic_agent_resize",
        "_record_heartbeat",
        "_flush_heartbeat",
        "_flush_status_changes",
        "update_all_statuses",
        "_poll_attended_async",
        "_fetch_unattended_status_async",
    ):
        setattr(app, name, counter(name))
    clock = Clock(app)
    app.set_timer = clock.set_timer
    app.set_interval = clock.set_interval
    return app, clock


def _start_all_timers(app):
    """What on_mount does in normal mode (probe on, no sisters)."""
    app._start_periodic("heartbeat_probe", app._record_heartbeat)
    app._start_periodic("heartbeat_flush", app._flush_heartbeat)
    app._start_periodic("status_changes", app._flush_status_changes)
    app._start_periodic("refresh_sessions", app.refresh_sessions)
    app._start_periodic("fast_status", app.update_focused_status)
    app._start_periodic("slow_stats", app._update_stats_async)
    app._start_periodic("daemon_status", app.update_daemon_status)
    app._start_periodic("timeline", app.update_timeline)
    app._start_periodic("summarizer", app._update_summaries_async)
    app._start_periodic("refresh_jobs", app._refresh_jobs)
    app._start_periodic("focused_job_pane", app._poll_focused_job_pane)
    app._start_periodic("agent_resize", app._periodic_agent_resize)
    app._start_periodic("attended_watch", app._attended_watch_tick)
    app._start_periodic("unattended_status", app._unattended_status_tick)


ATTENDED_ONLY = {
    "refresh_sessions",
    "update_focused_status",
    "_update_stats_async",
    "update_daemon_status",
    "update_timeline",
    "_update_summaries_async",
    "_refresh_jobs",
    "_poll_focused_job_pane",
    "_periodic_agent_resize",
    "_record_heartbeat",
}


class TestPausedSet:
    def test_every_paused_timer_exists_and_the_watch_never_pauses(self):
        assert PAUSED_WHEN_UNATTENDED <= set(TIMER_INTERVALS)
        for name in ("attended_watch", "unattended_status", "heartbeat_flush", "status_changes"):
            assert name not in PAUSED_WHEN_UNATTENDED
        # Every capture / render / stats path is in the paused set
        for name in (
            "fast_status",
            "slow_stats",
            "timeline",
            "summarizer",
            "sister_poll",
            "focused_sister",
            "refresh_jobs",
            "agent_resize",
            "daemon_status",
            "refresh_sessions",
            "focused_job_pane",
            "heartbeat_probe",
        ):
            assert name in PAUSED_WHEN_UNATTENDED


class TestWatcher:
    def test_detach_pauses_the_set_and_resumes_the_unattended_read(self):
        app, clock = _bare_app()
        _start_all_timers(app)
        clock.advance(5)  # every delayed start has happened
        with patch("overcode.tui.touch_tui_attended"), patch("overcode.tui.signal_activity"):
            app._set_attended(False)
        paused = {name for name, t in app._periodic_timers.items() if t.paused}
        assert paused == PAUSED_WHEN_UNATTENDED & set(app._periodic_timers)
        assert len(paused) == 10  # every started timer in the set (no sisters here)
        assert not app._periodic_timers["unattended_status"].paused
        assert not app._periodic_timers["attended_watch"].paused
        assert not app._periodic_timers["heartbeat_flush"].paused
        assert not app._periodic_timers["status_changes"].paused

    def test_reattach_resumes_everything_signals_the_daemon_and_refreshes(self, tmp_path):
        app, clock = _bare_app(attended=False)
        _start_all_timers(app)
        clock.advance(5)
        assert all(t.paused for n, t in app._periodic_timers.items() if n in PAUSED_WHEN_UNATTENDED)
        app.calls = {}  # what the 5 s of detached ticks asked for is not the refresh
        touched, signalled = [], []
        with (
            patch("overcode.tui.touch_tui_attended", touched.append),
            patch("overcode.tui.signal_activity", signalled.append),
        ):
            app._set_attended(True)
        assert not any(
            t.paused for n, t in app._periodic_timers.items() if n in PAUSED_WHEN_UNATTENDED
        )
        assert app._periodic_timers["unattended_status"].paused
        assert touched == ["agents"] and signalled == ["agents"]
        # One full refresh: sessions, daemon bar, timeline, statuses (fast + slow), jobs
        assert app.calls == {
            "refresh_sessions": 1,
            "update_daemon_status": 1,
            "update_timeline": 1,
            "update_all_statuses": 1,
            "_refresh_jobs": 1,
        }
        assert app._heartbeat_last > 0  # the probe restarts from now, not from the pause

    def test_setting_the_same_state_is_a_no_op(self):
        app, clock = _bare_app()
        _start_all_timers(app)
        clock.advance(5)
        app._set_attended(True)
        assert app.calls == {} or "update_all_statuses" not in app.calls
        assert all(t.transitions == [] for t in app._periodic_timers.values())

    def test_sisters_are_polled_on_return_when_configured(self):
        app, clock = _bare_app(attended=False)
        app.has_sisters = True
        with patch("overcode.tui.touch_tui_attended"), patch("overcode.tui.signal_activity"):
            app._set_attended(True)
        assert app.calls["_poll_sisters"] == 1

    def test_a_detach_during_the_unattended_reads_delayed_start_still_starts_it(self):
        """The first poll (0.45 s) can answer 0 clients before the 2 s read's
        delayed start (0.8 s) lands — a TUI launched under ``tmux new -d``.
        The read must start running, not paused: bells depend on it."""
        app, clock = _bare_app()
        _start_all_timers(app)
        with patch("overcode.tui.touch_tui_attended"), patch("overcode.tui.signal_activity"):
            clock.advance(0.5)  # the watch has started; the read has not
            assert "unattended_status" not in app._periodic_timers
            app._set_attended(False)
            before = dict(app.calls)
            clock.advance(60)
        assert not app._periodic_timers["unattended_status"].paused
        assert app.calls["_fetch_unattended_status_async"] == pytest.approx(30, abs=2)
        for name in ATTENDED_ONLY:
            assert app.calls.get(name, 0) == before.get(name, 0), name


class TestWorkerInvocationsAcrossDetachAttach:
    """Simulated minutes with the fake signal: paused workers are never invoked."""

    def _run(self, app, clock, seconds):
        before = dict(app.calls)
        clock.advance(seconds)
        return {k: app.calls.get(k, 0) - before.get(k, 0) for k in app.calls}

    def test_nothing_but_the_watch_and_flushes_run_while_detached(self):
        app, clock = _bare_app()
        _start_all_timers(app)
        with patch("overcode.tui.touch_tui_attended"), patch("overcode.tui.signal_activity"):
            attended = self._run(app, clock, 30)
            assert attended["update_focused_status"] == pytest.approx(120, abs=2)
            assert attended["_update_stats_async"] >= 5
            assert attended["update_daemon_status"] >= 28
            app._set_attended(False)
            detached = self._run(app, clock, 60)
        for name in ATTENDED_ONLY:
            assert detached.get(name, 0) == 0, name
        # The signal poll (1 s), the status read (2 s) and the flushes keep going
        assert detached["_poll_attended_async"] == pytest.approx(60, abs=2)
        assert detached["_fetch_unattended_status_async"] == pytest.approx(30, abs=2)
        assert detached["_flush_heartbeat"] == pytest.approx(12, abs=2)

    def test_reattach_restores_every_cadence_within_a_tick(self):
        app, clock = _bare_app()
        _start_all_timers(app)
        with patch("overcode.tui.touch_tui_attended"), patch("overcode.tui.signal_activity"):
            clock.advance(10)
            app._set_attended(False)
            clock.advance(60)
            app._set_attended(True)
            after = self._run(app, clock, 30)
        assert after["update_focused_status"] == pytest.approx(120, abs=2)
        assert after["update_daemon_status"] >= 28
        assert after["_update_stats_async"] >= 5
        assert after["_fetch_unattended_status_async"] == 0

    def test_the_fake_signal_drives_the_state_through_the_watch(self):
        """A poll answer of 0 clients detaches; 1 client re-attaches; None is ignored."""
        app, clock = _bare_app()
        _start_all_timers(app)
        with patch("overcode.tui.touch_tui_attended"), patch("overcode.tui.signal_activity"):
            clock.advance(5)
            app._apply_attended_poll(("agents", 0))
            assert app.attended is False
            assert app._tui_tmux_session == "agents"
            app._apply_attended_poll(None)  # tmux could not answer: no change
            assert app.attended is False
            app._apply_attended_poll(("agents", 1))
            assert app.attended is True
        assert app.calls["update_all_statuses"] == 1


class TestAttendedWatchTick:
    def test_polls_tmux_when_no_fresh_listing_reading(self):
        app, _ = _bare_app()
        with patch("overcode.tui.touch_tui_attended"):
            app._attended_watch_tick()
        assert app.calls["_poll_attended_async"] == 1

    def test_uses_the_fast_paths_listing_instead_of_a_command_when_fresh(self):
        app, _ = _bare_app()
        app._tui_tmux_session = "agents"
        app._attached_reading = (0, time.monotonic())
        with patch("overcode.tui.touch_tui_attended"), patch("overcode.tui.signal_activity"):
            app._attended_watch_tick()
        assert "_poll_attended_async" not in app.calls
        assert app.attended is False

    def test_a_stale_listing_reading_is_not_trusted(self):
        app, _ = _bare_app()
        app._attached_reading = (0, time.monotonic() - 2.0)
        with patch("overcode.tui.touch_tui_attended"):
            app._attended_watch_tick()
        assert app.calls["_poll_attended_async"] == 1
        assert app.attended is True

    def test_touches_the_liveness_file_every_five_seconds_while_attended(self):
        app, _ = _bare_app()
        touched = []
        with patch("overcode.tui.touch_tui_attended", touched.append):
            with patch(
                "overcode.tui.time.monotonic", side_effect=[100.0, 101.0, 104.9, 105.0, 111.0]
            ):
                for _ in range(5):
                    app._attended_watch_tick()
        assert touched == ["agents", "agents", "agents"]  # at 100, 105, 111

    def test_never_touches_while_unattended(self):
        app, _ = _bare_app(attended=False)
        touched = []
        with patch("overcode.tui.touch_tui_attended", touched.append):
            app._attended_watch_tick()
            app._attended_watch_tick()
        assert touched == []

    def test_the_poll_addresses_this_panes_own_server(self):
        """A pane id means nothing on another server: the query carries the
        socket from $TMUX, not -L $OVERCODE_TMUX_SOCKET."""
        app, _ = _bare_app()
        app.call_from_thread = lambda fn, *a: fn(*a)
        with patch("overcode.tui.query_pane_attended", return_value=("work", 0)) as query:
            SupervisorTUI._poll_attended_async.__wrapped__(app)
        query.assert_called_once_with("%3", socket_path="/tmp/tmux-1/default")
        assert app.attended is False and app._tui_tmux_session == "work"


class TestOutsideTmux:
    """A TUI in a plain terminal: nobody can tell, so it stays attended and
    keeps the daemon fast with its touch — the case the daemon's third
    signal exists for."""

    def test_the_watch_runs_touches_and_never_polls(self):
        app, clock = _bare_app(in_tmux=False)
        _start_all_timers(app)
        touched = []
        with (
            patch("overcode.tui.touch_tui_attended", touched.append),
            patch("overcode.tui.time.monotonic", lambda: clock.now),  # the touch is wall-clock
        ):
            clock.advance(60)
        assert app.attended is True
        assert "_poll_attended_async" not in app.calls
        assert touched == ["agents"] * 12  # every 5 s for a minute
        assert app.calls["update_focused_status"] == pytest.approx(240, abs=2)
        assert app.calls["update_daemon_status"] >= 58
        assert "_fetch_unattended_status_async" not in app.calls

    def test_a_stale_listing_reading_is_never_consulted(self):
        app, _ = _bare_app(in_tmux=False)
        app._tui_tmux_session = "agents"
        app._attached_reading = (0, time.monotonic())  # could not arise, but must not flip
        with patch("overcode.tui.touch_tui_attended"):
            app._attended_watch_tick()
        assert app.attended is True


class TestListingReading:
    def _panes(self, attached):
        from overcode.tmux_utils import PaneInfo

        return {"w": PaneInfo("w", 1, 10, 5, 0, 0, 0, "claude", attached)}

    def test_recorded_when_this_pane_is_in_the_agents_session(self):
        app, _ = _bare_app()
        app._tui_tmux_session = "agents"
        app._note_pane_listing(self._panes(2))
        count, at = app._attached_reading
        assert count == 2 and time.monotonic() - at < 1

    def test_ignored_for_a_tui_in_another_session_or_unknown(self):
        app, _ = _bare_app()
        app._note_pane_listing(self._panes(0))  # session not yet learned
        assert app._attached_reading is None
        app._tui_tmux_session = "work"
        app._note_pane_listing(self._panes(0))
        assert app._attached_reading is None

    def test_ignored_when_the_listing_failed(self):
        app, _ = _bare_app()
        app._tui_tmux_session = "agents"
        app._note_pane_listing(None)
        app._note_pane_listing({})
        assert app._attached_reading is None

    def test_ignored_when_the_listing_is_from_another_server(self):
        """OVERCODE_TMUX_SOCKET names a server this pane is not on: a session
        called "agents" there is not the one holding this pane."""
        app, _ = _bare_app()
        app._tui_tmux_session = "agents"
        app._listing_is_own_server = False
        app._note_pane_listing(self._panes(0))
        assert app._attached_reading is None

    def test_fast_path_records_the_listing_it_already_issues(self):
        """The real worker body: the gate's listing feeds the reading, no extra command."""
        from overcode.pane_capture_gate import PaneChangeTracker

        app, _ = _bare_app()
        app._tui_tmux_session = "agents"
        sessions = []
        for i in range(6):  # > every=4 non-focused: the listing is worth issuing
            s = MagicMock()
            s.id = f"s{i}"
            s.is_remote = False
            s.status = "running"
            s.tmux_window = f"w{i}"
            sessions.append(s)
        widgets = [MagicMock(session=s) for s in sessions]
        app.session_manager = MagicMock()
        app.session_manager.list_sessions.return_value = sessions
        app._get_focused_widget = lambda: widgets[0]
        app._previous_statuses = {}
        app._pane_content_cache = {}
        app._activity_cache = {}
        app._status_tick = 0
        app._remote_sessions = []
        app._summaries = {}
        app.detector = MagicMock()
        app.detector.detect_status.side_effect = lambda s, num_lines=0: ("running", "a", "p")
        app._pane_change_tracker = PaneChangeTracker()
        app._tmux = MagicMock()
        from overcode.tmux_utils import PaneInfo

        app._tmux.list_panes.return_value = {
            f"w{i}": PaneInfo(f"w{i}", i, 1, 1, 1, 0, 0, "claude", 1) for i in range(6)
        }
        app.call_from_thread = lambda fn, *a, **kw: None
        with patch("overcode.tui.get_monitor_daemon_state", return_value=None):
            SupervisorTUI._fetch_statuses_async.__wrapped__(app, widgets)
        assert app._tmux.list_panes.call_count == 1
        assert app._attached_reading[0] == 1


class TestOnKeyBackstop:
    def test_a_keypress_reattaches_at_once(self):
        app, clock = _bare_app(attended=False)
        _start_all_timers(app)
        clock.advance(5)
        app._last_keypress = 0.0
        app._last_heartbeat_write = 0.0
        app._summarizer_idle_paused = False
        app._summarizer = MagicMock(cost_cap_hit=False)
        app._should_recover_focus = lambda: False
        with (
            patch("overcode.tui.signal_activity"),
            patch("overcode.tui.write_tui_heartbeat"),
            patch("overcode.tui.touch_tui_attended"),
        ):
            app.on_key(MagicMock())
        assert app.attended is True
        assert not app._periodic_timers["fast_status"].paused


class TestUnattendedStatusPath:
    """Daemon-published status drives the same stall bookkeeping, with no repaint."""

    def _app(self):
        app, _ = _bare_app(attended=False)
        app._previous_statuses = {}
        app._stall_start_times = {}
        app._notified_stalls = set()
        app._non_stall_since = {}
        app._bell_dismiss_timers = {}
        app._prefs = MagicMock(status_change_logging=False, visited_stalled_agents=set())
        app._notifier = MagicMock()
        app._save_prefs = MagicMock()
        return app

    @pytest.fixture(autouse=True)
    def _no_preview(self):
        # preview_visible is a Textual reactive; a bare instance cannot set it
        with patch.object(SupervisorTUI, "preview_visible", False):
            yield

    def _widget(self, sid, name="a", start="2026-01-01T00:00:00"):
        w = MagicMock()
        w.session.id = sid
        w.session.name = name
        w.session.is_asleep = False
        w.session.start_time = start
        w.session.stats.current_task = "task"
        w.is_unvisited_stalled = False
        w.refresh = MagicMock()
        return w

    def test_worker_reads_state_and_applies_only_when_fresh(self):
        app = self._app()
        applied = []
        app.call_from_thread = lambda fn, *a: applied.append((fn, a))
        state = MagicMock()
        state.sessions = [MagicMock(session_id="s1", current_status="waiting_user")]
        state.is_stale.return_value = False
        with patch("overcode.tui.get_monitor_daemon_state", return_value=state):
            SupervisorTUI._fetch_unattended_status_async.__wrapped__(app)
        assert applied == [(app._apply_unattended_status, ({"s1": "waiting_user"},))]
        state.is_stale.return_value = True
        applied.clear()
        with patch("overcode.tui.get_monitor_daemon_state", return_value=state):
            SupervisorTUI._fetch_unattended_status_async.__wrapped__(app)
        assert applied == []
        with patch("overcode.tui.get_monitor_daemon_state", return_value=None):
            SupervisorTUI._fetch_unattended_status_async.__wrapped__(app)
        assert applied == []

    def test_a_stall_seen_while_detached_rings_the_bell_and_notifies(self):
        app = self._app()
        w = self._widget("s1")
        app.query = lambda cls: [w]
        app._previous_statuses["s1"] = "running"
        app._apply_unattended_status({"s1": "waiting_user"})
        assert w.is_unvisited_stalled is True
        assert app._previous_statuses["s1"] == "waiting_user"
        assert "s1" in app._stall_start_times
        app._notifier.queue.assert_not_called()  # deferred: 30 s of stall first
        app._notifier.flush.assert_called_once()
        w.refresh.assert_not_called()  # nothing is drawn while nobody watches
        app._stall_start_times["s1"] -= 31
        app._apply_unattended_status({"s1": "waiting_user"})
        app._notifier.queue.assert_called_once_with("a", "task")
        assert app._notified_stalls == {"s1"}

    def test_widgets_the_daemon_does_not_report_are_left_alone(self):
        app = self._app()
        w = self._widget("s2")
        app.query = lambda cls: [w]
        app._apply_unattended_status({"s1": "waiting_user"})
        assert app._previous_statuses == {}
        assert w.is_unvisited_stalled is False

    def test_attended_path_uses_the_same_bookkeeping(self):
        """_apply_status_results delegates to _track_stall: one transition, one bell."""
        app = self._app()
        w = self._widget("s1")
        app.query = lambda cls: [w]
        app._session_burn_rates = {}
        app._recompute_cell_column_widths = MagicMock()
        app._should_recover_focus = lambda: False
        app.detector = MagicMock()
        app._previous_statuses["s1"] = "running"
        app._apply_status_results({"s1": ("waiting_user", "Waiting", "pane")}, {})
        assert w.is_unvisited_stalled is True
        assert app._previous_statuses["s1"] == "waiting_user"
        w.apply_status_no_refresh.assert_called_once()
        w.refresh.assert_called_once()


class TestPhaseOffsetsOfTheNewTimers:
    def test_new_timers_have_offsets_off_the_fast_grid(self):
        for name in ("attended_watch", "unattended_status"):
            assert TIMER_PHASE_OFFSETS[name] % 0.25 != 0
