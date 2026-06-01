"""Tests for the unified hook handler."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from overcode.hook_handler import (
    OVERCODE_HOOKS,
    _get_hook_state_path,
    _get_hook_event_log_path,
    append_hook_event,
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

    def test_writes_tool_input(self, monkeypatch, tmp_path):
        """tool_input dict should be persisted in hook state (#289)."""
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        write_hook_state("PostToolUse", "agents", "my-agent",
                         tool_name="Bash", tool_input={"command": "sleep 60"})
        path = tmp_path / "agents" / "hook_state_my-agent.json"
        data = json.loads(path.read_text())
        assert data["tool_input"] == {"command": "sleep 60"}

    def test_omits_tool_input_when_none(self, monkeypatch, tmp_path):
        """tool_input should not appear in state when not provided."""
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        write_hook_state("PostToolUse", "agents", "my-agent", tool_name="Bash")
        path = tmp_path / "agents" / "hook_state_my-agent.json"
        data = json.loads(path.read_text())
        assert "tool_input" not in data

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

    def test_user_prompt_submit_outputs_enhanced_context(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "test-agent")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))

        with patch("sys.stdin") as mock_stdin, \
             patch("overcode.time_context.generate_enhanced_context", return_value="Clock: 14:00 PST | User: active | Office: yes"):
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

    def test_user_prompt_submit_empty_enhanced_context(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "test-agent")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))

        with patch("sys.stdin") as mock_stdin, \
             patch("overcode.time_context.generate_enhanced_context", return_value=""):
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

        # Hook state must show rejection, not stuck as UserPromptSubmit (#428)
        hook_state_path = state_dir / "hook_state_test-agent.json"
        hook_state = json.loads(hook_state_path.read_text())
        assert hook_state["event"] == "UserPromptSubmitRejected"

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
             patch("overcode.time_context.generate_enhanced_context", return_value="Clock: 14:00 PST"):
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
             patch("overcode.time_context.generate_enhanced_context", return_value="Clock: 14:00 PST"):
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


class TestAppendHookEvent:
    """Event log is append-only and preserves event bursts (#448)."""

    def test_appends_jsonl_line(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        append_hook_event("PreToolUse", "agents", "a1", tool_name="Read")
        path = tmp_path / "agents" / "hook_events_a1.jsonl"
        assert path.exists()
        lines = path.read_text().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event"] == "PreToolUse"
        assert entry["tool_name"] == "Read"
        assert "timestamp" in entry

    def test_appends_preserve_prior_events(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        append_hook_event("UserPromptSubmit", "agents", "a1")
        append_hook_event("PreToolUse", "agents", "a1", tool_name="Read")
        append_hook_event("PostToolUse", "agents", "a1", tool_name="Read")
        append_hook_event("Stop", "agents", "a1")
        path = tmp_path / "agents" / "hook_events_a1.jsonl"
        events = [json.loads(l) for l in path.read_text().splitlines()]
        assert [e["event"] for e in events] == [
            "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop",
        ]

    def test_handle_hook_event_writes_both_snapshot_and_log(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "a1")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = json.dumps({
                "hook_event_name": "PostToolUse",
                "tool_name": "Read",
                "session_id": "abc123",
            })
            handle_hook_event()
        snap = tmp_path / "agents" / "hook_state_a1.json"
        log = tmp_path / "agents" / "hook_events_a1.jsonl"
        assert snap.exists() and log.exists()
        assert json.loads(snap.read_text())["event"] == "PostToolUse"
        assert json.loads(log.read_text().splitlines()[-1])["event"] == "PostToolUse"

    def test_rotation_truncates_large_log(self, monkeypatch, tmp_path):
        """Log rotation keeps tail when file grows past the threshold (#448)."""
        import overcode.hook_handler as hh
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        # Shrink thresholds so the test is fast.
        monkeypatch.setattr(hh, "_EVENT_LOG_ROTATE_BYTES", 2048)
        monkeypatch.setattr(hh, "_EVENT_LOG_KEEP_LINES", 10)
        for _ in range(100):
            append_hook_event("PreToolUse", "agents", "a1", tool_name="Read")
        path = tmp_path / "agents" / "hook_events_a1.jsonl"
        lines = path.read_text().splitlines()
        # After rotation, only the trailing _EVENT_LOG_KEEP_LINES plus a few
        # post-rotation appends should remain — never the full 100.
        assert len(lines) < 100
        assert len(lines) >= 10

    def test_event_log_path_respects_state_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "custom"))
        path = _get_hook_event_log_path("agents", "a1")
        assert path == tmp_path / "custom" / "agents" / "hook_events_a1.jsonl"


# =============================================================================
# Phase 1: pending_obligations tracking (#TBD — two-column status model)
# =============================================================================

