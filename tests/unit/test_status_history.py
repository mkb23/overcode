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


class TestRotateStatusHistoryStreaming:
    """#468 — rotation streams rows through, so a multi-GB legacy file never
    has to fit in memory, and leaves no temp files behind either way."""

    def _write(self, path, rows, header=True):
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            if header:
                w.writerow(["timestamp", "agent", "status", "activity", "session_id", "hostname"])
            w.writerows(rows)

    def test_row_counts_are_conserved_across_a_large_rotation(self, tmp_path):
        from overcode.status_history import rotate_status_history
        path = tmp_path / "agent_status_history.csv"
        now = datetime(2026, 9, 17, 12, 0, 0)
        old = [(now - timedelta(days=3, seconds=i)).isoformat() for i in range(20_000)]
        recent = [(now - timedelta(minutes=i)).isoformat() for i in range(500)]
        self._write(path, [[ts, "a", "running", "x", "s", "h"] for ts in old + recent])

        archive = rotate_status_history(path, rotate_mb=0.001, keep_hours=30, now=now)

        assert archive is not None
        with gzip.open(archive, "rt", newline="") as f:
            archived = list(csv.reader(f))
        with open(path, newline="") as f:
            kept = list(csv.reader(f))
        assert len(archived) - 1 == 20_000
        assert len(kept) - 1 == 500
        assert kept[0][0] == "timestamp" and archived[0][0] == "timestamp"
        assert not list(tmp_path.glob("*.tmp"))

    def test_legacy_headerless_file_keeps_its_first_row(self, tmp_path):
        from overcode.status_history import rotate_status_history
        path = tmp_path / "agent_status_history.csv"
        now = datetime(2026, 9, 17, 12, 0, 0)
        # Rows in time order, as every writer appends them (the oldest-row
        # short-circuit relies on that): the headerless first row is old
        # yet stays in the active file as-is, the next old row is archived.
        first = [(now - timedelta(days=9)).isoformat(), "a", "running", "x", "s", "h"]
        old = [[(now - timedelta(days=9, minutes=-1)).isoformat(), "a", "idle", "x", "s", "h"]]
        recent = [[(now - timedelta(minutes=1)).isoformat(), "a", "running", "x", "s", "h"]]
        self._write(path, [first] + old + recent, header=False)

        archive = rotate_status_history(path, rotate_mb=0.0, keep_hours=30, now=now)

        assert archive is not None
        with open(path, newline="") as f:
            kept = list(csv.reader(f))
        assert kept[0][0] == "timestamp"
        assert kept[1] == first
        assert kept[2] == recent[0]
        with gzip.open(archive, "rt", newline="") as f:
            archived = list(csv.reader(f))
        assert archived[1:] == old
        assert not list(tmp_path.glob("*.tmp"))

    def test_nothing_archivable_leaves_no_temp_files(self, tmp_path):
        from overcode.status_history import rotate_status_history
        path = tmp_path / "agent_status_history.csv"
        now = datetime(2026, 9, 17, 12, 0, 0)
        self._write(path, [[(now - timedelta(minutes=i)).isoformat(), "a", "running", "x", "s", "h"] for i in range(50)])

        assert rotate_status_history(path, rotate_mb=0.0, keep_hours=30, now=now) is None
        assert not list(tmp_path.glob("*.tmp"))
        assert not list(tmp_path.glob("*.csv.gz"))


# ── Change-only logging (audit R10) ─────────────────────────────────────


class _Clock:
    """``status_history.datetime`` replacement whose ``now()`` is scripted."""

    def __init__(self, monkeypatch, start):
        self.now = start
        clock = self

        class _Frozen(datetime):
            @classmethod
            def now(cls, tz=None):  # noqa: D401 - datetime API
                return clock.now

        import overcode.status_history as sh
        monkeypatch.setattr(sh, "datetime", _Frozen)


