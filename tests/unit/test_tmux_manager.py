"""
Unit tests for TmuxManager.

These tests use MockTmux to test all tmux operations without
requiring a real tmux installation.
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.tmux_manager import TmuxManager
from overcode.mocks import MockTmux


class TestTmuxManagerSession:
    """Test session management operations"""

    def test_session_exists_returns_false_when_no_session(self):
        """Returns False when session doesn't exist"""
        mock_tmux = MockTmux()
        manager = TmuxManager("agents", tmux=mock_tmux)

        assert manager.session_exists() is False

    def test_session_exists_returns_true_when_session_exists(self):
        """Returns True when session exists"""
        mock_tmux = MockTmux()
        mock_tmux.new_session("agents")
        manager = TmuxManager("agents", tmux=mock_tmux)

        assert manager.session_exists() is True

    def test_ensure_session_creates_session(self):
        """ensure_session creates session if it doesn't exist"""
        mock_tmux = MockTmux()
        manager = TmuxManager("agents", tmux=mock_tmux)

        result = manager.ensure_session()

        assert result is True
        assert mock_tmux.has_session("agents")

    def test_ensure_session_returns_true_if_exists(self):
        """ensure_session returns True if session already exists"""
        mock_tmux = MockTmux()
        mock_tmux.new_session("agents")
        manager = TmuxManager("agents", tmux=mock_tmux)

        result = manager.ensure_session()

        assert result is True

    def test_kill_session(self):
        """Can kill a session"""
        mock_tmux = MockTmux()
        mock_tmux.new_session("agents")
        manager = TmuxManager("agents", tmux=mock_tmux)

        result = manager.kill_session()

        assert result is True
        assert not mock_tmux.has_session("agents")


class TestTmuxManagerWindows:
    """Test window management operations"""

    def test_create_window(self):
        """Can create a new window"""
        mock_tmux = MockTmux()
        mock_tmux.new_session("agents")
        manager = TmuxManager("agents", tmux=mock_tmux)

        window_name = manager.create_window("my-window")

        assert window_name is not None
        assert window_name == "my-window"

    def test_create_window_with_directory(self):
        """Can create window with start directory"""
        mock_tmux = MockTmux()
        mock_tmux.new_session("agents")
        manager = TmuxManager("agents", tmux=mock_tmux)

        window_idx = manager.create_window("my-window", start_directory="/tmp")

        assert window_idx is not None

    def test_create_multiple_windows(self):
        """Can create multiple windows with unique indices"""
        mock_tmux = MockTmux()
        mock_tmux.new_session("agents")
        manager = TmuxManager("agents", tmux=mock_tmux)

        idx1 = manager.create_window("window-1")
        idx2 = manager.create_window("window-2")
        idx3 = manager.create_window("window-3")

        assert idx1 != idx2 != idx3
        assert len({idx1, idx2, idx3}) == 3

    def test_window_exists(self):
        """Can check if window exists"""
        mock_tmux = MockTmux()
        mock_tmux.new_session("agents")
        manager = TmuxManager("agents", tmux=mock_tmux)

        window_idx = manager.create_window("my-window")

        assert manager.window_exists(window_idx) is True
        assert manager.window_exists("nonexistent") is False

    def test_list_windows(self):
        """Can list all windows"""
        mock_tmux = MockTmux()
        mock_tmux.new_session("agents")
        manager = TmuxManager("agents", tmux=mock_tmux)

        manager.create_window("window-1")
        manager.create_window("window-2")

        windows = manager.list_windows()

        assert len(windows) >= 2
        names = [w['name'] for w in windows]
        assert "window-1" in names
        assert "window-2" in names

    def test_kill_window(self):
        """Can kill a specific window"""
        mock_tmux = MockTmux()
        mock_tmux.new_session("agents")
        manager = TmuxManager("agents", tmux=mock_tmux)

        window_idx = manager.create_window("to-kill")
        assert manager.window_exists(window_idx)

        result = manager.kill_window(window_idx)

        assert result is True
        assert manager.window_exists(window_idx) is False

    def test_list_windows_empty_session(self):
        """list_windows returns empty list for non-existent session"""
        mock_tmux = MockTmux()
        manager = TmuxManager("nonexistent", tmux=mock_tmux)

        windows = manager.list_windows()

        assert windows == []


