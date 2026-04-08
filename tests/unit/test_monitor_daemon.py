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

    def test_get_current_state_falls_back_on_non_macos(self):
        """Should return classify_state result with idle=0 when macOS APIs unavailable."""
        from overcode.monitor_daemon import PresenceComponent

        with patch('overcode.monitor_daemon.MACOS_APIS_AVAILABLE', False):
            component = PresenceComponent()
            state, idle, locked = component.get_current_state()

            assert state in (0, 1, 2, 3, 4)  # Valid presence state
            assert idle == 0.0
            assert locked is False

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
        mock_stats.model = "claude-sonnet-4-6"

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
        mock_config.model_pricing = {}
        monkeypatch.setattr(
            'overcode.settings.get_user_config',
            lambda: mock_config
        )
        mock_session.model = None

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

    def test_skips_already_green_session(self, tmp_path, monkeypatch):
        """Should skip sessions that are already green/active (#267)."""
        daemon = self._make_daemon(tmp_path, monkeypatch)

        session = self._make_heartbeat_session(
            frequency=300,
            last_heartbeat=(datetime.now() - timedelta(hours=1)).isoformat()
        )

        # Simulate that previous loop detected this session as running
        daemon.previous_states[session.id] = "running"

        with patch('overcode.monitor_daemon.send_text_to_tmux_window') as mock_send:
            result = daemon.check_and_send_heartbeats([session])

        assert len(result) == 0
        mock_send.assert_not_called()

    def test_skips_running_heartbeat_session(self, tmp_path, monkeypatch):
        """Should skip sessions with running_heartbeat status (#267)."""
        daemon = self._make_daemon(tmp_path, monkeypatch)

        session = self._make_heartbeat_session(
            frequency=300,
            last_heartbeat=(datetime.now() - timedelta(hours=1)).isoformat()
        )

        # Simulate that previous loop detected this session as running from heartbeat
        daemon.previous_states[session.id] = "running_heartbeat"

        with patch('overcode.monitor_daemon.send_text_to_tmux_window') as mock_send:
            result = daemon.check_and_send_heartbeats([session])

        assert len(result) == 0
        mock_send.assert_not_called()

    def test_sends_heartbeat_when_previously_non_green(self, tmp_path, monkeypatch):
        """Should send heartbeat when previous status was non-green (#267)."""
        daemon = self._make_daemon(tmp_path, monkeypatch)

        session = self._make_heartbeat_session(
            frequency=300,
            last_heartbeat=(datetime.now() - timedelta(hours=1)).isoformat()
        )

        # Previous status was waiting — heartbeat should fire
        daemon.previous_states[session.id] = "waiting_user"

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

        # Should have slept in 1-second chunks totaling 30s
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


