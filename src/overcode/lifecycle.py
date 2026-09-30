"""
Fleet-wide lifecycle: bring every dead agent back (#481), or wind the whole
of overcode down (#509).

The two pair up: ``shutdown`` stops agents but keeps their session records
(and so their ``active_agent_session_id``), and ``revive_all`` relaunches
those records with ``--resume``. "shut down -> reboot -> revive --all" works
end to end.

Everything that touches tmux sessions or processes goes through
``SystemOps`` so tests can swap it out; per-agent work goes through
``AgentLauncher`` (whose tmux layer takes a ``MockTmux``).
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .status_constants import STATUS_TERMINATED, STATUS_WAITING_OVERSIGHT

# Statuses of an agent whose process is gone but whose record is kept.
# waiting_oversight is a child that stopped without reporting; after a
# reboot its window is gone like any other.
REVIVABLE_STATUSES = frozenset({STATUS_TERMINATED, STATUS_WAITING_OVERSIGHT})

JOBS_TMUX_SESSION = "jobs"
SPLIT_TMUX_SESSION = "overcode"  # cli/split.py: the split layout's own session
CONTROLLER_TMUX_SESSION = "overcode-controller"  # supervisor_layout.sh
LINKED_SESSION_PREFIX = "oc-view"  # cli/split.py


# ---------------------------------------------------------------------------
# Revive (#481)
# ---------------------------------------------------------------------------

@dataclass
class ReviveResult:
    """One agent's revive: how it came back (or would), and whether it did."""
    name: str
    tmux_session: str
    mode: str                 # "resume" or "fresh"
    ok: Optional[bool]        # None on a dry run
    reason: str = ""          # why fresh, or why it failed
    session_id: str = ""


def resume_mode(session, fresh: bool = False) -> tuple:
    """("resume" | "fresh", reason) for relaunching ``session``.

    Mirrors ``_send_launch_for_session``: it resumes only when not fresh and
    an agent session id is recorded; a backend without RESUME can't use it.
    """
    from .backends import BackendCapability, session_supports

    if fresh:
        return "fresh", "--fresh"
    if not session_supports(session, BackendCapability.RESUME):
        return "fresh", f"{getattr(session, 'backend', '?')} can't resume"
    if not session.active_agent_session_id:
        return "fresh", "no conversation recorded"
    return "resume", ""


def _has_report(session) -> bool:
    """A child that reported is finished work (done, or auto-archived done)."""
    if session.parent_session_id is None:
        return False
    from .follow_mode import _check_report
    try:
        return _check_report(session.tmux_session, session.name) is not None
    except Exception:
        return False


def find_dead_agents(launcher) -> list:
    """Agents in the launcher's tmux session whose window is gone, parents first.

    Refreshes terminated detection first (``list_sessions``). Skips done
    children and children that filed a report — auto-archived done agents
    are marked terminated too, and are finished, not dead.
    """
    from .launcher import window_in_lookup, window_lookup

    sessions = launcher.list_sessions()
    existing = window_lookup(launcher._list_tmux_windows_cheap())
    dead = [
        s for s in sessions
        if s.status in REVIVABLE_STATUSES
        and not window_in_lookup(s.tmux_window, existing)
        and not _has_report(s)
    ]
    dead.sort(key=lambda s: (launcher.sessions.compute_depth(s), s.name))
    return dead


def revive_all(
    launcher,
    *,
    fresh: bool = False,
    dry_run: bool = False,
    on_result: Optional[Callable[[ReviveResult], None]] = None,
) -> List[ReviveResult]:
    """Revive every dead agent in the launcher's tmux session, parents first."""
    results = []
    for session in find_dead_agents(launcher):
        mode, reason = resume_mode(session, fresh)
        if dry_run:
            ok = None
        else:
            try:
                ok = bool(launcher.revive(session, fresh=fresh))
            except Exception as e:  # one bad record must not stop the rest
                ok, reason = False, str(e)
            if not ok and not reason:
                reason = "could not create or start its window"
        result = ReviveResult(session.name, launcher.tmux_session, mode, ok, reason,
                              session_id=session.id)
        results.append(result)
        if on_result:
            on_result(result)
    return results


def known_tmux_sessions(session_manager) -> List[str]:
    """Every tmux session some agent record belongs to."""
    return sorted({s.tmux_session for s in session_manager.list_sessions() if s.tmux_session})