class TestTmuxManagerKeys:
    """Test key sending operations"""

    def test_send_keys(self):
        """Can send keys to a window"""
        mock_tmux = MockTmux()
        mock_tmux.new_session("agents")
        manager = TmuxManager("agents", tmux=mock_tmux)
        window_idx = manager.create_window("my-window")

        result = manager.send_keys(window_idx, "echo hello")

        assert result is True
        # Check that keys were recorded
        assert len(mock_tmux.sent_keys) == 1
        assert mock_tmux.sent_keys[0] == ("agents", window_idx, "echo hello", True)

    def test_send_keys_without_enter(self):
        """Can send keys without pressing Enter"""
        mock_tmux = MockTmux()
        mock_tmux.new_session("agents")
        manager = TmuxManager("agents", tmux=mock_tmux)
        window_idx = manager.create_window("my-window")

        result = manager.send_keys(window_idx, "partial", enter=False)

        assert result is True
        assert mock_tmux.sent_keys[0] == ("agents", window_idx, "partial", False)

    def test_send_multiple_keys(self):
        """Can send multiple key sequences"""
        mock_tmux = MockTmux()
        mock_tmux.new_session("agents")
        manager = TmuxManager("agents", tmux=mock_tmux)
        window_idx = manager.create_window("my-window")

        manager.send_keys(window_idx, "line 1")
        manager.send_keys(window_idx, "line 2")
        manager.send_keys(window_idx, "line 3")

        assert len(mock_tmux.sent_keys) == 3


class TestTmuxManagerEdgeCases:
    """Test edge cases and error handling"""

    def test_create_window_without_session(self):
        """Creating window auto-creates session"""
        mock_tmux = MockTmux()
        manager = TmuxManager("agents", tmux=mock_tmux)

        # Session doesn't exist yet
        assert not mock_tmux.has_session("agents")

        window_idx = manager.create_window("my-window")

        # Session should be created
        assert mock_tmux.has_session("agents")
        assert window_idx is not None

    def test_different_session_names(self):
        """Can manage multiple different sessions"""
        mock_tmux = MockTmux()

        manager1 = TmuxManager("session1", tmux=mock_tmux)
        manager2 = TmuxManager("session2", tmux=mock_tmux)

        manager1.ensure_session()
        manager2.ensure_session()

        assert mock_tmux.has_session("session1")
        assert mock_tmux.has_session("session2")

        # Windows are session-specific
        w1 = manager1.create_window("win1")
        w2 = manager2.create_window("win2")

        assert manager1.window_exists(w1)
        assert manager2.window_exists(w2)


from unittest.mock import MagicMock, patch, call  # noqa: E402


class TestTmuxManagerAttachSession:
    """Test attach_session (mock os.execvp)."""

    @pytest.fixture(autouse=True)
    def _default_socket(self, monkeypatch):
        # These pin the argv for the default server; the run's private socket
        # (tests/conftest.py) would add -L. The tmux calls are all mocked.
        monkeypatch.delenv("OVERCODE_TMUX_SOCKET", raising=False)

    @patch("overcode.tmux_manager.os.execvp")
    def test_attach_session_default(self, mock_execvp):
        """attach_session calls os.execvp with session name."""
        manager = TmuxManager("agents")

        manager.attach_session()

        mock_execvp.assert_called_once_with(
            "tmux", ["tmux", "attach-session", "-t", "agents"]
        )

    @patch("overcode.tmux_manager.os.execvp")
    def test_attach_session_with_window(self, mock_execvp):
        """attach_session includes window in target when specified."""
        manager = TmuxManager("agents")

        manager.attach_session(window="my-window")

        mock_execvp.assert_called_once_with(
            "tmux", ["tmux", "attach-session", "-t", "agents:=my-window"]
        )

    @patch("overcode.tmux_manager.os.execvp")
    def test_attach_session_respects_socket_env(self, mock_execvp, monkeypatch):
        """attach_session adds -L when OVERCODE_TMUX_SOCKET is set."""
        monkeypatch.setenv("OVERCODE_TMUX_SOCKET", "mysock")
        manager = TmuxManager("agents")

        manager.attach_session()

        mock_execvp.assert_called_once_with(
            "tmux", ["tmux", "-L", "mysock", "attach-session", "-t", "agents"]
        )

    @patch("overcode.tmux_utils.attach_bare")
    def test_attach_session_bare_delegates(self, mock_attach_bare):
        """attach_session with bare=True delegates to attach_bare."""
        manager = TmuxManager("agents")

        manager.attach_session(window="my-window", bare=True)

        mock_attach_bare.assert_called_once_with("agents", "my-window", socket_path=None)


