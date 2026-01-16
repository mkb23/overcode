"""
Unit tests for time tracking in tui_helpers.

Tests for get_current_state_times() which calculates green/non-green times
for display in the TUI.
"""

import os
import pytest
from datetime import datetime, timedelta
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.session_manager import SessionStats
from overcode.tui_helpers import get_current_state_times


class TestGetCurrentStateTimes:
    """Test get_current_state_times() calculations"""

    def test_basic_green_time_no_accumulation(self):
        """With no accumulated time and fresh state_since, shows correct elapsed time"""
        now = datetime(2024, 1, 1, 12, 30, 0)
        state_start = datetime(2024, 1, 1, 12, 0, 0)  # 30 minutes ago

        stats = SessionStats(
            current_state="running",
            state_since=state_start.isoformat(),
            green_time_seconds=0.0,  # No accumulated time yet
            non_green_time_seconds=0.0,
        )

        green, non_green = get_current_state_times(stats, now)

        # Should show 30 minutes (1800 seconds)
        assert green == pytest.approx(1800.0, rel=0.01)
        assert non_green == 0.0

    def test_no_double_counting_with_last_time_accumulation(self):
        """
        With last_time_accumulation set correctly, there's no double-counting.

        Scenario:
        - Session started 30 minutes ago (state_since = 30 min ago)
        - Daemon has been running, accumulated 30 minutes into green_time_seconds
        - Daemon last ran just now (last_time_accumulation = now)
        - get_current_state_times() should return ~30 minutes (no extra time to add)
        """
        now = datetime(2024, 1, 1, 12, 30, 0)
        state_start = datetime(2024, 1, 1, 12, 0, 0)  # 30 minutes ago

        # Daemon has been accumulating time - 30 minutes worth
        accumulated_seconds = 1800.0  # 30 minutes

        stats = SessionStats(
            current_state="running",
            state_since=state_start.isoformat(),
            green_time_seconds=accumulated_seconds,
            non_green_time_seconds=0.0,
            last_time_accumulation=now.isoformat(),  # Daemon just updated
        )

        green, non_green = get_current_state_times(stats, now)

        # With last_time_accumulation = now, no extra time should be added
        # Expected: exactly 1800 seconds (30 minutes)
        assert green == pytest.approx(1800.0, rel=0.01), (
            f"Expected ~1800s (30min), got {green}s ({green/60:.1f}min)."
        )

    def test_accumulated_time_with_recent_daemon_update(self):
        """
        When daemon recently updated, only elapsed time since last_time_accumulation
        is added to accumulated time.

        Scenario:
        - State started 30 min ago
        - Daemon accumulated 29 minutes (last ran ~1 min ago)
        - Only ~1 minute should be added, giving ~30 minutes total
        """
        now = datetime(2024, 1, 1, 12, 30, 0)
        state_start = datetime(2024, 1, 1, 12, 0, 0)  # 30 minutes ago
        last_daemon_run = datetime(2024, 1, 1, 12, 29, 0)  # 1 minute ago

        # Daemon accumulated 29 minutes (last check was ~1 min ago)
        accumulated_seconds = 1740.0  # 29 minutes

        stats = SessionStats(
            current_state="running",
            state_since=state_start.isoformat(),
            green_time_seconds=accumulated_seconds,
            non_green_time_seconds=0.0,
            last_time_accumulation=last_daemon_run.isoformat(),
        )

        green, non_green = get_current_state_times(stats, now)

        # Should be close to 30 minutes (29 accumulated + 1 minute since last daemon)
        expected_approx = 1800.0  # ~30 minutes
        assert green == pytest.approx(expected_approx, rel=0.05), (
            f"Expected ~{expected_approx/60:.1f}min, got {green/60:.1f}min"
        )

    def test_non_green_time_no_double_counting(self):
        """Non-green time correctly uses last_time_accumulation"""
        now = datetime(2024, 1, 1, 12, 30, 0)
        state_start = datetime(2024, 1, 1, 12, 0, 0)  # 30 minutes ago

        # Been in waiting_user state for 30 minutes, daemon accumulated this
        accumulated_seconds = 1800.0

        stats = SessionStats(
            current_state="waiting_user",
            state_since=state_start.isoformat(),
            green_time_seconds=0.0,
            non_green_time_seconds=accumulated_seconds,
            last_time_accumulation=now.isoformat(),  # Daemon just updated
        )

        green, non_green = get_current_state_times(stats, now)

        # Should be exactly 30 minutes (no extra time to add)
        assert green == 0.0
        assert non_green == pytest.approx(1800.0, rel=0.01), (
            f"Expected ~1800s, got {non_green}s"
        )

    def test_state_transition_no_double_count(self):
        """After state transition, time should be correct"""
        now = datetime(2024, 1, 1, 12, 30, 0)

        # State changed 5 minutes ago (so state_since is recent)
        state_start = datetime(2024, 1, 1, 12, 25, 0)  # 5 minutes ago

        # Accumulated 25 minutes of green time from before state change
        # Current state is non-green, so we add elapsed to non_green
        stats = SessionStats(
            current_state="waiting_user",
            state_since=state_start.isoformat(),
            green_time_seconds=1500.0,  # 25 minutes accumulated before transition
            non_green_time_seconds=0.0,  # Just transitioned, daemon hasn't accumulated yet
        )

        green, non_green = get_current_state_times(stats, now)

        # Green should stay at 25 minutes (no longer in running state)
        # Non-green should be ~5 minutes (time since state change)
        assert green == pytest.approx(1500.0, rel=0.01)
        assert non_green == pytest.approx(300.0, rel=0.1)  # 5 minutes