class TestPendingObligations:
    """write_hook_state should maintain a `pending_obligations` list across
    events so the detector can compute the YELLOW armed bucket.
    """

    def _state(self, tmp_path):
        return json.loads((tmp_path / "agents" / "hook_state_a1.json").read_text())

    def _write(self, tmp_path, monkeypatch, event, **kw):
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        write_hook_state(event, "agents", "a1", **kw)

    def test_schedule_wakeup_arms_obligation(self, monkeypatch, tmp_path):
        self._write(tmp_path, monkeypatch, "PreToolUse",
                    tool_name="ScheduleWakeup", tool_use_id="t1",
                    tool_input={"delaySeconds": 240, "reason": "watch ci"})
        obls = self._state(tmp_path)["pending_obligations"]
        assert len(obls) == 1
        assert obls[0]["kind"] == "schedule_wakeup"
        assert obls[0]["tool_use_id"] == "t1"
        assert obls[0]["eta_seconds"] == 240.0
        assert obls[0]["label"] == "in 240s"

    def test_cron_create_arms_and_carries_id(self, monkeypatch, tmp_path):
        self._write(tmp_path, monkeypatch, "PreToolUse",
                    tool_name="CronCreate", tool_use_id="t2",
                    tool_input={"id": "daily-9am", "schedule": "0 9 * * *"})
        obls = self._state(tmp_path)["pending_obligations"]
        assert obls[0]["kind"] == "cron"
        assert obls[0]["cron_id"] == "daily-9am"
        assert obls[0]["label"] == "0 9 * * *"

    def test_cron_delete_disarms_matching_id(self, monkeypatch, tmp_path):
        self._write(tmp_path, monkeypatch, "PreToolUse",
                    tool_name="CronCreate", tool_use_id="t2",
                    tool_input={"id": "daily-9am", "schedule": "0 9 * * *"})
        self._write(tmp_path, monkeypatch, "PreToolUse",
                    tool_name="CronDelete", tool_use_id="t3",
                    tool_input={"cron_id": "daily-9am"})
        assert "pending_obligations" not in self._state(tmp_path)

    def test_monitor_arms_unconditionally(self, monkeypatch, tmp_path):
        self._write(tmp_path, monkeypatch, "PreToolUse",
                    tool_name="Monitor", tool_use_id="t4",
                    tool_input={"command": "tail -f log"})
        obls = self._state(tmp_path)["pending_obligations"]
        assert obls[0]["kind"] == "monitor"

    def test_bash_arms_only_when_run_in_background(self, monkeypatch, tmp_path):
        # Foreground Bash — no obligation
        self._write(tmp_path, monkeypatch, "PreToolUse",
                    tool_name="Bash", tool_use_id="t5",
                    tool_input={"command": "ls"})
        assert "pending_obligations" not in self._state(tmp_path)
        # Background Bash — obligation
        self._write(tmp_path, monkeypatch, "PreToolUse",
                    tool_name="Bash", tool_use_id="t6",
                    tool_input={"command": "long-job", "run_in_background": True})
        obls = self._state(tmp_path)["pending_obligations"]
        assert obls[0]["kind"] == "bg_task"
        assert obls[0]["tool_use_id"] == "t6"

    def test_post_tool_use_disarms_by_tool_use_id(self, monkeypatch, tmp_path):
        self._write(tmp_path, monkeypatch, "PreToolUse",
                    tool_name="Monitor", tool_use_id="t4")
        self._write(tmp_path, monkeypatch, "PreToolUse",
                    tool_name="Monitor", tool_use_id="t5")
        assert len(self._state(tmp_path)["pending_obligations"]) == 2
        self._write(tmp_path, monkeypatch, "PostToolUse",
                    tool_name="Monitor", tool_use_id="t4")
        remaining = self._state(tmp_path)["pending_obligations"]
        assert len(remaining) == 1
        assert remaining[0]["tool_use_id"] == "t5"

    def test_user_prompt_submit_clears_schedule_wakeup(self, monkeypatch, tmp_path):
        """ScheduleWakeup fires as a synthetic prompt — disarm on next UserPromptSubmit."""
        self._write(tmp_path, monkeypatch, "PreToolUse",
                    tool_name="ScheduleWakeup", tool_use_id="t1",
                    tool_input={"delaySeconds": 60})
        self._write(tmp_path, monkeypatch, "PreToolUse",
                    tool_name="CronCreate", tool_use_id="t2",
                    tool_input={"id": "c1", "schedule": "* * * * *"})
        self._write(tmp_path, monkeypatch, "Stop")
        # Both obligations survive Stop
        assert len(self._state(tmp_path)["pending_obligations"]) == 2
        # UserPromptSubmit drops the wakeup but keeps the cron
        self._write(tmp_path, monkeypatch, "UserPromptSubmit")
        remaining = self._state(tmp_path)["pending_obligations"]
        assert len(remaining) == 1
        assert remaining[0]["kind"] == "cron"

    def test_session_end_clears_all(self, monkeypatch, tmp_path):
        self._write(tmp_path, monkeypatch, "PreToolUse",
                    tool_name="Monitor", tool_use_id="t1")
        self._write(tmp_path, monkeypatch, "SessionEnd")
        assert "pending_obligations" not in self._state(tmp_path)

    def test_cron_obligation_survives_post_tool_use(self, monkeypatch, tmp_path):
        """Persistent obligations (cron, schedule_wakeup) outlive PostToolUse.

        PostToolUse for CronCreate means "the registration completed", not
        "the cron is done firing". The obligation should remain until
        CronDelete or SessionEnd.
        """
        self._write(tmp_path, monkeypatch, "PreToolUse",
                    tool_name="CronCreate", tool_use_id="t1",
                    tool_input={"id": "c1", "schedule": "@hourly"})
        self._write(tmp_path, monkeypatch, "PostToolUse",
                    tool_name="CronCreate", tool_use_id="t1")
        obls = self._state(tmp_path)["pending_obligations"]
        assert len(obls) == 1
        assert obls[0]["kind"] == "cron"
        # And Stop also doesn't disarm it
        self._write(tmp_path, monkeypatch, "Stop")
        assert len(self._state(tmp_path)["pending_obligations"]) == 1

    def test_schedule_wakeup_survives_post_tool_use(self, monkeypatch, tmp_path):
        """ScheduleWakeup PostToolUse means scheduled, not fired."""
        self._write(tmp_path, monkeypatch, "PreToolUse",
                    tool_name="ScheduleWakeup", tool_use_id="t1",
                    tool_input={"delaySeconds": 300})
        self._write(tmp_path, monkeypatch, "PostToolUse",
                    tool_name="ScheduleWakeup", tool_use_id="t1")
        obls = self._state(tmp_path)["pending_obligations"]
        assert len(obls) == 1
        assert obls[0]["kind"] == "schedule_wakeup"


