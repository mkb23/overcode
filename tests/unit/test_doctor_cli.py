"""Tests for the `overcode doctor` CLI command.

The underlying health logic is covered by test_doctor.py. These tests cover
the CLI surface: table rendering, exit codes, verdict summary lines, and the
--fix restart path.
"""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from overcode.cli import app
from overcode.doctor import (
    AgentHealth,
    Finding,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    VERDICT_MISSING_SETTINGS,
    VERDICT_NO_CLAUDE,
    VERDICT_OK,
    VERDICT_REMOTE,
    VERDICT_WINDOW_GONE,
)
from overcode.session_manager import Session, SessionStats


runner = CliRunner()


def _make_session(name="agent-1", **overrides):
    defaults = dict(
        id=f"sid-{name}",
        name=name,
        tmux_session="agents",
        tmux_window=f"{name}-0",
        command=["claude"],
        start_directory="/tmp",
        start_time="2026-04-20T12:00:00",
        stats=SessionStats(),
        status="running",
    )
    defaults.update(overrides)
    return Session(**defaults)


def _patch_doctor(sessions=None, health_by_name=None, daemon_running=True,
                  skills_stale=False):
    """Stitch together mocks for every external dep the doctor CLI touches."""
    sessions = sessions or []
    health_by_name = health_by_name or {}

    mock_launcher = MagicMock()
    mock_launcher.list_sessions.return_value = sessions
    mock_launcher.tmux.get_pane_pid.return_value = 12345
    mock_launcher.restart.return_value = True

    def fake_inspect(sess, *args, **kwargs):
        return health_by_name[sess.name]

    return [
        patch("overcode.launcher.ClaudeLauncher", return_value=mock_launcher),
        patch("overcode.doctor.snapshot_process_table", return_value=({}, {})),
        patch("overcode.doctor.inspect_agent", side_effect=fake_inspect),
        patch("overcode.monitor_daemon.is_monitor_daemon_running",
              return_value=daemon_running),
        patch("overcode.history_reader.get_session_stats", return_value=None),
        patch("overcode.bundled_skills.any_skills_stale",
              return_value=skills_stale),
    ], mock_launcher


def _enter(patches):
    for p in patches:
        p.start()


def _exit(patches):
    for p in patches:
        p.stop()


class TestDoctorNoAgents:

    def test_no_agents_message(self):
        patches, _ = _patch_doctor(sessions=[])
        _enter(patches)
        try:
            result = runner.invoke(app, ["doctor"])
        finally:
            _exit(patches)
        assert result.exit_code == 0
        assert "No running agents" in result.output


class TestDoctorAllHealthy:

    def test_all_ok_summary(self):
        sess = _make_session("healthy-1")
        health = AgentHealth(
            name="healthy-1",
            tmux_window="healthy-1-0",
            launcher_version="0.4.0",
            claude_pid=9999,
            claude_argv="claude --settings /tmp/s.json",
            verdict=VERDICT_OK,
            details="hooks injected via --settings",
        )
        patches, _ = _patch_doctor(
            sessions=[sess], health_by_name={"healthy-1": health},
        )
        _enter(patches)
        try:
            result = runner.invoke(app, ["doctor"])
        finally:
            _exit(patches)
        assert result.exit_code == 0
        assert "healthy-1" in result.output
        assert "all 1 agents have hooks injected" in result.output
        # Fix-hint should NOT appear when nothing is broken.
        assert "overcode restart" not in result.output


class TestDoctorBrokenAgents:

    def test_missing_settings_prints_fix_hint(self):
        sess = _make_session("broken-1")
        health = AgentHealth(
            name="broken-1",
            tmux_window="broken-1-0",
            launcher_version="0.3.6",
            claude_pid=5555,
            claude_argv="claude",
            verdict=VERDICT_MISSING_SETTINGS,
            details="claude running without --settings",
        )
        patches, _ = _patch_doctor(
            sessions=[sess], health_by_name={"broken-1": health},
        )
        _enter(patches)
        try:
            result = runner.invoke(app, ["doctor"])
        finally:
            _exit(patches)
        assert result.exit_code == 0
        assert "1 broken" in result.output
        assert "overcode restart broken-1" in result.output
        assert "overcode doctor --fix" in result.output

    def test_mixed_roster(self):
        ok = _make_session("ok-1")
        broken = _make_session("broken-1")
        gone = _make_session("gone-1")
        health = {
            "ok-1": AgentHealth(
                name="ok-1", tmux_window="ok-1-0", launcher_version="0.4.0",
                claude_pid=1, claude_argv="claude --settings x",
                verdict=VERDICT_OK, details="ok",
            ),
            "broken-1": AgentHealth(
                name="broken-1", tmux_window="broken-1-0", launcher_version="0.4.0",
                claude_pid=2, claude_argv="claude",
                verdict=VERDICT_MISSING_SETTINGS, details="no --settings",
            ),
            "gone-1": AgentHealth(
                name="gone-1", tmux_window="gone-1-0", launcher_version="0.4.0",
                claude_pid=None, claude_argv="",
                verdict=VERDICT_WINDOW_GONE, details="window gone",
            ),
        }
        patches, _ = _patch_doctor(
            sessions=[ok, broken, gone], health_by_name=health,
        )
        _enter(patches)
        try:
            result = runner.invoke(app, ["doctor"])
        finally:
            _exit(patches)
        assert result.exit_code == 0
        # Summary reports counts accurately.
        assert "1 broken" in result.output
        # All three names appear in the table.
        for name in ("ok-1", "broken-1", "gone-1"):
            assert name in result.output


