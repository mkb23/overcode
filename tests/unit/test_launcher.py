"""
Unit tests for ClaudeLauncher.

These tests use MockTmux and a temp directory SessionManager to test
all launcher operations without requiring real tmux or Claude.
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.launcher import ClaudeLauncher
from overcode.tmux_manager import TmuxManager
from overcode.session_manager import SessionManager
from overcode.interfaces import MockTmux


@pytest.fixture(autouse=True)
def mock_dependency_checks():
    """Mock dependency checks so tests don't require real tmux/claude installed."""
    with patch("overcode.launcher.require_tmux"), \
         patch("overcode.launcher.require_claude"):
        yield


class TestLauncherBasics:
    """Test basic launcher operations"""

    def test_launch_creates_session(self, tmp_path):
        """Launching creates a new session"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        session = launcher.launch(name="test-agent")

        assert session is not None
        assert session.name == "test-agent"
        assert session.tmux_session == "agents"

    def test_launch_creates_tmux_window(self, tmp_path):
        """Launching creates a tmux window"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        session = launcher.launch(name="test-agent")

        assert tmux_manager.window_exists(session.tmux_window)

    def test_launch_sends_claude_command(self, tmp_path):
        """Launching sends 'claude code' command to window"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        launcher.launch(name="test-agent")

        # Check that claude code was sent
        assert len(mock_tmux.sent_keys) >= 1
        sent_commands = [k[2] for k in mock_tmux.sent_keys]
        assert any("claude code" in cmd for cmd in sent_commands)

    def test_launch_with_skip_permissions(self, tmp_path):
        """Launch with skip_permissions uses dontAsk mode"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        session = launcher.launch(name="test-agent", skip_permissions=True)

        assert session is not None
        sent_commands = [k[2] for k in mock_tmux.sent_keys]
        assert any("--permission-mode" in cmd and "dontAsk" in cmd for cmd in sent_commands)

    def test_launch_multiple_sessions(self, tmp_path):
        """Can launch multiple sessions"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        s1 = launcher.launch(name="agent-1")
        s2 = launcher.launch(name="agent-2")
        s3 = launcher.launch(name="agent-3")

        assert s1.name == "agent-1"
        assert s2.name == "agent-2"
        assert s3.name == "agent-3"
        assert s1.tmux_window != s2.tmux_window != s3.tmux_window


class TestLauncherDuplicates:
    """Test duplicate session handling"""

    def test_launch_existing_name_returns_existing(self, tmp_path):
        """Launching with existing name returns existing session"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        s1 = launcher.launch(name="duplicate")
        s2 = launcher.launch(name="duplicate")

        assert s1.id == s2.id
        assert s1.tmux_window == s2.tmux_window


class TestLauncherListSessions:
    """Test session listing"""

    def test_list_sessions_empty(self, tmp_path):
        """list_sessions returns empty list when no sessions"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        sessions = launcher.list_sessions()

        assert sessions == []

    def test_list_sessions_returns_launched(self, tmp_path):
        """list_sessions returns launched sessions"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        launcher.launch(name="session-1")
        launcher.launch(name="session-2")

        sessions = launcher.list_sessions()

        assert len(sessions) == 2
        names = {s.name for s in sessions}
        assert names == {"session-1", "session-2"}


class TestLauncherKillSession:
    """Test session termination"""

    def test_kill_session(self, tmp_path):
        """Can kill a session by name"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        session = launcher.launch(name="to-kill")
        window_idx = session.tmux_window

        result = launcher.kill_session("to-kill")

        assert result is True
        assert not tmux_manager.window_exists(window_idx)
        assert session_manager.get_session_by_name("to-kill") is None

    def test_kill_nonexistent_session(self, tmp_path):
        """Killing nonexistent session returns False"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        result = launcher.kill_session("nonexistent")

        assert result is False

    def test_kill_stale_session(self, tmp_path):
        """Killing a stale session (tmux window gone) cleans up state"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        # Create a session
        session = launcher.launch(name="stale-agent")
        assert session_manager.get_session_by_name("stale-agent") is not None

        # Simulate machine reboot - tmux window gone but state still exists
        # Kill the tmux window directly without updating state
        mock_tmux.kill_window("agents", session.tmux_window)

        # Verify window is gone
        assert not tmux_manager.window_exists(session.tmux_window)
        # But session still exists in state
        assert session_manager.get_session_by_name("stale-agent") is not None

        # Now try to kill via launcher - should clean up state
        result = launcher.kill_session("stale-agent")

        assert result is True
        assert session_manager.get_session_by_name("stale-agent") is None


class TestLauncherSendToSession:
    """Test sending text to sessions"""

    def test_send_to_session(self, tmp_path):
        """Can send text to a session"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        session = launcher.launch(name="test-agent")
        initial_keys = len(mock_tmux.sent_keys)

        result = launcher.send_to_session("test-agent", "hello world")

        assert result is True
        assert len(mock_tmux.sent_keys) > initial_keys

    def test_send_to_session_updates_last_activity(self, tmp_path):
        """Sending to session updates last_activity but not steers_count.

        steers_count is tracked via supervisor log parsing, not send_to_session,
        to avoid double-counting robot interventions.
        """
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        session = launcher.launch(name="test-agent")

        launcher.send_to_session("test-agent", "message 1")
        launcher.send_to_session("test-agent", "message 2")

        updated = session_manager.get_session(session.id)
        # steers_count should NOT be incremented here (tracked via log parsing)
        assert updated.stats.steers_count == 0
        # But last_activity should be updated
        assert updated.stats.last_activity is not None

    def test_send_special_keys(self, tmp_path):
        """Can send special keys like Enter and Escape"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        launcher.launch(name="test-agent")

        result = launcher.send_to_session("test-agent", "escape")
        assert result is True

    def test_send_to_nonexistent_session(self, tmp_path):
        """Sending to nonexistent session returns False"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        result = launcher.send_to_session("nonexistent", "hello")

        assert result is False


