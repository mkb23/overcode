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

    @patch("overcode.job_launcher.JobLauncher.attach")
    @patch("overcode.job_launcher.JobLauncher.launch")
    @patch("overcode.job_launcher.JobLauncher.__init__", return_value=None)
    def test_bash_launches_job(self, mock_init, mock_launch, mock_attach):
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
        assert "1 running" in result.output

    @patch("overcode.job_launcher.JobLauncher.list_jobs")
    @patch("overcode.job_launcher.JobLauncher.__init__", return_value=None)
    def test_list_all_summarises_running_and_finished(self, mock_init, mock_list):
        done = _mock_job()
        done.status = "completed"
        done.exit_code = 0
        done.end_time = done.start_time
        mock_list.return_value = [_mock_job(), done]

        result = runner.invoke(app, ["jobs", "list", "--all"])

        assert result.exit_code == 0
        assert "1 running, 1 finished" in result.output


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


class TestWaitCommand:
    """'overcode jobs wait': a watcher that never outlives its job (#515)."""

    def _run(self, states, panes, args):
        """Run ``jobs wait`` as each poll sees the next job state and pane."""
        states, panes = list(states), list(panes)

        def list_jobs(self, include_completed=False):
            job = states.pop(0) if len(states) > 1 else states[0]
            return [job] if job is not None else []

        def capture(session, window, lines=0):
            return panes.pop(0) if len(panes) > 1 else panes[0]

        with patch("overcode.job_launcher.JobLauncher.__init__", return_value=None), \
             patch("overcode.job_launcher.JobLauncher.list_jobs", list_jobs), \
             patch("overcode.follow_mode._capture_pane", capture), \
             patch("time.sleep"):
            return runner.invoke(app, ["jobs", "wait", "test-job", *args])

    def test_returns_when_the_pattern_appears(self):
        result = self._run([_mock_job()], ["booting", "booting\nListening on :8080"],
                           ["--until", "Listening on"])
        assert result.exit_code == 0
        assert "Listening on :8080" in result.output

    def test_the_echoed_command_is_not_a_match(self):
        pane = "│ Command: serve && echo Listening on\nstarting"
        result = self._run([_mock_job(), _mock_job(status="failed", exit_code=2)], [pane],
                           ["--until", "Listening on"])
        assert result.exit_code == 1

    def test_a_job_that_ends_without_a_match_fails(self):
        result = self._run([_mock_job(), _mock_job(status="completed", exit_code=0)],
                           ["bash: syntax error"], ["--until", "PASSED"])
        assert result.exit_code == 1
        assert "without a match" in result.output

    def test_a_match_in_the_last_output_still_counts(self):
        result = self._run([_mock_job(status="completed", exit_code=0)],
                           ["... 3 PASSED"], ["--until", "PASSED"])
        assert result.exit_code == 0

    def test_without_until_waits_for_the_end(self):
        ok = self._run([_mock_job(), _mock_job(status="completed", exit_code=0)], [""], [])
        failed = self._run([_mock_job(), _mock_job(status="killed")], [""], [])
        assert (ok.exit_code, failed.exit_code) == (0, 1)

    def test_missing_job(self):
        assert self._run([None], [""], []).exit_code == 2

    def test_timeout(self):
        with patch("time.monotonic", side_effect=[0.0, 0.0, 100.0, 200.0]):
            result = self._run([_mock_job()], [""], ["--until", "x", "--timeout", "30s"])
        assert result.exit_code == 124

    def test_bad_regex(self):
        assert self._run([_mock_job()], [""], ["--until", "("]).exit_code == 2
