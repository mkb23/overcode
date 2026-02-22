"""
Unit tests for ClaudeLauncher.

These tests use MockTmux and a temp directory SessionManager to test
all launcher operations without requiring real tmux or Claude.
"""

import os
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
    """Mock dependency checks so tests don't require real tmux/claude installed.

    Also strips OVERCODE_* env vars that leak from the host agent when tests
    run inside an overcode session (e.g. OVERCODE_SESSION_NAME=overcode).
    Without this, the launcher's auto-parent-detection reads the env var,
    tries to find a parent in the test's isolated SessionManager, and fails.
    """
    with patch("overcode.launcher.require_tmux"), \
         patch("overcode.launcher.require_claude"), \
         patch.dict(os.environ, {}, clear=False) as patched_env:
        for key in ["OVERCODE_SESSION_NAME", "OVERCODE_TMUX_SESSION",
                     "OVERCODE_PARENT_SESSION_ID", "OVERCODE_PARENT_NAME"]:
            patched_env.pop(key, None)
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


class TestWaitForPrompt:
    """Test _wait_for_prompt readiness polling"""

    def test_detects_prompt_immediately(self, tmp_path):
        """Returns True when Claude's prompt character is found in pane"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        launcher = ClaudeLauncher("agents", tmux_manager, session_manager)
        session = launcher.launch(name="test-agent")

        with patch("overcode.launcher.get_tmux_pane_content", return_value="some banner\n❯ \n"):
            result = launcher._wait_for_prompt(session.tmux_window, timeout=5.0)

        assert result is True

    def test_detects_angled_bracket_prompt(self, tmp_path):
        """Also detects > prompt character"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        launcher = ClaudeLauncher("agents", tmux_manager, session_manager)
        session = launcher.launch(name="test-agent")

        with patch("overcode.launcher.get_tmux_pane_content", return_value="banner\n>\n"):
            result = launcher._wait_for_prompt(session.tmux_window, timeout=5.0)

        assert result is True

    def test_returns_false_on_timeout(self, tmp_path):
        """Returns False when prompt never appears within timeout"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        launcher = ClaudeLauncher("agents", tmux_manager, session_manager)
        session = launcher.launch(name="test-agent")

        with patch("overcode.launcher.get_tmux_pane_content", return_value="loading..."):
            with patch("time.sleep"):
                result = launcher._wait_for_prompt(session.tmux_window, timeout=0.01)

        assert result is False

    def test_returns_false_when_pane_empty(self, tmp_path):
        """Returns False when pane content is None"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        launcher = ClaudeLauncher("agents", tmux_manager, session_manager)
        session = launcher.launch(name="test-agent")

        with patch("overcode.launcher.get_tmux_pane_content", return_value=None):
            with patch("time.sleep"):
                result = launcher._wait_for_prompt(session.tmux_window, timeout=0.01)

        assert result is False

    def test_polls_until_prompt_appears(self, tmp_path):
        """Polls multiple times until prompt appears"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        launcher = ClaudeLauncher("agents", tmux_manager, session_manager)
        session = launcher.launch(name="test-agent")

        # First two calls: loading, third call: prompt ready
        side_effects = ["loading...", "still loading...", "welcome\n❯ \n"]
        with patch("overcode.launcher.get_tmux_pane_content", side_effect=side_effects):
            with patch("time.sleep"):
                result = launcher._wait_for_prompt(session.tmux_window, timeout=30.0)

        assert result is True

    def test_strips_ansi_before_matching(self, tmp_path):
        """ANSI escape codes are stripped before checking for prompt"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        launcher = ClaudeLauncher("agents", tmux_manager, session_manager)
        session = launcher.launch(name="test-agent")

        # Prompt wrapped in ANSI codes
        ansi_prompt = "\x1b[32m❯\x1b[0m \n"
        with patch("overcode.launcher.get_tmux_pane_content", return_value=ansi_prompt):
            result = launcher._wait_for_prompt(session.tmux_window, timeout=5.0)

        assert result is True


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

        with patch.object(launcher, "_wait_for_prompt", return_value=True):
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

        with patch.object(launcher, "_wait_for_prompt", return_value=True):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                with patch("time.sleep"):
                    result = launcher._send_prompt_to_window(window_idx, "hello world", startup_delay=0)

        assert result is True

    def test_falls_back_to_delay_when_prompt_not_detected(self, tmp_path):
        """Uses startup_delay fallback when _wait_for_prompt times out"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        launcher = ClaudeLauncher("agents", tmux_manager, session_manager)
        session = launcher.launch(name="test-agent")

        with patch.object(launcher, "_wait_for_prompt", return_value=False):
            with patch("overcode.launcher.send_text_to_tmux_window", return_value=True) as mock_send:
                result = launcher._send_prompt_to_window(session.tmux_window, "hello", startup_delay=5.0)

        assert result is True
        # Should have been called with the fallback startup_delay
        mock_send.assert_called_once_with(
            "agents", session.tmux_window, "hello", send_enter=True, startup_delay=5.0
        )

    def test_skips_delay_when_prompt_detected(self, tmp_path):
        """Sends immediately with no delay when _wait_for_prompt succeeds"""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        launcher = ClaudeLauncher("agents", tmux_manager, session_manager)
        session = launcher.launch(name="test-agent")

        with patch.object(launcher, "_wait_for_prompt", return_value=True):
            with patch("overcode.launcher.send_text_to_tmux_window", return_value=True) as mock_send:
                result = launcher._send_prompt_to_window(session.tmux_window, "hello", startup_delay=5.0)

        assert result is True
        # Should have been called with startup_delay=0 since prompt was detected
        mock_send.assert_called_once_with(
            "agents", session.tmux_window, "hello", send_enter=True, startup_delay=0
        )


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

    def test_attach_by_name(self, tmp_path):
        """Should resolve agent name to window index"""
        mock_tmux = MockTmux()
        mock_tmux.sessions["agents"] = {}
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        # Launch an agent so we have a name -> window mapping
        with patch.dict('os.environ', {'CLAUDE_COMMAND': 'echo'}):
            launcher.launch(name="test-agent", start_directory=str(tmp_path))

        # attach with name should call attach_session with the window index
        with patch.object(tmux_manager, 'attach_session') as mock_attach:
            launcher.attach(name="test-agent")
            mock_attach.assert_called_once()
            _, kwargs = mock_attach.call_args
            assert kwargs['window'] is not None
            assert kwargs['bare'] is False

    def test_attach_by_name_not_found(self, tmp_path, capsys):
        """Should error when agent name doesn't exist"""
        mock_tmux = MockTmux()
        mock_tmux.sessions["agents"] = {}
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        launcher.attach(name="nonexistent")

        captured = capsys.readouterr()
        assert "not found" in captured.out

    def test_attach_bare_mode(self, tmp_path):
        """Should pass bare=True through to attach_session"""
        mock_tmux = MockTmux()
        mock_tmux.sessions["agents"] = {}
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager
        )

        with patch.dict('os.environ', {'CLAUDE_COMMAND': 'echo'}):
            launcher.launch(name="test-agent", start_directory=str(tmp_path))

        with patch.object(tmux_manager, 'attach_session') as mock_attach:
            launcher.attach(name="test-agent", bare=True)
            mock_attach.assert_called_once()
            _, kwargs = mock_attach.call_args
            assert kwargs['bare'] is True


