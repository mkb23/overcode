"""Single-flight guard and cancel check for Textual thread workers.

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

:func:`single_flight` skips a tick while the previous run of the same group
on the same instance is still executing. The running tick finishes and
applies its result, so the cost of a slow tick is one skipped period —
never a stack of threads, and never starvation.

:func:`worker_cancelled` is for long per-agent loops whose partial progress
is retained between runs (summaries cached per agent, windows already
resized). Checked between agents it lets a superseded or shutting-down
worker stop early instead of finishing work nobody will apply.
"""

from __future__ import annotations

import functools
import threading
from typing import Callable, Dict, Optional, TypeVar

from textual.worker import NoActiveWorker, get_current_worker

F = TypeVar("F", bound=Callable[..., object])

_LOCKS_ATTR = "_single_flight_locks"
_registry_guard = threading.Lock()


def _lock_for(instance: object, group: str) -> threading.Lock:
    """The per-instance, per-group lock, created on first use.

    Stored on the instance rather than in ``__init__`` so lightweight test
    doubles built via ``__new__`` work, and so a subclass never has to
    remember to initialise it.
    """
    locks: Optional[Dict[str, threading.Lock]] = instance.__dict__.get(_LOCKS_ATTR)
    if locks is None:
        with _registry_guard:
            locks = instance.__dict__.get(_LOCKS_ATTR)
            if locks is None:
                locks = {}
                instance.__dict__[_LOCKS_ATTR] = locks
    lock = locks.get(group)
    if lock is None:
        with _registry_guard:
            lock = locks.get(group)
            if lock is None:
                lock = threading.Lock()
                locks[group] = lock
    return lock


def single_flight(group: str) -> Callable[[F], F]:
    """Skip the call while a previous call in ``group`` on this instance is running.

    Stack it *under* ``@work(thread=True, ...)`` so the check happens in the
    worker thread and the skipped call costs one executor submission and
    nothing else. A skipped call returns ``None``. The lock is released
    whether the body returns or raises.
    """

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            lock = _lock_for(self, group)
            if not lock.acquire(blocking=False):
                return None
            try:
                return fn(self, *args, **kwargs)
            finally:
                lock.release()

        wrapper.single_flight_group = group  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator


def is_in_flight(instance: object, group: str) -> bool:
    """Whether a ``single_flight`` call in ``group`` is currently running on ``instance``."""
    locks = instance.__dict__.get(_LOCKS_ATTR) or {}
    lock = locks.get(group)
    return bool(lock is not None and lock.locked())


def worker_cancelled() -> bool:
    """True when the current Textual worker was cancelled; False outside a worker.

    With ``exclusive=True`` a newer tick cancels the previous worker, and
    the app cancels every worker on exit. Safe to call from plain code and
    tests (no active worker -> False). Note it only reflects the worker
    thread itself: callables handed to a nested ``ThreadPoolExecutor`` run in
    pool threads with no active worker, so evaluate it in the loop that
    submits them, not inside them.
    """
    try:
        return bool(get_current_worker().is_cancelled)
    except NoActiveWorker:
        return False
