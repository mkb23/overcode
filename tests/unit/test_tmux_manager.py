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
# Libtmux direct path tests (when self._tmux is None)
#
# These test the code paths that use libtmux directly, by mocking
# libtmux objects instead of using MockTmux.
# =============================================================================

from unittest.mock import MagicMock, patch, call
import libtmux
from libtmux.exc import LibTmuxException
from libtmux._internal.query_list import ObjectDoesNotExist


class TestTmuxManagerServerProperty:
    """Test lazy server initialization, socket name, and caching."""

    def test_server_creates_default_server_when_no_socket(self):
        """Server property creates libtmux.Server() with no args when socket is None."""
        manager = TmuxManager("agents")
        manager.socket = None

        with patch("overcode.tmux_manager.libtmux.Server") as mock_server_cls:
            mock_server_cls.return_value = MagicMock()
            server = manager.server
            mock_server_cls.assert_called_once_with()
            assert server is mock_server_cls.return_value

    def test_server_creates_server_with_socket_name(self):
        """Server property passes socket_name when socket is set."""
        manager = TmuxManager("agents", socket="test-socket")

        with patch("overcode.tmux_manager.libtmux.Server") as mock_server_cls:
            mock_server_cls.return_value = MagicMock()
            server = manager.server
            mock_server_cls.assert_called_once_with(socket_name="test-socket")
            assert server is mock_server_cls.return_value

    def test_server_uses_env_var_for_socket(self):
        """Socket name falls back to OVERCODE_TMUX_SOCKET env var."""
        with patch.dict("os.environ", {"OVERCODE_TMUX_SOCKET": "env-socket"}):
            manager = TmuxManager("agents")
            assert manager.socket == "env-socket"

    def test_server_caches_instance(self):
        """Server property caches the server instance on subsequent calls."""
        manager = TmuxManager("agents")
        manager.socket = None

        with patch("overcode.tmux_manager.libtmux.Server") as mock_server_cls:
            mock_server_cls.return_value = MagicMock()
            server1 = manager.server
            server2 = manager.server
            # Only called once due to caching
            mock_server_cls.assert_called_once()
            assert server1 is server2

    def test_server_does_not_recreate_if_already_set(self):
        """If _server is already set, the property returns it directly."""
        manager = TmuxManager("agents")
        mock_server = MagicMock()
        manager._server = mock_server

        assert manager.server is mock_server


class TestTmuxManagerLibtmuxSession:
    """Test _get_session, session_exists, ensure_session via libtmux paths."""

    def _make_manager(self):
        """Create a TmuxManager with no _tmux and a mocked server."""
        manager = TmuxManager("agents")
        manager._server = MagicMock()
        return manager

    def test_get_session_returns_session(self):
        """_get_session returns the session when it exists."""
        manager = self._make_manager()
        mock_session = MagicMock()
        manager._server.sessions.get.return_value = mock_session

        result = manager._get_session()

        assert result is mock_session
        manager._server.sessions.get.assert_called_once_with(session_name="agents")

    def test_get_session_returns_none_on_libtmux_exception(self):
        """_get_session returns None when LibTmuxException is raised."""
        manager = self._make_manager()
        manager._server.sessions.get.side_effect = LibTmuxException()

        result = manager._get_session()

        assert result is None

    def test_get_session_returns_none_on_object_does_not_exist(self):
        """_get_session returns None when ObjectDoesNotExist is raised."""
        manager = self._make_manager()
        manager._server.sessions.get.side_effect = ObjectDoesNotExist()

        result = manager._get_session()

        assert result is None

    def test_session_exists_returns_true(self):
        """session_exists returns True when server.has_session returns True."""
        manager = self._make_manager()
        manager._server.has_session.return_value = True

        assert manager.session_exists() is True
        manager._server.has_session.assert_called_once_with("agents")

    def test_session_exists_returns_false(self):
        """session_exists returns False when server.has_session returns False."""
        manager = self._make_manager()
        manager._server.has_session.return_value = False

        assert manager.session_exists() is False

    def test_session_exists_returns_false_on_exception(self):
        """session_exists returns False when LibTmuxException is raised."""
        manager = self._make_manager()
        manager._server.has_session.side_effect = LibTmuxException()

        assert manager.session_exists() is False

    def test_ensure_session_returns_true_when_exists(self):
        """ensure_session returns True when session already exists."""
        manager = self._make_manager()
        manager._server.has_session.return_value = True

        result = manager.ensure_session()

        assert result is True
        # Should not try to create a new session
        manager._server.new_session.assert_not_called()

    def test_ensure_session_creates_session_when_missing(self):
        """ensure_session creates session via server.new_session when it doesn't exist."""
        manager = self._make_manager()
        manager._server.has_session.return_value = False

        result = manager.ensure_session()

        assert result is True
        manager._server.new_session.assert_called_once_with(
            session_name="agents", attach=False
        )

    def test_ensure_session_returns_false_on_create_failure(self):
        """ensure_session returns False when new_session raises LibTmuxException."""
        manager = self._make_manager()
        manager._server.has_session.return_value = False
        manager._server.new_session.side_effect = LibTmuxException()

        result = manager.ensure_session()

        assert result is False


