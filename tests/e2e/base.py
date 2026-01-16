"""
Base class for E2E tests.

Provides common setup, teardown, and helper methods for E2E tests.
"""

import json
import subprocess
import time
from pathlib import Path
from typing import Optional

import pytest

from conftest import (
    TEST_TMUX_SOCKET,
    get_tmux_pane_content,
    send_to_tmux,
)


class E2ETestBase:
    """Base class for E2E tests with common setup and helpers."""

    # Subclasses can override these
    DEFAULT_AGENT_NAME = "test-agent"
    DEFAULT_SCENARIO = "startup_idle"
    LAUNCH_WAIT = 1.0  # seconds to wait after launch
    COMMAND_WAIT = 0.5  # seconds to wait after commands

    @pytest.fixture(autouse=True)
    def setup(self, clean_test_env, overcode_cli):
        """Store fixtures for use in tests."""
        self.env = clean_test_env
        self.cli = overcode_cli
        self.session = clean_test_env["session_name"]
        self.socket = clean_test_env["tmux_socket"]
        self.state_dir = clean_test_env["state_dir"]

    def _get_env(self, scenario: Optional[str] = None) -> dict:
        """Get environment with optional mock scenario override."""
        env = self.env["env"].copy()
        if scenario:
            env["MOCK_SCENARIO"] = scenario
        elif self.DEFAULT_SCENARIO:
            env["MOCK_SCENARIO"] = self.DEFAULT_SCENARIO
        return env

    def launch_agent(
        self,
        name: Optional[str] = None,
        scenario: Optional[str] = None,
        wait: bool = True,
        extra_args: Optional[list] = None,
    ) -> subprocess.CompletedProcess:
        """Launch an agent with the given name and scenario.

        Args:
            name: Agent name (defaults to DEFAULT_AGENT_NAME)
            scenario: Mock scenario (defaults to DEFAULT_SCENARIO)
            wait: Whether to wait after launch
            extra_args: Additional CLI arguments

        Returns:
            subprocess.CompletedProcess result
        """
        name = name or self.DEFAULT_AGENT_NAME
        env = self._get_env(scenario)

        cmd = [
            "python", "-m", "overcode.cli", "launch",
            "--name", name,
            "--session", self.session,
        ]
        if extra_args:
            cmd.extend(extra_args)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

        if wait:
            time.sleep(self.LAUNCH_WAIT)

        return result

    def kill_agent(self, name: str) -> subprocess.CompletedProcess:
        """Kill an agent by name.

        Args:
            name: Agent name to kill

        Returns:
            subprocess.CompletedProcess result
        """
        env = self._get_env()
        return subprocess.run(
            ["python", "-m", "overcode.cli", "kill", name,
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

    def send_to_agent(self, name: str, text: str) -> subprocess.CompletedProcess:
        """Send text/keys to an agent.

        Args:
            name: Agent name
            text: Text to send

        Returns:
            subprocess.CompletedProcess result
        """
        env = self._get_env()
        result = subprocess.run(
            ["python", "-m", "overcode.cli", "send", name, text,
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )
        time.sleep(self.COMMAND_WAIT)
        return result

    def list_agents(self) -> subprocess.CompletedProcess:
        """List all agents.

        Returns:
            subprocess.CompletedProcess result
        """
        env = self._get_env()
        return subprocess.run(
            ["python", "-m", "overcode.cli", "list",
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

    def set_instructions(self, name: str, instructions: str) -> subprocess.CompletedProcess:
        """Set standing instructions for an agent.

        Args:
            name: Agent name
            instructions: Standing instructions text

        Returns:
            subprocess.CompletedProcess result
        """
        env = self._get_env()
        return subprocess.run(
            ["python", "-m", "overcode.cli", "instruct",
             name, instructions,
             "--session", self.session],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

    def get_pane_content(self, window: str, lines: int = 50) -> str:
        """Get tmux pane content for an agent.

        Args:
            window: Window name/number
            lines: Number of lines to capture

        Returns:
            Pane content string
        """
        return get_tmux_pane_content(self.socket, self.session, window, lines)

    def send_keys_to_pane(self, window: str, text: str):
        """Send raw keys to a tmux pane.

        Args:
            window: Window name/number
            text: Text to send
        """
        send_to_tmux(self.socket, self.session, window, text)

    def load_state(self) -> dict:
        """Load the session state file.

        Returns:
            Session state as dict
        """
        state_file = self.state_dir / "sessions" / "sessions.json"
        with open(state_file) as f:
            return json.load(f)

    def get_session_by_name(self, name: str) -> Optional[dict]:
        """Get session data by name.

        Args:
            name: Agent name

        Returns:
            Session dict or None if not found
        """
        state = self.load_state()
        for session in state.values():
            if session.get("name") == name:
                return session
        return None

    def wait_for_condition(
        self,
        condition_fn,
        timeout: float = 10.0,
        poll_interval: float = 0.5,
        message: str = "Condition not met"
    ) -> bool:
        """Wait for a condition to become true.

        Args:
            condition_fn: Callable that returns True when condition is met
            timeout: Maximum wait time in seconds
            poll_interval: Time between checks
            message: Error message if timeout

        Returns:
            True if condition met, False if timeout

        Raises:
            AssertionError: If timeout and fail_on_timeout is True
        """
        start = time.time()
        while time.time() - start < timeout:
            try:
                if condition_fn():
                    return True
            except Exception:
                pass
            time.sleep(poll_interval)
        return False

    def assert_agent_exists(self, name: str, msg: str = None):
        """Assert that an agent exists in the session state.

        Args:
            name: Agent name
            msg: Optional assertion message
        """
        session = self.get_session_by_name(name)
        assert session is not None, msg or f"Agent '{name}' not found in state"

    def assert_agent_not_exists(self, name: str, msg: str = None):
        """Assert that an agent does not exist in the session state.

        Args:
            name: Agent name
            msg: Optional assertion message
        """
        session = self.get_session_by_name(name)
        assert session is None, msg or f"Agent '{name}' unexpectedly exists in state"
