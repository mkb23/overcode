"""
Tests for status detection patterns module.

Tests the centralized pattern definitions and helper functions.
"""

import pytest

from overcode.status_patterns import (
    StatusPatterns,
    DEFAULT_PATTERNS,
    get_patterns,
    matches_any,
    find_matching_line,
    is_prompt_line,
    is_status_bar_line,
    is_command_menu_line,
    count_command_menu_lines,
    clean_line,
    extract_background_bash_count,
)


class TestStatusPatterns:
    """Tests for StatusPatterns dataclass."""

    def test_default_patterns_has_permission_patterns(self):
        """Should have permission patterns defined."""
        patterns = DEFAULT_PATTERNS
        assert len(patterns.permission_patterns) > 0
        assert "enter to confirm" in patterns.permission_patterns

    def test_default_patterns_has_active_indicators(self):
        """Should have active indicators defined."""
        patterns = DEFAULT_PATTERNS
        assert len(patterns.active_indicators) > 0
        assert "thinking" in patterns.active_indicators
        assert "✽" in patterns.active_indicators

    def test_default_patterns_has_execution_indicators(self):
        """Should have execution indicators defined."""
        patterns = DEFAULT_PATTERNS
        assert len(patterns.execution_indicators) > 0
        assert "Reading" in patterns.execution_indicators
        assert "Writing" in patterns.execution_indicators

    def test_default_patterns_has_waiting_patterns(self):
        """Should have waiting patterns defined."""
        patterns = DEFAULT_PATTERNS
        assert len(patterns.waiting_patterns) > 0
        assert "[y/n]" in patterns.waiting_patterns

    def test_default_patterns_has_prompt_chars(self):
        """Should have prompt characters defined."""
        patterns = DEFAULT_PATTERNS
        assert ">" in patterns.prompt_chars
        assert "›" in patterns.prompt_chars

    def test_default_patterns_has_line_prefixes(self):
        """Should have line prefixes defined."""
        patterns = DEFAULT_PATTERNS
        assert len(patterns.line_prefixes) > 0

    def test_get_patterns_returns_default(self):
        """get_patterns should return default patterns."""
        patterns = get_patterns()
        assert patterns is DEFAULT_PATTERNS


class TestMatchesAny:
    """Tests for matches_any function."""

    def test_matches_case_insensitive(self):
        """Should match case-insensitively by default."""
        patterns = ["hello", "world"]
        assert matches_any("HELLO there", patterns) is True
        assert matches_any("say hello", patterns) is True
        assert matches_any("goodbye", patterns) is False

    def test_matches_case_sensitive(self):
        """Should match case-sensitively when specified."""
        patterns = ["Hello", "World"]
        assert matches_any("Hello there", patterns, case_sensitive=True) is True
        assert matches_any("hello there", patterns, case_sensitive=True) is False

    def test_matches_partial(self):
        """Should match partial strings."""
        patterns = ["search"]
        assert matches_any("web searching", patterns) is True

    def test_empty_patterns(self):
        """Should return False for empty patterns list."""
        assert matches_any("anything", []) is False

    def test_empty_text(self):
        """Should return False for empty text."""
        assert matches_any("", ["pattern"]) is False


class TestFindMatchingLine:
    """Tests for find_matching_line function."""

    def test_finds_match_from_end(self):
        """Should find matching line from the end by default."""
        lines = ["first", "second match", "third match", "fourth"]
        result = find_matching_line(lines, ["match"])
        assert result == "third match"

    def test_finds_match_from_start(self):
        """Should find matching line from start when reverse=False."""
        lines = ["first match", "second", "third match"]
        result = find_matching_line(lines, ["match"], reverse=False)
        assert result == "first match"

    def test_returns_none_when_no_match(self):
        """Should return None when no match found."""
        lines = ["first", "second", "third"]
        result = find_matching_line(lines, ["nomatch"])
        assert result is None

    def test_case_sensitive_matching(self):
        """Should respect case_sensitive parameter."""
        lines = ["Reading file", "reading file"]
        result = find_matching_line(lines, ["Reading"], case_sensitive=True)
        assert result == "Reading file"

    def test_empty_lines(self):
        """Should handle empty lines list."""
        result = find_matching_line([], ["pattern"])
        assert result is None


class TestIsPromptLine:
    """Tests for is_prompt_line function."""

    def test_recognizes_angle_bracket(self):
        """Should recognize > as prompt."""
        assert is_prompt_line(">") is True
        assert is_prompt_line("  >  ") is True

    def test_recognizes_chevron(self):
        """Should recognize › as prompt."""
        assert is_prompt_line("›") is True
        assert is_prompt_line("  ›  ") is True

    def test_rejects_non_prompts(self):
        """Should reject non-prompt lines."""
        assert is_prompt_line("> some text") is False
        assert is_prompt_line("regular line") is False
        assert is_prompt_line("") is False


class TestIsStatusBarLine:
    """Tests for is_status_bar_line function."""

    def test_recognizes_status_bar(self):
        """Should recognize status bar lines."""
        assert is_status_bar_line("⏵⏵ bypass permissions on") is True
        assert is_status_bar_line("  ⏵⏵ auto-approve  ") is True

    def test_rejects_regular_lines(self):
        """Should reject regular lines."""
        assert is_status_bar_line("Reading file.txt") is False
        assert is_status_bar_line("") is False
        assert is_status_bar_line("⏺ Claude output") is False


