"""
Unit tests for JobManager.
"""

import json
import pytest
from pathlib import Path
from datetime import datetime, timedelta

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.job_manager import JobManager, Job, _slugify_command


class TestSlugifyCommand:
    """Test auto-naming from command strings."""

    def test_simple_command(self):
        assert _slugify_command("pytest tests/") == "pytest-tests"

    def test_with_flags(self):
        assert _slugify_command("pytest -x -q tests/unit/") == "pytest-unit"

    def test_npm_command(self):
        assert _slugify_command("npm run build") == "npm-run-build"

    def test_long_command_truncates(self):
        result = _slugify_command("a" * 50)
        assert len(result) <= 30

    def test_empty_command(self):
        assert _slugify_command("") == "job"

    def test_path_basename(self):
        assert _slugify_command("/usr/bin/make deploy") == "make-deploy"

    def test_strips_extensions(self):
        assert _slugify_command("run_tests.py unit") == "run-tests-unit"


class TestJobDataclass:
    """Test Job serialization."""

    def test_to_dict(self):
        job = Job(id="abc", name="test", command="echo hi")
        d = job.to_dict()
        assert d["id"] == "abc"
        assert d["name"] == "test"
        assert d["command"] == "echo hi"
        assert d["status"] == "running"

    def test_from_dict(self):
        d = {"id": "abc", "name": "test", "command": "echo hi", "status": "running"}
        job = Job.from_dict(d)
        assert job is not None
        assert job.id == "abc"
        assert job.name == "test"

    def test_from_dict_missing_required(self):
        d = {"id": "abc"}  # Missing name and command
        job = Job.from_dict(d)
        assert job is None

    def test_from_dict_ignores_unknown_fields(self):
        d = {"id": "abc", "name": "test", "command": "echo hi", "unknown_field": True}
        job = Job.from_dict(d)
        assert job is not None
        assert job.name == "test"

    def test_round_trip(self):
        job = Job(id="abc", name="test", command="echo hi", exit_code=0, status="completed")
        d = job.to_dict()
        job2 = Job.from_dict(d)
        assert job2 is not None
        assert job2.id == job.id
        assert job2.exit_code == 0
        assert job2.status == "completed"


class TestJobManagerCRUD:
    """Test basic CRUD operations."""

    def test_creates_state_directory(self, tmp_path):
        state_dir = tmp_path / "jobs"
        assert not state_dir.exists()
        JobManager(state_dir=state_dir)
        assert state_dir.exists()

    def test_create_job(self, tmp_path):
        manager = JobManager(state_dir=tmp_path)
        job = manager.create_job(command="pytest tests/", name="unit-tests")
        assert job.name == "unit-tests"
        assert job.command == "pytest tests/"
        assert job.status == "running"
        assert job.id is not None

    def test_create_job_auto_name(self, tmp_path):
        manager = JobManager(state_dir=tmp_path)
        job = manager.create_job(command="npm run build")
        assert job.name == "npm-run-build"

    def test_create_job_dedup_name(self, tmp_path):
        manager = JobManager(state_dir=tmp_path)
        job1 = manager.create_job(command="echo hi", name="test")
        job2 = manager.create_job(command="echo hi", name="test")
        assert job1.name == "test"
        assert job2.name == "test-2"

    def test_get_job(self, tmp_path):
        manager = JobManager(state_dir=tmp_path)
        created = manager.create_job(command="echo hi", name="test")
        retrieved = manager.get_job(created.id)
        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.name == "test"

    def test_get_job_by_name(self, tmp_path):
        manager = JobManager(state_dir=tmp_path)
        manager.create_job(command="echo hi", name="my-job")
        retrieved = manager.get_job_by_name("my-job")
        assert retrieved is not None
        assert retrieved.name == "my-job"

    def test_get_job_not_found(self, tmp_path):
        manager = JobManager(state_dir=tmp_path)
        assert manager.get_job("nonexistent") is None

    def test_list_jobs_excludes_completed(self, tmp_path):
        manager = JobManager(state_dir=tmp_path)
        job1 = manager.create_job(command="echo 1", name="running-job")
        job2 = manager.create_job(command="echo 2", name="done-job")
        manager.mark_complete(job2.id, 0)

        jobs = manager.list_jobs(include_completed=False)
        assert len(jobs) == 1
        assert jobs[0].name == "running-job"

    def test_list_jobs_includes_completed(self, tmp_path):
        manager = JobManager(state_dir=tmp_path)
        manager.create_job(command="echo 1", name="running-job")
        job2 = manager.create_job(command="echo 2", name="done-job")
        manager.mark_complete(job2.id, 0)

        jobs = manager.list_jobs(include_completed=True)
        assert len(jobs) == 2

    def test_update_job(self, tmp_path):
        manager = JobManager(state_dir=tmp_path)
        job = manager.create_job(command="echo hi", name="test")
        manager.update_job(job.id, tmux_window="win-1")
        retrieved = manager.get_job(job.id)
        assert retrieved.tmux_window == "win-1"

    def test_delete_job(self, tmp_path):
        manager = JobManager(state_dir=tmp_path)
        job = manager.create_job(command="echo hi", name="test")
        manager.delete_job(job.id)
        assert manager.get_job(job.id) is None

    def test_mark_complete_success(self, tmp_path):
        manager = JobManager(state_dir=tmp_path)
        job = manager.create_job(command="echo hi", name="test")
        manager.mark_complete(job.id, 0)
        retrieved = manager.get_job(job.id)
        assert retrieved.status == "completed"
        assert retrieved.exit_code == 0
        assert retrieved.end_time is not None

    def test_mark_complete_failure(self, tmp_path):
        manager = JobManager(state_dir=tmp_path)
        job = manager.create_job(command="false", name="test")
        manager.mark_complete(job.id, 1)
        retrieved = manager.get_job(job.id)
        assert retrieved.status == "failed"
        assert retrieved.exit_code == 1


