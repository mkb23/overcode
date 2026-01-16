"""
Pytest configuration for overcode E2E tests

This module provides shared fixtures and configuration for all tests.
"""

import pytest
import subprocess
import os
from pathlib import Path


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
