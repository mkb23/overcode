"""Rename notice and enhanced context reach opencode models (#533).

Claude's UserPromptSubmit hook prints them into the model's context. The
opencode plugins get the same text from ``overcode hook-handler
--prompt-context`` and add it to the prompt: v1 as a synthetic text part in
``chat.message``, opencode2 by extending ``prompt.text`` in its server
``session.hook("prompt")``. Both are driven through node with
``tests/js/opencode_prompt_context_harness.mjs`` against the real hook
handler, so the notice is shown to be consumed once end to end.
"""

import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from overcode import hook_handler
from overcode.backends.opencode2_plugin_install import bundled_plugin_dir_v2

HARNESS = Path(__file__).parent.parent / "js" / "opencode_prompt_context_harness.mjs"
SRC = Path(__file__).parent.parent.parent / "src"
node = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")

AGENT_ID = "agent-0001"
ROOT = "ses_root000000000000000000000"
CHILD = "ses_child00000000000000000000"
NOTICE = "[overcode] This agent was renamed from 'alpha' to 'beta'."


@pytest.fixture
def agent_env(tmp_path):
    """The env an opencode agent's plugin sees, with the real handler as
    its hook command."""
    return {
        "OVERCODE_SESSION_NAME": "alpha",
        "OVERCODE_SESSION_ID": AGENT_ID,
        "OVERCODE_TMUX_SESSION": "agents",
        "OVERCODE_DIR": str(tmp_path / "home" / ".overcode"),
        "OVERCODE_STATE_DIR": str(tmp_path / "state"),
        "OVERCODE_HOOK_COMMAND": f"{sys.executable} -m overcode.cli",
        "HOME": str(tmp_path / "home"),
        "PYTHONPATH": str(SRC),
    }


def agent_dir(env):
    return Path(env["OVERCODE_STATE_DIR"]) / "agents" / "agents" / AGENT_ID


def leave_notice(env):
    path = agent_dir(env) / "rename_notice.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(NOTICE + "\n")
    return path


def daemon_state(env, enhanced):
    path = Path(env["OVERCODE_STATE_DIR"]) / "agents" / "monitor_daemon_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sessions": [
        {"session_id": AGENT_ID, "name": "beta", "enhanced_context_enabled": enhanced},
    ]}))


def run(job):
    result = subprocess.run(
        ["node", str(HARNESS)], input=json.dumps(job),
        capture_output=True, text=True, timeout=60,
    )
    assert result.stdout, f"harness produced no output; stderr={result.stderr}"
    payload = json.loads(result.stdout)
    assert payload.get("ok"), payload.get("error")
    return payload


def created(session_id, parent=None):
    info = {"id": session_id}
    if parent:
        info["parentID"] = parent
    return {"kind": "bus", "event": {"type": "session.created",
                                     "properties": {"sessionID": session_id, "info": info}}}


def chat(session_id, message_id, text):
    return {
        "kind": "chat",
        "input": {"sessionID": session_id, "agent": "build"},
        "output": {
            "message": {"id": message_id, "role": "user", "sessionID": session_id},
            "parts": [{"id": f"prt_{message_id}", "messageID": message_id,
                       "sessionID": session_id, "type": "text", "text": text}],
        },
    }


def run_v1(plugin_module, env, actions):
    return run({"version": 1, "plugin": str(plugin_module), "env": env, "actions": actions})


def run_v2(env, prompts, sessions=None):
    return run({
        "version": 2,
        "plugin": str(bundled_plugin_dir_v2() / "index.js"),
        "env": env,
        "sessions": sessions or {ROOT: {"id": ROOT}, CHILD: {"id": CHILD, "parentID": ROOT}},
        "prompts": prompts,
    })


def prompt(session_id, text):
    return {"sessionID": session_id, "messageID": "msg_1",
            "prompt": {"text": text, "files": [], "agents": []}, "delivery": "steer"}


def added_parts(output):
    return [p for p in output["parts"] if p.get("synthetic")]


@pytest.fixture(scope="module")
def plugin_module(tmp_path_factory):
    from tests.opencode_plugin_files import copy_v1_plugin
    return copy_v1_plugin(tmp_path_factory.mktemp("plugin"))


# ── The handler's --prompt-context mode ─────────────────────────────


class TestPromptContextMode:

    def test_prints_the_notice_once_and_writes_no_hook_state(
            self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "alpha")
        monkeypatch.setenv("OVERCODE_SESSION_ID", AGENT_ID)
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        path = hook_handler.get_rename_notice_path("agents", "alpha")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(NOTICE + "\n")
        monkeypatch.setattr("sys.stdin", io.StringIO(""))

        hook_handler.handle_prompt_context()
        assert NOTICE in capsys.readouterr().out
        assert not path.exists()
        assert not (path.parent / "hook_state.json").exists()

        hook_handler.handle_prompt_context()
        assert NOTICE not in capsys.readouterr().out

    def test_silent_outside_an_agent(self, monkeypatch, capsys):
        monkeypatch.delenv("OVERCODE_SESSION_NAME", raising=False)
        monkeypatch.delenv("OVERCODE_TMUX_SESSION", raising=False)
        hook_handler.handle_prompt_context()
        assert capsys.readouterr().out == ""

    def test_user_prompt_submit_prints_the_same(self, monkeypatch, capsys):
        # One source for both: the hook's UserPromptSubmit output is
        # emit_prompt_context's.
        calls = []
        monkeypatch.setattr(hook_handler, "emit_prompt_context",
                            lambda *a: calls.append(a))
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "alpha")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
            {"hook_event_name": "UserPromptSubmit", "session_id": "c"})))
        hook_handler.handle_hook_event()
        assert calls == [("agents", "alpha")]


