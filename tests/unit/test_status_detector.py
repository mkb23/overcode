"""
Unit tests for StatusDetector.

These tests use MockTmux to inject fake pane content, allowing us to
test all detection paths without requiring real tmux.
"""

import pytest
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.status_detector import StatusDetector
from overcode.interfaces import MockTmux
from tests.fixtures import (
    create_mock_session,
    create_mock_tmux_with_content,
    PANE_CONTENT_WAITING_USER,
    PANE_CONTENT_PERMISSION_PROMPT,
    PANE_CONTENT_RUNNING_WITH_SPINNER,
    PANE_CONTENT_RUNNING_WITH_TOOL,
    PANE_CONTENT_STALLED,
    PANE_CONTENT_ACTIVE_STREAMING,
    PANE_CONTENT_NO_OUTPUT,
    PANE_CONTENT_THINKING,
    PANE_CONTENT_WEB_SEARCH_PERMISSION,
    PANE_CONTENT_BASH_PERMISSION,
    PANE_CONTENT_READ_PERMISSION,
    PANE_CONTENT_AUTOCOMPLETE_SUGGESTION,
    PANE_CONTENT_ERROR_API_OVERLOADED,
    PANE_CONTENT_ERROR_TIMEOUT,
    PANE_CONTENT_ERROR_FINAL,
    PANE_CONTENT_ERROR_CONNECTION,
    PANE_CONTENT_ERROR_ECONNRESET,
    PANE_CONTENT_ERROR_RATE_LIMIT,
    PANE_CONTENT_ERROR_AUTH,
    PANE_CONTENT_NARRATIVE_ERRORS,
    PANE_CONTENT_NARRATIVE_ERROR_PATTERNS,
    PANE_CONTENT_PLAN_MODE_IDLE,
    PANE_CONTENT_PLAN_APPROVAL,
)


class TestStatusDetectorBasics:
    """Test basic status detection functionality"""

    def test_detects_waiting_user_at_empty_prompt(self):
        """When Claude shows an empty '>' prompt, user input is expected"""
        mock_tmux = create_mock_tmux_with_content("agents", 1, PANE_CONTENT_WAITING_USER)
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1)

        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_WAITING_USER
        assert "Waiting for user input" in activity

    def test_detects_permission_prompt(self):
        """Permission prompts should be detected as waiting_user"""
        mock_tmux = create_mock_tmux_with_content("agents", 1, PANE_CONTENT_PERMISSION_PROMPT)
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1)

        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_WAITING_USER
        assert "Permission:" in activity

    def test_detects_running_with_spinner(self):
        """Spinner characters indicate active work"""
        mock_tmux = create_mock_tmux_with_content("agents", 1, PANE_CONTENT_RUNNING_WITH_SPINNER)
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1, standing_instructions="Keep working")

        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_RUNNING

    def test_detects_running_with_tool_execution(self):
        """Tool execution indicators mean Claude is working"""
        mock_tmux = create_mock_tmux_with_content("agents", 1, PANE_CONTENT_RUNNING_WITH_TOOL)
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1, standing_instructions="Do the thing")

        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_RUNNING
        assert "Reading" in activity

    def test_detects_thinking(self):
        """'thinking' keyword indicates active work"""
        mock_tmux = create_mock_tmux_with_content("agents", 1, PANE_CONTENT_THINKING)
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1, standing_instructions="Think hard")

        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_RUNNING


class TestStatusDetectorStalledDetection:
    """Test detection of stalled sessions"""

    def test_detects_stalled_with_nbsp(self):
        """User input with non-breaking space and no response = stalled"""
        mock_tmux = create_mock_tmux_with_content("agents", 1, PANE_CONTENT_STALLED)
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1)

        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_WAITING_USER
        assert "Stalled" in activity or "no response" in activity.lower()


