"""
Contract tests for StatusDetectorProtocol.

Both PollingStatusDetector and HookStatusDetector must pass these tests,
ensuring they satisfy the StatusDetectorProtocol interface.
"""

import json
import time
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.status_detector import PollingStatusDetector
from overcode.hook_status_detector import HookStatusDetector
from overcode.protocols import StatusDetectorProtocol
from overcode.status_constants import ALL_STATUSES
from overcode.interfaces import MockTmux
from tests.fixtures import create_mock_session, create_mock_tmux_with_content


class ContractTests:
    """Base contract tests that both implementations must pass.

    Subclasses must implement:
    - create_detector(tmux_session, mock_tmux, **kwargs) -> StatusDetectorProtocol
    """

    def create_detector(self, tmux_session, mock_tmux, **kwargs):
        raise NotImplementedError

    def test_satisfies_protocol(self, tmp_path):
        """Implementation satisfies StatusDetectorProtocol."""
        mock_tmux = MockTmux()
        mock_tmux.new_session("agents")
        detector = self.create_detector("agents", mock_tmux, tmp_path=tmp_path)
        assert isinstance(detector, StatusDetectorProtocol)

    def test_has_tmux_session_attribute(self, tmp_path):
        """Has tmux_session string attribute."""
        mock_tmux = MockTmux()
        mock_tmux.new_session("agents")
        detector = self.create_detector("agents", mock_tmux, tmp_path=tmp_path)
        assert detector.tmux_session == "agents"

    def test_detect_status_returns_3_tuple(self, tmp_path):
        """detect_status returns a 3-tuple of strings."""
        mock_tmux = create_mock_tmux_with_content("agents", 1, """
⏺ Working...

>
  ? for shortcuts
""")
        detector = self.create_detector("agents", mock_tmux, tmp_path=tmp_path)
        session = create_mock_session(tmux_window=1)

        result = detector.detect_status(session)

        assert isinstance(result, tuple)
        assert len(result) == 3
        status, activity, pane = result
        assert isinstance(status, str)
        assert isinstance(activity, str)
        assert isinstance(pane, str)

    def test_detect_status_returns_valid_status(self, tmp_path):
        """detect_status returns a recognized status constant."""
        mock_tmux = create_mock_tmux_with_content("agents", 1, """
⏺ Working...

>
  ? for shortcuts
""")
        detector = self.create_detector("agents", mock_tmux, tmp_path=tmp_path)
        session = create_mock_session(tmux_window=1)

        status, _, _ = detector.detect_status(session)

        assert status in ALL_STATUSES, f"Unknown status: {status}"

    def test_missing_pane_returns_waiting_user(self, tmp_path):
        """When pane content is unavailable, returns waiting_user."""
        mock_tmux = MockTmux()
        mock_tmux.new_session("agents")
        # Window 1 has no content set

        detector = self.create_detector("agents", mock_tmux, tmp_path=tmp_path)
        session = create_mock_session(tmux_window=1)

        status, _, _ = detector.detect_status(session)

        assert status == "waiting_user"

    def test_get_pane_content_returns_optional_str(self, tmp_path):
        """get_pane_content returns Optional[str]."""
        mock_tmux = create_mock_tmux_with_content("agents", 1, "hello world")
        detector = self.create_detector("agents", mock_tmux, tmp_path=tmp_path)

        result = detector.get_pane_content(1)

        assert result is None or isinstance(result, str)

    def test_get_pane_content_with_content(self, tmp_path):
        """get_pane_content returns content when available."""
        mock_tmux = create_mock_tmux_with_content("agents", 1, "hello world")
        detector = self.create_detector("agents", mock_tmux, tmp_path=tmp_path)

        result = detector.get_pane_content(1)

        assert result is not None
        assert "hello world" in result

    def test_get_pane_content_missing_window(self, tmp_path):
        """get_pane_content returns None for missing window."""
        mock_tmux = MockTmux()
        mock_tmux.new_session("agents")
        detector = self.create_detector("agents", mock_tmux, tmp_path=tmp_path)

        result = detector.get_pane_content(999)

        assert result is None


class TestPollingContract(ContractTests):
    """PollingStatusDetector must satisfy all contract tests."""

    def create_detector(self, tmux_session, mock_tmux, **kwargs):
        return PollingStatusDetector(tmux_session, tmux=mock_tmux)


class TestHookContract(ContractTests):
    """HookStatusDetector must satisfy all contract tests.

    Uses a state_dir with no hook state files, so it falls back to polling
    for all tests. This verifies the fallback path satisfies the contract.
    """

    def create_detector(self, tmux_session, mock_tmux, **kwargs):
        tmp_path = kwargs.get("tmp_path")
        state_dir = tmp_path / "sessions" / tmux_session
        state_dir.mkdir(parents=True, exist_ok=True)
        return HookStatusDetector(tmux_session, tmux=mock_tmux, state_dir=state_dir)


class TestHookContractWithState(ContractTests):
    """HookStatusDetector with active hook state must also satisfy contract."""

    def create_detector(self, tmux_session, mock_tmux, **kwargs):
        tmp_path = kwargs.get("tmp_path")
        state_dir = tmp_path / "sessions" / tmux_session
        state_dir.mkdir(parents=True, exist_ok=True)
        # Write a fresh "Stop" state so hook detection is active
        path = state_dir / "hook_state_test-session.json"
        path.write_text(json.dumps({
            "event": "Stop",
            "timestamp": time.time(),
        }))
        return HookStatusDetector(tmux_session, tmux=mock_tmux, state_dir=state_dir)
