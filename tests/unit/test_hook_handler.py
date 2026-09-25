"""Tests for the unified hook handler."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from overcode.hook_handler import (
    CODEX_HOOK_EVENTS,
    OVERCODE_HOOKS,
    _get_hook_state_path,
    _get_hook_event_log_path,
    _normalize_hook_payload,
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
        """Log rotation keeps the tail when the file grows past the threshold (#448)."""
        import overcode.hook_handler as hh
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        # Shrink thresholds so the test is fast.
        monkeypatch.setattr(hh, "_EVENT_LOG_ROTATE_BYTES", 2048)
        monkeypatch.setattr(hh, "_EVENT_LOG_KEEP_BYTES", 1024)
        for _ in range(100):
            append_hook_event("PreToolUse", "agents", "a1", tool_name="Read")
        path = tmp_path / "agents" / "hook_events_a1.jsonl"
        lines = path.read_text().splitlines()
        # After rotation, only the trailing _EVENT_LOG_KEEP_BYTES of whole
        # lines plus the post-rotation appends remain — never the full 100.
        assert len(lines) < 100
        assert len(lines) >= 10
        assert path.stat().st_size <= 2048 + 200
        for line in lines:
            json.loads(line)  # every kept line is whole

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


class TestCodexHookEvents:
    """Codex needs two events Claude never registers — see hook_handler.py's
    module docstring on CODEX_HOOK_EVENTS."""

    def test_includes_session_start_and_interrupt(self):
        assert "SessionStart" in CODEX_HOOK_EVENTS
        assert "Interrupt" in CODEX_HOOK_EVENTS

    def test_matches_the_design_doc_s_event_list(self):
        # design doc §2.3/§5 Phase 2 brief: UserPromptSubmit, PreToolUse,
        # PostToolUse, PermissionRequest, Stop, Interrupt, SessionStart,
        # SessionEnd — codex's PreCompact/PostCompact/SubagentStart/
        # SubagentStop exist but have no overcode-side meaning yet.
        assert CODEX_HOOK_EVENTS == (
            "UserPromptSubmit", "PreToolUse", "PostToolUse", "PermissionRequest",
            "Stop", "Interrupt", "SessionStart", "SessionEnd",
        )

    def test_does_not_leak_into_claudes_own_hook_list(self):
        # OVERCODE_HOOKS is what claude_code.py's --settings injection reads
        # — codex-only events must never end up registered for Claude.
        claude_events = {event for event, _cmd in OVERCODE_HOOKS}
        assert "SessionStart" not in claude_events
        assert "Interrupt" not in claude_events


class TestDialectNormalization:
    """_normalize_hook_payload — one call site for every stdin dialect.

    Codex's stdin is already snake_case/Claude-shaped (Appendix A of the
    design doc — hook_event_name/session_id/turn_id/transcript_path/cwd/
    model/permission_mode/prompt captured live), so it is a pure pass-
    through today; the camelCase branch is exercised directly since grok
    (Phase 4) isn't wired to a real stdin yet.
    """

    # Verbatim payload captured live in Phase 0 (design doc §2.3) —
    # confirms codex needs no translation at all.
    CODEX_USER_PROMPT_SUBMIT = {
        "session_id": "01a043a2-f2fc-7f72-ac4a-6af740fcd4dc",
        "turn_id": "01a043a3-05d4-7072-b885-22e30a6454e5",
        "transcript_path": (
            "/Users/mike/.codex/sessions/2026/08/27/"
            "rollout-2026-08-27T15-32-27-01a043a2-f2fc-7f72-ac4a-6af740fcd4dc.jsonl"
        ),
        "cwd": "/Users/mike/.claude/jobs/f6bc7dbe/tmp/probe-codex",
        "hook_event_name": "UserPromptSubmit",
        "model": "gpt-5.6-sol",
        "permission_mode": "default",
        "prompt": "Reply with exactly: hook-tui-test",
    }

    def test_codex_payload_passes_through_unchanged(self):
        assert _normalize_hook_payload(self.CODEX_USER_PROMPT_SUBMIT) == self.CODEX_USER_PROMPT_SUBMIT

    def test_claude_payload_passes_through_unchanged(self):
        payload = {"hook_event_name": "Stop", "session_id": "abc"}
        assert _normalize_hook_payload(payload) == payload

    def test_camel_case_dialect_is_translated(self):
        # Shape grok is documented to send (design doc §3.3) — not wired to
        # a real backend yet, but the normalization mechanism is shared.
        payload = {
            "hookEventName": "user_prompt_submit",
            "sessionId": "01a043a2-...",
            "toolName": "Bash",
            "toolInput": {"command": "echo hi"},
            "toolUseId": "call_1",
            "permissionMode": "default",
        }
        normalized = _normalize_hook_payload(payload)
        assert normalized["hook_event_name"] == "user_prompt_submit"
        assert normalized["session_id"] == "01a043a2-..."
        assert normalized["tool_name"] == "Bash"
        assert normalized["tool_input"] == {"command": "echo hi"}
        assert normalized["tool_use_id"] == "call_1"
        assert normalized["permission_mode"] == "default"
        # Originals are kept alongside the translated keys, not replaced.
        assert normalized["hookEventName"] == "user_prompt_submit"

    def test_non_dict_input_is_returned_as_is(self):
        assert _normalize_hook_payload(None) is None  # type: ignore[arg-type]

    def test_existing_snake_case_key_wins_over_camel_case(self):
        # Defensive: if a payload somehow carries both, the explicit
        # snake_case value is never clobbered by the camelCase translation.
        payload = {
            "hookEventName": "user_prompt_submit",
            "hook_event_name": "UserPromptSubmit",
        }
        assert _normalize_hook_payload(payload)["hook_event_name"] == "UserPromptSubmit"


class TestSessionStartRecordsSessionId:
    """codex has no --session-id flag, so SessionStart's stdin session_id is
    the only way overcode learns which rollout file is this agent's own."""

    def _state(self, tmp_path, agent="test-agent"):
        return json.loads((tmp_path / "agents" / f"hook_state_{agent}.json").read_text())

    def _send(self, tmp_path, monkeypatch, payload):
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "test-agent")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = json.dumps(payload)
            handle_hook_event()

    def test_session_start_records_agent_session_id(self, monkeypatch, tmp_path):
        self._send(tmp_path, monkeypatch, {
            "hook_event_name": "SessionStart",
            "session_id": "01a0439d-63b8-71d0-bf11-38fb10d0f551",
        })
        data = self._state(tmp_path)
        assert data["agent_session_id"] == "01a0439d-63b8-71d0-bf11-38fb10d0f551"
        assert data["agent_session_ids"] == ["01a0439d-63b8-71d0-bf11-38fb10d0f551"]

    def test_session_id_persists_across_later_events(self, monkeypatch, tmp_path):
        self._send(tmp_path, monkeypatch, {
            "hook_event_name": "SessionStart", "session_id": "sid-1",
        })
        self._send(tmp_path, monkeypatch, {
            "hook_event_name": "UserPromptSubmit", "session_id": "sid-1",
            "prompt": "hello",
        })
        data = self._state(tmp_path)
        assert data["event"] == "UserPromptSubmit"
        assert data["agent_session_id"] == "sid-1"

    def test_every_event_records_session_id(self, monkeypatch, tmp_path):
        # Claude never sends SessionStart to overcode, so waiting for one
        # meant its id was only ever the launch-time prescribed value. Every
        # event carries the id, so every event records it.
        self._send(tmp_path, monkeypatch, {
            "hook_event_name": "Stop", "session_id": "sid-from-stop",
        })
        data = self._state(tmp_path)
        assert data["agent_session_id"] == "sid-from-stop"
        assert data["agent_session_ids"] == ["sid-from-stop"]

    def test_context_reset_moves_the_recorded_id(self, monkeypatch, tmp_path):
        # /clear mints a new session id mid-flight; the newest one must win,
        # because that is the conversation `restart --resume` has to land in.
        self._send(tmp_path, monkeypatch, {
            "hook_event_name": "UserPromptSubmit", "session_id": "before-clear",
        })
        self._send(tmp_path, monkeypatch, {
            "hook_event_name": "PostToolUse", "session_id": "after-clear",
        })
        data = self._state(tmp_path)
        assert data["agent_session_id"] == "after-clear"
        assert data["agent_session_ids"] == ["before-clear", "after-clear"]

    def test_a_second_session_start_appends_without_duplicating(self, monkeypatch, tmp_path):
        self._send(tmp_path, monkeypatch, {
            "hook_event_name": "SessionStart", "session_id": "sid-1",
        })
        self._send(tmp_path, monkeypatch, {
            "hook_event_name": "SessionStart", "session_id": "sid-2",
        })
        self._send(tmp_path, monkeypatch, {
            "hook_event_name": "SessionStart", "session_id": "sid-1",  # repeat
        })
        data = self._state(tmp_path)
        assert data["agent_session_ids"] == ["sid-1", "sid-2"]
        assert data["agent_session_id"] == "sid-1"


