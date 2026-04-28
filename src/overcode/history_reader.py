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
import threading
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING
from dataclasses import dataclass

if TYPE_CHECKING:
    from .session_manager import Session


CLAUDE_HISTORY_PATH = Path.home() / ".claude" / "history.jsonl"
CLAUDE_PROJECTS_PATH = Path.home() / ".claude" / "projects"

# Model name → context window size in tokens.
# Default 200K for unknown models.  Update as new models ship.
# Claude Code with 1M context reports the same model ID — we detect
# the actual context size from token counts at runtime and update here
# for the models known to support extended context.
MODEL_CONTEXT_WINDOWS: Dict[str, int] = {
    "claude-opus-4-7": 1_000_000,
    "claude-opus-4-6": 1_000_000,
    "claude-sonnet-4-6": 1_000_000,
    "claude-sonnet-4-5-20250929": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
    "claude-3-5-sonnet-20241022": 200_000,
    "claude-3-5-haiku-20241022": 200_000,
    "claude-3-opus-20240229": 200_000,
    "claude-3-sonnet-20240229": 200_000,
    "claude-3-haiku-20240307": 200_000,
}
DEFAULT_CONTEXT_WINDOW = 200_000

# Model ID → human-readable short name for display
MODEL_SHORT_NAMES: Dict[str, str] = {
    "claude-opus-4-7": "Op4.7",
    "claude-opus-4-6": "Op4.6",
    "claude-sonnet-4-6": "Sn4.6",
    "claude-sonnet-4-5-20250929": "Sn4.5",
    "claude-haiku-4-5-20251001": "Hk4.5",
    "claude-3-5-sonnet-20241022": "Sn3.5",
    "claude-3-5-haiku-20241022": "Hk3.5",
    "claude-3-opus-20240229": "Op3",
    "claude-3-sonnet-20240229": "Sn3",
    "claude-3-haiku-20240307": "Hk3",
}


def model_short_name(model: Optional[str]) -> str:
    """Return a short display name for a model ID.

    Examples:
        "claude-opus-4-6" → "Op4.6"
        "claude-haiku-4-5-20251001" → "Hk4.5"
        "unknown-model" → "unknown-model"
    """
    if not model:
        return ""
    return MODEL_SHORT_NAMES.get(model, model)


def model_context_window(model: Optional[str]) -> int:
    """Return the context window size for a given model name.

    Falls back to DEFAULT_CONTEXT_WINDOW for unknown/None models.
    """
    if not model:
        return DEFAULT_CONTEXT_WINDOW
    return MODEL_CONTEXT_WINDOWS.get(model, DEFAULT_CONTEXT_WINDOW)


def provider_from_model(model: Optional[str]) -> Optional[str]:
    """Derive API provider from a model ID returned in API responses.

    Older Bedrock model IDs have a dotted prefix (e.g. "us.anthropic.claude-..."),
    while API/Max IDs are plain (e.g. "claude-opus-4-7"). Note that current
    Bedrock responses often return the plain model ID too, so this heuristic
    only catches the dotted case — prefer provider_from_message_id when an
    assistant message ID is available.

    Returns "bedrock" for dotted IDs, "web" for plain, None if unknown/empty.
    """
    if not model:
        return None
    prefix = model.split("claude")[0] if "claude" in model else ""
    return "bedrock" if "." in prefix else "web"


def provider_from_message_id(msg_id: Optional[str]) -> Optional[str]:
    """Derive API provider from an assistant message ID.

    Bedrock responses stamp message IDs with a "msg_bdrk_" prefix; direct
    Anthropic API and Claude.ai OAuth use plain "msg_" IDs. This is more
    reliable than looking at the model field, which Bedrock now returns
    in its plain form (e.g. "claude-opus-4-6").

    Returns "bedrock", "web", or None if the ID doesn't match a known shape.
    """
    if not msg_id:
        return None
    if msg_id.startswith("msg_bdrk_"):
        return "bedrock"
    if msg_id.startswith("msg_"):
        return "web"
    return None


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
    provider: Optional[str] = None  # Detected API provider ("web" or "bedrock")
    last_command: Optional[str] = None  # Most recent user prompt text

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


def synthesize_remote_stats(session) -> "ClaudeSessionStats":
    """Synthesize ClaudeSessionStats for a remote session from daemon_state.

    Remote sessions carry a remote_daemon_state dict with all
    SessionDaemonState fields. Extract what we need so that render
    columns (cost, tokens, context %, model) display correctly.
    """
    rds = getattr(session, 'remote_daemon_state', None) or {}
    stats = session.stats
    mwt = getattr(session, 'remote_median_work_time', None) or rds.get('median_work_time', 0.0)
    return ClaudeSessionStats(
        interaction_count=stats.interaction_count,
        input_tokens=rds.get('input_tokens', stats.total_tokens),
        output_tokens=rds.get('output_tokens', 0),
        cache_creation_tokens=rds.get('cache_creation_tokens', 0),
        cache_read_tokens=rds.get('cache_read_tokens', 0),
        work_times=[mwt] if mwt > 0 else [],
        current_context_tokens=rds.get('current_context_tokens', 0),
        model=rds.get('model'),
        last_command=rds.get('last_command'),
    )


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


