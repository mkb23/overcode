"""
Unit tests for history_reader module.
"""

import pytest
import sys
import json
from pathlib import Path
from datetime import datetime, timedelta

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.history_reader import (
    read_history,
    get_interactions_for_session,
    count_interactions,
    get_session_ids_for_session,
    HistoryEntry,
)
from overcode.session_manager import Session, SessionStats


def create_test_session(
    start_directory: str = "/Users/test/project",
    start_time: str = None,
    **kwargs
) -> Session:
    """Create a test Session object."""
    if start_time is None:
        start_time = datetime.now().isoformat()
    return Session(
        id="test-session-id",
        name="test-session",
        tmux_session="agents",
        tmux_window=1,
        command=["claude", "code"],
        start_directory=start_directory,
        start_time=start_time,
        **kwargs
    )


class TestReadHistory:
    """Test reading history.jsonl"""

    def test_returns_empty_list_when_no_file(self, tmp_path):
        """Should return empty list when file doesn't exist."""
        result = read_history(tmp_path / "nonexistent.jsonl")
        assert result == []

    def test_reads_valid_entries(self, tmp_path):
        """Should parse valid JSONL entries."""
        history_file = tmp_path / "history.jsonl"
        entries = [
            {"display": "hello", "timestamp": 1700000000000, "project": "/test", "sessionId": "abc"},
            {"display": "world", "timestamp": 1700000001000, "project": "/test", "sessionId": "abc"},
        ]
        history_file.write_text("\n".join(json.dumps(e) for e in entries))

        result = read_history(history_file)

        assert len(result) == 2
        assert result[0].display == "hello"
        assert result[1].display == "world"
        assert result[0].timestamp_ms == 1700000000000
        assert result[0].session_id == "abc"

    def test_skips_malformed_entries(self, tmp_path):
        """Should skip lines that aren't valid JSON."""
        history_file = tmp_path / "history.jsonl"
        content = """{"display": "valid", "timestamp": 1700000000000}
not json at all
{"display": "also valid", "timestamp": 1700000001000}
"""
        history_file.write_text(content)

        result = read_history(history_file)

        assert len(result) == 2
        assert result[0].display == "valid"
        assert result[1].display == "also valid"

    def test_handles_missing_fields(self, tmp_path):
        """Should handle entries with missing optional fields."""
        history_file = tmp_path / "history.jsonl"
        entry = {"display": "test", "timestamp": 1700000000000}  # No project/sessionId
        history_file.write_text(json.dumps(entry))

        result = read_history(history_file)

        assert len(result) == 1
        assert result[0].display == "test"
        assert result[0].project is None
        assert result[0].session_id is None

    def test_skips_empty_lines(self, tmp_path):
        """Should skip empty lines in the file."""
        history_file = tmp_path / "history.jsonl"
        content = """{"display": "one", "timestamp": 1700000000000}

{"display": "two", "timestamp": 1700000001000}

"""
        history_file.write_text(content)

        result = read_history(history_file)

        assert len(result) == 2


class TestHistoryEntry:
    """Test HistoryEntry dataclass."""

    def test_timestamp_conversion(self):
        """Should convert millisecond timestamp to datetime."""
        entry = HistoryEntry(
            display="test",
            timestamp_ms=1700000000000,
            project=None,
            session_id=None
        )

        ts = entry.timestamp
        assert isinstance(ts, datetime)
        # 1700000000 seconds = Nov 14, 2023
        assert ts.year == 2023
        assert ts.month == 11


class TestGetInteractionsForSession:
    """Test matching history entries to sessions."""

    def test_matches_by_project_and_time(self, tmp_path):
        """Should match entries with same project after session start."""
        history_file = tmp_path / "history.jsonl"
        session_start = datetime(2024, 1, 15, 10, 0, 0)
        session_start_ms = int(session_start.timestamp() * 1000)

        entries = [
            # Before session - should not match
            {"display": "before", "timestamp": session_start_ms - 1000, "project": "/test/project"},
            # After session, same project - should match
            {"display": "match1", "timestamp": session_start_ms + 1000, "project": "/test/project"},
            {"display": "match2", "timestamp": session_start_ms + 2000, "project": "/test/project"},
            # After session, different project - should not match
            {"display": "other", "timestamp": session_start_ms + 3000, "project": "/other/project"},
        ]
        history_file.write_text("\n".join(json.dumps(e) for e in entries))

        session = create_test_session(
            start_directory="/test/project",
            start_time=session_start.isoformat()
        )

        result = get_interactions_for_session(session, history_file)

        assert len(result) == 2
        assert result[0].display == "match1"
        assert result[1].display == "match2"

    def test_returns_empty_for_no_start_directory(self, tmp_path):
        """Should return empty list if session has no start_directory."""
        history_file = tmp_path / "history.jsonl"
        entry = {"display": "test", "timestamp": 1700000000000, "project": "/test"}
        history_file.write_text(json.dumps(entry))

        session = create_test_session(start_directory=None)

        result = get_interactions_for_session(session, history_file)

        assert result == []

    def test_normalizes_paths(self, tmp_path):
        """Should match paths even with different formatting."""
        history_file = tmp_path / "history.jsonl"
        session_start = datetime(2024, 1, 15, 10, 0, 0)
        session_start_ms = int(session_start.timestamp() * 1000)

        # Path with trailing slash in history
        entry = {"display": "test", "timestamp": session_start_ms + 1000, "project": "/test/project/"}
        history_file.write_text(json.dumps(entry))

        # Path without trailing slash in session
        session = create_test_session(
            start_directory="/test/project",
            start_time=session_start.isoformat()
        )

        result = get_interactions_for_session(session, history_file)

        # Path.resolve() normalizes both, so they should match
        assert len(result) == 1


