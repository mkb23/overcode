"""Single-flight-with-coalescing guard and cancel check for Textual thread workers.

Textual's ``@work(thread=True, exclusive=True)`` cancels only the asyncio
task that awaits a thread worker; the thread itself runs to completion
(textual 7.5.0: ``Worker._run_threaded`` hands the callable to
``loop.run_in_executor`` and ``Worker.cancel`` just sets ``_cancelled`` and
cancels ``_task``). So a periodic worker whose run outlasts its period
stacks: a second thread starts while the first is still running, then a
third, up to the default executor's size (cpu_count + 4, max 32), all
GIL-bound, and the executor queue behind them grows for the life of the
process. That is the amplifier that turned every slow site in the TUI into
a permanently pinned core (scaling audit R2).

:func:`single_flight` runs at most one call per group on an instance at a
time. A call that arrives while one is running is *coalesced*, not dropped:
its arguments are queued (latest wins) and the running call executes the
body once more after it finishes. That matters because the guarded workers
are not only timer ticks — ``refresh_sessions``, ``update_timeline``,
``_refresh_jobs`` and the resize sweep are also the event-driven refresh
after a user creates, forks or revives an agent, changes a standing order
or resizes the terminal. Dropping such a call would hide the new agent for
up to a full period (10 s for sessions, 30 s for the timeline). Coalescing
bounds the cost of a slow tick to one extra run and never a thread stack,
and the rerun always reads fresher state than the run it followed, so the
last result applied is the latest.

The guarded workers are therefore *not* ``exclusive``: with the group
serialised here, ``exclusive=True`` only cancelled the in-flight pass that
the arriving tick then failed to replace (a per-agent loop stopping at its
next cancel check, then nothing running until the following period —
a 50 % duty cycle for any pass longer than its period).

:func:`worker_cancelled` is for long per-agent loops whose partial progress
is retained between runs (summaries cached per agent, windows already
resized). Checked between agents it lets a worker stop early on app exit
instead of finishing work nobody will apply.
"""

from __future__ import annotations

import functools
import threading
from typing import Any, Callable, Dict, Optional, Tuple, TypeVar

from textual.worker import NoActiveWorker, get_current_worker

F = TypeVar("F", bound=Callable[..., object])

_STATE_ATTR = "_single_flight_state"
_registry_guard = threading.Lock()

_Pending = Tuple[Tuple[Any, ...], Dict[str, Any]]


class _GroupState:
    """Run state for one (instance, group): the running flag and the queued rerun.

    ``mutex`` protects both fields. ``pending`` holds the ``(args, kwargs)``
    of the most recent call that arrived while ``running`` was set; the
    running call consumes it once after its body returns.
    """

    __slots__ = ("mutex", "running", "pending")

    def __init__(self) -> None:
        self.mutex = threading.Lock()
        self.running = False
        self.pending: Optional[_Pending] = None


def _state_for(instance: object, group: str) -> _GroupState:
    """The per-instance, per-group state, created on first use.

    Stored on the instance rather than in ``__init__`` so lightweight test
    doubles built via ``__new__`` work, and so a subclass never has to
    remember to initialise it.
    """
    states: Optional[Dict[str, _GroupState]] = instance.__dict__.get(_STATE_ATTR)
    if states is None:
        with _registry_guard:
            states = instance.__dict__.get(_STATE_ATTR)
            if states is None:
                states = {}
                instance.__dict__[_STATE_ATTR] = states
    state = states.get(group)
    if state is None:
        with _registry_guard:
            state = states.get(group)
            if state is None:
                state = _GroupState()
                states[group] = state
    return state


def single_flight(group: str) -> Callable[[F], F]:
    """Run one call in ``group`` at a time on this instance; coalesce the rest.

    Stack it *under* ``@work(thread=True, ...)`` so the check happens in the
    worker thread and a coalesced call costs one executor submission and
    nothing else. A coalesced call returns ``None`` immediately; its
    arguments replace any earlier queued ones, and the call currently
    running executes the body once more with them after its own body
    returns (in the same thread, so it still applies its result through
    ``call_from_thread`` before the rerun starts). The returned value is
    that of the last body executed. If the body raises, the queued rerun is
    discarded and the group is released.
    """

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            state = _state_for(self, group)
            with state.mutex:
                if state.running:
                    state.pending = (args, kwargs)
                    return None
                state.running = True
            try:
                result = fn(self, *args, **kwargs)
                while True:
                    with state.mutex:
                        queued = state.pending
                        state.pending = None
                        if queued is None:
                            state.running = False
                            return result
                    queued_args, queued_kwargs = queued
                    result = fn(self, *queued_args, **queued_kwargs)
            except BaseException:
                with state.mutex:
                    state.running = False
                    state.pending = None
                raise

        wrapper.single_flight_group = group  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator


def is_in_flight(instance: object, group: str) -> bool:
    """Whether a ``single_flight`` call in ``group`` is currently running on ``instance``."""
    states = instance.__dict__.get(_STATE_ATTR) or {}
    state = states.get(group)
    return bool(state is not None and state.running)


def worker_cancelled() -> bool:
    """True when the current Textual worker was cancelled; False outside a worker.

    The app cancels every worker on exit (``App._process_messages`` ->
    ``workers.cancel_all``). The guarded periodic workers are not
    ``exclusive``, so a newer tick never cancels a run; this only fires on
    shutdown or an explicit ``cancel_group``. Safe to call from plain code
    and tests (no active worker -> False). Note it only reflects the worker
    thread itself: callables handed to a nested ``ThreadPoolExecutor`` run in
    pool threads with no active worker, so evaluate it in the loop that
    submits them, not inside them.
    """
    try:
        return bool(get_current_worker().is_cancelled)
    except NoActiveWorker:
        return False