class TestStatusDetectorContentChange:
    """Test content change detection"""

    def test_detects_running_when_content_changes(self):
        """If pane content changed since last check, Claude is actively working"""
        mock_tmux = MockTmux()
        mock_tmux.new_session("agents")

        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1)

        # First call - set initial content
        mock_tmux.sessions["agents"][1] = "Initial content"
        status1, _, _ = detector.detect_status(session)

        # Second call - content changed
        mock_tmux.sessions["agents"][1] = "Different content now"
        status2, activity2, _ = detector.detect_status(session)

        assert status2 == StatusDetector.STATUS_RUNNING
        assert "Active:" in activity2

    def test_no_running_when_content_unchanged(self):
        """If content hasn't changed and no active indicators, not running"""
        mock_tmux = MockTmux()
        mock_tmux.new_session("agents")

        # Content that doesn't have active indicators
        static_content = """
Some idle output
More idle text
No spinners or tools running
"""

        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1)  # No standing instructions

        # Two calls with same content
        mock_tmux.sessions["agents"][1] = static_content
        detector.detect_status(session)  # Prime the content hash

        mock_tmux.sessions["agents"][1] = static_content
        status, _, _ = detector.detect_status(session)

        # Should be waiting_user since no standing instructions
        assert status == StatusDetector.STATUS_WAITING_USER

    def test_status_bar_changes_dont_trigger_running(self):
        """Status bar updates (token counts, time) should not trigger 'running' detection.

        This tests the fix for the bug where dynamic status bar elements like token
        counts and elapsed time would cause false 'running' detection.
        """
        mock_tmux = MockTmux()
        mock_tmux.new_session("agents")

        # Content with status bar showing some stats
        content_v1 = """
Some output from Claude
More text here
⏵⏵ bypass permissions on · 123 tokens · 5s
>
"""

        # Same content but status bar has updated stats (tokens and time changed)
        content_v2 = """
Some output from Claude
More text here
⏵⏵ bypass permissions on · 456 tokens · 10s
>
"""

        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1)  # No standing instructions

        # First call with v1
        mock_tmux.sessions["agents"][1] = content_v1
        detector.detect_status(session)  # Prime the content hash

        # Second call with v2 - only status bar changed
        mock_tmux.sessions["agents"][1] = content_v2
        status, activity, _ = detector.detect_status(session)

        # Should NOT be running - only the status bar changed
        # Should be waiting_user due to the empty prompt
        assert status == StatusDetector.STATUS_WAITING_USER
        assert "Active:" not in activity


class TestStatusDetectorNoInstructions:
    """Test the waiting_user state when no standing instructions"""

    def test_returns_waiting_user_when_idle_without_orders(self):
        """Sessions without standing instructions get waiting_user status when idle"""
        content = """
⏺ Task completed successfully!

  All done. Let me know if you need anything else.

──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
"""
        mock_tmux = create_mock_tmux_with_content("agents", 1, content)
        detector = StatusDetector("agents", tmux=mock_tmux)

        # Session WITHOUT standing instructions
        session = create_mock_session(tmux_window=1, standing_instructions="")

        # Prime content hash first
        detector.detect_status(session)
        status, _, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_WAITING_USER

    def test_returns_running_when_idle_with_orders(self):
        """Sessions WITH standing instructions get green status when idle"""
        content = """
⏺ Task completed successfully!

  All done. Let me know if you need anything else.

──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
"""
        mock_tmux = create_mock_tmux_with_content("agents", 1, content)
        detector = StatusDetector("agents", tmux=mock_tmux)

        # Session WITH standing instructions
        session = create_mock_session(
            tmux_window=1,
            standing_instructions="Keep the agent working until completion"
        )

        # Prime content hash first
        detector.detect_status(session)
        status, _, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_RUNNING


