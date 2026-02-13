"""Tests for the unified hook handler."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from overcode.hook_handler import (
    OVERCODE_HOOKS,
    _get_hook_state_path,
    write_hook_state,
    handle_hook_event,
)


class TestConstants:

    def test_overcode_hooks_has_all_events(self):
        events = [e for e, _ in OVERCODE_HOOKS]
        assert "UserPromptSubmit" in events
        assert "PostToolUse" in events
        assert "Stop" in events
        assert "PermissionRequest" in events
        assert "SessionEnd" in events

    def test_all_hooks_use_same_command(self):
        commands = set(cmd for _, cmd in OVERCODE_HOOKS)
        assert commands == {"overcode hook-handler"}



class TestGetHookStatePath:

    def test_default_path(self, monkeypatch, tmp_path):
        monkeypatch.delenv("OVERCODE_STATE_DIR", raising=False)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        path = _get_hook_state_path("agents", "my-agent")
        assert path == tmp_path / ".overcode" / "sessions" / "agents" / "hook_state_my-agent.json"

    def test_respects_state_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "custom"))
        path = _get_hook_state_path("agents", "my-agent")
        assert path == tmp_path / "custom" / "agents" / "hook_state_my-agent.json"


class TestWriteHookState:

    def test_writes_state_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        write_hook_state("Stop", "agents", "my-agent")
        path = tmp_path / "agents" / "hook_state_my-agent.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["event"] == "Stop"
        assert "timestamp" in data
        assert "tool_name" not in data

    def test_writes_tool_name(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        write_hook_state("PostToolUse", "agents", "my-agent", tool_name="Bash")
        path = tmp_path / "agents" / "hook_state_my-agent.json"
        data = json.loads(path.read_text())
        assert data["event"] == "PostToolUse"
        assert data["tool_name"] == "Bash"

    def test_creates_directory(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "deep" / "nested"))
        write_hook_state("Stop", "agents", "my-agent")
        path = tmp_path / "deep" / "nested" / "agents" / "hook_state_my-agent.json"
        assert path.exists()


class TestHandleHookEvent:

    def test_missing_env_vars_silent_exit(self, monkeypatch):
        monkeypatch.delenv("OVERCODE_SESSION_NAME", raising=False)
        monkeypatch.delenv("OVERCODE_TMUX_SESSION", raising=False)
        # Should not raise
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = '{"hook_event_name": "Stop"}'
            handle_hook_event()

    def test_empty_stdin_silent_exit(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "test-agent")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = ""
            handle_hook_event()
        # No state file written
        assert not list(tmp_path.rglob("hook_state_*.json"))

    def test_invalid_stdin_silent_exit(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "test-agent")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = "not json{{{}"
            handle_hook_event()
        assert not list(tmp_path.rglob("hook_state_*.json"))

    def test_stop_event_writes_state(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "test-agent")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = json.dumps({
                "hook_event_name": "Stop",
                "session_id": "abc123",
            })
            handle_hook_event()
        state_path = tmp_path / "agents" / "hook_state_test-agent.json"
        assert state_path.exists()
        data = json.loads(state_path.read_text())
        assert data["event"] == "Stop"

    def test_post_tool_use_extracts_tool_name(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "test-agent")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = json.dumps({
                "hook_event_name": "PostToolUse",
                "tool_name": "Read",
                "session_id": "abc123",
            })
            handle_hook_event()
        state_path = tmp_path / "agents" / "hook_state_test-agent.json"
        data = json.loads(state_path.read_text())
        assert data["event"] == "PostToolUse"
        assert data["tool_name"] == "Read"

    def test_user_prompt_submit_outputs_time_context(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "test-agent")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))

        with patch("sys.stdin") as mock_stdin, \
             patch("overcode.time_context.generate_time_context", return_value="Clock: 14:00 PST | User: active | Office: yes"):
            mock_stdin.read.return_value = json.dumps({
                "hook_event_name": "UserPromptSubmit",
                "session_id": "abc123",
            })
            handle_hook_event()

        captured = capsys.readouterr()
        assert "Clock: 14:00 PST" in captured.out

        # Also check state file was written
        state_path = tmp_path / "agents" / "hook_state_test-agent.json"
        assert state_path.exists()

    def test_user_prompt_submit_empty_time_context(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "test-agent")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))

        with patch("sys.stdin") as mock_stdin, \
             patch("overcode.time_context.generate_time_context", return_value=""):
            mock_stdin.read.return_value = json.dumps({
                "hook_event_name": "UserPromptSubmit",
                "session_id": "abc123",
            })
            handle_hook_event()

        captured = capsys.readouterr()
        assert captured.out == ""

    def test_permission_request_writes_state(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "test-agent")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = json.dumps({
                "hook_event_name": "PermissionRequest",
                "session_id": "abc123",
            })
            handle_hook_event()
        state_path = tmp_path / "agents" / "hook_state_test-agent.json"
        data = json.loads(state_path.read_text())
        assert data["event"] == "PermissionRequest"

    def test_session_end_writes_state(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "test-agent")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = json.dumps({
                "hook_event_name": "SessionEnd",
                "session_id": "abc123",
            })
            handle_hook_event()
        state_path = tmp_path / "agents" / "hook_state_test-agent.json"
        data = json.loads(state_path.read_text())
        assert data["event"] == "SessionEnd"

    def test_budget_exceeded_blocks_prompt(self, monkeypatch, tmp_path, capsys):
        """Exit code 2 when budget exceeded blocks prompt in Claude Code (#246)."""
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "test-agent")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))

        # Write daemon state with budget_exceeded=True
        state_dir = tmp_path / "agents"
        state_dir.mkdir(parents=True)
        state_path = state_dir / "monitor_daemon_state.json"
        state_path.write_text(json.dumps({
            "sessions": [{
                "name": "test-agent",
                "budget_exceeded": True,
                "cost_budget_usd": 5.0,
                "estimated_cost_usd": 5.42,
            }]
        }))

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = json.dumps({
                "hook_event_name": "UserPromptSubmit",
                "session_id": "abc123",
            })
            with pytest.raises(SystemExit) as exc_info:
                handle_hook_event()
            assert exc_info.value.code == 2

        captured = capsys.readouterr()
        assert "Budget exceeded" in captured.err
        assert "$5.42" in captured.err
        assert "$5.00" in captured.err

    def test_budget_not_exceeded_allows_prompt(self, monkeypatch, tmp_path, capsys):
        """Normal flow when budget is not exceeded (#246)."""
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "test-agent")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))

        # Write daemon state with budget_exceeded=False
        state_dir = tmp_path / "agents"
        state_dir.mkdir(parents=True)
        state_path = state_dir / "monitor_daemon_state.json"
        state_path.write_text(json.dumps({
            "sessions": [{
                "name": "test-agent",
                "budget_exceeded": False,
                "cost_budget_usd": 10.0,
                "estimated_cost_usd": 3.50,
            }]
        }))

        with patch("sys.stdin") as mock_stdin, \
             patch("overcode.time_context.generate_time_context", return_value="Clock: 14:00 PST"):
            mock_stdin.read.return_value = json.dumps({
                "hook_event_name": "UserPromptSubmit",
                "session_id": "abc123",
            })
            handle_hook_event()  # Should not raise

        captured = capsys.readouterr()
        assert "Clock: 14:00 PST" in captured.out

    def test_budget_check_skipped_when_no_state(self, monkeypatch, tmp_path, capsys):
        """Normal flow when no daemon state file exists (#246)."""
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "test-agent")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))

        # No daemon state file — budget check should be skipped
        with patch("sys.stdin") as mock_stdin, \
             patch("overcode.time_context.generate_time_context", return_value="Clock: 14:00 PST"):
            mock_stdin.read.return_value = json.dumps({
                "hook_event_name": "UserPromptSubmit",
                "session_id": "abc123",
            })
            handle_hook_event()  # Should not raise

        captured = capsys.readouterr()
        assert "Clock: 14:00 PST" in captured.out

    def test_budget_check_only_on_user_prompt_submit(self, monkeypatch, tmp_path):
        """Budget check should not fire for non-UserPromptSubmit events (#246)."""
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "test-agent")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))

        # Write daemon state with budget_exceeded=True
        state_dir = tmp_path / "agents"
        state_dir.mkdir(parents=True)
        state_path = state_dir / "monitor_daemon_state.json"
        state_path.write_text(json.dumps({
            "sessions": [{
                "name": "test-agent",
                "budget_exceeded": True,
                "cost_budget_usd": 5.0,
                "estimated_cost_usd": 5.42,
            }]
        }))

        # PostToolUse should not trigger budget check
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = json.dumps({
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "session_id": "abc123",
            })
            handle_hook_event()  # Should not raise despite budget exceeded

    def test_missing_event_name_silent_exit(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "test-agent")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = json.dumps({"session_id": "abc123"})
            handle_hook_event()
        assert not list(tmp_path.rglob("hook_state_*.json"))
