"""Unified hook handler for Claude Code hook events.

A single command (`overcode hook-handler`) handles all hook events.
It reads stdin JSON from Claude Code, writes state files for hook-based
status detection, and outputs enhanced context for UserPromptSubmit events.

Hook registrations (all use the same command):
    UserPromptSubmit  -> overcode hook-handler
    PostToolUse       -> overcode hook-handler
    Stop              -> overcode hook-handler
    PermissionRequest -> overcode hook-handler
    SessionEnd        -> overcode hook-handler
"""

import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

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
# Disarm rules:
#   - Bash/Agent/Workflow with run_in_background → PostToolUse(same id) removes
#   - Monitor                                    → PostToolUse(same id) removes
#   - ScheduleWakeup                             → next UserPromptSubmit
#                                                   (wakeup fires as a prompt)
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
_CONDITIONAL_BG_TOOLS: frozenset[str] = frozenset({"Bash", "Agent", "Workflow"})

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


# All hooks that overcode installs for Claude Code (via --settings).
OVERCODE_HOOKS: list[tuple[str, str]] = [
    ("UserPromptSubmit", "overcode hook-handler"),
    ("PreToolUse", "overcode hook-handler"),
    ("PostToolUse", "overcode hook-handler"),
    ("PostToolUseFailure", "overcode hook-handler"),
    ("Stop", "overcode hook-handler"),
    ("StopFailure", "overcode hook-handler"),
    ("PermissionRequest", "overcode hook-handler"),
    ("SessionEnd", "overcode hook-handler"),
]

# Codex has no --session-id-shaped flag (design doc §2.2/§2.4), so it needs
# two events Claude never registers: SessionStart (to learn the session id
# the hook-handler can't otherwise discover) and Interrupt (codex's own
# Escape-to-interrupt signal, see _HOOK_STATUS_MAP in hook_status_detector.py).
# codex's PreCompact/PostCompact/SubagentStart/SubagentStop exist but have no
# overcode-side meaning yet, so they are deliberately not registered.
CODEX_HOOK_EVENTS: tuple[str, ...] = (
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PermissionRequest",
    "Stop",
    "Interrupt",
    "SessionStart",
    "SessionEnd",
)

# Hook-state fields codex's SessionStart carries that Claude's stdin has no
# equivalent for. Capped the same way opencode's JS plugin caps
# agent_session_ids, so a long-lived agent that keeps starting new
# conversations doesn't grow the state file without bound.
_MAX_AGENT_SESSION_IDS = 20


def _detect_from_tmux_pane() -> tuple[str | None, str | None]:
    """Detect agent name and tmux session from the current tmux pane.

    Fallback for when OVERCODE_SESSION_NAME / OVERCODE_TMUX_SESSION env vars
    are missing (e.g. after a manual session restart with --session-id).

    Returns (session_name, tmux_session) or (None, None) if detection fails.
    """
    pane_id = os.environ.get("TMUX_PANE")
    if not pane_id:
        return None, None
    try:
        window_name = subprocess.run(
            ["tmux", "display-message", "-p", "-t", pane_id, "#{window_name}"],
            capture_output=True, text=True, timeout=2,
        ).stdout.strip()
        tmux_session = subprocess.run(
            ["tmux", "display-message", "-p", "-t", pane_id, "#{session_name}"],
            capture_output=True, text=True, timeout=2,
        ).stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None, None

    if not window_name or not tmux_session:
        return None, None

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

    return session_name, tmux_session


def _get_hook_state_path(tmux_session: str, session_name: str) -> Path:
    """Get the path for a hook state file.

    Returns ~/.overcode/sessions/{tmux_session}/hook_state_{session_name}.json
    Respects OVERCODE_STATE_DIR environment variable for test isolation.
    """
    state_dir = os.environ.get("OVERCODE_STATE_DIR")
    if state_dir:
        base = Path(state_dir)
    else:
        base = Path.home() / ".overcode" / "sessions"
    return base / tmux_session / f"hook_state_{session_name}.json"


