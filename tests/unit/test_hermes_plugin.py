"""The bundled Hermes plugin's hook translation, run without a Hermes install.

``src/overcode/hermes_plugin/__init__.py`` is stdlib-only and its
translators are pure, so overcode's own suite can pin the Hermes → Claude
vocabulary mapping the hook handler depends on. Payload shapes come from
the shell-hook/plugin captures taken against Hermes v0.21.3 on 2026-09-17
(``docs/design/agent-backend-hermes.md``).
"""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode import hermes_plugin as plugin
from overcode.hook_handler import CODEX_HOOK_EVENTS, OVERCODE_HOOKS
from overcode.hook_status_detector import _HOOK_STATUS_MAP


SID = "20260917_131721_8f80ea"
TURN = f"{SID}:{SID}:0ac920d6"


def kw(**extra):
    base = {"session_id": SID, "platform": "cli", "telemetry_schema_version": "hermes.observer.v1"}
    base.update(extra)
    return base


class TestTranslation:
    def test_session_start_records_the_id(self):
        out = plugin.translate_on_session_start(kw(model="gpt-5-mini"))
        assert out["hook_event_name"] == "SessionStart"
        assert out["session_id"] == SID
        assert "cwd" in out

    def test_session_reset_reports_the_new_id(self):
        out = plugin.translate_on_session_reset(kw(reason="new_session"))
        assert out == {**out, "hook_event_name": "SessionStart", "session_id": SID}
        gateway = plugin.translate_on_session_reset(kw(old_session_id="old", new_session_id="new"))
        assert gateway["session_id"] == "new"

    def test_pre_llm_call_is_user_prompt_submit(self):
        out = plugin.translate_pre_llm_call(kw(task_id=SID, turn_id=TURN, user_message="hi", is_first_turn=True))
        assert out["hook_event_name"] == "UserPromptSubmit"
        assert out["prompt"] == "hi"
        assert out["prompt_id"] == TURN

    def test_tool_calls_alias_hermes_tools_to_claude_names(self):
        pre = plugin.translate_pre_tool_call(kw(
            tool_name="terminal", args={"command": "sleep 5", "timeout": 120},
            tool_call_id="call_1", turn_id=TURN,
        ))
        assert pre["hook_event_name"] == "PreToolUse"
        assert pre["tool_name"] == "Bash"
        assert pre["tool_input"] == {"command": "sleep 5", "timeout": 120}
        assert pre["tool_use_id"] == "call_1"

        post = plugin.translate_post_tool_call(kw(
            tool_name="terminal", args={"command": "sleep 5"}, tool_call_id="call_1",
            status="ok", result="{}", duration_ms=5000,
        ))
        assert post["hook_event_name"] == "PostToolUse"
        assert post["tool_name"] == "Bash"

    def test_unknown_tool_names_pass_through(self):
        out = plugin.translate_pre_tool_call(kw(tool_name="kanban_create", args={}))
        assert out["tool_name"] == "kanban_create"

    def test_tool_error_is_post_tool_use_failure(self):
        out = plugin.translate_post_tool_call(kw(tool_name="read_file", args={"path": "x"}, status="error"))
        assert out["hook_event_name"] == "PostToolUseFailure"
        assert out["tool_name"] == "Read"

    @pytest.mark.parametrize("status", ["blocked", "cancelled", "ok", None])
    def test_non_error_outcomes_are_post_tool_use(self, status):
        out = plugin.translate_post_tool_call(kw(tool_name="terminal", args={}, status=status))
        assert out["hook_event_name"] == "PostToolUse"

    def test_human_approval_request_is_permission_request(self):
        out = plugin.translate_pre_approval_request(kw(
            command="rm -rf ./junkdir_a", description="recursive delete",
            pattern_key="recursive delete", session_key=SID, surface="cli",
        ))
        assert out["hook_event_name"] == "PermissionRequest"
        assert out["tool_name"] == "Bash"
        assert out["tool_input"] == {"command": "rm -rf ./junkdir_a", "description": "recursive delete"}

    def test_smart_surface_pre_screen_is_dropped(self):
        # The aux-LLM surface consults no human and resolves in seconds.
        assert plugin.translate_pre_approval_request(kw(command="rm -rf x", surface="smart")) is None
        assert plugin.translate_post_approval_response(kw(command="rm -rf x", surface="smart", choice="smart_deny")) is None

    @pytest.mark.parametrize("surface", ["cli", "tui", None])
    def test_other_surfaces_are_human(self, surface):
        out = plugin.translate_pre_approval_request(kw(command="x", surface=surface))
        assert out["hook_event_name"] == "PermissionRequest"

    @pytest.mark.parametrize("choice", ["once", "session", "always"])
    def test_approval_becomes_the_running_tool(self, choice):
        out = plugin.translate_post_approval_response(kw(command="rm -rf ./junkdir_a", surface="cli", choice=choice))
        assert out["hook_event_name"] == "PreToolUse"
        assert out["tool_name"] == "Bash"
        assert out["tool_input"]["command"] == "rm -rf ./junkdir_a"

    @pytest.mark.parametrize("choice", ["deny", "timeout", "cancelled"])
    def test_denials_are_left_to_post_tool_call(self, choice):
        assert plugin.translate_post_approval_response(kw(command="x", surface="cli", choice=choice)) is None

    def test_post_llm_call_is_stop(self):
        assert plugin.translate_post_llm_call(kw(assistant_response="done"))["hook_event_name"] == "Stop"

    def test_session_end_variants(self):
        completed = kw(completed=True, failed=False, interrupted=False, turn_exit_reason="text_response(finish_reason=stop)")
        assert plugin.translate_on_session_end(completed)["hook_event_name"] == "Stop"
        interrupted = kw(completed=False, failed=False, interrupted=True, turn_exit_reason="interrupted_by_user")
        assert plugin.translate_on_session_end(interrupted)["hook_event_name"] == "Interrupt"
        failed = kw(completed=False, failed=True, interrupted=False)
        assert plugin.translate_on_session_end(failed)["hook_event_name"] == "StopFailure"
        # The exit handler's on_session_end is finalize's job.
        assert plugin.translate_on_session_end(kw(completed=False, interrupted=True, reason="shutdown")) is None
        assert plugin.translate_on_session_end(kw(completed=False, failed=False, interrupted=False)) is None

    def test_finalize_only_on_process_exit(self):
        assert plugin.translate_on_session_finalize(kw(reason="shutdown"))["hook_event_name"] == "SessionEnd"
        assert plugin.translate_on_session_finalize(kw(reason="keyboard_interrupt"))["hook_event_name"] == "SessionEnd"
        # /new: finalize(session_boundary) -> reset(new id) -> start; not a teardown.
        assert plugin.translate_on_session_finalize(kw(reason="session_boundary")) is None
        assert plugin.translate_on_session_finalize(kw(reason="new_session")) is None
        assert plugin.translate_on_session_finalize(kw(reason="branch")) is None

    def test_every_emitted_event_is_one_the_handler_knows(self):
        # Everything the plugin can emit must already be status-mapped —
        # the whole point of translating on the adapter side is that
        # hook_handler/hook_status_detector need no hermes-specific rows.
        known = set(_HOOK_STATUS_MAP) | {e for e, _ in OVERCODE_HOOKS} | set(CODEX_HOOK_EVENTS)
        emitted = {
            "SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
            "PostToolUseFailure", "PermissionRequest", "Stop", "StopFailure",
            "Interrupt", "SessionEnd",
        }
        assert emitted <= known

    def test_manifest_lists_exactly_the_registered_hooks(self):
        manifest = (Path(plugin.__file__).parent / "plugin.yaml").read_text()
        declared = {line.strip()[2:] for line in manifest.splitlines() if line.strip().startswith("- ")}
        assert declared == set(plugin.TRANSLATORS)


