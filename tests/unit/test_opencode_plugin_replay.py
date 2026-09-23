"""Replay real opencode event streams through the bundled plugin (#474).

Every fixture in ``tests/fixtures_opencode_events/`` is verbatim hook
traffic from a live opencode v1.18.29 session (see the README there). It is
fed to the REAL plugin — the hooks object its factory returns, called the
way opencode calls it — by ``tests/js/opencode_plugin_replay.mjs``, and each
publish the plugin makes is compared with what ``tests.opencode_oracle``
says that record must produce.

The oracle is the specification; a mismatch is either a plugin bug or an
oracle rule that needs revisiting, and the failure message names the exact
record. ``test_opencode_event_grammar.py`` runs the same comparison over
generated streams.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from overcode.backends.opencode import bundled_plugin_path
from tests.opencode_oracle import (
    OpencodeOracle,
    diff_publishes,
    expected_status,
)

FIXTURES = Path(__file__).parent.parent / "fixtures_opencode_events"
REPLAY = Path(__file__).parent.parent / "js" / "opencode_plugin_replay.mjs"

node = pytest.mark.skipif(shutil.which("node") is None, reason="node is required to run the plugin")

SCENARIOS = sorted(p.stem for p in FIXTURES.glob("*.jsonl"))


def load_fixture(name: str) -> list:
    lines = (FIXTURES / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
    records = [json.loads(l) for l in lines if l.strip()]
    if records and "_fixture" in records[0]:
        records = records[1:]
    return records


@pytest.fixture(scope="module")
def plugin_module(tmp_path_factory):
    """The bundled plugin, copied out as .mjs so node loads it as ESM."""
    target = tmp_path_factory.mktemp("plugin") / "overcode-telemetry.mjs"
    target.write_text(bundled_plugin_path().read_text(encoding="utf-8"))
    return target


def replay(plugin_module, tmp_path, streams):
    """Run ``streams`` ([{name, records}]) through the plugin; returns results by name."""
    env = {
        "OVERCODE_SESSION_NAME": "oc",
        "OVERCODE_TMUX_SESSION": "agents",
        "OVERCODE_STATE_DIR": str(tmp_path / "state"),
        "HOME": str(tmp_path / "home"),
    }
    job = {"plugin": str(plugin_module), "env": env, "streams": streams}
    result = subprocess.run(
        ["node", str(REPLAY)], input=json.dumps(job),
        capture_output=True, text=True, timeout=120,
    )
    assert result.stdout, f"replay harness produced no output; stderr={result.stderr}"
    payload = json.loads(result.stdout)
    assert payload.get("ok"), payload.get("error")
    return {s["name"]: s for s in payload["streams"]}


def events_of(stream_result) -> list:
    return [p["event"] for per_record in stream_result["publishes"] for p in per_record]


@node
class TestCorpusAgainstOracle:
    """Every fixture: each record's publishes must match the oracle exactly."""

    @pytest.mark.parametrize("scenario", SCENARIOS)
    def test_publishes_match_oracle(self, plugin_module, tmp_path, scenario):
        records = load_fixture(scenario)
        result = replay(plugin_module, tmp_path, [{"name": scenario, "records": records}])[scenario]
        problems = diff_publishes(records, result["publishes"])
        assert not problems, f"{scenario}:\n  " + "\n  ".join(problems[:12])

    @pytest.mark.parametrize("scenario", SCENARIOS)
    def test_final_status_matches_oracle(self, plugin_module, tmp_path, scenario):
        records = load_fixture(scenario)
        result = replay(plugin_module, tmp_path, [{"name": scenario, "records": records}])[scenario]
        want = expected_status(records)
        state = result["state"]
        if want is None:
            assert state is None
        else:
            from tests.opencode_oracle import STATUS_OF
            assert STATUS_OF[state["event"]] == want, (scenario, state)


