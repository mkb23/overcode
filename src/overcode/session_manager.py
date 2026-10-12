"""
Session state management for Overcode.
"""

import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager
from typing import Dict, Iterable, List, Mapping, Optional, Tuple
from dataclasses import MISSING, dataclass, asdict, field, fields, replace
import uuid

from .exceptions import StateWriteError
from .json_store import JsonStore, Transaction
from .session_archive import SessionArchiveMixin
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

@dataclass
class SessionStats:
    """Runtime statistics for an agent session"""
    interaction_count: int = 0
    estimated_cost_usd: float = 0.0
    estimated_energy_j: float = 0.0  # tokens x the model's guessed GPU energy (#522)
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
# name. Only read now: ``from_dict`` migrates a state file written before
# the rename. The old names were also written for one release (0.5.x); 0.6
# writes the canonical names only.
LEGACY_SESSION_KEYS = {
    "agent_session_ids": "claude_session_ids",
    "active_agent_session_id": "active_claude_session_id",
    "extra_cli_args": "extra_claude_args",
    "agent_persona": "claude_agent",
}



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
    # Age of the conversation behind active_agent_session_id (its transcript's
    # mtime), and whether that age is actually known. Used by
    # advance_active_agent_session_id to order a delayed hook event whose id it
    # has never seen before.
    active_agent_session_ordinal: float | None = None
    active_agent_session_unordered: bool = False

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
    # Model the transcript last showed. `model` follows it only when it
    # changes, so a model picked by `restart --model` survives until the
    # agent answers on it (#505).
    transcript_model: Optional[str] = None
    effort: Optional[str] = None  # Reasoning effort, detected from the backend's store (#497)
    provider: str = "web"  # API provider: "web" (Claude.ai OAuth) or "bedrock" (AWS Bedrock)
    backend: str = "claude-code"  # Agent CLI backend (see overcode.backends)
    wrapper: Optional[str] = None  # Wrapper script path (wraps claude invocation)
    skill_profile: Optional[str] = None  # Skill profile applied at every (re)launch (#499)
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
    source_url: str = ""  # Sister API server URL (for sending control commands)
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

    def to_dict(self) -> dict:
        # asdict() recursively converts nested dataclasses (stats)
        return asdict(self)

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


_SESSION_FIELD_NAMES = frozenset(f.name for f in fields(Session))
_STATS_FIELD_NAMES = frozenset(f.name for f in fields(SessionStats))


# What ``SessionManager._state_transaction`` yields: the parsed state and
# whether it must be written back.
_StateTxn = Transaction


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
        self.fields.setdefault(session_id, {}).update(kwargs)

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


def rename_notice(requested: str, session: Optional["Session"]) -> Optional[str]:
    """The line to show when ``requested`` reached ``session`` through a rename.

    ``SessionManager.resolve_session_name`` follows old names; a caller that
    used one gets its answer plus this notice, so a person or agent holding
    a stale name learns the new one (#478). None when no rename was involved.
    """
    if session is None or session.name == requested:
        return None
    return f"note: agent '{requested}' was renamed to '{session.name}'"