class TestLauncherCleanup:
    """Test stale session cleanup"""

    def test_list_sessions_detects_terminated(self, tmp_path):
        """list_sessions detects and marks terminated sessions"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        # Launch then manually kill the window (simulating tmux window death)
        session = launcher.launch(name="orphan")
        assert session.status == "running"
        mock_tmux.kill_window("agents", session.tmux_window)

        # list_sessions detects terminated session and marks it
        sessions = launcher.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].status == "terminated"

        # Session is still in state file but marked terminated
        reloaded = session_manager.get_session_by_name("orphan")
        assert reloaded.status == "terminated"

    def test_cleanup_terminated_sessions(self, tmp_path):
        """cleanup_terminated_sessions removes terminated sessions"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        # Launch two sessions, then kill one window
        session1 = launcher.launch(name="alive")
        session2 = launcher.launch(name="dead")
        mock_tmux.kill_window("agents", session2.tmux_window)

        # Detect terminated session
        launcher.list_sessions()

        # Cleanup removes only terminated sessions
        count = launcher.cleanup_terminated_sessions()
        assert count == 1

        # Only alive session remains
        remaining = session_manager.list_sessions()
        assert len(remaining) == 1
        assert remaining[0].name == "alive"


class TestSessionNameValidation:
    """Test session name validation"""

    def test_valid_names_accepted(self, tmp_path):
        """Valid session names are accepted"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        # Test various valid names
        valid_names = ["test", "test-agent", "test_agent", "Test123", "a", "A" * 64]
        for name in valid_names:
            session = launcher.launch(name=name)
            assert session is not None, f"Name '{name}' should be valid"

    def test_invalid_names_rejected(self, tmp_path, capsys):
        """Invalid session names are rejected"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        # Test various invalid names
        invalid_names = [
            "",                    # empty
            "test agent",          # space
            "test;agent",          # semicolon (shell metachar)
            "test|agent",          # pipe (shell metachar)
            "test&agent",          # ampersand (shell metachar)
            "../test",             # path traversal
            "test\nagent",         # newline
            "A" * 65,              # too long
        ]
        for name in invalid_names:
            session = launcher.launch(name=name)
            assert session is None, f"Name '{name}' should be invalid"


class TestSendPromptToWindow:
    """Test _send_prompt_to_window prompt batching"""

    def test_sends_prompt_in_batches(self, tmp_path):
        """Large prompts are batched for tmux buffer limits"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        # Create a session first
        session = launcher.launch(name="test-agent")
        window_idx = session.tmux_window

        # Send a multi-line prompt (more than batch_size lines)
        large_prompt = "\n".join([f"line {i}" for i in range(25)])

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch("time.sleep"):
                result = launcher._send_prompt_to_window(window_idx, large_prompt, startup_delay=0)

        assert result is True
        # Should have multiple load-buffer and paste-buffer calls
        call_count = mock_run.call_count
        assert call_count > 2  # At least some batches plus final Enter

    def test_handles_single_line_prompt(self, tmp_path):
        """Single line prompts work correctly"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        session = launcher.launch(name="test-agent")
        window_idx = session.tmux_window

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch("time.sleep"):
                result = launcher._send_prompt_to_window(window_idx, "hello world", startup_delay=0)

        assert result is True


class TestListSessionsKillUntracked:
    """Test list_sessions with kill_untracked option"""

    def test_list_sessions_with_kill_untracked_flag(self, tmp_path):
        """kill_untracked=True should be accepted as parameter"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        # Launch a tracked session
        launcher.launch(name="tracked-agent")

        # List with kill_untracked=True should work without error
        sessions = launcher.list_sessions(kill_untracked=True)

        assert len(sessions) == 1
        assert sessions[0].name == "tracked-agent"


class TestGetSessionOutput:
    """Test get_session_output subprocess handling"""

    def test_returns_output_on_success(self, tmp_path):
        """Should return captured pane content on success"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        session = launcher.launch(name="test-agent")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="line 1\nline 2\nline 3\n"
            )

            output = launcher.get_session_output("test-agent", lines=50)

        assert output == "line 1\nline 2\nline 3"
        # Verify capture-pane was called with correct args
        call_args = mock_run.call_args[0][0]
        assert "capture-pane" in call_args
        assert "-50" in call_args

    def test_returns_none_on_failure(self, tmp_path):
        """Should return None on subprocess failure"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        session = launcher.launch(name="test-agent")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)

            output = launcher.get_session_output("test-agent")

        assert output is None

    def test_returns_none_for_nonexistent_session(self, tmp_path):
        """Should return None for nonexistent session"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        output = launcher.get_session_output("nonexistent")

        assert output is None

    def test_handles_subprocess_timeout(self, tmp_path):
        """Should return None on subprocess timeout"""
        import subprocess

        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        session = launcher.launch(name="test-agent")

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("tmux", 5)

            output = launcher.get_session_output("test-agent")

        assert output is None


class TestAttach:
    """Test attach functionality"""

    def test_attach_prints_error_when_no_session(self, tmp_path, capsys):
        """Should print error when tmux session doesn't exist"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        # Don't create any sessions
        launcher.attach()

        captured = capsys.readouterr()
        assert "does not exist" in captured.out


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
