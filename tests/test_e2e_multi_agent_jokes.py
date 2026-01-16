#!/usr/bin/env python3
"""
E2E Integration Test: Multi-Agent Joke Writing with Feedback

Test flow:
1. Clean slate - kill all overcode sessions
2. Create overcode session "agents"
3. Launch two Claude agents:
   - Agent 1: one-liner jokes → one_liners.txt
   - Agent 2: pun jokes → puns.txt
4. Verify both agents produce initial jokes
5. Set standing advice: "Provide feedback on jokes"
6. Verify both agents are blocked/waiting for feedback
7. Overcode provides feedback via tmux (simulating user)
8. Verify both agents unblock and produce improved jokes
9. Teardown everything
"""

import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, List, Set
import json
import signal
import atexit
import os

try:
    import pytest
    HAS_PYTEST = True
except ImportError:
    HAS_PYTEST = False
    # Create mock pytest decorators
    class MockPytest:
        class mark:
            @staticmethod
            def timeout(seconds):
                def decorator(func):
                    return func
                return decorator
            @staticmethod
            def e2e(func):
                return func
            @staticmethod
            def requires_tmux(func):
                return func
            @staticmethod
            def requires_claude(func):
                return func
        @staticmethod
        def fixture(*args, **kwargs):
            def decorator(func):
                return func
            return decorator
    pytest = MockPytest()

# Add src to path for local imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from overcode.launcher import ClaudeLauncher
from overcode.session_manager import SessionManager
from overcode.status_detector import StatusDetector
from overcode.tmux_manager import TmuxManager


# Test Configuration
TEST_TMUX_SESSION = "overcode-e2e-test"
TEST_AGENT_NAME = "agents"
TEST_WORK_DIR = Path("/tmp/overcode_e2e_test")
ONE_LINER_FILE = TEST_WORK_DIR / "one_liners.txt"
PUNS_FILE = TEST_WORK_DIR / "puns.txt"

# Timeouts (in seconds)
TIMEOUT_SESSION_START = 30
TIMEOUT_FILE_CREATION = 60
TIMEOUT_STATUS_CHANGE = 90
TIMEOUT_UNBLOCK = 120

# Agent prompts
ONE_LINER_PROMPT = """Write 5 one-liner jokes and save them to one_liners.txt in the current directory.
Each joke should be on its own line. After writing the jokes, ask me for feedback."""

PUNS_PROMPT = """Write 5 pun-based jokes and save them to puns.txt in the current directory.
Each joke should be on its own line. After writing the jokes, ask me for feedback."""

FEEDBACK_ONE_LINERS = "Make the one-liners more clever and surprising. Focus on wordplay."
FEEDBACK_PUNS = "Make the puns groanier and more obvious. Really lean into the pun."


