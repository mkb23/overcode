#!/usr/bin/env python3
"""Mock opencode CLI for integration testing.

Usage:
    MOCK_SCENARIO=oc_permission_bash ./mock_opencode.py [args...]
    ./mock_opencode.py --scenario=oc_permission_bash [args...]

Wired in via OPENCODE_COMMAND, mirroring how CLAUDE_COMMAND swaps in
tests/mock_claude.py. Scenario files live in tests/scenarios/*.yaml;
the built-ins below cover the three flows Phase 4 cares about.

The chrome is copied from real opencode v1.18.19 captures — the same ones
committed under tests/fixtures_opencode_panes/ — so a detector that passes
against this mock passes against the real TUI.

Store and telemetry (#534). When ``OPENCODE_DB`` or ``OPENCODE_DATA_DIR``
names a store (opencode's own env vars; the container harness points them
at a per-test directory), the mock also does what overcode learns an
opencode agent's conversation from:

* it writes the conversation to that SQLite store (``tests/opencode_store.py``,
  the schema the stats reader reads): a new ``ses_…`` row on the first
  prompt of a fresh launch, the same row on ``--session <id>`` (an unknown
  id exits with an error, as opencode does), a new ``(fork #1)`` row holding
  copies of the source's messages on ``--session <id> --fork``, and one
  assistant message with tokens and cost per scripted turn;
* it feeds the matching opencode events through the telemetry plugin
  overcode installed in ``.opencode/plugins/`` of the working directory
  (run by node through ``tests/js/opencode_plugin_harness.mjs``), so the
  hook-state files carry the conversation id exactly as from real opencode.

Without either variable the mock touches no store and runs no plugin: it is
never allowed to default to the user's real ``~/.local/share/opencode``.
The ``oc_*`` step types below are no-ops then, so the pane is the same
either way.
"""

import argparse
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mock_agent_lib import ScenarioRunner  # noqa: E402
from opencode_store import MockStore, SessionNotFound, configured_path  # noqa: E402

PLUGIN_HARNESS = Path(__file__).resolve().parent / "js" / "opencode_plugin_harness.mjs"
PLUGIN_FILE = Path(".opencode") / "plugins" / "overcode-telemetry.js"
PLUGIN_CORE = Path("overcode-telemetry-v2") / "overcode-telemetry-core.mjs"

# What one scripted turn costs. Deliberately large next to a real
# gpt-4o-mini turn so a budget test trips on a single turn.
TURN_COST_USD = 0.75
TURN_TOKENS = {"input_tokens": 7000, "output_tokens": 100, "reasoning_tokens": 20,
               "cache_read": 512, "cache_write": 64}


# =============================================================================
# opencode v1.18.19 chrome
#
# Two structural facts drive every pattern in backends/opencode.py:
#   * the input is a BOX with a ┃ gutter, not a single ❯ prompt line;
#   * "esc interrupt" in the bottom bar is the only in-flight signal.
# =============================================================================

BANNER = """\
                                   ▄
  █▀▀█ █▀▀█ █▀▀█ █▀▀▄ █▀▀▀ █▀▀█ █▀▀█ █▀▀█
  █  █ █  █ █▀▀▀ █  █ █    █  █ █  █ █▀▀▀
  ▀▀▀▀ █▀▀▀ ▀▀▀▀ ▀▀▀▀ ▀▀▀▀ ▀▀▀▀ ▀▀▀▀ ▀▀▀▀

"""

BOX_WIDTH = 74
BOX_BOTTOM = "  ╹" + "▀" * BOX_WIDTH

# Fresh launch: placeholder inside the box, hint bar underneath.
EMPTY_PROMPT = f"""
  ┃
  ┃  Ask anything... "Fix a TODO in the codebase"
  ┃
  ┃  Build · GPT-4o mini OpenAI
{BOX_BOTTOM}
  tab agents  ctrl+p commands
"""

# Settled after a turn: same box, info bar carries tokens/cost.
IDLE_PROMPT = f"""
  ┃
  ┃
  ┃
  ┃  Build · GPT-4o mini OpenAI
{BOX_BOTTOM}
   /Users/dev/code/demo-proj                       7.4K (6%) · $0.00  ctrl+p commands
"""