class TestApprovalDetection:
    """Test approval/plan mode detection.

    An idle session in plan mode (no Claude output) should be waiting_user,
    not waiting_approval. The ⏸ status bar line showing "plan mode on"
    must be filtered as UI chrome.
    """

    def test_plan_mode_idle_is_waiting_user(self):
        """Fresh plan-mode session should be waiting_user.

        Bug: "⏸ plan mode on" status bar line wasn't filtered as UI chrome,
        so "plan mode" matched approval_patterns on idle sessions.
        """
        mock_tmux = create_mock_tmux_with_content("agents", 1, PANE_CONTENT_PLAN_MODE_IDLE)
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1)

        # Prime content hash
        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_WAITING_USER, (
            f"Idle plan-mode session should be waiting_user, got {status}: {activity}"
        )

    def test_genuine_plan_approval_detected(self):
        """Claude output with approval text should be waiting_approval."""
        mock_tmux = create_mock_tmux_with_content("agents", 1, PANE_CONTENT_PLAN_APPROVAL)
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1)

        # Prime content hash
        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_WAITING_APPROVAL, (
            f"Genuine plan approval should be waiting_approval, got {status}: {activity}"
        )


class TestStatusDetectorAutocomplete:
    """Test handling of autocomplete suggestions"""

    def test_autocomplete_suggestion_is_waiting_user(self):
        """Autocomplete suggestions with '↵ send' should be detected as waiting_user.

        Claude Code shows autocomplete suggestions like:
            > delete both test files                             ↵ send
        The '↵ send' indicates this is an autocomplete suggestion. The agent is idle
        and waiting for user input, so should be WAITING_USER (red), not RUNNING (green).

        Previously this would fall through to default logic and show green if the session
        had standing instructions, which was incorrect.
        """
        mock_tmux = create_mock_tmux_with_content("agents", 1, PANE_CONTENT_AUTOCOMPLETE_SUGGESTION)
        detector = StatusDetector("agents", tmux=mock_tmux)
        # Session WITH standing instructions - previously this caused green status
        session = create_mock_session(tmux_window=1, standing_instructions="Keep working")

        # Prime content hash
        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        # Should be waiting_user - agent is idle with autocomplete showing
        assert status == StatusDetector.STATUS_WAITING_USER, (
            f"Autocomplete suggestion should be waiting_user, got {status}. "
            f"Activity: {activity}"
        )
        # Should NOT say "Stalled" - that's for user input with no response
        assert "Stalled" not in activity, (
            f"Autocomplete should not be 'Stalled', got: {activity}"
        )


class TestStatusDetectorEdgeCases:
    """Test edge cases and error handling"""

    def test_handles_empty_pane(self):
        """Empty pane content should return waiting_user"""
        # Empty string returns None from capture_pane (nothing to capture)
        mock_tmux = create_mock_tmux_with_content("agents", 1, "")
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1)

        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_WAITING_USER
        # Empty content returns "Unable to read pane" because capture returns None
        assert "Unable to read pane" in activity or "No output" in activity

    def test_handles_missing_pane(self):
        """Missing pane should return waiting_user with error message"""
        mock_tmux = MockTmux()
        mock_tmux.new_session("agents")
        # Don't create window 1

        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1)

        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_WAITING_USER
        assert "Unable to read pane" in activity

    def test_handles_whitespace_only_content(self):
        """Whitespace-only content should be treated as no output"""
        mock_tmux = create_mock_tmux_with_content("agents", 1, "   \n\n   \n")
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1)

        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_WAITING_USER

    def test_ignores_ui_chrome_status_bar(self):
        """UI chrome like '⏵⏵ bypass permissions on' should not trigger permission detection.

        Bug reproduction: The status bar at bottom of Claude Code shows lines like:
            ⏵⏵ bypass permissions on (shift+tab to cycle)
        This contains 'permission' which falsely triggered the permission prompt detection.
        """
        # Simulate output with status bar containing "permission"
        content = """⏺ I'll create a test file for you.

⏺ Write(test.md)
  ⎿  Created test.md

────────────────────────────────────────────────────────────────────────────────
>
────────────────────────────────────────────────────────────────────────────────
  ⏵⏵ bypass permissions on (shift+tab to cycle)"""

        mock_tmux = create_mock_tmux_with_content("agents", 1, content)
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1)

        status, activity, _ = detector.detect_status(session)

        # Should be waiting_user (empty prompt), NOT permission prompt
        assert status == StatusDetector.STATUS_WAITING_USER
        assert "Permission:" not in activity


