"""
Detect whether Claude Code's /sandbox is currently ON for a given claude PID.

Claude's @anthropic-ai/sandbox-runtime starts two loopback listeners when
/sandbox is enabled (HTTP + SOCKS proxies, ports 127.0.0.1:<ephemeral>).
Both are torn down when the user toggles /sandbox off. So presence of
loopback listeners on the claude PID is a live signal for the toggle state.

macOS-only for now; Linux sandboxing uses Unix-socket bridges in addition
to loopback listeners and is not yet covered.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Dict, Iterable, Optional


def _run_lsof(pids: Iterable[int], timeout: float = 3.0) -> Optional[str]:
    """Run lsof once for all pids, returning stdout or None on error.

    Uses -F pn (machine-parseable: `p<pid>` section headers + `n<addr>`
    name lines). rc=1 is a valid "one or more pids had no matches" result
    on macOS's lsof; only treat higher codes or OSError as failure.
    """
    pid_list = sorted({int(p) for p in pids})
    if not pid_list:
        return ""
    try:
        result = subprocess.run(
            ["lsof", "-p", ",".join(str(p) for p in pid_list),
             "-a", "-i", "TCP", "-sTCP:LISTEN", "-F", "pn"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode not in (0, 1):
        return None
    return result.stdout


def _parse_loopback_counts(stdout: str) -> Dict[int, int]:
    """Parse `lsof -F pn` output into {pid: loopback_listener_count}.

    Lines are either `p<pid>` (starts a section) or `n<addr>` (a listener
    for the current pid). We count only addresses on localhost / 127.0.0.1.
    Pids absent from output implicitly have zero listeners; callers treat
    missing pids as 0.
    """
    counts: Dict[int, int] = {}
    current: Optional[int] = None
    for line in stdout.splitlines():
        if not line:
            continue
        tag, rest = line[0], line[1:]
        if tag == "p":
            try:
                current = int(rest)
            except ValueError:
                current = None
            if current is not None:
                counts.setdefault(current, 0)
        elif tag == "n" and current is not None:
            if "localhost:" in rest or "127.0.0.1:" in rest:
                counts[current] += 1
    return counts


def detect_sandbox_states(pids: Iterable[int]) -> Dict[int, Optional[bool]]:
    """Batch-detect sandbox state for many claude PIDs in one lsof call.

    Returns {pid: True|False|None}. None means lsof failed entirely
    (binary missing, timeout). False means lsof ran but the pid had
    fewer than 2 loopback listeners.
    """
    pid_list = [int(p) for p in pids]
    if not pid_list:
        return {}
    if sys.platform != "darwin":
        return {p: None for p in pid_list}
    stdout = _run_lsof(pid_list)
    if stdout is None:
        return {p: None for p in pid_list}
    counts = _parse_loopback_counts(stdout)
    return {p: counts.get(p, 0) >= 2 for p in pid_list}


def is_sandbox_enabled(claude_pid: Optional[int]) -> Optional[bool]:
    """Return True if /sandbox appears to be ON for this claude PID.

    Returns None when the signal can't be read (lsof missing, timeout, etc).
    Only implemented for macOS; returns None on other platforms. Prefer
    detect_sandbox_states() when querying more than one PID.
    """
    if claude_pid is None:
        return None
    return detect_sandbox_states([claude_pid]).get(claude_pid)
