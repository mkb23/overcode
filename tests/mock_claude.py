#!/usr/bin/env python3
"""
Mock Claude Code CLI for integration testing.

Usage:
    MOCK_SCENARIO=permission_bash ./mock_claude.py [args...]
    ./mock_claude.py --scenario=permission_bash [args...]

Scenarios are defined in tests/scenarios/*.yaml files.
"""

import os
import sys
import time
import yaml
import select
import argparse
import random
from pathlib import Path
from typing import Optional, Dict, Any, List


# Claude Code's thinking verbs (randomly chosen during display)
# Source: https://github.com/levindixon/tengu_spinner_words
THINKING_VERBS = [
    "Accomplishing", "Actioning", "Actualizing", "Baking", "Booping",
    "Brewing", "Calculating", "Cerebrating", "Channelling", "Churning",
    "Clauding", "Coalescing", "Cogitating", "Combobulating", "Computing",
    "Concocting", "Conjuring", "Considering", "Contemplating", "Cooking",
    "Crafting", "Creating", "Crunching", "Deciphering", "Deliberating",
    "Determining", "Discombobulating", "Divining", "Doing", "Effecting",
    "Elucidating", "Enchanting", "Envisioning", "Finagling", "Flibbertigibbeting",
    "Forging", "Forming", "Frolicking", "Generating", "Germinating",
    "Hatching", "Herding", "Honking", "Hustling", "Ideating",
    "Imagining", "Incubating", "Inferring", "Jiving", "Manifesting",
    "Marinating", "Meandering", "Moseying", "Mulling", "Mustering",
    "Musing", "Noodling", "Percolating", "Perusing", "Philosophising",
    "Pondering", "Pontificating", "Processing", "Puttering", "Puzzling",
    "Reticulating", "Ruminating", "Scheming", "Schlepping", "Shimmying",
    "Shucking", "Simmering", "Smooshing", "Spelunking", "Spinning",
    "Stewing", "Sussing", "Synthesizing", "Thinking", "Tinkering",
    "Transmuting", "Unfurling", "Unravelling", "Vibing", "Wandering",
    "Whirring", "Wibbling", "Wizarding", "Working", "Wrangling",
]


def get_scenario_dir() -> Path:
    """Get the scenarios directory."""
    return Path(__file__).parent / "scenarios"


def load_scenario(name: str) -> Dict[str, Any]:
    """Load a scenario from YAML file."""
    scenario_file = get_scenario_dir() / f"{name}.yaml"
    if not scenario_file.exists():
        # Try built-in scenarios
        scenarios = get_builtin_scenarios()
        if name in scenarios:
            return scenarios[name]
        raise FileNotFoundError(f"Scenario not found: {name}")

    with open(scenario_file) as f:
        return yaml.safe_load(f)