class TestCleanLine:
    """Tests for clean_line function."""

    def test_strips_whitespace(self):
        """Should strip leading/trailing whitespace."""
        assert clean_line("  hello  ") == "hello"

    def test_removes_prefixes(self):
        """Should remove common prefixes."""
        assert clean_line("> hello") == "hello"
        assert clean_line("› hello") == "hello"
        assert clean_line("- list item") == "list item"
        assert clean_line("• bullet") == "bullet"

    def test_truncates_long_lines(self):
        """Should truncate lines over max_length."""
        long_line = "a" * 100
        result = clean_line(long_line, max_length=80)
        assert len(result) == 80
        assert result.endswith("...")

    def test_custom_max_length(self):
        """Should respect custom max_length."""
        long_line = "a" * 50
        result = clean_line(long_line, max_length=20)
        assert len(result) == 20
        assert result.endswith("...")

    def test_preserves_short_lines(self):
        """Should not truncate short lines."""
        short_line = "hello world"
        assert clean_line(short_line) == short_line


class TestCustomPatterns:
    """Tests for using custom StatusPatterns."""

    def test_custom_permission_patterns(self):
        """Should use custom permission patterns."""
        custom = StatusPatterns(permission_patterns=["custom approval"])
        assert matches_any("custom approval needed", custom.permission_patterns)
        assert not matches_any("enter to confirm", custom.permission_patterns)

    def test_custom_prompt_chars(self):
        """Should use custom prompt chars."""
        custom = StatusPatterns(prompt_chars=["$", "#"])
        assert is_prompt_line("$", custom)
        assert is_prompt_line("#", custom)
        assert not is_prompt_line(">", custom)

    def test_custom_line_prefixes(self):
        """Should use custom line prefixes."""
        custom = StatusPatterns(line_prefixes=[":: "])
        result = clean_line(":: message", custom)
        assert result == "message"


class TestIsCommandMenuLine:
    """Tests for is_command_menu_line function."""

    def test_recognizes_command_menu_lines(self):
        """Should recognize slash command menu lines."""
        assert is_command_menu_line("  /exit            Exit the REPL") is True
        assert is_command_menu_line("  /context         Visualize context") is True
        assert is_command_menu_line("  /memory          Edit Claude memory") is True
        assert is_command_menu_line("  /vim             Toggle Vim mode") is True

    def test_recognizes_hyphenated_commands(self):
        """Should recognize commands with hyphens."""
        assert is_command_menu_line("  /extra-usage     Configure usage") is True
        assert is_command_menu_line("  /release-notes   Show release notes") is True

    def test_rejects_regular_lines(self):
        """Should reject non-menu lines."""
        assert is_command_menu_line("Reading file.txt") is False
        assert is_command_menu_line("> /exit") is False  # User input, not menu
        assert is_command_menu_line("⏺ Output text") is False
        assert is_command_menu_line("") is False
        assert is_command_menu_line("/exit") is False  # No leading spaces

    def test_rejects_continuation_lines(self):
        """Should reject menu description continuation lines."""
        # These are indented descriptions that wrap to next line
        assert is_command_menu_line("                   summarization]") is False


class TestCountCommandMenuLines:
    """Tests for count_command_menu_lines function."""

    def test_counts_menu_lines(self):
        """Should count command menu lines correctly."""
        lines = [
            "───────────────────────────────────",
            "  /exit            Exit the REPL",
            "  /extra-usage     Configure usage",
            "  /context         Visualize context",
            "  /memory          Edit memory files",
        ]
        assert count_command_menu_lines(lines) == 4

    def test_returns_zero_for_no_menu(self):
        """Should return 0 when no menu lines."""
        lines = [
            "> Some user input",
            "⏺ Claude response",
            "Regular text here",
        ]
        assert count_command_menu_lines(lines) == 0

    def test_handles_empty_list(self):
        """Should handle empty list."""
        assert count_command_menu_lines([]) == 0


class TestExtractBackgroundBashCount:
    """Tests for extract_background_bash_count function."""

    def test_detects_multiple_bashes(self):
        """Should detect 'N bashes' pattern."""
        content = """Some output
⏵⏵ bypass permissions on · 2 bashes · esc to interrupt · ctrl+t to hide tasks"""
        assert extract_background_bash_count(content) == 2

        content_3 = """⏵⏵ auto-approve · 3 bashes · esc"""
        assert extract_background_bash_count(content_3) == 3

    def test_detects_single_bash(self):
        """Should detect single bash via (running) pattern."""
        content = """Some output
⏵⏵ bypass permissions on · for i in {1..300}; do echo "tick $i"; s… (running) · esc to interrupt"""
        assert extract_background_bash_count(content) == 1

    def test_returns_zero_when_no_bashes(self):
        """Should return 0 when no background bashes."""
        content = """Some output
⏵⏵ bypass permissions on · 50 files +229 -82"""
        assert extract_background_bash_count(content) == 0

    def test_returns_zero_for_empty_content(self):
        """Should return 0 for empty content."""
        assert extract_background_bash_count("") == 0

    def test_returns_zero_without_status_bar(self):
        """Should return 0 when no status bar line present."""
        content = """Just some regular output
without any status bar"""
        assert extract_background_bash_count(content) == 0

    def test_handles_multiline_content(self):
        """Should scan through multiline content to find status bar."""
        content = """⏺ Bash(sleep 60)
  ⎿  Running…

Some more output here

────────────────────────────
❯
────────────────────────────
  ⏵⏵ bypass permissions on · 2 bashes · esc to interrupt"""
        assert extract_background_bash_count(content) == 2