# In flight: spinner + interrupt hint replace the info bar.
BUSY_BAR = f"""
  ┃
  ┃
  ┃
  ┃  Build · GPT-4o mini OpenAI
{BOX_BOTTOM}
   ⬝⬝⬝⬝⬝⬝⬝⬝  esc interrupt                                    tab agents  ctrl+p commands
"""

USER_TURN = """
  ┃
  ┃  {prompt}
  ┃
"""

ASSISTANT_FOOTER = "\n     ▣  Build · GPT-4o mini · 4.4s\n"

# The permission dialog replaces the input box entirely — no info bar.
PERMISSION_DIALOG = """
  ┃
  ┃  △ Permission required
  ┃    # Shell command
  ┃
  ┃  $ echo hello
  ┃
  ┃
  ┃   Allow once   Allow always   Reject                 ctrl+f fullscreen  ⇆ select  enter confirm
  ┃
"""

TOOL_BLOCK = """
     → Read README.md
     ✱ Glob "*" in . (2 matches)
"""

SIMPLE_RESPONSE = """
     Here are five prime numbers along with a brief note on each:

      1. 2: The only even prime number, and the smallest prime.
      2. 3: The first odd prime.
      3. 5: The only prime factor of 10 besides 2.
      4. 7: The fourth prime.
      5. 11: The smallest two-digit prime.
"""


def get_scenario_dir() -> Path:
    return Path(__file__).parent / "scenarios"


