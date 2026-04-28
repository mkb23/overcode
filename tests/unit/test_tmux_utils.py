"""Tests for tmux_utils module."""

import os
import pytest
from unittest.mock import patch, MagicMock, call
import subprocess

from overcode.tmux_utils import (
    send_text_to_tmux_window,
    get_tmux_pane_content,
    exit_copy_mode_if_active,
)


class TestSendTextToTmuxWindow:
    """Tests for send_text_to_tmux_window."""

    def test_sends_single_line_text(self):
        with patch("overcode.tmux_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="0")
            result = send_text_to_tmux_window("agents", 1, "hello world")

        assert result is True
        # 4 calls: copy-mode probe, load-buffer, paste-buffer, send-keys (Enter)
        assert mock_run.call_count == 4

    def test_sends_without_enter(self):
        with patch("overcode.tmux_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="0")
            result = send_text_to_tmux_window("agents", 1, "hello", send_enter=False)

        assert result is True
        # 3 calls: copy-mode probe, load-buffer, paste-buffer (no send-keys)
        assert mock_run.call_count == 3

    def test_handles_multiline_text_batching(self):
        # Create text with more than 10 lines to trigger batching
        lines = [f"line {i}" for i in range(15)]
        text = "\n".join(lines)

        with patch("overcode.tmux_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="0")
            with patch("overcode.tmux_utils.time.sleep"):
                result = send_text_to_tmux_window("agents", 1, text)

        assert result is True
        # copy-mode probe + 2 batches * 2 calls (load-buffer + paste-buffer) + 1 send-keys = 6
        assert mock_run.call_count == 6

    def test_returns_false_on_load_buffer_failure(self):
        with patch("overcode.tmux_utils.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.SubprocessError("tmux not running")
            result = send_text_to_tmux_window("agents", 1, "hello")

        assert result is False

    def test_returns_false_on_send_keys_failure(self):
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                return MagicMock(returncode=0)
            raise subprocess.SubprocessError("send-keys failed")

        with patch("overcode.tmux_utils.subprocess.run", side_effect=side_effect):
            result = send_text_to_tmux_window("agents", 1, "hello")

        assert result is False

    def test_uses_custom_socket_from_env(self):
        with patch.dict(os.environ, {"OVERCODE_TMUX_SOCKET": "test-socket"}):
            with patch("overcode.tmux_utils.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                send_text_to_tmux_window("agents", 1, "hello")

            # First call should include -L test-socket
            first_call_args = mock_run.call_args_list[0][0][0]
            assert "-L" in first_call_args
            assert "test-socket" in first_call_args

    def test_no_socket_in_env(self):
        with patch.dict(os.environ, {}, clear=True):
            # Make sure OVERCODE_TMUX_SOCKET isn't set
            os.environ.pop("OVERCODE_TMUX_SOCKET", None)
            with patch("overcode.tmux_utils.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                send_text_to_tmux_window("agents", 1, "hello")

            first_call_args = mock_run.call_args_list[0][0][0]
            assert "-L" not in first_call_args

    def test_startup_delay(self):
        with patch("overcode.tmux_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch("overcode.tmux_utils.time.sleep") as mock_sleep:
                send_text_to_tmux_window("agents", 1, "hello", startup_delay=2.0)

            # First sleep call should be the startup delay
            mock_sleep.assert_any_call(2.0)

    def test_tempfile_cleanup_on_failure(self):
        """Temp files should be cleaned up even on failure."""
        with patch("overcode.tmux_utils.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.SubprocessError("fail")
            with patch("overcode.tmux_utils.os.unlink") as mock_unlink:
                send_text_to_tmux_window("agents", 1, "hello")
                # unlink should have been called to clean up tempfile
                assert mock_unlink.called


class TestExitCopyModeIfActive:
    """Tests for exit_copy_mode_if_active (#401)."""

    def test_sends_cancel_when_pane_in_copy_mode(self):
        with patch("overcode.tmux_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="1\n")
            exit_copy_mode_if_active("agents", "w1")

        # Two calls: display-message probe, then send-keys -X cancel
        assert mock_run.call_count == 2
        second_args = mock_run.call_args_list[1][0][0]
        assert "-X" in second_args and "cancel" in second_args

    def test_noop_when_not_in_copy_mode(self):
        with patch("overcode.tmux_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="0")
            exit_copy_mode_if_active("agents", "w1")

        # Only the display-message probe; no cancel
        assert mock_run.call_count == 1

    def test_swallows_errors(self):
        """Probe failures must never crash the caller — heartbeats still run."""
        with patch("overcode.tmux_utils.subprocess.run",
                   side_effect=subprocess.SubprocessError("tmux gone")):
            exit_copy_mode_if_active("agents", "w1")  # no exception


class TestGetTmuxPaneContent:
    """Tests for get_tmux_pane_content."""

    def test_returns_content_on_success(self):
        result = MagicMock()
        result.returncode = 0
        result.stdout = "line 1\nline 2\n"

        with patch("overcode.tmux_utils.subprocess.run", return_value=result):
            content = get_tmux_pane_content("agents", 1, lines=50)

        assert content == "line 1\nline 2"

    def test_returns_none_on_nonzero_exit(self):
        result = MagicMock()
        result.returncode = 1
        result.stdout = ""

        with patch("overcode.tmux_utils.subprocess.run", return_value=result):
            content = get_tmux_pane_content("agents", 1)

        assert content is None

    def test_returns_none_on_exception(self):
        with patch("overcode.tmux_utils.subprocess.run",
                   side_effect=subprocess.SubprocessError("tmux gone")):
            content = get_tmux_pane_content("agents", 1)

        assert content is None

    def test_uses_custom_socket(self):
        result = MagicMock()
        result.returncode = 0
        result.stdout = "content\n"

        with patch.dict(os.environ, {"OVERCODE_TMUX_SOCKET": "my-socket"}):
            with patch("overcode.tmux_utils.subprocess.run", return_value=result) as mock_run:
                get_tmux_pane_content("agents", 1)

            cmd = mock_run.call_args[0][0]
            assert "-L" in cmd
            assert "my-socket" in cmd

    def test_custom_line_count(self):
        result = MagicMock()
        result.returncode = 0
        result.stdout = "content\n"

        with patch("overcode.tmux_utils.subprocess.run", return_value=result) as mock_run:
            get_tmux_pane_content("agents", 1, lines=100)

        cmd = mock_run.call_args[0][0]
        assert "-100" in cmd
