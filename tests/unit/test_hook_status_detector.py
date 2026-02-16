"""
Unit tests for HookStatusDetector.

Tests hook state file reading, staleness detection, status mapping,
fallback to polling, and activity enrichment.
"""

import json
import time
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.hook_status_detector import HookStatusDetector
from overcode.status_constants import STATUS_RUNNING, STATUS_WAITING_USER, STATUS_TERMINATED
from overcode.interfaces import MockTmux
from tests.fixtures import create_mock_session, create_mock_tmux_with_content


def _write_hook_state(state_dir: Path, session_name: str, event: str,
                      timestamp: float = None, tool_name: str = None):
    """Helper to write a hook state file."""
    state_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "event": event,
        "timestamp": timestamp or time.time(),
    }
    if tool_name:
        data["tool_name"] = tool_name
    path = state_dir / f"hook_state_{session_name}.json"
    path.write_text(json.dumps(data))


class TestHookStateReading:
    """Test hook state file reading and parsing."""

    def test_reads_valid_hook_state(self, tmp_path):
        """Valid hook state file is read successfully."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "UserPromptSubmit")

        detector = HookStatusDetector("agents", state_dir=state_dir)
        state = detector._read_hook_state("test-agent")

        assert state is not None
        assert state["event"] == "UserPromptSubmit"

    def test_missing_file_returns_none(self, tmp_path):
        """Missing hook state file returns None."""
        state_dir = tmp_path / "sessions" / "agents"
        state_dir.mkdir(parents=True)

        detector = HookStatusDetector("agents", state_dir=state_dir)
        state = detector._read_hook_state("nonexistent-agent")

        assert state is None

    def test_corrupt_json_returns_none(self, tmp_path):
        """Corrupt JSON returns None."""
        state_dir = tmp_path / "sessions" / "agents"
        state_dir.mkdir(parents=True)
        path = state_dir / "hook_state_test-agent.json"
        path.write_text("{invalid json!!!")

        detector = HookStatusDetector("agents", state_dir=state_dir)
        state = detector._read_hook_state("test-agent")

        assert state is None

    def test_missing_required_fields_returns_none(self, tmp_path):
        """Hook state without required fields returns None."""
        state_dir = tmp_path / "sessions" / "agents"
        state_dir.mkdir(parents=True)
        path = state_dir / "hook_state_test-agent.json"
        path.write_text(json.dumps({"event": "Stop"}))  # Missing timestamp

        detector = HookStatusDetector("agents", state_dir=state_dir)
        state = detector._read_hook_state("test-agent")

        assert state is None

    def test_non_dict_json_returns_none(self, tmp_path):
        """Non-dict JSON (e.g., a list) returns None."""
        state_dir = tmp_path / "sessions" / "agents"
        state_dir.mkdir(parents=True)
        path = state_dir / "hook_state_test-agent.json"
        path.write_text(json.dumps([1, 2, 3]))

        detector = HookStatusDetector("agents", state_dir=state_dir)
        state = detector._read_hook_state("test-agent")

        assert state is None


class TestStalenessDetection:
    """Test staleness detection for hook state."""

    def test_fresh_state_is_not_stale(self, tmp_path):
        """State within threshold is considered fresh."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "Stop", timestamp=time.time())

        detector = HookStatusDetector("agents", state_dir=state_dir, stale_threshold_seconds=120)
        state = detector._read_hook_state("test-agent")

        assert state is not None

    def test_stale_state_returns_none(self, tmp_path):
        """State older than threshold is considered stale."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "Stop", timestamp=time.time() - 200)

        detector = HookStatusDetector("agents", state_dir=state_dir, stale_threshold_seconds=120)
        state = detector._read_hook_state("test-agent")

        assert state is None

    def test_custom_stale_threshold(self, tmp_path):
        """Custom stale threshold is respected."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "Stop", timestamp=time.time() - 5)

        # With 3-second threshold, 5-second-old state should be stale
        detector = HookStatusDetector("agents", state_dir=state_dir, stale_threshold_seconds=3)
        state = detector._read_hook_state("test-agent")

        assert state is None

    def test_invalid_timestamp_returns_none(self, tmp_path):
        """Invalid timestamp type returns None."""
        state_dir = tmp_path / "sessions" / "agents"
        state_dir.mkdir(parents=True)
        path = state_dir / "hook_state_test-agent.json"
        path.write_text(json.dumps({
            "event": "Stop",
            "timestamp": "not-a-number",
        }))

        detector = HookStatusDetector("agents", state_dir=state_dir)
        state = detector._read_hook_state("test-agent")

        assert state is None


