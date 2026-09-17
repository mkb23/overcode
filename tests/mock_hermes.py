#!/usr/bin/env python3
"""Mock hermes CLI for integration testing.

Usage:
    MOCK_SCENARIO=hm_permission_command ./mock_hermes.py [args...]
    ./mock_hermes.py --scenario=hm_permission_command [args...]

Wired in via HERMES_COMMAND, mirroring how CLAUDE_COMMAND/OPENCODE_COMMAND/
CODEX_COMMAND/GROK_COMMAND swap in the other mocks. Scenario files live in
tests/scenarios/*.yaml; the built-ins below cover launch-idle, a completed
turn, and a dangerous-command approval dialog.

The chrome is copied from real Hermes Agent v0.21.3 classic-CLI captures —
the same ones committed under tests/fixtures_hermes_panes/ — so a detector
that passes against this mock passes against the real REPL.

Besides the chat surface, the mock answers the two non-chat invocations the
backend makes: ``--version`` (a plausible version banner) and ``plugins
enable overcode --no-allow-tool-override`` (exit 0, nothing written) —
``HermesBackend.prepare_launch`` shells out to the latter through the same
HERMES_COMMAND override, so a mocked launch never boots a real Hermes.
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mock_agent_lib import ScenarioRunner  # noqa: E402


# =============================================================================
# Hermes v0.21.3 classic-CLI chrome
#
# Three structural facts drive every pattern in backends/hermes.py:
#   * the input line is never a bare glyph — idle is "❯ <placeholder>",
#     busy swaps the whole line for "☤ ❯ … Ctrl+C cancel", a dialog for "⚠ ❯";
#   * the "☤ …" status bar sits *above* the input line and ticks while idle;
#   * the approval dialog is a numbered box answered with a digit + Enter.
# =============================================================================

BANNER = """\
╭──────────────────────────────── Hermes Agent v0.21.3 (2026.9.14) · upstream 98f758ae ────────────────────────────────╮
│  gpt-5-mini · Nous Research                                Available Tools                                         │
│  ~/probe-hermes                                            file: patch, read_file, search_files, write_file        │
│  Session: 20260917_130116_4ad1bb                           terminal: terminal                                      │
│                                                            25 tools · 58 skills · /help for commands                │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
Welcome to Hermes Agent! Type your message or /help for commands.
✦ Tip: HERMES_TIMEZONE overrides the server timezone with any IANA timezone string.
"""

RULE = "─" * 120

STATUS_BAR_IDLE = " ☤ gpt-5-mini │ ctx -- │ [░░░░░░░░░░] -- │ 0s │ ⏲ 0s"
STATUS_BAR_BUSY = " ☤ gpt-5-mini │ 0/400K │ [░░░░░░░░░░] 0% │ 8s │ ⏱ 3s"

# Fresh/idle: status bar, rule, the placeholder-bearing prompt, rule.
IDLE_PROMPT = f"\n{STATUS_BAR_IDLE}\n{RULE}\n❯ Draft a reply to the last email in my inbox\n{RULE}\n"

# In flight: kaomoji spinner, ticking status bar, the swapped-in input line.
BUSY_BAR = (
    f"\n  ( ͡° ͜ʖ ͡°) contemplating...\n{STATUS_BAR_BUSY}\n{RULE}\n"
    f"☤ ❯ msg=interrupt · /queue · /bg · /steer · Ctrl+C cancel\n{RULE}\n"
)

USER_TURN = "\n" + "─" * 40 + "\n● {prompt}\n" + "─" * 40 + "\n"

SIMPLE_RESPONSE = """
  ┊ 💻 $         {command}  0.6s
╭─ ☤ Hermes ───────────────────────────────────────────────────────────────────────────────────────────────────────────╮
finished
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
"""

# The approval dialog: a boxed numbered menu, the (ticking) countdown line,
# the status bar and the "⚠ ❯" input line.
PERMISSION_DIALOG = """
╭────────────────────────────────────────────────╮
│ ⚠️  Dangerous Command                          │
│                                                │
│ {command}
│                                                │
│ ❯ 1. Allow once                                │
│   2. Allow for this session                    │
│   3. Add to permanent allowlist                │
│   4. Deny                                      │
│                                                │
│ {description}
╰────────────────────────────────────────────────╯
  💻 {command}  (  1.6s · ↓ 270 tok)
  ↑/↓ to select, Enter to confirm  (298s)
 ☤ gpt-5-mini │ 12.3K/400K │ [░░░░░░░░░░] 3% │ ◷ 4.8s │ ↑ 56 t/s │ 11s │ ⏱ 7s
