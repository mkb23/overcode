"""Stats seam: transcript/usage reading behind a backend-swappable protocol.

Everything overcode knows about an agent's tokens, cost, context window and
session-id lifecycle comes from a ``StatsReader``.  Backends that keep
readable transcripts (Claude Code) return a reader that wraps
``history_reader``; backends that don't return ``NullStatsReader``, whose
"unknown" (``None``) answers render as placeholders instead of misleading
zeros.  See ``docs/design/agent-agnostic-backends-opencode.md`` §2.1, §5.
"""

import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence

from .history_reader import AgentSessionStats

WINDOW_TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_tokens",
    "cache_read_tokens",
)


def empty_window_usage() -> Dict[str, int]:
    """Zeroed window-usage dict, the shape burn-rate math expects."""
    return {key: 0 for key in WINDOW_TOKEN_KEYS}


@dataclass
class DiscoveredSessionIds:
    """Agent session ids found on disk that no other agent owns.

    ``latest`` is the most recently active of ``ids`` — the one that should
    become the active session id.
    """

    ids: List[str] = field(default_factory=list)
    latest: Optional[str] = None


class StatsReader(Protocol):
    """Reads one backend's on-disk telemetry for a session."""

    backend_name: str

    def get_stats(
        self, session: Any, *, history_file: Any = None
    ) -> Optional[AgentSessionStats]: ...

    def get_current_session_id(
        self, session: Any, since: datetime
    ) -> Optional[str]: ...

    def discover_session_ids(
        self, session: Any, since: datetime, all_sessions: Sequence[Any]
    ) -> DiscoveredSessionIds: ...

    def get_window_token_usage(
        self, session: Any, since: datetime
    ) -> Dict[str, int]: ...

    def get_container_stats(self, session: Any) -> Optional[AgentSessionStats]: ...

    def get_stored_cost(self, session: Any) -> Optional[float]:
        """The backend's own cost figure for this agent, if it keeps one.

        Optional: callers use ``getattr(reader, "get_stored_cost", None)`` and
        fall back to recomputing from tokens via ``pricing.py``. Claude Code
        transcripts carry no cost, so only opencode implements it.
        """
        ...


class NullStatsReader:
    """Reader for backends with no readable transcripts.

    Answers "unknown" everywhere: no tokens, no cost, no session ids. The
    daemon writes nothing and the TUI renders placeholders.
    """

    backend_name = ""

    def __init__(self, backend_name: str = "") -> None:
        self.backend_name = backend_name

    def get_stats(
        self, session: Any, *, history_file: Any = None
    ) -> Optional[AgentSessionStats]:
        return None

    def get_current_session_id(
        self, session: Any, since: datetime
    ) -> Optional[str]:
        return None

    def discover_session_ids(
        self, session: Any, since: datetime, all_sessions: Sequence[Any]
    ) -> DiscoveredSessionIds:
        return DiscoveredSessionIds()

    def get_window_token_usage(
        self, session: Any, since: datetime
    ) -> Dict[str, int]:
        return empty_window_usage()

    def get_container_stats(self, session: Any) -> Optional[AgentSessionStats]:
        return None


