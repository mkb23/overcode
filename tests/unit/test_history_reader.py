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

    def test_encodes_unix_path(self, tmp_path):
        """Should encode Unix path to Claude Code format."""
        from overcode.history_reader import encode_project_path

        # Use tmp_path which is a real existing path that won't get resolved differently
        result = encode_project_path(str(tmp_path))

        # Should replace slashes with dashes and start with dash
        assert result.startswith("-")
        assert "/" not in result
        # Temp path name should be in the result
        assert tmp_path.name in result

    def test_handles_trailing_slash(self, tmp_path):
        """Should handle paths with trailing slashes."""
        from overcode.history_reader import encode_project_path

        path_with_slash = str(tmp_path) + "/"
        result = encode_project_path(path_with_slash)

        # Path.resolve() removes trailing slash - result should not end with dash
        assert not result.endswith("-")
        assert tmp_path.name in result


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
        # Current context = last message's input + cache_read: 150 + 50 = 200
        assert result["current_context_tokens"] == 200

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

    def test_tracks_current_context_from_input_plus_cache_read(self, tmp_path):
        """Should track current context as input_tokens + cache_read_input_tokens.

        This matches real Claude session files where most context comes from cache.
        Example: input_tokens=8, cache_read_input_tokens=129736 means 130K context.
        """
        from overcode.history_reader import read_token_usage_from_session_file

        session_file = tmp_path / "session.jsonl"
        # Real-world format from Claude session files
        entries = [
            {
                "type": "assistant",
                "timestamp": "2026-01-16T10:00:00.000Z",
                "message": {
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 500,
                        "cache_creation_input_tokens": 3502,
                        "cache_read_input_tokens": 15760,
                    }
                },
            },
            {
                "type": "assistant",
                "timestamp": "2026-01-16T10:01:00.000Z",
                "message": {
                    "usage": {
                        "input_tokens": 8,
                        "cache_creation_input_tokens": 232,
                        "cache_read_input_tokens": 129504,
                        "output_tokens": 1200,
                    }
                },
            },
        ]
        session_file.write_text("\n".join(json.dumps(e) for e in entries))

        result = read_token_usage_from_session_file(session_file)

        # Current context should be most recent: 8 + 129504 = 129512
        assert result["current_context_tokens"] == 129512
        # Verify it's ~65% of 200K context window
        assert result["current_context_tokens"] / 200_000 * 100 == pytest.approx(64.756, rel=0.01)

    def test_current_context_uses_last_message(self, tmp_path):
        """Should use the most recent message for current context."""
        from overcode.history_reader import read_token_usage_from_session_file

        session_file = tmp_path / "session.jsonl"
        entries = [
            {
                "type": "assistant",
                "timestamp": "2026-01-16T10:00:00.000Z",
                "message": {
                    "usage": {
                        "input_tokens": 50000,
                        "cache_read_input_tokens": 0,
                        "output_tokens": 100,
                    }
                },
            },
            {
                "type": "assistant",
                "timestamp": "2026-01-16T10:01:00.000Z",
                "message": {
                    "usage": {
                        "input_tokens": 1000,
                        "cache_read_input_tokens": 80000,
                        "output_tokens": 200,
                    }
                },
            },
        ]
        session_file.write_text("\n".join(json.dumps(e) for e in entries))

        result = read_token_usage_from_session_file(session_file)

        # Should be last message: 1000 + 80000 = 81000, not first: 50000
        assert result["current_context_tokens"] == 81000


