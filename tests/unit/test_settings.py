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


class TestBasePaths:
    """Test base path functions."""

    def test_get_overcode_dir_default(self):
        """get_overcode_dir should return ~/.overcode by default."""
        from overcode.settings import get_overcode_dir

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OVERCODE_DIR", None)
            result = get_overcode_dir()
            expected = Path.home() / ".overcode"
            assert result == expected

    def test_get_overcode_dir_respects_env(self, tmp_path):
        """get_overcode_dir should respect OVERCODE_DIR environment variable."""
        from overcode.settings import get_overcode_dir

        with patch.dict(os.environ, {"OVERCODE_DIR": str(tmp_path)}):
            result = get_overcode_dir()
            assert result == tmp_path

    def test_get_state_dir_default(self):
        """get_state_dir should return ~/.overcode/sessions by default."""
        from overcode.settings import get_state_dir

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OVERCODE_STATE_DIR", None)
            os.environ.pop("OVERCODE_DIR", None)
            result = get_state_dir()
            expected = Path.home() / ".overcode" / "sessions"
            assert result == expected

    def test_get_state_dir_respects_env(self, tmp_path):
        """get_state_dir should respect OVERCODE_STATE_DIR environment variable."""
        from overcode.settings import get_state_dir

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            result = get_state_dir()
            assert result == tmp_path

    def test_get_log_dir(self):
        """get_log_dir should return logs subdirectory."""
        from overcode.settings import get_log_dir

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OVERCODE_DIR", None)
            result = get_log_dir()
            expected = Path.home() / ".overcode" / "logs"
            assert result == expected


class TestOvercodePaths:
    """Test OvercodePaths dataclass."""

    def test_paths_default_values(self):
        """OvercodePaths should have sensible defaults."""
        from overcode.settings import OvercodePaths

        paths = OvercodePaths()
        assert paths.base_dir == Path.home() / ".overcode"

    def test_paths_config_file(self):
        """config_file property should return correct path."""
        from overcode.settings import OvercodePaths

        paths = OvercodePaths()
        assert paths.config_file == paths.base_dir / "config.yaml"

    def test_paths_sessions_file(self, tmp_path):
        """sessions_file property should return correct path."""
        from overcode.settings import OvercodePaths

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            paths = OvercodePaths()
            result = paths.sessions_file
            assert result == tmp_path / "sessions.json"

    def test_paths_daemon_log(self):
        """daemon_log property should return correct path."""
        from overcode.settings import OvercodePaths

        paths = OvercodePaths()
        assert paths.daemon_log == paths.base_dir / "daemon.log"

    def test_paths_daemon_pid(self):
        """daemon_pid property should return correct path."""
        from overcode.settings import OvercodePaths

        paths = OvercodePaths()
        assert paths.daemon_pid == paths.base_dir / "daemon.pid"

    def test_paths_monitor_daemon_state(self):
        """monitor_daemon_state property should return correct path."""
        from overcode.settings import OvercodePaths

        paths = OvercodePaths()
        assert paths.monitor_daemon_state == paths.base_dir / "monitor_daemon_state.json"

    def test_paths_presence_log(self):
        """presence_log property should return correct path."""
        from overcode.settings import OvercodePaths

        paths = OvercodePaths()
        assert paths.presence_log == paths.base_dir / "presence_log.csv"

    def test_paths_agent_history(self):
        """agent_history property should return correct path."""
        from overcode.settings import OvercodePaths

        paths = OvercodePaths()
        assert paths.agent_history == paths.base_dir / "agent_status_history.csv"


class TestDaemonSettings:
    """Test DaemonSettings dataclass."""

    def test_default_intervals(self):
        """DaemonSettings should have sensible default intervals."""
        from overcode.settings import DaemonSettings

        settings = DaemonSettings()
        assert settings.interval_fast == 10
        assert settings.interval_slow == 300
        assert settings.interval_idle == 3600

    def test_daemon_claude_settings(self):
        """DaemonSettings should have daemon claude settings."""
        from overcode.settings import DaemonSettings

        settings = DaemonSettings()
        assert settings.daemon_claude_timeout == 300
        assert settings.daemon_claude_poll == 5

    def test_default_tmux_session(self):
        """DaemonSettings should have default tmux session."""
        from overcode.settings import DaemonSettings

        settings = DaemonSettings()
        assert settings.default_tmux_session == "agents"


