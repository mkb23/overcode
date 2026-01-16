"""
E2E tests to reproduce and verify the Enter key sending issue.

The issue: Sometimes the TUI "send" command sends a carriage return
instead of an actual ENTER that would trigger the agent to continue.
"""

import pytest
import subprocess
import time
from unittest.mock import patch


class TestSendEnterIssue:
    """Tests to reproduce the intermittent Enter key issue."""

    def test_send_keys_atomic_command(self, clean_test_env):
        """Verify tmux send-keys with Enter in single command works."""
        tmux_socket = clean_test_env["tmux_socket"]
        tmux_session = clean_test_env["session_name"]

        # Ensure session exists first
        subprocess.run(
            ["tmux", "-L", tmux_socket, "new-session", "-d", "-s", tmux_session],
            capture_output=True
        )
        time.sleep(0.2)

        # Create a test window with a shell
        subprocess.run(
            ["tmux", "-L", tmux_socket, "new-window", "-t", tmux_session, "-n", "test-send"],
            capture_output=True
        )
        time.sleep(0.3)

        # Send "echo hello" with Enter in ONE command (atomic)
        result = subprocess.run(
            ["tmux", "-L", tmux_socket, "send-keys", "-t", f"{tmux_session}:test-send",
             "echo atomic-test", "Enter"],
            capture_output=True
        )
        assert result.returncode == 0

        time.sleep(0.5)

        # Capture pane content
        output = subprocess.run(
            ["tmux", "-L", tmux_socket, "capture-pane", "-t", f"{tmux_session}:test-send",
             "-p", "-S", "-20"],
            capture_output=True, text=True
        )

        # The command should have executed (we should see the output)
        assert "atomic-test" in output.stdout

    def test_send_keys_split_commands_race_condition(self, clean_test_env):
        """Test if split send-keys commands can cause race condition.

        This mimics the RealTmux implementation which sends keys and Enter
        in separate subprocess calls with a 0.1s delay.
        """
        tmux_socket = clean_test_env["tmux_socket"]
        tmux_session = clean_test_env["session_name"]

        # Ensure session exists
        subprocess.run(
            ["tmux", "-L", tmux_socket, "new-session", "-d", "-s", tmux_session],
            capture_output=True
        )
        time.sleep(0.2)

        # Create a test window
        subprocess.run(
            ["tmux", "-L", tmux_socket, "new-window", "-t", tmux_session, "-n", "test-split"],
            capture_output=True
        )
        time.sleep(0.3)

        # Send text in FIRST command
        result1 = subprocess.run(
            ["tmux", "-L", tmux_socket, "send-keys", "-t", f"{tmux_session}:test-split",
             "echo split-test"],
            capture_output=True
        )
        assert result1.returncode == 0

        # Small delay (mimics RealTmux's time.sleep(0.1))
        time.sleep(0.1)

        # Send Enter in SECOND command
        result2 = subprocess.run(
            ["tmux", "-L", tmux_socket, "send-keys", "-t", f"{tmux_session}:test-split",
             "Enter"],
            capture_output=True
        )
        assert result2.returncode == 0

        time.sleep(0.5)

        # Capture and verify
        output = subprocess.run(
            ["tmux", "-L", tmux_socket, "capture-pane", "-t", f"{tmux_session}:test-split",
             "-p", "-S", "-20"],
            capture_output=True, text=True
        )

        # This should work most of the time, but may fail under load
        assert "split-test" in output.stdout

    def test_tmux_manager_send_keys_production_path(self, clean_test_env):
        """Test the actual TmuxManager production code path."""
        tmux_socket = clean_test_env["tmux_socket"]
        tmux_session = clean_test_env["session_name"]

        from overcode.tmux_manager import TmuxManager

        # Create TmuxManager with our test socket
        mgr = TmuxManager(session_name=tmux_session, socket=tmux_socket)

        # Ensure session exists
        mgr.ensure_session()
        time.sleep(0.2)

        # Create a test window
        window_idx = mgr.create_window("test-mgr")
        assert window_idx is not None

        time.sleep(0.3)

        # Use the production send_keys method
        success = mgr.send_keys(window_idx, "echo manager-test", enter=True)
        assert success

        time.sleep(0.5)

        # Capture and verify
        output = subprocess.run(
            ["tmux", "-L", tmux_socket, "capture-pane", "-t", f"{tmux_session}:{window_idx}",
             "-p", "-S", "-20"],
            capture_output=True, text=True
        )

        assert "manager-test" in output.stdout

    def test_send_empty_string_with_enter(self, clean_test_env):
        """Test sending empty string with Enter (just pressing Enter)."""
        tmux_socket = clean_test_env["tmux_socket"]
        tmux_session = clean_test_env["session_name"]

        # Ensure session exists
        subprocess.run(
            ["tmux", "-L", tmux_socket, "new-session", "-d", "-s", tmux_session],
            capture_output=True
        )
        time.sleep(0.2)

        subprocess.run(
            ["tmux", "-L", tmux_socket, "new-window", "-t", tmux_session, "-n", "test-empty"],
            capture_output=True
        )
        time.sleep(0.3)

        # First send a partial command
        subprocess.run(
            ["tmux", "-L", tmux_socket, "send-keys", "-t", f"{tmux_session}:test-empty",
             "echo empty-test"],
            capture_output=True
        )
        time.sleep(0.1)

        # Then send just Enter (empty string + Enter)
        result = subprocess.run(
            ["tmux", "-L", tmux_socket, "send-keys", "-t", f"{tmux_session}:test-empty",
             "", "Enter"],
            capture_output=True
        )
        assert result.returncode == 0

        time.sleep(0.5)

        output = subprocess.run(
            ["tmux", "-L", tmux_socket, "capture-pane", "-t", f"{tmux_session}:test-empty",
             "-p", "-S", "-20"],
            capture_output=True, text=True
        )

        assert "empty-test" in output.stdout

    def test_rapid_multiple_sends(self, clean_test_env):
        """Test rapid multiple sends - could expose timing issues."""
        tmux_socket = clean_test_env["tmux_socket"]
        tmux_session = clean_test_env["session_name"]

        # Ensure session exists
        subprocess.run(
            ["tmux", "-L", tmux_socket, "new-session", "-d", "-s", tmux_session],
            capture_output=True
        )
        time.sleep(0.2)

        subprocess.run(
            ["tmux", "-L", tmux_socket, "new-window", "-t", tmux_session, "-n", "test-rapid"],
            capture_output=True
        )
        time.sleep(0.3)

        # Send multiple commands rapidly
        for i in range(5):
            subprocess.run(
                ["tmux", "-L", tmux_socket, "send-keys", "-t", f"{tmux_session}:test-rapid",
                 f"echo rapid-{i}", "Enter"],
                capture_output=True
            )
            # Minimal delay between sends
            time.sleep(0.05)

        time.sleep(1.0)  # Wait for all to execute

        output = subprocess.run(
            ["tmux", "-L", tmux_socket, "capture-pane", "-t", f"{tmux_session}:test-rapid",
             "-p", "-S", "-30"],
            capture_output=True, text=True
        )

        # All 5 should have executed
        for i in range(5):
            assert f"rapid-{i}" in output.stdout

    def test_carriage_return_vs_enter(self, clean_test_env):
        """Test if sending literal \\r differs from Enter key."""
        tmux_socket = clean_test_env["tmux_socket"]
        tmux_session = clean_test_env["session_name"]

        # Ensure session exists
        subprocess.run(
            ["tmux", "-L", tmux_socket, "new-session", "-d", "-s", tmux_session],
            capture_output=True
        )
        time.sleep(0.2)

        subprocess.run(
            ["tmux", "-L", tmux_socket, "new-window", "-t", tmux_session, "-n", "test-cr"],
            capture_output=True
        )
        time.sleep(0.3)

        # Test with Enter key
        subprocess.run(
            ["tmux", "-L", tmux_socket, "send-keys", "-t", f"{tmux_session}:test-cr",
             "echo enter-key", "Enter"],
            capture_output=True
        )
        time.sleep(0.3)

        # Test with literal \r (C-m is carriage return in tmux)
        subprocess.run(
            ["tmux", "-L", tmux_socket, "send-keys", "-t", f"{tmux_session}:test-cr",
             "echo carriage-return", "C-m"],
            capture_output=True
        )
        time.sleep(0.3)

        output = subprocess.run(
            ["tmux", "-L", tmux_socket, "capture-pane", "-t", f"{tmux_session}:test-cr",
             "-p", "-S", "-20"],
            capture_output=True, text=True
        )

        # Both should work - Enter and C-m (carriage return) should be equivalent
        assert "enter-key" in output.stdout
        assert "carriage-return" in output.stdout


