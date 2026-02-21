"""Tests for data_export module."""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, call
from datetime import datetime

from overcode.data_export import (
    export_to_parquet,
    _session_to_record,
    _build_sessions_table,
    _build_timeline_records,
    _build_timeline_table,
    _build_presence_records,
    _build_presence_table,
)
from overcode.session_manager import Session, SessionStats, SessionManager


@pytest.fixture
def temp_state_dir(tmp_path):
    """Create a temporary state directory."""
    state_dir = tmp_path / "sessions"
    state_dir.mkdir()
    return state_dir


@pytest.fixture
def session_manager(temp_state_dir):
    """Create a session manager with temp state directory."""
    return SessionManager(state_dir=temp_state_dir, skip_git_detection=True)


@pytest.fixture
def sample_session():
    """Create a sample session for testing."""
    stats = SessionStats(
        interaction_count=10,
        estimated_cost_usd=0.50,
        total_tokens=5000,
        input_tokens=3000,
        output_tokens=1500,
        cache_creation_tokens=300,
        cache_read_tokens=200,
        steers_count=2,
        current_state="running",
        green_time_seconds=3600.0,
        non_green_time_seconds=300.0,
    )
    return Session(
        id="test-id-123",
        name="test-agent",
        tmux_session="agents",
        tmux_window=1,
        command=["claude", "code"],
        start_directory="/test/dir",
        start_time="2024-01-01T10:00:00",
        repo_name="test-repo",
        branch="main",
        status="running",
        standing_instructions="Keep working",
        stats=stats,
    )


@pytest.fixture
def second_session():
    """Create a second sample session for testing."""
    stats = SessionStats(
        interaction_count=5,
        estimated_cost_usd=0.25,
        total_tokens=2000,
        input_tokens=1500,
        output_tokens=500,
        cache_creation_tokens=100,
        cache_read_tokens=50,
        steers_count=1,
        current_state="waiting_user",
        green_time_seconds=1800.0,
        non_green_time_seconds=600.0,
    )
    return Session(
        id="test-id-456",
        name="test-agent-2",
        tmux_session="agents",
        tmux_window=2,
        command=["claude", "code"],
        start_directory="/test/dir2",
        start_time="2024-01-01T11:00:00",
        repo_name="other-repo",
        branch="dev",
        status="waiting_user",
        standing_instructions="",
        stats=stats,
    )


class TestSessionToRecord:
    """Tests for _session_to_record function."""

    def test_converts_session_to_dict(self, sample_session):
        record = _session_to_record(sample_session, is_archived=False)

        assert record["id"] == "test-id-123"
        assert record["name"] == "test-agent"
        assert record["tmux_session"] == "agents"
        assert record["tmux_window"] == 1
        assert record["is_archived"] is False

    def test_includes_stats_fields(self, sample_session):
        record = _session_to_record(sample_session, is_archived=False)

        assert record["interaction_count"] == 10
        assert record["estimated_cost_usd"] == 0.50
        assert record["total_tokens"] == 5000
        assert record["input_tokens"] == 3000
        assert record["output_tokens"] == 1500
        assert record["green_time_seconds"] == 3600.0
        assert record["non_green_time_seconds"] == 300.0

    def test_archived_flag(self, sample_session):
        active_record = _session_to_record(sample_session, is_archived=False)
        archived_record = _session_to_record(sample_session, is_archived=True)

        assert active_record["is_archived"] is False
        assert archived_record["is_archived"] is True

    def test_end_time_defaults_to_none(self, sample_session):
        record = _session_to_record(sample_session, is_archived=False)
        assert record["end_time"] is None

    def test_includes_all_expected_fields(self, sample_session):
        """Verify all expected fields are present in the record."""
        record = _session_to_record(sample_session, is_archived=False)

        expected_fields = [
            "id", "name", "tmux_session", "tmux_window", "start_directory",
            "start_time", "end_time", "repo_name", "branch", "status",
            "is_archived", "permissiveness_mode", "standing_instructions",
            "standing_instructions_preset",
            "interaction_count", "estimated_cost_usd", "total_tokens",
            "input_tokens", "output_tokens", "cache_creation_tokens",
            "cache_read_tokens", "steers_count", "last_activity",
            "current_task", "current_state", "state_since",
            "green_time_seconds", "non_green_time_seconds", "last_stats_update",
        ]
        for field in expected_fields:
            assert field in record, f"Missing expected field: {field}"

    def test_git_context_fields(self, sample_session):
        record = _session_to_record(sample_session, is_archived=False)
        assert record["repo_name"] == "test-repo"
        assert record["branch"] == "main"

    def test_standing_instructions_fields(self, sample_session):
        record = _session_to_record(sample_session, is_archived=False)
        assert record["standing_instructions"] == "Keep working"
        assert record["standing_instructions_preset"] is None

    def test_cache_token_fields(self, sample_session):
        record = _session_to_record(sample_session, is_archived=False)
        assert record["cache_creation_tokens"] == 300
        assert record["cache_read_tokens"] == 200

    def test_state_tracking_fields(self, sample_session):
        record = _session_to_record(sample_session, is_archived=False)
        assert record["current_state"] == "running"
        assert record["state_since"] is None