def get_builtin_scenarios() -> Dict[str, Dict[str, Any]]:
    """Built-in opencode scenarios.

    Names are prefixed `oc_` so they can't collide with mock_claude's in the
    shared tests/scenarios/ directory.
    """
    return {
        "oc_launch_and_idle": {
            "name": "oc_launch_and_idle",
            "description": "Banner, empty input box, waits at the prompt",
            "steps": [
                {"type": "output", "text": BANNER, "delay_ms": 100},
                {"type": "output", "text": EMPTY_PROMPT, "delay_ms": 50},
                {"type": "wait_for_input", "timeout_seconds": 300},
            ],
        },
        "oc_simple_response": {
            "name": "oc_simple_response",
            "description": "One completed turn (tool calls + prose), then idle",
            "steps": [
                {"type": "output", "text": BANNER, "delay_ms": 100},
                {"type": "output", "text": USER_TURN.format(
                    prompt="list five prime numbers with a one line note on each"
                ), "delay_ms": 100},
                {"type": "oc_prompt",
                 "text": "list five prime numbers with a one line note on each"},
                {"type": "output", "text": BUSY_BAR, "delay_ms": 100},
                {"type": "oc_tool", "tool": "read", "args": {"filePath": "README.md"}},
                {"type": "output", "text": TOOL_BLOCK, "delay_ms": 200},
                {"type": "output", "text": SIMPLE_RESPONSE, "delay_ms": 200},
                {"type": "output", "text": ASSISTANT_FOOTER, "delay_ms": 50},
                {"type": "oc_reply"},
                {"type": "output", "text": IDLE_PROMPT, "delay_ms": 50},
                {"type": "wait_for_input", "timeout_seconds": 300},
            ],
        },
        "oc_task_complete": {
            "name": "oc_task_complete",
            "description": "One completed turn, then opencode exits to the shell",
            "steps": [
                {"type": "output", "text": BANNER, "delay_ms": 100},
                {"type": "output", "text": USER_TURN.format(
                    prompt="list five prime numbers with a one line note on each"
                ), "delay_ms": 100},
                {"type": "oc_prompt",
                 "text": "list five prime numbers with a one line note on each"},
                {"type": "output", "text": BUSY_BAR, "delay_ms": 100},
                {"type": "output", "text": SIMPLE_RESPONSE, "delay_ms": 200},
                {"type": "output", "text": ASSISTANT_FOOTER, "delay_ms": 50},
                {"type": "oc_reply"},
                {"type": "output", "text": IDLE_PROMPT, "delay_ms": 50},
                {"type": "exit", "code": 0},
            ],
        },
        "oc_task_running": {
            "name": "oc_task_running",
            "description": "A turn in flight (tool running) that never finishes",
            "steps": [
                {"type": "output", "text": BANNER, "delay_ms": 100},
                {"type": "output", "text": USER_TURN.format(
                    prompt="analyze the codebase and suggest improvements"
                ), "delay_ms": 100},
                {"type": "oc_prompt", "text": "analyze the codebase and suggest improvements"},
                {"type": "output", "text": TOOL_BLOCK, "delay_ms": 100},
                {"type": "oc_tool", "tool": "glob", "args": {"pattern": "*"}, "finish": False},
                {"type": "output", "text": BUSY_BAR, "delay_ms": 100},
                {"type": "wait_for_input", "timeout_seconds": 300},
            ],
        },
        "oc_crash_mid_task": {
            "name": "oc_crash_mid_task",
            "description": "Starts a turn, then dies back to the shell",
            "steps": [
                {"type": "output", "text": BANNER, "delay_ms": 100},
                {"type": "output", "text": USER_TURN.format(
                    prompt="analyze the codebase and suggest improvements"
                ), "delay_ms": 100},
                {"type": "oc_prompt", "text": "analyze the codebase and suggest improvements"},
                {"type": "output", "text": BUSY_BAR, "delay_ms": 100},
                {"type": "output", "text": TOOL_BLOCK, "delay_ms": 300},
                {"type": "exit", "code": 1, "message": "Error: Simulated crash\n"},
            ],
        },
        "oc_permission_bash": {
            "name": "oc_permission_bash",
            "description": "△ Permission required dialog for a shell command",
            "steps": [
                {"type": "output", "text": BANNER, "delay_ms": 100},
                {"type": "output", "text": USER_TURN.format(
                    prompt="run the shell command: echo hello"
                ), "delay_ms": 100},
                {"type": "oc_prompt", "text": "run the shell command: echo hello"},
                {"type": "output", "text": "\n     $ echo hello\n", "delay_ms": 100},
                {"type": "oc_permission", "command": "echo hello"},
                {"type": "output", "text": PERMISSION_DIALOG, "delay_ms": 100},
                # Enter confirms the preselected "Allow once"; Escape rejects.
                {"type": "menu", "options": [
                    "Allow once",
                    "Allow always",
                    "Reject",
                ], "prompt": "", "goto_map": {
                    0: "allowed", 1: "allowed_always", 2: "rejected",
                }},
            ],
            "labels": {
                "allowed": [
                    {"type": "oc_permission_reply", "reply": "once"},
                    {"type": "output", "text": "\n  ┃\n  ┃  $ echo hello\n  ┃\n  ┃  hello\n  ┃\n", "delay_ms": 150},
                    {"type": "output", "text": "\n     The command executed successfully, and the output is: hello.\n", "delay_ms": 100},
                    {"type": "output", "text": ASSISTANT_FOOTER, "delay_ms": 50},
                    {"type": "oc_reply"},
                    {"type": "output", "text": IDLE_PROMPT, "delay_ms": 50},
                    {"type": "wait_for_input", "timeout_seconds": 300},
                ],
                "allowed_always": [
                    {"type": "oc_permission_reply", "reply": "always"},
                    {"type": "output", "text": "\n  ┃\n  ┃  $ echo hello\n  ┃\n  ┃  hello\n  ┃\n", "delay_ms": 150},
                    {"type": "output", "text": "\n     Done — bash is now always allowed for this session.\n", "delay_ms": 100},
                    {"type": "output", "text": ASSISTANT_FOOTER, "delay_ms": 50},
                    {"type": "oc_reply"},
                    {"type": "output", "text": IDLE_PROMPT, "delay_ms": 50},
                    {"type": "wait_for_input", "timeout_seconds": 300},
                ],
                "rejected": [
                    {"type": "oc_permission_reply", "reply": "reject"},
                    {"type": "output", "text": "\n     I'll find another approach that doesn't need the shell.\n", "delay_ms": 100},
                    {"type": "output", "text": ASSISTANT_FOOTER, "delay_ms": 50},
                    {"type": "oc_reply"},
                    {"type": "output", "text": IDLE_PROMPT, "delay_ms": 50},
                    {"type": "wait_for_input", "timeout_seconds": 300},
                ],
            },
        },
    }



# =============================================================================
# Store + telemetry (see the module docstring)
# =============================================================================


