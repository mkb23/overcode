"""
Tests for protocol interfaces and mock implementations.

These tests verify that:
1. Mock implementations correctly implement their protocols
2. Mocks behave as expected for testing scenarios
3. Protocol type checking works correctly
"""


from overcode.protocols import (
    TmuxInterface,
)
from overcode.mocks import (
    MockTmux,
)
from overcode.implementations import (
    RealTmux,
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
        mock.set_pane_content("test-session", "win1", "Hello\nWorld\nFoo")

        content = mock.capture_pane("test-session", "win1")
        assert content == "Hello\nWorld\nFoo"

    def test_capture_pane_respects_line_limit(self):
        """capture_pane should respect the lines parameter."""
        mock = MockTmux()
        mock.set_pane_content("test-session", "win1", "Line1\nLine2\nLine3\nLine4\nLine5")

        content = mock.capture_pane("test-session", "win1", lines=2)
        assert content == "Line4\nLine5"

    def test_capture_pane_nonexistent_session(self):
        """capture_pane should return None for nonexistent session."""
        mock = MockTmux()
        assert mock.capture_pane("nonexistent", "win1") is None

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
        """new_window should create a window and return its name."""
        mock = MockTmux()
        mock.new_session("test-session")

        window = mock.new_window("test-session", "my-window")
        assert window is not None
        assert window == "my-window"

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

        mock.send_keys("test-session", "win1", "hello", enter=True)
        mock.send_keys("test-session", "win1", "world", enter=False)

        assert len(mock.sent_keys) == 2
        assert mock.sent_keys[0] == ("test-session", "win1", "hello", True)
        assert mock.sent_keys[1] == ("test-session", "win1", "world", False)

    def test_attach_is_noop(self):
        """attach should be a no-op in mocks."""
        mock = MockTmux()
        mock.new_session("test-session")
        # Should not raise
        mock.attach("test-session")


class TestRealImplementationsProtocol:
    """Verify real implementations satisfy protocols."""

    def test_real_tmux_implements_protocol(self):
        """RealTmux should implement TmuxInterface."""
        real = RealTmux()
        assert isinstance(real, TmuxInterface)