class TestStatusMapping:
    """Test hook event → status mapping."""

    def test_user_prompt_submit_is_running(self, tmp_path):
        """UserPromptSubmit → RUNNING."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "UserPromptSubmit")
        mock_tmux = create_mock_tmux_with_content("agents", 1, "some pane content")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, activity, pane = detector.detect_status(session)

        assert status == STATUS_RUNNING
        assert "Processing prompt" in activity

    def test_stop_is_waiting_user(self, tmp_path):
        """Stop → WAITING_USER."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "Stop")
        mock_tmux = create_mock_tmux_with_content("agents", 1, "some pane content")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, _, _ = detector.detect_status(session)

        assert status == STATUS_WAITING_USER

    def test_permission_request_is_waiting_user(self, tmp_path):
        """PermissionRequest → WAITING_USER."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "PermissionRequest")
        mock_tmux = create_mock_tmux_with_content("agents", 1, "some pane content")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, activity, _ = detector.detect_status(session)

        assert status == STATUS_WAITING_USER
        assert "Permission" in activity

    def test_post_tool_use_is_running(self, tmp_path):
        """PostToolUse → RUNNING."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "PostToolUse", tool_name="Read")
        mock_tmux = create_mock_tmux_with_content("agents", 1, "some pane content")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, activity, _ = detector.detect_status(session)

        assert status == STATUS_RUNNING
        assert "Read" in activity

    def test_post_tool_use_without_tool_name(self, tmp_path):
        """PostToolUse without tool_name still works."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "PostToolUse")
        mock_tmux = create_mock_tmux_with_content("agents", 1, "some pane content")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, activity, _ = detector.detect_status(session)

        assert status == STATUS_RUNNING
        assert "tool" in activity.lower()

    def test_session_end_falls_back_to_polling_terminated(self, tmp_path):
        """SessionEnd with shell prompt → TERMINATED (actual exit)."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "SessionEnd")
        # Shell prompt indicates Claude actually exited
        mock_tmux = create_mock_tmux_with_content("agents", 1, """
mike@mac ~/Code/overcode %
""")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, _, _ = detector.detect_status(session)

        assert status == STATUS_TERMINATED

    def test_session_end_terminated_with_claude_output_above(self, tmp_path):
        """SessionEnd with shell prompt + ⏺ in preceding lines → TERMINATED.

        After Claude exits, its output (with ⏺ markers) is still visible
        above the shell prompt. This must not prevent terminated detection.
        """
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "SessionEnd")
        mock_tmux = create_mock_tmux_with_content("agents", 1, """
⏺ Here's my final response:

  The task is complete. Let me know if you need anything else.

mike@mac ~/Code/overcode %
""")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, _, _ = detector.detect_status(session)

        assert status == STATUS_TERMINATED

    def test_session_end_falls_back_to_polling_after_clear(self, tmp_path):
        """SessionEnd with Claude prompt → WAITING_USER (/clear was used)."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "SessionEnd")
        # Claude prompt indicates /clear was used, not actual exit
        mock_tmux = create_mock_tmux_with_content("agents", 1, """
╭──────────────────────────────────────────╮
│ ✻ Welcome to Claude Code!                │
╰──────────────────────────────────────────╯

>
  ? for shortcuts
""")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, _, _ = detector.detect_status(session)

        assert status == STATUS_WAITING_USER

    def test_unknown_event_defaults_to_waiting_user(self, tmp_path):
        """Unknown event → WAITING_USER (safe default)."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "SomeNewEvent")
        mock_tmux = create_mock_tmux_with_content("agents", 1, "some pane content")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, _, _ = detector.detect_status(session)

        assert status == STATUS_WAITING_USER