class TestAgentSessionIdSync:
    """A reset conversation must reach sessions.json, because `restart`
    relaunches with `--resume <active_agent_session_id>` and a stale value
    silently drops everything the agent did after the reset.

    Every case here drives `handle_hook_event` and asserts the persisted
    session record, so the contract under test is the restart-visible
    behaviour rather than any particular helper.
    """

    def _send(self, tmp_path, monkeypatch, payload, agent="test-agent"):
        monkeypatch.setenv("OVERCODE_SESSION_NAME", agent)
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = json.dumps(payload)
            handle_hook_event()

    def _registered_agent(self, tmp_path, monkeypatch, launch_sid="launch-time-sid"):
        """Create a real overcode session record for the agent under test."""
        from overcode.session_manager import SessionManager

        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        sm = SessionManager(skip_git_detection=True)
        created = sm.create_session(
            name="test-agent", tmux_session="agents", tmux_window="test-agent-1",
            command=["claude"],
        )
        # Mirrors what the launcher records for a fresh launch (launcher.py):
        # the prescribed id is both owned and active.
        sm.add_agent_session_id(created.id, launch_sid)
        sm.set_active_agent_session_id(created.id, launch_sid)
        return created.id

    def _record(self, tmp_path):
        from overcode.session_manager import SessionManager

        return SessionManager(skip_git_detection=True).get_session_by_name("test-agent")

    def test_a_reset_repoints_the_record(self, monkeypatch, tmp_path):
        self._registered_agent(tmp_path, monkeypatch)
        self._send(tmp_path, monkeypatch, {
            "hook_event_name": "PostToolUse", "session_id": "after-clear",
        })
        record = self._record(tmp_path)
        assert record.active_agent_session_id == "after-clear"
        assert "after-clear" in record.agent_session_ids

    def test_the_replaced_id_is_kept_in_the_history(self, monkeypatch, tmp_path):
        # History is what later events compare against, and it is also the
        # precise filter stats/history lookup uses, so the outgoing id must
        # survive the move.
        self._registered_agent(tmp_path, monkeypatch, launch_sid="before-clear")
        self._send(tmp_path, monkeypatch, {
            "hook_event_name": "Stop", "session_id": "after-clear",
        })
        assert self._record(tmp_path).agent_session_ids == ["before-clear", "after-clear"]

    def test_unchanged_id_leaves_the_record_alone(self, monkeypatch, tmp_path):
        self._registered_agent(tmp_path, monkeypatch, launch_sid="sid-1")
        for event in ("UserPromptSubmit", "PostToolUse", "Stop"):
            self._send(tmp_path, monkeypatch, {
                "hook_event_name": event, "session_id": "sid-1",
            })
        record = self._record(tmp_path)
        assert record.active_agent_session_id == "sid-1"
        assert record.agent_session_ids == ["sid-1"]

    def test_payload_without_session_id_leaves_the_record_alone(self, monkeypatch, tmp_path):
        self._registered_agent(tmp_path, monkeypatch)
        self._send(tmp_path, monkeypatch, {"hook_event_name": "Stop"})
        assert self._record(tmp_path).active_agent_session_id == "launch-time-sid"

    def test_an_unregistered_agent_is_tolerated(self, monkeypatch, tmp_path):
        # Hooks also fire for agents overcode does not manage; that must not
        # raise (a failing hook takes the agent's turn with it) and must not
        # invent a record.
        self._send(tmp_path, monkeypatch, {
            "hook_event_name": "Stop", "session_id": "orphan-sid",
        })
        assert self._record(tmp_path) is None

    def test_a_failed_write_is_retried_on_the_next_event(self, monkeypatch, tmp_path):
        from overcode.session_manager import SessionManager

        self._registered_agent(tmp_path, monkeypatch)

        # Fail the durable write once, at the SessionManager boundary.
        real_advance = SessionManager.advance_active_agent_session_id
        calls = {"n": 0}

        def flaky(self, session_id, agent_session_id, ordinal=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("session store unavailable")
            return real_advance(self, session_id, agent_session_id, ordinal=ordinal)

        monkeypatch.setattr(
            SessionManager, "advance_active_agent_session_id", flaky
        )
        self._send(tmp_path, monkeypatch, {
            "hook_event_name": "PostToolUse", "session_id": "after-clear",
        })
        assert self._record(tmp_path).active_agent_session_id == "launch-time-sid"

        # Nothing recorded the attempt as done, so the next event tries again.
        self._send(tmp_path, monkeypatch, {
            "hook_event_name": "Stop", "session_id": "after-clear",
        })
        assert self._record(tmp_path).active_agent_session_id == "after-clear"

    def test_a_superseded_id_does_not_drag_the_record_backwards(self, monkeypatch, tmp_path):
        self._registered_agent(tmp_path, monkeypatch, launch_sid="before-clear")
        self._send(tmp_path, monkeypatch, {
            "hook_event_name": "PostToolUse", "session_id": "after-clear",
        })
        assert self._record(tmp_path).active_agent_session_id == "after-clear"

        # An event that was in flight across the reset still carries the old
        # id. Acting on it would point restart at a conversation the agent has
        # already left.
        self._send(tmp_path, monkeypatch, {
            "hook_event_name": "Stop", "session_id": "before-clear",
        })
        assert self._record(tmp_path).active_agent_session_id == "after-clear"

    def test_a_stale_event_does_not_poison_the_hook_snapshot(self, monkeypatch, tmp_path):
        # The hook-state id is what CodexStatsReader treats as current, so a
        # superseded event must not land there either.
        self._registered_agent(tmp_path, monkeypatch, launch_sid="before-clear")
        self._send(tmp_path, monkeypatch, {
            "hook_event_name": "PostToolUse", "session_id": "after-clear",
        })
        self._send(tmp_path, monkeypatch, {
            "hook_event_name": "Stop", "session_id": "before-clear",
        })
        state = json.loads(
            (tmp_path / "agents" / "hook_state_test-agent.json").read_text()
        )
        assert state["agent_session_id"] == "after-clear"
        assert self._record(tmp_path).active_agent_session_id == "after-clear"

    def test_an_older_unseen_id_is_rejected_by_age(self, monkeypatch, tmp_path):
        # Two resets in quick succession: the newer id can take the lock first,
        # leaving the older id unseen. History ordering cannot catch that, so
        # the transcript's age decides.
        older = tmp_path / "older.jsonl"
        newer = tmp_path / "newer.jsonl"
        older.write_text("{}")
        newer.write_text("{}")
        os.utime(older, (1_000_000, 1_000_000))
        os.utime(newer, (2_000_000, 2_000_000))

        self._registered_agent(tmp_path, monkeypatch, launch_sid="launch-time-sid")
        self._send(tmp_path, monkeypatch, {
            "hook_event_name": "PostToolUse",
            "session_id": "reset-b",
            "transcript_path": str(newer),
        })
        assert self._record(tmp_path).active_agent_session_id == "reset-b"

        # reset-a was never recorded, so only its age marks it as the older
        # conversation.
        self._send(tmp_path, monkeypatch, {
            "hook_event_name": "Stop",
            "session_id": "reset-a",
            "transcript_path": str(older),
        })
        assert self._record(tmp_path).active_agent_session_id == "reset-b"

    def test_advance_reports_the_id_the_record_holds(self, monkeypatch, tmp_path):
        # The caller writes this id into hook state, so it has to come from
        # inside the transaction rather than from a pre-lock snapshot.
        from overcode.session_manager import SessionManager

        overcode_id = self._registered_agent(tmp_path, monkeypatch, launch_sid="sid-a")
        sm = SessionManager(skip_git_detection=True)

        # A relaunch sets the active id without any age, so it starts out
        # unordered — and an age arriving later settles it.
        assert sm.advance_active_agent_session_id(overcode_id, "sid-a") == (
            "current_unordered", "sid-a",
        )
        assert sm.advance_active_agent_session_id(overcode_id, "sid-a", ordinal=100.0) == (
            "advanced", "sid-a",
        )
        assert sm.advance_active_agent_session_id(overcode_id, "sid-a") == (
            "current", "sid-a",
        )
        # sid-b with no age of its own cannot be ordered against sid-a's, so
        # it advances unconfirmed; with an age it advances outright.
        assert sm.advance_active_agent_session_id(overcode_id, "sid-b") == (
            "advanced_unordered", "sid-b",
        )
        assert sm.advance_active_agent_session_id(overcode_id, "sid-b", ordinal=200.0) == (
            "advanced", "sid-b",
        )
        # sid-a is now in history and no longer newest, so it is refused — and
        # the current id comes back with the refusal.
        assert sm.advance_active_agent_session_id(overcode_id, "sid-a") == (
            "superseded", "sid-b",
        )
        assert sm.advance_active_agent_session_id("no-such-session", "sid-c") == (
            "unknown", None,
        )

    def test_an_unorderable_advance_stays_retryable(self, monkeypatch, tmp_path):
        # Once the record carries ordering metadata, an event that cannot be
        # ordered against it is recorded (staleness is the worse failure) but
        # not confirmed, so a later orderable event gets to re-decide.
        transcript = tmp_path / "a.jsonl"
        transcript.write_text("{}")
        os.utime(transcript, (1_000_000, 1_000_000))

        self._registered_agent(tmp_path, monkeypatch, launch_sid="launch-time-sid")
        self._send(tmp_path, monkeypatch, {
            "hook_event_name": "PostToolUse",
            "session_id": "reset-a",
            "transcript_path": str(transcript),
        })
        assert self._record(tmp_path).active_agent_session_ordinal == 1_000_000
        assert self._record(tmp_path).active_agent_session_unordered is False

        # No transcript path: recorded, but flagged as unordered rather than
        # silently treated as the confirmed newest conversation.
        self._send(tmp_path, monkeypatch, {
            "hook_event_name": "Stop", "session_id": "reset-b",
        })
        record = self._record(tmp_path)
        assert record.active_agent_session_id == "reset-b"
        assert record.active_agent_session_unordered is True
        assert record.active_agent_session_ordinal is None

        # The same id arriving with an age settles it, so "unconfirmed" is
        # recoverable rather than permanent.
        later = tmp_path / "b.jsonl"
        later.write_text("{}")
        os.utime(later, (3_000_000, 3_000_000))
        self._send(tmp_path, monkeypatch, {
            "hook_event_name": "Stop",
            "session_id": "reset-b",
            "transcript_path": str(later),
        })
        record = self._record(tmp_path)
        assert record.active_agent_session_unordered is False
        assert record.active_agent_session_ordinal == 3_000_000

    def test_history_is_not_truncated(self, monkeypatch, tmp_path):
        # agent_session_ids is the precise filter for transcript, stats and
        # pid-ownership lookup, so a long-lived agent must not lose old ids.
        self._registered_agent(tmp_path, monkeypatch, launch_sid="sid-0")
        for i in range(1, 30):
            self._send(tmp_path, monkeypatch, {
                "hook_event_name": "Stop", "session_id": f"sid-{i}",
            })
        record = self._record(tmp_path)
        assert record.agent_session_ids == [f"sid-{i}" for i in range(30)]
        assert record.active_agent_session_id == "sid-29"



class TestInterruptEvent:
    def test_interrupt_writes_state(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "test-agent")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = json.dumps({
                "hook_event_name": "Interrupt", "session_id": "sid-1",
            })
            handle_hook_event()
        data = json.loads((tmp_path / "agents" / "hook_state_test-agent.json").read_text())
        assert data["event"] == "Interrupt"


class TestEventLogBytes:
    """The event log stores what its readers use and is bounded by bytes (audit R12)."""

    def test_consumed_input_fields_are_kept_and_strings_cut(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        long_command = "echo " + "x" * 5000
        append_hook_event(
            "PreToolUse", "agents", "a1", tool_name="Bash",
            tool_input={"command": long_command, "run_in_background": True, "timeout": 5},
        )
        path = tmp_path / "agents" / "hook_events_a1.jsonl"
        entry = json.loads(path.read_text().splitlines()[-1])
        assert entry["tool_input"]["command"] == long_command[:256]
        assert entry["tool_input"]["run_in_background"] is True
        assert "timeout" not in entry["tool_input"]
        assert json.loads(entry["tool_input"]["_preview"]) == {"timeout": 5}
        assert len(path.read_bytes()) < 600

    def test_edit_bodies_become_a_preview(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        body = {"file_path": "/repo/a.py", "old_string": "a" * 20_000, "new_string": "b" * 20_000}
        append_hook_event("PreToolUse", "agents", "a1", tool_name="Edit", tool_input=body)
        path = tmp_path / "agents" / "hook_events_a1.jsonl"
        entry = json.loads(path.read_text().splitlines()[-1])
        assert set(entry["tool_input"]) == {"_preview"}
        assert entry["tool_input"]["_preview"].startswith('{"file_path": "/repo/a.py"')
        assert len(entry["tool_input"]["_preview"]) == 256
        assert len(path.read_bytes()) < 400

    def test_obligation_fields_survive(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        append_hook_event(
            "PreToolUse", "agents", "a1", tool_name="CronCreate",
            tool_input={"id": "c1", "schedule": "*/5 * * * *", "prompt": "check", "extra": [1]},
        )
        append_hook_event(
            "PreToolUse", "agents", "a1", tool_name="ScheduleWakeup",
            tool_input={"delaySeconds": 90, "reason": "poll"},
        )
        append_hook_event(
            "PreToolUse", "agents", "a1", tool_name="Skill", tool_input={"skill": "overcode"},
        )
        path = tmp_path / "agents" / "hook_events_a1.jsonl"
        cron, wake, skill = [json.loads(l)["tool_input"] for l in path.read_text().splitlines()]
        assert cron["id"] == "c1" and cron["schedule"] == "*/5 * * * *" and cron["prompt"] == "check"
        assert cron["_preview"] == '{"extra": [1]}'
        assert wake["delaySeconds"] == 90 and wake["_preview"] == '{"reason": "poll"}'
        assert skill == {"skill": "overcode"}

    def test_snapshot_keeps_the_whole_input(self, monkeypatch, tmp_path):
        """The consumers of tool_input read hook_state; it is not compacted."""
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        body = {"command": "x" * 1000, "timeout": 5}
        write_hook_state("PreToolUse", "agents", "a1", tool_name="Bash", tool_input=body)
        snap = json.loads((tmp_path / "agents" / "hook_state_a1.json").read_text())
        assert snap["tool_input"] == body

    def test_rotation_keeps_the_last_keep_bytes_of_whole_lines(self, monkeypatch, tmp_path):
        import overcode.hook_handler as hh
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        path = tmp_path / "agents" / "hook_events_a1.jsonl"
        path.parent.mkdir(parents=True)
        lines = [json.dumps({"event": "PreToolUse", "timestamp": float(i), "pad": "p" * 90}) + "\n"
                 for i in range(2000)]
        path.write_text("".join(lines))
        size = path.stat().st_size
        assert size > hh._EVENT_LOG_ROTATE_BYTES

        hh._rotate_event_log(path)

        kept = path.read_bytes()
        assert len(kept) <= hh._EVENT_LOG_KEEP_BYTES
        assert kept.endswith(b"\n")
        kept_lines = kept.decode().splitlines()
        first = json.loads(kept_lines[0])  # a whole line: the partial head was dropped
        assert [json.loads(l)["timestamp"] for l in kept_lines] == list(
            range(int(first["timestamp"]), 2000)
        )
        assert not list(path.parent.glob("*.tmp"))

    def test_rotation_is_not_per_event_once_lines_are_long(self, monkeypatch, tmp_path):
        """The 200-line rule rewrote a long-lined file on every append; bytes do not."""
        import overcode.hook_handler as hh
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        replaced = []
        real_replace = os.replace

        def counting_replace(src, dst):
            replaced.append(dst)
            return real_replace(src, dst)

        monkeypatch.setattr(hh.os, "replace", counting_replace)
        big = {"command": "c" * 7000}  # 7 KB inputs: 200 lines would be 1.4 MB
        for _ in range(400):
            append_hook_event("PreToolUse", "agents", "a1", tool_name="Bash", tool_input=big)
        path = tmp_path / "agents" / "hook_events_a1.jsonl"
        # ~300 B per line after compaction: a rotation every ~120 appends, not every one
        assert len(replaced) <= 5, len(replaced)
        assert path.stat().st_size <= hh._EVENT_LOG_ROTATE_BYTES + 1024
        assert all(json.loads(l) for l in path.read_text().splitlines())

    def test_rotation_leaves_a_single_oversized_line_alone(self, monkeypatch, tmp_path):
        import overcode.hook_handler as hh
        path = tmp_path / "hook_events_a1.jsonl"
        path.write_bytes(b"{" + b"x" * (hh._EVENT_LOG_KEEP_BYTES + 10))
        before = path.read_bytes()
        hh._rotate_event_log(path)
        assert path.read_bytes() == before