class TestDoctorDataFindings:

    def test_findings_rendered_below_table(self):
        sess = _make_session("noisy-1")
        health = AgentHealth(
            name="noisy-1",
            tmux_window="noisy-1-0",
            launcher_version="0.4.0",
            claude_pid=1,
            claude_argv="claude --settings x",
            verdict=VERDICT_OK,
            details="ok",
            data_findings=[
                Finding(code="tokens_zero", severity=SEVERITY_ERROR,
                        message="no tokens recorded despite interactions"),
                Finding(code="model_drift", severity=SEVERITY_WARNING,
                        message="model mismatch"),
            ],
        )
        patches, _ = _patch_doctor(
            sessions=[sess], health_by_name={"noisy-1": health},
        )
        _enter(patches)
        try:
            result = runner.invoke(app, ["doctor"])
        finally:
            _exit(patches)
        assert result.exit_code == 0
        assert "Data-quality findings" in result.output
        assert "tokens_zero" in result.output
        assert "model_drift" in result.output
        assert "2 data-quality findings" in result.output


class TestDoctorVerbose:

    def test_verbose_shows_argv(self):
        sess = _make_session("v-1")
        argv = "claude --settings /tmp/inj.json --other-flag"
        health = AgentHealth(
            name="v-1", tmux_window="v-1-0", launcher_version="0.4.0",
            claude_pid=77, claude_argv=argv,
            verdict=VERDICT_OK, details="ok",
        )
        patches, _ = _patch_doctor(
            sessions=[sess], health_by_name={"v-1": health},
        )
        _enter(patches)
        try:
            result_plain = runner.invoke(app, ["doctor"])
            result_verbose = runner.invoke(app, ["doctor", "--verbose"])
        finally:
            _exit(patches)

        # Plain output shouldn't include the --other-flag token from argv;
        # verbose output should.
        assert "--other-flag" not in result_plain.output
        assert "--other-flag" in result_verbose.output


class TestDoctorFix:

    def test_fix_restarts_broken_agents(self):
        broken = _make_session("broken-1")
        health = {
            "broken-1": AgentHealth(
                name="broken-1", tmux_window="broken-1-0", launcher_version="0.3.6",
                claude_pid=5, claude_argv="claude",
                verdict=VERDICT_MISSING_SETTINGS, details="",
            ),
        }
        patches, mock_launcher = _patch_doctor(
            sessions=[broken], health_by_name=health,
        )
        with patch("overcode.session_manager.SessionManager") as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session_by_name.return_value = broken
            mock_sm_cls.return_value = mock_sm
            _enter(patches)
            try:
                result = runner.invoke(app, ["doctor", "--fix"])
            finally:
                _exit(patches)

        assert result.exit_code == 0
        assert "Restarting broken agents" in result.output
        assert "restarted broken-1" in result.output
        # fresh=False preserves the Claude session history on restart.
        mock_launcher.restart.assert_called_once_with(broken, fresh=False)

    def test_fix_noop_when_all_healthy(self):
        sess = _make_session("ok-1")
        health = {
            "ok-1": AgentHealth(
                name="ok-1", tmux_window="ok-1-0", launcher_version="0.4.0",
                claude_pid=1, claude_argv="claude --settings x",
                verdict=VERDICT_OK, details="ok",
            ),
        }
        patches, mock_launcher = _patch_doctor(
            sessions=[sess], health_by_name=health,
        )
        _enter(patches)
        try:
            result = runner.invoke(app, ["doctor", "--fix"])
        finally:
            _exit(patches)
        assert result.exit_code == 0
        assert "Restarting broken agents" not in result.output
        mock_launcher.restart.assert_not_called()


class TestDoctorSkillsStaleness:

    def test_stale_skills_warning_surfaced(self):
        sess = _make_session("ok-1")
        health = {
            "ok-1": AgentHealth(
                name="ok-1", tmux_window="ok-1-0", launcher_version="0.4.0",
                claude_pid=1, claude_argv="claude --settings x",
                verdict=VERDICT_OK, details="ok",
            ),
        }
        patches, _ = _patch_doctor(
            sessions=[sess], health_by_name=health, skills_stale=True,
        )
        _enter(patches)
        try:
            result = runner.invoke(app, ["doctor"])
        finally:
            _exit(patches)
        assert result.exit_code == 0
        assert "overcode skills install" in result.output
