"""
Diagnose agents whose running claude process is missing overcode's hook
injection (#435). Used by `overcode doctor`.

An agent is "healthy" when its claude process was launched with --settings
(which contains the overcode hook commands). Agents whose claude process was
started manually in the tmux window — or whose last relaunch predates the
--settings injection commit (#435, 40d1376) — carry no hooks, so the monitor
daemon sees no state changes.
"""

import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Dict, List, Optional

from .session_manager import Session

if TYPE_CHECKING:
    from .history_reader import ClaudeSessionStats


VERDICT_OK = "ok"
VERDICT_MISSING_SETTINGS = "missing-settings"
VERDICT_NO_CLAUDE = "no-claude-process"
VERDICT_WINDOW_GONE = "window-gone"
VERDICT_REMOTE = "remote"
VERDICT_UNKNOWN = "unknown"


FINDING_TOKENS_ZERO = "tokens_zero"
FINDING_CONTEXT_ZERO = "context_zero"
FINDING_COST_ZERO = "cost_zero"
FINDING_SID_ORPHAN = "sid_orphan"
FINDING_DAEMON_DOWN = "daemon_down"
FINDING_STALE_ACTIVITY = "stale_activity"
FINDING_OVERSIGHT_OVERDUE = "oversight_overdue"
FINDING_SLEEP_BUT_ACTIVE = "sleep_but_active"
FINDING_HEARTBEAT_OVERDUE = "heartbeat_overdue"
FINDING_BUDGET_EXCEEDED = "budget_exceeded"
FINDING_SIDS_EMPTY = "sids_empty_with_interactions"
FINDING_MODEL_DRIFT = "model_drift"

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"

# last_activity older than this on a "running" agent is suspicious —
# a healthy monitor bumps it whenever a hook fires.
STALE_ACTIVITY_SECONDS = 4 * 3600

# current_state values that mean "agent is doing something" — if we see any
# of these while the session is flagged asleep, something is wrong.
ACTIVE_STATES = frozenset({
    "running",
    "running_heartbeat",
    "waiting_user",
    "waiting_approval",
    "waiting_heartbeat",
    "waiting_oversight",
})


@dataclass
class Finding:
    """One data-quality concern about an agent."""
    code: str        # e.g. FINDING_TOKENS_ZERO
    severity: str    # SEVERITY_ERROR | SEVERITY_WARNING
    message: str     # human-readable explanation


