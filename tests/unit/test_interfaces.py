"""
Tests for protocol interfaces and mock implementations.

These tests verify that:
1. Mock implementations correctly implement their protocols
2. Mocks behave as expected for testing scenarios
3. Protocol type checking works correctly
"""

import pytest
from pathlib import Path

from overcode.protocols import (
    TmuxInterface,
    FileSystemInterface,
    SubprocessInterface,
)
from overcode.mocks import (
    MockTmux,
    MockFileSystem,
    MockSubprocess,
)
from overcode.implementations import (
    RealTmux,
    RealFileSystem,
    RealSubprocess,
)


class TestMockTmux:
    """Test MockTmux implementation."""

    def test_implements_protocol(self):
        """MockTmux should implement TmuxInterface."""
        mock = MockTmux()
        assert isinstance(mock, TmuxInterface)

    def test_set_and_capture_pane_content(self):
        """Should be able to set and retrieve pane content."""
        mock = MockTmux()
        mock.set_pane_content("test-session", 1, "Hello\nWorld\nFoo")

        content = mock.capture_pane("test-session", 1)
        assert content == "Hello\nWorld\nFoo"

    def test_capture_pane_respects_line_limit(self):
        """capture_pane should respect the lines parameter."""
        mock = MockTmux()
        mock.set_pane_content("test-session", 1, "Line1\nLine2\nLine3\nLine4\nLine5")

        content = mock.capture_pane("test-session", 1, lines=2)
        assert content == "Line4\nLine5"

    def test_capture_pane_nonexistent_session(self):
        """capture_pane should return None for nonexistent session."""
        mock = MockTmux()
        assert mock.capture_pane("nonexistent", 1) is None

    def test_has_session(self):
        """has_session should return True only for existing sessions."""
        mock = MockTmux()
        assert not mock.has_session("test-session")

        mock.new_session("test-session")
        assert mock.has_session("test-session")

    def test_new_session(self):
        """new_session should create a new session."""
        mock = MockTmux()

        result = mock.new_session("new-session")
        assert result is True
        assert mock.has_session("new-session")

    def test_new_session_duplicate(self):
        """new_session should fail for existing session."""
        mock = MockTmux()
        mock.new_session("existing")

        result = mock.new_session("existing")
        assert result is False

    def test_new_window(self):
        """new_window should create a window and return its index."""
        mock = MockTmux()
        mock.new_session("test-session")

        window = mock.new_window("test-session", "my-window")
        assert window is not None
        assert window >= 1

    def test_new_window_nonexistent_session(self):
        """new_window should fail for nonexistent session."""
        mock = MockTmux()

        window = mock.new_window("nonexistent", "my-window")
        assert window is None

    def test_kill_window(self):
        """kill_window should remove the window."""
        mock = MockTmux()
        mock.new_session("test-session")
        window = mock.new_window("test-session", "my-window")

        result = mock.kill_window("test-session", window)
        assert result is True

        # Should fail to kill again
        result = mock.kill_window("test-session", window)
        assert result is False

    def test_kill_session(self):
        """kill_session should remove the session."""
        mock = MockTmux()
        mock.new_session("test-session")

        result = mock.kill_session("test-session")
        assert result is True
        assert not mock.has_session("test-session")

    def test_list_windows(self):
        """list_windows should return all windows in session."""
        mock = MockTmux()
        mock.new_session("test-session")
        mock.new_window("test-session", "window1")
        mock.new_window("test-session", "window2")

        windows = mock.list_windows("test-session")
        assert len(windows) == 2
        assert all("index" in w for w in windows)
        assert all("name" in w for w in windows)

    def test_list_windows_empty_session(self):
        """list_windows should return empty list for nonexistent session."""
        mock = MockTmux()
        windows = mock.list_windows("nonexistent")
        assert windows == []

    def test_send_keys_recorded(self):
        """send_keys should record sent keys."""
        mock = MockTmux()
        mock.new_session("test-session")

        mock.send_keys("test-session", 1, "hello", enter=True)
        mock.send_keys("test-session", 1, "world", enter=False)

        assert len(mock.sent_keys) == 2
        assert mock.sent_keys[0] == ("test-session", 1, "hello", True)
        assert mock.sent_keys[1] == ("test-session", 1, "world", False)

    def test_attach_is_noop(self):
        """attach should be a no-op in mocks."""
        mock = MockTmux()
        mock.new_session("test-session")
        # Should not raise
        mock.attach("test-session")


