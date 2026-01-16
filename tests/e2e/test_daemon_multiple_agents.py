"""
E2E Test 06: Daemon with Multiple Non-Green Agents

Verify daemon can process multiple agents needing attention.

See: docs/e2e_tests/06_daemon_multiple_agents.md

Note: Full daemon-claude interaction requires complex mock setup.
This tests basic multi-agent state management with daemon.
"""

import pytest
import time
import subprocess
import json
from pathlib import Path

from conftest import (
    TEST_TMUX_SOCKET,
)


class TestDaemonMultipleAgents:
    """Test daemon with multiple agents."""

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

    def test_launch_multiple_permission_agents(self):
        """
        Step 2: Launch 3 agents all needing attention.
        """
        # Launch 3 agents with permission scenario
        self._launch_agent("perm-1", "permission_bash")
        self._launch_agent("perm-2", "permission_bash")
        self._launch_agent("perm-3", "permission_bash")

        time.sleep(2)

        # Verify all in state
        state_file = self.state_dir / "sessions" / "sessions.json"
        assert state_file.exists(), "State file not created"

        with open(state_file) as f:
            state = json.load(f)

        names = [s.get("name") for s in state.values()]
        assert len([n for n in names if n and n.startswith("perm-")]) == 3, \
            f"Not all 3 permission agents found: {names}"

    def test_instruct_multiple_agents(self):
        """
        Step 3: Set standing instructions for multiple agents.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "startup_idle"

        # Launch agents
        self._launch_agent("inst-1", "startup_idle")
        self._launch_agent("inst-2", "startup_idle")

        time.sleep(1)

        # Set instructions for each
        for name in ["inst-1", "inst-2"]:
            result = subprocess.run(
                ["python", "-m", "overcode.cli", "instruct",
                 name, f"Instructions for {name}",
                 "--session", self.session],
                capture_output=True,
                text=True,
                timeout=5,
                env=env,
                cwd=Path(__file__).parent.parent.parent,
            )
            assert result.returncode == 0, f"Instruct {name} failed: {result.stderr}"

        # Verify in state
        state_file = self.state_dir / "sessions" / "sessions.json"
        with open(state_file) as f:
            state = json.load(f)

        for session_id, s in state.items():
            if s.get("name") in ["inst-1", "inst-2"]:
                assert "Instructions for" in s.get("standing_instructions", ""), \
                    f"Instructions not saved for {s.get('name')}: {s}"


class TestAgentStateTracking:
    """Test that agent states are tracked correctly with multiple agents."""

    @pytest.fixture(autouse=True)
    def setup(self, clean_test_env, overcode_cli):
        """Store fixtures for use in tests."""
        self.env = clean_test_env
        self.cli = overcode_cli
        self.session = clean_test_env["session_name"]
        self.socket = clean_test_env["tmux_socket"]
        self.state_dir = clean_test_env["state_dir"]

    def test_each_agent_tracked_independently(self):
        """
        Verify each agent has its own state entry.
        """
        env = self.env["env"].copy()

        # Launch with different scenarios
        for i, scenario in enumerate(["startup_idle", "task_complete", "task_running"], 1):
            env["MOCK_SCENARIO"] = scenario
            subprocess.run(
                ["python", "-m", "overcode.cli", "launch",
                 "--name", f"track-{i}",
                 "--session", self.session],
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
                cwd=Path(__file__).parent.parent.parent,
            )

        time.sleep(1)

        # Verify each has unique entry
        state_file = self.state_dir / "sessions" / "sessions.json"
        with open(state_file) as f:
            state = json.load(f)

        names = [s.get("name") for s in state.values()]
        assert "track-1" in names
        assert "track-2" in names
        assert "track-3" in names

        # Verify unique IDs
        ids = list(state.keys())
        assert len(ids) == len(set(ids)), "Duplicate session IDs found"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
