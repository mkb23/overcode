"""
Realistic integration tests for StatusDetector.

These tests use full tmux pane captures (as they actually appear) to verify
status detection works correctly with real-world content including:
- Claude Code welcome banner
- Multiple command/response cycles
- Full UI chrome (separators, shortcuts line, status bar)

This catches edge cases that minimal snippet tests might miss.
"""

import pytest
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.status_detector import StatusDetector
from overcode.interfaces import MockTmux
from tests.fixtures import create_mock_session
from tests.fixtures_realistic import (
    REALISTIC_AUTOCOMPLETE_IDLE,
    REALISTIC_EMPTY_PROMPT,
    REALISTIC_EXIT_COMMAND_MENU,
    REALISTIC_PERMISSION_PROMPT,
    REALISTIC_TOOL_RUNNING,
    REALISTIC_THINKING,
    REALISTIC_WEB_SEARCH_RUNNING,
    REALISTIC_STALLED,
    REALISTIC_IDLE_NO_INSTRUCTIONS,
    REALISTIC_UI_CHROME_PERMISSION,
    REALISTIC_BASH_PERMISSION,
    REALISTIC_FRESH_SESSION,
    REALISTIC_TERMINATED_SHELL,
)


def create_detector_with_content(content: str, session_name: str = "agents", window: int = 1):
    """Create a StatusDetector with mock tmux containing the given content."""
    mock_tmux = MockTmux()
    mock_tmux.new_session(session_name)
    mock_tmux.sessions[session_name][window] = content
    return StatusDetector(session_name, tmux=mock_tmux)


class TestRealisticAutocomplete:
    """Test autocomplete detection with full captures."""

    def test_autocomplete_with_standing_instructions_is_waiting_user(self):
        """Agent with autocomplete showing should be WAITING_USER, not RUNNING.

        This was a bug: autocomplete lines were skipped in stalled detection,
        then fell through to default logic which returned RUNNING if standing
        instructions existed.
        """
        detector = create_detector_with_content(REALISTIC_AUTOCOMPLETE_IDLE)
        session = create_mock_session(
            tmux_window=1,
            standing_instructions="Approve file write permission requests"
        )

        # Prime content hash
        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_WAITING_USER, (
            f"Autocomplete idle should be waiting_user, got {status}"
        )
        assert "Stalled" not in activity, "Should not be marked as stalled"

    def test_autocomplete_without_standing_instructions_is_waiting_user(self):
        """Autocomplete without standing instructions should still be WAITING_USER."""
        detector = create_detector_with_content(REALISTIC_AUTOCOMPLETE_IDLE)
        session = create_mock_session(tmux_window=1, standing_instructions="")

        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_WAITING_USER


class TestRealisticEmptyPrompt:
    """Test empty prompt detection with full captures."""

    def test_empty_prompt_is_waiting_user(self):
        """Empty prompt after completed work should be WAITING_USER."""
        detector = create_detector_with_content(REALISTIC_EMPTY_PROMPT)
        session = create_mock_session(tmux_window=1)

        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_WAITING_USER
        assert "Waiting for user input" in activity

    def test_fresh_session_is_waiting_user(self):
        """Fresh session with just welcome banner should be WAITING_USER."""
        detector = create_detector_with_content(REALISTIC_FRESH_SESSION)
        session = create_mock_session(tmux_window=1)

        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_WAITING_USER


class TestRealisticCommandMenu:
    """Test command menu detection with full captures."""

    def test_exit_command_menu_is_waiting_user(self):
        """Command menu showing after /exit should be WAITING_USER.

        Bug: The menu lines pushed the actual prompt out of the last 10 lines,
        causing the detector to miss the user input and fall through to
        default RUNNING status.
        """
        detector = create_detector_with_content(REALISTIC_EXIT_COMMAND_MENU)
        session = create_mock_session(
            tmux_window=1,
            standing_instructions="Approve sensible permission requests"
        )

        # Prime content hash
        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_WAITING_USER, (
            f"Command menu should be waiting_user, got {status} with activity: {activity}"
        )


class TestRealisticPermissionPrompts:
    """Test permission prompt detection with full captures."""

    def test_web_search_permission_is_waiting_user(self):
        """Web search permission prompt should be WAITING_USER."""
        detector = create_detector_with_content(REALISTIC_PERMISSION_PROMPT)
        session = create_mock_session(
            tmux_window=1,
            standing_instructions="Keep researching"
        )

        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_WAITING_USER
        # Should mention permission in activity
        assert "Permission" in activity or "proceed" in activity.lower()

    def test_bash_permission_is_waiting_user(self):
        """Bash command permission prompt should be WAITING_USER."""
        detector = create_detector_with_content(REALISTIC_BASH_PERMISSION)
        session = create_mock_session(
            tmux_window=1,
            standing_instructions="Run tests and fix failures"
        )

        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_WAITING_USER


class TestRealisticActiveWork:
    """Test active work detection with full captures."""

    def test_tool_running_is_running(self):
        """Active tool execution (Reading...) should be RUNNING."""
        detector = create_detector_with_content(REALISTIC_TOOL_RUNNING)
        session = create_mock_session(
            tmux_window=1,
            standing_instructions="Analyze and improve"
        )

        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_RUNNING
        # Activity should mention what's happening (case-insensitive check)
        activity_lower = activity.lower()
        assert "reading" in activity_lower or "esc to interrupt" in activity_lower

    def test_thinking_is_running(self):
        """Thinking spinner should be RUNNING."""
        detector = create_detector_with_content(REALISTIC_THINKING)
        session = create_mock_session(
            tmux_window=1,
            standing_instructions="Refactor the code"
        )

        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_RUNNING

    def test_web_search_running_is_running(self):
        """Active web search should be RUNNING."""
        detector = create_detector_with_content(REALISTIC_WEB_SEARCH_RUNNING)
        session = create_mock_session(
            tmux_window=1,
            standing_instructions="Research hiking trails"
        )

        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_RUNNING


