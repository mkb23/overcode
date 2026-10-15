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

One agent's lifecycle lives here too: ``AgentLifecycleMixin`` is the part
of ``AgentLauncher`` that kills agents, clears terminated records and
settles lifecycle for a CLI run with no daemon (``reconcile_lifecycle``).
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from .status_constants import STATUS_TERMINATED
from .tmux_utils import untracked_window_names

if TYPE_CHECKING:
    from .session_manager import Session

JOBS_TMUX_SESSION = "jobs"
SPLIT_TMUX_SESSION = "overcode"  # cli/split.py: the split layout's own session
CONTROLLER_TMUX_SESSION = "overcode-controller"  # the pre-0.6.0 `overcode supervisor` layout
LINKED_SESSION_PREFIX = "oc-view"  # cli/split.py


# ---------------------------------------------------------------------------
# One agent: kill, clean up, settle (AgentLauncher)
# ---------------------------------------------------------------------------

def window_lookup(windows: List[Dict[str, Any]]) -> Tuple[set, set]:
    """Build (names, index-strings) sets from a ``list_windows()`` result.

    Lets callers answer "does window X exist?" for many X from one tmux
    round-trip, with the same semantics as ``TmuxManager.window_exists``:
    exact name match, falling back to a legacy digit-string index match.
    """
    names = {w.get("name", "") for w in windows}
    indices = {str(w.get("index")) for w in windows if w.get("index") is not None}
    return names, indices


def window_in_lookup(window: str, lookup: Tuple[set, set]) -> bool:
    """Existence check against ``window_lookup`` output (see there)."""
    names, indices = lookup
    if window in names:
        return True
    return window.isdigit() and window in indices


@dataclass
class LifecycleReconcile:
    """What ``AgentLauncher.reconcile_lifecycle`` changed (agent names)."""

    terminated: List[str] = field(default_factory=list)
    done: List[str] = field(default_factory=list)
    waiting_oversight: List[str] = field(default_factory=list)
    refunds: List[Tuple[str, float]] = field(default_factory=list)


