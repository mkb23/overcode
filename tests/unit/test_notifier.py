"""Tests for macOS notification integration (#235)."""

import sys
import time
from unittest.mock import patch, MagicMock

import pytest

from overcode.notifier import MacNotifier
from overcode.status_constants import (
    STATUS_RUNNING,
    STATUS_WAITING_USER,
    STATUS_WAITING_HEARTBEAT,
    STATUS_ERROR,
    STATUS_WAITING_APPROVAL,
    is_green_status,
)


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

    def test_six_agents(self):
        """Six agents should show 4 others."""
        subtitle, message = MacNotifier._format(
            ["a", "b", "c", "d", "e", "f"], None
        )
        assert subtitle is None
        assert message == "a, b, and 4 others need attention"

    def test_two_agents_with_task_ignores_task(self):
        """Task parameter is ignored when there are multiple agents."""
        subtitle, message = MacNotifier._format(
            ["agent-1", "agent-2"], "some task"
        )
        # For 2 agents, task is not used (only used for single-agent case)
        assert subtitle is None
        assert "need attention" in message


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
            assert len(n._pending) == 1
            assert n._pending[0] == ("agent-1", "some task")

    def test_queue_multiple_agents(self):
        """Multiple queue calls accumulate pending items."""
        n = MacNotifier(mode="both")
        with patch.object(sys.modules["overcode.notifier"], "sys") as mock_sys:
            mock_sys.platform = "darwin"
            n.queue("agent-1", "task 1")
            n.queue("agent-2", "task 2")
            n.queue("agent-3")
            assert len(n._pending) == 3
            assert n._pending[2] == ("agent-3", None)

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

    def test_flush_noop_when_off(self):
        """Flush with mode=off should clear pending and not send."""
        n = MacNotifier(mode="off")
        n._pending = [("agent-1", None)]
        with patch.object(n, "_send") as mock_send:
            n.flush()
        mock_send.assert_not_called()
        assert len(n._pending) == 0

    def test_flush_noop_when_not_darwin(self):
        """Flush on non-darwin should clear pending and not send."""
        n = MacNotifier(mode="both")
        n._pending = [("agent-1", None)]
        with patch.object(n, "_send") as mock_send:
            with patch("overcode.notifier.sys") as mock_sys:
                mock_sys.platform = "linux"
                n.flush()
        mock_send.assert_not_called()
        assert len(n._pending) == 0

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

    def test_flush_single_agent_no_task(self):
        """Single agent without task: message is name, no subtitle."""
        n = MacNotifier(mode="both")
        n._pending = [("agent-1", None)]
        with patch.object(n, "_send") as mock_send:
            with patch("overcode.notifier.sys") as mock_sys:
                mock_sys.platform = "darwin"
                n.flush()
        mock_send.assert_called_once_with("agent-1 needs attention", None)

    def test_flush_multiple_agents_ignores_task(self):
        """With multiple agents, task is set to None regardless of individual tasks."""
        n = MacNotifier(mode="both")
        n._pending = [("agent-1", "task A"), ("agent-2", "task B")]
        with patch.object(n, "_send") as mock_send:
            with patch("overcode.notifier.sys") as mock_sys:
                mock_sys.platform = "darwin"
                n.flush()
        # For multi-agent, subtitle should be None
        args, kwargs = mock_send.call_args
        assert args[1] is None  # subtitle

    def test_flush_updates_last_send_time(self):
        """After a successful send, _last_send should be updated."""
        import time
        n = MacNotifier(mode="both")
        n._pending = [("agent-1", None)]
        n._last_send = 0.0  # long ago

        with patch.object(n, "_send"):
            with patch("overcode.notifier.sys") as mock_sys:
                mock_sys.platform = "darwin"
                before = time.monotonic()
                n.flush()
                after = time.monotonic()

        assert n._last_send >= before
        assert n._last_send <= after


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

    def test_terminal_notifier_sound_only_no_banner_still_calls(self):
        """With mode=sound, terminal-notifier is still called for the sound."""
        n = MacNotifier(mode="sound")
        n._has_terminal_notifier = True
        with patch("overcode.notifier.subprocess.Popen") as mock_popen:
            n._send("agent-1 needs attention")
        mock_popen.assert_called_once()

    def test_terminal_notifier_without_subtitle(self):
        """When no subtitle is provided, -subtitle should not appear in cmd."""
        n = MacNotifier(mode="both")
        n._has_terminal_notifier = True
        with patch("overcode.notifier.subprocess.Popen") as mock_popen:
            n._send("agent-1 needs attention")
        cmd = mock_popen.call_args[0][0]
        assert "-subtitle" not in cmd