class TestBuildSessionsTable:
    """Tests for _build_sessions_table function — mocks pyarrow."""

    def test_empty_records_returns_empty_table(self):
        mock_pa = MagicMock()
        mock_schema = MagicMock()
        mock_pa.schema.return_value = mock_schema
        mock_schema.__iter__ = lambda self: iter([
            MagicMock(name="id", type="string"),
        ])
        mock_table = MagicMock()
        mock_table.num_rows = 0
        mock_pa.table.return_value = mock_table

        with patch.dict("sys.modules", {"pyarrow": mock_pa}):
            table = _build_sessions_table([])

        assert table.num_rows == 0

    def test_builds_table_from_records(self, sample_session):
        mock_pa = MagicMock()
        mock_table = MagicMock()
        mock_table.num_rows = 1
        mock_table.column_names = ["id", "name", "interaction_count"]
        mock_pa.table.return_value = mock_table

        with patch.dict("sys.modules", {"pyarrow": mock_pa}):
            records = [_session_to_record(sample_session, is_archived=False)]
            table = _build_sessions_table(records)

        assert table.num_rows == 1
        mock_pa.table.assert_called_once()

    def test_multiple_records(self, sample_session, second_session):
        mock_pa = MagicMock()
        mock_table = MagicMock()
        mock_table.num_rows = 2
        mock_pa.table.return_value = mock_table

        with patch.dict("sys.modules", {"pyarrow": mock_pa}):
            records = [
                _session_to_record(sample_session, is_archived=False),
                _session_to_record(second_session, is_archived=True),
            ]
            table = _build_sessions_table(records)

        assert table.num_rows == 2

    def test_passes_correct_arrays_to_pyarrow(self, sample_session):
        """Verify that the arrays dict passed to pa.table has all keys from the record."""
        mock_pa = MagicMock()
        captured_args = {}

        def capture_table(arrays, **kwargs):
            captured_args.update(arrays)
            return MagicMock(num_rows=1)

        mock_pa.table.side_effect = capture_table

        with patch.dict("sys.modules", {"pyarrow": mock_pa}):
            records = [_session_to_record(sample_session, is_archived=False)]
            _build_sessions_table(records)

        assert "id" in captured_args
        assert "name" in captured_args
        assert captured_args["id"] == ["test-id-123"]
        assert captured_args["name"] == ["test-agent"]


