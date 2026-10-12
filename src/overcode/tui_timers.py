"""The TUI's periodic timers: one table of cadences and one scheduler.

``TIMER_INTERVALS`` is the product's freshness contract: a cadence here is
never lowered to save CPU (do the work cheaper instead). Every periodic
timer in the TUI, the app's own and the daemon/log panels' tails, starts
through ``PeriodicScheduler.start``, so the cadences, the phase offsets and
the pause-while-unattended rule live in one place.
"""

from __future__ import annotations

from typing import Callable, Dict

# Periodic timer cadences (seconds). These are the product's freshness
# contract and are never changed to save CPU. Since 0.6.0 the TUI is a view
# of the engine (docs/design/engine-0.6.md): statuses, stats, git and burn
# arrive on engine.sock as the engine publishes them, so no timer here
# detects, reads stats or polls the daemon's state file. What remains:
# focused_pane is the one capture loop (the focused agent's terminal, for
# its pane-derived columns and the preview) and the row clock (durations
# and countdowns tick); daemon_status is the status bar's local checks
# (supervisor/API server/summarizer processes, usage, mean spin) and the
# engine-connection watch; heartbeat_probe is the event-loop diagnostic;
# attended_watch is the "is anyone looking" poll the engine is told about;
# daemon_panel and tui_log_panel tail the `d` and `O` panels' logs (no
# work while the panel is hidden).
TIMER_INTERVALS = {
    "heartbeat_probe": 0.1,
    "focused_pane": 0.25,
    "daemon_status": 1,
    "focused_job_pane": 1,
    "attended_watch": 1,
    "daemon_panel": 1,
    "tui_log_panel": 1,
    "focused_sister": 1.5,
    "summarizer": 5,
    "refresh_jobs": 5,
    "heartbeat_flush": 5,
    "activity_flush": 2,
    "view_control": 1,
    "status_changes": 5,
    "refresh_sessions": 10,
    "sister_poll": 10,
    "agent_resize": 15,
    "timeline": 30,
}

# Initial delay before each timer's first tick. The intervals above share
# common multiples (5/10/15/30 s), so timers started at the same instant fire
# together forever and their main-thread apply callbacks stack up. Distinct
# offsets (mod 5 s) spread them without touching any cadence. The >= 1 s
# timers are also kept off each other and off the 250 ms focused-pane grid:
# an offset that is not a multiple of 0.25 never lands on a focused tick
# (the hour-long test in test_tui_timers checks every pair). The 0.25 s and
# 0.1 s timers are the grid itself and stay at phase 0.
TIMER_PHASE_OFFSETS = {
    "heartbeat_probe": 0.0,
    "focused_pane": 0.0,
    "daemon_status": 1.55,
    "focused_job_pane": 0.85,
    "attended_watch": 0.45,
    "daemon_panel": 0.35,
    "tui_log_panel": 0.8,
    "focused_sister": 0.15,
    "agent_resize": 0.7,
    "refresh_jobs": 1.1,
    "refresh_sessions": 2.3,
    "status_changes": 2.9,
    "summarizer": 3.3,
    "timeline": 3.9,
    "sister_poll": 4.2,
    "heartbeat_flush": 4.6,
    "activity_flush": 1.95,
    "view_control": 1.05,
}

# Timers paused while no tmux client is attached to the pane the TUI runs
# in: the capture, the clock, every refresh and every render-driving apply.
# What keeps running: attended_watch (the signal itself, one tmux command a
# second) and the cheap flush timers that drain buffers. The engine keeps
# recording, and its bells still reach this TUI (notifications go out while
# detached). The whole set resumes, and a full refresh runs, the moment a
# client attaches again (SupervisorTUI._on_attended_changed).
PAUSED_WHEN_UNATTENDED = frozenset(
    {
        "heartbeat_probe",
        "focused_pane",
        "daemon_status",
        "focused_job_pane",
        "focused_sister",
        "summarizer",
        "refresh_jobs",
        "refresh_sessions",
        "sister_poll",
        "agent_resize",
        "timeline",
    }
)


class PeriodicScheduler:
    """Every periodic timer of one app, by ``TIMER_INTERVALS`` name.

    ``host`` is the Textual app the timers run on (its ``set_interval`` /
    ``set_timer``, looked up at call time, and its ``attended`` flag).
    ``enabled`` False (tests, ``SupervisorTUI(timers=False)``) starts
    nothing: the test calls the tick it is about.
    """

    def __init__(self, host, enabled: bool = True) -> None:
        self.host = host
        self.enabled = enabled
        self.timers: Dict[str, object] = {}

    def start(self, name: str, callback: Callable[[], object]) -> None:
        """Start the ``name`` timer at its TIMER_INTERVALS cadence, phase-shifted.

        A non-zero TIMER_PHASE_OFFSETS entry delays the first tick by that
        many seconds (set_timer, then set_interval), so the timer fires at
        offset + k * interval instead of k * interval. Whether it starts
        paused is decided when it actually starts — after the delay — from
        the host's attended state at that moment: a PAUSED_WHEN_UNATTENDED
        timer starts paused while nobody is attached.
        """
        if not self.enabled:
            return
        interval = TIMER_INTERVALS[name]
        delay = TIMER_PHASE_OFFSETS[name]
        host = self.host

        def begin() -> None:
            start_paused = name in PAUSED_WHEN_UNATTENDED and not getattr(host, "attended", True)
            self.timers[name] = host.set_interval(interval, callback, pause=start_paused)

        if delay > 0:
            host.set_timer(delay, begin)
        else:
            begin()

    def set_attended(self, attended: bool) -> None:
        """Pause (nobody attached) or resume every PAUSED_WHEN_UNATTENDED timer."""
        for name in PAUSED_WHEN_UNATTENDED:
            timer = self.timers.get(name)
            if timer is None:
                continue
            if attended:
                timer.resume()
            else:
                timer.pause()