class TestGetSessionStatsWithSubagents:
    """Test get_session_stats includes subagent token usage."""

    def test_includes_subagent_tokens(self, tmp_path):
        """Should sum tokens from main session and subagent files."""
        from overcode.history_reader import get_session_stats, encode_project_path

        # Setup: create history file with session entry
        history_file = tmp_path / "history.jsonl"
        session_start = datetime(2026, 1, 15, 10, 0, 0)
        session_start_ms = int(session_start.timestamp() * 1000)
        session_id = "main-session-123"

        history_entry = {
            "display": "test prompt",
            "timestamp": session_start_ms + 1000,
            "project": "/test/project",
            "sessionId": session_id,
        }
        history_file.write_text(json.dumps(history_entry))

        # Setup: create projects directory structure
        projects_path = tmp_path / "projects"
        encoded_path = encode_project_path("/test/project")
        project_dir = projects_path / encoded_path
        project_dir.mkdir(parents=True)

        # Create main session file with 1000 tokens
        main_session_file = project_dir / f"{session_id}.jsonl"
        main_entry = {
            "type": "assistant",
            "timestamp": "2026-01-15T10:01:00.000Z",
            "message": {
                "usage": {
                    "input_tokens": 500,
                    "output_tokens": 500,
                    "cache_creation_input_tokens": 100,
                    "cache_read_input_tokens": 50,
                }
            },
        }
        main_session_file.write_text(json.dumps(main_entry))

        # Create subagents directory with two subagent files
        subagents_dir = project_dir / session_id / "subagents"
        subagents_dir.mkdir(parents=True)

        # Subagent 1: 2000 tokens
        subagent1_file = subagents_dir / "agent-a86c0d0.jsonl"
        subagent1_entry = {
            "type": "assistant",
            "timestamp": "2026-01-15T10:02:00.000Z",
            "message": {
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 1000,
                    "cache_creation_input_tokens": 200,
                    "cache_read_input_tokens": 100,
                }
            },
        }
        subagent1_file.write_text(json.dumps(subagent1_entry))

        # Subagent 2: 3000 tokens
        subagent2_file = subagents_dir / "agent-a1207fb.jsonl"
        subagent2_entry = {
            "type": "assistant",
            "timestamp": "2026-01-15T10:03:00.000Z",
            "message": {
                "usage": {
                    "input_tokens": 1500,
                    "output_tokens": 1500,
                    "cache_creation_input_tokens": 300,
                    "cache_read_input_tokens": 150,
                }
            },
        }
        subagent2_file.write_text(json.dumps(subagent2_entry))

        # Create session object
        session = create_test_session(
            start_directory="/test/project",
            start_time=session_start.isoformat()
        )

        # Get stats
        stats = get_session_stats(
            session,
            history_path=history_file,
            projects_path=projects_path
        )

        # Verify totals include main + both subagents
        # Main: 500 + 500 = 1000, Sub1: 1000 + 1000 = 2000, Sub2: 1500 + 1500 = 3000
        assert stats.input_tokens == 500 + 1000 + 1500  # 3000
        assert stats.output_tokens == 500 + 1000 + 1500  # 3000
        assert stats.total_tokens == 6000
        assert stats.cache_creation_tokens == 100 + 200 + 300  # 600
        assert stats.cache_read_tokens == 50 + 100 + 150  # 300

    def test_handles_no_subagents_directory(self, tmp_path):
        """Should work correctly when no subagents directory exists."""
        from overcode.history_reader import get_session_stats, encode_project_path

        # Setup history file
        history_file = tmp_path / "history.jsonl"
        session_start = datetime(2026, 1, 15, 10, 0, 0)
        session_start_ms = int(session_start.timestamp() * 1000)
        session_id = "session-no-subagents"

        history_entry = {
            "display": "test",
            "timestamp": session_start_ms + 1000,
            "project": "/test/project",
            "sessionId": session_id,
        }
        history_file.write_text(json.dumps(history_entry))

        # Setup projects directory with only main session file (no subagents dir)
        projects_path = tmp_path / "projects"
        encoded_path = encode_project_path("/test/project")
        project_dir = projects_path / encoded_path
        project_dir.mkdir(parents=True)

        main_session_file = project_dir / f"{session_id}.jsonl"
        main_entry = {
            "type": "assistant",
            "timestamp": "2026-01-15T10:01:00.000Z",
            "message": {
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 500,
                }
            },
        }
        main_session_file.write_text(json.dumps(main_entry))

        session = create_test_session(
            start_directory="/test/project",
            start_time=session_start.isoformat()
        )

        stats = get_session_stats(
            session,
            history_path=history_file,
            projects_path=projects_path
        )

        # Should still work, just with main session tokens
        assert stats.input_tokens == 1000
        assert stats.output_tokens == 500


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