""" + RULE + "\n⚠ ❯\n" + RULE + "\n"

DENIED_RESPONSE = """
  ┊ 💻 $         {command}  2.1s [You denied this command — it did not run.]
╭─ ☤ Hermes ───────────────────────────────────────────────────────────────────────────────────────────────────────────╮
attempted
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
"""

INTERRUPTED_MARKER = """
⚡ Interrupted during API call.
 ─  ☤ Hermes  ─────────────────────────────────────────────────────────────────────────────────────────────────────────
 Operation interrupted: waiting for model response (2.8s elapsed).
 ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
"""

EXIT_HINT = """
Resume this session with:
  hermes --resume 20260917_131721_8f80ea
  hermes -c "Delete ./junkdir_a directory"
Session:        20260917_131721_8f80ea
Title:          Delete ./junkdir_a directory
Duration:       30s
Messages:       9 (3 user, 4 tool calls)
"""


def get_scenario_dir() -> Path:
    return Path(__file__).parent / "scenarios"


def get_builtin_scenarios() -> Dict[str, Dict[str, Any]]:
    """Built-in hermes scenarios.

    Names are prefixed `hm_` so they can't collide with the other mocks' in
    the shared tests/scenarios/ directory.
    """
    return {
        "hm_launch_and_idle": {
            "name": "hm_launch_and_idle",
            "description": "Banner, idle placeholder prompt, waits at the prompt",
            "steps": [
                {"type": "output", "text": BANNER, "delay_ms": 100},
                {"type": "output", "text": IDLE_PROMPT, "delay_ms": 50},
                {"type": "wait_for_input", "timeout_seconds": 300},
            ],
        },
        "hm_simple_response": {
            "name": "hm_simple_response",
            "description": "One completed turn (spinner + tool line + boxed reply), then idle",
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
        "hm_permission_command": {
            "name": "hm_permission_command",
            "description": "Dangerous-command approval box for a recursive delete",
            "steps": [
                {"type": "output", "text": BANNER, "delay_ms": 100},
                {"type": "output", "text": USER_TURN.format(
                    prompt="delete ./junkdir_a"
                ), "delay_ms": 100},
                {"type": "output", "text": BUSY_BAR, "delay_ms": 100},
                {"type": "output", "text": PERMISSION_DIALOG.format(
                    command="rm -rf ./junkdir_a",
                    description="recursive delete",
                ), "delay_ms": 100},
                # "1" + Enter allows once; "4" + Enter denies; Enter alone
                # confirms the preselected first option.
                {"type": "menu", "options": [
                    "Allow once",
                    "Allow for this session",
                    "Add to permanent allowlist",
                    "Deny",
                ], "prompt": "", "goto_map": {
                    0: "approved", 1: "approved", 2: "approved", 3: "denied",
                }},
            ],
            "labels": {
                "approved": [
                    {"type": "output", "text": SIMPLE_RESPONSE.format(
                        command="rm -rf ./junkdir_a"
                    ), "delay_ms": 150},
                    {"type": "output", "text": IDLE_PROMPT, "delay_ms": 50},
                    {"type": "wait_for_input", "timeout_seconds": 300},
                ],
                "denied": [
                    {"type": "output", "text": DENIED_RESPONSE.format(
                        command="rm -rf ./junkdir_a"
                    ), "delay_ms": 150},
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
    # Non-chat invocations the backend makes through HERMES_COMMAND.
    argv = sys.argv[1:]
    if argv[:2] == ["plugins", "enable"]:
        # `hermes plugins enable overcode --no-allow-tool-override`
        print("Plugin 'overcode' enabled.")
        return

    parser = argparse.ArgumentParser(description="Mock hermes CLI")
    parser.add_argument("--scenario", help="Scenario to run")
    parser.add_argument("--version", action="store_true",
                        help="Print a plausible hermes version banner and exit")
    parser.add_argument("prompt", nargs="*", help="Positional prompt/subcommand (ignored)")

    # Tolerate (and ignore) every real hermes flag the launcher may add —
    # --cli, --resume <id>, -m <model>, --yolo, extra passthrough args.
    args, _unknown = parser.parse_known_args()

    if args.version:
        print("Hermes Agent v0.21.3 (2026.9.14) · upstream 98f758ae")
        print("Install directory: /home/user/.hermes/hermes-agent")
        return

    scenario_name = args.scenario or os.environ.get(
        "MOCK_SCENARIO", "hm_launch_and_idle"
    )

    try:
        scenario = load_scenario(scenario_name)
    except FileNotFoundError as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)

    ScenarioRunner(scenario).run()


if __name__ == "__main__":
    main()
