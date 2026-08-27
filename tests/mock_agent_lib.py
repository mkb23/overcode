"""Shared scenario engine for the mock agent CLIs.

`mock_claude.py` grew the engine first (YAML/dict scenarios, an output/wait/
thinking/menu step vocabulary, an arrow-key menu); this module is the seam
that lets a second flavour — `mock_opencode.py` — reuse it verbatim instead
of forking 200 lines of terminal handling.

It deliberately re-exports rather than re-implements: mock_claude.py is the
contract the container E2E suite executes, so nothing here changes it. If a
third flavour ever lands, move the definitions down into this module and
have mock_claude import them back.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mock_claude import (  # noqa: E402,F401
    ScenarioRunner,
    check_for_input,
    interactive_menu,
    output_text,
    read_char,
    run_thinking,
)

__all__ = [
    "ScenarioRunner",
    "check_for_input",
    "interactive_menu",
    "output_text",
    "read_char",
    "run_thinking",
]