@dataclass
class AgentHealth:
    """Health snapshot for one agent's claude process."""
    name: str
    tmux_window: str
    launcher_version: str
    claude_pid: Optional[int]
    claude_argv: str
    verdict: str
    details: str
    data_findings: List[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.verdict == VERDICT_OK and not self.data_findings


def _snapshot_process_table() -> List[tuple[int, int, str]]:
    """Return a list of (pid, ppid, argv) for every process visible via `ps`.

    We rely on `ps -eo pid,ppid,args` rather than `pgrep -P` because pgrep's
    BSD variant on macOS has returned empty results in practice (observed
    inside Claude Code's sandboxed shell), while `ps` reliably exposes the
    full ppid graph. A single ps call also avoids an O(N) fork-storm.
    """
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,args="],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        return []
    rows: List[tuple[int, int, str]] = []
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        argv = parts[2] if len(parts) >= 3 else ""
        rows.append((pid, ppid, argv))
    return rows


def _build_child_index(
    rows: List[tuple[int, int, str]],
) -> tuple[Dict[int, List[int]], Dict[int, str]]:
    """Index `rows` into {ppid -> [child pids]} and {pid -> argv}."""
    children: Dict[int, List[int]] = {}
    argv_by_pid: Dict[int, str] = {}
    for pid, ppid, argv in rows:
        children.setdefault(ppid, []).append(pid)
        argv_by_pid[pid] = argv
    return children, argv_by_pid


def get_descendant_pids(
    root_pid: int,
    children: Dict[int, List[int]],
    max_depth: int = 6,
) -> List[int]:
    """BFS the process tree under `root_pid` using a precomputed child index.

    Does not include root_pid itself. Bounded depth prevents runaway on
    pathological trees.
    """
    seen: set[int] = set()
    frontier = [(root_pid, 0)]
    descendants: List[int] = []
    while frontier:
        pid, depth = frontier.pop()
        if depth >= max_depth:
            continue
        for child in children.get(pid, []):
            if child in seen:
                continue
            seen.add(child)
            descendants.append(child)
            frontier.append((child, depth + 1))
    return descendants


def find_claude_process(
    pane_pid: int,
    children: Dict[int, List[int]],
    argv_by_pid: Dict[int, str],
) -> tuple[Optional[int], str]:
    """Find the claude process in the tmux pane's process tree.

    Returns (pid, argv) of the first descendant whose argv's first token
    is `claude` (or an absolute path ending in `/claude`). Returns
    (None, '') if no claude process is found under pane_pid.

    We search by argv rather than by stored PID because the user may have
    manually relaunched claude, in which case any PID we recorded at launch
    is stale — and that's precisely the case we want to diagnose.
    """
    for pid in get_descendant_pids(pane_pid, children):
        argv = argv_by_pid.get(pid, "")
        if not argv:
            continue
        first_token = argv.split(None, 1)[0]
        basename = first_token.rsplit("/", 1)[-1]
        if basename == "claude":
            return pid, argv
    return None, ""


def gather_data_findings(
    session: Session,
    live_stats: Optional["ClaudeSessionStats"] = None,
    *,
    daemon_running: bool = True,
    now: Optional[datetime] = None,
    stale_threshold_seconds: float = STALE_ACTIVITY_SECONDS,
) -> List[Finding]:
    """Return data-quality concerns for one agent.

    These checks catch "the monitor isn't wiring data through" bugs that
    a clean hook verdict alone would miss — e.g. a zeroed token count
    despite visible interactions, an orphaned active_claude_session_id,
    or a dead monitor daemon starving all agents at once.

    Checks are self-gating: the function is safe to call on any session
    (including ones with no live_stats); conditions that require data
    simply don't fire when the data is absent.
    """
    findings: List[Finding] = []
    now_dt = now or datetime.now()

    if not daemon_running:
        findings.append(Finding(
            code=FINDING_DAEMON_DOWN,
            severity=SEVERITY_ERROR,
            message=(
                f"monitor daemon not running for tmux session "
                f"'{session.tmux_session}' — stats won't update"
            ),
        ))

    active = session.active_claude_session_id
    if active and active not in session.claude_session_ids:
        findings.append(Finding(
            code=FINDING_SID_ORPHAN,
            severity=SEVERITY_WARNING,
            message=(
                f"active_claude_session_id {active[:8]}… not in tracked "
                f"claude_session_ids — context window may be miscalculated"
            ),
        ))

    if live_stats is not None:
        if live_stats.interaction_count >= 2 and live_stats.total_tokens == 0:
            findings.append(Finding(
                code=FINDING_TOKENS_ZERO,
                severity=SEVERITY_WARNING,
                message=(
                    f"{live_stats.interaction_count} interactions but "
                    f"0 total tokens — token parser may be broken"
                ),
            ))
        if live_stats.total_tokens > 0 and live_stats.current_context_tokens == 0:
            findings.append(Finding(
                code=FINDING_CONTEXT_ZERO,
                severity=SEVERITY_WARNING,
                message=(
                    f"{live_stats.total_tokens} tokens used but "
                    f"current_context_tokens is 0"
                ),
            ))
        if live_stats.total_tokens > 1000 and session.stats.estimated_cost_usd == 0:
            findings.append(Finding(
                code=FINDING_COST_ZERO,
                severity=SEVERITY_WARNING,
                message=(
                    f"{live_stats.total_tokens} tokens used but "
                    f"estimated_cost is $0.00 — cost tracker may be stuck"
                ),
            ))

    if (
        session.status == "running"
        and session.stats.last_activity
        and not session.is_asleep
    ):
        try:
            last = datetime.fromisoformat(session.stats.last_activity)
            age = (now_dt - last).total_seconds()
            if age > stale_threshold_seconds:
                findings.append(Finding(
                    code=FINDING_STALE_ACTIVITY,
                    severity=SEVERITY_WARNING,
                    message=(
                        f"last_activity {int(age / 60)} min ago — "
                        f"hooks may not be firing"
                    ),
                ))
        except (ValueError, TypeError):
            pass

    if session.status == "running" and session.oversight_deadline:
        try:
            deadline = datetime.fromisoformat(session.oversight_deadline)
            overdue = (now_dt - deadline).total_seconds()
            if overdue > 0:
                findings.append(Finding(
                    code=FINDING_OVERSIGHT_OVERDUE,
                    severity=SEVERITY_WARNING,
                    message=(
                        f"oversight_deadline passed {int(overdue / 60)} min "
                        f"ago but status is still 'running'"
                    ),
                ))
        except (ValueError, TypeError):
            pass

    # An asleep agent whose hook-derived state says it's still active is a
    # desync bug: either the sleep flag was set without quiescing the agent,
    # or a hook fired without clearing the flag. Either way, treat as error
    # since it can cause the agent to keep spending while reported "asleep".
    if session.is_asleep and session.stats.current_state in ACTIVE_STATES:
        findings.append(Finding(
            code=FINDING_SLEEP_BUT_ACTIVE,
            severity=SEVERITY_ERROR,
            message=(
                f"marked asleep but current_state is "
                f"'{session.stats.current_state}' — a sleeping agent "
                f"should not be active"
            ),
        ))

    if (
        session.heartbeat_enabled
        and not session.heartbeat_paused
        and not session.is_asleep
        and session.status == "running"
        and session.last_heartbeat_time
        and session.heartbeat_frequency_seconds > 0
    ):
        try:
            last_hb = datetime.fromisoformat(session.last_heartbeat_time)
            overdue_by = (
                (now_dt - last_hb).total_seconds()
                - session.heartbeat_frequency_seconds
            )
            # 2× cadence guards against one-cycle slop from scheduling jitter.
            if overdue_by > session.heartbeat_frequency_seconds:
                findings.append(Finding(
                    code=FINDING_HEARTBEAT_OVERDUE,
                    severity=SEVERITY_WARNING,
                    message=(
                        f"heartbeat overdue by {int(overdue_by / 60)} min "
                        f"(cadence {session.heartbeat_frequency_seconds}s)"
                    ),
                ))
        except (ValueError, TypeError):
            pass

    if (
        session.cost_budget_usd > 0
        and session.stats.estimated_cost_usd > session.cost_budget_usd
        and session.status == "running"
    ):
        findings.append(Finding(
            code=FINDING_BUDGET_EXCEEDED,
            severity=SEVERITY_ERROR,
            message=(
                f"cost ${session.stats.estimated_cost_usd:.2f} exceeds "
                f"budget ${session.cost_budget_usd:.2f} but agent is still running"
            ),
        ))

    # claude_session_ids should accumulate one sid per Claude Code session
    # owned by this agent. If we see interactions via directory+timestamp
    # fallback matching but none are tracked, the SessionStart hook likely
    # never wired this agent to its sid — context window calc is then wrong.
    if (
        live_stats is not None
        and live_stats.interaction_count > 0
        and not session.claude_session_ids
        and session.status == "running"
    ):
        findings.append(Finding(
            code=FINDING_SIDS_EMPTY,
            severity=SEVERITY_WARNING,
            message=(
                f"{live_stats.interaction_count} interactions detected but "
                f"claude_session_ids is empty — falling back to directory matching"
            ),
        ))

    if (
        live_stats is not None
        and session.model
        and getattr(live_stats, "model", None)
        and session.model.lower() not in live_stats.model.lower()
    ):
        findings.append(Finding(
            code=FINDING_MODEL_DRIFT,
            severity=SEVERITY_WARNING,
            message=(
                f"launched with model '{session.model}' but Claude Code "
                f"session reports '{live_stats.model}'"
            ),
        ))

    return findings


def inspect_agent(
    session: Session,
    pane_pid: Optional[int],
    children: Dict[int, List[int]],
    argv_by_pid: Dict[int, str],
    *,
    live_stats: Optional["ClaudeSessionStats"] = None,
    daemon_running: bool = True,
    now: Optional[datetime] = None,
) -> AgentHealth:
    """Diagnose one agent's claude process for --settings health.

    `pane_pid` is the PID of the tmux pane's shell process, or None if the
    window is gone. `children`/`argv_by_pid` come from a single
    `snapshot_process_table()` call so that inspecting N agents does not
    spawn N*M subprocesses.

    `live_stats` and `daemon_running` feed the data-quality checks
    (zero tokens, empty context, dead monitor, etc.). Both are optional
    so call sites that only want the hook verdict can omit them.
    """
    base = dict(
        name=session.name,
        tmux_window=session.tmux_window,
        launcher_version=session.launcher_version or "",
    )

    if getattr(session, "is_remote", False):
        return AgentHealth(
            **base,
            claude_pid=None,
            claude_argv="",
            verdict=VERDICT_REMOTE,
            details="remote agent — cannot inspect locally",
        )

    findings = gather_data_findings(
        session, live_stats, daemon_running=daemon_running, now=now,
    )

    if pane_pid is None:
        return AgentHealth(
            **base,
            claude_pid=None,
            claude_argv="",
            verdict=VERDICT_WINDOW_GONE,
            details="tmux window no longer exists",
            data_findings=findings,
        )

    claude_pid, argv = find_claude_process(pane_pid, children, argv_by_pid)
    if claude_pid is None:
        return AgentHealth(
            **base,
            claude_pid=None,
            claude_argv="",
            verdict=VERDICT_NO_CLAUDE,
            details="no claude process under pane — agent may have exited",
            data_findings=findings,
        )

    if "--settings" in argv:
        return AgentHealth(
            **base,
            claude_pid=claude_pid,
            claude_argv=argv,
            verdict=VERDICT_OK,
            details="hooks injected via --settings",
            data_findings=findings,
        )

    return AgentHealth(
        **base,
        claude_pid=claude_pid,
        claude_argv=argv,
        verdict=VERDICT_MISSING_SETTINGS,
        details=(
            "claude running without --settings — hooks will not fire. "
            "Relaunch via `overcode restart` to re-inject."
        ),
        data_findings=findings,
    )


def snapshot_process_table() -> tuple[Dict[int, List[int]], Dict[int, str]]:
    """Public wrapper used by the CLI — snapshot once, index once."""
    rows = _snapshot_process_table()
    return _build_child_index(rows)