@node
class TestScenarioInvariants:
    """The user-visible guarantees each capture exists to protect."""

    def test_subagent_turn_never_stops_the_parent(self, plugin_module, tmp_path):
        records = load_fixture("subagent_task")
        result = replay(plugin_module, tmp_path, [{"name": "s", "records": records}])["s"]
        events = events_of(result)
        # The parent is inside its Task call from PreToolUse[Task] until
        # PostToolUse[Task]; nothing in between may read as idle.
        start = events.index("PreToolUse")
        end = len(events) - 1 - events[::-1].index("PostToolUse")
        assert "Stop" not in events[start:end], events
        assert events[-1] == "Stop"
        # The child's session id must not be recorded as one of this agent's
        # conversations — the stats reader would double count it.
        oracle = OpencodeOracle()
        oracle.run(records)
        for child in oracle.children:
            assert child not in (result["state"].get("agent_session_ids") or []), result["state"]

    def test_subagent_tool_calls_are_not_the_parents(self, plugin_module, tmp_path):
        records = load_fixture("subagent_task")
        result = replay(plugin_module, tmp_path, [{"name": "s", "records": records}])["s"]
        tools = [p["tool_name"] for per in result["publishes"] for p in per if p["tool_name"]]
        assert "Glob" not in tools and "Read" not in tools, tools
        assert tools == ["Task", "Task"]

    def test_child_permission_ask_surfaces_for_the_parent(self, plugin_module, tmp_path):
        records = load_fixture("child_permission")
        result = replay(plugin_module, tmp_path, [{"name": "c", "records": records}])["c"]
        events = events_of(result)
        assert "PermissionRequest" in events, events
        i = events.index("PermissionRequest")
        # Allow once → back to running, still inside the parent's Task call.
        assert events[i + 1] == "PreToolUse"
        assert "Stop" not in events[:-1]
        assert events[-1] == "Stop"

    def test_provider_error_stays_visible(self, plugin_module, tmp_path):
        records = load_fixture("provider_error")
        result = replay(plugin_module, tmp_path, [{"name": "e", "records": records}])["e"]
        events = events_of(result)
        assert events[-1] == "StopFailure", events
        assert result["state"]["event"] == "StopFailure"
        assert "Incorrect API key" in (result["state"].get("error") or "")

    def test_interrupt_settles_to_stop_not_error(self, plugin_module, tmp_path):
        records = load_fixture("interrupt")
        result = replay(plugin_module, tmp_path, [{"name": "i", "records": records}])["i"]
        events = events_of(result)
        assert "StopFailure" not in events, events
        assert events == ["UserPromptSubmit", "Stop"]

    def test_queued_prompts_publish_one_stop(self, plugin_module, tmp_path):
        records = load_fixture("queued_prompts")
        result = replay(plugin_module, tmp_path, [{"name": "q", "records": records}])["q"]
        assert events_of(result) == ["UserPromptSubmit", "UserPromptSubmit", "Stop"]

    def test_resumed_session_reports(self, plugin_module, tmp_path):
        records = load_fixture("resume")
        result = replay(plugin_module, tmp_path, [{"name": "r", "records": records}])["r"]
        assert events_of(result) == ["UserPromptSubmit", "Stop"]
        assert result["state"]["agent_session_ids"], result["state"]

    def test_new_session_records_second_root(self, plugin_module, tmp_path):
        records = load_fixture("new_session")
        result = replay(plugin_module, tmp_path, [{"name": "n", "records": records}])["n"]
        assert events_of(result) == ["UserPromptSubmit", "Stop"]

    def test_permission_allow_and_reject_carry_the_tool(self, plugin_module, tmp_path):
        allow = load_fixture("permission_allow")
        reject = load_fixture("permission_reject")
        results = replay(plugin_module, tmp_path, [
            {"name": "a", "records": allow}, {"name": "r", "records": reject},
        ])
        a = [(p["event"], p["tool_name"]) for per in results["a"]["publishes"] for p in per]
        r = [(p["event"], p["tool_name"]) for per in results["r"]["publishes"] for p in per]
        assert a == [
            ("UserPromptSubmit", None), ("PreToolUse", "Bash"), ("PermissionRequest", "Bash"),
            ("PreToolUse", "Bash"), ("PostToolUse", "Bash"), ("Stop", None),
        ]
        assert r == [
            ("UserPromptSubmit", None), ("PreToolUse", "Bash"), ("PermissionRequest", "Bash"),
            ("PostToolUse", "Bash"), ("Stop", None),
        ]

    def test_whole_run_in_one_process(self, plugin_module, tmp_path):
        """Run 1 was a single opencode process: the scenarios concatenated
        must still match the oracle — state carried across turns (seen
        message ids, roots, children) is what a long-lived agent depends on."""
        order = ["simple_turn", "read_tool", "permission_allow", "permission_reject",
                 "subagent_task", "interrupt", "new_session", "queued_prompts"]
        records = [r for name in order for r in load_fixture(name)]
        result = replay(plugin_module, tmp_path, [{"name": "all", "records": records}])["all"]
        problems = diff_publishes(records, result["publishes"])
        assert not problems, "\n  ".join(problems[:12])
        assert result["state"]["event"] == "Stop"
