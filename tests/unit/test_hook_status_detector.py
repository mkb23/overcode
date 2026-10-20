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
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.hook_status_detector import (
    RULES,
    HookStatusDetector,
    TickContext,
    _live_subagent_count,
    augment_with_legacy_heartbeat,
    decide,
    legacy_status_for,
    synthesize_status_detail_from_legacy,
)
from overcode.status_constants import (
    STATUS_RUNNING,
    STATUS_BUSY_SLEEPING,
    STATUS_WAITING_APPROVAL,
    STATUS_WAITING_USER,
    STATUS_WAITING_OVERSIGHT,
    STATUS_TERMINATED,
    STATUS_ERROR,
    STATUS_UNKNOWN,
    STATUS_COLOR_GREEN,
    STATUS_COLOR_ORANGE,
    STATUS_COLOR_YELLOW,
    STATUS_COLOR_RED,
    STATUS_COLOR_REST,
)
from tests.tmux_doubles import MockTmux
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

        detector = HookStatusDetector("agents", state_dir=state_dir)
        state = detector._read_hook_state("test-agent")

        assert state is not None

    def test_old_state_is_still_valid(self, tmp_path):
        """Old hook state is trusted (no staleness threshold)."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "PostToolUse", timestamp=time.time() - 600)

        detector = HookStatusDetector("agents", state_dir=state_dir)
        state = detector._read_hook_state("test-agent")

        # No stale threshold — running hooks are trusted indefinitely
        assert state is not None
        assert state["event"] == "PostToolUse"

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

    def test_permission_request_is_waiting_approval(self, tmp_path):
        """PermissionRequest → WAITING_APPROVAL."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "PermissionRequest")
        mock_tmux = create_mock_tmux_with_content("agents", 1, "some pane content")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, activity, _ = detector.detect_status(session)

        assert status == STATUS_WAITING_APPROVAL
        assert "Permission" in activity

    def test_interrupt_is_waiting_user(self, tmp_path):
        """Interrupt (codex-only, design doc §2.3) -> WAITING_USER.

        Downgrades a stuck RUNNING directly via the map, without needing the
        pane-scraped interrupt_prompt_markers fallback Claude relies on.
        """
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "Interrupt")
        mock_tmux = create_mock_tmux_with_content("agents", 1, "some pane content")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, _, _ = detector.detect_status(session)

        assert status == STATUS_WAITING_USER

    def test_session_start_is_waiting_user(self, tmp_path):
        """SessionStart (codex-only) -> WAITING_USER, same as the "no hook
        event yet" default a fresh launch already gets."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "SessionStart")
        mock_tmux = create_mock_tmux_with_content("agents", 1, "some pane content")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, _, _ = detector.detect_status(session)

        assert status == STATUS_WAITING_USER

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

    def test_user_prompt_submit_rejected_is_error(self, tmp_path):
        """UserPromptSubmitRejected → ERROR (purple) (#428)."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "UserPromptSubmitRejected")
        mock_tmux = create_mock_tmux_with_content("agents", 1, "Budget exceeded")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, activity, _ = detector.detect_status(session)

        assert status == STATUS_ERROR
        assert "blocked" in activity.lower()

    def test_escape_interrupt_downgrades_running_to_waiting(self, tmp_path):
        """Pane showing the Escape-interrupt prompt overrides RUNNING → WAITING_USER (#431)."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "PreToolUse", tool_name="Bash")
        pane = (
            "⏺ Agent(Search for foo)\n"
            "  ⎿  Bash(curl https://example.com)\n"
            "     Running…\n"
            "  ⎿  Interrupted · What should Claude do instead?\n"
            "\n"
            "❯ "
        )
        mock_tmux = create_mock_tmux_with_content("agents", 1, pane)

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, _, _ = detector.detect_status(session)

        assert status == STATUS_WAITING_USER

    def test_escape_interrupt_does_not_override_non_running(self, tmp_path):
        """Interrupt prompt in pane doesn't demote statuses below RUNNING (#431)."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "PermissionRequest")
        pane = "⎿  Interrupted · What should Claude do instead?"
        mock_tmux = create_mock_tmux_with_content("agents", 1, pane)

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, _, _ = detector.detect_status(session)

        assert status == STATUS_WAITING_APPROVAL

    def test_no_interrupt_marker_keeps_running(self, tmp_path):
        """Running status stays running when pane does not show the interrupt prompt (#431)."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "PreToolUse", tool_name="Read")
        mock_tmux = create_mock_tmux_with_content("agents", 1, "⏺ Reading file...")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, _, _ = detector.detect_status(session)

        assert status == STATUS_RUNNING

    def test_live_monitor_upgrades_waiting_user_to_busy_sleeping(self, tmp_path):
        """Stop + pane shows '1 monitor' → STATUS_BUSY_SLEEPING (#441)."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "Stop")
        pane = (
            "⏺ Monitor(date ticks every 15s)\n"
            "  ⎿  Monitor started · task abc123 · persistent\n"
            "❯  \n"
            "⏵⏵ bypass permissions on · 1 monitor · ↓ to manage"
        )
        mock_tmux = create_mock_tmux_with_content("agents", 1, pane)

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, activity, _ = detector.detect_status(session)

        assert status == STATUS_BUSY_SLEEPING
        assert "1 monitor" in activity

    def test_live_monitors_plural_in_activity(self, tmp_path):
        """Multiple monitors → plural 'monitors' in activity (#441)."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "Stop")
        pane = "⏵⏵ bypass permissions on · 3 monitors · ↓ to manage"
        mock_tmux = create_mock_tmux_with_content("agents", 1, pane)

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, activity, _ = detector.detect_status(session)

        assert status == STATUS_BUSY_SLEEPING
        assert "3 monitors" in activity

    def test_live_monitor_does_not_override_running(self, tmp_path):
        """Active monitors don't demote an in-flight RUNNING status (#441)."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "PreToolUse", tool_name="Bash")
        pane = "⏵⏵ bypass permissions on · 1 monitor · ↓ to manage"
        mock_tmux = create_mock_tmux_with_content("agents", 1, pane)

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, _, _ = detector.detect_status(session)

        assert status == STATUS_RUNNING

    def test_no_monitor_keeps_waiting_user(self, tmp_path):
        """Stop + no monitor in status bar → plain WAITING_USER (#441)."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "Stop")
        pane = "⏵⏵ bypass permissions on · esc to interrupt"
        mock_tmux = create_mock_tmux_with_content("agents", 1, pane)

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


class TestNoHookState:
    """Test behaviour when no hook state file exists."""

    def test_no_state_file_with_window_alive(self, tmp_path):
        """No hook state + window alive → unknown: starting, not red."""
        state_dir = tmp_path / "sessions" / "agents"
        state_dir.mkdir(parents=True)
        mock_tmux = create_mock_tmux_with_content("agents", 1, "some pane content")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, activity, _ = detector.detect_status(session)

        assert status == STATUS_UNKNOWN
        assert activity.startswith("Starting")
        assert detector.get_status_detail("test-agent") is None

    def test_no_state_file_with_window_gone(self, tmp_path):
        """No hook state + window gone → TERMINATED."""
        state_dir = tmp_path / "sessions" / "agents"
        state_dir.mkdir(parents=True)
        mock_tmux = MockTmux()
        mock_tmux.new_session("agents")
        # No window content → capture_pane returns None

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=999, name="test-agent")
        status, _, _ = detector.detect_status(session)

        assert status == STATUS_TERMINATED

    def test_old_running_hook_is_trusted(self, tmp_path):
        """Old running hook (e.g. PostToolUse 10 min ago) is still RUNNING."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "PostToolUse",
                         tool_name="Bash", timestamp=time.time() - 600)
        mock_tmux = create_mock_tmux_with_content("agents", 1, "some content")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, _, _ = detector.detect_status(session)

        assert status == STATUS_RUNNING


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

    def test_get_pane_content_via_tmux_interface(self, tmp_path):
        """get_pane_content works via the tmux interface."""
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


class TestBusySleepingDetection:
    """Tests for busy_sleeping detection in hook-based detector (#289)."""

    def test_sleep_in_tool_input_command(self, tmp_path):
        """PreToolUse with tool_input containing sleep → busy_sleeping."""
        state_dir = tmp_path / "sessions" / "agents"
        state_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "event": "PreToolUse",
            "timestamp": time.time(),
            "tool_name": "Bash",
            "tool_input": {"command": "sleep 60"},
        }
        (state_dir / "hook_state_test-agent.json").write_text(json.dumps(data))
        mock_tmux = create_mock_tmux_with_content("agents", 1, "some pane content")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, activity, _ = detector.detect_status(session)

        assert status == STATUS_BUSY_SLEEPING
        assert "Sleeping" in activity
        assert "60s" in activity or "1.0m" in activity

    def test_activity_includes_duration_from_tool_input(self, tmp_path):
        """Activity from tool_input should also include parsed duration."""
        state_dir = tmp_path / "sessions" / "agents"
        state_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "event": "PreToolUse",
            "timestamp": time.time(),
            "tool_name": "Bash",
            "tool_input": {"command": "sleep 300"},
        }
        path = state_dir / "hook_state_test-agent.json"
        path.write_text(json.dumps(data))

        mock_tmux = create_mock_tmux_with_content("agents", 1, "some pane content")
        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, activity, _ = detector.detect_status(session)

        assert "Sleeping" in activity, f"Activity should start with 'Sleeping', got: {activity}"
        assert "5.0m" in activity, f"Activity should include '5.0m' (300s), got: {activity}"

    def test_sleep_in_tool_input(self, tmp_path):
        """PreToolUse with tool_input containing sleep → busy_sleeping."""
        state_dir = tmp_path / "sessions" / "agents"
        state_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "event": "PreToolUse",
            "timestamp": time.time(),
            "tool_name": "Bash",
            "tool_input": {"command": "sleep 300"},
        }
        path = state_dir / "hook_state_test-agent.json"
        path.write_text(json.dumps(data))

        mock_tmux = create_mock_tmux_with_content("agents", 1, "some pane content")
        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, activity, _ = detector.detect_status(session)

        assert status == STATUS_BUSY_SLEEPING, (
            f"Sleep in tool_input should be busy_sleeping, got {status}: {activity}"
        )

    def test_non_sleep_bash_stays_running(self, tmp_path):
        """PostToolUse with non-sleep Bash → running."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "PostToolUse", tool_name="Bash")
        pane_content = '⏺ Running Bash("pytest tests/ -v")\n'
        mock_tmux = create_mock_tmux_with_content("agents", 1, pane_content)

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, activity, _ = detector.detect_status(session)

        assert status == STATUS_RUNNING, (
            f"Non-sleep bash should be running, got {status}: {activity}"
        )

    def test_pre_tool_use_with_sleep(self, tmp_path):
        """PreToolUse with Bash sleep → busy_sleeping (detected before execution)."""
        state_dir = tmp_path / "sessions" / "agents"
        state_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "event": "PreToolUse",
            "timestamp": time.time(),
            "tool_name": "Bash",
            "tool_input": {"command": "sleep 120"},
        }
        (state_dir / "hook_state_test-agent.json").write_text(json.dumps(data))
        mock_tmux = create_mock_tmux_with_content("agents", 1, "some content")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, activity, _ = detector.detect_status(session)

        assert status == STATUS_BUSY_SLEEPING
        assert "2.0m" in activity


class TestSkillTracking:
    """Test loaded skill tracking from Skill tool_use events (#252)."""

    def test_skill_tool_use_tracked(self, tmp_path):
        """Persisted loaded_skills in hook state are picked up by detector."""
        state_dir = tmp_path / "sessions" / "agents"
        state_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "event": "PreToolUse",
            "timestamp": time.time(),
            "tool_name": "Skill",
            "tool_input": {"skill": "overcode"},
            "loaded_skills": ["overcode"],
        }
        (state_dir / "hook_state_test-agent.json").write_text(json.dumps(data))
        mock_tmux = create_mock_tmux_with_content("agents", 1, "some content")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        detector.detect_status(session)

        assert detector.get_loaded_skills("test-agent") == ["overcode"]

    def test_multiple_skills_accumulated(self, tmp_path):
        """Accumulated loaded_skills from hook_handler are read by detector."""
        state_dir = tmp_path / "sessions" / "agents"
        state_dir.mkdir(parents=True, exist_ok=True)
        mock_tmux = create_mock_tmux_with_content("agents", 1, "some content")
        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")

        # Simulate hook_handler accumulating skills across writes
        data = {
            "event": "PreToolUse",
            "timestamp": time.time(),
            "tool_name": "Skill",
            "tool_input": {"skill": "overcode"},
            "loaded_skills": ["overcode"],
        }
        (state_dir / "hook_state_test-agent.json").write_text(json.dumps(data))
        detector.detect_status(session)

        # Second skill added by hook_handler
        data["tool_input"] = {"skill": "delegating-to-agents"}
        data["loaded_skills"] = ["overcode", "delegating-to-agents"]
        (state_dir / "hook_state_test-agent.json").write_text(json.dumps(data))
        detector.detect_status(session)

        assert detector.get_loaded_skills("test-agent") == ["delegating-to-agents", "overcode"]

    def test_non_skill_tool_not_tracked(self, tmp_path):
        """Non-Skill tool_use events don't add to loaded_skills."""
        state_dir = tmp_path / "sessions" / "agents"
        state_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "event": "PreToolUse",
            "timestamp": time.time(),
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        }
        (state_dir / "hook_state_test-agent.json").write_text(json.dumps(data))
        mock_tmux = create_mock_tmux_with_content("agents", 1, "some content")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        detector.detect_status(session)

        assert detector.get_loaded_skills("test-agent") == []

    def test_no_hook_state_returns_empty(self, tmp_path):
        """No hook state returns empty skill list."""
        state_dir = tmp_path / "sessions" / "agents"
        state_dir.mkdir(parents=True)
        detector = HookStatusDetector("agents", state_dir=state_dir)

        assert detector.get_loaded_skills("nonexistent") == []


