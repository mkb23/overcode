"""
Unit tests for settings module.

Tests configuration, path management, and environment variable handling.
"""

import os
import pytest
from pathlib import Path
from unittest.mock import patch


class TestSessionPaths:
    """Test session-specific path handling."""

    def test_get_session_dir_default(self):
        """get_session_dir should return ~/.overcode/sessions/{session} by default."""
        from overcode.settings import get_session_dir

        # Clear env var if set
        with patch.dict(os.environ, {}, clear=True):
            # Also need to clear OVERCODE_STATE_DIR
            os.environ.pop("OVERCODE_STATE_DIR", None)
            os.environ.pop("OVERCODE_DIR", None)

            result = get_session_dir("agents")
            expected = Path.home() / ".overcode" / "sessions" / "agents"
            assert result == expected

    def test_get_session_dir_respects_state_dir_env(self, tmp_path):
        """get_session_dir should respect OVERCODE_STATE_DIR environment variable."""
        from overcode.settings import get_session_dir

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            result = get_session_dir("test-session")
            expected = tmp_path / "test-session"
            assert result == expected

    def test_monitor_daemon_pid_path_respects_state_dir(self, tmp_path):
        """Monitor daemon PID path should be inside session dir."""
        from overcode.settings import get_monitor_daemon_pid_path

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            result = get_monitor_daemon_pid_path("my-session")
            expected = tmp_path / "my-session" / "monitor_daemon.pid"
            assert result == expected

    def test_supervisor_daemon_pid_path_respects_state_dir(self, tmp_path):
        """Supervisor daemon PID path should be inside session dir."""
        from overcode.settings import get_supervisor_daemon_pid_path

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            result = get_supervisor_daemon_pid_path("my-session")
            expected = tmp_path / "my-session" / "supervisor_daemon.pid"
            assert result == expected


class TestDaemonIsolation:
    """Test that daemons are properly isolated per session."""

    def test_different_sessions_have_different_pid_paths(self, tmp_path):
        """Each session should have its own daemon PID files."""
        from overcode.settings import (
            get_monitor_daemon_pid_path,
            get_supervisor_daemon_pid_path,
        )

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            session1_monitor = get_monitor_daemon_pid_path("session-1")
            session2_monitor = get_monitor_daemon_pid_path("session-2")
            session1_supervisor = get_supervisor_daemon_pid_path("session-1")
            session2_supervisor = get_supervisor_daemon_pid_path("session-2")

            # All should be different
            paths = [session1_monitor, session2_monitor, session1_supervisor, session2_supervisor]
            assert len(set(paths)) == 4, "All PID paths should be unique"

            # Check expected structure
            assert session1_monitor.parent.name == "session-1"
            assert session2_monitor.parent.name == "session-2"


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
