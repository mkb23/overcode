"""Unified hook handler for every backend's hook events.

A single command (`overcode hook-handler`) handles all hook
events. It reads stdin JSON from the agent CLI, translates it into Claude
Code's vocabulary (``hook_dialects``), writes state files for hook-based
status detection, and outputs enhanced context for UserPromptSubmit events.
"""

import json
import logging
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from .paths import agent_dir, agent_file, legacy_agent_file
from .tmux_utils import AGENT_ID_OPTION
from . import conversation_warning, hook_dialects
from . import swarm

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:  # pragma: no cover - Windows
    HAS_FCNTL = False

logger = logging.getLogger(__name__)


# =============================================================================
# Obligation tracking (#TBD — two-column status model)
# =============================================================================
#
# An "obligation" is a pending thing that will wake the agent WITHOUT a human
# touching it. While obligations are non-empty after Stop, the agent is in
# the YELLOW "armed" bucket, not RED "needs input". We maintain the list in
# the hook_state file so the detector can read it without re-parsing every
# event.
#
# Arming: Bash only with run_in_background. Agent runs in the background
# unless run_in_background is false (the default since Claude Code 2.1.300:
# a plain call returns "async_launched"), and Workflow always does (it has
# no such flag); both arm until their launch call returns. From there the
# work is tracked by agent id and workflow run (``swarm``), reconciled at
# each Stop/SubagentStop against Claude's own background_tasks list.
#
# Disarm rules:
#   - Bash/Agent/Workflow in the background      → PostToolUse(same id) removes
#   - Monitor                                    → PostToolUse(same id) removes
#   - ScheduleWakeup                             → next UserPromptSubmit
#                                                   (wakeup fires as a prompt),
#                                                   a newer ScheduleWakeup, or
#                                                   ScheduleWakeup(stop: true)
#   - CronCreate                                 → CronDelete(matching id) or
#                                                   SessionEnd
#   - TaskCreate                                 → PostToolUse(same id) plus
#                                                   any later TaskStop (not
#                                                   yet wired)

# tool_name → obligation kind for tools that arm unconditionally
_UNCONDITIONAL_ARMING_TOOLS: dict[str, str] = {
    "ScheduleWakeup": "schedule_wakeup",
    "CronCreate":     "cron",
    "Monitor":        "monitor",
    "TaskCreate":     "bg_task",
}

# Tools that arm ONLY when tool_input.run_in_background is truthy
_CONDITIONAL_BG_TOOLS: frozenset[str] = frozenset({"Bash"})
# Tools that run in the background unless run_in_background is false
_DEFAULT_BG_TOOLS: frozenset[str] = frozenset({"Agent", "Task", "Workflow"})

# Obligation kinds that represent *persistent* state registered with the
# system. Their PostToolUse means "the registration call completed" not
# "the obligation is done" — they require an explicit disarm signal
# (CronDelete for cron, UserPromptSubmit for schedule_wakeup, SessionEnd
# for anything still pending).
_PERSISTENT_OBLIGATION_KINDS: frozenset[str] = frozenset({"cron", "schedule_wakeup"})


def _obligation_kind(tool_name: str | None, tool_input: dict | None) -> str | None:
    """Classify what kind of obligation (if any) this PreToolUse arms."""
    if not tool_name:
        return None
    if tool_name in _UNCONDITIONAL_ARMING_TOOLS:
        return _UNCONDITIONAL_ARMING_TOOLS[tool_name]
    if tool_name in _CONDITIONAL_BG_TOOLS:
        if isinstance(tool_input, dict) and tool_input.get("run_in_background"):
            return "bg_task"
    if tool_name in _DEFAULT_BG_TOOLS:
        if not (isinstance(tool_input, dict) and tool_input.get("run_in_background") is False):
            return "bg_task"
    return None


def _obligation_label(kind: str, tool_input: dict | None) -> str | None:
    """Best-effort human-readable suffix for an obligation."""
    if not isinstance(tool_input, dict):
        return None
    if kind == "schedule_wakeup":
        # ScheduleWakeup tool input typically has delaySeconds + reason
        secs = tool_input.get("delaySeconds")
        if isinstance(secs, (int, float)) and secs > 0:
            return f"in {int(secs)}s"
    if kind == "cron":
        # CronCreate exposes a `schedule` field
        sched = tool_input.get("schedule") or tool_input.get("cron")
        if isinstance(sched, str):
            return sched
    if kind == "bg_task":
        # Best label is the tool that spawned it + a hint
        cmd = tool_input.get("command") or tool_input.get("prompt") or ""
        if isinstance(cmd, str) and cmd:
            return cmd[:40]
    return None


def _obligation_eta_seconds(kind: str, tool_input: dict | None) -> float | None:
    """Seconds-until-fire for wake-time obligations, or None."""
    if kind == "schedule_wakeup" and isinstance(tool_input, dict):
        secs = tool_input.get("delaySeconds")
        if isinstance(secs, (int, float)) and secs > 0:
            return float(secs)
    return None


# Classify foreground Bash commands that block on something external. The
# foreground tool is still genuinely RUNNING — we just want the column-2
# badge to say *why* the agent looks stalled.
_BLOCKED_ON_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("ci",      re.compile(r"\b(gh\s+run\s+watch|gh\s+pr\s+checks\s+--watch)\b")),
    ("process", re.compile(r"\b(tail\s+-[fF]|kubectl\s+wait|docker\s+wait|wait-on)\b")),
    ("sleep",   re.compile(r"^\s*sleep\s+\d")),
]


def _classify_foreground_blocked_on(command: str) -> str | None:
    """Identify what a foreground Bash command is blocked on, or None."""
    for kind, pattern in _BLOCKED_ON_PATTERNS:
        if pattern.search(command):
            return kind
    return None


def _compute_foreground(event: str, tool_name: str | None, tool_input: dict | None) -> dict | None:
    """Build a foreground-detail dict for the current event.

    Only meaningful while a tool call is in flight. Returns None outside that
    window so the reducer can fall back to other signals.
    """
    if event != "PreToolUse" or not tool_name:
        return None
    fg: dict = {"kind": "tool", "tool": tool_name}
    if tool_name == "Bash" and isinstance(tool_input, dict):
        cmd = tool_input.get("command") or ""
        if isinstance(cmd, str):
            blocked_on = _classify_foreground_blocked_on(cmd)
            if blocked_on:
                fg["blocked_on"] = blocked_on
    return fg