def _append_event_log(state_dir: Path, session_name: str, events: list) -> None:
    """Write events as JSONL to the event log.

    events is a list of (event_name, timestamp) tuples; order preserved.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / f"hook_events_{session_name}.jsonl"
    lines = []
    for name, ts in events:
        lines.append(json.dumps({"event": name, "timestamp": ts}))
    path.write_text("\n".join(lines) + "\n")


class TestStickyGreen:
    """Sticky-green upgrade avoids flicker on fast-turn Stops (#448)."""

    def test_recent_running_event_upgrades_stop_to_running(self, tmp_path):
        """A Stop with a RUNNING-class event within the window → RUNNING."""
        state_dir = tmp_path / "sessions" / "agents"
        now = time.time()
        # Latest snapshot is Stop, but the log shows a burst that ended <1s ago
        _write_hook_state(state_dir, "test-agent", "Stop", timestamp=now)
        _append_event_log(state_dir, "test-agent", [
            ("UserPromptSubmit", now - 2.0),
            ("PreToolUse", now - 1.2),
            ("PostToolUse", now - 0.3),
            ("Stop", now),
        ])
        mock_tmux = create_mock_tmux_with_content("agents", 1, "pane content")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, _, _ = detector.detect_status(session)

        assert status == STATUS_RUNNING

    def test_old_running_event_leaves_stop_as_waiting(self, tmp_path):
        """If last RUNNING-class event is older than the window, stay yellow."""
        state_dir = tmp_path / "sessions" / "agents"
        now = time.time()
        _write_hook_state(state_dir, "test-agent", "Stop", timestamp=now)
        _append_event_log(state_dir, "test-agent", [
            ("PostToolUse", now - 30.0),  # well outside 1.5s window
            ("Stop", now - 29.5),
        ])
        mock_tmux = create_mock_tmux_with_content("agents", 1, "pane content")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, _, _ = detector.detect_status(session)

        assert status == STATUS_WAITING_USER

    def test_no_event_log_stays_waiting(self, tmp_path):
        """Missing event log file → no upgrade, Stop stays WAITING_USER."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "Stop")
        # No event log written
        mock_tmux = create_mock_tmux_with_content("agents", 1, "pane content")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, _, _ = detector.detect_status(session)

        assert status == STATUS_WAITING_USER

    def test_interrupt_prompt_suppresses_sticky_green(self, tmp_path):
        """Real user-interrupt must not be papered over by sticky-green."""
        state_dir = tmp_path / "sessions" / "agents"
        now = time.time()
        _write_hook_state(state_dir, "test-agent", "Stop", timestamp=now)
        _append_event_log(state_dir, "test-agent", [
            ("PreToolUse", now - 0.5),
            ("Stop", now),
        ])
        pane = "running some command\nInterrupted · What should Claude do instead?\n"
        mock_tmux = create_mock_tmux_with_content("agents", 1, pane)

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        status, _, _ = detector.detect_status(session)

        assert status == STATUS_WAITING_USER

    def test_child_oversight_also_upgrades(self, tmp_path):
        """Child agents get WAITING_OVERSIGHT instead of WAITING_USER — upgrade must still apply."""
        state_dir = tmp_path / "sessions" / "agents"
        now = time.time()
        _write_hook_state(state_dir, "test-agent", "Stop", timestamp=now)
        _append_event_log(state_dir, "test-agent", [
            ("PostToolUse", now - 0.2),
            ("Stop", now),
        ])
        mock_tmux = create_mock_tmux_with_content("agents", 1, "pane content")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        session.parent_session_id = "parent-123"
        status, _, _ = detector.detect_status(session)

        assert status == STATUS_RUNNING

    def test_status_detail_cached_after_detect(self, tmp_path):
        """detect_status populates _status_details for the ⏰ column to read."""
        state_dir = tmp_path / "sessions" / "agents"
        _write_hook_state(state_dir, "test-agent", "UserPromptSubmit")
        mock_tmux = create_mock_tmux_with_content("agents", 1, "")

        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        detector.detect_status(session)

        detail = detector.get_status_detail("test-agent")
        assert detail is not None
        assert detail.color == "color_green"
        assert detail.badges[0].kind == "generating"

    def test_read_recent_events_skips_partial_lines(self, tmp_path):
        """Mid-rotation partial lines are tolerated."""
        state_dir = tmp_path / "sessions" / "agents"
        state_dir.mkdir(parents=True)
        now = time.time()
        path = state_dir / "hook_events_test-agent.jsonl"
        path.write_text(
            "{corrupt partial\n"
            + json.dumps({"event": "PreToolUse", "timestamp": now - 0.5}) + "\n"
            + "{\"event\":\"Stop\",\"timestamp\":"  # truncated final line
        )
        detector = HookStatusDetector("agents", state_dir=state_dir)
        events = detector._read_recent_events("test-agent")
        # Only the one complete JSON line should survive parsing.
        assert len(events) == 1
        assert events[0]["event"] == "PreToolUse"


# =============================================================================
# The rule table: TickContext → Verdict (colour, badges) → legacy status
# =============================================================================

NOW = 1_800_000_000.0


def _decide(event, child=False, now=NOW, **kw):
    """Run the rules over a context: ``state`` extends the hook state, the rest are signals."""
    state = {"event": event, "timestamp": now}
    state.update(kw.pop("state", {}))
    return decide(TickContext(state, now=now, is_child=child, **kw))


def _detail(event, **kw):
    return _decide(event, **kw)[0].detail()


class TestVerdicts:
    """Each hook event and signal → (colour, badges), and the enum derived from it."""

    # ---- GREEN — acting events ----
    def test_user_prompt_submit_green_generating(self):
        d = _detail("UserPromptSubmit")
        assert d.color == STATUS_COLOR_GREEN
        assert [b.kind for b in d.badges] == ["generating"]
        assert d.legacy_status == STATUS_RUNNING

    def test_pre_tool_use_green_with_tool_label(self):
        d = _detail("PreToolUse", state={"tool_name": "Read"})
        assert d.color == STATUS_COLOR_GREEN
        assert d.badges[0].kind == "tool"
        assert d.badges[0].label == "Read"

    def test_bash_blocked_on_ci_shows_blocked_ci(self):
        d = _detail("PreToolUse", state={
            "tool_name": "Bash", "foreground": {"kind": "tool", "tool": "Bash", "blocked_on": "ci"}})
        assert d.color == STATUS_COLOR_GREEN
        assert d.badges[0].kind == "blocked_ci"

    def test_sleep_is_green_blocked_sleep_and_busy_sleeping(self):
        state = {"timestamp": NOW, "tool_name": "Bash",
                 "foreground": {"kind": "tool", "tool": "Bash", "blocked_on": "sleep"}}
        d = _detail("PreToolUse", state=state, sleep_seconds=60, now=NOW + 1)
        assert d.color == STATUS_COLOR_GREEN
        assert d.badges[0].kind == "blocked_sleep"
        # It ends 60 s after the PreToolUse: a fixed moment that counts down
        assert d.badges[0].eta_at == pytest.approx(NOW + 60.0)
        assert d.badges[0].eta_seconds == pytest.approx(59.0)
        assert d.legacy_status == STATUS_BUSY_SLEEPING

    def test_a_sleep_that_is_over_is_a_plain_tool(self):
        """blocked_on alone is not sleeping: the sleep rule decides (past its end, not)."""
        state = {"tool_name": "Bash", "foreground": {"kind": "tool", "tool": "Bash", "blocked_on": "sleep"}}
        d = _detail("PreToolUse", state=state, sleep_seconds=None)
        assert (d.color, d.badges[0].kind, d.legacy_status) == (STATUS_COLOR_GREEN, "tool", STATUS_RUNNING)

    def test_a_sleeping_agents_published_detail_holds_still(self):
        """Published every tick, the badge must not change while it counts
        down: eta_at was now + duration, a delta every tick (review item 14)."""
        from overcode.episodes import status_detail_view

        state = {"tool_name": "Bash", "foreground": {"kind": "tool", "tool": "Bash", "blocked_on": "sleep"}}
        views = []
        for t in (0.0, 2.0, 4.0):
            d = decide(TickContext({"event": "PreToolUse", "timestamp": NOW, **state},
                                   now=NOW + t, sleep_seconds=60))[0].detail()
            views.append(status_detail_view(d, NOW + t))
        assert views[0] == views[1] == views[2]

    # ---- ORANGE — approval ----
    def test_permission_request_is_orange(self):
        d = _detail("PermissionRequest", state={"tool_name": "Bash"})
        assert d.color == STATUS_COLOR_ORANGE
        assert d.badges[0].kind == "permission"
        assert d.badges[0].label == "Bash"
        assert d.legacy_status == STATUS_WAITING_APPROVAL

    # ---- REST — a child whose turn is over ----
    def test_child_stop_is_at_rest(self):
        d = _detail("Stop", child=True)
        assert d.color == STATUS_COLOR_REST
        assert d.badges == []
        assert d.legacy_status == STATUS_WAITING_OVERSIGHT

    # ---- YELLOW — armed ----
    def test_stop_with_obligation_is_yellow(self):
        d = _detail("Stop", state={"pending_obligations": [
            {"kind": "cron", "cron_id": "c1", "label": "@hourly"}]})
        assert d.color == STATUS_COLOR_YELLOW
        assert d.badges[0].kind == "cron"
        # Only scheduled: nothing runs, so it is still waiting (not green time)
        assert d.legacy_status == STATUS_WAITING_USER

    def test_stop_with_a_running_monitor_is_busy_sleeping(self):
        d = _detail("Stop", state={"pending_obligations": [
            {"kind": "cron", "cron_id": "c1"}, {"kind": "monitor", "tool_use_id": "m"}]})
        assert d.color == STATUS_COLOR_YELLOW
        assert d.legacy_status == STATUS_BUSY_SLEEPING

    def test_obligations_stack_by_kind(self):
        d = _detail("Stop", state={"pending_obligations": [
            {"kind": "monitor", "tool_use_id": "a"},
            {"kind": "monitor", "tool_use_id": "b"},
            {"kind": "bg_task", "tool_use_id": "c"},
        ]})
        assert d.color == STATUS_COLOR_YELLOW
        assert {b.kind: b.count for b in d.badges} == {"monitor": 2, "bg_task": 1}

    def test_synthetic_monitor_count_from_pane(self):
        """A Monitor visible in the pane but not in obligations still shows up."""
        d = _detail("Stop", monitors=2)
        assert d.color == STATUS_COLOR_YELLOW
        assert (d.badges[0].kind, d.badges[0].count) == ("monitor", 2)

    def test_a_child_with_only_a_cron_is_at_rest(self):
        d = _detail("Stop", child=True, state={"pending_obligations": [{"kind": "cron"}]})
        assert d.color == STATUS_COLOR_REST

    # ---- A parent waiting on its children ----
    def test_a_parent_whose_children_work_is_yellow(self):
        verdict, rule = _decide("Stop", children=2)
        assert rule == "armed"
        assert (verdict.color, verdict.legacy_status) == (STATUS_COLOR_YELLOW, STATUS_BUSY_SLEEPING)
        assert [(b.kind, b.count) for b in verdict.badges] == [("children", 2)]
        assert verdict.activity == "Waiting on 2 children"
        assert _decide("Stop", children=1)[0].activity == "Waiting on 1 child"

    def test_a_parent_whose_children_rest_needs_input(self):
        d = _detail("Stop", children=0)
        assert (d.color, d.legacy_status) == (STATUS_COLOR_RED, STATUS_WAITING_USER)

    def test_an_interrupted_parent_stays_red_while_children_work(self):
        d = _detail("Stop", children=2, interrupted=True)
        assert (d.color, d.badges[0].kind) == (STATUS_COLOR_RED, "awaiting_input")

    def test_a_working_parent_stays_green_while_children_work(self):
        d = _detail("PreToolUse", children=2, state={"tool_name": "Read"})
        assert d.color == STATUS_COLOR_GREEN

    def test_a_child_whose_own_children_work_is_yellow(self):
        d = _detail("Stop", child=True, children=1)
        assert (d.color, d.legacy_status) == (STATUS_COLOR_YELLOW, STATUS_BUSY_SLEEPING)

    def test_a_child_with_background_work_is_armed(self):
        d = _detail("Stop", child=True, monitors=1)
        assert (d.color, d.legacy_status) == (STATUS_COLOR_YELLOW, STATUS_BUSY_SLEEPING)

    # ---- RED — needs input ----
    def test_stop_with_no_obligations_is_red_awaiting(self):
        d = _detail("Stop")
        assert d.color == STATUS_COLOR_RED
        assert d.badges[0].kind == "awaiting_input"
        assert d.legacy_status == STATUS_WAITING_USER

    def test_interrupt_is_red(self):
        d = _detail("PreToolUse", state={"tool_name": "Bash"}, interrupted=True)
        assert (d.color, d.badges[0].kind, d.legacy_status) == (
            STATUS_COLOR_RED, "awaiting_input", STATUS_WAITING_USER)

    def test_stop_failure_is_red_error(self):
        d = _detail("StopFailure")
        assert (d.color, d.badges[0].kind, d.legacy_status) == (STATUS_COLOR_RED, "error", STATUS_ERROR)

    def test_rejected_prompt_is_labelled(self):
        d = _detail("UserPromptSubmitRejected")
        assert (d.badges[0].kind, d.badges[0].label, d.legacy_status) == ("error", "rejected", STATUS_ERROR)

    # ---- Priority ----
    def test_red_wins_over_yellow_when_interrupt_and_obligation(self):
        """Mid-tool agent with a cron registered AND interrupted → RED, not YELLOW."""
        d = _detail("PreToolUse", state={"tool_name": "Bash", "pending_obligations": [{"kind": "cron"}]},
                    interrupted=True, monitors=1)
        assert d.color == STATUS_COLOR_RED

    def test_orange_wins_over_yellow(self):
        """Permission request while a cron is armed → ORANGE."""
        d = _detail("PermissionRequest", state={"tool_name": "Bash", "pending_obligations": [{"kind": "cron"}]},
                    monitors=1)
        assert (d.color, d.legacy_status) == (STATUS_COLOR_ORANGE, STATUS_WAITING_APPROVAL)

    def test_green_wins_over_yellow(self):
        """Acting agent with a cron armed → GREEN, badges show tool not cron."""
        d = _detail("PreToolUse", state={"tool_name": "Read", "pending_obligations": [{"kind": "cron"}]})
        assert d.color == STATUS_COLOR_GREEN
        assert d.badges[0].kind == "tool"


class TestRules:
    """Each named rule: the context that triggers it, what it decides, and the phase."""

    CASES = [
        # (name, event, context, rule that wins, colour, lead badge, legacy)
        ("acting event", "PostToolUse", {}, "base",
         STATUS_COLOR_GREEN, "tool", STATUS_RUNNING),
        ("session end at a shell", "SessionEnd", {"clean": "user@host proj %"}, "session_end",
         None, None, STATUS_TERMINATED),
        ("session end after /clear", "SessionEnd", {"clean": "❯ "}, "session_end",
         STATUS_COLOR_RED, "awaiting_input", STATUS_WAITING_USER),
        ("session end, monitors do not lift it", "SessionEnd", {"clean": "❯ ", "monitors": 2},
         "session_end", STATUS_COLOR_RED, "awaiting_input", STATUS_WAITING_USER),
        ("dead shell", "Stop", {"dead_shell": True, "monitors": 1}, "dead_shell",
         None, None, STATUS_TERMINATED),
        ("question", "PermissionRequest", {"state": {"tool_name": "AskUserQuestion"}}, "question",
         STATUS_COLOR_ORANGE, "ask_question", STATUS_WAITING_APPROVAL),
        ("question, monitors do not lift it", "PermissionRequest",
         {"state": {"tool_name": "AskUserQuestion"}, "monitors": 1}, "question",
         STATUS_COLOR_ORANGE, "ask_question", STATUS_WAITING_APPROVAL),
        ("plan", "PermissionRequest", {"state": {"tool_name": "ExitPlanMode"}}, "base",
         STATUS_COLOR_ORANGE, "plan_approval", STATUS_WAITING_APPROVAL),
        ("answered permission", "PermissionRequest",
         {"state": {"tool_name": "Bash"}, "permission_answered": True}, "permission_answered",
         STATUS_COLOR_GREEN, "tool", STATUS_RUNNING),
        ("answered question", "PermissionRequest",
         {"state": {"tool_name": "AskUserQuestion"}, "permission_answered": True}, "permission_answered",
         STATUS_COLOR_GREEN, "tool", STATUS_RUNNING),
        ("sleep", "PreToolUse", {"sleep_seconds": 30}, "sleep",
         STATUS_COLOR_GREEN, "blocked_sleep", STATUS_BUSY_SLEEPING),
        ("interrupt", "PreToolUse", {"interrupted": True}, "interrupt",
         STATUS_COLOR_RED, "awaiting_input", STATUS_WAITING_USER),
        ("interrupt ends a sleep", "PreToolUse", {"sleep_seconds": 30, "interrupted": True}, "interrupt",
         STATUS_COLOR_RED, "awaiting_input", STATUS_WAITING_USER),
        ("interrupt, monitors do not lift it", "UserPromptSubmit", {"interrupted": True, "monitors": 1},
         "interrupt", STATUS_COLOR_RED, "awaiting_input", STATUS_WAITING_USER),
        ("an interrupt marker leaves a permission orange", "PermissionRequest", {"interrupted": True},
         "base", STATUS_COLOR_ORANGE, "permission", STATUS_WAITING_APPROVAL),
        ("an interrupted child needs the person, not a report", "Stop",
         {"interrupted": True, "child": True}, "interrupt",
         STATUS_COLOR_RED, "awaiting_input", STATUS_WAITING_USER),
        ("an interrupted Stop, monitors do not lift it", "Stop", {"interrupted": True, "monitors": 1},
         "interrupt", STATUS_COLOR_RED, "awaiting_input", STATUS_WAITING_USER),
        ("turn ended without a Stop", "UserPromptSubmit", {"idle_confirmed": True}, "idle_confirmed",
         STATUS_COLOR_RED, "awaiting_input", STATUS_WAITING_USER),
        ("a child's turn ended without a Stop", "PreToolUse", {"idle_confirmed": True, "child": True},
         "idle_confirmed", STATUS_COLOR_REST, None, STATUS_WAITING_OVERSIGHT),
        ("turn ended without a Stop, monitor armed", "UserPromptSubmit",
         {"idle_confirmed": True, "monitors": 1}, "armed",
         STATUS_COLOR_YELLOW, "monitor", STATUS_BUSY_SLEEPING),
        ("sticky green", "Stop", {"running_age": 0.5}, "sticky_green",
         STATUS_COLOR_GREEN, "tool", STATUS_RUNNING),
        ("sticky green for a child", "Stop", {"running_age": 0.5, "child": True}, "sticky_green",
         STATUS_COLOR_GREEN, "tool", STATUS_RUNNING),
        ("an old burst is a real Stop", "Stop", {"running_age": 5.0}, "base",
         STATUS_COLOR_RED, "awaiting_input", STATUS_WAITING_USER),
        ("sticky green beats monitors", "Stop", {"running_age": 0.5, "monitors": 1}, "sticky_green",
         STATUS_COLOR_GREEN, "tool", STATUS_RUNNING),
        ("armed: shells", "Stop", {"shells": 2}, "armed",
         STATUS_COLOR_YELLOW, "bg_task", STATUS_BUSY_SLEEPING),
        ("armed: subagents", "Stop", {"subagents": 1}, "armed",
         STATUS_COLOR_YELLOW, "subagent", STATUS_BUSY_SLEEPING),
        ("armed: codex Interrupt event", "Interrupt", {"monitors": 1}, "armed",
         STATUS_COLOR_YELLOW, "monitor", STATUS_BUSY_SLEEPING),
        ("an error is not armed", "StopFailure", {"monitors": 1}, "base",
         STATUS_COLOR_RED, "error", STATUS_ERROR),
        ("overdue", "Stop",
         {"state": {"pending_obligations": [{"kind": "schedule_wakeup", "eta_absolute": NOW - 600}]}},
         "overdue", STATUS_COLOR_RED, "overdue", STATUS_WAITING_USER),
        ("overdue child stays at rest", "Stop",
         {"child": True,
          "state": {"pending_obligations": [{"kind": "schedule_wakeup", "eta_absolute": NOW - 600}]}},
         "base", STATUS_COLOR_REST, None, STATUS_WAITING_OVERSIGHT),
        ("armed: children", "Stop", {"children": 3}, "armed",
         STATUS_COLOR_YELLOW, "children", STATUS_BUSY_SLEEPING),
        ("unknown event", "Notification", {}, "base",
         STATUS_COLOR_RED, "awaiting_input", STATUS_WAITING_USER),
    ]

    @pytest.mark.parametrize("name,event,ctx,rule,color,lead,legacy", CASES, ids=[c[0] for c in CASES])
    def test_rule(self, name, event, ctx, rule, color, lead, legacy):
        verdict, winner = _decide(event, **ctx)
        assert winner == rule
        assert verdict.color == color
        assert verdict.lead == lead
        assert verdict.legacy_status == legacy
        detail = verdict.detail()
        if color is None:
            assert detail is None
        else:
            assert (detail.color, detail.legacy_status) == (color, legacy)

    def test_rule_names_are_unique_and_ordered(self):
        names = [name for name, _ in RULES]
        assert len(names) == len(set(names))
        assert names.index("interrupt") < names.index("idle_confirmed") < names.index("armed")

    @pytest.mark.parametrize("color,lead,legacy", [
        (None, None, STATUS_TERMINATED),
        (STATUS_COLOR_GREEN, "tool", STATUS_RUNNING),
        (STATUS_COLOR_GREEN, "generating", STATUS_RUNNING),
        (STATUS_COLOR_GREEN, "blocked_ci", STATUS_RUNNING),
        (STATUS_COLOR_GREEN, "blocked_sleep", STATUS_BUSY_SLEEPING),
        (STATUS_COLOR_YELLOW, "monitor", STATUS_BUSY_SLEEPING),
        (STATUS_COLOR_YELLOW, "cron", STATUS_BUSY_SLEEPING),
        (STATUS_COLOR_REST, None, STATUS_WAITING_OVERSIGHT),
        (STATUS_COLOR_ORANGE, "permission", STATUS_WAITING_APPROVAL),
        (STATUS_COLOR_ORANGE, "plan_approval", STATUS_WAITING_APPROVAL),
        (STATUS_COLOR_RED, "error", STATUS_ERROR),
        (STATUS_COLOR_RED, "awaiting_input", STATUS_WAITING_USER),
        (STATUS_COLOR_ORANGE, "ask_question", STATUS_WAITING_APPROVAL),
        (STATUS_COLOR_RED, "overdue", STATUS_WAITING_USER),
    ])
    def test_legacy_table(self, color, lead, legacy):
        assert legacy_status_for(color, lead) == legacy

    @pytest.mark.parametrize("name,event,ctx,rule,color,lead,legacy",
                             [c for c in CASES if c[4] is not None], ids=[c[0] for c in CASES if c[4]])
    def test_polling_synthesis_agrees_with_the_table(self, name, event, ctx, rule, color, lead, legacy):
        """The enum and activity alone give back the verdict's colour."""
        verdict, _ = _decide(event, **ctx)
        synthesized = synthesize_status_detail_from_legacy(verdict.legacy_status, verdict.activity)
        assert synthesized.color == color


class TestPhaseLabel:
    """The phase names the hook event and the rule that decided (daemon log)."""

    def _phase(self, tmp_path, event, pane="", parent=None, **state):
        state_dir = tmp_path / "sessions" / "agents"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "hook_state_a1.json").write_text(json.dumps(
            {"event": event, "timestamp": NOW - 5, **state}))
        tmux = MockTmux()
        tmux.set_pane_content("agents", "w1", pane)
        detector = HookStatusDetector("agents", tmux=tmux, state_dir=state_dir)
        session = create_mock_session(name="a1", tmux_window="w1")
        session.parent_session_id = parent
        detector.detect_status(session, now=NOW)
        return detector._last_detect_phase[session.id]

    def test_the_event_alone(self, tmp_path):
        assert self._phase(tmp_path, "PreToolUse", tool_name="Read") == "hook:PreToolUse"

    def test_an_override_names_its_rule(self, tmp_path):
        assert self._phase(tmp_path, "Stop", pane="❯ \n  ⏵⏵ bypass permissions on · 2 shells") \
            == "hook:Stop+armed"
        assert self._phase(tmp_path, "PreToolUse", tool_name="Bash",
                           tool_input={"command": "sleep 60"}) == "hook:PreToolUse+sleep"
        assert self._phase(tmp_path, "PreToolUse", tool_name="Bash",
                           pane="⎿  Interrupted · What should Claude do instead?") \
            == "hook:PreToolUse+interrupt"
        assert self._phase(tmp_path, "PermissionRequest", tool_name="AskUserQuestion") \
            == "hook:PermissionRequest+question"
        assert self._phase(tmp_path, "Stop", pane="user@host proj %") == "hook:Stop+dead_shell"

    def test_the_last_rule_to_decide_wins(self, tmp_path):
        """A sleep that was interrupted: the interrupt decided, not the sleep."""
        assert self._phase(tmp_path, "PreToolUse", tool_name="Bash",
                           tool_input={"command": "sleep 60"},
                           pane="⎿  Interrupted · What should Claude do instead?") \
            == "hook:PreToolUse+interrupt"


