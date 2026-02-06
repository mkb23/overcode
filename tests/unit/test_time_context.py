"""
Unit tests for time_context module.
"""

import json
import pytest
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.time_context import (
    get_agent_identity,
    format_clock,
    format_presence,
    format_office_hours,
    format_uptime,
    format_heartbeat,
    read_heartbeat_timestamp,
    build_time_context_line,
    generate_time_context,
)


class TestGetAgentIdentity:
    """Test environment variable reading."""

    def test_returns_both_when_set(self, monkeypatch):
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "my-agent")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        name, tmux = get_agent_identity()
        assert name == "my-agent"
        assert tmux == "agents"

    def test_returns_none_when_missing(self, monkeypatch):
        monkeypatch.delenv("OVERCODE_SESSION_NAME", raising=False)
        monkeypatch.delenv("OVERCODE_TMUX_SESSION", raising=False)
        name, tmux = get_agent_identity()
        assert name is None
        assert tmux is None

    def test_returns_partial(self, monkeypatch):
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "test")
        monkeypatch.delenv("OVERCODE_TMUX_SESSION", raising=False)
        name, tmux = get_agent_identity()
        assert name == "test"
        assert tmux is None


class TestFormatClock:
    """Test clock formatting."""

    def test_basic_format(self):
        # Use a timezone-aware datetime
        pst = timezone(timedelta(hours=-8))
        now = datetime(2025, 1, 15, 14, 32, 0, tzinfo=pst)
        result = format_clock(now)
        assert result.startswith("14:32")

    def test_leading_zero_hours(self):
        pst = timezone(timedelta(hours=-8))
        now = datetime(2025, 1, 15, 9, 5, 0, tzinfo=pst)
        result = format_clock(now)
        assert result.startswith("09:05")

    def test_midnight(self):
        utc = timezone.utc
        now = datetime(2025, 1, 15, 0, 0, 0, tzinfo=utc)
        result = format_clock(now)
        assert result.startswith("00:00")


class TestFormatPresence:
    """Test presence state formatting."""

    def test_active(self):
        assert format_presence(3) == "active"

    def test_inactive(self):
        assert format_presence(2) == "inactive"

    def test_locked(self):
        assert format_presence(1) == "locked"

    def test_none(self):
        assert format_presence(None) == "unknown"

    def test_invalid_value(self):
        assert format_presence(99) == "unknown"


class TestFormatOfficeHours:
    """Test office hours calculation."""

    def test_within_normal_hours(self):
        now = datetime(2025, 1, 15, 10, 0)
        assert format_office_hours(now, 9, 17) == "yes"

    def test_before_normal_hours(self):
        now = datetime(2025, 1, 15, 7, 0)
        assert format_office_hours(now, 9, 17) == "no"

    def test_after_normal_hours(self):
        now = datetime(2025, 1, 15, 18, 0)
        assert format_office_hours(now, 9, 17) == "no"

    def test_at_start_boundary(self):
        now = datetime(2025, 1, 15, 9, 0)
        assert format_office_hours(now, 9, 17) == "yes"

    def test_at_end_boundary(self):
        # End hour is exclusive (17:00 is not in 9-17)
        now = datetime(2025, 1, 15, 17, 0)
        assert format_office_hours(now, 9, 17) == "no"

    def test_midnight_wrap_during_night(self):
        # 22:00-06:00 range, currently 23:00
        now = datetime(2025, 1, 15, 23, 0)
        assert format_office_hours(now, 22, 6) == "yes"

    def test_midnight_wrap_early_morning(self):
        # 22:00-06:00 range, currently 03:00
        now = datetime(2025, 1, 15, 3, 0)
        assert format_office_hours(now, 22, 6) == "yes"

    def test_midnight_wrap_outside(self):
        # 22:00-06:00 range, currently 12:00
        now = datetime(2025, 1, 15, 12, 0)
        assert format_office_hours(now, 22, 6) == "no"