def _update_obligations(
    prev_obligations: list[dict],
    event: str,
    tool_name: str | None,
    tool_input: dict | None,
    tool_use_id: str | None,
    now: float,
) -> list[dict]:
    """Return the new obligation list after applying this event.

    Pure function — no I/O. Disarm rules described at module top.
    """
    obligations = list(prev_obligations)

    if event == "PreToolUse":
        # CronDelete tears down a specific cron by id
        if tool_name == "CronDelete" and isinstance(tool_input, dict):
            target = tool_input.get("cron_id") or tool_input.get("id")
            if target:
                obligations = [
                    o for o in obligations
                    if not (o.get("kind") == "cron" and o.get("cron_id") == target)
                ]
            return obligations

        if tool_name == "ScheduleWakeup":
            # One pending wakeup at a time: a new one replaces the last, and
            # ScheduleWakeup(stop: true) is how a /loop ends, arming nothing
            obligations = [o for o in obligations if o.get("kind") != "schedule_wakeup"]
            if isinstance(tool_input, dict) and tool_input.get("stop"):
                return obligations

        kind = _obligation_kind(tool_name, tool_input)
        if not kind:
            return obligations
        entry: dict = {
            "kind": kind,
            "added_at": now,
        }
        if tool_use_id:
            entry["tool_use_id"] = tool_use_id
        label = _obligation_label(kind, tool_input)
        if label:
            entry["label"] = label
        eta = _obligation_eta_seconds(kind, tool_input)
        if eta is not None:
            entry["eta_seconds"] = eta
            entry["eta_absolute"] = now + eta
        # For CronCreate, capture the id so a later CronDelete can target it
        if kind == "cron" and isinstance(tool_input, dict):
            cron_id = tool_input.get("id") or tool_input.get("cron_id")
            if cron_id:
                entry["cron_id"] = cron_id
        obligations.append(entry)
        return obligations

    if event in ("PostToolUse", "PostToolUseFailure"):
        # Persistent obligations (cron / schedule_wakeup) survive PostToolUse
        # — they require their dedicated disarm signal.
        if tool_use_id:
            obligations = [
                o for o in obligations
                if not (
                    o.get("tool_use_id") == tool_use_id
                    and o.get("kind") not in _PERSISTENT_OBLIGATION_KINDS
                )
            ]
        elif tool_name:
            # No id — fall back to LIFO match on tool's expected obligation kind
            kind = _obligation_kind(tool_name, tool_input)
            if kind and kind not in _PERSISTENT_OBLIGATION_KINDS:
                for i in range(len(obligations) - 1, -1, -1):
                    if obligations[i].get("kind") == kind:
                        obligations.pop(i)
                        break
        return obligations

    if event == "UserPromptSubmit":
        # ScheduleWakeup fires as a synthetic prompt — drop those.
        obligations = [o for o in obligations if o.get("kind") != "schedule_wakeup"]
        return obligations

    if event == "SessionEnd":
        return []

    return obligations


# =============================================================================
# Subagents (#507)
# =============================================================================
#
# Claude Code tags every hook a subagent fires with ``agent_id``: its own tool
# calls as well as the SubagentStart/SubagentStop pair that brackets it
# (live-verified on 2.1.286). None of that is the parent's turn. A background
# subagent keeps calling tools long after the parent's Stop, and writing those
# events into the parent's snapshot showed an idle parent as working. So a
# subagent event only maintains the parent's ``subagents`` map, which the
# detector reads as work in flight. The one exception is a subagent's
# permission prompt: it is answered in the parent's pane, so it surfaces as
# the parent's PermissionRequest until that subagent's next event, which puts
# back whatever the parent was doing before.

# Snapshot fields that describe the parent's current event
_EVENT_STATE_KEYS = ("event", "timestamp", "tool_name", "tool_input", "tool_use_id", "foreground")


def _store_swarm(state: dict, subagents: dict, swarm_state: dict) -> None:
    """Put the live map and the swarm (see ``swarm``) into a hook state."""
    for key, value in (("subagents", subagents), ("swarm", swarm_state)):
        if value:
            state[key] = value
        else:
            state.pop(key, None)


def _apply_subagent_event(prev: dict, event: str, agent_id: str, data: dict, now: float) -> dict:
    """Return the parent's hook state after one of its subagents' events.

    The live map and the 👥 rollup's record are ``swarm``'s; this adds the
    permission lift. Pure function — no I/O.
    """
    state = dict(prev)
    _store_swarm(state, *swarm.apply_subagent_event(
        state.get("subagents"), state.get("swarm"), event, agent_id, data, now))

    if event == "PermissionRequest":
        if state.get("permission_agent_id") is None:
            state["before_permission"] = {k: prev[k] for k in _EVENT_STATE_KEYS if k in prev}
        for k in _EVENT_STATE_KEYS:
            state.pop(k, None)
        state["event"] = event
        state["timestamp"] = now
        for k in ("tool_name", "tool_input", "tool_use_id"):
            if data.get(k) is not None:
                state[k] = data[k]
        state["permission_agent_id"] = agent_id
    elif state.get("permission_agent_id") == agent_id:
        before = state.pop("before_permission", None) or {}
        state.pop("permission_agent_id", None)
        for k in _EVENT_STATE_KEYS:
            state.pop(k, None)
        state.update(before)
    return state


def _write_state_file(path: Path, state: dict) -> None:
    """Replace the hook-state file atomically.

    The detector reads this file on every tick; a reader landing mid-write
    would see truncated JSON and report "Waiting for first hook event" for
    that tick.
    """
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state))
    os.replace(tmp, path)


# All hooks that overcode installs for Claude Code (via --settings).
# Claude also gets SessionStart, but only for these sources (a matcher on the
# --settings registration; see claude_code._build_launch_settings): /clear and
# /resume are how a Claude agent changes conversation, and "startup" is left
# out so a nested `claude -p` never fires it (#500). Not in OVERCODE_HOOKS,
# whose (event, command) pairs the legacy global `hooks install` also writes.
CLAUDE_SESSION_START_MATCHER = "clear|resume"
# And Notification, for idle_prompt only: the backstop for a turn that ends
# with no Stop (see _settle_idle_prompt).
CLAUDE_NOTIFICATION_MATCHER = "idle_prompt"

OVERCODE_HOOKS: list[tuple[str, str]] = [
    ("UserPromptSubmit", "overcode hook-handler"),
    ("PreToolUse", "overcode hook-handler"),
    ("PostToolUse", "overcode hook-handler"),
    ("PostToolUseFailure", "overcode hook-handler"),
    ("Stop", "overcode hook-handler"),
    ("StopFailure", "overcode hook-handler"),
    ("PermissionRequest", "overcode hook-handler"),
    ("SessionEnd", "overcode hook-handler"),
    # Bracket each subagent, background ones included (#507)
    ("SubagentStart", "overcode hook-handler"),
    ("SubagentStop", "overcode hook-handler"),
    # An in-process teammate went idle: listening, not working (0.6.1)
    ("TeammateIdle", "overcode hook-handler"),
]

# Retention window for the hook-state session-id history. Every backend that
# sends a session id on its hook events gets one recorded (see
# handle_hook_event); codex's SessionStart is simply where its first one
# arrives. Capped the same way opencode's JS plugin caps agent_session_ids, so
# a long-lived agent that keeps starting new conversations doesn't grow the
# state file without bound.
_MAX_AGENT_SESSION_IDS = 20

# Other conversations whose hooks ran under this agent's name, kept so the
# engine can tell which agent a Claude background session came from
# (claude_bg.attribute): the newest few, each with when it was last seen,
# whether it is a registered background session (``bg``) and how it was
# placed (``kind``, below). One not yet placed for good is looked at again
# at most this often (a background copy can register late).
_MAX_FOREIGN_SESSIONS = 20
_FOREIGN_RECHECK_SECONDS = 30.0

# How a foreign conversation was placed (``foreign_sessions[sid]["kind"]``).
# Only the agent's own CLI may steer its status, obligations and swarm:
#
# * ``bg``       a registered Claude background session (``claude_bg``)
# * ``continued`` the agent's own conversation moved into Claude's background
#                worker (``claude_bg.continues``), or moved back to the CLI in
#                its pane: this IS the agent
# * ``nested``   a Claude the agent's own Claude started (``claude -p`` in
#                its Bash tool inherits the agent's OVERCODE_* environment):
#                its CLI process descends from the agent's
# * ``own``      the agent's own CLI process (``/resume`` into a
#                conversation overcode never saw), or a new top-level CLI
#                in its name after the agent's own exited: this IS the agent
# * ``unplaced`` neither can be shown: its events are ignored and the agent
#                carries a warning
#
# The discriminator is the CLI's process id: Claude Code gives every hook
# ``CLAUDE_PID`` (its own ``process.pid``), and ``/resume`` and ``/clear``
# switch conversations inside the same process. A backend whose hooks carry
# no such id (codex, opencode, grok, hermes) is not placed: its foreign
# events behave as they always have (#500).
FOREIGN_BG = "bg"
FOREIGN_CONTINUED = "continued"
FOREIGN_NESTED = "nested"
FOREIGN_OWN = "own"
FOREIGN_UNPLACED = "unplaced"
_SETTLED_KINDS = frozenset({FOREIGN_BG, FOREIGN_CONTINUED, FOREIGN_NESTED, FOREIGN_OWN})