# =============================================================================
# Phase 2 task 6: legacy heartbeat status bridge
# =============================================================================

from overcode.status_constants import (  # noqa: E402
    STATUS_RUNNING_HEARTBEAT,
    STATUS_WAITING_HEARTBEAT,
    StatusBadge,
    StatusDetail,
)


class TestBackgroundWork:
    """Background shells and subagents keep a stopped agent yellow (#507)."""

    def _session(self, parent_session_id=None):
        s = create_mock_session(name="a1")
        s.parent_session_id = parent_session_id
        return s

    def _detail(self, state, background_shells=0, subagent_count=None):
        return decide(TickContext(state, now=time.time(), shells=background_shells,
                                  subagents=subagent_count))[0].detail()

    def test_shells_from_the_status_bar_are_a_bg_task_badge(self):
        d = self._detail({"event": "Stop", "timestamp": time.time()}, background_shells=2)
        assert d.color == STATUS_COLOR_YELLOW
        assert [(b.kind, b.count) for b in d.badges] == [("bg_task", 2)]

    def test_shells_already_tracked_as_obligations_are_not_counted_twice(self):
        state = {"event": "Stop", "timestamp": time.time(),
                 "pending_obligations": [{"kind": "bg_task", "added_at": time.time()}]}
        d = self._detail(state, background_shells=2)
        assert [(b.kind, b.count) for b in d.badges] == [("bg_task", 2)]

    def test_subagents_are_a_subagent_badge(self):
        d = self._detail({"event": "Stop", "timestamp": time.time()}, subagent_count=1)
        assert d.color == STATUS_COLOR_YELLOW
        assert [(b.kind, b.count) for b in d.badges] == [("subagent", 1)]

    def test_live_subagent_count_drops_entries_unheard_from_for_an_hour(self):
        now = time.time()
        state = {"subagents": {
            "a1": {"last_seen": now - 10},
            "a2": {"last_seen": now - 4000},
            "a3": "garbage",
        }}
        assert _live_subagent_count(state, now) == 1
        assert _live_subagent_count({}, now) == 0

    @staticmethod
    def _write(state_dir, state):
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "hook_state_a1.json").write_text(json.dumps(state))

    def test_detect_status_lifts_a_stop_with_a_live_subagent(self, tmp_path):
        state_dir = tmp_path / "agents"
        self._write(state_dir, {
            "event": "Stop", "timestamp": time.time() - 30,
            "subagents": {"x": {"type": "Explore", "started_at": time.time() - 40,
                                "last_seen": time.time() - 5}},
        })
        tmux = MagicMock()
        tmux.capture_pane.return_value = "⏺ Running.\n❯ \n  ⏵⏵ bypass permissions on"
        detector = HookStatusDetector("agents", tmux=tmux, state_dir=state_dir)
        status, activity, _ = detector.detect_status(self._session())
        assert status == STATUS_BUSY_SLEEPING
        assert activity == "Waiting on 1 background agent"
        assert detector.get_status_detail("a1").color == STATUS_COLOR_YELLOW

    def test_a_subagent_whose_own_shells_exited_no_longer_holds_the_stop(self, tmp_path):
        """Claude did not resume it when its shells ended, so no hook came:
        the shells' output files say they exited (seen live: 🟡 for 2 h)."""
        state_dir = tmp_path / "agents"
        now = time.time()
        self._write(state_dir, {
            "event": "Stop", "timestamp": now - 600, "swarm": {"bg_at": now - 600},
            "subagents": {"x": {"type": "general-purpose", "started_at": now - 900,
                                "last_seen": now - 600, "own_work": True,
                                "shells": ["bshell01"]}},
        })
        tasks = tmp_path / "claude-tmp" / "-proj" / "sess" / "tasks"
        tasks.mkdir(parents=True)
        tmux = MagicMock()
        tmux.capture_pane.return_value = "⏺ Done.\n❯ \n  ⏵⏵ bypass permissions on"
        detector = HookStatusDetector("agents", tmux=tmux, state_dir=state_dir)
        from overcode.swarm_files import ShellEnds
        detector.shell_ended = ShellEnds(root=str(tmp_path / "claude-tmp"))
        (tasks / "bshell01.output").write_text("running\n")
        assert detector.detect_status(self._session())[1] == "Waiting on 1 background agent"
        (tasks / "bshell01.output").write_text("running\n\n[exited with code 0]\n")
        status, activity, _ = detector.detect_status(self._session())
        assert status != STATUS_BUSY_SLEEPING and "background agent" not in activity
        assert detector.get_swarm_rollup("a1")["ok"] == 1

    def test_detect_status_lifts_a_stop_with_background_shells(self, tmp_path):
        state_dir = tmp_path / "agents"
        self._write(state_dir, {"event": "Stop", "timestamp": time.time() - 30})
        tmux = MagicMock()
        tmux.capture_pane.return_value = "⏺ Running.\n❯ \n  ⏵⏵ bypass permissions on · 2 shells"
        detector = HookStatusDetector("agents", tmux=tmux, state_dir=state_dir)
        status, activity, _ = detector.detect_status(self._session())
        assert status == STATUS_BUSY_SLEEPING
        assert activity == "Waiting on 2 background shells"

    def test_subagent_events_do_not_extend_the_sticky_green_window(self, tmp_path):
        state_dir = tmp_path / "agents"
        now = time.time()
        state_dir.mkdir(parents=True)
        (state_dir / "hook_events_a1.jsonl").write_text("\n".join([
            json.dumps({"event": "PostToolUse", "timestamp": now - 30}),
            json.dumps({"event": "Stop", "timestamp": now - 29}),
            json.dumps({"event": "PostToolUse", "timestamp": now - 0.5, "agent_id": "x"}),
        ]) + "\n")
        detector = HookStatusDetector("agents", state_dir=state_dir)
        assert detector._most_recent_running_event_age("a1", now=now) == pytest.approx(30)


