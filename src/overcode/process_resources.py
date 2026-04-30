"""
Per-session CPU and RSS sampling from `ps`.

Used by the monitor daemon to populate cpu_percent and rss_bytes on each
session so the TUI can surface runaway agents (#451 follow-up). One
batched `ps -eo pid,ppid,%cpu,rss,args` call per tick walks every
process; we then BFS each claude process tree and sum across descendants
so e.g. a `tsc --watch` spawned under a claude bash tool counts toward
its parent agent.

Keeping this separate from doctor._snapshot_process_table because that
one is consumed by a different set of callers that don't need the
resource columns.
"""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple
import subprocess


@dataclass(frozen=True)
class ProcInfo:
    ppid: int
    cpu_pct: float   # sum of per-CPU percent (so >100 means multi-core)
    rss_kb: int      # resident set size in kilobytes
    argv: str


def snapshot_processes() -> Dict[int, ProcInfo]:
    """Return {pid: ProcInfo} for every visible process.

    Uses `ps -eo pid,ppid,%cpu,rss,args`. Returns an empty dict on any
    failure — callers treat that as "no data, skip this tick".
    """
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,%cpu=,rss=,args="],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return {}
    if result.returncode != 0:
        return {}
    table: Dict[int, ProcInfo] = {}
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
            cpu = float(parts[2])
            rss = int(parts[3])
        except ValueError:
            continue
        argv = parts[4] if len(parts) >= 5 else ""
        table[pid] = ProcInfo(ppid=ppid, cpu_pct=cpu, rss_kb=rss, argv=argv)
    return table


def build_children_index(snapshot: Dict[int, ProcInfo]) -> Dict[int, List[int]]:
    """Build {ppid -> [child pids]} from a process snapshot."""
    children: Dict[int, List[int]] = {}
    for pid, info in snapshot.items():
        children.setdefault(info.ppid, []).append(pid)
    return children


def descendant_pids(
    root_pid: int,
    children: Dict[int, List[int]],
    max_depth: int = 6,
) -> List[int]:
    """BFS the process tree under root_pid, inclusive of root."""
    seen: set[int] = {root_pid}
    frontier: List[Tuple[int, int]] = [(root_pid, 0)]
    result: List[int] = [root_pid]
    while frontier:
        pid, depth = frontier.pop()
        if depth >= max_depth:
            continue
        for child in children.get(pid, []):
            if child in seen:
                continue
            seen.add(child)
            result.append(child)
            frontier.append((child, depth + 1))
    return result


def aggregate_tree(
    root_pid: int,
    snapshot: Dict[int, ProcInfo],
    children: Optional[Dict[int, List[int]]] = None,
) -> Tuple[float, int]:
    """Sum (cpu_pct, rss_bytes) over root_pid and all descendants.

    Returns (0.0, 0) when root_pid isn't in the snapshot.
    """
    if root_pid not in snapshot:
        return 0.0, 0
    idx = children if children is not None else build_children_index(snapshot)
    cpu = 0.0
    rss_kb = 0
    for pid in descendant_pids(root_pid, idx):
        info = snapshot.get(pid)
        if info is None:
            continue
        cpu += info.cpu_pct
        rss_kb += info.rss_kb
    return cpu, rss_kb * 1024
