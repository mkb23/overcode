"""
Session state management for Overcode.
"""

from datetime import datetime
from pathlib import Path
from contextlib import contextmanager
from typing import Dict, Iterable, List, Mapping, Optional, Tuple
from dataclasses import MISSING, dataclass, asdict, field, fields, replace
import uuid

import logging

from . import paths
from .agent_files import remove_agent_dir
from .exceptions import StateWriteError
from .json_store import JsonStore, Transaction
from .ledger import Ledger, billing_code_of, make_row
from .git_context import GitContextMixin, read_git_context_from_disk  # noqa: F401  (re-export)
from .session_archive import SessionArchiveMixin
from .session_identity import SessionIdentityMixin
from .stat_gate import FileSignature, StatGatedCache

logger = logging.getLogger(__name__)


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
    # Spend per agent conversation, which the totals above sum
    # (``conversation_spend``): kept per conversation so a read that no
    # longer covers one (pruned, or not the newest) keeps its spend
    conversations: Dict[str, dict] = field(default_factory=dict)

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
        if not isinstance(filtered.get("conversations", {}), dict):
            del filtered["conversations"]
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
    # Set by `overcode shutdown` on the agents it stops: the record is kept
    # for `revive --all`, so the daemon's archive pass leaves it alone.
    # Cleared when the agent is revived or restarted.
    kept_for_revive: bool = False
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
    # Set by a fresh launch whose backend mints its own conversation id
    # (codex, opencode): the conversation that launch starts is new, so its
    # first SessionStart may replace active_agent_session_id, which still
    # names the conversation the launch discarded.
    agent_session_fresh_pending: bool = False

    # Heartbeat configuration (#171)
    heartbeat_enabled: bool = False
    heartbeat_frequency_seconds: int = 300  # Default 5 minutes
    heartbeat_instruction: str = ""
    heartbeat_paused: bool = False
    last_heartbeat_time: Optional[str] = None  # ISO timestamp

    # Cost budget (#173) - 0.0 means no budget/unlimited
    cost_budget_usd: float = 0.0

    # Billing code: what the ledger files this agent's spend under. Sticky:
    # its launch name unless `launch --billing-code` set one, and always its
    # parent's for a child or a fork. `overcode rebill` changes it forward
    # only: a new segment (billing_seg) starts from the spend so far
    # (billing_base_*), which stays on the old code. None (a record from
    # before billing codes) reads as the agent's name.
    billing_code: Optional[str] = None
    billing_seg: int = 0
    billing_base_cost: float = 0.0
    billing_base_tokens: int = 0
    # The token breakdown at the rebill (ledger.usage_of): the new segment's
    # spend is priced from it, so a later /model switch does not reprice
    # the old code's tokens onto the new one
    billing_base_usage: Optional[Dict[str, int]] = None

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
    # A bedrock launch's routing (``pricing.bedrock_routing``): "global" is
    # billed at list price, any other profile at the model's regional premium
    bedrock_routing: Optional[str] = None
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
    # oversight_deadline in epoch seconds: places the naive stamp in the
    # repeated autumn hour (clock.stamp_epoch); None when unset or older
    oversight_deadline_at: Optional[float] = None
    # How long a done child keeps its window before the daemon closes it
    # (child_presets): None is the default hour, negative never
    archive_after_seconds: Optional[float] = None
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
        self.removed: List[str] = []  # ids to drop with no archive record (a failed launch)
        # Filled by commit_pending: the ids it actually moved to the archive
        self.archived: List[str] = []
        # Ledger rows to write with the commit, before the state file
        # (a spend ``correction``: ``ledger.correction_rows``)
        self.ledger_rows: List[dict] = []

    def __bool__(self) -> bool:
        return bool(self.fields or self.stats or self.archive or self.removed
                    or self.ledger_rows)

    def add_ledger_rows(self, rows: Iterable[dict]) -> None:
        """Stage ledger rows; ``commit_pending`` appends them write-ahead."""
        self.ledger_rows.extend(rows)

    def remove_session(self, session_id: str) -> None:
        """Stage dropping ``session_id`` with no archive record, and its directory."""
        if session_id not in self.removed:
            self.removed.append(session_id)

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
    already holds, each answer is a dict lookup.

    The one implementation of the hierarchy: ``SessionManager``'s
    ``get_children``, ``get_descendants``, ``get_parent_chain``,
    ``compute_depth`` and ``is_ancestor`` ask an index over its current
    snapshot.
    """

    def __init__(self, by_id: Mapping[str, "Session"]):
        self.by_id = by_id
        self._children: Optional[Dict[str, List["Session"]]] = None

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

    def is_ancestor(self, ancestor_id: str, descendant_id: str) -> bool:
        """Whether ``ancestor_id`` is on ``descendant_id``'s parent chain."""
        return any(s.id == ancestor_id for s in self.parent_chain(descendant_id))

    def children(self, session_id: str) -> List["Session"]:
        """Direct children, in table order; the parent-to-children map is built on first use."""
        if self._children is None:
            children: Dict[str, List[Session]] = {}
            for s in self.by_id.values():
                if s.parent_session_id:
                    children.setdefault(s.parent_session_id, []).append(s)
            self._children = children
        return list(self._children.get(session_id, ()))

    def children_count(self, session_id: str) -> int:
        """``len(children(session_id))``."""
        return len(self.children(session_id))

    def descendants(self, session_id: str) -> List["Session"]:
        """Every descendant, breadth first (cycle-safe: each session once)."""
        result: List[Session] = []
        seen = {session_id}
        queue = [session_id]
        while queue:
            for child in self.children(queue.pop(0)):
                if child.id not in seen:
                    seen.add(child.id)
                    result.append(child)
                    queue.append(child.id)
        return result