class TestTmuxManagerLibtmuxWindows:
    """Test window operations via libtmux paths."""

    def _make_manager_with_session(self):
        """Create a TmuxManager with mocked server and session."""
        manager = TmuxManager("agents")
        manager._server = MagicMock()
        manager._server.has_session.return_value = True
        mock_session = MagicMock()
        manager._server.sessions.get.return_value = mock_session
        return manager, mock_session

    def test_create_window_returns_index(self):
        """create_window returns the window index on success."""
        manager, mock_session = self._make_manager_with_session()
        mock_window = MagicMock()
        mock_window.window_index = "3"
        mock_session.new_window.return_value = mock_window

        result = manager.create_window("my-window")

        assert result == 3
        mock_session.new_window.assert_called_once_with(
            window_name="my-window", attach=False
        )

    def test_create_window_with_start_directory(self):
        """create_window passes start_directory when provided."""
        manager, mock_session = self._make_manager_with_session()
        mock_window = MagicMock()
        mock_window.window_index = "5"
        mock_session.new_window.return_value = mock_window

        result = manager.create_window("my-window", start_directory="/tmp")

        assert result == 5
        mock_session.new_window.assert_called_once_with(
            window_name="my-window", attach=False, start_directory="/tmp"
        )

    def test_create_window_returns_none_when_session_missing(self):
        """create_window returns None when _get_session returns None."""
        manager = TmuxManager("agents")
        manager._server = MagicMock()
        # ensure_session succeeds but _get_session returns None
        manager._server.has_session.return_value = True
        manager._server.sessions.get.side_effect = ObjectDoesNotExist()

        result = manager.create_window("my-window")

        assert result is None

    def test_create_window_returns_none_on_exception(self):
        """create_window returns None when new_window raises LibTmuxException."""
        manager, mock_session = self._make_manager_with_session()
        mock_session.new_window.side_effect = LibTmuxException()

        result = manager.create_window("my-window")

        assert result is None

    def test_create_window_returns_none_on_value_error(self):
        """create_window returns None when int() conversion fails (ValueError)."""
        manager, mock_session = self._make_manager_with_session()
        mock_window = MagicMock()
        mock_window.window_index = "not-a-number"
        mock_session.new_window.return_value = mock_window

        result = manager.create_window("my-window")

        assert result is None

    def test_get_window_returns_window(self):
        """_get_window returns the window when found by index."""
        manager, mock_session = self._make_manager_with_session()
        mock_window = MagicMock()
        mock_session.windows.get.return_value = mock_window

        result = manager._get_window(2)

        assert result is mock_window
        mock_session.windows.get.assert_called_once_with(window_index="2")

    def test_get_window_returns_none_when_no_session(self):
        """_get_window returns None when session doesn't exist."""
        manager = TmuxManager("agents")
        manager._server = MagicMock()
        manager._server.sessions.get.side_effect = ObjectDoesNotExist()

        result = manager._get_window(1)

        assert result is None

    def test_get_window_returns_none_on_exception(self):
        """_get_window returns None when windows.get raises exception."""
        manager, mock_session = self._make_manager_with_session()
        mock_session.windows.get.side_effect = ObjectDoesNotExist()

        result = manager._get_window(99)

        assert result is None

    def test_get_pane_returns_first_pane(self):
        """_get_pane returns the first pane of the window."""
        manager, mock_session = self._make_manager_with_session()
        mock_pane = MagicMock()
        mock_window = MagicMock()
        mock_window.panes = [mock_pane, MagicMock()]
        mock_session.windows.get.return_value = mock_window

        result = manager._get_pane(1)

        assert result is mock_pane

    def test_get_pane_returns_none_when_no_window(self):
        """_get_pane returns None when window doesn't exist."""
        manager = TmuxManager("agents")
        manager._server = MagicMock()
        manager._server.sessions.get.side_effect = ObjectDoesNotExist()

        result = manager._get_pane(1)

        assert result is None

    def test_get_pane_returns_none_when_no_panes(self):
        """_get_pane returns None when window has no panes."""
        manager, mock_session = self._make_manager_with_session()
        mock_window = MagicMock()
        mock_window.panes = []
        mock_session.windows.get.return_value = mock_window

        result = manager._get_pane(1)

        assert result is None

    def test_list_windows_returns_window_details(self):
        """list_windows returns list of dicts with index, name, command."""
        manager, mock_session = self._make_manager_with_session()

        mock_pane1 = MagicMock()
        mock_pane1.pane_current_command = "bash"
        mock_win1 = MagicMock()
        mock_win1.window_index = "1"
        mock_win1.window_name = "editor"
        mock_win1.panes = [mock_pane1]

        mock_pane2 = MagicMock()
        mock_pane2.pane_current_command = "python"
        mock_win2 = MagicMock()
        mock_win2.window_index = "2"
        mock_win2.window_name = "runner"
        mock_win2.panes = [mock_pane2]

        mock_session.windows = [mock_win1, mock_win2]

        result = manager.list_windows()

        assert len(result) == 2
        assert result[0] == {"index": 1, "name": "editor", "command": "bash"}
        assert result[1] == {"index": 2, "name": "runner", "command": "python"}

    def test_list_windows_handles_empty_panes(self):
        """list_windows returns empty command when window has no panes."""
        manager, mock_session = self._make_manager_with_session()

        mock_win = MagicMock()
        mock_win.window_index = "0"
        mock_win.window_name = "empty"
        mock_win.panes = []
        mock_session.windows = [mock_win]

        result = manager.list_windows()

        assert len(result) == 1
        assert result[0] == {"index": 0, "name": "empty", "command": ""}

    def test_list_windows_returns_empty_when_no_session(self):
        """list_windows returns [] when session doesn't exist."""
        manager = TmuxManager("agents")
        manager._server = MagicMock()
        manager._server.has_session.return_value = False

        result = manager.list_windows()

        assert result == []

    def test_list_windows_returns_empty_on_exception(self):
        """list_windows returns [] when LibTmuxException is raised."""
        manager = TmuxManager("agents")
        manager._server = MagicMock()
        manager._server.has_session.return_value = True
        manager._server.sessions.get.side_effect = LibTmuxException()

        result = manager.list_windows()

        assert result == []

    def test_list_windows_handles_none_pane_command(self):
        """list_windows handles pane_current_command being None."""
        manager, mock_session = self._make_manager_with_session()

        mock_pane = MagicMock()
        mock_pane.pane_current_command = None
        mock_win = MagicMock()
        mock_win.window_index = "1"
        mock_win.window_name = "test"
        mock_win.panes = [mock_pane]
        mock_session.windows = [mock_win]

        result = manager.list_windows()

        assert result[0]["command"] == ""

    def test_kill_window_success(self):
        """kill_window calls win.kill() and returns True."""
        manager, mock_session = self._make_manager_with_session()
        mock_window = MagicMock()
        mock_session.windows.get.return_value = mock_window

        result = manager.kill_window(3)

        assert result is True
        mock_window.kill.assert_called_once()

    def test_kill_window_returns_false_when_not_found(self):
        """kill_window returns False when window doesn't exist."""
        manager, mock_session = self._make_manager_with_session()
        mock_session.windows.get.side_effect = ObjectDoesNotExist()

        result = manager.kill_window(99)

        assert result is False

    def test_kill_window_returns_false_on_exception(self):
        """kill_window returns False when kill() raises LibTmuxException."""
        manager, mock_session = self._make_manager_with_session()
        mock_window = MagicMock()
        mock_window.kill.side_effect = LibTmuxException()
        mock_session.windows.get.return_value = mock_window

        result = manager.kill_window(3)

        assert result is False

    def test_window_exists_true(self):
        """window_exists returns True when window index is found."""
        manager, mock_session = self._make_manager_with_session()
        mock_win = MagicMock()
        mock_win.window_index = "2"
        mock_session.windows = [mock_win]

        result = manager.window_exists(2)

        assert result is True

    def test_window_exists_false(self):
        """window_exists returns False when window index is not found."""
        manager, mock_session = self._make_manager_with_session()
        mock_win = MagicMock()
        mock_win.window_index = "1"
        mock_session.windows = [mock_win]

        result = manager.window_exists(99)

        assert result is False

    def test_window_exists_false_when_no_session(self):
        """window_exists returns False when session doesn't exist."""
        manager = TmuxManager("agents")
        manager._server = MagicMock()
        manager._server.has_session.return_value = False

        result = manager.window_exists(1)

        assert result is False

    def test_window_exists_returns_false_on_exception(self):
        """window_exists returns False when LibTmuxException is raised."""
        manager = TmuxManager("agents")
        manager._server = MagicMock()
        manager._server.has_session.return_value = True
        manager._server.sessions.get.side_effect = LibTmuxException()

        result = manager.window_exists(1)

        assert result is False