# The warning an agent carries when a conversation overcode never saw ran
# under its name (``conversation_warning``; see that module)
WARN_ADOPTED = conversation_warning.ADOPTED
WARN_SHARED = conversation_warning.SHARED
WARN_UNPLACED = conversation_warning.UNPLACED


def _cli_pid() -> int | None:
    """The process id of the Claude CLI that ran this hook (``CLAUDE_PID``), or None."""
    try:
        pid = int(os.environ.get("CLAUDE_PID") or 0)
    except ValueError:
        return None
    return pid if pid > 1 else None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _descends_from(pid: int, ancestor: int) -> bool | None:
    """Whether ``ancestor`` is among ``pid``'s parents; None if the table can't be read."""
    from .process_resources import snapshot_processes

    table = snapshot_processes()
    if not table:
        return None
    current, seen = pid, set()
    while current > 1 and current not in seen and len(seen) < 64:
        seen.add(current)
        info = table.get(current)
        if info is None:
            return False
        if info.ppid == ancestor:
            return True
        current = info.ppid
    return False


def _place(entry: dict, cli_pid: int | None, agent_pid: int | None,
           pane_pid: int | None = None) -> str | None:
    """How a foreign conversation's CLI relates to the agent's own (see FOREIGN_*).

    ``pane_pid`` is the CLI in the agent's pane while its conversation runs
    in Claude's background worker (``bg_worker`` in hook state).
    """
    if entry.get("bg"):
        return FOREIGN_CONTINUED if entry.get("continues") else FOREIGN_BG
    if cli_pid is None:
        return None  # no process id to tell by: behave as before
    if pane_pid is not None and cli_pid == pane_pid:
        return FOREIGN_CONTINUED  # back from the worker to the agent's own pane
    if agent_pid is not None and cli_pid == agent_pid:
        return FOREIGN_OWN
    if agent_pid is None:
        return FOREIGN_UNPLACED  # the agent's own CLI has not been seen yet
    if not _pid_alive(agent_pid):
        # The agent's CLI is gone, so this one cannot be running inside it:
        # a CLI started by hand in the agent's pane, in its name
        return FOREIGN_OWN
    nested = _descends_from(cli_pid, agent_pid)
    if nested:
        return FOREIGN_NESTED
    return FOREIGN_UNPLACED


def _note_foreign_session(prev_state: dict, session_id: str, now: float,
                          cli_pid: int | None = None) -> dict:
    """``prev_state``'s foreign conversations with ``session_id`` (seen ``now``) in it.

    The entry is placed (``kind``) the first time it is seen and again at
    most every ``_FOREIGN_RECHECK_SECONDS`` until it settles; ``cli_pid``
    is the CLI that sent this event, compared with the agent's own
    (``cli_pid`` in its hook state).
    """
    from .claude_bg import continues, find_job

    known = prev_state.get("foreign_sessions")
    foreign = dict(known) if isinstance(known, dict) else {}
    entry = dict(foreign.get(session_id) or {})
    due = now - float(entry.get("checked") or 0.0) >= _FOREIGN_RECHECK_SECONDS
    job = None
    if due and (not entry.get("bg") or "continues" not in entry):
        try:
            job = find_job(session_id)
        except Exception:  # noqa: BLE001 — a hook must never fail the agent's turn
            job = None
        entry["bg"] = entry.get("bg") or job is not None
    if job is not None and "continues" not in entry:
        try:
            continued = continues(job, prev_state.get("agent_session_id"))
        except Exception:  # noqa: BLE001
            continued = None
        if continued is not None:
            entry["continues"] = continued
    if not _settled(entry) and (due or "kind" not in entry):
        try:
            kind = _place(entry, cli_pid, _int_or_none(prev_state.get("cli_pid")),
                          _pane_pid(prev_state))
        except Exception:  # noqa: BLE001
            kind = None
        if kind is None:
            entry.pop("kind", None)
        else:
            entry["kind"] = kind
            if cli_pid is not None:
                entry["pid"] = cli_pid
    if due:
        entry["checked"] = now
    entry["at"] = now
    foreign[session_id] = entry
    if len(foreign) > _MAX_FOREIGN_SESSIONS:
        newest = sorted(foreign, key=lambda k: float(foreign[k].get("at") or 0.0))
        for stale in newest[:-_MAX_FOREIGN_SESSIONS]:
            del foreign[stale]
    return foreign


def _settled(entry: dict) -> bool:
    """Whether a foreign conversation's placement is final.

    A background session is not until it is known not to continue the
    agent's own conversation (an entry written before that was asked).
    """
    kind = entry.get("kind")
    return kind in _SETTLED_KINDS and not (kind == FOREIGN_BG
                                           and entry.get("continues") is not False)


def _pane_pid(state: dict) -> int | None:
    """The CLI in the agent's pane while its conversation is in the background worker."""
    note = state.get("bg_worker")
    return _int_or_none(note.get("pane_pid")) if isinstance(note, dict) else None


