"""
Job launcher for Overcode.

Launches bash commands as tracked jobs in a dedicated "jobs" tmux session.
"""

import os
import shlex
from datetime import datetime
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
        auto_name = name or _slugify_command(command)

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

        # Build wrapper script that runs the command and reports completion
        escaped_cmd = command.replace("'", "'\\''")
        escaped_dir = shlex.quote(directory)
        wrapper = (
            f"echo '╭─── Job: {job.name}' && "
            f"echo '│ Command: {command}' && "
            f"echo '│ Directory: {directory}' && "
            f"echo '│ Started: {job.start_time}' && "
            f"echo '╰───────────────────────────────────' && "
            f"echo '' && "
            f"cd {escaped_dir} && "
            f"'{escaped_cmd}'; "
            f"__oc_exit=$?; "
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
        """List jobs, cross-referencing tmux to detect killed jobs."""
        jobs = self.jobs.list_jobs(include_completed=include_completed)

        if detect_killed:
            # Get list of actual tmux windows
            windows = self.tmux.list_windows()
            window_names = {w['name'] for w in windows}

            for job in jobs:
                if job.status == "running" and job.tmux_window not in window_names:
                    # Window gone but no _complete called → killed
                    self.jobs.update_job(
                        job.id,
                        status="killed",
                        end_time=datetime.now().isoformat(),
                    )
                    job.status = "killed"
                    job.end_time = datetime.now().isoformat()

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
