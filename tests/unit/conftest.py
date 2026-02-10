"""
Unit test configuration for Overcode.

This module provides fixtures for unit tests that need isolated state directories.
"""

import os
import pytest
import tempfile
import shutil

from tests.daemon_test_utils import stop_daemons_in_state_dir


@pytest.fixture(autouse=True)
def isolated_state_dir(request):
    """Automatically isolate state directory for tests that use TUI or daemons.

    This fixture is auto-used but only activates for tests that might start
    daemons (e.g., TUI tests). It sets OVERCODE_STATE_DIR to a temp directory
    so any daemons started during tests don't pollute the user's ~/.overcode.

    The fixture checks if the test file contains certain markers that indicate
    it might start daemons.
    """
    # Only activate for tests that might start daemons
    test_file = str(request.fspath)
    needs_isolation = any(marker in test_file for marker in [
        "test_tui.py",
        "test_command_bar.py",
    ])

    if not needs_isolation:
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
        # Stop any daemons that were started during the test
        stop_daemons_in_state_dir(state_dir)

        # Wait a bit for daemons to fully exit
        import time
        time.sleep(0.5)

        # Retry cleanup - daemon might have written PID file after first attempt
        stop_daemons_in_state_dir(state_dir)

        # Remove temp state directory
        shutil.rmtree(state_dir, ignore_errors=True)

        # Restore original environment
        if orig_state_dir is None:
            os.environ.pop("OVERCODE_STATE_DIR", None)
        else:
            os.environ["OVERCODE_STATE_DIR"] = orig_state_dir