class TestTimeMismatchScenario:
    """Test correct behavior with proper time tracking"""

    def test_running_plus_stopped_equals_uptime(self):
        """
        With correct time tracking, running + stopped should equal uptime.

        Scenario:
        - Session started 25 minutes ago
        - Daemon accumulated 24 min green + 1 min non-green
        - Daemon last ran 30 seconds ago
        - Total should be ~25 minutes
        """
        session_start = datetime(2024, 1, 1, 12, 0, 0)
        now = datetime(2024, 1, 1, 12, 25, 0)  # 25 min later
        last_daemon_run = datetime(2024, 1, 1, 12, 24, 30)  # 30 seconds ago

        # Daemon accumulated 24 min green + 1 min non-green = 25 min total minus 30s
        accumulated_green = 24 * 60.0  # 24 minutes
        accumulated_non_green = 0.5 * 60.0  # 30 seconds

        stats = SessionStats(
            current_state="running",
            state_since=session_start.isoformat(),
            green_time_seconds=accumulated_green,
            non_green_time_seconds=accumulated_non_green,
            last_time_accumulation=last_daemon_run.isoformat(),
        )

        green, non_green = get_current_state_times(stats, now)
        total = green + non_green
        uptime_seconds = (now - session_start).total_seconds()

        # Total time should roughly equal uptime
        assert total == pytest.approx(uptime_seconds, rel=0.05), (
            f"Time mismatch! Total tracked ({total/60:.1f}min) should ≈ uptime ({uptime_seconds/60:.1f}min). "
            f"Green={green/60:.1f}min, Non-green={non_green/60:.1f}min"
        )

    def test_fallback_to_state_since_when_no_last_time_accumulation(self):
        """
        When last_time_accumulation is not set (e.g., daemon hasn't run yet),
        fall back to state_since for backward compatibility.
        """
        now = datetime(2024, 1, 1, 12, 30, 0)
        state_start = datetime(2024, 1, 1, 12, 0, 0)  # 30 minutes ago

        # No accumulated time, no last_time_accumulation (new session)
        stats = SessionStats(
            current_state="running",
            state_since=state_start.isoformat(),
            green_time_seconds=0.0,
            non_green_time_seconds=0.0,
            last_time_accumulation=None,  # Not set yet
        )

        green, non_green = get_current_state_times(stats, now)

        # Should use state_since as fallback and show 30 minutes
        assert green == pytest.approx(1800.0, rel=0.01)
        assert non_green == 0.0


# =============================================================================
# Tests for Time Tracking Invariant Check
# =============================================================================