def get_builtin_scenarios() -> Dict[str, Dict]:
    """Return built-in scenarios for basic testing.

    All scenarios use realistic Claude Code v2.0.75 output format
    based on actual captures in tests/fixtures_realistic.py.
    """
    return {
        "startup_idle": {
            "name": "startup_idle",
            "description": "Shows welcome banner, waits at empty prompt",
            "steps": [
                {"type": "output", "text": WELCOME_BANNER, "delay_ms": 100},
                {"type": "output", "text": EMPTY_PROMPT, "delay_ms": 50},
                {"type": "wait_for_input", "timeout_seconds": 300},
            ]
        },
        "task_complete": {
            "name": "task_complete",
            "description": "Completes a realistic task (adding tests) and waits for next instruction",
            "steps": [
                {"type": "output", "text": WELCOME_BANNER, "delay_ms": 100},
                {"type": "output", "text": TASK_COMPLETE_REALISTIC, "delay_ms": 100},
                {"type": "wait_for_input", "timeout_seconds": 300},
            ]
        },
        "task_running": {
            "name": "task_running",
            "description": "Shows continuous thinking state, never completes",
            "steps": [
                {"type": "output", "text": WELCOME_BANNER, "delay_ms": 100},
                {"type": "output", "text": "\n> Analyze the codebase and suggest improvements\n\n", "delay_ms": 100},
                {"type": "output", "text": "⏺ I'll analyze the codebase structure and look for improvement opportunities.\n\n", "delay_ms": 200},
                {"type": "output", "text": TOOL_READ, "delay_ms": 300},
                # No task specified = random thinking verb (e.g., "Combobulating…")
                {"type": "thinking", "duration_seconds": 300},
            ]
        },
        "permission_bash": {
            "name": "permission_bash",
            "description": "Shows bash permission prompt (Claude Code v2 menu format)",
            "steps": [
                {"type": "output", "text": WELCOME_BANNER, "delay_ms": 100},
                {"type": "output", "text": f"\n{THINKING}\n", "delay_ms": 200},
                {"type": "output", "text": "\n⏺ I need to run a command to complete this task.\n\n", "delay_ms": 100},
                {"type": "output", "text": PERMISSION_PROMPT_HEADER, "delay_ms": 100},
                # Interactive menu with arrow key navigation
                {"type": "menu", "options": [
                    "Yes",
                    "Yes, and don't ask again for Bash commands",
                    "No, and tell Claude what to do differently (esc)"
                ], "prompt": "Do you want to proceed?", "goto_map": {0: "approved", 1: "approved_always", 2: "denied"}},
            ],
            "labels": {
                "approved": [
                    {"type": "output", "text": "\n", "delay_ms": 50},
                    {"type": "output", "text": "⏺ Bash(\"rm -rf /tmp/test-data\")\n", "delay_ms": 100},
                    {"type": "output", "text": "  ⎿  Command executed successfully\n\n", "delay_ms": 200},
                    {"type": "output", "text": "⏺ Done! The command completed successfully.\n", "delay_ms": 100},
                    {"type": "output", "text": EMPTY_PROMPT, "delay_ms": 100},
                    {"type": "wait_for_input", "timeout_seconds": 300},
                ],
                "approved_always": [
                    {"type": "output", "text": "\n", "delay_ms": 50},
                    {"type": "output", "text": "⏺ Bash(\"rm -rf /tmp/test-data\")\n", "delay_ms": 100},
                    {"type": "output", "text": "  ⎿  Command executed successfully\n\n", "delay_ms": 200},
                    {"type": "output", "text": "⏺ Done! (Auto-approve enabled for Bash commands)\n", "delay_ms": 100},
                    {"type": "output", "text": EMPTY_PROMPT, "delay_ms": 100},
                    {"type": "wait_for_input", "timeout_seconds": 300},
                ],
                "denied": [
                    {"type": "output", "text": "\n", "delay_ms": 50},
                    {"type": "output", "text": "⏺ Understood. I'll find another approach that doesn't require\n", "delay_ms": 100},
                    {"type": "output", "text": "  running that command.\n", "delay_ms": 100},
                    {"type": "output", "text": EMPTY_PROMPT, "delay_ms": 100},
                    {"type": "wait_for_input", "timeout_seconds": 300},
                ]
            }
        },
        "task_then_wait": {
            "name": "task_then_wait",
            "description": "Complete task, wait, respond to feedback, complete second task",
            "steps": [
                {"type": "output", "text": WELCOME_BANNER, "delay_ms": 100},
                {"type": "output", "text": TASK_PHASE_ONE, "delay_ms": 100},
                {"type": "wait_for_input", "timeout_seconds": 120},
                {"type": "output", "text": TASK_PHASE_TWO, "delay_ms": 100},
                {"type": "wait_for_input", "timeout_seconds": 300},
            ]
        },
        "crash_mid_task": {
            "name": "crash_mid_task",
            "description": "Starts task then crashes",
            "steps": [
                {"type": "output", "text": WELCOME_BANNER, "delay_ms": 100},
                {"type": "output", "text": f"\n{THINKING}\n", "delay_ms": 200},
                {"type": "output", "text": "\n⏺ Starting the task...\n", "delay_ms": 200},
                {"type": "output", "text": TOOL_READ, "delay_ms": 300},
                {"type": "exit", "code": 1, "message": "Error: Simulated crash\n"},
            ]
        },
    }


# =============================================================================
# Claude Code v2.0.75 realistic output templates
# Based on actual captures in tests/fixtures_realistic.py
# =============================================================================

WELCOME_BANNER = """\
╭─── Claude Code v2.0.75 ──────────────────────────────────────────────────────╮
│                                                    │ Recent activity         │
│                 Welcome back!                      │                         │
│                                                    │ No recent activity      │
│                     * ▐▛███▜▌ *                    │                         │
│                    * ▝▜█████▛▘ *                   │ ─────────────────────── │
│                     *  ▘▘ ▝▝  *                    │ What's new              │
│                                                    │ Mock Claude for testing │
│     Opus 4.5 · Claude Max                          │                         │
│             ~/test-project                         │                         │
╰──────────────────────────────────────────────────────────────────────────────╯
"""

# Horizontal separator used in Claude Code UI
SEPARATOR = "────────────────────────────────────────────────────────────────────────────────"

