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
from datetime import datetime, timedelta
from typing import Optional

from .session_manager import SessionManager
from .status_patterns import strip_ansi
from .settings import get_session_dir
from .status_constants import DEFAULT_CAPTURE_LINES, STATUS_WAITING_OVERSIGHT
from .tmux_utils import tmux_window_target


def _capture_pane(tmux_session: str, window_name: str, lines: int = DEFAULT_CAPTURE_LINES) -> Optional[str]:
    """Capture recent pane output via tmux."""
    try:
        result = subprocess.run(
            [
                "tmux", "capture-pane",
                "-t", tmux_window_target(tmux_session, window_name),
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


def _check_report(tmux_session: str, agent_name: str) -> Optional[dict]:
    """Check if a report file exists for this agent.

    Returns:
        Parsed report dict with 'status', 'reason', 'timestamp', or None.
    """
    session_dir = get_session_dir(tmux_session)
    report_file = session_dir / f"report_{agent_name}.json"

    if not report_file.exists():
        return None

    try:
        with open(report_file) as f:
            data = json.load(f)
        if "status" in data:
            return data
        return None
    except (json.JSONDecodeError, IOError):
        return None


def _check_session_terminated(sessions: SessionManager, agent_name: str) -> bool:
    """Check if the session has been terminated (tmux window gone)."""
    session = sessions.get_session_by_name(agent_name)
    if session is None:
        return True
    return session.status == "terminated"


def _find_dedup_start(new_lines: list[str], recent_lines: deque) -> int:
    """Find the index in new_lines where new (non-duplicate) content begins.

    Uses a sliding-window match against recent_lines to find the overlap point.

    Args:
        new_lines: Cleaned lines from latest pane capture
        recent_lines: Recent lines already emitted

    Returns:
        Index into new_lines where output should start
    """
    if not recent_lines:
        return 0

    last_known = recent_lines[-1]
    for i in range(len(new_lines) - 1, -1, -1):
        if new_lines[i] == last_known:
            match = True
            check_count = min(3, len(recent_lines), i + 1)
            for j in range(1, check_count):
                if i - j >= 0 and len(recent_lines) > j:
                    rl_idx = len(recent_lines) - 1 - j
                    if recent_lines[rl_idx] != new_lines[i - j]:
                        match = False
                        break
            if match:
                return i + 1

    return 0


def _emit_new_lines(raw: str, recent_lines: deque) -> None:
    """Process pane output and emit new lines to stdout."""
    new_lines = []
    for line in raw.rstrip().split('\n'):
        cleaned = strip_ansi(line).strip()
        new_lines.append(cleaned)

    output_start = _find_dedup_start(new_lines, recent_lines)

    # Emit new lines
    if output_start < len(new_lines):
        for line in new_lines[output_start:]:
            if line:
                print(line)
            recent_lines.append(line)


def _stream_final_output(tmux_session: str, window_name: str, poll_interval: float, recent_lines: deque) -> None:
    """Capture and emit any final output after a Stop event."""
    time.sleep(poll_interval)
    raw = _capture_pane(tmux_session, window_name)
    if raw:
        _emit_new_lines(raw, recent_lines)


def _handle_report(report: dict, name: str, sessions: SessionManager) -> int:
    """Process a report and return the appropriate exit code."""
    session = sessions.get_session_by_name(name)
    if session:
        sessions.update_session_status(session.id, "done")

    if report["status"] == "success":
        print(f"\n[follow] Agent '{name}' reported success", file=sys.stderr)
        return 0
    else:
        reason = report.get("reason", "")
        msg = f"\n[follow] Agent '{name}' reported failure"
        if reason:
            msg += f": {reason}"
        print(msg, file=sys.stderr)
        return 1


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
        Exit code: 0 on success report, 1 on failure/terminated, 2 on timeout, 130 on Ctrl-C
    """
    sessions = SessionManager()
    session = sessions.get_session_by_name(name)
    if session is None:
        print(f"Error: Agent '{name}' not found", file=sys.stderr)
        return 1

    window_name = session.tmux_window

    # Read oversight policy from session
    oversight_policy = getattr(session, 'oversight_policy', 'wait') or 'wait'
    oversight_timeout_seconds = getattr(session, 'oversight_timeout_seconds', 0.0) or 0.0

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
            raw = _capture_pane(tmux_session, window_name)
            if raw is None:
                if _check_session_terminated(sessions, name):
                    print(f"\n[follow] Agent '{name}' terminated", file=sys.stderr)
                    return 1
                time.sleep(poll_interval)
                continue

            _emit_new_lines(raw, recent_lines)

            # Check for Stop event via hook state
            if _check_hook_stop(tmux_session, name):
                _stream_final_output(tmux_session, window_name, poll_interval, recent_lines)

                # Check if report already filed before Stop
                report = _check_report(tmux_session, name)
                if report:
                    return _handle_report(report, name, sessions)

                # --on-stuck fail: exit immediately without waiting for report
                if oversight_policy == "fail":
                    session = sessions.get_session_by_name(name)
                    if session:
                        sessions.update_session_status(session.id, "done")
                    print(f"\n[follow] Agent '{name}' stopped without report (--on-stuck fail)", file=sys.stderr)
                    return 1

                # Mark as waiting_oversight and set deadline
                session = sessions.get_session_by_name(name)
                if session:
                    if oversight_policy == "timeout" and oversight_timeout_seconds > 0:
                        deadline = datetime.now() + timedelta(seconds=oversight_timeout_seconds)
                        sessions.update_session(
                            session.id,
                            status=STATUS_WAITING_OVERSIGHT,
                            oversight_deadline=deadline.isoformat(),
                        )
                        sessions.update_session_status(session.id, STATUS_WAITING_OVERSIGHT)
                    else:
                        sessions.update_session_status(session.id, STATUS_WAITING_OVERSIGHT)

                    print(f"\n[follow] Agent '{name}' stopped, waiting for report...", file=sys.stderr)

                # Enter report-polling sub-loop
                return _poll_for_report(
                    name, tmux_session, sessions, window_name,
                    oversight_policy, oversight_timeout_seconds,
                    poll_interval, recent_lines,
                )

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


def _poll_for_report(
    name: str,
    tmux_session: str,
    sessions: SessionManager,
    window_name: str,
    oversight_policy: str,
    oversight_timeout_seconds: float,
    poll_interval: float,
    recent_lines: deque,
) -> int:
    """Poll for a report file after Stop event detected.

    Returns:
        0 on success report, 1 on failure report or terminated, 2 on timeout
    """
    deadline = None
    if oversight_policy == "timeout" and oversight_timeout_seconds > 0:
        deadline = datetime.now() + timedelta(seconds=oversight_timeout_seconds)

    while True:
        # Check for report
        report = _check_report(tmux_session, name)
        if report:
            return _handle_report(report, name, sessions)

        # Check timeout
        if deadline and datetime.now() >= deadline:
            session = sessions.get_session_by_name(name)
            if session:
                sessions.update_session(
                    session.id,
                    report_status="failure",
                    report_reason="Oversight timeout expired",
                )
                sessions.update_session_status(session.id, "done")
            print(f"\n[follow] Agent '{name}' timed out waiting for report", file=sys.stderr)
            return 2

        # Check if window is gone
        if _check_session_terminated(sessions, name):
            print(f"\n[follow] Agent '{name}' terminated while waiting for report", file=sys.stderr)
            return 1

        # Continue streaming pane output while waiting
        raw = _capture_pane(tmux_session, window_name)
        if raw:
            _emit_new_lines(raw, recent_lines)

        time.sleep(poll_interval)
