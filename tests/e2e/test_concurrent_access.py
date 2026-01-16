"""
E2E Test 12: Concurrent Session Access

Verify session state file handles concurrent access without corruption.

See: docs/e2e_tests/12_concurrent_access.md
"""

import pytest
import time
import subprocess
import json
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from conftest import (
    TEST_TMUX_SOCKET,
)


class TestConcurrentAccess:
    """Test concurrent access to session state."""

    @pytest.fixture(autouse=True)
    def setup(self, clean_test_env, overcode_cli):
        """Store fixtures for use in tests."""
        self.env = clean_test_env
        self.cli = overcode_cli
        self.session = clean_test_env["session_name"]
        self.socket = clean_test_env["tmux_socket"]
        self.state_dir = clean_test_env["state_dir"]

    def test_concurrent_reads(self):
        """
        Step 2: Test concurrent reads don't corrupt state.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "startup_idle"

        # Launch agent
        subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "concurrent-agent",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        time.sleep(1)

        state_file = self.state_dir / "sessions" / "sessions.json"
        errors = []

        def read_state(iteration):
            try:
                with open(state_file) as f:
                    state = json.load(f)
                # Verify valid
                assert isinstance(state, dict)
                return True
            except Exception as e:
                errors.append(f"Read {iteration}: {e}")
                return False

        # Concurrent reads
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(read_state, i) for i in range(20)]
            results = [f.result() for f in as_completed(futures)]

        assert all(results), f"Some reads failed: {errors}"

    def test_state_remains_valid_json(self):
        """
        Step 7: Verify state file is always valid JSON.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "startup_idle"

        # Launch agent
        subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "json-agent",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        time.sleep(1)

        state_file = self.state_dir / "sessions" / "sessions.json"

        # Multiple reads
        for i in range(10):
            with open(state_file) as f:
                state = json.load(f)

            # Verify required fields
            for session_id, session in state.items():
                assert "name" in session, f"Missing name in {session_id}"
                assert "tmux_session" in session, f"Missing tmux_session in {session_id}"

            time.sleep(0.1)

    def test_list_concurrent_with_operations(self):
        """
        Test list command works during other operations.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "startup_idle"

        # Launch initial agent
        subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "base-agent",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        time.sleep(1)

        errors = []

        def run_list():
            try:
                result = subprocess.run(
                    ["python", "-m", "overcode.cli", "list",
                     "--session", self.session],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    env=env,
                    cwd=Path(__file__).parent.parent.parent,
                )
                return result.returncode == 0
            except Exception as e:
                errors.append(str(e))
                return False

        # Run multiple list commands concurrently
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(run_list) for _ in range(6)]
            results = [f.result() for f in as_completed(futures)]

        assert all(results), f"Some lists failed: {errors}"


class TestNoDeadlocks:
    """Test that concurrent operations don't deadlock."""

    @pytest.fixture(autouse=True)
    def setup(self, clean_test_env, overcode_cli):
        """Store fixtures for use in tests."""
        self.env = clean_test_env
        self.cli = overcode_cli
        self.session = clean_test_env["session_name"]
        self.socket = clean_test_env["tmux_socket"]
        self.state_dir = clean_test_env["state_dir"]

    def test_no_deadlock_on_launch_kill(self):
        """
        Test launch/kill cycle doesn't deadlock.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "startup_idle"

        # Rapid launch/kill cycle
        for i in range(3):
            # Launch
            result = subprocess.run(
                ["python", "-m", "overcode.cli", "launch",
                 "--name", f"cycle-{i}",
                 "--session", self.session],
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
                cwd=Path(__file__).parent.parent.parent,
            )
            assert result.returncode == 0, f"Launch {i} failed"

            time.sleep(0.5)

            # Kill
            subprocess.run(
                ["python", "-m", "overcode.cli", "kill", f"cycle-{i}",
                 "--session", self.session],
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
                cwd=Path(__file__).parent.parent.parent,
            )

            time.sleep(0.5)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
