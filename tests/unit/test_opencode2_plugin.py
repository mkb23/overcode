"""The bundled opencode2 TUI telemetry plugin, driven through node.

The plugin runs inside opencode2's Bun TUI process; these tests shell out to
``node`` (skipped when absent) via ``tests/js/opencode2_plugin_harness.mjs``,
feeding it a fake ``api.client.event.subscribe`` that replays captured v2 bus
traffic, then assert on the hook-state files — the same files
``HookStatusDetector`` reads.

Event payloads below are the REAL v2 shapes, captured live from
``opencode2 v0.0.0-dev-19272`` (the SSE ``/event`` stream delivers
``{id, created, type, location, data}`` envelopes; the turn vocabulary is
``session.inbox.enqueued`` / ``session.tool.*`` / ``session.execution.*``,
with ``permission.asked/replied`` carrying a flat ``action`` field). The
reducer also accepts the v1 ``{type, properties}`` nesting and flat payloads
defensively — one test pins each shape.
"""

import json, os, shutil, subprocess
from pathlib import Path
import pytest

from overcode.backends.opencode2_plugin_install import (
    PLUGIN_DIR_NAME_V2,
    PLUGIN_FILES_V2,
    PLUGIN_MARKER_V2,
    bundled_plugin_dir_v2,
    ensure_plugin_installed,
    plugin_installed,
)

HARNESS = Path(__file__).parent.parent / "js" / "opencode2_plugin_harness.mjs"
node = pytest.mark.skipif(shutil.which("node") is None, reason="node required")

SESSION_ID = "ses_f598f1212ffelIQor6hak3Q78d"
CHILD_ID = "ses_child00000000task000000000000"


def bundled_tui_path():
    return bundled_plugin_dir_v2() / "tui.js"


def run_harness(tmp_path, events, no_env=False, flaky=False, dispose_while_subscribing=False):
    env_file = tmp_path / "events.json"
    env_file.write_text(json.dumps(events))
    cmd = ["node", str(HARNESS), str(bundled_tui_path()), str(env_file)]
    if no_env:
        cmd.append("--no-overcode-env")
    if flaky:
        cmd.append("--flaky")
    if dispose_while_subscribing:
        cmd.append("--dispose-while-subscribing")
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def live_turn_events(session_id=SESSION_ID, tool=None):
    """A captured "say ok" turn, in the live v2 SSE envelope shape."""
    events = [
        {"type": "session.created", "data": {"sessionID": session_id, "agent": "build"}},
        {
            "type": "session.inbox.enqueued",
            "data": {
                "sessionID": session_id,
                "inboxID": "msg_u1",
                "item": {"type": "user", "payload": {"text": "say ok"}},
            },
        },
        {"type": "session.execution.started", "data": {"sessionID": session_id}},
    ]
    if tool:
        events.append(tool)
    events.append({"type": "session.execution.succeeded", "data": {"sessionID": session_id}})
    return events


