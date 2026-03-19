"""
Job state management for Overcode.

Tracks bash jobs running in a separate "jobs" tmux session.
Mirrors SessionManager patterns for atomic writes and file locking.
"""

import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import MISSING, asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .exceptions import StateWriteError

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False


def _slugify_command(command: str) -> str:
    """Auto-generate a short name from a command string.

    Takes the first 2-3 meaningful tokens and slugifies them.
    """
    # Strip leading env vars, cd, etc.
    tokens = command.strip().split()
    # Skip env var assignments (FOO=bar)
    meaningful = [t for t in tokens if "=" not in t or t.startswith("-")]
    if not meaningful:
        meaningful = tokens

    # Take first 3 tokens, skip flags
    name_parts = []
    for t in meaningful[:4]:
        if t.startswith("-"):
            continue
        # Strip path prefixes, handle trailing slashes
        base = os.path.basename(t.rstrip("/"))
        # Remove common extensions
        base = re.sub(r'\.(sh|py|js|ts|bash)$', '', base)
        if base:
            name_parts.append(base)
        if len(name_parts) >= 3:
            break

    if not name_parts:
        name_parts = ["job"]

    slug = "-".join(name_parts).lower()
    # Clean non-alphanumeric
    slug = re.sub(r'[^a-z0-9-]', '-', slug)
    slug = re.sub(r'-+', '-', slug).strip('-')
    return slug[:30] or "job"


@dataclass
class Job:
    """Represents a tracked bash job."""
    id: str
    name: str
    command: str
    tmux_session: str = "jobs"
    tmux_window: str = ""
    start_directory: str = ""
    start_time: str = ""
    end_time: Optional[str] = None
    exit_code: Optional[int] = None
    status: str = "running"  # running | completed | failed | killed
    agent_session_id: Optional[str] = None
    agent_name: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Optional['Job']:
        """Create Job from dict, handling unknown/invalid fields gracefully.

        Returns None if required fields are missing or data is corrupt.
        """
        cls_fields = fields(cls)
        required = {
            f.name for f in cls_fields
            if f.default is MISSING and f.default_factory is MISSING  # type: ignore[comparison-overlap]
        }
        if not all(k in data for k in required):
            return None

        valid_fields = {f.name for f in cls_fields}
        filtered = {k: v for k, v in data.items() if k in valid_fields}

        try:
            return cls(**filtered)
        except TypeError:
            return None


