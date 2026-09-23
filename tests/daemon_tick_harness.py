"""A MonitorDaemon over a real SessionManager on a temp dir, for tick-level tests.

Every seam the daemon reaches for during a tick is replaced by something
scripted and in-process — a fake tmux, a detector that returns a scripted
``(status, activity, pane)`` per session per tick, a frozen clock, and I/O
counters on ``sessions.json`` — so a test can run whole ticks over a real
state file and check both what lands on disk and how often the file is
touched.

Used by the unit tests and by ``daemon_tick_driver.py``, which runs the
same scripted ticks against a frozen copy of an older source tree, so this
module may only use APIs that exist on both sides: ``MonitorDaemon(...)``,
``_tick``, ``detector``, ``previous_states``/``last_state_times`` and the
``SessionManager`` constructor and ``_save_state``.
"""

from __future__ import annotations

import builtins
import contextlib
import io
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple

STATUS_SCRIPT = Callable[[int, object], Tuple[str, str, str]]
SKILLS_SCRIPT = Callable[[int, str], List[str]]


# ── fake tmux ────────────────────────────────────────────────────────────


class FakeTmux:
    """``TmuxInterface`` double serving scripted panes; counts every call."""

    def __init__(self, session: str, panes: Dict[str, str], pids: Optional[Dict[str, int]] = None):
        self.session = session
        self.panes = dict(panes)
        self.pids = dict(pids or {})
        self.calls: Counter = Counter()
        self.killed: List[str] = []

    def capture_pane(self, session: str, window: str, lines: int = 100) -> Optional[str]:
        self.calls["capture_pane"] += 1
        if session != self.session:
            return None
        content = self.panes.get(window)
        if content is None:
            return None
        return "\n".join(content.split("\n")[-lines:])

    def get_pane_pid(self, session: str, window: str) -> Optional[int]:
        self.calls["get_pane_pid"] += 1
        return self.pids.get(window) if session == self.session else None

    def list_windows(self, session: str) -> List[Dict[str, object]]:
        self.calls["list_windows"] += 1
        if session != self.session:
            return []
        return [{"index": i + 1, "name": n, "active": i == 0} for i, n in enumerate(self.panes)]

    def has_session(self, session: str) -> bool:
        self.calls["has_session"] += 1
        return session == self.session

    session_exists = has_session

    def send_keys(self, session: str, window: str, keys: str, enter: bool = True) -> bool:
        self.calls["send_keys"] += 1
        return window in self.panes

    def kill_window(self, session: str, window: str) -> bool:
        self.calls["kill_window"] += 1
        self.killed.append(window)
        return self.panes.pop(window, None) is not None


# ── scripted detector ────────────────────────────────────────────────────


class _Phaseless:
    """What ``_log_hook_event`` and the heartbeat check read off a detector."""

    _last_detect_phase: Dict[str, str] = {}

    def _read_hook_state(self, session_name: str):
        return None


class ScriptedDetector:
    """Stands in for ``StatusDetectorDispatcher``.

    ``script(tick, session)`` returns the ``(status, activity, pane_content)``
    the real detector would; ``skills(tick, name)`` the loaded skills. The
    test advances ``tick`` between daemon ticks.
    """

    mode = "polling"

    def __init__(self, script: STATUS_SCRIPT, skills: Optional[SKILLS_SCRIPT] = None):
        self.script = script
        self.skills = skills or (lambda tick, name: [])
        self.tick = 0
        self.hooks = _Phaseless()
        self.polling = _Phaseless()
        self.calls: Counter = Counter()

    def detect_status(self, session, num_lines: int = 0) -> Tuple[str, str, str]:
        self.calls["detect_status"] += 1
        return self.script(self.tick, session)

    def get_loaded_skills(self, session_name: str) -> List[str]:
        return list(self.skills(self.tick, session_name))

    def get_pane_content(self, window: str, num_lines: int = 0) -> Optional[str]:
        return ""


# ── frozen clock ─────────────────────────────────────────────────────────


class FrozenClock:
    """Patches ``overcode.monitor_daemon.datetime`` so ``now()`` is scripted."""

    def __init__(self, start: datetime):
        self.now = start

    @contextlib.contextmanager
    def installed(self) -> Iterator["FrozenClock"]:
        from overcode import monitor_daemon

        clock = self

        class _Frozen(datetime):
            @classmethod
            def now(cls, tz=None):  # noqa: D401 - datetime API
                return clock.now

        original = monitor_daemon.datetime
        monitor_daemon.datetime = _Frozen
        try:
            yield self
        finally:
            monitor_daemon.datetime = original