class TestAugmentWithLegacyHeartbeat:
    """augment_with_legacy_heartbeat bridges old enum overrides into the
    two-column model so the daemon can keep setting STATUS_*_HEARTBEAT while
    the column reflects YELLOW armed / GREEN running with a heartbeat badge.
    """

    def test_waiting_heartbeat_with_none_detail_yields_yellow_heartbeat(self):
        d = augment_with_legacy_heartbeat(None, STATUS_WAITING_HEARTBEAT)
        assert d.color == STATUS_COLOR_YELLOW
        assert [b.kind for b in d.badges] == ["heartbeat"]

    def test_waiting_heartbeat_upgrades_red_to_yellow(self):
        red = StatusDetail(STATUS_COLOR_RED, [StatusBadge(kind="awaiting_input")])
        d = augment_with_legacy_heartbeat(red, STATUS_WAITING_HEARTBEAT)
        assert d.color == STATUS_COLOR_YELLOW
        assert [b.kind for b in d.badges] == ["heartbeat"]

    def test_waiting_heartbeat_appends_to_existing_yellow(self):
        existing = StatusDetail(STATUS_COLOR_YELLOW, [StatusBadge(kind="cron")])
        d = augment_with_legacy_heartbeat(existing, STATUS_WAITING_HEARTBEAT)
        kinds = [b.kind for b in d.badges]
        assert "cron" in kinds and "heartbeat" in kinds
        assert d.color == STATUS_COLOR_YELLOW

    def test_waiting_heartbeat_leaves_orange_alone(self):
        existing = StatusDetail(STATUS_COLOR_ORANGE, [StatusBadge(kind="permission")])
        d = augment_with_legacy_heartbeat(existing, STATUS_WAITING_HEARTBEAT)
        assert d.color == STATUS_COLOR_ORANGE
        assert [b.kind for b in d.badges] == ["permission"]

    def test_running_heartbeat_with_none_yields_green_heartbeat(self):
        d = augment_with_legacy_heartbeat(None, STATUS_RUNNING_HEARTBEAT)
        assert d.color == STATUS_COLOR_GREEN
        assert [b.kind for b in d.badges] == ["heartbeat"]

    def test_running_heartbeat_appends_to_green_tool_badge(self):
        existing = StatusDetail(STATUS_COLOR_GREEN, [StatusBadge(kind="tool", label="Read")])
        d = augment_with_legacy_heartbeat(existing, STATUS_RUNNING_HEARTBEAT)
        kinds = [b.kind for b in d.badges]
        assert kinds == ["tool", "heartbeat"]
        assert d.color == STATUS_COLOR_GREEN

    def test_running_heartbeat_dedupes_existing_heartbeat_badge(self):
        existing = StatusDetail(STATUS_COLOR_GREEN, [
            StatusBadge(kind="tool", label="Read"),
            StatusBadge(kind="heartbeat"),
        ])
        d = augment_with_legacy_heartbeat(existing, STATUS_RUNNING_HEARTBEAT)
        kinds = [b.kind for b in d.badges]
        assert kinds.count("heartbeat") == 1

    def test_non_heartbeat_status_passes_through(self):
        existing = StatusDetail(STATUS_COLOR_GREEN, [StatusBadge(kind="tool", label="Read")])
        d = augment_with_legacy_heartbeat(existing, STATUS_RUNNING)
        assert d is existing

    def test_none_in_none_out_for_non_heartbeat(self):
        assert augment_with_legacy_heartbeat(None, STATUS_RUNNING) is None


