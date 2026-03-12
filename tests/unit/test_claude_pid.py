"""Tests for session ID ownership guard."""

from unittest.mock import patch, Mock

import pytest

from overcode.claude_pid import is_session_id_owned_by_others


class TestIsSessionIdOwnedByOthers:
    """Test exclusive ownership check."""

    def _make_session(self, agent_id, claude_session_ids=None):
        s = Mock()
        s.id = agent_id
        s.claude_session_ids = claude_session_ids or []
        return s

    def test_not_owned_by_anyone(self):
        sessions = [
            self._make_session("a1", ["sid-1"]),
            self._make_session("a2", ["sid-2"]),
        ]
        assert not is_session_id_owned_by_others("sid-3", "a1", sessions)

    def test_owned_by_self_is_ok(self):
        sessions = [
            self._make_session("a1", ["sid-1"]),
            self._make_session("a2", ["sid-2"]),
        ]
        assert not is_session_id_owned_by_others("sid-1", "a1", sessions)

    def test_owned_by_other_agent(self):
        sessions = [
            self._make_session("a1", ["sid-1"]),
            self._make_session("a2", ["sid-2"]),
        ]
        assert is_session_id_owned_by_others("sid-2", "a1", sessions)

    def test_handles_missing_claude_session_ids(self):
        s = Mock()
        s.id = "a2"
        s.claude_session_ids = None
        sessions = [self._make_session("a1", ["sid-1"]), s]
        assert not is_session_id_owned_by_others("sid-1", "a1", sessions)

    def test_empty_sessions_list(self):
        assert not is_session_id_owned_by_others("sid-1", "a1", [])


class TestSyncSessionIdIntegration:
    """Test that MonitorDaemon.sync_session_id uses ownership guard."""

    def _make_daemon(self, tmp_path, monkeypatch):
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

        with patch('overcode.monitor_daemon.SessionManager') as mock_sm_cls, \
             patch('overcode.monitor_daemon.StatusDetectorDispatcher'):
            mock_sm = Mock()
            mock_sm_cls.return_value = mock_sm
            daemon = MonitorDaemon(tmux_session="test")
            daemon.session_manager = mock_sm
            return daemon

    def test_adds_session_id_from_history(self, tmp_path, monkeypatch):
        """Picks up sessionId from history.jsonl when not owned by another agent."""
        daemon = self._make_daemon(tmp_path, monkeypatch)

        session = Mock()
        session.id = "agent-1"
        session.start_directory = "/some/dir"
        session.tmux_window = "agent-ab12"
        session.start_time = "2026-01-01T00:00:00"
        session.tmux_session = "test"
        session.claude_session_ids = []

        monkeypatch.setattr(
            'overcode.monitor_daemon.get_current_session_id_for_directory',
            lambda d, s: "history-session-id"
        )
        daemon.session_manager.list_sessions.return_value = [session]

        daemon.sync_session_id(session)

        daemon.session_manager.add_claude_session_id.assert_called_once_with(
            "agent-1", "history-session-id"
        )
        daemon.session_manager.set_active_claude_session_id.assert_called_once_with(
            "agent-1", "history-session-id"
        )

    def test_blocked_when_owned_by_other(self, tmp_path, monkeypatch):
        """History lookup is blocked when another agent already owns the sessionId."""
        daemon = self._make_daemon(tmp_path, monkeypatch)

        session_a = Mock()
        session_a.id = "agent-a"
        session_a.start_directory = "/shared/dir"
        session_a.tmux_window = "agent-a-ab12"
        session_a.start_time = "2026-01-01T00:00:00"
        session_a.tmux_session = "test"
        session_a.claude_session_ids = []

        session_b = Mock()
        session_b.id = "agent-b"
        session_b.tmux_session = "test"
        session_b.claude_session_ids = ["shared-session-id"]

        monkeypatch.setattr(
            'overcode.monitor_daemon.get_current_session_id_for_directory',
            lambda d, s: "shared-session-id"  # Would return B's session
        )
        daemon.session_manager.list_sessions.return_value = [session_a, session_b]

        daemon.sync_session_id(session_a)

        # Should NOT add the sessionId since agent-b owns it
        daemon.session_manager.add_claude_session_id.assert_not_called()

    def test_skips_when_no_start_directory(self, tmp_path, monkeypatch):
        daemon = self._make_daemon(tmp_path, monkeypatch)

        session = Mock()
        session.start_directory = None

        daemon.sync_session_id(session)

        daemon.session_manager.add_claude_session_id.assert_not_called()

    def test_skips_when_history_returns_none(self, tmp_path, monkeypatch):
        daemon = self._make_daemon(tmp_path, monkeypatch)

        session = Mock()
        session.id = "agent-1"
        session.start_directory = "/some/dir"
        session.start_time = "2026-01-01T00:00:00"

        monkeypatch.setattr(
            'overcode.monitor_daemon.get_current_session_id_for_directory',
            lambda d, s: None
        )

        daemon.sync_session_id(session)

        daemon.session_manager.add_claude_session_id.assert_not_called()