class TestTmuxManagerAttachBare:
    """Test _attach_bare (mock subprocess.run and os.execlp)."""

    @pytest.fixture(autouse=True)
    def _default_socket(self, monkeypatch):
        # These pin the argv for the default server; the run's private socket
        # (tests/conftest.py) would add -L. The tmux calls are all mocked.
        monkeypatch.delenv("OVERCODE_TMUX_SOCKET", raising=False)

    @patch("overcode.tmux_manager.os.execlp")
    @patch("subprocess.run")
    def test_attach_bare_success(self, mock_run, mock_execlp):
        """_attach_bare creates linked session with correct config and attaches."""
        manager = TmuxManager("agents")
        # All subprocess.run calls succeed
        mock_run.return_value = MagicMock(returncode=0)

        manager.attach_session(window="my-window", bare=True)

        # Verify the subprocess.run calls
        calls = mock_run.call_args_list

        # 1. Kill stale bare session
        assert calls[0] == call(
            ["tmux", "kill-session", "-t", "bare-agents-my-window"],
            capture_output=True,
        )

        # 2. Create linked session
        assert calls[1] == call(
            ["tmux", "new-session", "-d", "-s", "bare-agents-my-window", "-t", "agents"],
            capture_output=True,
        )

        # 3. Configure: status off
        assert calls[2] == call(
            ["tmux", "set", "-t", "bare-agents-my-window", "status", "off"],
            capture_output=True,
        )

        # 4. Configure: mouse off
        assert calls[3] == call(
            ["tmux", "set", "-t", "bare-agents-my-window", "mouse", "off"],
            capture_output=True,
        )

        # 5. Configure: destroy-unattached hook
        assert calls[4] == call(
            ["tmux", "set-hook", "-t", "bare-agents-my-window", "client-attached",
             "set destroy-unattached on"],
            capture_output=True,
        )

        # 6. Select window
        assert calls[5] == call(
            ["tmux", "select-window", "-t", "bare-agents-my-window:=my-window"],
            capture_output=True,
        )

        # 7. Final attach via execlp
        mock_execlp.assert_called_once_with(
            "tmux", "tmux", "attach-session", "-t", "bare-agents-my-window"
        )

    @patch("overcode.tmux_manager.os.execlp")
    @patch("subprocess.run")
    def test_attach_bare_fails_to_create_linked_session(self, mock_run, mock_execlp):
        """_attach_bare returns early when linked session creation fails."""
        manager = TmuxManager("agents")

        def run_side_effect(cmd, **kwargs):
            result = MagicMock()
            if "new-session" in cmd:
                result.returncode = 1
                result.stderr = b"session creation failed"
            else:
                result.returncode = 0
            return result

        mock_run.side_effect = run_side_effect

        manager.attach_session(window="my-window", bare=True)

        # Should have called kill-session and new-session, then stopped
        assert mock_run.call_count == 2
        # Should NOT have called execlp
        mock_execlp.assert_not_called()

    @patch("overcode.tmux_manager.os.execlp")
    @patch("subprocess.run")
    def test_attach_bare_session_name_includes_window(self, mock_run, mock_execlp):
        """_attach_bare constructs session name as bare-{session}-{window}."""
        manager = TmuxManager("my-session")
        mock_run.return_value = MagicMock(returncode=0)

        manager.attach_session(window="win-7", bare=True)

        # Check the bare session name in kill-session call
        first_call_cmd = mock_run.call_args_list[0][0][0]
        assert first_call_cmd == ["tmux", "kill-session", "-t", "bare-my-session-win-7"]

        # Check attach target
        mock_execlp.assert_called_once_with(
            "tmux", "tmux", "attach-session", "-t", "bare-my-session-win-7"
        )


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