class TestSynthesizeStatusDetailFromLegacy:
    """Polling-mode (or hooks-not-yet-fired) synthesis from the legacy enum."""

    def test_running_with_using_activity_extracts_tool_label(self):
        d = synthesize_status_detail_from_legacy(STATUS_RUNNING, "Using Read")
        assert d.color == STATUS_COLOR_GREEN
        assert d.badges[0].kind == "tool"
        assert d.badges[0].label == "Read"

    def test_running_with_bash_activity_labels_bash(self):
        d = synthesize_status_detail_from_legacy(STATUS_RUNNING, "Bash: gh run watch 1")
        assert d.badges[0].label == "Bash"

    def test_running_without_activity_has_no_label(self):
        d = synthesize_status_detail_from_legacy(STATUS_RUNNING, "")
        assert d.badges[0].kind == "tool"
        assert d.badges[0].label is None

    def test_busy_sleeping_is_green_blocked_sleep(self):
        d = synthesize_status_detail_from_legacy(STATUS_BUSY_SLEEPING)
        assert d.color == STATUS_COLOR_GREEN
        assert d.badges[0].kind == "blocked_sleep"

    def test_waiting_approval_is_orange_permission(self):
        d = synthesize_status_detail_from_legacy(STATUS_WAITING_APPROVAL)
        assert d.color == STATUS_COLOR_ORANGE
        assert d.badges[0].kind == "permission"

    def test_waiting_oversight_is_at_rest(self):
        d = synthesize_status_detail_from_legacy(STATUS_WAITING_OVERSIGHT)
        assert d.color == STATUS_COLOR_REST
        assert d.badges == []

    def test_waiting_on_children_is_yellow(self):
        d = synthesize_status_detail_from_legacy(STATUS_BUSY_SLEEPING, "Waiting on 2 children")
        assert d.color == STATUS_COLOR_YELLOW
        assert (d.badges[0].kind, d.badges[0].count) == ("children", 2)

    def test_unknown_has_no_colour(self):
        assert synthesize_status_detail_from_legacy(STATUS_UNKNOWN) is None

    def test_waiting_heartbeat_is_yellow_heartbeat(self):
        d = synthesize_status_detail_from_legacy(STATUS_WAITING_HEARTBEAT)
        assert d.color == STATUS_COLOR_YELLOW
        assert d.badges[0].kind == "heartbeat"

    def test_running_heartbeat_is_green_heartbeat(self):
        d = synthesize_status_detail_from_legacy(STATUS_RUNNING_HEARTBEAT)
        assert d.color == STATUS_COLOR_GREEN
        assert d.badges[0].kind == "heartbeat"

    def test_waiting_user_is_red_awaiting(self):
        d = synthesize_status_detail_from_legacy(STATUS_WAITING_USER)
        assert d.color == STATUS_COLOR_RED
        assert d.badges[0].kind == "awaiting_input"

    def test_error_is_red_error(self):
        d = synthesize_status_detail_from_legacy(STATUS_ERROR)
        assert d.color == STATUS_COLOR_RED
        assert d.badges[0].kind == "error"

    def test_terminated_returns_none(self):
        assert synthesize_status_detail_from_legacy(STATUS_TERMINATED) is None

    def test_asleep_returns_none(self):
        from overcode.status_constants import STATUS_ASLEEP
        assert synthesize_status_detail_from_legacy(STATUS_ASLEEP) is None