def ensure_monitor_daemon(tmux_session: str) -> Optional[bool]:
    """Start the monitor daemon if it isn't running.

    Returns True if started, False if the start failed, None if it was
    already running.
    """
    import sys
    from .monitor_daemon import is_monitor_daemon_running
    from .pid_utils import is_daemon_lock_held, spawn_daemon
    from .settings import get_monitor_daemon_pid_path

    if (is_daemon_lock_held(get_monitor_daemon_pid_path(tmux_session))
            or is_monitor_daemon_running(tmux_session)):
        return None
    pid = spawn_daemon([sys.executable, "-m", "overcode.monitor_daemon",
                        "--session", tmux_session])
    return pid is not None


# ---------------------------------------------------------------------------
# Shutdown (#509)
# ---------------------------------------------------------------------------

class SystemOps:
    """tmux-server and process operations shutdown needs (swap out in tests)."""

    def _tmux(self, *args: str) -> subprocess.CompletedProcess:
        from .tmux_utils import _build_tmux_cmd
        try:
            return subprocess.run([*_build_tmux_cmd(), *args], capture_output=True,
                                  text=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as e:
            return subprocess.CompletedProcess(args, 1, "", str(e))

    def list_tmux_sessions(self) -> List[str]:
        r = self._tmux("list-sessions", "-F", "#{session_name}")
        if r.returncode != 0:
            return []
        return [line for line in r.stdout.splitlines() if line]

    def kill_tmux_session(self, name: str) -> bool:
        return self._tmux("kill-session", "-t", f"={name}").returncode == 0

    def current_tmux_location(self) -> tuple:
        """(session, window) this process runs in, or (None, None)."""
        pane = os.environ.get("TMUX_PANE")
        if not os.environ.get("TMUX") or not pane:
            return None, None
        r = self._tmux("display-message", "-p", "-t", pane,
                       "#{session_name}\t#{window_name}")
        parts = r.stdout.strip().split("\t") if r.returncode == 0 else []
        if len(parts) != 2 or not parts[0]:
            return None, None
        return parts[0], parts[1]

    def pane_commands(self, tmux_session: str) -> Dict[str, str]:
        """window name -> the first pane's current command."""
        r = self._tmux("list-panes", "-s", "-t", f"={tmux_session}", "-F",
                       "#{window_name}\t#{pane_index}\t#{pane_current_command}")
        out: Dict[str, str] = {}
        if r.returncode != 0:
            return out
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) == 3 and parts[0] not in out:
                out[parts[0]] = parts[2]
        return out

    def running_pid(self, pid_file: Path) -> Optional[int]:
        from .pid_utils import get_process_pid
        return get_process_pid(pid_file)

    def stop_pid_file(self, pid_file: Path) -> bool:
        from .pid_utils import stop_process
        return stop_process(pid_file)

    def own_pid(self) -> int:
        return os.getpid()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def monotonic(self) -> float:
        return time.monotonic()


@dataclass
class ShutdownStep:
    """One line of the shutdown's output."""
    phase: str     # supervisor | agents | jobs | daemons | tmux
    target: str
    outcome: str   # stopped | killed | exited | skipped | would-stop | failed | not-running | deferred

    detail: str = ""

    def line(self) -> str:
        extra = f" ({self.detail})" if self.detail else ""
        return f"  {self.outcome:<11} {self.target}{extra}"


@dataclass
class ShutdownReport:
    steps: List[ShutdownStep] = field(default_factory=list)
    stop_self: bool = False  # this process holds a pid file it must stop itself

    def count(self, phase: str, *outcomes: str) -> int:
        return sum(1 for s in self.steps if s.phase == phase and s.outcome in outcomes)

    @property
    def failed(self) -> List[ShutdownStep]:
        return [s for s in self.steps if s.outcome == "failed"]


def _state_session_names() -> List[str]:
    from .settings import get_state_dir
    try:
        return sorted(p.name for p in get_state_dir().iterdir() if p.is_dir())
    except OSError:
        return []


def _is_shell(command: str) -> bool:
    from .backends.shell import KNOWN_SHELLS
    return os.path.basename(command.lstrip("-")) in KNOWN_SHELLS


