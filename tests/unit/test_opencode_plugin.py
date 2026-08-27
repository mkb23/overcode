"""The bundled opencode telemetry plugin, driven through node.

The plugin is JavaScript that runs inside opencode's Bun process, so these
tests shell out to ``node`` (skipped when it isn't installed) via
``tests/js/opencode_plugin_harness.mjs`` and then assert on the hook-state
files it produced — the same files ``HookStatusDetector`` reads.

Event payloads below are trimmed copies of real opencode v1.18.19 bus traffic
captured while driving a live session (see the header comment on
``overcode-telemetry.js``).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from overcode.backends.opencode import PLUGIN_MARKER, bundled_plugin_path

HARNESS = Path(__file__).parent.parent / "js" / "opencode_plugin_harness.mjs"

SESSION_ID = "ses_fdf96e506ffeALaW4cDa7B2064"
CALL_ID = "call_UnPDplyUFqQrW3Sjsf0RWkV4"
USER_MSG_ID = "msg_020691b03001yACWeOJ7FKwQCe"

node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is required to run the plugin"
)


@pytest.fixture(scope="module")
def plugin_module(tmp_path_factory):
    """The bundled plugin, copied out as .mjs so node loads it as ESM.

    The repo has no package.json, so a bare ``.js`` would be parsed as
    CommonJS and its ``export`` statements would fail.
    """
    target = tmp_path_factory.mktemp("plugin") / "overcode-telemetry.mjs"
    target.write_text(bundled_plugin_path().read_text(encoding="utf-8"))
    return target


def run_plugin(plugin_module, env, actions):
    """Replay ``actions`` through the plugin; returns the harness result dict."""
    job = {"plugin": str(plugin_module), "env": env, "actions": actions}
    result = subprocess.run(
        ["node", str(HARNESS)],
        input=json.dumps(job),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.stdout, f"harness produced no output; stderr={result.stderr}"
    payload = json.loads(result.stdout)
    assert payload.get("ok"), payload.get("error")
    return payload


@pytest.fixture
def env(tmp_path):
    return {
        "OVERCODE_SESSION_NAME": "oc-agent",
        "OVERCODE_TMUX_SESSION": "agents",
        "OVERCODE_STATE_DIR": str(tmp_path / "state"),
        "HOME": str(tmp_path / "home"),
    }


def state_file(env):
    return (
        Path(env["OVERCODE_STATE_DIR"])
        / env["OVERCODE_TMUX_SESSION"]
        / f"hook_state_{env['OVERCODE_SESSION_NAME']}.json"
    )


def event_log(env):
    return (
        Path(env["OVERCODE_STATE_DIR"])
        / env["OVERCODE_TMUX_SESSION"]
        / f"hook_events_{env['OVERCODE_SESSION_NAME']}.jsonl"
    )


def read_state(env):
    return json.loads(state_file(env).read_text())


def read_events(env):
    return [json.loads(line) for line in event_log(env).read_text().splitlines() if line]


def bus(type_, **properties):
    return {"kind": "bus", "event": {"type": type_, "properties": properties}}


SESSION_CREATED = bus(
    "session.created",
    sessionID=SESSION_ID,
    info={"id": SESSION_ID, "slug": "shiny-island", "directory": "/proj"},
)

USER_PROMPT = {
    "kind": "chat",
    "input": {"sessionID": SESSION_ID, "agent": "build"},
    "output": {"message": {"id": USER_MSG_ID, "role": "user", "sessionID": SESSION_ID}},
}

TOOL_BEFORE = {
    "kind": "before",
    "input": {"tool": "bash", "sessionID": SESSION_ID, "callID": CALL_ID},
    "output": {"args": {"command": "echo hello-from-overcode"}},
}

TOOL_AFTER = {
    "kind": "after",
    "input": {
        "tool": "bash",
        "sessionID": SESSION_ID,
        "callID": CALL_ID,
        "args": {"command": "echo hello-from-overcode"},
    },
    "output": {"output": "hello-from-overcode\n"},
}

PERMISSION_ASKED = bus(
    "permission.asked",
    id="per_020692cee001pTk27EHWAY3ky8",
    sessionID=SESSION_ID,
    permission="bash",
    patterns=["echo hello-from-overcode"],
    metadata={"command": "echo hello-from-overcode"},
    tool={"messageID": "msg_020691b0c001Q5G8ET7AKETaFw", "callID": CALL_ID},
)


class TestBundledFile:
    def test_ships_with_the_package(self):
        assert bundled_plugin_path().is_file()

    def test_carries_the_ownership_marker(self):
        # ensure_plugin_installed refuses to overwrite a file without it.
        assert PLUGIN_MARKER in bundled_plugin_path().read_text()


@node
class TestNoOpGuard:
    def test_registers_nothing_without_overcode_env(self, plugin_module, tmp_path):
        result = run_plugin(
            plugin_module,
            {"HOME": str(tmp_path)},
            [SESSION_CREATED, USER_PROMPT],
        )
        assert result["hooks"] == []

    def test_registers_nothing_without_tmux_session(self, plugin_module, tmp_path):
        result = run_plugin(
            plugin_module,
            {"OVERCODE_SESSION_NAME": "oc", "HOME": str(tmp_path)},
            [],
        )
        assert result["hooks"] == []

    def test_registers_the_expected_hooks(self, plugin_module, env):
        result = run_plugin(plugin_module, env, [])
        assert result["hooks"] == [
            "chat.message",
            "event",
            "tool.execute.after",
            "tool.execute.before",
        ]


@node
class TestEventMapping:
    def test_user_prompt_writes_running(self, plugin_module, env):
        run_plugin(plugin_module, env, [SESSION_CREATED, USER_PROMPT])
        assert read_state(env)["event"] == "UserPromptSubmit"

    def test_tool_before_carries_name_and_input(self, plugin_module, env):
        run_plugin(plugin_module, env, [SESSION_CREATED, USER_PROMPT, TOOL_BEFORE])
        state = read_state(env)
        assert state["event"] == "PreToolUse"
        # opencode's lowercase `bash` is normalised to Claude's tool taxonomy
        # so the detector's Bash-activity and sleep parsing keep working.
        assert state["tool_name"] == "Bash"
        assert state["tool_input"] == {"command": "echo hello-from-overcode"}
        assert state["tool_use_id"] == CALL_ID
        assert state["foreground"] == {"kind": "tool", "tool": "Bash"}

    def test_permission_asked_writes_permission_request(self, plugin_module, env):
        run_plugin(
            plugin_module,
            env,
            [SESSION_CREATED, USER_PROMPT, TOOL_BEFORE, PERMISSION_ASKED],
        )
        state = read_state(env)
        assert state["event"] == "PermissionRequest"
        assert state["tool_name"] == "Bash"

    def test_approval_clears_back_to_running(self, plugin_module, env):
        run_plugin(
            plugin_module,
            env,
            [
                SESSION_CREATED,
                USER_PROMPT,
                TOOL_BEFORE,
                PERMISSION_ASKED,
                bus(
                    "permission.replied",
                    sessionID=SESSION_ID,
                    requestID="per_020692cee001pTk27EHWAY3ky8",
                    reply="once",
                ),
            ],
        )
        state = read_state(env)
        assert state["event"] == "PreToolUse"
        # permission.replied carries only requestID, so the tool it unblocked
        # has to be remembered from permission.asked or the badge loses its label.
        assert state["tool_name"] == "Bash"
        assert state["tool_use_id"] == CALL_ID

    def test_rejection_also_clears_back_to_running(self, plugin_module, env):
        run_plugin(
            plugin_module,
            env,
            [
                SESSION_CREATED,
                USER_PROMPT,
                TOOL_BEFORE,
                PERMISSION_ASKED,
                bus("permission.replied", sessionID=SESSION_ID, reply="reject"),
            ],
        )
        # A rejected call is still "the agent is working" — session.idle is
        # what settles it, exactly as observed live.
        assert read_state(env)["event"] == "PostToolUse"

    def test_tool_after_then_idle_settles_to_stop(self, plugin_module, env):
        run_plugin(
            plugin_module,
            env,
            [
                SESSION_CREATED,
                USER_PROMPT,
                TOOL_BEFORE,
                TOOL_AFTER,
                bus("session.idle", sessionID=SESSION_ID),
            ],
        )
        state = read_state(env)
        assert state["event"] == "Stop"
        # Foreground detail must not outlive the tool call it described.
        assert "foreground" not in state

    def test_session_error_maps_to_stop_failure(self, plugin_module, env):
        run_plugin(
            plugin_module,
            env,
            [SESSION_CREATED, USER_PROMPT, bus("session.error", sessionID=SESSION_ID)],
        )
        assert read_state(env)["event"] == "StopFailure"

    def test_session_deleted_maps_to_session_end(self, plugin_module, env):
        run_plugin(
            plugin_module,
            env,
            [SESSION_CREATED, USER_PROMPT, bus("session.deleted", sessionID=SESSION_ID)],
        )
        assert read_state(env)["event"] == "SessionEnd"

    def test_high_volume_events_are_ignored(self, plugin_module, env):
        run_plugin(
            plugin_module,
            env,
            [
                SESSION_CREATED,
                USER_PROMPT,
                bus("message.part.delta", sessionID=SESSION_ID, delta="hi"),
                bus("message.part.updated", sessionID=SESSION_ID, part={}),
                bus("session.status", sessionID=SESSION_ID, status={"type": "busy"}),
                bus("plugin.added", id="openai"),
            ],
        )
        assert [e["event"] for e in read_events(env)] == ["UserPromptSubmit"]


@node
class TestSessionScoping:
    def test_records_the_opencode_session_id(self, plugin_module, env):
        run_plugin(plugin_module, env, [SESSION_CREATED, USER_PROMPT])
        state = read_state(env)
        assert state["agent_session_id"] == SESSION_ID
        assert state["agent_session_ids"] == [SESSION_ID]

    def test_child_session_idle_does_not_stop_the_parent(self, plugin_module, env):
        run_plugin(
            plugin_module,
            env,
            [
                SESSION_CREATED,
                USER_PROMPT,
                TOOL_BEFORE,
                # A `task` sub-agent finishing must not mark the parent idle.
                bus("session.idle", sessionID="ses_childXXXX"),
            ],
        )
        assert read_state(env)["event"] == "PreToolUse"

    def test_child_session_creation_is_not_adopted(self, plugin_module, env):
        result = run_plugin(
            plugin_module,
            env,
            [
                SESSION_CREATED,
                bus(
                    "session.created",
                    sessionID="ses_childXXXX",
                    info={"id": "ses_childXXXX", "parentID": SESSION_ID},
                ),
            ],
        )
        assert result["rootSessionIds"] == [SESSION_ID]

    def test_resumed_session_adopts_the_first_id_it_sees(self, plugin_module, env):
        # No session.created fires on `opencode --session <id>`, so the first
        # event has to be trusted or a resumed agent reports nothing at all.
        result = run_plugin(plugin_module, env, [USER_PROMPT])
        assert result["rootSessionIds"] == [SESSION_ID]
        assert read_state(env)["event"] == "UserPromptSubmit"

    def test_repeated_user_message_does_not_resurrect_running(self, plugin_module, env):
        # opencode re-emits message.updated for the same user message after
        # the turn ends; taking it at face value would pin the agent green.
        run_plugin(
            plugin_module,
            env,
            [
                SESSION_CREATED,
                USER_PROMPT,
                bus("session.idle", sessionID=SESSION_ID),
                bus(
                    "message.updated",
                    sessionID=SESSION_ID,
                    info={"id": USER_MSG_ID, "role": "user"},
                ),
            ],
        )
        assert read_state(env)["event"] == "Stop"

    def test_a_new_user_message_does_start_a_turn(self, plugin_module, env):
        run_plugin(
            plugin_module,
            env,
            [
                SESSION_CREATED,
                USER_PROMPT,
                bus("session.idle", sessionID=SESSION_ID),
                bus(
                    "message.updated",
                    sessionID=SESSION_ID,
                    info={"id": "msg_second", "role": "user"},
                ),
            ],
        )
        assert read_state(env)["event"] == "UserPromptSubmit"


@node
class TestFileFormat:
    def test_event_log_matches_hook_handler_shape(self, plugin_module, env):
        run_plugin(plugin_module, env, [SESSION_CREATED, USER_PROMPT, TOOL_BEFORE])
        events = read_events(env)
        assert [e["event"] for e in events] == ["UserPromptSubmit", "PreToolUse"]
        for entry in events:
            assert isinstance(entry["timestamp"], float)
        assert events[-1]["tool_name"] == "Bash"
        assert events[-1]["tool_input"] == {"command": "echo hello-from-overcode"}

    def test_timestamps_are_epoch_seconds(self, plugin_module, env):
        import time

        before = time.time()
        run_plugin(plugin_module, env, [SESSION_CREATED, USER_PROMPT])
        after = time.time()
        assert before <= read_state(env)["timestamp"] <= after

    def test_state_write_leaves_no_temp_files(self, plugin_module, env):
        run_plugin(plugin_module, env, [SESSION_CREATED, USER_PROMPT, TOOL_BEFORE])
        leftovers = list(state_file(env).parent.glob("*.tmp"))
        assert leftovers == []

    def test_skill_uses_accumulate(self, plugin_module, env):
        run_plugin(
            plugin_module,
            env,
            [
                SESSION_CREATED,
                USER_PROMPT,
                {
                    "kind": "before",
                    "input": {"tool": "skill", "sessionID": SESSION_ID, "callID": "c1"},
                    "output": {"args": {"skill": "overcode"}},
                },
                {
                    "kind": "before",
                    "input": {"tool": "skill", "sessionID": SESSION_ID, "callID": "c2"},
                    "output": {"args": {"skill": "dataviz"}},
                },
            ],
        )
        assert read_state(env)["loaded_skills"] == ["overcode", "dataviz"]

    def test_blocked_on_classification_matches_hook_handler(self, plugin_module, env):
        run_plugin(
            plugin_module,
            env,
            [
                SESSION_CREATED,
                USER_PROMPT,
                {
                    "kind": "before",
                    "input": {"tool": "bash", "sessionID": SESSION_ID, "callID": "c1"},
                    "output": {"args": {"command": "sleep 30"}},
                },
            ],
        )
        assert read_state(env)["foreground"]["blocked_on"] == "sleep"


@node
class TestReadByDetector:
    """The written files must satisfy the real consumer, not just look right."""

    def test_hook_status_detector_reports_the_transitions(
        self, plugin_module, env, monkeypatch
    ):
        from overcode.hook_status_detector import HookStatusDetector
        from overcode.status_constants import (
            STATUS_RUNNING,
            STATUS_WAITING_APPROVAL,
            STATUS_WAITING_USER,
        )

        class FakeTmux:
            def capture_pane(self, session, window, lines=0):
                return "┃  \n╹▀▀▀\ntab agents  ctrl+p commands"

        class FakeSession:
            id = "sid"
            name = env["OVERCODE_SESSION_NAME"]
            tmux_window = "w"
            parent_session_id = None

        monkeypatch.setenv("OVERCODE_STATE_DIR", env["OVERCODE_STATE_DIR"])
        detector = HookStatusDetector(
            env["OVERCODE_TMUX_SESSION"], tmux=FakeTmux()
        )
        session = FakeSession()

        def replay(actions):
            run_plugin(plugin_module, env, actions)
            return detector.detect_status(session)[0]

        prefix = [SESSION_CREATED, USER_PROMPT, TOOL_BEFORE]
        assert replay(prefix) == STATUS_RUNNING
        assert replay(prefix + [PERMISSION_ASKED]) == STATUS_WAITING_APPROVAL
        assert replay(
            prefix
            + [
                PERMISSION_ASKED,
                bus("permission.replied", sessionID=SESSION_ID, reply="once"),
            ]
        ) == STATUS_RUNNING

        # Stop lands last. The sticky-green window (#448) deliberately holds a
        # Stop green for 1.5s after RUNNING-class traffic — the whole replay
        # happens inside that window — so the event log is cleared to stand in
        # for the pause a real agent takes before going idle.
        run_plugin(
            plugin_module,
            env,
            prefix + [TOOL_AFTER, bus("session.idle", sessionID=SESSION_ID)],
        )
        event_log(env).unlink()
        assert detector.detect_status(session)[0] == STATUS_WAITING_USER

    def test_fast_turn_stays_green_across_stop(self, plugin_module, env, monkeypatch):
        """The sticky-green anti-flicker path works off the plugin's log too."""
        from overcode.hook_status_detector import HookStatusDetector
        from overcode.status_constants import STATUS_RUNNING

        class FakeTmux:
            def capture_pane(self, session, window, lines=0):
                return "┃  \n╹▀▀▀"

        class FakeSession:
            id = "sid"
            name = env["OVERCODE_SESSION_NAME"]
            tmux_window = "w"
            parent_session_id = None

        monkeypatch.setenv("OVERCODE_STATE_DIR", env["OVERCODE_STATE_DIR"])
        run_plugin(
            plugin_module,
            env,
            [
                SESSION_CREATED,
                USER_PROMPT,
                TOOL_BEFORE,
                TOOL_AFTER,
                bus("session.idle", sessionID=SESSION_ID),
            ],
        )
        detector = HookStatusDetector(env["OVERCODE_TMUX_SESSION"], tmux=FakeTmux())
        assert detector.detect_status(FakeSession())[0] == STATUS_RUNNING

    def test_permission_request_renders_an_orange_badge(
        self, plugin_module, env, monkeypatch
    ):
        from overcode.hook_status_detector import HookStatusDetector
        from overcode.status_constants import STATUS_COLOR_ORANGE

        class FakeTmux:
            def capture_pane(self, session, window, lines=0):
                return "┃  \n╹▀▀▀"

        class FakeSession:
            id = "sid"
            name = env["OVERCODE_SESSION_NAME"]
            tmux_window = "w"
            parent_session_id = None

        monkeypatch.setenv("OVERCODE_STATE_DIR", env["OVERCODE_STATE_DIR"])
        run_plugin(
            plugin_module,
            env,
            [SESSION_CREATED, USER_PROMPT, TOOL_BEFORE, PERMISSION_ASKED],
        )
        detector = HookStatusDetector(env["OVERCODE_TMUX_SESSION"], tmux=FakeTmux())
        session = FakeSession()
        detector.detect_status(session)
        detail = detector.get_status_detail(session.name)
        assert detail.color == STATUS_COLOR_ORANGE
        assert [b.kind for b in detail.badges] == ["permission"]
        assert detail.badges[0].label == "Bash"
