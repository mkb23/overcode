#!/usr/bin/env python3
"""Mock opencode2 CLI for integration testing.

Usage:
    MOCK_SCENARIO=oc2_permission_bash ./mock_opencode2.py [args...]
    ./mock_opencode2.py --scenario=oc2_permission_bash [args...]

Wired in via OPENCODE2_COMMAND, mirroring how OPENCODE_COMMAND swaps in
tests/mock_opencode.py (v1) and CLAUDE_COMMAND swaps in mock_claude.py.
Scenario files live in tests/scenarios/*.yaml; the built-ins below cover
the flows the e2e layer cares about plus the armed-interrupt pane the
chrome tests need to reach.

The chrome is copied from real opencode2 v0.0.0-dev-19272 captures — the
ones committed under tests/fixtures_opencode2_panes/ — so a detector that
passes against this mock passes against the real TUI. Every constant
cites the corpus file it came from; the key sequences the scenarios
implement (bare Enter approving the preselected "Allow once", Escape
rejecting with "The user declined this tool call", /exit, /new) were
verified live against that build — see
src/overcode/backends/opencode2.py's key methods.
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mock_agent_lib import ScenarioRunner  # noqa: E402


# =============================================================================
# opencode2 v0.0.0-dev-19272 chrome
#
# Same structural facts as v1 (the ┃-guttered input box, "esc interrupt"
# as the only in-flight signal) with three deltas that drove the v2
# pattern set: the agents key is now "shift+tab", the idle footer carries
# context+cost inline, and the permission dialog says "Always allow"
# where v1 said "Allow always".
# =============================================================================

BANNER = """\
                                    ▄
   █▀▀█ █▀▀█ █▀▀█ █▀▀▄ █▀▀▀ █▀▀█ █▀▀█ █▀▀█
   █  █ █  █ █▀▀▀ █  █ █    █  █ █  █ █▀▀▀
   ▀▀▀▀ █▀▀▀ ▀▀▀▀ ▀▀▀▀ ▀▀▀▀ ▀▀▀▀ ▀▀▀▀ ▀▀▀▀

"""

BOX_WIDTH = 72
BOX_BOTTOM = "  ╹" + "▀" * BOX_WIDTH

# The model line inside the box (idle_fresh.txt):
#   ┃  Build · acme-llm-1 Acme · high
MODEL_LINE = "  ┃  Build · acme-llm-1 Acme · high"

# Fresh launch: placeholder inside the box, hint bar underneath
# (idle_fresh.txt — note v2's agents key is shift+tab, not v1's tab).
EMPTY_PROMPT = f"""
  ┃
  ┃  Ask anything… "Fix broken tests"
  ┃
{MODEL_LINE}
{BOX_BOTTOM}
  /home/dev/code/demo-proj           shift+tab agents  ctrl+p commands
"""

# Settled after a turn: same box, and the footer carries context+cost
# inline (idle_after_response.txt):
#   /tmp/opencode/oc2cap-work/plain    10.7K (2%) · $0.01  ctrl+p commands
IDLE_PROMPT = f"""
  ┃
  ┃
  ┃
{MODEL_LINE}
{BOX_BOTTOM}
  /home/dev/code/demo-proj           10.7K (2%) · $0.01  ctrl+p commands
"""

# In flight (busy.txt): a braille frame in the session tab, spinner bar
# and "esc interrupt" hint in the bottom bar.
BUSY_BAR = f"""
   Casual greeting               ⠦ New session                   +

  ┃
  ┃  write a 300-line poem
  ┃
  ┃
  ┃
{MODEL_LINE}
{BOX_BOTTOM}
   ⬝⬝■■■■■■ esc interrupt                          shift+tab agents  ctrl+p commands
"""

# One Escape into the interrupt sequence the hint becomes
# "esc again to interrupt" (captured live in the same session as
# busy.txt) — the armed state the double-Escape graceful exit passes
# through.
BUSY_ARMED = f"""
   Casual greeting               ⠦ New session                   +

  ┃
  ┃  write a 300-line poem
  ┃
  ┃
  ┃
{MODEL_LINE}
{BOX_BOTTOM}
   ⬝⬝■■■■■■ esc again to interrupt                  shift+tab agents  ctrl+p commands
"""

USER_TURN = """
  ┃
  ┃  {prompt}
  ┃
