"""
Unit tests for Supervisor Daemon.

These tests verify the helper functions, stats tracking, and state management
without running the actual daemon loop.
"""

import json
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch


class TestSupervisorStats:
    """Test SupervisorStats dataclass."""

    def test_to_dict_serializes_all_fields(self):
        """Should serialize all fields to dictionary."""
        from overcode.supervisor_daemon import SupervisorStats

        stats = SupervisorStats(
            supervisor_launches=5,
            supervisor_tokens=10000,
            supervisor_input_tokens=8000,
            supervisor_output_tokens=2000,
            supervisor_cache_tokens=500,
            last_sync_time="2024-01-15T10:00:00",
            seen_session_ids=["session1", "session2"],
            supervisor_claude_running=True,
            supervisor_claude_started_at="2024-01-15T10:00:00",
            supervisor_claude_total_run_seconds=3600.0,
        )

        result = stats.to_dict()

        assert result["supervisor_launches"] == 5
        assert result["supervisor_tokens"] == 10000
        assert result["supervisor_input_tokens"] == 8000
        assert result["supervisor_output_tokens"] == 2000
        assert result["supervisor_cache_tokens"] == 500
        assert result["seen_session_ids"] == ["session1", "session2"]
        assert result["supervisor_claude_running"] is True
        assert result["supervisor_claude_total_run_seconds"] == 3600.0

    def test_from_dict_deserializes_all_fields(self):
        """Should deserialize all fields from dictionary."""
        from overcode.supervisor_daemon import SupervisorStats

        data = {
            "supervisor_launches": 10,
            "supervisor_tokens": 20000,
            "supervisor_input_tokens": 15000,
            "supervisor_output_tokens": 5000,
            "supervisor_cache_tokens": 1000,
            "last_sync_time": "2024-01-15T12:00:00",
            "seen_session_ids": ["session3"],
            "supervisor_claude_running": False,
            "supervisor_claude_started_at": None,
            "supervisor_claude_total_run_seconds": 7200.0,
        }

        stats = SupervisorStats.from_dict(data)

        assert stats.supervisor_launches == 10
        assert stats.supervisor_tokens == 20000
        assert stats.seen_session_ids == ["session3"]
        assert stats.supervisor_claude_total_run_seconds == 7200.0

    def test_from_dict_handles_missing_fields(self):
        """Should handle missing fields with defaults."""
        from overcode.supervisor_daemon import SupervisorStats

        data = {
            "supervisor_launches": 5,
            # Other fields missing
        }

        stats = SupervisorStats.from_dict(data)

        assert stats.supervisor_launches == 5
        assert stats.supervisor_tokens == 0
        assert stats.seen_session_ids == []
        assert stats.supervisor_claude_running is False

    def test_save_creates_file(self, tmp_path):
        """Should save stats to file."""
        from overcode.supervisor_daemon import SupervisorStats

        stats = SupervisorStats(supervisor_launches=3)
        stats_path = tmp_path / "stats.json"

        stats.save(stats_path)

        assert stats_path.exists()
        with open(stats_path) as f:
            data = json.load(f)
        assert data["supervisor_launches"] == 3

    def test_load_returns_empty_for_missing_file(self, tmp_path):
        """Should return empty stats when file doesn't exist."""
        from overcode.supervisor_daemon import SupervisorStats

        stats_path = tmp_path / "nonexistent.json"

        stats = SupervisorStats.load(stats_path)

        assert stats.supervisor_launches == 0
        assert stats.supervisor_tokens == 0

    def test_load_returns_empty_for_invalid_json(self, tmp_path):
        """Should return empty stats for invalid JSON."""
        from overcode.supervisor_daemon import SupervisorStats

        stats_path = tmp_path / "invalid.json"
        stats_path.write_text("not valid json {{{")

        stats = SupervisorStats.load(stats_path)

        assert stats.supervisor_launches == 0

    def test_roundtrip_save_load(self, tmp_path):
        """Should preserve all data through save/load cycle."""
        from overcode.supervisor_daemon import SupervisorStats

        original = SupervisorStats(
            supervisor_launches=42,
            supervisor_tokens=100000,
            seen_session_ids=["a", "b", "c"],
            supervisor_claude_total_run_seconds=1234.5,
        )

        stats_path = tmp_path / "stats.json"
        original.save(stats_path)
        loaded = SupervisorStats.load(stats_path)

        assert loaded.supervisor_launches == original.supervisor_launches
        assert loaded.supervisor_tokens == original.supervisor_tokens
        assert loaded.seen_session_ids == original.seen_session_ids
        assert loaded.supervisor_claude_total_run_seconds == original.supervisor_claude_total_run_seconds


class TestBuildDaemonClaudeContext:
    """Test build_daemon_claude_context method."""

    def test_builds_context_with_sessions(self, tmp_path, monkeypatch):
        """Should build context string with session info."""
        from overcode.supervisor_daemon import SupervisorDaemon
        from overcode.monitor_daemon_state import SessionDaemonState

        monkeypatch.setattr('overcode.supervisor_daemon.ensure_session_dir', lambda x: None)
        monkeypatch.setattr(
            'overcode.supervisor_daemon.get_supervisor_daemon_pid_path',
            lambda x: tmp_path / "pid"
        )
        monkeypatch.setattr(
            'overcode.supervisor_daemon.get_supervisor_stats_path',
            lambda x: tmp_path / "stats.json"
        )
        monkeypatch.setattr(
            'overcode.supervisor_daemon.get_supervisor_log_path',
            lambda x: tmp_path / "log"
        )

        with patch('overcode.supervisor_daemon.SessionManager'):
            with patch('overcode.supervisor_daemon.TmuxManager'):
                daemon = SupervisorDaemon(tmux_session="test")

        sessions = [
            SessionDaemonState(
                session_id="1",
                name="agent-1",
                tmux_window=1,
                current_status="waiting_user",
                current_activity="",
                status_since=datetime.now().isoformat(),
                green_time_seconds=100.0,
                non_green_time_seconds=50.0,
                standing_instructions="Do stuff",
                repo_name="my-repo",
            ),
            SessionDaemonState(
                session_id="2",
                name="agent-2",
                tmux_window=2,
                current_status="error",
                current_activity="",
                standing_instructions=None,
            ),
        ]

        result = daemon.build_daemon_claude_context(sessions)

        assert "agent-1" in result
        assert "agent-2" in result
        assert "window 1" in result
        assert "window 2" in result
        assert "Do stuff" in result
        assert "my-repo" in result
        assert "Sessions needing attention: 2" in result


