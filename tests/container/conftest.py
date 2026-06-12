"""Fixtures for containerized E2E tests.

These tests are designed to run inside the E2E container (scripts/e2e.sh),
where the container itself is the cleanup boundary. They are skipped on the
host unless OVERCODE_E2E_CONTAINER=1 is set.

Isolation model (per test):
  - private tmux server (unique -L socket)        -> TmuxSandbox
  - private OVERCODE_DIR / OVERCODE_STATE_DIR     -> tmp_path
  - env passed explicitly to subprocesses; os.environ is never mutated
  - leak audit: after teardown, any surviving process referencing this
    test's socket or state dir fails the test
"""

import os
import subprocess
import time

import pytest

from tests.container.harness import OvercodeCLI, TmuxSandbox, wait_for


def pytest_ignore_collect(collection_path, config):
    """On the host, don't even import these modules — they depend on
    container-only packages (requests, playwright). Run via scripts/e2e.sh
    (see docs/design/e2e-devcontainer-testing.md)."""
    if os.environ.get("OVERCODE_E2E_CONTAINER") != "1":
        return str(collection_path).endswith(".py")
    return None


class LeakRegistry:
    """Identifiers owned by the current test; audited after teardown."""

    def __init__(self):
        self.identifiers: list[str] = []

    def add(self, identifier: str) -> None:
        self.identifiers.append(identifier)

    def surviving_processes(self) -> list[str]:
        result = subprocess.run(
            ["ps", "-eo", "pid=,args="], capture_output=True, text=True, timeout=10
        )
        leaks = []
        for line in result.stdout.splitlines():
            if any(ident in line for ident in self.identifiers):
                leaks.append(line.strip())
        return leaks


@pytest.fixture
def leak_registry():
    """Autoclosing leak audit. Instantiated before (so finalized after) the
    sandbox/CLI fixtures that register identifiers with it."""
    registry = LeakRegistry()
    yield registry
    # Give SIGTERM'd processes a beat to exit before declaring a leak
    leaks = registry.surviving_processes()
    if leaks:
        time.sleep(1.0)
        leaks = registry.surviving_processes()
    if leaks:
        # Contain the damage, then fail loudly naming the culprit
        for line in leaks:
            pid = line.split()[0]
            subprocess.run(["kill", "-9", pid], capture_output=True)
        pytest.fail(
            "leaked processes survived teardown (killed now):\n" + "\n".join(leaks),
            pytrace=False,
        )


@pytest.fixture
def make_oc(tmp_path, leak_registry):
    """Factory for fully-isolated OverCode instances (e.g. sister scenarios)."""
    instances: list[tuple[TmuxSandbox, OvercodeCLI]] = []

    def _make(label: str = "main") -> OvercodeCLI:
        box = TmuxSandbox()
        leak_registry.add(box.socket)
        cli = OvercodeCLI(tmp_path / label, box)
        leak_registry.add(str(cli.state_dir))
        instances.append((box, cli))
        return cli

    yield _make
    for box, cli in reversed(instances):
        cli.stop_daemons()
        box.kill_server()


@pytest.fixture
def oc(make_oc):
    return make_oc("main")


@pytest.fixture
def sandbox(oc):
    return oc.sandbox


@pytest.fixture
def oc_wait(oc):
    """wait_for pre-wired with this test's diagnostics dump."""

    def _wait(predicate, timeout=30.0, interval=0.5, desc="condition"):
        return wait_for(
            predicate, timeout=timeout, interval=interval, desc=desc,
            on_fail=oc.diagnostics,
        )

    return _wait