class TestForegroundClassification:
    """write_hook_state should classify foreground Bash commands so the
    GREEN bucket can show *why* the agent looks blocked (CI watch, sleep…).
    """

    def _state(self, tmp_path):
        return json.loads((tmp_path / "agents" / "hook_state_a1.json").read_text())

    def _write(self, tmp_path, monkeypatch, event, **kw):
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        write_hook_state(event, "agents", "a1", **kw)

    def test_gh_run_watch_classified_as_ci(self, monkeypatch, tmp_path):
        self._write(tmp_path, monkeypatch, "PreToolUse",
                    tool_name="Bash", tool_use_id="t1",
                    tool_input={"command": "gh run watch 12345"})
        fg = self._state(tmp_path)["foreground"]
        assert fg["kind"] == "tool"
        assert fg["tool"] == "Bash"
        assert fg["blocked_on"] == "ci"

    def test_gh_pr_checks_watch_classified_as_ci(self, monkeypatch, tmp_path):
        self._write(tmp_path, monkeypatch, "PreToolUse",
                    tool_name="Bash", tool_use_id="t1",
                    tool_input={"command": "gh pr checks --watch"})
        assert self._state(tmp_path)["foreground"]["blocked_on"] == "ci"

    def test_tail_f_classified_as_process(self, monkeypatch, tmp_path):
        self._write(tmp_path, monkeypatch, "PreToolUse",
                    tool_name="Bash", tool_use_id="t1",
                    tool_input={"command": "tail -f /var/log/system.log"})
        assert self._state(tmp_path)["foreground"]["blocked_on"] == "process"

    def test_sleep_classified_as_sleep(self, monkeypatch, tmp_path):
        self._write(tmp_path, monkeypatch, "PreToolUse",
                    tool_name="Bash", tool_use_id="t1",
                    tool_input={"command": "sleep 60"})
        assert self._state(tmp_path)["foreground"]["blocked_on"] == "sleep"

    def test_plain_bash_has_no_blocked_on(self, monkeypatch, tmp_path):
        self._write(tmp_path, monkeypatch, "PreToolUse",
                    tool_name="Bash", tool_use_id="t1",
                    tool_input={"command": "git status"})
        fg = self._state(tmp_path)["foreground"]
        assert fg["tool"] == "Bash"
        assert "blocked_on" not in fg

    def test_foreground_only_set_on_pre_tool_use(self, monkeypatch, tmp_path):
        self._write(tmp_path, monkeypatch, "Stop")
        assert "foreground" not in self._state(tmp_path)
        self._write(tmp_path, monkeypatch, "PostToolUse", tool_name="Bash")
        assert "foreground" not in self._state(tmp_path)
