"""
Job launcher for Overcode.

Launches bash commands as tracked jobs in a dedicated "jobs" tmux session.
"""

import os
import shlex
import subprocess
import uuid as _uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .job_manager import Job, JobManager, _slugify_command
from .tmux_manager import TmuxManager


class JobLauncher:
    """Launches and manages bash jobs in a dedicated tmux session."""

    def __init__(
        self,
        tmux_session: str = "jobs",
        tmux_manager: Optional[TmuxManager] = None,
        job_manager: Optional[JobManager] = None,
    ):
        self.tmux_session = tmux_session
        self.tmux = tmux_manager or TmuxManager(session_name=tmux_session)
        self.jobs = job_manager or JobManager()

    def launch(
        self,
        command: str,
        name: Optional[str] = None,
        directory: Optional[str] = None,
        agent_session_id: Optional[str] = None,
        agent_name: Optional[str] = None,
    ) -> Job:
        """Launch a bash command as a tracked job.

        Creates a tmux window, sends a wrapper script that reports completion,
        and persists the job to state.
        """
        directory = directory or os.getcwd()
        base_name = name or _slugify_command(command)
        auto_name = f"{base_name}-{_uuid.uuid4().hex[:4]}"

        # Create job first to get the ID
        job = self.jobs.create_job(
            command=command,
            name=auto_name,
            start_directory=directory,
            agent_session_id=agent_session_id,
            agent_name=agent_name,
        )

        # Create tmux window
        window_name = job.name
        self.tmux.ensure_session()
        result = self.tmux.create_window(window_name, start_directory=directory)
        if result is None:
            # Cleanup on failure
            self.jobs.delete_job(job.id)
            raise RuntimeError(f"Failed to create tmux window for job '{job.name}'")

        # Update job with actual window name
        self.jobs.update_job(job.id, tmux_window=result)
        job.tmux_window = result

        # Build wrapper script that runs the command and reports completion.
        # The exit code is written to a file as a fallback — if the _complete
        # call doesn't run, the process monitor can still read it.
        escaped_cmd = command.replace("'", "'\\''")
        escaped_dir = shlex.quote(directory)
        exit_file = self._exit_code_path(job.id)
        wrapper = (
            f"echo '╭─── Job: {job.name}' && "
            f"echo '│ Command: {command}' && "
            f"echo '│ Directory: {directory}' && "
            f"echo '│ Started: {job.start_time}' && "
            f"echo '╰───────────────────────────────────' && "
            f"echo '' && "
            f"cd {escaped_dir} && "
            f"eval '{escaped_cmd}'; "
            f"__oc_exit=$?; "
            f"echo $__oc_exit > {exit_file}; "
            f"echo ''; "
            f"echo '╭─── Job finished ───'; "
            f"echo \"│ Exit code: $__oc_exit\"; "
            f"echo '╰───────────────────'; "
            f"overcode jobs _complete {job.id} $__oc_exit; "
            f"exec bash"
        )

        # Send the wrapper to tmux
        self.tmux.send_keys(result, wrapper)

        return job

    def list_jobs(self, include_completed: bool = False, detect_killed: bool = True) -> List[Job]:
        """List jobs, cross-referencing tmux to detect killed/completed jobs.

        Detection layers for running jobs:
        1. Window gone + no _complete call → killed
        2. Window alive but shell has no child processes → command finished,
           read exit code from file and mark completed/failed
        """
        jobs = self.jobs.list_jobs(include_completed=include_completed)

        if not detect_killed:
            return jobs

        # Get list of actual tmux windows.
        # If the list is empty, it's likely a query failure (stale libtmux
        # session cache or transient error) rather than all windows being
        # gone — skip kill detection entirely to avoid false kills (#396).
        windows = self.tmux.list_windows()
        if not windows:
            return jobs
        window_names = {w['name'] for w in windows}

        now = datetime.now()
        for job in jobs:
            if job.status != "running":
                continue

            if job.tmux_window not in window_names:
                # Grace period: don't mark as killed until the job has been
                # "missing" for at least 30s, to avoid race conditions during
                # window creation (#396).
                try:
                    age = (now - datetime.fromisoformat(job.start_time)).total_seconds()
                except (ValueError, TypeError):
                    age = 0
                if age < 30:
                    continue
                # Window gone but no _complete called → killed
                self.jobs.update_job(
                    job.id,
                    status="killed",
                    end_time=now.isoformat(),
                )
                job.status = "killed"
                job.end_time = now.isoformat()
                continue

            # Window still alive — check if the command has finished by
            # looking for child processes of the pane's shell.
            pane_pid = self.tmux.get_pane_pid(job.tmux_window)
            if pane_pid is not None and not _has_child_processes(pane_pid):
                exit_code = self._read_exit_code(job.id)
                status = "completed" if exit_code == 0 else "failed"
                now = datetime.now().isoformat()
                self.jobs.mark_complete(job.id, exit_code)
                job.status = status
                job.exit_code = exit_code
                job.end_time = now
                self._cleanup_exit_code_file(job.id)

        return jobs

    def kill_job(self, name: str) -> bool:
        """Kill a job by name."""
        job = self.jobs.get_job_by_name(name)
        if not job:
            return False

        if job.status != "running":
            return False

        # Kill the tmux window
        killed = self.tmux.kill_window(job.tmux_window)

        # Update state
        self.jobs.update_job(
            job.id,
            status="killed",
            end_time=datetime.now().isoformat(),
        )

        return killed

    def attach(self, name: str, bare: bool = False):
        """Attach terminal to a job's tmux window."""
        job = self.jobs.get_job_by_name(name)
        if not job:
            raise ValueError(f"Job '{name}' not found")

        self.tmux.attach_session(window=job.tmux_window, bare=bare)

    @staticmethod
    def _exit_code_path(job_id: str) -> Path:
        """Path to the exit code file for a job."""
        env_state_dir = os.environ.get("OVERCODE_STATE_DIR")
        base = Path(env_state_dir) / "jobs" if env_state_dir else Path.home() / ".overcode" / "jobs"
        return base / f"exit-{job_id}"

    def _read_exit_code(self, job_id: str) -> int:
        """Read exit code from file, defaulting to 0 if missing/unreadable."""
        path = self._exit_code_path(job_id)
        try:
            return int(path.read_text().strip())
        except (FileNotFoundError, ValueError, OSError):
            return 0

    def _cleanup_exit_code_file(self, job_id: str):
        """Remove the exit code file after reading it."""
        try:
            self._exit_code_path(job_id).unlink(missing_ok=True)
        except OSError:
            pass


def _has_child_processes(pid: int) -> bool:
    """Check if a process has any child processes."""
    try:
        result = subprocess.run(
            ["pgrep", "-P", str(pid)],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        # If we can't check, assume still running
        return True
