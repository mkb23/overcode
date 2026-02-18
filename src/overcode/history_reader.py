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
import os
import threading
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING
from dataclasses import dataclass

if TYPE_CHECKING:
    from .session_manager import Session


CLAUDE_HISTORY_PATH = Path.home() / ".claude" / "history.jsonl"
CLAUDE_PROJECTS_PATH = Path.home() / ".claude" / "projects"

# Model name → context window size in tokens.
# Default 200K for unknown models.  Update as new models ship.
MODEL_CONTEXT_WINDOWS: Dict[str, int] = {
    "claude-opus-4-6": 200_000,
    "claude-sonnet-4-5-20250929": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
    "claude-3-5-sonnet-20241022": 200_000,
    "claude-3-5-haiku-20241022": 200_000,
    "claude-3-opus-20240229": 200_000,
    "claude-3-sonnet-20240229": 200_000,
    "claude-3-haiku-20240307": 200_000,
}
DEFAULT_CONTEXT_WINDOW = 200_000


def model_context_window(model: Optional[str]) -> int:
    """Return the context window size for a given model name.

    Falls back to DEFAULT_CONTEXT_WINDOW for unknown/None models.
    """
    if not model:
        return DEFAULT_CONTEXT_WINDOW
    return MODEL_CONTEXT_WINDOWS.get(model, DEFAULT_CONTEXT_WINDOW)


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
    subagent_count: int = 0  # Number of subagent files (#176)
    live_subagent_count: int = 0  # Subagents with recently-modified files (#256)
    background_task_count: int = 0  # Number of background/farm tasks (#177)
    model: Optional[str] = None  # Most recently seen model name (#272)

    @property
    def max_context_tokens(self) -> int:
        """Context window size for the detected model."""
        return model_context_window(self.model)

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