class TestCountInteractions:
    """Test interaction counting."""

    def test_counts_matching_entries(self, tmp_path):
        """Should return count of matching entries."""
        history_file = tmp_path / "history.jsonl"
        session_start = datetime(2024, 1, 15, 10, 0, 0)
        session_start_ms = int(session_start.timestamp() * 1000)

        entries = [
            {"display": "1", "timestamp": session_start_ms + 1000, "project": "/test"},
            {"display": "2", "timestamp": session_start_ms + 2000, "project": "/test"},
            {"display": "3", "timestamp": session_start_ms + 3000, "project": "/test"},
        ]
        history_file.write_text("\n".join(json.dumps(e) for e in entries))

        session = create_test_session(
            start_directory="/test",
            start_time=session_start.isoformat()
        )

        count = count_interactions(session, history_file)

        assert count == 3

    def test_returns_zero_for_no_matches(self, tmp_path):
        """Should return 0 when no entries match."""
        history_file = tmp_path / "history.jsonl"
        entry = {"display": "test", "timestamp": 1700000000000, "project": "/other"}
        history_file.write_text(json.dumps(entry))

        session = create_test_session(
            start_directory="/test",
            start_time=datetime.now().isoformat()
        )

        count = count_interactions(session, history_file)

        assert count == 0


class TestGetSessionIdsForSession:
    """Test extracting Claude Code sessionIds."""

    def test_returns_unique_session_ids(self, tmp_path):
        """Should return unique sessionIds from matching entries."""
        history_file = tmp_path / "history.jsonl"
        session_start = datetime(2024, 1, 15, 10, 0, 0)
        session_start_ms = int(session_start.timestamp() * 1000)

        entries = [
            {"display": "1", "timestamp": session_start_ms + 1000, "project": "/test", "sessionId": "aaa"},
            {"display": "2", "timestamp": session_start_ms + 2000, "project": "/test", "sessionId": "aaa"},
            {"display": "3", "timestamp": session_start_ms + 3000, "project": "/test", "sessionId": "bbb"},
        ]
        history_file.write_text("\n".join(json.dumps(e) for e in entries))

        session = create_test_session(
            start_directory="/test",
            start_time=session_start.isoformat()
        )

        result = get_session_ids_for_session(session, history_file)

        assert len(result) == 2
        assert "aaa" in result
        assert "bbb" in result

    def test_excludes_none_session_ids(self, tmp_path):
        """Should not include None sessionIds."""
        history_file = tmp_path / "history.jsonl"
        session_start = datetime(2024, 1, 15, 10, 0, 0)
        session_start_ms = int(session_start.timestamp() * 1000)

        entries = [
            {"display": "1", "timestamp": session_start_ms + 1000, "project": "/test", "sessionId": "aaa"},
            {"display": "2", "timestamp": session_start_ms + 2000, "project": "/test"},  # No sessionId
        ]
        history_file.write_text("\n".join(json.dumps(e) for e in entries))

        session = create_test_session(
            start_directory="/test",
            start_time=session_start.isoformat()
        )

        result = get_session_ids_for_session(session, history_file)

        assert result == ["aaa"]


class TestEncodeProjectPath:
    """Test project path encoding for Claude Code directory names."""

    def test_encodes_unix_path(self):
        """Should encode Unix path to Claude Code format."""
        from overcode.history_reader import encode_project_path

        result = encode_project_path("/home/user/project")

        assert result == "-home-user-project"

    def test_handles_trailing_slash(self):
        """Should handle paths with trailing slashes."""
        from overcode.history_reader import encode_project_path

        result = encode_project_path("/test/path/")

        # Path.resolve() removes trailing slash
        assert result == "-test-path"


