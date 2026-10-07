"""The TUI's attended state: timers pause while no client is attached, resume on return.

Nothing in the TUI was gated on anyone watching (scaling audit,
cross-cutting finding): every timer ran at full rate with the tmux client
detached. Now one ``attended`` flag on the app, written only through
``_set_attended`` and reacted to only in ``_on_attended_changed``, pauses
every timer in PAUSED_WHEN_UNATTENDED and tells the engine (``visible``) so
it can run its own unattended cadences. Bells keep arriving over
engine.sock while detached (test_engine_view_tui.py). These tests drive the
real app methods on a bare instance with fake timers and a fake
attached/detached signal, and count what the workers were asked to do.
"""

import sys
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
            for due, cb in sorted(self.pending, key=lambda p: p[0]):
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
    app._tui_tmux_session = None
    app._heartbeat_last = 0.0
    app._heartbeat_enabled = False
    app.has_sisters = False
    app.diagnostics = False
    app._engine_client = MagicMock()
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
        "_focused_pane_tick",
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
        "_repaint_rows",
        "_poll_attended_async",
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
    app._start_periodic("focused_pane", app._focused_pane_tick)
    app._start_periodic("daemon_status", app.update_daemon_status)
    app._start_periodic("timeline", app.update_timeline)
    app._start_periodic("summarizer", app._update_summaries_async)
    app._start_periodic("refresh_jobs", app._refresh_jobs)
    app._start_periodic("focused_job_pane", app._poll_focused_job_pane)
    app._start_periodic("agent_resize", app._periodic_agent_resize)
    app._start_periodic("attended_watch", app._attended_watch_tick)


