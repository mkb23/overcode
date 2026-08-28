"""grok's global hooks file + the hook_handler dialect it feeds.

Two halves, mirroring the split the design doc's Phase 4 brief calls for:

* ``TestHooksFile*`` — ``~/.grok/hooks/overcode.json``'s footprint contract
  (marker, idempotence, never-clobber, inertness-guarded command), the exact
  analogue of ``tests/unit/test_opencode_plugin.py``'s coverage of
  ``ensure_plugin_installed``.
* ``TestGrokDialect*`` — ``hook_handler``'s camelCase/tool-alias/event
  translation, using Appendix B's live-captured payloads (design doc §3.3)
  as fixtures wherever a real one was captured, and the bundled hooks doc's
  own documented rules (subagentType skip, promptId staleness, Stop reason
  filter, StopCancelled interrupt, Notification matchers) elsewhere.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.backends.grok import (
    GROK_HOOKS_MARKER,
    build_hooks_file_content,
    ensure_hooks_installed,
    hooks_file_path,
    hooks_installed,
)
from overcode.hook_handler import (
    GROK_HOOK_EVENTS,
    GROK_NOTIFICATION_MATCHERS,
    _apply_grok_semantics,
    _normalize_hook_payload,
    handle_hook_event,
)


@pytest.fixture(autouse=True)
def grok_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GROK_HOME", str(tmp_path / ".grok"))
    return tmp_path / ".grok"


class TestHooksFileContent:
    def test_carries_the_marker(self):
        content = build_hooks_file_content()
        assert GROK_HOOKS_MARKER in content["description"]

    def test_registers_every_design_doc_event(self):
        content = build_hooks_file_content()
        for event in GROK_HOOK_EVENTS:
            assert event in content["hooks"]
        assert content["hooks"].keys() >= {
            "UserPromptSubmit", "PreToolUse", "PostToolUse",
            "Stop", "StopFailure", "StopCancelled",
            "SessionEnd", "SessionStart", "Notification",
        }

    def test_notification_registers_both_matchers(self):
        content = build_hooks_file_content()
        matchers = {entry["matcher"] for entry in content["hooks"]["Notification"]}
        assert matchers == set(GROK_NOTIFICATION_MATCHERS)

    def test_command_is_inertness_guarded(self):
        content = build_hooks_file_content()
        cmd = content["hooks"]["Stop"][0]["hooks"][0]["command"]
        assert 'OVERCODE_SESSION_NAME' in cmd
        assert "hook-handler" in cmd

    def test_no_blocking_stop_gate_timeout(self):
        # Grok defaults Stop/StopFailure/StopCancelled gates to 600s; the
        # design brief calls for short timeouts explicitly since none of
        # these registrations ever return a block decision.
        content = build_hooks_file_content()
        for event in ("Stop", "StopFailure", "StopCancelled"):
            assert content["hooks"][event][0]["hooks"][0]["timeout"] <= 5


class TestEnsureHooksInstalled:
    def test_writes_when_missing(self, grok_home):
        target = ensure_hooks_installed()
        assert target == hooks_file_path()
        assert target.exists()
        assert GROK_HOOKS_MARKER in json.loads(target.read_text())["description"]

    def test_idempotent_when_unchanged(self, grok_home):
        first = ensure_hooks_installed()
        mtime1 = first.stat().st_mtime_ns
        second = ensure_hooks_installed()
        assert second.stat().st_mtime_ns == mtime1 or json.loads(
            first.read_text()
        ) == json.loads(second.read_text())

    def test_rewrites_a_marked_file_that_has_drifted(self, grok_home):
        target = hooks_file_path()
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps({
            "description": f"{GROK_HOOKS_MARKER}: old",
            "hooks": {"Stop": []},
        }))
        ensure_hooks_installed()
        content = json.loads(target.read_text())
        assert "UserPromptSubmit" in content["hooks"]

    def test_never_clobbers_a_de_markered_file(self, grok_home):
        target = hooks_file_path()
        target.parent.mkdir(parents=True)
        user_content = json.dumps({"hooks": {"SessionStart": []}})
        target.write_text(user_content)
        result = ensure_hooks_installed()
        assert result is None
        assert target.read_text() == user_content

    def test_never_clobbers_invalid_json(self, grok_home):
        target = hooks_file_path()
        target.parent.mkdir(parents=True)
        target.write_text("not json at all")
        result = ensure_hooks_installed()
        assert result is None
        assert target.read_text() == "not json at all"

    def test_hooks_installed_reports_presence(self, grok_home):
        assert hooks_installed() is False
        ensure_hooks_installed()
        assert hooks_installed() is True

    def test_hooks_installed_false_for_a_de_markered_file(self, grok_home):
        target = hooks_file_path()
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps({"hooks": {}}))
        assert hooks_installed() is False


class TestPrepareLaunchInstallsHooks:
    def test_prepare_launch_writes_the_global_file(self, grok_home):
        from overcode.backends.grok import get_grok_backend
        from overcode.backends.base import LaunchSpec

        get_grok_backend().prepare_launch(LaunchSpec())
        assert hooks_installed() is True


# ---------------------------------------------------------------------------
# hook_handler dialect: Appendix B's live-captured payloads (design doc
# §3.3), field values kept verbatim.
# ---------------------------------------------------------------------------

USER_PROMPT_SUBMIT = {
    "hookEventName": "user_prompt_submit",
    "sessionId": "01a043a2-f2fc-7f72-ac4a-6af740fcd4dc",
    "cwd": "/Users/mike/.claude/jobs/f6bc7dbe/tmp/probe-grok",
    "workspaceRoot": "/Users/mike/.claude/jobs/f6bc7dbe/tmp/probe-grok",
    "timestamp": "2026-08-27T14:32:25.782754+00:00",
    "promptId": "1f28e0e5-aaaa-bbbb-cccc-000000000000",
    "permissionMode": "default",
    "prompt": "<user_query>\nRun the command: echo hookprobe\n</user_query>",
}

NOTIFICATION_PERMISSION_PROMPT = {
    "hookEventName": "notification",
    "sessionId": "01a043a2-f2fc-7f72-ac4a-6af740fcd4dc",
    "cwd": "/Users/mike/.claude/jobs/f6bc7dbe/tmp/probe-grok",
    "workspaceRoot": "/Users/mike/.claude/jobs/f6bc7dbe/tmp/probe-grok",
    "timestamp": "2026-08-27T14:32:26+00:00",
    "transcriptPath": "/Users/mike/.grok/sessions/x/y/updates.jsonl",
    "permissionMode": "default",
    "notificationType": "permission_prompt",
    "message": "Tool permission requested",
    "level": "info",
}

NOTIFICATION_IDLE_PROMPT = {
    "hookEventName": "notification",
    "sessionId": "01a043a2-f2fc-7f72-ac4a-6af740fcd4dc",
    "cwd": "/Users/mike/.claude/jobs/f6bc7dbe/tmp/probe-grok",
    "timestamp": "2026-08-27T14:33:00+00:00",
    "notificationType": "idle_prompt",
    "message": "Session idle",
    "level": "info",
}

STOP_END_TURN = {
    "hookEventName": "stop",
    "sessionId": "01a043a2-f2fc-7f72-ac4a-6af740fcd4dc",
    "cwd": "/Users/mike/.claude/jobs/f6bc7dbe/tmp/probe-grok",
    "workspaceRoot": "/Users/mike/.claude/jobs/f6bc7dbe/tmp/probe-grok",
    "timestamp": "2026-08-27T14:32:30+00:00",
    "transcriptPath": "/Users/mike/.grok/sessions/x/y/updates.jsonl",
    "promptId": "1f28e0e5-aaaa-bbbb-cccc-000000000000",
    "permissionMode": "default",
    "reason": "end_turn",
    "stopHookActive": False,
    "lastAssistantMessage": "`hookprobe`",
    "backgroundTasks": [],
    "sessionCrons": [],
}

STOP_SHUTDOWN = {
    "hookEventName": "stop",
    "sessionId": "01a043a2-f2fc-7f72-ac4a-6af740fcd4dc",
    "cwd": "/Users/mike/.claude/jobs/f6bc7dbe/tmp/probe-grok",
    "timestamp": "2026-08-27T14:40:00+00:00",
    "permissionMode": "default",
    "reason": "shutdown",
    "stopHookActive": False,
}


class TestGrokEventSemantics:
    """_apply_grok_semantics after key normalization (design doc §3.3)."""

    def _normalized(self, payload):
        return _normalize_hook_payload(dict(payload))

    def test_user_prompt_submit_passes_through(self):
        result = _apply_grok_semantics(self._normalized(USER_PROMPT_SUBMIT))
        assert result["hook_event_name"] == "UserPromptSubmit"
        assert result["prompt_id"] == USER_PROMPT_SUBMIT["promptId"]

    def test_permission_prompt_notification_becomes_permission_request(self):
        result = _apply_grok_semantics(self._normalized(NOTIFICATION_PERMISSION_PROMPT))
        assert result["hook_event_name"] == "PermissionRequest"

    def test_idle_prompt_notification_becomes_stop(self):
        result = _apply_grok_semantics(self._normalized(NOTIFICATION_IDLE_PROMPT))
        assert result["hook_event_name"] == "Stop"

    def test_other_notification_types_are_dropped(self):
        payload = dict(NOTIFICATION_IDLE_PROMPT)
        payload["notificationType"] = "task_complete"
        result = _apply_grok_semantics(self._normalized(payload))
        assert result is None

    def test_stop_cancelled_becomes_stop(self):
        payload = {
            "hookEventName": "stop_cancelled",
            "sessionId": "sid",
            "promptId": "pid-1",
            "reason": "user_interrupt",
            "cancelledBy": "user",
            "cancelTrigger": "esc",
        }
        result = _apply_grok_semantics(self._normalized(payload))
        assert result["hook_event_name"] == "Stop"

    def test_genuine_stop_end_turn_passes_through(self):
        result = _apply_grok_semantics(self._normalized(STOP_END_TURN))
        assert result["hook_event_name"] == "Stop"

    def test_session_teardown_stop_is_dropped(self):
        # reason:"shutdown" — SessionEnd's job, not Stop's; a handler that
        # doesn't filter this double-settles on every session exit.
        result = _apply_grok_semantics(self._normalized(STOP_SHUTDOWN))
        assert result is None

    def test_channel_closed_reason_is_also_dropped(self):
        payload = dict(STOP_SHUTDOWN)
        payload["reason"] = "channel_closed"
        result = _apply_grok_semantics(self._normalized(payload))
        assert result is None

    def test_stop_failure_passes_through(self):
        payload = {
            "hookEventName": "stop_failure",
            "sessionId": "sid",
            "promptId": "pid-1",
            "error": "rate_limit",
            "errorDetails": "429 from provider",
        }
        result = _apply_grok_semantics(self._normalized(payload))
        assert result["hook_event_name"] == "StopFailure"

    def test_subagent_events_are_dropped_entirely(self):
        payload = dict(STOP_END_TURN)
        payload["subagentType"] = "explore"
        result = _apply_grok_semantics(self._normalized(payload))
        assert result is None

    def test_subagent_notification_is_also_dropped(self):
        payload = dict(NOTIFICATION_PERMISSION_PROMPT)
        payload["subagentType"] = "explore"
        result = _apply_grok_semantics(self._normalized(payload))
        assert result is None

    def test_tool_result_key_is_mapped_to_tool_response(self):
        payload = {
            "hookEventName": "post_tool_use",
            "sessionId": "sid",
            "toolName": "run_terminal_command",
            "toolResult": {"output": "hi"},
        }
        result = _apply_grok_semantics(self._normalized(payload))
        assert result["tool_response"] == {"output": "hi"}

    @pytest.mark.parametrize("grok_name,claude_name", [
        ("run_terminal_command", "Bash"),
        ("read_file", "Read"),
        ("search_replace", "Edit"),
        ("grep", "Grep"),
        ("list_dir", "Glob"),
        ("web_search", "WebSearch"),
        ("spawn_subagent", "Task"),
    ])
    def test_tool_name_aliases(self, grok_name, claude_name):
        payload = {
            "hookEventName": "pre_tool_use",
            "sessionId": "sid",
            "toolName": grok_name,
            "toolInput": {},
        }
        result = _apply_grok_semantics(self._normalized(payload))
        assert result["tool_name"] == claude_name

    def test_unaliased_tool_name_passes_through(self):
        payload = {
            "hookEventName": "pre_tool_use", "sessionId": "sid",
            "toolName": "some_mcp_tool", "toolInput": {},
        }
        result = _apply_grok_semantics(self._normalized(payload))
        assert result["tool_name"] == "some_mcp_tool"

    def test_session_start_and_end_pass_through(self):
        for raw, expected in (("session_start", "SessionStart"), ("session_end", "SessionEnd")):
            payload = {"hookEventName": raw, "sessionId": "sid"}
            result = _apply_grok_semantics(self._normalized(payload))
            assert result["hook_event_name"] == expected


class TestHandleHookEventGrokIntegration:
    """End-to-end through handle_hook_event() — stdin to hook_state file."""

    def _send(self, tmp_path, monkeypatch, payload):
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "grok-agent")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = json.dumps(payload)
            handle_hook_event()

    def _state(self, tmp_path):
        return json.loads((tmp_path / "agents" / "hook_state_grok-agent.json").read_text())

    def test_permission_prompt_notification_yields_permission_request_state(self, tmp_path, monkeypatch):
        self._send(tmp_path, monkeypatch, NOTIFICATION_PERMISSION_PROMPT)
        assert self._state(tmp_path)["event"] == "PermissionRequest"

    def test_session_teardown_stop_writes_nothing(self, tmp_path, monkeypatch):
        self._send(tmp_path, monkeypatch, STOP_SHUTDOWN)
        assert not (tmp_path / "agents" / "hook_state_grok-agent.json").exists()

    def test_genuine_stop_settles(self, tmp_path, monkeypatch):
        self._send(tmp_path, monkeypatch, USER_PROMPT_SUBMIT)
        self._send(tmp_path, monkeypatch, STOP_END_TURN)
        assert self._state(tmp_path)["event"] == "Stop"

    def test_stale_stop_report_for_a_superseded_turn_is_ignored(self, tmp_path, monkeypatch):
        first_prompt = dict(USER_PROMPT_SUBMIT)
        first_prompt["promptId"] = "turn-1"
        second_prompt = dict(USER_PROMPT_SUBMIT)
        second_prompt["promptId"] = "turn-2"
        stale_stop = dict(STOP_END_TURN)
        stale_stop["promptId"] = "turn-1"

        self._send(tmp_path, monkeypatch, first_prompt)
        self._send(tmp_path, monkeypatch, second_prompt)
        # A late StopCancelled/Stop report for turn-1 arrives after turn-2
        # already started — must not clobber turn-2's running state.
        self._send(tmp_path, monkeypatch, stale_stop)

        state = self._state(tmp_path)
        assert state["event"] == "UserPromptSubmit"
        assert state["active_prompt_id"] == "turn-2"

    def test_fresh_stop_report_for_the_current_turn_settles(self, tmp_path, monkeypatch):
        prompt = dict(USER_PROMPT_SUBMIT)
        prompt["promptId"] = "turn-1"
        stop = dict(STOP_END_TURN)
        stop["promptId"] = "turn-1"

        self._send(tmp_path, monkeypatch, prompt)
        self._send(tmp_path, monkeypatch, stop)
        assert self._state(tmp_path)["event"] == "Stop"

    def test_idle_prompt_backstop_settles_unconditionally(self, tmp_path, monkeypatch):
        # idle_prompt carries no promptId — must settle even with no prior
        # UserPromptSubmit recorded at all.
        self._send(tmp_path, monkeypatch, NOTIFICATION_IDLE_PROMPT)
        assert self._state(tmp_path)["event"] == "Stop"

    def test_subagent_stop_never_flips_the_session_state(self, tmp_path, monkeypatch):
        self._send(tmp_path, monkeypatch, USER_PROMPT_SUBMIT)
        subagent_stop = dict(STOP_END_TURN)
        subagent_stop["subagentType"] = "explore"
        self._send(tmp_path, monkeypatch, subagent_stop)
        # Still UserPromptSubmit — the subagent's own stop never touched it.
        assert self._state(tmp_path)["event"] == "UserPromptSubmit"

    def test_stop_cancelled_settles_as_waiting_user(self, tmp_path, monkeypatch):
        prompt = dict(USER_PROMPT_SUBMIT)
        prompt["promptId"] = "turn-1"
        cancelled = {
            "hookEventName": "stop_cancelled",
            "sessionId": "sid",
            "promptId": "turn-1",
            "reason": "user_interrupt",
            "cancelledBy": "user",
            "cancelTrigger": "esc",
        }
        self._send(tmp_path, monkeypatch, prompt)
        self._send(tmp_path, monkeypatch, cancelled)
        assert self._state(tmp_path)["event"] == "Stop"

    def test_grok_dialect_never_leaks_into_claude_payloads(self, tmp_path, monkeypatch):
        # A plain Claude/codex snake_case payload has no "hookEventName" key
        # at all, so is_grok_dialect must stay False and grok-only filtering
        # (subagent skip, reason filter, staleness) must never apply to it.
        self._send(tmp_path, monkeypatch, {
            "hook_event_name": "Stop", "session_id": "sid", "reason": "shutdown",
        })
        # Claude/codex have no "shutdown"-reason filtering — this must settle.
        assert self._state(tmp_path)["event"] == "Stop"
