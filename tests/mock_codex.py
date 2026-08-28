#!/usr/bin/env python3
"""Mock codex CLI for integration testing.

Usage:
    MOCK_SCENARIO=cx_permission_command ./mock_codex.py [args...]
    ./mock_codex.py --scenario=cx_permission_command [args...]

Wired in via CODEX_COMMAND, mirroring how CLAUDE_COMMAND/OPENCODE_COMMAND
swap in tests/mock_claude.py and tests/mock_opencode.py. Scenario files live
in tests/scenarios/*.yaml; the built-ins below cover the four flows Phase 1
cares about (launch-idle, a completed turn, a permission dialog, busy).

The chrome is copied from real Codex CLI v0.150.1 captures — the same ones
committed under tests/fixtures_codex_panes/ — so a detector that passes
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
# codex v0.150.1 chrome
#
# Two structural facts drive every pattern in backends/codex.py:
#   * the input placeholder ("Ask Codex to do anything") is what proves the
#     TUI is live, not a bare prompt glyph — codex never draws one empty;
#   * "esc to interrupt" in the spinner line is the only in-flight signal,
#     and it sits *above* the (still-drawn) placeholder + footer, not at
#     the pane's trailing edge.
# =============================================================================

BANNER = """\
╭────────────────────────────────────────────────────╮
│ >_ OpenAI Codex (v0.150.1)                         │
│                                                    │
│ model:     gpt-5.6-sol high   /model to change     │
│ directory: ~/probe-codex                            │
╰────────────────────────────────────────────────────╯

  Tip: Try the Desktop app. Run 'codex app' or visit https://chatgpt.com/codex?app-landing-page=true

• You have 1 usage limit reset available. Run /usage to use one.

"""

FOOTER = "\n  gpt-5.6-sol high · ~/probe-codex\n"

# Fresh/idle: the box placeholder plus the model/dir footer, no spinner.
IDLE_PROMPT = f"\n› Ask Codex to do anything\n{FOOTER}"

# In flight: the spinner line, with the (dormant) placeholder + footer still
# painted below it — verified live, this is why is_busy needs a wider window
# than Claude's default 2-line tail.
BUSY_BAR = f"\n• Working (1s • esc to interrupt)\n{IDLE_PROMPT}"

USER_TURN = "\n› {prompt}\n"

# The approval dialog replaces the input box entirely — no placeholder shown.
PERMISSION_DIALOG = """
  Would you like to run the following command?

  Environment: local

  Reason: {reason}

  $ {command}

› 1. Yes, proceed (y)
  2. Yes, and don't ask again for commands that start with `{command}` (p)
  3. No, and tell Codex what to do differently (esc)

  Press enter to confirm or esc to cancel
"""

SIMPLE_RESPONSE = """
• Ran {command}
  └ hello

"""

INTERRUPTED_MARKER = (
    "\n■ Conversation interrupted - tell the model what to do differently. "
    "Something went wrong? Hit `/feedback` to report the issue.\n"
)


def get_scenario_dir() -> Path:
    return Path(__file__).parent / "scenarios"


def get_builtin_scenarios() -> Dict[str, Dict[str, Any]]:
    """Built-in codex scenarios.

    Names are prefixed `cx_` so they can't collide with mock_claude's or
    mock_opencode's in the shared tests/scenarios/ directory.
    """
    return {
        "cx_launch_and_idle": {
            "name": "cx_launch_and_idle",
            "description": "Banner, idle placeholder, waits at the prompt",
            "steps": [
                {"type": "output", "text": BANNER, "delay_ms": 100},
                {"type": "output", "text": IDLE_PROMPT, "delay_ms": 50},
                {"type": "wait_for_input", "timeout_seconds": 300},
            ],
        },
        "cx_simple_response": {
            "name": "cx_simple_response",
            "description": "One completed turn (spinner + tool output), then idle",
            "steps": [
                {"type": "output", "text": BANNER, "delay_ms": 100},
                {"type": "output", "text": USER_TURN.format(
                    prompt="run: echo hello"
                ), "delay_ms": 100},
                {"type": "output", "text": BUSY_BAR, "delay_ms": 100},
                {"type": "output", "text": SIMPLE_RESPONSE.format(
                    command="echo hello"
                ), "delay_ms": 200},
                {"type": "output", "text": IDLE_PROMPT, "delay_ms": 50},
                {"type": "wait_for_input", "timeout_seconds": 300},
            ],
        },
        "cx_permission_command": {
            "name": "cx_permission_command",
            "description": "Approval dialog for a command outside the sandboxed workspace",
            "steps": [
                {"type": "output", "text": BANNER, "delay_ms": 100},
                {"type": "output", "text": USER_TURN.format(
                    prompt="touch ~/codex_probe_outside_test.txt"
                ), "delay_ms": 100},
                {"type": "output", "text": BUSY_BAR, "delay_ms": 100},
                {"type": "output", "text": PERMISSION_DIALOG.format(
                    reason="Do you want to allow creating this file in your home directory?",
                    command="touch ~/codex_probe_outside_test.txt",
                ), "delay_ms": 100},
                # Enter confirms the preselected "Yes, proceed"; Escape rejects.
                {"type": "menu", "options": [
                    "Yes, proceed",
                    "Yes, and don't ask again for commands that start with this one",
                    "No, and tell Codex what to do differently",
                ], "prompt": "", "goto_map": {
                    0: "approved", 1: "approved_always", 2: "rejected",
                }},
            ],
            "labels": {
                "approved": [
                    {"type": "output", "text": SIMPLE_RESPONSE.format(
                        command="touch ~/codex_probe_outside_test.txt"
                    ), "delay_ms": 150},
                    {"type": "output", "text": IDLE_PROMPT, "delay_ms": 50},
                    {"type": "wait_for_input", "timeout_seconds": 300},
                ],
                "approved_always": [
                    {"type": "output", "text": SIMPLE_RESPONSE.format(
                        command="touch ~/codex_probe_outside_test.txt"
                    ), "delay_ms": 150},
                    {"type": "output", "text": IDLE_PROMPT, "delay_ms": 50},
                    {"type": "wait_for_input", "timeout_seconds": 300},
                ],
                "rejected": [
                    {"type": "output", "text": INTERRUPTED_MARKER, "delay_ms": 100},
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
    parser = argparse.ArgumentParser(description="Mock codex CLI")
    parser.add_argument("--scenario", help="Scenario to run")
    parser.add_argument("--version", action="store_true",
                        help="Print a plausible codex version and exit")
    parser.add_argument("prompt", nargs="*", help="Positional prompt/subcommand (ignored)")

    # Tolerate (and ignore) every real codex flag the launcher may add —
    # -m, -a, --sandbox, --dangerously-bypass-approvals-and-sandbox, resume/
    # fork subcommand + id, extra passthrough args.
    args, _unknown = parser.parse_known_args()

    if args.version:
        print("0.150.1")
        return

    scenario_name = args.scenario or os.environ.get(
        "MOCK_SCENARIO", "cx_launch_and_idle"
    )

    try:
        scenario = load_scenario(scenario_name)
    except FileNotFoundError as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)

    ScenarioRunner(scenario).run()


if __name__ == "__main__":
    main()