class TestTmuxManagerLibtmuxKeys:
    """Test send_keys via libtmux path including ! command handling."""

    def _make_manager_with_pane(self):
        """Create a TmuxManager with a mocked pane accessible via _get_pane."""
        manager = TmuxManager("agents")
        manager._server = MagicMock()
        mock_session = MagicMock()
        manager._server.sessions.get.return_value = mock_session
        mock_pane = MagicMock()
        mock_window = MagicMock()
        mock_window.panes = [mock_pane]
        mock_session.windows.get.return_value = mock_window
        return manager, mock_pane

    @patch("overcode.tmux_manager.time.sleep")
    def test_send_keys_normal_text_with_enter(self, mock_sleep):
        """send_keys sends text then Enter separately with delay."""
        manager, mock_pane = self._make_manager_with_pane()

        result = manager.send_keys(1, "echo hello", enter=True)

        assert result is True
        assert mock_pane.send_keys.call_count == 2
        mock_pane.send_keys.assert_any_call("echo hello", enter=False)
        mock_pane.send_keys.assert_any_call("", enter=True)
        mock_sleep.assert_called_once_with(0.1)

    @patch("overcode.tmux_manager.time.sleep")
    def test_send_keys_normal_text_without_enter(self, mock_sleep):
        """send_keys sends text only when enter=False."""
        manager, mock_pane = self._make_manager_with_pane()

        result = manager.send_keys(1, "partial text", enter=False)

        assert result is True
        mock_pane.send_keys.assert_called_once_with("partial text", enter=False)
        # No Enter call
        mock_sleep.assert_called_once_with(0.1)

    @patch("overcode.tmux_manager.time.sleep")
    def test_send_keys_empty_text_with_enter(self, mock_sleep):
        """send_keys with empty text and enter=True sends only Enter."""
        manager, mock_pane = self._make_manager_with_pane()

        result = manager.send_keys(1, "", enter=True)

        assert result is True
        # Empty keys means the `if keys:` block is skipped
        mock_pane.send_keys.assert_called_once_with("", enter=True)
        mock_sleep.assert_not_called()

    @patch("overcode.tmux_manager.time.sleep")
    def test_send_keys_empty_text_without_enter(self, mock_sleep):
        """send_keys with empty text and enter=False does nothing."""
        manager, mock_pane = self._make_manager_with_pane()

        result = manager.send_keys(1, "", enter=False)

        assert result is True
        mock_pane.send_keys.assert_not_called()
        mock_sleep.assert_not_called()

    @patch("overcode.tmux_manager.time.sleep")
    def test_send_keys_bang_command_splits_exclamation(self, mock_sleep):
        """send_keys with ! prefix sends ! first then the rest (#139)."""
        manager, mock_pane = self._make_manager_with_pane()

        result = manager.send_keys(1, "!ls -la", enter=True)

        assert result is True
        # Three send_keys calls: !, rest, Enter
        assert mock_pane.send_keys.call_count == 3
        calls = mock_pane.send_keys.call_args_list
        assert calls[0] == call("!", enter=False)
        assert calls[1] == call("ls -la", enter=False)
        assert calls[2] == call("", enter=True)
        # Two sleeps: 0.15 after !, 0.1 after rest
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(0.15)
        mock_sleep.assert_any_call(0.1)

    @patch("overcode.tmux_manager.time.sleep")
    def test_send_keys_bang_only_not_split(self, mock_sleep):
        """send_keys with just '!' does NOT split (len == 1)."""
        manager, mock_pane = self._make_manager_with_pane()

        result = manager.send_keys(1, "!", enter=True)

        assert result is True
        # Normal path: send text, then Enter
        calls = mock_pane.send_keys.call_args_list
        assert calls[0] == call("!", enter=False)
        assert calls[1] == call("", enter=True)
        mock_sleep.assert_called_once_with(0.1)

    @patch("overcode.tmux_manager.time.sleep")
    def test_send_keys_bang_command_without_enter(self, mock_sleep):
        """send_keys with ! prefix and enter=False sends ! then rest, no Enter."""
        manager, mock_pane = self._make_manager_with_pane()

        result = manager.send_keys(1, "!pwd", enter=False)

        assert result is True
        assert mock_pane.send_keys.call_count == 2
        calls = mock_pane.send_keys.call_args_list
        assert calls[0] == call("!", enter=False)
        assert calls[1] == call("pwd", enter=False)

    def test_send_keys_returns_false_when_no_pane(self):
        """send_keys returns False when pane is not found."""
        manager = TmuxManager("agents")
        manager._server = MagicMock()
        manager._server.sessions.get.side_effect = ObjectDoesNotExist()

        result = manager.send_keys(99, "echo hello")

        assert result is False

    @patch("overcode.tmux_manager.time.sleep")
    def test_send_keys_returns_false_on_exception(self, mock_sleep):
        """send_keys returns False when pane.send_keys raises LibTmuxException."""
        manager, mock_pane = self._make_manager_with_pane()
        mock_pane.send_keys.side_effect = LibTmuxException()

        result = manager.send_keys(1, "echo hello")

        assert result is False

    @patch("overcode.tmux_manager.time.sleep")
    def test_send_keys_sleep_order_for_normal_text(self, mock_sleep):
        """Verify sleep happens between text send and Enter send."""
        manager, mock_pane = self._make_manager_with_pane()

        call_order = []
        mock_pane.send_keys.side_effect = lambda *a, **kw: call_order.append(("send", a, kw))
        mock_sleep.side_effect = lambda t: call_order.append(("sleep", t))

        manager.send_keys(1, "test", enter=True)

        assert call_order == [
            ("send", ("test",), {"enter": False}),
            ("sleep", 0.1),
            ("send", ("",), {"enter": True}),
        ]

    @patch("overcode.tmux_manager.time.sleep")
    def test_send_keys_sleep_order_for_bang_command(self, mock_sleep):
        """Verify sleep timing for ! commands: 0.15 after !, 0.1 after rest."""
        manager, mock_pane = self._make_manager_with_pane()

        call_order = []
        mock_pane.send_keys.side_effect = lambda *a, **kw: call_order.append(("send", a, kw))
        mock_sleep.side_effect = lambda t: call_order.append(("sleep", t))

        manager.send_keys(1, "!cmd", enter=True)

        assert call_order == [
            ("send", ("!",), {"enter": False}),
            ("sleep", 0.15),
            ("send", ("cmd",), {"enter": False}),
            ("sleep", 0.1),
            ("send", ("",), {"enter": True}),
        ]


