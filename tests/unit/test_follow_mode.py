"""
Unit tests for follow mode (#244).

Tests the incremental pane capture, deduplication, Stop detection,
and terminated detection.
"""

import json
import pytest
from collections import deque
from pathlib import Path
from unittest.mock import patch, MagicMock

from overcode.follow_mode import (
    _capture_pane,
    _check_hook_stop,
    _check_report,
    _check_session_terminated,
    _emit_new_lines,
    _poll_for_report,
    follow_agent,
)
from overcode.session_manager import SessionManager


class TestCheckHookStop:
    """Test Stop detection via hook state file."""

    def test_returns_true_on_stop_event(self, tmp_path):
        """Should detect Stop event in hook state file."""
        # Create a mock session dir and hook state file
        hook_state = tmp_path / "hook_state_my-agent.json"
        hook_state.write_text(json.dumps({"event": "Stop"}))

        with patch("overcode.follow_mode.get_session_dir", return_value=tmp_path):
            result = _check_hook_stop("agents", "my-agent")

        assert result is True

    def test_returns_false_on_other_event(self, tmp_path):
        """Should return False for non-Stop events."""
        hook_state = tmp_path / "hook_state_my-agent.json"
        hook_state.write_text(json.dumps({"event": "UserPromptSubmit"}))

        with patch("overcode.follow_mode.get_session_dir", return_value=tmp_path):
            result = _check_hook_stop("agents", "my-agent")

        assert result is False

    def test_returns_false_when_file_missing(self, tmp_path):
        """Should return False when hook state file doesn't exist."""
        with patch("overcode.follow_mode.get_session_dir", return_value=tmp_path):
            result = _check_hook_stop("agents", "my-agent")

        assert result is False

    def test_returns_false_on_corrupt_json(self, tmp_path):
        """Should return False on corrupt JSON."""
        hook_state = tmp_path / "hook_state_my-agent.json"
        hook_state.write_text("not valid json")

        with patch("overcode.follow_mode.get_session_dir", return_value=tmp_path):
            result = _check_hook_stop("agents", "my-agent")

        assert result is False


class TestCheckSessionTerminated:
    """Test session terminated detection."""

    def test_returns_true_when_session_not_found(self, tmp_path):
        """Should return True when session doesn't exist."""
        manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        result = _check_session_terminated(manager, "nonexistent")

        assert result is True

    def test_returns_true_when_terminated(self, tmp_path):
        """Should return True when session status is 'terminated'."""
        manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        session = manager.create_session(
            name="test-agent", tmux_session="agents", tmux_window=1, command=["claude"]
        )
        manager.update_session_status(session.id, "terminated")

        result = _check_session_terminated(manager, "test-agent")

        assert result is True

    def test_returns_false_when_running(self, tmp_path):
        """Should return False when session is running."""
        manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        manager.create_session(
            name="test-agent", tmux_session="agents", tmux_window=1, command=["claude"]
        )

        result = _check_session_terminated(manager, "test-agent")

        assert result is False


