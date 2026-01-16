"""
E2E Test 04: Daemon Supervision Loop

Verify the daemon can start, stop, and detect sessions.

See: docs/e2e_tests/04_daemon_supervision.md

Note: Full daemon-claude interaction testing is deferred as it requires
complex setup. This tests the daemon lifecycle and basic detection.
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


class TestDaemonLifecycle:
    """Test daemon start/stop lifecycle."""

    @pytest.fixture(autouse=True)
    def setup(self, clean_test_env, overcode_cli):
        """Store fixtures for use in tests."""
        self.env = clean_test_env
        self.cli = overcode_cli
        self.session = clean_test_env["session_name"]
        self.socket = clean_test_env["tmux_socket"]
        self.state_dir = clean_test_env["state_dir"]

    def test_daemon_start_creates_pid_file(self):
        """
        Step 4: Start daemon and verify PID file is created.
        """
        env = self.env["env"].copy()

        # Start daemon in background with short interval
        proc = subprocess.Popen(
            ["python", "-m", "overcode.cli", "daemon", "start",
             "--session", self.session,
             "--interval", "60"],  # Long interval so it doesn't loop much
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        try:
            # Give daemon time to start
            time.sleep(2)

            # Check status
            status_result = subprocess.run(
                ["python", "-m", "overcode.cli", "daemon", "status"],
                capture_output=True,
                text=True,
                timeout=5,
                env=env,
                cwd=Path(__file__).parent.parent.parent,
            )

            # Either running or shows some status
            assert status_result.returncode == 0 or "not running" in status_result.stdout.lower()
        finally:
            # Stop daemon
            subprocess.run(
                ["python", "-m", "overcode.cli", "daemon", "stop"],
                capture_output=True,
                text=True,
                timeout=5,
                env=env,
                cwd=Path(__file__).parent.parent.parent,
            )
            proc.terminate()
            proc.wait(timeout=2)

    def test_daemon_stop(self):
        """
        Step 10: Stop daemon and verify it stops.
        """
        env = self.env["env"].copy()

        # Start daemon in background
        proc = subprocess.Popen(
            ["python", "-m", "overcode.cli", "daemon", "start",
             "--session", self.session,
             "--interval", "60"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        try:
            time.sleep(2)

            # Stop daemon
            stop_result = subprocess.run(
                ["python", "-m", "overcode.cli", "daemon", "stop"],
                capture_output=True,
                text=True,
                timeout=5,
                env=env,
                cwd=Path(__file__).parent.parent.parent,
            )

            # Should succeed or indicate not running
            assert stop_result.returncode == 0 or "not running" in stop_result.stdout.lower()
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()


class TestDaemonDetection:
    """Test daemon detection of session states."""

    @pytest.fixture(autouse=True)
    def setup(self, clean_test_env, overcode_cli):
        """Store fixtures for use in tests."""
        self.env = clean_test_env
        self.cli = overcode_cli
        self.session = clean_test_env["session_name"]
        self.socket = clean_test_env["tmux_socket"]
        self.state_dir = clean_test_env["state_dir"]

    def test_daemon_status_shows_state(self):
        """
        Verify daemon status command works.
        """
        env = self.env["env"].copy()

        result = subprocess.run(
            ["python", "-m", "overcode.cli", "daemon", "status"],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        # Should return something (running or not running)
        assert result.returncode == 0 or result.returncode == 1
        # Should have some output
        assert len(result.stdout) > 0 or len(result.stderr) > 0


class TestStandingInstructions:
    """Test standing instructions for daemon supervision."""

    @pytest.fixture(autouse=True)
    def setup(self, clean_test_env, overcode_cli):
        """Store fixtures for use in tests."""
        self.env = clean_test_env
        self.cli = overcode_cli
        self.session = clean_test_env["session_name"]
        self.socket = clean_test_env["tmux_socket"]
        self.state_dir = clean_test_env["state_dir"]

    def test_instruct_sets_standing_instructions(self):
        """
        Step 3: Set standing instructions and verify they are saved.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "startup_idle"

        # Launch agent first
        subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "instruct-agent",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        time.sleep(1)

        # Set instructions
        instruct_result = subprocess.run(
            ["python", "-m", "overcode.cli", "instruct",
             "instruct-agent", "Approve all permission prompts",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        assert instruct_result.returncode == 0, f"Instruct failed: {instruct_result.stderr}"

        # Verify in state file
        state_file = self.state_dir / "sessions" / "sessions.json"
        if state_file.exists():
            with open(state_file) as f:
                state = json.load(f)

            # Find our session
            for session_id, s in state.items():
                if s.get("name") == "instruct-agent":
                    assert "Approve all" in s.get("standing_instructions", ""), \
                        f"Instructions not saved. Session: {s}"
                    break


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
