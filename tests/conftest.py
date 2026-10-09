"""
Pytest configuration for overcode E2E tests

This module provides shared fixtures and configuration for all tests.
"""

import pytest
import shutil
import subprocess
import os
import tempfile
from pathlib import Path

# Tests never write the user's usage log (#483): pilot-driven keys and test
# CLI calls would otherwise count as the developer's own use. Subprocesses
# (the CLI, the TUI under tmux) inherit this. Tests of the recorder pass
# enabled=True or a temp directory explicitly.
os.environ["OVERCODE_ACTIVITY"] = "0"

# Tests never touch the live fleet: the real ~/.overcode, the user's tmux
# server, or the agent this pytest happens to run inside. Isolation is on
# by default for every test and every subprocess it starts, not opt-in per
# test class. Before this, pilot tests that mounted SupervisorTUI without
# opting in wrote into ~/.overcode/sessions/test-pilot, monitor daemons they
# spawned polled the user's tmux server for weeks, and pressing `e` in a
# pilot test launched a real Claude overagent into a `test-pilot` session on
# the user's tmux server once per run.
#
# Set at import, before any test module imports overcode, so module-level
# paths resolve here too. Tiers that need their own values (e2e's private
# socket, per-test state dirs) still override these per test.
RUN_DIR = Path(tempfile.mkdtemp(prefix="overcode-pytest-"))
os.environ["OVERCODE_DIR"] = str(RUN_DIR / "overcode")
os.environ["OVERCODE_STATE_DIR"] = str(RUN_DIR / "overcode" / "sessions")
os.environ["OVERCODE_TMUX_SOCKET"] = f"overcode-pytest-{os.getpid()}"
# Inside tmux (the usual case: overcode agents run tests), TMUX points every
# bare `tmux` call at the user's server, and the agent's own identity would
# make CLI code act as, or on, that agent.
for _var in (
    "TMUX", "TMUX_PANE",
    "OVERCODE_SESSION_NAME", "OVERCODE_SESSION_ID", "OVERCODE_TMUX_SESSION",
    "OVERCODE_PARENT_NAME", "OVERCODE_PARENT_SESSION_ID",
):
    os.environ.pop(_var, None)
PRIVATE_TMUX_SOCKET = os.environ["OVERCODE_TMUX_SOCKET"]


def pytest_unconfigure(config):
    """Stop this run's private tmux server and remove its state."""
    # Whatever a test left on the private server (sessions, panes, an agent
    # CLI a test launched by mistake) dies with it.
    try:
        subprocess.run(["tmux", "-L", PRIVATE_TMUX_SOCKET, "kill-server"],
                       capture_output=True, timeout=5)
    except (subprocess.SubprocessError, OSError):
        pass
    shutil.rmtree(RUN_DIR, ignore_errors=True)


def pytest_configure(config):
    """Configure pytest for E2E tests"""
    # Register custom markers
    config.addinivalue_line(
        "markers", "e2e: mark test as end-to-end integration test (slow)"
    )
    config.addinivalue_line(
        "markers", "requires_tmux: mark test as requiring tmux"
    )
    config.addinivalue_line(
        "markers", "requires_claude: mark test as requiring Claude API access"
    )


@pytest.fixture(scope="session")
def check_prerequisites():
    """Check that required tools are available (for E2E tests only)"""
    # Check for tmux
    try:
        result = subprocess.run(
            ["tmux", "-V"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode != 0:
            pytest.skip("tmux not available")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pytest.skip("tmux not installed or not in PATH")

    # Check for claude command
    try:
        result = subprocess.run(
            ["which", "claude"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode != 0:
            pytest.skip("claude command not available")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pytest.skip("claude command not found")

    print("\n✓ Prerequisites check passed (tmux and claude available)")


@pytest.fixture(scope="session")
def test_data_dir(tmp_path_factory):
    """Create a temporary directory for test data"""
    return tmp_path_factory.mktemp("overcode_test_data")


@pytest.fixture
def print_test_header(request):
    """Print a header before each test (for E2E tests, not auto-used for unit tests)"""
    print("\n" + "=" * 70)
    print(f"Running: {request.node.name}")
    print("=" * 70)
    yield
    print("\n" + "=" * 70)
    print(f"Finished: {request.node.name}")
    print("=" * 70)
