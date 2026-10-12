"""Wall-clock cadences of the monitor daemon's periodic work.

Each periodic sync (stats, session ids, skills, sandbox, CPU/RSS, git, burn,
history rotation, models.dev refresh, housekeeping) runs when its interval
has passed since it last ran, on the tick's clock. A ``Cadence`` holds the
interval and the last run; the caller marks a run where the work counts as
done, so a sync that raises part-way is tried again next tick.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterator, Optional, Tuple

from .monitor_daemon_core import should_sync_stats


class Cadence:
    """Due once ``interval`` seconds have passed since the last ``mark``.

    Never marked, it is due at once. ``elapsed_seconds`` measures the gap,
    so a daylight-saving change neither stalls nor rushes it.
    """

    __slots__ = ("interval", "last")

    def __init__(self, interval: float):
        self.interval = interval
        self.last: Optional[datetime] = None

    def due(self, now: datetime, interval: Optional[float] = None) -> bool:
        """Whether to run at ``now``; ``interval`` overrides the usual one."""
        return should_sync_stats(self.last, now, self.interval if interval is None else interval)

    def mark(self, now: datetime) -> None:
        """The work ran at ``now``."""
        self.last = now

    def __repr__(self) -> str:
        return f"Cadence({self.interval!r}, last={self.last!r})"


@dataclass
class DaemonCadences:
    """The daemon's periodic syncs, by name."""

    stats: Cadence  # transcript stats; the interval depends on attendance
    session_ids: Cadence  # post-/clear session id detection (#116)
    skills: Cadence  # installed skills (#252)
    sandbox: Cadence  # /sandbox toggle from claude listeners (#451)
    resources: Cadence  # CPU / RSS per agent process tree
    git: Cadence  # git diff / untracked, attended only
    burn: Cadence  # burn over the windows views asked for, attended only
    history_rotation: Cadence  # agent_status_history.csv rotation (#465, #468)
    model_metadata: Cadence  # models.dev catalog auto-refresh (#473)
    housekeeping: Cadence  # auto-archive, untracked count, archive, job pruning

    def items(self) -> Iterator[Tuple[str, Cadence]]:
        return iter(vars(self).items())
