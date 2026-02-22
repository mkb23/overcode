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
    extract_live_subagent_count,
    is_sleep_command,
    extract_sleep_duration,
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

    def test_handles_ansi_codes_in_status_bar(self):
        """Should detect bashes even when status bar has ANSI escape codes."""
        # Real tmux capture includes ANSI color codes around the status bar
        content = """Some output
\x1b[36m⏵⏵ bypass permissions on · 3 bashes · esc to interrupt\x1b[0m"""
        assert extract_background_bash_count(content) == 3

    def test_handles_ansi_codes_single_bash(self):
        """Should detect single bash with ANSI codes."""
        content = """\x1b[1;36m⏵⏵ auto-approve · sleep 120 (running) · esc\x1b[0m"""
        assert extract_background_bash_count(content) == 1

    def test_handles_ansi_with_leading_spaces(self):
        """Should detect bashes when ANSI codes wrap leading spaces (real-world case).

        Claude Code's status bar is rendered with ANSI reset codes before leading
        spaces, so strip() alone can't remove them — must strip_ansi first.
        """
        # Real captured line: \x1b[0m  \x1b[38;2;255;107;128m⏵⏵\x1b[39m ...
        content = """\x1b[0m  \x1b[38;2;255;107;128m⏵⏵\x1b[39m \x1b[38;2;255;107;128mbypass\x1b[39m \x1b[38;2;255;107;128mpermissions\x1b[39m \x1b[38;2;255;107;128mon\x1b[39m \x1b[38;2;153;153;153m·\x1b[39m \x1b[38;2;0;204;204m3\x1b[39m \x1b[38;2;0;204;204mbashes\x1b[39m \x1b[38;2;153;153;153m·\x1b[39m \x1b[38;2;153;153;153mctrl+t\x1b[39m"""
        assert extract_background_bash_count(content) == 3

    def test_uses_last_status_bar_line(self):
        """Should use the LAST status bar line, not the first.

        Old status bar lines persist in tmux scrollback. The current/active
        status bar is always at the bottom of the pane capture.
        """
        content = """Some output
⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt
more output here
⏵⏵ bypass permissions on · 3 bashes · esc to interrupt"""
        assert extract_background_bash_count(content) == 3


class TestExtractLiveSubagentCount:
    """Tests for extract_live_subagent_count function."""

    def test_detects_multiple_agents(self):
        """Should detect 'N local agents' pattern."""
        content = """Some output
⏵⏵ bypass permissions on · 2 local agents · esc to interrupt"""
        assert extract_live_subagent_count(content) == 2

    def test_detects_single_agent(self):
        """Should detect '1 local agent' pattern."""
        content = """⏵⏵ auto-approve · 1 local agent · esc"""
        assert extract_live_subagent_count(content) == 1

    def test_returns_zero_when_no_agents(self):
        """Should return 0 when no local agents in status bar."""
        content = """Some output
⏵⏵ bypass permissions on · 3 bashes · esc to interrupt"""
        assert extract_live_subagent_count(content) == 0

    def test_returns_zero_for_empty_content(self):
        """Should return 0 for empty content."""
        assert extract_live_subagent_count("") == 0

    def test_returns_zero_without_status_bar(self):
        """Should return 0 when no status bar present."""
        content = """Just regular output"""
        assert extract_live_subagent_count(content) == 0

    def test_handles_ansi_codes(self):
        """Should detect agents with ANSI escape codes."""
        content = """\x1b[0m  \x1b[36m⏵⏵\x1b[39m bypass permissions on · \x1b[36m2\x1b[39m \x1b[36mlocal\x1b[39m \x1b[36magents\x1b[39m · esc"""
        assert extract_live_subagent_count(content) == 2

    def test_handles_agents_and_bashes_together(self):
        """Should detect agents when both agents and bashes are present."""
        content = """⏵⏵ bypass permissions on · 2 local agents · 3 bashes · esc"""
        assert extract_live_subagent_count(content) == 2
        assert extract_background_bash_count(content) == 3

    def test_uses_last_status_bar_line(self):
        """Should use the LAST status bar line for agent count.

        Old status bar lines persist in scrollback — only the bottom one is current.
        """
        content = """⏵⏵ bypass permissions on · esc to interrupt
some output
⏵⏵ bypass permissions on · 2 local agents · esc to interrupt"""
        assert extract_live_subagent_count(content) == 2