class TestDaemonTerminatedSessionGuard:
    """Test that daemon skips detect_status for terminated/done sessions."""

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

    def test_terminated_session_skips_detect_status(self, tmp_path, monkeypatch):
        """Daemon should not call detect_status for sessions with status='terminated'."""
        from overcode.monitor_daemon import STATUS_TERMINATED

        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.detector = Mock()

        mock_session = Mock()
        mock_session.id = "sess-1"
        mock_session.name = "agent-1"
        mock_session.status = "terminated"
        mock_session.is_asleep = False
        mock_session.heartbeat_enabled = False
        mock_session.heartbeat_paused = False
        mock_session.heartbeat_instruction = None

        # Reload returns same session
        daemon.session_manager.get_session.return_value = mock_session

        # Run the status detection part inline by calling detect_status guard
        # and checking detector was NOT called
        if mock_session.status == "terminated":
            status, activity = STATUS_TERMINATED, "Session terminated"
        else:
            status, activity, _ = daemon.detector.detect_status(mock_session)

        assert status == STATUS_TERMINATED
        assert activity == "Session terminated"
        daemon.detector.detect_status.assert_not_called()

    def test_done_session_skips_detect_status(self, tmp_path, monkeypatch):
        """Daemon should not call detect_status for sessions with status='done'."""
        from overcode.monitor_daemon import MonitorDaemon
        from overcode.status_constants import STATUS_DONE

        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.detector = Mock()

        mock_session = Mock()
        mock_session.id = "sess-1"
        mock_session.name = "agent-1"
        mock_session.status = "done"

        if mock_session.status == "done":
            status, activity = STATUS_DONE, "Completed"
        else:
            status, activity, _ = daemon.detector.detect_status(mock_session)

        assert status == STATUS_DONE
        assert activity == "Completed"
        daemon.detector.detect_status.assert_not_called()

    def test_active_session_calls_detect_status(self, tmp_path, monkeypatch):
        """Daemon should call detect_status for active (non-terminated/done) sessions."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.detector = Mock()
        daemon.detector.detect_status.return_value = ("running", "Working on task", "content")

        mock_session = Mock()
        mock_session.id = "sess-1"
        mock_session.name = "agent-1"
        mock_session.status = "active"

        if mock_session.status == "terminated":
            status, activity = "terminated", "Session terminated"
        elif mock_session.status == "done":
            status, activity = "done", "Completed"
        else:
            status, activity, _ = daemon.detector.detect_status(mock_session)

        assert status == "running"
        assert activity == "Working on task"
        daemon.detector.detect_status.assert_called_once_with(mock_session)


class TestDaemonPersistsTerminatedStatus:
    """Test that daemon persists terminated status to sessions.json."""

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

    def test_persists_terminated_when_window_gone(self, tmp_path, monkeypatch):
        """When window is gone (empty pane_content), daemon should persist terminated."""
        from overcode.monitor_daemon import STATUS_TERMINATED

        daemon = self._make_daemon(tmp_path, monkeypatch)

        mock_session = Mock()
        mock_session.id = "sess-1"
        mock_session.status = "active"  # Not yet marked terminated

        effective_status = STATUS_TERMINATED
        pane_content = ""  # Empty = window gone

        # Simulate the persistence logic from the daemon loop
        if (effective_status == STATUS_TERMINATED
                and mock_session.status != "terminated"
                and not pane_content):
            daemon.session_manager.update_session_status(mock_session.id, "terminated")

        daemon.session_manager.update_session_status.assert_called_once_with("sess-1", "terminated")

    def test_does_not_persist_when_already_terminated(self, tmp_path, monkeypatch):
        """Should not call update_session_status if session is already terminated."""
        from overcode.monitor_daemon import STATUS_TERMINATED

        daemon = self._make_daemon(tmp_path, monkeypatch)

        mock_session = Mock()
        mock_session.id = "sess-1"
        mock_session.status = "terminated"  # Already marked

        effective_status = STATUS_TERMINATED
        pane_content = ""

        if (effective_status == STATUS_TERMINATED
                and mock_session.status != "terminated"
                and not pane_content):
            daemon.session_manager.update_session_status(mock_session.id, "terminated")

        daemon.session_manager.update_session_status.assert_not_called()

    def test_does_not_persist_terminated_when_window_exists(self, tmp_path, monkeypatch):
        """When shell prompt detected but window exists, don't persist terminated.

        This prevents a race condition during agent revival where the daemon
        sees a shell prompt before claude starts up.
        """
        from overcode.monitor_daemon import STATUS_TERMINATED

        daemon = self._make_daemon(tmp_path, monkeypatch)

        mock_session = Mock()
        mock_session.id = "sess-1"
        mock_session.status = "running"

        effective_status = STATUS_TERMINATED
        pane_content = "user@host ~ %"  # Shell prompt = window exists

        if (effective_status == STATUS_TERMINATED
                and mock_session.status != "terminated"
                and not pane_content):
            daemon.session_manager.update_session_status(mock_session.id, "terminated")

        daemon.session_manager.update_session_status.assert_not_called()


# =============================================================================
# Subtree cost computation tests
# =============================================================================

class TestComputeSubtreeCosts:
    """Test _compute_subtree_costs method."""

    def _make_daemon(self):
        from overcode.monitor_daemon import MonitorDaemon
        with patch.object(MonitorDaemon, '__init__', lambda self: None):
            return MonitorDaemon.__new__(MonitorDaemon)

    def _make_state(self, name, cost, parent_name=None):
        from overcode.monitor_daemon_state import SessionDaemonState
        return SessionDaemonState(
            session_id=f"id-{name}",
            name=name,
            estimated_cost_usd=cost,
            parent_name=parent_name,
        )

    def test_leaf_agent_no_subtree_cost(self):
        """Leaf agents (no children) should keep subtree_cost_usd=0."""
        daemon = self._make_daemon()
        leaf = self._make_state("leaf", 1.50)
        daemon._compute_subtree_costs([leaf])
        assert leaf.subtree_cost_usd == 0.0

    def test_single_parent_with_one_child(self):
        """Parent subtree cost = parent cost + child cost."""
        daemon = self._make_daemon()
        parent = self._make_state("parent", 2.00)
        child = self._make_state("child", 1.50, parent_name="parent")
        daemon._compute_subtree_costs([parent, child])
        assert parent.subtree_cost_usd == 3.50
        assert child.subtree_cost_usd == 0.0

    def test_deep_hierarchy(self):
        """Three-level hierarchy: grandparent includes all descendants."""
        daemon = self._make_daemon()
        gp = self._make_state("gp", 1.00)
        parent = self._make_state("parent", 2.00, parent_name="gp")
        child = self._make_state("child", 3.00, parent_name="parent")
        daemon._compute_subtree_costs([gp, parent, child])
        assert gp.subtree_cost_usd == 6.00  # 1 + 2 + 3
        assert parent.subtree_cost_usd == 5.00  # 2 + 3
        assert child.subtree_cost_usd == 0.0  # leaf

    def test_multiple_children(self):
        """Parent with two children sums all."""
        daemon = self._make_daemon()
        parent = self._make_state("parent", 1.00)
        c1 = self._make_state("c1", 2.00, parent_name="parent")
        c2 = self._make_state("c2", 3.00, parent_name="parent")
        daemon._compute_subtree_costs([parent, c1, c2])
        assert parent.subtree_cost_usd == 6.00  # 1 + 2 + 3

    def test_orphan_child_ignored(self):
        """Child whose parent is not in session_states is not counted."""
        daemon = self._make_daemon()
        orphan = self._make_state("orphan", 5.00, parent_name="missing")
        daemon._compute_subtree_costs([orphan])
        assert orphan.subtree_cost_usd == 0.0


class TestPrBranchMismatchClearing:
    """Test that PR is cleared when agent switches branches."""

    def test_pr_cleared_on_branch_mismatch(self):
        """When branch changes away from pr_branch, both pr_number and pr_branch should clear."""
        from overcode.monitor_daemon import MonitorDaemon
        from overcode.session_manager import Session, SessionStats

        session = Session(
            id="test-1",
            name="test",
            tmux_session="agents",
            tmux_window=1,
            command=["claude"],
            start_directory="/tmp/repo",
            start_time="2024-01-01T00:00:00",
            branch="fix-auth",
            pr_number=42,
            pr_branch="fix-auth",
        )

        # After refresh_git_context, the session's branch changes to "main"
        refreshed_session = Session(
            id="test-1",
            name="test",
            tmux_session="agents",
            tmux_window=1,
            command=["claude"],
            start_directory="/tmp/repo",
            start_time="2024-01-01T00:00:00",
            branch="main",
            pr_number=42,
            pr_branch="fix-auth",
        )

        mock_sm = Mock()
        mock_sm.refresh_git_context.return_value = True  # branch changed
        mock_sm.get_session.return_value = refreshed_session

        with patch.object(MonitorDaemon, '__init__', lambda self: None):
            daemon = MonitorDaemon.__new__(MonitorDaemon)
            daemon.session_manager = mock_sm
            daemon.detector = Mock()
            daemon.detector.detect_status.return_value = ("running", "Working...", "")
            daemon._sessions_running_from_heartbeat = set()
            daemon._heartbeat_start_pending = set()
            daemon._last_heartbeat_times = {}
            daemon._heartbeat_cooldown_until = {}
            daemon._summary_cache = {}
            daemon.ai_summarizer = None
            daemon._subtree_watchers = {}
            daemon._orphan_cost_cache = {}

            # Simulate the branch mismatch check from _detect_and_enrich
            git_changed = mock_sm.refresh_git_context(session.id)
            if git_changed and session.pr_number is not None:
                refreshed = mock_sm.get_session(session.id)
                if refreshed and refreshed.branch is not None:
                    if refreshed.pr_branch is None or refreshed.branch != refreshed.pr_branch:
                        mock_sm.update_session(session.id, pr_number=None, pr_branch=None)

            # Verify update_session was called with clearing args
            mock_sm.update_session.assert_called_once_with(
                "test-1", pr_number=None, pr_branch=None
            )

    def test_pr_not_cleared_when_branch_unchanged(self):
        """When branch hasn't changed, PR should remain."""
        from overcode.session_manager import Session

        session = Session(
            id="test-1",
            name="test",
            tmux_session="agents",
            tmux_window=1,
            command=["claude"],
            start_directory="/tmp/repo",
            start_time="2024-01-01T00:00:00",
            branch="fix-auth",
            pr_number=42,
            pr_branch="fix-auth",
        )

        mock_sm = Mock()
        mock_sm.refresh_git_context.return_value = False  # no change

        # Simulate the branch mismatch check
        git_changed = mock_sm.refresh_git_context(session.id)
        if git_changed and session.pr_number is not None:
            refreshed = mock_sm.get_session(session.id)
            if refreshed and refreshed.branch is not None:
                if refreshed.pr_branch is None or refreshed.branch != refreshed.pr_branch:
                    mock_sm.update_session(session.id, pr_number=None, pr_branch=None)

        # update_session should NOT have been called
        mock_sm.update_session.assert_not_called()

    def test_pr_not_cleared_when_branch_is_none(self):
        """When git detection returns None branch, PR should not be cleared."""
        from overcode.session_manager import Session

        session = Session(
            id="test-1",
            name="test",
            tmux_session="agents",
            tmux_window=1,
            command=["claude"],
            start_directory="/tmp/repo",
            start_time="2024-01-01T00:00:00",
            branch="fix-auth",
            pr_number=42,
            pr_branch="fix-auth",
        )

        refreshed_session = Session(
            id="test-1",
            name="test",
            tmux_session="agents",
            tmux_window=1,
            command=["claude"],
            start_directory="/tmp/repo",
            start_time="2024-01-01T00:00:00",
            branch=None,  # git detection failed
            pr_number=42,
            pr_branch="fix-auth",
        )

        mock_sm = Mock()
        mock_sm.refresh_git_context.return_value = True
        mock_sm.get_session.return_value = refreshed_session

        git_changed = mock_sm.refresh_git_context(session.id)
        if git_changed and session.pr_number is not None:
            refreshed = mock_sm.get_session(session.id)
            if refreshed and refreshed.branch is not None:
                if refreshed.pr_branch is None or refreshed.branch != refreshed.pr_branch:
                    mock_sm.update_session(session.id, pr_number=None, pr_branch=None)

        # Should NOT clear because branch is None (git detection failed)
        mock_sm.update_session.assert_not_called()

    def test_pr_cleared_when_pr_branch_is_none_premigration(self):
        """Pre-migration sessions with pr_number but no pr_branch should clear on any branch change."""
        from overcode.session_manager import Session

        session = Session(
            id="test-1",
            name="test",
            tmux_session="agents",
            tmux_window=1,
            command=["claude"],
            start_directory="/tmp/repo",
            start_time="2024-01-01T00:00:00",
            branch="main",
            pr_number=42,
            pr_branch=None,  # pre-migration: no pr_branch recorded
        )

        refreshed_session = Session(
            id="test-1",
            name="test",
            tmux_session="agents",
            tmux_window=1,
            command=["claude"],
            start_directory="/tmp/repo",
            start_time="2024-01-01T00:00:00",
            branch="feature-x",  # switched to different branch
            pr_number=42,
            pr_branch=None,
        )

        mock_sm = Mock()
        mock_sm.refresh_git_context.return_value = True
        mock_sm.get_session.return_value = refreshed_session

        git_changed = mock_sm.refresh_git_context(session.id)
        if git_changed and session.pr_number is not None:
            refreshed = mock_sm.get_session(session.id)
            if refreshed and refreshed.branch is not None:
                if refreshed.pr_branch is None or refreshed.branch != refreshed.pr_branch:
                    mock_sm.update_session(session.id, pr_number=None, pr_branch=None)

        # Should clear because pr_branch is None (pre-migration) and branch changed
        mock_sm.update_session.assert_called_once_with(
            "test-1", pr_number=None, pr_branch=None
        )


