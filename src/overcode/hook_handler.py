"""Unified hook handler for Claude Code hook events.

A single command (`overcode hook-handler`) handles all hook events.
It reads stdin JSON from Claude Code, writes state files for hook-based
status detection, and outputs time-context for UserPromptSubmit events.

Hook registrations (all use the same command):
    UserPromptSubmit  -> overcode hook-handler
    PostToolUse       -> overcode hook-handler
    Stop              -> overcode hook-handler
    PermissionRequest -> overcode hook-handler
    SessionEnd        -> overcode hook-handler
"""

import json
import os
import sys
import time
from pathlib import Path


# All hooks that overcode installs
OVERCODE_HOOKS: list[tuple[str, str]] = [
    ("UserPromptSubmit", "overcode hook-handler"),
    ("PostToolUse", "overcode hook-handler"),
    ("Stop", "overcode hook-handler"),
    ("PermissionRequest", "overcode hook-handler"),
    ("SessionEnd", "overcode hook-handler"),
]


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

    state = {
        "event": event,
        "timestamp": time.time(),
    }
    if tool_name is not None:
        state["tool_name"] = tool_name
    if tool_input is not None:
        state["tool_input"] = tool_input

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
        return

    # Read stdin JSON
    try:
        stdin_data = sys.stdin.read()
        if not stdin_data.strip():
            return
        data = json.loads(stdin_data)
    except (json.JSONDecodeError, IOError):
        return

    event = data.get("hook_event_name")
    if not event:
        return

    tool_name = data.get("tool_name")
    tool_input = data.get("tool_input")

    # Write state file for status detection
    write_hook_state(event, tmux_session, session_name, tool_name=tool_name, tool_input=tool_input)

    # For UserPromptSubmit, check budget and output time-context
    if event == "UserPromptSubmit":
        from .time_context import _load_daemon_state, _find_session_in_state

        # Block prompt if agent has exceeded its cost budget (#246)
        state = _load_daemon_state(tmux_session)
        if state:
            session_data = _find_session_in_state(state, session_name)
            if session_data and session_data.get("budget_exceeded", False):
                budget = session_data.get("cost_budget_usd", 0)
                cost = session_data.get("estimated_cost_usd", 0)
                print(
                    f"Budget exceeded (${cost:.2f} / ${budget:.2f}). Prompt blocked.",
                    file=sys.stderr,
                )
                sys.exit(2)

        from .time_context import generate_time_context

        line = generate_time_context(tmux_session, session_name)
        if line:
            print(line)