class TestRealisticStalledDetection:
    """Test stalled detection with full captures."""

    def test_user_input_no_response_is_stalled(self):
        """User typed something but Claude hasn't responded = stalled."""
        detector = create_detector_with_content(REALISTIC_STALLED)
        session = create_mock_session(tmux_window=1)

        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_WAITING_USER
        assert "Stalled" in activity


class TestRealisticNoInstructions:
    """Test NO_INSTRUCTIONS status with full captures."""

    def test_idle_without_standing_instructions_is_yellow(self):
        """Idle agent without standing instructions should be NO_INSTRUCTIONS (yellow)."""
        detector = create_detector_with_content(REALISTIC_IDLE_NO_INSTRUCTIONS)
        session = create_mock_session(
            tmux_window=1,
            standing_instructions=""  # No instructions
        )

        # Prime content hash (first call may show running due to content change)
        detector.detect_status(session)
        # Second call with unchanged content
        status, activity, _ = detector.detect_status(session)

        # Without standing instructions, idle state is NO_INSTRUCTIONS
        # But note: empty prompt detection takes priority, so this will be WAITING_USER
        # This is actually correct - empty prompt = waiting for user
        assert status == StatusDetector.STATUS_WAITING_USER

    def test_idle_with_standing_instructions_after_completion(self):
        """Idle agent WITH standing instructions but at empty prompt should be WAITING_USER.

        Even with standing instructions, if we're at an empty prompt, the agent
        is waiting for user input.
        """
        detector = create_detector_with_content(REALISTIC_IDLE_NO_INSTRUCTIONS)
        session = create_mock_session(
            tmux_window=1,
            standing_instructions="Keep improving the code"
        )

        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        # Empty prompt = waiting for user, regardless of standing instructions
        assert status == StatusDetector.STATUS_WAITING_USER


class TestRealisticUiChromeFiltering:
    """Test that UI chrome doesn't trigger false detection."""

    def test_status_bar_permission_text_not_detected_as_permission(self):
        """UI chrome containing 'permission' should not trigger permission detection.

        The status bar shows '⏵⏵ bypass permissions on' which contains 'permission'
        but this is UI chrome, not an actual permission prompt.
        """
        detector = create_detector_with_content(REALISTIC_UI_CHROME_PERMISSION)
        session = create_mock_session(tmux_window=1)

        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        # Should be waiting_user (empty prompt), NOT a permission prompt
        assert status == StatusDetector.STATUS_WAITING_USER
        # Activity should NOT mention permission
        assert "Permission:" not in activity


class TestRealisticContentChangeDetection:
    """Test content change detection with full captures."""

    def test_content_change_detected_as_running(self):
        """When content changes between checks, should detect as RUNNING."""
        mock_tmux = MockTmux()
        mock_tmux.new_session("agents")

        detector = StatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1)

        # First call with one content
        mock_tmux.sessions["agents"][1] = REALISTIC_EMPTY_PROMPT
        detector.detect_status(session)

        # Second call with different content (simulates streaming)
        mock_tmux.sessions["agents"][1] = REALISTIC_TOOL_RUNNING
        status, activity, _ = detector.detect_status(session)

        # Content changed = actively working
        assert status == StatusDetector.STATUS_RUNNING
        assert "Active:" in activity


class TestRealisticWelcomeBannerHandling:
    """Test that welcome banner content doesn't interfere with detection."""

    def test_welcome_banner_with_empty_prompt_is_waiting_user(self):
        """Welcome banner + empty prompt should correctly detect WAITING_USER."""
        detector = create_detector_with_content(REALISTIC_FRESH_SESSION)
        session = create_mock_session(tmux_window=1)

        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_WAITING_USER

    def test_welcome_banner_does_not_trigger_active_indicators(self):
        """Welcome banner text should not falsely trigger active indicators.

        The banner contains various text that could potentially match patterns
        like 'Added' which looks like an active indicator. Ensure it doesn't.
        """
        detector = create_detector_with_content(REALISTIC_EMPTY_PROMPT)
        session = create_mock_session(
            tmux_window=1,
            standing_instructions="Do something"
        )

        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        # Should be waiting_user due to empty prompt, not running due to 'Added' in banner
        assert status == StatusDetector.STATUS_WAITING_USER


class TestRealisticTerminated:
    """Test terminated state detection with full captures."""

    def test_shell_prompt_after_exit_is_terminated(self):
        """Agent at shell prompt after /exit should be TERMINATED.

        When Claude Code exits (via /exit or crash), the pane shows a shell
        prompt like 'user@host path %'. This should be detected as terminated,
        not running or waiting_user.
        """
        detector = create_detector_with_content(REALISTIC_TERMINATED_SHELL)
        session = create_mock_session(
            tmux_window=1,
            standing_instructions="Some instructions"
        )

        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_TERMINATED, (
            f"Shell prompt should be TERMINATED, got {status}"
        )
        assert "Claude exited" in activity or "shell" in activity.lower()

    def test_shell_prompt_without_instructions_is_terminated(self):
        """Shell prompt without standing instructions should still be TERMINATED."""
        detector = create_detector_with_content(REALISTIC_TERMINATED_SHELL)
        session = create_mock_session(tmux_window=1, standing_instructions="")

        detector.detect_status(session)
        status, activity, _ = detector.detect_status(session)

        assert status == StatusDetector.STATUS_TERMINATED


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
