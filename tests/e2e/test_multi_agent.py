"""
E2E Test 05: Multi-Agent Coordination

Verify multiple agents can run simultaneously with independent status tracking.

See: docs/e2e_tests/05_multi_agent.md
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


class TestMultiAgent:
    """Test multiple agents running simultaneously."""

    @pytest.fixture(autouse=True)
    def setup(self, clean_test_env, overcode_cli):
        """Store fixtures for use in tests."""
        self.env = clean_test_env
        self.cli = overcode_cli
        self.session = clean_test_env["session_name"]
        self.socket = clean_test_env["tmux_socket"]
        self.state_dir = clean_test_env["state_dir"]

    def _launch_agent(self, name: str, scenario: str):
        """Helper to launch an agent."""
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = scenario
        return subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", name,
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

    def test_launch_multiple_agents(self):
        """
        Steps 2-4: Launch 3 agents with different scenarios.
        """
        # Launch 3 agents
        result1 = self._launch_agent("agent-1", "task_complete")
        assert result1.returncode == 0, f"Agent 1 launch failed: {result1.stderr}"

        result2 = self._launch_agent("agent-2", "permission_bash")
        assert result2.returncode == 0, f"Agent 2 launch failed: {result2.stderr}"

        result3 = self._launch_agent("agent-3", "task_running")
        assert result3.returncode == 0, f"Agent 3 launch failed: {result3.stderr}"

        time.sleep(2)

        # Verify all in tmux
        tmux_result = subprocess.run(
            ["tmux", "-L", self.socket, "list-windows", "-t", self.session, "-F", "#{window_name}"],
            capture_output=True,
            text=True
        )

        assert "agent-1" in tmux_result.stdout, f"Agent 1 window missing: {tmux_result.stdout}"
        assert "agent-2" in tmux_result.stdout, f"Agent 2 window missing: {tmux_result.stdout}"
        assert "agent-3" in tmux_result.stdout, f"Agent 3 window missing: {tmux_result.stdout}"

    def test_all_registered_in_state(self):
        """
        Step 5: Verify all agents registered in state file.
        """
        # Launch agents
        self._launch_agent("state-1", "startup_idle")
        self._launch_agent("state-2", "startup_idle")
        self._launch_agent("state-3", "startup_idle")

        time.sleep(1)

        # Check state file
        state_file = self.state_dir / "sessions" / "sessions.json"
        assert state_file.exists(), "State file not created"

        with open(state_file) as f:
            state = json.load(f)

        # Find all 3 sessions
        names = [s.get("name") for s in state.values()]
        assert "state-1" in names, f"state-1 not in state: {names}"
        assert "state-2" in names, f"state-2 not in state: {names}"
        assert "state-3" in names, f"state-3 not in state: {names}"

    def test_list_shows_all_agents(self):
        """
        Step 9: List command shows all agents.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "startup_idle"

        # Launch 2 agents
        self._launch_agent("list-1", "startup_idle")
        self._launch_agent("list-2", "startup_idle")

        time.sleep(1)

        # List
        result = subprocess.run(
            ["python", "-m", "overcode.cli", "list",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        assert result.returncode == 0, f"List failed: {result.stderr}"
        assert "list-1" in result.stdout, f"list-1 not in output: {result.stdout}"
        assert "list-2" in result.stdout, f"list-2 not in output: {result.stdout}"

    def test_kill_individual_agent(self):
        """
        Step 10: Kill one agent without affecting others.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "startup_idle"

        # Launch 2 agents
        self._launch_agent("keep", "startup_idle")
        self._launch_agent("kill", "startup_idle")

        time.sleep(1)

        # Kill one
        kill_result = subprocess.run(
            ["python", "-m", "overcode.cli", "kill", "kill",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        assert kill_result.returncode == 0, f"Kill failed: {kill_result.stderr}"

        time.sleep(0.5)

        # Verify one remains
        tmux_result = subprocess.run(
            ["tmux", "-L", self.socket, "list-windows", "-t", self.session, "-F", "#{window_name}"],
            capture_output=True,
            text=True
        )

        assert "keep" in tmux_result.stdout, "Kept agent was removed"
        assert "kill" not in tmux_result.stdout, "Killed agent still exists"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
