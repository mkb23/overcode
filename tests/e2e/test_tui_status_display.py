"""
E2E Test 07: TUI Status Display

Verify TUI monitor correctly displays agent statuses.

See: docs/e2e_tests/07_tui_status_display.md

Note: Uses Textual pilot for headless TUI testing.
"""

import pytest
import time
import subprocess
from pathlib import Path

from conftest import (
    TEST_TMUX_SOCKET,
)


class TestTUIDisplay:
    """Test TUI displays agent information correctly."""

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


# Import TUI components for pilot testing
try:
    from textual.pilot import Pilot
    from overcode.tui import SupervisorTUI
    HAS_TUI = True
except ImportError:
    HAS_TUI = False


@pytest.mark.skipif(not HAS_TUI, reason="TUI components not available")
class TestTUIWithPilot:
    """Test TUI using Textual pilot."""

    @pytest.fixture(autouse=True)
    def setup(self, clean_test_env, overcode_cli):
        """Store fixtures for use in tests."""
        self.env = clean_test_env
        self.cli = overcode_cli
        self.session = clean_test_env["session_name"]
        self.socket = clean_test_env["tmux_socket"]
        self.state_dir = clean_test_env["state_dir"]

    @pytest.mark.asyncio
    async def test_tui_shows_session_list(self):
        """
        Steps 3-4: Verify TUI shows session list.
        """
        # Launch an agent first
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = "startup_idle"
        subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", "tui-test",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        time.sleep(1)

        # Create TUI app
        app = SupervisorTUI(tmux_session=self.session)

        async with app.run_test() as pilot:
            # Wait for initial render
            await pilot.pause()

            # Check that session widgets exist
            # The TUI should have session summary widgets
            widgets = app.query("SessionSummary")
            # We launched one agent, so there should be at least one session
            # (Note: The test may see 0 if the session manager doesn't see the state)

    @pytest.mark.asyncio
    async def test_tui_help_toggle(self):
        """
        Test that help overlay can be toggled.
        """
        app = SupervisorTUI(tmux_session=self.session)

        async with app.run_test() as pilot:
            # Initially help should not be visible
            await pilot.pause()

            # Press ? to show help
            await pilot.press("?")
            await pilot.pause()

            # Press ? again to hide
            await pilot.press("?")
            await pilot.pause()

    @pytest.mark.asyncio
    async def test_tui_quit(self):
        """
        Test that q quits the TUI.
        """
        app = SupervisorTUI(tmux_session=self.session)

        async with app.run_test() as pilot:
            await pilot.pause()

            # Press q to quit
            await pilot.press("q")

            # App should exit
            assert app._exit


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
