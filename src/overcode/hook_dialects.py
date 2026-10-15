"""Each backend's hook-payload dialect.

``overcode hook-handler`` reads one JSON payload per hook event and writes
the agent's hook state in Claude Code's vocabulary: snake_case keys
(``hook_event_name``, ``session_id``, ``tool_name``, ...) and PascalCase
event names. A backend whose CLI speaks something else owns a
``HookDialect`` that translates its payload into that shape
(``AgentBackend.hook_dialect`` / ``normalize_hook_payload``), and
``for_backend`` tells it from the payload itself.

Registered hook commands are a bare ``overcode hook-handler``, never
``--backend <name>``: a running agent outlives the overcode that launched
it, and an overcode that predates the option (0.5.x after a downgrade)
rejects it with exit 2, which Claude Code and codex treat as "block": every
prompt and tool call refused. The handler still accepts ``--backend`` from
commands registered while it was emitted.

The hook path runs on every tool call of every agent, so this module is
stdlib-only and the handler resolves a dialect here by backend name
(``for_backend``) rather than through the backend registry, whose import
costs about as much again as the handler itself.
"""

from __future__ import annotations


class HookDialect:
    """Claude Code's shape, which codex and the Hermes plugin also send."""

    # Whether the payload's prompt_id names the turn, so a late Stop for an
    # older turn can be told from the current one (see handle_hook_event).
    tracks_prompt_turns = False

    def normalize(self, data: dict) -> dict | None:
        """The payload in Claude's vocabulary, or None to drop the event."""
        return data


# Key aliases for grok's camelCase stdin (Phase 4). codex's stdin is
# already snake_case and Claude-shaped (design doc §2.3, Appendix A:
# hook_event_name/session_id/turn_id/transcript_path/cwd/model/
# permission_mode/prompt), so codex uses the default HookDialect.
#
# grok's own porting guide (~/.grok/docs/user-guide/10-hooks.md, "Porting
# Claude Code stop hooks") is the source for the fields beyond the original
# five: promptId/notificationType/subagentType key the turn-tracking and
# subagent-skip rules _apply_grok_semantics implements below; the rest
# (transcriptPath, workspaceRoot, stopHookActive, lastAssistantMessage,
# backgroundTasks, sessionCrons, cancelledBy, cancelTrigger, reasonDetails,
# errorDetails) are carried through mostly for parity/diagnostics. toolResult
# is grok's name for Claude's tool_response — not just a casing difference —
# so it gets its own explicit mapping rather than a mechanical camelCase->
# snake_case rule.
_CAMEL_CASE_KEY_ALIASES: dict[str, str] = {
    "hookEventName": "hook_event_name",
    "sessionId": "session_id",
    "toolName": "tool_name",
    "toolInput": "tool_input",
    "toolUseId": "tool_use_id",
    "permissionMode": "permission_mode",
    "promptId": "prompt_id",
    "notificationType": "notification_type",
    "subagentType": "subagent_type",
    "transcriptPath": "transcript_path",
    "workspaceRoot": "workspace_root",
    "stopHookActive": "stop_hook_active",
    "lastAssistantMessage": "last_assistant_message",
    "backgroundTasks": "background_tasks",
    "sessionCrons": "session_crons",
    "cancelledBy": "cancelled_by",
    "cancelTrigger": "cancel_trigger",
    "reasonDetails": "reason_details",
    "errorDetails": "error_details",
    "toolResult": "tool_response",
}

# grok's hookEventName *value* is snake_case too (e.g. "user_prompt_submit"),
# unlike Claude/codex's PascalCase — a second dialect axis beyond key casing.
# ~/.grok/docs/user-guide/10-hooks.md, "Hook Events" + "Cursor Hook
# Compatibility" tables list these verbatim (env var section: GROK_HOOK_EVENT
# examples pre_tool_use/session_start/post_tool_use/session_end/stop/
# notification). Unmapped names pass through unchanged rather than raising —
# a future grok event overcode doesn't know about degrades to an unrecognized
# event string, not a crash.
_GROK_EVENT_NAME_MAP: dict[str, str] = {
    "user_prompt_submit": "UserPromptSubmit",
    "pre_tool_use": "PreToolUse",
    "post_tool_use": "PostToolUse",
    "post_tool_use_failure": "PostToolUseFailure",
    "permission_denied": "PermissionDenied",
    "stop": "Stop",
    "stop_failure": "StopFailure",
    "stop_cancelled": "StopCancelled",
    "notification": "Notification",
    "session_start": "SessionStart",
    "session_end": "SessionEnd",
    "subagent_start": "SubagentStart",
    "subagent_stop": "SubagentStop",
    "pre_compact": "PreCompact",
    "post_compact": "PostCompact",
}