# =============================================================================
# Send dispatch routing
# =============================================================================

class TestSendDispatch:
    """Test _send routes to the correct backend."""

    def test_routes_to_terminal_notifier_when_available(self):
        n = MacNotifier(mode="both")
        n._has_terminal_notifier = True
        with patch.object(n, "_send_terminal_notifier") as mock_tn:
            with patch.object(n, "_send_osascript") as mock_osa:
                n._send("test message")
        mock_tn.assert_called_once()
        mock_osa.assert_not_called()

    def test_routes_to_osascript_when_terminal_notifier_unavailable(self):
        n = MacNotifier(mode="both")
        n._has_terminal_notifier = False
        with patch.object(n, "_send_terminal_notifier") as mock_tn:
            with patch.object(n, "_send_osascript") as mock_osa:
                n._send("test message")
        mock_tn.assert_not_called()
        mock_osa.assert_called_once()


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

    def test_osascript_sound_only_oserror_swallowed(self):
        """OSError in afplay for sound-only mode should be swallowed."""
        n = MacNotifier(mode="sound")
        n._has_terminal_notifier = False
        with patch("overcode.notifier.subprocess.Popen", side_effect=OSError):
            n._send("agent-1 needs attention")  # Should not raise

    def test_osascript_banner_uses_popen_devnull(self):
        """Popen should be called with DEVNULL for stdout and stderr."""
        import subprocess
        n = MacNotifier(mode="banner")
        n._has_terminal_notifier = False
        with patch("overcode.notifier.subprocess.Popen") as mock_popen:
            n._send("test message")
        kwargs = mock_popen.call_args[1]
        assert kwargs["stdout"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.DEVNULL


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

    def test_empty_string_mode_defaults_to_off(self):
        n = MacNotifier(mode="")
        assert n.mode == "off"

    def test_coalesce_seconds_stored(self):
        n = MacNotifier(mode="both", coalesce_seconds=5.0)
        assert n.coalesce_seconds == 5.0

    def test_default_coalesce_seconds(self):
        n = MacNotifier(mode="both")
        assert n.coalesce_seconds == 2.0

    def test_initial_last_send_is_zero(self):
        n = MacNotifier(mode="both")
        assert n._last_send == 0.0

    def test_initial_pending_is_empty(self):
        n = MacNotifier(mode="both")
        assert n._pending == []


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

    def test_detection_cached_as_false(self):
        """When terminal-notifier not found, False is cached too."""
        n = MacNotifier(mode="both")
        with patch("overcode.notifier.shutil.which", return_value=None):
            assert n._use_terminal_notifier() is False
        # Cached as False, even if shutil.which would now return something
        with patch("overcode.notifier.shutil.which", return_value="/usr/bin/terminal-notifier"):
            assert n._use_terminal_notifier() is False


# =============================================================================
# Regression: Repeated Cycles Must Not Re-notify (#235)
# =============================================================================

class TestNoRepeatedNotifications:
    """Prove that an agent staying in stalled state does NOT trigger repeated
    notifications.  The bug was: queue() was called on every 250ms status cycle
    for any agent with is_unvisited_stalled=True (a persistent state), instead
    of only on the *transition* to stalled.  These tests verify the fix at the
    notifier level -- queue+flush should produce at most one _send per agent
    even when called many times in a row.
    """

    def test_repeated_queue_flush_cycles_send_once(self):
        """Simulate 10 consecutive status cycles all queuing the same agent.
        Only the first flush (outside the coalesce window) should send."""
        n = MacNotifier(mode="both", coalesce_seconds=2.0)
        send_count = 0

        def counting_send(*args, **kwargs):
            nonlocal send_count
            send_count += 1

        n._send = counting_send

        with patch("overcode.notifier.sys") as mock_sys:
            mock_sys.platform = "darwin"
            # First cycle -- should send
            n.queue("agent-1", "task A")
            n.flush()
            assert send_count == 1

            # Subsequent cycles within coalesce window -- should NOT send
            for _ in range(9):
                n.queue("agent-1", "task A")
                n.flush()

        assert send_count == 1, f"Expected 1 send, got {send_count}"

    def test_coalesce_window_blocks_rapid_fire(self):
        """Even with different agents queued each cycle, coalesce window
        prevents sends until the window expires."""
        import time
        n = MacNotifier(mode="both", coalesce_seconds=100.0)  # huge window
        n._last_send = time.monotonic()  # just sent

        n._send = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not send"))

        with patch("overcode.notifier.sys") as mock_sys:
            mock_sys.platform = "darwin"
            for i in range(20):
                n.queue(f"agent-{i}")
                n.flush()

    def test_flush_after_coalesce_window_sends_exactly_once(self):
        """After the coalesce window expires, the next flush sends exactly
        once -- not once per queued cycle."""
        n = MacNotifier(mode="both", coalesce_seconds=0.0)  # no delay
        n._last_send = 0  # long ago

        calls = []
        n._send = lambda msg, sub=None: calls.append((msg, sub))

        with patch("overcode.notifier.sys") as mock_sys:
            mock_sys.platform = "darwin"
            n.queue("agent-1")
            n.flush()
            assert len(calls) == 1

            # Same agent queued again (simulating persistent-state bug)
            n.queue("agent-1")
            n.flush()

        # With a realistic coalesce window, it's suppressed:
        n2 = MacNotifier(mode="both", coalesce_seconds=2.0)
        calls2 = []
        n2._send = lambda msg, sub=None: calls2.append((msg, sub))

        with patch("overcode.notifier.sys") as mock_sys:
            mock_sys.platform = "darwin"
            n2.queue("agent-1")
            n2.flush()
            assert len(calls2) == 1

            # Immediately queue same agent again (simulating repeated cycles)
            n2.queue("agent-1")
            n2.flush()
            assert len(calls2) == 1, "Coalesce window should block second send"

    def test_pending_not_lost_during_coalesce_hold(self):
        """When flush holds due to coalesce window, pending items are NOT
        cleared -- they're retained for the next flush attempt."""
        import time
        n = MacNotifier(mode="both", coalesce_seconds=2.0)
        n._last_send = time.monotonic()  # just sent

        with patch("overcode.notifier.sys") as mock_sys:
            mock_sys.platform = "darwin"
            n.queue("agent-1")
            n.flush()  # held -- too soon
            assert len(n._pending) == 1, "Pending should be retained when held"

            n.queue("agent-2")
            n.flush()  # still held
            assert len(n._pending) == 2, "Both agents should be pending"


# =============================================================================
# Edge case: _send_terminal_notifier sound-only without banner
# =============================================================================

class TestTerminalNotifierEdgeCases:
    """Test edge cases in terminal-notifier dispatch."""

    def test_sound_only_no_banner_sends_for_sound(self):
        """mode=sound -> want_sound=True, want_banner=False.
        Should still call terminal-notifier for the sound."""
        n = MacNotifier(mode="sound")
        n._has_terminal_notifier = True
        with patch("overcode.notifier.subprocess.Popen") as mock_popen:
            n._send_terminal_notifier("test", None, want_sound=True, want_banner=False)
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert "-sound" in cmd

    def test_no_sound_no_banner_returns_early(self):
        """want_sound=False, want_banner=False should not call Popen."""
        n = MacNotifier(mode="off")
        n._has_terminal_notifier = True
        with patch("overcode.notifier.subprocess.Popen") as mock_popen:
            n._send_terminal_notifier("test", None, want_sound=False, want_banner=False)
        mock_popen.assert_not_called()

    def test_banner_with_sound(self):
        """want_sound=True, want_banner=True should include sound in cmd."""
        n = MacNotifier(mode="both")
        n._has_terminal_notifier = True
        with patch("overcode.notifier.subprocess.Popen") as mock_popen:
            n._send_terminal_notifier("test", None, want_sound=True, want_banner=True)
        cmd = mock_popen.call_args[0][0]
        assert "-sound" in cmd

    def test_banner_without_sound(self):
        """want_sound=False, want_banner=True should not include sound."""
        n = MacNotifier(mode="banner")
        n._has_terminal_notifier = True
        with patch("overcode.notifier.subprocess.Popen") as mock_popen:
            n._send_terminal_notifier("test", None, want_sound=False, want_banner=True)
        cmd = mock_popen.call_args[0][0]
        assert "-sound" not in cmd


class TestOsascriptEdgeCases:
    """Test edge cases in osascript dispatch."""

    def test_no_sound_no_banner_does_nothing(self):
        """want_sound=False, want_banner=False should not call Popen."""
        n = MacNotifier(mode="off")
        n._has_terminal_notifier = False
        with patch("overcode.notifier.subprocess.Popen") as mock_popen:
            n._send_osascript("test", None, want_sound=False, want_banner=False)
        mock_popen.assert_not_called()

    def test_sound_only_uses_afplay(self):
        """want_sound=True, want_banner=False should use afplay."""
        n = MacNotifier(mode="sound")
        n._has_terminal_notifier = False
        with patch("overcode.notifier.subprocess.Popen") as mock_popen:
            n._send_osascript("test", None, want_sound=True, want_banner=False)
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "afplay"

    def test_banner_and_sound_uses_osascript(self):
        """want_sound=True, want_banner=True should use osascript with sound."""
        n = MacNotifier(mode="both")
        n._has_terminal_notifier = False
        with patch("overcode.notifier.subprocess.Popen") as mock_popen:
            n._send_osascript("test", None, want_sound=True, want_banner=True)
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "osascript"
        assert 'sound name "Hero"' in cmd[2]


# =============================================================================
# Stall Debounce Logic (notification re-triggering fix)
# =============================================================================


class _StallTracker:
    """Minimal harness replicating TUI stall detection logic for testing.

    Mirrors the debounce logic in OvercodeApp._apply_status_results without
    requiring a full Textual app.
    """

    def __init__(self):
        self._previous_statuses: dict[str, str] = {}
        self._stall_start_times: dict[str, float] = {}
        self._notified_stalls: set[str] = set()
        self._non_stall_since: dict[str, float] = {}
        self.visited_stalled_agents: set[str] = set()
        self.prefs_changed = False

    def apply_status(self, session_id: str, status: str):
        """Run the stall detection logic for one session/status pair."""
        from overcode.tui_logic import compute_stall_state
        prev_status = self._previous_statuses.get(session_id)
        stall = compute_stall_state(
            status, prev_status, session_id,
            self.visited_stalled_agents, False,
        )

        prev_was_green = prev_status is not None and is_green_status(prev_status)
        if stall.should_clear_tracking and not prev_was_green:
            self._non_stall_since[session_id] = time.monotonic()

        if stall.is_new_stall:
            non_stall_start = self._non_stall_since.pop(session_id, None)
            active_duration = (time.monotonic() - non_stall_start) if non_stall_start else float('inf')

            if active_duration >= 60 or prev_status is None:
                self.visited_stalled_agents.discard(session_id)
                self.prefs_changed = True
                self._stall_start_times[session_id] = time.monotonic()
                self._notified_stalls.discard(session_id)
            else:
                if session_id not in self._stall_start_times:
                    self._stall_start_times[session_id] = time.monotonic()

        if stall.should_clear_tracking:
            self._stall_start_times.pop(session_id, None)
        elif status == STATUS_WAITING_USER:
            self._non_stall_since.pop(session_id, None)

        self._previous_statuses[session_id] = status


class TestStallDebounce:
    """Test that notification tracking is resilient to brief green flickers
    and daemon enrichment status changes."""

    def test_first_stall_sets_timer_and_clears_notified(self):
        """First observation of waiting_user should start stall timer."""
        t = _StallTracker()
        t.apply_status("s1", STATUS_WAITING_USER)
        assert "s1" in t._stall_start_times
        assert "s1" not in t._notified_stalls

    def test_brief_green_flicker_does_not_clear_notified(self):
        """A <60s green flicker should NOT reset _notified_stalls."""
        t = _StallTracker()
        # Initial stall
        t.apply_status("s1", STATUS_WAITING_USER)
        t._stall_start_times["s1"] = time.monotonic() - 35  # stalled 35s
        t._notified_stalls.add("s1")  # notification already sent

        # Brief green flicker (< 60s)
        t.apply_status("s1", STATUS_RUNNING)
        # Back to waiting
        t.apply_status("s1", STATUS_WAITING_USER)

        # _notified_stalls must NOT have been cleared
        assert "s1" in t._notified_stalls, "Brief flicker should not re-trigger notification"

    def test_sustained_green_resets_notified_on_new_stall(self):
        """After 60s+ of green work, a new stall should re-trigger notification."""
        t = _StallTracker()
        # Initial stall and notification
        t.apply_status("s1", STATUS_WAITING_USER)
        t._notified_stalls.add("s1")

        # Transition to green
        t.apply_status("s1", STATUS_RUNNING)
        # Backdate the green start to > 60s ago
        t._non_stall_since["s1"] = time.monotonic() - 90

        # New stall after sustained green
        t.apply_status("s1", STATUS_WAITING_USER)

        assert "s1" not in t._notified_stalls, "Sustained green should allow re-notification"
        assert "s1" in t._stall_start_times, "New stall timer should be set"

    def test_waiting_heartbeat_does_not_clear_notified(self):
        """WAITING_HEARTBEAT (daemon enrichment) should NOT clear _notified_stalls."""
        t = _StallTracker()
        # Stall and notify
        t.apply_status("s1", STATUS_WAITING_USER)
        t._notified_stalls.add("s1")
        stall_time = t._stall_start_times["s1"]

        # Daemon enriches to waiting_heartbeat
        t.apply_status("s1", STATUS_WAITING_HEARTBEAT)

        assert "s1" in t._notified_stalls, "waiting_heartbeat should not clear notification tracking"
        assert t._stall_start_times.get("s1") == stall_time, "stall timer should be preserved"

    def test_error_status_does_not_clear_notified(self):
        """ERROR status should NOT clear _notified_stalls."""
        t = _StallTracker()
        t.apply_status("s1", STATUS_WAITING_USER)
        t._notified_stalls.add("s1")

        t.apply_status("s1", STATUS_ERROR)

        assert "s1" in t._notified_stalls, "error should not clear notification tracking"

    def test_waiting_approval_does_not_clear_notified(self):
        """WAITING_APPROVAL status should NOT clear _notified_stalls."""
        t = _StallTracker()
        t.apply_status("s1", STATUS_WAITING_USER)
        t._notified_stalls.add("s1")

        t.apply_status("s1", STATUS_WAITING_APPROVAL)

        assert "s1" in t._notified_stalls, "waiting_approval should not clear notification tracking"

    def test_waiting_heartbeat_to_waiting_user_no_retrigger(self):
        """WAITING_HEARTBEAT → WAITING_USER should NOT trigger a new stall."""
        t = _StallTracker()
        # Initial stall
        t.apply_status("s1", STATUS_WAITING_USER)
        t._notified_stalls.add("s1")

        # Daemon enriches to waiting_heartbeat
        t.apply_status("s1", STATUS_WAITING_HEARTBEAT)
        # Back to waiting_user (daemon state file temporarily unreadable)
        t.apply_status("s1", STATUS_WAITING_USER)

        assert "s1" in t._notified_stalls, "enrichment flip should not re-trigger"

    def test_green_clears_stall_timer(self):
        """Green (running) status should clear the stall timer."""
        t = _StallTracker()
        t.apply_status("s1", STATUS_WAITING_USER)
        assert "s1" in t._stall_start_times

        t.apply_status("s1", STATUS_RUNNING)
        assert "s1" not in t._stall_start_times

    def test_multiple_brief_flickers_do_not_accumulate(self):
        """Repeated brief green flickers should not eventually clear _notified_stalls."""
        t = _StallTracker()
        t.apply_status("s1", STATUS_WAITING_USER)
        t._notified_stalls.add("s1")

        # Simulate 5 brief green flickers
        for _ in range(5):
            t.apply_status("s1", STATUS_RUNNING)
            t.apply_status("s1", STATUS_WAITING_USER)

        assert "s1" in t._notified_stalls, "Repeated flickers should not re-trigger"
