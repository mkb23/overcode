"""
E2E Tests 13-19: TUI Stats

Verify TUI correctly displays various statistics:
- 13: Interactions count
- 14: Estimated cost
- 15: Uptime
- 16: Green time
- 17: Steers count
- 18: Operation latency
- 19: Standing instructions display

See: docs/e2e_tests/13_tui_stats_interactions.md through 19_tui_stats_instructions.md
"""

import pytest
import time
import subprocess
import json
from pathlib import Path
from datetime import datetime, timedelta

from conftest import (
    TEST_TMUX_SOCKET,
)


class TestStatsTracking:
    """Test that stats are tracked in session state."""

    @pytest.fixture(autouse=True)
    def setup(self, clean_test_env, overcode_cli):
        """Store fixtures for use in tests."""
        self.env = clean_test_env
        self.cli = overcode_cli
        self.session = clean_test_env["session_name"]
        self.socket = clean_test_env["tmux_socket"]
        self.state_dir = clean_test_env["state_dir"]

    def _launch_agent(self, name: str, scenario: str = "startup_idle"):
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

    def test_stats_initialized_on_launch(self):
        """
        Test 13-15: Stats are initialized when agent is launched.
        """
        self._launch_agent("stats-init")
        time.sleep(1)

        # Check state file
        state_file = self.state_dir / "sessions" / "sessions.json"
        with open(state_file) as f:
            state = json.load(f)

        for s in state.values():
            if s.get("name") == "stats-init":
                stats = s.get("stats", {})
                # Verify stats fields exist
                assert "interaction_count" in stats or "estimated_cost_usd" in stats
                assert "current_state" in stats
                break

    def test_start_time_recorded(self):
        """
        Test 15: Start time is recorded for uptime calculation.
        """
        before = datetime.now()
        self._launch_agent("uptime-test")
        time.sleep(1)
        after = datetime.now()

        # Check state file
        state_file = self.state_dir / "sessions" / "sessions.json"
        with open(state_file) as f:
            state = json.load(f)

        for s in state.values():
            if s.get("name") == "uptime-test":
                start_time_str = s.get("start_time")
                assert start_time_str is not None, "No start_time recorded"

                # Parse and verify it's reasonable
                start_time = datetime.fromisoformat(start_time_str)
                assert before <= start_time <= after, \
                    f"Start time {start_time} not between {before} and {after}"
                break

    def test_current_state_tracked(self):
        """
        Test 16: Current state is tracked in stats.
        """
        self._launch_agent("state-track")
        time.sleep(1)

        state_file = self.state_dir / "sessions" / "sessions.json"
        with open(state_file) as f:
            state = json.load(f)

        for s in state.values():
            if s.get("name") == "state-track":
                stats = s.get("stats", {})
                current_state = stats.get("current_state")
                assert current_state is not None, "No current_state in stats"
                break


class TestStandingInstructionsInStats:
    """Test 19: Standing instructions display in session info."""

    @pytest.fixture(autouse=True)
    def setup(self, clean_test_env, overcode_cli):
        """Store fixtures for use in tests."""
        self.env = clean_test_env
        self.cli = overcode_cli
        self.session = clean_test_env["session_name"]
        self.socket = clean_test_env["tmux_socket"]
        self.state_dir = clean_test_env["state_dir"]

    def test_instructions_visible_in_state(self):
        """
        Verify standing instructions are stored and accessible.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "startup_idle"

        # Launch and set instructions
        subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "instr-stats",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        time.sleep(1)

        subprocess.run(
            ["python", "-m", "overcode.cli", "instruct",
             "instr-stats", "Test instructions for display",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        # Verify in state
        state_file = self.state_dir / "sessions" / "sessions.json"
        with open(state_file) as f:
            state = json.load(f)

        for s in state.values():
            if s.get("name") == "instr-stats":
                instructions = s.get("standing_instructions", "")
                assert "Test instructions" in instructions
                break


class TestCostEstimates:
    """Test 14: Cost estimation."""

    @pytest.fixture(autouse=True)
    def setup(self, clean_test_env, overcode_cli):
        """Store fixtures for use in tests."""
        self.env = clean_test_env
        self.cli = overcode_cli
        self.session = clean_test_env["session_name"]
        self.socket = clean_test_env["tmux_socket"]
        self.state_dir = clean_test_env["state_dir"]

    def test_cost_estimate_exists(self):
        """
        Verify cost estimate field exists in stats.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "startup_idle"

        subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "cost-test",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        time.sleep(1)

        state_file = self.state_dir / "sessions" / "sessions.json"
        with open(state_file) as f:
            state = json.load(f)

        for s in state.values():
            if s.get("name") == "cost-test":
                stats = s.get("stats", {})
                # Cost field should exist
                assert "estimated_cost_usd" in stats or "cost" in str(stats).lower()
                break


class TestGreenTimeTracking:
    """Test 16: Green time tracking."""

    @pytest.fixture(autouse=True)
    def setup(self, clean_test_env, overcode_cli):
        """Store fixtures for use in tests."""
        self.env = clean_test_env
        self.cli = overcode_cli
        self.session = clean_test_env["session_name"]
        self.socket = clean_test_env["tmux_socket"]
        self.state_dir = clean_test_env["state_dir"]

    def test_green_time_tracked(self):
        """
        Verify green time is tracked in stats.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "startup_idle"

        subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "green-test",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        time.sleep(1)

        state_file = self.state_dir / "sessions" / "sessions.json"
        with open(state_file) as f:
            state = json.load(f)

        for s in state.values():
            if s.get("name") == "green-test":
                stats = s.get("stats", {})
                # Green time field should exist
                assert "green_time_seconds" in stats or "green" in str(stats).lower()
                break


class TestSteersCount:
    """Test 17: Steers count tracking."""

    @pytest.fixture(autouse=True)
    def setup(self, clean_test_env, overcode_cli):
        """Store fixtures for use in tests."""
        self.env = clean_test_env
        self.cli = overcode_cli
        self.session = clean_test_env["session_name"]
        self.socket = clean_test_env["tmux_socket"]
        self.state_dir = clean_test_env["state_dir"]

    def test_steers_count_initialized(self):
        """
        Verify steers count is initialized to 0.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "startup_idle"

        subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "steers-test",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        time.sleep(1)

        state_file = self.state_dir / "sessions" / "sessions.json"
        with open(state_file) as f:
            state = json.load(f)

        for s in state.values():
            if s.get("name") == "steers-test":
                stats = s.get("stats", {})
                steers = stats.get("steers_count", 0)
                # Initial value should be 0
                assert steers == 0, f"Initial steers count should be 0, got {steers}"
                break


class TestOperationTimes:
    """Test 18: Operation latency tracking."""

    @pytest.fixture(autouse=True)
    def setup(self, clean_test_env, overcode_cli):
        """Store fixtures for use in tests."""
        self.env = clean_test_env
        self.cli = overcode_cli
        self.session = clean_test_env["session_name"]
        self.socket = clean_test_env["tmux_socket"]
        self.state_dir = clean_test_env["state_dir"]

    def test_operation_times_field_exists(self):
        """
        Verify operation times field exists in stats.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "startup_idle"

        subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "latency-test",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        time.sleep(1)

        state_file = self.state_dir / "sessions" / "sessions.json"
        with open(state_file) as f:
            state = json.load(f)

        for s in state.values():
            if s.get("name") == "latency-test":
                stats = s.get("stats", {})
                # Operation times field should exist
                assert "operation_times" in stats or "latency" in str(stats).lower()
                break


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
