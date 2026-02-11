"""
Follow mode: stream a child agent's pane output and block until it stops.

Used by `overcode launch --follow` and `overcode follow <name>` to provide
a blocking, subprocess-like experience while the child remains a first-class
tmux citizen that humans can observe and intervene in.
"""

import json
import signal
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Optional

from .session_manager import SessionManager
from .status_patterns import strip_ansi
from .settings import get_session_dir


def _capture_pane(tmux_session: str, window_index: int, lines: int = 200) -> Optional[str]:
    """Capture recent pane output via tmux."""
    try:
        result = subprocess.run(
            [
                "tmux", "capture-pane",
                "-t", f"{tmux_session}:{window_index}",
                "-p",
                "-S", f"-{lines}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout
        return None
    except subprocess.SubprocessError:
        return None


def _check_hook_stop(tmux_session: str, agent_name: str) -> bool:
    """Check if hook state file shows a Stop event for this agent."""
    session_dir = get_session_dir(tmux_session)
    hook_state_file = session_dir / f"hook_state_{agent_name}.json"

    if not hook_state_file.exists():
        return False

    try:
        with open(hook_state_file) as f:
            data = json.load(f)
        return data.get("event") == "Stop"
    except (json.JSONDecodeError, IOError):
        return False


def _check_session_terminated(sessions: SessionManager, agent_name: str) -> bool:
    """Check if the session has been terminated (tmux window gone)."""
    session = sessions.get_session_by_name(agent_name)
    if session is None:
        return True
    return session.status == "terminated"


def follow_agent(
    name: str,
    tmux_session: str = "agents",
    poll_interval: float = 0.5,
) -> int:
    """Stream pane output to stdout, exit when agent stops.

    Args:
        name: Agent name to follow
        tmux_session: Tmux session name
        poll_interval: Seconds between polls

    Returns:
        Exit code: 0 on clean stop, 1 on terminated, 130 on Ctrl-C
    """
    sessions = SessionManager()
    session = sessions.get_session_by_name(name)
    if session is None:
        print(f"Error: Agent '{name}' not found", file=sys.stderr)
        return 1

    window_index = session.tmux_window

    # Track recent lines for deduplication
    recent_lines: deque = deque(maxlen=50)
    interrupted = False

    def handle_sigint(signum, frame):
        nonlocal interrupted
        interrupted = True

    old_handler = signal.signal(signal.SIGINT, handle_sigint)

    try:
        while not interrupted:
            # Capture pane content
            raw = _capture_pane(tmux_session, window_index)
            if raw is None:
                # Window may be gone
                if _check_session_terminated(sessions, name):
                    print(f"\n[follow] Agent '{name}' terminated", file=sys.stderr)
                    return 1
                time.sleep(poll_interval)
                continue

            # Process lines: strip ANSI then strip whitespace (correct order per MEMORY.md)
            new_lines = []
            for line in raw.rstrip().split('\n'):
                cleaned = strip_ansi(line).strip()
                new_lines.append(cleaned)

            # Find overlap with recent output for deduplication
            output_start = 0
            if recent_lines:
                # Find where the last known line appears in new capture
                last_known = recent_lines[-1] if recent_lines else None
                if last_known is not None:
                    # Search from the end for the last known line
                    for i in range(len(new_lines) - 1, -1, -1):
                        if new_lines[i] == last_known:
                            # Verify a few more lines match to avoid false positives
                            match = True
                            check_count = min(3, len(recent_lines), i + 1)
                            for j in range(1, check_count):
                                if i - j >= 0 and len(recent_lines) > j:
                                    rl_idx = len(recent_lines) - 1 - j
                                    if recent_lines[rl_idx] != new_lines[i - j]:
                                        match = False
                                        break
                            if match:
                                output_start = i + 1
                                break

            # Emit new lines
            if output_start < len(new_lines):
                for line in new_lines[output_start:]:
                    if line:  # Skip empty lines
                        print(line)
                    recent_lines.append(line)

            # Check for Stop event via hook state
            if _check_hook_stop(tmux_session, name):
                # Wait one extra cycle to capture final output
                time.sleep(poll_interval)
                raw = _capture_pane(tmux_session, window_index)
                if raw:
                    for line in raw.rstrip().split('\n'):
                        cleaned = strip_ansi(line).strip()
                        if cleaned and cleaned not in recent_lines:
                            print(cleaned)

                # Mark child as "done" (#244)
                session = sessions.get_session_by_name(name)
                if session:
                    sessions.update_session_status(session.id, "done")
                    print(f"\n[follow] Agent '{name}' completed (done)", file=sys.stderr)
                return 0

            # Check if agent terminated (window gone)
            if _check_session_terminated(sessions, name):
                print(f"\n[follow] Agent '{name}' terminated", file=sys.stderr)
                return 1

            time.sleep(poll_interval)

    finally:
        signal.signal(signal.SIGINT, old_handler)

    # Ctrl-C: don't kill child, just stop following
    if interrupted:
        print(f"\n[follow] Stopped following '{name}' (agent still running)", file=sys.stderr)
        return 130

    return 0
