"""Containerized E2E test harness for OverCode.

Design: docs/design/e2e-devcontainer-testing.md
"""

from .backends import BACKENDS, CLAUDE, OPENCODE, MockBackend
from .core import REPO_ROOT, MOCK_CLAUDE, MOCK_OPENCODE, artifacts_dir, wait_for
from .tmux_sandbox import TmuxSandbox
from .overcode_cli import OvercodeCLI

__all__ = [
    "BACKENDS",
    "CLAUDE",
    "OPENCODE",
    "MockBackend",
    "REPO_ROOT",
    "MOCK_CLAUDE",
    "MOCK_OPENCODE",
    "artifacts_dir",
    "wait_for",
    "TmuxSandbox",
    "OvercodeCLI",
]
