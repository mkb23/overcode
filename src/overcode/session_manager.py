"""
Session state management for Overcode.
"""

import functools
import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager
from typing import Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple
from dataclasses import MISSING, dataclass, asdict, field, fields, replace
import uuid
import time

from .exceptions import StateWriteError
from .stat_gate import FileSignature, StatGatedCache


_HEADS_PREFIX = "ref: refs/heads/"


def _looks_like_object_id(text: str) -> bool:
    return len(text) in (40, 64) and all(c in "0123456789abcdef" for c in text)


def read_git_context_from_disk(directory: str) -> Optional[tuple[Optional[str], Optional[str]]]:
    """Return ``(repo_name, branch)`` for ``directory`` by reading ``.git`` files.

    A subprocess-free equivalent of ``git rev-parse --show-toplevel`` plus
    ``git branch --show-current``, for the monitor daemon's per-agent
    per-tick refresh:

    - walks up from ``directory`` to the nearest ``.git`` — a directory, or
      the ``gitdir: <path>`` file that worktrees and submodules use;
    - ``repo_name`` is the name of the directory holding that ``.git``
      (the worktree root, exactly what ``--show-toplevel`` reports);
    - ``branch`` is the name behind ``ref: refs/heads/``, or ``""`` for a
      detached HEAD / non-branch ref (``--show-current`` prints nothing).

    Returns ``(None, None)`` when no repository encloses ``directory`` and
    ``None`` when the layout is unrecognised or unreadable — the caller
    then falls back to asking git itself.
    """
    try:
        path = Path(directory).resolve()
    except OSError:
        return None

    for candidate in (path, *path.parents):
        dot_git = candidate / ".git"
        try:
            if dot_git.is_dir():
                git_dir = dot_git
            elif dot_git.is_file():
                text = dot_git.read_text().strip()
                if not text.startswith("gitdir:"):
                    return None
                git_dir = Path(text[len("gitdir:"):].strip())
                if not git_dir.is_absolute():
                    git_dir = (candidate / git_dir).resolve()
                if not git_dir.is_dir():
                    return None
            else:
                continue
            content = (git_dir / "HEAD").read_text().strip()
        except OSError:
            return None

        if content.startswith(_HEADS_PREFIX):
            branch = content[len(_HEADS_PREFIX):]
        elif content.startswith("ref: ") or _looks_like_object_id(content):
            branch = ""
        else:
            return None
        return candidate.name, branch

    return None, None

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    # Windows doesn't have fcntl
    HAS_FCNTL = False


@dataclass
class SessionStats:
    """Runtime statistics for an agent session"""
    interaction_count: int = 0
    estimated_cost_usd: float = 0.0
    total_tokens: int = 0
    operation_times: List[float] = field(default_factory=list)  # seconds per operation
    steers_count: int = 0  # number of overcode interventions
    last_activity: Optional[str] = None  # ISO timestamp
    current_task: str = "Initializing..."  # one-sentence description

    # Token breakdown (persisted from the backend's transcripts)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    current_context_tokens: int = 0  # Current context window usage
    last_stats_update: Optional[str] = None  # ISO timestamp of last stats sync

    # State tracking
    current_state: str = "running"  # running, waiting_user, waiting_approval, waiting_heartbeat
    state_since: Optional[str] = None  # ISO timestamp when current state started
    green_time_seconds: float = 0.0  # time spent in "running" state
    non_green_time_seconds: float = 0.0  # time spent in non-running states
    sleep_time_seconds: float = 0.0  # time spent in "asleep" state
    last_time_accumulation: Optional[str] = None  # ISO timestamp when times were last accumulated

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'SessionStats':
        """Create SessionStats from dict, handling unknown/invalid fields gracefully."""
        # Get valid field names from the dataclass
        valid_fields = {f.name for f in fields(cls)}
        # Filter to only known fields to avoid TypeError on unknown keys
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        try:
            return cls(**filtered)
        except TypeError:
            # If still failing (wrong types), return defaults
            return cls()


# Phase 6 rename map: canonical field name → pre-Phase-6 (Claude-flavoured)
# name. Drives the ``Session`` constructor/attribute aliases, the ``to_dict``
# dual-write and the ``from_dict`` read migration, plus the equivalent
# aliasing in ``SessionManager.update_session``.
LEGACY_SESSION_KEYS = {
    "agent_session_ids": "claude_session_ids",
    "active_agent_session_id": "active_claude_session_id",
    "extra_cli_args": "extra_claude_args",
    "agent_persona": "claude_agent",
}

# Reverse lookup: old name → canonical name.
CANONICAL_SESSION_KEYS = {old: new for new, old in LEGACY_SESSION_KEYS.items()}


