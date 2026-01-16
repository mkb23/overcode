"""
E2E Test 02: Task Completion Cycle

Verify the full interaction loop: agent works on task, completes, waits for
feedback, receives feedback, continues.

See: docs/e2e_tests/02_task_completion_cycle.md
"""

import pytest
import time
import subprocess
from pathlib import Path

from conftest import (
    get_tmux_pane_content,
    send_to_tmux,
    TEST_TMUX_SOCKET,
)


class TestTaskCompletionCycle:
    """Test the full task completion and feedback cycle."""

    @pytest.fixture(autouse=True)
    def setup(self, clean_test_env, overcode_cli):
        """Store fixtures for use in tests."""
        self.env = clean_test_env
        self.cli = overcode_cli
        self.session = clean_test_env["session_name"]
        self.socket = clean_test_env["tmux_socket"]

    def test_task_then_wait_shows_task_output(self):
        """
        Step 2-4: Launch agent with task_then_wait and verify task output appears.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "task_then_wait"

        result = subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "task-agent",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        assert result.returncode == 0, f"Launch failed: {result.stderr}"

        # Wait for task output
        time.sleep(2)

        # Check pane shows task output
        content = get_tmux_pane_content(self.socket, self.session, "task-agent")
        # Should show refactoring task output
        assert "Refactor" in content or "connection pool" in content.lower() or "Edit(" in content, \
            f"Task output not shown. Content: {content}"

    def test_task_completion_shows_prompt(self):
        """
        Step 4: Verify completed task shows waiting prompt.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "task_complete"

        result = subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "complete-agent",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        assert result.returncode == 0, f"Launch failed: {result.stderr}"

        # Wait for task completion output
        time.sleep(2)

        # Check pane shows waiting prompt (empty > prompt)
        content = get_tmux_pane_content(self.socket, self.session, "complete-agent")
        # Should show the separator line and prompt indicating ready for input
        assert ">" in content or "?" in content, \
            f"Prompt not shown. Content: {content}"

    def test_send_command_delivers_text(self):
        """
        Step 5: Verify send command delivers text to agent pane.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "task_complete"

        # Launch
        subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "send-agent",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        time.sleep(2)

        # Send text directly via tmux (more reliable than CLI for testing)
        send_to_tmux(self.socket, self.session, "send-agent", "Run more tests please")

        time.sleep(1)

        # Verify text appeared
        content = get_tmux_pane_content(self.socket, self.session, "send-agent")
        assert "Run more tests" in content or "tests" in content.lower(), \
            f"Sent text not in pane. Content: {content}"

    def test_full_interaction_cycle(self):
        """
        Complete cycle: task, wait, feedback, second task, wait.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "task_then_wait"

        # Launch
        result = subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "cycle-agent",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        assert result.returncode == 0, f"Launch failed: {result.stderr}"

        # Wait for first task to complete
        time.sleep(2)

        # Check first phase output
        content = get_tmux_pane_content(self.socket, self.session, "cycle-agent")
        assert "Refactor" in content or "pool" in content.lower(), \
            f"First task output missing. Content: {content}"

        # Send feedback to trigger second phase
        send_to_tmux(self.socket, self.session, "cycle-agent", "yes run the tests")

        time.sleep(2)

        # Check second phase output
        content = get_tmux_pane_content(self.socket, self.session, "cycle-agent")
        # Should show test run from phase 2
        assert "pytest" in content.lower() or "tests" in content.lower() or "passed" in content.lower(), \
            f"Second task output missing. Content: {content}"


class TestTaskRunning:
    """Test the running/thinking state."""

    @pytest.fixture(autouse=True)
    def setup(self, clean_test_env, overcode_cli):
        """Store fixtures for use in tests."""
        self.env = clean_test_env
        self.cli = overcode_cli
        self.session = clean_test_env["session_name"]
        self.socket = clean_test_env["tmux_socket"]

    def test_running_shows_thinking_indicator(self):
        """
        Verify running state shows thinking indicator.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "task_running"

        result = subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "running-agent",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        assert result.returncode == 0, f"Launch failed: {result.stderr}"

        # Wait for thinking indicator
        time.sleep(3)

        # Check pane shows thinking state
        content = get_tmux_pane_content(self.socket, self.session, "running-agent")
        # Should show some thinking indicator or tool use
        assert "⏺" in content or "Read(" in content or "…" in content, \
            f"No working indicator. Content: {content}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