class TestTmuxManagerLibtmuxKillSession:
    """Test kill_session via libtmux."""

    def test_kill_session_success(self):
        """kill_session calls sess.kill() and returns True."""
        manager = TmuxManager("agents")
        manager._server = MagicMock()
        mock_session = MagicMock()
        manager._server.sessions.get.return_value = mock_session

        result = manager.kill_session()

        assert result is True
        mock_session.kill.assert_called_once()

    def test_kill_session_returns_false_when_no_session(self):
        """kill_session returns False when session doesn't exist."""
        manager = TmuxManager("agents")
        manager._server = MagicMock()
        manager._server.sessions.get.side_effect = ObjectDoesNotExist()

        result = manager.kill_session()

        assert result is False

    def test_kill_session_returns_false_on_exception(self):
        """kill_session returns False when sess.kill() raises LibTmuxException."""
        manager = TmuxManager("agents")
        manager._server = MagicMock()
        mock_session = MagicMock()
        mock_session.kill.side_effect = LibTmuxException()
        manager._server.sessions.get.return_value = mock_session

        result = manager.kill_session()

        assert result is False


class TestTmuxManagerAttachSession:
    """Test attach_session (mock os.execlp)."""

    @patch("overcode.tmux_manager.os.execlp")
    def test_attach_session_default(self, mock_execlp):
        """attach_session calls os.execlp with session name."""
        manager = TmuxManager("agents")

        manager.attach_session()

        mock_execlp.assert_called_once_with(
            "tmux", "tmux", "attach-session", "-t", "agents"
        )

    @patch("overcode.tmux_manager.os.execlp")
    def test_attach_session_with_window(self, mock_execlp):
        """attach_session includes window in target when specified."""
        manager = TmuxManager("agents")

        manager.attach_session(window=3)

        mock_execlp.assert_called_once_with(
            "tmux", "tmux", "attach-session", "-t", "agents:=3"
        )

    @patch("overcode.tmux_manager.os.execlp")
    def test_attach_session_with_window_zero(self, mock_execlp):
        """attach_session handles window=0 correctly (not treated as falsy)."""
        manager = TmuxManager("agents")

        manager.attach_session(window=0)

        mock_execlp.assert_called_once_with(
            "tmux", "tmux", "attach-session", "-t", "agents:=0"
        )

    @patch("overcode.tmux_manager.TmuxManager._attach_bare")
    def test_attach_session_bare_delegates(self, mock_attach_bare):
        """attach_session with bare=True delegates to _attach_bare."""
        manager = TmuxManager("agents")

        manager.attach_session(window=2, bare=True)

        mock_attach_bare.assert_called_once_with(2)