@dataclass
class Session:
    """Represents one agent session (Claude Code, opencode, …)"""
    id: str
    name: str
    tmux_session: str
    tmux_window: str
    command: List[str]
    start_directory: Optional[str]
    start_time: str

    # Git context
    repo_name: Optional[str] = None
    branch: Optional[str] = None
    pr_number: Optional[int] = None
    pr_branch: Optional[str] = None

    # Management
    status: str = "running"
    permissiveness_mode: str = "normal"  # normal, permissive, bypass
    standing_instructions: str = ""  # e.g., "keep herding it on to completion"
    standing_instructions_preset: Optional[str] = None  # preset name if using library preset
    standing_orders_complete: bool = False  # True when supervisor marks orders as done

    # Statistics
    stats: SessionStats = field(default_factory=SessionStats)

    # Sleep mode - agent is paused and excluded from stats
    is_asleep: bool = False

    # Enhanced context hook - per-agent toggle for enhanced context injection
    enhanced_context_enabled: bool = False

    # Agent value - priority indicator for sorting/attention (#61)
    # Default 1000, higher = more important
    agent_value: int = 1000

    # Human annotation - user's notes about this agent (#74)
    human_annotation: str = ""

    # Names this agent had before `overcode rename`, oldest first (#478).
    # Name lookups fall back to these (resolve_session_name) so a parent,
    # the supervisor or a script still holding an old name reaches the
    # agent — with a notice — instead of "not found". An old name belongs
    # to one agent at a time, and a live agent's own name always wins.
    previous_names: List[str] = field(default_factory=list)

    # Backend session ids owned by this overcode session (#119)
    # Used to accurately calculate context window for this specific agent
    agent_session_ids: List[str] = field(default_factory=list)

    # The currently active backend session ID (#116)
    # Replaced (not appended) when /clear creates a new session.
    # Used for context window calculation — only the active session matters.
    active_agent_session_id: Optional[str] = None

    # Heartbeat configuration (#171)
    heartbeat_enabled: bool = False
    heartbeat_frequency_seconds: int = 300  # Default 5 minutes
    heartbeat_instruction: str = ""
    heartbeat_paused: bool = False
    last_heartbeat_time: Optional[str] = None  # ISO timestamp

    # Cost budget (#173) - 0.0 means no budget/unlimited
    cost_budget_usd: float = 0.0

    # Hook-based status detection - per-agent toggle (#5).
    # False = never use hooks for this agent (web API's hook-detection switch).
    hook_status_detection: bool = True
    # Per-agent detection mode override: "hooks", "polling", or None to
    # follow the fleet default. Written by the TUI's K hotkey; resolved by
    # status_detector_factory.resolve_session_detection_mode.
    detection_mode_override: Optional[str] = None

    # Skills loaded during this session (#252)
    loaded_skills: List[str] = field(default_factory=list)
    available_skills: List[str] = field(default_factory=list)

    # Agent CLI flag passthrough (#290)
    allowed_tools: Optional[str] = None  # Comma-separated tool list for --allowedTools
    extra_cli_args: List[str] = field(default_factory=list)  # Extra CLI flags via --backend-arg
    agent_teams: bool = False  # Claude Code agent teams mode (#309)
    agent_persona: Optional[str] = None  # Agent persona (--agent), e.g. .claude/agents/
    model: Optional[str] = None  # Model (e.g. "sonnet", "opus", or "openai/gpt-4o-mini")
    effort: Optional[str] = None  # Reasoning effort, detected from the backend's store (#497)
    provider: str = "web"  # API provider: "web" (Claude.ai OAuth) or "bedrock" (AWS Bedrock)
    backend: str = "claude-code"  # Agent CLI backend (see overcode.backends)
    wrapper: Optional[str] = None  # Wrapper script path (wraps claude invocation)
    sandbox_enabled: Optional[bool] = None  # Live /sandbox state, None = unknown

    # Resource usage (summed over the claude process tree).
    # Updated each daemon tick by _sync_process_resources.
    cpu_percent: float = 0.0  # Sum of per-CPU %; >100 means multi-core
    rss_bytes: int = 0        # Resident set size in bytes

    # Agent hierarchy (#244) - parent/child relationships
    parent_session_id: Optional[str] = None  # ID of parent agent (None = root)

    # User-applied tags for grouping/filtering (#356).
    # Lower-cased on write so lookups can be case-insensitive without storing
    # multiple casings of the same logical tag.
    tags: List[str] = field(default_factory=list)

    # Multi-repo focal subdir (#170). When the agent's start_directory is a
    # workspace containing several sibling git repos, ``focal_repo_subdir``
    # picks which one repo_name / branch / git_diff / untracked are sampled
    # from. None means "no multi-repo, use start_directory directly".
    focal_repo_subdir: Optional[str] = None

    # Oversight system - report + timeout for child agents
    oversight_policy: str = "wait"  # wait | fail | timeout
    oversight_timeout_seconds: float = 0.0  # 0 = indefinite
    oversight_deadline: Optional[str] = None  # ISO timestamp, set on entering waiting_oversight
    report_status: Optional[str] = None  # "success" | "failure"
    report_reason: str = ""

    # Sister integration (#245) - remote agents from other machines
    is_remote: bool = False
    source_host: str = ""
    source_url: str = ""  # Sister web server URL (for sending control commands)
    source_api_key: str = ""  # Sister API key (for authentication)
    pane_content: str = ""  # Cached pane content from remote API (empty for local sessions)
    remote_git_diff: Optional[tuple] = None  # (files, insertions, deletions) from remote API
    remote_git_untracked: Optional[int] = None  # Untracked file count from remote API (#455)
    remote_median_work_time: float = 0.0  # Median work time from remote API
    remote_activity_summary: str = ""  # AI summary from remote summarizer
    remote_activity_summary_context: str = ""  # AI context summary from remote summarizer
    remote_daemon_state: Optional[dict] = None  # Raw daemon state dict from sister API (for generic forwarding)

    # SSH connectivity for remote agents
    source_ssh: str = ""  # SSH target (e.g., "user@host") for tmux attach
    source_tmux_session: str = ""  # Remote tmux session name (default: "agents")

    # Overcode build that launched this agent, e.g. "0.4.0 (ff82801-dirty)".
    # Lets us tell which code path spawned the agent — and whether it predates
    # feature changes like --settings hook injection (#435) — without digging
    # through tmux pane history.
    launcher_version: str = ""

    # ---- Pre-Phase-6 attribute names -------------------------------------
    # The fields were renamed to be backend-neutral; these keep every
    # existing ``session.claude_session_ids`` reader and writer working.

    @property
    def claude_session_ids(self) -> List[str]:
        return self.agent_session_ids

    @claude_session_ids.setter
    def claude_session_ids(self, value: List[str]) -> None:
        self.agent_session_ids = value

    @property
    def active_claude_session_id(self) -> Optional[str]:
        return self.active_agent_session_id

    @active_claude_session_id.setter
    def active_claude_session_id(self, value: Optional[str]) -> None:
        self.active_agent_session_id = value

    @property
    def extra_claude_args(self) -> List[str]:
        return self.extra_cli_args

    @extra_claude_args.setter
    def extra_claude_args(self, value: List[str]) -> None:
        self.extra_cli_args = value

    @property
    def claude_agent(self) -> Optional[str]:
        return self.agent_persona

    @claude_agent.setter
    def claude_agent(self, value: Optional[str]) -> None:
        self.agent_persona = value

    def to_dict(self) -> dict:
        # asdict() recursively converts nested dataclasses (stats)
        data = asdict(self)
        # Emit the pre-Phase-6 keys alongside the new ones for one release,
        # so an older overcode (or an older sister) reading this state file
        # still finds the fields where it expects them.
        for new_key, old_key in LEGACY_SESSION_KEYS.items():
            data[old_key] = data[new_key]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Optional['Session']:
        """Create Session from dict, handling unknown/invalid fields gracefully.

        Returns None if required fields are missing or data is corrupt.
        Uses dataclasses.fields() to auto-detect required fields and valid keys.
        Never mutates ``data``: ``SessionManager`` hands the same parsed dict
        to every reader until the state file changes.
        """
        cls_fields = fields(cls)

        # Required = fields with no default and no default_factory
        required = {
            f.name for f in cls_fields
            if f.default is MISSING and f.default_factory is MISSING  # type: ignore[comparison-overlap]
        }
        if not all(k in data for k in required):
            return None

        # Filter to only known fields
        valid_fields = {f.name for f in cls_fields}
        filtered = {k: v for k, v in data.items() if k in valid_fields}

        # Handle stats separately (nested dataclass needs manual conversion)
        if 'stats' in data and isinstance(data['stats'], dict):
            stats_data = data['stats']
            # Backward compat: migrate stats.model → session.model
            stats_model = stats_data.get('model')
            if stats_model and not data.get('model'):
                filtered['model'] = stats_model
            filtered['stats'] = SessionStats.from_dict(stats_data)
        elif 'stats' not in data:
            filtered['stats'] = SessionStats()

        # Backward compat: convert int tmux_window to str
        if 'tmux_window' in filtered and isinstance(filtered['tmux_window'], int):
            filtered['tmux_window'] = str(filtered['tmux_window'])

        # Backward compat: migrate time_context_enabled → enhanced_context_enabled (#378)
        if 'enhanced_context_enabled' not in filtered and 'time_context_enabled' in data:
            filtered['enhanced_context_enabled'] = data['time_context_enabled']

        # Backward compat: pre-Phase-6 Claude-flavoured field names. A state
        # file written by an older overcode carries only the old keys; the new
        # key wins whenever both are present.
        for new_key, old_key in LEGACY_SESSION_KEYS.items():
            if new_key not in filtered and old_key in data:
                filtered[new_key] = data[old_key]

        try:
            return cls(**filtered)
        except TypeError:
            # Type mismatch or other issue - session is corrupt
            return None


def _accept_legacy_kwargs(init):
    """Let ``Session(...)`` still be constructed with the pre-Phase-6 names."""

    @functools.wraps(init)
    def wrapper(self, *args, **kwargs):
        for old_key, new_key in CANONICAL_SESSION_KEYS.items():
            if old_key in kwargs:
                value = kwargs.pop(old_key)
                kwargs.setdefault(new_key, value)
        init(self, *args, **kwargs)

    return wrapper


Session.__init__ = _accept_legacy_kwargs(Session.__init__)

_SESSION_FIELD_NAMES = frozenset(f.name for f in fields(Session))
_STATS_FIELD_NAMES = frozenset(f.name for f in fields(SessionStats))


def _with_legacy_keys(kwargs: Dict[str, object]) -> Dict[str, object]:
    """The keys ``update_session`` writes for ``kwargs``.

    A pre-Phase-6 name is folded onto its canonical field (the canonical
    one wins when both are given), and every renamed field is written
    under both names so a state file stays readable by an older overcode
    for one release.
    """
    out = dict(kwargs)
    for old_key, new_key in CANONICAL_SESSION_KEYS.items():
        if old_key in out:
            out.setdefault(new_key, out.pop(old_key))
            out.pop(old_key, None)
    for new_key, old_key in LEGACY_SESSION_KEYS.items():
        if new_key in out:
            out[old_key] = out[new_key]
    return out