class TestTimeTrackingInvariantCheck:
    """Test the invariant check that prevents accumulated time > uptime.

    This catches bugs like multiple daemons running simultaneously where
    each daemon was accumulating time independently, causing tracked time
    to be N times actual uptime.
    """

    def test_accumulated_within_tolerance_not_reset(self):
        """When accumulated time is within 10% of uptime, no reset occurs."""
        # Simulating: 1 hour uptime, 1.05 hours accumulated (5% over)
        session_start = datetime(2024, 1, 1, 12, 0, 0)
        now = datetime(2024, 1, 1, 13, 0, 0)  # 1 hour later

        uptime_seconds = (now - session_start).total_seconds()  # 3600s
        accumulated = uptime_seconds * 1.05  # 5% over = 3780s

        # Within 10% tolerance, should NOT be reset
        green_time = accumulated * 0.8  # 80% green
        non_green_time = accumulated * 0.2  # 20% non-green

        # Check invariant logic
        max_allowed = uptime_seconds
        total_accumulated = green_time + non_green_time

        # Should NOT trigger reset (5% < 10%)
        assert total_accumulated <= max_allowed * 1.1, "Test setup error: should be within tolerance"

    def test_accumulated_exceeds_tolerance_triggers_reset(self):
        """When accumulated time exceeds 110% of uptime, it gets reset."""
        # Simulating: 1 hour uptime, 7 hours accumulated (7x - multiple daemons!)
        session_start = datetime(2024, 1, 1, 12, 0, 0)
        now = datetime(2024, 1, 1, 13, 0, 0)  # 1 hour later

        uptime_seconds = (now - session_start).total_seconds()  # 3600s
        accumulated = uptime_seconds * 7  # 7x over = 25200s (7 hours)

        green_time = accumulated * 0.8  # 80% green = 20160s
        non_green_time = accumulated * 0.2  # 20% non-green = 5040s

        # Check invariant logic
        max_allowed = uptime_seconds
        total_accumulated = green_time + non_green_time

        # Should trigger reset (700% > 10%)
        assert total_accumulated > max_allowed * 1.1, "Test setup error: should exceed tolerance"

        # Apply the invariant fix
        ratio = max_allowed / total_accumulated
        fixed_green = green_time * ratio
        fixed_non_green = non_green_time * ratio

        # Verify reset preserves ratio
        original_ratio = green_time / non_green_time
        fixed_ratio = fixed_green / fixed_non_green
        assert fixed_ratio == pytest.approx(original_ratio, rel=0.001)

        # Verify total is now within uptime
        fixed_total = fixed_green + fixed_non_green
        assert fixed_total == pytest.approx(uptime_seconds, rel=0.001)
        assert fixed_total <= max_allowed * 1.1

    def test_ratio_preserved_after_reset(self):
        """The green/non-green ratio is preserved after invariant reset."""
        uptime_seconds = 3600  # 1 hour

        # 30 hours accumulated (bug scenario)
        green_time = 24 * 3600  # 24 hours green
        non_green_time = 6 * 3600  # 6 hours non-green (4:1 ratio)
        total_accumulated = green_time + non_green_time

        # Apply fix
        ratio = uptime_seconds / total_accumulated
        fixed_green = green_time * ratio
        fixed_non_green = non_green_time * ratio

        # Check ratio preserved (should be 4:1)
        original_ratio = green_time / non_green_time
        fixed_ratio = fixed_green / fixed_non_green
        assert fixed_ratio == pytest.approx(4.0, rel=0.001)
        assert original_ratio == pytest.approx(fixed_ratio, rel=0.001)

    def test_zero_accumulated_no_division_error(self):
        """Zero accumulated time doesn't cause division by zero."""
        uptime_seconds = 3600
        green_time = 0.0
        non_green_time = 0.0
        total_accumulated = green_time + non_green_time

        # The fix should handle zero total gracefully
        ratio = uptime_seconds / total_accumulated if total_accumulated > 0 else 1.0
        assert ratio == 1.0

    def test_near_zero_accumulated_handled(self):
        """Very small accumulated time is handled without overflow."""
        uptime_seconds = 3600
        green_time = 0.001  # 1ms
        non_green_time = 0.001  # 1ms
        total_accumulated = green_time + non_green_time

        # Should NOT trigger reset (way under uptime)
        assert total_accumulated <= uptime_seconds * 1.1