def _write_rows(path, rows):
    """rows: (ts, agent, status, activity, session_id, hostname) tuples, file order."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "agent", "status", "activity", "session_id", "hostname"])
        for row in rows:
            w.writerow([row[0].isoformat(), *row[1:]])


class TestStatusRowDue:
    """The daemon's rule for when an agent's history row is written."""

    def test_first_sight_is_due(self):
        from overcode.status_history import status_row_due
        now = datetime(2026, 9, 23, 12, 0, 0)
        assert status_row_due(None, None, "running", "x", now, 60)

    def test_same_pair_within_the_keepalive_is_not_due(self):
        from overcode.status_history import status_row_due
        now = datetime(2026, 9, 23, 12, 0, 0)
        last = now - timedelta(seconds=58)
        assert not status_row_due(("running", "x"), last, "running", "x", now, 60)

    def test_status_change_is_due(self):
        from overcode.status_history import status_row_due
        now = datetime(2026, 9, 23, 12, 0, 0)
        assert status_row_due(("running", "x"), now, "waiting_user", "x", now, 60)

    def test_activity_change_is_due(self):
        from overcode.status_history import status_row_due
        now = datetime(2026, 9, 23, 12, 0, 0)
        assert status_row_due(("running", "Read(a.py)"), now, "running", "Read(b.py)", now, 60)

    def test_activity_compared_as_the_writer_stores_it(self):
        """A change past the 100-char truncation is not a new row; empty is ''."""
        from overcode.status_history import status_row_due
        now = datetime(2026, 9, 23, 12, 0, 0)
        base = "x" * 100
        assert not status_row_due(("running", base), now, "running", base + "tail", now, 60)
        assert not status_row_due(("running", ""), now, "running", None, now, 60)

    def test_keepalive_elapsed_is_due(self):
        from overcode.status_history import status_row_due
        now = datetime(2026, 9, 23, 12, 0, 0)
        assert status_row_due(("running", "x"), now - timedelta(seconds=60), "running", "x", now, 60)
        assert status_row_due(("running", "x"), now - timedelta(seconds=90), "running", "x", now, 60)

    def test_default_keepalive_is_the_daemon_setting(self):
        from overcode.settings import DAEMON
        from overcode.status_history import STATUS_HISTORY_KEEPALIVE_SECONDS, status_row_due
        assert STATUS_HISTORY_KEEPALIVE_SECONDS == DAEMON.status_history_keepalive_seconds
        now = datetime(2026, 9, 23, 12, 0, 0)
        just_under = now - timedelta(seconds=STATUS_HISTORY_KEEPALIVE_SECONDS - 1)
        assert not status_row_due(("running", "x"), just_under, "running", "x", now)


class TestStatusHistoryCarry:
    """``read(carry=True)``: each agent's state at the window's left edge."""

    def test_carry_is_the_last_row_before_the_cutoff_timestamped_at_the_cutoff(self, tmp_path, monkeypatch):
        now = datetime(2026, 9, 23, 12, 0, 0)
        _Clock(monkeypatch, now)
        path = tmp_path / "history.csv"
        _write_rows(path, [
            (now - timedelta(minutes=90), "a3", "running", "", "s3", "h"),   # beyond the lookback
            (now - timedelta(minutes=63), "a1", "running", "r", "s1", "h"),  # 3 min before cutoff
            (now - timedelta(minutes=62), "a2", "waiting_user", "w", "s2", "h"),
            (now - timedelta(minutes=61), "a1", "running", "r2", "s1", "h"),
            (now - timedelta(minutes=30), "a1", "waiting_user", "", "s1", "h"),
        ])
        cutoff = now - timedelta(hours=1)

        plain = StatusHistoryFile(path).read(hours=1.0)
        assert [(r[0], r[1], r[2]) for r in plain] == [(now - timedelta(minutes=30), "a1", "waiting_user")]

        result = StatusHistoryFile(path).read(hours=1.0, carry=True)
        assert [(r[0], r[1], r[2], r[3]) for r in result[:2]] == [
            (cutoff, "a1", "running", "r2"),  # its last row before the cutoff, not its first
            (cutoff, "a2", "waiting_user", "w"),
        ]
        assert result[2:] == plain
        assert not [r for r in result if r[1] == "a3"]

    def test_carry_keeps_the_other_columns(self, tmp_path, monkeypatch):
        now = datetime(2026, 9, 23, 12, 0, 0)
        _Clock(monkeypatch, now)
        path = tmp_path / "history.csv"
        _write_rows(path, [(now - timedelta(minutes=61), "a1", "running", "act", "sid", "host")])
        (row,) = StatusHistoryFile(path).read(hours=1.0, carry=True)
        assert row == (now - timedelta(hours=1), "a1", "running", "act", "sid", "host")

    def test_carry_honours_the_agent_filter(self, tmp_path, monkeypatch):
        now = datetime(2026, 9, 23, 12, 0, 0)
        _Clock(monkeypatch, now)
        path = tmp_path / "history.csv"
        _write_rows(path, [
            (now - timedelta(minutes=61), "a1", "running", "", "s1", "h"),
            (now - timedelta(minutes=61), "a2", "waiting_user", "", "s2", "h"),
            (now - timedelta(minutes=10), "a2", "running", "", "s2", "h"),
        ])
        result = StatusHistoryFile(path).read(hours=1.0, agent_name="a2", carry=True)
        assert [(r[1], r[2]) for r in result] == [("a2", "waiting_user"), ("a2", "running")]

    def test_rows_that_age_out_become_the_carry_on_an_incremental_read(self, tmp_path, monkeypatch):
        now = datetime(2026, 9, 23, 12, 0, 0)
        clock = _Clock(monkeypatch, now)
        path = tmp_path / "history.csv"
        _write_rows(path, [
            (now - timedelta(minutes=50), "a1", "running", "", "s1", "h"),
            (now - timedelta(minutes=20), "a1", "waiting_user", "", "s1", "h"),
        ])
        reader = StatusHistoryFile(path)
        assert len(reader.read(hours=1.0, carry=True)) == 2
        assert len(reader._cached_entries) == 2

        # 43 minutes later a row lands for another agent; the file grew, so
        # the read is incremental and the deque is trimmed from the left.
        clock.now = now + timedelta(minutes=43)
        log_agent_status("a2", "running", "", path, session_id="s2", hostname="h")
        result = reader.read(hours=1.0, carry=True)

        assert len(reader._cached_entries) == 1  # only a2's row is inside the window
        assert reader._read_offset > 0
        cutoff = clock.now - timedelta(hours=1)
        assert [(r[0], r[1], r[2]) for r in result][:1] == [(cutoff, "a1", "waiting_user")]
        assert result[1][1] == "a2"

    def test_carry_ages_out_of_the_lookback(self, tmp_path, monkeypatch):
        now = datetime(2026, 9, 23, 12, 0, 0)
        clock = _Clock(monkeypatch, now)
        path = tmp_path / "history.csv"
        _write_rows(path, [(now - timedelta(minutes=30), "a1", "running", "", "s1", "h")])
        reader = StatusHistoryFile(path)
        assert len(reader.read(hours=1.0, carry=True)) == 1
        clock.now = now + timedelta(minutes=33)  # the row is now 3 min before the cutoff
        assert [r[1] for r in reader.read(hours=1.0, carry=True)] == ["a1"]
        clock.now = now + timedelta(minutes=40)  # 10 min before it: not logged at the cutoff
        assert reader.read(hours=1.0, carry=True) == []
        assert reader._carry == {}

    def test_carry_for_a_window_narrower_than_the_cached_one(self, tmp_path, monkeypatch):
        now = datetime(2026, 9, 23, 12, 0, 0)
        _Clock(monkeypatch, now)
        path = tmp_path / "history.csv"
        _write_rows(path, [
            (now - timedelta(minutes=100), "a1", "running", "", "s1", "h"),
            (now - timedelta(minutes=33), "a1", "waiting_user", "", "s1", "h"),
            (now - timedelta(minutes=10), "a1", "running", "", "s1", "h"),
        ])
        reader = StatusHistoryFile(path)
        assert len(reader.read(hours=3.0)) == 3  # the cached window is 3 h
        result = reader.read(hours=0.5, carry=True)
        assert [(r[0], r[2]) for r in result] == [
            (now - timedelta(minutes=30), "waiting_user"),
            (now - timedelta(minutes=10), "running"),
        ]
        assert len(reader._cached_entries) == 3  # the wider cache is untouched

    def test_repeated_columns_are_interned(self, tmp_path):
        path = tmp_path / "history.csv"
        now = datetime.now()
        _write_rows(path, [
            (now - timedelta(minutes=i), "agent-with-a-long-name", "waiting_user", f"act{i}", "sid", "host")
            for i in range(20, 0, -1)
        ])
        rows = StatusHistoryFile(path).read(hours=1.0)
        assert len(rows) == 20
        for column in (1, 2, 4, 5):
            assert all(r[column] is rows[0][column] for r in rows)
        assert rows[0][3] != rows[1][3]


class TestRotationShortCircuit:
    """A size-triggered rotation with nothing beyond keep_hours reads one row."""

    def test_nothing_old_enough_does_not_stream_the_file(self, tmp_path, monkeypatch):
        import overcode.status_history as sh
        path = tmp_path / "agent_status_history.csv"
        now = datetime(2026, 9, 17, 12, 0, 0)
        _write_rows(path, [
            (now - timedelta(minutes=i), "a", "running", "x", "s", "h") for i in range(50, 0, -1)
        ])

        def no_stream(*args, **kwargs):
            raise AssertionError("rotation opened the archive stream")

        monkeypatch.setattr(sh.gzip, "open", no_stream)
        assert rotate_status_history(path, rotate_mb=0.0, keep_hours=30, now=now) is None
        assert not list(tmp_path.glob("*.tmp"))

    def test_rotation_still_runs_when_the_oldest_row_is_beyond_keep_hours(self, tmp_path):
        path = tmp_path / "agent_status_history.csv"
        now = datetime(2026, 9, 17, 12, 0, 0)
        _write_rows(path, [
            (now - timedelta(hours=31), "a", "running", "x", "s", "h"),
            (now - timedelta(minutes=1), "a", "running", "x", "s", "h"),
        ])
        archive = rotate_status_history(path, rotate_mb=0.0, keep_hours=30, now=now)
        assert archive is not None


class TestReadAgentStatusHistoryRangeCarry:
    def _archive(self, tmp_path, history_file, rotated_at, rows):
        from overcode.status_history import ARCHIVE_SUFFIX, ARCHIVE_TS_FORMAT
        name = f"{history_file.stem}.{rotated_at.strftime(ARCHIVE_TS_FORMAT)}{ARCHIVE_SUFFIX}"
        archive = tmp_path / name
        with gzip.open(archive, "wt", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "agent", "status", "activity", "session_id", "hostname"])
            for row in rows:
                w.writerow([row[0].isoformat(), *row[1:]])
        return archive

    def test_carry_at_start_from_active_rows(self, tmp_path):
        path = tmp_path / "history.csv"
        end = datetime.now()
        start = end - timedelta(hours=2)
        _write_rows(path, [
            (start - timedelta(minutes=10), "a1", "running", "", "s1", "h"),  # beyond the lookback
            (start - timedelta(minutes=2), "a1", "waiting_user", "w", "s1", "h"),
            (start - timedelta(minutes=1), "a2", "running", "r", "s2", "h"),
            (start + timedelta(minutes=30), "a1", "running", "", "s1", "h"),
        ])
        plain = read_agent_status_history_range(start, end, path)
        assert [(r[1], r[2]) for r in plain] == [("a1", "running")]

        result = read_agent_status_history_range(start, end, path, carry=True)
        assert [(r[0], r[1], r[2], r[3]) for r in result] == [
            (start, "a1", "waiting_user", "w"),
            (start, "a2", "running", "r"),
            (start + timedelta(minutes=30), "a1", "running", ""),
        ]

    def test_carry_at_start_from_an_archive_within_the_lookback(self, tmp_path):
        path = tmp_path / "history.csv"
        end = datetime.now()
        start = end - timedelta(hours=2)
        _write_rows(path, [(start + timedelta(minutes=5), "a1", "running", "", "s1", "h")])
        self._archive(tmp_path, path, start + timedelta(hours=1), [
            (start - timedelta(minutes=2), "a2", "waiting_user", "", "s2", "h"),
        ])
        result = read_agent_status_history_range(start, end, path, carry=True)
        assert [(r[0], r[1], r[2]) for r in result] == [
            (start, "a2", "waiting_user"),
            (start + timedelta(minutes=5), "a1", "running"),
        ]

    def test_archives_rotated_before_the_lookback_are_skipped(self, tmp_path):
        path = tmp_path / "history.csv"
        end = datetime.now()
        start = end - timedelta(hours=2)
        _write_rows(path, [(start + timedelta(minutes=5), "a1", "running", "", "s1", "h")])
        # Rotated before the lookback opens: every row in it is older still
        # (the file would not really hold this row; it proves the skip).
        self._archive(tmp_path, path, start - timedelta(minutes=10), [
            (start - timedelta(minutes=2), "a2", "waiting_user", "", "s2", "h"),
        ])
        result = read_agent_status_history_range(start, end, path, carry=True)
        assert [r[1] for r in result] == ["a1"]
