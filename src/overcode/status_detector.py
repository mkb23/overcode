"""
Status detection for Claude sessions in tmux.
"""

from typing import Optional, Tuple, TYPE_CHECKING

from .status_constants import (
    STATUS_RUNNING,
    STATUS_NO_INSTRUCTIONS,
    STATUS_WAITING_SUPERVISOR,
    STATUS_WAITING_USER,
    STATUS_TERMINATED,
)
from .status_patterns import (
    get_patterns,
    matches_any,
    find_matching_line,
    is_status_bar_line,
    is_command_menu_line,
    count_command_menu_lines,
    clean_line,
    StatusPatterns,
)

if TYPE_CHECKING:
    from .interfaces import TmuxInterface


class StatusDetector:
    """Detects the current status of a Claude session"""

    # Re-export status constants for backwards compatibility
    STATUS_RUNNING = STATUS_RUNNING
    STATUS_NO_INSTRUCTIONS = STATUS_NO_INSTRUCTIONS
    STATUS_WAITING_SUPERVISOR = STATUS_WAITING_SUPERVISOR
    STATUS_WAITING_USER = STATUS_WAITING_USER
    STATUS_TERMINATED = STATUS_TERMINATED

    def __init__(
        self,
        tmux_session: str,
        tmux: "TmuxInterface" = None,
        patterns: StatusPatterns = None
    ):
        """Initialize the status detector.

        Args:
            tmux_session: Name of the tmux session to monitor
            tmux: TmuxInterface implementation (defaults to RealTmux for production)
            patterns: StatusPatterns to use for detection (defaults to DEFAULT_PATTERNS)
        """
        self.tmux_session = tmux_session

        # Dependency injection for testability
        if tmux is None:
            from .interfaces import RealTmux
            tmux = RealTmux()
        self.tmux = tmux

        # Use provided patterns or default
        self.patterns = patterns or get_patterns()

        # Track previous content per session for change detection
        self._previous_content: dict[int, str] = {}  # window -> content hash
        self._content_changed: dict[int, bool] = {}  # window -> changed flag

    def get_pane_content(self, window: int, num_lines: int = 50) -> Optional[str]:
        """Get the last N meaningful lines from a tmux pane.

        Captures more content than requested and filters out trailing blank lines
        to find the actual content (Claude Code often has blank lines at bottom).
        """
        content = self.tmux.capture_pane(self.tmux_session, window, lines=150)
        if content is None:
            return None

        # Strip trailing blank lines, then return last num_lines
        lines = content.rstrip().split('\n')
        meaningful_lines = lines[-num_lines:] if len(lines) > num_lines else lines
        return '\n'.join(meaningful_lines)

    def detect_status(self, session) -> Tuple[str, str, str]:
        """
        Detect session status and current activity.

        Returns:
            Tuple of (status, current_activity, pane_content)
            - status: one of STATUS_* constants
            - current_activity: single line description of what's happening
            - pane_content: the raw pane content (to avoid duplicate tmux calls)
        """
        content = self.get_pane_content(session.tmux_window)

        if not content:
            return self.STATUS_WAITING_USER, "Unable to read pane", ""

        # Content change detection - if content is changing, Claude is actively working
        # Key by session.id, not window index, to avoid stale hashes when windows are recycled
        # IMPORTANT: Filter out status bar lines before hashing to avoid false positives
        # from dynamic status bar elements (token counts, elapsed time) that update when idle
        session_id = session.id
        content_for_hash = self._filter_status_bar_for_hash(content)
        content_hash = hash(content_for_hash)
        content_changed = False
        if session_id in self._previous_content:
            content_changed = self._previous_content[session_id] != content_hash
        self._previous_content[session_id] = content_hash
        self._content_changed[session_id] = content_changed

        lines = content.strip().split('\n')
        # Get more lines for better context (menu prompts can be 5+ lines)
        last_lines = [l.strip() for l in lines[-10:] if l.strip()]

        if not last_lines:
            return self.STATUS_WAITING_USER, "No output", content

        last_line = last_lines[-1]

        # Check for spawn failure FIRST (command not found, etc.)
        # This should be detected before shell prompt check because the error
        # message appears before the shell prompt returns
        spawn_error = self._detect_spawn_failure(lines)
        if spawn_error:
            return self.STATUS_WAITING_USER, spawn_error, content

        # Check for shell prompt (Claude Code has terminated)
        # Shell prompts typically end with $ or % and have username@hostname pattern
        # Also check for absence of Claude Code UI elements
        if self._is_shell_prompt(last_lines):
            return self.STATUS_TERMINATED, "Claude exited - shell prompt", content

        # Filter out UI chrome lines before pattern matching
        content_lines = [l for l in last_lines if not is_status_bar_line(l, self.patterns)]

        # Join more lines for pattern matching (menus have multiple lines)
        last_few = ' '.join(content_lines[-6:]).lower() if content_lines else ''

        # Check for permission/confirmation prompts (HIGHEST priority)
        # This MUST come before active indicator checks because permission dialogs
        # can contain tool names like "Web Search commands in" that would falsely
        # match active indicators.
        if matches_any(last_few, self.patterns.permission_patterns):
            request_text = self._extract_permission_request(last_lines)
            return self.STATUS_WAITING_USER, f"Permission: {request_text}", content

        # Check for command menu display (slash command autocomplete)
        # When user types a slash command, Claude shows a menu of available commands.
        # This means Claude is waiting for the user to complete/select a command.
        # We check if most of the last lines are menu entries.
        menu_lines = count_command_menu_lines(last_lines, self.patterns)
        if menu_lines >= 3 and menu_lines >= len(last_lines) * 0.4:
            return self.STATUS_WAITING_USER, "Command menu - waiting for input", content

        # Content change detection - if pane content is actively changing, Claude is working
        # This is the most reliable indicator as it catches streaming output
        if content_changed:
            activity = self._extract_last_activity(last_lines)
            return self.STATUS_RUNNING, f"Active: {activity}", content

        # Check for ACTIVE WORK indicators BEFORE checking for prompt
        # These indicate Claude is busy even if the prompt is visible
        if matches_any(last_few, self.patterns.active_indicators):
            matching_line = find_matching_line(
                last_lines, self.patterns.active_indicators, reverse=True
            )
            if matching_line:
                return self.STATUS_RUNNING, clean_line(matching_line, self.patterns), content
            return self.STATUS_RUNNING, "Processing...", content

        # Check for tool execution indicators (case-sensitive)
        matching_line = find_matching_line(
            last_lines, self.patterns.execution_indicators, case_sensitive=True, reverse=True
        )
        if matching_line:
            return self.STATUS_RUNNING, clean_line(matching_line, self.patterns), content

        # Check for thinking/planning
        if any("thinking" in line.lower() for line in last_lines):
            return self.STATUS_RUNNING, "Thinking...", content

        # NOW check for Claude's prompt (user input prompt) - means waiting for user
        # Only check after ruling out active work indicators above
        # We need to distinguish:
        #   - Empty prompt `>` or `› ` = waiting for user input
        #   - User input `> some text` with no Claude response = stalled
        for line in last_lines[-4:]:
            stripped = line.strip()
            # Empty prompt ready for input
            if stripped in self.patterns.prompt_chars:
                return self.STATUS_WAITING_USER, "Waiting for user input", content
            # Autocomplete suggestion showing (prompt with suggested content + send indicator)
            # This means Claude is idle and waiting for user input
            if any(stripped.startswith(c) for c in self.patterns.prompt_chars):
                if '↵' in stripped and 'send' in stripped.lower():
                    return self.STATUS_WAITING_USER, "Waiting for user input", content

        # Check if there's user input that Claude hasn't responded to (stalled)
        # Look for `> text` followed by no Claude response (no ⏺ line after it)
        # Note: ⏺ is Claude's output indicator, ⏵⏵ in status bar is just UI chrome
        # Note: Claude Code uses \xa0 (non-breaking space) after prompt, not regular space
        found_user_input = False
        found_claude_response = False
        for line in last_lines:
            stripped = line.strip()
            # Skip autocomplete suggestion lines - they end with "↵ send" indicator
            # These are not actual user input, just UI showing suggested completions
            if '↵' in stripped and 'send' in stripped.lower():
                continue
            # Check for prompt with either regular space or non-breaking space (\xa0)
            is_user_input = (
                stripped.startswith('> ') or stripped.startswith('>\xa0') or
                stripped.startswith('› ') or stripped.startswith('›\xa0') or
                stripped.startswith('❯ ') or stripped.startswith('❯\xa0')
            )
            if is_user_input and len(stripped) > 2:  # Has actual content after prompt
                found_user_input = True
                found_claude_response = False  # Reset - look for response after this
            elif stripped.startswith('⏺'):  # Claude's response indicator (not ⏵⏵ status bar)
                found_claude_response = True

        if found_user_input and not found_claude_response:
            return self.STATUS_WAITING_USER, "Stalled - no response to user input", content

        # Check for common waiting patterns
        if matches_any(last_few, self.patterns.waiting_patterns):
            return self.STATUS_WAITING_USER, self._extract_question(last_lines), content

        # Default: if no standing instructions, it's yellow
        if not session.standing_instructions:
            return self.STATUS_NO_INSTRUCTIONS, self._extract_last_activity(last_lines), content

        # Otherwise, assume running
        return self.STATUS_RUNNING, self._extract_last_activity(last_lines), content

    def _extract_permission_request(self, lines: list) -> str:
        """Extract the permission request text from lines before the prompt"""
        # Look for lines before "Enter to confirm" that contain the request
        relevant_lines = []
        for line in reversed(lines):
            line_lower = line.lower()
            # Stop when we hit the confirmation line
            if "enter to confirm" in line_lower or "esc to reject" in line_lower:
                continue
            # Stop at empty lines
            if not line.strip():
                break
            # Collect meaningful lines
            clean = self._clean_line(line)
            if len(clean) > 5:
                relevant_lines.insert(0, clean)
            # Don't go too far back
            if len(relevant_lines) >= 3:
                break

        if relevant_lines:
            # Join and truncate
            request = " ".join(relevant_lines)
            if len(request) > 100:
                request = request[:97] + "..."
            return request
        return "approval required"

    def _extract_question(self, lines: list) -> str:
        """Extract a question from recent output"""
        for line in reversed(lines):
            if '?' in line:
                return self._clean_line(line)
        return self._clean_line(lines[-1])

    def _extract_last_activity(self, lines: list) -> str:
        """Extract the most recent activity description"""
        # Look for lines that look like activity descriptions
        # Skip status bar lines (they contain UI chrome, not actual activity)
        for line in reversed(lines):
            # Skip status bar lines
            if is_status_bar_line(line, self.patterns):
                continue
            cleaned = clean_line(line, self.patterns)
            if len(cleaned) > 10 and not cleaned.startswith('›'):
                return cleaned
        return "Idle"

    def _clean_line(self, line: str) -> str:
        """Clean a line for display"""
        return clean_line(line, self.patterns)

    def _filter_status_bar_for_hash(self, content: str) -> str:
        """Filter out status bar lines before computing content hash.

        The Claude Code status bar contains dynamic elements (token counts,
        elapsed time, etc.) that change even when Claude is idle. Including
        these in the hash causes false "content changed" detection.

        Args:
            content: Raw pane content

        Returns:
            Content with status bar lines removed
        """
        lines = content.split('\n')
        filtered = [
            line for line in lines
            if not is_status_bar_line(line, self.patterns)
        ]
        return '\n'.join(filtered)

    def _detect_spawn_failure(self, lines: list) -> str | None:
        """Detect if the claude command failed to spawn.

        Checks for common error messages like "command not found" that indicate
        the claude CLI is not installed or not in PATH.

        Args:
            lines: All lines from the pane content

        Returns:
            Error message string if spawn failure detected, None otherwise
        """
        # Check recent lines for spawn failure patterns
        # We check the last 20 lines to catch the error message
        recent_lines = lines[-20:] if len(lines) > 20 else lines
        recent_text = ' '.join(recent_lines).lower()

        if matches_any(recent_text, self.patterns.spawn_failure_patterns):
            # Find the specific error line for a better message
            for line in reversed(recent_lines):
                line_lower = line.lower()
                if any(p.lower() in line_lower for p in self.patterns.spawn_failure_patterns):
                    # Extract just the error part, clean it up
                    error_msg = line.strip()
                    if len(error_msg) > 80:
                        error_msg = error_msg[:77] + "..."
                    return f"Spawn failed: {error_msg}"
            return "Spawn failed: claude command not found - is Claude CLI installed?"

        return None

    def _is_shell_prompt(self, lines: list) -> bool:
        """Detect if we're at a shell prompt (Claude Code has exited).

        Shell prompts typically:
        - End with $ or % (bash/zsh)
        - Have username@hostname pattern
        - Don't have Claude Code UI elements (>, ⏺, status bar chars)

        Returns True if this looks like a shell prompt, not Claude Code.
        """
        import re

        if not lines:
            return False

        # Get last non-empty line
        last_line = lines[-1].strip()

        # Common shell prompt patterns:
        # - user@host path $
        # - user@host path %
        # - [user@host path]$
        # - path $
        shell_prompt_patterns = [
            r'\w+@\w+.*[%$]\s*$',  # user@hostname ... $ or %
            r'\[.*\][%$#]\s*$',    # [prompt]$ or [prompt]%
            r'^[~\/].*[%$]\s*$',   # /path/to/dir $ or ~/dir %
        ]

        for pattern in shell_prompt_patterns:
            if re.search(pattern, last_line):
                # Verify there's no Claude Code UI in recent lines
                claude_ui_indicators = ['⏺', '›', '? for shortcuts', '⎿', '⏵']
                recent_text = ' '.join(lines[-5:])
                has_claude_ui = any(indicator in recent_text for indicator in claude_ui_indicators)

                if not has_claude_ui:
                    return True

        return False