class TestStatusDetectorHelperMethods:
    """Test helper/utility methods"""

    def test_clean_line_removes_prefixes(self):
        """_clean_line should strip common prefixes"""
        mock_tmux = MockTmux()
        detector = StatusDetector("agents", tmux=mock_tmux)

        assert detector._clean_line("> some text") == "some text"
        assert detector._clean_line("› other text") == "other text"
        assert detector._clean_line("- list item") == "list item"
        assert detector._clean_line("• bullet point") == "bullet point"

    def test_clean_line_truncates_long_lines(self):
        """_clean_line should truncate lines over 80 chars"""
        mock_tmux = MockTmux()
        detector = StatusDetector("agents", tmux=mock_tmux)

        long_line = "x" * 100
        result = detector._clean_line(long_line)

        assert len(result) == 80
        assert result.endswith("...")

    def test_get_pane_content_limits_lines(self):
        """get_pane_content should respect num_lines limit"""
        many_lines = "\n".join([f"Line {i}" for i in range(100)])
        mock_tmux = create_mock_tmux_with_content("agents", 1, many_lines)
        detector = StatusDetector("agents", tmux=mock_tmux)

        content = detector.get_pane_content(1, num_lines=10)

        # Should only have last 10 lines
        lines = content.strip().split('\n')
        assert len(lines) == 10
        assert "Line 99" in lines[-1]  # Last line should be Line 99


