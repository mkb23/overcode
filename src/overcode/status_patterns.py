"""
Centralized status detection patterns.

This module contains all the pattern lists used by StatusDetector to identify
Claude's current state. Centralizing these makes them:
- Easier to maintain and extend
- Testable in isolation
- Potentially configurable via config file in the future

Each pattern set includes documentation about when it's used and what it matches.
"""

import re
from dataclasses import dataclass, field
from typing import List

# Regex to match ANSI escape sequences (colors, cursor movement, etc.)
ANSI_ESCAPE_PATTERN = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text.

    This is needed because tmux capture_pane with escape_sequences=True
    preserves color codes, but pattern matching needs plain text.

    Args:
        text: Text potentially containing ANSI escape sequences

    Returns:
        Text with all ANSI escape sequences removed
    """
    return ANSI_ESCAPE_PATTERN.sub('', text)


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

    # Approval waiting patterns (#22)
    # These indicate Claude is waiting for user approval of a plan or decision
    approval_patterns: List[str] = field(default_factory=lambda: [
        "waiting for.*approval",
        "plan mode",
        "approve.*plan",
        "select.*option",
        "choose.*[1-4]",
        "review the plan",
        "approve this plan",
        "plan requires approval",
    ])

    # Error patterns (#216)
    # These match the SPECIFIC formats Claude Code uses to display errors.
    # Previous broad patterns ("timeout", "429", etc.) caused false positives
    # when Claude's response text discussed errors (#216).
    #
    # Real Claude Code errors have distinct structural formats:
    # - Retryable: "⎿ API Error (...) · Retrying in N seconds… (attempt X/10)"
    # - Final:     "⎿ API Error: <message>"
    # - Network:   "⎿ TypeError (fetch failed)"
    # - Network:   "⎿ Unable to connect to API (ECONNRESET)"
    # - Rate limit: "You've hit your limit · resets <time>"
    # - Auth:      "Invalid API key · Please run /login"
    # - Auth:      "Missing API key · Run /login"
    #
    # These are matched per-line against the last 3 content lines (not joined).
    error_patterns: List[str] = field(default_factory=lambda: [
        r"⎿\s*API Error",            # All API errors (retryable + final)
        r"⎿\s*TypeError",            # Network fetch failures
        r"⎿\s*Unable to connect",    # ECONNRESET and similar
        r"⎿\s*Error:.*compaction",   # Compaction errors
        r"You've hit your limit",     # Rate limit banner
        r"Invalid API key",           # Auth error
        r"Missing API key",           # Auth error
        r"Retrying in.*seconds.*attempt",  # Retry indicator (any format)
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


def _find_status_bar_line(content: str, patterns: StatusPatterns = None) -> str | None:
    """Find and return the LAST status bar line from pane content.

    Uses the last match because old status bar lines can persist in scrollback.
    The current/active status bar is always at the bottom of the pane.

    Args:
        content: Raw pane content (can include ANSI codes)
        patterns: StatusPatterns to use (defaults to DEFAULT_PATTERNS)

    Returns:
        The ANSI-stripped, whitespace-stripped status bar line, or None if not found
    """
    patterns = patterns or DEFAULT_PATTERNS

    # Must strip ANSI codes first since pane content is captured with escape_sequences=True
    # Search from bottom up — old status bar lines persist in scrollback,
    # but the current one is always at the bottom of the pane.
    for line in reversed(content.split('\n')):
        stripped = strip_ansi(line).strip()
        if any(stripped.startswith(prefix) for prefix in patterns.status_bar_prefixes):
            return stripped

    return None


def extract_background_bash_count(content: str, patterns: StatusPatterns = None) -> int:
    """Extract the number of background bash tasks from pane content.

    Claude Code shows background task counts in the status bar:
    - "2 bashes" when there are 2+ background tasks
    - "command... (running)" when there is 1 background task
    - Nothing when there are 0 background tasks

    Args:
        content: Raw pane content (can include ANSI codes)
        patterns: StatusPatterns to use (defaults to DEFAULT_PATTERNS)

    Returns:
        Number of active background bash tasks (0 if none detected)
    """
    stripped = _find_status_bar_line(content, patterns)
    if stripped is None:
        return 0

    # Pattern 1: "N bashes" for 2+ background tasks
    match = re.search(r'(\d+)\s+bashes', stripped)
    if match:
        return int(match.group(1))

    # Pattern 2: "(running)" without "bashes" = 1 background task
    # This appears when a single command is running in background
    if '(running)' in stripped and 'bashes' not in stripped:
        return 1

    return 0


def extract_live_subagent_count(content: str, patterns: StatusPatterns = None) -> int:
    """Extract the number of currently running subagents from pane content.

    Claude Code shows live subagent counts in the status bar:
    - "N local agents" when there are N subagents running
    - Nothing when there are 0 subagents

    Args:
        content: Raw pane content (can include ANSI codes)
        patterns: StatusPatterns to use (defaults to DEFAULT_PATTERNS)

    Returns:
        Number of active subagents (0 if none detected)
    """
    stripped = _find_status_bar_line(content, patterns)
    if stripped is None:
        return 0

    # Pattern: "N local agents" for running subagents
    match = re.search(r'(\d+)\s+local\s+agents?', stripped)
    if match:
        return int(match.group(1))

    return 0


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