class TestIsSleepCommand:
    """Tests for is_sleep_command function (#289)."""

    def test_basic_sleep(self):
        """Should detect 'sleep N' patterns."""
        assert is_sleep_command("sleep 30") is True
        assert is_sleep_command("sleep 300") is True
        assert is_sleep_command("sleep 1") is True

    def test_sleep_in_bash_tool_output(self):
        """Should detect sleep in Claude Code tool execution lines."""
        assert is_sleep_command('Running Bash("sleep 60")') is True
        assert is_sleep_command("Bash  sleep 300") is True
        assert is_sleep_command('⏺ Bash("sleep 120")') is True

    def test_sleep_with_suffix(self):
        """Should detect sleep with time suffixes like 30s, 5m."""
        assert is_sleep_command("sleep 30s") is True
        assert is_sleep_command("sleep 5m") is True

    def test_sleep_in_compound_commands(self):
        """Should detect sleep in 'sleep N && command' patterns."""
        assert is_sleep_command("sleep 900 && echo 'checking'") is True
        assert is_sleep_command("sleep 60 && curl http://localhost:3000") is True
        assert is_sleep_command('Bash("sleep 300 && git status")') is True
        assert is_sleep_command("sleep 120; echo done") is True

    def test_no_sleep(self):
        """Should not match non-sleep commands."""
        assert is_sleep_command("Reading config.json") is False
        assert is_sleep_command("Running pytest tests/") is False
        assert is_sleep_command("Writing output.txt") is False

    def test_sleep_in_narrative(self):
        """Should detect sleep even in narrative text."""
        assert is_sleep_command("I'll run sleep 60 to wait") is True

    def test_empty_and_whitespace(self):
        """Should handle empty/whitespace input."""
        assert is_sleep_command("") is False
        assert is_sleep_command("   ") is False


class TestExtractSleepDuration:
    """Tests for extract_sleep_duration function (#289)."""

    def test_basic_sleep(self):
        """Should extract duration from 'sleep N'."""
        assert extract_sleep_duration("sleep 30") == 30
        assert extract_sleep_duration("sleep 300") == 300
        assert extract_sleep_duration("sleep 1") == 1

    def test_sleep_in_bash_tool_output(self):
        """Should extract duration from Claude Code tool execution lines."""
        assert extract_sleep_duration('Running Bash("sleep 60")') == 60
        assert extract_sleep_duration("Bash  sleep 300") == 300
        assert extract_sleep_duration('⏺ Bash("sleep 120")') == 120

    def test_sleep_in_compound_commands(self):
        """Should extract duration from 'sleep N && command' patterns."""
        assert extract_sleep_duration("sleep 900 && echo 'checking'") == 900
        assert extract_sleep_duration("sleep 60 && curl http://localhost:3000") == 60
        assert extract_sleep_duration('Bash("sleep 300 && git status")') == 300
        assert extract_sleep_duration("sleep 120; echo done") == 120

    def test_no_sleep(self):
        """Should return None for non-sleep commands."""
        assert extract_sleep_duration("Reading config.json") is None
        assert extract_sleep_duration("Running pytest tests/") is None
        assert extract_sleep_duration("Writing output.txt") is None

    def test_empty_and_whitespace(self):
        """Should return None for empty/whitespace input."""
        assert extract_sleep_duration("") is None
        assert extract_sleep_duration("   ") is None

    def test_enriched_activity_string(self):
        """Should extract duration from enriched activity like 'Sleeping 5.0m'."""
        # The enriched activity doesn't contain the raw command, so it returns None
        assert extract_sleep_duration("Sleeping 5.0m") is None
        # But the raw pane content should work
        assert extract_sleep_duration('Running Bash("sleep 300")') == 300
