"""
Tests for status history tracking.
"""

import csv
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from overcode.status_history import (
    log_agent_status,
    read_agent_status_history,
    get_agent_timeline,
    clear_old_history,
)


class TestLogAgentStatus:
    """Tests for log_agent_status function."""

    def test_creates_file_with_header(self):
        """Should create file with CSV header on first write."""
        with TemporaryDirectory() as tmpdir:
            history_file = Path(tmpdir) / "history.csv"

            log_agent_status("agent1", "running", "Working...", history_file)

            assert history_file.exists()
            content = history_file.read_text()
            assert "timestamp,agent,status,activity" in content
            assert "agent1" in content
            assert "running" in content

    def test_appends_to_existing_file(self):
        """Should append to existing file without duplicate header."""
        with TemporaryDirectory() as tmpdir:
            history_file = Path(tmpdir) / "history.csv"

            log_agent_status("agent1", "running", "", history_file)
            log_agent_status("agent2", "waiting_user", "", history_file)

            content = history_file.read_text()
            # Should have only one header
            assert content.count("timestamp,agent,status") == 1
            # Should have both agents
            assert "agent1" in content
            assert "agent2" in content

    def test_truncates_long_activity(self):
        """Should truncate activity to 100 characters."""
        with TemporaryDirectory() as tmpdir:
            history_file = Path(tmpdir) / "history.csv"

            long_activity = "x" * 200
            log_agent_status("agent1", "running", long_activity, history_file)

            content = history_file.read_text()
            # Should be truncated
            assert "x" * 100 in content
            assert "x" * 101 not in content

    def test_handles_empty_activity(self):
        """Should handle empty activity string."""
        with TemporaryDirectory() as tmpdir:
            history_file = Path(tmpdir) / "history.csv"

            log_agent_status("agent1", "running", "", history_file)

            content = history_file.read_text()
            assert "agent1" in content


class TestReadAgentStatusHistory:
    """Tests for read_agent_status_history function."""

    def test_reads_recent_history(self):
        """Should read entries within time window."""
        with TemporaryDirectory() as tmpdir:
            history_file = Path(tmpdir) / "history.csv"

            # Write some entries
            log_agent_status("agent1", "running", "Working", history_file)
            log_agent_status("agent1", "waiting_user", "Waiting", history_file)

            # Read history
            history = read_agent_status_history(hours=1.0, history_file=history_file)

            assert len(history) == 2
            assert history[0][1] == "agent1"  # agent name
            assert history[0][2] == "running"  # status

    def test_filters_by_agent_name(self):
        """Should filter by agent name when specified."""
        with TemporaryDirectory() as tmpdir:
            history_file = Path(tmpdir) / "history.csv"

            log_agent_status("agent1", "running", "", history_file)
            log_agent_status("agent2", "waiting_user", "", history_file)
            log_agent_status("agent1", "waiting_user", "", history_file)

            history = read_agent_status_history(
                agent_name="agent1", history_file=history_file
            )

            assert len(history) == 2
            assert all(h[1] == "agent1" for h in history)

    def test_returns_empty_for_nonexistent_file(self):
        """Should return empty list when file doesn't exist."""
        result = read_agent_status_history(history_file=Path("/nonexistent.csv"))
        assert result == []

    def test_returns_chronological_order(self):
        """Should return entries in chronological order."""
        with TemporaryDirectory() as tmpdir:
            history_file = Path(tmpdir) / "history.csv"

            log_agent_status("agent1", "status1", "", history_file)
            log_agent_status("agent1", "status2", "", history_file)
            log_agent_status("agent1", "status3", "", history_file)

            history = read_agent_status_history(history_file=history_file)

            assert history[0][2] == "status1"
            assert history[1][2] == "status2"
            assert history[2][2] == "status3"


class TestGetAgentTimeline:
    """Tests for get_agent_timeline function."""

    def test_returns_simplified_timeline(self):
        """Should return (timestamp, status) tuples."""
        with TemporaryDirectory() as tmpdir:
            history_file = Path(tmpdir) / "history.csv"

            log_agent_status("agent1", "running", "Activity 1", history_file)
            log_agent_status("agent1", "waiting_user", "Activity 2", history_file)

            timeline = get_agent_timeline("agent1", history_file=history_file)

            assert len(timeline) == 2
            # Each entry should be (datetime, status)
            assert len(timeline[0]) == 2
            assert isinstance(timeline[0][0], datetime)
            assert timeline[0][1] == "running"


class TestClearOldHistory:
    """Tests for clear_old_history function."""

    def test_removes_old_entries(self):
        """Should remove entries older than max_age_hours."""
        with TemporaryDirectory() as tmpdir:
            history_file = Path(tmpdir) / "history.csv"

            # Create file with old entry
            old_time = datetime.now() - timedelta(hours=48)
            with open(history_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['timestamp', 'agent', 'status', 'activity'])
                writer.writerow([old_time.isoformat(), 'old_agent', 'running', ''])

            # Add recent entry
            log_agent_status("new_agent", "running", "", history_file)

            # Clear old entries
            removed = clear_old_history(max_age_hours=24.0, history_file=history_file)

            assert removed == 1

            # Read back
            history = read_agent_status_history(hours=100, history_file=history_file)
            assert len(history) == 1
            assert history[0][1] == "new_agent"

    def test_returns_zero_for_empty_file(self):
        """Should return 0 when no entries removed."""
        with TemporaryDirectory() as tmpdir:
            history_file = Path(tmpdir) / "history.csv"

            log_agent_status("agent1", "running", "", history_file)

            removed = clear_old_history(max_age_hours=24.0, history_file=history_file)

            assert removed == 0

    def test_returns_zero_for_nonexistent_file(self):
        """Should return 0 for nonexistent file."""
        removed = clear_old_history(history_file=Path("/nonexistent.csv"))
        assert removed == 0

    def test_preserves_header(self):
        """Should preserve CSV header after clearing."""
        with TemporaryDirectory() as tmpdir:
            history_file = Path(tmpdir) / "history.csv"

            # Create file with entries
            old_time = datetime.now() - timedelta(hours=48)
            with open(history_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['timestamp', 'agent', 'status', 'activity'])
                writer.writerow([old_time.isoformat(), 'old', 'running', ''])

            clear_old_history(max_age_hours=24.0, history_file=history_file)

            content = history_file.read_text()
            assert "timestamp,agent,status,activity" in content