class JobManager:
    """Manages job state persistence.

    State file: ~/.overcode/jobs/jobs.json
    Uses same atomic write + fcntl locking pattern as SessionManager.
    """

    def __init__(self, state_dir: Optional[Path] = None):
        if state_dir is None:
            env_state_dir = os.environ.get("OVERCODE_STATE_DIR")
            if env_state_dir:
                state_dir = Path(env_state_dir) / "jobs"
            else:
                state_dir = Path.home() / ".overcode" / "jobs"
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.state_dir / "jobs.json"

    def _load_state(self) -> Dict[str, dict]:
        """Load all jobs from state file with file locking."""
        if not self.state_file.exists():
            return {}

        max_retries = 5
        retry_delay = 0.1

        for attempt in range(max_retries):
            try:
                with open(self.state_file, 'r') as f:
                    if HAS_FCNTL:
                        fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                        try:
                            return json.load(f)
                        finally:
                            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    else:
                        return json.load(f)
            except json.JSONDecodeError:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                return {}
            except IOError:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                return {}

        return {}

    def _save_state(self, state: Dict[str, dict]):
        """Save all jobs to state file with file locking and atomic writes."""
        import threading
        max_retries = 5
        retry_delay = 0.1

        for attempt in range(max_retries):
            try:
                if HAS_FCNTL:
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
                        temp_file.rename(self.state_file)
                    finally:
                        if temp_file.exists():
                            temp_file.unlink()
                else:
                    with open(self.state_file, 'w') as f:
                        json.dump(state, f, indent=2)
                return
            except (IOError, OSError) as e:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                raise StateWriteError(f"Failed to save jobs state after {max_retries} attempts: {e}")

    @contextmanager
    def _locked_state(self):
        """Load state under file lock, yield it, save on exit."""
        if not HAS_FCNTL:
            state = self._load_state()
            yield state
            self._save_state(state)
            return

        max_retries = 5
        retry_delay = 0.1
        f = None

        for attempt in range(max_retries):
            try:
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
                raise StateWriteError(f"Failed to load jobs state after {max_retries} attempts: {e}")

        try:
            yield state
            f.seek(0)
            f.truncate()
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        except (IOError, OSError) as e:
            raise StateWriteError(f"Failed to save jobs state: {e}")
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            f.close()

    def _unique_name(self, base_name: str) -> str:
        """Ensure name is unique by appending -2, -3, etc. if needed."""
        state = self._load_state()
        existing_names = {d.get('name') for d in state.values()}
        if base_name not in existing_names:
            return base_name
        for i in range(2, 100):
            candidate = f"{base_name}-{i}"
            if candidate not in existing_names:
                return candidate
        return f"{base_name}-{uuid.uuid4().hex[:6]}"

    def create_job(self, command: str, name: Optional[str] = None,
                   tmux_window: str = "", start_directory: str = "",
                   agent_session_id: Optional[str] = None,
                   agent_name: Optional[str] = None) -> Job:
        """Create and persist a new job."""
        if not name:
            name = self._unique_name(_slugify_command(command))
        else:
            name = self._unique_name(name)

        job = Job(
            id=str(uuid.uuid4()),
            name=name,
            command=command,
            tmux_window=tmux_window,
            start_directory=start_directory,
            start_time=datetime.now().isoformat(),
            agent_session_id=agent_session_id,
            agent_name=agent_name,
        )

        with self._locked_state() as state:
            state[job.id] = job.to_dict()

        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        """Get a job by ID."""
        state = self._load_state()
        if job_id in state:
            return Job.from_dict(state[job_id])
        return None

    def get_job_by_name(self, name: str) -> Optional[Job]:
        """Get a job by name."""
        state = self._load_state()
        for data in state.values():
            if data.get('name') == name:
                return Job.from_dict(data)
        return None

    def list_jobs(self, include_completed: bool = False) -> List[Job]:
        """List jobs, optionally including completed/failed/killed."""
        state = self._load_state()
        jobs = []
        for data in state.values():
            job = Job.from_dict(data)
            if job is None:
                continue
            if not include_completed and job.status in ("completed", "failed", "killed"):
                continue
            jobs.append(job)
        return jobs

    def update_job(self, job_id: str, **kwargs):
        """Update job fields."""
        with self._locked_state() as state:
            if job_id in state:
                state[job_id].update(kwargs)

    def delete_job(self, job_id: str):
        """Delete a job from state."""
        with self._locked_state() as state:
            if job_id in state:
                del state[job_id]

    def mark_complete(self, job_id: str, exit_code: int):
        """Mark a job as completed or failed based on exit code."""
        status = "completed" if exit_code == 0 else "failed"
        with self._locked_state() as state:
            if job_id in state:
                state[job_id]['status'] = status
                state[job_id]['exit_code'] = exit_code
                state[job_id]['end_time'] = datetime.now().isoformat()

    def cleanup_completed(self, retention_hours: float = 24):
        """Remove completed/failed/killed jobs older than retention period."""
        cutoff = datetime.now().timestamp() - (retention_hours * 3600)

        with self._locked_state() as state:
            to_delete = []
            for job_id, data in state.items():
                if data.get('status') in ('completed', 'failed', 'killed'):
                    end_time = data.get('end_time')
                    if end_time:
                        try:
                            end_ts = datetime.fromisoformat(end_time).timestamp()
                            if end_ts < cutoff:
                                to_delete.append(job_id)
                        except ValueError:
                            continue
            for job_id in to_delete:
                del state[job_id]

    def clear_completed(self):
        """Remove all completed/failed/killed jobs immediately."""
        with self._locked_state() as state:
            to_delete = [
                jid for jid, data in state.items()
                if data.get('status') in ('completed', 'failed', 'killed')
            ]
            for jid in to_delete:
                del state[jid]
