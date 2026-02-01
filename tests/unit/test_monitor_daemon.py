"""
Unit tests for Monitor Daemon.

These tests verify the helper functions and state management
without running the actual daemon loop.
"""

import json
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch


class TestCalculateMedianWorkTime:
    """Test _calculate_median_work_time method."""

    def test_empty_list_returns_zero(self):
        """Empty operation times should return 0."""
        from overcode.monitor_daemon import MonitorDaemon

        with patch.object(MonitorDaemon, '__init__', lambda self: None):
            daemon = MonitorDaemon.__new__(MonitorDaemon)
            result = daemon._calculate_median_work_time([])

            assert result == 0.0

    def test_single_value_returns_that_value(self):
        """Single operation time should return that value."""
        from overcode.monitor_daemon import MonitorDaemon

        with patch.object(MonitorDaemon, '__init__', lambda self: None):
            daemon = MonitorDaemon.__new__(MonitorDaemon)
            result = daemon._calculate_median_work_time([42.0])

            assert result == 42.0

    def test_odd_count_returns_middle(self):
        """Odd count should return middle value."""
        from overcode.monitor_daemon import MonitorDaemon

        with patch.object(MonitorDaemon, '__init__', lambda self: None):
            daemon = MonitorDaemon.__new__(MonitorDaemon)
            result = daemon._calculate_median_work_time([10.0, 20.0, 30.0])

            assert result == 20.0

    def test_even_count_returns_average_of_middle_two(self):
        """Even count should return average of middle two."""
        from overcode.monitor_daemon import MonitorDaemon

        with patch.object(MonitorDaemon, '__init__', lambda self: None):
            daemon = MonitorDaemon.__new__(MonitorDaemon)
            result = daemon._calculate_median_work_time([10.0, 20.0, 30.0, 40.0])

            assert result == 25.0

    def test_unsorted_input_still_works(self):
        """Should sort input before calculating median."""
        from overcode.monitor_daemon import MonitorDaemon

        with patch.object(MonitorDaemon, '__init__', lambda self: None):
            daemon = MonitorDaemon.__new__(MonitorDaemon)
            result = daemon._calculate_median_work_time([30.0, 10.0, 20.0])

            assert result == 20.0


class TestCheckActivitySignal:
    """Test check_activity_signal function."""

    def test_returns_true_when_signal_exists(self, tmp_path, monkeypatch):
        """Should return True and delete file when signal exists."""
        from overcode.monitor_daemon import check_activity_signal

        signal_file = tmp_path / "activity_signal"
        signal_file.touch()

        monkeypatch.setattr(
            'overcode.monitor_daemon.get_activity_signal_path',
            lambda session: signal_file
        )

        result = check_activity_signal("test")

        assert result is True
        assert not signal_file.exists()

    def test_returns_false_when_no_signal(self, tmp_path, monkeypatch):
        """Should return False when signal file doesn't exist."""
        from overcode.monitor_daemon import check_activity_signal

        signal_file = tmp_path / "activity_signal"
        # Don't create the file

        monkeypatch.setattr(
            'overcode.monitor_daemon.get_activity_signal_path',
            lambda session: signal_file
        )

        result = check_activity_signal("test")

        assert result is False

    def test_returns_false_on_permission_error(self, tmp_path, monkeypatch):
        """Should return False on permission errors."""
        from overcode.monitor_daemon import check_activity_signal

        signal_file = tmp_path / "activity_signal"
        signal_file.touch()  # Create the file

        # Mock to raise OSError
        original_unlink = Path.unlink

        def mock_unlink(self, missing_ok=False):
            if self == signal_file:
                raise OSError("Permission denied")
            return original_unlink(self, missing_ok=missing_ok)

        monkeypatch.setattr(
            'overcode.monitor_daemon.get_activity_signal_path',
            lambda session: signal_file
        )
        monkeypatch.setattr(Path, 'unlink', mock_unlink)

        result = check_activity_signal("test")

        assert result is False