def shutdown(
    tmux_session: str = "agents",
    *,
    all_sessions: bool = False,
    services_only: bool = False,
    dry_run: bool = False,
    force: bool = False,
    keep_jobs: bool = False,
    timeout: float = 10.0,
    ops: Optional[SystemOps] = None,
    session_manager=None,
    launcher_factory: Optional[Callable[[str], object]] = None,
    echo: Callable[[str], None] = print,
) -> ShutdownReport:
    """Wind overcode down: supervisor, agents, jobs, daemons, tmux sessions.

    Agent records are kept (never archived) so ``revive --all`` can bring
    them back with ``--resume``. Scope is ``tmux_session`` unless
    ``all_sessions``; the shared pieces (split/controller tmux sessions,
    the presence logger) are only stopped when no other overcode tmux
    session is left running.

    ``services_only`` stops just the supervisor, monitor and web server
    (what ``POST /api/shutdown`` does by default, e.g. before an upgrade).

    A pid file held by this very process (the web server serving
    /api/shutdown) is left for the caller: ``report.stop_self`` is set.
    The tmux session this process runs in is killed last, since that ends
    the process.
    """
    from .settings import (
        PATHS,
        get_monitor_daemon_pid_path,
        get_supervisor_daemon_pid_path,
        get_web_server_pid_path,
        get_web_server_port_path,
    )

    ops = ops or SystemOps()
    if session_manager is None:
        from .session_manager import SessionManager
        session_manager = SessionManager()
    if launcher_factory is None:
        from .launcher import AgentLauncher

        def launcher_factory(ts: str):
            return AgentLauncher(ts, session_manager=session_manager)

    report = ShutdownReport()
    verb = "would-stop" if dry_run else None

    def add(phase, target, outcome, detail=""):
        step = ShutdownStep(phase, target, outcome, detail)
        report.steps.append(step)
        echo(step.line())
        return step

    live_tmux = set(ops.list_tmux_sessions())
    own_session, own_window = ops.current_tmux_location()
    # A linked oc-view-X session shares X's windows: both hold this process.
    own_sessions = set()
    if own_session:
        own_sessions = {own_session, f"{LINKED_SESSION_PREFIX}-{own_session}"}
        if own_session.startswith(LINKED_SESSION_PREFIX + "-"):
            own_sessions.add(own_session[len(LINKED_SESSION_PREFIX) + 1:])
    record_sessions = set(known_tmux_sessions(session_manager))
    overcode_sessions = record_sessions | set(_state_session_names())
    if all_sessions:
        scope = sorted(overcode_sessions | {tmux_session})
    else:
        scope = [tmux_session]
    others_alive = sorted((record_sessions & live_tmux) - set(scope))
    shared = all_sessions or not others_alive

    def stop_pid(phase: str, target: str, pid_file: Path) -> None:
        pid = ops.running_pid(pid_file)
        if pid is None:
            if not dry_run and pid_file.exists():
                ops.stop_pid_file(pid_file)  # clears the stale file
            add(phase, target, "not-running")
            return
        if pid == ops.own_pid():
            report.stop_self = True
            add(phase, target, "deferred", f"PID {pid}, this process; stops last")
            return
        if dry_run:
            add(phase, target, verb, f"PID {pid}")
        elif ops.stop_pid_file(pid_file):
            add(phase, target, "stopped", f"PID {pid}")
        else:
            add(phase, target, "failed", f"PID {pid}")

    # 1. Supervisor first, so it can't relaunch daemon-claude or nudge agents.
    echo("Supervisor daemon")
    for ts in scope:
        stop_pid("supervisor", f"supervisor daemon [{ts}]",
                 get_supervisor_daemon_pid_path(ts))

    # 2. Agents, children first; records kept.
    if not services_only:
        echo("Agents" + (" (force: no graceful exit)" if force else ""))
        for ts in scope:
            if ts not in live_tmux:
                continue
            _stop_agents(ts, launcher_factory(ts), ops, force=force,
                         dry_run=dry_run, timeout=timeout, add=add,
                         own_window=own_window if ts in own_sessions else None)

    # 3. Jobs.
    if not services_only:
        echo("Jobs")
        if keep_jobs:
            add("jobs", f"tmux session '{JOBS_TMUX_SESSION}'", "skipped", "--keep-jobs")
        elif JOBS_TMUX_SESSION not in live_tmux:
            add("jobs", f"tmux session '{JOBS_TMUX_SESSION}'", "not-running")
        elif dry_run:
            add("jobs", f"tmux session '{JOBS_TMUX_SESSION}'", verb)
        elif ops.kill_tmux_session(JOBS_TMUX_SESSION):
            add("jobs", f"tmux session '{JOBS_TMUX_SESSION}'", "killed")
        else:
            add("jobs", f"tmux session '{JOBS_TMUX_SESSION}'", "failed")

    # 4. Daemons (before tmux sessions: killing this process's own tmux
    # session must be the very last thing).
    echo("Daemons")
    for ts in scope:
        stop_pid("daemons", f"monitor daemon [{ts}]", get_monitor_daemon_pid_path(ts))
        web_pid = get_web_server_pid_path(ts)
        stop_pid("daemons", f"web server [{ts}]", web_pid)
        if not dry_run and not report.stop_self and not web_pid.exists():
            get_web_server_port_path(ts).unlink(missing_ok=True)
    if not services_only:
        if shared:
            from .presence_logger import PRESENCE_PID_FILE
            stop_pid("daemons", "presence logger", PRESENCE_PID_FILE)
        else:
            add("daemons", "presence logger", "skipped",
                f"still used by {', '.join(others_alive)}")
        for legacy in (PATHS.daemon_pid, PATHS.monitor_daemon_pid,
                       PATHS.supervisor_daemon_pid):
            if legacy.exists():
                stop_pid("daemons", f"legacy {legacy.name}", legacy)

    # 5. tmux sessions, this process's own last.
    if not services_only:
        echo("tmux sessions")
        targets = []
        for ts in scope:
            targets.append(f"{LINKED_SESSION_PREFIX}-{ts}")
        if shared:
            targets += [s for s in sorted(live_tmux)
                        if s.startswith(LINKED_SESSION_PREFIX + "-") and s not in targets]
            targets += [CONTROLLER_TMUX_SESSION, SPLIT_TMUX_SESSION]
        targets += list(scope)
        targets = ([t for t in targets if t not in own_sessions]
                   + [t for t in targets if t in own_sessions])
        for name in targets:
            if name not in live_tmux:
                continue
            last = name in own_sessions
            if dry_run:
                add("tmux", f"tmux session '{name}'", verb,
                    "this terminal; last" if last else "")
                continue
            if last:
                echo(f"  killing   tmux session '{name}' (this terminal) last")
            ok = ops.kill_tmux_session(name)
            add("tmux", f"tmux session '{name}'", "killed" if ok else "failed")
        if not shared:
            add("tmux", "split/controller sessions", "skipped",
                f"still used by {', '.join(others_alive)}")

    return report


