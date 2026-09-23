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

from overcode.tui import TIMER_INTERVALS, TIMER_PHASE_OFFSETS, SupervisorTUI  # noqa: E402


class TestCadences:
    def test_intervals_are_the_freshness_contract(self):
        """Never lowered to save CPU; a change here is a product decision."""
        assert TIMER_INTERVALS == {
            "fast_status": 0.25,
            "daemon_status": 1,
            "focused_job_pane": 1,
            "focused_sister": 1.5,
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


class TestStartPeriodic:
    def _app(self):
        app = SupervisorTUI.__new__(SupervisorTUI)
        app.set_timer = MagicMock()
        app.set_interval = MagicMock()
        return app

    def test_zero_offset_starts_the_interval_directly(self):
        app = self._app()
        cb = object()
        app._start_periodic("slow_stats", cb)
        app.set_interval.assert_called_once_with(5, cb)
        app.set_timer.assert_not_called()

    def test_offset_delays_the_first_tick_then_keeps_the_interval(self):
        app = self._app()
        cb = object()
        app._start_periodic("timeline", cb)
        app.set_interval.assert_not_called()
        delay, start = app.set_timer.call_args.args
        assert delay == 3.9
        start()  # the delayed callback installs the real interval
        app.set_interval.assert_called_once_with(30, cb)

    @pytest.mark.parametrize("name", sorted(TIMER_INTERVALS))
    def test_every_timer_starts_with_its_own_cadence(self, name):
        app = self._app()
        cb = object()
        app._start_periodic(name, cb)
        if TIMER_PHASE_OFFSETS[name] > 0:
            app.set_timer.call_args.args[1]()
        app.set_interval.assert_called_once_with(TIMER_INTERVALS[name], cb)
