"""
Unit tests for CLI jobs commands.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from typer.testing import CliRunner

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.cli._shared import app


runner = CliRunner()


def _mock_job(**kwargs):
    """Create a mock job with sensible defaults."""
    from overcode.job_manager import Job
    defaults = dict(
        id="abc", name="test-job", command="echo hi", status="running",
        tmux_session="jobs", tmux_window="test-job", start_time="2026-01-01T00:00:00",
        agent_name=None, agent_session_id=None,
    )
    defaults.update(kwargs)
    return Job(**defaults)


class TestBashCommand:
    """Test the 'overcode bash' command."""

    @patch("overcode.job_launcher.JobLauncher.launch")
    @patch("overcode.job_launcher.JobLauncher.__init__", return_value=None)
    def test_bash_launches_job(self, mock_init, mock_launch):
        mock_launch.return_value = _mock_job()

        result = runner.invoke(app, ["bash", "echo hi", "--name", "test-job"])

        assert result.exit_code == 0
        assert "test-job" in result.output
        mock_launch.assert_called_once()


class TestJobsListCommand:
    """Test the 'overcode jobs list' command."""

    @patch("overcode.job_launcher.JobLauncher.list_jobs")
    @patch("overcode.job_launcher.JobLauncher.__init__", return_value=None)
    def test_list_empty(self, mock_init, mock_list):
        mock_list.return_value = []

        result = runner.invoke(app, ["jobs", "list"])

        assert result.exit_code == 0
        assert "No jobs" in result.output

    @patch("overcode.job_launcher.JobLauncher.list_jobs")
    @patch("overcode.job_launcher.JobLauncher.__init__", return_value=None)
    def test_list_shows_jobs(self, mock_init, mock_list):
        mock_list.return_value = [_mock_job()]

        result = runner.invoke(app, ["jobs", "list"])

        assert result.exit_code == 0
        assert "test-job" in result.output


class TestJobsKillCommand:
    """Test the 'overcode jobs kill' command."""

    @patch("overcode.job_launcher.JobLauncher.kill_job")
    @patch("overcode.job_launcher.JobLauncher.__init__", return_value=None)
    def test_kill_success(self, mock_init, mock_kill):
        mock_kill.return_value = True

        result = runner.invoke(app, ["jobs", "kill", "test-job"])

        assert result.exit_code == 0
        assert "killed" in result.output.lower()

    @patch("overcode.job_launcher.JobLauncher.kill_job")
    @patch("overcode.job_launcher.JobLauncher.__init__", return_value=None)
    def test_kill_failure(self, mock_init, mock_kill):
        mock_kill.return_value = False

        result = runner.invoke(app, ["jobs", "kill", "test-job"])

        assert result.exit_code == 1


class TestJobsClearCommand:
    """Test the 'overcode jobs clear' command."""

    @patch("overcode.job_manager.JobManager.clear_completed")
    @patch("overcode.job_manager.JobManager.__init__", return_value=None)
    def test_clear(self, mock_init, mock_clear):
        result = runner.invoke(app, ["jobs", "clear"])

        assert result.exit_code == 0
        assert "Cleared" in result.output
        mock_clear.assert_called_once()


class TestJobsCompleteCommand:
    """Test the 'overcode jobs _complete' internal command."""

    @patch("overcode.job_manager.JobManager.mark_complete")
    @patch("overcode.job_manager.JobManager.__init__", return_value=None)
    def test_mark_complete(self, mock_init, mock_mark):
        result = runner.invoke(app, ["jobs", "_complete", "job-id-123", "0"])

        assert result.exit_code == 0
        mock_mark.assert_called_once_with("job-id-123", 0)
