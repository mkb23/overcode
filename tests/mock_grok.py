#!/usr/bin/env python3
"""Mock grok CLI for integration testing.

Usage:
    MOCK_SCENARIO=gk_permission_command ./mock_grok.py [args...]
    ./mock_grok.py --scenario=gk_permission_command [args...]

Wired in via GROK_COMMAND, mirroring how CLAUDE_COMMAND/OPENCODE_COMMAND/
CODEX_COMMAND swap in tests/mock_claude.py, tests/mock_opencode.py and
tests/mock_codex.py. Scenario files live in tests/scenarios/*.yaml; the
built-ins below cover the four flows Phase 3 cares about (launch-idle, a
completed turn, a permission dialog, busy, and a headless model error).

The chrome is copied from real Grok Build v1.0.5 captures — the same ones
committed under tests/fixtures_grok_panes/ — so a detector that passes
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
# grok v1.0.5 chrome
#
# Three structural facts drive every pattern in backends/grok.py:
#   * the input box has no fixed-width empty-prompt string — it's matched as
#     a shape ("│ ❯" ... "│") rather than a literal placeholder;
#   * the busy/idle footer hint bar differs by exactly one segment
#     ("Esc:cancel" only appears while a turn is in flight) — that's the
#     signal GROK_PATTERNS actually keys off, not the spinner line, since the
#     footer is fixed UI chrome at the pane's trailing edge and the spinner
#     line can scroll out of the 10-line tail on a longer turn;
#   * the permission dialog replaces the input box entirely, using digit
#     keys that execute immediately with no Enter required.
# =============================================================================

TELEMETRY_BANNER = (
    "  Help improve Grok                                                     "
    "[Opt out] [Opt in]\n"
    "  Off by default. Opt-in to allow SpaceXAI to retain coding data, e.g., "
    "prompts, traces, & metrics, for training and debugging purposes. Change "
    "anytime via settings.\n"
    "  Read Terms and Privacy Policy.\n"
)

WELCOME_BANNER = """
                                     ╭──────────────────────────────────────────────────────────────────╮
                                     │  Grok Build  1.0.5                                                │
                                     │  Grok 4.6 is here, try it out for free for a limited time!         │
                                     │                                                                    │
                                     │                   New worktree                             ctrl+w  │
                                     │                   Resume session                           ctrl+s  │
                                     │                   Changelog                                        │
                                     │                   Quit                                     ctrl+q  │
                                     ╰──────────────────────────────────────────────────────────────────╯

"""

_BOX_TOP = "  ╭" + "─" * 76 + "╮\n"
_BOX_EMPTY_INPUT = "  │ ❯" + " " * 76 + "│\n"
_BOX_BOTTOM = "  ╰" + "─" * 56 + " Grok 4.6 (high) · always-approve ─╯\n"

IDLE_HINT_BAR = "  Shift+Tab:mode  │  Ctrl+x:shortcuts\n"
BUSY_HINT_BAR = "  Shift+Tab:mode  │  Esc:cancel  │  Ctrl+x:shortcuts\n"

FRESH_TAIL = "\n" + " " * 90 + "[stable]\n"


def _chrome(hint_bar: str) -> str:
    """The box + telemetry banner + footer hint bar every live pane shows."""
    return f"\n{TELEMETRY_BANNER}\n{_BOX_TOP}{_BOX_EMPTY_INPUT}{_BOX_BOTTOM}\n{hint_bar}"


# Fresh/idle: welcome banner still up, no hint bar yet (replaced by the
# right-aligned "[stable]" channel tag) — this is the state
# GrokBackend.prompt_ready_chars() actually has to detect right after spawn.
FRESH_IDLE = f"{WELCOME_BANNER}\n{TELEMETRY_BANNER}\n{_BOX_TOP}{_BOX_EMPTY_INPUT}{_BOX_BOTTOM}{FRESH_TAIL}"

# Post-interaction idle: welcome banner gone, hint bar present.
IDLE_PROMPT = _chrome(IDLE_HINT_BAR)

# In flight: the spinner line, with the dormant box + busy hint bar below it.
BUSY_BAR = (
    "\n  ⠼ Waiting for response… 0.9s" + " " * 80 + "[stop]\n"
    + _chrome(BUSY_HINT_BAR)
)

USER_TURN = "\n     ❯ {prompt}\n"

# The approval dialog replaces the input box entirely — digit keys execute
# immediately, no Enter required (confirmed live, Appendix B).
PERMISSION_DIALOG = """
  ┃
  ┃  {reason}
  ┃  {command}
  ┃
  ┃  1 (●) Yes, and don't ask again for anything (always-approve mode)
  ┃  2 (○) Yes, proceed
  ┃  3 (○) No, reject (type to add feedback)
  ┃

  1/3:select  │  Tab:next option  │  Ctrl+o:always-approve  │  Ctrl+c:cancel  │  Esc:scrollback