# ── sessions.json I/O counters ───────────────────────────────────────────


@dataclass
class IoCounters:
    reads: int = 0  # sessions.json opened for reading (one parse each)
    writes: int = 0  # fsync'd writes landing on sessions.json or its temp file
    per_tick: List[Tuple[int, int]] = field(default_factory=list)

    def reset(self) -> None:
        self.reads = self.writes = 0


def _fd_path(fd: int) -> Optional[str]:
    try:
        if sys.platform == "darwin":
            import fcntl

            raw = fcntl.fcntl(fd, fcntl.F_GETPATH, b"\0" * 1024)
            return raw.split(b"\0", 1)[0].decode()
        return os.readlink(f"/proc/self/fd/{fd}")
    except (OSError, AttributeError, ValueError):
        return None


@contextlib.contextmanager
def count_sessions_io(sessions_file: Path) -> Iterator[IoCounters]:
    """Count opens-for-read of ``sessions_file`` and fsyncs onto it (or its temp).

    Independent of SessionManager's internals: a read is an ``open`` with a
    read or update mode on the file itself; a write is an ``os.fsync`` of a
    descriptor whose path is ``sessions.json`` or ``sessions.json.tmp.*``.
    """
    counters = IoCounters()
    target = os.fspath(sessions_file)
    orig_open, orig_io_open, orig_fsync = builtins.open, io.open, os.fsync

    def counting_open(file, mode="r", *args, **kwargs):
        try:
            if os.fspath(file) == target and ("r" in mode or "+" in mode):
                counters.reads += 1
        except TypeError:
            pass
        return orig_open(file, mode, *args, **kwargs)

    def counting_fsync(fd):
        path = _fd_path(fd)
        if path is not None and os.path.basename(path).startswith("sessions.json"):
            counters.writes += 1
        return orig_fsync(fd)

    builtins.open = counting_open
    io.open = counting_open
    os.fsync = counting_fsync
    try:
        yield counters
    finally:
        builtins.open = orig_open
        io.open = orig_io_open
        os.fsync = orig_fsync


def settle(path: Path, seconds_ago: float = 1.0) -> None:
    """Age ``path``'s mtime so the stat gate trusts its signature (see stat_gate)."""
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns - int(seconds_ago * 1e9)))


# ── fixture sessions ─────────────────────────────────────────────────────


def seed_sessions(
    session_manager,
    n: int,
    tmux_session: str,
    work_dir: Path,
    start: datetime,
    *,
    other_tmux_sessions: int = 0,
) -> list:
    """Write ``n`` live sessions (plus terminated ones in other tmux sessions).

    A shallow tree like the scaling bench's: sessions 10-19 are children of
    0-9, 20-21 grandchildren. Every session has a ``.git`` in its own
    directory (HEAD on ``main``) so the git refresh is deterministic. One
    write, straight through ``_save_state``.
    """
    from overcode.session_manager import Session, SessionStats

    work_dir.mkdir(parents=True, exist_ok=True)
    sessions = []
    state: Dict[str, dict] = {}
    for i in range(n):
        repo = work_dir / f"repo-{i:02d}"
        (repo / ".git").mkdir(parents=True, exist_ok=True)
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        parent = None
        if 10 <= i < 20 and n > 10:
            parent = sessions[i - 10].id
        elif 20 <= i < 22 and n > 20:
            parent = sessions[i - 10].id
        sid = f"sess-{i:04d}"
        s = Session(
            id=sid,
            name=f"agent-{i:02d}",
            tmux_session=tmux_session,
            tmux_window=f"agent-{i:02d}-{sid[-4:]}",
            command=["claude", "--session-id", f"claude-{i:04d}"],
            start_directory=str(repo),
            start_time=(start - timedelta(hours=3)).isoformat(),
            repo_name=repo.name,
            branch="main",
            status="running",
            stats=SessionStats(
                interaction_count=i,
                estimated_cost_usd=round(0.25 * i, 4),
                total_tokens=1000 * i,
                operation_times=[10.0, 20.0, 30.0][: i % 4],
                current_task="Initializing...",
                input_tokens=100 * i,
                output_tokens=10 * i,
                current_state="running" if i % 2 == 0 else "waiting_user",
                state_since=(start - timedelta(minutes=5)).isoformat(),
                green_time_seconds=100.0 + i,
                non_green_time_seconds=50.0 + i,
                last_time_accumulation=(start - timedelta(seconds=2)).isoformat(),
            ),
            agent_session_ids=[f"claude-{i:04d}"],
            active_agent_session_id=f"claude-{i:04d}",
            loaded_skills=["overcode"],
            parent_session_id=parent,
            cpu_percent=3.0 + i,
            rss_bytes=(300 + i) * 1024 * 1024,
        )
        sessions.append(s)
        state[s.id] = s.to_dict()
    for j in range(other_tmux_sessions):
        sid = f"old-{j:04d}"
        s = Session(
            id=sid,
            name=f"old-{j:04d}",
            tmux_session=f"{tmux_session}-other",
            tmux_window=f"old-{j:04d}",
            command=["claude"],
            start_directory=None,
            start_time=(start - timedelta(days=j + 1)).isoformat(),
            status="terminated",
        )
        state[s.id] = s.to_dict()
    session_manager._save_state(state)
    settle(session_manager.state_file)
    return sessions


