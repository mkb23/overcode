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
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mock_agent_lib import ScenarioRunner  # noqa: E402


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
                {"type": "output", "text": BUSY_BAR, "delay_ms": 100},
                {"type": "output", "text": TOOL_BLOCK, "delay_ms": 200},
                {"type": "output", "text": SIMPLE_RESPONSE, "delay_ms": 200},
                {"type": "output", "text": ASSISTANT_FOOTER, "delay_ms": 50},
                {"type": "output", "text": IDLE_PROMPT, "delay_ms": 50},
                {"type": "wait_for_input", "timeout_seconds": 300},
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
                {"type": "output", "text": "\n     $ echo hello\n", "delay_ms": 100},
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
                    {"type": "output", "text": "\n  ┃\n  ┃  $ echo hello\n  ┃\n  ┃  hello\n  ┃\n", "delay_ms": 150},
                    {"type": "output", "text": "\n     The command executed successfully, and the output is: hello.\n", "delay_ms": 100},
                    {"type": "output", "text": ASSISTANT_FOOTER, "delay_ms": 50},
                    {"type": "output", "text": IDLE_PROMPT, "delay_ms": 50},
                    {"type": "wait_for_input", "timeout_seconds": 300},
                ],
                "allowed_always": [
                    {"type": "output", "text": "\n  ┃\n  ┃  $ echo hello\n  ┃\n  ┃  hello\n  ┃\n", "delay_ms": 150},
                    {"type": "output", "text": "\n     Done — bash is now always allowed for this session.\n", "delay_ms": 100},
                    {"type": "output", "text": ASSISTANT_FOOTER, "delay_ms": 50},
                    {"type": "output", "text": IDLE_PROMPT, "delay_ms": 50},
                    {"type": "wait_for_input", "timeout_seconds": 300},
                ],
                "rejected": [
                    {"type": "output", "text": "\n     I'll find another approach that doesn't need the shell.\n", "delay_ms": 100},
                    {"type": "output", "text": ASSISTANT_FOOTER, "delay_ms": 50},
                    {"type": "output", "text": IDLE_PROMPT, "delay_ms": 50},
                    {"type": "wait_for_input", "timeout_seconds": 300},
                ],
            },
        },
    }


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
    parser.add_argument("prompt", nargs="*", help="Positional project path (ignored)")

    # Tolerate (and ignore) every real opencode flag the launcher may add —
    # --model, --agent, --auto, --session, --fork, extra passthrough args.
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

    ScenarioRunner(scenario).run()


if __name__ == "__main__":
    main()
