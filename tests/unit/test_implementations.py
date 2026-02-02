"""Tests for implementations module."""

import pytest
import json
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

from overcode.implementations import (
    RealTmux,
    RealFileSystem,
)


class TestRealFileSystem:
    """Tests for RealFileSystem class."""

    def test_read_json_success(self, tmp_path):
        """Should read JSON file successfully."""
        fs = RealFileSystem()
        json_file = tmp_path / "test.json"
        json_file.write_text('{"key": "value"}')

        result = fs.read_json(json_file)

        assert result == {"key": "value"}

    def test_read_json_nonexistent(self, tmp_path):
        """Should return None for nonexistent file."""
        fs = RealFileSystem()
        json_file = tmp_path / "nonexistent.json"

        result = fs.read_json(json_file)

        assert result is None

    def test_read_json_invalid(self, tmp_path):
        """Should return None for invalid JSON."""
        fs = RealFileSystem()
        json_file = tmp_path / "invalid.json"
        json_file.write_text("not valid json")

        result = fs.read_json(json_file)

        assert result is None

    def test_read_json_io_error(self, tmp_path):
        """Should return None on IO error."""
        fs = RealFileSystem()
        # Create a directory instead of file
        dir_path = tmp_path / "is_a_dir.json"
        dir_path.mkdir()

        result = fs.read_json(dir_path)

        assert result is None

    def test_write_json_success(self, tmp_path):
        """Should write JSON file successfully."""
        fs = RealFileSystem()
        json_file = tmp_path / "test.json"
        data = {"key": "value", "number": 42}

        result = fs.write_json(json_file, data)

        assert result is True
        assert json_file.exists()
        assert json.loads(json_file.read_text()) == data

    def test_write_json_creates_atomic(self, tmp_path):
        """Should write atomically via temp file."""
        fs = RealFileSystem()
        json_file = tmp_path / "test.json"
        data = {"key": "value"}

        fs.write_json(json_file, data)

        # Temp file should be cleaned up
        temp_file = json_file.with_suffix('.tmp')
        assert not temp_file.exists()
        assert json_file.exists()

    def test_write_json_io_error(self, tmp_path):
        """Should return False on IO error."""
        fs = RealFileSystem()
        # Try to write to a directory
        dir_path = tmp_path / "is_a_dir"
        dir_path.mkdir()
        json_file = dir_path / "subdir" / "test.json"

        # This should fail because parent doesn't exist
        with patch('builtins.open', side_effect=IOError("Permission denied")):
            result = fs.write_json(json_file, {"key": "value"})

        assert result is False

    def test_exists_true(self, tmp_path):
        """Should return True for existing file."""
        fs = RealFileSystem()
        test_file = tmp_path / "exists.txt"
        test_file.write_text("content")

        result = fs.exists(test_file)

        assert result is True

    def test_exists_false(self, tmp_path):
        """Should return False for nonexistent file."""
        fs = RealFileSystem()
        test_file = tmp_path / "nonexistent.txt"

        result = fs.exists(test_file)

        assert result is False

    def test_mkdir_success(self, tmp_path):
        """Should create directory successfully."""
        fs = RealFileSystem()
        new_dir = tmp_path / "newdir"

        result = fs.mkdir(new_dir)

        assert result is True
        assert new_dir.exists()
        assert new_dir.is_dir()

    def test_mkdir_parents(self, tmp_path):
        """Should create parent directories."""
        fs = RealFileSystem()
        new_dir = tmp_path / "parent" / "child" / "grandchild"

        result = fs.mkdir(new_dir, parents=True)

        assert result is True
        assert new_dir.exists()

    def test_mkdir_exists(self, tmp_path):
        """Should handle existing directory."""
        fs = RealFileSystem()
        existing_dir = tmp_path / "existing"
        existing_dir.mkdir()

        result = fs.mkdir(existing_dir)

        assert result is True

    def test_mkdir_io_error(self, tmp_path):
        """Should return False on IO error."""
        fs = RealFileSystem()

        with patch.object(Path, 'mkdir', side_effect=IOError("Permission denied")):
            result = fs.mkdir(tmp_path / "fail")

        assert result is False

    def test_read_text_success(self, tmp_path):
        """Should read text file successfully."""
        fs = RealFileSystem()
        text_file = tmp_path / "test.txt"
        text_file.write_text("Hello, World!")

        result = fs.read_text(text_file)

        assert result == "Hello, World!"

    def test_read_text_nonexistent(self, tmp_path):
        """Should return None for nonexistent file."""
        fs = RealFileSystem()
        text_file = tmp_path / "nonexistent.txt"

        result = fs.read_text(text_file)

        assert result is None

    def test_read_text_io_error(self, tmp_path):
        """Should return None on IO error."""
        fs = RealFileSystem()
        # Create a directory instead of file
        dir_path = tmp_path / "is_a_dir.txt"
        dir_path.mkdir()

        result = fs.read_text(dir_path)

        assert result is None

    def test_write_text_success(self, tmp_path):
        """Should write text file successfully."""
        fs = RealFileSystem()
        text_file = tmp_path / "test.txt"

        result = fs.write_text(text_file, "Hello, World!")

        assert result is True
        assert text_file.read_text() == "Hello, World!"

    def test_write_text_io_error(self, tmp_path):
        """Should return False on IO error."""
        fs = RealFileSystem()

        with patch.object(Path, 'write_text', side_effect=IOError("Permission denied")):
            result = fs.write_text(tmp_path / "fail.txt", "content")

        assert result is False