class TestGetNonGreenSessions:
    """Test get_non_green_sessions method."""

    def test_filters_running_sessions(self, tmp_path, monkeypatch):
        """Should filter out running (green) sessions."""
        from overcode.supervisor_daemon import SupervisorDaemon
        from overcode.monitor_daemon_state import MonitorDaemonState, SessionDaemonState

        monkeypatch.setattr('overcode.supervisor_daemon.ensure_session_dir', lambda x: None)
        monkeypatch.setattr(
            'overcode.supervisor_daemon.get_supervisor_daemon_pid_path',
            lambda x: tmp_path / "pid"
        )
        monkeypatch.setattr(
            'overcode.supervisor_daemon.get_supervisor_stats_path',
            lambda x: tmp_path / "stats.json"
        )
        monkeypatch.setattr(
            'overcode.supervisor_daemon.get_supervisor_log_path',
            lambda x: tmp_path / "log"
        )

        with patch('overcode.supervisor_daemon.SessionManager'):
            with patch('overcode.supervisor_daemon.TmuxManager'):
                daemon = SupervisorDaemon(tmux_session="test")

        monitor_state = MonitorDaemonState(
            pid=1234,
            status="active",
            started_at=datetime.now().isoformat(),
        )
        monitor_state.sessions = [
            SessionDaemonState(session_id="1", name="green-agent", current_status="running"),
            SessionDaemonState(session_id="2", name="waiting-agent", current_status="waiting_user"),
            SessionDaemonState(session_id="3", name="error-agent", current_status="error"),
        ]

        result = daemon.get_non_green_sessions(monitor_state)

        assert len(result) == 2
        names = [s.name for s in result]
        assert "green-agent" not in names
        assert "waiting-agent" in names
        assert "error-agent" in names

    def test_filters_asleep_sessions(self, tmp_path, monkeypatch):
        """Should filter out asleep sessions."""
        from overcode.supervisor_daemon import SupervisorDaemon
        from overcode.monitor_daemon_state import MonitorDaemonState, SessionDaemonState

        monkeypatch.setattr('overcode.supervisor_daemon.ensure_session_dir', lambda x: None)
        monkeypatch.setattr(
            'overcode.supervisor_daemon.get_supervisor_daemon_pid_path',
            lambda x: tmp_path / "pid"
        )
        monkeypatch.setattr(
            'overcode.supervisor_daemon.get_supervisor_stats_path',
            lambda x: tmp_path / "stats.json"
        )
        monkeypatch.setattr(
            'overcode.supervisor_daemon.get_supervisor_log_path',
            lambda x: tmp_path / "log"
        )

        with patch('overcode.supervisor_daemon.SessionManager'):
            with patch('overcode.supervisor_daemon.TmuxManager'):
                daemon = SupervisorDaemon(tmux_session="test")

        monitor_state = MonitorDaemonState(
            pid=1234,
            status="active",
            started_at=datetime.now().isoformat(),
        )
        monitor_state.sessions = [
            SessionDaemonState(session_id="1", name="awake-agent", current_status="waiting_user", is_asleep=False),
            SessionDaemonState(session_id="2", name="sleeping-agent", current_status="waiting_user", is_asleep=True),
        ]

        result = daemon.get_non_green_sessions(monitor_state)

        assert len(result) == 1
        assert result[0].name == "awake-agent"

    def test_filters_do_nothing_instructions(self, tmp_path, monkeypatch):
        """Should filter out sessions with DO_NOTHING instructions."""
        from overcode.supervisor_daemon import SupervisorDaemon
        from overcode.monitor_daemon_state import MonitorDaemonState, SessionDaemonState

        monkeypatch.setattr('overcode.supervisor_daemon.ensure_session_dir', lambda x: None)
        monkeypatch.setattr(
            'overcode.supervisor_daemon.get_supervisor_daemon_pid_path',
            lambda x: tmp_path / "pid"
        )
        monkeypatch.setattr(
            'overcode.supervisor_daemon.get_supervisor_stats_path',
            lambda x: tmp_path / "stats.json"
        )
        monkeypatch.setattr(
            'overcode.supervisor_daemon.get_supervisor_log_path',
            lambda x: tmp_path / "log"
        )

        with patch('overcode.supervisor_daemon.SessionManager'):
            with patch('overcode.supervisor_daemon.TmuxManager'):
                daemon = SupervisorDaemon(tmux_session="test")

        monitor_state = MonitorDaemonState(
            pid=1234,
            status="active",
            started_at=datetime.now().isoformat(),
        )
        monitor_state.sessions = [
            SessionDaemonState(session_id="1", name="active-agent", current_status="waiting_user", standing_instructions="Do stuff"),
            SessionDaemonState(session_id="2", name="paused-agent", current_status="waiting_user", standing_instructions="DO_NOTHING"),
        ]

        result = daemon.get_non_green_sessions(monitor_state)

        assert len(result) == 1
        assert result[0].name == "active-agent"

    def test_filters_daemon_claude_session(self, tmp_path, monkeypatch):
        """Should filter out the daemon_claude session itself."""
        from overcode.supervisor_daemon import SupervisorDaemon
        from overcode.monitor_daemon_state import MonitorDaemonState, SessionDaemonState

        monkeypatch.setattr('overcode.supervisor_daemon.ensure_session_dir', lambda x: None)
        monkeypatch.setattr(
            'overcode.supervisor_daemon.get_supervisor_daemon_pid_path',
            lambda x: tmp_path / "pid"
        )
        monkeypatch.setattr(
            'overcode.supervisor_daemon.get_supervisor_stats_path',
            lambda x: tmp_path / "stats.json"
        )
        monkeypatch.setattr(
            'overcode.supervisor_daemon.get_supervisor_log_path',
            lambda x: tmp_path / "log"
        )

        with patch('overcode.supervisor_daemon.SessionManager'):
            with patch('overcode.supervisor_daemon.TmuxManager'):
                daemon = SupervisorDaemon(tmux_session="test")

        monitor_state = MonitorDaemonState(
            pid=1234,
            status="active",
            started_at=datetime.now().isoformat(),
        )
        monitor_state.sessions = [
            SessionDaemonState(session_id="1", name="real-agent", current_status="waiting_user"),
            SessionDaemonState(session_id="2", name="daemon_claude", current_status="waiting_user"),
        ]

        result = daemon.get_non_green_sessions(monitor_state)

        assert len(result) == 1
        assert result[0].name == "real-agent"