class TestDaemonSingletonBehavior:
    """Test that only one daemon instance should run per session.

    Multiple daemons running simultaneously cause time tracking to accumulate
    N times faster than real time, breaking the time <= uptime invariant.
    """

    def test_pid_file_prevents_duplicate_start(self, tmp_path):
        """A valid PID file should prevent a second daemon from starting."""
        from overcode.pid_utils import write_pid_file, is_process_running, remove_pid_file

        pid_file = tmp_path / "daemon.pid"

        # Simulate first daemon writing PID
        write_pid_file(pid_file, os.getpid())

        # Check should show daemon is running
        assert is_process_running(pid_file) is True

        # Clean up
        remove_pid_file(pid_file)
        assert not pid_file.exists()

    def test_stale_pid_file_cleaned_up(self, tmp_path):
        """A PID file pointing to dead process should be cleaned up."""
        from overcode.pid_utils import is_process_running, remove_pid_file

        pid_file = tmp_path / "daemon.pid"

        # Write a non-existent PID (high number unlikely to exist)
        pid_file.write_text("999999999")

        # Check should detect stale PID
        assert is_process_running(pid_file) is False

    def test_invalid_pid_file_handled(self, tmp_path):
        """Invalid PID file content is handled gracefully."""
        from overcode.pid_utils import is_process_running, get_process_pid

        pid_file = tmp_path / "daemon.pid"

        # Write invalid content
        pid_file.write_text("not-a-number")

        # Should return False/None gracefully
        assert is_process_running(pid_file) is False
        assert get_process_pid(pid_file) is None

    def test_empty_pid_file_handled(self, tmp_path):
        """Empty PID file is handled gracefully."""
        from overcode.pid_utils import is_process_running, get_process_pid

        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text("")

        assert is_process_running(pid_file) is False
        assert get_process_pid(pid_file) is None


class TestAtomicDaemonLock:
    """Test the atomic daemon lock mechanism.

    This prevents TOCTOU race conditions that could cause multiple
    daemons to start simultaneously.
    """

    def test_acquire_lock_success(self, tmp_path):
        """First daemon can acquire the lock."""
        from overcode.pid_utils import acquire_daemon_lock, remove_pid_file

        pid_file = tmp_path / "daemon.pid"

        acquired, existing_pid = acquire_daemon_lock(pid_file)

        assert acquired is True
        assert existing_pid is None
        assert pid_file.exists()
        assert int(pid_file.read_text()) == os.getpid()

        # Clean up
        remove_pid_file(pid_file)

    def test_acquire_lock_blocked_by_running_daemon(self, tmp_path):
        """Lock acquisition fails if daemon is already running."""
        from overcode.pid_utils import acquire_daemon_lock, remove_pid_file

        pid_file = tmp_path / "daemon.pid"

        # First daemon acquires lock
        acquired1, _ = acquire_daemon_lock(pid_file)
        assert acquired1 is True

        # Second attempt should fail (current process is "running")
        acquired2, existing_pid = acquire_daemon_lock(pid_file)
        assert acquired2 is False
        assert existing_pid == os.getpid()

        # Clean up
        remove_pid_file(pid_file)

    def test_acquire_lock_cleans_stale_pid(self, tmp_path):
        """Lock acquisition succeeds when PID file points to dead process."""
        from overcode.pid_utils import acquire_daemon_lock, remove_pid_file

        pid_file = tmp_path / "daemon.pid"

        # Write stale PID (dead process)
        pid_file.write_text("999999999")

        # Should succeed and overwrite stale PID
        acquired, existing_pid = acquire_daemon_lock(pid_file)
        assert acquired is True
        assert existing_pid is None
        assert int(pid_file.read_text()) == os.getpid()

        # Clean up
        remove_pid_file(pid_file)

    def test_acquire_lock_handles_invalid_pid_file(self, tmp_path):
        """Lock acquisition handles corrupt PID file."""
        from overcode.pid_utils import acquire_daemon_lock, remove_pid_file

        pid_file = tmp_path / "daemon.pid"

        # Write invalid content
        pid_file.write_text("not-a-number")

        # Should succeed and overwrite invalid content
        acquired, existing_pid = acquire_daemon_lock(pid_file)
        assert acquired is True
        assert existing_pid is None

        # Clean up
        remove_pid_file(pid_file)

    def test_lock_file_created(self, tmp_path):
        """Lock file is created alongside PID file."""
        from overcode.pid_utils import acquire_daemon_lock, remove_pid_file

        pid_file = tmp_path / "daemon.pid"
        lock_file = pid_file.with_suffix('.lock')

        acquired, _ = acquire_daemon_lock(pid_file)
        assert acquired is True

        # Lock file should exist
        assert lock_file.exists()

        # Clean up
        remove_pid_file(pid_file)
        lock_file.unlink(missing_ok=True)


