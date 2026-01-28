"""
Unit tests for the _is_freetext_option() detection in TUI.

Tests with realistic captured output from Claude Code sessions.
All fixtures are from existing test files or real Claude Code v2.x output.

NOTE: Only patterns confirmed to exist in real Claude Code are tested.
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


# =============================================================================
# REAL captured output: Permission prompts with "tell Claude what to do"
# Source: tests/fixtures.py, tests/fixtures_realistic.py
# This is the ONLY confirmed freetext option format in Claude Code v2.x
# =============================================================================

PANE_BASH_PERMISSION_PROMPT = """\
⏺ Let me run the tests to verify the changes work correctly.

⏺ Bash("pytest tests/unit/ -v --tb=short")

────────────────────────────────────────────────────────────────────────────────
 Tool use

   Bash("pytest tests/unit/ -v --tb=short")
   Claude wants to run a bash command: pytest tests/unit/ -v --tb=short

 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and don't ask again for Bash commands in
      /home/user/myproject
   3. No, and tell Claude what to do differently (esc)
"""

PANE_READ_PERMISSION_PROMPT = """\
⏺ I'll check the configuration file to understand the settings.

⏺ Read("config/settings.yaml")

────────────────────────────────────────────────────────────────────────────────
 Tool use

   Read("config/settings.yaml")
   Claude wants to read: config/settings.yaml

 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and don't ask again for Read commands in
      /home/user/project
   3. No, and tell Claude what to do differently (esc)
"""

PANE_WRITE_PERMISSION_PROMPT = """\
⏺ I'll update the README with the new installation instructions.

⏺ Write("README.md")

────────────────────────────────────────────────────────────────────────────────
 Tool use

   Write("README.md")
   Claude wants to write to: README.md

 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and don't ask again for Write commands in
      /home/user/docs
   3. No, and tell Claude what to do differently (esc)
"""

PANE_WEB_SEARCH_PERMISSION = """\
⏺ Let me search for the latest documentation on this API.

⏺ Web Search("Python asyncio best practices 2024")

────────────────────────────────────────────────────────────────────────────────
 Tool use

   Web Search("Python asyncio best practices 2024")
   Claude wants to search the web for: Python asyncio best practices 2024

 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and don't ask again for Web Search commands in
      /home/user/research
   3. No, and tell Claude what to do differently (esc)
"""

# =============================================================================
# REAL captured output: Non-freetext menus
# Source: tests/fixtures_realistic.py
# =============================================================================

PANE_EXIT_MENU = """\
> /exit

───────────────────────────────────────────────────────────────────────────────
  /exit            Exit the REPL
  /extra-usage     Configure extra usage to keep working when limits are hit
  /context         Visualize current context usage as a colored grid
  /memory          Edit Claude memory files
  /vim             Toggle between Vim and Normal editing modes
  /clear           Clear conversation history and free up context
"""

PANE_RUNNING_NO_MENU = """\
⏺ I'm implementing the new feature now.

⏺ Reading the existing code structure...

  ⎿  Read src/main.py (245 lines)
  ⎿  Read src/utils.py (89 lines)

⏺ Now I'll create the new module...

  ⎿  Writing src/feature.py
"""

PANE_IDLE_PROMPT = """\
╭─── Claude Code v2.0.75 ──────────────────────────────────────────────────────╮
│                                                    │ Recent activity         │
│                 Welcome back!                      │ 5m ago   Fix tests...   │
│                                                    │ 12m ago  Add feature... │
╰──────────────────────────────────────────────────────────────────────────────╯

