"""
E2E Test 10: Standing Instructions Flow

Verify standing instructions are persisted and can be updated.

See: docs/e2e_tests/10_standing_instructions.md
"""

import pytest
import time
import subprocess
import json
from pathlib import Path

from conftest import (
    TEST_TMUX_SOCKET,
)


class TestStandingInstructions:
    """Test standing instructions persistence and updates."""

    @pytest.fixture(autouse=True)
    def setup(self, clean_test_env, overcode_cli):
        """Store fixtures for use in tests."""
        self.env = clean_test_env
        self.cli = overcode_cli
        self.session = clean_test_env["session_name"]
        self.socket = clean_test_env["tmux_socket"]
        self.state_dir = clean_test_env["state_dir"]

    def _launch_agent(self, name: str):
        """Helper to launch an agent."""
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "startup_idle"
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

    def test_initial_instructions_empty_or_default(self):
        """
        Steps 2-3: Check initial instructions state.
        """
        self._launch_agent("init-agent")
        time.sleep(1)

        # Check state
        state_file = self.state_dir / "sessions" / "sessions.json"
        with open(state_file) as f:
            state = json.load(f)

        for s in state.values():
            if s.get("name") == "init-agent":
                # May have default instructions or be empty
                instructions = s.get("standing_instructions", "")
                # Just verify the field exists
                assert "standing_instructions" in s
                break

    def test_set_standing_instructions(self):
        """
        Steps 4-5: Set and verify standing instructions.
        """
        env = self.env["env"].copy()

        self._launch_agent("set-agent")
        time.sleep(1)

        # Set instructions
        result = subprocess.run(
            ["python", "-m", "overcode.cli", "instruct",
             "set-agent", "Always approve file reads. Never approve file writes.",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        assert result.returncode == 0, f"Instruct failed: {result.stderr}"

        # Verify in state
        state_file = self.state_dir / "sessions" / "sessions.json"
        with open(state_file) as f:
            state = json.load(f)

        for s in state.values():
            if s.get("name") == "set-agent":
                assert "Always approve file reads" in s.get("standing_instructions", ""), \
                    f"Instructions not saved: {s}"
                break

    def test_update_standing_instructions(self):
        """
        Step 10: Update standing instructions.
        """
        env = self.env["env"].copy()

        self._launch_agent("update-agent")
        time.sleep(1)

        # Set initial instructions
        subprocess.run(
            ["python", "-m", "overcode.cli", "instruct",
             "update-agent", "Initial instructions",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        # Update instructions
        result = subprocess.run(
            ["python", "-m", "overcode.cli", "instruct",
             "update-agent", "New updated instructions",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        assert result.returncode == 0, f"Update failed: {result.stderr}"

        # Verify update
        state_file = self.state_dir / "sessions" / "sessions.json"
        with open(state_file) as f:
            state = json.load(f)

        for s in state.values():
            if s.get("name") == "update-agent":
                instructions = s.get("standing_instructions", "")
                assert "New updated" in instructions, f"Update not applied: {instructions}"
                assert "Initial" not in instructions or "New updated" in instructions
                break


class TestInstructCommand:
    """Test the instruct CLI command."""

    @pytest.fixture(autouse=True)
    def setup(self, clean_test_env, overcode_cli):
        """Store fixtures for use in tests."""
        self.env = clean_test_env
        self.cli = overcode_cli
        self.session = clean_test_env["session_name"]
        self.socket = clean_test_env["tmux_socket"]
        self.state_dir = clean_test_env["state_dir"]

    def test_instruct_requires_agent_name(self):
        """
        Verify instruct command requires agent name.
        """
        env = self.env["env"].copy()

        result = subprocess.run(
            ["python", "-m", "overcode.cli", "instruct"],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        # Should fail with usage error
        assert result.returncode != 0

    def test_instruct_nonexistent_agent(self):
        """
        Verify instruct handles nonexistent agent gracefully.
        """
        env = self.env["env"].copy()

        result = subprocess.run(
            ["python", "-m", "overcode.cli", "instruct",
             "nonexistent-agent", "Some instructions",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        # Should fail but not crash
        assert result.returncode != 0 or "not found" in result.stdout.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