class TestDelivery:
    def test_inert_without_overcode_env(self, monkeypatch):
        monkeypatch.delenv("OVERCODE_SESSION_NAME", raising=False)
        monkeypatch.delenv("OVERCODE_TMUX_SESSION", raising=False)
        with patch("overcode.hermes_plugin.subprocess.run") as run:
            assert plugin.handle("pre_llm_call", kw(user_message="x")) is None
        run.assert_not_called()

    def test_hook_command_from_env(self, monkeypatch):
        monkeypatch.setenv("OVERCODE_HOOK_COMMAND", "/usr/bin/python3 -m overcode.cli")
        assert plugin._hook_command() == ["/usr/bin/python3", "-m", "overcode.cli", "hook-handler"]
        monkeypatch.delenv("OVERCODE_HOOK_COMMAND")
        assert plugin._hook_command() == ["overcode", "hook-handler"]

    def test_pre_llm_call_returns_handler_stdout_as_context(self, monkeypatch):
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "hm")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setenv("OVERCODE_HOOK_COMMAND", "overcode")
        with patch("overcode.hermes_plugin.subprocess.run") as run:
            run.return_value = MagicMock(stdout="Current time: 13:17\n", returncode=0)
            out = plugin.handle("pre_llm_call", kw(user_message="x", turn_id=TURN))
        assert out == {"context": "Current time: 13:17"}
        sent = json.loads(run.call_args.kwargs["input"])
        assert sent["hook_event_name"] == "UserPromptSubmit" and sent["session_id"] == SID
        assert run.call_args.args[0] == ["overcode", "hook-handler"]

    def test_other_events_return_nothing(self, monkeypatch):
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "hm")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        with patch("overcode.hermes_plugin.subprocess.run") as run:
            run.return_value = MagicMock(stdout="ignored", returncode=0)
            assert plugin.handle("post_llm_call", kw()) is None
        assert json.loads(run.call_args.kwargs["input"])["hook_event_name"] == "Stop"

    def test_dropped_events_spawn_nothing(self, monkeypatch):
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "hm")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        with patch("overcode.hermes_plugin.subprocess.run") as run:
            plugin.handle("pre_approval_request", kw(command="x", surface="smart"))
            plugin.handle("no_such_hook", kw())
        run.assert_not_called()

    def test_delivery_never_raises(self, monkeypatch):
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "hm")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        with patch("overcode.hermes_plugin.subprocess.run", side_effect=subprocess.TimeoutExpired("x", 1)):
            assert plugin.handle("pre_llm_call", kw(user_message="x")) is None
            assert plugin.handle("post_tool_call", kw(tool_name="terminal", args={})) is None

    def test_register_installs_every_translator(self):
        ctx = MagicMock()
        plugin.register(ctx)
        registered = {call.args[0] for call in ctx.register_hook.call_args_list}
        assert registered == set(plugin.TRANSLATORS)
        callbacks = {call.args[0]: call.args[1] for call in ctx.register_hook.call_args_list}
        # Callbacks accept **kwargs (Hermes's compatibility contract).
        import inspect
        for cb in callbacks.values():
            assert any(p.kind is inspect.Parameter.VAR_KEYWORD for p in inspect.signature(cb).parameters.values())

    def test_register_tolerates_unknown_hook_names(self):
        ctx = MagicMock()
        ctx.register_hook.side_effect = [None, ValueError("unknown hook")] + [None] * 20
        plugin.register(ctx)  # must not raise

    def test_end_to_end_through_the_real_hook_handler(self, tmp_path, monkeypatch):
        """Pipe a translated payload through `overcode hook-handler` for real
        and check the hook-state file the detector reads."""
        state_dir = tmp_path / "state"
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(state_dir))
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "hm")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setenv("OVERCODE_HOOK_COMMAND", f"{sys.executable} -m overcode.cli")
        plugin.handle("on_session_start", kw(model="gpt-5-mini"))
        plugin.handle("pre_approval_request", kw(command="rm -rf ./junkdir_a", description="recursive delete", surface="cli"))
        state = json.loads((state_dir / "agents" / "hook_state_hm.json").read_text())
        assert state["event"] == "PermissionRequest"
        assert state["agent_session_id"] == SID
        assert state["tool_name"] == "Bash"
