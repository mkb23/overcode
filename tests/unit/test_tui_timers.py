"""The TUI's periodic timers: cadences are the freshness contract, phases are spread.

The 5/10/15/30 s timers used to start at the same instant and so fired
together every fifth second (stats executor, git subprocesses, tmux
list-windows, jobs refresh and their main-thread apply callbacks at once).
De-phasing them spreads that burst without changing any interval.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.tui import (  # noqa: E402
    RUNS_ONLY_WHEN_UNATTENDED,
    TIMER_INTERVALS,
    TIMER_PHASE_OFFSETS,
    SupervisorTUI,
)


class TestCadences:
    def test_intervals_are_the_freshness_contract(self):
        """Never lowered to save CPU; a change here is a product decision."""
        assert TIMER_INTERVALS == {
            "heartbeat_probe": 0.1,
            "fast_status": 0.25,
            "daemon_status": 1,
            "focused_job_pane": 1,
            "attended_watch": 1,
            "focused_sister": 1.5,
            "unattended_status": 2,
            "slow_stats": 5,
            "summarizer": 5,
            "refresh_jobs": 5,
            "heartbeat_flush": 5,
            "status_changes": 5,
            "refresh_sessions": 10,
            "sister_poll": 10,
            "agent_resize": 15,
            "timeline": 30,
        }

    def test_every_timer_has_an_offset(self):
        assert set(TIMER_PHASE_OFFSETS) == set(TIMER_INTERVALS)

    def test_offsets_are_within_the_phase_modulus(self):
        """Offsets only shift phase; none delays a first tick beyond 5 s.

        (daemon_status keeps its pre-existing 1.7 s offset on a 1 s timer.)
        """
        for name, delay in TIMER_PHASE_OFFSETS.items():
            assert 0 <= delay < 5, name


class TestPhases:
    def test_long_timers_never_share_a_phase(self):
        """All >= 5 s intervals are multiples of 5, so distinct offsets mod 5 keep
        them from ever coinciding; require >= 0.3 s of separation."""
        phases = sorted(
            TIMER_PHASE_OFFSETS[name] % 5 for name, iv in TIMER_INTERVALS.items() if iv >= 5
        )
        gaps = [round(b - a, 6) for a, b in zip(phases, phases[1:])]
        gaps.append(round(phases[0] + 5 - phases[-1], 6))  # wrap-around
        assert min(gaps) >= 0.3, phases

    def test_no_two_long_timers_fire_in_the_same_instant_over_an_hour(self):
        fire_times = {}
        for name, iv in TIMER_INTERVALS.items():
            if iv < 5:
                continue
            delay = TIMER_PHASE_OFFSETS[name]
            fire_times[name] = {round(delay + k * iv, 3) for k in range(int(3600 / iv) + 1)}
        names = list(fire_times)
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                assert not (fire_times[a] & fire_times[b]), (a, b)

    def test_no_two_timers_of_a_second_or_more_ever_coincide(self):
        """Every >= 1 s timer is de-phased from every other one, over an hour
        of fire times, and none lands on the 250 ms fast-status grid."""
        fire_times = {}
        for name, iv in TIMER_INTERVALS.items():
            if iv < 1:
                continue
            delay = TIMER_PHASE_OFFSETS[name]
            fire_times[name] = {round(delay + k * iv, 3) for k in range(int(3600 / iv) + 1)}
        grid = {round(k * 0.25, 3) for k in range(4 * 3600 + 1)}
        names = list(fire_times)
        for i, a in enumerate(names):
            assert not (fire_times[a] & grid), a
            for b in names[i + 1 :]:
                assert not (fire_times[a] & fire_times[b]), (a, b)


class TestStartPeriodic:
    def _app(self, attended=True):
        app = SupervisorTUI.__new__(SupervisorTUI)
        app.set_timer = MagicMock()
        app.set_interval = MagicMock()
        app.attended = attended
        app._periodic_timers = {}
        return app

    def test_zero_offset_starts_the_interval_directly(self):
        app = self._app()
        cb = object()
        app._start_periodic("fast_status", cb)
        app.set_interval.assert_called_once_with(0.25, cb, pause=False)
        app.set_timer.assert_not_called()

    def test_offset_delays_the_first_tick_then_keeps_the_interval(self):
        app = self._app()
        cb = object()
        app._start_periodic("timeline", cb)
        app.set_interval.assert_not_called()
        delay, start = app.set_timer.call_args.args
        assert delay == 3.9
        start()  # the delayed callback installs the real interval
        app.set_interval.assert_called_once_with(30, cb, pause=False)

    @pytest.mark.parametrize("name", sorted(TIMER_INTERVALS))
    def test_every_timer_starts_with_its_own_cadence(self, name):
        app = self._app()  # attended: only the unattended-only read starts paused
        cb = object()
        app._start_periodic(name, cb)
        if TIMER_PHASE_OFFSETS[name] > 0:
            app.set_timer.call_args.args[1]()
        app.set_interval.assert_called_once_with(
            TIMER_INTERVALS[name], cb, pause=name in RUNS_ONLY_WHEN_UNATTENDED
        )
        assert app._periodic_timers[name] is app.set_interval.return_value

    def test_the_unattended_read_starts_paused_only_while_attended(self):
        app = self._app(attended=True)
        app._start_periodic("unattended_status", object())
        app.set_timer.call_args.args[1]()
        assert app.set_interval.call_args.kwargs == {"pause": True}
        app = self._app(attended=False)
        app._start_periodic("unattended_status", object())
        app.set_timer.call_args.args[1]()
        assert app.set_interval.call_args.kwargs == {"pause": False}

    def test_the_pause_decision_is_made_when_the_delayed_start_lands(self):
        """A detach during the 0.8 s delay must not leave the read paused."""
        app = self._app(attended=True)
        app._start_periodic("unattended_status", object())
        app.attended = False
        app.set_timer.call_args.args[1]()
        assert app.set_interval.call_args.kwargs == {"pause": False}

    def test_a_pausable_timer_started_while_unattended_starts_paused(self):
        app = self._app(attended=False)
        app._start_periodic("fast_status", object())
        assert app.set_interval.call_args.kwargs == {"pause": True}
        app._start_periodic("status_changes", object())  # not in the paused set
        app.set_timer.call_args.args[1]()
        assert app.set_interval.call_args.kwargs == {"pause": False}
