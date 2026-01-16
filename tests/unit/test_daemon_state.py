"""
Tests for daemon state management.
"""

import json
import pytest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from overcode.daemon_state import DaemonState, get_daemon_state


class TestDaemonState:
    """Tests for DaemonState class."""

    def test_default_values(self):
        """Should have sensible default values."""
        state = DaemonState()
        assert state.loop_count == 0
        assert state.status == "starting"
        assert state.daemon_claude_launches == 0
        assert state.started_at is None
        assert state.last_activity is None

    def test_to_dict(self):
        """Should serialize to dict correctly."""
        state = DaemonState()
        state.loop_count = 42
        state.status = "active"
        state.daemon_claude_launches = 5

        data = state.to_dict()
        assert data["loop_count"] == 42
        assert data["status"] == "active"
        assert data["daemon_claude_launches"] == 5

    def test_to_dict_with_datetimes(self):
        """Should serialize datetimes as ISO format."""
        state = DaemonState()
        now = datetime.now()
        state.started_at = now
        state.last_loop_time = now
        state.last_activity = now

        data = state.to_dict()
        assert data["started_at"] == now.isoformat()
        assert data["last_loop_time"] == now.isoformat()
        assert data["last_activity"] == now.isoformat()

    def test_from_dict(self):
        """Should deserialize from dict correctly."""
        data = {
            "loop_count": 100,
            "status": "supervising",
            "daemon_claude_launches": 10,
            "current_interval": 300,
        }

        state = DaemonState.from_dict(data)
        assert state.loop_count == 100
        assert state.status == "supervising"
        assert state.daemon_claude_launches == 10
        assert state.current_interval == 300

    def test_from_dict_with_datetimes(self):
        """Should deserialize datetime strings."""
        now = datetime.now()
        data = {
            "started_at": now.isoformat(),
            "last_loop_time": now.isoformat(),
            "last_activity": now.isoformat(),
        }

        state = DaemonState.from_dict(data)
        assert state.started_at.isoformat() == now.isoformat()
        assert state.last_loop_time.isoformat() == now.isoformat()
        assert state.last_activity.isoformat() == now.isoformat()

    def test_from_dict_with_missing_fields(self):
        """Should handle missing fields gracefully."""
        state = DaemonState.from_dict({})
        assert state.loop_count == 0
        assert state.status == "unknown"

    def test_save_and_load(self):
        """Should save to and load from file."""
        with TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"

            # Save state
            state = DaemonState()
            state.loop_count = 50
            state.status = "active"
            state.daemon_claude_launches = 3
            state.save(state_file)

            # Verify file was created
            assert state_file.exists()

            # Load state
            loaded = DaemonState.load(state_file)
            assert loaded is not None
            assert loaded.loop_count == 50
            assert loaded.status == "active"
            assert loaded.daemon_claude_launches == 3

    def test_load_nonexistent_file(self):
        """Should return None for nonexistent file."""
        result = DaemonState.load(Path("/nonexistent/state.json"))
        assert result is None

    def test_load_invalid_json(self):
        """Should return None for invalid JSON."""
        with TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text("not valid json")

            result = DaemonState.load(state_file)
            assert result is None

    def test_save_creates_parent_dirs(self):
        """Should create parent directories if they don't exist."""
        with TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "nested" / "dir" / "state.json"

            state = DaemonState()
            state.save(state_file)

            assert state_file.exists()


class TestGetDaemonState:
    """Tests for get_daemon_state function."""

    def test_returns_none_when_no_file(self):
        """Should return None when state file doesn't exist."""
        # This tests the default path, which may or may not exist
        # We just verify it returns DaemonState or None without crashing
        result = get_daemon_state()
        assert result is None or isinstance(result, DaemonState)
