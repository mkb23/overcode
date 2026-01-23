"""
Centralized status detection patterns.

This module contains all the pattern lists used by StatusDetector to identify
Claude's current state. Centralizing these makes them:
- Easier to maintain and extend
- Testable in isolation
- Potentially configurable via config file in the future

Each pattern set includes documentation about when it's used and what it matches.
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class StatusPatterns:
    """All patterns used for status detection.

    Patterns are case-insensitive unless noted otherwise.
    """

    # Permission/confirmation prompts - HIGHEST priority
    # These indicate Claude needs user approval before proceeding.
    # Matched against the last few lines of output (lowercased).
    permission_patterns: List[str] = field(default_factory=lambda: [
        "enter to confirm",
        "esc to reject",
        # Note: removed "approve" - too broad, matches "auto-approve" in status bar
        # Note: removed "permission" - too broad, matches "bypass permissions" in status bar
        "allow this",
        # Claude Code v2 permission dialog format
        "do you want to proceed",
        "❯ 1. yes",  # Menu selector on first option
        "tell claude what to do differently",  # Option 3 text
    ])

    # Active work indicators - checked when content hasn't changed
    # These indicate Claude is busy even if the prompt appears visible.
    # Matched against the last few lines of output (lowercased).
    active_indicators: List[str] = field(default_factory=lambda: [
        "web search",
        "searching",
        "fetching",
        "esc to interrupt",  # Shows active operation in progress
        "thinking",
        "✽",  # Spinner character
        # Fun thinking indicators from Claude Code
        "razzmatazzing",
        "fiddle-faddling",
        "pondering",
        "cogitating",
        # Note: removed "tokens" - too broad, matches normal text
        # The spinner ✽ and "esc to interrupt" are sufficient
    ])

    # Tool execution indicators - CASE SENSITIVE
    # These indicate Claude is executing a tool.
    # Matched directly against lines (case-sensitive).
    execution_indicators: List[str] = field(default_factory=lambda: [
        "Reading",
        "Writing",
        "Editing",
        "Running",
        "Executing",
        "Searching",
        "Analyzing",
        "Processing",
        "Installing",
        "Building",
        "Compiling",
        "Testing",
        "Deploying",
    ])

    # Waiting patterns - indicate Claude is waiting for user decision
    # Matched against the last few lines of output (lowercased).
    waiting_patterns: List[str] = field(default_factory=lambda: [
        "paused",
        "do you want",
        "proceed",
        "continue",
        "yes/no",
        "[y/n]",
        "press any key",
    ])

    # Prompt characters - indicate empty prompt waiting for user input
    # These are exact matches for line content.
    prompt_chars: List[str] = field(default_factory=lambda: [
        ">",
        "›",
        "❯",  # Claude Code's prompt character (U+276F)
    ])

    # Line prefixes to clean/remove for display
    # These are stripped from the beginning of lines.
    line_prefixes: List[str] = field(default_factory=lambda: [
        "› ",
        "> ",
        "❯ ",  # Claude Code's prompt character (U+276F)
        "- ",
        "• ",
    ])

    # Status bar prefixes to filter out
    # Lines starting with these are UI chrome, not Claude output.
    status_bar_prefixes: List[str] = field(default_factory=lambda: [
        "⏵⏵",  # Status bar indicator (e.g., "⏵⏵ bypass permissions on")
    ])

    # Command menu pattern - regex pattern for slash command menu lines
    # These appear when user types a slash command and Claude shows autocomplete
    # Format: "  /command-name     Description text"
    command_menu_pattern: str = r"^\s*/[\w-]+\s{2,}\S"

    # Spawn failure patterns - when the claude command fails to start
    # These indicate the command was not found or failed to execute
    # Checked against pane content to detect failed spawns
    spawn_failure_patterns: List[str] = field(default_factory=lambda: [
        "command not found",
        "not found:",  # zsh style: "zsh: command not found: claude"
        "no such file or directory",
        "permission denied",
        "cannot execute",
        "is not recognized",  # Windows-style (for future compatibility)
    ])


# Default patterns instance
DEFAULT_PATTERNS = StatusPatterns()


def get_patterns() -> StatusPatterns:
    """Get the status detection patterns.

    Returns the default patterns. In the future, this could be
    extended to load from a config file.

    Returns:
        StatusPatterns instance with all pattern lists
    """
    return DEFAULT_PATTERNS


def matches_any(text: str, patterns: List[str], case_sensitive: bool = False) -> bool:
    """Check if text matches any of the patterns.

    Args:
        text: Text to search in
        patterns: List of patterns to match
        case_sensitive: Whether matching is case-sensitive

    Returns:
        True if any pattern is found in text
    """
    if not case_sensitive:
        text = text.lower()
        return any(p.lower() in text for p in patterns)
    return any(p in text for p in patterns)


def find_matching_line(
    lines: List[str],
    patterns: List[str],
    case_sensitive: bool = False,
    reverse: bool = True
) -> str | None:
    """Find the first line that matches any pattern.

    Args:
        lines: Lines to search
        patterns: Patterns to match
        case_sensitive: Whether matching is case-sensitive
        reverse: Search from end to beginning

    Returns:
        The matching line, or None if no match
    """
    search_lines = reversed(lines) if reverse else lines
    for line in search_lines:
        if matches_any(line, patterns, case_sensitive):
            return line
    return None


def is_prompt_line(line: str, patterns: StatusPatterns = None) -> bool:
    """Check if a line is an empty prompt waiting for input.

    Args:
        line: Line to check
        patterns: StatusPatterns to use (defaults to DEFAULT_PATTERNS)

    Returns:
        True if line is an empty prompt
    """
    patterns = patterns or DEFAULT_PATTERNS
    stripped = line.strip()
    return stripped in patterns.prompt_chars


def is_status_bar_line(line: str, patterns: StatusPatterns = None) -> bool:
    """Check if a line is status bar UI chrome.

    Args:
        line: Line to check
        patterns: StatusPatterns to use (defaults to DEFAULT_PATTERNS)

    Returns:
        True if line is status bar chrome
    """
    patterns = patterns or DEFAULT_PATTERNS
    stripped = line.strip()
    return any(stripped.startswith(prefix) for prefix in patterns.status_bar_prefixes)


def is_command_menu_line(line: str, patterns: StatusPatterns = None) -> bool:
    """Check if a line is part of a slash command menu.

    Claude Code shows a menu of commands when user types a slash.
    Format: "  /command-name     Description text"

    Args:
        line: Line to check
        patterns: StatusPatterns to use (defaults to DEFAULT_PATTERNS)

    Returns:
        True if line is a command menu entry
    """
    import re
    patterns = patterns or DEFAULT_PATTERNS
    return bool(re.match(patterns.command_menu_pattern, line))


def count_command_menu_lines(lines: List[str], patterns: StatusPatterns = None) -> int:
    """Count how many lines in the list are command menu lines.

    Args:
        lines: Lines to check
        patterns: StatusPatterns to use (defaults to DEFAULT_PATTERNS)

    Returns:
        Number of lines matching the command menu pattern
    """
    patterns = patterns or DEFAULT_PATTERNS
    return sum(1 for line in lines if is_command_menu_line(line, patterns))


def clean_line(line: str, patterns: StatusPatterns = None, max_length: int = 80) -> str:
    """Clean a line for display.

    Removes prefixes, strips whitespace, and truncates.

    Args:
        line: Line to clean
        patterns: StatusPatterns to use (defaults to DEFAULT_PATTERNS)
        max_length: Maximum length before truncation

    Returns:
        Cleaned line
    """
    patterns = patterns or DEFAULT_PATTERNS
    cleaned = line.strip()

    # Remove common prefixes
    for prefix in patterns.line_prefixes:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break  # Only remove one prefix

    # Truncate if too long
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length - 3] + "..."

    return cleaned