class _StateTxn:
    """What ``SessionManager._state_transaction`` yields: the parsed state and
    whether it must be written back."""

    __slots__ = ("state", "dirty")

    def __init__(self, state: Dict[str, dict]):
        self.state = state
        self.dirty = False


class PendingUpdates:
    """A tick's worth of session mutations, committed in one read-modify-write.

    The monitor daemon used to persist each change as it found it — the
    current task, the state-time accumulators, a branch, a PR number, a
    token count — and each was a full parse plus an fsync'd rewrite of
    ``sessions.json`` under the exclusive lock, twice per agent per tick
    (audit R5). The tick now stages everything here and
    ``SessionManager.commit_pending`` writes the file once, and only when a
    staged value differs from what is on disk.

    Staged values are also what the rest of the tick reads: ``view``
    returns a session with the staged changes applied, so code that used to
    re-read the file to see its own earlier write sees the same values
    from memory. ``update_session`` mirrors the manager's method, legacy
    key aliasing included, so what lands on disk is identical.
    """

    def __init__(self) -> None:
        self.fields: Dict[str, Dict[str, object]] = {}
        self.stats: Dict[str, Dict[str, object]] = {}
        self.archive: List[str] = []  # ids to move from the live file to the archive

    def __bool__(self) -> bool:
        return bool(self.fields or self.stats or self.archive)

    def archive_session(self, session_id: str) -> None:
        """Stage moving ``session_id`` to the archive (the ``delete_session`` record)."""
        if session_id not in self.archive:
            self.archive.append(session_id)

    def update_session(self, session_id: str, **kwargs) -> None:
        self.fields.setdefault(session_id, {}).update(_with_legacy_keys(kwargs))

    def update_session_status(self, session_id: str, status: str) -> None:
        self.update_session(session_id, status=status)

    def update_stats(self, session_id: str, **stats_kwargs) -> None:
        self.stats.setdefault(session_id, {}).update(stats_kwargs)

    def view(self, session: Session) -> Session:
        """``session`` with its staged changes applied.

        A copy when anything is staged for it — the snapshot object is
        never touched — and the object itself otherwise.
        """
        fields_ = self.fields.get(session.id)
        stats_ = self.stats.get(session.id)
        if not fields_ and not stats_:
            return session
        changes = {k: v for k, v in (fields_ or {}).items() if k in _SESSION_FIELD_NAMES}
        if stats_:
            changes["stats"] = replace(
                session.stats, **{k: v for k, v in stats_.items() if k in _STATS_FIELD_NAMES}
            )
        return replace(session, **changes)


class SessionIndex:
    """Parent/child lookups over one snapshot of the session table (#244).

    The monitor daemon publishes ``parent_name``, ``depth`` and
    ``children_count`` for every session every tick. Answering those through
    ``get_session`` / ``compute_depth`` / ``get_children`` costs, per session,
    a stat per ancestor and a scan of every entry (``get_children`` walks the
    whole table), so a tick was O(agents x entries) even with nothing
    changed (audit R5). Built once per tick from the snapshot the tick
    already holds, each answer is a dict lookup; the values are exactly what
    the per-call methods return over the same snapshot, and
    ``SessionManager.get_parent_chain`` is this class's walk.
    """

    def __init__(self, by_id: Mapping[str, "Session"]):
        self.by_id = by_id
        self._children_count: Optional[Counter] = None

    @classmethod
    def of(cls, sessions: Iterable["Session"]) -> "SessionIndex":
        return cls({s.id: s for s in sessions})

    def parent_name(self, session: "Session") -> Optional[str]:
        """Name of ``session``'s parent, or None for a root or a missing parent."""
        if not session.parent_session_id:
            return None
        parent = self.by_id.get(session.parent_session_id)
        return parent.name if parent else None

    def parent_chain(self, session_id: str) -> List["Session"]:
        """Ancestors from the immediate parent up to the root (cycle-safe)."""
        chain: List[Session] = []
        current_id = session_id
        visited = set()
        while current_id and current_id not in visited:
            visited.add(current_id)
            session = self.by_id.get(current_id)
            if not session or not session.parent_session_id:
                break
            parent = self.by_id.get(session.parent_session_id)
            if parent:
                chain.append(parent)
                current_id = parent.id
            else:
                break
        return chain

    def depth(self, session: "Session") -> int:
        """Depth in the hierarchy (0 = root), as ``compute_depth`` reports it."""
        return len(self.parent_chain(session.id))

    def children_count(self, session_id: str) -> int:
        """``len(get_children(session_id))``; the count is built on first use."""
        if self._children_count is None:
            self._children_count = Counter(
                s.parent_session_id for s in self.by_id.values() if s.parent_session_id
            )
        return self._children_count.get(session_id, 0)


def _archive_record(line: bytes) -> Optional[dict]:
    """The record on one archive line, or None for a blank or damaged line."""
    if not line.strip():
        return None
    try:
        record = json.loads(line)
    except ValueError:
        return None
    if isinstance(record, dict) and isinstance(record.get("id"), str):
        return record
    return None


def _parse_archive_lines(data: bytes) -> Tuple[Dict[str, dict], int]:
    """Records by id from archive bytes, and the offset after the last complete line.

    Bytes after the final newline are an unfinished line (a crashed
    append) and are left for the next read. A repeated id keeps the last
    record at the position of the first, as ``json.load`` of the old dict
    did.
    """
    records: Dict[str, dict] = {}
    end = data.rfind(b"\n") + 1
    for line in data[:end].split(b"\n"):
        record = _archive_record(line)
        if record is not None:
            records[record["id"]] = record
    return records, end


def _archived_session(record: dict) -> Optional["Session"]:
    """The ``Session`` for an archive record, with ``end_time`` kept as an attribute."""
    try:
        data = dict(record)
        end_time = data.pop('end_time', None)  # not a Session field
        session = Session.from_dict(data)
        if session is None:
            return None
        session._end_time = end_time  # type: ignore
        return session
    except (KeyError, TypeError):
        return None


def rename_notice(requested: str, session: Optional["Session"]) -> Optional[str]:
    """The line to show when ``requested`` reached ``session`` through a rename.

    ``SessionManager.resolve_session_name`` follows old names; a caller that
    used one gets its answer plus this notice, so a person or agent holding
    a stale name learns the new one (#478). None when no rename was involved.
    """
    if session is None or session.name == requested:
        return None
    return f"note: agent '{requested}' was renamed to '{session.name}'"


