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
        mock_session.cost_budget_usd = 0.0

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


class TestCreateMonitorLogger:
    """Test _create_monitor_logger factory function."""

    def test_returns_base_daemon_logger_instance(self, tmp_path):
        """Should return a BaseDaemonLogger instance."""
        from overcode.monitor_daemon import _create_monitor_logger
        from overcode.daemon_logging import BaseDaemonLogger

        log_file = tmp_path / "test.log"
        logger = _create_monitor_logger(session="test", log_file=log_file)

        assert isinstance(logger, BaseDaemonLogger)
        assert logger.log_file == log_file

    def test_creates_default_log_file_path(self, tmp_path, monkeypatch):
        """Should create logger with default session-specific log path."""
        from overcode.monitor_daemon import _create_monitor_logger
        from overcode.daemon_logging import BaseDaemonLogger

        monkeypatch.setattr(
            'overcode.monitor_daemon.ensure_session_dir',
            lambda x: tmp_path
        )

        logger = _create_monitor_logger(session="test")

        assert isinstance(logger, BaseDaemonLogger)
        assert logger.log_file == tmp_path / "monitor_daemon.log"


class TestSyncClaudeCodeStats:
    """Test sync_claude_code_stats method."""

    def _make_daemon(self, tmp_path, monkeypatch):
        """Helper to create a minimal MonitorDaemon for testing."""
        from overcode.monitor_daemon import MonitorDaemon

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

        with patch('overcode.monitor_daemon.SessionManager') as mock_sm_cls:
            with patch('overcode.monitor_daemon.StatusDetector'):
                daemon = MonitorDaemon(tmux_session="test")
                daemon.session_manager = mock_sm_cls.return_value
        return daemon

    def test_updates_stats_when_session_stats_available(self, tmp_path, monkeypatch):
        """Should call update_stats with token and cost data from Claude history."""
        from overcode.monitor_daemon import MonitorDaemon
        from unittest.mock import MagicMock

        daemon = self._make_daemon(tmp_path, monkeypatch)

        mock_session = Mock()
        mock_session.id = "sess-1"
        mock_session.name = "agent-1"
        mock_session.start_directory = "/tmp/project"
        mock_session.start_time = datetime.now().isoformat()

        mock_stats = Mock()
        mock_stats.interaction_count = 10
        mock_stats.input_tokens = 5000
        mock_stats.output_tokens = 2000
        mock_stats.cache_creation_tokens = 100
        mock_stats.cache_read_tokens = 50

        monkeypatch.setattr(
            'overcode.monitor_daemon.get_session_stats',
            lambda s: mock_stats
        )
        monkeypatch.setattr(
            'overcode.monitor_daemon.get_current_session_id_for_directory',
            lambda d, s: "claude-sess-abc"
        )

        # Mock get_user_config - it's imported lazily inside sync_claude_code_stats
        mock_config = Mock()
        mock_config.price_input = 15.0
        mock_config.price_output = 75.0
        mock_config.price_cache_write = 18.75
        mock_config.price_cache_read = 1.50
        monkeypatch.setattr(
            'overcode.settings.get_user_config',
            lambda: mock_config
        )

        daemon.sync_claude_code_stats(mock_session)

        # Verify add_claude_session_id was called
        daemon.session_manager.add_claude_session_id.assert_called_once_with(
            "sess-1", "claude-sess-abc"
        )

        # Verify update_stats was called with correct values
        daemon.session_manager.update_stats.assert_called_once()
        call_kwargs = daemon.session_manager.update_stats.call_args[1]
        assert call_kwargs["interaction_count"] == 10
        assert call_kwargs["input_tokens"] == 5000
        assert call_kwargs["output_tokens"] == 2000
        assert call_kwargs["cache_creation_tokens"] == 100
        assert call_kwargs["cache_read_tokens"] == 50
        assert call_kwargs["total_tokens"] == 7150  # 5000+2000+100+50

    def test_returns_early_when_stats_are_none(self, tmp_path, monkeypatch):
        """Should return early when get_session_stats returns None."""
        daemon = self._make_daemon(tmp_path, monkeypatch)

        mock_session = Mock()
        mock_session.id = "sess-1"
        mock_session.name = "agent-1"
        mock_session.start_directory = "/tmp/project"
        mock_session.start_time = datetime.now().isoformat()

        monkeypatch.setattr(
            'overcode.monitor_daemon.get_session_stats',
            lambda s: None
        )
        monkeypatch.setattr(
            'overcode.monitor_daemon.get_current_session_id_for_directory',
            lambda d, s: None
        )

        daemon.sync_claude_code_stats(mock_session)

        # update_stats should not be called when stats are None
        daemon.session_manager.update_stats.assert_not_called()

    def test_handles_exception_gracefully(self, tmp_path, monkeypatch):
        """Should log warning and not raise on exception."""
        daemon = self._make_daemon(tmp_path, monkeypatch)

        mock_session = Mock()
        mock_session.id = "sess-1"
        mock_session.name = "agent-1"
        mock_session.start_directory = "/tmp/project"
        mock_session.start_time = datetime.now().isoformat()

        monkeypatch.setattr(
            'overcode.monitor_daemon.get_session_stats',
            Mock(side_effect=RuntimeError("disk failure"))
        )
        monkeypatch.setattr(
            'overcode.monitor_daemon.get_current_session_id_for_directory',
            lambda d, s: None
        )

        # Should not raise
        daemon.sync_claude_code_stats(mock_session)

    def test_skips_session_id_capture_without_start_directory(self, tmp_path, monkeypatch):
        """Should skip add_claude_session_id when session has no start_directory."""
        daemon = self._make_daemon(tmp_path, monkeypatch)

        mock_session = Mock()
        mock_session.id = "sess-1"
        mock_session.name = "agent-1"
        mock_session.start_directory = None  # No start dir
        mock_session.start_time = datetime.now().isoformat()

        monkeypatch.setattr(
            'overcode.monitor_daemon.get_session_stats',
            lambda s: None
        )

        daemon.sync_claude_code_stats(mock_session)

        # add_claude_session_id should not be called
        daemon.session_manager.add_claude_session_id.assert_not_called()


