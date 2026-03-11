"""Tests for PID-based Claude session ID discovery."""

import subprocess
from unittest.mock import patch, Mock

import pytest

from overcode.claude_pid import (
    get_claude_pid_from_pane_pid,
    get_session_id_from_args,
    get_pane_pid_for_window,
    discover_session_id_via_pid,
    is_session_id_owned_by_others,
)


class TestGetClaudePidFromPanePid:
    """Test finding Claude child process of a pane shell."""

    def test_finds_claude_child(self):
        ps_output = (
            "  PID  PPID COMM\n"
            "12345     1 zsh\n"
            "12346 12345 claude\n"
            "12347     1 vim\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout=ps_output)
            assert get_claude_pid_from_pane_pid(12345) == 12346

    def test_finds_node_child(self):
        """Claude may appear as 'node' in process list."""
        ps_output = (
            "  PID  PPID COMM\n"
            "12345     1 zsh\n"
            "12346 12345 node\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout=ps_output)
            assert get_claude_pid_from_pane_pid(12345) == 12346

    def test_returns_none_when_no_child(self):
        ps_output = (
            "  PID  PPID COMM\n"
            "12345     1 zsh\n"
            "99999     1 claude\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout=ps_output)
            assert get_claude_pid_from_pane_pid(12345) is None

    def test_returns_none_on_ps_failure(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=1, stdout="")
            assert get_claude_pid_from_pane_pid(12345) is None

    def test_returns_none_on_timeout(self):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("ps", 5)
            assert get_claude_pid_from_pane_pid(12345) is None


class TestGetSessionIdFromArgs:
    """Test extracting --resume sessionId from process args."""

    def test_extracts_resume_id(self):
        args = "claude --resume 72d80791-2ce0-4d8b-926a-7f944c46daf1 --dangerously-skip-permissions"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout=args)
            result = get_session_id_from_args(12345)
            assert result == "72d80791-2ce0-4d8b-926a-7f944c46daf1"

    def test_returns_none_without_resume(self):
        args = "claude code --dangerously-skip-permissions"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout=args)
            assert get_session_id_from_args(12345) is None

    def test_returns_none_on_ps_failure(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=1, stdout="")
            assert get_session_id_from_args(12345) is None

    def test_rejects_malformed_uuid(self):
        args = "claude --resume not-a-uuid --dangerously-skip-permissions"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout=args)
            assert get_session_id_from_args(12345) is None


class TestGetPanePidForWindow:
    """Test getting pane PID from tmux window."""

    def test_gets_pane_pid(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="95672\n")
            assert get_pane_pid_for_window("agents", "my-agent-ab12") == 95672

    def test_returns_none_on_missing_window(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=1, stdout="")
            assert get_pane_pid_for_window("agents", "nonexistent") is None

    def test_returns_none_on_empty_output(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="\n")
            assert get_pane_pid_for_window("agents", "my-agent") is None


class TestDiscoverSessionIdViaPid:
    """Test the full discovery chain."""

    def test_full_chain_with_resume(self):
        """tmux window → pane_pid → claude_pid → --resume sessionId."""
        with patch("overcode.claude_pid.get_pane_pid_for_window", return_value=95672), \
             patch("overcode.claude_pid.get_claude_pid_from_pane_pid", return_value=95694), \
             patch("overcode.claude_pid.get_session_id_from_args",
                   return_value="72d80791-2ce0-4d8b-926a-7f944c46daf1"):
            result = discover_session_id_via_pid("agents", "my-agent-ab12")
            assert result == "72d80791-2ce0-4d8b-926a-7f944c46daf1"

    def test_returns_none_when_no_pane(self):
        with patch("overcode.claude_pid.get_pane_pid_for_window", return_value=None):
            assert discover_session_id_via_pid("agents", "my-agent") is None

    def test_returns_none_when_no_claude_process(self):
        with patch("overcode.claude_pid.get_pane_pid_for_window", return_value=95672), \
             patch("overcode.claude_pid.get_claude_pid_from_pane_pid", return_value=None):
            assert discover_session_id_via_pid("agents", "my-agent") is None

    def test_returns_none_for_fresh_start(self):
        """Fresh claude code start has no --resume flag."""
        with patch("overcode.claude_pid.get_pane_pid_for_window", return_value=95672), \
             patch("overcode.claude_pid.get_claude_pid_from_pane_pid", return_value=95694), \
             patch("overcode.claude_pid.get_session_id_from_args", return_value=None):
            assert discover_session_id_via_pid("agents", "my-agent") is None


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
    """Test that MonitorDaemon.sync_session_id uses PID-based discovery."""

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

    def test_uses_pid_discovery_first(self, tmp_path, monkeypatch):
        """PID-based discovery takes priority over history.jsonl."""
        daemon = self._make_daemon(tmp_path, monkeypatch)

        session = Mock()
        session.id = "agent-1"
        session.start_directory = "/some/dir"
        session.tmux_window = "agent-ab12"
        session.start_time = "2026-01-01T00:00:00"

        monkeypatch.setattr(
            'overcode.monitor_daemon.discover_session_id_via_pid',
            lambda ts, wn: "pid-discovered-session-id"
        )
        # history.jsonl would return a different ID — should NOT be used
        monkeypatch.setattr(
            'overcode.monitor_daemon.get_current_session_id_for_directory',
            lambda d, s: "history-fallback-id"
        )

        daemon.sync_session_id(session)

        daemon.session_manager.add_claude_session_id.assert_called_once_with(
            "agent-1", "pid-discovered-session-id"
        )
        daemon.session_manager.set_active_claude_session_id.assert_called_once_with(
            "agent-1", "pid-discovered-session-id"
        )

    def test_falls_back_to_history_when_pid_fails(self, tmp_path, monkeypatch):
        """When PID discovery returns None, falls back to history.jsonl."""
        daemon = self._make_daemon(tmp_path, monkeypatch)

        session = Mock()
        session.id = "agent-1"
        session.start_directory = "/some/dir"
        session.tmux_window = "agent-ab12"
        session.start_time = "2026-01-01T00:00:00"

        monkeypatch.setattr(
            'overcode.monitor_daemon.discover_session_id_via_pid',
            lambda ts, wn: None
        )
        monkeypatch.setattr(
            'overcode.monitor_daemon.get_current_session_id_for_directory',
            lambda d, s: "history-session-id"
        )
        # No other agents own this sessionId
        daemon.session_manager.list_sessions.return_value = [session]

        daemon.sync_session_id(session)

        daemon.session_manager.add_claude_session_id.assert_called_once_with(
            "agent-1", "history-session-id"
        )

    def test_fallback_blocked_when_owned_by_other(self, tmp_path, monkeypatch):
        """History fallback is blocked when another agent already owns the sessionId."""
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
            'overcode.monitor_daemon.discover_session_id_via_pid',
            lambda ts, wn: None
        )
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