class HistoryFile:
    """Cached reader for Claude Code's history.jsonl.

    All access to history.jsonl should go through this class.  It parses
    the file at most once per mtime+size change, so multiple callers in
    the same update cycle share a single parse.

    Thread-safe: a lock protects the cache so concurrent workers in a
    ThreadPoolExecutor can call methods without re-parsing.
    """

    def __init__(self, history_path: Path = CLAUDE_HISTORY_PATH):
        self._path = history_path
        self._lock = threading.Lock()
        self._cached_mtime: float = 0.0
        self._cached_size: int = 0
        self._cached_entries: List[HistoryEntry] = []
        # Separate cache for backward-read session ID lookups
        self._session_id_cache: Dict[str, Tuple[float, int, Optional[str]]] = {}

    # ── Core cache ────────────────────────────────────────────────────

    def _entries(self) -> List[HistoryEntry]:
        """Return parsed entries, re-parsing only if the file changed."""
        try:
            stat = self._path.stat()
        except OSError:
            return []

        with self._lock:
            if stat.st_mtime == self._cached_mtime and stat.st_size == self._cached_size:
                return self._cached_entries

            entries: List[HistoryEntry] = []
            try:
                with open(self._path, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            entries.append(HistoryEntry(
                                display=data.get("display", ""),
                                timestamp_ms=data.get("timestamp", 0),
                                project=data.get("project"),
                                session_id=data.get("sessionId"),
                            ))
                        except (json.JSONDecodeError, KeyError):
                            continue
            except IOError:
                return []

            self._cached_entries = entries
            self._cached_mtime = stat.st_mtime
            self._cached_size = stat.st_size
            return entries

    # ── Public query methods ──────────────────────────────────────────

    def read_all(self) -> List[HistoryEntry]:
        """Read all entries from history.jsonl (cached)."""
        return list(self._entries())

    def get_interactions_for_session(
        self, session: "Session"
    ) -> List[HistoryEntry]:
        """Get history entries matching a session's directory and time window.

        When the session has known claude_session_ids, filters by sessionId
        to avoid cross-contamination between agents sharing a directory (#264).
        Falls back to directory+timestamp matching for older sessions without
        tracked sessionIds.
        """
        if not session.start_directory:
            return []

        try:
            session_start = datetime.fromisoformat(session.start_time)
            session_start_ms = int(session_start.timestamp() * 1000)
        except (ValueError, TypeError):
            return []

        # Use owned sessionIds when available for precise matching (#264)
        owned_ids = set(getattr(session, 'claude_session_ids', None) or [])

        session_dir = str(Path(session.start_directory).resolve())
        matching = []

        for entry in self._entries():
            if entry.timestamp_ms < session_start_ms:
                continue
            if owned_ids:
                # Precise: only count interactions from this session's own Claude sessions
                if entry.session_id in owned_ids:
                    matching.append(entry)
            elif entry.project:
                # Fallback: directory matching for sessions without tracked IDs
                entry_dir = str(Path(entry.project).resolve())
                if entry_dir == session_dir:
                    matching.append(entry)

        return matching

    def count_interactions(self, session: "Session") -> int:
        """Count interactions for a session."""
        return len(self.get_interactions_for_session(session))

    def get_session_ids_for_session(self, session: "Session") -> List[str]:
        """Get unique Claude Code sessionIds for an overcode session."""
        entries = self.get_interactions_for_session(session)
        session_ids = set()
        for entry in entries:
            if entry.session_id:
                session_ids.add(entry.session_id)
        return sorted(session_ids)

    def get_current_session_id_for_directory(
        self, directory: str, since: datetime
    ) -> Optional[str]:
        """Get the most recent Claude sessionId for a directory.

        Optimized: reads history.jsonl backwards and caches by mtime+size.
        """
        if not self._path.exists():
            return None

        try:
            stat = self._path.stat()
            file_mtime = stat.st_mtime
            file_size = stat.st_size
        except OSError:
            return None

        session_dir = str(Path(directory).resolve())
        cache_key = session_dir

        with self._lock:
            cached = self._session_id_cache.get(cache_key)
            if cached and cached[0] == file_mtime and cached[1] == file_size:
                return cached[2]

        since_ms = int(since.timestamp() * 1000)

        result = None
        for line in _read_lines_reversed(self._path):
            try:
                data = json.loads(line)
                ts = data.get("timestamp", 0)
                if ts < since_ms:
                    break
                project = data.get("project")
                if project:
                    entry_dir = str(Path(project).resolve())
                    if entry_dir == session_dir:
                        sid = data.get("sessionId")
                        if sid:
                            result = sid
                            break
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

        with self._lock:
            self._session_id_cache[cache_key] = (file_mtime, file_size, result)
        return result


def _read_lines_reversed(filepath: Path, max_bytes: int = 64 * 1024) -> List[str]:
    """Read the last chunk of a file and return lines in reverse order.

    Reads up to max_bytes from the end of the file. This is much faster than
    reading the entire file when we only need recent entries.
    """
    try:
        file_size = filepath.stat().st_size
    except OSError:
        return []

    read_size = min(file_size, max_bytes)
    try:
        with open(filepath, 'rb') as f:
            f.seek(max(0, file_size - read_size))
            chunk = f.read().decode('utf-8', errors='replace')
    except IOError:
        return []

    lines = chunk.split('\n')
    # First line may be partial if we didn't read from start — drop it
    if file_size > read_size and lines:
        lines = lines[1:]
    # Return non-empty lines in reverse order
    return [line for line in reversed(lines) if line.strip()]


# ── Module-level singleton for backward-compat free functions ─────────

_default_history = HistoryFile()


def read_history(history_path: Path = CLAUDE_HISTORY_PATH) -> List[HistoryEntry]:
    """Read all entries from history.jsonl.

    Prefer using a HistoryFile instance directly for cached access.
    """
    if history_path == CLAUDE_HISTORY_PATH:
        return _default_history.read_all()
    return HistoryFile(history_path).read_all()


def get_interactions_for_session(
    session: "Session",
    history_path: Path = CLAUDE_HISTORY_PATH
) -> List[HistoryEntry]:
    """Get history entries matching a session.

    Prefer using a HistoryFile instance directly for cached access.
    """
    if history_path == CLAUDE_HISTORY_PATH:
        return _default_history.get_interactions_for_session(session)
    return HistoryFile(history_path).get_interactions_for_session(session)


def count_interactions(
    session: "Session",
    history_path: Path = CLAUDE_HISTORY_PATH
) -> int:
    """Count interactions for a session."""
    return len(get_interactions_for_session(session, history_path))


def get_session_ids_for_session(
    session: "Session",
    history_path: Path = CLAUDE_HISTORY_PATH
) -> List[str]:
    """Get unique Claude Code sessionIds for an overcode session."""
    if history_path == CLAUDE_HISTORY_PATH:
        return _default_history.get_session_ids_for_session(session)
    return HistoryFile(history_path).get_session_ids_for_session(session)


def get_current_session_id_for_directory(
    directory: str,
    since: datetime,
    history_path: Path = CLAUDE_HISTORY_PATH
) -> Optional[str]:
    """Get the most recent Claude sessionId for a directory since a given time.

    Prefer using a HistoryFile instance directly for cached access.
    """
    if history_path == CLAUDE_HISTORY_PATH:
        return _default_history.get_current_session_id_for_directory(directory, since)
    return HistoryFile(history_path).get_current_session_id_for_directory(directory, since)


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
        "model": None,  # Most recently seen model name (#272)
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
                        model = message.get("model")
                        if model:
                            totals["model"] = model
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
    projects_path: Path = CLAUDE_PROJECTS_PATH,
    history_file: Optional["HistoryFile"] = None,
) -> Optional[ClaudeSessionStats]:
    """Get comprehensive stats for an overcode session.

    Combines interaction counting with token usage from session files.

    Session scoping: get_interactions_for_session() is the single source of
    truth for which Claude Code sessions belong to this overcode session.
    When claude_session_ids are tracked, it filters precisely by sessionId;
    otherwise falls back to directory+timestamp matching (#119, #264).

    Context window uses active_claude_session_id after /clear (#116),
    falling back to MAX across all matched sessions.

    Args:
        session: The overcode Session
        history_path: Path to history.jsonl
        projects_path: Path to Claude projects directory
        history_file: Optional HistoryFile for cached access (avoids re-parsing)

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

    # get_interactions_for_session is the single gate for session scoping:
    # uses claude_session_ids when available, else directory+timestamp fallback
    hf = history_file or (
        _default_history if history_path == CLAUDE_HISTORY_PATH
        else HistoryFile(history_path)
    )
    interactions = hf.get_interactions_for_session(session)
    interaction_count = len(interactions)

    # Derive Claude sessionIds from the already-scoped interactions
    session_ids = {e.session_id for e in interactions if e.session_id}

    # Active session ID for context window after /clear (#116)
    active_session_id = getattr(session, 'active_claude_session_id', None)

    # Sum token usage and work times across session files
    total_input = 0
    total_output = 0
    total_cache_creation = 0
    total_cache_read = 0
    current_context = 0
    detected_model: Optional[str] = None
    all_work_times: List[float] = []
    subagent_count = 0  # Count subagent files (#176)
    live_subagent_count = 0  # Subagents with recently-modified files (#256)
    background_task_count = 0  # Count background task files (#177)
    now = time.time()

    for sid in session_ids:
        session_file = get_session_file_path(
            session.start_directory, sid, projects_path
        )
        usage = read_token_usage_from_session_file(session_file, since=session_start)
        total_input += usage["input_tokens"]
        total_output += usage["output_tokens"]
        total_cache_creation += usage["cache_creation_tokens"]
        total_cache_read += usage["cache_read_tokens"]

        # Context & model: prefer active session (#116), fall back to MAX across all
        if active_session_id:
            if sid == active_session_id:
                current_context = usage["current_context_tokens"]
                if usage["model"]:
                    detected_model = usage["model"]
        else:
            if usage["current_context_tokens"] > current_context:
                current_context = usage["current_context_tokens"]
            if usage["model"]:
                detected_model = usage["model"]

        # Collect work times from this session file
        work_times = read_work_times_from_session_file(session_file, since=session_start)
        all_work_times.extend(work_times)

        # Check for subagent files in {sessionId}/subagents/
        encoded = encode_project_path(session.start_directory)
        subagents_dir = projects_path / encoded / sid / "subagents"
        if subagents_dir.exists():
            for subagent_file in subagents_dir.glob("agent-*.jsonl"):
                subagent_count += 1
                if now - subagent_file.stat().st_mtime < 30:
                    live_subagent_count += 1
                sub_usage = read_token_usage_from_session_file(
                    subagent_file, since=session_start
                )
                total_input += sub_usage["input_tokens"]
                total_output += sub_usage["output_tokens"]
                total_cache_creation += sub_usage["cache_creation_tokens"]
                total_cache_read += sub_usage["cache_read_tokens"]

        # Check for background tasks (run_in_background agents) (#177)
        # These are subagents that were started in background mode
        tasks_dir = projects_path / encoded / sid / "tasks"
        if tasks_dir.exists():
            background_task_count += len(list(tasks_dir.glob("task-*.jsonl")))

    return ClaudeSessionStats(
        interaction_count=interaction_count,
        input_tokens=total_input,
        output_tokens=total_output,
        cache_creation_tokens=total_cache_creation,
        cache_read_tokens=total_cache_read,
        work_times=all_work_times,
        current_context_tokens=current_context,
        subagent_count=subagent_count,
        live_subagent_count=live_subagent_count,
        background_task_count=background_task_count,
        model=detected_model,
    )