class TestMockFileSystem:
    """Test MockFileSystem implementation."""

    def test_implements_protocol(self):
        """MockFileSystem should implement FileSystemInterface."""
        mock = MockFileSystem()
        assert isinstance(mock, FileSystemInterface)

    def test_write_and_read_json(self):
        """Should be able to write and read JSON."""
        mock = MockFileSystem()
        path = Path("/test/data.json")

        result = mock.write_json(path, {"key": "value"})
        assert result is True

        data = mock.read_json(path)
        assert data == {"key": "value"}

    def test_read_json_nonexistent(self):
        """read_json should return None for nonexistent file."""
        mock = MockFileSystem()
        assert mock.read_json(Path("/nonexistent.json")) is None

    def test_write_and_read_text(self):
        """Should be able to write and read text."""
        mock = MockFileSystem()
        path = Path("/test/file.txt")

        result = mock.write_text(path, "Hello World")
        assert result is True

        content = mock.read_text(path)
        assert content == "Hello World"

    def test_read_text_nonexistent(self):
        """read_text should return None for nonexistent file."""
        mock = MockFileSystem()
        assert mock.read_text(Path("/nonexistent.txt")) is None

    def test_exists(self):
        """exists should return True for files and directories."""
        mock = MockFileSystem()

        assert not mock.exists(Path("/test"))
        assert not mock.exists(Path("/test/file.txt"))

        mock.mkdir(Path("/test"))
        mock.write_text(Path("/test/file.txt"), "content")

        assert mock.exists(Path("/test"))
        assert mock.exists(Path("/test/file.txt"))

    def test_mkdir(self):
        """mkdir should create directories."""
        mock = MockFileSystem()
        path = Path("/new/nested/dir")

        result = mock.mkdir(path, parents=True)
        assert result is True
        assert mock.exists(path)


class TestMockSubprocess:
    """Test MockSubprocess implementation."""

    def test_implements_protocol(self):
        """MockSubprocess should implement SubprocessInterface."""
        mock = MockSubprocess()
        assert isinstance(mock, SubprocessInterface)

    def test_run_default_response(self):
        """run should return default success response."""
        mock = MockSubprocess()

        result = mock.run(["echo", "hello"])
        assert result is not None
        assert result["returncode"] == 0
        assert result["stdout"] == ""
        assert result["stderr"] == ""

    def test_run_custom_response(self):
        """run should return custom response when set."""
        mock = MockSubprocess()
        mock.set_response("git", returncode=0, stdout="main", stderr="")

        result = mock.run(["git", "branch"])
        assert result is not None
        assert result["returncode"] == 0
        assert result["stdout"] == "main"

    def test_run_records_commands(self):
        """run should record all executed commands."""
        mock = MockSubprocess()

        mock.run(["cmd1", "arg1"])
        mock.run(["cmd2", "arg2", "arg3"])

        assert len(mock.commands) == 2
        assert mock.commands[0] == ["cmd1", "arg1"]
        assert mock.commands[1] == ["cmd2", "arg2", "arg3"]

    def test_popen_records_commands(self):
        """popen should record executed commands."""
        mock = MockSubprocess()

        mock.popen(["background", "process"])

        assert len(mock.commands) == 1
        assert mock.commands[0] == ["background", "process"]


class TestRealImplementationsProtocol:
    """Verify real implementations satisfy protocols."""

    def test_real_tmux_implements_protocol(self):
        """RealTmux should implement TmuxInterface."""
        real = RealTmux()
        assert isinstance(real, TmuxInterface)

    def test_real_filesystem_implements_protocol(self):
        """RealFileSystem should implement FileSystemInterface."""
        real = RealFileSystem()
        assert isinstance(real, FileSystemInterface)

    def test_real_subprocess_implements_protocol(self):
        """RealSubprocess should implement SubprocessInterface."""
        real = RealSubprocess()
        assert isinstance(real, SubprocessInterface)