class TestPresenceSettings:
    """Test PresenceSettings dataclass."""

    def test_default_intervals(self):
        """PresenceSettings should have sensible defaults."""
        from overcode.settings import PresenceSettings

        settings = PresenceSettings()
        assert settings.sample_interval == 60
        assert settings.idle_threshold == 60


class TestTUISettings:
    """Test TUISettings dataclass."""

    def test_has_refresh_interval(self):
        """TUISettings should have refresh_interval."""
        from overcode.settings import TUISettings

        settings = TUISettings()
        assert hasattr(settings, 'refresh_interval')


class TestSessionHelpers:
    """Test session-related helper functions."""

    def test_get_agent_history_path(self, tmp_path):
        """get_agent_history_path should return correct path."""
        from overcode.settings import get_agent_history_path

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            result = get_agent_history_path("my-session")
            expected = tmp_path / "my-session" / "agent_status_history.csv"
            assert result == expected

    def test_get_activity_signal_path(self, tmp_path):
        """get_activity_signal_path should return correct path."""
        from overcode.settings import get_activity_signal_path

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            result = get_activity_signal_path("my-session")
            expected = tmp_path / "my-session" / "activity_signal"
            assert result == expected

    def test_get_supervisor_stats_path(self, tmp_path):
        """get_supervisor_stats_path should return correct path."""
        from overcode.settings import get_supervisor_stats_path

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            result = get_supervisor_stats_path("my-session")
            expected = tmp_path / "my-session" / "supervisor_stats.json"
            assert result == expected

    def test_get_supervisor_log_path(self, tmp_path):
        """get_supervisor_log_path should return correct path."""
        from overcode.settings import get_supervisor_log_path

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            result = get_supervisor_log_path("my-session")
            expected = tmp_path / "my-session" / "supervisor.log"
            assert result == expected

    def test_get_web_server_pid_path(self, tmp_path):
        """get_web_server_pid_path should return correct path."""
        from overcode.settings import get_web_server_pid_path

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            result = get_web_server_pid_path("my-session")
            expected = tmp_path / "my-session" / "web_server.pid"
            assert result == expected

    def test_get_web_server_port_path(self, tmp_path):
        """get_web_server_port_path should return correct path."""
        from overcode.settings import get_web_server_port_path

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            result = get_web_server_port_path("my-session")
            expected = tmp_path / "my-session" / "web_server.port"
            assert result == expected

    def test_get_tui_preferences_path(self, tmp_path):
        """get_tui_preferences_path should return correct path."""
        from overcode.settings import get_tui_preferences_path

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            result = get_tui_preferences_path("my-session")
            expected = tmp_path / "my-session" / "tui_preferences.json"
            assert result == expected

    def test_ensure_session_dir_creates_directory(self, tmp_path):
        """ensure_session_dir should create the directory."""
        from overcode.settings import ensure_session_dir

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            result = ensure_session_dir("new-session")
            expected = tmp_path / "new-session"
            assert result == expected
            assert expected.exists()
            assert expected.is_dir()


class TestSignalActivity:
    """Test signal_activity function."""

    def test_signal_activity_creates_file(self, tmp_path):
        """signal_activity should create signal file."""
        from overcode.settings import signal_activity, get_activity_signal_path

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            # Create session dir
            session_dir = tmp_path / "test-session"
            session_dir.mkdir()

            signal_activity("test-session")

            signal_path = get_activity_signal_path("test-session")
            assert signal_path.exists()

    def test_signal_activity_uses_default_session(self, tmp_path):
        """signal_activity should use default session when None."""
        from overcode.settings import signal_activity, DAEMON

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            # Create session dir with default name
            session_dir = tmp_path / DAEMON.default_tmux_session
            session_dir.mkdir()

            # Should not raise
            signal_activity()