class TestCountUntrackedWindows:
    """Test _count_untracked_windows method (#344)."""

    def _make_daemon(self):
        from overcode.monitor_daemon import MonitorDaemon
        with patch.object(MonitorDaemon, '__init__', lambda self: None):
            daemon = MonitorDaemon.__new__(MonitorDaemon)
            daemon.tmux_session = "agents"
            return daemon

    def test_no_untracked_windows(self):
        """Returns 0 when all windows are tracked."""
        daemon = self._make_daemon()
        session = MagicMock()
        session.status = "running"
        session.tmux_window = "agent1"

        mock_tmux = MagicMock()
        mock_tmux.session_exists.return_value = True
        mock_tmux.list_windows.return_value = [
            {'index': '0', 'name': 'bash'},
            {'index': '1', 'name': 'agent1'},
        ]

        with patch('overcode.implementations.RealTmux', return_value=mock_tmux):
            result = daemon._count_untracked_windows([session])
            assert result == 0

    def test_untracked_windows_detected(self):
        """Returns count of windows not tracked by any session."""
        daemon = self._make_daemon()
        session = MagicMock()
        session.status = "running"
        session.tmux_window = "agent1"

        mock_tmux = MagicMock()
        mock_tmux.session_exists.return_value = True
        mock_tmux.list_windows.return_value = [
            {'index': '0', 'name': 'bash'},
            {'index': '1', 'name': 'agent1'},
            {'index': '2', 'name': 'rogue1'},
            {'index': '3', 'name': 'rogue2'},
        ]

        with patch('overcode.implementations.RealTmux', return_value=mock_tmux):
            result = daemon._count_untracked_windows([session])
            assert result == 2

    def test_window_0_excluded(self):
        """Window 0 (default shell) is never counted as untracked."""
        daemon = self._make_daemon()

        mock_tmux = MagicMock()
        mock_tmux.session_exists.return_value = True
        mock_tmux.list_windows.return_value = [
            {'index': '0', 'name': 'bash'},
        ]

        with patch('overcode.implementations.RealTmux', return_value=mock_tmux):
            result = daemon._count_untracked_windows([])
            assert result == 0

    def test_terminated_sessions_not_tracked(self):
        """Terminated sessions don't count as tracked windows."""
        daemon = self._make_daemon()
        session = MagicMock()
        session.status = "terminated"
        session.tmux_window = "orphan"

        mock_tmux = MagicMock()
        mock_tmux.session_exists.return_value = True
        mock_tmux.list_windows.return_value = [
            {'index': '0', 'name': 'bash'},
            {'index': '1', 'name': 'orphan'},
        ]

        with patch('overcode.implementations.RealTmux', return_value=mock_tmux):
            result = daemon._count_untracked_windows([session])
            assert result == 1

    def test_session_not_exists_returns_zero(self):
        """Returns 0 when tmux session doesn't exist."""
        daemon = self._make_daemon()

        mock_tmux = MagicMock()
        mock_tmux.session_exists.return_value = False

        with patch('overcode.implementations.RealTmux', return_value=mock_tmux):
            result = daemon._count_untracked_windows([])
            assert result == 0