class TestStatusDetectorNewPermissionFormat:
    """Test detection of Claude Code v2 permission dialog format.

    Bug reproduction: The new permission dialog format shows:
        Do you want to proceed?
        ❯ 1. Yes
          2. Yes, and don't ask again for Web Search commands in
             /path/to/directory
          3. No, and tell Claude what to do differently (esc)

    The old detector matched "web search" as an active indicator, which
    falsely matched "Web Search commands in" from option 2.
    """

    def test_web_search_permission_is_waiting_user(self):
        """Web Search permission prompt should be waiting_user, not running.

        Bug: 'web search' active indicator falsely matches 'Web Search commands in'
        from the permission dialog option text.
        """
        mock_tmux = create_mock_tmux_with_content("agents", 1, PANE_CONTENT_WEB_SEARCH_PERMISSION)
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1, standing_instructions="Keep working")

        # Prime content hash (first call)
        detector.detect_status(session)
        # Second call - content unchanged
        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_WAITING_USER, (
            f"Expected waiting_user but got {status}. "
            f"Activity was: {activity}"
        )

    def test_bash_permission_is_waiting_user(self):
        """Bash permission prompt should be waiting_user.

        Similar to web search - 'Bash commands in' should not trigger active detection.
        """
        mock_tmux = create_mock_tmux_with_content("agents", 1, PANE_CONTENT_BASH_PERMISSION)
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1, standing_instructions="Keep working")

        # Prime content hash
        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_WAITING_USER, (
            f"Expected waiting_user but got {status}. "
            f"Activity was: {activity}"
        )

    def test_read_permission_is_waiting_user(self):
        """Read permission prompt should be waiting_user.

        The text 'Reading' is a tool execution indicator that could falsely match.
        """
        mock_tmux = create_mock_tmux_with_content("agents", 1, PANE_CONTENT_READ_PERMISSION)
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1, standing_instructions="Keep working")

        # Prime content hash
        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_WAITING_USER, (
            f"Expected waiting_user but got {status}. "
            f"Activity was: {activity}"
        )

    def test_permission_activity_mentions_permission_or_proceed(self):
        """Activity text for permission prompts should be descriptive."""
        mock_tmux = create_mock_tmux_with_content("agents", 1, PANE_CONTENT_WEB_SEARCH_PERMISSION)
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1)

        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        # Activity should mention this is a permission/proceed question
        activity_lower = activity.lower()
        has_good_description = (
            "permission" in activity_lower or
            "proceed" in activity_lower or
            "do you want" in activity_lower or
            "tool use" in activity_lower
        )
        assert has_good_description, (
            f"Activity should describe the permission prompt, got: {activity}"
        )


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestStatusDetectorSpawnFailure:
    """Test detection of spawn failures (command not found, etc.)"""

    def test_detects_spawn_failure_bash_style(self):
        """bash: command not found should be detected as spawn failure."""
        from tests.fixtures import PANE_CONTENT_SPAWN_FAILED_BASH

        mock_tmux = create_mock_tmux_with_content("agents", 1, PANE_CONTENT_SPAWN_FAILED_BASH)
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1)

        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_WAITING_USER, (
            f"Spawn failure should be waiting_user, got {status}"
        )
        assert "spawn failed" in activity.lower(), (
            f"Activity should mention spawn failure, got: {activity}"
        )
        assert "command not found" in activity.lower(), (
            f"Activity should include the error message, got: {activity}"
        )

    def test_detects_spawn_failure_zsh_style(self):
        """zsh: command not found: claude should be detected as spawn failure."""
        from tests.fixtures import PANE_CONTENT_SPAWN_FAILED_ZSH

        mock_tmux = create_mock_tmux_with_content("agents", 1, PANE_CONTENT_SPAWN_FAILED_ZSH)
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1)

        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_WAITING_USER, (
            f"Spawn failure should be waiting_user, got {status}"
        )
        assert "spawn failed" in activity.lower(), (
            f"Activity should mention spawn failure, got: {activity}"
        )

    def test_detects_spawn_failure_permission_denied(self):
        """Permission denied should be detected as spawn failure."""
        from tests.fixtures import PANE_CONTENT_SPAWN_FAILED_PERMISSION

        mock_tmux = create_mock_tmux_with_content("agents", 1, PANE_CONTENT_SPAWN_FAILED_PERMISSION)
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1)

        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_WAITING_USER, (
            f"Spawn failure should be waiting_user, got {status}"
        )
        assert "spawn failed" in activity.lower(), (
            f"Activity should mention spawn failure, got: {activity}"
        )
        assert "permission denied" in activity.lower(), (
            f"Activity should include 'permission denied', got: {activity}"
        )


