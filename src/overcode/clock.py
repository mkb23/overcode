"""One clock for the monitor daemon, the status detectors and history.

The daemon reads the time in three ways: the wall clock as a datetime (the
timestamps it stores), the wall clock as epoch seconds (hook events,
episodes, badges' ``eta_at``) and the monotonic clock (cadences that must
not jump with the wall clock). A ``Clock`` gives all three from one place,
so a test freezes or steps every reading together; freezing ``datetime``
alone left the monotonic ones running, which is how the quiet-fleet test
flaked.

In memory the wall clock is timezone-aware (local zone, with its offset).
On disk a timestamp stays naive local ISO, as 0.5.x wrote it (``stamp``):
an older overcode still running beside this one after an upgrade (its
supervisor daemon or API server), or one rolled back to, reads the same
files and subtracts their stamps from a naive ``datetime.now()``, which
raises on an aware value. Every reader here takes both:

- ``to_epoch`` / ``parse_time`` for comparisons: epoch seconds, or an
  aware datetime, never a naive/aware subtraction (which raises);
- ``from_iso`` for code that keeps naive local datetimes in memory (the
  views' renderers, the history rows): a drop-in for
  ``datetime.fromisoformat`` that converts an aware value to local time
  and drops the zone.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Optional, Protocol, Union, runtime_checkable

TimeValue = Union[datetime, str, int, float, None]


@runtime_checkable
class Clock(Protocol):
    """The time, three ways, from one source."""

    def wall(self) -> datetime:
        """The wall clock: an aware datetime in the local zone."""
        ...

    def time(self) -> float:
        """The wall clock in epoch seconds."""
        ...

    def monotonic(self) -> float:
        """Seconds on a clock that never steps (only differences mean anything)."""
        ...


class SystemClock:
    """The host's clocks."""

    def wall(self) -> datetime:
        return datetime.now().astimezone()

    def time(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()


SYSTEM_CLOCK = SystemClock()


def aware(value: datetime) -> datetime:
    """``value`` with a zone: a naive one is taken as local time."""
    return value if value.tzinfo is not None else value.astimezone()


def local_naive(value: datetime) -> datetime:
    """``value`` as a naive local time (an aware one converted first)."""
    if value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


def stamp(value: datetime) -> str:
    """``value`` as the ISO text a file stores: naive local time.

    What every persisted timestamp is written with (sessions.json, the
    daemon state files, the history CSVs), so 0.5.x code reading them
    keeps working; aware values stay in memory.
    """
    return local_naive(value).isoformat()


def stamp_epoch(text: TimeValue, at: TimeValue) -> Optional[float]:
    """Epoch seconds of stamp ``text``, placed by ``at`` (its epoch) when they agree.

    A naive stamp cannot say which pass of the repeated autumn hour it
    means, and ``to_epoch`` takes the first. Where a reading acts on the
    time (the engine's liveness, an oversight deadline) the writer stores
    the epoch beside the stamp; it is used only when it is one of the
    stamp's readings, so a stamp rewritten without it (an older overcode,
    a hand edit) is read on its own.
    """
    fallback = to_epoch(text)
    if fallback is None or isinstance(at, bool) or not isinstance(at, (int, float)):
        return fallback
    if isinstance(text, str):
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return fallback
        if parsed.tzinfo is None:
            readings = (parsed.replace(fold=0).timestamp(), parsed.replace(fold=1).timestamp())
            if any(abs(float(at) - r) < 1.0 for r in readings):
                return float(at)
    return fallback


def seconds_since(value: TimeValue, now: Optional[float] = None) -> Optional[float]:
    """Seconds from a stored or in-memory time to ``now`` (epoch; default the wall clock).

    Exact across a DST change: a naive datetime is placed by ``timestamp()``
    (``fold`` included), never by naive wall-clock subtraction. None when
    ``value`` is unknown or unreadable.
    """
    then = to_epoch(value)
    if then is None:
        return None
    return (time.time() if now is None else now) - then


def from_iso(text: str) -> datetime:
    """``datetime.fromisoformat`` that always returns a naive local time.

    Raises as ``fromisoformat`` does, so it drops into existing
    ``try``/``except (ValueError, TypeError)`` blocks.
    """
    return local_naive(datetime.fromisoformat(text))


def parse_time(value: TimeValue) -> Optional[datetime]:
    """An aware datetime from a stored timestamp, or None.

    Takes an ISO string (naive: local time), a datetime, or epoch seconds.
    """
    if value is None or value == "":
        return None
    try:
        if isinstance(value, datetime):
            return aware(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(value).astimezone()
        if isinstance(value, str):
            return aware(datetime.fromisoformat(value))
    except (ValueError, TypeError, OverflowError, OSError):
        return None
    return None


def to_epoch(value: TimeValue) -> Optional[float]:
    """Epoch seconds from a stored timestamp, or None.

    A naive value is local time (``timestamp()`` places it, honouring
    ``fold`` in the repeated hour); an aware one carries its offset.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, datetime):
        try:
            return value.timestamp()
        except (ValueError, OverflowError, OSError):
            return None
    parsed = parse_time(value)
    return parsed.timestamp() if parsed is not None else None