@node
class TestV2Plugin:
    def test_loads_as_tui_plugin_and_noops_without_env(self, tmp_path):
        result = run_harness(tmp_path, [], no_env=True)
        assert result["registered"] is False  # no OVERCODE_* env -> no bus subscriptions

    def test_session_lifecycle(self, tmp_path):
        result = run_harness(tmp_path, live_turn_events())
        assert result["registered"] is True
        assert result["state"]["event"] == "Stop"
        assert result["state"]["agent_session_id"] == SESSION_ID
        assert [e["event"] for e in result["events"]] == ["UserPromptSubmit", "Stop"]

    def test_tool_roundtrip(self, tmp_path):
        # Real captured shapes: the tool NAME arrives in
        # session.tool.input.started; session.tool.called carries only
        # {id, input}; success/failed carry no name at all.
        events = live_turn_events(
            tool={
                "type": "session.tool.input.started",
                "data": {"sessionID": SESSION_ID, "id": "call_1", "name": "shell"},
            }
        )
        events.insert(
            -1,
            {
                "type": "session.tool.called",
                "data": {"sessionID": SESSION_ID, "id": "call_1", "input": {"command": "echo hi"}},
            },
        )
        events.insert(
            -1,
            {
                "type": "session.tool.success",
                "data": {"sessionID": SESSION_ID, "id": "call_1"},
            },
        )
        result = run_harness(tmp_path, events)
        assert [e["event"] for e in result["events"]] == [
            "UserPromptSubmit",
            "PreToolUse",
            "PostToolUse",
            "Stop",
        ]
        pre = result["events"][1]
        assert pre["tool_name"] == "Bash"  # "shell" (from input.started) → Bash
        assert pre["tool_input"] == {"command": "echo hi"}
        assert result["events"][2]["tool_name"] == "Bash"
        assert result["state"]["event"] == "Stop"

    def test_tool_failure_publishes_post_tool_use_failure(self, tmp_path):
        # session.tool.failed must NOT publish PostToolUse (the success
        # event): a failed call is a PostToolUseFailure, the distinct
        # event name the status detector reads for failed tools. One
        # turn, two tool calls — one succeeds, one fails — so both names
        # appear, in the right order per call.
        events = live_turn_events(
            tool={
                "type": "session.tool.input.started",
                "data": {"sessionID": SESSION_ID, "id": "call_1", "name": "shell"},
            }
        )
        events.insert(
            -1,
            {
                "type": "session.tool.called",
                "data": {"sessionID": SESSION_ID, "id": "call_1", "input": {"command": "echo hi"}},
            },
        )
        events.insert(
            -1,
            {
                "type": "session.tool.input.started",
                "data": {"sessionID": SESSION_ID, "id": "call_2", "name": "read"},
            },
        )
        events.insert(
            -1,
            {
                "type": "session.tool.called",
                "data": {"sessionID": SESSION_ID, "id": "call_2", "input": {"file_path": "/absent"}},
            },
        )
        events.insert(
            -1,
            {
                "type": "session.tool.success",
                "data": {"sessionID": SESSION_ID, "id": "call_1"},
            },
        )
        events.insert(
            -1,
            {
                "type": "session.tool.failed",
                "data": {"sessionID": SESSION_ID, "id": "call_2"},
            },
        )
        result = run_harness(tmp_path, events)
        assert [e["event"] for e in result["events"]] == [
            "UserPromptSubmit",
            "PreToolUse",
            "PreToolUse",
            "PostToolUse",        # call_1 succeeded
            "PostToolUseFailure",  # call_2 failed — not PostToolUse
            "Stop",
        ]
        success = result["events"][3]
        failure = result["events"][4]
        assert success["tool_name"] == "Bash"
        assert success["tool_input"] == {"command": "echo hi"}
        assert failure["tool_name"] == "Read"
        assert failure["tool_input"] == {"file_path": "/absent"}
        assert result["state"]["event"] == "Stop"

    def test_permission_roundtrip(self, tmp_path):
        events = live_turn_events(tool=None)[:-1]
        events += [
            # Live shape (captured): flat `action`, command in `resources`,
            # tool call id in `source.id`.
            {
                "type": "permission.asked",
                "data": {
                    "id": "per_1",
                    "sessionID": SESSION_ID,
                    "action": "shell",
                    "resources": ["echo hi"],
                    "source": {"type": "tool", "messageID": "msg_a1", "id": "call_1"},
                },
            },
            {
                "type": "permission.replied",
                "data": {"sessionID": SESSION_ID, "requestID": "per_1", "reply": "once"},
            },
            {"type": "session.execution.succeeded", "data": {"sessionID": SESSION_ID}},
        ]
        result = run_harness(tmp_path, events)
        assert result["events"][0]["event"] == "UserPromptSubmit"
        assert result["events"][1]["event"] == "PermissionRequest"
        assert result["events"][1]["tool_name"] == "Bash"
        assert result["events"][1]["tool_input"] == {"command": "echo hi"}
        assert result["events"][2]["event"] == "PreToolUse"  # allow -> running
        assert result["state"]["event"] == "Stop"

    def test_interrupted_turn_maps_to_stop(self, tmp_path):
        events = live_turn_events()[:-1]
        events.append({"type": "session.execution.interrupted", "data": {"sessionID": SESSION_ID}})
        result = run_harness(tmp_path, events)
        assert result["state"]["event"] == "Stop"

    def test_child_task_session_ignored(self, tmp_path):
        events = [
            {"type": "session.created", "data": {"sessionID": SESSION_ID, "agent": "build"}},
            # Child sessions (task tool) carry a parent id — never ours.
            {"type": "session.created", "data": {"sessionID": CHILD_ID, "parentID": "ses_parent"}},
            {
                "type": "session.execution.succeeded",
                "data": {"sessionID": CHILD_ID},
            },  # must not Stop the parent
        ]
        result = run_harness(tmp_path, events)
        # No owned session ever idled, so nothing was published at all.
        assert result["state"] is None or result["state"]["event"] != "Stop"

    def test_child_subagent_turn_fully_ignored_live_shape(self, tmp_path):
        # The REAL captured child-session traffic (live, v0.0.0-dev-19272,
        # Sep 15 2026, prompt "spawn a subagent that answers: done"): the
        # spawner is the `subagent` tool; the child session's
        # session.created carries `parentID` FLAT in the payload
        # ({sessionID, parentID, slug, ...}); the child then emits its own
        # inbox.enqueued / execution.started / execution.succeeded. None
        # of the child's turn may publish for the parent — in particular
        # the child's inbox.enqueued must not ride owns()'s adopt path
        # into a mid-turn UserPromptSubmit, and the child's
        # session.execution.succeeded must not Stop the parent while its
        # subagent tool call is still open.
        child_call = "chatcmpl-tool-a5b70ac4319d7835"
        events = [
            {"type": "session.created", "data": {"sessionID": SESSION_ID, "agent": "build"}},
            {
                "type": "session.inbox.enqueued",
                "data": {
                    "sessionID": SESSION_ID,
                    "inboxID": "msg_u1",
                    "item": {"type": "user", "payload": {"text": "spawn a subagent"}},
                },
            },
            {
                "type": "session.tool.input.started",
                "data": {
                    "sessionID": SESSION_ID,
                    "assistantMessageID": "msg_a1",
                    "id": child_call,
                    "name": "subagent",
                },
            },
            {
                "type": "session.tool.called",
                "data": {
                    "sessionID": SESSION_ID,
                    "id": child_call,
                    "executed": True,
                    "input": {"agent": "general", "prompt": "answer done"},
                },
            },
            # Child session.created — the real flat-parentID shape.
            {
                "type": "session.created",
                "data": {
                    "sessionID": CHILD_ID,
                    "parentID": SESSION_ID,
                    "slug": "shiny-tiger",
                    "version": "0.0.0-dev-19272",
                },
            },
            # The child's own turn — none of this may publish.
            {
                "type": "session.inbox.enqueued",
                "data": {
                    "sessionID": CHILD_ID,
                    "inboxID": "msg_c1",
                    "item": {"type": "user", "payload": {"text": "answer done"}},
                },
            },
            {"type": "session.execution.started", "data": {"sessionID": CHILD_ID}},
            {"type": "session.execution.succeeded", "data": {"sessionID": CHILD_ID}},
            # The parent's subagent call completes and its turn ends.
            {
                "type": "session.tool.success",
                "data": {
                    "sessionID": SESSION_ID,
                    "assistantMessageID": "msg_a1",
                    "id": child_call,
                    "executed": True,
                },
            },
            {"type": "session.execution.succeeded", "data": {"sessionID": SESSION_ID}},
        ]
        result = run_harness(tmp_path, events)
        assert [e["event"] for e in result["events"]] == [
            "UserPromptSubmit",
            "PreToolUse",
            "PostToolUse",
            "Stop",
        ]
        pre = result["events"][1]
        assert pre["tool_name"] == "Task"  # "subagent" canonicalizes to Claude's Task
        assert result["state"]["event"] == "Stop"
        # The child session id was never adopted into the parent's ids.
        assert result["state"]["agent_session_ids"] == [SESSION_ID]
        assert result["state"]["agent_session_id"] == SESSION_ID

    def test_backoff_doubles_on_failure_and_resets_on_first_event(self, tmp_path):
        # Pin the reconnect policy in tui.js: `subscribe()` resolves to a
        # LAZY async generator, so a dead connection only fails inside
        # `for await`. The backoff must therefore reset ONLY once a
        # connection has actually delivered an event — resetting when
        # subscribe() resolves would reconnect a dying stream at the 250ms
        # floor forever (4x/s) instead of backing off 250ms -> 5s.
        #
        # Stream schedule (harness --flaky): two dead connections, one
        # healthy connection that delivers NO events, then the real turn.
        #
        # The policy is asserted on the harness's recorded sleep_delays
        # (every setTimeout delay issued in the process), NOT on
        # wall-clock subscribe() gaps: under load setTimeout sleeps only
        # ever stretch, so comparative gap assertions (the reset sleep
        # must be shorter than the previous backoff sleep) flake — the
        # 250ms reset sleep can stretch past the 1000ms sleep's actual
        # duration. By value the plugin's backoff (250/500/1000) is
        # distinguishable from the ambient harness timers (2000/8000):
        # filtering to the backoff values, the sleeps must read
        # [250, 500, 1000, 250] — doubling per failure, treating an
        # event-less connection as unhealthy, and resetting to the floor
        # only after the healthy stream's first consumed event.
        result = run_harness(tmp_path, ["throw", "throw", [], live_turn_events()], flaky=True)
        times = result["subscribe_times"]
        delays = result["sleep_delays"]
        assert len(times) >= 5  # reconnected through the whole schedule
        backoff = [d for d in delays if d in (250, 500, 1000)]
        assert backoff[:4] == [250, 500, 1000, 250], delays
        # Lenient wall-clock sanity only — no comparative gap assertions.
        assert times[-1] < 60_000, times
        # And the reconnected stream's turn published correctly.
        assert [e["event"] for e in result["events"]] == ["UserPromptSubmit", "Stop"]
        assert result["state"]["event"] == "Stop"

    def test_dispose_while_subscribing_closes_late_stream(self, tmp_path):
        # Teardown can race the FIRST subscription: dispose() runs while
        # `await subscribe()` is still pending, so the disposer cannot
        # return a generator it does not have yet. The subscription then
        # resolves to a PARKED connection (a live SSE stream with no
        # traffic — `next()` never resolves). tui.js must re-check
        # `closed` once subscribe resolves and call `.return()` on the
        # late stream itself — the pre-fix pump parked in `for await`
        # on it forever and the stream was never closed.
        result = run_harness(
            tmp_path, live_turn_events(), dispose_while_subscribing=True
        )
        assert result["registered"] is True
        assert result["late_stream_returned"] is True
        # Nothing was ever published from the torn-down session.
        assert result["events"] == []
        assert result["state"] is None

    def test_execution_failed_publishes_stop_failure_with_reason(self, tmp_path):
        # A failed turn surfaces as StopFailure carrying the event's
        # error payload as a bounded `error` field — same optional-field
        # style as tool_name/tool_input, present in both the state and
        # the events files.
        events = [
            {"type": "session.created", "data": {"sessionID": SESSION_ID, "agent": "build"}},
            {
                "type": "session.execution.failed",
                "data": {"sessionID": SESSION_ID, "error": {"message": "boom"}},
            },
        ]
        result = run_harness(tmp_path, events)
        assert [e["event"] for e in result["events"]] == ["StopFailure"]
        assert result["events"][0]["error"] == "boom"
        assert result["state"]["event"] == "StopFailure"
        assert result["state"]["error"] == "boom"

    def test_execution_failed_without_error_publishes_no_error_field(self, tmp_path):
        events = [
            {"type": "session.created", "data": {"sessionID": SESSION_ID, "agent": "build"}},
            {"type": "session.execution.failed", "data": {"sessionID": SESSION_ID}},
        ]
        result = run_harness(tmp_path, events)
        assert [e["event"] for e in result["events"]] == ["StopFailure"]
        assert "error" not in result["events"][0]
        assert result["state"]["event"] == "StopFailure"
        assert "error" not in result["state"]

    def test_foreign_tool_input_started_is_owns_gated(self, tmp_path):
        # Two sessions sharing one bus (the pre-standalone cross-talk
        # shape): a foreign session's session.tool.input.started must not
        # populate the callID->name map — only owned sessions' tool names
        # are remembered.
        events = [
            {"type": "session.created", "data": {"sessionID": SESSION_ID, "agent": "build"}},
            {
                "type": "session.tool.input.started",
                "data": {"sessionID": "ses_foreign", "id": "call_1", "name": "websearch"},
            },
            # Own session's tool.called carries no name — with the foreign
            # name gated out, the map has no entry and no tool_name leaks.
            {
                "type": "session.tool.called",
                "data": {"sessionID": SESSION_ID, "id": "call_1", "input": {"command": "echo hi"}},
            },
            {"type": "session.tool.success", "data": {"sessionID": SESSION_ID, "id": "call_1"}},
            {"type": "session.execution.succeeded", "data": {"sessionID": SESSION_ID}},
        ]
        result = run_harness(tmp_path, events)
        pre = [e for e in result["events"] if e["event"] == "PreToolUse"]
        assert len(pre) == 1
        assert "tool_name" not in pre[0]  # foreign "WebSearch" did not leak in
        assert pre[0]["tool_input"] == {"command": "echo hi"}
        assert result["state"]["event"] == "Stop"

    def test_accepts_v1_properties_nesting(self, tmp_path):
        events = [
            {
                "type": "session.created",
                "properties": {"sessionID": SESSION_ID, "info": {"id": SESSION_ID}},
            },
            {
                "type": "message.updated",
                "properties": {"info": {"role": "user", "id": "msg_u1", "sessionID": SESSION_ID}},
            },
            {"type": "session.idle", "properties": {"sessionID": SESSION_ID}},
        ]
        result = run_harness(tmp_path, events)
        assert [e["event"] for e in result["events"]] == ["UserPromptSubmit", "Stop"]
        assert result["state"]["event"] == "Stop"

    def test_accepts_flat_payloads_and_permission_v2_aliases(self, tmp_path):
        events = [
            {"type": "session.created", "sessionID": SESSION_ID},  # flat, no data/properties
            {
                "type": "permission.v2.asked",
                "sessionID": SESSION_ID,
                "id": "per_1",
                "permission": {"action": "shell"},
                "metadata": {"command": "echo hi"},
            },
            {
                "type": "permission.v2.replied",
                "sessionID": SESSION_ID,
                "requestID": "per_1",
                "reply": "once",
            },
            {"type": "session.idle", "sessionID": SESSION_ID},
        ]
        result = run_harness(tmp_path, events)
        assert [e["event"] for e in result["events"]] == [
            "PermissionRequest",
            "PreToolUse",
            "Stop",
        ]
        assert result["events"][0]["tool_name"] == "Bash"
        assert result["state"]["event"] == "Stop"