"""

# v2 dropped v1's ▣ response pill — turns close with a bare model pill
# (idle_after_response.txt: "Build · acme-llm-1 · 1.3s · 93.0 tok/s").
ASSISTANT_FOOTER = "\n     Build · acme-llm-1 · 1.3s · 93.0 tok/s\n"

# The permission dialog replaces the input box (permission_required.txt).
# v2 renamed v1's "Allow always" to "Always allow" and dropped the
# "# Shell command" heading — the dialog is title, command, options.
PERMISSION_DIALOG = """
  ┃
  ┃  △ Permission required
  ┃
  ┃  $ echo hi2
  ┃
  ┃
  ┃   Allow once   Always allow   Reject                                 ctrl+f fullscreen  ⇆ select  enter confirm
  ┃
"""

# v2 renders finished tool work with "→" and "+" glyphs
# (idle_after_response.txt: '→ Skill "using-superpowers"', "+ Thought ·
# 189ms") — v1's ▣/✱ appear nowhere in the v2 corpus.
TOOL_BLOCK = """
     → Skill "using-superpowers"

     + Thought · 189ms
"""

SIMPLE_RESPONSE = """
     Here are five prime numbers along with a brief note on each:

      1. 2: The only even prime number, and the smallest prime.
      2. 3: The first odd prime.
      3. 5: The only prime factor of 10 besides 2.
      4. 7: The fourth prime.
      5. 11: The smallest two-digit prime.