class TestTrackSessionStats:
    """Test track_session_stats method."""

    def test_creates_session_daemon_state(self, tmp_path, monkeypatch):
        """Should create SessionDaemonState with correct fields."""
        from overcode.monitor_daemon import MonitorDaemon, SessionDaemonState

        # Setup minimal daemon
        monkeypatch.setattr('overcode.monitor_daemon.ensure_session_dir', lambda x: tmp_path)
        monkeypatch.setattr(
            'overcode.monitor_daemon.get_monitor_daemon_pid_path',
            lambda x: tmp_path / "pid"
        )
        monkeypatch.setattr(
            'overcode.monitor_daemon.get_monitor_daemon_state_path',
            lambda x: tmp_path / "state.json"
        )
        monkeypatch.setattr(
            'overcode.monitor_daemon.get_agent_history_path',
            lambda x: tmp_path / "history.csv"
        )

        with patch('overcode.monitor_daemon.SessionManager'):
            with patch('overcode.monitor_daemon.StatusDetector'):
                daemon = MonitorDaemon(tmux_session="test")

        # Create mock session
        mock_session = Mock()
        mock_session.id = "test-session-id"
        mock_session.name = "test-agent"
        mock_session.tmux_window = 1
        mock_session.repo_name = "test-repo"
        mock_session.branch = "main"
        mock_session.standing_instructions = None
        mock_session.standing_orders_complete = False
        mock_session.start_time = datetime.now().isoformat()
        mock_session.permissiveness_mode = "normal"
        mock_session.start_directory = "/tmp/test"
        mock_session.is_asleep = False
        mock_session.agent_value = 1.0

        mock_stats = Mock()
        mock_stats.current_task = "doing stuff"
        mock_stats.state_since = datetime.now().isoformat()
        mock_stats.green_time_seconds = 100.0
        mock_stats.non_green_time_seconds = 50.0
        mock_stats.interaction_count = 5
        mock_stats.input_tokens = 1000
        mock_stats.output_tokens = 500
        mock_stats.cache_creation_tokens = 0
        mock_stats.cache_read_tokens = 0
        mock_stats.estimated_cost_usd = 0.05
        mock_stats.operation_times = [30.0, 60.0, 45.0]
        mock_stats.steers_count = 2
        mock_stats.last_time_accumulation = None

        mock_session.stats = mock_stats

        result = daemon.track_session_stats(mock_session, "running")

        assert isinstance(result, SessionDaemonState)
        assert result.session_id == "test-session-id"
        assert result.name == "test-agent"
        assert result.current_status == "running"
        assert result.green_time_seconds == 100.0


class TestCalculateInterval:
    """Test calculate_interval method."""

    def test_always_returns_fast_interval(self, tmp_path, monkeypatch):
        """Monitor daemon always uses fast interval."""
        from overcode.monitor_daemon import MonitorDaemon, INTERVAL_FAST

        monkeypatch.setattr('overcode.monitor_daemon.ensure_session_dir', lambda x: tmp_path)
        monkeypatch.setattr(
            'overcode.monitor_daemon.get_monitor_daemon_pid_path',
            lambda x: tmp_path / "pid"
        )
        monkeypatch.setattr(
            'overcode.monitor_daemon.get_monitor_daemon_state_path',
            lambda x: tmp_path / "state.json"
        )
        monkeypatch.setattr(
            'overcode.monitor_daemon.get_agent_history_path',
            lambda x: tmp_path / "history.csv"
        )

        with patch('overcode.monitor_daemon.SessionManager'):
            with patch('overcode.monitor_daemon.StatusDetector'):
                daemon = MonitorDaemon(tmux_session="test")

        result = daemon.calculate_interval([], all_waiting_user=True)

        assert result == INTERVAL_FAST

        result2 = daemon.calculate_interval([Mock()], all_waiting_user=False)

        assert result2 == INTERVAL_FAST


class TestPresenceComponent:
    """Test PresenceComponent class."""

    def test_get_current_state_returns_none_when_unavailable(self):
        """Should return (None, None, None) when APIs unavailable."""
        from overcode.monitor_daemon import PresenceComponent

        with patch('overcode.monitor_daemon.MACOS_APIS_AVAILABLE', False):
            component = PresenceComponent()
            result = component.get_current_state()

            assert result == (None, None, None)

    def test_stop_handles_no_logger(self):
        """Should handle stop when no logger initialized."""
        from overcode.monitor_daemon import PresenceComponent

        with patch('overcode.monitor_daemon.MACOS_APIS_AVAILABLE', False):
            component = PresenceComponent()
            component.stop()  # Should not raise


class TestMonitorDaemonState:
    """Test MonitorDaemonState dataclass."""

    def test_save_and_load(self, tmp_path):
        """Should serialize and deserialize correctly."""
        from overcode.monitor_daemon_state import MonitorDaemonState

        state = MonitorDaemonState(
            pid=12345,
            status="active",
            started_at=datetime.now().isoformat(),
        )

        state_path = tmp_path / "state.json"
        state.save(state_path)

        # Verify file was created
        assert state_path.exists()

        # Verify content
        with open(state_path) as f:
            data = json.load(f)

        assert data["pid"] == 12345
        assert data["status"] == "active"

    def test_is_stale_returns_true_when_old(self, tmp_path):
        """Should return True when state is older than threshold."""
        from overcode.monitor_daemon_state import MonitorDaemonState

        old_time = (datetime.now() - timedelta(seconds=90)).isoformat()
        state = MonitorDaemonState(
            pid=12345,
            status="active",
            started_at=old_time,
            last_loop_time=old_time,
        )

        assert state.is_stale() is True

    def test_is_stale_returns_false_when_recent(self, tmp_path):
        """Should return False when state is recent."""
        from overcode.monitor_daemon_state import MonitorDaemonState

        recent_time = datetime.now().isoformat()
        state = MonitorDaemonState(
            pid=12345,
            status="active",
            started_at=recent_time,
            last_loop_time=recent_time,
        )

        assert state.is_stale() is False


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