class TestEdgeCases:
    """Tests for edge cases that might cause Enter issues."""

    def test_text_with_trailing_newline(self, clean_test_env):
        """Test if text with trailing newline causes double-Enter."""
        tmux_socket = clean_test_env["tmux_socket"]
        tmux_session = clean_test_env["session_name"]

        subprocess.run(
            ["tmux", "-L", tmux_socket, "new-session", "-d", "-s", tmux_session],
            capture_output=True
        )
        time.sleep(0.2)

        subprocess.run(
            ["tmux", "-L", tmux_socket, "new-window", "-t", tmux_session, "-n", "test-newline"],
            capture_output=True
        )
        time.sleep(0.3)

        # Send text WITH trailing newline + Enter
        # This mimics what might happen if text isn't stripped properly
        text_with_newline = "echo newline-test\n"
        subprocess.run(
            ["tmux", "-L", tmux_socket, "send-keys", "-t", f"{tmux_session}:test-newline",
             text_with_newline, "Enter"],
            capture_output=True
        )
        time.sleep(0.5)

        output = subprocess.run(
            ["tmux", "-L", tmux_socket, "capture-pane", "-t", f"{tmux_session}:test-newline",
             "-p", "-S", "-20"],
            capture_output=True, text=True
        )

        # Should still work (tmux handles this)
        assert "newline-test" in output.stdout

    def test_text_with_carriage_return(self, clean_test_env):
        """Test if text with \\r causes issues."""
        tmux_socket = clean_test_env["tmux_socket"]
        tmux_session = clean_test_env["session_name"]

        subprocess.run(
            ["tmux", "-L", tmux_socket, "new-session", "-d", "-s", tmux_session],
            capture_output=True
        )
        time.sleep(0.2)

        subprocess.run(
            ["tmux", "-L", tmux_socket, "new-window", "-t", tmux_session, "-n", "test-cr2"],
            capture_output=True
        )
        time.sleep(0.3)

        # Send text WITH carriage return embedded
        text_with_cr = "echo cr-test\r"
        subprocess.run(
            ["tmux", "-L", tmux_socket, "send-keys", "-t", f"{tmux_session}:test-cr2",
             text_with_cr, "Enter"],
            capture_output=True
        )
        time.sleep(0.5)

        output = subprocess.run(
            ["tmux", "-L", tmux_socket, "capture-pane", "-t", f"{tmux_session}:test-cr2",
             "-p", "-S", "-20"],
            capture_output=True, text=True
        )

        # The carriage return might cause weirdness
        print(f"Output with \\r in text: {repr(output.stdout)}")
        # At minimum we should see the echo command was entered
        assert "cr-test" in output.stdout or "echo" in output.stdout

    def test_send_literal_mode(self, clean_test_env):
        """Test tmux send-keys with -l flag for literal mode."""
        tmux_socket = clean_test_env["tmux_socket"]
        tmux_session = clean_test_env["session_name"]

        subprocess.run(
            ["tmux", "-L", tmux_socket, "new-session", "-d", "-s", tmux_session],
            capture_output=True
        )
        time.sleep(0.2)

        subprocess.run(
            ["tmux", "-L", tmux_socket, "new-window", "-t", tmux_session, "-n", "test-literal"],
            capture_output=True
        )
        time.sleep(0.3)

        # Use -l flag for literal text (doesn't interpret special keys)
        # Then send Enter separately
        subprocess.run(
            ["tmux", "-L", tmux_socket, "send-keys", "-l", "-t", f"{tmux_session}:test-literal",
             "echo literal-test"],
            capture_output=True
        )
        subprocess.run(
            ["tmux", "-L", tmux_socket, "send-keys", "-t", f"{tmux_session}:test-literal",
             "Enter"],
            capture_output=True
        )
        time.sleep(0.5)

        output = subprocess.run(
            ["tmux", "-L", tmux_socket, "capture-pane", "-t", f"{tmux_session}:test-literal",
             "-p", "-S", "-20"],
            capture_output=True, text=True
        )

        assert "literal-test" in output.stdout


