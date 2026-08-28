"""
Tests for status history tracking.
"""

import csv
import gzip
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from overcode.status_history import (
    StatusHistoryFile,
    log_agent_status,
    read_agent_status_history,
    read_agent_status_history_range,
    get_agent_timeline,
    clear_old_history,
    rotate_status_history,
    apply_retention,
    rotate_and_retain,
    disk_usage_findings,
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


class TestRotateStatusHistory:
    """Tests for rotate_status_history (#465, #468)."""

    def test_no_rotation_below_thresholds(self, tmp_path):
        """A small, recent file should not be rotated."""
        path = tmp_path / "history.csv"
        now = datetime.now()
        rows = [(now - timedelta(minutes=i), "a1", f"s{i}", "") for i in range(5)]
        _write_test_csv(path, rows)

        result = rotate_status_history(path, rotate_mb=50, max_age_days=7, now=now)

        assert result is None
        assert path.exists()
        assert len(list(tmp_path.glob("history.*.csv.gz"))) == 0

    def test_rotation_triggers_on_size(self, tmp_path):
        """Exceeding rotate_mb should trigger rotation."""
        path = tmp_path / "history.csv"
        now = datetime.now()
        # Old rows (well outside keep_hours) + padding to exceed a tiny threshold.
        rows = [
            (now - timedelta(hours=40), "a1", f"s{i}", "x" * 200)
            for i in range(50)
        ]
        _write_test_csv(path, rows)
        assert path.stat().st_size > 1024  # sanity: bigger than our 1KB threshold

        result = rotate_status_history(
            path, rotate_mb=0.001, max_age_days=7, keep_hours=24, now=now
        )

        assert result is not None
        assert result.exists()
        assert result.name.endswith(".csv.gz")

    def test_rotation_triggers_on_age(self, tmp_path):
        """A file whose oldest row exceeds max_age_days should rotate even if small."""
        path = tmp_path / "history.csv"
        now = datetime.now()
        rows = [
            (now - timedelta(days=10), "a1", "old", ""),
            (now - timedelta(minutes=5), "a1", "recent", ""),
        ]
        _write_test_csv(path, rows)

        result = rotate_status_history(
            path, rotate_mb=50, max_age_days=7, keep_hours=24, now=now
        )

        assert result is not None

    def test_rotation_size_trigger_but_nothing_old_enough_is_noop(self, tmp_path):
        """Size trigger fires, but every row is within keep_hours: don't rotate."""
        path = tmp_path / "history.csv"
        now = datetime.now()
        rows = [
            (now - timedelta(minutes=i), "a1", f"s{i}", "x" * 200)
            for i in range(50)
        ]
        _write_test_csv(path, rows)

        result = rotate_status_history(
            path, rotate_mb=0.001, max_age_days=7, keep_hours=24, now=now
        )

        assert result is None
        # Active file must be untouched — still has all 50 rows.
        content = path.read_text()
        assert content.count("\n") >= 50

    def test_rotation_compression_round_trip(self, tmp_path):
        """Archived rows should be recoverable, unmodified, from the gzip archive."""
        path = tmp_path / "history.csv"
        now = datetime.now()
        rows = [
            (now - timedelta(hours=40), "a1", "old_status", "old activity"),
            (now - timedelta(minutes=1), "a1", "recent", ""),
        ]
        _write_test_csv(path, rows)

        archive = rotate_status_history(
            path, rotate_mb=0.0001, max_age_days=7, keep_hours=24, now=now
        )

        assert archive is not None
        with gzip.open(archive, 'rt', newline='') as f:
            reader = list(csv.reader(f))
        assert reader[0] == ['timestamp', 'agent', 'status', 'activity']
        assert reader[1][1] == "a1"
        assert reader[1][2] == "old_status"
        assert reader[1][3] == "old activity"
        assert len(reader) == 2  # header + the one old row

    def test_rotation_keeps_recent_rows_in_active_file(self, tmp_path):
        """Rows within keep_hours must remain in the active file after rotation."""
        path = tmp_path / "history.csv"
        now = datetime.now()
        rows = [
            (now - timedelta(hours=40), "a1", "old", ""),
            (now - timedelta(hours=2), "a1", "recent", ""),
            (now - timedelta(minutes=1), "a1", "newest", ""),
        ]
        _write_test_csv(path, rows)

        archive = rotate_status_history(
            path, rotate_mb=0.0001, max_age_days=7, keep_hours=24, now=now
        )

        assert archive is not None
        remaining = read_agent_status_history(hours=100, history_file=path)
        statuses = [r[2] for r in remaining]
        assert "old" not in statuses
        assert "recent" in statuses
        assert "newest" in statuses

    def test_rotation_active_file_has_header_after_rotation(self, tmp_path):
        """The freshly-written active file must still be a valid CSV with a header."""
        path = tmp_path / "history.csv"
        now = datetime.now()
        rows = [(now - timedelta(hours=40), "a1", f"s{i}", "") for i in range(20)]
        _write_test_csv(path, rows)

        rotate_status_history(path, rotate_mb=0.0001, max_age_days=7, keep_hours=24, now=now)

        header_line = path.read_text().splitlines()[0]
        assert header_line == "timestamp,agent,status,activity"

    def test_windowed_reader_unaffected_across_rotation(self, tmp_path):
        """A live StatusHistoryFile reader (3h/24h window) must see the same
        recent rows before and after a rotation happens underneath it."""
        path = tmp_path / "history.csv"
        now = datetime.now()
        rows = [
            (now - timedelta(hours=40), "a1", "old", ""),
            (now - timedelta(hours=1), "a1", "recent1", ""),
            (now - timedelta(minutes=10), "a1", "recent2", ""),
        ]
        _write_test_csv(path, rows)

        reader = StatusHistoryFile(path)
        before = reader.read(hours=24.0)
        assert [r[2] for r in before] == ["recent1", "recent2"]

        archive = rotate_status_history(
            path, rotate_mb=0.0001, max_age_days=7, keep_hours=24, now=now
        )
        assert archive is not None

        after = reader.read(hours=24.0)
        assert [r[2] for r in after] == ["recent1", "recent2"]

    def test_nonexistent_file_returns_none(self, tmp_path):
        result = rotate_status_history(tmp_path / "missing.csv")
        assert result is None


class TestApplyRetention:
    """Tests for apply_retention (#465, #468)."""

    def test_deletes_archives_older_than_max_age(self, tmp_path):
        path = tmp_path / "history.csv"
        now = datetime.now()
        old_archive = tmp_path / (
            "history." + (now - timedelta(days=100)).strftime("%Y%m%d-%H%M%S") + ".csv.gz"
        )
        with gzip.open(old_archive, 'wt') as f:
            f.write("timestamp,agent,status,activity\n")

        deleted = apply_retention(path, max_age_days=90, now=now)

        assert deleted == [old_archive]
        assert not old_archive.exists()

    def test_keeps_archives_within_max_age(self, tmp_path):
        path = tmp_path / "history.csv"
        now = datetime.now()
        recent_archive = tmp_path / (
            "history." + (now - timedelta(days=10)).strftime("%Y%m%d-%H%M%S") + ".csv.gz"
        )
        with gzip.open(recent_archive, 'wt') as f:
            f.write("timestamp,agent,status,activity\n")

        deleted = apply_retention(path, max_age_days=90, now=now)

        assert deleted == []
        assert recent_archive.exists()

    def test_falls_back_to_mtime_for_unparseable_name(self, tmp_path, monkeypatch):
        path = tmp_path / "history.csv"
        weird_archive = tmp_path / "history.not-a-timestamp.csv.gz"
        with gzip.open(weird_archive, 'wt') as f:
            f.write("timestamp,agent,status,activity\n")

        # Backdate mtime well past the retention window.
        import os
        old_time = (datetime.now() - timedelta(days=200)).timestamp()
        os.utime(weird_archive, (old_time, old_time))

        deleted = apply_retention(path, max_age_days=90)

        assert deleted == [weird_archive]


class TestRotateAndRetain:
    """Tests for the rotate_and_retain convenience wrapper."""

    def test_combines_rotation_and_retention(self, tmp_path):
        path = tmp_path / "history.csv"
        now = datetime.now()
        rows = [(now - timedelta(hours=40), "a1", f"s{i}", "") for i in range(20)]
        _write_test_csv(path, rows)

        stale_archive = tmp_path / (
            "history." + (now - timedelta(days=200)).strftime("%Y%m%d-%H%M%S") + ".csv.gz"
        )
        with gzip.open(stale_archive, 'wt') as f:
            f.write("timestamp,agent,status,activity\n")

        result = rotate_and_retain(
            path, rotate_mb=0.0001, max_age_days=7, retention_days=90,
            keep_hours=24, now=now,
        )

        assert result["archived"] is not None
        assert stale_archive in result["deleted"]

    def test_noop_below_thresholds(self, tmp_path):
        path = tmp_path / "history.csv"
        now = datetime.now()
        rows = [(now - timedelta(minutes=1), "a1", "s", "")]
        _write_test_csv(path, rows)

        result = rotate_and_retain(path, now=now)

        assert result["archived"] is None
        assert result["deleted"] == []


class TestReadAgentStatusHistoryRange:
    """Tests for the archive-aware deep-history reader (#465, #468)."""

    def test_reads_active_file_only_when_no_archives(self, tmp_path):
        path = tmp_path / "history.csv"
        now = datetime.now()
        rows = [
            (now - timedelta(hours=2), "a1", "s1", ""),
            (now - timedelta(minutes=1), "a1", "s2", ""),
        ]
        _write_test_csv(path, rows)

        result = read_agent_status_history_range(
            now - timedelta(hours=3), now, path
        )

        assert [r[2] for r in result] == ["s1", "s2"]

    def test_merges_archive_and_active_rows(self, tmp_path):
        path = tmp_path / "history.csv"
        now = datetime.now()

        # Rotate an old row into an archive, then log fresh rows to the active file.
        # max_age_days=1 (not size) is the trigger here — a single-row file
        # never gets big enough to trip the size threshold.
        rows = [(now - timedelta(hours=40), "a1", "archived_status", "")]
        _write_test_csv(path, rows)
        archive = rotate_status_history(
            path, rotate_mb=50, max_age_days=1, keep_hours=24, now=now
        )
        assert archive is not None
        log_agent_status("a1", "active_status", "", path)

        result = read_agent_status_history_range(
            now - timedelta(hours=48), datetime.now() + timedelta(seconds=5), path
        )

        statuses = [r[2] for r in result]
        assert "archived_status" in statuses
        assert "active_status" in statuses
        # Chronological order preserved across the merge.
        assert result[0][0] <= result[-1][0]

    def test_skips_archives_entirely_before_start(self, tmp_path):
        path = tmp_path / "history.csv"
        now = datetime.now()
        rows = [(now - timedelta(hours=40), "a1", "archived_status", "")]
        _write_test_csv(path, rows)
        archive = rotate_status_history(
            path, rotate_mb=50, max_age_days=1, keep_hours=24, now=now
        )
        assert archive is not None

        # Query a range entirely after the archive's rotation time.
        result = read_agent_status_history_range(
            now - timedelta(minutes=5), now, path
        )

        assert result == []

    def test_filters_by_agent_name(self, tmp_path):
        path = tmp_path / "history.csv"
        now = datetime.now()
        rows = [
            (now - timedelta(hours=40), "a1", "archived_a1", ""),
            (now - timedelta(hours=40), "a2", "archived_a2", ""),
        ]
        _write_test_csv(path, rows)
        rotate_status_history(path, rotate_mb=0.0001, max_age_days=7, keep_hours=24, now=now)

        result = read_agent_status_history_range(
            now - timedelta(hours=48), now, path, agent_name="a1"
        )

        assert [r[2] for r in result] == ["archived_a1"]


class TestDiskUsageFindings:
    """Tests for the doctor disk-usage finding (#465, #468)."""

    def test_no_findings_below_threshold(self, tmp_path, monkeypatch):
        from overcode import settings
        monkeypatch.setattr(settings, "get_state_dir", lambda: tmp_path)
        history_path = tmp_path / "test-session" / "agent_status_history.csv"
        history_path.parent.mkdir(parents=True)
        history_path.write_text("timestamp,agent,status,activity\n")

        findings = disk_usage_findings("test-session", threshold_mb=500)

        assert findings == []

    def test_finding_when_history_plus_archives_exceeds_threshold(self, tmp_path, monkeypatch):
        from overcode import settings
        monkeypatch.setattr(settings, "get_state_dir", lambda: tmp_path)
        session_dir = tmp_path / "test-session"
        session_dir.mkdir(parents=True)
        history_path = session_dir / "agent_status_history.csv"
        history_path.write_text("timestamp,agent,status,activity\n")
        archive_path = session_dir / "agent_status_history.20200101-000000.csv.gz"
        # Random (incompressible) bytes so the gzip stays >0.5MB on disk.
        import os
        with gzip.open(archive_path, 'wb') as f:
            f.write(os.urandom(600 * 1024))

        findings = disk_usage_findings("test-session", threshold_mb=0.5)

        assert len(findings) == 1
        assert "agent_status_history" in findings[0]
        assert "status_history_max_days" in findings[0]

    def test_finding_when_event_loop_timing_exceeds_threshold(self, tmp_path, monkeypatch):
        from overcode import settings
        monkeypatch.setattr(settings, "get_state_dir", lambda: tmp_path)
        session_dir = tmp_path / "test-session" / "diagnostics"
        session_dir.mkdir(parents=True)
        diag_path = session_dir / "event_loop_timing.csv"
        diag_path.write_bytes(b"x" * (600 * 1024))

        findings = disk_usage_findings("test-session", threshold_mb=0.5)

        assert len(findings) == 1
        assert "event_loop_timing.csv" in findings[0]
        assert "event_loop_timing_cap_mb" in findings[0]