# Empty prompt format (waiting for user input)
EMPTY_PROMPT = f"""
{SEPARATOR}
>
{SEPARATOR}
  ? for shortcuts
"""

# Permission prompt header (before interactive menu)
PERMISSION_PROMPT_HEADER = f"""\
⏺ Bash("rm -rf /tmp/test-data")

{SEPARATOR}
 Tool use

   Bash("rm -rf /tmp/test-data")
   Claude wants to run: rm -rf /tmp/test-data

"""

# Full permission prompt (for static output / status detection testing)
PERMISSION_PROMPT = f"""\
⏺ Bash("rm -rf /tmp/test-data")

{SEPARATOR}
 Tool use

   Bash("rm -rf /tmp/test-data")
   Claude wants to run: rm -rf /tmp/test-data

 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and don't ask again for Bash commands
   3. No, and tell Claude what to do differently (esc)
"""

# Task complete output format (simple)
TASK_COMPLETE_OUTPUT = f"""\
⏺ I've completed the task successfully.

{SEPARATOR}
>
{SEPARATOR}
  ? for shortcuts
"""

# Realistic task completion - adding unit tests to a module
# Based on actual Claude Code output patterns from fixtures_realistic.py
TASK_COMPLETE_REALISTIC = f"""\

> Add unit tests for the validator module

⏺ I'll analyze the validator module and create comprehensive unit tests.

⏺ Read(src/validator.py)
  ⎿  Read 89 lines from src/validator.py

⏺ The validator has three main functions. Let me create tests for each.

⏺ Write(tests/unit/test_validator.py)
  ⎿  Wrote 156 lines to tests/unit/test_validator.py

⏺ Bash("python -m pytest tests/unit/test_validator.py -v")
  ⎿  15 passed in 0.34s

⏺ Created unit tests for the validator module:
  - validate_email: 6 test cases covering valid/invalid formats
  - validate_phone: 5 test cases for US and international formats
  - sanitize_input: 4 test cases for XSS and SQL injection prevention

  All 15 tests pass. Let me know if you'd like me to add more edge cases
  or integration tests with the form handler.

{SEPARATOR}
>
{SEPARATOR}
  ? for shortcuts
"""

# Two-phase task: refactor code, get feedback, add error handling
# Phase 1: Initial refactor, ask for feedback
TASK_PHASE_ONE = f"""\

> Refactor the database connection code to use connection pooling

⏺ I'll analyze the current database code and refactor it to use connection pooling.

⏺ Read(src/db/connection.py)
  ⎿  Read 67 lines from src/db/connection.py

⏺ Read(src/db/queries.py)
  ⎿  Read 124 lines from src/db/queries.py

⏺ I see the issue - each query creates a new connection. Let me refactor to use
  a connection pool.

⏺ Edit(src/db/connection.py)
  ⎿  Updated src/db/connection.py (4 edits)

⏺ Edit(src/db/queries.py)
  ⎿  Updated src/db/queries.py (8 edits)

⏺ Refactored the database code to use connection pooling:
  - Added ConnectionPool class with configurable pool size
  - Updated all query functions to use pool.get_connection()
  - Connections are now returned to pool after use

  This should significantly reduce connection overhead. Want me to also add
  retry logic for transient failures, or run the existing tests first?

{SEPARATOR}
>
{SEPARATOR}
  ? for shortcuts
"""

# Phase 2: User asked to run tests, Claude does and finds/fixes an issue
TASK_PHASE_TWO = f"""\

⏺ Good idea, let me run the tests first.

⏺ Bash("python -m pytest tests/db/ -v")
  ⎿  14 passed, 2 failed in 1.23s

⏺ Two tests failed due to connection timeout handling. Let me fix that.

⏺ Edit(src/db/connection.py)
  ⎿  Updated src/db/connection.py (2 edits)

⏺ Bash("python -m pytest tests/db/ -v")
  ⎿  16 passed in 0.98s

⏺ Fixed the failing tests - the pool wasn't handling connection timeouts
  correctly. All 16 tests now pass, and the average test time improved
  from 1.23s to 0.98s due to connection reuse.

{SEPARATOR}
>
{SEPARATOR}
  ? for shortcuts
"""

# Tool execution format
TOOL_READ = """\
⏺ Read(src/main.py)
  ⎿  Read 50 lines from src/main.py
"""

# Thinking indicator (matches real Claude Code)
# Uses single-char ellipsis (…) not three dots (...)
THINKING = "  ✽ Thinking…"