class TestBuildTimelineRecords:
    """Tests for _build_timeline_records function."""

    def test_returns_list(self):
        with patch("overcode.data_export.read_agent_status_history") as mock_read:
            mock_read.return_value = []
            records = _build_timeline_records()
            assert isinstance(records, list)

    def test_converts_history_to_records(self):
        with patch("overcode.data_export.read_agent_status_history") as mock_read:
            # Returns List[Tuple[datetime, agent, status, activity]]
            mock_read.return_value = [
                (datetime(2024, 1, 1, 12, 0), "agent1", "running", ""),
                (datetime(2024, 1, 1, 12, 5), "agent2", "waiting_user", ""),
            ]
            records = _build_timeline_records()

            assert len(records) == 2
            assert records[0]["agent"] == "agent1"
            assert records[0]["status"] == "running"

    def test_calls_with_24_hours(self):
        """Should request 24 hours of history."""
        with patch("overcode.data_export.read_agent_status_history") as mock_read:
            mock_read.return_value = []
            _build_timeline_records()
            mock_read.assert_called_once_with(hours=24.0)

    def test_datetime_timestamps_converted_to_isoformat(self):
        """datetime objects should be converted to ISO format strings."""
        with patch("overcode.data_export.read_agent_status_history") as mock_read:
            ts = datetime(2024, 1, 15, 14, 30, 45)
            mock_read.return_value = [
                (ts, "agent1", "running", "task"),
            ]
            records = _build_timeline_records()

            assert records[0]["timestamp"] == ts.isoformat()

    def test_handles_string_timestamps(self):
        """Should handle timestamps that are already strings."""
        with patch("overcode.data_export.read_agent_status_history") as mock_read:
            mock_read.return_value = [
                ("2024-01-01T12:00:00", "agent1", "running", "activity1"),
            ]
            records = _build_timeline_records()

            assert len(records) == 1
            assert records[0]["timestamp"] == "2024-01-01T12:00:00"

    def test_empty_history_returns_empty_list(self):
        with patch("overcode.data_export.read_agent_status_history") as mock_read:
            mock_read.return_value = []
            records = _build_timeline_records()
            assert records == []

    def test_activity_field_not_included_in_record(self):
        """Activity is the 4th tuple element but should not be in the record."""
        with patch("overcode.data_export.read_agent_status_history") as mock_read:
            mock_read.return_value = [
                (datetime(2024, 1, 1, 12, 0), "agent1", "running", "some activity"),
            ]
            records = _build_timeline_records()

            assert "activity" not in records[0]
            # Only timestamp, agent, status
            assert set(records[0].keys()) == {"timestamp", "agent", "status"}


class TestBuildTimelineTable:
    """Tests for _build_timeline_table function — mocks pyarrow."""

    def test_empty_records(self):
        mock_pa = MagicMock()
        mock_schema = MagicMock()
        mock_pa.schema.return_value = mock_schema
        mock_schema.__iter__ = lambda self: iter([])
        mock_table = MagicMock()
        mock_table.num_rows = 0
        mock_pa.table.return_value = mock_table

        with patch.dict("sys.modules", {"pyarrow": mock_pa}):
            table = _build_timeline_table([])

        assert table.num_rows == 0

    def test_builds_table(self):
        mock_pa = MagicMock()
        mock_table = MagicMock()
        mock_table.num_rows = 1
        mock_table.column_names = ["timestamp", "agent", "status"]
        mock_pa.table.return_value = mock_table

        with patch.dict("sys.modules", {"pyarrow": mock_pa}):
            records = [
                {"timestamp": "2024-01-01T12:00:00", "agent": "agent1", "status": "running"},
            ]
            table = _build_timeline_table(records)

        assert table.num_rows == 1


class TestBuildPresenceRecords:
    """Tests for _build_presence_records function."""

    def test_returns_list(self):
        with patch("overcode.data_export.read_presence_history") as mock_read:
            mock_read.return_value = []
            records = _build_presence_records()
            assert isinstance(records, list)

    def test_converts_history_to_records(self):
        with patch("overcode.data_export.read_presence_history") as mock_read:
            mock_read.return_value = [
                (datetime(2024, 1, 1, 12, 0), 3),  # active
                (datetime(2024, 1, 1, 12, 5), 1),  # locked
            ]
            records = _build_presence_records()

            assert len(records) == 2
            assert records[0]["state"] == 3
            assert records[0]["state_name"] == "active"
            assert records[1]["state"] == 1
            assert records[1]["state_name"] == "locked"

    def test_calls_with_24_hours(self):
        """Should request 24 hours of history."""
        with patch("overcode.data_export.read_presence_history") as mock_read:
            mock_read.return_value = []
            _build_presence_records()
            mock_read.assert_called_once_with(hours=24.0)

    def test_datetime_timestamps_converted_to_isoformat(self):
        with patch("overcode.data_export.read_presence_history") as mock_read:
            ts = datetime(2024, 6, 15, 8, 0, 0)
            mock_read.return_value = [(ts, 3)]
            records = _build_presence_records()

            assert records[0]["timestamp"] == ts.isoformat()

    def test_string_timestamp_converted_via_str(self):
        """Non-datetime timestamps should use str()."""
        with patch("overcode.data_export.read_presence_history") as mock_read:
            mock_read.return_value = [("2024-01-01T12:00:00", 3)]
            records = _build_presence_records()

            assert records[0]["timestamp"] == "2024-01-01T12:00:00"


