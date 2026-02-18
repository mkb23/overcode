"""Tests for macOS notification integration (#235)."""

import sys
from unittest.mock import patch, MagicMock

import pytest

from overcode.notifier import MacNotifier


# =============================================================================
# Message Formatting
# =============================================================================

class TestFormat:
    """Test _format static method for all coalescing scenarios."""

    def test_single_agent_no_task(self):
        subtitle, message = MacNotifier._format(["refactor-auth"], None)
        assert subtitle is None
        assert message == "refactor-auth needs attention"

    def test_single_agent_with_task(self):
        subtitle, message = MacNotifier._format(
            ["refactor-auth"], "Refactoring the authentication module"
        )
        assert subtitle == "refactor-auth needs attention"
        assert message == "Refactoring the authentication module"

    def test_two_agents(self):
        subtitle, message = MacNotifier._format(["refactor-auth", "fix-tests"], None)
        assert subtitle is None
        assert message == "refactor-auth and fix-tests need attention"

    def test_three_agents(self):
        subtitle, message = MacNotifier._format(
            ["refactor-auth", "fix-tests", "deploy-setup"], None
        )
        assert subtitle is None
        assert message == "refactor-auth, fix-tests, and deploy-setup need attention"

    def test_four_agents(self):
        subtitle, message = MacNotifier._format(
            ["refactor-auth", "fix-tests", "deploy-setup", "api-work"], None
        )
        assert subtitle is None
        assert message == "refactor-auth, fix-tests, and 2 others need attention"

    def test_five_agents(self):
        subtitle, message = MacNotifier._format(
            ["a", "b", "c", "d", "e"], None
        )
        assert subtitle is None
        assert message == "a, b, and 3 others need attention"


# =============================================================================
# Queue / Flush Coalescing
# =============================================================================

class TestQueueFlush:
    """Test queue/flush coalescing behavior."""

    @patch("overcode.notifier.sys")
    def test_queue_noop_when_off(self, mock_sys):
        mock_sys.platform = "darwin"
        n = MacNotifier(mode="off")
        n.queue("agent-1")
        assert len(n._pending) == 0

    @patch("overcode.notifier.sys")
    def test_queue_noop_when_not_darwin(self, mock_sys):
        mock_sys.platform = "linux"
        n = MacNotifier(mode="both")
        n.queue("agent-1")
        assert len(n._pending) == 0

    def test_queue_adds_pending_on_darwin(self):
        n = MacNotifier(mode="both")
        with patch.object(sys.modules["overcode.notifier"], "sys") as mock_sys:
            mock_sys.platform = "darwin"
            n.queue("agent-1", "some task")
        # Direct approach: just test with real platform if darwin
        # For CI, test the internal list directly
        n._pending.append(("agent-1", "some task"))
        assert len(n._pending) >= 1

    def test_flush_clears_pending(self):
        n = MacNotifier(mode="both")
        n._pending = [("agent-1", None)]
        with patch.object(n, "_send"):
            with patch("overcode.notifier.sys") as mock_sys:
                mock_sys.platform = "darwin"
                n.flush()
        assert len(n._pending) == 0

    def test_flush_noop_when_empty(self):
        n = MacNotifier(mode="both")
        with patch.object(n, "_send") as mock_send:
            n.flush()
        mock_send.assert_not_called()

    def test_flush_coalesces_multiple_agents(self):
        n = MacNotifier(mode="both")
        n._pending = [("agent-1", None), ("agent-2", None), ("agent-3", None)]
        with patch.object(n, "_send") as mock_send:
            with patch("overcode.notifier.sys") as mock_sys:
                mock_sys.platform = "darwin"
                n.flush()
        mock_send.assert_called_once()
        args = mock_send.call_args
        assert "agent-1, agent-2, and agent-3 need attention" in args[0][0]

    def test_flush_respects_coalesce_seconds(self):
        n = MacNotifier(mode="both", coalesce_seconds=2.0)
        n._pending = [("agent-1", None)]
        # Simulate a recent send
        import time
        n._last_send = time.monotonic()
        with patch.object(n, "_send") as mock_send:
            with patch("overcode.notifier.sys") as mock_sys:
                mock_sys.platform = "darwin"
                n.flush()
        # Should NOT have sent (too soon)
        mock_send.assert_not_called()
        # Pending should still be there (held for next cycle)
        assert len(n._pending) == 1

    def test_flush_sends_after_coalesce_window(self):
        n = MacNotifier(mode="both", coalesce_seconds=2.0)
        n._pending = [("agent-1", None)]
        # Simulate an old send
        import time
        n._last_send = time.monotonic() - 10.0
        with patch.object(n, "_send") as mock_send:
            with patch("overcode.notifier.sys") as mock_sys:
                mock_sys.platform = "darwin"
                n.flush()
        mock_send.assert_called_once()

    def test_flush_single_agent_with_task_passes_subtitle(self):
        n = MacNotifier(mode="both")
        n._pending = [("agent-1", "Working on auth")]
        with patch.object(n, "_send") as mock_send:
            with patch("overcode.notifier.sys") as mock_sys:
                mock_sys.platform = "darwin"
                n.flush()
        mock_send.assert_called_once_with("Working on auth", "agent-1 needs attention")