def output_text(text: str, char_delay: float = 0.0, line_delay: float = 0.0):
    """Output text with optional timing."""
    if char_delay > 0 or line_delay > 0:
        for line in text.split('\n'):
            for char in line:
                sys.stdout.write(char)
                sys.stdout.flush()
                if char_delay > 0:
                    time.sleep(char_delay)
            sys.stdout.write('\n')
            sys.stdout.flush()
            if line_delay > 0:
                time.sleep(line_delay)
    else:
        sys.stdout.write(text)
        sys.stdout.flush()


def check_for_input(timeout: float = 0.1) -> Optional[str]:
    """Check if input available on stdin."""
    try:
        readable, _, _ = select.select([sys.stdin], [], [], timeout)
        if readable:
            return sys.stdin.readline().strip()
    except (ValueError, OSError):
        # stdin might not be selectable in some contexts
        pass
    return None


def read_char(timeout: float = 0.1) -> Optional[str]:
    """Read a single character from stdin with timeout."""
    import termios
    import tty

    try:
        readable, _, _ = select.select([sys.stdin], [], [], timeout)
        if not readable:
            return None

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)

            # Handle escape sequences (arrow keys)
            if ch == '\x1b':
                # Check for more chars (arrow key sequence)
                readable, _, _ = select.select([sys.stdin], [], [], 0.05)
                if readable:
                    ch += sys.stdin.read(2)

            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    except (ValueError, OSError, termios.error):
        return None


def interactive_menu(options: List[str], prompt: str = "") -> int:
    """Display an interactive menu with arrow key navigation.

    Returns the selected index (0-based), or -1 if cancelled.
    """
    import termios
    import tty

    selected = 0
    num_options = len(options)

    fd = sys.stdin.fileno()
    try:
        old_settings = termios.tcgetattr(fd)
    except termios.error:
        # Not a terminal, just wait for line input
        if prompt:
            sys.stdout.write(f" {prompt}\n")
        for i, option in enumerate(options):
            marker = "❯" if i == 0 else " "
            sys.stdout.write(f" {marker} {i + 1}. {option}\n")
        sys.stdout.flush()

        line = sys.stdin.readline().strip()
        if line in ['1', 'y', 'Y']:
            return 0
        elif line in ['2']:
            return 1
        elif line in ['3', 'n', 'N', '']:
            return 2
        return -1

    def render_menu():
        """Render the menu with current selection."""
        # Move cursor up to redraw menu
        sys.stdout.write(f"\033[{num_options}A")  # Move up

        for i, option in enumerate(options):
            if i == selected:
                sys.stdout.write(f"\r ❯ {i + 1}. {option}\033[K\n")
            else:
                sys.stdout.write(f"\r   {i + 1}. {option}\033[K\n")
        sys.stdout.flush()

    # Initial render
    if prompt:
        sys.stdout.write(f" {prompt}\n")
    for i, option in enumerate(options):
        if i == selected:
            sys.stdout.write(f" ❯ {i + 1}. {option}\n")
        else:
            sys.stdout.write(f"   {i + 1}. {option}\n")
    sys.stdout.flush()

    try:
        # Set terminal to raw mode for character-by-character input
        tty.setraw(fd)

        while True:
            # Read one character
            ch = sys.stdin.read(1)

            # Handle escape sequences (arrow keys, etc.)
            if ch == '\x1b':
                # Read the next two characters for arrow keys
                # In raw mode, they should be available immediately
                next1 = sys.stdin.read(1)
                if next1 == '[':
                    next2 = sys.stdin.read(1)
                    if next2 == 'A':  # Up arrow
                        selected = (selected - 1) % num_options
                        render_menu()
                        continue
                    elif next2 == 'B':  # Down arrow
                        selected = (selected + 1) % num_options
                        render_menu()
                        continue
                # Just escape key alone or unknown sequence - cancel
                return 2  # "No" option

            # Handle enter
            elif ch in ['\r', '\n']:
                return selected

            # Handle number keys
            elif ch == '1':
                return 0
            elif ch == '2':
                return 1
            elif ch == '3':
                return 2

            # Handle y/n
            elif ch in ['y', 'Y']:
                return 0
            elif ch in ['n', 'N']:
                return 2

            # Handle q or Ctrl+C
            elif ch in ['q', '\x03']:
                return -1

    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        sys.stdout.write("\n")
        sys.stdout.flush()


