"""The TUI's reading of what the engine publishes (docs/design/engine-0.6.md).

Since 0.6.0 the TUI computes nothing about agents: the monitor daemon (the
engine) publishes each agent's state on engine.sock as a flat mapping of
``SessionDaemonState`` fields (an "agent view"), and the TUI renders it.
These are the pure translations from an agent view to what a summary row
draws: no file reads, no pane captures, no status detection. Times in a
view are absolute (epoch seconds), so the countdowns and durations are
worked out here, at render time.
"""

from __future__ import annotations

import copy
import dataclasses
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .history_reader import AgentSessionStats
from .status_constants import (
    STATUS_ASLEEP,
    STATUS_DONE,
    STATUS_TERMINATED,
    StatusBadge,
    StatusDetail,
)
from .tui_logic import WindowBurnStats

AgentView = Mapping[str, Any]

# Lifecycle states hold the recorded colour where it was; their time in
# state is the status's own.
_LIFECYCLE = frozenset({STATUS_TERMINATED, STATUS_ASLEEP, STATUS_DONE})

# Session fields the engine keeps current (sessions.json carries the same
# values, written by the same daemon, a little later).
_SESSION_FIELDS = ("model", "effort", "cpu_percent", "rss_bytes", "repo_name", "branch",
                   "pr_number")

# SessionStats fields the engine publishes under the same name
_STATS_FIELDS = (
    "interaction_count", "estimated_cost_usd", "estimated_energy_j", "steers_count",
    "input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens",
    "current_context_tokens",
)

# The pane-derived counts, as published and as the row widget names them
PANE_FIELDS = ("background_bash_count", "bash_count_ambiguous", "live_subagent_count",
               "auto_accept_mode")


def engine_socket_path(tmux_session: str) -> Path:
    """engine.sock for ``tmux_session``: beside the daemon's state file."""
    from .engine_socket import socket_path
    from .settings import get_monitor_daemon_state_path

    return socket_path(get_monitor_daemon_state_path(tmux_session).parent)


def _iso(epoch: Optional[float]) -> Optional[str]:
    if not isinstance(epoch, (int, float)):
        return None
    return datetime.fromtimestamp(epoch).isoformat()


def session_stats(base, view: AgentView):
    """``base`` (a SessionStats from sessions.json) with the engine's values.

    The time accumulators come from the view's base (``time_base`` as of
    ``time_base_at``, in ``current_status``); ``get_current_state_times``
    adds the time since, exactly as the daemon accumulates it.
    """
    if not view:
        return base
    overrides: Dict[str, Any] = {
        name: view[name] for name in _STATS_FIELDS if view.get(name) is not None
    }
    if view.get("current_status"):
        overrides["current_state"] = view["current_status"]
    if "current_activity" in view:
        overrides["current_task"] = view.get("current_activity") or ""
    if view.get("status_since"):
        overrides["state_since"] = view["status_since"]
    time_base = view.get("time_base")
    if isinstance(time_base, list) and len(time_base) == 3 and view.get("time_base_at"):
        overrides["green_time_seconds"] = time_base[0]
        overrides["non_green_time_seconds"] = time_base[1]
        overrides["sleep_time_seconds"] = time_base[2]
        overrides["last_time_accumulation"] = _iso(view["time_base_at"])
    return dataclasses.replace(base, **overrides) if overrides else base


def session_with_view(session, view: AgentView):
    """A copy of ``session`` with the engine's values (stats included).

    ``session`` is the shared, read-only sessions.json snapshot object, so
    the overlay is a shallow copy; nothing writes through it.
    """
    if not view:
        return session
    out = copy.copy(session)
    for name in _SESSION_FIELDS:
        if name in view:
            setattr(out, name, view[name])
    out.stats = session_stats(session.stats, view)
    return out


def agent_stats(view: AgentView) -> Optional[AgentSessionStats]:
    """The transcript stats the token/context/work columns render.

    None when the backend reports no stats (the columns show "-") or the
    engine has not synced them yet.
    """
    if not view or not view.get("stats_available"):
        return None
    median = view.get("work_median_seconds") or 0.0
    return AgentSessionStats(
        interaction_count=view.get("interaction_count") or 0,
        input_tokens=view.get("input_tokens") or 0,
        output_tokens=view.get("output_tokens") or 0,
        cache_creation_tokens=view.get("cache_creation_tokens") or 0,
        cache_read_tokens=view.get("cache_read_tokens") or 0,
        work_times=[median] if median > 0 else [],
        current_context_tokens=view.get("current_context_tokens") or 0,
        live_subagent_count=view.get("file_subagent_count") or 0,
        model=view.get("model"),
        effort=view.get("effort"),
        last_command=view.get("last_command"),
        reported_context_window=view.get("context_window"),
    )


