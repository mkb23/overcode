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
from overcode.interfaces import MockTmux


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

        window_idx = manager.create_window("my-window")

        assert window_idx is not None
        assert window_idx >= 1

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
        assert manager.window_exists(999) is False

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


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