"""

SIMPLE_RESPONSE = "\n  ◆ {command}\n\n"

INTERRUPTED_MARKER = "\n  Turn cancelled by user in 1.0s.\n"

# error_bad_model.txt is a *headless* capture (`-p`/`--single`), not the
# interactive TUI — Phase 0 confirmed the interactive TUI silently ignores a
# bad --model id. This scenario reproduces the headless failure text and exit.
BAD_MODEL_ERROR = (
    "Couldn't set model '{model}': Invalid params: \"unknown model id\". "
    "Run 'grok models' to see available models.\n"
    "Error: Couldn't set model '{model}': Invalid params: \"unknown model id\". "
    "Run 'grok models' to see available models.\n"
)


def get_scenario_dir() -> Path:
    return Path(__file__).parent / "scenarios"


def get_builtin_scenarios() -> Dict[str, Dict[str, Any]]:
    """Built-in grok scenarios.

    Names are prefixed `gk_` so they can't collide with mock_claude's,
    mock_opencode's or mock_codex's in the shared tests/scenarios/ directory.
    """
    return {
        "gk_launch_and_idle": {
            "name": "gk_launch_and_idle",
            "description": "Welcome banner, fresh idle box, waits at the prompt",
            "steps": [
                {"type": "output", "text": FRESH_IDLE, "delay_ms": 100},
                {"type": "wait_for_input", "timeout_seconds": 300},
            ],
        },
        "gk_simple_response": {
            "name": "gk_simple_response",
            "description": "One completed turn (spinner + tool output), then idle",
            "steps": [
                {"type": "output", "text": USER_TURN.format(
                    prompt="run: echo hello"
                ), "delay_ms": 100},
                {"type": "output", "text": BUSY_BAR, "delay_ms": 100},
                {"type": "output", "text": SIMPLE_RESPONSE.format(
                    command="Ran echo hello"
                ), "delay_ms": 200},
                {"type": "output", "text": IDLE_PROMPT, "delay_ms": 50},
                {"type": "wait_for_input", "timeout_seconds": 300},
            ],
        },
        "gk_permission_command": {
            "name": "gk_permission_command",
            "description": "Approval dialog for a shell command under normal mode",
            "steps": [
                {"type": "output", "text": USER_TURN.format(
                    prompt="Run the command: echo hello"
                ), "delay_ms": 100},
                {"type": "output", "text": BUSY_BAR, "delay_ms": 100},
                {"type": "output", "text": PERMISSION_DIALOG.format(
                    reason="Print hello to stdout",
                    command="echo hello",
                ), "delay_ms": 100},
                # Digit keys execute immediately — no Enter, no arrow nav.
                {"type": "menu", "options": [
                    "Yes, and don't ask again for anything (always-approve mode)",
                    "Yes, proceed",
                    "No, reject",
                ], "prompt": "", "goto_map": {
                    0: "approved_always", 1: "approved", 2: "rejected",
                }},
            ],
            "labels": {
                "approved": [
                    {"type": "output", "text": SIMPLE_RESPONSE.format(
                        command="Ran echo hello"
                    ), "delay_ms": 150},
                    {"type": "output", "text": IDLE_PROMPT, "delay_ms": 50},
                    {"type": "wait_for_input", "timeout_seconds": 300},
                ],
                "approved_always": [
                    {"type": "output", "text": SIMPLE_RESPONSE.format(
                        command="Ran echo hello"
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
        "gk_error_bad_model": {
            "name": "gk_error_bad_model",
            "description": "Headless (-p) run with an unknown --model id",
            "steps": [
                {"type": "output", "text": BAD_MODEL_ERROR.format(
                    model="totally-bogus-model-id-123"
                ), "delay_ms": 50},
                {"type": "exit", "code": 1},
            ],
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
    parser = argparse.ArgumentParser(description="Mock grok CLI")
    parser.add_argument("--scenario", help="Scenario to run")
    parser.add_argument("--version", action="store_true",
                        help="Print a plausible grok version and exit")
    parser.add_argument("prompt", nargs="*", help="Positional prompt (ignored)")

    # Tolerate (and ignore) every real grok flag the launcher may add —
    # -m/--model, --agent, --permission-mode, --allow (repeatable),
    # --resume, --fork-session, --session-id, --fullscreen, extra passthrough.
    args, _unknown = parser.parse_known_args()

    if args.version:
        print("1.0.5")
        return

    scenario_name = args.scenario or os.environ.get(
        "MOCK_SCENARIO", "gk_launch_and_idle"
    )

    try:
        scenario = load_scenario(scenario_name)
    except FileNotFoundError as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)

    ScenarioRunner(scenario).run()


if __name__ == "__main__":
    main()