class TestJobManagerCleanup:
    """Test cleanup operations."""

    def test_clear_completed(self, tmp_path):
        manager = JobManager(state_dir=tmp_path)
        job1 = manager.create_job(command="echo 1", name="running")
        job2 = manager.create_job(command="echo 2", name="done")
        manager.mark_complete(job2.id, 0)

        manager.clear_completed()

        assert manager.get_job(job1.id) is not None
        assert manager.get_job(job2.id) is None

    def test_cleanup_completed_respects_retention(self, tmp_path):
        manager = JobManager(state_dir=tmp_path)

        # Create a "completed" job with end_time in the past
        job = manager.create_job(command="echo old", name="old-job")
        old_time = (datetime.now() - timedelta(hours=25)).isoformat()
        manager.update_job(job.id, status="completed", exit_code=0, end_time=old_time)

        # Create a recent completed job
        job2 = manager.create_job(command="echo new", name="new-job")
        manager.mark_complete(job2.id, 0)

        manager.cleanup_completed(retention_hours=24)

        # Old job should be gone, new job should remain
        assert manager.get_job(job.id) is None
        assert manager.get_job(job2.id) is not None

    def test_cleanup_ignores_running_jobs(self, tmp_path):
        manager = JobManager(state_dir=tmp_path)
        job = manager.create_job(command="sleep 999", name="running")
        manager.cleanup_completed(retention_hours=0)
        assert manager.get_job(job.id) is not None


class TestJobManagerPersistence:
    """Test state file persistence."""

    def test_state_file_created(self, tmp_path):
        manager = JobManager(state_dir=tmp_path)
        manager.create_job(command="echo hi", name="test")
        assert (tmp_path / "jobs.json").exists()

    def test_state_persists_across_instances(self, tmp_path):
        manager1 = JobManager(state_dir=tmp_path)
        job = manager1.create_job(command="echo hi", name="test")

        manager2 = JobManager(state_dir=tmp_path)
        retrieved = manager2.get_job(job.id)
        assert retrieved is not None
        assert retrieved.name == "test"

    def test_empty_state_file(self, tmp_path):
        manager = JobManager(state_dir=tmp_path)
        jobs = manager.list_jobs()
        assert jobs == []

    def test_corrupt_state_file(self, tmp_path):
        state_file = tmp_path / "jobs.json"
        state_file.write_text("not valid json{{{")
        manager = JobManager(state_dir=tmp_path)
        jobs = manager.list_jobs()
        assert jobs == []