class SessionManager(SessionArchiveMixin):
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
        self._store = JsonStore(self.state_file)
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
        """Read and parse the state file — uncached, lock-free.

        Writers replace the file atomically, so one open sees one complete
        version. Also returns the ``fstat`` signature of the bytes parsed;
        it is ``None`` whenever the result did not come from one clean read
        of the file (missing file, or corrupt and served read-only from the
        backup), so the caller never remembers such a result. See
        :mod:`overcode.json_store`.
        """
        return self._store.read()

    def restore_from_backup(self) -> bool:
        """Make ``sessions.json.bak`` the live state, under the lock.

        The current file is kept as ``sessions.json.pre-restore-<time>``.

        Returns:
            True if backup was restored, False otherwise
        """
        try:
            return self._store.restore_from_backup()
        except StateWriteError:
            return False

    def _save_state(self, state: Dict[str, dict]):
        """Replace all sessions in the state file (locked, atomic)."""
        self._store.save(state)

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
        is replaced (one dump, one fsync) on exit only if the caller set
        ``dirty``. ``_locked_state`` always does; ``commit_pending`` does
        only when a staged value differs from what is on disk, so a tick
        with nothing to change costs a parse and no write.
        """
        with self._store.transaction() as txn:
            yield txn

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
        """``(repo_name, branch)`` of ``session``'s focal repo, else its start directory, right now.

        The detection half of ``refresh_git_context`` with no write: the
        daemon compares the answer with the session it holds and stages
        the change for its per-tick commit. It must follow the focal repo,
        or every tick undoes ``set_focal_repo`` (#494).
        """
        return self._detect_git_context(self.resolve_focal_directory(
            session.start_directory, getattr(session, "focal_repo_subdir", None)))

    def refresh_git_context(self, session_id: str) -> bool:
        """Refresh git repo/branch info for a session.

        Detects current branch from the session's focal repo (else its
        start_directory) and updates the session if it has changed.

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
                      skill_profile: Optional[str] = None,
                      launcher_version: str = "") -> Session:
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
        """
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
            skill_profile=skill_profile,
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

    def update_session(self, session_id: str, **kwargs):
        """Update session fields.

        Renamed fields are written under both their canonical and their
        pre-Phase-6 key so a state file stays readable by an older overcode
        for one release; either name may be passed in.
        """
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
                entry.pop('claude_session_ids', None)  # a pre-0.6 twin
        return True

    def set_active_agent_session_id(self, session_id: str, agent_session_id: str):
        """Set the active backend session ID for context tracking (#116).

        Unlike add_agent_session_id which accumulates, this replaces the
        active session. After /clear the backend starts a new session and
        only that session's context window is relevant.

        Carries no ordering information, so any age recorded for the previous
        conversation is dropped rather than left attached to a different id —
        a relaunch sets the active id this way, and a stale age would then be
        compared against the new conversation.
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
                state[session_id].pop('active_claude_session_id', None)  # a pre-0.6 twin
                state[session_id].pop('active_agent_session_ordinal', None)
                state[session_id]['active_agent_session_unordered'] = True

    def advance_active_agent_session_id(
        self, session_id: str, agent_session_id: str, ordinal: float | None = None,
        *, allow_new: bool = True, allow_first: bool = False, allow_return: bool = False,
    ) -> tuple[str, str | None]:
        """Move the active backend session id forward, atomically.

        A backend mints a new session id whenever the agent resets its context
        (`/clear`, auto-compaction), and `restart` relaunches with
        `--resume <active_agent_session_id>`, so this field has to follow the
        conversation the agent is actually in.

        The ordering check, the history append and the active-id write all
        happen inside one `_locked_state` transaction, so the decision is made
        against the same state it is written to. Hook invocations for one agent
        can overlap, and doing this as separate locked updates let a stale one
        land after a newer one.

        Which moves are allowed is the caller's to say, from the event (#500):

        ``allow_new``    — may move to an id this record has never seen (an
                           explicit reset: SessionStart with source clear or
                           compact). False for ordinary events, so a nested
                           agent CLI inheriting this agent's env cannot take
                           the record over.
        ``allow_first``  — may take an unseen id when the record has no active
                           id yet (the first id of a backend with no
                           prescribed launch id, e.g. codex).
        ``allow_return`` — may move back to an id already in the history (an
                           explicit resume), which otherwise is refused as
                           superseded.

        ``ordinal`` is a caller-supplied age for the conversation behind
        ``agent_session_id`` — the modification time of its transcript. It is
        recorded alongside the active id and compared on the next call, which
        is what lets a delayed hook be recognised as stale even when its id has
        never been seen before and lock acquisition happens out of event order.
        Callers that cannot supply one degrade to the history-ordering check
        alone.

        Returns ``(outcome, active_agent_session_id)`` where the second element
        is the id the record holds when the transaction ends — read inside the
        lock, so callers never have to fall back on a pre-transaction snapshot
        that a concurrent hook may already have replaced. Outcomes:

            "advanced"           — the record now names ``agent_session_id``
            "advanced_unordered" — advanced, but its age could not be compared
                                   against the previous conversation's, so the
                                   record is flagged unordered and the caller
                                   should treat the sync as unconfirmed
            "current"            — it already did, with a known age
            "current_unordered"  — it already did, but the age is still
                                   unknown, so a later event carrying one can
                                   still settle it
            "superseded"         — ``agent_session_id`` is an older
                                   conversation the agent has left; ignored
            "rejected"           — an unseen id from an event not allowed to
                                   introduce one; ignored
            "unknown"            — no such session
        """
        # Written only when the entry changes (#501): the hook handler calls
        # this on events whose id may already be current, and a rewrite of
        # sessions.json per hook event is the audit's R5 shape.
        with self._state_transaction() as txn:
            state = txn.state
            entry = state.get(session_id)
            if entry is None:
                return "unknown", None

            ids = entry.get('agent_session_ids')
            if ids is None:
                ids = list(entry.get('claude_session_ids') or [])
            active = (
                entry.get('active_agent_session_id')
                or entry.get('active_claude_session_id')
            )

            unordered = bool(entry.get('active_agent_session_unordered'))

            if active == agent_session_id:
                # Re-evaluation of an earlier unordered advance: the same id
                # arriving with an age attached is what settles it, which is
                # what makes "unconfirmed" recoverable rather than permanent.
                if unordered and ordinal is not None:
                    txn.dirty = True
                    entry['active_agent_session_ordinal'] = ordinal
                    entry['active_agent_session_unordered'] = False
                    return "advanced", active
                if unordered:
                    return "current_unordered", active
                return "current", active

            known = agent_session_id in ids
            if known and allow_return:
                # An explicit resume of a conversation this agent already
                # owns: history order says nothing about which one is live.
                txn.dirty = True
                entry['active_agent_session_id'] = agent_session_id
                entry.pop('active_claude_session_id', None)  # a pre-0.6 twin
                if ordinal is not None:
                    entry['active_agent_session_ordinal'] = ordinal
                else:
                    entry.pop('active_agent_session_ordinal', None)
                entry['active_agent_session_unordered'] = ordinal is None
                return ("advanced" if ordinal is not None else "advanced_unordered"), agent_session_id

            # Ids are appended the first time they are seen, so one that is
            # already recorded and is no longer the newest belongs to a
            # conversation already left behind — a hook event in flight across
            # a reset carries exactly that.
            if known and ids[-1] != agent_session_id:
                return "superseded", active

            if not known and not (allow_new or (allow_first and not active)):
                return "rejected", active

            # Age check, for a delayed hook whose id has never been seen: two
            # resets in quick succession can have the newer id take the lock
            # first, and history ordering alone would then accept the older id
            # as new. Comparing recorded ages catches it whichever order the
            # locks are acquired in.
            active_ordinal = entry.get('active_agent_session_ordinal')
            has_active_ordinal = isinstance(active_ordinal, (int, float))
            comparable = ordinal is not None and has_active_ordinal
            if comparable and ordinal < active_ordinal:
                return "superseded", active
            # Equal ages order nothing: two different conversations with the
            # same transcript mtime cannot be told apart, so accept the move
            # but do not claim it was confirmed.
            ages_tied = comparable and ordinal == active_ordinal

            # Keep the outgoing id in the history so the ordering check above
            # can recognise it as superseded next time. A fresh launch only
            # sets it active, so it is not necessarily there yet.
            if active and active not in ids:
                ids.append(active)
            if agent_session_id not in ids:
                ids.append(agent_session_id)

            txn.dirty = True
            entry['agent_session_ids'] = ids
            entry.pop('claude_session_ids', None)  # a pre-0.6 twin
            entry['active_agent_session_id'] = agent_session_id
            entry.pop('active_claude_session_id', None)  # a pre-0.6 twin
            confirmed = ordinal is not None and not ages_tied
            if confirmed:
                entry['active_agent_session_ordinal'] = ordinal
            else:
                # Nothing to order the next event against. Drop any inherited
                # age rather than comparing a new conversation against an older
                # one's, and flag the record so a later event carrying an age
                # re-evaluates instead of inheriting this uncertainty forever.
                entry.pop('active_agent_session_ordinal', None)
            entry['active_agent_session_unordered'] = not confirmed

            if not confirmed:
                return "advanced_unordered", agent_session_id
            return "advanced", agent_session_id


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
