"""Shared helpers for the containerized E2E harness."""

import os
import time
from pathlib import Path
from typing import Callable, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
MOCK_CLAUDE = REPO_ROOT / "tests" / "mock_claude.py"


def artifacts_dir() -> Path:
    """Directory for run artifacts (screenshots, logs); mounted out of the container."""
    path = Path(os.environ.get("E2E_ARTIFACTS", "/tmp/overcode-e2e-artifacts"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def wait_for(
    predicate: Callable[[], object],
    timeout: float = 30.0,
    interval: float = 0.5,
    desc: str = "condition",
    on_fail: Optional[Callable[[], str]] = None,
):
    """Poll until predicate returns a truthy value; raise with diagnostics on timeout.

    Returns the truthy value the predicate produced. This is the only sanctioned
    way to wait in E2E tests — no bare time.sleep() assertions.
    """
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() >= deadline:
            break
        time.sleep(interval)

    diagnostics = ""
    if on_fail is not None:
        try:
            diagnostics = "\n--- diagnostics ---\n" + on_fail()
        except Exception as exc:  # diagnostics must never mask the timeout
            diagnostics = f"\n(diagnostics collection failed: {exc})"
    raise TimeoutError(f"timed out after {timeout}s waiting for {desc}{diagnostics}")