class TestUpdateStateTime:
    """Test _update_state_time method."""

    def _make_daemon(self, tmp_path, monkeypatch):
        """Helper to create a minimal MonitorDaemon for testing."""
        from overcode.monitor_daemon import MonitorDaemon

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

        with patch('overcode.monitor_daemon.SessionManager') as mock_sm_cls:
            with patch('overcode.monitor_daemon.StatusDetector'):
                daemon = MonitorDaemon(tmux_session="test")
                daemon.session_manager = mock_sm_cls.return_value
        return daemon

    def _make_session(self, session_id="sess-1", green_time=0.0, non_green_time=0.0,
                      sleep_time=0.0, state_since=None, last_time_accumulation=None,
                      start_time=None):
        """Helper to create a mock session."""
        mock_session = Mock()
        mock_session.id = session_id
        mock_session.name = "agent-1"
        mock_session.start_time = start_time or datetime.now().isoformat()

        mock_stats = Mock()
        mock_stats.green_time_seconds = green_time
        mock_stats.non_green_time_seconds = non_green_time
        mock_stats.sleep_time_seconds = sleep_time
        mock_stats.state_since = state_since or datetime.now().isoformat()
        mock_stats.last_time_accumulation = last_time_accumulation
        mock_session.stats = mock_stats

        return mock_session

    def test_first_observation_does_not_accumulate(self, tmp_path, monkeypatch):
        """First observation should set last_state_time but not accumulate."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        session = self._make_session()
        now = datetime.now()

        daemon._update_state_time(session, "running", now)

        # Should not call update_stats on first observation (just records baseline)
        daemon.session_manager.update_stats.assert_not_called()
        # last_state_times should now have an entry
        assert session.id in daemon.last_state_times

    def test_green_status_accumulates_green_time(self, tmp_path, monkeypatch):
        """Green status should increase green_time_seconds."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        session = self._make_session(
            green_time=100.0,
            non_green_time=50.0,
            start_time=(datetime.now() - timedelta(hours=1)).isoformat()
        )
        now = datetime.now()

        # Set up prior observation time (10 seconds ago)
        daemon.last_state_times[session.id] = now - timedelta(seconds=10)

        daemon._update_state_time(session, "running", now)

        # update_stats should be called
        daemon.session_manager.update_stats.assert_called_once()
        call_kwargs = daemon.session_manager.update_stats.call_args[1]
        # Green time should have increased by ~10 seconds
        assert call_kwargs["green_time_seconds"] > 100.0

    def test_non_green_status_accumulates_non_green_time(self, tmp_path, monkeypatch):
        """Non-green status should increase non_green_time_seconds."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        session = self._make_session(
            green_time=100.0,
            non_green_time=50.0,
            start_time=(datetime.now() - timedelta(hours=1)).isoformat()
        )
        now = datetime.now()

        daemon.last_state_times[session.id] = now - timedelta(seconds=10)

        daemon._update_state_time(session, "waiting_user", now)

        daemon.session_manager.update_stats.assert_called_once()
        call_kwargs = daemon.session_manager.update_stats.call_args[1]
        # Non-green time should have increased
        assert call_kwargs["non_green_time_seconds"] > 50.0

    def test_state_transition_detection(self, tmp_path, monkeypatch):
        """State transition should update state_since."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        session = self._make_session(
            green_time=100.0,
            non_green_time=50.0,
            state_since=(datetime.now() - timedelta(minutes=5)).isoformat(),
            start_time=(datetime.now() - timedelta(hours=1)).isoformat()
        )
        now = datetime.now()

        daemon.last_state_times[session.id] = now - timedelta(seconds=10)
        # Previous state was running, now waiting - state changed
        daemon.previous_states[session.id] = "running"

        daemon._update_state_time(session, "waiting_user", now)

        daemon.session_manager.update_stats.assert_called_once()
        call_kwargs = daemon.session_manager.update_stats.call_args[1]
        assert call_kwargs["current_state"] == "waiting_user"
        # state_since should be updated to now (state changed)
        assert call_kwargs["state_since"] == now.isoformat()