class TestClaudeSessionStats:
    """Test ClaudeSessionStats dataclass."""

    def test_total_tokens(self):
        """Should sum input and output tokens."""
        from overcode.history_reader import ClaudeSessionStats

        stats = ClaudeSessionStats(
            interaction_count=5,
            input_tokens=1000,
            output_tokens=2000,
            cache_creation_tokens=500,
            cache_read_tokens=300,
            work_times=[10.0, 20.0, 30.0],
        )

        assert stats.total_tokens == 3000

    def test_total_tokens_with_cache(self):
        """Should sum all token types including cache."""
        from overcode.history_reader import ClaudeSessionStats

        stats = ClaudeSessionStats(
            interaction_count=5,
            input_tokens=1000,
            output_tokens=2000,
            cache_creation_tokens=500,
            cache_read_tokens=300,
            work_times=[10.0, 20.0, 30.0],
        )

        assert stats.total_tokens_with_cache == 3800

    def test_median_work_time_odd_count(self):
        """Should return median for odd number of work times."""
        from overcode.history_reader import ClaudeSessionStats

        stats = ClaudeSessionStats(
            interaction_count=3,
            input_tokens=0,
            output_tokens=0,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            work_times=[10.0, 30.0, 20.0],  # sorted: 10, 20, 30
        )

        assert stats.median_work_time == 20.0

    def test_median_work_time_even_count(self):
        """Should return average of middle two for even count."""
        from overcode.history_reader import ClaudeSessionStats

        stats = ClaudeSessionStats(
            interaction_count=4,
            input_tokens=0,
            output_tokens=0,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            work_times=[10.0, 40.0, 20.0, 30.0],  # sorted: 10, 20, 30, 40
        )

        assert stats.median_work_time == 25.0  # (20 + 30) / 2

    def test_median_work_time_empty(self):
        """Should return 0 for empty work times."""
        from overcode.history_reader import ClaudeSessionStats

        stats = ClaudeSessionStats(
            interaction_count=0,
            input_tokens=0,
            output_tokens=0,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            work_times=[],
        )

        assert stats.median_work_time == 0.0


class TestReadTokenUsageFromSessionFile:
    """Test reading token usage from Claude Code session files."""

    def test_returns_zero_for_nonexistent_file(self, tmp_path):
        """Should return zero counts for nonexistent file."""
        from overcode.history_reader import read_token_usage_from_session_file

        result = read_token_usage_from_session_file(tmp_path / "nonexistent.jsonl")

        assert result["input_tokens"] == 0
        assert result["output_tokens"] == 0

    def test_sums_token_usage_from_assistant_messages(self, tmp_path):
        """Should sum tokens from assistant messages."""
        from overcode.history_reader import read_token_usage_from_session_file

        session_file = tmp_path / "session.jsonl"
        entries = [
            {"type": "user", "message": {"role": "user"}},
            {
                "type": "assistant",
                "timestamp": "2026-01-02T10:00:00.000Z",
                "message": {
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 200,
                        "cache_creation_input_tokens": 50,
                        "cache_read_input_tokens": 25,
                    }
                },
            },
            {
                "type": "assistant",
                "timestamp": "2026-01-02T10:01:00.000Z",
                "message": {
                    "usage": {
                        "input_tokens": 150,
                        "output_tokens": 300,
                        "cache_creation_input_tokens": 75,
                        "cache_read_input_tokens": 50,
                    }
                },
            },
        ]
        session_file.write_text("\n".join(json.dumps(e) for e in entries))

        result = read_token_usage_from_session_file(session_file)

        assert result["input_tokens"] == 250
        assert result["output_tokens"] == 500
        assert result["cache_creation_tokens"] == 125
        assert result["cache_read_tokens"] == 75

    def test_filters_by_timestamp(self, tmp_path):
        """Should only count tokens from messages after 'since' timestamp."""
        from overcode.history_reader import read_token_usage_from_session_file

        session_file = tmp_path / "session.jsonl"
        entries = [
            {
                "type": "assistant",
                "timestamp": "2026-01-02T09:00:00.000Z",  # Before cutoff
                "message": {"usage": {"input_tokens": 100, "output_tokens": 200}},
            },
            {
                "type": "assistant",
                "timestamp": "2026-01-02T11:00:00.000Z",  # After cutoff
                "message": {"usage": {"input_tokens": 150, "output_tokens": 300}},
            },
        ]
        session_file.write_text("\n".join(json.dumps(e) for e in entries))

        cutoff = datetime(2026, 1, 2, 10, 0, 0)
        result = read_token_usage_from_session_file(session_file, since=cutoff)

        assert result["input_tokens"] == 150
        assert result["output_tokens"] == 300


class TestFormatTokens:
    """Test token formatting helper."""

    def test_formats_thousands(self):
        """Should format thousands with K suffix."""
        from overcode.tui_helpers import format_tokens

        assert format_tokens(1500) == "1.5K"
        assert format_tokens(10000) == "10.0K"
        assert format_tokens(999999) == "1000.0K"

    def test_formats_millions(self):
        """Should format millions with M suffix."""
        from overcode.tui_helpers import format_tokens

        assert format_tokens(1500000) == "1.5M"
        assert format_tokens(10000000) == "10.0M"

    def test_formats_small_numbers(self):
        """Should format small numbers without suffix."""
        from overcode.tui_helpers import format_tokens

        assert format_tokens(500) == "500"
        assert format_tokens(0) == "0"
        assert format_tokens(999) == "999"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