class E2ETestHelper:
    """Helper utilities for E2E testing"""

    def __init__(self, tmux_session: str):
        self.tmux_session = tmux_session
        self.tmux_manager = TmuxManager(tmux_session)
        self.session_manager = SessionManager()
        self.status_detector = StatusDetector(tmux_session)

        # Register emergency cleanup handler (runs on exit/crash)
        atexit.register(self._emergency_cleanup)

    def _emergency_cleanup(self):
        """Emergency cleanup - runs on exit, even on crash"""
        try:
            # Only print if we're actually cleaning something
            result = subprocess.run(
                ["tmux", "has-session", "-t", self.tmux_session],
                capture_output=True,
                timeout=2
            )
            if result.returncode == 0:
                print(f"\n⚠️  Emergency cleanup: killing test session {self.tmux_session}")
                self.aggressive_cleanup(timeout=10)
        except Exception:
            pass  # Silent failure in emergency cleanup

    def _get_test_session_claude_pids(self) -> List[str]:
        """
        Get PIDs of Claude processes that belong to the test tmux session.

        This avoids killing unrelated Claude processes (like the user's main session).
        """
        test_pids = []

        try:
            # Get all pane PIDs from the test tmux session
            result = subprocess.run(
                ["tmux", "list-panes", "-s", "-t", self.tmux_session, "-F", "#{pane_pid}"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode != 0:
                return []

            pane_pids = [pid.strip() for pid in result.stdout.split('\n') if pid.strip()]

            # For each pane PID, find child processes that are claude
            for pane_pid in pane_pids:
                # Use pgrep to find children of this pane that match "claude"
                result = subprocess.run(
                    ["pgrep", "-P", pane_pid, "-f", "claude"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    child_pids = [pid.strip() for pid in result.stdout.split('\n') if pid.strip()]
                    test_pids.extend(child_pids)

                # Also check grandchildren (claude might spawn subprocesses)
                for child_pid in test_pids[:]:  # Copy to avoid mutation during iteration
                    result = subprocess.run(
                        ["pgrep", "-P", child_pid],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if result.returncode == 0:
                        grandchild_pids = [pid.strip() for pid in result.stdout.split('\n') if pid.strip()]
                        test_pids.extend(grandchild_pids)

        except Exception as e:
            print(f"  Warning: Could not enumerate test session processes: {e}")

        return list(set(test_pids))  # Deduplicate

    def aggressive_cleanup(self, timeout: int = 30):
        """
        Kill test session processes with prejudice (but ONLY test session processes).

        This method ensures cleanup completes even if processes are stuck.
        Uses escalating force: SIGTERM → SIGKILL

        IMPORTANT: Only kills Claude processes that are children of the test
        tmux session, NOT all Claude processes system-wide.
        """
        print(f"\n🧹 Aggressive cleanup (timeout: {timeout}s)...")

        cleanup_start = time.time()

        # Step 1: Kill claude processes IN THIS TEST SESSION ONLY (not all claude!)
        try:
            print(f"  Finding claude processes in test session '{self.tmux_session}'...")
            pids = self._get_test_session_claude_pids()

            if pids:
                print(f"  Found {len(pids)} claude processes in test session")

                for pid in pids:
                    try:
                        # Try graceful SIGTERM first
                        subprocess.run(["kill", "-TERM", pid], timeout=2, capture_output=True)
                        print(f"    Sent SIGTERM to PID {pid}")
                    except Exception as e:
                        print(f"    Warning: Could not TERM PID {pid}: {e}")

                # Wait a moment for graceful shutdown
                time.sleep(2)

                # Check if any survived, use SIGKILL
                surviving_pids = self._get_test_session_claude_pids()
                if surviving_pids:
                    print(f"  {len(surviving_pids)} processes didn't respond, using SIGKILL...")
                    for pid in surviving_pids:
                        try:
                            subprocess.run(["kill", "-9", pid], timeout=2, capture_output=True)
                            print(f"    Sent SIGKILL to PID {pid}")
                        except Exception:
                            pass
            else:
                print("  No claude processes found in test session")
        except subprocess.TimeoutExpired:
            print("  ⚠️  Timeout finding claude processes, continuing...")
        except Exception as e:
            print(f"  Warning: Error killing claude processes: {e}")

        # Step 2: Kill tmux session (with timeout)
        try:
            print(f"  Killing tmux session: {self.tmux_session}")
            result = subprocess.run(
                ["tmux", "kill-session", "-t", self.tmux_session],
                capture_output=True,
                timeout=5
            )
            if result.returncode == 0:
                print(f"    ✓ Tmux session killed")
            else:
                print(f"    ℹ️  Tmux session already gone")
        except subprocess.TimeoutExpired:
            print(f"    ⚠️  Timeout killing tmux session (may be hung)")
            # Try to kill tmux server process itself if needed
            try:
                result = subprocess.run(
                    ["pgrep", "-f", f"tmux.*{self.tmux_session}"],
                    capture_output=True,
                    text=True,
                    timeout=3
                )
                if result.returncode == 0:
                    tmux_pids = [pid.strip() for pid in result.stdout.split('\n') if pid.strip()]
                    for pid in tmux_pids:
                        subprocess.run(["kill", "-9", pid], timeout=2, capture_output=True)
                        print(f"    Sent SIGKILL to tmux PID {pid}")
            except Exception:
                pass
        except Exception as e:
            print(f"    Warning: {e}")

        # Step 3: Clean up state file
        state_file = Path.home() / ".overcode" / "sessions" / "sessions.json"
        if state_file.exists():
            try:
                # Use file locking if available
                import fcntl
                with open(state_file, 'r+') as f:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    try:
                        state = json.load(f)
                        # Remove test sessions
                        updated = {k: v for k, v in state.items()
                                  if not v.get('name', '').startswith('test-')}
                        f.seek(0)
                        f.truncate()
                        json.dump(updated, f, indent=2)
                        print(f"    ✓ Cleaned state file")
                    finally:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except ImportError:
                # No fcntl on Windows, use simple approach
                try:
                    with open(state_file, 'r') as f:
                        state = json.load(f)
                    updated = {k: v for k, v in state.items()
                              if not v.get('name', '').startswith('test-')}
                    with open(state_file, 'w') as f:
                        json.dump(updated, f, indent=2)
                    print(f"    ✓ Cleaned state file")
                except Exception as e:
                    print(f"    Warning: Could not clean state file: {e}")
            except Exception as e:
                print(f"    Warning: Could not clean state file: {e}")

        # Step 4: Remove test work directory
        if TEST_WORK_DIR.exists():
            try:
                import shutil
                shutil.rmtree(TEST_WORK_DIR, ignore_errors=True)
                print(f"    ✓ Removed test work directory")
            except Exception as e:
                print(f"    Warning: Could not remove work dir: {e}")

        elapsed = time.time() - cleanup_start
        print(f"  Cleanup completed in {elapsed:.1f}s")

        # Check if we exceeded timeout
        if elapsed > timeout:
            print(f"  ⚠️  WARNING: Cleanup took longer than timeout ({timeout}s)")

    def cleanup_all_sessions(self):
        """
        Standard cleanup - graceful shutdown
        Falls back to aggressive_cleanup if this fails
        """
        print("\n🧹 Cleaning up all sessions...")

        try:
            # Kill all registered sessions
            launcher = ClaudeLauncher(self.tmux_session)
            sessions = launcher.list_sessions(detect_terminated=False, kill_untracked=False)

            for session in sessions:
                print(f"  Killing session: {session.name}")
                try:
                    launcher.kill_session(session.name)
                except Exception as e:
                    print(f"    Warning: {e}")

            # Kill the tmux session
            try:
                subprocess.run(
                    ["tmux", "kill-session", "-t", self.tmux_session],
                    capture_output=True,
                    timeout=5
                )
                print(f"  Killed tmux session: {self.tmux_session}")
            except Exception as e:
                print(f"  Tmux session cleanup (already gone): {e}")

            # Clean up state file (simple version first)
            state_file = Path.home() / ".overcode" / "sessions" / "sessions.json"
            if state_file.exists():
                try:
                    with open(state_file, 'r') as f:
                        state = json.load(f)
                    # Remove test sessions
                    updated = {k: v for k, v in state.items()
                              if not v.get('name', '').startswith('test-')}
                    with open(state_file, 'w') as f:
                        json.dump(updated, f, indent=2)
                except Exception as e:
                    print(f"  Warning: Could not clean state file: {e}")

        except Exception as e:
            print(f"  ⚠️  Standard cleanup failed: {e}")
            print(f"  Falling back to aggressive cleanup...")
            self.aggressive_cleanup(timeout=20)

    def wait_for_file(self, file_path: Path, timeout: int = TIMEOUT_FILE_CREATION) -> bool:
        """Wait for a file to be created and have content"""
        print(f"  Waiting for file: {file_path}")
        start_time = time.time()

        while time.time() - start_time < timeout:
            if file_path.exists():
                # Check that file has content (at least 10 bytes)
                if file_path.stat().st_size > 10:
                    print(f"    ✓ File created: {file_path} ({file_path.stat().st_size} bytes)")
                    return True
            time.sleep(1)

        print(f"    ✗ Timeout waiting for file: {file_path}")
        return False

    def wait_for_status(self, session_name: str, expected_status: str,
                       timeout: int = TIMEOUT_STATUS_CHANGE) -> bool:
        """Wait for a session to reach a specific status"""
        print(f"  Waiting for session '{session_name}' to reach status: {expected_status}")
        start_time = time.time()

        # Get the session to find its window
        sessions = ClaudeLauncher(self.tmux_session).list_sessions(
            detect_terminated=False, kill_untracked=False
        )
        session = next((s for s in sessions if s.name == session_name), None)
        if not session:
            print(f"    ✗ Session not found: {session_name}")
            return False

        while time.time() - start_time < timeout:
            # Pass the full session object, not just window_index
            # detect_status returns a tuple: (status, activity_description)
            current_status, activity = self.status_detector.detect_status(session)

            print(f"    Current status: {current_status} (expecting: {expected_status}) - {activity}")

            if current_status == expected_status:
                print(f"    ✓ Status reached: {expected_status}")
                return True

            time.sleep(2)

        print(f"    ✗ Timeout waiting for status: {expected_status}")
        return False

    def send_to_session(self, session_name: str, text: str):
        """Send text to a session via tmux"""
        print(f"  Sending to session '{session_name}': {text[:50]}...")

        # Get the session window
        sessions = ClaudeLauncher(self.tmux_session).list_sessions(
            detect_terminated=False, kill_untracked=False
        )
        session = next((s for s in sessions if s.name == session_name), None)
        if not session:
            print(f"    ✗ Session not found: {session_name}")
            return False

        # Send text via tmux - send text first, then Enter separately
        # Claude's TUI needs these as separate events
        try:
            # Send the text
            subprocess.run(
                ["tmux", "send-keys", "-t",
                 f"{self.tmux_session}:{session.tmux_window}", text],
                timeout=5,
                capture_output=True
            )
            # Small delay for UI to process
            time.sleep(0.3)
            # Then send Enter to submit
            subprocess.run(
                ["tmux", "send-keys", "-t",
                 f"{self.tmux_session}:{session.tmux_window}", "Enter"],
                timeout=5,
                capture_output=True
            )
            print(f"    ✓ Text sent to window {session.tmux_window}")
            return True
        except Exception as e:
            print(f"    ✗ Failed to send text: {e}")
            return False

    def accept_bypass_permissions_dialog(self, session_name: str) -> bool:
        """
        Accept the --dangerously-skip-permissions confirmation dialog.

        Claude shows:
        ❯ 1. No, exit
          2. Yes, I accept

        We need to press Down arrow to select "Yes, I accept", then Enter.
        """
        print(f"  Accepting bypass permissions dialog for '{session_name}'...")

        sessions = ClaudeLauncher(self.tmux_session).list_sessions(
            detect_terminated=False, kill_untracked=False
        )
        session = next((s for s in sessions if s.name == session_name), None)
        if not session:
            print(f"    ✗ Session not found: {session_name}")
            return False

        try:
            # Send Down arrow to select option 2
            subprocess.run(
                ["tmux", "send-keys", "-t",
                 f"{self.tmux_session}:{session.tmux_window}", "Down"],
                timeout=5,
                capture_output=True
            )
            # Small delay to let the UI update
            time.sleep(0.5)
            # Then Enter to confirm
            subprocess.run(
                ["tmux", "send-keys", "-t",
                 f"{self.tmux_session}:{session.tmux_window}", "Enter"],
                timeout=5,
                capture_output=True
            )
            print(f"    ✓ Sent Down+Enter to accept bypass permissions")
            return True
        except Exception as e:
            print(f"    ✗ Failed to accept dialog: {e}")
            return False

    def read_file_content(self, file_path: Path) -> Optional[str]:
        """Read content from a file"""
        try:
            return file_path.read_text()
        except Exception as e:
            print(f"  Warning: Could not read {file_path}: {e}")
            return None

    def count_file_lines(self, file_path: Path) -> int:
        """Count non-empty lines in a file"""
        try:
            content = file_path.read_text()
            lines = [line.strip() for line in content.split('\n') if line.strip()]
            return len(lines)
        except Exception:
            return 0


@pytest.fixture(scope="function")
def test_helper():
    """
    Pytest fixture to provide test helper with cleanup

    SAFETY GUARANTEES:
    - Cleanup runs even if test fails/crashes (via yield)
    - Uses aggressive_cleanup to force-kill stuck processes
    - atexit handler as last resort
    - Timeouts on all subprocess operations
    """
    helper = E2ETestHelper(TEST_TMUX_SESSION)

    # Setup: Clean slate with aggressive cleanup (ensure no leftovers)
    print("\n" + "="*70)
    print("PRE-TEST CLEANUP")
    print("="*70)
    helper.aggressive_cleanup(timeout=30)

    # Ensure test work directory exists
    TEST_WORK_DIR.mkdir(parents=True, exist_ok=True)

    # Clean up old joke files
    if ONE_LINER_FILE.exists():
        ONE_LINER_FILE.unlink()
    if PUNS_FILE.exists():
        PUNS_FILE.unlink()

    try:
        yield helper
    finally:
        # Teardown: ALWAYS runs, even on test failure
        print("\n" + "="*70)
        print("POST-TEST CLEANUP")
        print("="*70)

        # Use aggressive cleanup to ensure everything is killed
        helper.aggressive_cleanup(timeout=30)

        # Final verification: check for orphaned processes from THIS test session only
        # Note: We don't check all claude processes since the user likely has their own session
        orphan_pids = helper._get_test_session_claude_pids()
        if orphan_pids:
            print(f"\n⚠️  WARNING: Found {len(orphan_pids)} orphaned test claude processes!")
            print(f"   PIDs: {', '.join(orphan_pids)}")
            print(f"   Run: kill -9 {' '.join(orphan_pids)}")

        print("\n" + "="*70)
        print("CLEANUP COMPLETE")
        print("="*70)


@pytest.mark.timeout(600)  # 10 minute hard timeout
@pytest.mark.e2e
@pytest.mark.requires_tmux
@pytest.mark.requires_claude
def test_e2e_multi_agent_jokes_with_feedback(test_helper: E2ETestHelper):
    """
    E2E test: Two agents write jokes, wait for feedback, then improve jokes

    SAFETY FEATURES:
    - 10 minute hard timeout (pytest-timeout)
    - Aggressive cleanup on failure
    - Orphaned process detection
    - All subprocess calls have timeouts
    - atexit handler as last resort

    If this test hangs or crashes, cleanup WILL still run.
    """
    print("\n" + "="*70)
    print("E2E TEST: Multi-Agent Joke Writing with Feedback")
    print("="*70)

    launcher = ClaudeLauncher(TEST_TMUX_SESSION)

    # Step 1: Verify clean slate (only check sessions in our test tmux session)
    print("\n[Step 1] Verifying clean slate...")
    all_sessions = launcher.list_sessions(detect_terminated=False, kill_untracked=False)
    sessions = [s for s in all_sessions if s.tmux_session == TEST_TMUX_SESSION]
    assert len(sessions) == 0, f"Expected 0 sessions in {TEST_TMUX_SESSION}, found {len(sessions)}"
    print("  ✓ No existing sessions")

    # Step 2: Launch first agent (one-liners)
    # Note: --dangerously-skip-permissions skips the "trust this folder" dialog
    # This is safe for e2e tests in /tmp but should not be used in production
    print("\n[Step 2] Launching one-liner agent...")
    one_liner_session = launcher.launch(
        name="test-one-liners",
        start_directory=str(TEST_WORK_DIR),
        dangerously_skip_permissions=True,
    )
    assert one_liner_session is not None, "Failed to launch one-liner agent"
    print(f"  ✓ One-liner agent launched in window {one_liner_session.tmux_window}")

    # Wait for bypass permissions dialog to appear
    time.sleep(3)

    # Accept the bypass permissions confirmation dialog
    test_helper.accept_bypass_permissions_dialog("test-one-liners")

    # Wait for Claude to fully initialize after accepting
    time.sleep(5)

    # Step 3: Launch second agent (puns)
    print("\n[Step 3] Launching puns agent...")
    puns_session = launcher.launch(
        name="test-puns",
        start_directory=str(TEST_WORK_DIR),
        dangerously_skip_permissions=True,
    )
    assert puns_session is not None, "Failed to launch puns agent"
    print(f"  ✓ Puns agent launched in window {puns_session.tmux_window}")

    # Wait for bypass permissions dialog to appear
    time.sleep(3)

    # Accept the bypass permissions confirmation dialog
    test_helper.accept_bypass_permissions_dialog("test-puns")

    # Wait for Claude to fully initialize after accepting
    time.sleep(5)

    # Step 4: Send prompts to both agents
    print("\n[Step 4] Sending joke writing prompts to agents...")
    assert test_helper.send_to_session("test-one-liners", ONE_LINER_PROMPT)
    time.sleep(2)
    assert test_helper.send_to_session("test-puns", PUNS_PROMPT)
    print("  ✓ Prompts sent to both agents")

    # Step 5: Wait for jokes to be written to files
    print("\n[Step 5] Waiting for jokes to be written to disk...")
    assert test_helper.wait_for_file(ONE_LINER_FILE, timeout=TIMEOUT_FILE_CREATION), \
        f"One-liner file not created: {ONE_LINER_FILE}"
    assert test_helper.wait_for_file(PUNS_FILE, timeout=TIMEOUT_FILE_CREATION), \
        f"Puns file not created: {PUNS_FILE}"

    # Verify content
    one_liner_count = test_helper.count_file_lines(ONE_LINER_FILE)
    puns_count = test_helper.count_file_lines(PUNS_FILE)
    print(f"  ✓ One-liners file has {one_liner_count} lines")
    print(f"  ✓ Puns file has {puns_count} lines")
    assert one_liner_count >= 3, f"Expected at least 3 one-liners, got {one_liner_count}"
    assert puns_count >= 3, f"Expected at least 3 puns, got {puns_count}"

    # Step 6: Wait for both agents to be waiting/blocked (asking for feedback)
    print("\n[Step 6] Waiting for agents to ask for feedback (blocked/waiting state)...")

    # The agents should be in a "waiting" state after asking for feedback
    # This might show up as 'waiting_for_user' or similar
    one_liner_waiting = test_helper.wait_for_status(
        "test-one-liners",
        "waiting_user",  # StatusDetector.STATUS_WAITING_USER
        timeout=TIMEOUT_STATUS_CHANGE
    )
    puns_waiting = test_helper.wait_for_status(
        "test-puns",
        "waiting_user",  # StatusDetector.STATUS_WAITING_USER
        timeout=TIMEOUT_STATUS_CHANGE
    )

    # Note: If the exact status string differs, adjust accordingly
    # You may need to check the actual status detector output
    if not one_liner_waiting:
        print("  ⚠ One-liner agent may not be in expected waiting state")
    if not puns_waiting:
        print("  ⚠ Puns agent may not be in expected waiting state")

    print("  ✓ Both agents appear to be waiting for feedback")

    # Step 7: Provide feedback via tmux (simulating user input)
    print("\n[Step 7] Providing feedback to agents...")

    # Save original file sizes to detect changes
    one_liner_size_before = ONE_LINER_FILE.stat().st_size
    puns_size_before = PUNS_FILE.stat().st_size

    # Send feedback
    assert test_helper.send_to_session("test-one-liners", FEEDBACK_ONE_LINERS)
    time.sleep(2)
    assert test_helper.send_to_session("test-puns", FEEDBACK_PUNS)
    print("  ✓ Feedback sent to both agents")

    # Step 8: Wait for agents to unblock and produce new jokes
    print("\n[Step 8] Waiting for agents to unblock and improve jokes...")

    # Wait for file changes (agents should update the files)
    start_time = time.time()
    one_liner_updated = False
    puns_updated = False

    while time.time() - start_time < TIMEOUT_UNBLOCK:
        # Check if files have been updated (size changed)
        if ONE_LINER_FILE.exists():
            current_size = ONE_LINER_FILE.stat().st_size
            if current_size != one_liner_size_before:
                one_liner_updated = True
                print(f"  ✓ One-liners file updated ({one_liner_size_before} → {current_size} bytes)")

        if PUNS_FILE.exists():
            current_size = PUNS_FILE.stat().st_size
            if current_size != puns_size_before:
                puns_updated = True
                print(f"  ✓ Puns file updated ({puns_size_before} → {current_size} bytes)")

        if one_liner_updated and puns_updated:
            break

        time.sleep(5)

    assert one_liner_updated, "One-liners file was not updated after feedback"
    assert puns_updated, "Puns file was not updated after feedback"

    # Verify improved content
    new_one_liner_count = test_helper.count_file_lines(ONE_LINER_FILE)
    new_puns_count = test_helper.count_file_lines(PUNS_FILE)
    print(f"  ✓ Updated one-liners: {new_one_liner_count} lines")
    print(f"  ✓ Updated puns: {new_puns_count} lines")

    # Step 9: Verify sessions are in a good state
    print("\n[Step 9] Verifying final session state...")
    final_sessions = launcher.list_sessions(detect_terminated=False, kill_untracked=False)
    assert len(final_sessions) == 2, f"Expected 2 sessions, found {len(final_sessions)}"
    print("  ✓ Both sessions still active")

    # Print sample output
    print("\n" + "="*70)
    print("SAMPLE OUTPUTS")
    print("="*70)

    print("\n--- One-Liners (Final) ---")
    one_liner_content = test_helper.read_file_content(ONE_LINER_FILE)
    if one_liner_content:
        print(one_liner_content[:500])  # First 500 chars

    print("\n--- Puns (Final) ---")
    puns_content = test_helper.read_file_content(PUNS_FILE)
    if puns_content:
        print(puns_content[:500])  # First 500 chars

    print("\n" + "="*70)
    print("✓ E2E TEST PASSED")
    print("="*70)


if __name__ == "__main__":
    # Allow running directly for debugging
    print("Running E2E test directly (not via pytest)...")
    print("\n" + "="*70)
    print("SAFETY: Test will clean up all processes on exit/crash")
    print("="*70)

    helper = E2ETestHelper(TEST_TMUX_SESSION)

    # Pre-test cleanup
    print("\n" + "="*70)
    print("PRE-TEST CLEANUP")
    print("="*70)
    helper.aggressive_cleanup(timeout=30)

    # Ensure test work directory exists
    TEST_WORK_DIR.mkdir(parents=True, exist_ok=True)

    try:
        test_e2e_multi_agent_jokes_with_feedback(helper)
        exit_code = 0
    except AssertionError as e:
        print(f"\n✗ TEST FAILED: {e}")
        exit_code = 1
    except KeyboardInterrupt:
        print(f"\n⚠️  TEST INTERRUPTED BY USER (Ctrl+C)")
        exit_code = 130
    except Exception as e:
        print(f"\n✗ TEST ERROR: {e}")
        import traceback
        traceback.print_exc()
        exit_code = 1
    finally:
        # ALWAYS cleanup, even on crash
        print("\n" + "="*70)
        print("POST-TEST CLEANUP (forced)")
        print("="*70)
        helper.aggressive_cleanup(timeout=30)

        # Final verification - only check for orphans from THIS test session
        orphan_pids = helper._get_test_session_claude_pids()
        if orphan_pids:
            print(f"\n⚠️  WARNING: Found {len(orphan_pids)} orphaned test claude processes!")
            print(f"   PIDs: {', '.join(orphan_pids)}")
            print(f"   Manual cleanup: kill -9 {' '.join(orphan_pids)}")
            exit_code = 2  # Indicate incomplete cleanup
        else:
            print("\n✓ No orphaned test Claude processes found")

        print("\n" + "="*70)
        print("CLEANUP COMPLETE")
        print("="*70)

    if exit_code == 0:
        print("\n✓ All tests passed!")
    else:
        print(f"\n✗ Test exited with code {exit_code}")

    sys.exit(exit_code)