class TestPluginInstallationV2:
    """The v2 installer stages the plugin as a subdirectory of plugins/.

    Verified live: opencode2's server loads ``plugins/<name>/index.js`` and
    only registers ``features.tui`` when the directory also resolves a
    ``tui`` entrypoint (``tui.js``) — the TUI then loads the TUI entry and
    invokes its ``setup(api)``. Loose ``.js`` files never load TUI-side.
    Same marker semantics as the v1 installer: a target file without the
    marker is the user's own and is never touched; an overcode copy is
    refreshed in place when the bundled content moved on.
    """

    def test_install_creates_the_plugin_directory(self, tmp_path):
        result = ensure_plugin_installed(str(tmp_path))
        assert result == tmp_path / ".opencode" / "plugins" / PLUGIN_DIR_NAME_V2
        for name in PLUGIN_FILES_V2:
            installed = result / name
            assert installed.is_file()
            assert PLUGIN_MARKER_V2 in installed.read_text()

    def test_installed_copies_match_the_bundled_files(self, tmp_path):
        installed_dir = ensure_plugin_installed(str(tmp_path))
        for name in PLUGIN_FILES_V2:
            assert (installed_dir / name).read_text() == (
                bundled_plugin_dir_v2() / name
            ).read_text()

    def test_is_idempotent(self, tmp_path):
        first = ensure_plugin_installed(str(tmp_path))
        second = ensure_plugin_installed(str(tmp_path))
        assert first == second
        assert plugin_installed(str(tmp_path))

    def test_stale_overcode_copies_are_refreshed(self, tmp_path):
        target = tmp_path / ".opencode" / "plugins" / PLUGIN_DIR_NAME_V2
        target.mkdir(parents=True)
        for name in PLUGIN_FILES_V2:
            (target / name).write_text(f"// old build\n// {PLUGIN_MARKER_V2}\n")
        ensure_plugin_installed(str(tmp_path))
        for name in PLUGIN_FILES_V2:
            assert (target / name).read_text() == (bundled_plugin_dir_v2() / name).read_text()

    def test_user_owned_file_is_never_clobbered(self, tmp_path):
        target = tmp_path / ".opencode" / "plugins" / PLUGIN_DIR_NAME_V2
        target.mkdir(parents=True)
        mine = "export default { id: 'mine', setup: async () => {} }\n"
        (target / "tui.js").write_text(mine)
        assert ensure_plugin_installed(str(tmp_path)) is None
        assert (target / "tui.js").read_text() == mine
        assert not plugin_installed(str(tmp_path))

    def test_user_owned_file_blocks_all_writes(self, tmp_path):
        # Pre-flight: every existing target is checked for the marker
        # BEFORE anything is written, so a user-owned file late in the
        # sequence cannot leave overcode's earlier files half-installed
        # next to it (partial install).
        target = tmp_path / ".opencode" / "plugins" / PLUGIN_DIR_NAME_V2
        target.mkdir(parents=True)
        stale = f"// old overcode copy\n// {PLUGIN_MARKER_V2}\n"
        (target / "index.js").write_text(stale)  # ours, but stale
        (target / "tui.js").write_text("export default {}\n")  # user's, no marker
        assert ensure_plugin_installed(str(tmp_path)) is None
        # Nothing was written: the stale overcode copy was not refreshed
        # and the missing file was not created.
        assert (target / "index.js").read_text() == stale
        assert not (target / "overcode-telemetry-core.mjs").exists()
        assert not plugin_installed(str(tmp_path))

    def test_unreadable_target_aborts_without_writes(self, tmp_path):
        # An unreadable file (e.g. PermissionError) in a WRITABLE directory
        # is not "missing": only FileNotFoundError means absent, so the
        # install must abort without writing anything rather than clobber
        # it as if it didn't exist.
        target = tmp_path / ".opencode" / "plugins" / PLUGIN_DIR_NAME_V2
        target.mkdir(parents=True)
        mine = "export default {}\n"
        blocked = target / "tui.js"
        blocked.write_text(mine)
        blocked.chmod(0o000)
        try:
            if os.access(blocked, os.R_OK):
                pytest.skip("platform cannot produce an unreadable file (e.g. root)")
            assert ensure_plugin_installed(str(tmp_path)) is None
        finally:
            blocked.chmod(0o644)
        assert blocked.read_text() == mine
        # Nothing was written next to the unreadable file.
        assert not (target / "index.js").exists()

    def test_no_start_directory_is_a_no_op(self):
        assert ensure_plugin_installed(None) is None
        assert plugin_installed(None) is False

    def test_unwritable_directory_is_survivable(self, tmp_path):
        blocked = tmp_path / "ro"
        blocked.mkdir(mode=0o500)
        try:
            assert ensure_plugin_installed(str(blocked)) is None
        finally:
            blocked.chmod(0o700)

    def test_leaves_no_temp_files(self, tmp_path):
        ensure_plugin_installed(str(tmp_path))
        assert list((tmp_path / ".opencode" / "plugins").rglob("*.tmp")) == []

    def test_failed_replace_leaves_no_tmp_and_keeps_the_target(self, tmp_path, monkeypatch):
        # A failure between the tmp write and the replace must not
        # strand the staged .tmp next to the target, and the target
        # keeps its previous content untouched.
        target_dir = tmp_path / ".opencode" / "plugins" / PLUGIN_DIR_NAME_V2
        target_dir.mkdir(parents=True)
        stale = f"// old overcode copy\n// {PLUGIN_MARKER_V2}\n"
        (target_dir / "index.js").write_text(stale)

        def _boom(*args, **kwargs):
            raise OSError("replace failed")

        monkeypatch.setattr(os, "replace", _boom)
        assert ensure_plugin_installed(str(tmp_path)) is None
        # The target was never clobbered and no tmp was stranded.
        assert (target_dir / "index.js").read_text() == stale
        assert list(target_dir.rglob("*.tmp")) == []
        # The install aborted before any other file was written.
        assert not (target_dir / "tui.js").exists()

    def test_prepare_launch_installs_the_plugin_directory(self, tmp_path):
        from overcode.backends.base import LaunchSpec
        from overcode.backends.opencode2 import Opencode2Backend

        Opencode2Backend().prepare_launch(LaunchSpec(start_directory=str(tmp_path)))
        target = tmp_path / ".opencode" / "plugins" / PLUGIN_DIR_NAME_V2
        for name in PLUGIN_FILES_V2:
            assert (target / name).is_file()
