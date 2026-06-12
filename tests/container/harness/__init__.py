"""Containerized E2E test harness for OverCode.

Design: docs/design/e2e-devcontainer-testing.md
"""

from .core import REPO_ROOT, MOCK_CLAUDE, artifacts_dir, wait_for
from .tmux_sandbox import TmuxSandbox
from .overcode_cli import OvercodeCLI

__all__ = [
    "REPO_ROOT",
    "MOCK_CLAUDE",
    "artifacts_dir",
    "wait_for",
    "TmuxSandbox",
    "OvercodeCLI",
]