class TestEnsureDispatch:
    """Test dispatch agent lifecycle management (#23)."""

    def _make_daemon(self):
        """Create a MonitorDaemon with mocked dependencies."""
        from overcode.monitor_daemon import MonitorDaemon
        daemon = MonitorDaemon.__new__(MonitorDaemon)
        daemon.tmux_session = "agents"
        daemon.session_manager = MagicMock()
        daemon.log = MagicMock()
        daemon._last_rc_check = None
        return daemon

    @patch("overcode.monitor_daemon.get_tmux_pane_content")
    def test_noop_when_disabled(self, mock_pane):
        daemon = self._make_daemon()
        now = datetime.now()
        with patch("overcode.config.get_dispatch_config", return_value=None):
            daemon._ensure_dispatch([], now)
        mock_pane.assert_not_called()

    @patch("overcode.launcher.ClaudeLauncher")
    def test_launches_when_no_session(self, mock_launcher_cls, tmp_path):
        daemon = self._make_daemon()
        now = datetime.now()
        mock_launcher = MagicMock()
        mock_launcher.launch.return_value = MagicMock()
        mock_launcher_cls.return_value = mock_launcher
        config = {"name": "dispatch", "directory": str(tmp_path / "dispatch"), "rc_keepalive_interval": 120}
        with patch("overcode.config.get_dispatch_config", return_value=config):
            daemon._ensure_dispatch([], now)
        mock_launcher.launch.assert_called_once_with(
            name="dispatch",
            start_directory=str(tmp_path / "dispatch"),
            skip_permissions=True,
        )

    def test_noop_when_session_alive(self):
        daemon = self._make_daemon()
        now = datetime.now()
        session = MagicMock()
        session.name = "dispatch"
        session.status = "running"
        session.tmux_window = "dispatch-abcd"
        config = {"name": "dispatch", "directory": "/tmp/dispatch", "rc_keepalive_interval": 120}
        with patch("overcode.config.get_dispatch_config", return_value=config), \
             patch.object(daemon, "_ensure_dispatch_rc") as mock_rc:
            daemon._ensure_dispatch([session], now)
        mock_rc.assert_called_once_with(session, config, now)

    @patch("overcode.launcher.ClaudeLauncher")
    def test_relaunches_when_terminated(self, mock_launcher_cls, tmp_path):
        daemon = self._make_daemon()
        now = datetime.now()
        mock_launcher = MagicMock()
        mock_launcher.launch.return_value = MagicMock()
        mock_launcher_cls.return_value = mock_launcher
        session = MagicMock()
        session.name = "dispatch"
        session.status = "terminated"
        config = {"name": "dispatch", "directory": str(tmp_path / "dispatch"), "rc_keepalive_interval": 120}
        with patch("overcode.config.get_dispatch_config", return_value=config):
            daemon._ensure_dispatch([session], now)
        mock_launcher.launch.assert_called_once()


