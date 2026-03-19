"""
Unit tests for JobSummary widget.
"""

import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.job_manager import Job
from overcode.tui_widgets.job_summary import JobSummary
from rich.text import Text


class TestJobSummaryRender:
    """Test JobSummary widget rendering."""

    def test_running_job_renders(self):
        job = Job(
            id="abc",
            name="unit-tests",
            command="pytest tests/",
            status="running",
            start_time="2026-01-01T00:00:00",
        )
        widget = JobSummary(job)
        text = widget.render()
        assert isinstance(text, Text)
        plain = text.plain
        assert "unit-tests" in plain
        assert "pytest tests/" in plain
        assert "running" in plain

    def test_completed_job_renders(self):
        job = Job(
            id="abc",
            name="build",
            command="make build",
            status="completed",
            exit_code=0,
            start_time="2026-01-01T00:00:00",
            end_time="2026-01-01T00:05:00",
        )
        widget = JobSummary(job)
        text = widget.render()
        plain = text.plain
        assert "build" in plain
        assert "completed" in plain
        assert "(0)" in plain

    def test_failed_job_renders(self):
        job = Job(
            id="abc",
            name="deploy",
            command="make deploy",
            status="failed",
            exit_code=1,
            start_time="2026-01-01T00:00:00",
            end_time="2026-01-01T00:01:00",
        )
        widget = JobSummary(job)
        text = widget.render()
        plain = text.plain
        assert "deploy" in plain
        assert "failed" in plain
        assert "(1)" in plain

    def test_agent_link_shown(self):
        job = Job(
            id="abc",
            name="test",
            command="pytest",
            status="running",
            start_time="2026-01-01T00:00:00",
            agent_name="my-agent",
        )
        widget = JobSummary(job)
        text = widget.render()
        assert "my-agent" in text.plain

    def test_emoji_free_mode(self):
        job = Job(
            id="abc",
            name="test",
            command="echo hi",
            status="running",
            start_time="2026-01-01T00:00:00",
        )
        widget = JobSummary(job)
        widget.emoji_free = True
        text = widget.render()
        # Should use ASCII instead of Unicode
        assert "●" not in text.plain

    def test_monochrome_mode(self):
        job = Job(
            id="abc",
            name="test",
            command="echo hi",
            status="running",
            start_time="2026-01-01T00:00:00",
        )
        widget = JobSummary(job)
        widget.monochrome = True
        text = widget.render()
        assert isinstance(text, Text)

    def test_long_command_truncated(self):
        long_cmd = "a" * 100
        job = Job(
            id="abc",
            name="test",
            command=long_cmd,
            status="running",
            start_time="2026-01-01T00:00:00",
        )
        widget = JobSummary(job)
        text = widget.render()
        plain = text.plain
        assert "…" in plain

    def test_refresh_job(self):
        job1 = Job(id="abc", name="test", command="echo 1", status="running",
                    start_time="2026-01-01T00:00:00")
        job2 = Job(id="abc", name="test", command="echo 1", status="completed",
                    exit_code=0, start_time="2026-01-01T00:00:00", end_time="2026-01-01T00:01:00")

        widget = JobSummary(job1)
        assert widget.job.status == "running"

        widget.refresh_job(job2)
        assert widget.job.status == "completed"

    def test_duration_formatting(self):
        job = Job(
            id="abc",
            name="test",
            command="sleep 3700",
            status="completed",
            exit_code=0,
            start_time="2026-01-01T00:00:00",
            end_time="2026-01-01T01:01:40",
        )
        widget = JobSummary(job)
        text = widget.render()
        plain = text.plain
        # 1h01m
        assert "1h01m" in plain