# ── opencode v1: a synthetic part in chat.message ───────────────────


@node
class TestV1:

    def test_rename_notice_reaches_the_prompt_once(self, plugin_module, agent_env):
        daemon_state(agent_env, enhanced=False)
        notice = leave_notice(agent_env)
        result = run_v1(plugin_module, agent_env, [
            created(ROOT), chat(ROOT, "msg_1", "first"), chat(ROOT, "msg_2", "second"),
        ])
        first, second = result["outputs"]
        [part] = added_parts(first)
        assert part["text"] == NOTICE
        assert part["type"] == "text"
        assert part["messageID"] == "msg_1" and part["sessionID"] == ROOT
        # opencode's ascending part id, sorting after the user's own part
        assert part["id"].startswith("prt_") and len(part["id"]) == 30
        assert added_parts(second) == []
        assert not notice.exists()

    def test_enhanced_context_on_every_prompt_when_enabled(self, plugin_module, agent_env):
        daemon_state(agent_env, enhanced=True)
        result = run_v1(plugin_module, agent_env, [
            created(ROOT), chat(ROOT, "msg_1", "a"), chat(ROOT, "msg_2", "b"),
        ])
        for output in result["outputs"]:
            [part] = added_parts(output)
            assert part["text"].startswith("Agent: beta |")

    def test_nothing_added_when_there_is_nothing_to_say(self, plugin_module, agent_env):
        daemon_state(agent_env, enhanced=False)
        result = run_v1(plugin_module, agent_env, [created(ROOT), chat(ROOT, "msg_1", "a")])
        assert result["outputs"][0]["parts"][1:] == []

    def test_a_task_child_does_not_take_the_notice(self, plugin_module, agent_env):
        daemon_state(agent_env, enhanced=True)
        notice = leave_notice(agent_env)
        result = run_v1(plugin_module, agent_env, [
            created(ROOT), created(CHILD, parent=ROOT), chat(CHILD, "msg_c", "child work"),
        ])
        assert added_parts(result["outputs"][0]) == []
        assert notice.exists()

    def test_a_failing_hook_command_leaves_the_prompt_alone(self, plugin_module, agent_env):
        leave_notice(agent_env)
        agent_env["OVERCODE_HOOK_COMMAND"] = "/nonexistent/overcode"
        result = run_v1(plugin_module, agent_env, [created(ROOT), chat(ROOT, "msg_1", "a")])
        assert len(result["outputs"][0]["parts"]) == 1

    def test_first_three_prompts_are_recorded(self, plugin_module, agent_env):
        daemon_state(agent_env, enhanced=False)
        long = "x" * 3000
        run_v1(plugin_module, agent_env, [created(ROOT)] + [
            chat(ROOT, f"msg_{i}", text)
            for i, text in enumerate(["fix the login bug", long, "third", "fourth"])
        ])
        lines = (agent_dir(agent_env) / "prompts.jsonl").read_text().splitlines()
        rows = [json.loads(line) for line in lines]
        assert [r["prompt"] for r in rows] == ["fix the login bug", "x" * 1024, "third"]
        assert all(set(r) == {"t", "prompt"} and r["t"].endswith("Z") for r in rows)


# ── opencode2: the server's prompt hook ─────────────────────────────


@node
class TestV2:

    def test_registers_the_prompt_hook_and_disposes_it(self, agent_env):
        result = run_v2(agent_env, [])
        assert result["registered"] == ["prompt"]
        assert result["disposed"]

    def test_registers_nothing_outside_overcode(self, agent_env):
        del agent_env["OVERCODE_SESSION_NAME"]
        assert run_v2(agent_env, [])["registered"] == []

    def test_rename_notice_is_appended_once(self, agent_env):
        daemon_state(agent_env, enhanced=False)
        notice = leave_notice(agent_env)
        result = run_v2(agent_env, [prompt(ROOT, "first"), prompt(ROOT, "second")])
        first, second = result["inputs"]
        assert first["prompt"]["text"] == f"first\n\n{NOTICE}"
        assert second["prompt"]["text"] == "second"
        assert not notice.exists()

    def test_enhanced_context_when_enabled(self, agent_env):
        daemon_state(agent_env, enhanced=True)
        result = run_v2(agent_env, [prompt(ROOT, "go")])
        assert result["inputs"][0]["prompt"]["text"].startswith("go\n\nAgent: beta |")

    def test_a_subagent_child_is_left_alone(self, agent_env):
        daemon_state(agent_env, enhanced=True)
        notice = leave_notice(agent_env)
        result = run_v2(agent_env, [prompt(CHILD, "child work")])
        assert result["inputs"][0]["prompt"]["text"] == "child work"
        assert notice.exists()
        assert not (agent_dir(agent_env) / "prompts.jsonl").exists()

    def test_first_prompts_are_recorded(self, agent_env):
        daemon_state(agent_env, enhanced=False)
        run_v2(agent_env, [prompt(ROOT, "name me")])
        [line] = (agent_dir(agent_env) / "prompts.jsonl").read_text().splitlines()
        assert json.loads(line)["prompt"] == "name me"