class TestBuildPresenceTable:
    """Tests for _build_presence_table function — mocks pyarrow."""

    def test_empty_records(self):
        mock_pa = MagicMock()
        mock_schema = MagicMock()
        mock_pa.schema.return_value = mock_schema
        mock_schema.__iter__ = lambda self: iter([])
        mock_table = MagicMock()
        mock_table.num_rows = 0
        mock_pa.table.return_value = mock_table

        with patch.dict("sys.modules", {"pyarrow": mock_pa}):
            table = _build_presence_table([])

        assert table.num_rows == 0

    def test_builds_table(self):
        mock_pa = MagicMock()
        mock_table = MagicMock()
        mock_table.num_rows = 1
        mock_table.column_names = ["timestamp", "state", "state_name"]
        mock_pa.table.return_value = mock_table

        with patch.dict("sys.modules", {"pyarrow": mock_pa}):
            records = [
                {"timestamp": "2024-01-01T12:00:00", "state": 3, "state_name": "active"},
            ]
            table = _build_presence_table(records)

        assert table.num_rows == 1


class TestBuildPresenceRecordsStateMapping:
    """Test presence state name mapping."""

    def test_unknown_state_maps_to_unknown(self):
        """Unknown state values should map to 'unknown'."""
        with patch("overcode.data_export.read_presence_history") as mock_read:
            mock_read.return_value = [
                (datetime(2024, 1, 1, 12, 0), 99),  # Unknown state
            ]
            records = _build_presence_records()

            assert len(records) == 1
            assert records[0]["state_name"] == "unknown"

    def test_inactive_state_maps_correctly(self):
        """State 2 should map to 'inactive'."""
        with patch("overcode.data_export.read_presence_history") as mock_read:
            mock_read.return_value = [
                (datetime(2024, 1, 1, 12, 0), 2),
            ]
            records = _build_presence_records()

            assert len(records) == 1
            assert records[0]["state_name"] == "inactive"

    def test_locked_state_maps_correctly(self):
        """State 1 should map to 'locked'."""
        with patch("overcode.data_export.read_presence_history") as mock_read:
            mock_read.return_value = [
                (datetime(2024, 1, 1, 12, 0), 1),
            ]
            records = _build_presence_records()
            assert records[0]["state_name"] == "locked"

    def test_active_state_maps_correctly(self):
        """State 3 should map to 'active'."""
        with patch("overcode.data_export.read_presence_history") as mock_read:
            mock_read.return_value = [
                (datetime(2024, 1, 1, 12, 0), 3),
            ]
            records = _build_presence_records()
            assert records[0]["state_name"] == "active"

    def test_zero_state_maps_to_unknown(self):
        """State 0 is not in the mapping, should be 'unknown'."""
        with patch("overcode.data_export.read_presence_history") as mock_read:
            mock_read.return_value = [
                (datetime(2024, 1, 1, 12, 0), 0),
            ]
            records = _build_presence_records()
            assert records[0]["state_name"] == "unknown"