class TestRealTmux:
    """Tests for RealTmux class."""

    def test_init_default(self):
        """Should initialize with no socket name by default."""
        with patch.dict('os.environ', {}, clear=True):
            tmux = RealTmux()
            assert tmux._socket_name is None

    def test_init_with_socket_name(self):
        """Should accept socket name parameter."""
        tmux = RealTmux(socket_name="test_socket")
        assert tmux._socket_name == "test_socket"

    def test_init_from_env_var(self):
        """Should read socket name from environment."""
        with patch.dict('os.environ', {'OVERCODE_TMUX_SOCKET': 'env_socket'}):
            tmux = RealTmux()
            assert tmux._socket_name == "env_socket"

    def test_server_lazy_load(self):
        """Should lazy-load server on first access."""
        with patch('overcode.implementations.libtmux.Server') as mock_server:
            tmux = RealTmux()
            assert tmux._server is None

            # Access server property
            _ = tmux.server

            mock_server.assert_called_once_with()
            assert tmux._server is not None

    def test_server_with_socket_name(self):
        """Should pass socket name to server."""
        with patch('overcode.implementations.libtmux.Server') as mock_server:
            tmux = RealTmux(socket_name="custom_socket")
            _ = tmux.server

            mock_server.assert_called_once_with(socket_name="custom_socket")

    def test_server_cached(self):
        """Should cache server instance."""
        with patch('overcode.implementations.libtmux.Server') as mock_server:
            tmux = RealTmux()
            _ = tmux.server
            _ = tmux.server

            # Should only create once
            mock_server.assert_called_once()

    def test_get_session_success(self):
        """Should get session by name."""
        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_session = MagicMock()
            mock_server = MagicMock()
            mock_server.sessions.get.return_value = mock_session
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            result = tmux._get_session("test_session")

            assert result == mock_session
            mock_server.sessions.get.assert_called_once_with(session_name="test_session")

    def test_get_session_not_found(self):
        """Should return None when session not found."""
        from libtmux._internal.query_list import ObjectDoesNotExist

        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_server = MagicMock()
            mock_server.sessions.get.side_effect = ObjectDoesNotExist()
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            result = tmux._get_session("nonexistent")

            assert result is None

    def test_get_window_success(self):
        """Should get window by session and index."""
        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_window = MagicMock()
            mock_session = MagicMock()
            mock_session.windows.get.return_value = mock_window
            mock_server = MagicMock()
            mock_server.sessions.get.return_value = mock_session
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            result = tmux._get_window("test_session", 1)

            assert result == mock_window

    def test_get_window_no_session(self):
        """Should return None when session doesn't exist."""
        from libtmux._internal.query_list import ObjectDoesNotExist

        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_server = MagicMock()
            mock_server.sessions.get.side_effect = ObjectDoesNotExist()
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            result = tmux._get_window("nonexistent", 1)

            assert result is None

    def test_get_pane_success(self):
        """Should get first pane of window."""
        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_pane = MagicMock()
            mock_window = MagicMock()
            mock_window.panes = [mock_pane]
            mock_session = MagicMock()
            mock_session.windows.get.return_value = mock_window
            mock_server = MagicMock()
            mock_server.sessions.get.return_value = mock_session
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            result = tmux._get_pane("test_session", 1)

            assert result == mock_pane

    def test_get_pane_no_panes(self):
        """Should return None when window has no panes."""
        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_window = MagicMock()
            mock_window.panes = []
            mock_session = MagicMock()
            mock_session.windows.get.return_value = mock_window
            mock_server = MagicMock()
            mock_server.sessions.get.return_value = mock_session
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            result = tmux._get_pane("test_session", 1)

            assert result is None

    def test_capture_pane_success(self):
        """Should capture pane content."""
        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_pane = MagicMock()
            mock_pane.capture_pane.return_value = ["line 1", "line 2"]
            mock_window = MagicMock()
            mock_window.panes = [mock_pane]
            mock_session = MagicMock()
            mock_session.windows.get.return_value = mock_window
            mock_server = MagicMock()
            mock_server.sessions.get.return_value = mock_session
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            result = tmux.capture_pane("test_session", 1, lines=50)

            assert result == "line 1\nline 2"
            mock_pane.capture_pane.assert_called_once_with(start=-50, escape_sequences=True)

    def test_capture_pane_string_result(self):
        """Should handle string result from capture_pane."""
        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_pane = MagicMock()
            mock_pane.capture_pane.return_value = "single string"
            mock_window = MagicMock()
            mock_window.panes = [mock_pane]
            mock_session = MagicMock()
            mock_session.windows.get.return_value = mock_window
            mock_server = MagicMock()
            mock_server.sessions.get.return_value = mock_session
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            result = tmux.capture_pane("test_session", 1)

            assert result == "single string"

    def test_capture_pane_no_pane(self):
        """Should return None when pane doesn't exist."""
        from libtmux._internal.query_list import ObjectDoesNotExist

        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_server = MagicMock()
            mock_server.sessions.get.side_effect = ObjectDoesNotExist()
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            result = tmux.capture_pane("nonexistent", 1)

            assert result is None

    def test_capture_pane_exception(self):
        """Should return None on LibTmuxException."""
        from libtmux.exc import LibTmuxException

        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_pane = MagicMock()
            mock_pane.capture_pane.side_effect = LibTmuxException("error")
            mock_window = MagicMock()
            mock_window.panes = [mock_pane]
            mock_session = MagicMock()
            mock_session.windows.get.return_value = mock_window
            mock_server = MagicMock()
            mock_server.sessions.get.return_value = mock_session
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            result = tmux.capture_pane("test_session", 1)

            assert result is None

    def test_send_keys_success(self):
        """Should send keys to pane."""
        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            with patch('overcode.implementations.time.sleep'):
                mock_pane = MagicMock()
                mock_window = MagicMock()
                mock_window.panes = [mock_pane]
                mock_session = MagicMock()
                mock_session.windows.get.return_value = mock_window
                mock_server = MagicMock()
                mock_server.sessions.get.return_value = mock_session
                mock_server_class.return_value = mock_server

                tmux = RealTmux()
                result = tmux.send_keys("test_session", 1, "echo hello", enter=True)

                assert result is True
                # Should send text without enter first
                mock_pane.send_keys.assert_any_call("echo hello", enter=False)
                # Then send enter separately
                mock_pane.send_keys.assert_any_call('', enter=True)

    def test_send_keys_no_enter(self):
        """Should send keys without enter."""
        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            with patch('overcode.implementations.time.sleep'):
                mock_pane = MagicMock()
                mock_window = MagicMock()
                mock_window.panes = [mock_pane]
                mock_session = MagicMock()
                mock_session.windows.get.return_value = mock_window
                mock_server = MagicMock()
                mock_server.sessions.get.return_value = mock_session
                mock_server_class.return_value = mock_server

                tmux = RealTmux()
                result = tmux.send_keys("test_session", 1, "text", enter=False)

                assert result is True
                mock_pane.send_keys.assert_called_once_with("text", enter=False)

    def test_send_keys_empty_text_with_enter(self):
        """Should send just enter when text is empty."""
        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_pane = MagicMock()
            mock_window = MagicMock()
            mock_window.panes = [mock_pane]
            mock_session = MagicMock()
            mock_session.windows.get.return_value = mock_window
            mock_server = MagicMock()
            mock_server.sessions.get.return_value = mock_session
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            result = tmux.send_keys("test_session", 1, "", enter=True)

            assert result is True
            mock_pane.send_keys.assert_called_once_with('', enter=True)

    def test_send_keys_no_pane(self):
        """Should return False when pane doesn't exist."""
        from libtmux._internal.query_list import ObjectDoesNotExist

        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_server = MagicMock()
            mock_server.sessions.get.side_effect = ObjectDoesNotExist()
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            result = tmux.send_keys("nonexistent", 1, "text")

            assert result is False

    def test_has_session_true(self):
        """Should return True when session exists."""
        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_server = MagicMock()
            mock_server.has_session.return_value = True
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            result = tmux.has_session("existing")

            assert result is True

    def test_has_session_false(self):
        """Should return False when session doesn't exist."""
        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_server = MagicMock()
            mock_server.has_session.return_value = False
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            result = tmux.has_session("nonexistent")

            assert result is False

    def test_has_session_exception(self):
        """Should return False on exception."""
        from libtmux.exc import LibTmuxException

        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_server = MagicMock()
            mock_server.has_session.side_effect = LibTmuxException("error")
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            result = tmux.has_session("any")

            assert result is False

    def test_new_session_success(self):
        """Should create new session."""
        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_server = MagicMock()
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            result = tmux.new_session("new_session")

            assert result is True
            mock_server.new_session.assert_called_once_with(
                session_name="new_session", attach=False
            )

    def test_new_session_exception(self):
        """Should return False on exception."""
        from libtmux.exc import LibTmuxException

        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_server = MagicMock()
            mock_server.new_session.side_effect = LibTmuxException("error")
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            result = tmux.new_session("new_session")

            assert result is False

    def test_new_window_success(self):
        """Should create new window."""
        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_window = MagicMock()
            mock_window.window_index = "2"
            mock_session = MagicMock()
            mock_session.new_window.return_value = mock_window
            mock_server = MagicMock()
            mock_server.sessions.get.return_value = mock_session
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            result = tmux.new_window("test_session", "window_name")

            assert result == 2

    def test_new_window_with_cwd(self):
        """Should create window with working directory."""
        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_window = MagicMock()
            mock_window.window_index = "1"
            mock_session = MagicMock()
            mock_session.new_window.return_value = mock_window
            mock_server = MagicMock()
            mock_server.sessions.get.return_value = mock_session
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            tmux.new_window("test_session", "window", cwd="/some/path")

            mock_session.new_window.assert_called_once()
            call_kwargs = mock_session.new_window.call_args[1]
            assert call_kwargs['start_directory'] == "/some/path"

    def test_new_window_with_command(self):
        """Should create window with command."""
        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_window = MagicMock()
            mock_window.window_index = "1"
            mock_session = MagicMock()
            mock_session.new_window.return_value = mock_window
            mock_server = MagicMock()
            mock_server.sessions.get.return_value = mock_session
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            tmux.new_window("test_session", "window", command=["ls", "-la"])

            mock_session.new_window.assert_called_once()
            call_kwargs = mock_session.new_window.call_args[1]
            assert call_kwargs['window_shell'] == "ls -la"

    def test_new_window_no_session(self):
        """Should return None when session doesn't exist."""
        from libtmux._internal.query_list import ObjectDoesNotExist

        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_server = MagicMock()
            mock_server.sessions.get.side_effect = ObjectDoesNotExist()
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            result = tmux.new_window("nonexistent", "window")

            assert result is None

    def test_kill_window_success(self):
        """Should kill window."""
        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_window = MagicMock()
            mock_session = MagicMock()
            mock_session.windows.get.return_value = mock_window
            mock_server = MagicMock()
            mock_server.sessions.get.return_value = mock_session
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            result = tmux.kill_window("test_session", 1)

            assert result is True
            mock_window.kill.assert_called_once()

    def test_kill_window_not_found(self):
        """Should return False when window doesn't exist."""
        from libtmux._internal.query_list import ObjectDoesNotExist

        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_session = MagicMock()
            mock_session.windows.get.side_effect = ObjectDoesNotExist()
            mock_server = MagicMock()
            mock_server.sessions.get.return_value = mock_session
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            result = tmux.kill_window("test_session", 99)

            assert result is False

    def test_kill_session_success(self):
        """Should kill session."""
        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_session = MagicMock()
            mock_server = MagicMock()
            mock_server.sessions.get.return_value = mock_session
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            result = tmux.kill_session("test_session")

            assert result is True
            mock_session.kill.assert_called_once()

    def test_kill_session_not_found(self):
        """Should return False when session doesn't exist."""
        from libtmux._internal.query_list import ObjectDoesNotExist

        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_server = MagicMock()
            mock_server.sessions.get.side_effect = ObjectDoesNotExist()
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            result = tmux.kill_session("nonexistent")

            assert result is False

    def test_list_windows_success(self):
        """Should list windows in session."""
        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_window1 = MagicMock()
            mock_window1.window_index = "0"
            mock_window1.window_name = "shell"
            mock_window1.window_active = "1"
            mock_window2 = MagicMock()
            mock_window2.window_index = "1"
            mock_window2.window_name = "agent"
            mock_window2.window_active = "0"

            mock_session = MagicMock()
            mock_session.windows = [mock_window1, mock_window2]
            mock_server = MagicMock()
            mock_server.sessions.get.return_value = mock_session
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            result = tmux.list_windows("test_session")

            assert len(result) == 2
            assert result[0] == {'index': 0, 'name': 'shell', 'active': True}
            assert result[1] == {'index': 1, 'name': 'agent', 'active': False}

    def test_list_windows_no_session(self):
        """Should return empty list when session doesn't exist."""
        from libtmux._internal.query_list import ObjectDoesNotExist

        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_server = MagicMock()
            mock_server.sessions.get.side_effect = ObjectDoesNotExist()
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            result = tmux.list_windows("nonexistent")

            assert result == []

    def test_select_window_success(self):
        """Should select window."""
        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_window = MagicMock()
            mock_session = MagicMock()
            mock_session.windows.get.return_value = mock_window
            mock_server = MagicMock()
            mock_server.sessions.get.return_value = mock_session
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            result = tmux.select_window("test_session", 1)

            assert result is True
            mock_window.select.assert_called_once()

    def test_select_window_not_found(self):
        """Should return False when window doesn't exist."""
        from libtmux._internal.query_list import ObjectDoesNotExist

        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_session = MagicMock()
            mock_session.windows.get.side_effect = ObjectDoesNotExist()
            mock_server = MagicMock()
            mock_server.sessions.get.return_value = mock_session
            mock_server_class.return_value = mock_server

            tmux = RealTmux()
            result = tmux.select_window("test_session", 99)

            assert result is False

    def test_pane_caching_reduces_lookups(self):
        """Should cache pane lookups to reduce tmux subprocess calls."""
        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_pane = MagicMock()
            mock_pane.capture_pane.return_value = ["line1", "line2"]
            mock_window = MagicMock()
            mock_window.panes = [mock_pane]
            mock_session = MagicMock()
            mock_session.windows.get.return_value = mock_window
            mock_server = MagicMock()
            mock_server.sessions.get.return_value = mock_session
            mock_server_class.return_value = mock_server

            tmux = RealTmux()

            # First call - should hit tmux
            result1 = tmux.capture_pane("test_session", 1)
            # Second call - should use cache
            result2 = tmux.capture_pane("test_session", 1)

            assert result1 == "line1\nline2"
            assert result2 == "line1\nline2"
            # Session lookup should only happen once (cached)
            assert mock_server.sessions.get.call_count == 1

    def test_session_caching_reduces_lookups(self):
        """Should cache session lookups."""
        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_pane = MagicMock()
            mock_pane.capture_pane.return_value = ["line"]
            mock_window = MagicMock()
            mock_window.panes = [mock_pane]
            mock_session = MagicMock()
            mock_session.windows.get.return_value = mock_window
            mock_server = MagicMock()
            mock_server.sessions.get.return_value = mock_session
            mock_server_class.return_value = mock_server

            tmux = RealTmux()

            # Multiple capture_pane calls for different windows in same session
            tmux.capture_pane("test_session", 1)
            tmux.capture_pane("test_session", 2)
            tmux.capture_pane("test_session", 3)

            # Session should only be looked up once
            assert mock_server.sessions.get.call_count == 1

    def test_invalidate_cache_clears_all(self):
        """Should clear all cached objects when invalidate_cache() called."""
        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_pane = MagicMock()
            mock_pane.capture_pane.return_value = ["line"]
            mock_window = MagicMock()
            mock_window.panes = [mock_pane]
            mock_session = MagicMock()
            mock_session.windows.get.return_value = mock_window
            mock_server = MagicMock()
            mock_server.sessions.get.return_value = mock_session
            mock_server_class.return_value = mock_server

            tmux = RealTmux()

            # First call - populates cache
            tmux.capture_pane("test_session", 1)
            assert mock_server.sessions.get.call_count == 1

            # Invalidate all caches
            tmux.invalidate_cache()

            # Next call should hit tmux again
            tmux.capture_pane("test_session", 1)
            assert mock_server.sessions.get.call_count == 2

    def test_invalidate_cache_specific_window(self):
        """Should clear only specific window's cache when specified."""
        with patch('overcode.implementations.libtmux.Server') as mock_server_class:
            mock_pane = MagicMock()
            mock_pane.capture_pane.return_value = ["line"]
            mock_window = MagicMock()
            mock_window.panes = [mock_pane]
            mock_session = MagicMock()
            mock_session.windows.get.return_value = mock_window
            mock_server = MagicMock()
            mock_server.sessions.get.return_value = mock_session
            mock_server_class.return_value = mock_server

            tmux = RealTmux()

            # Populate cache for two windows
            tmux.capture_pane("test_session", 1)
            tmux.capture_pane("test_session", 2)

            # Invalidate only window 1
            tmux.invalidate_cache("test_session", 1)

            # Window 2 should still be cached, window 1 should refetch
            initial_window_calls = mock_session.windows.get.call_count
            tmux.capture_pane("test_session", 2)  # Should use cache
            tmux.capture_pane("test_session", 1)  # Should refetch

            # Only window 1 should have caused a new lookup
            assert mock_session.windows.get.call_count == initial_window_calls + 1
