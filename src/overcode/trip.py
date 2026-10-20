"""Trip counters: an agent's spend since its trip was last reset.

Like a car's trip meter. A reset stores the agent's cumulative spend on its
record (``Session.trip_base``, ``trip_reset_at``); the engine publishes the
spend since (``SessionDaemonState.trip``) and every row's spend cells (TOK,
ENRG, $, Q%) show it, marked ↺. The odometer keeps counting everything:
the record's stats, the ledger, billing codes, ``overcode billing`` and
budgets (a reset never gives an agent more budget).

Q% restarts each quota week by itself, so its base holds only within the
week it was taken in: once the week rolls over, the trip is this week's pp.
"""

from datetime import datetime
from typing import Any, Iterable, List, Mapping, Optional

# The cumulative spend a trip subtracts, as SessionStats / SessionDaemonState name it
TRIP_FIELDS = (
    "estimated_cost_usd", "estimated_energy_j",
    "input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens",
)
_FLOAT_FIELDS = ("estimated_cost_usd", "estimated_energy_j")

# Two quota-week starts closer than this are the same week (a reading's
# reset time can drift by seconds between polls)
_SAME_WEEK_SLACK = 3600.0


def _get(source: Any, name: str):
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _num(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def week_start(usage: Optional[Mapping], now: float) -> Optional[float]:
    """The current quota week's start from the fleet's ``usage`` reading, None without one."""
    from .quota_engine import WEEK

    r7 = usage.get("r7") if isinstance(usage, Mapping) else None
    if not isinstance(r7, (int, float)) or r7 <= now - WEEK:
        return None
    return float(r7) - WEEK


def base_of(stats: Any, quota: Optional[Mapping] = None,
            quota_week: Optional[float] = None) -> dict:
    """The trip base: ``stats``' cumulative spend now, and the Q% points with their week."""
    base = {name: _num(_get(stats, name)) for name in TRIP_FIELDS}
    pp = quota.get("pp7d") if isinstance(quota, Mapping) else None
    if isinstance(pp, (int, float)):
        base["pp7d"] = float(pp)
        base["quota_week"] = quota_week
    return base


def same_week(base: Mapping, reset_at: Optional[float], current_week: Optional[float]) -> bool:
    """Whether the base's Q% points are this quota week's.

    By the week recorded with them; a base without one (no reading at the
    reset) by when the reset happened.
    """
    if current_week is None:
        return True  # no reading now: nothing says the week moved
    week = base.get("quota_week")
    if isinstance(week, (int, float)):
        return abs(week - current_week) < _SAME_WEEK_SLACK
    return isinstance(reset_at, (int, float)) and reset_at >= current_week


def trip_view(session: Any, totals: Any, quota: Optional[Mapping] = None,
              current_week: Optional[float] = None) -> Optional[dict]:
    """What ``session``'s trip counter reads, None if it was never reset.

    ``totals`` holds the cumulative spend (a SessionStats or a
    SessionDaemonState); ``quota`` the agent's quota view. Values never go
    below 0 (a recomputed total can come in under the base).
    """
    base = getattr(session, "trip_base", None)
    reset_at = getattr(session, "trip_reset_at", None)
    if not isinstance(base, Mapping) or not reset_at:
        return None
    view: dict = {"reset_at": reset_at}
    for name in TRIP_FIELDS:
        value = max(0.0, _num(_get(totals, name)) - _num(base.get(name)))
        view[name] = round(value, 4) if name in _FLOAT_FIELDS else int(value)
    pp = quota.get("pp7d") if isinstance(quota, Mapping) else None
    if isinstance(pp, (int, float)):
        if same_week(base, _epoch(reset_at), current_week):
            pp = max(0.0, pp - _num(base.get("pp7d")))
        view["pp7d"] = round(pp, 2)
    return view


def _epoch(text: Any) -> Optional[float]:
    if not isinstance(text, str):
        return None
    try:
        from .clock import to_epoch
        return to_epoch(text)
    except Exception:
        return None


def trip_tokens(trip: Mapping) -> int:
    """The TOK column's count of a trip (input + output, as AgentSessionStats.total_tokens)."""
    return int(_num(trip.get("input_tokens")) + _num(trip.get("output_tokens")))


def subtree_trip_cost(members: Iterable[Any]) -> Optional[float]:
    """SUB$ on trips: the members' $ as their rows show it (trip where reset).

    None when no member was reset: SUB$ is then the plain total.
    """
    total, any_trip = 0.0, False
    for state in members:
        trip = getattr(state, "trip", None)
        if isinstance(trip, Mapping):
            any_trip = True
            total += _num(trip.get("estimated_cost_usd"))
        else:
            total += _num(getattr(state, "estimated_cost_usd", 0.0))
    return round(total, 4) if any_trip else None


# -- resetting ---------------------------------------------------------------

def tree_ids(manager, session_id: str) -> List[str]:
    """``session_id`` and its overcode descendants (delegated subagents
    are inside the agent's own spend already)."""
    return [session_id] + [d.id for d in manager.get_descendants(session_id)]


def reset(manager, ids: Iterable[str], tmux_session: Optional[str] = None,
          now: Optional[datetime] = None) -> List[str]:
    """Reset the trips of ``ids``; the ids reset (gone ones are skipped).

    The spend base is the record's stats; the Q% base is the engine's last
    published quota view, from its state file. The engine is woken to
    publish the zeroed rows at once.
    """
    from .monitor_daemon_state import get_monitor_daemon_state
    from .settings import signal_activity

    now = now or datetime.now().astimezone()
    quotas: dict = {}
    week = None
    try:
        state = get_monitor_daemon_state(tmux_session)
    except Exception:
        state = None
    if state is not None:
        week = week_start(state.usage, now.timestamp())
        quotas = {s.session_id: s.quota for s in state.sessions if isinstance(s.quota, dict)}
    done = manager.reset_trips(
        list(ids), now.isoformat(), {sid: (q, week) for sid, q in quotas.items()})
    if done:
        signal_activity(tmux_session)
    return done


def describe(trip: Optional[Mapping]) -> str:
    """'since 14:02' / 'since 3 Oct 14:02' for a trip's reset time."""
    if not isinstance(trip, Mapping):
        return ""
    at = _epoch(trip.get("reset_at"))
    if at is None:
        return ""
    when = datetime.fromtimestamp(at)
    if when.date() == datetime.now().date():
        return f"since {when:%H:%M}"
    return f"since {when.day} {when:%b %H:%M}"