def status_detail(view: AgentView, now: float) -> Optional[StatusDetail]:
    """The 4-colour detail with its badges; a badge's ``eta_at`` becomes a countdown."""
    detail = view.get("status_detail") if view else None
    if not isinstance(detail, dict) or not detail.get("color"):
        return None
    badges = []
    for b in detail.get("badges") or ():
        if not isinstance(b, dict) or not b.get("kind"):
            continue
        eta_at = b.get("eta_at")
        badges.append(StatusBadge(
            kind=b["kind"],
            label=b.get("label"),
            count=b.get("count", 1),
            eta_seconds=max(0.0, eta_at - now) if isinstance(eta_at, (int, float)) else None,
        ))
    return StatusDetail(detail["color"], badges, detail.get("legacy_status") or "")


def status_changed_at(view: AgentView) -> Optional[datetime]:
    """When the row's time-in-state counts from.

    The recorded episode's start; while the live colour is on an excursion
    from it (not yet G seconds long), the excursion's own start, so the
    timer shows what is on screen. Lifecycle states have no colour: their
    status's own start.
    """
    if not view:
        return None
    if view.get("current_status") not in _LIFECYCLE:
        live, episode = view.get("live_colour"), view.get("episode_colour")
        ts = view.get("live_since") if live and live != episode else view.get("episode_start")
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts)
    since = view.get("status_since")
    if since:
        try:
            return datetime.fromisoformat(since)
        except (TypeError, ValueError):
            return None
    return None


def git_diff(view: AgentView) -> Optional[tuple]:
    diff = view.get("git_diff") if view else None
    return tuple(diff) if isinstance(diff, (list, tuple)) and len(diff) == 3 else None


def burn_key(hours: float) -> str:
    """How the engine keys a burn window (engine_socket.set_burn_window hours)."""
    return str(float(hours))


def window_burn(view: AgentView, hours: float) -> Optional[WindowBurnStats]:
    """This agent's spend over the burn window, None before the engine has it."""
    if not view or not hours or hours <= 0:
        return None
    entry = (view.get("burn") or {}).get(burn_key(hours))
    if not isinstance(entry, dict):
        return None
    return WindowBurnStats(
        window_hours=hours,
        input_tokens=entry.get("input_tokens", 0),
        output_tokens=entry.get("output_tokens", 0),
        cache_creation_tokens=entry.get("cache_creation_tokens", 0),
        cache_read_tokens=entry.get("cache_read_tokens", 0),
        cost_usd=entry.get("cost_usd", 0.0),
        energy_j=entry.get("energy_j", 0.0),
    )


def fleet_burn(agents: Mapping[str, AgentView], hours: float) -> Optional[WindowBurnStats]:
    """The status bar's aggregate: the agents' burn summed (asleep agents have none)."""
    if not hours or hours <= 0:
        return None
    total = WindowBurnStats(window_hours=hours)
    for sid, view in agents.items():
        burn = window_burn(view, hours)
        if burn is None:
            continue
        total.input_tokens += burn.input_tokens
        total.output_tokens += burn.output_tokens
        total.cache_creation_tokens += burn.cache_creation_tokens
        total.cache_read_tokens += burn.cache_read_tokens
        total.cost_usd += burn.cost_usd
        total.energy_j += burn.energy_j
        total.per_session[sid] = burn
    return total


def is_unvisited(view: AgentView, visited_here: Optional[float] = None) -> bool:
    """Needs a look: in an input-needed stretch that began after the last visit.

    ``visited_here`` is a visit this TUI made that the engine may not have
    published yet (it applies visits on its next tick).
    """
    since = view.get("input_needed_since") if view else None
    if not isinstance(since, (int, float)):
        return False
    visits = [v for v in (view.get("visited_at"), visited_here) if isinstance(v, (int, float))]
    return not visits or since > max(visits)


def monitor_state(fleet: Mapping[str, Any], agents: Mapping[str, AgentView]):
    """A ``MonitorDaemonState`` for the status bar, built from a snapshot."""
    from .monitor_daemon_state import MonitorDaemonState

    data = {k: v for k, v in fleet.items() if k != "presence_idle_since"}
    data["sessions"] = list(agents.values())
    return MonitorDaemonState.from_dict(data)