class TestFallbackToPolling:
    """Test fallback to polling when hook state is unavailable."""

    def test_falls_back_when_no_state_file(self, tmp_path):
        """Falls back to polling when no hook state file exists."""
        state_dir = tmp_path / "sessions" / "agents"
        state_dir.mkdir(parents=True)
        mock_tmux = create_mock_tmux_with_content("agents", 1, """
⏺ Finished work.

>
  ? for shortcuts
""")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, _, _ = detector.detect_status(session)

        # Polling should detect waiting_user from the empty prompt
        assert status == STATUS_WAITING_USER

    def test_falls_back_when_stale(self, tmp_path):
        """Falls back to polling when hook state is stale."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "UserPromptSubmit",
                         timestamp=time.time() - 300)  # 5 minutes old
        mock_tmux = create_mock_tmux_with_content("agents", 1, """
⏺ Finished work.

>
  ? for shortcuts
""")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir,
                                     stale_threshold_seconds=120)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, _, _ = detector.detect_status(session)

        # Should fall back to polling and detect waiting_user
        assert status == STATUS_WAITING_USER


class TestPaneContent:
    """Test pane content reading."""

    def test_returns_pane_content(self, tmp_path):
        """Pane content is returned alongside hook-determined status."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "UserPromptSubmit")
        mock_tmux = create_mock_tmux_with_content("agents", 1, "some pane content here")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        _, _, pane = detector.detect_status(session)

        assert "some pane content here" in pane

    def test_get_pane_content_delegates_to_polling(self, tmp_path):
        """get_pane_content works via the polling detector's tmux interface."""
        state_dir = tmp_path / "sessions" / "agents"
        mock_tmux = create_mock_tmux_with_content("agents", 1, "line1\nline2\nline3")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        content = detector.get_pane_content(1)

        assert content is not None
        assert "line1" in content

    def test_empty_pane_content(self, tmp_path):
        """Handles empty pane content gracefully."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "Stop")
        mock_tmux = MockTmux()
        mock_tmux.new_session("agents")
        # No pane content set for window 1

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, _, pane = detector.detect_status(session)

        assert status == STATUS_WAITING_USER
        assert pane == ""


class TestEnvVarOverride:
    """Test OVERCODE_STATE_DIR environment variable override."""

    def test_uses_env_var_state_dir(self, tmp_path, monkeypatch):
        """OVERCODE_STATE_DIR env var overrides default state dir.

        Hook state path must match hook_handler._get_hook_state_path():
            OVERCODE_STATE_DIR / {tmux_session} / hook_state_{name}.json
        """
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))

        # Write to the correct path: OVERCODE_STATE_DIR / tmux_session
        state_dir = tmp_path / "agents"
        _write_hook_state(state_dir, "test-agent", "PostToolUse", tool_name="Read")
        mock_tmux = create_mock_tmux_with_content("agents", 1, "content")

        detector = HookStatusDetector("agents", tmux=mock_tmux)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, activity, _ = detector.detect_status(session)

        # PostToolUse → running (proves hooks were read, not polling fallback)
        assert status == STATUS_RUNNING
        assert "Using Read" in activity


class TestStatusDetectorAttributes:
    """Test that HookStatusDetector has required attributes."""

    def test_has_tmux_session(self, tmp_path):
        """Has tmux_session attribute."""
        detector = HookStatusDetector("test-session", state_dir=tmp_path)
        assert detector.tmux_session == "test-session"

    def test_has_status_constants(self, tmp_path):
        """Has backward-compat status constants."""
        detector = HookStatusDetector("agents", state_dir=tmp_path)
        assert detector.STATUS_RUNNING == STATUS_RUNNING
        assert detector.STATUS_WAITING_USER == STATUS_WAITING_USER
        assert detector.STATUS_TERMINATED == STATUS_TERMINATED