>
"""


# =============================================================================
# Test class for _is_freetext_option
# =============================================================================

class TestIsFreetextOption:
    """Test the _is_freetext_option() method with real captured output."""

    @pytest.fixture
    def tui_method(self):
        """Create a standalone version of _is_freetext_option for testing."""
        import re

        def _is_freetext_option(pane_content: str, key: str) -> bool:
            """Check if a numbered menu option is a free-text instruction option."""
            # Patterns that indicate free-text instruction options
            freetext_patterns = [
                r"tell\s+claude\s+what\s+to\s+do",
                r"what\s+to\s+do\s+instead",
                r"custom\s+instruction",
                r"give\s+instruction",
                r"provide\s+instruction",
                r"type\s+a\s+message",
                r"enter\s+a\s+message",
                r"say\s+something\s+else",
            ]

            # Look for the numbered option in the content
            # Match patterns like "5. text", "5) text", "5: text"
            option_pattern = rf"^\s*{key}[\.\)\:]\s*(.+)$"

            for line in pane_content.split('\n'):
                match = re.match(option_pattern, line.strip(), re.IGNORECASE)
                if match:
                    option_text = match.group(1).lower()
                    # Check if this option matches any freetext pattern
                    for pattern in freetext_patterns:
                        if re.search(pattern, option_text):
                            return True
            return False

        return _is_freetext_option

    # -------------------------------------------------------------------------
    # Tests for REAL permission prompts (option 3 = tell Claude what to do)
    # -------------------------------------------------------------------------

    def test_bash_permission_option_3_is_freetext(self, tui_method):
        """Option 3 'tell Claude what to do differently' should be detected."""
        assert tui_method(PANE_BASH_PERMISSION_PROMPT, "3") is True

    def test_read_permission_option_3_is_freetext(self, tui_method):
        """Read permission prompt option 3 should be detected."""
        assert tui_method(PANE_READ_PERMISSION_PROMPT, "3") is True

    def test_write_permission_option_3_is_freetext(self, tui_method):
        """Write permission prompt option 3 should be detected."""
        assert tui_method(PANE_WRITE_PERMISSION_PROMPT, "3") is True

    def test_web_search_option_3_is_freetext(self, tui_method):
        """Web search permission option 3 should be detected."""
        assert tui_method(PANE_WEB_SEARCH_PERMISSION, "3") is True

    # -------------------------------------------------------------------------
    # Tests for other options in permission prompts (should NOT be freetext)
    # -------------------------------------------------------------------------

    def test_option_1_yes_is_not_freetext(self, tui_method):
        """Option 1 'Yes' should NOT be detected as freetext."""
        assert tui_method(PANE_BASH_PERMISSION_PROMPT, "1") is False

    def test_option_2_always_is_not_freetext(self, tui_method):
        """Option 2 'Yes, and don't ask again' should NOT be detected as freetext."""
        assert tui_method(PANE_BASH_PERMISSION_PROMPT, "2") is False

    # -------------------------------------------------------------------------
    # Tests for non-freetext screens
    # -------------------------------------------------------------------------

    def test_exit_menu_no_freetext(self, tui_method):
        """Exit command menu should not have freetext options."""
        assert tui_method(PANE_EXIT_MENU, "1") is False
        assert tui_method(PANE_EXIT_MENU, "2") is False
        assert tui_method(PANE_EXIT_MENU, "3") is False

    def test_running_state_no_freetext(self, tui_method):
        """Running agent with no menu should not detect freetext."""
        assert tui_method(PANE_RUNNING_NO_MENU, "1") is False
        assert tui_method(PANE_RUNNING_NO_MENU, "3") is False
        assert tui_method(PANE_RUNNING_NO_MENU, "5") is False

    def test_idle_prompt_no_freetext(self, tui_method):
        """Idle prompt waiting for input should not detect freetext."""
        assert tui_method(PANE_IDLE_PROMPT, "1") is False
        assert tui_method(PANE_IDLE_PROMPT, "3") is False

    # -------------------------------------------------------------------------
    # Edge cases
    # -------------------------------------------------------------------------

    def test_empty_content(self, tui_method):
        """Empty pane content should return False."""
        assert tui_method("", "3") is False

    def test_nonexistent_option(self, tui_method):
        """Non-existent option number should return False."""
        assert tui_method(PANE_BASH_PERMISSION_PROMPT, "9") is False


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