ATTENDED_ONLY = {
    "refresh_sessions",
    "_focused_pane_tick",
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
        for name in ("attended_watch", "heartbeat_flush", "status_changes", "activity_flush"):
            assert name not in PAUSED_WHEN_UNATTENDED
        # The capture, every render and every refresh path is in the paused set
        for name in (
            "focused_pane",
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
    def test_detach_pauses_the_set_and_tells_the_engine(self):
        app, clock = _bare_app()
        _start_all_timers(app)
        clock.advance(5)  # every delayed start has happened
        with patch("overcode.tui.signal_activity"):
            app._set_attended(False)
        paused = {name for name, t in app._periodic_timers.items() if t.paused}
        assert paused == PAUSED_WHEN_UNATTENDED & set(app._periodic_timers)
        assert len(paused) == 9  # every started timer in the set (no sisters here)
        assert not app._periodic_timers["attended_watch"].paused
        assert not app._periodic_timers["heartbeat_flush"].paused
        assert not app._periodic_timers["status_changes"].paused
        app._engine_client.set_visible.assert_called_once_with(False)

    def test_reattach_resumes_everything_tells_the_engine_and_refreshes(self):
        app, clock = _bare_app(attended=False)
        _start_all_timers(app)
        clock.advance(5)
        assert all(t.paused for n, t in app._periodic_timers.items() if n in PAUSED_WHEN_UNATTENDED)
        app.calls = {}  # what the 5 s of detached ticks asked for is not the refresh
        signalled = []
        with patch("overcode.tui.signal_activity", signalled.append):
            app._set_attended(True)
        assert not any(
            t.paused for n, t in app._periodic_timers.items() if n in PAUSED_WHEN_UNATTENDED
        )
        app._engine_client.set_visible.assert_called_once_with(True)
        assert signalled == ["agents"]
        # One full refresh: sessions, daemon bar, timeline, every row, jobs
        assert app.calls == {
            "refresh_sessions": 1,
            "update_daemon_status": 1,
            "update_timeline": 1,
            "_repaint_rows": 1,
            "_refresh_jobs": 1,
        }
        assert app._heartbeat_last > 0  # the probe restarts from now, not from the pause

    def test_setting_the_same_state_is_a_no_op(self):
        app, clock = _bare_app()
        _start_all_timers(app)
        clock.advance(5)
        app._set_attended(True)
        assert "_repaint_rows" not in app.calls
        assert all(t.transitions == [] for t in app._periodic_timers.values())
        app._engine_client.set_visible.assert_not_called()

    def test_sisters_are_polled_on_return_when_configured(self):
        app, clock = _bare_app(attended=False)
        app.has_sisters = True
        with patch("overcode.tui.signal_activity"):
            app._set_attended(True)
        assert app.calls["_poll_sisters"] == 1

    def test_no_engine_subscription_yet_is_fine(self):
        app, _ = _bare_app()
        app._engine_client = None
        with patch("overcode.tui.signal_activity"):
            app._set_attended(False)
        assert app.attended is False


class TestWorkerInvocationsAcrossDetachAttach:
    """Simulated minutes with the fake signal: paused workers are never invoked."""

    def _run(self, app, clock, seconds):
        before = dict(app.calls)
        clock.advance(seconds)
        return {k: app.calls.get(k, 0) - before.get(k, 0) for k in app.calls}

    def test_nothing_but_the_watch_and_flushes_run_while_detached(self):
        app, clock = _bare_app()
        _start_all_timers(app)
        with patch("overcode.tui.signal_activity"):
            attended = self._run(app, clock, 30)
            assert attended["_focused_pane_tick"] == pytest.approx(120, abs=2)
            assert attended["update_daemon_status"] >= 28
            app._set_attended(False)
            detached = self._run(app, clock, 60)
        for name in ATTENDED_ONLY:
            assert detached.get(name, 0) == 0, name
        # The signal poll (1 s) and the flushes keep going
        assert detached["_poll_attended_async"] == pytest.approx(60, abs=2)
        assert detached["_flush_heartbeat"] == pytest.approx(12, abs=2)

    def test_reattach_restores_every_cadence_within_a_tick(self):
        app, clock = _bare_app()
        _start_all_timers(app)
        with patch("overcode.tui.signal_activity"):
            clock.advance(10)
            app._set_attended(False)
            clock.advance(60)
            app._set_attended(True)
            after = self._run(app, clock, 30)
        assert after["_focused_pane_tick"] == pytest.approx(120, abs=2)
        assert after["update_daemon_status"] >= 28

    def test_the_fake_signal_drives_the_state_through_the_watch(self):
        """A poll answer of 0 clients detaches; 1 client re-attaches; None is ignored."""
        app, clock = _bare_app()
        _start_all_timers(app)
        with patch("overcode.tui.signal_activity"):
            clock.advance(5)
            app._apply_attended_poll(("agents", 0))
            assert app.attended is False
            assert app._tui_tmux_session == "agents"
            app._apply_attended_poll(None)  # tmux could not answer: no change
            assert app.attended is False
            app._apply_attended_poll(("agents", 1))
            assert app.attended is True
        assert app.calls["_repaint_rows"] == 1
        assert [c.args for c in app._engine_client.set_visible.call_args_list] == [(False,), (True,)]


class TestAttendedWatchTick:
    def test_polls_tmux_every_tick(self):
        app, _ = _bare_app()
        app._attended_watch_tick()
        app._attended_watch_tick()
        assert app.calls["_poll_attended_async"] == 2

    def test_the_poll_addresses_this_panes_own_server(self):
        """A pane id means nothing on another server: the query carries the
        socket from $TMUX, not -L $OVERCODE_TMUX_SOCKET."""
        app, _ = _bare_app()
        app.call_from_thread = lambda fn, *a: fn(*a)
        with patch("overcode.tui.query_pane_attended", return_value=("work", 0)) as query, \
                patch("overcode.tui.signal_activity"):
            SupervisorTUI._poll_attended_async.__wrapped__(app)
        query.assert_called_once_with("%3", socket_path="/tmp/tmux-1/default")
        assert app.attended is False and app._tui_tmux_session == "work"


class TestOutsideTmux:
    """A TUI in a plain terminal: nobody can tell, so it stays attended."""

    def test_the_watch_runs_and_never_polls(self):
        app, clock = _bare_app(in_tmux=False)
        _start_all_timers(app)
        clock.advance(60)
        assert app.attended is True
        assert "_poll_attended_async" not in app.calls
        assert app.calls["_focused_pane_tick"] == pytest.approx(240, abs=2)
        assert app.calls["update_daemon_status"] >= 58


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
        ):
            app.on_key(MagicMock())
        assert app.attended is True
        assert not app._periodic_timers["focused_pane"].paused
        app._engine_client.set_visible.assert_called_once_with(True)


class TestPhaseOffsetsOfTheNewTimers:
    def test_new_timers_have_offsets_off_the_fast_grid(self):
        for name in ("attended_watch", "daemon_status"):
            assert TIMER_PHASE_OFFSETS[name] % 0.25 != 0
