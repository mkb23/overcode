"""
E2E Test 03: Permission Prompt Handling

Verify that permission prompts are correctly detected and can be approved via send command.

See: docs/e2e_tests/03_permission_handling.md
"""

import pytest
import time
import subprocess
import json
from pathlib import Path

from conftest import (
    wait_for_status,
    get_tmux_pane_content,
    send_to_tmux,
    TEST_TMUX_SOCKET,
)


class TestPermissionHandling:
    """Test permission prompt detection and approval."""

    @pytest.fixture(autouse=True)
    def setup(self, clean_test_env, overcode_cli):
        """Store fixtures for use in tests."""
        self.env = clean_test_env
        self.cli = overcode_cli
        self.session = clean_test_env["session_name"]
        self.socket = clean_test_env["tmux_socket"]

    def test_permission_prompt_detected(self):
        """
        Step 2-4: Launch agent with permission scenario, verify detection.
        """
        # Launch agent with permission_bash scenario
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "permission_bash"

        result = subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "perm-agent",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        assert result.returncode == 0, f"Launch failed: {result.stderr}"

        # Wait for permission prompt state
        # The agent should reach "waiting_for_user" when at the permission prompt
        time.sleep(2)  # Give mock time to output permission prompt

        # Check pane content shows permission prompt
        content = get_tmux_pane_content(
            self.socket, self.session, "perm-agent"
        )
        assert "Bash(" in content or "Allow" in content, \
            f"Permission prompt not shown. Content: {content}"

    def test_permission_approval_flow(self):
        """
        Step 5-7: Approve permission and verify agent continues.
        """
        # Launch agent
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "permission_bash"

        result = subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "perm-agent-2",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )
        assert result.returncode == 0, f"Launch failed: {result.stderr}"

        # Wait for permission prompt
        time.sleep(2)

        # Verify at permission prompt
        content = get_tmux_pane_content(
            self.socket, self.session, "perm-agent-2"
        )
        # New Claude Code v2 format uses numbered menu
        assert "❯ 1. Yes" in content or "Do you want to proceed" in content, \
            f"Not at permission prompt. Content: {content}"

        # Send approval
        send_to_tmux(
            self.socket, self.session, "perm-agent-2", "y"
        )

        # Wait for continuation
        time.sleep(2)

        # Verify agent continued (should show completion message)
        content = get_tmux_pane_content(
            self.socket, self.session, "perm-agent-2"
        )
        assert "granted" in content.lower() or "complete" in content.lower(), \
            f"Agent didn't continue after approval. Content: {content}"

    def test_permission_denial_flow(self):
        """
        Test that denying permission also works correctly.
        """
        # Launch agent
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "permission_bash"

        result = subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "perm-agent-3",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )
        assert result.returncode == 0, f"Launch failed: {result.stderr}"

        # Wait for permission prompt
        time.sleep(2)

        # Send denial
        send_to_tmux(
            self.socket, self.session, "perm-agent-3", "n"
        )

        # Wait for response
        time.sleep(2)

        # Verify agent handled denial
        content = get_tmux_pane_content(
            self.socket, self.session, "perm-agent-3"
        )
        assert "denied" in content.lower() or "another" in content.lower(), \
            f"Agent didn't handle denial. Content: {content}"


class TestPermissionViaOvercode:
    """Test permission handling via overcode send command."""

    @pytest.fixture(autouse=True)
    def setup(self, clean_test_env, overcode_cli):
        """Store fixtures for use in tests."""
        self.env = clean_test_env
        self.cli = overcode_cli
        self.session = clean_test_env["session_name"]
        self.socket = clean_test_env["tmux_socket"]

    def test_send_command_approves_permission(self):
        """
        Test using 'overcode send' to approve permission.
        """
        # Launch agent
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "permission_bash"

        result = subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "send-test",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )
        assert result.returncode == 0, f"Launch failed: {result.stderr}"

        # Wait for permission prompt
        time.sleep(2)

        # Use overcode send to approve
        result = subprocess.run(
            ["python", "-m", "overcode.cli", "send",
             "send-test", "y",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )
        assert result.returncode == 0, f"Send failed: {result.stderr}"

        # Verify approval took effect
        time.sleep(2)
        content = get_tmux_pane_content(
            self.socket, self.session, "send-test"
        )
        assert "granted" in content.lower() or "complete" in content.lower(), \
            f"Permission not approved via send. Content: {content}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