def _int_or_none(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _conversation_owner(session_id: str, session_name: str) -> str:
    """The name of another live agent that owns ``session_id``, or ``""``."""
    try:
        from .claude_pid import is_session_id_owned_by_others
        from .session_manager import SessionManager

        sm = SessionManager(skip_git_detection=True)
        sessions = sm.list_sessions()
        own = _own_session(sm, session_name)
        if own is None or not is_session_id_owned_by_others(session_id, own.id, sessions):
            return ""
        for s in sessions:
            if s.id != own.id and session_id in (getattr(s, "agent_session_ids", None) or []):
                return s.name
    except Exception:  # noqa: BLE001 — a hook must never fail the agent's turn
        return ""
    return ""


def _detect_from_tmux_pane() -> tuple[str | None, str | None, str | None]:
    """Detect the agent and tmux session from the current tmux pane.

    Fallback for when OVERCODE_SESSION_NAME / OVERCODE_TMUX_SESSION env vars
    are missing (e.g. after a manual session restart with --session-id).

    The agent's id is the window's ``@overcode_id`` option (set at launch,
    kept by a rename). The name is parsed from the window name, for an
    agent whose window predates the option and for its name-keyed files.

    Returns (session_name, tmux_session, session_id); (None, None, None)
    if detection fails, and session_id None when the window has no option.
    """
    pane_id = os.environ.get("TMUX_PANE")
    if not pane_id:
        return None, None, None
    try:
        out = subprocess.run(
            ["tmux", "display-message", "-p", "-t", pane_id,
             f"#{{session_name}}\t#{{{AGENT_ID_OPTION}}}\t#{{window_name}}"],
            capture_output=True, text=True, timeout=2,
        ).stdout.rstrip("\n")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None, None, None
    tmux_session, session_id, window_name = (out.split("\t", 2) + ["", ""])[:3]

    if not window_name or not tmux_session:
        return None, None, None

    # Strip oc-view- prefix from split-view session names
    if tmux_session.startswith("oc-view-"):
        tmux_session = tmux_session[len("oc-view-"):]

    # Window names are "agentname-XXXX" where XXXX is a UUID prefix
    # Strip the last "-XXXX" suffix to get the agent name
    dash_idx = window_name.rfind("-")
    if dash_idx > 0:
        session_name = window_name[:dash_idx]
    else:
        session_name = window_name

    return session_name, tmux_session, session_id.strip() or None


def _own_session(sm, session_name: str):
    """This agent's record in ``sm``: by its id, else (pre-0.6) by ``session_name``.

    The id survives a rename; the launch name in the environment does not
    (auto-naming §1). An agent with no id is found by name, following renames.
    """
    sid = _agent_session_id()
    if sid:
        return sm.get_session(sid)
    return sm.resolve_session_name(session_name)


def _agent_session_id() -> str | None:
    """This agent's overcode session id (``OVERCODE_SESSION_ID``), or None."""
    return os.environ.get("OVERCODE_SESSION_ID") or None


def _session_id_for_name(tmux_session: str, session_name: str) -> str | None:
    """The overcode session id of the live agent ``session_name``, from sessions.json.

    For the pane-detection fallback, whose environment lost the id.
    """
    try:
        from .session_manager import SessionManager

        for session in SessionManager(skip_git_detection=True).list_sessions():
            if session.name == session_name and session.tmux_session == tmux_session:
                return session.id
    except Exception:  # never fail a hook over the lookup
        return None
    return None


def agent_file_for_write(tmux_session: str, session_name: str, kind: str,
                         session_id: str | None = None) -> Path:
    """Where the agent's ``kind`` file is written (item 35).

    The agent's own directory, ``agents/<session id>/`` (``session_id``,
    else ``OVERCODE_SESSION_ID``): every launch creates it, so a new agent
    never reads what an earlier agent of its name left. The name-keyed
    file is kept for an agent with no session id in its environment, and
    for an agent launched before per-agent directories (no directory, but
    a name-keyed hook state file) until it is relaunched. A hook arriving
    after the agent was archived and its directory removed recreates it;
    the orphan sweep removes it again.
    """
    sid = session_id or _agent_session_id()
    if sid:
        if agent_dir(tmux_session, sid).is_dir() or not legacy_agent_file(
                tmux_session, session_name, "hook_state").exists():
            return agent_file(tmux_session, sid, kind)
    return legacy_agent_file(tmux_session, session_name, kind)


def _get_hook_state_path(tmux_session: str, session_name: str) -> Path:
    """Get the path for a hook state file.

    ``<sessions root>/<tmux_session>/agents/<session id>/hook_state.json``
    (see ``agent_file_for_write``).
    """
    return agent_file_for_write(tmux_session, session_name, "hook_state")


def get_rename_notice_path(tmux_session: str, session_name: str,
                           session_id: str | None = None) -> Path:
    """One-shot note telling a renamed agent its new name (#478).

    ``overcode rename`` writes it in the agent's directory; the agent's
    next UserPromptSubmit prints it as prompt context and deletes it.
    """
    return agent_file_for_write(tmux_session, session_name, "rename_notice", session_id)


def _emit_rename_notice(tmux_session: str, session_name: str) -> None:
    """Print, then consume, the agent's pending rename note if there is one."""
    path = get_rename_notice_path(tmux_session, session_name)
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return  # none pending (the common case)
    path.unlink(missing_ok=True)
    if text:
        print(text)


def _emit_child_notices(tmux_session: str) -> None:
    """Print, then consume, the notes of this agent's children renamed since its last prompt."""
    sid = _agent_session_id()
    if not sid:
        return
    path = agent_file(tmux_session, sid, "child_notice")
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return
    path.unlink(missing_ok=True)
    if text:
        print(text)


# The namer's view of what the agent was asked (#542, auto-naming §4)
PROMPTS_KEPT = 3
PROMPT_BYTES = 1024


def _capture_prompt(tmux_session: str, prompt) -> None:
    """Keep a prompt for the namer: one of the agent's first ``PROMPTS_KEPT``
    in its ``prompts.jsonl``, and its last ``PROMPTS_KEPT`` in
    ``recent_prompts.jsonl`` (what it is doing now, #547)."""
    sid = _agent_session_id()
    if not sid or not isinstance(prompt, str) or not prompt.strip():
        return
    text = prompt.encode("utf-8")[:PROMPT_BYTES].decode("utf-8", "ignore")
    line = (json.dumps({"t": time.time(), "prompt": text}) + "\n").encode("utf-8")
    path = agent_file(tmux_session, sid, "prompts")
    try:
        with open(path, "a+b") as f:
            f.seek(0)
            if f.read().count(b"\n") < PROMPTS_KEPT:
                f.write(line)
    except OSError:
        pass  # the namer reads what there is
    recent = agent_file(tmux_session, sid, "recent_prompts")
    try:
        try:
            kept = recent.read_bytes().splitlines(keepends=True)[-(PROMPTS_KEPT - 1):]
        except FileNotFoundError:
            kept = []
        tmp = recent.with_name(f".{recent.name}.{os.getpid()}.tmp")
        tmp.write_bytes(b"".join(kept) + line)
        os.replace(tmp, recent)
    except OSError:
        pass


def emit_prompt_context(tmux_session: str, session_name: str) -> None:
    """Print what a user prompt carries into the model's context.

    The enhanced-context line (when the agent has it on), then the pending
    rename note and its children's rename notes, consumed. Claude, codex, grok and hermes get it from the
    UserPromptSubmit hook; the opencode plugins from ``--prompt-context``.
    """
    from .time_context import generate_enhanced_context

    line = generate_enhanced_context(tmux_session, session_name)
    if line:
        print(line)
    _emit_rename_notice(tmux_session, session_name)
    _emit_child_notices(tmux_session)


def handle_prompt_context() -> None:
    """``hook-handler --prompt-context``: the UserPromptSubmit context alone.

    For a plugin that publishes the hook files itself and only needs the
    text to add to the prompt (opencode, opencode2): reads no stdin and
    writes no hook state. Silent without the agent's env vars.
    """
    session_name = os.environ.get("OVERCODE_SESSION_NAME")
    tmux_session = os.environ.get("OVERCODE_TMUX_SESSION")
    if not session_name or not tmux_session:
        return
    emit_prompt_context(tmux_session, session_name)


def _get_hook_event_log_path(tmux_session: str, session_name: str) -> Path:
    """Get the path for the append-only hook event log (#448).

    ``agents/<session id>/hook_events.jsonl`` (see ``agent_file_for_write``).
    """
    return agent_file_for_write(tmux_session, session_name, "hook_events")


# Rotate the event log when it grows past _EVENT_LOG_ROTATE_BYTES, down to
# the last _EVENT_LOG_KEEP_BYTES of whole lines (audit R12). Both are bytes:
# the old rule triggered on bytes but trimmed to 200 lines, so once lines
# averaged over 512 B the trimmed file was still over the trigger and every
# append rewrote it whole — a 1.4 MB file per tool call, inside Claude
# Code's synchronous hook path. Keeping less than the trigger leaves room
# for ~36 KB of appends between rotations, and a rotation reads 64 KB.
# The detector reads a ~200 KB tail, so the kept tail is always whole.
_EVENT_LOG_ROTATE_BYTES = 100 * 1024
_EVENT_LOG_KEEP_BYTES = 64 * 1024

# The event log carries only what its readers use (audit R12): the detector
# reads ``event`` and ``timestamp``; every consumer of a tool's input reads
# the snapshot (hook_state), where it stays whole. These are the input
# fields those consumers read — obligation kind/label/eta and CronDelete
# targets (_update_obligations), the foreground classifier, sleep
# detection and the Bash activity line (hook_status_detector), and the
# Skill accumulator — kept so the log can answer the same questions;
# strings are cut to _EVENT_LOG_INPUT_CHARS and everything else becomes a
# preview of the same length, so a Write/Edit body never lands in the log.
_EVENT_LOG_INPUT_FIELDS = (
    "command",
    "prompt",
    "run_in_background",
    "delaySeconds",
    "schedule",
    "cron",
    "id",
    "cron_id",
    "skill",
    "file_path",  # the namer's most-edited files (#542)
)
_EVENT_LOG_INPUT_CHARS = 256


def _compact_tool_input(tool_input: dict | None) -> dict | None:
    """The event-log form of a tool input: consumed fields plus a preview."""
    if not isinstance(tool_input, dict):
        return tool_input
    compact: dict = {}
    for key in _EVENT_LOG_INPUT_FIELDS:
        if key not in tool_input:
            continue
        value = tool_input[key]
        if isinstance(value, str) and len(value) > _EVENT_LOG_INPUT_CHARS:
            value = value[:_EVENT_LOG_INPUT_CHARS]
        compact[key] = value
    rest = {k: v for k, v in tool_input.items() if k not in compact}
    if rest:
        try:
            preview = json.dumps(rest, default=str)
        except (TypeError, ValueError):
            preview = repr(rest)
        compact["_preview"] = preview[:_EVENT_LOG_INPUT_CHARS]
    return compact


def _rotate_event_log(path: Path) -> None:
    """Truncate the event log to its last _EVENT_LOG_KEEP_BYTES of whole lines.

    Reads only the tail it keeps (seek from the end, drop the partial first
    line) and swaps it in with write-temp + os.replace, so a reader sees
    the old file or the new one, whole.
    """
    try:
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            if size <= _EVENT_LOG_KEEP_BYTES:
                return
            f.seek(size - _EVENT_LOG_KEEP_BYTES)
            tail = f.read()
    except OSError:
        return
    newline = tail.find(b"\n")
    if newline == -1:
        return  # one line longer than the whole tail: nothing whole to keep
    tail = tail[newline + 1:]
    tmp = path.with_suffix(".jsonl.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(tail)
        os.replace(tmp, path)
    except OSError:
        # Best-effort; leave the file alone if rotation fails.
        try:
            tmp.unlink()
        except OSError:
            pass


def append_hook_event(
    event: str,
    tmux_session: str,
    session_name: str,
    tool_name: str | None = None,
    tool_input: dict | None = None,
    agent_id: str | None = None,
) -> None:
    """Append one event record to the hook event log (#448).

    The log is the authoritative source for recent-activity detection.
    Overwrite-based state files hide fast event bursts (PreToolUse →
    PostToolUse → Stop within a single poll); the log preserves them so
    the detector can keep the agent marked RUNNING across short Stops.

    ``tool_input`` is stored compacted (_compact_tool_input): the fields
    its consumers read, strings cut to 256 chars, and a preview of the
    rest. The snapshot (write_hook_state) keeps the whole input.
    """
    path = _get_hook_event_log_path(tmux_session, session_name)
    path.parent.mkdir(parents=True, exist_ok=True)

    entry: dict = {"event": event, "timestamp": time.time()}
    if tool_name is not None:
        entry["tool_name"] = tool_name
    if tool_input is not None:
        entry["tool_input"] = _compact_tool_input(tool_input)
    if agent_id is not None:
        entry["agent_id"] = agent_id

    line = json.dumps(entry) + "\n"
    # O_APPEND writes are atomic on POSIX for payloads under PIPE_BUF (4KB
    # typical) — no lock needed for concurrent hook invocations.
    with open(path, "a") as f:
        f.write(line)

    try:
        if path.stat().st_size > _EVENT_LOG_ROTATE_BYTES:
            _rotate_event_log(path)
    except OSError:
        pass




def _get_hook_state_lock_path(tmux_session: str, session_name: str) -> Path:
    """Sibling lock file guarding one agent's hook-state read-modify-write."""
    state_path = _get_hook_state_path(tmux_session, session_name)
    return state_path.with_name(state_path.name + ".lock")


@contextmanager
def _locked_hook_state(tmux_session: str, session_name: str):
    """Serialize one agent's hook-state cycle and yield the current snapshot.

    Hook invocations for an agent can overlap — two claude processes exist
    briefly across a restart — and the session-id decision is a read-modify-
    write over this file: peek what is already durable, settle the id against
    the session record, write the result. Without a lock an older event can
    land its snapshot after a newer one and leave `agent_session_id` naming a
    conversation the agent has left, which is what `CodexStatsReader` reads as
    current.

    Yields the parsed snapshot (``{}`` when absent or unreadable) so the cycle
    reads the file once. Degrades to no locking where fcntl is unavailable,
    matching SessionManager._locked_state.
    """
    state_path = _get_hook_state_path(tmux_session, session_name)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    def _load() -> dict:
        try:
            loaded = json.loads(state_path.read_text())
            return loaded if isinstance(loaded, dict) else {}
        except (json.JSONDecodeError, FileNotFoundError, OSError):
            return {}

    if not HAS_FCNTL:
        yield _load()
        return

    lock_path = _get_hook_state_lock_path(tmux_session, session_name)
    handle = None
    try:
        # Held past this block deliberately: the flock lives as long as the
        # yield below, so it cannot be a `with`.
        handle = open(lock_path, "a+")  # noqa: SIM115
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except OSError as e:
        logger.debug("Hook-state lock unavailable for %s: %s", session_name, e)
        if handle is not None:
            handle.close()
            handle = None
    try:
        yield _load()
    finally:
        if handle is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            handle.close()


def _session_ordinal(transcript_path: str | None) -> float | None:
    """Age of the conversation behind a hook event, or None if unknowable.

    The transcript's modification time orders two conversations even when the
    hook events carrying them arrive out of order — a reset starts a new
    transcript, so the newer conversation always has the newer file.
    """
    if not transcript_path or not isinstance(transcript_path, str):
        return None
    try:
        return Path(transcript_path).stat().st_mtime
    except OSError:
        return None


# SessionStart sources that mean "this agent is now in a different
# conversation" (#500). Anything else — startup, or no source at all — is a
# process starting, which may be a nested agent CLI that inherited this
# agent's OVERCODE_* env, so it may only supply an agent's first id.
_RESET_SOURCES = frozenset({"clear", "compact"})
_RESUME_SOURCE = "resume"


def _sync_agent_session_id(
    session_name: str, session_id: str, transcript_path: str | None = None,
    event: str | None = None, source: str | None = None, retry_new: bool = False,
    adopt: bool = False,
) -> tuple[str | None, bool]:
    """Point the session record at the conversation the agent is actually in.

    ``restart`` relaunches with ``--resume <active_agent_session_id>``, so a
    record left on a pre-``/clear`` id costs the agent everything it did after
    the reset.

    Returns ``(effective_session_id, durable)``:

    * ``effective_session_id`` is the id the record holds when the transaction
      ends, read inside its lock. A rejected event therefore reports the
      current conversation rather than its own, and never a pre-lock snapshot
      that a concurrent hook may already have replaced — the caller writes this
      into hook state, which readers treat as current.
    * Only ``event``/``source`` can move the record to an id it has never
      seen: SessionStart from a reset, or the agent's first id. Other events
      are nested agent CLIs as far as the record is concerned (#500) and
      report the record's current id instead. ``retry_new`` re-allows an id
      whose reset did not land durably (see handle_hook_event). ``adopt``
      moves the record to a conversation the agent's own CLI process is now
      in (``FOREIGN_OWN``), aged now: a ``/resume`` of an old conversation
      has an old transcript, which the age check would call superseded.
    * ``durable`` is False when an error stopped the write, and also when the
      advance could not be ordered against the record's existing ordering
      metadata, so the synced marker is left alone and a later event
      re-evaluates.

    Never raises: a hook that fails takes the agent's turn with it, and a
    bookkeeping miss is not worth that.
    """
    try:
        from .session_manager import SessionManager

        sm = SessionManager()
        session = _own_session(sm, session_name)
        if not session:
            # Not an overcode-managed agent, or not registered yet. Nothing to
            # write, and retrying would never change that.
            return session_id, True

        is_start = event == "SessionStart"
        outcome, current = sm.advance_active_agent_session_id(
            session.id, session_id,
            ordinal=time.time() if adopt else _session_ordinal(transcript_path),
            allow_new=(is_start and source in _RESET_SOURCES) or retry_new or adopt,
            allow_first=is_start,
            allow_return=is_start and source == _RESUME_SOURCE,
        )
        if outcome == "rejected":
            logger.debug(
                "Ignoring session id %s for %s from %s (source %s): not a reset",
                session_id, session_name, event, source,
            )
            return current, True
        effective = current or session_id

        if outcome == "superseded":
            logger.debug(
                "Ignoring superseded agent session id %s for %s (current is %s)",
                session_id, session_name, effective,
            )
            return effective, True
        if outcome == "advanced_unordered":
            # Recorded, because leaving the pointer on a conversation the agent
            # has left is the worse failure, but not confirmed: no transcript
            # was available to prove this event is the newer one, so a later
            # orderable event gets to re-decide.
            logger.debug(
                "Agent session id %s for %s is recorded but unordered",
                session_id, session_name,
            )
            return effective, False
        # "current_unordered" counts as synced (#501): the record already names
        # this id, and this event had no age to settle it with. A backend that
        # never sends a transcript path (hermes) would otherwise go back to the
        # session store on every hook event for the agent's whole life.
        return effective, True
    except Exception as e:  # noqa: BLE001 — a hook must never fail the agent's turn
        logger.debug(
            "Failed to sync agent session id %s for %s: %s", session_id, session_name, e
        )
        return session_id, False


def write_hook_state(
    event: str,
    tmux_session: str,
    session_name: str,
    tool_name: str | None = None,
    tool_input: dict | None = None,
    tool_use_id: str | None = None,
    session_id: str | None = None,
    active_prompt_id: str | None = None,
    synced_session_id: str | None = None,
    pending_session_id: str | None = None,
    prev_state: dict | None = None,
    data: dict | None = None,
    cli_pid: int | None = None,
) -> None:
    """Write hook state JSON for status detection.

    Writes to ~/.overcode/sessions/{tmux_session}/hook_state_{session_name}.json

    ``session_id`` is recorded for every backend that sends one (all of them
    do, on every event): it is folded into
    ``agent_session_ids``/``agent_session_id`` the same way opencode's
    bundled plugin records its own ids, so a backend-neutral
    ``CodexStatsReader``-style reader can find the right transcript file
    without directory+time guessing. ``handle_hook_event`` additionally
    mirrors a *changed* id into the session record, which is what keeps
    ``restart`` resuming the conversation the agent is actually in.

    ``active_prompt_id`` is grok-only: set on ``UserPromptSubmit`` to the
    turn's ``promptId``, then preserved across later events (mirroring
    ``agent_session_id``) so the stale-turn check can compare a later
    turn-end report's ``promptId`` against it.

    ``data`` is the event's whole payload, for what ``swarm`` reads from it
    (``background_tasks``, an Agent or Workflow call's ``tool_response``, a
    ``<task-notification>`` prompt).

    ``cli_pid`` is the process id of the agent's own CLI, given only for an
    event of the agent's own conversation and carried forward otherwise:
    what tells a nested or foreign CLI's events apart (``_place``).
    """
    state_path = _get_hook_state_path(tmux_session, session_name)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    # Read previous state to preserve accumulated loaded_skills, obligations
    # and any session ids recorded by an earlier SessionStart.
    prev_skills: list[str] = []
    prev_obligations: list[dict] = []
    prev_agent_session_ids: list[str] = []
    prev_active_agent_session_id: str | None = None
    prev_active_prompt_id: str | None = None
    prev_synced_agent_session_id: str | None = None
    prev_subagents: dict | None = None
    prev_swarm: dict | None = None
    prev_foreign: dict | None = None
    prev_cli_pid = None
    prev_warning = None
    prev_bg_worker = None
    try:
        prev = prev_state if prev_state is not None else json.loads(state_path.read_text())
        prev_foreign = prev.get("foreign_sessions") if isinstance(
            prev.get("foreign_sessions"), dict) else None
        prev_skills = prev.get("loaded_skills", [])
        prev_obligations = prev.get("pending_obligations", []) or []
        prev_agent_session_ids = prev.get("agent_session_ids", []) or []
        prev_active_agent_session_id = prev.get("agent_session_id")
        prev_active_prompt_id = prev.get("active_prompt_id")
        prev_synced_agent_session_id = prev.get("agent_session_id_synced")
        prev_subagents = prev.get("subagents")
        prev_swarm = prev.get("swarm")
        prev_cli_pid = prev.get("cli_pid")
        prev_warning = prev.get("conversation_warning")
        prev_bg_worker = prev.get("bg_worker")
    except (json.JSONDecodeError, FileNotFoundError, OSError):
        pass

    # Accumulate Skill tool invocations
    if tool_name == "Skill" and isinstance(tool_input, dict):
        skill = tool_input.get("skill", "")
        if skill and skill not in prev_skills:
            prev_skills = prev_skills + [skill]

    now = time.time()
    state = {
        "event": event,
        "timestamp": now,
    }
    if tool_name is not None:
        state["tool_name"] = tool_name
    if tool_input is not None:
        state["tool_input"] = tool_input
    if tool_use_id is not None:
        state["tool_use_id"] = tool_use_id
    if prev_skills:
        state["loaded_skills"] = prev_skills

    agent_session_ids = list(prev_agent_session_ids)
    if session_id and session_id not in agent_session_ids:
        agent_session_ids.append(session_id)
        if len(agent_session_ids) > _MAX_AGENT_SESSION_IDS:
            agent_session_ids = agent_session_ids[-_MAX_AGENT_SESSION_IDS:]
    active_agent_session_id = session_id or prev_active_agent_session_id
    if agent_session_ids:
        state["agent_session_ids"] = agent_session_ids
    if active_agent_session_id:
        state["agent_session_id"] = active_agent_session_id

    resolved_active_prompt_id = active_prompt_id if active_prompt_id is not None else prev_active_prompt_id
    if resolved_active_prompt_id:
        state["active_prompt_id"] = resolved_active_prompt_id

    # Maintain the pending-obligation set across events (#TBD).
    obligations = _update_obligations(
        prev_obligations, event, tool_name, tool_input, tool_use_id, now
    )
    if obligations:
        state["pending_obligations"] = obligations

    # Subagents outlive the parent's turns; only the session ending drops
    # them. The parent's own events reconcile them (swarm).
    _store_swarm(state, *swarm.apply_parent_event(
        prev_subagents, prev_swarm, event, tool_name, tool_input, data, now))

    # Foreground detail — only populated mid-tool (PreToolUse); cleared by
    # any subsequent event so a stale entry can't outlive its tool call.
    foreground = _compute_foreground(event, tool_name, tool_input)
    if foreground is not None:
        state["foreground"] = foreground

    # Only a confirmed session-store write advances this; otherwise it is
    # carried forward so the next event retries. Written here, in the single
    # per-event hook-state write, so nothing else has to rewrite this file.
    if prev_foreign:
        state["foreign_sessions"] = prev_foreign
    resolved_cli_pid = cli_pid if cli_pid is not None else prev_cli_pid
    if resolved_cli_pid:
        state["cli_pid"] = resolved_cli_pid
    if isinstance(prev_warning, dict):
        state["conversation_warning"] = prev_warning
    if isinstance(prev_bg_worker, dict):
        state["bg_worker"] = prev_bg_worker

    synced = synced_session_id or prev_synced_agent_session_id
    if synced:
        state["agent_session_id_synced"] = synced
    if pending_session_id:
        state["agent_session_id_pending"] = pending_session_id

    _write_state_file(state_path, state)


_RUNNING_EVENTS = frozenset({
    "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure",
})

# The events the snapshot may hold: exactly those the hook detector maps to
# a status (hook_status_detector._HOOK_STATUS_MAP, pinned by a test). The
# detector reads any other event as "waiting for the user" (red), so an
# event a CLI starts sending that overcode doesn't know goes to the event
# log only.
SNAPSHOT_EVENTS = frozenset({
    "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure",
    "Stop", "StopFailure", "UserPromptSubmitRejected", "PermissionRequest",
    "SessionEnd", "Interrupt", "SessionStart",
})


def _is_idle_prompt(data: dict) -> bool:
    if data.get("notification_type") == "idle_prompt":
        return True
    # Older Claude builds send only the message
    message = data.get("message")
    return isinstance(message, str) and "waiting for your input" in message.lower()


def _settle_idle_prompt(data: dict, tmux_session: str, session_name: str) -> None:
    """A Claude idle_prompt Notification: write Stop if the snapshot still says running."""
    if not _is_idle_prompt(data):
        return
    session_id = data.get("session_id")
    with _locked_hook_state(tmux_session, session_name) as prev_state:
        if prev_state.get("event") not in _RUNNING_EVENTS:
            return
        current = prev_state.get("agent_session_id")
        if session_id and current and session_id != current:
            return  # another conversation's prompt (a nested CLI), not this turn
        write_hook_state(
            "Stop", tmux_session, session_name,
            pending_session_id=prev_state.get("agent_session_id_pending"),
            prev_state=prev_state,
        )
    append_hook_event("Stop", tmux_session, session_name)


def handle_hook_event(backend: str | None = None) -> None:
    """Main entry point: read stdin JSON, write state file, output time-context if UserPromptSubmit.

    Called by the agent CLI for every hook event. Reads the hook event JSON
    from stdin, writes a state file for status detection, and for
    UserPromptSubmit events also outputs time-context to stdout.

    ``backend`` (``hook-handler --backend <name>``, which no command
    registers any more) picks the payload's dialect; without it
    ``hook_dialects.for_backend`` tells it from the payload.

    Silent exit (code 0) if env vars missing or stdin is empty/invalid.
    """
    session_name = os.environ.get("OVERCODE_SESSION_NAME")
    tmux_session = os.environ.get("OVERCODE_TMUX_SESSION")

    if not session_name or not tmux_session:
        # Fallback: detect from tmux pane when env vars are missing
        # (e.g. after manual session restart with --session-id)
        session_name, tmux_session, window_sid = _detect_from_tmux_pane()
        if not session_name or not tmux_session:
            return
        if not _agent_session_id():
            # The files are keyed by session id: the window's, else the
            # record's (a window launched before @overcode_id)
            sid = window_sid or _session_id_for_name(tmux_session, session_name)
            if sid:
                os.environ["OVERCODE_SESSION_ID"] = sid

    # Read stdin JSON
    try:
        stdin_data = sys.stdin.read()
        if not stdin_data.strip():
            return
        data = json.loads(stdin_data)
    except (json.JSONDecodeError, IOError) as e:
        logger.debug("Failed to parse hook stdin: %s", e)
        return

    if not isinstance(data, dict):
        return
    dialect = hook_dialects.for_backend(backend, data)
    data = dialect.normalize(data)
    if data is None:
        # The dialect dropped it (grok: a subagent's own event, the
        # session-teardown Stop, an unregistered Notification type).
        return

    event = data.get("hook_event_name")
    if not event:
        return

    tool_name = data.get("tool_name")
    tool_input = data.get("tool_input")

    agent_id = data.get("agent_id")
    if agent_id:
        # A subagent's event: bookkeeping on the parent, not the parent's turn
        with _locked_hook_state(tmux_session, session_name) as prev_state:
            session_id, current = data.get("session_id"), prev_state.get("agent_session_id")
            if session_id and current and session_id != current:
                return  # another conversation's subagent (a nested or background CLI)
            _write_state_file(
                _get_hook_state_path(tmux_session, session_name),
                _apply_subagent_event(prev_state, event, agent_id, data, time.time()),
            )
        append_hook_event(event, tmux_session, session_name,
                          tool_name=tool_name, tool_input=tool_input, agent_id=agent_id)
        return

    if event == "Notification":
        # Claude's idle_prompt (registered with that matcher only) fires once
        # the prompt has sat idle for about a minute. It is the backstop for
        # a turn that ended with no Stop (an Esc, a crash), which would
        # otherwise read as running forever. It only settles a snapshot that
        # still says the turn is running, and only for this conversation.
        _settle_idle_prompt(data, tmux_session, session_name)
        return

    if event not in SNAPSHOT_EVENTS:
        logger.info("unmapped hook event %r from %s; not written to the snapshot",
                    event, backend or "an unnamed backend")
        append_hook_event(event, tmux_session, session_name, tool_name=tool_name, tool_input=tool_input)
        return

    tool_use_id = data.get("tool_use_id")
    # Every backend's hook payload carries the session id on every event
    # (live-verified on Claude Code 2.1.258: UserPromptSubmit, PostToolUse
    # and Stop all include it), so record it unconditionally rather than
    # only on SessionStart.
    #
    # Prescribing `--session-id` at launch is not enough to keep knowing it.
    # `--resume <id>` does preserve the id, but `/clear` mints a brand-new
    # one (SessionStart fires with source="clear" and a new id, and the
    # transcript moves to a new file); auto-compaction does the same. From
    # that moment the launch-time id in sessions.json is stale, and because
    # `restart` relaunches with `--resume <active_agent_session_id>` the
    # agent silently comes back at its pre-reset history — losing however
    # much work happened after the reset. Reading the id off each event and
    # syncing it below (see _sync_agent_session_id) makes the record
    # self-healing within one tool call of any reset.
    #
    # Which ids may *move* the record is narrower (#500): a nested agent CLI
    # run inside this agent's shell inherits OVERCODE_SESSION_NAME and would
    # otherwise take the record over. Only SessionStart from a reset (or an
    # agent's first id) introduces a new one; see _sync_agent_session_id.
    session_id = data.get("session_id")

    # Stale-turn protection (dialects that track prompt turns: grok): a
    # StopCancelled/StopFailure report is dispatched off grok's own command
    # loop and can arrive after the
    # *next* turn's UserPromptSubmit already moved the session on. Compare
    # against the newest promptId this session has recorded and drop a
    # report for an older one rather than settling a session that is
    # already busy again. Events with no promptId (the idle_prompt backstop,
    # the session-end Stop) always settle unconditionally — that's the
    # session reporting on itself, not a turn.
    prompt_id = data.get("prompt_id") if dialect.tracks_prompt_turns else None
    stale_check = event in ("Stop", "StopFailure") and prompt_id

    # One locked cycle per event over this agent's hook state: read it once,
    # settle the session id against the session record, then write. The id in
    # this snapshot is what CodexStatsReader and the monitor treat as current,
    # so an older event must not be able to write it after a newer one has
    # moved on — hence the lock rather than two independent read/writes.
    with _locked_hook_state(tmux_session, session_name) as prev_state:
        # Under the lock: checked before it, a UserPromptSubmit landing in
        # between let a stale report settle the turn that had just begun
        if stale_check:
            active_prompt_id = prev_state.get("active_prompt_id")
            if active_prompt_id and active_prompt_id != prompt_id:
                return
        effective_session_id = session_id
        synced_session_id = None
        # A reset whose write didn't land durably leaves its id pending, so a
        # later event carrying it may still introduce it (#500).
        pending_session_id = prev_state.get("agent_session_id_pending")
        foreign_known = prev_state.get("foreign_sessions")
        known = foreign_known.get(session_id) if (
            session_id and isinstance(foreign_known, dict)) else None
        if isinstance(known, dict) and known.get("kind") in (FOREIGN_BG, FOREIGN_NESTED) \
                and _settled(known):
            # A background session's or nested CLI's event: the record is not
            # asked (it could only refuse), the agent's own id stands
            effective_session_id = prev_state.get("agent_session_id") or None
        elif session_id:
            if session_id == prev_state.get("agent_session_id_synced"):
                # Already known durable, and no newer id has been recorded
                # under this lock, so the session store stays out of the
                # steady-state path.
                synced_session_id = session_id
            else:
                is_reset = (event == "SessionStart"
                            and data.get("source") in _RESET_SOURCES)
                retry = session_id == pending_session_id
                effective_session_id, durable = _sync_agent_session_id(
                    session_name, session_id, data.get("transcript_path"),
                    event=event, source=data.get("source"), retry_new=retry,
                )
                if durable:
                    synced_session_id = effective_session_id
                if is_reset or retry:
                    pending_session_id = None if durable else session_id

        cli_pid = _cli_pid()
        own_event = True
        if (session_id and effective_session_id and session_id != effective_session_id
                and session_id not in (prev_state.get("agent_session_ids") or [])):
            # Another conversation running under this agent's name: a nested
            # CLI, a Claude background session that inherited the agent's
            # environment (/fork from its Claude), the agent's own
            # conversation moved into Claude's background worker, or the
            # agent's own CLI in a conversation overcode never saw (_place).
            # Noted, so the engine
            # can nest a background session's row under this agent
            # (claude_bg.attribute).
            now = time.time()
            foreign = _note_foreign_session(prev_state, session_id, now, cli_pid)
            entry = foreign[session_id]
            kind = entry.get("kind")
            if kind in (FOREIGN_BG, FOREIGN_NESTED, FOREIGN_UNPLACED):
                # It lives on its own: its turns are not this agent's, so none
                # of them may move its status, obligations or swarm
                quiet = dict(prev_state, foreign_sessions=foreign)
                if kind == FOREIGN_UNPLACED:
                    quiet["conversation_warning"] = {
                        "kind": WARN_UNPLACED, "session_id": session_id, "at": now}
                _write_state_file(_get_hook_state_path(tmux_session, session_name), quiet)
                return
            if kind == FOREIGN_CONTINUED and not entry.get("bg"):
                # Back in the agent's pane: the worker no longer has it, and
                # another agent's conversation is still only shared
                prev_state = {k: v for k, v in prev_state.items() if k != "bg_worker"}
                if "owner" not in entry:
                    entry["owner"] = _conversation_owner(session_id, session_name)
                if entry["owner"]:
                    kind = FOREIGN_OWN
            if kind == FOREIGN_CONTINUED:
                # The agent's own conversation, moved into Claude's background
                # worker (or from it back to the agent's pane): it IS the
                # agent, so it steers and the record follows it, no warning
                adopted, durable = _sync_agent_session_id(
                    session_name, session_id, data.get("transcript_path"),
                    event=event, source=data.get("source"), adopt=True,
                )
                if adopted == session_id:
                    foreign.pop(session_id, None)
                    effective_session_id = session_id
                    if durable:
                        synced_session_id = session_id
                    pending_session_id = None if durable else session_id
                prev_state = dict(prev_state, foreign_sessions=foreign)
                if entry.get("bg"):
                    pane = _pane_pid(prev_state) or _int_or_none(prev_state.get("cli_pid"))
                    prev_state["bg_worker"] = {"session_id": session_id, "pane_pid": pane,
                                               "at": now}
                else:
                    prev_state.pop("bg_worker", None)
                own_event = effective_session_id == session_id
            elif kind == FOREIGN_OWN:
                # The agent's own CLI in a conversation overcode never saw
                # (/resume into it): it IS the agent, so it steers, and the
                # record follows it unless another agent owns that conversation
                if "owner" not in entry:
                    entry["owner"] = _conversation_owner(session_id, session_name)
                previous = effective_session_id
                if entry["owner"]:
                    warning = {"kind": WARN_SHARED, "session_id": session_id,
                               "owner": entry["owner"], "previous": previous, "at": now}
                else:
                    adopted, durable = _sync_agent_session_id(
                        session_name, session_id, data.get("transcript_path"),
                        event=event, source=data.get("source"), adopt=True,
                    )
                    if adopted == session_id:
                        foreign.pop(session_id, None)
                        effective_session_id = session_id
                        if durable:
                            synced_session_id = session_id
                        pending_session_id = None if durable else session_id
                    warning = {"kind": WARN_ADOPTED, "session_id": session_id,
                               "previous": previous, "at": now}
                prev_state = dict(prev_state, foreign_sessions=foreign,
                                  conversation_warning=warning)
                own_event = effective_session_id == session_id
            else:
                # Not placed (a backend whose hooks carry no CLI process id):
                # it reaches the snapshot as it always has, under the
                # agent's own id (#500)
                own_event = False
                prev_state = dict(prev_state, foreign_sessions=foreign)

        if own_event and cli_pid is not None and cli_pid == _pane_pid(prev_state):
            # The CLI in the agent's pane drives its conversation again
            prev_state = {k: v for k, v in prev_state.items() if k != "bg_worker"}

        # Write state file for status detection (snapshot) and append to the
        # event log (#448 — preserves bursts hidden by overwrite).
        write_hook_state(
            event, tmux_session, session_name,
            tool_name=tool_name, tool_input=tool_input, tool_use_id=tool_use_id,
            session_id=effective_session_id,
            active_prompt_id=(prompt_id if event == "UserPromptSubmit" else None),
            synced_session_id=synced_session_id,
            pending_session_id=pending_session_id,
            prev_state=prev_state,
            data=data,
            cli_pid=cli_pid if own_event else None,
        )
    append_hook_event(event, tmux_session, session_name, tool_name=tool_name, tool_input=tool_input)

    # For UserPromptSubmit, check budget and output enhanced context
    if event == "UserPromptSubmit":
        from .time_context import _load_daemon_state, _find_session_in_state

        # Block prompt if agent has exceeded its cost budget (#246)
        state = _load_daemon_state(tmux_session)
        if state:
            session_data = _find_session_in_state(state, session_name, _agent_session_id())
            if session_data and session_data.get("budget_exceeded", False):
                budget = session_data.get("cost_budget_usd", 0)
                cost = session_data.get("estimated_cost_usd", 0)
                # Overwrite hook state so status detector shows error, not stuck green (#428).
                # Same per-agent lock as the main cycle: this is another
                # read-modify-write of the snapshot, and an overlapping hook
                # must not interleave with it.
                with _locked_hook_state(tmux_session, session_name) as rejected_prev:
                    write_hook_state(
                        "UserPromptSubmitRejected", tmux_session, session_name,
                        pending_session_id=rejected_prev.get("agent_session_id_pending"),
                        prev_state=rejected_prev,
                    )
                append_hook_event("UserPromptSubmitRejected", tmux_session, session_name)
                print(
                    f"Budget exceeded (${cost:.2f} / ${budget:.2f}). Prompt blocked.",
                    file=sys.stderr,
                )
                sys.exit(2)

        emit_prompt_context(tmux_session, session_name)
        _capture_prompt(tmux_session, data.get("prompt"))