class TestTmuxManagerAttachBare:
    """Test _attach_bare (mock subprocess.run and os.execlp)."""

    @patch("overcode.tmux_manager.os.execlp")
    @patch("subprocess.run")
    def test_attach_bare_success(self, mock_run, mock_execlp):
        """_attach_bare creates linked session with correct config and attaches."""
        manager = TmuxManager("agents")
        # All subprocess.run calls succeed
        mock_run.return_value = MagicMock(returncode=0)

        manager._attach_bare(2)

        # Verify the subprocess.run calls
        calls = mock_run.call_args_list

        # 1. Kill stale bare session
        assert calls[0] == call(
            ["tmux", "kill-session", "-t", "bare-agents-2"],
            capture_output=True,
        )

        # 2. Create linked session
        assert calls[1] == call(
            ["tmux", "new-session", "-d", "-s", "bare-agents-2", "-t", "agents"],
            capture_output=True,
        )

        # 3. Configure: status off
        assert calls[2] == call(
            ["tmux", "set", "-t", "bare-agents-2", "status", "off"],
            capture_output=True,
        )

        # 4. Configure: mouse off
        assert calls[3] == call(
            ["tmux", "set", "-t", "bare-agents-2", "mouse", "off"],
            capture_output=True,
        )

        # 5. Configure: destroy-unattached hook
        assert calls[4] == call(
            ["tmux", "set-hook", "-t", "bare-agents-2", "client-attached",
             "set destroy-unattached on"],
            capture_output=True,
        )

        # 6. Select window
        assert calls[5] == call(
            ["tmux", "select-window", "-t", "bare-agents-2:=2"],
            capture_output=True,
        )

        # 7. Final attach via execlp
        mock_execlp.assert_called_once_with(
            "tmux", "tmux", "attach-session", "-t", "bare-agents-2"
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

        manager._attach_bare(5)

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

        manager._attach_bare(7)

        # Check the bare session name in kill-session call
        first_call_cmd = mock_run.call_args_list[0][0][0]
        assert first_call_cmd == ["tmux", "kill-session", "-t", "bare-my-session-7"]

        # Check attach target
        mock_execlp.assert_called_once_with(
            "tmux", "tmux", "attach-session", "-t", "bare-my-session-7"
        )


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