def run_thinking(duration_seconds: float, task: str = None):
    """Simulate Claude 'thinking' state with animated spinner.

    Claude Code cycles through: · ✢ ✳ ∗ ✻ ✽
    Source: https://daveschumaker.net/digging-into-the-claude-code-source-saved-by-sublime-text/

    Uses random verb from THINKING_VERBS list + single-char ellipsis (…)
    Source: https://github.com/levindixon/tengu_spinner_words

    Real Claude Code format:
      ✽ Combobulating…

      Esc to interrupt
    """
    # Pick a random thinking verb if not specified
    if task is None:
        task = f"{random.choice(THINKING_VERBS)}…"

    # Claude Code's actual spinner frames
    spinner_frames = ['·', '✢', '✳', '∗', '✻', '✽']
    frame_delay = 0.12  # ~8 fps, feels smooth

    # Set up the static "Esc to interrupt" two lines below
    # Format: \n[spinner line]\n\n  Esc to interrupt
    # Then cursor stays on spinner line to animate
    sys.stdout.write(f"\n  {spinner_frames[0]} {task}\n\n  Esc to interrupt")
    sys.stdout.write("\033[2A")  # Move cursor up 2 lines to spinner line
    sys.stdout.flush()

    start = time.time()
    frame_idx = 0

    while time.time() - start < duration_seconds:
        # Render current frame (overwrite in place)
        frame = spinner_frames[frame_idx % len(spinner_frames)]
        sys.stdout.write(f"\r  {frame} {task}")
        sys.stdout.flush()

        frame_idx += 1
        time.sleep(frame_delay)

        # Check for interrupt (non-blocking)
        input_text = check_for_input(0.0)
        if input_text:
            # Move to end and clean up
            sys.stdout.write("\033[2B\n")  # Move down past "Esc to interrupt"
            sys.stdout.flush()
            return input_text

    # Move to end
    sys.stdout.write("\033[2B\n")
    sys.stdout.flush()
    return None


class ScenarioRunner:
    """Runs a mock scenario."""

    def __init__(self, scenario: Dict[str, Any]):
        self.scenario = scenario
        self.steps = scenario.get("steps", [])
        self.labels = scenario.get("labels", {})
        self.current_step = 0

    def run(self):
        """Execute the scenario."""
        while self.current_step < len(self.steps):
            step = self.steps[self.current_step]
            result = self.execute_step(step)

            if result == "exit":
                return
            elif result and result.startswith("goto:"):
                label = result[5:]
                if label in self.labels:
                    self.steps = self.labels[label]
                    self.current_step = 0
                    continue

            self.current_step += 1

    def execute_step(self, step: Dict) -> Optional[str]:
        """Execute a single step."""
        step_type = step.get("type")

        if step_type == "output":
            delay_ms = step.get("delay_ms", 0)
            time.sleep(delay_ms / 1000.0)
            output_text(step["text"])

        elif step_type == "wait_for_input":
            timeout = step.get("timeout_seconds", 60)
            start = time.time()
            while time.time() - start < timeout:
                input_text = check_for_input(0.5)
                if input_text:
                    # Check for pattern matching in next steps
                    for future_step in self.steps[self.current_step + 1:]:
                        if future_step.get("type") == "on_input":
                            import re
                            if re.match(future_step.get("match", ".*"), input_text):
                                return f"goto:{future_step.get('goto')}"
                    return None
            return None

        elif step_type == "thinking":
            # Animated thinking spinner (matches real Claude Code)
            duration = step.get("duration_seconds", 10)
            task = step.get("task")  # None = random verb
            run_thinking(duration, task)

        elif step_type == "exit":
            message = step.get("message", "")
            if message:
                sys.stderr.write(message)
            sys.exit(step.get("code", 0))

        elif step_type == "menu":
            # Interactive menu with arrow key navigation
            options = step.get("options", [])
            prompt = step.get("prompt", "")
            goto_map = step.get("goto_map", {})

            selected = interactive_menu(options, prompt)

            if selected >= 0 and selected in goto_map:
                return f"goto:{goto_map[selected]}"
            elif selected == -1:
                # Cancelled
                return None

        elif step_type == "on_input":
            # Handled in wait_for_input
            pass

        return None


def main():
    parser = argparse.ArgumentParser(description="Mock Claude Code CLI")
    parser.add_argument("--scenario", "-s", help="Scenario to run")
    parser.add_argument("--print", "-p", help="Print message and exit")
    parser.add_argument("prompt", nargs="*", help="Initial prompt (ignored)")

    args = parser.parse_args()

    # Handle --print for simple testing
    if args.print:
        print(args.print)
        return

    # Get scenario from args or environment
    scenario_name = args.scenario or os.environ.get("MOCK_SCENARIO", "startup_idle")

    try:
        scenario = load_scenario(scenario_name)
    except FileNotFoundError as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)

    runner = ScenarioRunner(scenario)
    runner.run()


if __name__ == "__main__":
    main()
