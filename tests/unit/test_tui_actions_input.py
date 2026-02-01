"""
Unit tests for TUI input actions.

Tests the helper functions that can be tested in isolation.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch


class TestIsFreetextOption:
    """Test _is_freetext_option helper method."""

    def _create_mixin_instance(self):
        """Create a minimal instance with _is_freetext_option method."""
        from overcode.tui_actions.input import InputActionsMixin

        # Create a mock object that has the mixin's methods
        instance = Mock()
        instance._is_freetext_option = InputActionsMixin._is_freetext_option.__get__(instance)
        return instance

    def test_detects_tell_claude_option(self):
        """Should detect 'tell claude what to do' option."""
        instance = self._create_mixin_instance()

        pane_content = """
1. Yes, proceed
2. No, don't do that
3. No, and tell Claude what to do differently (esc)
"""

        result = instance._is_freetext_option(pane_content, "3")
        assert result is True

    def test_returns_false_for_regular_option(self):
        """Should return False for non-freetext options."""
        instance = self._create_mixin_instance()

        pane_content = """
1. Yes, proceed
2. No, don't do that
3. No, and tell Claude what to do differently (esc)
"""

        result = instance._is_freetext_option(pane_content, "1")
        assert result is False

        result = instance._is_freetext_option(pane_content, "2")
        assert result is False

    def test_handles_different_numbering_formats(self):
        """Should handle various numbering formats."""
        instance = self._create_mixin_instance()

        # With period
        pane_content = "3. Tell Claude what to do"
        assert instance._is_freetext_option(pane_content, "3") is True

        # With parenthesis
        pane_content = "3) Tell Claude what to do"
        assert instance._is_freetext_option(pane_content, "3") is True

        # With colon
        pane_content = "3: Tell Claude what to do"
        assert instance._is_freetext_option(pane_content, "3") is True

    def test_case_insensitive_matching(self):
        """Should match case-insensitively."""
        instance = self._create_mixin_instance()

        pane_content = "3. TELL CLAUDE WHAT TO DO"
        assert instance._is_freetext_option(pane_content, "3") is True

        pane_content = "3. Tell Claude What To Do"
        assert instance._is_freetext_option(pane_content, "3") is True

    def test_returns_false_for_empty_content(self):
        """Should return False for empty pane content."""
        instance = self._create_mixin_instance()

        result = instance._is_freetext_option("", "3")
        assert result is False

    def test_returns_false_for_missing_option(self):
        """Should return False when option number not found."""
        instance = self._create_mixin_instance()

        pane_content = """
1. Yes, proceed
2. No, don't do that
"""

        result = instance._is_freetext_option(pane_content, "5")
        assert result is False

    def test_handles_multiline_content(self):
        """Should handle multiline pane content correctly."""
        instance = self._create_mixin_instance()

        pane_content = """
╭─ Bash ──────────────────────────────────────────────────────────────────────╮
│ rm -rf /tmp/test                                                            │
╰─────────────────────────────────────────────────────────────────────────────╯

1. Yes, allow this action
2. Yes, and don't ask again for this session
3. No, and tell Claude what to do differently (esc)
"""

        result = instance._is_freetext_option(pane_content, "3")
        assert result is True

    def test_handles_whitespace_in_options(self):
        """Should handle leading whitespace in option lines."""
        instance = self._create_mixin_instance()

        pane_content = """
   3. No, and tell Claude what to do differently (esc)
"""

        result = instance._is_freetext_option(pane_content, "3")
        assert result is True


class TestSendEnterToFocused:
    """Test action_send_enter_to_focused method."""

    def test_notifies_when_no_agent_focused(self):
        """Should notify when no agent is focused."""
        from overcode.tui_actions.input import InputActionsMixin

        # Create mock TUI instance
        mock_tui = MagicMock()
        mock_tui.focused = Mock()  # Not a SessionSummary
        mock_tui.notify = Mock()

        # Bind the mixin method
        InputActionsMixin.action_send_enter_to_focused(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "No agent focused" in mock_tui.notify.call_args[0][0]


class TestSendKeyToFocused:
    """Test _send_key_to_focused method."""

    def test_notifies_when_no_agent_focused(self):
        """Should notify when no agent is focused."""
        from overcode.tui_actions.input import InputActionsMixin

        mock_tui = MagicMock()
        mock_tui.focused = Mock()  # Not a SessionSummary
        mock_tui.notify = Mock()

        InputActionsMixin._send_key_to_focused(mock_tui, "1")

        mock_tui.notify.assert_called_once()
        assert "No agent focused" in mock_tui.notify.call_args[0][0]


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
