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

# Model-metadata lookups resolve against the freshest models.dev catalog on
# the machine (~/.overcode/cache, opencode's cache) — tests must see the
# bundled snapshot only, so results don't depend on what the developer's
# host happens to have fetched. Tests of the local tiers delenv this.
os.environ.setdefault("OVERCODE_MODEL_METADATA_BUNDLED_ONLY", "1")


# Test classes that actually mount Textual apps or start daemons and need
# an isolated OVERCODE_STATE_DIR with daemon cleanup on teardown.
_CLASSES_NEEDING_ISOLATION = frozenset({
    "TestSupervisorTUIPilot",
    "TestHelpOverlayPilot",
    "TestCommandBarWidget",
    "TestCommandBarIntegration",
    "TestCommandBarWithSessions",
    "TestUniqueAgentName",
    "TestActivityPilot",
})


@pytest.fixture(autouse=True)
def no_real_daemons(monkeypatch):
    """Unit tests never start real daemons (#485).

    Mounting SupervisorTUI spawns a monitor daemon. The pilot tests finish
    before it writes its PID file, so isolated_state_dir's cleanup can miss
    it and it outlives the run. Tests that check the spawn patch it
    themselves, over this stub.
    """
    import overcode.pid_utils
    import overcode.tui

    def fake_spawn_daemon(args):
        return None

    monkeypatch.setattr(overcode.pid_utils, "spawn_daemon", fake_spawn_daemon)
    monkeypatch.setattr(overcode.tui, "spawn_daemon", fake_spawn_daemon)


@pytest.fixture(autouse=True)
def activity_log_in_tmp(monkeypatch, tmp_path_factory):
    """Unit tests never write the real usage log (#483).

    OVERCODE_ACTIVITY=0 (tests/conftest.py) is not enough on its own: some
    tests clear every OVERCODE_* variable. Tests that check the log's
    location patch get_activity_dir themselves, over this.
    """
    import overcode.activity_log
    d = tmp_path_factory.mktemp("activity")
    monkeypatch.setattr(overcode.activity_log, "get_activity_dir", lambda: d)


def _daemon_children() -> list[str]:
    """This process's child daemons, as "pid command" lines.

    spawn_daemon's reaper thread keeps a daemon our child while we run, so
    this only sees daemons this test run started.
    """
    import subprocess
    out = subprocess.run(
        ["pgrep", "-lf", "-P", str(os.getpid()), r"overcode\.(monitor|supervisor)_daemon"],
        capture_output=True, text=True,
    ).stdout
    return out.splitlines()


def pytest_sessionfinish(session, exitstatus):
    """Fail the run if a unit test left a real daemon behind (#485)."""
    leaked = _daemon_children()
    if not leaked:
        return
    import signal
    for line in leaked:
        try:
            os.kill(int(line.split()[0]), signal.SIGTERM)
        except (ValueError, OSError):
            pass
    print("\n\nUnit tests started real daemons (killed now):\n  " + "\n  ".join(leaked))
    session.exitstatus = pytest.ExitCode.TESTS_FAILED


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