class AgentLifecycleMixin:
    """Kill agents and settle their lifecycle (see module docstring).

    Uses the launcher's ``tmux``, ``sessions``, ``_resolve`` and
    ``list_sessions``.
    """

    def reconcile_lifecycle(self) -> "LifecycleReconcile":
        """Settle every agent's lifecycle once, for a CLI run with no daemon.

        The monitor daemon does this every tick (``MonitorDaemon._settle_children``
        and its terminated detection); this is the same rule applied once,
        for ``overcode list`` / ``cleanup`` when no daemon is running to
        have done it. Never call it while the daemon runs: there must be one
        writer of lifecycle state.

        - A legacy digit window id is resolved to the window's name.
        - A child whose Stop hook fired becomes done (with a report) or
          waiting_oversight (without), whether or not its window is there.
        - Any other agent whose window is gone becomes terminated.
        - A child that just became done has its unused budget refunded to
          its parent (#432; idempotent, ``reclaim_budget`` caps the child).

        Writes sessions.json, prints nothing; returns what changed.
        """
        from .child_lifecycle import hook_stopped, settled_child_status

        result = LifecycleReconcile()
        my_sessions = self.list_sessions()
        tmux_windows = self._list_tmux_windows_cheap()
        self._migrate_legacy_window_ids(my_sessions, tmux_windows)
        existing = window_lookup(tmux_windows)
        for snapshot in my_sessions:
            session = self.sessions.get_session(snapshot.id) or snapshot
            if session.status == "done":
                continue
            stopped_child = (session.parent_session_id is not None
                             and hook_stopped(session.tmux_session, session.name, session.id))
            new_status = settled_child_status(session, stopped=stopped_child)
            # A stopped child is settled above, never terminated: its window
            # going is how a finished child ends (the daemon's
            # _is_waiting_on_oversight guard)
            if (new_status is None and not stopped_child
                    and session.status != "terminated"
                    and not window_in_lookup(session.tmux_window, existing)):
                new_status = "terminated"
            if new_status is None:
                continue
            self.sessions.update_session_status(session.id, new_status)
            if new_status == "terminated":
                result.terminated.append(session.name)
            elif new_status == "done":
                result.done.append(session.name)
                refunded = self.sessions.reclaim_budget(session.id)
                if refunded and refunded > 0:
                    result.refunds.append((session.name, refunded))
            else:
                result.waiting_oversight.append(session.name)
        return result

    def kill_untracked_windows(self) -> List[str]:
        """Kill this tmux session's windows no live agent is tracked in; their names.

        The same predicate the monitor daemon counts with (#344), so what it
        reports as untracked is exactly what this kills: never window 0
        (default shell), a live agent's window, or one of overcode's own
        (placeholder #457, supervisor claude, SSH proxies).
        """
        if not self.tmux.session_exists():
            return []
        tracked_windows = {s.tmux_window for s in self.list_sessions() if s.status != "terminated"}
        untracked = untracked_window_names(self.tmux.list_windows(), tracked_windows)
        for w_name in untracked:
            self.tmux.kill_window(w_name)
        return list(untracked)

    def cleanup_terminated_sessions(self) -> int:
        """Remove all terminated sessions from state.

        Returns:
            Number of sessions cleaned up
        """
        all_sessions = self.sessions.list_sessions()
        terminated = [s for s in all_sessions if s.status == "terminated"]

        # One commit for all of them (agent_bundle's write-ahead order)
        self.sessions.delete_sessions([s.id for s in terminated])
        self.tidy_agent_storage()

        return len(terminated)

    def tidy_agent_storage(self) -> None:
        """Orphaned agent directories and a bounded bundle prune (``SessionManager.tidy_agent_storage``)."""
        tidy = getattr(self.sessions, "tidy_agent_storage", None)
        if tidy is not None:
            tidy(self.tmux_session, prune_limit=31)

    def kill_session(self, name: str, cascade: bool = True) -> bool:
        """Kill a session by name.

        Handles both active sessions and stale sessions (where tmux window/session
        no longer exists, e.g., after a machine reboot).

        Args:
            name: Name of the session to kill
            cascade: If True (default), also kill all descendant agents.
                If False, orphan children (set their parent_session_id to None).

        Returns:
            False when ``name`` is not found or a window (its own or, with
            ``cascade``, a descendant's) could not be killed.
        """
        session = self._resolve(name)
        if session is None:
            print(f"Session '{name}' not found")
            return False

        # Handle cascade: kill descendants deepest-first (#244)
        descendants_ok = True
        if cascade:
            descendants = self.sessions.get_descendants(session.id)
            # Sort by depth (deepest first) for clean teardown
            descendants.sort(key=lambda s: self.sessions.compute_depth(s), reverse=True)
            # Every descendant is tried; any that would not die fails the kill
            descendants_ok = all([self._kill_single_session(desc) for desc in descendants])
        else:
            # Orphan children: set their parent_session_id to None
            children = self.sessions.get_children(session.id)
            for child in children:
                self.sessions.update_session(child.id, parent_session_id=None)

        killed = self._kill_single_session(session)
        self.tidy_agent_storage()
        return killed and descendants_ok

    def _kill_single_session(self, session: Session) -> bool:
        """Kill a single session (no cascade). Internal helper.

        Acts in the agent's own tmux session: looked for in this launcher's,
        a live window elsewhere reads as gone and only the record would go.
        """
        from .protocols import TmuxTimeoutError

        owner = self.for_agent(session)
        if owner is not self:
            return owner._kill_single_session(session)
        # Check if the tmux window/session still exists. A server too slow
        # to say is not "gone": the record stays, nothing is killed.
        try:
            window_exists = self.tmux.window_exists(session.tmux_window)
        except TmuxTimeoutError:
            print(f"Failed to kill '{session.name}': tmux is not responding; "
                  f"try again in a moment")
            return False

        if window_exists:
            # Active session - try to kill the tmux window
            if self.tmux.kill_window(session.tmux_window):
                self.sessions.delete_session(session.id)
                print(f"✓ Killed session '{session.name}'")
                return True
            else:
                print(f"Failed to kill tmux window for '{session.name}'")
                return False
        else:
            # Stale session - tmux window/session is already gone (e.g., after reboot)
            # Just clean up the state file
            self.sessions.delete_session(session.id)
            print(f"✓ Cleaned up stale session '{session.name}' (tmux window no longer exists)")
            return True

    def _list_tmux_windows_cheap(self) -> List[Dict[str, Any]]:
        """The tmux session's windows (``TmuxManager.list_windows``)."""
        return self.tmux.list_windows()

    def _migrate_legacy_window_ids(
        self, sessions: List[Session], windows: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        """Migrate legacy digit-string tmux_window values to actual window names.

        Legacy sessions stored the window index (int) which got converted to a
        digit string like "4". We resolve these to the actual window name by
        looking up the tmux window list by index.

        Args:
            windows: pre-fetched ``list_windows()`` result to avoid a second
                tmux round-trip; fetched here when omitted.
        """
        if windows is None:
            windows = self._list_tmux_windows_cheap()
        if not windows:
            return
        index_to_name = {str(w['index']): w['name'] for w in windows}

        for session in sessions:
            if session.tmux_window.isdigit() and session.tmux_window in index_to_name:
                # Written, not set on ``session``: it is the shared snapshot
                self.sessions.update_session(
                    session.id, tmux_window=index_to_name[session.tmux_window]
                )


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
    from .child_lifecycle import read_report
    try:
        return read_report(session.tmux_session, session.name, session.id) is not None
    except Exception:
        return False


def find_dead_agents(launcher) -> list:
    """Agents in the launcher's tmux session whose window is gone, parents first.

    Read-only: an agent is dead when its window is gone, whatever status
    sessions.json has caught up to (the daemon may not have marked it
    terminated yet, or may not be running). Skips done children and
    children that filed a report — auto-archived done agents are marked
    terminated too, and are finished, not dead.
    """
    sessions = launcher.list_sessions()
    existing = window_lookup(launcher._list_tmux_windows_cheap())
    dead = [
        s for s in sessions
        if s.status != "done"
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
    from .pid_utils import spawn_daemon

    if is_monitor_daemon_running(tmux_session):  # the daemon's lock decides
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
    """Tmux sessions with a state directory under the sessions root.

    Not the registry or jobs directory of a pre-0.6 ``OVERCODE_STATE_DIR``
    tree (``<root>/sessions``, ``<root>/jobs``), which sit among them.
    """
    from .paths import RESERVED_DIR_NAMES, jobs_dir, sessions_registry_dir, sessions_root
    root = sessions_root()
    not_sessions = {d.name for d in (sessions_registry_dir(), jobs_dir()) if d.parent == root}
    try:
        return sorted(p.name for p in root.iterdir()
                      if p.is_dir() and p.name not in RESERVED_DIR_NAMES
                      and p.name not in not_sessions)
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

    ``services_only`` stops just the supervisor, monitor and API server
    (what ``POST /api/shutdown`` does by default, e.g. before an upgrade).

    A pid file held by this very process (the API server serving
    /api/shutdown) is left for the caller: ``report.stop_self`` is set.
    The tmux session this process runs in is killed last, since that ends
    the process.
    """
    from .paths import overcode_dir
    from .settings import (
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
        stop_pid("daemons", f"API server [{ts}]", web_pid)
        if not dry_run and not report.stop_self and not web_pid.exists():
            get_web_server_port_path(ts).unlink(missing_ok=True)
    if not services_only:
        if shared:
            from .presence_logger import presence_pid_file
            stop_pid("daemons", "presence logger", presence_pid_file())
        else:
            add("daemons", "presence logger", "skipped",
                f"still used by {', '.join(others_alive)}")
        # PID files of the pre-per-session daemons, at the top of the tree
        for name in ("daemon.pid", "monitor_daemon.pid", "supervisor_daemon.pid"):
            legacy = overcode_dir() / name
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
            if keep_jobs and name == JOBS_TMUX_SESSION:
                continue  # phase 3 left it, as asked
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


def _keep_for_revive(launcher, session) -> None:
    """Mark a stopped agent terminated, and exempt its record from archiving.

    Any monitor daemon still running (another tmux session's, or one started
    after a reboot) archives terminated records after the grace; a record
    shutdown keeps for ``revive --all`` must outlive that.

    A finished child (done, or one that filed its report) is not kept:
    ``revive --all`` skips it, so nothing would ever clear the flag, and it
    would stay in sessions.json for good instead of being archived after
    the grace like any other terminated record.
    """
    finished = session.status == "done" or _has_report(session)
    launcher.sessions.update_session(
        session.id, status=STATUS_TERMINATED, kept_for_revive=not finished)


def _stop_agents(ts, launcher, ops: SystemOps, *, force, dry_run, timeout, add,
                 own_window: Optional[str] = None) -> None:
    """Exit the agents in one tmux session, deepest first, keeping records.

    An agent whose window runs this very process (an agent calling
    ``overcode shutdown``) can't be exited mid-run: its record is marked
    and its window goes with its tmux session, killed last.
    """
    from .protocols import TmuxTimeoutError

    sessions = [s for s in launcher.sessions.list_sessions() if s.tmux_session == ts]
    try:
        existing = window_lookup(launcher._list_tmux_windows_cheap())
    except TmuxTimeoutError:
        # Can't tell which windows are up, so none gets a graceful exit:
        # every record is kept, and the windows end with the tmux session,
        # killed below with the rest of the shutdown.
        if not dry_run:
            for s in sessions:
                if s.status != STATUS_TERMINATED:
                    _keep_for_revive(launcher, s)
        add("agents", f"agents [{ts}]", "deferred",
            "tmux not responding; records kept, windows end with the tmux session")
        return
    alive = [s for s in sessions if window_in_lookup(s.tmux_window, existing)]
    for s in [s for s in alive if own_window and s.tmux_window == own_window]:
        alive.remove(s)
        if not dry_run:
            _keep_for_revive(launcher, s)
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
            _keep_for_revive(launcher, s)
            if s.id in exited:
                add("agents", s.name, "exited", "window closed")
            elif force:
                add("agents", s.name, "killed" if killed else "failed", "window killed")
            else:
                add("agents", s.name, "killed" if killed else "failed",
                    f"no exit within {timeout:g}s; window killed")
