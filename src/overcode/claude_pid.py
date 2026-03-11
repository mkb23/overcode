"""
PID-based Claude session ID discovery.

Maps tmux pane → Claude process PID → Claude session ID to prevent
cross-contamination when multiple agents share the same working directory.

Without this, sync_session_id uses get_current_session_id_for_directory()
which returns the most-recent sessionId for a directory — when agents share
a directory, each agent picks up the other's sessionId, causing
double-counted tokens.
"""

import re
import subprocess
from typing import Optional, Set


def get_claude_pid_from_pane_pid(pane_pid: int) -> Optional[int]:
    """Find the Claude child process of a tmux pane's shell.

    Args:
        pane_pid: The PID of the shell running in the tmux pane
                  (from libtmux's pane.pane_pid).

    Returns:
        The PID of the Claude process, or None if not found.
    """
    try:
        result = subprocess.run(
            ["ps", "-o", "pid,ppid,comm", "-ax"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return None

        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3:
                try:
                    pid = int(parts[0])
                    ppid = int(parts[1])
                    comm = parts[2]
                except ValueError:
                    continue
                if ppid == pane_pid and comm in ("claude", "node"):
                    return pid
        return None
    except (subprocess.TimeoutExpired, OSError):
        return None


def get_session_id_from_args(claude_pid: int) -> Optional[str]:
    """Extract the Claude session ID from --resume in the process args.

    When Claude resumes a session (after /clear or explicit resume), the
    command line contains --resume <sessionId>. This is the most reliable
    way to determine which session file a Claude process owns.

    Args:
        claude_pid: PID of the Claude process.

    Returns:
        The session UUID from --resume, or None if not present.
    """
    try:
        result = subprocess.run(
            ["ps", "-o", "args=", "-p", str(claude_pid)],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return None

        args = result.stdout.strip()
        match = re.search(r'--resume\s+([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', args)
        if match:
            return match.group(1)
        return None
    except (subprocess.TimeoutExpired, OSError):
        return None


def get_pane_pid_for_window(tmux_session: str, window_name: str) -> Optional[int]:
    """Get the pane PID for a tmux window using tmux CLI.

    This avoids needing a libtmux Pane object — just uses tmux directly.

    Args:
        tmux_session: tmux session name (e.g., "agents")
        window_name: tmux window name

    Returns:
        The pane PID, or None if the window doesn't exist.
    """
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-t", f"{tmux_session}:{window_name}",
             "-F", "#{pane_pid}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return None

        pid_str = result.stdout.strip().split('\n')[0]
        return int(pid_str) if pid_str else None
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None


def discover_session_id_via_pid(
    tmux_session: str,
    window_name: str,
) -> Optional[str]:
    """Discover a Claude session ID by tracing tmux pane → Claude PID → args.

    Full discovery chain:
    1. tmux window → pane_pid (shell PID)
    2. pane_pid → child Claude PID
    3. Claude PID → --resume <sessionId> from command args

    Returns None if any step fails (e.g., fresh start without --resume).

    Args:
        tmux_session: tmux session name
        window_name: tmux window name for the agent

    Returns:
        Claude session UUID, or None.
    """
    pane_pid = get_pane_pid_for_window(tmux_session, window_name)
    if not pane_pid:
        return None

    claude_pid = get_claude_pid_from_pane_pid(pane_pid)
    if not claude_pid:
        return None

    return get_session_id_from_args(claude_pid)


def is_session_id_owned_by_others(
    session_id: str,
    own_agent_id: str,
    all_sessions: list,
) -> bool:
    """Check if a Claude session ID is already owned by another agent.

    Prevents cross-contamination when the directory-based fallback discovers
    a sessionId that belongs to a different agent.

    Args:
        session_id: The Claude sessionId to check.
        own_agent_id: The overcode agent ID doing the check.
        all_sessions: All active overcode sessions to check against.

    Returns:
        True if another agent already owns this sessionId.
    """
    for session in all_sessions:
        if session.id == own_agent_id:
            continue
        owned_ids = getattr(session, 'claude_session_ids', None) or []
        if session_id in owned_ids:
            return True
    return False
