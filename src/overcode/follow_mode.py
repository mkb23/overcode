"""
Follow mode: stream a child agent's pane output and block until it stops.

Used by `overcode launch --follow` and `overcode follow <name>` to provide
a blocking, subprocess-like experience while the child remains a first-class
tmux citizen that humans can observe and intervene in.
"""

import signal
import subprocess
import sys
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Optional

from .clock import stamp, stamp_epoch, to_epoch
from .session_manager import Session, SessionManager, rename_notice
from .status_patterns import strip_ansi
from .child_lifecycle import OVERSIGHT_TIMEOUT_REASON, hook_stopped, read_report
from .status_constants import DEFAULT_CAPTURE_LINES, STATUS_WAITING_OVERSIGHT
from .tmux_utils import tmux_window_target, _build_tmux_cmd


def _capture_pane(tmux_session: str, window_name: str, lines: int = DEFAULT_CAPTURE_LINES) -> Optional[str]:
    """Capture recent pane output via tmux."""
    try:
        result = subprocess.run(
            [
                # _build_tmux_cmd respects OVERCODE_TMUX_SOCKET — a bare
                # "tmux" would capture from the wrong server (#follow/jobs)
                *_build_tmux_cmd(), "capture-pane",
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


# What the change probe reads: the identity of the pane the target resolved
# to (tmux 3.7 resolves a missing ``session:=name`` to another window rather
# than failing), then everything that moves when the pane's content does.
_PROBE_FORMAT = "\t".join((
    "#{session_name}",
    "#{window_name}",
    "#{window_index}",
    "#{window_activity}",
    "#{history_size}",
    "#{cursor_x}",
    "#{cursor_y}",
    "#{pane_width}",
    "#{pane_height}",
))

# A pane whose probe has not moved is captured again after this long anyway:
# a backstop for a wall-clock step, not part of the change rule.
PROBE_KEEPALIVE_SECONDS = 5.0

# The follow loops' own wall clock: tests freeze ``time.time`` to drive
# deadlines, and the gate must not consume their values.
_wall_clock = time.time
_monotonic = time.monotonic


def _probe_pane(tmux_session: str, window_name) -> Optional[tuple]:
    """``(activity, signature)`` of the window's pane from ONE ``display-message``.

    ``activity`` is ``window_activity``: tmux stamps it (whole seconds of
    wall time) every time it parses output for a pane of the window, in-place
    redraws (spinners, ``\\r`` progress) included. The signature adds the
    history size, cursor and pane size, and the resolved window's name and
    index, so a rename or a resize also reads as a change. None when tmux
    cannot answer or the target resolved to another window.
    """
    try:
        result = subprocess.run(
            [*_build_tmux_cmd(), "display-message", "-p",
             "-t", tmux_window_target(tmux_session, window_name), _PROBE_FORMAT],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    parts = result.stdout.rstrip("\n").split("\t")
    if len(parts) < 9:
        return None
    session, name, index = "\t".join(parts[:-8]), parts[-8], parts[-7]
    window = str(window_name)
    if session != tmux_session or (name != window and not (window.isdigit() and index == window)):
        return None
    try:
        activity = int(parts[-6])
    except ValueError:
        return None
    return activity, tuple(parts)


# What ChangeGatedCapture.capture returns when the pane cannot have changed
# since the last capture: the caller prints nothing new, as it would have.
UNCHANGED = object()


class ChangeGatedCapture:
    """A follower's pane capture, skipped while the pane cannot have changed.

    Every poll costs one ``display-message`` (:func:`_probe_pane`) at the
    same rate as before; the 500-line ``capture-pane`` and its parse run only
    when the probe says the pane may have changed since the last capture. The
    rule has no same-second blind spot: a capture is skipped only when the
    probe equals the one taken just before the last capture AND that
    activity second is strictly before the second the last capture started
    in. Output arriving after that capture started would be stamped with a
    later second, so it moves the probe; output in the same second as the
    capture keeps the gate open until a quiet second has passed. Without a
    probe answer (window gone, tmux down) it captures, as before, so the
    caller sees ``None`` exactly when it did.
    """

    def __init__(self, keepalive_seconds: float = PROBE_KEEPALIVE_SECONDS):
        self.keepalive_seconds = keepalive_seconds
        self._last: Optional[tuple] = None
        self._last_started: float = 0.0
        self._last_mono: float = 0.0
        self.captures = 0
        self.skipped = 0

    def capture(self, tmux_session: str, window_name):
        started = _wall_clock()
        mono = _monotonic()
        probe = _probe_pane(tmux_session, window_name)
        if (probe is not None and self._last is not None
                and probe[1] == self._last
                and probe[0] < int(self._last_started)
                and mono - self._last_mono < self.keepalive_seconds):
            self.skipped += 1
            return UNCHANGED
        raw = _capture_pane(tmux_session, window_name)
        self.captures += 1
        if probe is None or raw is None:
            self._last = None
        else:
            self._last, self._last_started, self._last_mono = probe[1], started, mono
        return raw


# The read side lives with the lifecycle rule; follow uses it under these names.
_check_hook_stop = hook_stopped
_check_report = read_report


def _check_session_terminated(
    sessions: SessionManager, agent_name: str, session_id: Optional[str] = None,
) -> bool:
    """Check if the session has been terminated (tmux window gone).

    With ``session_id`` the agent is found by id, so a rename (#478) — which
    changes the name mid-follow — is not mistaken for termination.
    """
    session = _lookup(sessions, agent_name, session_id)
    if session is None:
        return True
    return session.status == "terminated"


def _released_from(
    sessions: SessionManager, session_id: Optional[str],
) -> Optional[str]:
    """The parent's name (or id) once the followed child has been released
    from it (``overcode release``, #520); None while it is still a child.

    Released, it owes nobody a report: the follow ends cleanly.
    """
    if session_id is None:
        return None
    session = sessions.get_session(session_id)
    if session is None or getattr(session, "parent_session_id", None) is not None:
        return None
    released = getattr(session, "released_from_session_id", None)
    if not released:
        return None
    return getattr(session, "released_from_name", None) or released


def _end_released(name: str, parent: str) -> int:
    print(f"\n[follow] Agent '{name}' was released from '{parent}': it no longer "
          f"reports, stopped following (agent still running)", file=sys.stderr)
    return 0


def _lookup(
    sessions: SessionManager, name: str, session_id: Optional[str],
) -> Optional[Session]:
    """The followed agent: by id when known, else by name; both survive renames."""
    if session_id is not None:
        return sessions.get_session(session_id)
    return sessions.resolve_session_name(name)


def _current_identity(
    sessions: SessionManager, session_id: Optional[str], name: str, window_name: str,
) -> tuple:
    """(name, window) of the followed agent now — both change on a rename (#478)."""
    if session_id is None:
        return name, window_name
    session = sessions.get_session(session_id)
    if session is None:
        return name, window_name
    if session.name != name:
        print(f"\n[follow] Agent '{name}' was renamed to '{session.name}'", file=sys.stderr)
    return session.name, session.tmux_window


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


def _handle_report(
    report: dict, name: str, sessions: SessionManager, session_id: Optional[str] = None,
) -> int:
    """Process a report and return the appropriate exit code."""
    session = _lookup(sessions, name, session_id)
    if session:
        sessions.update_session_status(session.id, "done")
        # Refund unused budget to parent on successful completion (#432).
        if report.get("status") == "success":
            refunded = sessions.auto_reclaim_budget(session.id)
            if isinstance(refunded, (int, float)) and refunded > 0:
                print(
                    f"[follow] Refunded ${refunded:.4f} from '{name}' to parent",
                    file=sys.stderr,
                )

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
        Exit code: 0 on success report or when the child is released from
        its parent (#520), 1 on failure/terminated, 2 on timeout, 130 on Ctrl-C
    """
    sessions = SessionManager()
    session = sessions.resolve_session_name(name)
    if session is None:
        print(f"Error: Agent '{name}' not found", file=sys.stderr)
        return 1
    notice = rename_notice(name, session)
    if notice:
        print(f"[follow] {notice}", file=sys.stderr)

    # Follow by id: the name and window are re-read every poll, so a rename
    # of the followed agent (#478) is tracked instead of reported as its
    # termination (which a parent would read as the child failing).
    session_id = session.id
    name = session.name
    window_name = session.tmux_window
    # A child released mid-follow ends the follow; an agent that was never
    # a child (or was released already) is followed as before
    watch_release = getattr(session, "parent_session_id", None) is not None

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
    pane = ChangeGatedCapture()

    try:
        while not interrupted:
            name, window_name = _current_identity(sessions, session_id, name, window_name)
            released = _released_from(sessions, session_id) if watch_release else None
            if released:
                return _end_released(name, released)
            # Capture pane content (skipped while the pane cannot have changed)
            raw = pane.capture(tmux_session, window_name)
            if raw is None:
                if _check_session_terminated(sessions, name, session_id):
                    print(f"\n[follow] Agent '{name}' terminated", file=sys.stderr)
                    return 1
                time.sleep(poll_interval)
                continue

            if raw is not UNCHANGED:
                _emit_new_lines(raw, recent_lines)

            # Check for Stop event via hook state
            if _check_hook_stop(tmux_session, name, session_id):
                _stream_final_output(tmux_session, window_name, poll_interval, recent_lines)

                # Check if report already filed before Stop
                report = _check_report(tmux_session, name, session_id)
                if report:
                    return _handle_report(report, name, sessions, session_id)

                # --on-stuck fail: exit immediately without waiting for report
                if oversight_policy == "fail":
                    session = _lookup(sessions, name, session_id)
                    if session:
                        sessions.update_session_status(session.id, "done")
                    print(f"\n[follow] Agent '{name}' stopped without report (--on-stuck fail)", file=sys.stderr)
                    return 1

                # Mark as waiting_oversight and set deadline (unless the
                # monitor daemon already started the child's timeout)
                session = _lookup(sessions, name, session_id)
                if session:
                    if (oversight_policy == "timeout" and oversight_timeout_seconds > 0
                            and not session.oversight_deadline):
                        deadline = datetime.now().astimezone() + timedelta(
                            seconds=oversight_timeout_seconds)
                        # The epoch beside the naive stamp places it in the
                        # repeated autumn hour, as the daemon's does
                        sessions.update_session(
                            session.id,
                            status=STATUS_WAITING_OVERSIGHT,
                            oversight_deadline=stamp(deadline),
                            oversight_deadline_at=deadline.timestamp(),
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
                    session_id=session_id,
                    watch_release=watch_release,
                    pane=pane,
                )

            # Check if agent terminated (window gone)
            if _check_session_terminated(sessions, name, session_id):
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
    session_id: Optional[str] = None,
    watch_release: bool = False,
    pane: Optional[ChangeGatedCapture] = None,
) -> int:
    """Poll for a report file after Stop event detected.

    With ``session_id`` the agent's name and window are re-read each poll,
    so a child renamed while its parent waits (#478) keeps being followed
    and its report is found under the new name.

    Returns:
        0 on success report or release from the parent (#520), 1 on failure
        report or terminated, 2 on timeout
    """
    # Epoch seconds: the stored deadline is a naive local stamp, which a
    # naive now misreads in the repeated autumn hour (a deadline in the
    # second pass read as already past in the first)
    if pane is None:
        pane = ChangeGatedCapture()
    deadline: Optional[float] = None
    if oversight_policy == "timeout" and oversight_timeout_seconds > 0:
        now_at = time.time()
        deadline = now_at + oversight_timeout_seconds
        # The session's own deadline when one is set: the daemon enforces
        # that one too, so both agree on when the child times out
        session = _lookup(sessions, name, session_id)
        set_deadline = getattr(session, "oversight_deadline", None)
        if isinstance(set_deadline, str) and set_deadline:
            set_at = getattr(session, "oversight_deadline_at", None)
            placed = isinstance(set_at, (int, float)) and not isinstance(set_at, bool)
            set_epoch = (stamp_epoch(set_deadline, set_at) if placed
                         else to_epoch(set_deadline, near=now_at, ahead=True))
            if set_epoch is not None:
                deadline = set_epoch

    while True:
        name, window_name = _current_identity(sessions, session_id, name, window_name)
        released = _released_from(sessions, session_id) if watch_release else None
        if released:
            return _end_released(name, released)
        # Check for report
        report = _check_report(tmux_session, name, session_id)
        if report:
            return _handle_report(report, name, sessions, session_id)

        # Check timeout
        if deadline is not None and time.time() >= deadline:
            session = _lookup(sessions, name, session_id)
            if session:
                sessions.update_session(
                    session.id,
                    report_status="failure",
                    report_reason=OVERSIGHT_TIMEOUT_REASON,
                )
                sessions.update_session_status(session.id, "done")
            print(f"\n[follow] Agent '{name}' timed out waiting for report", file=sys.stderr)
            return 2

        # Check if window is gone
        if _check_session_terminated(sessions, name, session_id):
            print(f"\n[follow] Agent '{name}' terminated while waiting for report", file=sys.stderr)
            return 1

        # Continue streaming pane output while waiting
        raw = pane.capture(tmux_session, window_name)
        if raw and raw is not UNCHANGED:
            _emit_new_lines(raw, recent_lines)

        time.sleep(poll_interval)
