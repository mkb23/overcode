"""
Session state management for Overcode.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager
from typing import Callable, Dict, List, Optional
from dataclasses import MISSING, dataclass, asdict, field, fields
import uuid
import time

from .exceptions import StateWriteError

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    # Windows doesn't have fcntl
    HAS_FCNTL = False


@dataclass
class SessionStats:
    """Runtime statistics for a Claude session"""
    interaction_count: int = 0
    estimated_cost_usd: float = 0.0
    total_tokens: int = 0
    operation_times: List[float] = field(default_factory=list)  # seconds per operation
    steers_count: int = 0  # number of overcode interventions
    last_activity: Optional[str] = None  # ISO timestamp
    current_task: str = "Initializing..."  # one-sentence description

    # Token breakdown (persisted from Claude Code history)
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


@dataclass
class Session:
    """Represents a Claude session"""
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

    # Claude sessionIds owned by this overcode session (#119)
    # Used to accurately calculate context window for this specific agent
    claude_session_ids: List[str] = field(default_factory=list)

    # The currently active Claude session ID (#116)
    # Replaced (not appended) when /clear creates a new session.
    # Used for context window calculation — only the active session matters.
    active_claude_session_id: Optional[str] = None

    # Heartbeat configuration (#171)
    heartbeat_enabled: bool = False
    heartbeat_frequency_seconds: int = 300  # Default 5 minutes
    heartbeat_instruction: str = ""
    heartbeat_paused: bool = False
    last_heartbeat_time: Optional[str] = None  # ISO timestamp

    # Cost budget (#173) - 0.0 means no budget/unlimited
    cost_budget_usd: float = 0.0

    # Hook-based status detection - per-agent toggle (#5)
    hook_status_detection: bool = True

    # Skills loaded during this session (#252)
    loaded_skills: List[str] = field(default_factory=list)
    available_skills: List[str] = field(default_factory=list)

    # Claude CLI flag passthrough (#290)
    allowed_tools: Optional[str] = None  # Comma-separated tool list for --allowedTools
    extra_claude_args: List[str] = field(default_factory=list)  # Extra CLI flags via --claude-arg
    agent_teams: bool = False  # Claude Code agent teams mode (#309)
    claude_agent: Optional[str] = None  # Claude agent persona (from .claude/agents/)
    model: Optional[str] = None  # Claude model (e.g. "sonnet", "opus", "haiku", or full name)
    provider: str = "web"  # API provider: "web" (Claude.ai OAuth) or "bedrock" (AWS Bedrock)
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

    def to_dict(self) -> dict:
        # asdict() recursively converts nested dataclasses (stats)
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Optional['Session']:
        """Create Session from dict, handling unknown/invalid fields gracefully.

        Returns None if required fields are missing or data is corrupt.
        Uses dataclasses.fields() to auto-detect required fields and valid keys.
        """
        cls_fields = fields(cls)

        # Required = fields with no default and no default_factory
        required = {
            f.name for f in cls_fields
            if f.default is MISSING and f.default_factory is MISSING  # type: ignore[comparison-overlap]
        }
        if not all(k in data for k in required):
            return None

        # Backward compat: migrate stats.model → session.model
        if 'stats' in data and isinstance(data['stats'], dict):
            stats_model = data['stats'].get('model')
            if stats_model and not data.get('model'):
                data['model'] = stats_model

        # Handle stats separately (nested dataclass needs manual conversion)
        if 'stats' in data and isinstance(data['stats'], dict):
            data['stats'] = SessionStats.from_dict(data['stats'])
        elif 'stats' not in data:
            data['stats'] = SessionStats()

        # Filter to only known fields
        valid_fields = {f.name for f in cls_fields}
        filtered = {k: v for k, v in data.items() if k in valid_fields}

        # Backward compat: convert int tmux_window to str
        if 'tmux_window' in filtered and isinstance(filtered['tmux_window'], int):
            filtered['tmux_window'] = str(filtered['tmux_window'])

        # Backward compat: migrate time_context_enabled → enhanced_context_enabled (#378)
        if 'enhanced_context_enabled' not in filtered and 'time_context_enabled' in data:
            filtered['enhanced_context_enabled'] = data['time_context_enabled']

        try:
            return cls(**filtered)
        except TypeError:
            # Type mismatch or other issue - session is corrupt
            return None


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
        self.archive_file = self.state_dir / "archive.json"
        self._skip_git_detection = skip_git_detection

    def _load_state(self) -> Dict[str, dict]:
        """Load all sessions from state file with file locking.

        On JSON corruption, attempts to restore from backup automatically.
        """
        if not self.state_file.exists():
            return {}

        max_retries = 5
        retry_delay = 0.1

        for attempt in range(max_retries):
            try:
                with open(self.state_file, 'r') as f:
                    if HAS_FCNTL:
                        # Acquire shared lock for reading
                        fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                        try:
                            return json.load(f)
                        finally:
                            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    else:
                        # No locking on Windows
                        return json.load(f)
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
                            return json.load(f)
                    except json.JSONDecodeError:
                        print("Warning: Backup file also corrupted, starting fresh")
                        return {}
                else:
                    print("Warning: No backup available, starting fresh")
                    return {}
            except IOError as e:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                print(f"Warning: Could not load state file: {e}")
                return {}

        return {}

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
        if not HAS_FCNTL:
            # No locking on Windows - fall back to read/modify/write
            state = self._load_state()
            yield state
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
            yield state
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
        """Detect git repo and branch from directory"""
        if not directory:
            return None, None

        # Check directory exists
        if not os.path.isdir(directory):
            return None, None

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

        repo_name, branch = self._detect_git_context(session.start_directory)

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
                      extra_claude_args: Optional[List[str]] = None,
                      agent_teams: bool = False,
                      claude_agent: Optional[str] = None,
                      model: Optional[str] = None,
                      provider: str = "web",
                      session_id: Optional[str] = None,
                      wrapper: Optional[str] = None,
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
            extra_claude_args: Extra Claude CLI flags via --claude-arg
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
            extra_claude_args=extra_claude_args or [],
            agent_teams=agent_teams,
            claude_agent=claude_agent,
            model=model,
            provider=provider,
            wrapper=wrapper,
            launcher_version=launcher_version,
        )

        with self._locked_state() as state:
            state[session.id] = session.to_dict()

        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        """Get a session by ID"""
        state = self._load_state()
        if session_id in state:
            return Session.from_dict(state[session_id])
        return None

    def get_session_by_name(self, name: str) -> Optional[Session]:
        """Get a session by name"""
        state = self._load_state()
        for session_data in state.values():
            if session_data['name'] == name:
                return Session.from_dict(session_data)
        return None

    def list_sessions(self) -> List[Session]:
        """List all sessions (skips corrupted entries)"""
        state = self._load_state()
        sessions = [Session.from_dict(data) for data in state.values()]
        # Filter out None (corrupted sessions)
        return [s for s in sessions if s is not None]

    def update_session_status(self, session_id: str, status: str):
        """Update session status"""
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

    def _load_archive(self) -> Dict[str, dict]:
        """Load archived sessions."""
        if not self.archive_file.exists():
            return {}

        try:
            with open(self.archive_file, 'r') as f:
                if HAS_FCNTL:
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                    try:
                        return json.load(f)
                    finally:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                else:
                    return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    def _save_archive(self, archive: Dict[str, dict]):
        """Save archived sessions."""
        import threading
        if HAS_FCNTL:
            temp_suffix = f'.tmp.{os.getpid()}.{threading.get_ident()}'
            temp_file = self.archive_file.with_suffix(temp_suffix)
            try:
                with open(temp_file, 'w') as f:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                    try:
                        json.dump(archive, f, indent=2)
                        f.flush()
                        os.fsync(f.fileno())
                    finally:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                temp_file.rename(self.archive_file)
            finally:
                if temp_file.exists():
                    temp_file.unlink()
        else:
            with open(self.archive_file, 'w') as f:
                json.dump(archive, f, indent=2)

    def _archive_session(self, session_data: dict):
        """Add a session to the archive."""
        archive = self._load_archive()
        archive[session_data['id']] = session_data
        self._save_archive(archive)

    def list_archived_sessions(self) -> List[Session]:
        """List all archived sessions (skips corrupted entries)."""
        archive = self._load_archive()
        sessions = []
        for data in archive.values():
            try:
                # Handle end_time field that's not in Session dataclass
                data_copy = data.copy()
                end_time = data_copy.pop('end_time', None)
                session = Session.from_dict(data_copy)
                if session is None:
                    continue
                # Store end_time as attribute for display
                session._end_time = end_time  # type: ignore
                sessions.append(session)
            except (KeyError, TypeError):
                continue
        return sessions

    def get_archived_session(self, session_id: str) -> Optional[Session]:
        """Get an archived session by ID."""
        archive = self._load_archive()
        if session_id in archive:
            data = archive[session_id].copy()
            end_time = data.pop('end_time', None)
            session = Session.from_dict(data)
            if session is None:
                return None
            session._end_time = end_time  # type: ignore
            return session
        return None

    def update_session(self, session_id: str, **kwargs):
        """Update session fields"""
        with self._locked_state() as state:
            if session_id in state:
                state[session_id].update(kwargs)

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

    def add_claude_session_id(self, session_id: str, claude_session_id: str) -> bool:
        """Add a Claude sessionId to a session's owned list if not already present.

        This tracks which Claude sessionIds belong to this overcode agent,
        enabling accurate context window calculation when multiple agents
        run in the same directory (#119).

        Args:
            session_id: The overcode session ID
            claude_session_id: The Claude Code sessionId to add

        Returns:
            True if the sessionId was added, False if already present or session not found
        """
        session = self.get_session(session_id)
        if not session or claude_session_id in session.claude_session_ids:
            return False

        with self._locked_state() as state:
            if session_id in state:
                ids = state[session_id].get('claude_session_ids', [])
                if claude_session_id not in ids:
                    ids.append(claude_session_id)
                    state[session_id]['claude_session_ids'] = ids
        return True

    def set_active_claude_session_id(self, session_id: str, claude_session_id: str):
        """Set the active Claude session ID for context tracking (#116).

        Unlike add_claude_session_id which accumulates, this replaces the
        active session. After /clear, Claude creates a new session and only
        that session's context window is relevant.
        """
        session = self.get_session(session_id)
        if not session:
            return

        with self._locked_state() as state:
            if session_id in state:
                state[session_id]['active_claude_session_id'] = claude_session_id

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

        Returns list ordered from immediate parent to root.
        """
        chain = []
        current_id = session_id
        visited = set()  # Cycle protection
        while current_id and current_id not in visited:
            visited.add(current_id)
            session = self.get_session(current_id)
            if not session or not session.parent_session_id:
                break
            parent = self.get_session(session.parent_session_id)
            if parent:
                chain.append(parent)
                current_id = parent.id
            else:
                break
        return chain

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