class TestClaudeLauncherSendPath:
    """Test the full ClaudeLauncher send path."""

    def test_launcher_send_to_session(self, clean_test_env):
        """Test ClaudeLauncher.send_to_session() full path."""
        tmux_socket = clean_test_env["tmux_socket"]
        tmux_session = clean_test_env["session_name"]
        state_dir = clean_test_env["state_dir"]

        from overcode.launcher import ClaudeLauncher
        from overcode.tmux_manager import TmuxManager
        from overcode.session_manager import SessionManager

        # Set up managers with test socket
        tmux_mgr = TmuxManager(session_name=tmux_session, socket=tmux_socket)
        session_mgr = SessionManager(state_dir=state_dir)

        # Ensure session exists
        tmux_mgr.ensure_session()
        time.sleep(0.2)

        # Create a test window and register session
        window_idx = tmux_mgr.create_window("test-launcher")
        assert window_idx is not None

        # Register the session
        session = session_mgr.create_session(
            name="test-launcher",
            tmux_session=tmux_session,
            tmux_window=window_idx,
            command=["bash"],
            start_directory="/tmp"
        )

        time.sleep(0.3)

        # Create launcher and send
        launcher = ClaudeLauncher(
            tmux_session=tmux_session,
            tmux_manager=tmux_mgr,
            session_manager=session_mgr
        )

        success = launcher.send_to_session("test-launcher", "echo launcher-test")
        assert success

        time.sleep(0.5)

        output = subprocess.run(
            ["tmux", "-L", tmux_socket, "capture-pane", "-t", f"{tmux_session}:{window_idx}",
             "-p", "-S", "-20"],
            capture_output=True, text=True
        )

        assert "launcher-test" in output.stdout