class TestCheckAndSendHeartbeats:
    """Test check_and_send_heartbeats method."""

    def _make_daemon(self, tmp_path, monkeypatch):
        """Helper to create a minimal MonitorDaemon for testing."""
        from overcode.monitor_daemon import MonitorDaemon

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

        with patch('overcode.monitor_daemon.SessionManager') as mock_sm_cls:
            with patch('overcode.monitor_daemon.StatusDetector'):
                daemon = MonitorDaemon(tmux_session="test")
                daemon.session_manager = mock_sm_cls.return_value
        return daemon

    def _make_heartbeat_session(self, session_id="sess-hb", enabled=True,
                                 paused=False, is_asleep=False,
                                 instruction="continue working",
                                 frequency=300, last_heartbeat=None,
                                 start_time=None, tmux_session="test",
                                 tmux_window=1):
        """Helper to create a mock session with heartbeat fields."""
        mock = Mock()
        mock.id = session_id
        mock.name = "agent-hb"
        mock.heartbeat_enabled = enabled
        mock.heartbeat_paused = paused
        mock.is_asleep = is_asleep
        mock.heartbeat_instruction = instruction
        mock.heartbeat_frequency_seconds = frequency
        mock.last_heartbeat_time = last_heartbeat
        mock.start_time = start_time or (datetime.now() - timedelta(hours=1)).isoformat()
        mock.tmux_session = tmux_session
        mock.tmux_window = tmux_window
        return mock

    def test_sends_heartbeat_when_due(self, tmp_path, monkeypatch):
        """Should send heartbeat when frequency interval has elapsed."""
        daemon = self._make_daemon(tmp_path, monkeypatch)

        # Last heartbeat was 10 minutes ago, frequency is 5 minutes
        session = self._make_heartbeat_session(
            frequency=300,
            last_heartbeat=(datetime.now() - timedelta(minutes=10)).isoformat()
        )

        with patch('overcode.monitor_daemon.send_text_to_tmux_window', return_value=True) as mock_send:
            result = daemon.check_and_send_heartbeats([session])

        assert session.id in result
        mock_send.assert_called_once_with(
            "test", 1, "continue working", send_enter=True
        )
        daemon.session_manager.update_session.assert_called_once()

    def test_does_not_send_heartbeat_when_not_due(self, tmp_path, monkeypatch):
        """Should not send heartbeat when interval has not elapsed."""
        daemon = self._make_daemon(tmp_path, monkeypatch)

        # Last heartbeat was 1 minute ago, frequency is 5 minutes
        session = self._make_heartbeat_session(
            frequency=300,
            last_heartbeat=(datetime.now() - timedelta(minutes=1)).isoformat()
        )

        with patch('overcode.monitor_daemon.send_text_to_tmux_window') as mock_send:
            result = daemon.check_and_send_heartbeats([session])

        assert len(result) == 0
        mock_send.assert_not_called()

    def test_skips_paused_heartbeat(self, tmp_path, monkeypatch):
        """Should skip sessions with heartbeat_paused=True."""
        daemon = self._make_daemon(tmp_path, monkeypatch)

        session = self._make_heartbeat_session(
            paused=True,
            last_heartbeat=(datetime.now() - timedelta(hours=1)).isoformat()
        )

        with patch('overcode.monitor_daemon.send_text_to_tmux_window') as mock_send:
            result = daemon.check_and_send_heartbeats([session])

        assert len(result) == 0
        mock_send.assert_not_called()

    def test_skips_sleeping_session(self, tmp_path, monkeypatch):
        """Should skip sessions that are asleep."""
        daemon = self._make_daemon(tmp_path, monkeypatch)

        session = self._make_heartbeat_session(
            is_asleep=True,
            last_heartbeat=(datetime.now() - timedelta(hours=1)).isoformat()
        )

        with patch('overcode.monitor_daemon.send_text_to_tmux_window') as mock_send:
            result = daemon.check_and_send_heartbeats([session])

        assert len(result) == 0
        mock_send.assert_not_called()

    def test_skips_session_without_instruction(self, tmp_path, monkeypatch):
        """Should skip sessions with no heartbeat_instruction."""
        daemon = self._make_daemon(tmp_path, monkeypatch)

        session = self._make_heartbeat_session(
            instruction="",
            last_heartbeat=(datetime.now() - timedelta(hours=1)).isoformat()
        )

        with patch('overcode.monitor_daemon.send_text_to_tmux_window') as mock_send:
            result = daemon.check_and_send_heartbeats([session])

        assert len(result) == 0
        mock_send.assert_not_called()

    def test_skips_disabled_heartbeat(self, tmp_path, monkeypatch):
        """Should skip sessions with heartbeat_enabled=False."""
        daemon = self._make_daemon(tmp_path, monkeypatch)

        session = self._make_heartbeat_session(
            enabled=False,
            last_heartbeat=(datetime.now() - timedelta(hours=1)).isoformat()
        )

        with patch('overcode.monitor_daemon.send_text_to_tmux_window') as mock_send:
            result = daemon.check_and_send_heartbeats([session])

        assert len(result) == 0
        mock_send.assert_not_called()

    def test_uses_start_time_when_no_last_heartbeat(self, tmp_path, monkeypatch):
        """Should fall back to session start_time when last_heartbeat_time is None."""
        daemon = self._make_daemon(tmp_path, monkeypatch)

        # No last_heartbeat_time, but start_time is 10 minutes ago
        session = self._make_heartbeat_session(
            frequency=300,
            last_heartbeat=None,
            start_time=(datetime.now() - timedelta(minutes=10)).isoformat()
        )

        with patch('overcode.monitor_daemon.send_text_to_tmux_window', return_value=True) as mock_send:
            result = daemon.check_and_send_heartbeats([session])

        assert session.id in result
        mock_send.assert_called_once()


