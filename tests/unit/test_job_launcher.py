"""
Unit tests for JobLauncher.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.job_launcher import JobLauncher
from overcode.job_manager import JobManager


class TestJobLauncherLaunch:
    """Test job launching."""

    def test_launch_creates_job(self, tmp_path):
        """Launch creates a job and calls tmux."""
        mock_tmux = MagicMock()
        mock_tmux.ensure_session.return_value = True
        mock_tmux.create_window.return_value = "test-job"
        mock_tmux.send_keys.return_value = True

        manager = JobManager(state_dir=tmp_path)
        launcher = JobLauncher(tmux_manager=mock_tmux, job_manager=manager)

        job = launcher.launch(command="pytest tests/", name="test-job", directory="/tmp")

        assert job.name.startswith("test-job-")
        assert job.command == "pytest tests/"
        assert job.status == "running"
        mock_tmux.ensure_session.assert_called_once()
        mock_tmux.create_window.assert_called_once()
        mock_tmux.send_keys.assert_called_once()

    def test_launch_auto_names(self, tmp_path):
        """Launch auto-generates name from command."""
        mock_tmux = MagicMock()
        mock_tmux.ensure_session.return_value = True
        mock_tmux.create_window.return_value = "npm-run-build"
        mock_tmux.send_keys.return_value = True

        manager = JobManager(state_dir=tmp_path)
        launcher = JobLauncher(tmux_manager=mock_tmux, job_manager=manager)

        job = launcher.launch(command="npm run build")

        assert job.name.startswith("npm-run-build-")

    def test_launch_cleans_up_on_tmux_failure(self, tmp_path):
        """If tmux window creation fails, job is deleted."""
        mock_tmux = MagicMock()
        mock_tmux.ensure_session.return_value = True
        mock_tmux.create_window.return_value = None

        manager = JobManager(state_dir=tmp_path)
        launcher = JobLauncher(tmux_manager=mock_tmux, job_manager=manager)

        with pytest.raises(RuntimeError, match="Failed to create tmux window"):
            launcher.launch(command="echo hi")

        # Job should not be persisted
        assert manager.list_jobs() == []

    def test_launch_with_agent_link(self, tmp_path):
        """Launch can link to an agent session."""
        mock_tmux = MagicMock()
        mock_tmux.ensure_session.return_value = True
        mock_tmux.create_window.return_value = "test"
        mock_tmux.send_keys.return_value = True

        manager = JobManager(state_dir=tmp_path)
        launcher = JobLauncher(tmux_manager=mock_tmux, job_manager=manager)

        job = launcher.launch(
            command="make test",
            agent_session_id="agent-123",
            agent_name="my-agent",
        )

        assert job.agent_session_id == "agent-123"
        assert job.agent_name == "my-agent"


class TestJobLauncherListJobs:
    """Test job listing with killed detection."""

    def test_list_detects_killed_jobs(self, tmp_path):
        """Jobs whose tmux windows are gone get marked as killed."""
        mock_tmux = MagicMock()
        # Return a non-empty window list (session is reachable) but without
        # the job's window — this proves the window was genuinely killed
        # rather than list_windows() failing (#396).
        mock_tmux.list_windows.return_value = [{"name": "bash", "index": 0}]

        manager = JobManager(state_dir=tmp_path)
        # Create a job manually (simulating prior launch)
        job = manager.create_job(command="sleep 999", name="test-job", tmux_window="test-job")
        # Backdate start_time past the 30s grace period (#396)
        manager.update_job(job.id, start_time="2020-01-01T00:00:00")

        launcher = JobLauncher(tmux_manager=mock_tmux, job_manager=manager)
        jobs = launcher.list_jobs(include_completed=True, detect_killed=True)

        assert len(jobs) == 1
        assert jobs[0].status == "killed"

    def test_list_skips_kill_detection_when_windows_empty(self, tmp_path):
        """Empty window list (query failure) should not mark jobs as killed (#396)."""
        mock_tmux = MagicMock()
        mock_tmux.list_windows.return_value = []  # Query failure / stale cache

        manager = JobManager(state_dir=tmp_path)
        manager.create_job(command="sleep 999", name="test-job", tmux_window="test-job")

        launcher = JobLauncher(tmux_manager=mock_tmux, job_manager=manager)
        jobs = launcher.list_jobs(include_completed=True, detect_killed=True)

        assert len(jobs) == 1
        assert jobs[0].status == "running"  # Not falsely killed

    def test_list_grace_period_protects_young_jobs(self, tmp_path):
        """Jobs younger than 30s should not be marked as killed (#396)."""
        mock_tmux = MagicMock()
        mock_tmux.list_windows.return_value = [{"name": "bash", "index": 0}]

        manager = JobManager(state_dir=tmp_path)
        # Fresh job (just created, within grace period)
        manager.create_job(command="sleep 999", name="test-job", tmux_window="test-job")

        launcher = JobLauncher(tmux_manager=mock_tmux, job_manager=manager)
        jobs = launcher.list_jobs(include_completed=True, detect_killed=True)

        assert len(jobs) == 1
        assert jobs[0].status == "running"  # Protected by grace period

    @patch("overcode.job_launcher._has_child_processes", return_value=True)
    def test_list_preserves_running_jobs_with_windows(self, mock_has_children, tmp_path):
        """Running jobs with existing windows and active children stay running."""
        mock_tmux = MagicMock()
        mock_tmux.list_windows.return_value = [{"name": "test-job", "index": 0}]
        mock_tmux.get_pane_pid.return_value = 12345

        manager = JobManager(state_dir=tmp_path)
        manager.create_job(command="sleep 999", name="test-job", tmux_window="test-job")

        launcher = JobLauncher(tmux_manager=mock_tmux, job_manager=manager)
        jobs = launcher.list_jobs(include_completed=True, detect_killed=True)

        assert len(jobs) == 1
        assert jobs[0].status == "running"


class TestJobLauncherKill:
    """Test job killing."""

    def test_kill_running_job(self, tmp_path):
        """Can kill a running job."""
        mock_tmux = MagicMock()
        mock_tmux.kill_window.return_value = True

        manager = JobManager(state_dir=tmp_path)
        manager.create_job(command="sleep 999", name="test-job", tmux_window="test-job")

        launcher = JobLauncher(tmux_manager=mock_tmux, job_manager=manager)
        result = launcher.kill_job("test-job")

        assert result is True
        job = manager.get_job_by_name("test-job")
        assert job.status == "killed"

    def test_kill_nonexistent_job(self, tmp_path):
        """Killing a nonexistent job returns False."""
        mock_tmux = MagicMock()
        manager = JobManager(state_dir=tmp_path)
        launcher = JobLauncher(tmux_manager=mock_tmux, job_manager=manager)

        result = launcher.kill_job("nonexistent")
        assert result is False

    def test_kill_completed_job_returns_false(self, tmp_path):
        """Can't kill an already completed job."""
        mock_tmux = MagicMock()
        manager = JobManager(state_dir=tmp_path)
        job = manager.create_job(command="echo done", name="done-job")
        manager.mark_complete(job.id, 0)

        launcher = JobLauncher(tmux_manager=mock_tmux, job_manager=manager)
        result = launcher.kill_job("done-job")
        assert result is False


class TestJobLauncherAttach:
    """Test job attachment."""

    def test_attach_calls_tmux(self, tmp_path):
        """Attach delegates to tmux manager."""
        mock_tmux = MagicMock()
        manager = JobManager(state_dir=tmp_path)
        manager.create_job(command="sleep 999", name="test-job", tmux_window="test-job")

        launcher = JobLauncher(tmux_manager=mock_tmux, job_manager=manager)
        launcher.attach("test-job")

        mock_tmux.attach_session.assert_called_once_with(window="test-job", bare=False)

    def test_attach_nonexistent_raises(self, tmp_path):
        """Attaching to a nonexistent job raises ValueError."""
        mock_tmux = MagicMock()
        manager = JobManager(state_dir=tmp_path)
        launcher = JobLauncher(tmux_manager=mock_tmux, job_manager=manager)

        with pytest.raises(ValueError, match="not found"):
            launcher.attach("nonexistent")
