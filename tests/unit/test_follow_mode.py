"""
Unit tests for follow mode (#244).

Tests the incremental pane capture, deduplication, Stop detection,
and terminated detection.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from overcode.follow_mode import _check_hook_stop, _check_session_terminated
from overcode.session_manager import SessionManager


class TestCheckHookStop:
    """Test Stop detection via hook state file."""

    def test_returns_true_on_stop_event(self, tmp_path):
        """Should detect Stop event in hook state file."""
        # Create a mock session dir and hook state file
        hook_state = tmp_path / "hook_state_my-agent.json"
        hook_state.write_text(json.dumps({"event": "Stop"}))

        with patch("overcode.follow_mode.get_session_dir", return_value=tmp_path):
            result = _check_hook_stop("agents", "my-agent")

        assert result is True

    def test_returns_false_on_other_event(self, tmp_path):
        """Should return False for non-Stop events."""
        hook_state = tmp_path / "hook_state_my-agent.json"
        hook_state.write_text(json.dumps({"event": "UserPromptSubmit"}))

        with patch("overcode.follow_mode.get_session_dir", return_value=tmp_path):
            result = _check_hook_stop("agents", "my-agent")

        assert result is False

    def test_returns_false_when_file_missing(self, tmp_path):
        """Should return False when hook state file doesn't exist."""
        with patch("overcode.follow_mode.get_session_dir", return_value=tmp_path):
            result = _check_hook_stop("agents", "my-agent")

        assert result is False

    def test_returns_false_on_corrupt_json(self, tmp_path):
        """Should return False on corrupt JSON."""
        hook_state = tmp_path / "hook_state_my-agent.json"
        hook_state.write_text("not valid json")

        with patch("overcode.follow_mode.get_session_dir", return_value=tmp_path):
            result = _check_hook_stop("agents", "my-agent")

        assert result is False


class TestCheckSessionTerminated:
    """Test session terminated detection."""

    def test_returns_true_when_session_not_found(self, tmp_path):
        """Should return True when session doesn't exist."""
        manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        result = _check_session_terminated(manager, "nonexistent")

        assert result is True

    def test_returns_true_when_terminated(self, tmp_path):
        """Should return True when session status is 'terminated'."""
        manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        session = manager.create_session(
            name="test-agent", tmux_session="agents", tmux_window=1, command=["claude"]
        )
        manager.update_session_status(session.id, "terminated")

        result = _check_session_terminated(manager, "test-agent")

        assert result is True

    def test_returns_false_when_running(self, tmp_path):
        """Should return False when session is running."""
        manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        manager.create_session(
            name="test-agent", tmux_session="agents", tmux_window=1, command=["claude"]
        )

        result = _check_session_terminated(manager, "test-agent")

        assert result is False


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
