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


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