class SessionManager:
    """Manages session state persistence.

    For testing, pass a custom state_dir (temp directory) and skip_git_detection=True.
    """

    def __init__(self, state_dir: Optional[Path] = None, skip_git_detection: bool = False):
        """Initialize the session manager.

        Args:
            state_dir: Directory for state files (defaults to ~/.overcode/sessions)
            skip_git_detection: If True, skip git repo/branch detection (for testing)
        """
        if state_dir is None:
            # Support OVERCODE_STATE_DIR env var for testing
            env_state_dir = os.environ.get("OVERCODE_STATE_DIR")
            if env_state_dir:
                state_dir = Path(env_state_dir) / "sessions"
            else:
                state_dir = Path.home() / ".overcode" / "sessions"
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.state_dir / "sessions.json"
        # Append-only, one JSON record per line; a legacy archive.json is
        # migrated into it on first access (see _migrate_legacy_archive).
        self.archive_file = self.state_dir / "archive.jsonl"
        self.legacy_archive_file = self.state_dir / "archive.json"
        # (inode, byte offset after the last complete line, Session by id)
        # of the last archive parse: the file only grows, so the next parse
        # reads from the offset and reuses those objects.
        self._archive_tail: Tuple[Optional[int], int, Dict[str, Session]] = (None, 0, {})
        self._skip_git_detection = skip_git_detection
        # The Session objects built from the last parse of each file, keyed
        # by entry id and reused while the file's stat signature is unchanged
        # (audit R4). Per instance: the daemon and each TUI hold their own
        # manager, and the TUI's workers call list_sessions() five to six
        # times a second against a file that changes only when something
        # writes it.
        self._state_cache: StatGatedCache[Dict[str, Session]] = StatGatedCache()
        self._archive_cache: StatGatedCache[Dict[str, Session]] = StatGatedCache()

    def _load_state(self) -> Dict[str, dict]:
        """Load all sessions from the state file as a fresh, private dict.

        Uncached on purpose: a caller that wants the raw entries gets its own
        copy to do with as it likes. Readers of ``Session`` objects go
        through :meth:`_snapshot`, which parses only when ``sessions.json``
        changed (see :mod:`overcode.stat_gate`); writers go through
        :meth:`_locked_state`, which re-reads under the exclusive lock.
        """
        return self._read_state_file()[1]

    def _snapshot(self) -> Dict[str, Session]:
        """The ``Session`` objects built from the state file, by entry id.

        Built once per change of ``sessions.json`` and handed to every
        ``get_session`` / ``list_sessions`` call until the file changes; a
        call in between costs one ``os.stat``.
        """
        return self._state_cache.get(self.state_file, self._parse_state_file)

    def _parse_state_file(self) -> Tuple[Optional[FileSignature], Dict[str, Session]]:
        sig, state = self._read_state_file()
        by_id: Dict[str, Session] = {}
        # Pop each entry as its Session is built. The snapshot keeps the
        # objects, not the raw dicts, and releasing those as we go keeps
        # the cyclic GC's traversals during a 20,000-entry parse short:
        # measured 8% off the cold parse at the power scale, and the
        # retained snapshot is half the size.
        for key in list(state):
            session = Session.from_dict(state.pop(key))
            if session is not None:  # skips corrupted entries
                by_id[key] = session
        return sig, by_id

    def _read_state_file(self) -> Tuple[Optional[FileSignature], Dict[str, dict]]:
        """Read and parse the state file under a shared lock — uncached.

        Also returns the ``fstat`` signature of the bytes parsed, taken while
        the shared lock is held so an in-place writer cannot slip between
        the two; it is ``None`` whenever the result did not come from one
        clean read of the file (missing file, backup restore, give-up after
        retries), so the caller never remembers such a result.

        On JSON corruption, attempts to restore from backup automatically.
        """
        if not self.state_file.exists():
            return None, {}

        max_retries = 5
        retry_delay = 0.1

        for attempt in range(max_retries):
            try:
                with open(self.state_file, 'r') as f:
                    if HAS_FCNTL:
                        # Acquire shared lock for reading
                        fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                        try:
                            sig = FileSignature.of(os.fstat(f.fileno()))
                            return sig, json.load(f)
                        finally:
                            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    else:
                        # No locking on Windows
                        sig = FileSignature.of(os.fstat(f.fileno()))
                        return sig, json.load(f)
            except json.JSONDecodeError as e:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                # JSON corruption detected - try to restore from backup
                print(f"Warning: State file corrupted: {e}")
                if self.restore_from_backup():
                    print("Restored sessions from backup file")
                    # Try loading the restored file
                    try:
                        with open(self.state_file, 'r') as f:
                            return None, json.load(f)
                    except json.JSONDecodeError:
                        print("Warning: Backup file also corrupted, starting fresh")
                        return None, {}
                else:
                    print("Warning: No backup available, starting fresh")
                    return None, {}
            except IOError as e:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                print(f"Warning: Could not load state file: {e}")
                return None, {}

        return None, {}

    def _backup_state(self) -> None:
        """Create a backup of the current state file before writing."""
        if not self.state_file.exists():
            return

        backup_file = self.state_file.with_suffix('.json.bak')
        try:
            import shutil
            shutil.copy2(self.state_file, backup_file)
        except (OSError, IOError):
            # Backup is best-effort, don't fail the write
            pass

    def restore_from_backup(self) -> bool:
        """Restore state from backup file if available.

        Returns:
            True if backup was restored, False otherwise
        """
        backup_file = self.state_file.with_suffix('.json.bak')
        if not backup_file.exists():
            return False

        try:
            import shutil
            shutil.copy2(backup_file, self.state_file)
            return True
        except (OSError, IOError):
            return False

    def _save_state(self, state: Dict[str, dict]):
        """Save all sessions to state file with file locking and atomic writes"""
        import threading
        max_retries = 5
        retry_delay = 0.1

        # Create backup before writing
        self._backup_state()

        for attempt in range(max_retries):
            try:
                if HAS_FCNTL:
                    # Use atomic write with exclusive lock
                    # Use unique temp file name to avoid race conditions
                    temp_suffix = f'.tmp.{os.getpid()}.{threading.get_ident()}'
                    temp_file = self.state_file.with_suffix(temp_suffix)
                    try:
                        with open(temp_file, 'w') as f:
                            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                            try:
                                json.dump(state, f, indent=2)
                                f.flush()
                                os.fsync(f.fileno())
                            finally:
                                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                        # Atomic rename
                        temp_file.rename(self.state_file)
                    finally:
                        # Clean up temp file if rename failed
                        if temp_file.exists():
                            temp_file.unlink()
                else:
                    # No locking on Windows, just write
                    with open(self.state_file, 'w') as f:
                        json.dump(state, f, indent=2)
                return
            except (IOError, OSError) as e:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                raise StateWriteError(f"Failed to save state file after {max_retries} attempts: {e}")

    @contextmanager
    def _locked_state(self):
        """Load state under file lock, yield it, save on exit.

        Holds an exclusive lock for the entire read-modify-write cycle,
        preventing TOCTOU race conditions. The yielded dict is written
        back to the state file when the context manager exits normally.
        """
        with self._state_transaction() as txn:
            yield txn.state
            txn.dirty = True

    @contextmanager
    def _state_transaction(self):
        """Read-modify-write under the exclusive lock, writing only if dirty.

        Yields a ``_StateTxn`` whose ``state`` is the parsed file; the file
        is rewritten (one dump, one fsync) on exit only if the caller set
        ``dirty``. ``_locked_state`` always does; ``commit_pending`` does
        only when a staged value differs from what is on disk, so a tick
        with nothing to change costs a parse and no write.
        """
        if not HAS_FCNTL:
            # No locking on Windows - fall back to read/modify/write
            _, state = self._read_state_file()
            txn = _StateTxn(state)
            yield txn
            if txn.dirty:
                self._save_state(state)
            return

        max_retries = 5
        retry_delay = 0.1
        f = None

        for attempt in range(max_retries):
            try:
                # Use 'a+' to create file if missing, then seek to start
                f = open(self.state_file, 'a+')
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                f.seek(0)
                content = f.read()
                state = json.loads(content) if content.strip() else {}
                break
            except (IOError, OSError, json.JSONDecodeError) as e:
                if f is not None:
                    try:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
                    f.close()
                    f = None
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                raise StateWriteError(f"Failed to load state after {max_retries} attempts: {e}")

        try:
            txn = _StateTxn(state)
            yield txn
            if txn.dirty:
                f.seek(0)
                f.truncate()
                json.dump(state, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
        except (IOError, OSError) as e:
            raise StateWriteError(f"Failed to save state: {e}")
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            f.close()

    def commit_pending(self, pending: "PendingUpdates") -> bool:
        """Apply a tick's staged mutations in one read-modify-write.

        Every staged field and stat is compared with the entry on disk and
        the file is rewritten — one ``json.dump``, one fsync — only if at
        least one differs. Entries that vanished since they were staged are
        skipped, as ``update_session`` skips an unknown id. Entries staged
        for the archive leave the live file in the same write and are
        appended to the archive once the lock is released, with the record
        ``delete_session`` writes. Returns True if the file was written.
        """
        if not pending:
            return False
        archived: List[dict] = []
        with self._state_transaction() as txn:
            state = txn.state
            for session_id in pending.archive:
                entry = state.pop(session_id, None)
                if entry is None:
                    continue  # already gone (cleanup, or another daemon's archive pass)
                record = entry.copy()
                record['end_time'] = datetime.now().isoformat()
                record['status'] = 'archived'
                archived.append(record)
                txn.dirty = True
            for session_id, values in pending.fields.items():
                entry = state.get(session_id)
                if entry is None:
                    continue
                for key, value in values.items():
                    if key not in entry or entry[key] != value:
                        entry[key] = value
                        txn.dirty = True
            for session_id, values in pending.stats.items():
                entry = state.get(session_id)
                if entry is None:
                    continue
                if 'stats' not in entry:
                    entry['stats'] = SessionStats().to_dict()
                    txn.dirty = True
                stats = entry['stats']
                for key, value in values.items():
                    if key not in stats or stats[key] != value:
                        stats[key] = value
                        txn.dirty = True
            written = txn.dirty
        # Archive after the lock is released (separate file, separate lock)
        if archived:
            self._append_archive_records(archived)
        return written

    def _atomic_update(self, update_fn: Callable[[Dict[str, dict]], Dict[str, dict]]) -> None:
        """Atomically read, modify, and write state with exclusive lock held throughout.

        This prevents TOCTOU race conditions by holding the lock during the entire
        read-modify-write cycle.

        Args:
            update_fn: Function that takes the current state dict and returns the updated state.
        """
        with self._locked_state() as state:
            update_fn(state)

    @staticmethod
    def detect_focal_repo_candidates(start_directory: Optional[str]) -> List[str]:
        """Return one-layer-deep git-repo subdir names under ``start_directory`` (#170).

        Multi-repo workspaces are detected by scanning the immediate
        children of ``start_directory`` for entries that contain ``.git``.
        Returns the list of subdir *names* (relative to start_directory),
        sorted alphabetically.

        If ``start_directory`` is itself a git repo, returns an empty list:
        the user is in a single-repo situation and there's nothing to cycle.
        Same for missing / unreadable directories.
        """
        if not start_directory:
            return []
        try:
            if not os.path.isdir(start_directory):
                return []
            # If start_directory itself is a repo, this is single-repo.
            if os.path.isdir(os.path.join(start_directory, ".git")) or \
                    os.path.isfile(os.path.join(start_directory, ".git")):
                return []
            candidates: List[str] = []
            for entry in os.listdir(start_directory):
                if entry.startswith("."):
                    continue
                child = os.path.join(start_directory, entry)
                if not os.path.isdir(child):
                    continue
                git_marker = os.path.join(child, ".git")
                # Either a directory (normal clone) or a file (worktree / submodule)
                if os.path.isdir(git_marker) or os.path.isfile(git_marker):
                    candidates.append(entry)
            return sorted(candidates)
        except OSError:
            return []

    @staticmethod
    def resolve_focal_directory(
        start_directory: Optional[str], focal_subdir: Optional[str]
    ) -> Optional[str]:
        """Return the effective directory for git-stat reads (#170).

        When ``focal_subdir`` is set and exists under ``start_directory``,
        returns the joined path. Otherwise falls back to start_directory.
        """
        if not start_directory:
            return None
        if not focal_subdir:
            return start_directory
        candidate = os.path.join(start_directory, focal_subdir)
        if os.path.isdir(candidate):
            return candidate
        return start_directory

    def _detect_git_context(self, directory: Optional[str]) -> tuple[Optional[str], Optional[str]]:
        """Detect git repo and branch from directory.

        Reads ``.git/HEAD`` directly when it can (no subprocess — the monitor
        daemon calls this for every agent every 2s) and only shells out to
        git when the on-disk layout is something it doesn't understand.
        """
        if not directory:
            return None, None

        # Check directory exists
        if not os.path.isdir(directory):
            return None, None

        fast = read_git_context_from_disk(directory)
        if fast is not None:
            return fast

        try:
            import subprocess

            # Get repo name
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=2
            )
            repo_path = result.stdout.strip() if result.returncode == 0 else None
            repo_name = Path(repo_path).name if repo_path else None

            # Get branch
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=2
            )
            branch = result.stdout.strip() if result.returncode == 0 else None

            return repo_name, branch
        except subprocess.TimeoutExpired:
            print(f"Warning: Git command timed out in {directory}")
            return None, None
        except subprocess.CalledProcessError as e:
            print(f"Warning: Git command failed: {e}")
            return None, None
        except (OSError, IOError) as e:
            print(f"Warning: Could not detect git context: {e}")
            return None, None

    def read_git_context(self, session: Session) -> tuple[Optional[str], Optional[str]]:
        """``(repo_name, branch)`` of ``session``'s start directory right now.

        The detection half of ``refresh_git_context`` with no write: the
        daemon compares the answer with the session it holds and stages
        the change for its per-tick commit.
        """
        return self._detect_git_context(session.start_directory)

    def refresh_git_context(self, session_id: str) -> bool:
        """Refresh git repo/branch info for a session.

        Detects current branch from the session's start_directory and
        updates the session if it has changed.

        Returns:
            True if git context was updated, False otherwise
        """
        session = self.get_session(session_id)
        if not session or not session.start_directory:
            return False

        repo_name, branch = self.read_git_context(session)

        # Only update if something changed
        if repo_name != session.repo_name or branch != session.branch:
            self.update_session(
                session_id,
                repo_name=repo_name,
                branch=branch
            )
            return True
        return False

    def create_session(self, name: str, tmux_session: str, tmux_window: str,
                      command: List[str], start_directory: Optional[str] = None,
                      standing_instructions: str = "",
                      permissiveness_mode: str = "normal",
                      allowed_tools: Optional[str] = None,
                      extra_cli_args: Optional[List[str]] = None,
                      agent_teams: bool = False,
                      agent_persona: Optional[str] = None,
                      model: Optional[str] = None,
                      provider: str = "web",
                      backend: str = "claude-code",
                      session_id: Optional[str] = None,
                      wrapper: Optional[str] = None,
                      launcher_version: str = "",
                      **legacy_kwargs) -> Session:
        """Create and register a new session.

        Args:
            name: Session name
            tmux_session: Name of the tmux session
            tmux_window: Tmux window name
            command: Command used to start the session
            start_directory: Working directory for the session
            standing_instructions: Initial standing instructions (e.g., from config)
            permissiveness_mode: Permission mode (normal, permissive, bypass)
            allowed_tools: Comma-separated tool list for --allowedTools
            extra_cli_args: Extra agent-CLI flags via --backend-arg
            agent_persona: Agent persona name passed as --agent
            backend: Agent CLI backend name (see overcode.backends)
            session_id: Optional pre-generated session ID (used when ID must be known before window creation)
            **legacy_kwargs: Pre-Phase-6 parameter names (extra_claude_args,
                claude_agent) are still accepted.
        """
        # Only the two renamed fields that are actually create_session
        # parameters are aliased; anything else falls through to the
        # TypeError below rather than being silently dropped.
        if "extra_claude_args" in legacy_kwargs:
            value = legacy_kwargs.pop("extra_claude_args")
            if extra_cli_args is None:
                extra_cli_args = value
        if "claude_agent" in legacy_kwargs:
            value = legacy_kwargs.pop("claude_agent")
            if agent_persona is None:
                agent_persona = value
        if legacy_kwargs:
            raise TypeError(
                f"create_session() got unexpected keyword arguments: "
                f"{', '.join(sorted(legacy_kwargs))}"
            )
        if self._skip_git_detection:
            repo_name, branch = None, None
        else:
            repo_name, branch = self._detect_git_context(start_directory)

        session = Session(
            id=session_id or str(uuid.uuid4()),
            name=name,
            tmux_session=tmux_session,
            tmux_window=tmux_window,
            command=command,
            start_directory=start_directory,
            start_time=datetime.now().isoformat(),
            repo_name=repo_name,
            branch=branch,
            standing_instructions=standing_instructions,
            permissiveness_mode=permissiveness_mode,
            allowed_tools=allowed_tools,
            extra_cli_args=extra_cli_args or [],
            agent_teams=agent_teams,
            agent_persona=agent_persona,
            model=model,
            provider=provider,
            backend=backend,
            wrapper=wrapper,
            launcher_version=launcher_version,
        )

        with self._locked_state() as state:
            # A new agent taking a name that is another agent's alias takes
            # it over outright: the alias would otherwise come back to life
            # when this agent is archived (#478).
            for record in state.values():
                aliases = record.get("previous_names") or []
                if name in aliases:
                    record["previous_names"] = [a for a in aliases if a != name]
            state[session.id] = session.to_dict()

        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        """Get a session by ID.

        The object is shared with every other reader of this manager until
        ``sessions.json`` changes — a read-only snapshot. Persist changes
        through ``update_session`` / ``update_stats`` (which rewrite the file
        and so invalidate the snapshot) rather than by assigning to it.
        """
        return self._snapshot().get(session_id)

    def get_session_by_name(self, name: str) -> Optional[Session]:
        """Get a session by its current name (same shared snapshot as ``get_session``).

        Exact match only — use this where the question is "is this name
        taken?" (launch/fork duplicate checks). Anything addressing an agent
        a user or another agent named should use ``resolve_session_name``,
        which also follows renames.
        """
        for session in self._snapshot().values():
            if session.name == name:
                return session
        return None

    def resolve_session_name(self, name: str) -> Optional[Session]:
        """Find the agent ``name`` refers to, following renames (#478).

        The agent currently called ``name`` wins; otherwise the agent that
        was called ``name`` before an ``overcode rename``. The caller can
        tell the two apart by ``session.name != name`` (``rename_notice``).
        """
        exact = self.get_session_by_name(name)
        if exact is not None:
            return exact
        for session in self._snapshot().values():
            if name in session.previous_names:
                return session
        return None

    def list_sessions(self) -> List[Session]:
        """List all sessions (skips corrupted entries).

        A new list each call, of ``Session`` objects that are shared with
        every other reader until ``sessions.json`` changes — see
        ``get_session`` for the read-only contract.
        """
        return list(self._snapshot().values())

    def sessions_by_id(self) -> Mapping[str, Session]:
        """Every session keyed by id — the snapshot itself, in file order.

        The same objects ``list_sessions`` returns, without the list copy; the
        daemon builds its per-tick ``SessionIndex`` on it. Read-only, as for
        ``get_session``: the mapping is replaced, never mutated, when
        ``sessions.json`` changes.
        """
        return self._snapshot()

    def update_session_status(self, session_id: str, status: str):
        """Update session status.

        A no-op — no lock, no rewrite — when the snapshot already shows
        ``status``: the daemon and the TUI's launcher both re-assert a
        terminal status every pass, and each assertion used to be a
        full rewrite of the file.
        """
        session = self.get_session(session_id)
        if session is not None and session.status == status:
            return
        with self._locked_state() as state:
            if session_id in state:
                state[session_id]['status'] = status

    def delete_session(self, session_id: str, archive: bool = True):
        """Delete a session, optionally archiving it first.

        Args:
            session_id: The session ID to delete
            archive: If True (default), archive the session before removing
        """
        archived_data = None

        with self._locked_state() as state:
            if session_id in state:
                if archive:
                    archived_data = state[session_id].copy()
                    archived_data['end_time'] = datetime.now().isoformat()
                    archived_data['status'] = 'archived'
                del state[session_id]

        # Archive after the lock is released (separate file, separate lock)
        if archived_data is not None:
            self._archive_session(archived_data)

    # =========================================================================
    # Archive: archive.jsonl, one record per line, append-only
    # =========================================================================
    #
    # archive.json was a dict rewritten whole on every archive: archiving one
    # session cost a parse and an indent=2 dump of every session ever
    # archived, and the daemon now archives on its own (terminated sessions
    # after a grace), so that cost would have run on a timer. A line per
    # record makes an archive O(record) and a read O(what was appended).

    def _load_archive(self) -> Dict[str, dict]:
        """Load archived sessions as a fresh, private dict (see ``_load_state``)."""
        return self._read_archive_file()[1]

    def _migrate_legacy_archive(self) -> None:
        """One-time, idempotent move of a legacy ``archive.json`` into the JSONL.

        One ``exists()`` per archive access. The legacy records are
        appended in their dict order under the JSONL's exclusive lock
        (two processes migrating at once serialise; the second finds the
        file gone) and the legacy file is renamed ``archive.json.migrated``;
        an unreadable one becomes ``archive.json.unreadable`` and is left
        for the user. Should an older overcode write archive.json again, it
        is migrated again — the reader keeps the last record per id, which
        is what the dict did.
        """
        if not self.legacy_archive_file.exists():
            return
        with self._archive_appender(migrate=False) as f:
            self._migrate_legacy_archive_into(f)

    def _migrate_legacy_archive_into(self, f) -> None:
        legacy = self.legacy_archive_file
        if not legacy.exists():  # re-checked under the lock
            return
        try:
            with open(legacy, 'r') as lf:
                records = json.load(lf)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: could not migrate {legacy}: {e}")
            try:
                legacy.rename(legacy.with_name(legacy.name + ".unreadable"))
            except OSError:
                pass
            return
        if isinstance(records, dict):
            self._write_archive_lines(f, [r for r in records.values() if isinstance(r, dict)])
        try:
            legacy.rename(legacy.with_name(legacy.name + ".migrated"))
        except OSError:
            pass

    @contextmanager
    def _archive_appender(self, migrate: bool = True):
        """The JSONL open for appending, under its exclusive lock."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with open(self.archive_file, 'a+b') as f:
            if HAS_FCNTL:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                if migrate:
                    self._migrate_legacy_archive_into(f)
                yield f
            finally:
                if HAS_FCNTL:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _write_archive_lines(f, records: Iterable[dict]) -> None:
        """Append ``records`` as lines and fsync once.

        A crash mid-append leaves a line without its newline; the reader
        skips that line and the next append terminates it first, so one
        record is lost rather than two merged.
        """
        f.seek(0, os.SEEK_END)
        if f.tell() > 0:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                f.write(b"\n")
        for record in records:
            f.write((json.dumps(record) + "\n").encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())

    def _append_archive_records(self, records: List[dict]) -> None:
        """Add ``records`` to the archive: one lock, one fsync for all of them."""
        if not records:
            return
        with self._archive_appender() as f:
            self._write_archive_lines(f, records)

    def _archive_session(self, session_data: dict):
        """Add a session to the archive."""
        self._append_archive_records([session_data])

    def _read_archive_bytes(self) -> Tuple[Optional[os.stat_result], bytes, int]:
        """``(fstat, bytes from offset, offset)`` of the archive under a shared lock.

        ``offset`` is the previous parse's end when the file is the same
        inode and has not shrunk — the archive only grows, so everything
        before it was parsed already — and 0 otherwise.
        """
        self._migrate_legacy_archive()
        if not self.archive_file.exists():
            return None, b"", 0
        prev_ino, prev_offset, _ = self._archive_tail
        try:
            with open(self.archive_file, 'rb') as f:
                if HAS_FCNTL:
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    st = os.fstat(f.fileno())
                    offset = prev_offset if (st.st_ino == prev_ino and st.st_size >= prev_offset) else 0
                    f.seek(offset)
                    return st, f.read(), offset
                finally:
                    if HAS_FCNTL:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except OSError:
            return None, b"", 0

    def _read_archive_file(self) -> Tuple[Optional[FileSignature], Dict[str, dict]]:
        """Read and parse the whole archive under a shared lock — uncached raw records."""
        self._migrate_legacy_archive()
        if not self.archive_file.exists():
            return None, {}
        try:
            with open(self.archive_file, 'rb') as f:
                if HAS_FCNTL:
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    sig = FileSignature.of(os.fstat(f.fileno()))
                    data = f.read()
                finally:
                    if HAS_FCNTL:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except OSError:
            return None, {}
        return sig, _parse_archive_lines(data)[0]

    def _archive_snapshot(self) -> Dict[str, Session]:
        """The archived ``Session`` objects by entry id, extended when archive.jsonl grows."""
        return self._archive_cache.get(self.archive_file, self._parse_archive_file)

    def _parse_archive_file(self) -> Tuple[Optional[FileSignature], Dict[str, Session]]:
        """The stat-gated cache's reader: parses only the lines appended since last time.

        Objects for records parsed before are reused (the same sharing
        contract as the live snapshot); a shrunk or replaced file is parsed
        from the start.
        """
        st, data, offset = self._read_archive_bytes()
        if st is None:
            return None, {}
        _, _, prev_by_id = self._archive_tail
        by_id: Dict[str, Session] = dict(prev_by_id) if offset else {}
        records, end = _parse_archive_lines(data)
        for key, record in records.items():
            session = _archived_session(record)
            if session is not None:
                by_id[key] = session
        self._archive_tail = (st.st_ino, offset + end, by_id)
        return FileSignature.of(st), by_id

    def list_archived_sessions(self) -> List[Session]:
        """List all archived sessions (skips corrupted entries).

        Same sharing contract as ``list_sessions``: the objects are reused
        until ``archive.jsonl`` changes, and then for every record that was
        already there.
        """
        return list(self._archive_snapshot().values())

    def iter_archived_sessions(self) -> Iterator[Session]:
        """Archived sessions one at a time, in file order.

        For a caller that only needs a filter (``overcode history <name>``)
        and need not hold every record at once: the bytes are read under
        the shared lock, each record is built as the caller advances, and
        nothing is cached. Every line is yielded, so a re-migrated
        duplicate id appears twice where ``list_archived_sessions`` keeps
        the last record.
        """
        self._migrate_legacy_archive()
        if not self.archive_file.exists():
            return
        try:
            with open(self.archive_file, 'rb') as f:
                if HAS_FCNTL:
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    data = f.read()
                finally:
                    if HAS_FCNTL:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except OSError:
            return
        for line in data[: data.rfind(b"\n") + 1].split(b"\n"):
            record = _archive_record(line)
            if record is None:
                continue
            session = _archived_session(record)
            if session is not None:
                yield session

    def get_archived_session(self, session_id: str) -> Optional[Session]:
        """Get an archived session by ID (shared snapshot, see ``get_session``)."""
        return self._archive_snapshot().get(session_id)

    def update_session(self, session_id: str, **kwargs):
        """Update session fields.

        Renamed fields are written under both their canonical and their
        pre-Phase-6 key so a state file stays readable by an older overcode
        for one release; either name may be passed in.
        """
        kwargs = _with_legacy_keys(kwargs)
        with self._locked_state() as state:
            if session_id in state:
                state[session_id].update(kwargs)

    def rename_session(self, session_id: str, new_name: str, **fields) -> bool:
        """Atomically rename a session: duplicate check and update in one
        locked read-modify-write, so two concurrent renames cannot both win.

        The old name is appended to the session's ``previous_names`` and
        taken off every other session's, so an old name resolves to the
        agent that held it most recently; ``new_name`` is dropped from all
        alias lists, since it is now a live name.

        Args:
            session_id: The session being renamed.
            new_name: The new name; the write is refused (False) when any
                OTHER session already owns it.
            **fields: Additional fields to set in the same atomic write
                (e.g. ``tmux_window`` alongside the rename).

        Returns:
            True when the record was renamed, False on a duplicate name.
        """
        with self._locked_state() as state:
            if session_id not in state:
                return False
            for sid, record in state.items():
                if sid != session_id and record.get("name") == new_name:
                    return False
            old_name = state[session_id].get("name")
            for sid, record in state.items():
                aliases = record.get("previous_names") or []
                drop = {new_name} if sid == session_id else {new_name, old_name}
                if any(a in drop for a in aliases):
                    record["previous_names"] = [a for a in aliases if a not in drop]
            record = state[session_id]
            record.update(fields)
            if old_name and old_name != new_name:
                record["previous_names"] = [
                    *(a for a in record.get("previous_names") or [] if a != old_name),
                    old_name,
                ]
            record["name"] = new_name
            return True

    def update_stats(self, session_id: str, **stats_kwargs):
        """Update session statistics"""
        with self._locked_state() as state:
            if session_id in state:
                if 'stats' not in state[session_id]:
                    state[session_id]['stats'] = SessionStats().to_dict()
                state[session_id]['stats'].update(stats_kwargs)

    def set_standing_instructions(
        self,
        session_id: str,
        instructions: str,
        preset_name: Optional[str] = None
    ):
        """Set standing instructions for a session (resets complete flag).

        Args:
            session_id: The session ID
            instructions: Full instruction text
            preset_name: Preset name if using a library preset, None for custom
        """
        self.update_session(
            session_id,
            standing_instructions=instructions,
            standing_instructions_preset=preset_name,
            standing_orders_complete=False
        )

    def set_standing_orders_complete(self, session_id: str, complete: bool = True):
        """Mark standing orders as complete or incomplete"""
        self.update_session(session_id, standing_orders_complete=complete)

    def set_permissiveness(self, session_id: str, mode: str):
        """Set permissiveness mode (normal, permissive, strict)"""
        self.update_session(session_id, permissiveness_mode=mode)

    def set_agent_value(self, session_id: str, value: int):
        """Set agent value for priority sorting (#61).

        Args:
            session_id: The session ID
            value: Priority value (default 1000, higher = more important)
        """
        self.update_session(session_id, agent_value=value)

    def set_cost_budget(self, session_id: str, budget_usd: float):
        """Set cost budget for an agent (#173).

        Args:
            session_id: The session ID
            budget_usd: Budget in USD (0.0 = no budget/unlimited)
        """
        self.update_session(session_id, cost_budget_usd=budget_usd)

    def set_human_annotation(self, session_id: str, annotation: str):
        """Set human annotation for a session (#74)."""
        self.update_session(session_id, human_annotation=annotation)

    def add_agent_session_id(self, session_id: str, agent_session_id: str) -> bool:
        """Add a backend sessionId to a session's owned list if not present.

        This tracks which backend sessionIds belong to this overcode agent,
        enabling accurate context window calculation when multiple agents
        run in the same directory (#119).

        Args:
            session_id: The overcode session ID
            agent_session_id: The backend's own sessionId to add

        Returns:
            True if the sessionId was added, False if already present or session not found
        """
        session = self.get_session(session_id)
        if not session or agent_session_id in session.agent_session_ids:
            return False

        with self._locked_state() as state:
            if session_id in state:
                entry = state[session_id]
                ids = entry.get('agent_session_ids')
                if ids is None:
                    ids = entry.get('claude_session_ids', [])
                if agent_session_id not in ids:
                    ids.append(agent_session_id)
                entry['agent_session_ids'] = ids
                entry['claude_session_ids'] = ids
        return True

    def set_active_agent_session_id(self, session_id: str, agent_session_id: str):
        """Set the active backend session ID for context tracking (#116).

        Unlike add_agent_session_id which accumulates, this replaces the
        active session. After /clear the backend starts a new session and
        only that session's context window is relevant.
        """
        session = self.get_session(session_id)
        if not session:
            return
        if session.active_agent_session_id == agent_session_id:
            # The daemon re-binds the current id every 10 s per agent; a
            # rebind to the id already on disk is not a write (audit R5).
            return

        with self._locked_state() as state:
            if session_id in state:
                state[session_id]['active_agent_session_id'] = agent_session_id
                state[session_id]['active_claude_session_id'] = agent_session_id

    # Pre-Phase-6 method names.
    add_claude_session_id = add_agent_session_id
    set_active_claude_session_id = set_active_agent_session_id

    # =========================================================================
    # Agent Hierarchy (#244)
    # =========================================================================

    def get_children(self, session_id: str) -> List[Session]:
        """Get direct children of a session.

        Scans all sessions for matching parent_session_id.
        Typically <50 agents, so scanning is free.
        """
        all_sessions = self.list_sessions()
        return [s for s in all_sessions if s.parent_session_id == session_id]

    def get_descendants(self, session_id: str) -> List[Session]:
        """Get all descendants of a session (recursive BFS)."""
        result = []
        queue = [session_id]
        while queue:
            parent_id = queue.pop(0)
            children = self.get_children(parent_id)
            result.extend(children)
            queue.extend(c.id for c in children)
        return result

    def get_parent_chain(self, session_id: str) -> List[Session]:
        """Walk up from session to root, returning list of ancestors.

        Returns list ordered from immediate parent to root, over one
        snapshot (see ``SessionIndex.parent_chain``).
        """
        return SessionIndex(self._snapshot()).parent_chain(session_id)

    def compute_depth(self, session: Session) -> int:
        """Compute depth of a session in the hierarchy (0 = root)."""
        return len(self.get_parent_chain(session.id))

    def is_ancestor(self, ancestor_id: str, descendant_id: str) -> bool:
        """Check if ancestor_id is an ancestor of descendant_id."""
        chain = self.get_parent_chain(descendant_id)
        return any(s.id == ancestor_id for s in chain)

    def set_focal_repo(self, session_id: str, focal_subdir: Optional[str]) -> Optional[str]:
        """Set the focal repo subdir for a multi-repo workspace agent (#170).

        - ``focal_subdir=None`` clears the focal (back to start_directory).
        - Otherwise the subdir must be one of the candidates returned by
          ``detect_focal_repo_candidates(start_directory)``; an unknown
          value is rejected with a ``ValueError``.

        Updates the session's ``repo_name`` and ``branch`` to reflect the
        new effective directory so downstream renders pick it up without
        plumbing extra context.

        Returns the new focal_subdir (or None on clear), or None if the
        session is missing.
        """
        session = self.get_session(session_id)
        if session is None:
            return None
        if focal_subdir is not None:
            candidates = self.detect_focal_repo_candidates(session.start_directory)
            if not candidates:
                raise ValueError(
                    f"'{session.name}' is a single-repo workspace — nothing to focus."
                )
            if focal_subdir not in candidates:
                raise ValueError(
                    f"'{focal_subdir}' is not one of the focal candidates: "
                    f"{', '.join(candidates)}"
                )
        new_dir = self.resolve_focal_directory(session.start_directory, focal_subdir)
        new_repo, new_branch = self._detect_git_context(new_dir)
        self.update_session(
            session_id,
            focal_repo_subdir=focal_subdir,
            repo_name=new_repo,
            branch=new_branch,
        )
        return focal_subdir

    def cycle_focal_repo(self, session_id: str) -> Optional[str]:
        """Advance the focal repo to the next candidate (#170).

        Returns the new focal subdir, or None when the agent is single-repo
        (nothing to cycle). Wraps around at the end of the list. If no
        focal is currently set, picks the first candidate.
        """
        session = self.get_session(session_id)
        if session is None:
            return None
        candidates = self.detect_focal_repo_candidates(session.start_directory)
        if not candidates:
            return None
        current = session.focal_repo_subdir
        if current in candidates:
            idx = (candidates.index(current) + 1) % len(candidates)
        else:
            idx = 0
        return self.set_focal_repo(session_id, candidates[idx])

    def add_tags(self, session_id: str, tags: List[str]) -> List[str]:
        """Add tags to a session (#356).

        Tags are lower-cased and de-duplicated. Returns the resulting tag
        list after the update, or an empty list if the session is missing.
        """
        normalised = [t.strip().lower() for t in tags if t and t.strip()]
        if not normalised:
            return []
        with self._locked_state() as state:
            if session_id not in state:
                return []
            current = list(state[session_id].get('tags') or [])
            for t in normalised:
                if t not in current:
                    current.append(t)
            state[session_id]['tags'] = current
            return list(current)

    def remove_tags(self, session_id: str, tags: List[str]) -> List[str]:
        """Remove tags from a session (#356).

        Pass an empty list to clear all tags. Returns the resulting tag
        list after the update, or an empty list if the session is missing.
        """
        drop = {t.strip().lower() for t in tags if t and t.strip()}
        with self._locked_state() as state:
            if session_id not in state:
                return []
            if not drop:
                state[session_id]['tags'] = []
                return []
            current = [t for t in (state[session_id].get('tags') or []) if t not in drop]
            state[session_id]['tags'] = current
            return list(current)

    def reclaim_budget(self, child_id: str) -> Optional[float]:
        """Refund a child's unused budget back to its parent (#432).

        Computes `remaining = max(0, budget - spent)` for the child, transfers
        that amount onto the parent's budget, and caps the child's budget at
        what was actually spent so the same allowance can't be reclaimed twice.

        Returns the refunded amount in USD, or None if nothing was refunded
        (no parent / unlimited child budget / nothing left over). Idempotent:
        a second call returns 0.0.

        Notes:
        - If the parent has an unlimited budget (0.0), the child's budget is
          still trimmed but no on-disk addition happens to the parent.
        - Operates atomically under the same state lock as transfer_budget.
        """
        with self._locked_state() as state:
            if child_id not in state:
                return None
            child = state[child_id]
            child_budget = child.get('cost_budget_usd', 0.0)
            if child_budget <= 0:
                return None  # unlimited budget — nothing to reclaim

            parent_id = child.get('parent_session_id')
            if not parent_id or parent_id not in state:
                return None

            spent = float(((child.get('stats') or {}).get('estimated_cost_usd', 0.0)) or 0.0)
            remaining = max(0.0, child_budget - spent)
            if remaining <= 0:
                return 0.0

            # Cap child at what was actually spent to make the reclaim idempotent.
            child['cost_budget_usd'] = spent

            parent_budget = state[parent_id].get('cost_budget_usd', 0.0)
            # Unlimited parent budget stays unlimited.
            if parent_budget > 0:
                state[parent_id]['cost_budget_usd'] = parent_budget + remaining

            return remaining

    def transfer_budget(self, from_id: str, to_id: str, amount: float) -> bool:
        """Transfer budget from one agent to another.

        Validates that source is an ancestor of target and has sufficient budget.

        Args:
            from_id: Source session ID (must be ancestor of target)
            to_id: Target session ID
            amount: Amount in USD to transfer (must be > 0)

        Returns:
            True if transfer succeeded, False otherwise
        """
        if amount <= 0:
            return False

        # Validate relationship
        if not self.is_ancestor(from_id, to_id):
            return False

        # Atomic transfer
        success = False

        with self._locked_state() as state:
            if from_id not in state or to_id not in state:
                return False

            source_budget = state[from_id].get('cost_budget_usd', 0.0)

            # 0.0 = unlimited: always succeeds but just sets target's budget
            if source_budget > 0 and source_budget < amount:
                return False  # Insufficient funds

            # Deduct from source (skip if unlimited)
            if source_budget > 0:
                state[from_id]['cost_budget_usd'] = source_budget - amount

            # Add to target
            target_budget = state[to_id].get('cost_budget_usd', 0.0)
            state[to_id]['cost_budget_usd'] = target_budget + amount

            success = True

        return success
