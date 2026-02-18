"""
Tests for status history tracking.
"""

import csv
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from overcode.status_history import (
    StatusHistoryFile,
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


def _write_test_csv(path, rows, with_header=True):
    """Write a test CSV file with explicit timestamps.

    rows: list of (datetime, agent, status, activity) tuples
    """
    with open(path, 'w', newline='') as f:
        writer = csv.writer(f)
        if with_header:
            writer.writerow(['timestamp', 'agent', 'status', 'activity'])
        for ts, agent, status, activity in rows:
            writer.writerow([ts.isoformat(), agent, status, activity])


class TestStatusHistoryFile:
    """Tests for the StatusHistoryFile cached incremental reader."""

    def test_basic_read(self):
        """Should read all recent entries."""
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.csv"
            now = datetime.now()
            rows = [
                (now - timedelta(minutes=10), "a1", "running", "work"),
                (now - timedelta(minutes=5), "a1", "waiting_user", "wait"),
                (now - timedelta(minutes=1), "a1", "running", "more"),
            ]
            _write_test_csv(path, rows)

            reader = StatusHistoryFile(path)
            result = reader.read(hours=1.0)

            assert len(result) == 3
            assert result[0][2] == "running"
            assert result[1][2] == "waiting_user"
            assert result[2][2] == "running"

    def test_time_filtering(self):
        """Should exclude entries outside the time window."""
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.csv"
            now = datetime.now()
            rows = [
                (now - timedelta(hours=5), "a1", "old", ""),
                (now - timedelta(hours=2), "a1", "recent", ""),
                (now - timedelta(minutes=30), "a1", "newest", ""),
            ]
            _write_test_csv(path, rows)

            reader = StatusHistoryFile(path)
            result = reader.read(hours=3.0)

            assert len(result) == 2
            statuses = [r[2] for r in result]
            assert "old" not in statuses
            assert "recent" in statuses
            assert "newest" in statuses

    def test_agent_filtering(self):
        """Should filter by agent name."""
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.csv"
            now = datetime.now()
            rows = [
                (now - timedelta(minutes=10), "a1", "running", ""),
                (now - timedelta(minutes=5), "a2", "waiting", ""),
                (now - timedelta(minutes=1), "a1", "done", ""),
            ]
            _write_test_csv(path, rows)

            reader = StatusHistoryFile(path)
            result = reader.read(hours=1.0, agent_name="a1")

            assert len(result) == 2
            assert all(r[1] == "a1" for r in result)

    def test_cache_hit(self):
        """Second read with unchanged file should return cached results."""
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.csv"
            now = datetime.now()
            rows = [(now - timedelta(minutes=i), "a1", f"s{i}", "") for i in range(10)]
            _write_test_csv(path, rows)

            reader = StatusHistoryFile(path)
            result1 = reader.read(hours=1.0)
            assert len(result1) == 10

            # Verify cache state is populated
            assert reader._read_offset > 0
            cached_mtime = reader._cached_mtime

            # Second read should hit cache
            result2 = reader.read(hours=1.0)
            assert len(result2) == 10
            # mtime unchanged confirms cache was used (not re-read)
            assert reader._cached_mtime == cached_mtime

    def test_incremental_append(self):
        """Appending rows then re-reading should pick up new rows only."""
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.csv"
            now = datetime.now()
            rows = [
                (now - timedelta(minutes=10), "a1", "initial", ""),
            ]
            _write_test_csv(path, rows)

            reader = StatusHistoryFile(path)
            result1 = reader.read(hours=1.0)
            assert len(result1) == 1
            old_offset = reader._read_offset

            # Append more rows via log_agent_status
            log_agent_status("a1", "appended1", "", path)
            log_agent_status("a1", "appended2", "", path)

            result2 = reader.read(hours=1.0)
            assert len(result2) == 3
            assert result2[-1][2] == "appended2"
            # Offset should have advanced (incremental read)
            assert reader._read_offset > old_offset

    def test_file_rewrite_invalidates_cache(self):
        """If file shrinks (rewrite), cache is invalidated and full re-read occurs."""
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.csv"
            now = datetime.now()
            rows = [
                (now - timedelta(minutes=i), "a1", f"s{i}", "x" * 50)
                for i in range(20)
            ]
            _write_test_csv(path, rows)

            reader = StatusHistoryFile(path)
            result1 = reader.read(hours=1.0)
            assert len(result1) == 20
            old_size = reader._cached_size

            # Rewrite with fewer rows (simulates clear_old_history)
            small_rows = [
                (now - timedelta(minutes=1), "a1", "only_one", ""),
            ]
            _write_test_csv(path, small_rows)
            assert path.stat().st_size < old_size

            result2 = reader.read(hours=1.0)
            assert len(result2) == 1
            assert result2[0][2] == "only_one"

    def test_nonexistent_file(self):
        """Should return empty list for nonexistent file."""
        reader = StatusHistoryFile(Path("/tmp/does_not_exist_xyz.csv"))
        assert reader.read() == []

    def test_empty_file(self):
        """Should return empty list for empty file."""
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.csv"
            path.write_text("")

            reader = StatusHistoryFile(path)
            assert reader.read() == []

    def test_header_only_file(self):
        """Should return empty list for file with only header."""
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.csv"
            _write_test_csv(path, [])

            reader = StatusHistoryFile(path)
            assert reader.read() == []

    def test_malformed_rows(self):
        """Should skip malformed rows without crashing."""
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.csv"
            now = datetime.now()
            with open(path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['timestamp', 'agent', 'status', 'activity'])
                writer.writerow([now.isoformat(), 'a1', 'good', ''])
                writer.writerow(['not-a-date', 'a1', 'bad', ''])
                writer.writerow([now.isoformat(), 'a1', 'also_good', ''])
                writer.writerow([''])  # short row

            reader = StatusHistoryFile(path)
            result = reader.read(hours=1.0)
            assert len(result) == 2
            assert result[0][2] == "good"
            assert result[1][2] == "also_good"

    def test_hours_expansion(self):
        """Expanding hours window should trigger full re-read with wider range."""
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.csv"
            now = datetime.now()
            rows = [
                (now - timedelta(hours=20), "a1", "old", ""),
                (now - timedelta(hours=2), "a1", "mid", ""),
                (now - timedelta(minutes=10), "a1", "new", ""),
            ]
            _write_test_csv(path, rows)

            reader = StatusHistoryFile(path)
            result_3h = reader.read(hours=3.0)
            assert len(result_3h) == 2  # mid + new

            # Expand to 24h — should pick up the old entry
            result_24h = reader.read(hours=24.0)
            assert len(result_24h) == 3
            assert result_24h[0][2] == "old"

    def test_binary_seek_accuracy(self):
        """Binary seek should find correct cutoff in a large file."""
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.csv"
            now = datetime.now()
            # 1000 rows spanning 48 hours
            rows = []
            for i in range(1000):
                ts = now - timedelta(hours=48) + timedelta(minutes=i * 2.88)
                rows.append((ts, "a1", f"s{i}", "activity"))
            _write_test_csv(path, rows)

            reader = StatusHistoryFile(path)
            result = reader.read(hours=3.0)

            # Verify all returned entries are within the 3h window
            cutoff = now - timedelta(hours=3.0)
            for entry in result:
                assert entry[0] >= cutoff, f"Entry {entry[0]} is before cutoff {cutoff}"

            # Verify we got the expected count (~62 entries in last 3h of 48h span)
            # 3h / 48h * 1000 ≈ 62.5
            assert 55 <= len(result) <= 70, f"Expected ~62 entries, got {len(result)}"

            # Verify completeness: compare with naive parse
            naive = []
            with open(path, 'r', newline='') as f:
                csv_reader = csv.DictReader(f)
                for row in csv_reader:
                    ts = datetime.fromisoformat(row['timestamp'])
                    if ts >= cutoff:
                        naive.append(ts)
            assert len(result) == len(naive)

    def test_backward_compat_wrapper(self):
        """read_agent_status_history() should work with same signature."""
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.csv"
            log_agent_status("a1", "running", "work", path)
            log_agent_status("a2", "waiting", "", path)

            # Test all parameter combinations
            all_entries = read_agent_status_history(history_file=path)
            assert len(all_entries) == 2

            filtered = read_agent_status_history(
                hours=1.0, agent_name="a1", history_file=path
            )
            assert len(filtered) == 1
            assert filtered[0][1] == "a1"

            empty = read_agent_status_history(
                history_file=Path("/nonexistent.csv")
            )
            assert empty == []