class TestFormatUptime:
    """Test uptime formatting."""

    def test_hours_and_minutes(self):
        now = datetime(2025, 1, 15, 15, 23, 0)
        start = "2025-01-15T14:00:00"
        result = format_uptime(start, now)
        assert result == "1h23m"

    def test_minutes_only(self):
        now = datetime(2025, 1, 15, 14, 45, 0)
        start = "2025-01-15T14:00:00"
        result = format_uptime(start, now)
        assert result == "45m"

    def test_zero_minutes(self):
        now = datetime(2025, 1, 15, 16, 0, 30)
        start = "2025-01-15T14:00:00"
        result = format_uptime(start, now)
        assert result == "2h0m"

    def test_none_start(self):
        now = datetime(2025, 1, 15, 14, 0)
        assert format_uptime(None, now) is None

    def test_empty_start(self):
        now = datetime(2025, 1, 15, 14, 0)
        assert format_uptime("", now) is None

    def test_invalid_iso(self):
        now = datetime(2025, 1, 15, 14, 0)
        assert format_uptime("not-a-date", now) is None

    def test_negative_uptime(self):
        now = datetime(2025, 1, 15, 13, 0, 0)
        start = "2025-01-15T14:00:00"
        result = format_uptime(start, now)
        assert result == "0m"


class TestFormatHeartbeat:
    """Test heartbeat formatting."""

    def test_disabled(self):
        now = datetime(2025, 1, 15, 14, 0)
        assert format_heartbeat(None, None, now) is None

    def test_zero_interval(self):
        now = datetime(2025, 1, 15, 14, 0)
        assert format_heartbeat(0, None, now) is None

    def test_no_last_timestamp(self):
        now = datetime(2025, 1, 15, 14, 0)
        result = format_heartbeat(15, None, now)
        assert result == "15m (next: now)"

    def test_overdue(self):
        now = datetime(2025, 1, 15, 14, 30, 0)
        last = "2025-01-15T14:00:00"
        result = format_heartbeat(15, last, now)
        assert result == "15m (next: now)"

    def test_remaining_time(self):
        now = datetime(2025, 1, 15, 14, 8, 0)
        last = "2025-01-15T14:00:00"
        result = format_heartbeat(15, last, now)
        assert result == "15m (next: 7m)"

    def test_invalid_last_timestamp(self):
        now = datetime(2025, 1, 15, 14, 0)
        result = format_heartbeat(15, "bad-date", now)
        assert result == "15m (next: now)"


class TestReadHeartbeatTimestamp:
    """Test heartbeat file reading."""

    def test_reads_existing_file(self, tmp_path, monkeypatch):
        # Create the file structure
        hb_dir = tmp_path / ".overcode" / "sessions" / "agents"
        hb_dir.mkdir(parents=True)
        hb_file = hb_dir / "heartbeat_my-agent.last"
        hb_file.write_text("2025-01-15T14:00:00\n")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = read_heartbeat_timestamp("agents", "my-agent")
        assert result == "2025-01-15T14:00:00"

    def test_returns_none_for_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = read_heartbeat_timestamp("agents", "nonexistent")
        assert result is None


class TestBuildTimeContextLine:
    """Test line assembly."""

    def test_all_fields(self):
        result = build_time_context_line(
            clock="14:32 PST",
            presence="active",
            office="yes",
            uptime="1h23m",
            heartbeat="15m (next: 7m)",
        )
        assert result == "Clock: 14:32 PST | User: active | Office: yes | Uptime: 1h23m | Heartbeat: 15m (next: 7m)"

    def test_minimal_fields(self):
        result = build_time_context_line(
            clock="14:32 PST",
            presence="unknown",
            office="no",
        )
        assert result == "Clock: 14:32 PST | User: unknown | Office: no"
        assert "Uptime" not in result
        assert "Heartbeat" not in result

    def test_uptime_only(self):
        result = build_time_context_line(
            clock="09:00 EST",
            presence="active",
            office="yes",
            uptime="45m",
        )
        assert "Uptime: 45m" in result
        assert "Heartbeat" not in result

    def test_heartbeat_only(self):
        result = build_time_context_line(
            clock="09:00 EST",
            presence="active",
            office="yes",
            heartbeat="15m (next: now)",
        )
        assert "Heartbeat: 15m (next: now)" in result
        assert "Uptime" not in result


