"""Tests for status_detector_factory module."""

import pytest
from unittest.mock import MagicMock, patch

from overcode.status_detector_factory import create_status_detector, StatusDetectorDispatcher


class TestCreateStatusDetector:
    """Tests for create_status_detector factory function."""

    def test_creates_polling_detector_by_default(self):
        detector = create_status_detector("agents")
        from overcode.status_detector import PollingStatusDetector
        assert isinstance(detector, PollingStatusDetector)

    def test_creates_polling_detector_explicitly(self):
        detector = create_status_detector("agents", strategy="polling")
        from overcode.status_detector import PollingStatusDetector
        assert isinstance(detector, PollingStatusDetector)

    def test_creates_hook_detector(self):
        detector = create_status_detector("agents", strategy="hooks")
        from overcode.hook_status_detector import HookStatusDetector
        assert isinstance(detector, HookStatusDetector)

    def test_passes_tmux_interface(self):
        mock_tmux = MagicMock()
        detector = create_status_detector("agents", tmux=mock_tmux)
        assert detector.tmux is mock_tmux

    def test_passes_patterns(self):
        from overcode.status_patterns import StatusPatterns
        patterns = StatusPatterns()
        detector = create_status_detector("agents", patterns=patterns)
        assert detector.patterns is patterns


class TestStatusDetectorDispatcher:
    """Tests for StatusDetectorDispatcher."""

    def test_creates_both_detectors(self):
        dispatcher = StatusDetectorDispatcher("agents")
        from overcode.status_detector import PollingStatusDetector
        from overcode.hook_status_detector import HookStatusDetector
        assert isinstance(dispatcher.polling, PollingStatusDetector)
        assert isinstance(dispatcher.hooks, HookStatusDetector)

    def test_uses_injected_detectors(self):
        mock_polling = MagicMock()
        mock_hooks = MagicMock()
        dispatcher = StatusDetectorDispatcher(
            "agents",
            polling_detector=mock_polling,
            hook_detector=mock_hooks,
        )
        assert dispatcher.polling is mock_polling
        assert dispatcher.hooks is mock_hooks

    def test_capture_lines_property_reads_from_polling(self):
        mock_polling = MagicMock()
        mock_polling.capture_lines = 42
        mock_hooks = MagicMock()
        dispatcher = StatusDetectorDispatcher(
            "agents",
            polling_detector=mock_polling,
            hook_detector=mock_hooks,
        )
        assert dispatcher.capture_lines == 42

    def test_capture_lines_setter_updates_both(self):
        mock_polling = MagicMock()
        mock_hooks = MagicMock()
        dispatcher = StatusDetectorDispatcher(
            "agents",
            polling_detector=mock_polling,
            hook_detector=mock_hooks,
        )
        dispatcher.capture_lines = 100
        assert mock_polling.capture_lines == 100
        assert mock_hooks.capture_lines == 100

    def test_detect_status_uses_hooks_when_enabled(self):
        mock_polling = MagicMock()
        mock_hooks = MagicMock()
        mock_hooks.detect_status.return_value = ("running", "working", "hooks")

        dispatcher = StatusDetectorDispatcher(
            "agents",
            polling_detector=mock_polling,
            hook_detector=mock_hooks,
        )

        session = MagicMock()
        session.hook_status_detection = True

        result = dispatcher.detect_status(session)

        mock_hooks.detect_status.assert_called_once_with(session)
        mock_polling.detect_status.assert_not_called()
        assert result == ("running", "working", "hooks")

    def test_detect_status_uses_polling_when_hooks_disabled(self):
        mock_polling = MagicMock()
        mock_polling.detect_status.return_value = ("waiting_user", "prompt", "polling")
        mock_hooks = MagicMock()

        dispatcher = StatusDetectorDispatcher(
            "agents",
            polling_detector=mock_polling,
            hook_detector=mock_hooks,
        )

        session = MagicMock()
        session.hook_status_detection = False

        result = dispatcher.detect_status(session)

        mock_polling.detect_status.assert_called_once_with(session)
        mock_hooks.detect_status.assert_not_called()
        assert result == ("waiting_user", "prompt", "polling")

    def test_get_pane_content_delegates_to_polling(self):
        mock_polling = MagicMock()
        mock_polling.get_pane_content.return_value = "pane content"
        mock_hooks = MagicMock()

        dispatcher = StatusDetectorDispatcher(
            "agents",
            polling_detector=mock_polling,
            hook_detector=mock_hooks,
        )

        result = dispatcher.get_pane_content(1, num_lines=50)
        mock_polling.get_pane_content.assert_called_once_with(1, 50)
        assert result == "pane content"
