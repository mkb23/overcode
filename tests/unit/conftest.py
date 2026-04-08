"""
Unit test configuration for Overcode.

This module provides fixtures for unit tests that need isolated state directories.
"""

import os
import pytest
import tempfile
import shutil
from pathlib import Path

from tests.daemon_test_utils import stop_daemons_in_state_dir


# Test classes that actually mount Textual apps or start daemons and need
# an isolated OVERCODE_STATE_DIR with daemon cleanup on teardown.
_CLASSES_NEEDING_ISOLATION = frozenset({
    "TestSupervisorTUIPilot",
    "TestHelpOverlayPilot",
    "TestCommandBarWidget",
    "TestCommandBarIntegration",
    "TestCommandBarWithSessions",
    "TestUniqueAgentName",
})


@pytest.fixture(autouse=True)
def isolated_state_dir(request):
    """Isolate state directory for tests that mount TUI apps or start daemons.

    Only activates for test classes listed in _CLASSES_NEEDING_ISOLATION.
    Pure function tests in the same files are not affected.
    """
    cls_name = request.node.cls.__name__ if request.node.cls else ""
    if cls_name not in _CLASSES_NEEDING_ISOLATION:
        yield
        return

    # Create temp directory for state
    state_dir = tempfile.mkdtemp(prefix="overcode-unit-test-")

    # Save original value
    orig_state_dir = os.environ.get("OVERCODE_STATE_DIR")

    # Set environment variable so child processes inherit it
    os.environ["OVERCODE_STATE_DIR"] = state_dir

    try:
        yield state_dir
    finally:
        # Only run expensive daemon cleanup if PID files were actually created
        state_path = Path(state_dir)
        has_pid_files = any(state_path.rglob("*.pid")) if state_path.exists() else False

        if has_pid_files:
            stop_daemons_in_state_dir(state_dir)
            import time
            time.sleep(0.3)
            stop_daemons_in_state_dir(state_dir)

        # Remove temp state directory
        shutil.rmtree(state_dir, ignore_errors=True)

        # Restore original environment
        if orig_state_dir is None:
            os.environ.pop("OVERCODE_STATE_DIR", None)
        else:
            os.environ["OVERCODE_STATE_DIR"] = orig_state_dir