# =============================================================================
# Agent Hierarchy Tests (#244)
# =============================================================================


class TestLauncherHierarchy:
    """Test parent/child hierarchy in launcher"""

    def test_launch_with_parent_name(self, tmp_path):
        """Can launch a child agent with explicit parent."""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager,
        )

        parent = launcher.launch(name="parent-agent")
        assert parent is not None

        child = launcher.launch(name="child-agent", parent_name="parent-agent")
        assert child is not None
        assert child.parent_session_id == parent.id

    def test_launch_parent_not_found(self, tmp_path, capsys):
        """Launch fails when parent doesn't exist."""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager,
        )

        child = launcher.launch(name="child-agent", parent_name="nonexistent")
        assert child is None
        captured = capsys.readouterr()
        assert "not found" in captured.out

    def test_auto_detect_parent_from_env(self, tmp_path):
        """Auto-detects parent from OVERCODE_SESSION_NAME env var."""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager,
        )

        parent = launcher.launch(name="auto-parent")
        assert parent is not None

        with patch.dict("os.environ", {"OVERCODE_SESSION_NAME": "auto-parent"}):
            child = launcher.launch(name="auto-child")
            assert child is not None
            assert child.parent_session_id == parent.id

    def test_depth_limit_enforced(self, tmp_path, capsys):
        """Launch fails when max depth would be exceeded."""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager,
        )

        # Build chain up to max depth
        prev = launcher.launch(name="level-0")
        for i in range(1, ClaudeLauncher.MAX_HIERARCHY_DEPTH):
            child = launcher.launch(name=f"level-{i}", parent_name=f"level-{i-1}")
            assert child is not None, f"Should succeed at depth {i}"
            prev = child

        # One more should fail
        too_deep = launcher.launch(
            name="too-deep",
            parent_name=f"level-{ClaudeLauncher.MAX_HIERARCHY_DEPTH - 1}",
        )
        assert too_deep is None
        captured = capsys.readouterr()
        assert "depth" in captured.out.lower()

    def test_parent_env_vars_propagated(self, tmp_path):
        """Parent env vars are included in the command sent to tmux."""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager,
        )

        parent = launcher.launch(name="env-parent")
        child = launcher.launch(name="env-child", parent_name="env-parent")

        assert child is not None
        # Check that the env vars were sent to tmux
        sent_commands = [k[2] for k in mock_tmux.sent_keys]
        child_cmd = [c for c in sent_commands if "env-child" in c]
        assert any("OVERCODE_PARENT_SESSION_ID" in cmd for cmd in child_cmd)
        assert any("OVERCODE_PARENT_NAME=env-parent" in cmd for cmd in child_cmd)