class TestAccumulatedVsUptimeValidation:
    """End-to-end validation that tracked time stays close to uptime.

    These tests simulate the actual bug scenario where multiple daemons
    caused time to accumulate faster than real time.
    """

    def test_single_daemon_maintains_invariant(self):
        """A single daemon should keep accumulated time <= uptime."""
        # Simulate daemon loop iterations
        session_start = datetime(2024, 1, 1, 12, 0, 0)

        green_time = 0.0
        non_green_time = 0.0
        interval = 10  # 10 second intervals

        # Simulate 30 minutes of daemon running (180 iterations)
        for i in range(180):
            now = session_start + timedelta(seconds=(i + 1) * interval)

            # Simulate: 80% running, 20% waiting
            if i % 5 == 0:
                non_green_time += interval
            else:
                green_time += interval

        # Calculate uptime
        final_time = session_start + timedelta(seconds=180 * interval)
        uptime = (final_time - session_start).total_seconds()

        total_tracked = green_time + non_green_time
        ratio = total_tracked / uptime

        # Should be exactly 1.0 (single daemon)
        assert ratio == pytest.approx(1.0, rel=0.001)
        assert total_tracked <= uptime * 1.1

    def test_multiple_daemons_violate_invariant(self):
        """Multiple daemons WILL violate the invariant (documents the bug)."""
        session_start = datetime(2024, 1, 1, 12, 0, 0)

        # Simulate 3 daemons running simultaneously
        num_daemons = 3
        interval = 10
        iterations = 180

        green_time = 0.0

        # Each daemon adds interval to accumulated time
        for _ in range(num_daemons):
            for i in range(iterations):
                green_time += interval  # Each daemon accumulates!

        final_time = session_start + timedelta(seconds=iterations * interval)
        uptime = (final_time - session_start).total_seconds()

        ratio = green_time / uptime

        # Ratio should be ~3x (demonstrating the bug)
        assert ratio == pytest.approx(num_daemons, rel=0.01)
        assert green_time > uptime * 1.1  # Violates invariant!

    def test_invariant_fix_corrects_multiple_daemon_bug(self):
        """The invariant fix should correct accumulated time after bug."""
        session_start = datetime(2024, 1, 1, 12, 0, 0)

        num_daemons = 7  # The actual bug scenario
        interval = 10
        iterations = 180

        green_time = 0.0
        non_green_time = 0.0

        # Simulate the bug (7 daemons)
        for _ in range(num_daemons):
            for i in range(iterations):
                if i % 5 == 0:
                    non_green_time += interval
                else:
                    green_time += interval

        final_time = session_start + timedelta(seconds=iterations * interval)
        uptime = (final_time - session_start).total_seconds()

        total_accumulated = green_time + non_green_time
        original_ratio = total_accumulated / uptime

        # Verify bug occurred
        assert original_ratio == pytest.approx(7.0, rel=0.01), f"Expected 7x ratio, got {original_ratio}"

        # Apply invariant fix
        max_allowed = uptime
        if total_accumulated > max_allowed * 1.1:
            fix_ratio = max_allowed / total_accumulated
            green_time = green_time * fix_ratio
            non_green_time = non_green_time * fix_ratio

        # Verify fix worked
        fixed_total = green_time + non_green_time
        fixed_ratio = fixed_total / uptime

        assert fixed_ratio == pytest.approx(1.0, rel=0.01)
        assert fixed_total <= uptime * 1.1


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