class TestGetCurrentSessionIdForDirectory:
    """Test get_current_session_id_for_directory function."""

    def test_returns_most_recent_session_id(self, tmp_path):
        """Should return the most recent sessionId for the directory.

        history.jsonl is append-only so entries are in chronological order.
        The last matching entry in the file is the most recent.
        """
        from overcode.history_reader import get_current_session_id_for_directory

        history_file = tmp_path / "history.jsonl"
        since = datetime(2024, 1, 15, 10, 0, 0)
        since_ms = int(since.timestamp() * 1000)

        entries = [
            {"display": "1", "timestamp": since_ms + 1000, "project": "/test/project", "sessionId": "old-session"},
            {"display": "2", "timestamp": since_ms + 3000, "project": "/test/project", "sessionId": "middle-session"},
            {"display": "3", "timestamp": since_ms + 5000, "project": "/test/project", "sessionId": "new-session"},
        ]
        history_file.write_text("\n".join(json.dumps(e) for e in entries))

        result = get_current_session_id_for_directory("/test/project", since, history_file)

        assert result == "new-session"

    def test_returns_none_when_no_matching_entries(self, tmp_path):
        """Should return None when no entries match."""
        from overcode.history_reader import get_current_session_id_for_directory

        history_file = tmp_path / "history.jsonl"
        since = datetime(2024, 1, 15, 10, 0, 0)
        since_ms = int(since.timestamp() * 1000)

        # Entry before cutoff
        entries = [
            {"display": "1", "timestamp": since_ms - 1000, "project": "/test/project", "sessionId": "old-session"},
        ]
        history_file.write_text("\n".join(json.dumps(e) for e in entries))

        result = get_current_session_id_for_directory("/test/project", since, history_file)

        assert result is None

    def test_filters_by_directory(self, tmp_path):
        """Should only match entries for the specified directory."""
        from overcode.history_reader import get_current_session_id_for_directory

        history_file = tmp_path / "history.jsonl"
        since = datetime(2024, 1, 15, 10, 0, 0)
        since_ms = int(since.timestamp() * 1000)

        entries = [
            {"display": "1", "timestamp": since_ms + 1000, "project": "/other/project", "sessionId": "other-session"},
            {"display": "2", "timestamp": since_ms + 2000, "project": "/test/project", "sessionId": "correct-session"},
        ]
        history_file.write_text("\n".join(json.dumps(e) for e in entries))

        result = get_current_session_id_for_directory("/test/project", since, history_file)

        assert result == "correct-session"


class TestReadWorkTimesFromSessionFile:
    """Test read_work_times_from_session_file function."""

    def test_returns_empty_for_nonexistent_file(self, tmp_path):
        """Should return empty list for nonexistent file."""
        from overcode.history_reader import read_work_times_from_session_file

        result = read_work_times_from_session_file(tmp_path / "nonexistent.jsonl")

        assert result == []

    def test_calculates_work_times_between_prompts(self, tmp_path):
        """Should calculate duration between user prompts."""
        from overcode.history_reader import read_work_times_from_session_file

        session_file = tmp_path / "session.jsonl"
        entries = [
            {
                "type": "user",
                "timestamp": "2024-01-15T10:00:00.000Z",
                "message": {"content": "first prompt"}
            },
            {
                "type": "assistant",
                "timestamp": "2024-01-15T10:00:30.000Z",
                "message": {"content": "response 1"}
            },
            {
                "type": "user",
                "timestamp": "2024-01-15T10:01:00.000Z",
                "message": {"content": "second prompt"}
            },
            {
                "type": "user",
                "timestamp": "2024-01-15T10:03:00.000Z",
                "message": {"content": "third prompt"}
            },
        ]
        session_file.write_text("\n".join(json.dumps(e) for e in entries))

        result = read_work_times_from_session_file(session_file)

        # Between first and second prompt: 60 seconds
        # Between second and third prompt: 120 seconds
        assert len(result) == 2
        assert result[0] == 60.0
        assert result[1] == 120.0

    def test_skips_tool_results(self, tmp_path):
        """Should skip tool result entries (not real user prompts)."""
        from overcode.history_reader import read_work_times_from_session_file

        session_file = tmp_path / "session.jsonl"
        entries = [
            {
                "type": "user",
                "timestamp": "2024-01-15T10:00:00.000Z",
                "message": {"content": "first prompt"}
            },
            {
                "type": "user",
                "timestamp": "2024-01-15T10:00:30.000Z",
                "message": {"content": [{"type": "tool_result", "content": "tool output"}]}
            },
            {
                "type": "user",
                "timestamp": "2024-01-15T10:01:00.000Z",
                "message": {"content": "second prompt"}
            },
        ]
        session_file.write_text("\n".join(json.dumps(e) for e in entries))

        result = read_work_times_from_session_file(session_file)

        # Only one work time between first real prompt and second real prompt
        assert len(result) == 1
        assert result[0] == 60.0

    def test_filters_by_since_timestamp(self, tmp_path):
        """Should only include work times after the since timestamp."""
        from overcode.history_reader import read_work_times_from_session_file

        session_file = tmp_path / "session.jsonl"
        entries = [
            {
                "type": "user",
                "timestamp": "2024-01-15T09:00:00.000Z",
                "message": {"content": "before cutoff"}
            },
            {
                "type": "user",
                "timestamp": "2024-01-15T09:30:00.000Z",
                "message": {"content": "also before cutoff"}
            },
            {
                "type": "user",
                "timestamp": "2024-01-15T10:30:00.000Z",
                "message": {"content": "after cutoff 1"}
            },
            {
                "type": "user",
                "timestamp": "2024-01-15T10:31:00.000Z",
                "message": {"content": "after cutoff 2"}
            },
        ]
        session_file.write_text("\n".join(json.dumps(e) for e in entries))

        since = datetime(2024, 1, 15, 10, 0, 0)
        result = read_work_times_from_session_file(session_file, since=since)

        # Only one work time between the two entries after cutoff
        assert len(result) == 1
        assert result[0] == 60.0

    def test_handles_io_error(self, tmp_path):
        """Should return empty list on IO errors."""
        from overcode.history_reader import read_work_times_from_session_file

        # Create a directory instead of a file to cause IO error
        dir_path = tmp_path / "not_a_file.jsonl"
        dir_path.mkdir()

        result = read_work_times_from_session_file(dir_path)

        assert result == []