def rename_notice(requested: str, session: Optional["Session"]) -> Optional[str]:
    """The line to show when ``requested`` reached ``session`` through a rename.

    ``SessionManager.resolve_session_name`` follows old names; a caller that
    used one gets its answer plus this notice, so a person or agent holding
    a stale name learns the new one (#478). None when no rename was involved.
    """
    if session is None or session.name == requested:
        return None
    return f"note: agent '{requested}' was renamed to '{session.name}'"


class _StatsLog:
    """The log ``StatsSync`` writes to, outside the daemon: this module's logger."""

    def info(self, message: str) -> None:
        logger.info(message)

    def warn(self, message: str) -> None:
        logger.warning(message)

    def debug(self, message: str) -> None:
        logger.debug(message)


class SessionManager(SessionArchiveMixin, GitContextMixin, SessionIdentityMixin):
    """Manages session state persistence.

    For testing, pass a custom state_dir (temp directory) and skip_git_detection=True.
    """

    def __init__(self, state_dir: Optional[Path] = None, skip_git_detection: bool = False):
        """Initialize the session manager.

        Args:
            state_dir: Directory for state files (defaults to
                paths.sessions_registry_dir(): the sessions root)
            skip_git_detection: If True, skip git repo/branch detection (for testing)
        """
        if state_dir is None:
            state_dir = paths.sessions_registry_dir()
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.state_dir / "sessions.json"
        self._store = JsonStore(self.state_file)
        # The session archive: a slim record per agent, a JSONL file per
        # month, beside the sessions root (session_archive). The single-file
        # archives of earlier versions are split into it on first access.
        self.archive_dir = paths.archive_dir(self.state_dir)
        self.old_archive_file = self.state_dir / "archive.jsonl"
        self.legacy_archive_file = self.state_dir / "archive.json"
        # The spend ledger, beside the archive (ledger)
        self.ledger = Ledger(self.state_dir)
        self._skip_git_detection = skip_git_detection
        # The Session objects built from the last parse of each file, keyed
        # by entry id and reused while the file's stat signature is unchanged
        # (audit R4). Per instance: the daemon and each TUI hold their own
        # manager, and the TUI's workers call list_sessions() five to six
        # times a second against a file that changes only when something
        # writes it.
        self._state_cache: StatGatedCache[Dict[str, Session]] = StatGatedCache()

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
        skipped, as ``update_session`` skips an unknown id.

        Entries staged for the archive go write-ahead (``agent_bundle``):
        each agent's directory is packed into its day bundle first (outside
        the lock), then, under the state lock, the archive records (with
        their bundle pointers) are appended and fsynced *before* the live
        file drops them, and only then is each directory removed. A crash
        anywhere leaves a duplicate, never a loss. ``pending.archived``
        lists the ids this call moved (a daemon that lost the race to
        another moves none, and leaves the directories alone). Returns True
        if the file was written.
        """
        if not pending:
            return False
        pointers = self._pack_for_archive(pending.archive)
        archived: List[dict] = []
        with self._state_transaction() as txn:
            state = txn.state
            # Write-ahead: a spend correction reaches the ledger before the
            # record that no longer needs one (a crash repeats it; the rows
            # are cumulative, so a repeat changes nothing)
            if pending.ledger_rows:
                self.ledger.append(pending.ledger_rows)
            # Staged values first, so an entry archived in the same commit
            # takes them (its final ledger row is its last word on spend)
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
            codes = self._inherited_codes(pending.archive, state)
            for session_id in pending.archive:
                entry = state.pop(session_id, None)
                if entry is None:
                    continue  # already gone (cleanup, or another daemon's archive pass)
                record = entry.copy()
                if session_id in codes:
                    record['billing_code'] = codes[session_id]
                record['end_time'] = datetime.now().isoformat()
                record['status'] = 'archived'
                if session_id in pointers:
                    record['bundle'] = pointers[session_id]
                archived.append(record)
                txn.dirty = True
            # Write-ahead: durable in the archive before it leaves the live
            # file (which the transaction writes on exit)
            if archived:
                self._append_archive_records(archived)
                self.ledger.append([make_row(r, "final") for r in archived])
            removed_dirs = []
            for session_id in pending.removed:
                entry = state.pop(session_id, None)
                if entry is not None:
                    removed_dirs.append((session_id, entry.get("tmux_session")))
                    txn.dirty = True
            written = txn.dirty
        pending.archived = [r['id'] for r in archived if isinstance(r.get('id'), str)]
        for record in archived:
            self._remove_archived_dir(record, packed=record.get('id') in pointers)
        for session_id, tmux_session in removed_dirs:
            if isinstance(tmux_session, str) and tmux_session:
                remove_agent_dir(tmux_session, session_id)
        return written

    def _inherited_codes(self, session_ids: Iterable[str], state: dict) -> Dict[str, str]:
        """``{id: code}`` for those of ``session_ids`` that are children with no billing code.

        A child an older overcode launched has a parent but no code until a
        daemon housekeeping pass gives it one; archived before that, its
        final ledger row and archive record must still bill its root's code
        (``inherited_billing_code``), not its own name. The archive is read
        only when such a record is found. (A codeless root bills to its
        name, which ``make_row`` already gives it.)
        """
        from .ledger import billing_lookup, inherited_billing_code

        missing = [sid for sid in session_ids
                   if isinstance(state.get(sid), dict) and not state[sid].get('billing_code')
                   and state[sid].get('parent_session_id')]
        if not missing:
            return {}
        try:
            lookup = billing_lookup(state.values(), self.iter_archived_records(newest_first=True))
        except Exception as e:  # the live records still give most codes
            logger.warning("archived billing codes not read: %s", e)
            lookup = billing_lookup(state.values())
        return {sid: inherited_billing_code(state[sid], lookup) for sid in missing}

    def _pack_for_archive(self, session_ids: Iterable[str]) -> Dict[str, dict]:
        """Pack each staged agent's directory into its day bundle: {id: pointer}.

        An agent with no directory (launched before them, remote, or
        already packed) gets no pointer; one whose packing failed keeps its
        directory for the orphan sweep to retry.
        """
        ids = list(session_ids)
        if not ids:
            return {}
        from .agent_bundle import pack_agent_dir

        snapshot = self._snapshot()
        pointers: Dict[str, dict] = {}
        for session_id in ids:
            session = snapshot.get(session_id)
            if session is None or not session.tmux_session:
                continue
            try:
                pointer = pack_agent_dir(session.tmux_session, session_id, {
                    "name": session.name,
                    "parent": session.parent_session_id,
                })
            except Exception as e:  # never block archiving on the bundle
                logger.warning("could not pack agent %s: %s", session_id, e)
                continue
            if pointer is not None:
                pointers[session_id] = pointer
        return pointers

    @staticmethod
    def _remove_archived_dir(record: dict, *, packed: bool) -> None:
        """The last write-ahead step: remove an archived agent's directory.

        Kept when it exists but was not packed (packing failed): the orphan
        sweep packs it later.
        """
        session_id, tmux_session = record.get('id'), record.get('tmux_session')
        if not isinstance(session_id, str) or not isinstance(tmux_session, str) or not tmux_session:
            return
        if packed or not paths.agent_dir(tmux_session, session_id).is_dir():
            remove_agent_dir(tmux_session, session_id)

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
                      launcher_version: str = "",
                      billing_code: Optional[str] = None) -> Session:
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
            billing_code=billing_code or name,
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

        The same write-ahead path as the daemon's archive pass
        (``commit_pending``): the agent's directory is packed, the archive
        record written, the record removed, the directory removed. Without
        ``archive`` (a launch that failed) the record and the directory
        simply go.

        Args:
            session_id: The session ID to delete
            archive: If True (default), archive the session before removing
        """
        self.delete_sessions([session_id], archive=archive)

    def delete_sessions(self, session_ids: Iterable[str], archive: bool = True) -> List[str]:
        """Delete many sessions in one commit (``overcode cleanup``); returns those archived."""
        pending = PendingUpdates()
        session_ids = list(session_ids)
        for session_id in session_ids:
            if archive:
                pending.archive_session(session_id)
            else:
                pending.remove_session(session_id)
        if archive:
            self._refresh_final_stats(session_ids, pending)
        self.commit_pending(pending)
        return pending.archived

    def _refresh_final_stats(self, session_ids: List[str], pending: "PendingUpdates") -> None:
        """Read each agent's transcripts once more, into ``pending``, before it is archived.

        The daemon syncs stats every 5 to 60 s; an agent killed in between
        would otherwise close its ledger segment without its last turn's
        spend, and nothing adds it later. Never raises.
        """
        try:
            from .daemon_stats_sync import StatsSync

            sync = StatsSync(session_manager=self, log=_StatsLog())
            snapshot = self._snapshot()
        except Exception as e:
            logger.warning("final stats not refreshed: %s", e)
            return
        for session_id in session_ids:
            session = snapshot.get(session_id)
            if session is None:
                continue
            scratch = PendingUpdates()
            try:
                sync.sync_agent_stats(session, scratch)
            except Exception as e:
                logger.warning("final stats of %s not refreshed: %s", session_id, e)
                continue
            # The same per-conversation merge as the daemon's sync
            # (``conversation_spend``): a gone transcript keeps its spend
            for key, changes in scratch.fields.items():
                pending.update_session(key, **changes)
            for key, changes in scratch.stats.items():
                pending.update_stats(key, **changes)
            pending.add_ledger_rows(scratch.ledger_rows)

    def rebill(self, session_id: str, billing_code: str) -> Optional[str]:
        """Move an agent to another billing code, forward only; returns the old code.

        Under the state lock, write-ahead: the ledger gets a ``close`` row
        for the old code (the agent's spend so far stays there) and an
        ``open`` row for the new one, then the record starts a new segment
        whose base is the spend so far. None if the agent is unknown; the
        code unchanged (no rows) if it already is ``billing_code``.
        """
        billing_code = billing_code.strip()
        if not billing_code:
            raise ValueError("a billing code cannot be empty")
        with self._state_transaction() as txn:
            entry = txn.state.get(session_id)
            if entry is None:
                return None
            # A child an older overcode launched has no code of its own yet:
            # its spend so far is its root's, not its name's
            old = (self._inherited_codes([session_id], txn.state).get(session_id)
                   or billing_code_of(entry))
            if old == billing_code:
                if entry.get("billing_code") != billing_code:
                    entry["billing_code"] = billing_code
                    txn.dirty = True
                return old
            from .ledger import spend_of

            from .ledger import usage_of

            spend, usage = spend_of(entry), usage_of(entry)
            seg = int(entry.get("billing_seg", 0) or 0)
            close = make_row(entry, "close", billing_code=old)
            opened = make_row(entry, "open", billing_code=billing_code, seg=seg + 1,
                              base={"cost": spend["cost"], "tokens": spend["tokens"],
                                    "usage": usage})
            self.ledger.append([close, opened])
            entry.update(billing_code=billing_code, billing_seg=seg + 1,
                         billing_base_cost=spend["cost"], billing_base_tokens=spend["tokens"],
                         billing_base_usage=usage)
            txn.dirty = True
        return old

    def tidy_agent_storage(
        self,
        tmux_session: str,
        *,
        sweep_limit: int = 200,
        prune_limit: Optional[int] = None,
    ) -> Dict[str, int]:
        """Clear orphaned agent directories and apply bundle retention; never raises.

        The orphan sweep (``agent_bundle.sweep_orphans``): a directory
        whose record has gone (a crash between the archive commit and its
        removal, a hook after it, an older overcode's cleanup) is packed,
        recorded in the archive and removed, or just removed when the
        archive has it already. Then the bundle retention
        (``agent_history``; off by default). Run by the daemon at start
        and hourly, and by ``overcode cleanup`` and ``kill``, so a user
        without a daemon stays bounded too. Returns the sweep's counts plus
        ``pruned`` (bundle days deleted).
        """
        from . import agent_bundle
        from .config import get_agent_history_config

        def live_ids() -> set:
            return set(self._snapshot())

        def archived(session_ids: set) -> Dict[str, bool]:
            # Whether each archived one's record has its bundle: one whose
            # packing failed at archive time kept its directory for this
            return {sid: bool(r.get("bundle"))
                    for sid, r in self.newest_archived_records(session_ids).items()}

        def attach(session_id: str, pointer: dict) -> None:
            newest = self.newest_archived_records({session_id}).get(session_id)
            if newest is not None:
                self._append_archive_records([dict(newest, bundle=pointer)])

        def record(session_id: str, pointer: dict) -> None:
            now = datetime.now().isoformat()
            self._append_archive_records([{
                "id": session_id, "name": f"orphan-{session_id[:8]}",
                "tmux_session": tmux_session, "tmux_window": "", "command": [],
                "start_directory": None, "start_time": now, "end_time": now,
                "status": "archived", "orphan": True, "bundle": pointer,
            }])

        counts: Dict[str, int] = {}
        try:
            if sweep_limit > 0:
                counts.update(agent_bundle.sweep_orphans(
                    tmux_session, live_ids, archived=archived, record=record, attach=attach,
                    limit=sweep_limit))
        except Exception as e:
            logger.warning("agent directory sweep failed: %s", e)
        try:
            cfg = get_agent_history_config()
            counts["pruned"] = len(agent_bundle.prune(
                retention_days=cfg["retention_days"], max_bytes=cfg["max_bytes"],
                limit=prune_limit))
        except Exception as e:
            logger.warning("bundle retention failed: %s", e)
        try:
            from .config import get_archive_config
            counts["archive_months_pruned"] = len(
                self.prune_archive(get_archive_config()["retention_months"]))
        except Exception as e:
            logger.warning("archive retention failed: %s", e)
        return counts

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

    # =========================================================================
    # Agent Hierarchy (#244)
    # =========================================================================

    def index(self) -> SessionIndex:
        """A ``SessionIndex`` over the current snapshot (one stat)."""
        return SessionIndex(self._snapshot())

    def get_children(self, session_id: str) -> List[Session]:
        """Direct children of a session, in table order."""
        return self.index().children(session_id)

    def get_descendants(self, session_id: str) -> List[Session]:
        """All descendants of a session, breadth first, over one snapshot."""
        return self.index().descendants(session_id)

    def get_parent_chain(self, session_id: str) -> List[Session]:
        """Ancestors from the immediate parent up to the root, over one snapshot."""
        return self.index().parent_chain(session_id)

    def compute_depth(self, session: Session) -> int:
        """Depth of a session in the hierarchy (0 = root)."""
        return self.index().depth(session)

    def is_ancestor(self, ancestor_id: str, descendant_id: str) -> bool:
        """Check if ancestor_id is an ancestor of descendant_id."""
        return self.index().is_ancestor(ancestor_id, descendant_id)

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