class TestWaitForMonitorDaemon:
    """Test wait_for_monitor_daemon method."""

    def test_returns_true_when_monitor_running(self, tmp_path, monkeypatch):
        """Should return True when monitor daemon is running."""
        from overcode.supervisor_daemon import SupervisorDaemon
        from overcode.monitor_daemon_state import MonitorDaemonState

        monkeypatch.setattr('overcode.supervisor_daemon.ensure_session_dir', lambda x: None)
        monkeypatch.setattr(
            'overcode.supervisor_daemon.get_supervisor_daemon_pid_path',
            lambda x: tmp_path / "pid"
        )
        monkeypatch.setattr(
            'overcode.supervisor_daemon.get_supervisor_stats_path',
            lambda x: tmp_path / "stats.json"
        )
        monkeypatch.setattr(
            'overcode.supervisor_daemon.get_supervisor_log_path',
            lambda x: tmp_path / "log"
        )

        with patch('overcode.supervisor_daemon.SessionManager'):
            with patch('overcode.supervisor_daemon.TmuxManager'):
                daemon = SupervisorDaemon(tmux_session="test")

        mock_state = MonitorDaemonState(
            pid=1234,
            status="active",
            started_at=datetime.now().isoformat(),
            last_loop_time=datetime.now().isoformat(),
        )

        with patch('overcode.supervisor_daemon.get_monitor_daemon_state', return_value=mock_state):
            result = daemon.wait_for_monitor_daemon(timeout=1, poll_interval=0.1)

        assert result is True

    def test_returns_false_when_timeout(self, tmp_path, monkeypatch):
        """Should return False when timeout expires."""
        from overcode.supervisor_daemon import SupervisorDaemon

        monkeypatch.setattr('overcode.supervisor_daemon.ensure_session_dir', lambda x: None)
        monkeypatch.setattr(
            'overcode.supervisor_daemon.get_supervisor_daemon_pid_path',
            lambda x: tmp_path / "pid"
        )
        monkeypatch.setattr(
            'overcode.supervisor_daemon.get_supervisor_stats_path',
            lambda x: tmp_path / "stats.json"
        )
        monkeypatch.setattr(
            'overcode.supervisor_daemon.get_supervisor_log_path',
            lambda x: tmp_path / "log"
        )

        with patch('overcode.supervisor_daemon.SessionManager'):
            with patch('overcode.supervisor_daemon.TmuxManager'):
                daemon = SupervisorDaemon(tmux_session="test")

        with patch('overcode.supervisor_daemon.get_monitor_daemon_state', return_value=None):
            result = daemon.wait_for_monitor_daemon(timeout=0.1, poll_interval=0.05)

        assert result is False


class TestMarkDaemonClaudeStopped:
    """Test _mark_daemon_claude_stopped method."""

    def test_accumulates_run_time(self, tmp_path, monkeypatch):
        """Should accumulate run time when stopping."""
        from overcode.supervisor_daemon import SupervisorDaemon

        monkeypatch.setattr('overcode.supervisor_daemon.ensure_session_dir', lambda x: None)
        monkeypatch.setattr(
            'overcode.supervisor_daemon.get_supervisor_daemon_pid_path',
            lambda x: tmp_path / "pid"
        )
        monkeypatch.setattr(
            'overcode.supervisor_daemon.get_supervisor_stats_path',
            lambda x: tmp_path / "stats.json"
        )
        monkeypatch.setattr(
            'overcode.supervisor_daemon.get_supervisor_log_path',
            lambda x: tmp_path / "log"
        )

        with patch('overcode.supervisor_daemon.SessionManager'):
            with patch('overcode.supervisor_daemon.TmuxManager'):
                daemon = SupervisorDaemon(tmux_session="test")

        # Simulate started 60 seconds ago
        started_at = (datetime.now() - timedelta(seconds=60)).isoformat()
        daemon.supervisor_stats.supervisor_claude_running = True
        daemon.supervisor_stats.supervisor_claude_started_at = started_at
        daemon.supervisor_stats.supervisor_claude_total_run_seconds = 100.0

        daemon._mark_daemon_claude_stopped()

        assert daemon.supervisor_stats.supervisor_claude_running is False
        assert daemon.supervisor_stats.supervisor_claude_started_at is None
        # Should have added ~60 seconds
        assert daemon.supervisor_stats.supervisor_claude_total_run_seconds > 150.0

    def test_no_op_when_not_running(self, tmp_path, monkeypatch):
        """Should do nothing when daemon claude not running."""
        from overcode.supervisor_daemon import SupervisorDaemon

        monkeypatch.setattr('overcode.supervisor_daemon.ensure_session_dir', lambda x: None)
        monkeypatch.setattr(
            'overcode.supervisor_daemon.get_supervisor_daemon_pid_path',
            lambda x: tmp_path / "pid"
        )
        monkeypatch.setattr(
            'overcode.supervisor_daemon.get_supervisor_stats_path',
            lambda x: tmp_path / "stats.json"
        )
        monkeypatch.setattr(
            'overcode.supervisor_daemon.get_supervisor_log_path',
            lambda x: tmp_path / "log"
        )

        with patch('overcode.supervisor_daemon.SessionManager'):
            with patch('overcode.supervisor_daemon.TmuxManager'):
                daemon = SupervisorDaemon(tmux_session="test")

        daemon.supervisor_stats.supervisor_claude_running = False
        daemon.supervisor_stats.supervisor_claude_total_run_seconds = 100.0

        daemon._mark_daemon_claude_stopped()

        # Should not change
        assert daemon.supervisor_stats.supervisor_claude_total_run_seconds == 100.0