def _get_hook_event_log_path(tmux_session: str, session_name: str) -> Path:
    """Get the path for the append-only hook event log (#448).

    Returns ~/.overcode/sessions/{tmux_session}/hook_events_{session_name}.jsonl
    """
    state_dir = os.environ.get("OVERCODE_STATE_DIR")
    if state_dir:
        base = Path(state_dir)
    else:
        base = Path.home() / ".overcode" / "sessions"
    return base / tmux_session / f"hook_events_{session_name}.jsonl"


# Rotate the event log when it grows past this (roughly). We keep the tail
# so recent-activity lookups stay cheap.
_EVENT_LOG_ROTATE_BYTES = 100 * 1024
_EVENT_LOG_KEEP_LINES = 200


def _rotate_event_log(path: Path) -> None:
    """Truncate the event log to the last N lines when it grows too big."""
    try:
        with open(path) as f:
            lines = f.readlines()
    except OSError:
        return
    if len(lines) <= _EVENT_LOG_KEEP_LINES:
        return
    tmp = path.with_suffix(".jsonl.tmp")
    try:
        tmp.write_text("".join(lines[-_EVENT_LOG_KEEP_LINES:]))
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
) -> None:
    """Append one event record to the hook event log (#448).

    The log is the authoritative source for recent-activity detection.
    Overwrite-based state files hide fast event bursts (PreToolUse →
    PostToolUse → Stop within a single poll); the log preserves them so
    the detector can keep the agent marked RUNNING across short Stops.
    """
    path = _get_hook_event_log_path(tmux_session, session_name)
    path.parent.mkdir(parents=True, exist_ok=True)

    entry: dict = {"event": event, "timestamp": time.time()}
    if tool_name is not None:
        entry["tool_name"] = tool_name
    if tool_input is not None:
        entry["tool_input"] = tool_input

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


def write_hook_state(
    event: str,
    tmux_session: str,
    session_name: str,
    tool_name: str | None = None,
    tool_input: dict | None = None,
    tool_use_id: str | None = None,
    session_id: str | None = None,
) -> None:
    """Write hook state JSON for status detection.

    Writes to ~/.overcode/sessions/{tmux_session}/hook_state_{session_name}.json

    ``session_id`` is codex-only (Claude's stdin carries one too, but it is
    never passed here for Claude — see ``handle_hook_event``): when given, it
    is folded into ``agent_session_ids``/``agent_session_id`` the same way
    opencode's bundled plugin records its own ids, so a backend-neutral
    ``CodexStatsReader``-style reader can find the right transcript file
    without directory+time guessing.
    """
    state_path = _get_hook_state_path(tmux_session, session_name)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    # Read previous state to preserve accumulated loaded_skills, obligations
    # and any session ids recorded by an earlier SessionStart.
    prev_skills: list[str] = []
    prev_obligations: list[dict] = []
    prev_agent_session_ids: list[str] = []
    prev_active_agent_session_id: str | None = None
    try:
        prev = json.loads(state_path.read_text())
        prev_skills = prev.get("loaded_skills", [])
        prev_obligations = prev.get("pending_obligations", []) or []
        prev_agent_session_ids = prev.get("agent_session_ids", []) or []
        prev_active_agent_session_id = prev.get("agent_session_id")
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

    # Maintain the pending-obligation set across events (#TBD).
    obligations = _update_obligations(
        prev_obligations, event, tool_name, tool_input, tool_use_id, now
    )
    if obligations:
        state["pending_obligations"] = obligations

    # Foreground detail — only populated mid-tool (PreToolUse); cleared by
    # any subsequent event so a stale entry can't outlive its tool call.
    foreground = _compute_foreground(event, tool_name, tool_input)
    if foreground is not None:
        state["foreground"] = foreground

    state_path.write_text(json.dumps(state))


# Dialect key aliases for a camelCase stdin (grok, Phase 4). codex's stdin
# is already snake_case and Claude-shaped (design doc §2.3, Appendix A:
# hook_event_name/session_id/turn_id/transcript_path/cwd/model/
# permission_mode/prompt) so it needs no translation and takes the identity
# path below. Kept as one normalization call site — rather than scattering
# per-backend branches through the handler — so a future camelCase dialect
# (or any other) only ever needs a new entry in this table.
_CAMEL_CASE_KEY_ALIASES: dict[str, str] = {
    "hookEventName": "hook_event_name",
    "sessionId": "session_id",
    "toolName": "tool_name",
    "toolInput": "tool_input",
    "toolUseId": "tool_use_id",
    "permissionMode": "permission_mode",
}