class ClaudeStatsReader:
    """Reads Claude Code's history.jsonl + project transcript files.

    A thin delegation layer over ``history_reader`` — the parsing itself
    stays there. Imports are function-local so the functions stay
    patchable at ``overcode.history_reader.<name>``.
    """

    backend_name = "claude-code"

    def get_stats(
        self, session: Any, *, history_file: Any = None
    ) -> Optional[AgentSessionStats]:
        from .history_reader import get_session_stats

        if history_file is not None:
            return get_session_stats(session, history_file=history_file)
        return get_session_stats(session)

    def get_current_session_id(
        self, session: Any, since: datetime
    ) -> Optional[str]:
        from .history_reader import get_current_session_id_for_directory

        if not session.start_directory:
            return None
        return get_current_session_id_for_directory(session.start_directory, since)

    def discover_session_ids(
        self, session: Any, since: datetime, all_sessions: Sequence[Any]
    ) -> DiscoveredSessionIds:
        """Scan history.jsonl for all unowned sessionIds in this directory.

        When the prescribed --session-id wasn't honored by Claude Code,
        the agent's actual sessionIds are unknown.  This scans all history
        entries matching the directory+timestamp and adopts any sessionId
        not already owned by another agent.
        """
        from .claude_pid import is_session_id_owned_by_others
        from .history_reader import HistoryFile

        if not session.start_directory:
            return DiscoveredSessionIds()

        hf = HistoryFile()
        session_dir = str(Path(session.start_directory).resolve())
        session_start_ms = int(since.timestamp() * 1000)
        owned_ids = set(session.agent_session_ids or [])

        discovered: List[str] = []
        latest_id = None
        latest_ts = 0
        for entry in hf.read_all():
            if entry.timestamp_ms < session_start_ms:
                continue
            if not entry.project or not entry.session_id:
                continue
            entry_dir = str(Path(entry.project).resolve())
            if entry_dir != session_dir:
                continue
            sid = entry.session_id
            if sid in owned_ids:
                continue
            if is_session_id_owned_by_others(sid, session.id, all_sessions):
                continue
            if sid not in discovered:
                discovered.append(sid)
            if entry.timestamp_ms > latest_ts:
                latest_ts = entry.timestamp_ms
                latest_id = sid

        return DiscoveredSessionIds(ids=discovered, latest=latest_id)

    def get_window_token_usage(
        self, session: Any, since: datetime
    ) -> Dict[str, int]:
        from .history_reader import get_session_window_token_usage

        return get_session_window_token_usage(session, since)

    def get_container_stats(self, session: Any) -> Optional[AgentSessionStats]:
        """Read stats from a container agent via docker exec.

        Reads session JSONL files from inside the container since the
        host filesystem doesn't have them (container uses /workspace path
        encoding, not the host project path).

        Returns None when the container can't be read or has no tokens yet,
        so the caller falls back to the normal host path.
        """
        from .history_reader import read_session_stats_from_content

        if not session.wrapper:
            return None

        container_name = f"overcode-{session.name}"
        active_sid = session.active_agent_session_id
        if not active_sid and session.agent_session_ids:
            active_sid = session.agent_session_ids[-1]
        if not active_sid:
            return None

        # Detect container user's home directory
        try:
            home_result = subprocess.run(
                ["docker", "exec", container_name, "sh", "-c", "echo $HOME"],
                capture_output=True, text=True, timeout=5,
            )
            if home_result.returncode != 0:
                return None
            container_home = home_result.stdout.strip() or "/home/node"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None

        total_input = 0
        total_output = 0
        total_cache_creation = 0
        total_cache_read = 0
        current_context = 0
        detected_model = None
        detected_provider = None
        all_work_times: List[float] = []

        for sid in (session.agent_session_ids or [active_sid]):
            # Claude encodes /workspace as -workspace inside the container
            session_path = f"{container_home}/.claude/projects/-workspace/{sid}.jsonl"
            try:
                result = subprocess.run(
                    ["docker", "exec", container_name, "cat", session_path],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode != 0:
                    continue
            except (subprocess.TimeoutExpired, FileNotFoundError):
                continue

            usage, work_times = read_session_stats_from_content(result.stdout)
            total_input += usage["input_tokens"]
            total_output += usage["output_tokens"]
            total_cache_creation += usage["cache_creation_tokens"]
            total_cache_read += usage["cache_read_tokens"]
            all_work_times.extend(work_times)

            if sid == active_sid:
                current_context = usage["current_context_tokens"]
                if usage["model"]:
                    detected_model = usage["model"]
                if usage["provider"]:
                    detected_provider = usage["provider"]
            elif usage["current_context_tokens"] > current_context:
                current_context = usage["current_context_tokens"]
            if usage["model"] and not detected_model:
                detected_model = usage["model"]
            if usage["provider"] and not detected_provider:
                detected_provider = usage["provider"]

        if total_input + total_output == 0:
            return None

        return AgentSessionStats(
            interaction_count=0,
            input_tokens=total_input,
            output_tokens=total_output,
            cache_creation_tokens=total_cache_creation,
            cache_read_tokens=total_cache_read,
            work_times=all_work_times,
            current_context_tokens=current_context,
            model=detected_model,
            provider=detected_provider,
        )


_READERS: Dict[str, StatsReader] = {}


def stats_reader_for_session(session: Any) -> StatsReader:
    """Resolve the StatsReader for a session's backend.

    Backends without TRANSCRIPT_STATS — and backend names overcode doesn't
    know — get a ``NullStatsReader`` so callers degrade to "unknown"
    instead of reading another backend's files.
    """
    from .backends import (
        BackendCapability,
        UnknownBackendError,
        get_backend,
        session_backend_name,
        supports,
    )

    name = session_backend_name(session)
    reader = _READERS.get(name)
    if reader is not None:
        return reader
    try:
        backend = get_backend(name)
    except UnknownBackendError:
        reader = NullStatsReader(name)
    else:
        if supports(backend, BackendCapability.TRANSCRIPT_STATS):
            reader = backend.make_stats_reader()
        else:
            reader = NullStatsReader(name)
    _READERS[name] = reader
    return reader


def clear_reader_cache() -> None:
    """Drop cached readers. Used by tests that register backend doubles."""
    _READERS.clear()


__all__ = [
    "AgentSessionStats",
    "ClaudeStatsReader",
    "DiscoveredSessionIds",
    "NullStatsReader",
    "StatsReader",
    "clear_reader_cache",
    "empty_window_usage",
    "stats_reader_for_session",
]
