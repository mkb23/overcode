"""
Read Claude Code's history and session files for interaction/token counting.

Claude Code stores data in:
- ~/.claude/history.jsonl - interaction history (prompts sent)
- ~/.claude/projects/{encoded-path}/{sessionId}.jsonl - full conversation with token usage

Each assistant message in session files has usage data:
{
  "usage": {
    "input_tokens": 1003,
    "cache_creation_input_tokens": 2884,
    "cache_read_input_tokens": 25944,
    "output_tokens": 278
  }
}
"""

import json
from pathlib import Path
from datetime import datetime
from typing import List, Optional, TYPE_CHECKING
from dataclasses import dataclass

if TYPE_CHECKING:
    from .session_manager import Session


CLAUDE_HISTORY_PATH = Path.home() / ".claude" / "history.jsonl"
CLAUDE_PROJECTS_PATH = Path.home() / ".claude" / "projects"


@dataclass
class ClaudeSessionStats:
    """Statistics for a Claude Code session."""
    interaction_count: int
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    work_times: List[float]  # seconds per work cycle (prompt to next prompt)
    current_context_tokens: int = 0  # Most recent input_tokens (current context size)

    @property
    def total_tokens(self) -> int:
        """Total tokens (input + output, not counting cache)."""
        return self.input_tokens + self.output_tokens

    @property
    def total_tokens_with_cache(self) -> int:
        """Total tokens including cache operations."""
        return (self.input_tokens + self.output_tokens +
                self.cache_creation_tokens + self.cache_read_tokens)

    @property
    def median_work_time(self) -> float:
        """Median work time in seconds (50th percentile)."""
        if not self.work_times:
            return 0.0
        sorted_times = sorted(self.work_times)
        n = len(sorted_times)
        if n % 2 == 0:
            return (sorted_times[n // 2 - 1] + sorted_times[n // 2]) / 2
        return sorted_times[n // 2]


@dataclass
class HistoryEntry:
    """A single interaction from Claude Code history."""
    display: str
    timestamp_ms: int
    project: Optional[str]
    session_id: Optional[str]

    @property
    def timestamp(self) -> datetime:
        """Convert millisecond timestamp to datetime."""
        return datetime.fromtimestamp(self.timestamp_ms / 1000)


def read_history(history_path: Path = CLAUDE_HISTORY_PATH) -> List[HistoryEntry]:
    """Read all entries from history.jsonl.

    Args:
        history_path: Path to history file (defaults to ~/.claude/history.jsonl)

    Returns:
        List of HistoryEntry objects, oldest first
    """
    if not history_path.exists():
        return []

    entries = []
    try:
        with open(history_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    entry = HistoryEntry(
                        display=data.get("display", ""),
                        timestamp_ms=data.get("timestamp", 0),
                        project=data.get("project"),
                        session_id=data.get("sessionId"),
                    )
                    entries.append(entry)
                except (json.JSONDecodeError, KeyError):
                    # Skip malformed entries
                    continue
    except IOError:
        return []

    return entries


def get_interactions_for_session(
    session: "Session",
    history_path: Path = CLAUDE_HISTORY_PATH
) -> List[HistoryEntry]:
    """Get history entries matching a session.

    Matches by:
    1. Project path == session.start_directory
    2. Timestamp >= session.start_time

    Args:
        session: The overcode Session to match
        history_path: Path to history file

    Returns:
        List of matching HistoryEntry objects
    """
    if not session.start_directory:
        return []

    # Parse session start time
    try:
        session_start = datetime.fromisoformat(session.start_time)
        session_start_ms = int(session_start.timestamp() * 1000)
    except (ValueError, TypeError):
        return []

    # Normalize the project path for comparison
    session_dir = str(Path(session.start_directory).resolve())

    entries = read_history(history_path)
    matching = []

    for entry in entries:
        # Must be after session started
        if entry.timestamp_ms < session_start_ms:
            continue

        # Must match project directory
        if entry.project:
            entry_dir = str(Path(entry.project).resolve())
            if entry_dir == session_dir:
                matching.append(entry)

    return matching


def count_interactions(
    session: "Session",
    history_path: Path = CLAUDE_HISTORY_PATH
) -> int:
    """Count interactions for a session.

    Args:
        session: The overcode Session to count for
        history_path: Path to history file

    Returns:
        Number of interactions (user prompts) for this session
    """
    return len(get_interactions_for_session(session, history_path))


def get_session_ids_for_session(
    session: "Session",
    history_path: Path = CLAUDE_HISTORY_PATH
) -> List[str]:
    """Get unique Claude Code sessionIds for an overcode session.

    One overcode session may span multiple Claude Code sessions
    (if Claude is restarted in the same tmux window).

    Args:
        session: The overcode Session
        history_path: Path to history file

    Returns:
        List of unique sessionId strings
    """
    entries = get_interactions_for_session(session, history_path)
    session_ids = set()
    for entry in entries:
        if entry.session_id:
            session_ids.add(entry.session_id)
    return sorted(session_ids)


def encode_project_path(path: str) -> str:
    """Encode a project path to Claude Code's directory naming format.

    Claude Code stores project data in directories named like:
    /home/user/myproject -> -home-user-myproject

    Args:
        path: The project path to encode

    Returns:
        Encoded directory name
    """
    # Resolve to absolute path and replace / with -
    resolved = str(Path(path).resolve())
    # Replace path separators with dashes, prepend dash
    return resolved.replace("/", "-")


def get_session_file_path(
    project_path: str,
    session_id: str,
    projects_path: Path = CLAUDE_PROJECTS_PATH
) -> Path:
    """Get the path to a Claude Code session JSONL file.

    Args:
        project_path: The project directory path
        session_id: The Claude Code sessionId
        projects_path: Base path for Claude projects

    Returns:
        Path to the session JSONL file
    """
    encoded = encode_project_path(project_path)
    return projects_path / encoded / f"{session_id}.jsonl"


def read_token_usage_from_session_file(
    session_file: Path,
    since: Optional[datetime] = None
) -> dict:
    """Read token usage from a Claude Code session JSONL file.

    Args:
        session_file: Path to the session JSONL file
        since: Only count tokens from messages after this time

    Returns:
        Dict with input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens,
        and current_context_tokens (most recent input_tokens value)
    """
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "current_context_tokens": 0,  # Most recent input_tokens
    }

    if not session_file.exists():
        return totals

    try:
        with open(session_file, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    # Only assistant messages have usage data
                    if data.get("type") == "assistant":
                        # Check timestamp if filtering by time
                        if since:
                            ts_str = data.get("timestamp")
                            if ts_str:
                                try:
                                    # Parse ISO timestamp (e.g., "2026-01-02T06:56:01.975Z")
                                    msg_time = datetime.fromisoformat(
                                        ts_str.replace("Z", "+00:00")
                                    ).replace(tzinfo=None)
                                    if msg_time < since:
                                        continue
                                except (ValueError, TypeError):
                                    pass

                        message = data.get("message", {})
                        usage = message.get("usage", {})
                        if usage:
                            input_tokens = usage.get("input_tokens", 0)
                            cache_read = usage.get("cache_read_input_tokens", 0)
                            totals["input_tokens"] += input_tokens
                            totals["output_tokens"] += usage.get("output_tokens", 0)
                            totals["cache_creation_tokens"] += usage.get(
                                "cache_creation_input_tokens", 0
                            )
                            totals["cache_read_tokens"] += cache_read
                            # Track most recent context size (input + cached context)
                            context_size = input_tokens + cache_read
                            if context_size > 0:
                                totals["current_context_tokens"] = context_size
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue
    except IOError:
        pass

    return totals


def read_work_times_from_session_file(
    session_file: Path,
    since: Optional[datetime] = None
) -> List[float]:
    """Calculate work times from a Claude Code session file.

    Work time = time from one user prompt to the next user prompt.
    This represents how long the agent worked autonomously.

    Only counts actual user prompts (not tool results which are automatic).

    Args:
        session_file: Path to the session JSONL file
        since: Only count work times from messages after this time

    Returns:
        List of work times in seconds
    """
    if not session_file.exists():
        return []

    user_prompt_times: List[datetime] = []

    try:
        with open(session_file, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("type") != "user":
                        continue

                    # Check if this is an actual user prompt (not a tool result)
                    message = data.get("message", {})
                    content = message.get("content", "")

                    # Tool results have content as a list with tool_result type
                    if isinstance(content, list):
                        # Check if it's a tool result
                        if content and content[0].get("type") == "tool_result":
                            continue

                    # Parse timestamp
                    ts_str = data.get("timestamp")
                    if not ts_str:
                        continue

                    try:
                        msg_time = datetime.fromisoformat(
                            ts_str.replace("Z", "+00:00")
                        ).replace(tzinfo=None)

                        # Filter by since time
                        if since and msg_time < since:
                            continue

                        user_prompt_times.append(msg_time)
                    except (ValueError, TypeError):
                        continue

                except (json.JSONDecodeError, KeyError, TypeError):
                    continue
    except IOError:
        return []

    # Calculate durations between consecutive prompts
    work_times = []
    for i in range(1, len(user_prompt_times)):
        duration = (user_prompt_times[i] - user_prompt_times[i - 1]).total_seconds()
        if duration > 0:
            work_times.append(duration)

    return work_times


def get_session_stats(
    session: "Session",
    history_path: Path = CLAUDE_HISTORY_PATH,
    projects_path: Path = CLAUDE_PROJECTS_PATH
) -> Optional[ClaudeSessionStats]:
    """Get comprehensive stats for an overcode session.

    Combines interaction counting with token usage from session files.

    Args:
        session: The overcode Session
        history_path: Path to history.jsonl
        projects_path: Path to Claude projects directory

    Returns:
        ClaudeSessionStats if session has start_directory, None otherwise
    """
    if not session.start_directory:
        return None

    # Parse session start time for filtering
    try:
        session_start = datetime.fromisoformat(session.start_time)
    except (ValueError, TypeError):
        return None

    # Get interaction count and session IDs
    interactions = get_interactions_for_session(session, history_path)
    interaction_count = len(interactions)

    # Get unique session IDs
    session_ids = set()
    for entry in interactions:
        if entry.session_id:
            session_ids.add(entry.session_id)

    # Sum token usage and work times across all session files
    total_input = 0
    total_output = 0
    total_cache_creation = 0
    total_cache_read = 0
    current_context = 0  # Track most recent context size
    all_work_times: List[float] = []

    for sid in session_ids:
        session_file = get_session_file_path(
            session.start_directory, sid, projects_path
        )
        usage = read_token_usage_from_session_file(session_file, since=session_start)
        total_input += usage["input_tokens"]
        total_output += usage["output_tokens"]
        total_cache_creation += usage["cache_creation_tokens"]
        total_cache_read += usage["cache_read_tokens"]
        # Keep the largest current context (most recent across all session files)
        if usage["current_context_tokens"] > current_context:
            current_context = usage["current_context_tokens"]

        # Collect work times from this session file
        work_times = read_work_times_from_session_file(session_file, since=session_start)
        all_work_times.extend(work_times)

    return ClaudeSessionStats(
        interaction_count=interaction_count,
        input_tokens=total_input,
        output_tokens=total_output,
        cache_creation_tokens=total_cache_creation,
        cache_read_tokens=total_cache_read,
        work_times=all_work_times,
        current_context_tokens=current_context,
    )
