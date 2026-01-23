"""
Session state management for Overcode.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional
from dataclasses import dataclass, asdict, field, fields
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
    last_stats_update: Optional[str] = None  # ISO timestamp of last stats sync

    # State tracking
    current_state: str = "running"  # running, no_instructions, waiting_supervisor, waiting_user
    state_since: Optional[str] = None  # ISO timestamp when current state started
    green_time_seconds: float = 0.0  # time spent in "running" state
    non_green_time_seconds: float = 0.0  # time spent in non-running states
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
    tmux_window: int
    command: List[str]
    start_directory: Optional[str]
    start_time: str

    # Git context
    repo_name: Optional[str] = None
    branch: Optional[str] = None

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

    def to_dict(self) -> dict:
        data = asdict(self)
        # Convert stats to dict
        data['stats'] = self.stats.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Optional['Session']:
        """Create Session from dict, handling unknown/invalid fields gracefully.

        Returns None if required fields are missing or data is corrupt.
        """
        # Required fields that must be present
        required = {'id', 'name', 'tmux_session', 'tmux_window', 'command', 'start_directory', 'start_time'}
        if not all(k in data for k in required):
            return None

        # Handle stats separately
        if 'stats' in data and isinstance(data['stats'], dict):
            data['stats'] = SessionStats.from_dict(data['stats'])
        elif 'stats' not in data:
            data['stats'] = SessionStats()

        # Get valid field names and filter unknown keys
        valid_fields = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in valid_fields}

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

    def _atomic_update(self, update_fn: Callable[[Dict[str, dict]], Dict[str, dict]]) -> None:
        """Atomically read, modify, and write state with exclusive lock held throughout.

        This prevents TOCTOU race conditions by holding the lock during the entire
        read-modify-write cycle.

        Args:
            update_fn: Function that takes the current state dict and returns the updated state.
        """
        max_retries = 5
        retry_delay = 0.1

        for attempt in range(max_retries):
            try:
                if HAS_FCNTL:
                    # Use 'a+' to create file if missing, then seek to start
                    # This avoids TOCTOU race where file is created outside lock
                    with open(self.state_file, 'a+') as f:
                        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                        try:
                            f.seek(0)
                            content = f.read()
                            state = json.loads(content) if content.strip() else {}
                            state = update_fn(state)
                            f.seek(0)
                            f.truncate()
                            json.dump(state, f, indent=2)
                            f.flush()
                            os.fsync(f.fileno())
                        finally:
                            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                else:
                    # No locking on Windows - fall back to read/modify/write
                    state = self._load_state()
                    state = update_fn(state)
                    self._save_state(state)
                return
            except (IOError, OSError, json.JSONDecodeError) as e:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                raise StateWriteError(f"Failed to update state file after {max_retries} attempts: {e}")

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

    def create_session(self, name: str, tmux_session: str, tmux_window: int,
                      command: List[str], start_directory: Optional[str] = None,
                      standing_instructions: str = "",
                      permissiveness_mode: str = "normal") -> Session:
        """Create and register a new session.

        Args:
            name: Session name
            tmux_session: Name of the tmux session
            tmux_window: Tmux window index
            command: Command used to start the session
            start_directory: Working directory for the session
            standing_instructions: Initial standing instructions (e.g., from config)
            permissiveness_mode: Permission mode (normal, permissive, bypass)
        """
        if self._skip_git_detection:
            repo_name, branch = None, None
        else:
            repo_name, branch = self._detect_git_context(start_directory)

        session = Session(
            id=str(uuid.uuid4()),
            name=name,
            tmux_session=tmux_session,
            tmux_window=tmux_window,
            command=command,
            start_directory=start_directory,
            start_time=datetime.now().isoformat(),
            repo_name=repo_name,
            branch=branch,
            standing_instructions=standing_instructions,
            permissiveness_mode=permissiveness_mode
        )

        state = self._load_state()
        state[session.id] = session.to_dict()
        self._save_state(state)

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
        def do_update(state: Dict[str, dict]) -> Dict[str, dict]:
            if session_id in state:
                state[session_id]['status'] = status
            return state
        self._atomic_update(do_update)

    def delete_session(self, session_id: str, archive: bool = True):
        """Delete a session, optionally archiving it first.

        Args:
            session_id: The session ID to delete
            archive: If True (default), archive the session before removing
        """
        # Capture session data for archiving before the atomic update
        archived_data = None

        def do_delete(state: Dict[str, dict]) -> Dict[str, dict]:
            nonlocal archived_data
            if session_id in state:
                if archive:
                    # Capture data for archiving
                    archived_data = state[session_id].copy()
                    archived_data['end_time'] = datetime.now().isoformat()
                    archived_data['status'] = 'archived'
                del state[session_id]
            return state

        self._atomic_update(do_delete)

        # Archive after the atomic update (separate file, separate lock)
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
        def do_update(state: Dict[str, dict]) -> Dict[str, dict]:
            if session_id in state:
                state[session_id].update(kwargs)
            return state
        self._atomic_update(do_update)

    def update_stats(self, session_id: str, **stats_kwargs):
        """Update session statistics"""
        def do_update(state: Dict[str, dict]) -> Dict[str, dict]:
            if session_id in state:
                if 'stats' not in state[session_id]:
                    state[session_id]['stats'] = SessionStats().to_dict()
                state[session_id]['stats'].update(stats_kwargs)
            return state
        self._atomic_update(do_update)

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