def _stop_agents(ts, launcher, ops: SystemOps, *, force, dry_run, timeout, add,
                 own_window: Optional[str] = None) -> None:
    """Exit the agents in one tmux session, deepest first, keeping records.

    An agent whose window runs this very process (an agent calling
    ``overcode shutdown``) can't be exited mid-run: its record is marked
    and its window goes with its tmux session, killed last.
    """
    from .launcher import window_in_lookup, window_lookup

    sessions = [s for s in launcher.sessions.list_sessions() if s.tmux_session == ts]
    existing = window_lookup(launcher._list_tmux_windows_cheap())
    alive = [s for s in sessions if window_in_lookup(s.tmux_window, existing)]
    for s in [s for s in alive if own_window and s.tmux_window == own_window]:
        alive.remove(s)
        if not dry_run:
            launcher.sessions.update_session_status(s.id, STATUS_TERMINATED)
        add("agents", s.name, "deferred", "this terminal; ends with its tmux session")
    if not alive:
        add("agents", f"agents [{ts}]", "not-running")
        return

    by_depth: Dict[int, list] = {}
    for s in alive:
        by_depth.setdefault(launcher.sessions.compute_depth(s), []).append(s)

    for depth in sorted(by_depth, reverse=True):
        group = sorted(by_depth[depth], key=lambda s: s.name)
        if dry_run:
            for s in group:
                add("agents", s.name, "would-stop",
                    "kill window" if force else "graceful exit, then kill window")
            continue

        exited = set()
        if not force:
            for s in group:
                try:
                    launcher._send_graceful_exit(launcher.backend_for(s), s.tmux_window)
                except Exception:
                    pass  # it gets its window killed below
            deadline = ops.monotonic() + timeout
            while True:
                commands = ops.pane_commands(ts)
                exited = {s.id for s in group
                          if _is_shell(commands.get(s.tmux_window, "")) or s.tmux_window not in commands}
                if len(exited) == len(group) or ops.monotonic() >= deadline:
                    break
                ops.sleep(0.25)

        for s in group:
            killed = launcher.tmux.kill_window(s.tmux_window)
            launcher.sessions.update_session_status(s.id, STATUS_TERMINATED)
            if s.id in exited:
                add("agents", s.name, "exited", "window closed")
            elif force:
                add("agents", s.name, "killed" if killed else "failed", "window killed")
            else:
                add("agents", s.name, "killed" if killed else "failed",
                    f"no exit within {timeout:g}s; window killed")
