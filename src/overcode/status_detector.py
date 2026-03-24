"""
Status detection for Claude sessions in tmux.
"""

from typing import Optional, Tuple, TYPE_CHECKING

from .status_constants import (
    DEFAULT_CAPTURE_LINES,
    STATUS_CAPTURE_LINES,
    STATUS_RUNNING,
    STATUS_BUSY_SLEEPING,
    STATUS_WAITING_USER,
    STATUS_TERMINATED,
    STATUS_WAITING_APPROVAL,
    STATUS_ERROR,
)
from .status_patterns import (
    get_patterns,
    matches_any,
    find_matching_line,
    line_starts_with_any,
    is_status_bar_line,
    count_command_menu_lines,
    clean_line,
    strip_ansi,
    is_sleep_command,
    extract_sleep_duration,
    is_shell_prompt as _is_shell_prompt_line,
    StatusPatterns,
)
from .tui_helpers import format_duration

if TYPE_CHECKING:
    from .interfaces import TmuxInterface

# Pre-compiled regex patterns for hot-path methods (avoid re-compiling per call)
import re as _re


class PollingStatusDetector:
    """Detects the current status of a Claude session via tmux pane scraping."""


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
        self.capture_lines = DEFAULT_CAPTURE_LINES

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
        # Diagnostic: which phase produced the last status per session
        self._last_detect_phase: dict[str, str] = {}  # session_id -> phase name

        # Pre-compile approval and error patterns (called in hot path)
        self._compiled_approval_patterns = [
            _re.compile(p, _re.IGNORECASE) for p in self.patterns.approval_patterns
        ]
        self._compiled_error_patterns = [
            _re.compile(p, _re.IGNORECASE) for p in self.patterns.error_patterns
        ]

    def get_pane_content(self, window: str, num_lines: int = 0) -> Optional[str]:
        """Get the last N meaningful lines from a tmux pane.

        Captures more content than requested and filters out trailing blank lines
        to find the actual content (Claude Code often has blank lines at bottom).

        Args:
            num_lines: Lines to return. 0 (default) uses self.capture_lines.
        """
        effective_lines = num_lines or self.capture_lines
        content = self.tmux.capture_pane(self.tmux_session, window, lines=effective_lines + 50)
        if content is None:
            return None

        # Strip trailing blank lines, then return last effective_lines
        lines = content.rstrip().split('\n')
        meaningful_lines = lines[-effective_lines:] if len(lines) > effective_lines else lines
        return '\n'.join(meaningful_lines)

    def detect_status(self, session, num_lines: int = 0) -> Tuple[str, str, str]:
        """
        Detect session status and current activity.

        Runs detection phases in priority order, returning on first match:
        1. Terminated (window gone, empty pane)
        2. Spawn failure
        3. Shell prompt (Claude exited)
        4. Permission request
        5. Content changing (active work)
        6. Error output
        7. Approval waiting
        8. Command menu
        9. Active work indicators
        10. Tool execution
        11. Thinking
        12. User prompt / stalled input
        13. Waiting patterns
        14. Default (waiting or running based on standing instructions)

        Args:
            session: Session to detect status for.
            num_lines: Lines to capture. 0 (default) uses self.capture_lines.
                Use STATUS_CAPTURE_LINES for non-focused agents to reduce
                tmux subprocess overhead.

        Returns:
            Tuple of (status, current_activity, pane_content)
            - status: one of STATUS_* constants
            - current_activity: single line description of what's happening
            - pane_content: the raw pane content (to avoid duplicate tmux calls)
        """
        # Phase 1: Check if window exists
        terminated, content = self._detect_terminated(session, num_lines)
        if terminated is not None:
            return terminated

        # Strip ANSI escape sequences for pattern matching
        clean_content = strip_ansi(content)

        # Content change detection
        content_changed = self._update_content_hash(session.id, clean_content)

        lines = clean_content.strip().split('\n')
        last_lines = [l.strip() for l in lines[-10:] if l.strip()]

        if not last_lines:
            return STATUS_WAITING_USER, "No output", content

        # Phase 2: Spawn failure (before shell prompt — error appears first)
        spawn_error = self._detect_spawn_failure(lines)
        if spawn_error:
            return STATUS_WAITING_USER, spawn_error, content

        # Phase 3: Shell prompt (Claude exited)
        if self._is_shell_prompt(last_lines):
            return STATUS_TERMINATED, "Claude exited - shell prompt", content

        # Prepare filtered lines for remaining phases
        content_lines = [l for l in last_lines if not is_status_bar_line(l, self.patterns)]
        last_few = ' '.join(content_lines[-6:]).lower() if content_lines else ''

        # Phase 4: Permission request (HIGHEST priority among active checks)
        result = self._detect_permission_request(last_lines, last_few, content)
        if result is not None:
            return result

        # Phase 5: Content changing = active work (#214, #216)
        # But if a user prompt is visible, the agent is waiting — content
        # changes from TUI refreshes or status-bar updates shouldn't override
        # prompt detection.
        #
        # HOWEVER: Claude Code always renders the ❯ prompt as UI chrome, even
        # while actively working (#393). When Claude IS active, "esc to
        # interrupt" appears at the bottom of the pane — either as a
        # standalone line or appended to the ⏵⏵ permissions status bar.
        # We check only the last 2 lines for this, because historical
        # thinking output (✻ Twisting…, ⎿ Running…) persists in scrollback
        # above the prompt and would false-match general active indicators.
        if content_changed:
            status_bar_active = any(
                "esc to interrupt" in line.lower()
                for line in last_lines[-2:]
            )
            if not status_bar_active:
                prompt_result = self._detect_user_prompt(last_lines, content)
                if prompt_result is not None:
                    self._last_detect_phase[session.id] = "P5+P12:prompt_override"
                    return prompt_result
            activity = self._extract_last_activity(last_lines)
            self._last_detect_phase[session.id] = "P5:content_changed"
            return STATUS_RUNNING, f"Active: {activity}", content

        # Phase 6: Error output (#216) — only when content NOT changing
        result = self._detect_error(content_lines, content)
        if result is not None:
            self._last_detect_phase[session.id] = "P6:error"
            return result

        # Phase 7: Approval waiting (#22)
        result = self._detect_approval(lines, last_few, content)
        if result is not None:
            self._last_detect_phase[session.id] = "P7:approval"
            return result

        # Phase 8: Command menu
        result = self._detect_command_menu(last_lines, content)
        if result is not None:
            self._last_detect_phase[session.id] = "P8:menu"
            return result

        # Phase 9: Active work indicators
        result = self._detect_active_work(last_lines, last_few, content)
        if result is not None:
            self._last_detect_phase[session.id] = "P9:active_ind"
            return result

        # Phase 10: Tool execution (case-sensitive)
        result = self._detect_tool_execution(last_lines, content)
        if result is not None:
            self._last_detect_phase[session.id] = "P10:tool_exec"
            return result

        # Phase 11: Thinking
        if any("thinking" in line.lower() for line in last_lines):
            self._last_detect_phase[session.id] = "P11:thinking"
            return STATUS_RUNNING, "Thinking...", content

        # Phase 12: User prompt / stalled input
        result = self._detect_user_prompt(last_lines, content)
        if result is not None:
            self._last_detect_phase[session.id] = "P12:prompt"
            return result

        # Phase 13: Waiting patterns
        if matches_any(last_few, self.patterns.waiting_patterns):
            self._last_detect_phase[session.id] = "P13:waiting"
            return STATUS_WAITING_USER, self._extract_question(last_lines), content

        # Phase 14: Default based on standing instructions
        self._last_detect_phase[session.id] = "P14:default"
        return self._detect_default(session, last_lines, content)

    def _detect_terminated(self, session, num_lines: int):
        """Check if tmux window is gone or empty.

        Returns:
            (status_tuple, None) on terminal condition, or
            (None, content) when the window has content for further analysis.
        """
        content = self.get_pane_content(session.tmux_window, num_lines=num_lines)

        if content is None:
            return (STATUS_TERMINATED, "Window no longer exists", ""), None
        if not content:
            return (STATUS_WAITING_USER, "Empty pane", ""), None

        return None, content

    def _update_content_hash(self, session_id, clean_content: str) -> bool:
        """Update content hash and return whether content changed.

        Filters out status bar lines before hashing to avoid false positives
        from dynamic elements (token counts, elapsed time) that update when idle.
        """
        content_for_hash = self._filter_status_bar_for_hash(clean_content)
        # Normalize to a fixed tail length so the hash is stable regardless of
        # capture depth.  Focused agents capture the full pane while non-focused
        # agents capture only STATUS_CAPTURE_LINES; without normalization the
        # hash changes on every focus switch, producing a false "running" flash.
        hash_lines = content_for_hash.split('\n')[-STATUS_CAPTURE_LINES:]
        content_hash = hash('\n'.join(hash_lines))
        content_changed = False
        if session_id in self._previous_content:
            content_changed = self._previous_content[session_id] != content_hash
        self._previous_content[session_id] = content_hash
        self._content_changed[session_id] = content_changed
        return content_changed

    def _detect_permission_request(self, last_lines: list, last_few: str, content: str) -> Optional[Tuple[str, str, str]]:
        """Check for permission/confirmation prompts (HIGHEST priority).

        Must come before active indicator checks because permission dialogs
        can contain tool names that would falsely match active indicators.
        """
        if matches_any(last_few, self.patterns.permission_patterns):
            request_text = self._extract_permission_request(last_lines)
            return STATUS_WAITING_USER, f"Permission: {request_text}", content
        return None

    def _detect_error(self, content_lines: list, content: str) -> Optional[Tuple[str, str, str]]:
        """Check for API/system errors (#216).

        Only reached when content is NOT changing (Claude has stalled).
        Matches structural error formats per-line against the last 3 content lines.
        """
        error_line = self._find_error_line(content_lines[-3:] if content_lines else [])
        if error_line:
            error_msg = clean_line(error_line, self.patterns)
            if len(error_msg) > 80:
                error_msg = error_msg[:77] + "..."
            return STATUS_ERROR, f"Error: {error_msg}", content
        return None

    def _detect_approval(self, lines: list, last_few: str, content: str) -> Optional[Tuple[str, str, str]]:
        """Check for approval waiting state (#22).

        Only reached when content is NOT changing, so plan mode correctly shows
        orange only when Claude has stopped and is waiting for approval (#214).
        Guard: require Claude output (⏺) in content.
        """
        has_claude_output = any(line.strip().startswith('⏺') for line in lines)
        if has_claude_output and self._matches_approval_patterns(last_few):
            return STATUS_WAITING_APPROVAL, "Waiting for plan/decision approval", content
        return None

    def _detect_command_menu(self, last_lines: list, content: str) -> Optional[Tuple[str, str, str]]:
        """Check for command menu display (slash command autocomplete)."""
        menu_lines = count_command_menu_lines(last_lines, self.patterns)
        if menu_lines >= 3 and menu_lines >= len(last_lines) * 0.4:
            return STATUS_WAITING_USER, "Command menu - waiting for input", content
        return None

    def _detect_active_work(self, last_lines: list, last_few: str, content: str) -> Optional[Tuple[str, str, str]]:
        """Check for active work indicators (busy even if prompt is visible)."""
        if matches_any(last_few, self.patterns.active_indicators):
            matching_line = find_matching_line(
                last_lines, self.patterns.active_indicators, reverse=True
            )
            if matching_line:
                return STATUS_RUNNING, clean_line(matching_line, self.patterns), content
            return STATUS_RUNNING, "Processing...", content
        return None

    def _detect_tool_execution(self, last_lines: list, content: str) -> Optional[Tuple[str, str, str]]:
        """Check for tool execution indicators (case-sensitive).

        Uses start-of-line matching to avoid false positives from mid-sentence
        occurrences like 'Running the tests revealed...' (#359).
        """
        matching_line = line_starts_with_any(
            last_lines, self.patterns.execution_indicators, case_sensitive=True, reverse=True
        )
        if matching_line:
            # Check if the executing tool is a sleep command (#289)
            if is_sleep_command(matching_line):
                dur = extract_sleep_duration(matching_line)
                activity = f"Sleeping {format_duration(dur)}" if dur else "Sleeping"
                return STATUS_BUSY_SLEEPING, activity, content
            return STATUS_RUNNING, clean_line(matching_line, self.patterns), content
        return None

    def _detect_user_prompt(self, last_lines: list, content: str) -> Optional[Tuple[str, str, str]]:
        """Check for Claude's prompt (user input) and stalled input.

        Distinguishes:
        - Empty prompt `>` or `› ` = waiting for user input
        - User input `> some text` with no Claude response = stalled

        Claude Code always renders the ❯ prompt as UI chrome, even while
        actively working (#393). When active indicators are present in the
        last lines, the prompt is just decoration — skip prompt detection.
        """
        # If "esc to interrupt" appears at the bottom of the pane, Claude
        # is actively working — the prompt and user input are just UI chrome.
        # Only check the last 2 lines to avoid matching historical thinking
        # output (✻, Running…) that persists in scrollback (#393).
        if any(
            "esc to interrupt" in line.lower()
            for line in last_lines[-2:]
        ):
            return None

        # Check for empty prompt or autocomplete suggestion
        for line in last_lines[-4:]:
            stripped = line.strip()
            if stripped in self.patterns.prompt_chars:
                return STATUS_WAITING_USER, "Waiting for user input", content
            if any(stripped.startswith(c) for c in self.patterns.prompt_chars):
                if '↵' in stripped and 'send' in stripped.lower():
                    return STATUS_WAITING_USER, "Waiting for user input", content

        # Check for user input with no Claude response (stalled)
        # Note: ⏺ is Claude's output indicator, ⏵⏵ in status bar is just UI chrome
        # Note: Claude Code uses \xa0 (non-breaking space) after prompt, not regular space
        found_user_input = False
        found_claude_response = False
        for line in last_lines:
            stripped = line.strip()
            # Skip autocomplete suggestion lines
            if '↵' in stripped and 'send' in stripped.lower():
                continue
            is_user_input = (
                stripped.startswith('> ') or stripped.startswith('>\xa0') or
                stripped.startswith('› ') or stripped.startswith('›\xa0') or
                stripped.startswith('❯ ') or stripped.startswith('❯\xa0')
            )
            if is_user_input and len(stripped) > 2:
                found_user_input = True
                found_claude_response = False
            elif stripped.startswith('⏺'):
                found_claude_response = True

        if found_user_input and not found_claude_response:
            return STATUS_WAITING_USER, "Stalled - no response to user input", content

        return None

    def _detect_default(self, session, last_lines: list, content: str) -> Tuple[str, str, str]:
        """Default detection when no other phase matched.

        If none of the preceding phases (content change, error, approval,
        active indicators, tool execution, thinking, prompt, waiting patterns)
        identified the agent's state, we have no positive evidence of work.
        Return waiting_user regardless of standing instructions — P5 (content
        change) already covers the case where standing-order agents are active.
        """
        # Check for sleep commands before returning waiting (#289)
        for line in last_lines:
            if is_sleep_command(line):
                dur = extract_sleep_duration(line)
                activity = f"Sleeping {format_duration(dur)}" if dur else "Sleeping"
                return STATUS_BUSY_SLEEPING, activity, content
        return STATUS_WAITING_USER, self._extract_last_activity(last_lines), content

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

    def _scan_from_bottom(self, lines: list, predicate, max_lines: int = 20) -> Optional[str]:
        """Scan lines from bottom looking for first line matching predicate.

        Args:
            lines: Lines to scan
            predicate: Callable(line) -> bool, receives raw (stripped) lines
            max_lines: Maximum number of lines to scan from bottom

        Returns:
            The matching line (raw), or None if no match
        """
        for line in reversed(lines[-max_lines:]):
            if predicate(line):
                return line
        return None

    def _extract_question(self, lines: list) -> str:
        """Extract a question from recent output"""
        match = self._scan_from_bottom(lines, lambda line: '?' in line)
        if match:
            return self._clean_line(match)
        return self._clean_line(lines[-1])

    def _extract_last_activity(self, lines: list) -> str:
        """Extract the most recent activity description"""
        def is_activity(line: str) -> bool:
            if is_status_bar_line(line, self.patterns):
                return False
            cleaned = clean_line(line, self.patterns)
            return len(cleaned) > 10 and not cleaned.startswith('›')

        match = self._scan_from_bottom(lines, is_activity)
        if match:
            return clean_line(match, self.patterns)
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
        if not lines:
            return False

        last_line = lines[-1].strip()

        if _is_shell_prompt_line(last_line):
            # Verify Claude Code's active prompt isn't showing nearby.
            # Only check for '? for shortcuts' — it appears exclusively in
            # Claude's live input prompt and never in scrollback after exit.
            # Previous indicators (⏺, ⏵, ⎿, ›) persist in pane scrollback
            # after exit and caused false negatives.
            recent_text = ' '.join(lines[-3:])
            if '? for shortcuts' not in recent_text:
                return True

        return False

    def _matches_approval_patterns(self, text: str) -> bool:
        """Check if text matches approval waiting patterns (#22).

        Uses pre-compiled regex patterns for more flexible matching.

        Args:
            text: Text to check (should be lowercased)

        Returns:
            True if approval pattern is found
        """
        for pattern in self._compiled_approval_patterns:
            if pattern.search(text):
                return True
        return False

    def _find_error_line(self, lines: list) -> str | None:
        """Find a line matching a structural error pattern (#216).

        Checks each line individually against the pre-compiled error patterns,
        which match specific Claude Code error output formats (not broad keywords).

        Args:
            lines: Recent content lines to check (should be last 3)

        Returns:
            The matching error line, or None if no match
        """
        for line in reversed(lines):
            for pattern in self._compiled_error_patterns:
                if pattern.search(line):
                    return line
        return None


# Backward-compat alias: all existing imports continue working
StatusDetector = PollingStatusDetector