def _normalize_hook_payload(data: dict) -> dict:
    """Normalize hook stdin into the snake_case vocabulary the rest of this
    module reads (``hook_event_name``, ``session_id``, ``tool_name``, ...).

    Claude Code and codex both send snake_case already, so this is a no-op
    for both. A camelCase ``hookEventName`` key (confirmed for grok, Phase 4
    of the design doc — not wired yet) marks the other dialect this repo
    knows about; translated keys are added alongside the originals rather
    than replacing them, so a field this table doesn't yet know about still
    survives untouched.
    """
    if not isinstance(data, dict):
        return data
    if "hookEventName" not in data or "hook_event_name" in data:
        return data
    translated = dict(data)
    for camel, snake in _CAMEL_CASE_KEY_ALIASES.items():
        if camel in data and snake not in translated:
            translated[snake] = data[camel]
    return translated


def handle_hook_event() -> None:
    """Main entry point: read stdin JSON, write state file, output time-context if UserPromptSubmit.

    Called by Claude Code for every hook event. Reads the hook event JSON
    from stdin, writes a state file for status detection, and for
    UserPromptSubmit events also outputs time-context to stdout.

    Silent exit (code 0) if env vars missing or stdin is empty/invalid.
    """
    session_name = os.environ.get("OVERCODE_SESSION_NAME")
    tmux_session = os.environ.get("OVERCODE_TMUX_SESSION")

    if not session_name or not tmux_session:
        # Fallback: detect from tmux pane when env vars are missing
        # (e.g. after manual session restart with --session-id)
        session_name, tmux_session = _detect_from_tmux_pane()
        if not session_name or not tmux_session:
            return

    # Read stdin JSON
    try:
        stdin_data = sys.stdin.read()
        if not stdin_data.strip():
            return
        data = json.loads(stdin_data)
    except (json.JSONDecodeError, IOError) as e:
        logger.debug("Failed to parse hook stdin: %s", e)
        return

    data = _normalize_hook_payload(data)

    event = data.get("hook_event_name")
    if not event:
        return

    tool_name = data.get("tool_name")
    tool_input = data.get("tool_input")
    tool_use_id = data.get("tool_use_id")
    # Claude's stdin also carries session_id, but only codex's SessionStart
    # (the one event Claude never sends — it prescribes --session-id at
    # launch instead) needs it recorded: codex has no such flag, so this is
    # the only way overcode learns which rollout file is this agent's own.
    session_id = data.get("session_id") if event == "SessionStart" else None

    # Write state file for status detection (snapshot) and append to the
    # event log (#448 — preserves bursts hidden by overwrite).
    write_hook_state(
        event, tmux_session, session_name,
        tool_name=tool_name, tool_input=tool_input, tool_use_id=tool_use_id,
        session_id=session_id,
    )
    append_hook_event(event, tmux_session, session_name, tool_name=tool_name, tool_input=tool_input)

    # For UserPromptSubmit, check budget and output enhanced context
    if event == "UserPromptSubmit":
        from .time_context import _load_daemon_state, _find_session_in_state

        # Block prompt if agent has exceeded its cost budget (#246)
        state = _load_daemon_state(tmux_session)
        if state:
            session_data = _find_session_in_state(state, session_name)
            if session_data and session_data.get("budget_exceeded", False):
                budget = session_data.get("cost_budget_usd", 0)
                cost = session_data.get("estimated_cost_usd", 0)
                # Overwrite hook state so status detector shows error, not stuck green (#428)
                write_hook_state("UserPromptSubmitRejected", tmux_session, session_name)
                append_hook_event("UserPromptSubmitRejected", tmux_session, session_name)
                print(
                    f"Budget exceeded (${cost:.2f} / ${budget:.2f}). Prompt blocked.",
                    file=sys.stderr,
                )
                sys.exit(2)

        from .time_context import generate_enhanced_context

        line = generate_enhanced_context(tmux_session, session_name)
        if line:
            print(line)