class TestHookDetectorCaptureGate:
    """get_pane_content consults an installed PaneCaptureGate; the raw capture is unchanged."""

    def test_gate_serves_cached_text_for_an_unchanged_pane(self, tmp_path):
        from overcode.pane_capture_gate import PaneCaptureGate

        state_dir = tmp_path / "sessions" / "agents"
        mock_tmux = create_mock_tmux_with_content("agents", 1, "first")
        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        assert detector.capture_gate is None
        gate = PaneCaptureGate()
        detector.capture_gate = gate
        gate.begin_loop()
        gate.plan(1, True)
        assert detector.get_pane_content(1) == "first"
        mock_tmux.set_pane_content("agents", 1, "second")
        gate.begin_loop()
        gate.plan(1, False)
        assert detector.get_pane_content(1) == "first"
        assert gate.raw_captures == 1 and gate.served_from_cache == 1

    def test_subprocess_fallback_command_is_unchanged(self, tmp_path):
        """Without a tmux interface the raw capture is the same plain
        ``capture-pane -p -S -N`` (no -e), gated or not."""
        from unittest.mock import MagicMock, patch

        state_dir = tmp_path / "sessions" / "agents"
        detector = HookStatusDetector("agents", state_dir=state_dir)
        with patch("overcode.hook_status_detector.subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stdout="pane\n")
            assert detector.get_pane_content("w1") == "pane\n"
            assert detector.get_pane_content("w1", num_lines=40) == "pane\n"
        assert run.call_args_list[0].args[0][-6:] == [
            "capture-pane", "-t", "agents:w1", "-p", "-S", f"-{detector.capture_lines}",
        ]
        assert run.call_args_list[1].args[0][-2:] == ["-S", "-40"]
        assert run.call_args.kwargs["timeout"] == 5


class TestRecentEventsCache:
    """_read_recent_events serves an unchanged log from its last parse (audit R12)."""

    def _detector(self, tmp_path):
        state_dir = tmp_path / "sessions" / "agents"
        state_dir.mkdir(parents=True)
        return HookStatusDetector("agents", state_dir=state_dir), state_dir

    def test_unchanged_log_is_one_stat_and_no_open(self, tmp_path, monkeypatch):
        import builtins
        detector, state_dir = self._detector(tmp_path)
        now = time.time()
        _append_event_log(state_dir, "a1", [("UserPromptSubmit", now - 2), ("PreToolUse", now - 1)])
        first = detector._read_recent_events("a1")
        assert [e["event"] for e in first] == ["UserPromptSubmit", "PreToolUse"]

        opens = []
        real_open = builtins.open

        def counting_open(file, *args, **kwargs):
            opens.append(str(file))
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", counting_open)
        again = detector._read_recent_events("a1")
        assert again == first
        assert not [o for o in opens if "hook_events" in o]

    def test_result_is_a_copy(self, tmp_path):
        detector, state_dir = self._detector(tmp_path)
        _append_event_log(state_dir, "a1", [("PreToolUse", time.time())])
        first = detector._read_recent_events("a1")
        first.clear()
        assert len(detector._read_recent_events("a1")) == 1

    def test_appended_event_is_seen(self, tmp_path):
        detector, state_dir = self._detector(tmp_path)
        now = time.time()
        _append_event_log(state_dir, "a1", [("PreToolUse", now - 1)])
        assert len(detector._read_recent_events("a1")) == 1
        with open(state_dir / "hook_events_a1.jsonl", "a") as f:
            f.write(json.dumps({"event": "Stop", "timestamp": now}) + "\n")
        events = detector._read_recent_events("a1")
        assert [e["event"] for e in events] == ["PreToolUse", "Stop"]

    def test_limit_is_part_of_the_key(self, tmp_path):
        detector, state_dir = self._detector(tmp_path)
        now = time.time()
        _append_event_log(state_dir, "a1", [("PreToolUse", now - i) for i in range(5, 0, -1)])
        assert len(detector._read_recent_events("a1")) == 5
        assert len(detector._read_recent_events("a1", limit=2)) == 2
        assert len(detector._read_recent_events("a1")) == 5

    def test_missing_log_is_empty_and_forgets_the_cache(self, tmp_path):
        detector, state_dir = self._detector(tmp_path)
        _append_event_log(state_dir, "a1", [("PreToolUse", time.time())])
        assert len(detector._read_recent_events("a1")) == 1
        (state_dir / "hook_events_a1.jsonl").unlink()
        assert detector._read_recent_events("a1") == []
        assert "a1" not in detector._events_cache


class TestDeadShellDetection:
    """An agent CLI that exits without a SessionEnd hook (#474).

    opencode's ``/exit`` (or any crash) emits no bus event, so the last hook
    state — a ``Stop`` — would keep the agent at waiting_user forever. The
    pane is the only witness: a bare shell prompt on the last line and no
    live-TUI hint anywhere near it means the process is gone.
    """

    EXITED_PANE = (
        "  Session   pong\n"
        "  Continue  opencode -s ses_f39d6ec47ffeWwbQwGLUIkx5MK\n"
        "user@host cap-proj %\n"
    )
    LIVE_OPENCODE_PANE = (
        "     pong\n"
        "     ▣  Build · GPT-4o mini · 2.9s\n"
        "  ┃\n"
        "  ┃  Build · GPT-4o mini OpenAI\n"
        "  ╹▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀\n"
        "   /proj   8.5K (1%) · $0.00  ctrl+p commands\n"
    )
    # A live Claude pane whose model output happens to end in a prompt-shaped
    # line: the input box and its hint follow, so it must stay alive.
    LIVE_CLAUDE_PANE = (
        "⏺ Bash(ssh host)\n"
        "  ⎿  user@host ~ %\n"
        "╭──────────────────────────────╮\n"
        "│ > \n"
        "╰──────────────────────────────╯\n"
        "  ? for shortcuts\n"
    )

    def _detector(self, tmp_path, pane, backend="opencode"):
        from overcode.status_patterns import get_patterns
        state_dir = tmp_path / "sessions" / "agents"
        tmux = MockTmux()
        tmux.set_pane_content("agents", "w1", pane)
        detector = HookStatusDetector(
            "agents", tmux=tmux, patterns=get_patterns(backend), state_dir=state_dir
        )
        return detector, state_dir

    def test_stop_then_shell_prompt_is_terminated(self, tmp_path):
        detector, state_dir = self._detector(tmp_path, self.EXITED_PANE)
        _write_hook_state(state_dir, "oc", "Stop")
        session = create_mock_session(name="oc", tmux_window="w1")
        status, activity, _ = detector.detect_status(session)
        assert status == STATUS_TERMINATED
        assert "shell prompt" in activity
        assert detector.get_status_detail("oc") is None

    def test_running_then_shell_prompt_is_terminated(self, tmp_path):
        # A crash mid-turn leaves PreToolUse behind; the shell prompt wins.
        detector, state_dir = self._detector(tmp_path, self.EXITED_PANE)
        _write_hook_state(state_dir, "oc", "PreToolUse", tool_name="Bash")
        session = create_mock_session(name="oc", tmux_window="w1")
        status, _, _ = detector.detect_status(session)
        assert status == STATUS_TERMINATED

    def test_no_hook_state_yet_and_shell_prompt_is_terminated(self, tmp_path):
        # The CLI died before its first hook (bad flag, missing binary).
        detector, state_dir = self._detector(tmp_path, self.EXITED_PANE)
        state_dir.mkdir(parents=True)
        session = create_mock_session(name="oc", tmux_window="w1")
        status, _, _ = detector.detect_status(session)
        assert status == STATUS_TERMINATED

    def test_live_opencode_pane_after_stop_is_waiting_user(self, tmp_path):
        detector, state_dir = self._detector(tmp_path, self.LIVE_OPENCODE_PANE)
        _write_hook_state(state_dir, "oc", "Stop")
        session = create_mock_session(name="oc", tmux_window="w1")
        status, _, _ = detector.detect_status(session)
        assert status == STATUS_WAITING_USER

    def test_prompt_shaped_tool_output_in_live_claude_pane_is_not_terminated(self, tmp_path):
        detector, state_dir = self._detector(tmp_path, self.LIVE_CLAUDE_PANE, backend="claude-code")
        _write_hook_state(state_dir, "cc", "PostToolUse", tool_name="Bash")
        session = create_mock_session(name="cc", tmux_window="w1")
        status, _, _ = detector.detect_status(session)
        assert status == STATUS_RUNNING

    def test_real_exited_opencode_capture(self, tmp_path):
        # Verbatim tmux capture 5s after `/exit` on opencode v1.18.29.
        pane = (
            Path(__file__).parent.parent / "fixtures_opencode_panes" / "v1.18.29" / "exited_shell.txt"
        ).read_text(encoding="utf-8")
        detector, state_dir = self._detector(tmp_path, pane)
        _write_hook_state(state_dir, "oc", "Stop")
        session = create_mock_session(name="oc", tmux_window="w1")
        status, _, _ = detector.detect_status(session)
        assert status == STATUS_TERMINATED


# The incident of 2026-10-08: UserPromptSubmit, then no Stop. The pane went
# back to the prompt and the agent showed "Processing prompt" for 34 hours.
_IDLE_PANE = (
    "⏺ Done.\n"
    "──────────────────────────────\n"
    "❯ \n"
    "──────────────────────────────\n"
    "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← 1 agent\n"
)
_BUSY_PANE = (
    "✽ Testing…\n"
    "──────────────────────────────\n"
    "❯ \n"
    "──────────────────────────────\n"
    "  ⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt · ← 1 agent\n"
    "  ⏺ main\n"
    "  ◯ general-purpose  Tracing things     45s · ↓ 69.8k tokens\n"
)


class TestForegroundSleepEnds:
    """A sleep is only "sleeping" while its PreToolUse stands, and not forever."""

    def _status(self, tmp_path, monkeypatch, event, command, age):

        state_dir = tmp_path / "sessions" / "agents"
        state_dir.mkdir(parents=True, exist_ok=True)
        base = 1_800_000_000.0
        (state_dir / "hook_state_test-agent.json").write_text(json.dumps({
            "event": event, "timestamp": base - age, "tool_name": "Bash",
            "tool_input": {"command": command},
        }))
        mock_tmux = create_mock_tmux_with_content("agents", 1, "⏺ Bash(...)")
        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        return detector.detect_status(create_mock_session(tmux_window=1, name="test-agent"), now=base)

    def test_a_finished_sleep_is_not_sleeping(self, tmp_path, monkeypatch):
        status, activity, _ = self._status(tmp_path, monkeypatch, "PostToolUse", "sleep 300", 1)
        assert status == STATUS_RUNNING
        assert "Sleeping" not in activity

    def test_a_sleep_long_past_its_end_is_not_sleeping(self, tmp_path, monkeypatch):
        assert self._status(tmp_path, monkeypatch, "PreToolUse", "sleep 60", 600)[0] == STATUS_RUNNING
        assert self._status(tmp_path, monkeypatch, "PreToolUse", "sleep 60", 30)[0] == STATUS_BUSY_SLEEPING

    def test_a_command_that_mentions_sleep_is_not_sleeping(self, tmp_path, monkeypatch):
        for command in ("grep -n 'sleep 30' x.py", "make test; sleep 5", "python -c 'time.sleep(9)'"):
            assert self._status(tmp_path, monkeypatch, "PreToolUse", command, 1)[0] == STATUS_RUNNING, command
        assert self._status(tmp_path, monkeypatch, "PreToolUse", "cd /tmp && sleep 30", 1)[0] \
            == STATUS_BUSY_SLEEPING

    def test_sleep_units_and_decimals(self):
        """`sleep 1m` was read as 1 s, so a minute's sleep stopped counting
        after ~31 s; `sleep 0.5` wasn't a sleep at all."""
        from overcode.hook_status_detector import _FOREGROUND_SLEEP_RE, _SLEEP_UNIT_SECONDS

        def secs(cmd):
            m = _FOREGROUND_SLEEP_RE.match(cmd)
            return max(1, round(float(m.group(1)) * _SLEEP_UNIT_SECONDS[m.group(2)])) if m else None

        assert secs("sleep 30") == 30
        assert secs("sleep 1m") == 60
        assert secs("sleep 1.5m") == 90
        assert secs("sleep 2h") == 7200
        assert secs("sleep 0.5") == 1
        assert secs("cd /x && sleep 5s") == 5
        assert secs("sleep 5x") is None

    def test_an_esc_during_a_sleep_ends_it(self, tmp_path, monkeypatch):

        state_dir = tmp_path / "sessions" / "agents"
        state_dir.mkdir(parents=True, exist_ok=True)
        base = 1_800_000_000.0
        (state_dir / "hook_state_test-agent.json").write_text(json.dumps({
            "event": "PreToolUse", "timestamp": base - 10, "tool_name": "Bash",
            "tool_input": {"command": "sleep 300"},
        }))
        pane = "⏺ Bash(sleep 300)\n  ⎿  Interrupted · What should Claude do instead?\n❯ \n"
        detector = HookStatusDetector("agents", tmux=create_mock_tmux_with_content("agents", 1, pane),
                                      state_dir=state_dir)
        status, activity, _ = detector.detect_status(create_mock_session(tmux_window=1, name="test-agent"), now=base)
        assert status == STATUS_WAITING_USER
        assert "Sleeping" not in activity


class TestTurnEndedWithoutStop:
    """Hooks say running, but the pane shows the CLI idle: trust the pane after a while."""

    def _detect(self, tmp_path, monkeypatch, pane, event="UserPromptSubmit", hook_age=3600.0,
                ticks=(0.0, 20.0), tool_name=None, parent=None):

        state_dir = tmp_path / "sessions" / "agents"
        base = 1_800_000_000.0
        _write_hook_state(state_dir, "test-agent", event, timestamp=base - hook_age,
                          tool_name=tool_name)
        mock_tmux = create_mock_tmux_with_content("agents", 1, pane)
        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        detector._busy_marker_seen = True  # some agent has shown the marker
        session = create_mock_session(tmux_window=1, name="test-agent")
        session.parent_session_id = parent
        results = []
        for t in ticks:
            status, activity, _ = detector.detect_status(session, now=base + t)
            results.append((status, activity, detector.get_status_detail("test-agent")))
        return results

    def test_an_idle_pane_settles_a_running_hook_after_the_wait(self, tmp_path, monkeypatch):
        first, later = self._detect(tmp_path, monkeypatch, _IDLE_PANE)
        assert first[0] == STATUS_RUNNING  # not on first sight: a redraw is not proof
        assert later[0] == STATUS_WAITING_USER
        assert "without a Stop" in later[1]
        assert later[2].color == STATUS_COLOR_RED

    def test_a_busy_marker_keeps_it_running(self, tmp_path, monkeypatch):
        results = self._detect(tmp_path, monkeypatch, _BUSY_PANE, ticks=(0.0, 20.0, 60.0))
        assert [r[0] for r in results] == [STATUS_RUNNING] * 3

    def test_a_pane_with_no_chrome_proves_nothing(self, tmp_path, monkeypatch):
        """A dialog or menu covering the bar: keep what the hooks say."""
        results = self._detect(tmp_path, monkeypatch, "⏺ Reading file...", ticks=(0.0, 60.0))
        assert [r[0] for r in results] == [STATUS_RUNNING] * 2

    def test_a_hook_after_the_pane_went_idle_restarts_the_wait(self, tmp_path, monkeypatch):

        state_dir = tmp_path / "sessions" / "agents"
        base = 1_800_000_000.0
        _write_hook_state(state_dir, "test-agent", "PreToolUse", timestamp=base - 5, tool_name="Read")
        mock_tmux = create_mock_tmux_with_content("agents", 1, _IDLE_PANE)
        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        detector._busy_marker_seen = True
        session = create_mock_session(tmux_window=1, name="test-agent")
        detector.detect_status(session, now=base)
        # The turn is alive after all: a new hook lands while the pane read idle
        _write_hook_state(state_dir, "test-agent", "PostToolUse", timestamp=base + 10, tool_name="Read")
        assert detector.detect_status(session, now=base + 20)[0] == STATUS_RUNNING
        assert detector.detect_status(session, now=base + 40)[0] == STATUS_WAITING_USER

    def test_a_foreground_sleep_that_was_interrupted_settles_too(self, tmp_path, monkeypatch):

        state_dir = tmp_path / "sessions" / "agents"
        base = 1_800_000_000.0
        state_dir.mkdir(parents=True)
        (state_dir / "hook_state_test-agent.json").write_text(json.dumps({
            "event": "PreToolUse", "timestamp": base - 3600, "tool_name": "Bash",
            "tool_input": {"command": "sleep 300"},
        }))
        mock_tmux = create_mock_tmux_with_content("agents", 1, _IDLE_PANE)
        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        detector._busy_marker_seen = True
        session = create_mock_session(tmux_window=1, name="test-agent")
        for t in (0.0, 20.0):
            status, activity, _ = detector.detect_status(session, now=base + t)
        assert status == STATUS_WAITING_USER
        assert "Sleeping" not in activity

    def test_an_unproven_marker_is_not_trusted(self, tmp_path, monkeypatch):
        """No pane has shown "esc to interrupt" yet (a CLI that reworded it):
        a missing marker proves nothing, so hooks keep the say."""

        state_dir = tmp_path / "sessions" / "agents"
        base = 1_800_000_000.0
        _write_hook_state(state_dir, "test-agent", "UserPromptSubmit", timestamp=base - 3600)
        panes = {"pane": _IDLE_PANE}
        mock_tmux = create_mock_tmux_with_content("agents", 1, _IDLE_PANE)
        mock_tmux.capture_pane = lambda *a, **k: panes["pane"]
        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        for t in (0.0, 60.0):
            assert detector.detect_status(session, now=base + t)[0] == STATUS_RUNNING
        # The marker shows once (this or any agent working): now it is proven
        panes["pane"] = _BUSY_PANE
        detector.detect_status(session, now=base + 60.0)
        panes["pane"] = _IDLE_PANE
        for t in (70.0, 90.0):
            status = detector.detect_status(session, now=base + t)[0]
        assert status == STATUS_WAITING_USER

    def test_a_child_settles_to_waiting_oversight(self, tmp_path, monkeypatch):
        """Like a child's Stop: at rest, never red (review item 25)."""
        _, later = self._detect(tmp_path, monkeypatch, _IDLE_PANE, parent="parent-id")
        assert later[0] == STATUS_WAITING_OVERSIGHT
        assert later[1] == "At rest: no report yet (turn ended without a Stop)"
        assert later[2].color == STATUS_COLOR_REST
        assert later[2].badges == []

    def test_a_stop_is_unaffected(self, tmp_path, monkeypatch):
        _, later = self._detect(tmp_path, monkeypatch, _IDLE_PANE, event="Stop")
        assert later[0] == STATUS_WAITING_USER
        assert "without a Stop" not in later[1]

    def test_backends_without_a_verified_marker_are_left_alone(self, tmp_path, monkeypatch):
        from overcode.status_patterns import StatusPatterns

        state_dir = tmp_path / "sessions" / "agents"
        base = 1_800_000_000.0
        _write_hook_state(state_dir, "test-agent", "UserPromptSubmit", timestamp=base - 3600)
        mock_tmux = create_mock_tmux_with_content("agents", 1, _IDLE_PANE)
        detector = HookStatusDetector("agents", tmux=mock_tmux, state_dir=state_dir,
                                      patterns=StatusPatterns())  # pane_confirms_idle off
        session = create_mock_session(tmux_window=1, name="test-agent")
        for t in (0.0, 60.0):
            status = detector.detect_status(session, now=base + t)[0]
        assert status == STATUS_RUNNING


class TestPaneShowsIdle:
    def test_idle_busy_and_unknown(self):
        from overcode.status_patterns import DEFAULT_PATTERNS, pane_shows_idle

        assert pane_shows_idle(_IDLE_PANE, DEFAULT_PATTERNS) is True
        assert pane_shows_idle(_BUSY_PANE, DEFAULT_PATTERNS) is False
        assert pane_shows_idle("⏺ Reading file...", DEFAULT_PATTERNS) is None
        assert pane_shows_idle("", DEFAULT_PATTERNS) is None

    def test_the_input_hint_counts_as_chrome(self):
        from overcode.status_patterns import DEFAULT_PATTERNS, pane_shows_idle

        assert pane_shows_idle("❯ \n  ? for shortcuts\n", DEFAULT_PATTERNS) is True
        assert pane_shows_idle("✽ Thinking… (esc to interrupt)\n❯ \n  ? for shortcuts\n",
                               DEFAULT_PATTERNS) is False

    def test_only_claude_opts_in(self):
        from overcode.status_patterns import DEFAULT_PATTERNS, get_patterns

        assert DEFAULT_PATTERNS.pane_confirms_idle
        for name in ("codex", "grok", "opencode", "hermes", "shell"):
            assert not get_patterns(name).pane_confirms_idle, name


class TestOverdueWakeup:
    def test_an_overdue_wakeup_alone_turns_red_overdue(self):
        from overcode.hook_status_detector import _WAKEUP_OVERDUE_SECONDS

        state = {"event": "Stop", "timestamp": 900.0,
                 "pending_obligations": [{"kind": "schedule_wakeup", "eta_absolute": 1000.0}]}
        detail = decide(TickContext(state, now=1000.0 + _WAKEUP_OVERDUE_SECONDS + 1))[0].detail()
        assert detail.color == STATUS_COLOR_RED
        assert [b.kind for b in detail.badges] == ["overdue"]
        assert detail.legacy_status == STATUS_WAITING_USER
        # Still within its slack: armed (yellow)
        detail = decide(TickContext(state, now=1000.0))[0].detail()
        assert detail.color == STATUS_COLOR_YELLOW
        assert detail.legacy_status == STATUS_WAITING_USER

    def test_a_wakeup_long_past_its_time_is_not_armed(self):
        from overcode.hook_status_detector import _WAKEUP_OVERDUE_SECONDS, _live_obligations

        obl = {"kind": "schedule_wakeup", "eta_absolute": 1000.0}
        cron = {"kind": "cron"}
        assert _live_obligations([obl, cron], 1000.0 + _WAKEUP_OVERDUE_SECONDS - 1) == [obl, cron]
        assert _live_obligations([obl, cron], 1000.0 + _WAKEUP_OVERDUE_SECONDS + 1) == [cron]


class TestSecondInterrupt:
    def test_a_new_marker_after_a_reprompt_counts_again(self, tmp_path, monkeypatch):

        state_dir = tmp_path / "sessions" / "agents"
        base = 1_800_000_000.0
        d = HookStatusDetector("agents", state_dir=state_dir)
        state_dir.mkdir(parents=True, exist_ok=True)
        log = state_dir / "hook_events_test-agent.jsonl"

        def running_at(t):
            with open(log, "a") as f:
                f.write(json.dumps({"event": "PreToolUse", "timestamp": base + t}) + "\n")

        def check(t, count):
            return d._interrupt_still_current("test-agent", True, count, now=base + t)

        running_at(0)
        assert check(1, 1)          # first Esc
        running_at(5)               # re-prompted: the old marker is history
        assert not check(6, 1)
        assert check(8, 2)          # a second Esc: one more marker in the tail


_OLD_ESC = "⏺ Bash(make test)\n  ⎿  Interrupted · What should Claude do instead?\n\n"
_PROMPT_TAIL = "❯ \n  ? for shortcuts\n"


class TestOldInterruptMarkerAfterReprompt:
    """An Esc marker left in the tail does not turn a later clean Stop red."""

    def _run(self, tmp_path, parent, final_pane):
        state_dir = tmp_path / "sessions" / "agents"
        state_dir.mkdir(parents=True)
        tmux = MockTmux()
        det = HookStatusDetector("agents", tmux=tmux, state_dir=state_dir)
        s = create_mock_session(name="a1", tmux_window="w1")
        s.parent_session_id = parent
        t0 = 1_800_000_000.0
        events = []

        def hook(event, t, **kw):
            events.append({"event": event, "timestamp": t0 + t})
            (state_dir / "hook_state_a1.json").write_text(
                json.dumps({"event": event, "timestamp": t0 + t, **kw}))
            (state_dir / "hook_events_a1.jsonl").write_text(
                "".join(json.dumps(e) + "\n" for e in events))

        hook("PreToolUse", 0, tool_name="Bash")
        tmux.set_pane_content("agents", "w1", _OLD_ESC + _PROMPT_TAIL)
        det.detect_status(s, now=t0 + 5)
        assert det.get_status_detail("a1").color == STATUS_COLOR_RED  # the Esc

        hook("UserPromptSubmit", 20)  # re-prompted: busy under the old marker
        tmux.set_pane_content("agents", "w1", _OLD_ESC + "> go on\n\n"
                              "✻ Thinking… (3s · esc to interrupt)\n\n" + _PROMPT_TAIL)
        det.detect_status(s, now=t0 + 22)

        hook("Stop", 30)
        tmux.set_pane_content("agents", "w1", final_pane)
        det.detect_status(s, now=t0 + 35)
        return det.get_status_detail("a1")

    def test_a_child_clean_stop_rests(self, tmp_path):
        pane = _OLD_ESC + "> go on\n\n⏺ Done.\n\n" + _PROMPT_TAIL
        assert self._run(tmp_path, "p1", pane).color == STATUS_COLOR_REST

    def test_a_new_esc_after_the_turn_still_counts(self, tmp_path):
        pane = (_OLD_ESC + "> go on\n\n⏺ Bash(ls)\n  ⎿  Interrupted · What should Claude "
                "do instead?\n\n" + _PROMPT_TAIL)
        assert self._run(tmp_path, "p1", pane).color == STATUS_COLOR_RED
