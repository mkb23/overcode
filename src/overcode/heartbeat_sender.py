"""Heartbeat sends off the daemon tick (#528, audit R16).

A heartbeat is a paste into the agent's pane: a copy-mode probe, a
load-buffer/paste-buffer pair per 10 lines with a 0.1 s pause each, and an
Enter. The daemon used to send them one after another inside its tick, so
15 one-batch heartbeats falling due together (they phase-lock: every agent
with the same frequency started near the same time) held the tick past
2 s and every agent's status went stale for that long.

``HeartbeatSender`` runs each send on a small worker pool the moment the
tick finds it due, so the sends overlap and start no later than before.
The tick waits a short, fixed time for them (most finish inside it, so the
heartbeat is stamped and marked in the same tick, as before); a send still
running is collected by a later tick. A session with a send in flight is
not sent again, and the stamp is staged only once the send succeeded, so
each due heartbeat is sent exactly once.
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional

# How long a tick waits for the sends it started. A one-batch heartbeat
# takes ~0.12 s; sends run in parallel, so a burst of them takes about as
# long as one. Anything slower finishes in the background.
HEARTBEAT_WAIT_SECONDS = 0.3

HEARTBEAT_WORKERS = 8


@dataclass
class HeartbeatResult:
    session_id: str
    name: str
    at: datetime  # the tick time the heartbeat went out at
    ok: bool


class HeartbeatSender:
    """Runs heartbeat sends on a worker pool; the tick collects the results."""

    def __init__(self, workers: int = HEARTBEAT_WORKERS):
        self._workers = workers
        self._pool: Optional[ThreadPoolExecutor] = None
        self._lock = threading.Lock()
        self._in_flight: Dict[str, Future] = {}
        self._meta: Dict[str, tuple] = {}

    def in_flight(self, session_id: str) -> bool:
        return session_id in self._in_flight

    def submit(self, session_id: str, name: str, at: datetime, send: Callable[[], bool]) -> None:
        """Start ``send`` now; its result is collected by ``collect``."""
        if session_id in self._in_flight:
            return
        with self._lock:
            if self._pool is None:
                self._pool = ThreadPoolExecutor(
                    max_workers=self._workers, thread_name_prefix="heartbeat")
        self._in_flight[session_id] = self._pool.submit(send)
        self._meta[session_id] = (name, at)

    def collect(self, timeout: float = 0.0) -> List[HeartbeatResult]:
        """The sends that have finished, waiting up to ``timeout`` for the rest.

        A send that raised counts as failed (not stamped: the next tick
        tries again, as a send returning False always did).
        """
        if not self._in_flight:
            return []
        if timeout > 0:
            wait(list(self._in_flight.values()), timeout=timeout)
        results = []
        for sid, future in list(self._in_flight.items()):
            if not future.done():
                continue
            del self._in_flight[sid]
            name, at = self._meta.pop(sid)
            try:
                ok = bool(future.result())
            except Exception:
                ok = False
            results.append(HeartbeatResult(sid, name, at, ok))
        return results

    def shutdown(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False)
