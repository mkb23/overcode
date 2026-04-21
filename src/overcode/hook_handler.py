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
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)


# All hooks that overcode installs
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
) -> None:
    """Write hook state JSON for status detection.

    Writes to ~/.overcode/sessions/{tmux_session}/hook_state_{session_name}.json
    """
    state_path = _get_hook_state_path(tmux_session, session_name)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    # Read previous state to preserve accumulated loaded_skills
    prev_skills: list[str] = []
    try:
        prev = json.loads(state_path.read_text())
        prev_skills = prev.get("loaded_skills", [])
    except (json.JSONDecodeError, FileNotFoundError, OSError):
        pass

    # Accumulate Skill tool invocations
    if tool_name == "Skill" and isinstance(tool_input, dict):
        skill = tool_input.get("skill", "")
        if skill and skill not in prev_skills:
            prev_skills = prev_skills + [skill]

    state = {
        "event": event,
        "timestamp": time.time(),
    }
    if tool_name is not None:
        state["tool_name"] = tool_name
    if tool_input is not None:
        state["tool_input"] = tool_input
    if prev_skills:
        state["loaded_skills"] = prev_skills

    state_path.write_text(json.dumps(state))


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

    event = data.get("hook_event_name")
    if not event:
        return

    tool_name = data.get("tool_name")
    tool_input = data.get("tool_input")

    # Write state file for status detection (snapshot) and append to the
    # event log (#448 — preserves bursts hidden by overwrite).
    write_hook_state(event, tmux_session, session_name, tool_name=tool_name, tool_input=tool_input)
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