def _is_duplicate_subagent(subagent_file: Path) -> bool:
    """Detect subagent files that duplicate parent session messages.

    Claude Code's compaction (``/compact``, auto-compact) and side-question
    (``/btw``) features write conversation logs into subagent files named
    ``agent-acompact-*.jsonl`` or ``agent-aside_question-*.jsonl``.  When
    the first line has ``isMeta: true``, the file is a copy of messages
    already present in the parent session JSONL — counting its tokens
    would double-count spend.

    Small compact files (≤10 lines) without ``isMeta`` are the actual API
    calls Claude Code made to generate the compaction summary.  Those
    represent real, unique token usage and must still be counted.
    """
    name = subagent_file.name
    if not (name.startswith("agent-acompact-") or name.startswith("agent-aside_question-")):
        return False
    # Read only the first line to check isMeta — fast even for huge files
    try:
        with open(subagent_file, 'r') as f:
            first_line = f.readline().strip()
        if not first_line:
            return False
        data = json.loads(first_line)
        return bool(data.get("isMeta"))
    except (IOError, json.JSONDecodeError, TypeError):
        return False


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
    /home/user/.config   -> -home-user--config
    Both '/' and '.' are replaced with '-'.

    Args:
        path: The project path to encode

    Returns:
        Encoded directory name
    """
    resolved = str(Path(path).resolve())
    return resolved.replace("/", "-").replace(".", "-")


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


def _parse_session_lines(
    lines,
    since: Optional[datetime] = None,
) -> Tuple[dict, List[float]]:
    """Parse token usage and work times from session JSONL lines.

    Core parsing logic shared by read_session_file_stats (file-based)
    and read_session_stats_from_content (string-based, for containers).

    Args:
        lines: Iterable of JSONL line strings
        since: Only count data from messages after this time

    Returns:
        (token_usage_dict, work_times_list)
    """
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "current_context_tokens": 0,
        "model": None,
        "provider": None,
    }

    user_prompt_times: List[datetime] = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            msg_type = data.get("type")

            if msg_type == "assistant":
                # Check timestamp if filtering by time
                if since:
                    ts_str = data.get("timestamp")
                    if ts_str:
                        try:
                            msg_time = datetime.fromisoformat(
                                ts_str.replace("Z", "+00:00")
                            ).astimezone().replace(tzinfo=None)
                            if msg_time < since:
                                continue
                        except (ValueError, TypeError):
                            pass

                message = data.get("message", {})
                usage = message.get("usage", {})
                if usage:
                    input_tokens = usage.get("input_tokens", 0)
                    output_tokens = usage.get("output_tokens", 0)
                    cache_read = usage.get("cache_read_input_tokens", 0)
                    cache_creation = usage.get(
                        "cache_creation_input_tokens", 0
                    )
                    totals["input_tokens"] += input_tokens
                    totals["output_tokens"] += output_tokens
                    totals["cache_creation_tokens"] += cache_creation
                    totals["cache_read_tokens"] += cache_read
                    context_size = input_tokens + cache_read
                    if context_size > 0:
                        totals["current_context_tokens"] = context_size
                    # Only track model/provider from messages with actual API
                    # usage (skips synthetic error messages with zero tokens).
                    if input_tokens + output_tokens + cache_creation + cache_read > 0:
                        model = message.get("model")
                        if model:
                            totals["model"] = model
                        detected = provider_from_message_id(message.get("id"))
                        if detected:
                            totals["provider"] = detected

            elif msg_type == "user":
                # Check if this is an actual user prompt (not a tool result)
                message = data.get("message", {})
                content = message.get("content", "")
                if isinstance(content, list):
                    if content and content[0].get("type") == "tool_result":
                        continue

                ts_str = data.get("timestamp")
                if not ts_str:
                    continue

                try:
                    msg_time = datetime.fromisoformat(
                        ts_str.replace("Z", "+00:00")
                    ).astimezone().replace(tzinfo=None)
                    if since and msg_time < since:
                        continue
                    user_prompt_times.append(msg_time)
                except (ValueError, TypeError):
                    continue

        except (json.JSONDecodeError, KeyError, TypeError):
            continue

    # Calculate durations between consecutive prompts
    work_times = []
    for i in range(1, len(user_prompt_times)):
        duration = (user_prompt_times[i] - user_prompt_times[i - 1]).total_seconds()
        if duration > 0:
            work_times.append(duration)

    return totals, work_times


def read_session_file_stats(
    session_file: Path,
    since: Optional[datetime] = None,
) -> Tuple[dict, List[float]]:
    """Read token usage and work times from a session file in a single pass.

    Combines the work of read_token_usage_from_session_file and
    read_work_times_from_session_file so the file is only read once.

    Args:
        session_file: Path to the session JSONL file
        since: Only count data from messages after this time

    Returns:
        (token_usage_dict, work_times_list)
    """
    if not session_file.exists():
        defaults = {
            "input_tokens": 0, "output_tokens": 0,
            "cache_creation_tokens": 0, "cache_read_tokens": 0,
            "current_context_tokens": 0, "model": None,
        }
        return defaults, []

    try:
        with open(session_file, 'r') as f:
            return _parse_session_lines(f, since=since)
    except IOError:
        defaults = {
            "input_tokens": 0, "output_tokens": 0,
            "cache_creation_tokens": 0, "cache_read_tokens": 0,
            "current_context_tokens": 0, "model": None,
        }
        return defaults, []


def read_session_stats_from_content(
    content: str,
    since: Optional[datetime] = None,
) -> Tuple[dict, List[float]]:
    """Read token usage and work times from session JSONL content string.

    Same as read_session_file_stats but accepts string content instead
    of a file path.  Used for reading session files from containers
    via docker exec.

    Args:
        content: JSONL content as a string
        since: Only count data from messages after this time

    Returns:
        (token_usage_dict, work_times_list)
    """
    if not content or not content.strip():
        defaults = {
            "input_tokens": 0, "output_tokens": 0,
            "cache_creation_tokens": 0, "cache_read_tokens": 0,
            "current_context_tokens": 0, "model": None,
        }
        return defaults, []

    return _parse_session_lines(content.splitlines(), since=since)


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
    totals, _ = read_session_file_stats(session_file, since)
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
    _, work_times = read_session_file_stats(session_file, since)
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

    # Parse session start time for filtering.
    # session.start_time is local time (naive), but Claude Code session files
    # store timestamps in UTC.  Convert to UTC-naive for correct comparison.
    try:
        session_start_local = datetime.fromisoformat(session.start_time)
        session_start = session_start_local.astimezone(timezone.utc).replace(tzinfo=None)
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

    # Derive Claude sessionIds and their project paths from interactions.
    # Claude Code may store session files under a different project path
    # than start_directory (e.g., when the directory doesn't exist or Claude
    # chooses a different project root).
    session_ids = {e.session_id for e in interactions if e.session_id}
    sid_to_project: Dict[str, str] = {}
    for e in interactions:
        if e.session_id and e.project:
            sid_to_project[e.session_id] = e.project

    # Active session ID for context window after /clear (#116)
    active_session_id = getattr(session, 'active_claude_session_id', None)

    # Sum token usage and work times across session files
    total_input = 0
    total_output = 0
    total_cache_creation = 0
    total_cache_read = 0
    current_context = 0
    detected_model: Optional[str] = None
    detected_provider: Optional[str] = None
    all_work_times: List[float] = []
    subagent_count = 0  # Count subagent files (#176)
    live_subagent_count = 0  # Subagents with recently-modified files (#256)
    background_task_count = 0  # Count background task files (#177)
    now = time.time()

    for sid in session_ids:
        session_file = get_session_file_path(
            session.start_directory, sid, projects_path
        )
        # Fall back to the project path from history entries if the session
        # file doesn't exist at the expected start_directory path.  Claude
        # Code may use a different project root (e.g. home dir) when the
        # launch directory no longer exists.
        if not session_file.exists():
            alt_project = sid_to_project.get(sid)
            if alt_project:
                session_file = get_session_file_path(
                    alt_project, sid, projects_path
                )
        usage, work_times = read_session_file_stats(session_file, since=session_start)
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
                if usage["provider"]:
                    detected_provider = usage["provider"]
        else:
            if usage["current_context_tokens"] > current_context:
                current_context = usage["current_context_tokens"]
            if usage["model"]:
                detected_model = usage["model"]
            if usage["provider"]:
                detected_provider = usage["provider"]

        # Collect work times from this session file
        all_work_times.extend(work_times)

        # Check for subagent files in {sessionId}/subagents/
        # Use the actual project path where the session file was found.
        actual_project = sid_to_project.get(sid, session.start_directory)
        encoded = encode_project_path(actual_project)
        subagents_dir = projects_path / encoded / sid / "subagents"
        if subagents_dir.exists():
            for subagent_file in subagents_dir.glob("agent-*.jsonl"):
                # Skip duplicate conversation logs from compaction/side-question
                # subagents. Claude Code writes these with isMeta=True and they
                # contain copies of messages already in the parent session file.
                # See docs/claude-session-files.md for details.
                if _is_duplicate_subagent(subagent_file):
                    continue
                subagent_count += 1
                if now - subagent_file.stat().st_mtime < 30:
                    live_subagent_count += 1
                sub_usage, _ = read_session_file_stats(
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

    # Extract last command from history interactions
    last_command = None
    if interactions:
        last_entry = interactions[-1]
        if last_entry.display:
            last_command = last_entry.display

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
        provider=detected_provider,
        last_command=last_command,
    )
