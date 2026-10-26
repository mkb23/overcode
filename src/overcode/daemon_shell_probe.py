"""The I/O behind background-shell classification, for the monitor daemon (#515).

``background_shells.survey`` is pure; the hook detector calls this probe
for the three things it can't know itself, and only for an agent whose
last Stop listed running shells:

* ``processes``: the Bash-tool shells in the process table for the agent.
  It reuses the table the resource sampling took (at most one cadence
  old: shells change rarely), so it adds no ``ps``.
* ``target_state``: whether the overcode agent or job a watcher watches is
  still alive, from the daemon's last published states and ``jobs.json``
  (read once per tick, only when asked).
* ``listening``: whether anything under a shell holds a listening TCP
  socket. One ``lsof`` per shell the rules could not place, repeated at
  most every ``LISTEN_RECHECK_SECONDS``.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from .background_shells import (
    TARGET_ALIVE, TARGET_ENDED, TARGET_MISSING, ShellProc, shell_procs,
)

# A shell the rules call batch is asked about sockets again this often
LISTEN_RECHECK_SECONDS = 60.0
# Agent statuses after which a watcher's target has ended
_AGENT_ENDED = frozenset({"done", "terminated"})


def _listening_pids(pids: Iterable[int]) -> Optional[set]:
    """The pids among ``pids`` with a listening TCP socket (None: lsof failed)."""
    from .sandbox_detect import _run_lsof

    out = _run_lsof(pids)
    if out is None:
        return None
    found, current = set(), None
    for line in out.splitlines():
        if line.startswith("p"):
            try:
                current = int(line[1:])
            except ValueError:
                current = None
        elif line.startswith("n") and current is not None:
            found.add(current)
    return found


def _iso_epoch(value) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


class ShellProbe:
    """Process, target and socket lookups for one daemon."""

    def __init__(self, table: Callable[[], Optional[object]],
                 agent_pid: Callable[[object, object], Optional[int]],
                 load_jobs: Optional[Callable[[], Mapping[str, dict]]] = None,  # name -> job dict
                 listening_pids: Callable[[Iterable[int]], Optional[set]] = _listening_pids,
                 clock: Callable[[], float] = time.monotonic):
        self._table = table
        self._agent_pid = agent_pid
        self._load_jobs = load_jobs
        self._listening_pids = listening_pids
        self._clock = clock
        self._statuses: Dict[str, str] = {}
        self._jobs: Optional[Dict[str, dict]] = None
        self._shells: Optional[Tuple[object, List[ShellProc]]] = None
        self._listen: Dict[int, Tuple[float, bool]] = {}

    def begin_tick(self, statuses: Mapping[str, str]) -> None:
        """Start a tick: ``statuses`` is each agent's last published status, by name."""
        self._statuses = dict(statuses)
        self._jobs = None

    def _all_shells(self, table) -> List[ShellProc]:
        if self._shells is None or self._shells[0] is not table:
            self._shells = (table, shell_procs(table.argv_by_pid, table.children,
                                               table.argv_by_pid.keys()))
        return self._shells[1]

    def processes(self, session) -> Optional[List[ShellProc]]:
        """The agent's shells: those under its CLI, then any elsewhere in the
        table (Claude's daemon can host them outside the agent's tree)
        whose command no other shell shares. None: no process table."""
        table = self._table()
        if table is None:
            return None
        from .process_resources import descendant_pids

        everything = self._all_shells(table)
        pid = self._agent_pid(table, session)
        mine: List[ShellProc] = []
        if pid is not None:
            under = set(descendant_pids(pid, table.children, max_depth=8))
            mine = [p for p in everything if p.pid in under]
        claimed = {p.pid for p in mine}
        by_command: Dict[str, List[ShellProc]] = {}
        for p in everything:
            if p.pid not in claimed:
                by_command.setdefault(p.command, []).append(p)
        mine += [ps[0] for ps in by_command.values() if len(ps) == 1]
        return mine

    def target_state(self, kind: str, name: str) -> Tuple[str, Optional[float]]:
        """(alive | ended | missing, when it ended if known) for a watcher's target."""
        if kind == "agent":
            status = self._statuses.get(name)
            if status is None:
                return TARGET_MISSING, None
            return (TARGET_ENDED if status in _AGENT_ENDED else TARGET_ALIVE), None
        if kind == "job":
            if self._load_jobs is None:
                return TARGET_ALIVE, None
            if self._jobs is None:
                try:
                    self._jobs = dict(self._load_jobs())
                except Exception:  # an unreadable jobs file says nothing
                    return TARGET_ALIVE, None
            job = self._jobs.get(name)
            if job is None:
                return TARGET_MISSING, None
            if job.get("status", "running") == "running":
                return TARGET_ALIVE, None
            return TARGET_ENDED, _iso_epoch(job.get("end_time"))
        return TARGET_ALIVE, None

    def listening(self, proc: ShellProc) -> bool:
        """Whether anything under the shell listens on TCP (cached per shell)."""
        now = self._clock()
        hit = self._listen.get(proc.pid)
        if hit is not None and (hit[1] or now - hit[0] < LISTEN_RECHECK_SECONDS):
            return hit[1]
        found = self._listening_pids(proc.pids)
        result = bool(found)
        if len(self._listen) > 500:
            self._listen.clear()
        self._listen[proc.pid] = (now, result)
        return result