class TestGenerateTimeContext:
    """Integration test for the orchestrator."""

    def test_full_output_with_daemon_state(self, tmp_path, monkeypatch):
        """Test with mocked daemon state and config."""
        # Set up state dir
        state_dir = tmp_path / "state"
        session_dir = state_dir / "agents"
        session_dir.mkdir(parents=True)

        monkeypatch.setenv("OVERCODE_STATE_DIR", str(state_dir))

        # Write daemon state
        daemon_state = {
            "presence_state": 3,
            "sessions": [
                {
                    "name": "my-agent",
                    "start_time": "2025-01-15T13:00:00",
                }
            ],
        }
        state_file = session_dir / "monitor_daemon_state.json"
        state_file.write_text(json.dumps(daemon_state))

        config = {
            "office_start": 9,
            "office_end": 17,
            "heartbeat_interval_minutes": None,
        }

        now = datetime(2025, 1, 15, 14, 32, 0)
        result = generate_time_context("agents", "my-agent", now=now, config=config)

        assert "Clock: 14:32" in result
        assert "User: active" in result
        assert "Office: yes" in result
        assert "Uptime: 1h32m" in result
        assert "Heartbeat" not in result

    def test_no_daemon_state(self, tmp_path, monkeypatch):
        """Test graceful degradation when daemon state is missing."""
        state_dir = tmp_path / "state"
        session_dir = state_dir / "agents"
        session_dir.mkdir(parents=True)

        monkeypatch.setenv("OVERCODE_STATE_DIR", str(state_dir))

        config = {
            "office_start": 9,
            "office_end": 17,
            "heartbeat_interval_minutes": None,
        }

        now = datetime(2025, 1, 15, 14, 0, 0)
        result = generate_time_context("agents", "my-agent", now=now, config=config)

        assert "User: unknown" in result
        assert "Uptime" not in result

    def test_session_not_in_daemon_state(self, tmp_path, monkeypatch):
        """Test when session exists in daemon but our agent isn't tracked."""
        state_dir = tmp_path / "state"
        session_dir = state_dir / "agents"
        session_dir.mkdir(parents=True)

        monkeypatch.setenv("OVERCODE_STATE_DIR", str(state_dir))

        daemon_state = {
            "presence_state": 2,
            "sessions": [
                {"name": "other-agent", "start_time": "2025-01-15T10:00:00"}
            ],
        }
        state_file = session_dir / "monitor_daemon_state.json"
        state_file.write_text(json.dumps(daemon_state))

        config = {
            "office_start": 9,
            "office_end": 17,
            "heartbeat_interval_minutes": None,
        }

        now = datetime(2025, 1, 15, 14, 0, 0)
        result = generate_time_context("agents", "my-agent", now=now, config=config)

        assert "User: inactive" in result
        assert "Uptime" not in result

    def test_with_heartbeat(self, tmp_path, monkeypatch):
        """Test heartbeat field when configured."""
        state_dir = tmp_path / "state"
        session_dir = state_dir / "agents"
        session_dir.mkdir(parents=True)

        monkeypatch.setenv("OVERCODE_STATE_DIR", str(state_dir))

        # Write daemon state
        state_file = session_dir / "monitor_daemon_state.json"
        state_file.write_text(json.dumps({"sessions": []}))

        # Write heartbeat file
        hb_dir = tmp_path / ".overcode" / "sessions" / "agents"
        hb_dir.mkdir(parents=True)
        hb_file = hb_dir / "heartbeat_my-agent.last"
        hb_file.write_text("2025-01-15T14:00:00")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        config = {
            "office_start": 9,
            "office_end": 17,
            "heartbeat_interval_minutes": 15,
        }

        now = datetime(2025, 1, 15, 14, 8, 0)
        result = generate_time_context("agents", "my-agent", now=now, config=config)

        assert "Heartbeat: 15m (next: 7m)" in result


class TestCliSilentExit:
    """Test CLI silent exit when env vars are missing."""

    def test_silent_exit_no_env_vars(self, monkeypatch):
        """time-context command should exit silently when env vars missing."""
        from typer.testing import CliRunner
        from overcode.cli import app

        monkeypatch.delenv("OVERCODE_SESSION_NAME", raising=False)
        monkeypatch.delenv("OVERCODE_TMUX_SESSION", raising=False)

        runner = CliRunner()
        result = runner.invoke(app, ["time-context"])

        assert result.exit_code == 0
        assert result.output.strip() == ""

    def test_outputs_line_with_env_vars(self, tmp_path, monkeypatch):
        """time-context command should output a line when env vars are set."""
        from typer.testing import CliRunner
        from overcode.cli import app

        monkeypatch.setenv("OVERCODE_SESSION_NAME", "test-agent")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")

        # Set up empty state dir so daemon state is missing (graceful degradation)
        state_dir = tmp_path / "state"
        session_dir = state_dir / "agents"
        session_dir.mkdir(parents=True)
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(state_dir))

        runner = CliRunner()
        result = runner.invoke(app, ["time-context"])

        assert result.exit_code == 0
        output = result.output.strip()
        assert "Clock:" in output
        assert "User:" in output
        assert "Office:" in output


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