class TestUserConfig:
    """Test UserConfig and related functions."""

    def test_get_user_config_returns_config(self):
        """get_user_config should return UserConfig instance."""
        from overcode.settings import get_user_config, UserConfig

        config = get_user_config()
        assert isinstance(config, UserConfig)

    def test_reload_user_config_returns_config(self):
        """reload_user_config should return UserConfig instance."""
        from overcode.settings import reload_user_config, UserConfig

        config = reload_user_config()
        assert isinstance(config, UserConfig)

    def test_get_default_standing_instructions(self):
        """get_default_standing_instructions should return string."""
        from overcode.settings import get_default_standing_instructions

        result = get_default_standing_instructions()
        assert isinstance(result, str)

    def test_get_default_tmux_session(self):
        """get_default_tmux_session should return string."""
        from overcode.settings import get_default_tmux_session

        result = get_default_tmux_session()
        assert isinstance(result, str)
        assert result == "agents"  # Default value


class TestTUIPreferences:
    """Test TUIPreferences dataclass."""

    def test_default_values(self):
        """TUIPreferences should have sensible defaults."""
        from overcode.settings import TUIPreferences

        prefs = TUIPreferences()
        assert hasattr(prefs, 'show_terminated')
        assert isinstance(prefs.show_terminated, bool)

    def test_has_summary_detail(self):
        """TUIPreferences should have summary_detail field."""
        from overcode.settings import TUIPreferences

        prefs = TUIPreferences()
        assert hasattr(prefs, 'summary_detail')
        assert prefs.summary_detail in ["low", "med", "full"]

    def test_has_view_mode(self):
        """TUIPreferences should have view_mode field."""
        from overcode.settings import TUIPreferences

        prefs = TUIPreferences()
        assert hasattr(prefs, 'view_mode')
        assert prefs.view_mode in ["tree", "list_preview"]

    def test_load_returns_default_when_no_file(self, tmp_path):
        """TUIPreferences.load should return defaults when file doesn't exist."""
        from overcode.settings import TUIPreferences

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            prefs = TUIPreferences.load("nonexistent-session")
            assert isinstance(prefs, TUIPreferences)
            assert prefs.show_terminated is False  # Default value

    def test_save_creates_file(self, tmp_path):
        """TUIPreferences.save should create preferences file."""
        from overcode.settings import TUIPreferences, ensure_session_dir

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            ensure_session_dir("test-session")
            prefs = TUIPreferences(show_terminated=True)
            prefs.save("test-session")

            prefs_file = tmp_path / "test-session" / "tui_preferences.json"
            assert prefs_file.exists()

    def test_load_reads_saved_values(self, tmp_path):
        """TUIPreferences.load should read saved values."""
        from overcode.settings import TUIPreferences, ensure_session_dir

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            ensure_session_dir("test-session")

            # Save preferences
            original = TUIPreferences(show_terminated=True, view_mode="list_preview")
            original.save("test-session")

            # Load and verify
            loaded = TUIPreferences.load("test-session")
            assert loaded.show_terminated is True
            assert loaded.view_mode == "list_preview"

    def test_has_summary_groups(self):
        """TUIPreferences should have summary_groups field for column visibility (#178)."""
        from overcode.settings import TUIPreferences

        prefs = TUIPreferences()
        assert hasattr(prefs, 'summary_groups')
        assert isinstance(prefs.summary_groups, dict)
        # All toggleable groups should be present
        expected_groups = ["time", "tokens", "git", "supervision", "priority", "performance"]
        for group_id in expected_groups:
            assert group_id in prefs.summary_groups
            assert isinstance(prefs.summary_groups[group_id], bool)

    def test_summary_groups_persist(self, tmp_path):
        """TUIPreferences should persist summary_groups settings (#178)."""
        from overcode.settings import TUIPreferences, ensure_session_dir

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            ensure_session_dir("test-session")

            # Save preferences with modified summary_groups
            original = TUIPreferences()
            original.summary_groups = {
                "time": False,
                "tokens": True,
                "git": False,
                "supervision": True,
                "priority": False,
                "performance": True,
                "activity": True,
            }
            original.save("test-session")

            # Load and verify
            loaded = TUIPreferences.load("test-session")
            assert loaded.summary_groups["time"] is False
            assert loaded.summary_groups["tokens"] is True
            assert loaded.summary_groups["git"] is False
            assert loaded.summary_groups["supervision"] is True
            assert loaded.summary_groups["priority"] is False
            assert loaded.summary_groups["performance"] is True
            assert loaded.summary_groups["activity"] is True


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