"""

# The mid-run tool state inside the box (permission_required.txt, the
# braille spinner next to the running command):
#   ┃  ⠧ echo hi2
TOOL_RUNNING = "\n  ┃\n  ┃  ⠧ echo hi2\n  ┃\n"

# The /exit handler's stderr line — the observable that distinguishes a
# genuine /exit from the engine's unmatched-input fallback. Both end the
# process with code 0 (the fallback runs the scenario off its last step
# and lets main() return); only the handler prints this. The engine's
# exit step writes it via sys.stderr before sys.exit(0).
EXIT_MESSAGE = "opencode2 mock: /exit received — exiting\n"


def _idle_tail(timeout_seconds: int = 300):
    """Wait for input, then handle the two verified slash commands.

    In the real TUI the command autocomplete consumes the first Enter
    (/exit + Enter only fills the box) and a trailing bare Enter executes
    it; the mock's readline sees the typed line as one input, so the net
    effect is identical: /exit exits, /new resets to the banner.

    The match patterns tolerate leading ``\\x1b`` bytes because overcode's
    graceful_exit_keys (Escape, Escape, /exit, Enter, Enter) reach the
    mock's cooked-mode readline as ONE line with both Escapes still in
    it — the line discipline passes a bare ESC through to the buffer.
    The engine's re.match anchors at the string start, so an un-prefixed
    pattern would miss the command and the mock would end via the
    fallback instead of the /exit handler.
    """
    return [
        {"type": "wait_for_input", "timeout_seconds": timeout_seconds},
        # Leading \x1b*: the graceful-exit Escapes share the readline
        # line with the command itself (see docstring).
        {"type": "on_input", "match": r"\x1b*/exit", "goto": "exit_app"},
        {"type": "on_input", "match": r"\x1b*/new", "goto": "new_session"},
    ]


def _idle_labels() -> Dict[str, Any]:
    return {
        "exit_app": [
            {"type": "exit", "code": 0, "message": EXIT_MESSAGE},
        ],
        "new_session": [
            {"type": "output", "text": BANNER, "delay_ms": 100},
            {"type": "output", "text": EMPTY_PROMPT, "delay_ms": 50},
            *_idle_tail(),
        ],
    }


def get_scenario_dir() -> Path:
    return Path(__file__).parent / "scenarios"


def get_builtin_scenarios() -> Dict[str, Dict[str, Any]]:
    """Built-in opencode2 scenarios.

    Names are prefixed ``oc2_`` so they can't collide with mock_claude's
    or mock_opencode's in the shared tests/scenarios/ directory.
    """
    return {
        "oc2_launch_and_idle": {
            "name": "oc2_launch_and_idle",
            "description": "Banner, empty input box, waits at the prompt",
            "steps": [
                {"type": "output", "text": BANNER, "delay_ms": 100},
                {"type": "output", "text": EMPTY_PROMPT, "delay_ms": 50},
                *_idle_tail(),
            ],
            "labels": _idle_labels(),
        },
        "oc2_simple_response": {
            "name": "oc2_simple_response",
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
                *_idle_tail(),
            ],
            "labels": _idle_labels(),
        },
        # One Escape into the interrupt sequence (BUSY_ARMED) — the state
        # the double-Escape graceful exit passes through. Reached by
        # script only: the real TUI arms it from a live mid-turn pane,
        # which the step engine cannot express.
        "oc2_busy_interrupt": {
            "name": "oc2_busy_interrupt",
            "description": "Armed busy bar (esc again to interrupt), waits at the prompt",
            "steps": [
                {"type": "output", "text": BANNER, "delay_ms": 100},
                {"type": "output", "text": USER_TURN.format(
                    prompt="write a 300-line poem"
                ), "delay_ms": 100},
                {"type": "output", "text": BUSY_ARMED, "delay_ms": 100},
                *_idle_tail(),
            ],
            "labels": _idle_labels(),
        },
        "oc2_permission_bash": {
            "name": "oc2_permission_bash",
            "description": "△ Permission required dialog for a shell command",
            "steps": [
                {"type": "output", "text": BANNER, "delay_ms": 100},
                {"type": "output", "text": USER_TURN.format(
                    prompt="run the shell command: echo hi2"
                ), "delay_ms": 100},
                {"type": "output", "text": TOOL_RUNNING, "delay_ms": 100},
                {"type": "output", "text": PERMISSION_DIALOG, "delay_ms": 100},
                # Enter confirms the preselected "Allow once"; Escape rejects.
                {"type": "menu", "options": [
                    "Allow once",
                    "Always allow",
                    "Reject",
                ], "prompt": "", "goto_map": {
                    0: "allowed", 1: "allowed_always", 2: "rejected",
                }},
            ],
            "labels": {
                "allowed": [
                    {"type": "output", "text": "\n  ┃\n  ┃  $ echo hi2\n  ┃\n  ┃  hi2\n  ┃\n", "delay_ms": 150},
                    {"type": "output", "text": "\n  ┃\n  ┃  Command exited with code 0.\n  ┃\n", "delay_ms": 100},
                    {"type": "output", "text": "\n     The command executed successfully, and the output is: hi2.\n", "delay_ms": 100},
                    {"type": "output", "text": ASSISTANT_FOOTER, "delay_ms": 50},
                    {"type": "output", "text": IDLE_PROMPT, "delay_ms": 50},
                    *_idle_tail(),
                ],
                "allowed_always": [
                    {"type": "output", "text": "\n  ┃\n  ┃  $ echo hi2\n  ┃\n  ┃  hi2\n  ┃\n", "delay_ms": 150},
                    {"type": "output", "text": "\n  ┃\n  ┃  Command exited with code 0.\n  ┃\n", "delay_ms": 100},
                    {"type": "output", "text": "\n     Done — the shell tool is now always allowed for this session.\n", "delay_ms": 100},
                    {"type": "output", "text": ASSISTANT_FOOTER, "delay_ms": 50},
                    {"type": "output", "text": IDLE_PROMPT, "delay_ms": 50},
                    *_idle_tail(),
                ],
                "rejected": [
                    # Verified live: after Escape the pane shows
                    # "The user declined this tool call" and returns to
                    # the idle input box.
                    {"type": "output", "text": "\n     The user declined this tool call\n", "delay_ms": 100},
                    {"type": "output", "text": "\n     I'll find another approach that doesn't need the shell.\n", "delay_ms": 100},
                    {"type": "output", "text": ASSISTANT_FOOTER, "delay_ms": 50},
                    {"type": "output", "text": IDLE_PROMPT, "delay_ms": 50},
                    *_idle_tail(),
                ],
                **_idle_labels(),
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
    parser = argparse.ArgumentParser(description="Mock opencode2 CLI")
    # No short form: opencode2's own -s is --session.
    parser.add_argument("--scenario", help="Scenario to run")
    parser.add_argument("--print", help="Print message and exit")
    parser.add_argument("--version", "-v", action="store_true",
                        help="Print a plausible opencode2 version and exit")
    parser.add_argument("prompt", nargs="*", help="Positional project path (ignored)")

    # Tolerate (and ignore) every real opencode2 flag the launcher may
    # add — --standalone (always), --session (resume), --auto
    # (permission bypass), extra passthrough args. --model/--agent/--fork
    # don't exist on the bare v2 TUI, so the launcher never sends them.
    args, _unknown = parser.parse_known_args()

    if args.version:
        # Real output of `opencode2 --version` (leading v, dev build id).
        print("opencode2 v0.0.0-dev-19272")
        return

    if args.print:
        print(args.print)
        return

    scenario_name = args.scenario or os.environ.get(
        "MOCK_SCENARIO", "oc2_launch_and_idle"
    )

    try:
        scenario = load_scenario(scenario_name)
    except FileNotFoundError as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)

    ScenarioRunner(scenario).run()


if __name__ == "__main__":
    main()
