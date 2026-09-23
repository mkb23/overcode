"""Capture-pane gating for the monitor daemon's loop and the TUI's fast path (audit R11).

Every ``capture-pane`` is a command on the single-threaded tmux server that
every overcode process on the host shares, and the daemon issued one per
agent per 2 s loop whether or not anything in the pane had moved — 25
spawns a second from the daemon alone at 50 agents, most of them for
panes that had not changed since the last loop. One ``list-panes -s`` per
loop (``tmux_utils.PANE_LISTING_FORMAT``) says which panes did, so the
loop captures only those and serves the last text for the rest: the same
enrichment inputs, from a cache instead of a subprocess, because a pane
whose signature has not moved shows what it showed last time.

``PaneChangeTracker`` decides, per key, whether a fresh capture is due;
``PaneCaptureGate`` is what the detectors' ``get_pane_content`` consult —
a raw capture when the loop planned one, the cached text otherwise.

The signature — ``window_activity``, ``history_size``, ``cursor_x``,
``cursor_y``, ``pane_current_command`` — has one known blind spot:
``window_activity`` is whole seconds, so output that rewrites the pane in
place (no line scrolls into the history, the cursor ends where it was)
within the same second as the previously observed activity leaves the
signature unchanged. The keepalive bounds it: a pane is captured again
after ``KEEPALIVE_SECONDS`` regardless. Status is unaffected in hooks mode
(it comes from the hook_state file, whose stat is a second input to the
decision, so a transition is always seen with fresh pane text); in polling
mode a pane that changed is captured on the very next loop, so detection
latency is unchanged, and one follow-up capture after every change lets
the polling detector observe "content unchanged since last time" — its
running-to-waiting step — one loop later, exactly as it did.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Container, Dict, Optional, Set, Tuple

KEEPALIVE_SECONDS = 5.0

RawCapture = Callable[[str, int], Optional[str]]


@dataclass
class _Record:
    signature: Any
    extra: Any
    captured_at: float
    follow_up: bool


class PaneChangeTracker:
    """Per-key bookkeeping: does this pane need a fresh capture now?

    ``due`` is asked once per loop per key and records the capture it
    grants, so "changed" always means "since the last capture". A capture
    is due when the key was never captured, when its ``signature`` or
    ``extra`` (the hook_state stat, for the daemon) differs from the last
    capture's, for the one loop after a change (``follow_up``), or when the
    last capture is older than ``keepalive_seconds``. ``now`` is any
    seconds clock the caller keeps consistent (``time.monotonic()``, or a
    frozen timestamp in tests).
    """

    def __init__(self, keepalive_seconds: float = KEEPALIVE_SECONDS):
        self.keepalive_seconds = keepalive_seconds
        self._records: Dict[str, _Record] = {}

    def due(self, key: str, signature: Any, extra: Any, now: float) -> bool:
        record = self._records.get(key)
        if record is None:
            follow_up = False
        elif signature != record.signature or extra != record.extra:
            follow_up = True
        elif record.follow_up:
            follow_up = False
        elif now - record.captured_at >= self.keepalive_seconds:
            follow_up = False
        else:
            return False
        self._records[key] = _Record(signature, extra, now, follow_up)
        return True

    def last_captured_at(self, key: str) -> Optional[float]:
        record = self._records.get(key)
        return None if record is None else record.captured_at

    def forget(self, keep: Container[str]) -> None:
        """Drop the records of keys not in ``keep`` (sessions no longer present)."""
        for key in [k for k in self._records if k not in keep]:
            del self._records[key]

    def __len__(self) -> int:
        return len(self._records)


class PaneCaptureGate:
    """Serves a detector's pane reads: a raw capture if planned, else the last text.

    The loop calls ``begin_loop`` then ``plan(window, capture)`` for every
    window it will detect; ``capture`` (called by ``get_pane_content``)
    runs the raw capture for a window planned as due, for one the loop did
    not plan at all (a caller outside the loop: always safe), and for a
    (window, lines) pair never captured — and otherwise returns what the
    last raw capture returned, ``None`` (window gone) included. Every read
    of a due window in one loop is a raw capture, as every read was before
    gating, so a detector that captures twice sees what it saw then.
    """

    def __init__(self) -> None:
        self._due: Set[str] = set()
        self._planned: Set[str] = set()
        self._cache: Dict[Tuple[str, int], Optional[str]] = {}
        self.raw_captures = 0
        self.served_from_cache = 0

    def begin_loop(self) -> None:
        self._due.clear()
        self._planned.clear()

    def plan(self, window: str, capture: bool) -> None:
        self._planned.add(window)
        if capture:
            self._due.add(window)

    def capture(self, window: str, lines: int, raw: RawCapture) -> Optional[str]:
        key = (window, lines)
        if window in self._due or window not in self._planned or key not in self._cache:
            text = raw(window, lines)
            self._cache[key] = text
            self.raw_captures += 1
            return text
        self.served_from_cache += 1
        return self._cache[key]

    def forget(self, live_windows: Container[str]) -> None:
        """Drop cached text for windows not in ``live_windows``."""
        for key in [k for k in self._cache if k[0] not in live_windows]:
            del self._cache[key]

    def __len__(self) -> int:
        return len(self._cache)