class TestInterruptibleSleep:
    """Test _interruptible_sleep method."""

    def _make_daemon(self, tmp_path, monkeypatch):
        """Helper to create a minimal MonitorDaemon for testing."""
        from overcode.monitor_daemon import MonitorDaemon

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

        with patch('overcode.monitor_daemon.SessionManager') as mock_sm_cls:
            with patch('overcode.monitor_daemon.StatusDetector'):
                daemon = MonitorDaemon(tmux_session="test")
                daemon.session_manager = mock_sm_cls.return_value
        return daemon

    def test_returns_immediately_when_shutdown_set(self, tmp_path, monkeypatch):
        """Should return immediately if _shutdown is True."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon._shutdown = True

        with patch('overcode.monitor_daemon.time.sleep') as mock_sleep:
            daemon._interruptible_sleep(60)

        # Should not sleep at all since shutdown was already set
        mock_sleep.assert_not_called()

    def test_wakes_on_activity_signal(self, tmp_path, monkeypatch):
        """Should return early when activity signal is detected."""
        daemon = self._make_daemon(tmp_path, monkeypatch)

        call_count = [0]

        def mock_check_signal(session):
            call_count[0] += 1
            # Return True on first check (after first sleep chunk)
            return True

        monkeypatch.setattr(
            'overcode.monitor_daemon.check_activity_signal',
            mock_check_signal
        )

        with patch('overcode.monitor_daemon.time.sleep'):
            daemon._interruptible_sleep(60)

        # Should have checked activity signal and returned early
        assert call_count[0] >= 1

    def test_sleeps_full_duration_without_signals(self, tmp_path, monkeypatch):
        """Should sleep the full duration when no signals occur."""
        daemon = self._make_daemon(tmp_path, monkeypatch)

        monkeypatch.setattr(
            'overcode.monitor_daemon.check_activity_signal',
            lambda session: False
        )

        sleep_calls = []

        def track_sleep(seconds):
            sleep_calls.append(seconds)

        with patch('overcode.monitor_daemon.time.sleep', side_effect=track_sleep):
            daemon._interruptible_sleep(30)

        # Should have slept in 10-second chunks: 10 + 10 + 10 = 30
        assert sum(sleep_calls) == 30


class TestPublishState:
    """Test _publish_state method."""

    def _make_daemon(self, tmp_path, monkeypatch):
        """Helper to create a minimal MonitorDaemon for testing."""
        from overcode.monitor_daemon import MonitorDaemon

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
        monkeypatch.setattr(
            'overcode.monitor_daemon.get_supervisor_stats_path',
            lambda x: tmp_path / "supervisor_stats.json"
        )

        with patch('overcode.monitor_daemon.SessionManager') as mock_sm_cls:
            with patch('overcode.monitor_daemon.StatusDetector'):
                daemon = MonitorDaemon(tmux_session="test")
                daemon.session_manager = mock_sm_cls.return_value
        return daemon

    def test_saves_state_to_file(self, tmp_path, monkeypatch):
        """Should save MonitorDaemonState to the state JSON file."""
        from overcode.monitor_daemon import SessionDaemonState

        daemon = self._make_daemon(tmp_path, monkeypatch)

        session_state = SessionDaemonState(
            session_id="sess-1",
            name="agent-1",
            current_status="running",
            green_time_seconds=120.0,
        )

        daemon._publish_state([session_state])

        state_path = tmp_path / "state.json"
        assert state_path.exists()

        with open(state_path) as f:
            data = json.load(f)

        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["session_id"] == "sess-1"
        assert data["sessions"][0]["current_status"] == "running"
        assert data["last_loop_time"] is not None

    def test_reads_supervisor_stats_when_available(self, tmp_path, monkeypatch):
        """Should read supervisor stats from file when it exists."""
        daemon = self._make_daemon(tmp_path, monkeypatch)

        # Write supervisor stats file
        supervisor_stats = {
            "supervisor_launches": 5,
            "supervisor_tokens": 10000,
            "supervisor_claude_running": True,
            "supervisor_claude_started_at": "2025-01-01T12:00:00",
            "supervisor_claude_total_run_seconds": 300.0,
        }
        supervisor_path = tmp_path / "supervisor_stats.json"
        with open(supervisor_path, 'w') as f:
            json.dump(supervisor_stats, f)

        daemon._publish_state([])

        assert daemon.state.supervisor_launches == 5
        assert daemon.state.supervisor_tokens == 10000
        assert daemon.state.supervisor_claude_running is True
        assert daemon.state.supervisor_claude_total_run_seconds == 300.0

    def test_handles_missing_supervisor_stats(self, tmp_path, monkeypatch):
        """Should handle gracefully when supervisor stats file does not exist."""
        daemon = self._make_daemon(tmp_path, monkeypatch)

        # No supervisor stats file exists
        daemon._publish_state([])

        # Should still save state without error
        state_path = tmp_path / "state.json"
        assert state_path.exists()
        assert daemon.state.supervisor_launches == 0

    def test_updates_presence_state(self, tmp_path, monkeypatch):
        """Should update presence state from PresenceComponent."""
        daemon = self._make_daemon(tmp_path, monkeypatch)

        # Mock presence to return specific state
        daemon.presence = Mock()
        daemon.presence.available = True
        daemon.presence.get_current_state.return_value = (3, 5.0, False)

        daemon._publish_state([])

        assert daemon.state.presence_available is True
        assert daemon.state.presence_state == 3
        assert daemon.state.presence_idle_seconds == 5.0

    def test_calls_maybe_push_to_relay(self, tmp_path, monkeypatch):
        """Should call _maybe_push_to_relay after saving state."""
        daemon = self._make_daemon(tmp_path, monkeypatch)

        with patch.object(daemon, '_maybe_push_to_relay') as mock_relay:
            daemon._publish_state([])

        mock_relay.assert_called_once()


class TestMaybePushToRelay:
    """Test _maybe_push_to_relay method."""

    def _make_daemon(self, tmp_path, monkeypatch):
        """Helper to create a minimal MonitorDaemon for testing."""
        from overcode.monitor_daemon import MonitorDaemon

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

        with patch('overcode.monitor_daemon.SessionManager') as mock_sm_cls:
            with patch('overcode.monitor_daemon.StatusDetector'):
                daemon = MonitorDaemon(tmux_session="test")
                daemon.session_manager = mock_sm_cls.return_value
        return daemon

    def test_disabled_when_no_relay_config(self, tmp_path, monkeypatch):
        """Should set status to disabled when relay_config is None."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon._relay_config = None

        daemon._maybe_push_to_relay()

        assert daemon.state.relay_enabled is False
        assert daemon.state.relay_last_status == "disabled"

    def test_skips_push_when_interval_not_elapsed(self, tmp_path, monkeypatch):
        """Should not push when interval has not elapsed since last push."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon._relay_config = {"url": "https://example.com/update", "api_key": "key", "interval": 30}
        daemon._last_relay_push = datetime.now()  # Just pushed

        with patch('urllib.request.urlopen') as mock_urlopen:
            daemon._maybe_push_to_relay()

        # Should not have made a request (interval hasn't elapsed)
        mock_urlopen.assert_not_called()
        assert daemon.state.relay_enabled is True

    def test_successful_push(self, tmp_path, monkeypatch):
        """Should push state and update relay status on success."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon._relay_config = {"url": "https://example.com/update", "api_key": "secret", "interval": 30}
        daemon._last_relay_push = datetime.min  # Force push

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)

        mock_status_data = {"agents": [], "status": "active"}

        with patch('urllib.request.urlopen', return_value=mock_response) as mock_urlopen:
            with patch('overcode.web_api.get_status_data', return_value=mock_status_data):
                daemon._maybe_push_to_relay()

        assert daemon.state.relay_enabled is True
        assert daemon.state.relay_last_status == "ok"
        assert daemon.state.relay_last_push is not None

    def test_failed_push_sets_error_status(self, tmp_path, monkeypatch):
        """Should set error status on push failure."""
        import urllib.error
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon._relay_config = {"url": "https://example.com/update", "api_key": "secret", "interval": 30}
        daemon._last_relay_push = datetime.min  # Force push

        mock_status_data = {"agents": [], "status": "active"}

        with patch('urllib.request.urlopen', side_effect=urllib.error.URLError("connection refused")):
            with patch('overcode.web_api.get_status_data', return_value=mock_status_data):
                daemon._maybe_push_to_relay()

        assert daemon.state.relay_last_status == "error"

    def test_handles_generic_exception(self, tmp_path, monkeypatch):
        """Should handle generic exceptions during relay push."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon._relay_config = {"url": "https://example.com/update", "api_key": "secret", "interval": 30}
        daemon._last_relay_push = datetime.min  # Force push

        mock_status_data = {"agents": [], "status": "active"}

        with patch('urllib.request.urlopen', side_effect=RuntimeError("unexpected")):
            with patch('overcode.web_api.get_status_data', return_value=mock_status_data):
                daemon._maybe_push_to_relay()

        assert daemon.state.relay_last_status == "error"


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
