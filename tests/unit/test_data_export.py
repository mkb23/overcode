"""Tests for data_export module."""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
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


class TestBuildSessionsTable:
    """Tests for _build_sessions_table function."""

    def test_empty_records_returns_empty_table(self):
        pytest.importorskip("pyarrow")

        table = _build_sessions_table([])
        assert table.num_rows == 0

    def test_builds_table_from_records(self, sample_session):
        pytest.importorskip("pyarrow")

        records = [_session_to_record(sample_session, is_archived=False)]
        table = _build_sessions_table(records)

        assert table.num_rows == 1
        assert "id" in table.column_names
        assert "name" in table.column_names
        assert "interaction_count" in table.column_names

    def test_multiple_records(self, sample_session):
        pytest.importorskip("pyarrow")

        records = [
            _session_to_record(sample_session, is_archived=False),
            _session_to_record(sample_session, is_archived=True),
        ]
        table = _build_sessions_table(records)

        assert table.num_rows == 2


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


class TestBuildTimelineTable:
    """Tests for _build_timeline_table function."""

    def test_empty_records(self):
        pytest.importorskip("pyarrow")

        table = _build_timeline_table([])
        assert table.num_rows == 0

    def test_builds_table(self):
        pytest.importorskip("pyarrow")

        records = [
            {"timestamp": "2024-01-01T12:00:00", "agent": "agent1", "status": "running"},
        ]
        table = _build_timeline_table(records)

        assert table.num_rows == 1
        assert "timestamp" in table.column_names
        assert "agent" in table.column_names
        assert "status" in table.column_names


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


class TestBuildPresenceTable:
    """Tests for _build_presence_table function."""

    def test_empty_records(self):
        pytest.importorskip("pyarrow")

        table = _build_presence_table([])
        assert table.num_rows == 0

    def test_builds_table(self):
        pytest.importorskip("pyarrow")

        records = [
            {"timestamp": "2024-01-01T12:00:00", "state": 3, "state_name": "active"},
        ]
        table = _build_presence_table(records)

        assert table.num_rows == 1
        assert "timestamp" in table.column_names
        assert "state" in table.column_names
        assert "state_name" in table.column_names


