"""OVERCODE-PLUGIN-MARKER: overcode-hermes-telemetry

overcode's telemetry plugin for the Hermes agent (NousResearch/hermes-agent).

Installed by ``overcode.backends.hermes.ensure_plugin_installed`` into
``$HERMES_HOME/plugins/overcode/`` and enabled once via
``hermes plugins enable overcode --no-allow-tool-override``. Hermes loads
it in-process; every callback below runs inside the agent's own Python.

It is a NO-OP unless OVERCODE_SESSION_NAME and OVERCODE_TMUX_SESSION are both
set in the environment — i.e. the hermes process was launched by overcode.
A user's own ``hermes`` sessions never see a hook fire.

Design (docs/design/agent-backend-hermes.md): the *plugin* translates
Hermes's observer-hook vocabulary (``pre_llm_call``, ``post_tool_call``,
``pre_approval_request``, ...) into the Claude Code hook vocabulary
(``UserPromptSubmit``, ``PostToolUse``, ``PermissionRequest``, ...) and pipes
one JSON object per event to ``overcode hook-handler`` on stdin — the same
subprocess Claude Code, codex and grok invoke. That keeps
``overcode.hook_handler`` a two-dialect module (snake_case Claude/codex,
camelCase grok): the third backend's dialect lives here, on the adapter
side, exactly as opencode's JavaScript plugin does for opencode.

This file must stay dependency-free (stdlib only) and must never raise into
Hermes: Hermes catches callback exceptions and logs a warning, but a plugin
that throws on every event is a plugin the user will disable.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from typing import Any, Dict, Optional

# Bumped whenever the translation changes, so
# ``overcode.backends.hermes.ensure_plugin_installed`` rewrites stale copies.
PLUGIN_VERSION = 1

# How long to wait for one ``overcode hook-handler`` invocation. Claude Code
# gives its hooks 60s; the handler itself takes ~200ms (a typer app boot).
# Bounded so a wedged handler can never stall the agent loop for long.
_HOOK_TIMEOUT_SECONDS = 10

# Hermes tool names -> Claude Code tool names, so the TUI's running-state
# detail ("Bash: sleep 5", foreground blocked_on classification) and the
# obligation tracking in hook_handler see the vocabulary they were written
# for. Anything not listed passes through under its Hermes name.
TOOL_NAME_ALIASES: Dict[str, str] = {
    "terminal": "Bash",
    "execute_code": "Bash",
    "read_file": "Read",
    "write_file": "Write",
    "patch": "Edit",
    "search_files": "Grep",
    "web_search": "WebSearch",
    "web_extract": "WebFetch",
    "delegate_task": "Agent",
}

# Approval surfaces that put a prompt in front of a *human*. Hermes fires
# ``pre_approval_request``/``post_approval_response`` once per surface it
# consults; the ``smart`` surface is the auxiliary-LLM pre-screen (default
# ``approvals.mode: smart``) and resolves in a few seconds with no human
# involved — reporting it as PermissionRequest would flash yellow for every
# dangerous command the aux model waves through.
_HUMAN_APPROVAL_SURFACES = frozenset({"cli", "tui", "desktop", "gateway"})

# Approval choices that mean "the tool is about to run".
_APPROVE_CHOICES = frozenset({"once", "session", "always"})

# ``on_session_finalize`` reasons that end the *process*, not just the
# conversation. A ``/new`` finalizes with ``session_boundary`` and is
# followed by on_session_reset/on_session_start for the replacement id — not
# a SessionEnd.
_TERMINAL_FINALIZE_REASONS = frozenset({"shutdown", "keyboard_interrupt"})


def _active() -> bool:
    env = os.environ
    return bool(env.get("OVERCODE_SESSION_NAME") and env.get("OVERCODE_TMUX_SESSION"))


def _hook_command() -> list:
    """argv for the handler: OVERCODE_HOOK_COMMAND (set by the launcher to
    the absolute overcode binary, or ``python -m overcode.cli``) else a bare
    ``overcode`` on PATH."""
    raw = os.environ.get("OVERCODE_HOOK_COMMAND") or "overcode"
    try:
        argv = shlex.split(raw)
    except ValueError:
        argv = [raw]
    return argv + ["hook-handler"]


def _alias_tool(name: Any) -> Optional[str]:
    if not isinstance(name, str) or not name:
        return None
    return TOOL_NAME_ALIASES.get(name, name)


def _payload(event: str, kwargs: Dict[str, Any], **fields: Any) -> Dict[str, Any]:
    """A Claude-shaped hook payload for ``event``."""
    payload: Dict[str, Any] = {
        "hook_event_name": event,
        "session_id": kwargs.get("session_id") or "",
        "cwd": os.getcwd(),
    }
    payload.update({k: v for k, v in fields.items() if v is not None})
    return payload


def deliver(payload: Dict[str, Any], *, wait_for_output: bool = False) -> Optional[str]:
    """Pipe ``payload`` to ``overcode hook-handler``. Returns stdout when
    asked for, else None. Never raises."""
    try:
        completed = subprocess.run(
            _hook_command(),
            input=json.dumps(payload, default=str),
            capture_output=True,
            text=True,
            timeout=_HOOK_TIMEOUT_SECONDS,
            env=os.environ.copy(),
        )
    except Exception:
        return None
    if wait_for_output:
        return (completed.stdout or "").strip()
    return None


# ── translation ──────────────────────────────────────────────────────────
#
# Each function returns the Claude-shaped payload for one Hermes hook, or
# None when the event has no overcode-side meaning. Pure: no I/O, so they
# are unit-testable from overcode's own test suite (tests/unit/
# test_hermes_plugin.py) without a Hermes install.

def translate_on_session_start(kwargs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # SessionStart is the codex-style "learn the session id" event —
    # hook_handler records it into agent_session_ids for the stats reader.
    if not kwargs.get("session_id"):
        return None
    return _payload("SessionStart", kwargs)


def translate_on_session_reset(kwargs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # /new, /reset, /clear: Hermes swaps to a fresh id. The CLI reports it
    # as ``session_id`` (the gateway adds new_session_id; prefer that).
    new_id = kwargs.get("new_session_id") or kwargs.get("session_id")
    if not new_id:
        return None
    return _payload("SessionStart", {**kwargs, "session_id": new_id})


def translate_pre_llm_call(kwargs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # Hermes's own docs: "Claude Code's UserPromptSubmit event is
    # intentionally not a separate Hermes event — pre_llm_call fires at the
    # same place" (website/docs/user-guide/features/hooks.md).
    prompt = kwargs.get("user_message")
    return _payload(
        "UserPromptSubmit", kwargs,
        prompt=prompt if isinstance(prompt, str) else None,
        prompt_id=kwargs.get("turn_id"),
    )


def translate_pre_tool_call(kwargs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    args = kwargs.get("args")
    return _payload(
        "PreToolUse", kwargs,
        tool_name=_alias_tool(kwargs.get("tool_name")),
        tool_input=args if isinstance(args, dict) else None,
        tool_use_id=kwargs.get("tool_call_id"),
    )


def translate_post_tool_call(kwargs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    args = kwargs.get("args")
    # Hermes's ``status`` is the observer-grade outcome: ok | error |
    # blocked | cancelled. Only a genuine tool error is Claude's
    # PostToolUseFailure; blocked/cancelled still mean "the agent moved on".
    event = "PostToolUseFailure" if kwargs.get("status") == "error" else "PostToolUse"
    return _payload(
        event, kwargs,
        tool_name=_alias_tool(kwargs.get("tool_name")),
        tool_input=args if isinstance(args, dict) else None,
        tool_use_id=kwargs.get("tool_call_id"),
    )


def _approval_tool_input(kwargs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    command = kwargs.get("command")
    if not isinstance(command, str):
        return None
    tool_input: Dict[str, Any] = {"command": command}
    description = kwargs.get("description")
    if isinstance(description, str) and description:
        tool_input["description"] = description
    return tool_input


def _is_human_surface(kwargs: Dict[str, Any]) -> bool:
    surface = kwargs.get("surface")
    # An unknown/missing surface is treated as human-facing: a false
    # waiting_approval clears on the next event, a missed one sticks.
    return not isinstance(surface, str) or surface in _HUMAN_APPROVAL_SURFACES


def translate_pre_approval_request(kwargs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not _is_human_surface(kwargs):
        return None
    return _payload(
        "PermissionRequest", kwargs,
        tool_name="Bash",
        tool_input=_approval_tool_input(kwargs),
    )


def translate_post_approval_response(kwargs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not _is_human_surface(kwargs):
        return None
    if kwargs.get("choice") not in _APPROVE_CHOICES:
        # deny / timeout / cancelled: Hermes fires post_tool_call (status
        # blocked) immediately after, which settles the status on its own.
        return None
    # Approved: the command runs now. Report it as the in-flight tool so the
    # running-state detail shows "Bash: <command>" until post_tool_call.
    return _payload(
        "PreToolUse", kwargs,
        tool_name="Bash",
        tool_input=_approval_tool_input(kwargs),
    )


def translate_post_llm_call(kwargs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return _payload("Stop", kwargs)


def translate_on_session_end(kwargs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # Fires at the end of every run_conversation() call (turn-scoped) and
    # again from the exit handler with reason=shutdown; the latter is
    # on_session_finalize's job.
    if kwargs.get("reason") in _TERMINAL_FINALIZE_REASONS:
        return None
    if kwargs.get("interrupted"):
        # The user's Ctrl-C mid-turn: Hermes prints "Operation interrupted"
        # and returns to the prompt with no post_llm_call. Same shape as
        # codex's Interrupt event, which _HOOK_STATUS_MAP already downgrades
        # to waiting_user.
        return _payload("Interrupt", kwargs)
    if kwargs.get("failed"):
        return _payload("StopFailure", kwargs)
    if kwargs.get("completed"):
        return _payload("Stop", kwargs)
    return None


def translate_on_session_finalize(kwargs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if kwargs.get("reason") not in _TERMINAL_FINALIZE_REASONS:
        return None
    return _payload("SessionEnd", kwargs)


TRANSLATORS = {
    "on_session_start": translate_on_session_start,
    "on_session_reset": translate_on_session_reset,
    "pre_llm_call": translate_pre_llm_call,
    "pre_tool_call": translate_pre_tool_call,
    "post_tool_call": translate_post_tool_call,
    "pre_approval_request": translate_pre_approval_request,
    "post_approval_response": translate_post_approval_response,
    "post_llm_call": translate_post_llm_call,
    "on_session_end": translate_on_session_end,
    "on_session_finalize": translate_on_session_finalize,
}


def handle(hook_name: str, kwargs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Translate + deliver one Hermes hook. Returns the hook's return value.

    ``pre_llm_call`` is the one event whose handler output matters: Claude
    Code injects a UserPromptSubmit hook's stdout into the conversation, and
    Hermes does the same for a ``{"context": ...}`` return from
    pre_llm_call — so overcode's time/budget context line reaches a Hermes
    agent exactly as it reaches a Claude one.
    """
    if not _active():
        return None
    translator = TRANSLATORS.get(hook_name)
    if translator is None:
        return None
    try:
        payload = translator(kwargs)
    except Exception:
        return None
    if payload is None:
        return None
    if hook_name == "pre_llm_call":
        context = deliver(payload, wait_for_output=True)
        if context:
            return {"context": context}
        return None
    deliver(payload)
    return None


def _make_callback(hook_name: str):
    def callback(**kwargs):
        try:
            return handle(hook_name, kwargs)
        except Exception:
            return None
    callback.__name__ = f"overcode_{hook_name}"
    return callback


def register(ctx) -> None:
    """Hermes plugin entry point."""
    for hook_name in TRANSLATORS:
        try:
            ctx.register_hook(hook_name, _make_callback(hook_name))
        except Exception:
            # An unknown hook name on an older/newer Hermes is not fatal —
            # the remaining events still deliver.
            continue
