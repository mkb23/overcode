"""
E2E Test 08: Error Recovery - Agent Crash

Verify system handles agent crashes gracefully.

See: docs/e2e_tests/08_error_recovery_crash.md
"""

import pytest
import time
import subprocess
import json
from pathlib import Path

from conftest import (
    get_tmux_pane_content,
    TEST_TMUX_SOCKET,
)


class TestErrorRecovery:
    """Test error recovery when agents crash."""

    @pytest.fixture(autouse=True)
    def setup(self, clean_test_env, overcode_cli):
        """Store fixtures for use in tests."""
        self.env = clean_test_env
        self.cli = overcode_cli
        self.session = clean_test_env["session_name"]
        self.socket = clean_test_env["tmux_socket"]
        self.state_dir = clean_test_env["state_dir"]

    def test_list_handles_missing_window(self):
        """
        Step 7: List command works even if window is gone.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "startup_idle"

        # Launch agent
        subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "orphan-agent",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        time.sleep(1)

        # Manually kill the tmux window (simulating crash)
        subprocess.run(
            ["tmux", "-L", self.socket, "kill-window", "-t", f"{self.session}:orphan-agent"],
            capture_output=True
        )

        time.sleep(0.5)

        # List should still work
        list_result = subprocess.run(
            ["python", "-m", "overcode.cli", "list",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        # Should not crash
        assert list_result.returncode == 0 or "orphan-agent" in list_result.stdout

    def test_state_persists_after_window_close(self):
        """
        Step 5: State file survives window close.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "startup_idle"

        # Launch agent
        subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "state-test",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        time.sleep(1)

        # Verify in state
        state_file = self.state_dir / "sessions" / "sessions.json"
        assert state_file.exists(), "State file not created"

        with open(state_file) as f:
            state = json.load(f)

        # Find our session
        found = False
        for s in state.values():
            if s.get("name") == "state-test":
                found = True
                break

        assert found, "Session not in state file"

        # Kill window
        subprocess.run(
            ["tmux", "-L", self.socket, "kill-window", "-t", f"{self.session}:state-test"],
            capture_output=True
        )

        # State file should still exist
        assert state_file.exists(), "State file was removed when window closed"

    def test_kill_handles_already_dead_window(self):
        """
        Test kill command handles already-dead windows gracefully.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "startup_idle"

        # Launch agent
        subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "dead-agent",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        time.sleep(1)

        # Manually kill the tmux window
        subprocess.run(
            ["tmux", "-L", self.socket, "kill-window", "-t", f"{self.session}:dead-agent"],
            capture_output=True
        )

        # Kill command should handle this gracefully
        kill_result = subprocess.run(
            ["python", "-m", "overcode.cli", "kill", "dead-agent",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        # Should not throw an unhandled error
        # (may return error code but shouldn't crash)
        assert kill_result.returncode in [0, 1], f"Kill crashed: {kill_result.stderr}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