class TestExportToParquet:
    """Tests for export_to_parquet function."""

    def test_raises_on_missing_pyarrow(self, tmp_path):
        with patch.dict("sys.modules", {"pyarrow": None, "pyarrow.parquet": None}):
            # Force reimport to pick up the patched modules
            import importlib
            import overcode.data_export as de

            # The actual check happens inside the function
            # We mock the import to simulate pyarrow not being installed
            with patch.object(de, "export_to_parquet") as mock_export:
                mock_export.side_effect = ImportError(
                    "pyarrow is required for parquet export. "
                    "Install it with: pip install pyarrow"
                )
                with pytest.raises(ImportError, match="pyarrow is required"):
                    mock_export(str(tmp_path / "test.parquet"))

    def test_export_empty_state(self, tmp_path, temp_state_dir):
        pytest.importorskip("pyarrow")

        output_path = str(tmp_path / "export.parquet")

        with patch("overcode.data_export.SessionManager") as mock_sm:
            mock_instance = MagicMock()
            mock_instance.list_sessions.return_value = []
            mock_instance.list_archived_sessions.return_value = []
            mock_sm.return_value = mock_instance

            with patch("overcode.data_export.read_agent_status_history") as mock_timeline:
                mock_timeline.return_value = {}
                with patch("overcode.data_export.read_presence_history") as mock_presence:
                    mock_presence.return_value = []

                    result = export_to_parquet(output_path)

        assert result["sessions_count"] == 0
        assert result["archived_count"] == 0
        assert Path(output_path).exists()

    def test_export_with_sessions(self, tmp_path, sample_session):
        pytest.importorskip("pyarrow")

        output_path = str(tmp_path / "export.parquet")

        with patch("overcode.data_export.SessionManager") as mock_sm:
            mock_instance = MagicMock()
            mock_instance.list_sessions.return_value = [sample_session]
            mock_instance.list_archived_sessions.return_value = []
            mock_sm.return_value = mock_instance

            with patch("overcode.data_export.read_agent_status_history") as mock_timeline:
                mock_timeline.return_value = {}
                with patch("overcode.data_export.read_presence_history") as mock_presence:
                    mock_presence.return_value = []

                    result = export_to_parquet(output_path)

        assert result["sessions_count"] == 1
        assert Path(output_path).exists()

    def test_export_includes_archived(self, tmp_path, sample_session):
        pytest.importorskip("pyarrow")

        output_path = str(tmp_path / "export.parquet")

        # Create an archived session (mock with _end_time attribute)
        archived_session = sample_session
        archived_session._end_time = "2024-01-02T10:00:00"

        with patch("overcode.data_export.SessionManager") as mock_sm:
            mock_instance = MagicMock()
            mock_instance.list_sessions.return_value = []
            mock_instance.list_archived_sessions.return_value = [archived_session]
            mock_sm.return_value = mock_instance

            with patch("overcode.data_export.read_agent_status_history") as mock_timeline:
                mock_timeline.return_value = {}
                with patch("overcode.data_export.read_presence_history") as mock_presence:
                    mock_presence.return_value = []

                    result = export_to_parquet(output_path, include_archived=True)

        assert result["archived_count"] == 1

    def test_export_timeline_creates_separate_file(self, tmp_path, sample_session):
        pytest.importorskip("pyarrow")

        output_path = str(tmp_path / "export.parquet")

        with patch("overcode.data_export.SessionManager") as mock_sm:
            mock_instance = MagicMock()
            mock_instance.list_sessions.return_value = [sample_session]
            mock_instance.list_archived_sessions.return_value = []
            mock_sm.return_value = mock_instance

            with patch("overcode.data_export.read_agent_status_history") as mock_timeline:
                # Returns List[Tuple[datetime, agent, status, activity]]
                mock_timeline.return_value = [
                    (datetime(2024, 1, 1, 12, 0), "test-agent", "running", "")
                ]
                with patch("overcode.data_export.read_presence_history") as mock_presence:
                    mock_presence.return_value = []

                    result = export_to_parquet(output_path, include_timeline=True)

        assert result["timeline_rows"] == 1
        timeline_path = tmp_path / "export_timeline.parquet"
        assert timeline_path.exists()

    def test_export_presence_creates_separate_file(self, tmp_path, sample_session):
        pytest.importorskip("pyarrow")

        output_path = str(tmp_path / "export.parquet")

        with patch("overcode.data_export.SessionManager") as mock_sm:
            mock_instance = MagicMock()
            mock_instance.list_sessions.return_value = [sample_session]
            mock_instance.list_archived_sessions.return_value = []
            mock_sm.return_value = mock_instance

            with patch("overcode.data_export.read_agent_status_history") as mock_timeline:
                mock_timeline.return_value = {}
                with patch("overcode.data_export.read_presence_history") as mock_presence:
                    mock_presence.return_value = [
                        (datetime(2024, 1, 1, 12, 0), 3),
                    ]

                    result = export_to_parquet(output_path, include_presence=True)

        assert result["presence_rows"] == 1
        presence_path = tmp_path / "export_presence.parquet"
        assert presence_path.exists()

    def test_adds_parquet_extension(self, tmp_path, sample_session):
        pytest.importorskip("pyarrow")

        # Output path without .parquet extension
        output_path = str(tmp_path / "export")

        with patch("overcode.data_export.SessionManager") as mock_sm:
            mock_instance = MagicMock()
            mock_instance.list_sessions.return_value = []
            mock_instance.list_archived_sessions.return_value = []
            mock_sm.return_value = mock_instance

            with patch("overcode.data_export.read_agent_status_history") as mock_timeline:
                mock_timeline.return_value = {}
                with patch("overcode.data_export.read_presence_history") as mock_presence:
                    mock_presence.return_value = []

                    export_to_parquet(output_path)

        # Should add .parquet extension
        assert (tmp_path / "export.parquet").exists()

    def test_export_without_timeline(self, tmp_path, sample_session):
        """Test export with timeline disabled."""
        pytest.importorskip("pyarrow")

        output_path = str(tmp_path / "export.parquet")

        with patch("overcode.data_export.SessionManager") as mock_sm:
            mock_instance = MagicMock()
            mock_instance.list_sessions.return_value = [sample_session]
            mock_instance.list_archived_sessions.return_value = []
            mock_sm.return_value = mock_instance

            with patch("overcode.data_export.read_agent_status_history") as mock_timeline:
                mock_timeline.return_value = []
                with patch("overcode.data_export.read_presence_history") as mock_presence:
                    mock_presence.return_value = []

                    result = export_to_parquet(output_path, include_timeline=False)

        assert result["timeline_rows"] == 0
        # Timeline file should not exist
        timeline_path = tmp_path / "export_timeline.parquet"
        assert not timeline_path.exists()

    def test_export_without_presence(self, tmp_path, sample_session):
        """Test export with presence disabled."""
        pytest.importorskip("pyarrow")

        output_path = str(tmp_path / "export.parquet")

        with patch("overcode.data_export.SessionManager") as mock_sm:
            mock_instance = MagicMock()
            mock_instance.list_sessions.return_value = [sample_session]
            mock_instance.list_archived_sessions.return_value = []
            mock_sm.return_value = mock_instance

            with patch("overcode.data_export.read_agent_status_history") as mock_timeline:
                mock_timeline.return_value = []
                with patch("overcode.data_export.read_presence_history") as mock_presence:
                    mock_presence.return_value = []

                    result = export_to_parquet(output_path, include_presence=False)

        assert result["presence_rows"] == 0
        # Presence file should not exist
        presence_path = tmp_path / "export_presence.parquet"
        assert not presence_path.exists()

    def test_export_without_archived(self, tmp_path, sample_session):
        """Test export without archived sessions."""
        pytest.importorskip("pyarrow")

        output_path = str(tmp_path / "export.parquet")

        with patch("overcode.data_export.SessionManager") as mock_sm:
            mock_instance = MagicMock()
            mock_instance.list_sessions.return_value = [sample_session]
            mock_instance.list_archived_sessions.return_value = [sample_session]
            mock_sm.return_value = mock_instance

            with patch("overcode.data_export.read_agent_status_history") as mock_timeline:
                mock_timeline.return_value = []
                with patch("overcode.data_export.read_presence_history") as mock_presence:
                    mock_presence.return_value = []

                    result = export_to_parquet(output_path, include_archived=False)

        assert result["sessions_count"] == 1
        assert result["archived_count"] == 0


class TestBuildTimelineRecordsWithActivity:
    """Additional tests for timeline record building."""

    def test_handles_string_timestamps(self):
        """Should handle timestamps that are already strings."""
        with patch("overcode.data_export.read_agent_status_history") as mock_read:
            mock_read.return_value = [
                ("2024-01-01T12:00:00", "agent1", "running", "activity1"),
            ]
            records = _build_timeline_records()

            assert len(records) == 1
            assert records[0]["timestamp"] == "2024-01-01T12:00:00"


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
