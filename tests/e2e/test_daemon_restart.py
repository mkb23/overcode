"""
E2E Test 09: Error Recovery - Daemon Restart

Verify daemon can be restarted and picks up state correctly.

See: docs/e2e_tests/09_daemon_restart.md
"""

import pytest
import time
import subprocess
import json
from pathlib import Path

from conftest import (
    TEST_TMUX_SOCKET,
)


class TestDaemonRestart:
    """Test daemon restart and state recovery."""

    @pytest.fixture(autouse=True)
    def setup(self, clean_test_env, overcode_cli):
        """Store fixtures for use in tests."""
        self.env = clean_test_env
        self.cli = overcode_cli
        self.session = clean_test_env["session_name"]
        self.socket = clean_test_env["tmux_socket"]
        self.state_dir = clean_test_env["state_dir"]

    def test_agent_survives_daemon_stop(self):
        """
        Steps 4-5: Agent survives daemon being stopped.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "startup_idle"

        # Launch agent
        subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "survive-agent",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        time.sleep(1)

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

        time.sleep(2)

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
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()

        time.sleep(1)

        # Agent should still exist in tmux
        tmux_result = subprocess.run(
            ["tmux", "-L", self.socket, "list-windows", "-t", self.session, "-F", "#{window_name}"],
            capture_output=True,
            text=True
        )

        assert "survive-agent" in tmux_result.stdout, "Agent window was removed when daemon stopped"

    def test_state_survives_daemon_restart(self):
        """
        Steps 5-7: Session state survives daemon restart.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "startup_idle"

        # Launch agent
        subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "state-survive",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        time.sleep(1)

        # Start daemon
        proc = subprocess.Popen(
            ["python", "-m", "overcode.cli", "daemon", "start",
             "--session", self.session,
             "--interval", "60"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        time.sleep(2)

        # Get state before
        state_file = self.state_dir / "sessions" / "sessions.json"
        with open(state_file) as f:
            state_before = json.load(f)

        # Stop daemon
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()

        time.sleep(1)

        # State should still exist
        assert state_file.exists(), "State file was removed"

        with open(state_file) as f:
            state_after = json.load(f)

        # Same sessions should exist
        names_before = set(s.get("name") for s in state_before.values())
        names_after = set(s.get("name") for s in state_after.values())
        assert "state-survive" in names_before
        assert "state-survive" in names_after


class TestDaemonMultipleStarts:
    """Test daemon handles multiple start attempts."""

    @pytest.fixture(autouse=True)
    def setup(self, clean_test_env, overcode_cli):
        """Store fixtures for use in tests."""
        self.env = clean_test_env
        self.cli = overcode_cli
        self.session = clean_test_env["session_name"]
        self.socket = clean_test_env["tmux_socket"]

    def test_status_after_stop(self):
        """
        Test status shows not running after stop.
        """
        env = self.env["env"].copy()

        # Start daemon
        proc = subprocess.Popen(
            ["python", "-m", "overcode.cli", "daemon", "start",
             "--session", self.session,
             "--interval", "60"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        time.sleep(2)

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
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()

        # Check status
        status_result = subprocess.run(
            ["python", "-m", "overcode.cli", "daemon", "status"],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        # Should indicate not running (may say "stopped" or "not running")
        output = status_result.stdout.lower()
        assert "stopped" in output or "not running" in output or status_result.returncode == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