class TestExportToParquet:
    """Tests for export_to_parquet function.

    Note: pyarrow is not installed in the test environment. When the function
    does `import pyarrow as pa` and `import pyarrow.parquet as pq`, Python
    resolves `pq` as `pa.parquet` (attribute access on the parent mock).
    So we use `mock_pa.parquet.write_table` to verify write calls.
    """

    @staticmethod
    def _pyarrow_modules(mock_pa):
        """Return sys.modules dict for mocking pyarrow imports."""
        return {"pyarrow": mock_pa, "pyarrow.parquet": mock_pa.parquet}

    def test_raises_on_missing_pyarrow(self):
        """Test that ImportError is raised when pyarrow is not installed."""
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "pyarrow" or name == "pyarrow.parquet":
                raise ImportError("No module named 'pyarrow'")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            with pytest.raises(ImportError, match="pyarrow is required"):
                export_to_parquet("/tmp/test.parquet")

    def test_export_empty_state(self, tmp_path):
        """Test export with no sessions and no history data."""
        mock_pa = MagicMock()
        mock_pa.schema.return_value = MagicMock(
            __iter__=lambda self: iter([MagicMock(name="id", type="string")])
        )

        with patch.dict("sys.modules", self._pyarrow_modules(mock_pa)):
            with patch("overcode.data_export.SessionManager") as mock_sm:
                mock_instance = MagicMock()
                mock_instance.list_sessions.return_value = []
                mock_instance.list_archived_sessions.return_value = []
                mock_sm.return_value = mock_instance

                with patch("overcode.data_export.read_agent_status_history") as mock_timeline:
                    mock_timeline.return_value = []
                    with patch("overcode.data_export.read_presence_history") as mock_presence:
                        mock_presence.return_value = []

                        result = export_to_parquet(str(tmp_path / "export.parquet"))

        assert result["sessions_count"] == 0
        assert result["archived_count"] == 0
        assert result["timeline_rows"] == 0
        assert result["presence_rows"] == 0
        # Should have written the main sessions table
        mock_pa.parquet.write_table.assert_called_once()

    def test_export_with_sessions(self, tmp_path, sample_session):
        """Test export with active sessions."""
        mock_pa = MagicMock()

        with patch.dict("sys.modules", self._pyarrow_modules(mock_pa)):
            with patch("overcode.data_export.SessionManager") as mock_sm:
                mock_instance = MagicMock()
                mock_instance.list_sessions.return_value = [sample_session]
                mock_instance.list_archived_sessions.return_value = []
                mock_sm.return_value = mock_instance

                with patch("overcode.data_export.read_agent_status_history") as mock_timeline:
                    mock_timeline.return_value = []
                    with patch("overcode.data_export.read_presence_history") as mock_presence:
                        mock_presence.return_value = []

                        result = export_to_parquet(str(tmp_path / "export.parquet"))

        assert result["sessions_count"] == 1

    def test_export_includes_archived(self, tmp_path, sample_session):
        """Test that archived sessions are included when include_archived=True."""
        mock_pa = MagicMock()

        # Create an archived session (mock with _end_time attribute)
        archived_session = sample_session
        archived_session._end_time = "2024-01-02T10:00:00"

        with patch.dict("sys.modules", self._pyarrow_modules(mock_pa)):
            with patch("overcode.data_export.SessionManager") as mock_sm:
                mock_instance = MagicMock()
                mock_instance.list_sessions.return_value = []
                mock_instance.list_archived_sessions.return_value = [archived_session]
                mock_sm.return_value = mock_instance

                with patch("overcode.data_export.read_agent_status_history") as mock_timeline:
                    mock_timeline.return_value = []
                    with patch("overcode.data_export.read_presence_history") as mock_presence:
                        mock_presence.return_value = []

                        result = export_to_parquet(
                            str(tmp_path / "export.parquet"),
                            include_archived=True,
                        )

        assert result["archived_count"] == 1

    def test_export_without_archived(self, tmp_path, sample_session):
        """Test export without archived sessions."""
        mock_pa = MagicMock()

        with patch.dict("sys.modules", self._pyarrow_modules(mock_pa)):
            with patch("overcode.data_export.SessionManager") as mock_sm:
                mock_instance = MagicMock()
                mock_instance.list_sessions.return_value = [sample_session]
                # Even if archived exist, they should not be fetched
                mock_instance.list_archived_sessions.return_value = [sample_session]
                mock_sm.return_value = mock_instance

                with patch("overcode.data_export.read_agent_status_history") as mock_timeline:
                    mock_timeline.return_value = []
                    with patch("overcode.data_export.read_presence_history") as mock_presence:
                        mock_presence.return_value = []

                        result = export_to_parquet(
                            str(tmp_path / "export.parquet"),
                            include_archived=False,
                        )

        assert result["sessions_count"] == 1
        assert result["archived_count"] == 0
        # list_archived_sessions should NOT have been called
        mock_instance.list_archived_sessions.assert_not_called()

    def test_export_timeline_creates_separate_file(self, tmp_path, sample_session):
        """Test that timeline data creates a separate parquet file."""
        mock_pa = MagicMock()

        with patch.dict("sys.modules", self._pyarrow_modules(mock_pa)):
            with patch("overcode.data_export.SessionManager") as mock_sm:
                mock_instance = MagicMock()
                mock_instance.list_sessions.return_value = [sample_session]
                mock_instance.list_archived_sessions.return_value = []
                mock_sm.return_value = mock_instance

                with patch("overcode.data_export.read_agent_status_history") as mock_timeline:
                    mock_timeline.return_value = [
                        (datetime(2024, 1, 1, 12, 0), "test-agent", "running", "")
                    ]
                    with patch("overcode.data_export.read_presence_history") as mock_presence:
                        mock_presence.return_value = []

                        result = export_to_parquet(
                            str(tmp_path / "export.parquet"),
                            include_timeline=True,
                        )

        assert result["timeline_rows"] == 1
        # Should have written 2 tables: sessions + timeline
        assert mock_pa.parquet.write_table.call_count == 2

    def test_export_presence_creates_separate_file(self, tmp_path, sample_session):
        """Test that presence data creates a separate parquet file."""
        mock_pa = MagicMock()

        with patch.dict("sys.modules", self._pyarrow_modules(mock_pa)):
            with patch("overcode.data_export.SessionManager") as mock_sm:
                mock_instance = MagicMock()
                mock_instance.list_sessions.return_value = [sample_session]
                mock_instance.list_archived_sessions.return_value = []
                mock_sm.return_value = mock_instance

                with patch("overcode.data_export.read_agent_status_history") as mock_timeline:
                    mock_timeline.return_value = []
                    with patch("overcode.data_export.read_presence_history") as mock_presence:
                        mock_presence.return_value = [
                            (datetime(2024, 1, 1, 12, 0), 3),
                        ]

                        result = export_to_parquet(
                            str(tmp_path / "export.parquet"),
                            include_presence=True,
                        )

        assert result["presence_rows"] == 1
        # Should have written 2 tables: sessions + presence
        assert mock_pa.parquet.write_table.call_count == 2

    def test_export_without_timeline(self, tmp_path, sample_session):
        """Test export with timeline disabled."""
        mock_pa = MagicMock()

        with patch.dict("sys.modules", self._pyarrow_modules(mock_pa)):
            with patch("overcode.data_export.SessionManager") as mock_sm:
                mock_instance = MagicMock()
                mock_instance.list_sessions.return_value = [sample_session]
                mock_instance.list_archived_sessions.return_value = []
                mock_sm.return_value = mock_instance

                with patch("overcode.data_export.read_agent_status_history") as mock_timeline:
                    mock_timeline.return_value = []
                    with patch("overcode.data_export.read_presence_history") as mock_presence:
                        mock_presence.return_value = []

                        result = export_to_parquet(
                            str(tmp_path / "export.parquet"),
                            include_timeline=False,
                        )

        assert result["timeline_rows"] == 0
        # Only sessions table should be written
        mock_pa.parquet.write_table.assert_called_once()

    def test_export_without_presence(self, tmp_path, sample_session):
        """Test export with presence disabled."""
        mock_pa = MagicMock()

        with patch.dict("sys.modules", self._pyarrow_modules(mock_pa)):
            with patch("overcode.data_export.SessionManager") as mock_sm:
                mock_instance = MagicMock()
                mock_instance.list_sessions.return_value = [sample_session]
                mock_instance.list_archived_sessions.return_value = []
                mock_sm.return_value = mock_instance

                with patch("overcode.data_export.read_agent_status_history") as mock_timeline:
                    mock_timeline.return_value = []
                    with patch("overcode.data_export.read_presence_history") as mock_presence:
                        mock_presence.return_value = []

                        result = export_to_parquet(
                            str(tmp_path / "export.parquet"),
                            include_presence=False,
                        )

        assert result["presence_rows"] == 0
        # Only sessions table should be written
        mock_pa.parquet.write_table.assert_called_once()

    def test_adds_parquet_extension(self, tmp_path):
        """Test that .parquet extension is added when missing."""
        mock_pa = MagicMock()
        mock_pa.schema.return_value = MagicMock(
            __iter__=lambda self: iter([MagicMock(name="id", type="string")])
        )

        with patch.dict("sys.modules", self._pyarrow_modules(mock_pa)):
            with patch("overcode.data_export.SessionManager") as mock_sm:
                mock_instance = MagicMock()
                mock_instance.list_sessions.return_value = []
                mock_instance.list_archived_sessions.return_value = []
                mock_sm.return_value = mock_instance

                with patch("overcode.data_export.read_agent_status_history") as mock_timeline:
                    mock_timeline.return_value = []
                    with patch("overcode.data_export.read_presence_history") as mock_presence:
                        mock_presence.return_value = []

                        export_to_parquet(str(tmp_path / "export"))

        # Verify write_table was called with a path ending in .parquet
        write_call = mock_pa.parquet.write_table.call_args
        written_path = write_call[0][1]
        assert str(written_path).endswith(".parquet")

    def test_export_all_data_creates_three_files(self, tmp_path, sample_session):
        """Test export with all data types creates three separate files."""
        mock_pa = MagicMock()

        with patch.dict("sys.modules", self._pyarrow_modules(mock_pa)):
            with patch("overcode.data_export.SessionManager") as mock_sm:
                mock_instance = MagicMock()
                mock_instance.list_sessions.return_value = [sample_session]
                mock_instance.list_archived_sessions.return_value = []
                mock_sm.return_value = mock_instance

                with patch("overcode.data_export.read_agent_status_history") as mock_timeline:
                    mock_timeline.return_value = [
                        (datetime(2024, 1, 1, 12, 0), "agent", "running", "")
                    ]
                    with patch("overcode.data_export.read_presence_history") as mock_presence:
                        mock_presence.return_value = [
                            (datetime(2024, 1, 1, 12, 0), 3),
                        ]

                        result = export_to_parquet(
                            str(tmp_path / "export.parquet"),
                            include_timeline=True,
                            include_presence=True,
                        )

        assert result["sessions_count"] == 1
        assert result["timeline_rows"] == 1
        assert result["presence_rows"] == 1
        # 3 tables: sessions, timeline, presence
        assert mock_pa.parquet.write_table.call_count == 3

    def test_timeline_file_path_has_correct_stem(self, tmp_path, sample_session):
        """Verify the timeline file path uses the correct stem format."""
        mock_pa = MagicMock()

        with patch.dict("sys.modules", self._pyarrow_modules(mock_pa)):
            with patch("overcode.data_export.SessionManager") as mock_sm:
                mock_instance = MagicMock()
                mock_instance.list_sessions.return_value = [sample_session]
                mock_instance.list_archived_sessions.return_value = []
                mock_sm.return_value = mock_instance

                with patch("overcode.data_export.read_agent_status_history") as mock_timeline:
                    mock_timeline.return_value = [
                        (datetime(2024, 1, 1, 12, 0), "agent", "running", "")
                    ]
                    with patch("overcode.data_export.read_presence_history") as mock_presence:
                        mock_presence.return_value = []

                        export_to_parquet(str(tmp_path / "mydata.parquet"))

        # Second write_table call should be for the timeline file
        timeline_write = mock_pa.parquet.write_table.call_args_list[1]
        timeline_path = timeline_write[0][1]
        assert "mydata_timeline" in str(timeline_path)
        assert str(timeline_path).endswith(".parquet")

    def test_presence_file_path_has_correct_stem(self, tmp_path, sample_session):
        """Verify the presence file path uses the correct stem format."""
        mock_pa = MagicMock()

        with patch.dict("sys.modules", self._pyarrow_modules(mock_pa)):
            with patch("overcode.data_export.SessionManager") as mock_sm:
                mock_instance = MagicMock()
                mock_instance.list_sessions.return_value = [sample_session]
                mock_instance.list_archived_sessions.return_value = []
                mock_sm.return_value = mock_instance

                with patch("overcode.data_export.read_agent_status_history") as mock_timeline:
                    mock_timeline.return_value = []
                    with patch("overcode.data_export.read_presence_history") as mock_presence:
                        mock_presence.return_value = [
                            (datetime(2024, 1, 1, 12, 0), 3),
                        ]

                        export_to_parquet(str(tmp_path / "mydata.parquet"))

        # Second write_table call should be for the presence file
        presence_write = mock_pa.parquet.write_table.call_args_list[1]
        presence_path = presence_write[0][1]
        assert "mydata_presence" in str(presence_path)
        assert str(presence_path).endswith(".parquet")

    def test_archived_session_gets_end_time(self, tmp_path, sample_session):
        """Archived sessions should have end_time set from _end_time attribute."""
        mock_pa = MagicMock()

        captured_arrays = {}

        def capture_table(arrays, **kwargs):
            captured_arrays.update(arrays)
            return MagicMock()

        mock_pa.table.side_effect = capture_table

        archived = sample_session
        archived._end_time = "2024-01-02T10:00:00"

        with patch.dict("sys.modules", self._pyarrow_modules(mock_pa)):
            with patch("overcode.data_export.SessionManager") as mock_sm:
                mock_instance = MagicMock()
                mock_instance.list_sessions.return_value = []
                mock_instance.list_archived_sessions.return_value = [archived]
                mock_sm.return_value = mock_instance

                with patch("overcode.data_export.read_agent_status_history") as mock_timeline:
                    mock_timeline.return_value = []
                    with patch("overcode.data_export.read_presence_history") as mock_presence:
                        mock_presence.return_value = []

                        export_to_parquet(
                            str(tmp_path / "export.parquet"),
                            include_archived=True,
                        )

        # The archived record should have end_time set
        assert captured_arrays["end_time"] == ["2024-01-02T10:00:00"]

    def test_empty_timeline_does_not_create_file(self, tmp_path, sample_session):
        """Empty timeline records should not create a timeline file."""
        mock_pa = MagicMock()

        with patch.dict("sys.modules", self._pyarrow_modules(mock_pa)):
            with patch("overcode.data_export.SessionManager") as mock_sm:
                mock_instance = MagicMock()
                mock_instance.list_sessions.return_value = [sample_session]
                mock_instance.list_archived_sessions.return_value = []
                mock_sm.return_value = mock_instance

                with patch("overcode.data_export.read_agent_status_history") as mock_timeline:
                    mock_timeline.return_value = []  # Empty
                    with patch("overcode.data_export.read_presence_history") as mock_presence:
                        mock_presence.return_value = []

                        result = export_to_parquet(
                            str(tmp_path / "export.parquet"),
                            include_timeline=True,
                        )

        assert result["timeline_rows"] == 0
        # Only sessions table written (no timeline file)
        mock_pa.parquet.write_table.assert_called_once()

    def test_empty_presence_does_not_create_file(self, tmp_path, sample_session):
        """Empty presence records should not create a presence file."""
        mock_pa = MagicMock()

        with patch.dict("sys.modules", self._pyarrow_modules(mock_pa)):
            with patch("overcode.data_export.SessionManager") as mock_sm:
                mock_instance = MagicMock()
                mock_instance.list_sessions.return_value = [sample_session]
                mock_instance.list_archived_sessions.return_value = []
                mock_sm.return_value = mock_instance

                with patch("overcode.data_export.read_agent_status_history") as mock_timeline:
                    mock_timeline.return_value = []
                    with patch("overcode.data_export.read_presence_history") as mock_presence:
                        mock_presence.return_value = []  # Empty

                        result = export_to_parquet(
                            str(tmp_path / "export.parquet"),
                            include_presence=True,
                        )

        assert result["presence_rows"] == 0
        mock_pa.parquet.write_table.assert_called_once()
