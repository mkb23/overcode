"""
Per-session CPU and RSS sampling from `ps`.

Used by the monitor daemon to populate cpu_percent and rss_bytes on each
session so the TUI can surface runaway agents (#451 follow-up). One
batched `ps -eo pid,ppid,%cpu,rss,args` call per tick walks every
process; we then BFS each agent process tree and sum across descendants
so e.g. a `tsc --watch` spawned under an agent's bash tool counts toward
its parent agent.

Keeping this separate from doctor._snapshot_process_table because that
one is consumed by a different set of callers that don't need the
resource columns.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
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


def shell_in_foreground(pid: Optional[int]) -> Optional[bool]:
    """Whether the shell ``pid`` (a tmux pane's) owns its terminal: at its prompt,
    with nothing running in the foreground (#549).

    The terminal's foreground process group (``tpgid``) is the shell's own
    group exactly when no command is running in front of it: the kernel's
    answer, not the screen's. Read from ``/proc/<pid>/stat`` on Linux
    (minimal containers often have no ``ps``), else from ``ps`` (macOS).
    None when it can't be told (no pid, the process is gone).
    """
    if not pid:
        return None
    ids = _proc_stat_groups(pid)
    if ids is None:
        ids = _ps_groups(pid)
    if ids is None:
        return None
    pgid, tpgid = ids
    if tpgid <= 0:
        return None  # no controlling terminal
    return pgid == tpgid


def _proc_stat_groups(pid: int) -> Optional[Tuple[int, int]]:
    """``(pgrp, tpgid)`` from ``/proc/<pid>/stat`` (Linux), or None."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as f:
            stat = f.read()
    except OSError:
        return None
    # pid (comm) state ppid pgrp session tty_nr tpgid ...; comm may hold
    # spaces and parentheses, so the fields start after the last ")"
    fields = stat.rpartition(")")[2].split()
    try:
        return int(fields[2]), int(fields[5])
    except (IndexError, ValueError):
        return None


def _ps_groups(pid: int) -> Optional[Tuple[int, int]]:
    """``(pgid, tpgid)`` from ``ps`` (macOS, or a Linux without /proc), or None."""
    try:
        result = subprocess.run(["ps", "-o", "pgid=,tpgid=", "-p", str(pid)],
                                capture_output=True, text=True, timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        return None
    try:
        pgid, tpgid = (int(v) for v in result.stdout.split())
    except ValueError:
        return None
    return pgid, tpgid
