"""Idle stages: how long a plain idle agent has waited, and what follows.

A plain idle agent (needs input: its turn is over, nothing pending, the
📥 ``awaiting_input`` badge) moves through stages:

* fresh, for ``idle.drowsy_after`` (default 1h): 📥, as before;
* drowsy, after that: 😴, dim; waiting a while, no urgency;
* after ``idle.sleep_after`` (default never) the engine puts it to sleep
  (as ``z`` does) and marks it ``asleep_auto``; such an agent wakes by
  itself when it works again. A ``z`` sleep stays until ``z``.

The engine publishes when the idle began (``idle_since``, epoch seconds,
still between ticks); views derive the stage from it and the config. An
approval (a question included), a heartbeat wait, a child at rest, an
error or an unknown status is never idle.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Optional

from .status_constants import (
    STATUS_COLOR_RED,
    STATUS_WAITING_APPROVAL,
    STATUS_WAITING_USER,
    StatusBadge,
    StatusDetail,
    is_green_status,
)

DROWSY_AFTER_DEFAULT = 3600.0
SLEEP_AFTER_DEFAULT: Optional[float] = None  # never
_NEVER = ("never", "off", "none", "")

# What the visibility dialog offers: (seconds or None, shown as)
DROWSY_CHOICES = ((600.0, "10m"), (1800.0, "30m"), (3600.0, "1h"), (7200.0, "2h"),
                  (None, "never"))
SLEEP_CHOICES = ((None, "never"), (21600.0, "6h"), (86400.0, "1d"), (259200.0, "3d"),
                 (604800.0, "7d"))


def parse_after(value: Any, default: Optional[float]) -> Optional[float]:
    """Seconds from ``30m``, ``2h``, ``3d`` or a number; None for never/off/false.

    Anything else is the default (and the caller warns)."""
    from .child_presets import parse_duration
    if value is None:
        return default
    if value is False or (isinstance(value, str) and value.strip().lower() in _NEVER):
        return None
    try:
        seconds = parse_duration(value)
    except ValueError:
        return default
    return seconds if seconds > 0 else None


def format_after(seconds: Optional[float]) -> str:
    """How config.yaml says it: ``never``, ``45m``, ``2h``, ``3d``."""
    if seconds is None:
        return "never"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size and seconds % size == 0:
            return f"{int(seconds // size)}{unit}"
    return f"{int(seconds)}s"


def plain_idle(status: str, detail: Optional[StatusDetail]) -> bool:
    """Needs input and nothing else: the 📥 awaiting_input verdict."""
    if status != STATUS_WAITING_USER or detail is None:
        return False
    return (detail.color == STATUS_COLOR_RED and bool(detail.badges)
            and detail.badges[0].kind == "awaiting_input")


def idle_since(status: str, detail: Optional[StatusDetail],
               status_since: Optional[float]) -> Optional[float]:
    """When the plain idle began (the status's own start), None if not idle."""
    if not plain_idle(status, detail) or status_since is None:
        return None
    return float(status_since)


def is_drowsy(idle_at: Optional[float], now: float, drowsy_after: Optional[float]) -> bool:
    return (isinstance(idle_at, (int, float)) and drowsy_after is not None
            and now - idle_at >= drowsy_after)


def staged_detail(detail: Optional[StatusDetail], idle_at: Optional[float], now: float,
                  drowsy_after: Optional[float]) -> Optional[StatusDetail]:
    """``detail`` with 📥 turned 😴 once the agent has been idle drowsy_after."""
    if (detail is None or not detail.badges or detail.badges[0].kind != "awaiting_input"
            or not is_drowsy(idle_at, now, drowsy_after)):
        return detail
    return replace(detail, badges=[StatusBadge(kind="drowsy")] + list(detail.badges[1:]))


def should_auto_sleep(session, idle_at: Optional[float], now: float,
                      sleep_after: Optional[float]) -> bool:
    """A root agent, awake, with no heartbeat to come, idle sleep_after or more."""
    if sleep_after is None or idle_at is None or now - idle_at < sleep_after:
        return False
    if getattr(session, "is_asleep", False) or getattr(session, "parent_session_id", None):
        return False
    if getattr(session, "heartbeat_enabled", False) and not getattr(session, "heartbeat_paused", False):
        return False
    return True


def should_auto_wake(session, status: str) -> bool:
    """An agent the engine put to sleep that works (or asks) again."""
    return (bool(getattr(session, "is_asleep", False)) and bool(getattr(session, "asleep_auto", False))
            and (is_green_status(status) or status == STATUS_WAITING_APPROVAL))

