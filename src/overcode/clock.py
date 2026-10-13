"""One clock for the monitor daemon, the status detectors and history.

The daemon reads the time in three ways: the wall clock as a datetime (the
timestamps it stores), the wall clock as epoch seconds (hook events,
episodes, badges' ``eta_at``) and the monotonic clock (cadences that must
not jump with the wall clock). A ``Clock`` gives all three from one place,
so a test freezes or steps every reading together; freezing ``datetime``
alone left the monotonic ones running, which is how the quiet-fleet test
flaked.

Timestamps written from here on are timezone-aware (local zone, with its
offset). Files written before carry naive local times, so every reader
takes both:

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