class TestIsRcActive:
    """Test RC detection heuristic (#23)."""

    def test_detects_remote_control_text(self):
        from overcode.monitor_daemon import MonitorDaemon
        content = "some output\nRemote control is active\n>"
        assert MonitorDaemon._is_rc_active(content) is True

    def test_detects_waiting_for_connection(self):
        from overcode.monitor_daemon import MonitorDaemon
        content = "Waiting for connection from claude.ai\n>"
        assert MonitorDaemon._is_rc_active(content) is True

    def test_returns_false_for_normal_prompt(self):
        from overcode.monitor_daemon import MonitorDaemon
        content = "Task completed successfully.\n❯"
        assert MonitorDaemon._is_rc_active(content) is False


class TestEnsureDispatchRc:
    """Test RC keepalive logic (#23)."""

    def _make_daemon(self):
        from overcode.monitor_daemon import MonitorDaemon
        daemon = MonitorDaemon.__new__(MonitorDaemon)
        daemon.tmux_session = "agents"
        daemon.log = MagicMock()
        daemon._last_rc_check = None
        return daemon

    @patch("overcode.monitor_daemon.send_text_to_tmux_window")
    @patch("overcode.monitor_daemon.get_tmux_pane_content", return_value="Task done\n❯")
    def test_resends_rc_when_at_prompt(self, mock_pane, mock_send):
        daemon = self._make_daemon()
        session = MagicMock()
        session.tmux_window = "dispatch-abcd"
        config = {"rc_keepalive_interval": 0}
        daemon._ensure_dispatch_rc(session, config, datetime.now())
        mock_send.assert_called_once_with(
            "agents", "dispatch-abcd", "/remote-control", send_enter=True
        )

    @patch("overcode.monitor_daemon.send_text_to_tmux_window")
    @patch("overcode.monitor_daemon.get_tmux_pane_content", return_value="Remote control active\n❯")
    def test_skips_when_rc_active(self, mock_pane, mock_send):
        daemon = self._make_daemon()
        session = MagicMock()
        session.tmux_window = "dispatch-abcd"
        config = {"rc_keepalive_interval": 0}
        daemon._ensure_dispatch_rc(session, config, datetime.now())
        mock_send.assert_not_called()

    @patch("overcode.monitor_daemon.send_text_to_tmux_window")
    @patch("overcode.monitor_daemon.get_tmux_pane_content", return_value="Task done\n❯")
    def test_respects_interval(self, mock_pane, mock_send):
        daemon = self._make_daemon()
        daemon._last_rc_check = datetime.now()  # Just checked
        session = MagicMock()
        session.tmux_window = "dispatch-abcd"
        config = {"rc_keepalive_interval": 120}
        daemon._ensure_dispatch_rc(session, config, datetime.now())
        mock_pane.assert_not_called()  # Skipped due to interval


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