# grok->Claude tool-name aliases, taken verbatim from 10-hooks.md's "Tool
# Name Aliases" section (design doc §3.3's "full table is in the grok hooks
# doc"). Applied to tool_name so obligation tracking and the ⏰ column's tool
# badges see the same vocabulary regardless of backend. A name grok doesn't
# alias (or an already-Claude-shaped name — grok's matcher keeps both) passes
# through unchanged.
_GROK_TOOL_NAME_ALIASES: dict[str, str] = {
    "run_terminal_command": "Bash",
    "read_file": "Read",
    "search_replace": "Edit",
    "grep": "Grep",
    "list_dir": "Glob",
    "web_search": "WebSearch",
    "spawn_subagent": "Task",
}


def _apply_grok_semantics(data: dict) -> dict | None:
    """grok-only event remapping/filtering, applied after key normalization.

    Returns ``None`` to mean "drop this event entirely" — no state write, no
    event-log append. Three things get dropped, per
    ~/.grok/docs/user-guide/10-hooks.md's own busy/idle recipe:

    * any event carrying ``subagentType`` — "a subagent's stop is not the
      session's" (subagentType survives key-casing as subagent_type).
    * the session-teardown ``Stop`` (``reason`` != ``"end_turn"``, e.g.
      ``"shutdown"``/``"channel_closed"``) — that is SessionEnd's job; a
      hook handler that doesn't filter this double-settles on every exit.
    * a ``Notification`` whose ``notificationType`` isn't one of the two
      matchers overcode registers (``permission_prompt``, ``idle_prompt``) —
      no overcode-side meaning yet.

    Two remaps happen for everything that survives:

    * ``Notification(permission_prompt)`` -> ``PermissionRequest``,
      ``Notification(idle_prompt)`` -> ``Stop`` (the idle backstop for turns
      that report none of Stop/StopFailure/StopCancelled).
    * ``StopCancelled`` -> ``Stop`` (interrupt case; the existing
      ``interrupt_prompt_markers`` pane fallback in ``GROK_PATTERNS`` already
      renders this correctly as waiting_user once the event reads "Stop").
    """
    if data.get("subagent_type"):
        return None

    data = dict(data)
    tool_name = data.get("tool_name")
    if tool_name in _GROK_TOOL_NAME_ALIASES:
        data["tool_name"] = _GROK_TOOL_NAME_ALIASES[tool_name]

    raw_event = data.get("hook_event_name")
    event = _GROK_EVENT_NAME_MAP.get(raw_event, raw_event)

    if event == "Notification":
        notification_type = data.get("notification_type")
        if notification_type == "permission_prompt":
            event = "PermissionRequest"
        elif notification_type == "idle_prompt":
            # Through the same path as Claude's (_settle_idle_prompt): it
            # settles only a turn still reading running, in this conversation,
            # never a pending permission or another conversation's state
            pass
        else:
            return None
    elif event == "StopCancelled":
        event = "Stop"
    elif event == "Stop":
        reason = data.get("reason")
        if reason is not None and reason != "end_turn":
            return None

    data["hook_event_name"] = event
    return data


class GrokHookDialect(HookDialect):
    """grok: camelCase keys, snake_case event names, its own drop rules."""

    tracks_prompt_turns = True

    def normalize(self, data: dict) -> dict | None:
        """Translate the keys (``_translate_camel_case``), then apply
        ``_apply_grok_semantics``."""
        return _apply_grok_semantics(_translate_camel_case(data))


def _translate_camel_case(data: dict) -> dict:
    """Add the snake_case twin of each camelCase key grok sends.

    Translated keys are added alongside the originals rather than replacing
    them, so a field this table doesn't yet know about still survives
    untouched. A payload already in snake_case is returned as is.
    """
    if "hookEventName" not in data or "hook_event_name" in data:
        return data
    translated = dict(data)
    for camel, snake in _CAMEL_CASE_KEY_ALIASES.items():
        if camel in data and snake not in translated:
            translated[snake] = data[camel]
    return translated


CLAUDE = HookDialect()
GROK = GrokHookDialect()

# Backends whose dialect isn't Claude's. Must agree with each backend's
# ``hook_dialect`` (pinned by a test); kept here so the hook path never
# imports the backend registry.
_BY_BACKEND: dict[str, HookDialect] = {"grok": GROK}


def for_backend(backend: str | None, data: dict) -> HookDialect:
    """The dialect of a payload sent by ``backend``'s hook command.

    Registered commands send no name (see the module docstring): grok's
    camelCase ``hookEventName`` key gives it away, anything else is
    Claude-shaped. A name, from a command registered while ``--backend``
    was emitted, still wins.
    """
    if backend:
        return _BY_BACKEND.get(backend, CLAUDE)
    if isinstance(data, dict) and "hookEventName" in data:
        return GROK
    return CLAUDE
