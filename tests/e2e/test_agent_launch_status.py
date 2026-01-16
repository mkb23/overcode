"""
E2E Test 01: Agent Launch and Status Detection

Verify that launching an agent creates the correct tmux window, registers
the session in state, and status detection works correctly.

See: docs/e2e_tests/01_agent_launch_and_status.md
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


class TestAgentLaunchAndStatus:
    """Test agent launch and status detection."""

    @pytest.fixture(autouse=True)
    def setup(self, clean_test_env, overcode_cli):
        """Store fixtures for use in tests."""
        self.env = clean_test_env
        self.cli = overcode_cli
        self.session = clean_test_env["session_name"]
        self.socket = clean_test_env["tmux_socket"]
        self.state_dir = clean_test_env["state_dir"]

    def test_launch_creates_tmux_window(self):
        """
        Step 2-3: Launch agent and verify tmux window is created.
        """
        # Set scenario
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "startup_idle"

        result = subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "test-agent-1",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        assert result.returncode == 0, f"Launch failed: {result.stderr}"

        # Wait for window to be created
        time.sleep(1)

        # Verify window exists
        tmux_result = subprocess.run(
            ["tmux", "-L", self.socket, "list-windows", "-t", self.session, "-F", "#{window_name}"],
            capture_output=True,
            text=True
        )
        assert tmux_result.returncode == 0, "Failed to list tmux windows"
        assert "test-agent-1" in tmux_result.stdout, f"Window not found. Windows: {tmux_result.stdout}"

    def test_launch_registers_session_state(self):
        """
        Step 4: Verify session is registered in state file.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "startup_idle"

        result = subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "state-agent",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        assert result.returncode == 0, f"Launch failed: {result.stderr}"

        time.sleep(1)

        # Check state file
        state_file = self.state_dir / "sessions" / "sessions.json"
        assert state_file.exists(), f"State file not created at {state_file}"

        with open(state_file) as f:
            state = json.load(f)

        # Find our session - state is a dict with UUID keys
        session_data = None
        for session_id, s in state.items():
            if s.get("name") == "state-agent":
                session_data = s
                break

        assert session_data is not None, f"Session not found in state: {state}"
        assert session_data["tmux_session"] == self.session
        assert "start_time" in session_data

    def test_list_shows_launched_agent(self):
        """
        Verify list command shows the launched agent.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "startup_idle"

        # Launch agent
        result = subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "list-test",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )
        assert result.returncode == 0, f"Launch failed: {result.stderr}"

        time.sleep(1)

        # List agents
        list_result = subprocess.run(
            ["python", "-m", "overcode.cli", "list",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )
        assert list_result.returncode == 0, f"List failed: {list_result.stderr}"
        assert "list-test" in list_result.stdout, f"Agent not in list: {list_result.stdout}"

    def test_kill_removes_agent(self):
        """
        Step 6: Verify kill removes window and updates state.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "startup_idle"

        # Launch
        subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "kill-test",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )
        time.sleep(1)

        # Verify launched
        tmux_result = subprocess.run(
            ["tmux", "-L", self.socket, "list-windows", "-t", self.session, "-F", "#{window_name}"],
            capture_output=True,
            text=True
        )
        assert "kill-test" in tmux_result.stdout, "Window wasn't created"

        # Kill
        kill_result = subprocess.run(
            ["python", "-m", "overcode.cli", "kill", "kill-test",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )
        assert kill_result.returncode == 0, f"Kill failed: {kill_result.stderr}"

        time.sleep(0.5)

        # Verify window removed
        tmux_result = subprocess.run(
            ["tmux", "-L", self.socket, "list-windows", "-t", self.session, "-F", "#{window_name}"],
            capture_output=True,
            text=True
        )
        assert "kill-test" not in tmux_result.stdout, f"Window still exists: {tmux_result.stdout}"

    def test_pane_shows_welcome_banner(self):
        """
        Verify the mock displays the welcome banner.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "startup_idle"

        subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "banner-test",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        time.sleep(2)

        # Check pane content
        content = get_tmux_pane_content(self.socket, self.session, "banner-test")
        assert "Claude Code" in content, f"Welcome banner not shown. Content: {content}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