class TestIsDaemonClaudeRunning:
    """Test is_daemon_claude_running method."""

    def _make_daemon(self, tmp_path, monkeypatch):
        from overcode.supervisor_daemon import SupervisorDaemon

        monkeypatch.setattr('overcode.supervisor_daemon.ensure_session_dir', lambda x: None)
        monkeypatch.setattr('overcode.supervisor_daemon.get_supervisor_daemon_pid_path', lambda x: tmp_path / "pid")
        monkeypatch.setattr('overcode.supervisor_daemon.get_supervisor_stats_path', lambda x: tmp_path / "stats.json")
        monkeypatch.setattr('overcode.supervisor_daemon.get_supervisor_log_path', lambda x: tmp_path / "log")

        with patch('overcode.supervisor_daemon.SessionManager'):
            with patch('overcode.supervisor_daemon.TmuxManager'):
                daemon = SupervisorDaemon(tmux_session="test")
        return daemon

    def test_returns_false_when_no_window(self, tmp_path, monkeypatch):
        """Should return False when daemon_claude_window is None."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_window = None

        assert daemon.is_daemon_claude_running() is False

    def test_returns_true_when_window_exists(self, tmp_path, monkeypatch):
        """Should return True when window is set and tmux says it exists."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_window = 5
        daemon.tmux.window_exists = Mock(return_value=True)

        assert daemon.is_daemon_claude_running() is True
        daemon.tmux.window_exists.assert_called_once_with(5)

    def test_returns_false_when_window_gone(self, tmp_path, monkeypatch):
        """Should return False when window is set but tmux says it no longer exists."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_window = 3
        daemon.tmux.window_exists = Mock(return_value=False)

        assert daemon.is_daemon_claude_running() is False


class TestIsDaemonClaudeDone:
    """Test is_daemon_claude_done method."""

    def _make_daemon(self, tmp_path, monkeypatch):
        from overcode.supervisor_daemon import SupervisorDaemon

        monkeypatch.setattr('overcode.supervisor_daemon.ensure_session_dir', lambda x: None)
        monkeypatch.setattr('overcode.supervisor_daemon.get_supervisor_daemon_pid_path', lambda x: tmp_path / "pid")
        monkeypatch.setattr('overcode.supervisor_daemon.get_supervisor_stats_path', lambda x: tmp_path / "stats.json")
        monkeypatch.setattr('overcode.supervisor_daemon.get_supervisor_log_path', lambda x: tmp_path / "log")

        with patch('overcode.supervisor_daemon.SessionManager'):
            with patch('overcode.supervisor_daemon.TmuxManager'):
                daemon = SupervisorDaemon(tmux_session="test")
        return daemon

    def test_returns_true_when_window_does_not_exist(self, tmp_path, monkeypatch):
        """Should return True when the window no longer exists."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_window = None  # Not running

        assert daemon.is_daemon_claude_done() is True

    def test_returns_false_when_active_indicator_dot_present(self, tmp_path, monkeypatch):
        """Should return False when active indicator '· ' is in pane content."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_window = 1
        daemon.tmux.window_exists = Mock(return_value=True)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Some output\n· Thinking about it\nMore output\n"

        with patch('overcode.supervisor_daemon.subprocess.run', return_value=mock_result):
            assert daemon.is_daemon_claude_done() is False

    def test_returns_false_when_running_indicator(self, tmp_path, monkeypatch):
        """Should return False when 'Running...' indicator is present."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_window = 1
        daemon.tmux.window_exists = Mock(return_value=True)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Some output\nRunning\u2026\nMore output\n"

        with patch('overcode.supervisor_daemon.subprocess.run', return_value=mock_result):
            assert daemon.is_daemon_claude_done() is False

    def test_returns_false_when_esc_to_interrupt(self, tmp_path, monkeypatch):
        """Should return False when '(esc to interrupt' indicator is present."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_window = 1
        daemon.tmux.window_exists = Mock(return_value=True)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Doing work\n(esc to interrupt\n"

        with patch('overcode.supervisor_daemon.subprocess.run', return_value=mock_result):
            assert daemon.is_daemon_claude_done() is False

    def test_returns_false_when_sparkle_indicator(self, tmp_path, monkeypatch):
        """Should return False when sparkle indicator is present."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_window = 1
        daemon.tmux.window_exists = Mock(return_value=True)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Working\n\u273d processing\n"

        with patch('overcode.supervisor_daemon.subprocess.run', return_value=mock_result):
            assert daemon.is_daemon_claude_done() is False

    def test_returns_true_when_empty_prompt_gt(self, tmp_path, monkeypatch):
        """Should return True when '>' prompt found in last lines."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_window = 1
        daemon.tmux.window_exists = Mock(return_value=True)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Previous output\nDone with task\n>\n"

        with patch('overcode.supervisor_daemon.subprocess.run', return_value=mock_result):
            assert daemon.is_daemon_claude_done() is True

    def test_returns_true_when_empty_prompt_chevron(self, tmp_path, monkeypatch):
        """Should return True when chevron prompt found in last lines."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_window = 1
        daemon.tmux.window_exists = Mock(return_value=True)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Previous output\nDone with task\n\u203a\n"

        with patch('overcode.supervisor_daemon.subprocess.run', return_value=mock_result):
            assert daemon.is_daemon_claude_done() is True

    def test_returns_false_when_tool_call_without_result(self, tmp_path, monkeypatch):
        """Should return False when tool call marker present but no result marker."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_window = 1
        daemon.tmux.window_exists = Mock(return_value=True)

        mock_result = Mock()
        mock_result.returncode = 0
        # Tool call with no result marker following it
        mock_result.stdout = "Some output\n\u23fa Read(file.py)\nWaiting...\n"

        with patch('overcode.supervisor_daemon.subprocess.run', return_value=mock_result):
            assert daemon.is_daemon_claude_done() is False

    def test_returns_false_on_subprocess_timeout(self, tmp_path, monkeypatch):
        """Should return False when subprocess times out."""
        import subprocess as sp

        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_window = 1
        daemon.tmux.window_exists = Mock(return_value=True)

        with patch('overcode.supervisor_daemon.subprocess.run', side_effect=sp.TimeoutExpired(cmd="tmux", timeout=5)):
            assert daemon.is_daemon_claude_done() is False

    def test_returns_false_on_subprocess_error(self, tmp_path, monkeypatch):
        """Should return False on generic SubprocessError."""
        import subprocess as sp

        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_window = 1
        daemon.tmux.window_exists = Mock(return_value=True)

        with patch('overcode.supervisor_daemon.subprocess.run', side_effect=sp.SubprocessError("fail")):
            assert daemon.is_daemon_claude_done() is False

    def test_returns_true_when_capture_fails_nonzero(self, tmp_path, monkeypatch):
        """Should return True when capture-pane returns non-zero (window gone)."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_window = 1
        daemon.tmux.window_exists = Mock(return_value=True)

        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch('overcode.supervisor_daemon.subprocess.run', return_value=mock_result):
            assert daemon.is_daemon_claude_done() is True

    def test_returns_false_when_no_prompt_and_no_indicators(self, tmp_path, monkeypatch):
        """Should return False when no indicators and no prompt found."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_window = 1
        daemon.tmux.window_exists = Mock(return_value=True)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Some random output\nAnother line\nNo prompt here\n"

        with patch('overcode.supervisor_daemon.subprocess.run', return_value=mock_result):
            assert daemon.is_daemon_claude_done() is False

    def test_tool_call_with_result_does_not_block(self, tmp_path, monkeypatch):
        """Should not consider tool call as blocking when result marker follows."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_window = 1
        daemon.tmux.window_exists = Mock(return_value=True)

        mock_result = Mock()
        mock_result.returncode = 0
        # Tool call with result marker after it, then prompt
        mock_result.stdout = "Some output\n\u23fa Read(file.py)\n\u23bf content here\nDone\n>\n"

        with patch('overcode.supervisor_daemon.subprocess.run', return_value=mock_result):
            assert daemon.is_daemon_claude_done() is True