class TestErrorDetection:
    """Tests for STATUS_ERROR detection (#216).

    Real Claude Code errors use specific structural formats (⎿ API Error, etc.)
    and should be detected as purple/error. Claude's narrative text that merely
    discusses errors should NOT trigger error status.
    """

    def test_detects_api_overloaded_error(self):
        """529 overloaded with retry should be detected as error."""
        mock_tmux = create_mock_tmux_with_content("agents", 1, PANE_CONTENT_ERROR_API_OVERLOADED)
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1)

        # First call sets baseline, second detects static content
        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_ERROR, (
            f"API overloaded error should be STATUS_ERROR, got {status}: {activity}"
        )
        assert "API Error" in activity

    def test_detects_request_timeout_error(self):
        """Request timeout with retry should be detected as error."""
        mock_tmux = create_mock_tmux_with_content("agents", 1, PANE_CONTENT_ERROR_TIMEOUT)
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1)

        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_ERROR, (
            f"Timeout error should be STATUS_ERROR, got {status}: {activity}"
        )

    def test_detects_final_error_after_retries(self):
        """Final API error (retries exhausted) should be detected as error."""
        mock_tmux = create_mock_tmux_with_content("agents", 1, PANE_CONTENT_ERROR_FINAL)
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1)

        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_ERROR, (
            f"Final error should be STATUS_ERROR, got {status}: {activity}"
        )
        assert "API Error" in activity

    def test_detects_connection_error(self):
        """Connection error with TypeError should be detected as error."""
        mock_tmux = create_mock_tmux_with_content("agents", 1, PANE_CONTENT_ERROR_CONNECTION)
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1)

        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_ERROR, (
            f"Connection error should be STATUS_ERROR, got {status}: {activity}"
        )

    def test_detects_econnreset_error(self):
        """ECONNRESET should be detected as error."""
        mock_tmux = create_mock_tmux_with_content("agents", 1, PANE_CONTENT_ERROR_ECONNRESET)
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1)

        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_ERROR, (
            f"ECONNRESET should be STATUS_ERROR, got {status}: {activity}"
        )

    def test_detects_rate_limit_banner(self):
        """Rate limit banner should be detected as error."""
        mock_tmux = create_mock_tmux_with_content("agents", 1, PANE_CONTENT_ERROR_RATE_LIMIT)
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1)

        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_ERROR, (
            f"Rate limit banner should be STATUS_ERROR, got {status}: {activity}"
        )
        assert "hit your limit" in activity.lower()

    def test_detects_auth_error(self):
        """Auth error should be detected as error."""
        mock_tmux = create_mock_tmux_with_content("agents", 1, PANE_CONTENT_ERROR_AUTH)
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1)

        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_ERROR, (
            f"Auth error should be STATUS_ERROR, got {status}: {activity}"
        )

    def test_narrative_errors_not_detected_as_error(self):
        """Claude discussing errors in response text should NOT trigger error (#216)."""
        mock_tmux = create_mock_tmux_with_content("agents", 1, PANE_CONTENT_NARRATIVE_ERRORS)
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1)

        # First call sets baseline, second detects static content
        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        assert status != StatusDetector.STATUS_ERROR, (
            f"Narrative error text should NOT be STATUS_ERROR, got {status}: {activity}"
        )

    def test_error_pattern_discussion_not_detected_as_error(self):
        """Claude discussing error detection patterns should NOT trigger error (#216).

        This is the exact scenario from the bug report: Claude's output contains
        words like 'timeout', '429', 'api.*error', 'rate.*limit' as part of a
        discussion about error detection, not as actual system errors.
        """
        mock_tmux = create_mock_tmux_with_content("agents", 1, PANE_CONTENT_NARRATIVE_ERROR_PATTERNS)
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1)

        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        assert status != StatusDetector.STATUS_ERROR, (
            f"Discussion of error patterns should NOT be STATUS_ERROR, got {status}: {activity}"
        )

    def test_shell_prompt_detected_with_claude_scrollback(self):
        """Shell prompt with Claude output (⏺, ⏵) in scrollback → TERMINATED.

        After Claude exits, its output markers and status bar remain in
        scrollback above the shell prompt. These must not prevent detection.
        """
        content = """
⏺ Here's the fix I made:

  ⎿ Updated the config file

  ⏵⏵ bypass permissions on (shift+tab to cycle)
                                                          1 MCP server failed · /mcp

Resume this session with:
claude --resume d0c16531-fef4-44ec-b3ba-bc64dda5027e
mike@shirka overcode-main %
"""
        mock_tmux = create_mock_tmux_with_content("agents", 1, content)
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1)

        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_TERMINATED, (
            f"Shell prompt after exit should be TERMINATED, got {status}: {activity}"
        )

    def test_content_changing_suppresses_error(self):
        """Even if error text is present, content changing means running (#216)."""
        # First poll with error content
        error_content = PANE_CONTENT_ERROR_API_OVERLOADED
        mock_tmux = create_mock_tmux_with_content("agents", 1, error_content)
        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1)

        # First call establishes baseline
        detector.detect_status(session)

        # Second call with DIFFERENT content (simulating active streaming)
        different_content = error_content + "\n  ⎿ API Error (529) · Retrying in 8 seconds… (attempt 4/10)"
        mock_tmux.sessions["agents"][1] = different_content
        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_RUNNING, (
            f"Content changing should return RUNNING even with error text, got {status}: {activity}"
        )
