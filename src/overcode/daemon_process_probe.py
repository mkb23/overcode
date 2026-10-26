"""Agent processes for the monitor daemon: CPU / RSS and the /sandbox toggle.

Both read the process table: CPU and RSS summed over each agent's process
tree, and the agent's loopback listeners for /sandbox (#451). One
``ps -eo pid,ppid,%cpu,rss,args`` per tick serves both (``ProcessProbe.table``);
they used to take two different ``ps`` snapshots.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Mapping, Optional

from .backends import BackendCapability, session_supports
from .session_manager import PendingUpdates
from .tmux_utils import PaneInfo, pane_for_window


@dataclass(frozen=True)
class ProcessTable:
    """One ``ps`` snapshot with the indexes the agent lookup walks."""

    snapshot: dict  # pid -> process_resources.ProcInfo
    children: Dict[int, List[int]]  # ppid -> child pids
    argv_by_pid: Dict[int, str]


class ProcessProbe:
    """The process table, once per tick, and what each sync reads from it."""

    def __init__(self):
        self._table_at: Optional[datetime] = None
        self._table: Optional[ProcessTable] = None

    def table(self, now: datetime) -> Optional[ProcessTable]:
        """The process table as of ``now``'s tick (one ``ps``); None when ``ps`` failed."""
        if self._table_at != now:
            from .process_resources import build_children_index, snapshot_processes

            self._table_at = now
            snapshot = snapshot_processes()
            if not snapshot:
                self._table = None
            else:
                # build_children_index walks the whole snapshot, so the
                # argv_by_pid shape doctor expects is derived on the fly.
                self._table = ProcessTable(
                    snapshot=snapshot,
                    children=build_children_index(snapshot),
                    argv_by_pid={pid: info.argv for pid, info in snapshot.items()},
                )
        return self._table

    def latest(self) -> Optional[ProcessTable]:
        """The last table taken, without taking one (None: none yet, or ``ps`` failed)."""
        return self._table

    @staticmethod
    def _agent_pid(table: ProcessTable, session, pane: PaneInfo) -> Optional[int]:
        from .doctor import (
            find_agent_process, session_process_argv_markers, session_process_basenames,
        )

        pid, _ = find_agent_process(
            pane.pane_pid, table.children, table.argv_by_pid,
            session_process_basenames(session), session_process_argv_markers(session),
        )
        return pid

    def sample_resources(
        self, sessions: list, table: ProcessTable, panes: Mapping[str, PaneInfo],
        pending: PendingUpdates,
    ) -> None:
        """Stage CPU and RSS for each agent's claude process tree.

        cpu_percent (sum of per-CPU %) and rss_bytes (sum of resident set
        size) are summed across the claude process and every descendant.
        Tools spawned under a bash call (e.g. a runaway `tsc --watch`)
        therefore show up on the parent agent's row.
        """
        from .process_resources import aggregate_tree

        for session in sessions:
            if getattr(session, "is_remote", False):
                continue
            pane = pane_for_window(panes, session.tmux_window)
            if pane is None:
                continue
            claude_pid = self._agent_pid(table, session, pane)
            if claude_pid is None:
                # Reset to 0 so a dead/missing agent doesn't pin a stale reading.
                if session.cpu_percent or session.rss_bytes:
                    pending.update_session(
                        session.id, cpu_percent=0.0, rss_bytes=0,
                    )
                continue
            cpu, rss = aggregate_tree(claude_pid, table.snapshot, table.children)
            # Only stage when the value moved meaningfully — avoids a JSON
            # write every 5s for an idle agent whose CPU is drifting by 0.1%.
            # Everything staged lands in the tick's single write.
            if (
                abs(cpu - session.cpu_percent) >= 1.0
                or abs(rss - session.rss_bytes) >= 1024 * 1024  # 1 MiB
            ):
                pending.update_session(
                    session.id, cpu_percent=cpu, rss_bytes=rss,
                )

    def sample_sandbox(
        self, sessions: list, table: ProcessTable, panes: Mapping[str, PaneInfo],
        pending: PendingUpdates,
    ) -> None:
        """Stage the /sandbox toggle state read from claude's listeners (#451)."""
        from .sandbox_detect import detect_sandbox_states

        # Gather all local claude PIDs with one lsof call (#451 optimization).
        session_pids: dict = {}  # session.id -> claude_pid
        for session in sessions:
            if getattr(session, "is_remote", False):
                continue
            # The loopback-listener heuristic only means anything for
            # backends that have a sandbox toggle.
            if not session_supports(session, BackendCapability.SANDBOX_PROBE):
                continue
            pane = pane_for_window(panes, session.tmux_window)
            if pane is None:
                continue
            claude_pid = self._agent_pid(table, session, pane)
            if claude_pid is not None:
                session_pids[session.id] = claude_pid
        states = detect_sandbox_states(session_pids.values())
        for session in sessions:
            if session.id not in session_pids:
                continue
            detected = states.get(session_pids[session.id])
            if detected != session.sandbox_enabled:
                pending.update_session(session.id, sandbox_enabled=detected)