class TestCapturePane:
    """Test _capture_pane subprocess wrapper."""

    def test_returns_stdout_on_success(self):
        result = MagicMock()
        result.returncode = 0
        result.stdout = "line 1\nline 2\n"

        with patch("overcode.follow_mode.subprocess.run", return_value=result):
            output = _capture_pane("agents", 1, lines=50)

        assert output == "line 1\nline 2\n"

    def test_returns_none_on_nonzero_exit(self):
        result = MagicMock()
        result.returncode = 1
        result.stdout = ""

        with patch("overcode.follow_mode.subprocess.run", return_value=result):
            output = _capture_pane("agents", 1)

        assert output is None

    def test_returns_none_on_subprocess_error(self):
        import subprocess
        with patch("overcode.follow_mode.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="tmux", timeout=5)):
            output = _capture_pane("agents", 1)

        assert output is None


class TestCheckReport:
    """Test report file checking."""

    def test_returns_report_on_valid_file(self, tmp_path):
        report = {"status": "success", "reason": "", "timestamp": "2024-01-01T12:00:00"}
        report_file = tmp_path / "report_my-agent.json"
        report_file.write_text(json.dumps(report))

        with patch("overcode.follow_mode.get_session_dir", return_value=tmp_path):
            result = _check_report("agents", "my-agent")

        assert result is not None
        assert result["status"] == "success"

    def test_returns_none_when_file_missing(self, tmp_path):
        with patch("overcode.follow_mode.get_session_dir", return_value=tmp_path):
            result = _check_report("agents", "my-agent")

        assert result is None

    def test_returns_none_on_corrupt_json(self, tmp_path):
        report_file = tmp_path / "report_my-agent.json"
        report_file.write_text("not json")

        with patch("overcode.follow_mode.get_session_dir", return_value=tmp_path):
            result = _check_report("agents", "my-agent")

        assert result is None

    def test_returns_none_when_no_status_field(self, tmp_path):
        report_file = tmp_path / "report_my-agent.json"
        report_file.write_text(json.dumps({"reason": "done"}))

        with patch("overcode.follow_mode.get_session_dir", return_value=tmp_path):
            result = _check_report("agents", "my-agent")

        assert result is None

    def test_returns_failure_report(self, tmp_path):
        report = {"status": "failure", "reason": "tests failed"}
        report_file = tmp_path / "report_my-agent.json"
        report_file.write_text(json.dumps(report))

        with patch("overcode.follow_mode.get_session_dir", return_value=tmp_path):
            result = _check_report("agents", "my-agent")

        assert result["status"] == "failure"
        assert result["reason"] == "tests failed"


class TestEmitNewLines:
    """Test pane output deduplication and emission."""

    def test_emits_all_lines_on_empty_history(self, capsys):
        recent = deque(maxlen=50)
        _emit_new_lines("line 1\nline 2\nline 3", recent)

        output = capsys.readouterr().out
        assert "line 1" in output
        assert "line 2" in output
        assert "line 3" in output

    def test_deduplicates_against_recent_lines(self, capsys):
        recent = deque(["line 1", "line 2"], maxlen=50)
        _emit_new_lines("line 1\nline 2\nline 3", recent)

        output = capsys.readouterr().out
        assert "line 1" not in output
        assert "line 2" not in output
        assert "line 3" in output

    def test_updates_recent_lines(self):
        recent = deque(maxlen=50)
        _emit_new_lines("line 1\nline 2", recent)

        assert "line 1" in recent
        assert "line 2" in recent

    def test_skips_empty_lines_in_output(self, capsys):
        recent = deque(maxlen=50)
        _emit_new_lines("line 1\n\nline 3", recent)

        output = capsys.readouterr().out
        lines = [l for l in output.strip().split('\n') if l]
        assert len(lines) == 2

    def test_strips_ansi_codes(self, capsys):
        recent = deque(maxlen=50)
        _emit_new_lines("\033[32mcolored text\033[0m", recent)

        output = capsys.readouterr().out
        assert "colored text" in output
        assert "\033[" not in output


class TestFollowAgent:
    """Test the main follow_agent function."""

    def test_returns_1_when_agent_not_found(self, tmp_path):
        with patch("overcode.follow_mode.SessionManager") as mock_sm:
            mock_instance = MagicMock()
            mock_instance.get_session_by_name.return_value = None
            mock_sm.return_value = mock_instance

            result = follow_agent("nonexistent", "agents")

        assert result == 1

    def test_returns_1_on_immediate_termination(self, tmp_path):
        mock_session = MagicMock()
        mock_session.tmux_window = 1
        mock_session.oversight_policy = "wait"
        mock_session.oversight_timeout_seconds = 0.0

        with patch("overcode.follow_mode.SessionManager") as mock_sm:
            mock_instance = MagicMock()
            mock_instance.get_session_by_name.side_effect = [mock_session, None]
            mock_sm.return_value = mock_instance

            with patch("overcode.follow_mode._capture_pane", return_value=None):
                with patch("overcode.follow_mode._check_session_terminated", return_value=True):
                    result = follow_agent("test-agent", "agents")

        assert result == 1

    def test_returns_0_on_success_report(self, tmp_path):
        mock_session = MagicMock()
        mock_session.tmux_window = 1
        mock_session.oversight_policy = "wait"
        mock_session.oversight_timeout_seconds = 0.0
        mock_session.status = "running"

        report = {"status": "success", "reason": "", "timestamp": "2024-01-01T12:00:00"}

        call_count = [0]

        def mock_capture(*args, **kwargs):
            return "some output"

        def mock_hook_stop(*args):
            nonlocal call_count
            call_count[0] += 1
            return call_count[0] >= 1  # Stop on first check

        with patch("overcode.follow_mode.SessionManager") as mock_sm:
            mock_instance = MagicMock()
            mock_instance.get_session_by_name.return_value = mock_session
            mock_sm.return_value = mock_instance

            with patch("overcode.follow_mode._capture_pane", side_effect=mock_capture):
                with patch("overcode.follow_mode._check_hook_stop", side_effect=mock_hook_stop):
                    with patch("overcode.follow_mode._check_report", return_value=report):
                        with patch("overcode.follow_mode.time.sleep"):
                            result = follow_agent("test-agent", "agents")

        assert result == 0


class TestPollForReport:
    """Test the report polling sub-loop."""

    def test_returns_0_on_success_report(self):
        mock_sessions = MagicMock()
        mock_session = MagicMock()
        mock_sessions.get_session_by_name.return_value = mock_session

        report = {"status": "success"}

        with patch("overcode.follow_mode._check_report", return_value=report):
            with patch("overcode.follow_mode.time.sleep"):
                result = _poll_for_report(
                    "test", "agents", mock_sessions, 1,
                    "wait", 0.0, 0.1, deque(),
                )

        assert result == 0

    def test_returns_1_on_failure_report(self):
        mock_sessions = MagicMock()
        mock_sessions.get_session_by_name.return_value = MagicMock()

        report = {"status": "failure", "reason": "tests failed"}

        with patch("overcode.follow_mode._check_report", return_value=report):
            with patch("overcode.follow_mode.time.sleep"):
                result = _poll_for_report(
                    "test", "agents", mock_sessions, 1,
                    "wait", 0.0, 0.1, deque(),
                )

        assert result == 1

    def test_returns_2_on_timeout(self):
        mock_sessions = MagicMock()
        mock_sessions.get_session_by_name.return_value = MagicMock()

        from datetime import datetime, timedelta

        # First now() sets deadline, second now() checks against it
        times = iter([
            datetime(2024, 1, 1, 12, 0, 0),   # deadline = this + 1s
            datetime(2024, 1, 1, 12, 0, 10),   # past deadline -> timeout
        ])

        with patch("overcode.follow_mode._check_report", return_value=None):
            with patch("overcode.follow_mode._check_session_terminated", return_value=False):
                with patch("overcode.follow_mode._capture_pane", return_value=None):
                    with patch("overcode.follow_mode.time.sleep"):
                        with patch("overcode.follow_mode.datetime") as mock_dt:
                            mock_dt.now.side_effect = lambda: next(times)
                            result = _poll_for_report(
                                "test", "agents", mock_sessions, 1,
                                "timeout", 1.0, 0.1, deque(),
                            )

        assert result == 2

    def test_returns_1_on_terminated(self):
        mock_sessions = MagicMock()
        mock_sessions.get_session_by_name.return_value = MagicMock()

        with patch("overcode.follow_mode._check_report", return_value=None):
            with patch("overcode.follow_mode._check_session_terminated", return_value=True):
                with patch("overcode.follow_mode._capture_pane", return_value=None):
                    with patch("overcode.follow_mode.time.sleep"):
                        result = _poll_for_report(
                            "test", "agents", mock_sessions, 1,
                            "wait", 0.0, 0.1, deque(),
                        )

        assert result == 1

    def test_streams_output_while_waiting(self, capsys):
        mock_sessions = MagicMock()
        mock_sessions.get_session_by_name.return_value = MagicMock()

        call_count = [0]

        def check_report_side_effect(*args):
            nonlocal call_count
            call_count[0] += 1
            if call_count[0] >= 2:
                return {"status": "success"}
            return None

        with patch("overcode.follow_mode._check_report", side_effect=check_report_side_effect):
            with patch("overcode.follow_mode._check_session_terminated", return_value=False):
                with patch("overcode.follow_mode._capture_pane", return_value="streaming output"):
                    with patch("overcode.follow_mode.time.sleep"):
                        result = _poll_for_report(
                            "test", "agents", mock_sessions, 1,
                            "wait", 0.0, 0.1, deque(),
                        )

        assert result == 0
        output = capsys.readouterr().out
        assert "streaming output" in output


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