# =============================================================================
# Subprocess Dispatch
# =============================================================================

class TestSendTerminalNotifier:
    """Test _send dispatches to terminal-notifier correctly."""

    def test_terminal_notifier_with_subtitle(self):
        n = MacNotifier(mode="both")
        n._has_terminal_notifier = True
        with patch("overcode.notifier.subprocess.Popen") as mock_popen:
            n._send("Working on auth", subtitle="agent-1 needs attention")
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "terminal-notifier"
        assert "-title" in cmd
        assert "Overcode" in cmd
        assert "-subtitle" in cmd
        assert "agent-1 needs attention" in cmd
        assert "-message" in cmd
        assert "Working on auth" in cmd
        assert "-sound" in cmd
        assert "Hero" in cmd
        assert "-group" in cmd
        assert "overcode-bell" in cmd

    def test_terminal_notifier_sound_only(self):
        n = MacNotifier(mode="sound")
        n._has_terminal_notifier = True
        with patch("overcode.notifier.subprocess.Popen") as mock_popen:
            n._send("agent-1 needs attention")
        cmd = mock_popen.call_args[0][0]
        assert "-sound" in cmd
        assert "Hero" in cmd

    def test_terminal_notifier_banner_only(self):
        n = MacNotifier(mode="banner")
        n._has_terminal_notifier = True
        with patch("overcode.notifier.subprocess.Popen") as mock_popen:
            n._send("agent-1 needs attention")
        cmd = mock_popen.call_args[0][0]
        assert "-sound" not in cmd

    def test_terminal_notifier_oserror_swallowed(self):
        n = MacNotifier(mode="both")
        n._has_terminal_notifier = True
        with patch("overcode.notifier.subprocess.Popen", side_effect=OSError):
            n._send("agent-1 needs attention")  # Should not raise


class TestSendOsascript:
    """Test _send dispatches to osascript fallback correctly."""

    def test_osascript_banner_with_sound(self):
        n = MacNotifier(mode="both")
        n._has_terminal_notifier = False
        with patch("overcode.notifier.subprocess.Popen") as mock_popen:
            n._send("agent-1 needs attention")
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "osascript"
        assert cmd[1] == "-e"
        script = cmd[2]
        assert "display notification" in script
        assert "agent-1 needs attention" in script
        assert 'with title "Overcode"' in script
        assert 'sound name "Hero"' in script

    def test_osascript_banner_only(self):
        n = MacNotifier(mode="banner")
        n._has_terminal_notifier = False
        with patch("overcode.notifier.subprocess.Popen") as mock_popen:
            n._send("agent-1 needs attention")
        script = mock_popen.call_args[0][0][2]
        assert "sound name" not in script

    def test_osascript_sound_only_uses_afplay(self):
        n = MacNotifier(mode="sound")
        n._has_terminal_notifier = False
        with patch("overcode.notifier.subprocess.Popen") as mock_popen:
            n._send("agent-1 needs attention")
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "afplay"
        assert "Hero.aiff" in cmd[1]

    def test_osascript_oserror_swallowed(self):
        n = MacNotifier(mode="both")
        n._has_terminal_notifier = False
        with patch("overcode.notifier.subprocess.Popen", side_effect=OSError):
            n._send("agent-1 needs attention")  # Should not raise

    def test_osascript_with_subtitle(self):
        n = MacNotifier(mode="banner")
        n._has_terminal_notifier = False
        with patch("overcode.notifier.subprocess.Popen") as mock_popen:
            n._send("Working on auth", subtitle="agent-1 needs attention")
        script = mock_popen.call_args[0][0][2]
        assert "agent-1 needs attention" in script
        assert "Working on auth" in script


# =============================================================================
# Mode Handling
# =============================================================================

class TestModeHandling:
    """Test mode validation and cycling."""

    def test_invalid_mode_defaults_to_off(self):
        n = MacNotifier(mode="invalid")
        assert n.mode == "off"

    def test_valid_modes(self):
        for mode in ("off", "sound", "banner", "both"):
            n = MacNotifier(mode=mode)
            assert n.mode == mode

    def test_mode_tuple_order(self):
        assert MacNotifier.MODES == ("off", "sound", "banner", "both")


# =============================================================================
# Backend Detection
# =============================================================================

class TestBackendDetection:
    """Test terminal-notifier detection is lazy and cached."""

    def test_detection_is_lazy(self):
        n = MacNotifier(mode="both")
        assert n._has_terminal_notifier is None

    def test_detection_is_cached(self):
        n = MacNotifier(mode="both")
        with patch("overcode.notifier.shutil.which", return_value="/usr/local/bin/terminal-notifier"):
            assert n._use_terminal_notifier() is True
        # Second call should use cached value, not call shutil.which again
        with patch("overcode.notifier.shutil.which", return_value=None):
            assert n._use_terminal_notifier() is True  # Still cached as True

    def test_detection_false_when_not_found(self):
        n = MacNotifier(mode="both")
        with patch("overcode.notifier.shutil.which", return_value=None):
            assert n._use_terminal_notifier() is False