class TestKillDaemonClaude:
    """Test kill_daemon_claude method."""

    def _make_daemon(self, tmp_path, monkeypatch):
        from overcode.supervisor_daemon import SupervisorDaemon

        monkeypatch.setattr('overcode.supervisor_daemon.ensure_session_dir', lambda x: None)
        monkeypatch.setattr('overcode.supervisor_daemon.get_supervisor_daemon_pid_path', lambda x: tmp_path / "pid")
        monkeypatch.setattr('overcode.supervisor_daemon.get_supervisor_stats_path', lambda x: tmp_path / "stats.json")
        monkeypatch.setattr('overcode.supervisor_daemon.get_supervisor_log_path', lambda x: tmp_path / "log")

        with patch('overcode.supervisor_daemon.SessionManager'):
            with patch('overcode.supervisor_daemon.TmuxManager'):
                daemon = SupervisorDaemon(tmux_session="test")
        return daemon

    def test_kills_existing_window(self, tmp_path, monkeypatch):
        """Should call kill_window and reset daemon_claude_window to None."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_window = 7
        daemon.tmux.window_exists = Mock(return_value=True)
        daemon.tmux.kill_window = Mock()

        daemon.kill_daemon_claude()

        daemon.tmux.kill_window.assert_called_once_with(7)
        assert daemon.daemon_claude_window is None

    def test_no_kill_when_window_is_none(self, tmp_path, monkeypatch):
        """Should not call kill_window when daemon_claude_window is None."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_window = None
        daemon.tmux.kill_window = Mock()

        daemon.kill_daemon_claude()

        daemon.tmux.kill_window.assert_not_called()
        assert daemon.daemon_claude_window is None

    def test_no_kill_when_window_already_gone(self, tmp_path, monkeypatch):
        """Should not call kill_window when window no longer exists."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_window = 5
        daemon.tmux.window_exists = Mock(return_value=False)
        daemon.tmux.kill_window = Mock()

        daemon.kill_daemon_claude()

        daemon.tmux.kill_window.assert_not_called()
        assert daemon.daemon_claude_window is None


class TestCleanupStaleDaemonClaudes:
    """Test cleanup_stale_daemon_claudes method."""

    def _make_daemon(self, tmp_path, monkeypatch):
        from overcode.supervisor_daemon import SupervisorDaemon

        monkeypatch.setattr('overcode.supervisor_daemon.ensure_session_dir', lambda x: None)
        monkeypatch.setattr('overcode.supervisor_daemon.get_supervisor_daemon_pid_path', lambda x: tmp_path / "pid")
        monkeypatch.setattr('overcode.supervisor_daemon.get_supervisor_stats_path', lambda x: tmp_path / "stats.json")
        monkeypatch.setattr('overcode.supervisor_daemon.get_supervisor_log_path', lambda x: tmp_path / "log")

        with patch('overcode.supervisor_daemon.SessionManager'):
            with patch('overcode.supervisor_daemon.TmuxManager'):
                daemon = SupervisorDaemon(tmux_session="test")
        return daemon

    def test_clears_stale_window_reference(self, tmp_path, monkeypatch):
        """Should set daemon_claude_window to None when tracked window no longer exists."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_window = 3
        daemon.tmux.window_exists = Mock(return_value=False)
        daemon.tmux.list_windows = Mock(return_value=[])

        daemon.cleanup_stale_daemon_claudes()

        assert daemon.daemon_claude_window is None

    def test_kills_orphaned_daemon_claude_windows(self, tmp_path, monkeypatch):
        """Should kill daemon claude windows that are not the current tracked one."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_window = 5
        daemon.tmux.window_exists = Mock(return_value=True)
        daemon.tmux.list_windows = Mock(return_value=[
            {'name': '_daemon_claude', 'index': 5},
            {'name': '_daemon_claude', 'index': 8},  # orphan
            {'name': 'agent-1', 'index': 1},
        ])
        daemon.tmux.kill_window = Mock()

        daemon.cleanup_stale_daemon_claudes()

        daemon.tmux.kill_window.assert_called_once_with(8)
        assert daemon.daemon_claude_window == 5

    def test_kills_orphans_when_no_tracked_window(self, tmp_path, monkeypatch):
        """Should kill all daemon claude windows when no window is tracked."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_window = None
        daemon.tmux.list_windows = Mock(return_value=[
            {'name': '_daemon_claude', 'index': 2},
            {'name': '_daemon_claude', 'index': 9},
            {'name': 'agent-1', 'index': 1},
        ])
        daemon.tmux.kill_window = Mock()

        daemon.cleanup_stale_daemon_claudes()

        assert daemon.tmux.kill_window.call_count == 2
        daemon.tmux.kill_window.assert_any_call(2)
        daemon.tmux.kill_window.assert_any_call(9)

    def test_no_cleanup_when_no_orphans(self, tmp_path, monkeypatch):
        """Should do nothing when no orphaned windows exist."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_window = 5
        daemon.tmux.window_exists = Mock(return_value=True)
        daemon.tmux.list_windows = Mock(return_value=[
            {'name': '_daemon_claude', 'index': 5},
            {'name': 'agent-1', 'index': 1},
        ])
        daemon.tmux.kill_window = Mock()

        daemon.cleanup_stale_daemon_claudes()

        daemon.tmux.kill_window.assert_not_called()


class TestCountInterventionsFromLog:
    """Test count_interventions_from_log method."""

    def _make_daemon(self, tmp_path, monkeypatch):
        from overcode.supervisor_daemon import SupervisorDaemon

        monkeypatch.setattr('overcode.supervisor_daemon.ensure_session_dir', lambda x: None)
        monkeypatch.setattr('overcode.supervisor_daemon.get_supervisor_daemon_pid_path', lambda x: tmp_path / "pid")
        monkeypatch.setattr('overcode.supervisor_daemon.get_supervisor_stats_path', lambda x: tmp_path / "stats.json")
        monkeypatch.setattr('overcode.supervisor_daemon.get_supervisor_log_path', lambda x: tmp_path / "log")

        with patch('overcode.supervisor_daemon.SessionManager'):
            with patch('overcode.supervisor_daemon.TmuxManager'):
                daemon = SupervisorDaemon(tmux_session="test")
        return daemon

    def test_returns_empty_when_no_launch_time(self, tmp_path, monkeypatch):
        """Should return empty dict when daemon_claude_launch_time is None."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_launch_time = None

        result = daemon.count_interventions_from_log(["agent-1"])

        assert result == {}

    def test_returns_empty_when_log_missing(self, tmp_path, monkeypatch):
        """Should return empty dict when log file does not exist."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_launch_time = datetime(2025, 1, 15, 10, 0, 0)
        # log_path points to tmp_path / "log" which doesn't exist

        result = daemon.count_interventions_from_log(["agent-1"])

        assert result == {}

    def test_counts_approved_interventions(self, tmp_path, monkeypatch):
        """Should count 'approved' actions per session."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_launch_time = datetime(2025, 1, 15, 10, 0, 0)

        log_content = (
            "Wed 15 Jan 2025 10:30:00 UTC: agent-1 - Tool call approved\n"
            "Wed 15 Jan 2025 10:31:00 UTC: agent-1 - Another tool approved\n"
            "Wed 15 Jan 2025 10:32:00 UTC: agent-2 - Action approved\n"
        )
        daemon.log_path.write_text(log_content)

        result = daemon.count_interventions_from_log(["agent-1", "agent-2"])

        assert result["agent-1"] == 2
        assert result["agent-2"] == 1

    def test_counts_rejected_interventions(self, tmp_path, monkeypatch):
        """Should count 'rejected' actions."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_launch_time = datetime(2025, 1, 15, 10, 0, 0)

        log_content = "Wed 15 Jan 2025 10:30:00 UTC: agent-1 - Tool call rejected\n"
        daemon.log_path.write_text(log_content)

        result = daemon.count_interventions_from_log(["agent-1"])

        assert result["agent-1"] == 1

    def test_counts_sent_interventions(self, tmp_path, monkeypatch):
        """Should count 'sent ' actions."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_launch_time = datetime(2025, 1, 15, 10, 0, 0)

        log_content = "Wed 15 Jan 2025 10:30:00 UTC: agent-1 - Message sent to window\n"
        daemon.log_path.write_text(log_content)

        result = daemon.count_interventions_from_log(["agent-1"])

        assert result["agent-1"] == 1

    def test_counts_provided_interventions(self, tmp_path, monkeypatch):
        """Should count 'provided' actions."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_launch_time = datetime(2025, 1, 15, 10, 0, 0)

        log_content = "Wed 15 Jan 2025 10:30:00 UTC: agent-1 - Guidance provided\n"
        daemon.log_path.write_text(log_content)

        result = daemon.count_interventions_from_log(["agent-1"])

        assert result["agent-1"] == 1

    def test_counts_unblocked_interventions(self, tmp_path, monkeypatch):
        """Should count 'unblocked' actions."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_launch_time = datetime(2025, 1, 15, 10, 0, 0)

        log_content = "Wed 15 Jan 2025 10:30:00 UTC: agent-1 - Session unblocked\n"
        daemon.log_path.write_text(log_content)

        result = daemon.count_interventions_from_log(["agent-1"])

        assert result["agent-1"] == 1

    def test_excludes_no_action_phrases(self, tmp_path, monkeypatch):
        """Should not count lines with 'no intervention needed' or 'no action needed'."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_launch_time = datetime(2025, 1, 15, 10, 0, 0)

        log_content = (
            "Wed 15 Jan 2025 10:30:00 UTC: agent-1 - No intervention needed, approved to continue\n"
            "Wed 15 Jan 2025 10:31:00 UTC: agent-2 - No action needed\n"
        )
        daemon.log_path.write_text(log_content)

        result = daemon.count_interventions_from_log(["agent-1", "agent-2"])

        assert result == {}

    def test_excludes_entries_before_launch_time(self, tmp_path, monkeypatch):
        """Should only count entries after daemon_claude_launch_time."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_launch_time = datetime(2025, 1, 15, 10, 30, 0)

        log_content = (
            "Wed 15 Jan 2025 10:00:00 UTC: agent-1 - Tool call approved\n"
            "Wed 15 Jan 2025 10:31:00 UTC: agent-1 - Another tool approved\n"
        )
        daemon.log_path.write_text(log_content)

        result = daemon.count_interventions_from_log(["agent-1"])

        assert result.get("agent-1", 0) == 1

    def test_ignores_unknown_session_names(self, tmp_path, monkeypatch):
        """Should not count interventions for sessions not in the provided list."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_launch_time = datetime(2025, 1, 15, 10, 0, 0)

        log_content = "Wed 15 Jan 2025 10:30:00 UTC: unknown-agent - Tool call approved\n"
        daemon.log_path.write_text(log_content)

        result = daemon.count_interventions_from_log(["agent-1"])

        assert result == {}

    def test_ignores_malformed_lines(self, tmp_path, monkeypatch):
        """Should skip lines without timestamps or proper format."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_launch_time = datetime(2025, 1, 15, 10, 0, 0)

        log_content = (
            "not a valid line\n"
            "\n"
            "no colon here\n"
            "Wed 15 Jan 2025 10:30:00 UTC: agent-1 - Tool call approved\n"
        )
        daemon.log_path.write_text(log_content)

        result = daemon.count_interventions_from_log(["agent-1"])

        assert result["agent-1"] == 1

    def test_ignores_lines_without_action_phrases(self, tmp_path, monkeypatch):
        """Should not count lines that match session but have no action phrase."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.daemon_claude_launch_time = datetime(2025, 1, 15, 10, 0, 0)

        log_content = "Wed 15 Jan 2025 10:30:00 UTC: agent-1 - Status is running\n"
        daemon.log_path.write_text(log_content)

        result = daemon.count_interventions_from_log(["agent-1"])

        assert result == {}


class TestUpdateInterventionCounts:
    """Test update_intervention_counts method."""

    def _make_daemon(self, tmp_path, monkeypatch):
        from overcode.supervisor_daemon import SupervisorDaemon

        monkeypatch.setattr('overcode.supervisor_daemon.ensure_session_dir', lambda x: None)
        monkeypatch.setattr('overcode.supervisor_daemon.get_supervisor_daemon_pid_path', lambda x: tmp_path / "pid")
        monkeypatch.setattr('overcode.supervisor_daemon.get_supervisor_stats_path', lambda x: tmp_path / "stats.json")
        monkeypatch.setattr('overcode.supervisor_daemon.get_supervisor_log_path', lambda x: tmp_path / "log")

        with patch('overcode.supervisor_daemon.SessionManager'):
            with patch('overcode.supervisor_daemon.TmuxManager'):
                daemon = SupervisorDaemon(tmux_session="test")
        return daemon

    def test_updates_steers_count_for_sessions(self, tmp_path, monkeypatch):
        """Should update steers_count for sessions with interventions."""
        daemon = self._make_daemon(tmp_path, monkeypatch)

        # Mock count_interventions_from_log
        daemon.count_interventions_from_log = Mock(return_value={"agent-1": 3, "agent-2": 1})

        # Mock session_manager.list_sessions
        mock_session_1 = Mock()
        mock_session_1.name = "agent-1"
        mock_session_1.id = "id-1"
        mock_session_1.stats = Mock()
        mock_session_1.stats.steers_count = 5

        mock_session_2 = Mock()
        mock_session_2.name = "agent-2"
        mock_session_2.id = "id-2"
        mock_session_2.stats = Mock()
        mock_session_2.stats.steers_count = 2

        daemon.session_manager.list_sessions = Mock(return_value=[mock_session_1, mock_session_2])
        daemon.session_manager.update_stats = Mock()

        daemon.update_intervention_counts(["agent-1", "agent-2"])

        daemon.session_manager.update_stats.assert_any_call("id-1", steers_count=8)
        daemon.session_manager.update_stats.assert_any_call("id-2", steers_count=3)
        assert daemon.session_manager.update_stats.call_count == 2

    def test_no_update_when_no_interventions(self, tmp_path, monkeypatch):
        """Should not call update_stats when there are no interventions."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.count_interventions_from_log = Mock(return_value={})
        daemon.session_manager.update_stats = Mock()

        daemon.update_intervention_counts(["agent-1"])

        daemon.session_manager.update_stats.assert_not_called()

    def test_skips_sessions_not_in_manager(self, tmp_path, monkeypatch):
        """Should skip intervention counts for sessions not found in session manager."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.count_interventions_from_log = Mock(return_value={"agent-1": 2, "unknown-agent": 1})

        mock_session = Mock()
        mock_session.name = "agent-1"
        mock_session.id = "id-1"
        mock_session.stats = Mock()
        mock_session.stats.steers_count = 0

        daemon.session_manager.list_sessions = Mock(return_value=[mock_session])
        daemon.session_manager.update_stats = Mock()

        daemon.update_intervention_counts(["agent-1", "unknown-agent"])

        # Only agent-1 should be updated, unknown-agent is not in session_manager
        daemon.session_manager.update_stats.assert_called_once_with("id-1", steers_count=2)


class TestSyncDaemonClaudeTokens:
    """Test _sync_daemon_claude_tokens method."""

    def _make_daemon(self, tmp_path, monkeypatch):
        from overcode.supervisor_daemon import SupervisorDaemon

        monkeypatch.setattr('overcode.supervisor_daemon.ensure_session_dir', lambda x: None)
        monkeypatch.setattr('overcode.supervisor_daemon.get_supervisor_daemon_pid_path', lambda x: tmp_path / "pid")
        monkeypatch.setattr('overcode.supervisor_daemon.get_supervisor_stats_path', lambda x: tmp_path / "stats.json")
        monkeypatch.setattr('overcode.supervisor_daemon.get_supervisor_log_path', lambda x: tmp_path / "log")

        with patch('overcode.supervisor_daemon.SessionManager'):
            with patch('overcode.supervisor_daemon.TmuxManager'):
                daemon = SupervisorDaemon(tmux_session="test")
        return daemon

    def test_no_op_when_projects_dir_missing(self, tmp_path, monkeypatch):
        """Should do nothing when the Claude projects directory does not exist."""
        daemon = self._make_daemon(tmp_path, monkeypatch)

        # Point home to tmp_path so ~/.claude/projects/... doesn't exist
        monkeypatch.setattr('overcode.supervisor_daemon.Path.home', lambda: tmp_path)
        monkeypatch.setattr('overcode.supervisor_daemon.encode_project_path', lambda x: "encoded")

        daemon._sync_daemon_claude_tokens()

        assert daemon.supervisor_stats.supervisor_tokens == 0

    def test_syncs_new_session_tokens(self, tmp_path, monkeypatch):
        """Should accumulate tokens from new session files."""
        daemon = self._make_daemon(tmp_path, monkeypatch)

        # Create mock projects dir
        overcode_dir = tmp_path / ".overcode"
        overcode_dir.mkdir()
        claude_dir = tmp_path / ".claude" / "projects" / "encoded"
        claude_dir.mkdir(parents=True)

        # Create fake session files
        (claude_dir / "session-abc.jsonl").write_text("")
        (claude_dir / "session-def.jsonl").write_text("")

        monkeypatch.setattr('overcode.supervisor_daemon.Path.home', lambda: tmp_path)
        monkeypatch.setattr('overcode.supervisor_daemon.encode_project_path', lambda x: "encoded")

        usage_calls = iter([
            {"input_tokens": 1000, "output_tokens": 200, "cache_creation_tokens": 50, "cache_read_tokens": 30},
            {"input_tokens": 500, "output_tokens": 100, "cache_creation_tokens": 10, "cache_read_tokens": 5},
        ])

        def mock_read_usage(session_file):
            return next(usage_calls)

        monkeypatch.setattr('overcode.supervisor_daemon.read_token_usage_from_session_file', mock_read_usage)

        daemon._sync_daemon_claude_tokens()

        assert daemon.supervisor_stats.supervisor_input_tokens == 1500
        assert daemon.supervisor_stats.supervisor_output_tokens == 300
        assert daemon.supervisor_stats.supervisor_cache_tokens == 95
        assert daemon.supervisor_stats.supervisor_tokens == 1800
        assert len(daemon.supervisor_stats.seen_session_ids) == 2
        assert daemon.supervisor_stats.last_sync_time is not None

    def test_skips_already_seen_sessions(self, tmp_path, monkeypatch):
        """Should not re-count tokens from already seen session IDs."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.supervisor_stats.seen_session_ids = ["session-abc"]
        daemon.supervisor_stats.supervisor_tokens = 500

        overcode_dir = tmp_path / ".overcode"
        overcode_dir.mkdir()
        claude_dir = tmp_path / ".claude" / "projects" / "encoded"
        claude_dir.mkdir(parents=True)

        # One already-seen, one new
        (claude_dir / "session-abc.jsonl").write_text("")
        (claude_dir / "session-new.jsonl").write_text("")

        monkeypatch.setattr('overcode.supervisor_daemon.Path.home', lambda: tmp_path)
        monkeypatch.setattr('overcode.supervisor_daemon.encode_project_path', lambda x: "encoded")

        def mock_read_usage(session_file):
            return {"input_tokens": 100, "output_tokens": 50, "cache_creation_tokens": 0, "cache_read_tokens": 0}

        monkeypatch.setattr('overcode.supervisor_daemon.read_token_usage_from_session_file', mock_read_usage)

        daemon._sync_daemon_claude_tokens()

        # Only the new session's tokens should be added
        assert daemon.supervisor_stats.supervisor_tokens == 650
        assert daemon.supervisor_stats.supervisor_input_tokens == 100
        assert daemon.supervisor_stats.supervisor_output_tokens == 50
        assert "session-new" in daemon.supervisor_stats.seen_session_ids
        assert len(daemon.supervisor_stats.seen_session_ids) == 2

    def test_handles_read_error_gracefully(self, tmp_path, monkeypatch):
        """Should continue when reading a session file raises an error."""
        daemon = self._make_daemon(tmp_path, monkeypatch)

        overcode_dir = tmp_path / ".overcode"
        overcode_dir.mkdir()
        claude_dir = tmp_path / ".claude" / "projects" / "encoded"
        claude_dir.mkdir(parents=True)

        (claude_dir / "session-bad.jsonl").write_text("")
        (claude_dir / "session-good.jsonl").write_text("")

        monkeypatch.setattr('overcode.supervisor_daemon.Path.home', lambda: tmp_path)
        monkeypatch.setattr('overcode.supervisor_daemon.encode_project_path', lambda x: "encoded")

        call_count = [0]

        def mock_read_usage(session_file):
            call_count[0] += 1
            if "bad" in str(session_file):
                raise OSError("Read error")
            return {"input_tokens": 200, "output_tokens": 100, "cache_creation_tokens": 0, "cache_read_tokens": 0}

        monkeypatch.setattr('overcode.supervisor_daemon.read_token_usage_from_session_file', mock_read_usage)

        daemon._sync_daemon_claude_tokens()

        # Only the good session should contribute
        assert daemon.supervisor_stats.supervisor_tokens == 300
        assert daemon.supervisor_stats.supervisor_input_tokens == 200
        assert len(daemon.supervisor_stats.seen_session_ids) == 1

    def test_saves_stats_when_tokens_found(self, tmp_path, monkeypatch):
        """Should save stats to disk when new tokens are found."""
        daemon = self._make_daemon(tmp_path, monkeypatch)

        overcode_dir = tmp_path / ".overcode"
        overcode_dir.mkdir()
        claude_dir = tmp_path / ".claude" / "projects" / "encoded"
        claude_dir.mkdir(parents=True)

        (claude_dir / "session-x.jsonl").write_text("")

        monkeypatch.setattr('overcode.supervisor_daemon.Path.home', lambda: tmp_path)
        monkeypatch.setattr('overcode.supervisor_daemon.encode_project_path', lambda x: "encoded")

        def mock_read_usage(session_file):
            return {"input_tokens": 100, "output_tokens": 50, "cache_creation_tokens": 0, "cache_read_tokens": 0}

        monkeypatch.setattr('overcode.supervisor_daemon.read_token_usage_from_session_file', mock_read_usage)

        daemon._sync_daemon_claude_tokens()

        # Stats file should have been saved
        assert daemon.stats_path.exists()
        saved = json.loads(daemon.stats_path.read_text())
        assert saved["supervisor_tokens"] == 150

    def test_no_save_when_no_new_tokens(self, tmp_path, monkeypatch):
        """Should not save stats when there are no new tokens (all sessions seen)."""
        daemon = self._make_daemon(tmp_path, monkeypatch)
        daemon.supervisor_stats.seen_session_ids = ["session-old"]

        overcode_dir = tmp_path / ".overcode"
        overcode_dir.mkdir()
        claude_dir = tmp_path / ".claude" / "projects" / "encoded"
        claude_dir.mkdir(parents=True)

        (claude_dir / "session-old.jsonl").write_text("")

        monkeypatch.setattr('overcode.supervisor_daemon.Path.home', lambda: tmp_path)
        monkeypatch.setattr('overcode.supervisor_daemon.encode_project_path', lambda x: "encoded")

        daemon._sync_daemon_claude_tokens()

        # Stats file should NOT have been created (no new tokens to save)
        assert not daemon.stats_path.exists()


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