class TestCascadeKill:
    """Test cascade kill functionality."""

    def test_cascade_kill_children(self, tmp_path):
        """Killing parent cascades to children by default."""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager,
        )

        parent = launcher.launch(name="parent")
        child1 = launcher.launch(name="child1", parent_name="parent")
        child2 = launcher.launch(name="child2", parent_name="parent")

        result = launcher.kill_session("parent", cascade=True)
        assert result is True

        # All sessions should be gone
        assert session_manager.get_session_by_name("parent") is None
        assert session_manager.get_session_by_name("child1") is None
        assert session_manager.get_session_by_name("child2") is None

    def test_no_cascade_orphans_children(self, tmp_path):
        """Killing parent with cascade=False orphans children."""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager,
        )

        parent = launcher.launch(name="parent")
        child = launcher.launch(name="child", parent_name="parent")

        result = launcher.kill_session("parent", cascade=False)
        assert result is True

        # Parent gone, child still exists but orphaned
        assert session_manager.get_session_by_name("parent") is None
        orphan = session_manager.get_session_by_name("child")
        assert orphan is not None
        assert orphan.parent_session_id is None

    def test_cascade_kill_deepest_first(self, tmp_path):
        """Cascade kill removes deepest descendants first."""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager,
        )

        root = launcher.launch(name="root")
        child = launcher.launch(name="child", parent_name="root")
        grandchild = launcher.launch(name="grandchild", parent_name="child")

        result = launcher.kill_session("root", cascade=True)
        assert result is True

        # All three should be gone
        assert session_manager.get_session_by_name("root") is None
        assert session_manager.get_session_by_name("child") is None
        assert session_manager.get_session_by_name("grandchild") is None


# =============================================================================
# Claude CLI Flag Passthrough Tests (#290)
# =============================================================================


class TestCLIFlagPassthrough:
    """Test --allowed-tools and --claude-arg passthrough."""

    def test_launch_with_allowed_tools(self, tmp_path):
        """--allowedTools appears in the tmux command string."""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager,
        )

        session = launcher.launch(name="scoped", allowed_tools="Read,Glob,Grep")

        assert session is not None
        sent_commands = [k[2] for k in mock_tmux.sent_keys]
        assert any("--allowedTools" in cmd and "Read,Glob,Grep" in cmd for cmd in sent_commands)

    def test_launch_with_extra_claude_args(self, tmp_path):
        """Extra args are appended to the claude command."""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager,
        )

        session = launcher.launch(
            name="custom",
            extra_claude_args=["--model haiku", "--effort low"],
        )

        assert session is not None
        sent_commands = [k[2] for k in mock_tmux.sent_keys]
        assert any("--model" in cmd and "haiku" in cmd for cmd in sent_commands)
        assert any("--effort" in cmd and "low" in cmd for cmd in sent_commands)

    def test_launch_with_both_allowed_tools_and_extra_args(self, tmp_path):
        """Both --allowedTools and extra args appear in the command."""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager,
        )

        session = launcher.launch(
            name="both",
            allowed_tools="Read,Write",
            extra_claude_args=["--model haiku"],
        )

        assert session is not None
        sent_commands = [k[2] for k in mock_tmux.sent_keys]
        assert any("--allowedTools" in cmd and "Read,Write" in cmd for cmd in sent_commands)
        assert any("--model" in cmd and "haiku" in cmd for cmd in sent_commands)

    def test_session_stores_allowed_tools(self, tmp_path):
        """allowed_tools and extra_claude_args are persisted in session state."""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager,
        )

        session = launcher.launch(
            name="persist",
            allowed_tools="Read,Glob",
            extra_claude_args=["--model haiku"],
        )

        # Reload from state to verify persistence
        reloaded = session_manager.get_session(session.id)
        assert reloaded.allowed_tools == "Read,Glob"
        assert reloaded.extra_claude_args == ["--model haiku"]

    def test_session_without_flags_has_defaults(self, tmp_path):
        """Sessions without flags have None/empty defaults."""
        mock_tmux = MockTmux()
        tmux_manager = TmuxManager("agents", tmux=mock_tmux)
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=tmux_manager,
            session_manager=session_manager,
        )

        session = launcher.launch(name="defaults")

        reloaded = session_manager.get_session(session.id)
        assert reloaded.allowed_tools is None
        assert reloaded.extra_claude_args == []


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
