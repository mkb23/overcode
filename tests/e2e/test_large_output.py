"""
E2E Test 11: Large Output Handling

Verify system handles agents with large output without hanging or crashing.

See: docs/e2e_tests/11_large_output.md
"""

import pytest
import time
import subprocess
from pathlib import Path

from conftest import (
    get_tmux_pane_content,
    TEST_TMUX_SOCKET,
)


class TestLargeOutput:
    """Test handling of large output from agents."""

    @pytest.fixture(autouse=True)
    def setup(self, clean_test_env, overcode_cli):
        """Store fixtures for use in tests."""
        self.env = clean_test_env
        self.cli = overcode_cli
        self.session = clean_test_env["session_name"]
        self.socket = clean_test_env["tmux_socket"]
        self.state_dir = clean_test_env["state_dir"]

    def test_pane_capture_works_with_content(self):
        """
        Step 6: Test pane capture with normal output.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "task_complete"

        # Launch agent
        subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "capture-test",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        time.sleep(2)

        # Capture pane content
        start = time.time()
        content = get_tmux_pane_content(self.socket, self.session, "capture-test", lines=100)
        elapsed = time.time() - start

        # Should complete quickly
        assert elapsed < 2.0, f"Pane capture took too long: {elapsed}s"

        # Should have content
        assert len(content) > 0, "Pane capture returned empty"

        # Content should be valid (no obvious corruption)
        assert "\x00" not in content, "Null bytes in captured content"

    def test_list_performance_with_multiple_agents(self):
        """
        Step 4: Test list command performance.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "startup_idle"

        # Launch several agents
        for i in range(3):
            subprocess.run(
                ["python", "-m", "overcode.cli", "launch",
                 "--name", f"perf-agent-{i}",
                 "--session", self.session],
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
                cwd=Path(__file__).parent.parent.parent,
            )

        time.sleep(1)

        # Time list command
        start = time.time()
        result = subprocess.run(
            ["python", "-m", "overcode.cli", "list",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )
        elapsed = time.time() - start

        assert result.returncode == 0, f"List failed: {result.stderr}"
        assert elapsed < 5.0, f"List took too long: {elapsed}s"

    def test_kill_with_scrollback(self):
        """
        Step 10: Test clean teardown with output history.
        """
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "task_complete"

        # Launch agent that produces output
        subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "scrollback-test",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        time.sleep(2)

        # Kill should work
        start = time.time()
        result = subprocess.run(
            ["python", "-m", "overcode.cli", "kill", "scrollback-test",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )
        elapsed = time.time() - start

        assert result.returncode == 0, f"Kill failed: {result.stderr}"
        assert elapsed < 3.0, f"Kill took too long: {elapsed}s"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