# ── daemon construction ──────────────────────────────────────────────────


def make_daemon(state_dir: Path, tmux_session: str, detector, *, session_manager=None):
    """A ``MonitorDaemon`` on ``state_dir`` with every periodic sync already done.

    ``OVERCODE_STATE_DIR`` must point at ``state_dir`` (the daemon's paths
    are resolved from it at construction). The presence logger is not
    started; the log goes to a StringIO. Only the per-tick session body
    runs until a test moves a ``_last_*_sync`` stamp back.
    """
    from rich.console import Console

    from overcode import monitor_daemon
    from overcode.daemon_logging import DAEMON_THEME
    from overcode.session_manager import SessionManager
    from overcode.status_detector import PollingStatusDetector

    sm = session_manager or SessionManager(
        state_dir=state_dir / "sessions", skip_git_detection=True
    )
    original_presence = monitor_daemon.PresenceLogger
    monitor_daemon.PresenceLogger = None
    try:
        daemon = monitor_daemon.MonitorDaemon(
            tmux_session=tmux_session,
            session_manager=sm,
            status_detector=PollingStatusDetector(tmux_session, tmux=FakeTmux(tmux_session, {})),
        )
    finally:
        monitor_daemon.PresenceLogger = original_presence
    daemon.detector = detector
    daemon._hostname = "test-host"
    daemon.log.console = Console(file=io.StringIO(), theme=DAEMON_THEME, force_terminal=True)
    daemon._legacy_windows_migrated = True
    daemon.state.loop_count = 1
    return daemon


def seed_steady_state(daemon, sessions, now: datetime) -> None:
    """Make the next tick look like any tick after the first (no first-observation skips)."""
    for s in sessions:
        daemon.previous_states[s.id] = s.stats.current_state
        daemon.last_state_times[s.id] = now - timedelta(seconds=2)
    for name in (
        "_last_stats_sync",
        "_last_session_id_sync",
        "_last_skills_sync",
        "_last_sandbox_sync",
        "_last_resources_sync",
        "_last_history_rotation_check",
        "_last_model_metadata_check",
    ):
        setattr(daemon, name, now)


def run_ticks(
    daemon,
    detector: ScriptedDetector,
    clock: FrozenClock,
    ticks: int,
    *,
    step_seconds: float = 2.0,
    counters: Optional[IoCounters] = None,
    between_ticks: Optional[Callable[[int], None]] = None,
) -> None:
    """Run ``ticks`` daemon ticks, advancing the scripted detector and clock.

    ``sessions.json`` is aged between ticks the way two seconds of wall clock
    would age it, so the stat gate treats each tick's write as settled by
    the next tick (the frozen clock does not move the filesystem's).
    """
    for i in range(ticks):
        detector.tick = i
        daemon.state.loop_count = i + 1
        if counters is not None:
            counters.reset()
        daemon._tick(clock.now)
        if counters is not None:
            counters.per_tick.append((counters.reads, counters.writes))
        clock.now = clock.now + timedelta(seconds=step_seconds)
        if daemon.session_manager.state_file.exists():
            settle(daemon.session_manager.state_file)
        if between_ticks is not None:
            between_ticks(i)


def state_without_timestamps(state: Dict[str, dict]) -> Dict[str, dict]:
    """``sessions.json`` content minus the fields that are wall-clock stamps."""
    stamps = {
        "state_since",
        "last_time_accumulation",
        "last_activity",
        "last_stats_update",
    }
    out: Dict[str, dict] = {}
    for sid, entry in state.items():
        e = dict(entry)
        e.pop("last_heartbeat_time", None)
        if isinstance(e.get("stats"), dict):
            e["stats"] = {k: v for k, v in e["stats"].items() if k not in stamps}
        out[sid] = e
    return out