class Telemetry:
    """Feeds opencode events through the plugin overcode installed in cwd.

    opencode loads ``.opencode/plugins/*.js`` from the project; overcode
    copies its telemetry plugin there at launch. The installed copy (not the
    bundled one) is what runs, copied out as ``.mjs`` with its shared core
    beside it the way ``tests/opencode_plugin_files.py`` lays it out for node.
    One node run per step: the plugin keeps no state it needs across them
    (with no root session known it adopts the first one it sees, the same
    rule that covers a resumed opencode).
    """

    def __init__(self, project_dir: Path, log: Path):
        self.log = log
        self.plugin: Optional[Path] = None
        installed = project_dir / PLUGIN_FILE
        core = installed.parent / PLUGIN_CORE
        if not (installed.is_file() and core.is_file()):
            self._note(f"no telemetry plugin at {installed}; running without it")
            return
        if shutil.which("node") is None:
            self._note("node not found; running without the telemetry plugin")
            return
        workdir = Path(tempfile.mkdtemp(prefix="mock-opencode-plugin-"))
        atexit.register(shutil.rmtree, workdir, True)
        self.plugin = workdir / "overcode-telemetry.mjs"
        self.plugin.write_text(installed.read_text(encoding="utf-8"), encoding="utf-8")
        (workdir / PLUGIN_CORE).parent.mkdir(parents=True)
        (workdir / PLUGIN_CORE).write_text(core.read_text(encoding="utf-8"), encoding="utf-8")

    def _note(self, message: str) -> None:
        try:
            with self.log.open("a") as f:
                f.write(f"{time.strftime('%H:%M:%S')} [{os.getpid()}] {message}\n")
        except OSError:
            pass

    def send(self, actions: List[Dict[str, Any]]) -> None:
        if self.plugin is None or not actions:
            return
        job = {"plugin": str(self.plugin), "env": dict(os.environ), "actions": actions}
        try:
            result = subprocess.run(
                ["node", str(PLUGIN_HARNESS)], input=json.dumps(job),
                capture_output=True, text=True, timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._note(f"plugin run failed: {exc}")
            return
        if result.returncode != 0:
            self._note(f"plugin run failed: {result.stdout} {result.stderr}")


def bus(type_: str, **properties: Any) -> Dict[str, Any]:
    return {"kind": "bus", "event": {"type": type_, "properties": properties}}


class OpencodeRunner(ScenarioRunner):
    """``ScenarioRunner`` plus the ``oc_*`` steps that write the store and
    publish telemetry. Every ``oc_*`` step is a no-op without a store."""

    def __init__(self, scenario: Dict[str, Any], store: Optional[MockStore] = None,
                 telemetry: Optional[Telemetry] = None, session_id: Optional[str] = None,
                 model: Optional[str] = None):
        super().__init__(scenario)
        self.store = store
        self.telemetry = telemetry
        self.session_id = session_id
        self.model = model
        self.directory = os.getcwd()
        self._call = 0
        self._permission: Optional[Dict[str, Any]] = None

    def publish(self, *actions: Dict[str, Any]) -> None:
        if self.telemetry is not None:
            self.telemetry.send(list(actions))

    def session_created(self) -> Dict[str, Any]:
        row = self.store.session(self.session_id) or {}
        return bus("session.created", sessionID=self.session_id, info={
            "id": self.session_id, "slug": row.get("slug"),
            "directory": row.get("directory"), "title": row.get("title"),
        })

    def _next_call_id(self) -> str:
        self._call += 1
        return f"call_mock{os.getpid()}x{self._call}"

    def execute_step(self, step: Dict) -> Optional[str]:
        step_type = step.get("type", "")
        if not step_type.startswith("oc_"):
            return super().execute_step(step)
        if self.store is None:
            return None
        sid = self.session_id

        if step_type == "oc_prompt":
            text = step.get("text", "")
            actions = []
            if sid is None:
                # opencode creates the conversation on the first prompt
                sid = self.session_id = self.store.create_session(
                    self.directory, self.model, title=text[:50] or "Session")
                actions.append(self.session_created())
            msg_id = self.store.add_user_message(sid, text)
            actions += [
                {"kind": "chat", "input": {"sessionID": sid, "agent": "build"},
                 "output": {"message": {"id": msg_id, "role": "user", "sessionID": sid}}},
                bus("session.status", sessionID=sid, status={"type": "busy"}),
            ]
            self.publish(*actions)

        elif sid is None:
            return None  # no conversation yet: nothing for opencode to record

        elif step_type == "oc_tool":
            call = {"tool": step.get("tool", "read"), "sessionID": sid,
                    "callID": self._next_call_id()}
            args = step.get("args", {})
            actions = [{"kind": "before", "input": call, "output": {"args": args}}]
            if step.get("finish", True):
                actions.append({"kind": "after", "input": {**call, "args": args},
                                "output": {"output": step.get("output", "")}})
            self.publish(*actions)

        elif step_type == "oc_permission":
            command = step.get("command", "")
            call = {"tool": "bash", "sessionID": sid, "callID": self._next_call_id()}
            self._permission = {"id": f"per_mock{os.getpid()}", "call": call,
                                "command": command}
            self.publish(
                {"kind": "before", "input": call, "output": {"args": {"command": command}}},
                bus("permission.asked", id=self._permission["id"], sessionID=sid,
                    permission="bash", patterns=[command],
                    metadata={"command": command},
                    tool={"messageID": "msg_mock", "callID": call["callID"]}),
            )

        elif step_type == "oc_permission_reply":
            reply = step.get("reply", "once")
            pending = self._permission or {"id": "per_mock", "call": {}, "command": ""}
            actions = [bus("permission.replied", sessionID=sid,
                           requestID=pending["id"], reply=reply)]
            if reply != "reject" and pending["call"]:
                actions.append({"kind": "after",
                                "input": {**pending["call"],
                                          "args": {"command": pending["command"]}},
                                "output": {"output": "hello\n"}})
            self._permission = None
            self.publish(*actions)

        elif step_type == "oc_reply":
            self.store.add_assistant_turn(
                sid, cost=step.get("cost", TURN_COST_USD), **TURN_TOKENS)
            self.publish(bus("session.status", sessionID=sid, status={"type": "idle"}),
                         bus("session.idle", sessionID=sid))
        return None


def load_scenario(name: str) -> Dict[str, Any]:
    """Load a scenario from YAML, falling back to the built-ins."""
    scenario_file = get_scenario_dir() / f"{name}.yaml"
    if scenario_file.exists():
        import yaml
        with open(scenario_file) as f:
            return yaml.safe_load(f)

    scenarios = get_builtin_scenarios()
    if name in scenarios:
        return scenarios[name]
    raise FileNotFoundError(f"Scenario not found: {name}")


def main():
    parser = argparse.ArgumentParser(description="Mock opencode CLI")
    # No short form: opencode's own -s is --session.
    parser.add_argument("--scenario", help="Scenario to run")
    parser.add_argument("--print", help="Print message and exit")
    parser.add_argument("--version", "-v", action="store_true",
                        help="Print a plausible opencode version and exit")
    parser.add_argument("--session", help="Resume this conversation")
    parser.add_argument("--fork", action="store_true",
                        help="With --session: branch it into a new conversation")
    parser.add_argument("--model", "-m", help="provider/model")
    parser.add_argument("prompt", nargs="*", help="Positional project path (ignored)")

    # Tolerate (and ignore) every other real opencode flag the launcher may
    # add — --agent, --auto, extra passthrough args.
    args, _unknown = parser.parse_known_args()

    if args.version:
        print("1.18.19")
        return

    if args.print:
        print(args.print)
        return

    scenario_name = args.scenario or os.environ.get(
        "MOCK_SCENARIO", "oc_launch_and_idle"
    )

    try:
        scenario = load_scenario(scenario_name)
    except FileNotFoundError as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)

    store_path = configured_path()
    store = telemetry = None
    session_id = None
    if store_path is not None:
        store = MockStore(store_path)
        telemetry = Telemetry(Path.cwd(), store_path.parent / "mock-opencode.log")
        if args.session:
            try:
                if args.fork:
                    session_id = store.fork_session(args.session, os.getcwd())
                elif store.session(args.session) is not None:
                    session_id = args.session
                else:
                    raise SessionNotFound(args.session)
            except SessionNotFound:
                # Real opencode refuses an unknown id rather than starting fresh
                sys.stderr.write(f"Error: Session not found: {args.session}\n")
                sys.exit(1)

    runner = OpencodeRunner(scenario, store, telemetry, session_id, args.model)
    if store is not None and args.fork:
        # opencode creates the fork at startup, before any prompt
        runner.publish(runner.session_created())
    runner.run()


if __name__ == "__main__":
    main()