class TestGetSessionStatsOwnership:
    """Test get_session_stats with owned vs unowned sessions."""

    def test_only_uses_owned_session_ids_for_context(self, tmp_path):
        """Context window should only use owned sessionIds."""
        from overcode.history_reader import get_session_stats, encode_project_path

        # Setup history file
        history_file = tmp_path / "history.jsonl"
        session_start = datetime(2026, 1, 15, 10, 0, 0)
        session_start_ms = int(session_start.timestamp() * 1000)

        # Two sessions in same directory
        entries = [
            {"display": "1", "timestamp": session_start_ms + 1000, "project": "/test/project", "sessionId": "owned-session"},
            {"display": "2", "timestamp": session_start_ms + 2000, "project": "/test/project", "sessionId": "unowned-session"},
        ]
        history_file.write_text("\n".join(json.dumps(e) for e in entries))

        # Setup session files
        projects_path = tmp_path / "projects"
        encoded_path = encode_project_path("/test/project")
        project_dir = projects_path / encoded_path
        project_dir.mkdir(parents=True)

        # Owned session with small context
        owned_file = project_dir / "owned-session.jsonl"
        owned_entry = {
            "type": "assistant",
            "timestamp": "2026-01-15T10:01:00.000Z",
            "message": {"usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 500}}
        }
        owned_file.write_text(json.dumps(owned_entry))

        # Unowned session with large context (should not be used for context calc)
        unowned_file = project_dir / "unowned-session.jsonl"
        unowned_entry = {
            "type": "assistant",
            "timestamp": "2026-01-15T10:02:00.000Z",
            "message": {"usage": {"input_tokens": 50000, "output_tokens": 100, "cache_read_input_tokens": 100000}}
        }
        unowned_file.write_text(json.dumps(unowned_entry))

        # Create session with only owned-session tracked
        session = create_test_session(
            start_directory="/test/project",
            start_time=session_start.isoformat()
        )
        # Simulate session.claude_session_ids = ["owned-session"]
        session.claude_session_ids = ["owned-session"]

        stats = get_session_stats(
            session,
            history_path=history_file,
            projects_path=projects_path
        )

        # Context should be from owned session only: 100 + 500 = 600
        assert stats.current_context_tokens == 600
        # Total tokens also scoped to owned sessions only (#264)
        assert stats.input_tokens == 100
        # Interaction count should only reflect owned session entries
        assert stats.interaction_count == 1


class TestActiveSessionContext:
    """Test that context uses active_claude_session_id after /clear (#116)."""

    def test_uses_active_session_not_max(self, tmp_path):
        """After /clear, context should use active session, not MAX of all owned."""
        from overcode.history_reader import get_session_stats, encode_project_path

        history_file = tmp_path / "history.jsonl"
        session_start = datetime(2026, 1, 15, 10, 0, 0)
        session_start_ms = int(session_start.timestamp() * 1000)

        entries = [
            {"display": "1", "timestamp": session_start_ms + 1000, "project": "/test/project", "sessionId": "old-session"},
            {"display": "2", "timestamp": session_start_ms + 2000, "project": "/test/project", "sessionId": "new-session"},
        ]
        history_file.write_text("\n".join(json.dumps(e) for e in entries))

        projects_path = tmp_path / "projects"
        encoded_path = encode_project_path("/test/project")
        project_dir = projects_path / encoded_path
        project_dir.mkdir(parents=True)

        # Old session (pre-clear) with high context
        old_file = project_dir / "old-session.jsonl"
        old_entry = {
            "type": "assistant",
            "timestamp": "2026-01-15T10:01:00.000Z",
            "message": {"usage": {"input_tokens": 5000, "output_tokens": 100, "cache_read_input_tokens": 130000}}
        }
        old_file.write_text(json.dumps(old_entry))

        # New session (post-clear) with low context
        new_file = project_dir / "new-session.jsonl"
        new_entry = {
            "type": "assistant",
            "timestamp": "2026-01-15T10:05:00.000Z",
            "message": {"usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 15000}}
        }
        new_file.write_text(json.dumps(new_entry))

        session = create_test_session(
            start_directory="/test/project",
            start_time=session_start.isoformat()
        )
        session.claude_session_ids = ["old-session", "new-session"]
        session.active_claude_session_id = "new-session"

        stats = get_session_stats(
            session,
            history_path=history_file,
            projects_path=projects_path
        )

        # Context should be from active session only: 100 + 15000 = 15100
        # NOT max of old (135000) and new (15100)
        assert stats.current_context_tokens == 15100
        # Total tokens still include both sessions
        assert stats.input_tokens == 5000 + 100

    def test_falls_back_to_max_owned_when_no_active(self, tmp_path):
        """Without active_claude_session_id, fall back to MAX of owned (old behavior)."""
        from overcode.history_reader import get_session_stats, encode_project_path

        history_file = tmp_path / "history.jsonl"
        session_start = datetime(2026, 1, 15, 10, 0, 0)
        session_start_ms = int(session_start.timestamp() * 1000)

        entries = [
            {"display": "1", "timestamp": session_start_ms + 1000, "project": "/test/project", "sessionId": "session-a"},
            {"display": "2", "timestamp": session_start_ms + 2000, "project": "/test/project", "sessionId": "session-b"},
        ]
        history_file.write_text("\n".join(json.dumps(e) for e in entries))

        projects_path = tmp_path / "projects"
        encoded_path = encode_project_path("/test/project")
        project_dir = projects_path / encoded_path
        project_dir.mkdir(parents=True)

        file_a = project_dir / "session-a.jsonl"
        file_a.write_text(json.dumps({
            "type": "assistant",
            "timestamp": "2026-01-15T10:01:00.000Z",
            "message": {"usage": {"input_tokens": 500, "output_tokens": 50, "cache_read_input_tokens": 80000}}
        }))

        file_b = project_dir / "session-b.jsonl"
        file_b.write_text(json.dumps({
            "type": "assistant",
            "timestamp": "2026-01-15T10:02:00.000Z",
            "message": {"usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 20000}}
        }))

        session = create_test_session(
            start_directory="/test/project",
            start_time=session_start.isoformat()
        )
        session.claude_session_ids = ["session-a", "session-b"]
        # No active_claude_session_id set

        stats = get_session_stats(
            session,
            history_path=history_file,
            projects_path=projects_path
        )

        # Should fall back to MAX: 80500 > 20100
        assert stats.current_context_tokens == 80500


class TestHistoryEntryEdgeCases:
    """Test edge cases in HistoryEntry and history reading."""

    def test_handles_zero_timestamp(self):
        """Should handle zero timestamp gracefully."""
        entry = HistoryEntry(
            display="test",
            timestamp_ms=0,
            project=None,
            session_id=None
        )

        ts = entry.timestamp
        assert isinstance(ts, datetime)
        assert ts.year == 1970  # Epoch

    def test_read_history_handles_io_error(self, tmp_path):
        """Should return empty list on IO error."""
        # Create a directory instead of a file
        dir_path = tmp_path / "not_a_file.jsonl"
        dir_path.mkdir()

        result = read_history(dir_path)

        # Should return empty list, not raise
        assert result == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
