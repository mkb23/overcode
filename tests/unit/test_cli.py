"""
Unit tests for CLI using Typer.

These tests verify that the CLI correctly handles commands
using Typer's CliRunner.
"""

import pytest
import re
import sys
from pathlib import Path
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.cli import app


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


runner = CliRunner()


class TestCLICommands:
    """Test CLI commands"""

    def test_main_help(self):
        """Main help shows all commands"""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Manage and supervise Claude Code agents" in result.stdout
        assert "launch" in result.stdout
        assert "list" in result.stdout
        assert "daemon" in result.stdout

    def test_no_args_launches_tui(self):
        """No arguments launches the TUI monitor (#185)"""
        result = runner.invoke(app, [])
        # TUI launch fails outside a real terminal
        assert result.exit_code != 0
        assert "TTY" in result.output or "terminal" in result.output.lower()


class TestLaunchCommand:
    """Test launch command"""

    def test_launch_help(self):
        """Launch help shows options"""
        result = runner.invoke(app, ["launch", "--help"])
        assert result.exit_code == 0
        output = strip_ansi(result.stdout)
        assert "--name" in output
        assert "--directory" in output
        assert "--prompt" in output
        assert "--skip-permissions" in output

    def test_launch_requires_name(self):
        """Launch requires --name"""
        result = runner.invoke(app, ["launch"])
        assert result.exit_code != 0
        assert "Missing option" in result.output or "--name" in result.output


class TestKillCommand:
    """Test kill command"""

    def test_kill_requires_name(self):
        """Kill requires name argument"""
        result = runner.invoke(app, ["kill"])
        assert result.exit_code != 0


class TestSendCommand:
    """Test send command"""

    def test_send_requires_name(self):
        """Send requires name argument"""
        result = runner.invoke(app, ["send"])
        assert result.exit_code != 0


class TestShowCommand:
    """Test show command"""

    def test_show_requires_name(self):
        """Show requires name argument"""
        result = runner.invoke(app, ["show"])
        assert result.exit_code != 0


class TestInstructCommand:
    """Test instruct command"""

    def test_instruct_requires_name(self):
        """Instruct requires name argument"""
        result = runner.invoke(app, ["instruct"])
        assert result.exit_code != 0


# =============================================================================
# Extended CLI tests with mocked dependencies
# =============================================================================

from unittest.mock import patch, MagicMock


class TestListCommandWithMocks:
    """Test list command with mocked sessions"""

    def test_list_outputs_no_sessions(self):
        """List outputs message when no sessions exist"""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_launcher_class:
            mock_launcher = MagicMock()
            mock_launcher.list_sessions.return_value = []
            mock_launcher_class.return_value = mock_launcher

            result = runner.invoke(app, ["list"])

            assert result.exit_code == 0
            # Might say "No running agents" or be empty
            assert "no" in result.output.lower() or result.output.strip() == ""


class TestSendCommandWithMocks:
    """Test send command with mocked sessions"""

    def test_send_text_to_session(self):
        """Send text to existing session"""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_launcher_class:
            mock_launcher = MagicMock()
            mock_launcher.send_to_session.return_value = True
            mock_launcher_class.return_value = mock_launcher

            result = runner.invoke(app, ["send", "test-agent", "hello world"])

            assert result.exit_code == 0
            mock_launcher.send_to_session.assert_called()

    def test_send_key_with_no_enter(self):
        """Send key without pressing enter"""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_launcher_class:
            mock_launcher = MagicMock()
            mock_launcher.send_to_session.return_value = True
            mock_launcher_class.return_value = mock_launcher

            result = runner.invoke(app, ["send", "test-agent", "Escape", "--no-enter"])

            assert result.exit_code == 0


class TestConfigCommandWithMocks:
    """Test config command"""

    def test_config_show_outputs_config(self):
        """Config show outputs configuration"""
        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0


class TestHistoryCommandWithMocks:
    """Test history command"""

    def test_history_help(self):
        """History help works"""
        result = runner.invoke(app, ["history", "--help"])
        assert result.exit_code == 0


class TestShowCommandWithMocks:
    """Test show command with mocked output"""

    def _make_mock_session(self, name="test-agent"):
        """Create a mock Session object with all required fields."""
        from overcode.session_manager import Session, SessionStats
        return Session(
            id="test-id",
            name=name,
            tmux_session="agents",
            tmux_window=0,
            command=["claude"],
            start_directory="/tmp/test",
            start_time="2026-02-06T10:00:00",
            repo_name="test-repo",
            branch="main",
            status="running",
            permissiveness_mode="normal",
            standing_instructions="",
            agent_value=1000,
            stats=SessionStats(
                green_time_seconds=3600.0,
                non_green_time_seconds=600.0,
                sleep_time_seconds=0.0,
                estimated_cost_usd=1.50,
                steers_count=2,
                state_since="2026-02-06T11:50:00",
            ),
        )

    def _make_mock_claude_stats(self):
        """Create a mock ClaudeSessionStats."""
        from overcode.history_reader import ClaudeSessionStats
        return ClaudeSessionStats(
            interaction_count=5,
            input_tokens=50000,
            output_tokens=10000,
            cache_creation_tokens=1000,
            cache_read_tokens=2000,
            work_times=[120.0, 180.0, 90.0],
            current_context_tokens=90000,
            subagent_count=2,
            background_task_count=0,
        )

    def test_show_session_output_no_stats(self):
        """Show --no-stats outputs only pane content"""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_launcher_class:
            mock_launcher = MagicMock()
            mock_launcher.sessions.get_session_by_name.return_value = self._make_mock_session()
            mock_launcher.get_session_output.return_value = None
            mock_launcher_class.return_value = mock_launcher

            with patch('overcode.status_detector_factory.create_status_detector') as mock_factory:
                mock_sd = MagicMock()
                mock_sd.detect_status.return_value = ("running", "Working...", "line 1\nline 2\nline 3")
                mock_factory.return_value = mock_sd

                result = runner.invoke(app, ["show", "test-agent", "--no-stats"])

                assert result.exit_code == 0
                assert "line 1" in result.output
                # Stats should NOT be present
                assert "Tokens:" not in result.output

    def test_show_displays_stats(self):
        """Show displays stats section by default"""
        mock_session = self._make_mock_session()
        mock_claude_stats = self._make_mock_claude_stats()

        with patch('overcode.cli.agent.ClaudeLauncher') as mock_launcher_class:
            mock_launcher = MagicMock()
            mock_launcher.sessions.get_session_by_name.return_value = mock_session
            mock_launcher_class.return_value = mock_launcher

            with patch('overcode.status_detector_factory.create_status_detector') as mock_factory:
                mock_sd = MagicMock()
                pane = "Some output\n⏵⏵ bypass permissions on · 3 bashes · esc"
                mock_sd.detect_status.return_value = ("running", "Working on tests", pane)
                mock_factory.return_value = mock_sd

                with patch('overcode.history_reader.get_session_stats', return_value=mock_claude_stats):
                    with patch('overcode.tui_helpers.get_git_diff_stats', return_value=(3, 120, 45)):
                        with patch('overcode.monitor_daemon_state.get_monitor_daemon_state', return_value=None):
                            result = runner.invoke(app, ["show", "test-agent"])

                            assert result.exit_code == 0
                            output = result.output
                            assert "=== test-agent ===" in output
                            assert "running" in output
                            assert "test-repo" in output
                            assert "main" in output
                            assert "active" in output
                            assert "stalled" in output
                            assert "60.0K" in output  # total tokens
                            assert "subagents" in output
                            assert "3 background bashes" in output
                            assert "Δ3 files" in output

    def test_show_agent_not_found(self):
        """Show exits with error when agent not found"""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_launcher_class:
            mock_launcher = MagicMock()
            mock_launcher.sessions.get_session_by_name.return_value = None
            mock_launcher_class.return_value = mock_launcher

            result = runner.invoke(app, ["show", "nonexistent"])

            assert result.exit_code == 1

    def test_show_handles_no_claude_stats(self):
        """Show handles missing claude stats gracefully"""
        mock_session = self._make_mock_session()

        with patch('overcode.cli.agent.ClaudeLauncher') as mock_launcher_class:
            mock_launcher = MagicMock()
            mock_launcher.sessions.get_session_by_name.return_value = mock_session
            mock_launcher_class.return_value = mock_launcher

            with patch('overcode.status_detector_factory.create_status_detector') as mock_factory:
                mock_sd = MagicMock()
                mock_sd.detect_status.return_value = ("running", "Working...", "output")
                mock_factory.return_value = mock_sd

                with patch('overcode.history_reader.get_session_stats', return_value=None):
                    with patch('overcode.tui_helpers.get_git_diff_stats', return_value=None):
                        with patch('overcode.monitor_daemon_state.get_monitor_daemon_state', return_value=None):
                            result = runner.invoke(app, ["show", "test-agent"])

                            assert result.exit_code == 0
                            # With no claude stats, Tokens/Work lines are omitted
                            assert "Tokens" not in result.output
                            assert "=== test-agent ===" in result.output

    def test_show_background_bash_count_with_ansi(self):
        """Show correctly extracts background bash count from ANSI content"""
        mock_session = self._make_mock_session()
        mock_claude_stats = self._make_mock_claude_stats()

        with patch('overcode.cli.agent.ClaudeLauncher') as mock_launcher_class:
            mock_launcher = MagicMock()
            mock_launcher.sessions.get_session_by_name.return_value = mock_session
            mock_launcher_class.return_value = mock_launcher

            with patch('overcode.status_detector_factory.create_status_detector') as mock_factory:
                mock_sd = MagicMock()
                # Simulate ANSI-coded status bar
                pane = "Output\n\x1b[36m⏵⏵ auto-approve · 2 bashes · esc\x1b[0m"
                mock_sd.detect_status.return_value = ("running", "Working", pane)
                mock_factory.return_value = mock_sd

                with patch('overcode.history_reader.get_session_stats', return_value=mock_claude_stats):
                    with patch('overcode.tui_helpers.get_git_diff_stats', return_value=None):
                        with patch('overcode.monitor_daemon_state.get_monitor_daemon_state', return_value=None):
                            result = runner.invoke(app, ["show", "test-agent"])

                            assert result.exit_code == 0
                            assert "2 background bashes" in result.output


class TestKillCommandWithMocks:
    """Test kill command with mocked sessions"""

    def test_kill_existing_session(self):
        """Kill existing session"""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_launcher_class:
            mock_launcher = MagicMock()
            mock_launcher.kill_session.return_value = True
            mock_launcher_class.return_value = mock_launcher

            result = runner.invoke(app, ["kill", "test-agent"])

            assert result.exit_code == 0

    def test_kill_nonexistent_session(self):
        """Kill nonexistent session shows message"""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_launcher_class:
            mock_launcher = MagicMock()
            mock_launcher.kill_session.return_value = False
            mock_launcher_class.return_value = mock_launcher

            result = runner.invoke(app, ["kill", "nonexistent"])

            assert result.exit_code == 0


class TestLaunchCommandWithMocks:
    """Test launch command with mocked ClaudeLauncher"""

    def test_launch_creates_session(self):
        """Launch creates new session"""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_launcher_class:
            mock_session = MagicMock()
            mock_session.name = "new-agent"
            mock_session.tmux_window = 1

            mock_launcher = MagicMock()
            mock_launcher.launch.return_value = mock_session
            mock_launcher_class.return_value = mock_launcher

            result = runner.invoke(app, ["launch", "--name", "new-agent"])

            assert result.exit_code == 0
            mock_launcher.launch.assert_called()


class TestInstructCommandWithMocks:
    """Test instruct command with mocked dependencies."""

    def test_instruct_list_presets(self):
        """Should list available presets."""
        with patch('overcode.standing_instructions.load_presets') as mock_presets:
            mock_presets.return_value = {
                "DO_NOTHING": MagicMock(description="Do nothing"),
                "STANDARD": MagicMock(description="Standard mode"),
            }

            result = runner.invoke(app, ["instruct", "--list"])

            assert result.exit_code == 0
            assert "DO_NOTHING" in result.output
            assert "STANDARD" in result.output

    def test_instruct_requires_name_without_list(self):
        """Should require agent name when not listing."""
        result = runner.invoke(app, ["instruct"])

        assert result.exit_code == 1
        assert "Agent name required" in result.output

    def test_instruct_agent_not_found(self):
        """Should error when agent not found."""
        with patch('overcode.session_manager.SessionManager') as mock_sm:
            mock_instance = MagicMock()
            mock_instance.get_session_by_name.return_value = None
            mock_sm.return_value = mock_instance

            result = runner.invoke(app, ["instruct", "nonexistent", "DO_NOTHING"])

            assert result.exit_code == 1
            assert "not found" in result.output

    def test_instruct_clears_instructions(self):
        """Should clear instructions with --clear flag."""
        with patch('overcode.session_manager.SessionManager') as mock_sm:
            mock_session = MagicMock()
            mock_session.id = "test-id"

            mock_instance = MagicMock()
            mock_instance.get_session_by_name.return_value = mock_session
            mock_sm.return_value = mock_instance

            result = runner.invoke(app, ["instruct", "test-agent", "--clear"])

            assert result.exit_code == 0
            assert "Cleared" in result.output
            mock_instance.set_standing_instructions.assert_called_with("test-id", "", preset_name=None)


class TestCleanupCommandWithMocks:
    """Test cleanup command with mocked dependencies."""

    def test_cleanup_removes_terminated_sessions(self):
        """Should remove terminated sessions."""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_launcher_class:
            mock_launcher = MagicMock()
            mock_launcher.cleanup_terminated_sessions.return_value = 3
            mock_launcher_class.return_value = mock_launcher

            result = runner.invoke(app, ["cleanup"])

            assert result.exit_code == 0
            assert "Cleaned up 3" in result.output

    def test_cleanup_no_terminated_sessions(self):
        """Should show message when no terminated sessions."""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_launcher_class:
            mock_launcher = MagicMock()
            mock_launcher.cleanup_terminated_sessions.return_value = 0
            mock_launcher_class.return_value = mock_launcher

            result = runner.invoke(app, ["cleanup"])

            assert result.exit_code == 0
            assert "No sessions to clean up" in result.output


class TestSetValueCommand:
    """Test set-value command."""

    def test_set_value_requires_name(self):
        """Should require agent name."""
        result = runner.invoke(app, ["set-value"])
        assert result.exit_code != 0


class TestExportCommand:
    """Test export command."""

    def test_export_requires_output_path(self):
        """Should require output path."""
        result = runner.invoke(app, ["export"])
        assert result.exit_code != 0

    def test_attach_bare_without_name_errors(self):
        """--bare without agent name should error."""
        result = runner.invoke(app, ["attach", "--bare"])
        assert result.exit_code == 1
        output = strip_ansi(result.output)
        assert "--bare requires an agent name" in output


# =============================================================================
# Expanded coverage tests
# =============================================================================


def _make_session(name="test-agent", **kwargs):
    """Helper to create a Session object with sensible defaults."""
    from overcode.session_manager import Session, SessionStats
    defaults = dict(
        id="test-id",
        name=name,
        tmux_session="agents",
        tmux_window=0,
        command=["claude"],
        start_directory="/tmp/test",
        start_time="2026-02-06T10:00:00",
        repo_name="test-repo",
        branch="main",
        status="running",
        permissiveness_mode="normal",
        standing_instructions="",
        agent_value=1000,
        stats=SessionStats(
            green_time_seconds=3600.0,
            non_green_time_seconds=600.0,
            sleep_time_seconds=0.0,
            estimated_cost_usd=1.50,
            steers_count=2,
            state_since="2026-02-06T11:50:00",
        ),
    )
    defaults.update(kwargs)
    return Session(**defaults)


def _make_claude_stats(**kwargs):
    """Helper to create a ClaudeSessionStats."""
    from overcode.history_reader import ClaudeSessionStats
    defaults = dict(
        interaction_count=5,
        input_tokens=50000,
        output_tokens=10000,
        cache_creation_tokens=1000,
        cache_read_tokens=2000,
        work_times=[120.0, 180.0, 90.0],
        current_context_tokens=90000,
        subagent_count=2,
        background_task_count=0,
    )
    defaults.update(kwargs)
    return ClaudeSessionStats(**defaults)


class TestParseDuration:
    """Test _parse_duration helper."""

    def test_parse_seconds_suffix(self):
        from overcode.cli import _parse_duration
        assert _parse_duration("30s") == 30.0

    def test_parse_minutes_suffix(self):
        from overcode.cli import _parse_duration
        assert _parse_duration("5m") == 300.0

    def test_parse_hours_suffix(self):
        from overcode.cli import _parse_duration
        assert _parse_duration("1h") == 3600.0

    def test_parse_bare_number(self):
        from overcode.cli import _parse_duration
        assert _parse_duration("90") == 90.0

    def test_parse_strips_whitespace(self):
        from overcode.cli import _parse_duration
        assert _parse_duration("  5M  ") == 300.0


class TestLaunchExtended:
    """Extended launch command tests covering oversight policies and follow mode."""

    def test_launch_with_prompt(self):
        """Launch with --prompt sends initial prompt."""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_sess = MagicMock()
            mock_sess.name = "agent"
            mock_sess.parent_session_id = None
            mock_launcher = MagicMock()
            mock_launcher.launch.return_value = mock_sess
            mock_cls.return_value = mock_launcher

            result = runner.invoke(app, ["launch", "--name", "agent", "--prompt", "do stuff"])
            assert result.exit_code == 0
            assert "Initial prompt sent" in result.output

    def test_launch_with_parent(self):
        """Launch with --parent sets parent info."""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_sess = MagicMock()
            mock_sess.name = "child"
            mock_sess.parent_session_id = "parent-id"
            mock_launcher = MagicMock()
            mock_launcher.launch.return_value = mock_sess
            mock_cls.return_value = mock_launcher

            result = runner.invoke(app, ["launch", "--name", "child", "--parent", "dad"])
            assert result.exit_code == 0
            assert "Parent: dad" in result.output

    def test_launch_with_oversight_timeout(self):
        """Launch with --oversight-timeout stores policy."""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_sess = MagicMock()
            mock_sess.name = "child"
            mock_sess.parent_session_id = None
            mock_sess.id = "child-id"
            mock_launcher = MagicMock()
            mock_launcher.launch.return_value = mock_sess
            mock_cls.return_value = mock_launcher

            with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
                mock_sm = MagicMock()
                mock_sm_cls.return_value = mock_sm

                result = runner.invoke(app, ["launch", "--name", "child", "--oversight-timeout", "5m"])
                assert result.exit_code == 0
                assert "Oversight: timeout" in result.output
                mock_sm.update_session.assert_called_once_with(
                    "child-id",
                    oversight_policy="timeout",
                    oversight_timeout_seconds=300.0,
                )

    def test_launch_invalid_oversight_timeout(self):
        """Launch with invalid --oversight-timeout errors."""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_launcher.launch.return_value = None
            mock_cls.return_value = mock_launcher

            result = runner.invoke(app, ["launch", "--name", "child", "--oversight-timeout", "abc"])
            assert result.exit_code == 1
            assert "Invalid duration" in result.output

    def test_launch_on_stuck_fail(self):
        """Launch with --on-stuck fail."""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_sess = MagicMock()
            mock_sess.name = "child"
            mock_sess.parent_session_id = None
            mock_sess.id = "child-id"
            mock_launcher = MagicMock()
            mock_launcher.launch.return_value = mock_sess
            mock_cls.return_value = mock_launcher

            with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
                mock_sm = MagicMock()
                mock_sm_cls.return_value = mock_sm

                result = runner.invoke(app, ["launch", "--name", "child", "--on-stuck", "fail"])
                assert result.exit_code == 0
                assert "Oversight: fail" in result.output

    def test_launch_on_stuck_wait(self):
        """Launch with --on-stuck wait uses default (no oversight update)."""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_sess = MagicMock()
            mock_sess.name = "child"
            mock_sess.parent_session_id = None
            mock_launcher = MagicMock()
            mock_launcher.launch.return_value = mock_sess
            mock_cls.return_value = mock_launcher

            result = runner.invoke(app, ["launch", "--name", "child", "--on-stuck", "wait"])
            assert result.exit_code == 0

    def test_launch_on_stuck_timeout(self):
        """Launch with --on-stuck timeout:1h."""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_sess = MagicMock()
            mock_sess.name = "child"
            mock_sess.parent_session_id = None
            mock_sess.id = "child-id"
            mock_launcher = MagicMock()
            mock_launcher.launch.return_value = mock_sess
            mock_cls.return_value = mock_launcher

            with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
                mock_sm = MagicMock()
                mock_sm_cls.return_value = mock_sm

                result = runner.invoke(app, ["launch", "--name", "child", "--on-stuck", "timeout:1h"])
                assert result.exit_code == 0
                mock_sm.update_session.assert_called_once_with(
                    "child-id",
                    oversight_policy="timeout",
                    oversight_timeout_seconds=3600.0,
                )

    def test_launch_on_stuck_timeout_invalid(self):
        """Launch with --on-stuck timeout:bad errors."""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_launcher.launch.return_value = None
            mock_cls.return_value = mock_launcher

            result = runner.invoke(app, ["launch", "--name", "child", "--on-stuck", "timeout:bad"])
            assert result.exit_code == 1
            assert "Invalid duration" in result.output

    def test_launch_on_stuck_invalid_value(self):
        """Launch with --on-stuck bogus errors."""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_launcher.launch.return_value = None
            mock_cls.return_value = mock_launcher

            result = runner.invoke(app, ["launch", "--name", "child", "--on-stuck", "bogus"])
            assert result.exit_code == 1
            assert "Invalid --on-stuck" in result.output

    def test_launch_with_follow(self):
        """Launch with --follow calls follow_agent."""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_sess = MagicMock()
            mock_sess.name = "child"
            mock_sess.parent_session_id = None
            mock_launcher = MagicMock()
            mock_launcher.launch.return_value = mock_sess
            mock_cls.return_value = mock_launcher

            with patch('overcode.follow_mode.follow_agent', return_value=0) as mock_follow:
                result = runner.invoke(app, ["launch", "--name", "child", "--follow"])
                assert result.exit_code == 0
                mock_follow.assert_called_once_with("child", "agents")

    def test_launch_returns_none(self):
        """Launch returns None when launch fails."""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_launcher.launch.return_value = None
            mock_cls.return_value = mock_launcher

            result = runner.invoke(app, ["launch", "--name", "agent"])
            assert result.exit_code == 0
            assert "launched" not in result.output


class TestFollowCommand:
    """Test follow command."""

    def test_follow_calls_follow_agent(self):
        """Follow calls follow_agent and exits with its code."""
        with patch('overcode.follow_mode.follow_agent', return_value=0) as mock_follow:
            result = runner.invoke(app, ["follow", "my-agent"])
            assert result.exit_code == 0
            mock_follow.assert_called_once_with("my-agent", "agents")

    def test_follow_propagates_exit_code(self):
        """Follow propagates non-zero exit code."""
        with patch('overcode.follow_mode.follow_agent', return_value=1) as mock_follow:
            result = runner.invoke(app, ["follow", "my-agent"])
            assert result.exit_code == 1


class TestReportCommand:
    """Test report command."""

    def test_report_invalid_status(self):
        """Report rejects invalid status."""
        result = runner.invoke(app, ["report", "--status", "invalid"])
        assert result.exit_code == 1
        assert "must be 'success' or 'failure'" in result.output

    def test_report_missing_env_vars(self):
        """Report errors when env vars not set."""
        with patch.dict('os.environ', {}, clear=True):
            result = runner.invoke(app, ["report", "--status", "success"])
            assert result.exit_code == 1
            assert "env vars required" in result.output

    def test_report_success(self):
        """Report writes file and updates session."""
        import os
        env = {
            "OVERCODE_SESSION_NAME": "child-agent",
            "OVERCODE_TMUX_SESSION": "agents",
        }
        with patch.dict(os.environ, env):
            with patch('overcode.settings.get_session_dir') as mock_dir:
                mock_path = MagicMock()
                mock_dir.return_value = mock_path
                mock_file = MagicMock()
                mock_path.__truediv__ = MagicMock(return_value=mock_file)

                with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
                    mock_sm = MagicMock()
                    mock_session = MagicMock()
                    mock_session.id = "child-id"
                    mock_sm.get_session_by_name.return_value = mock_session
                    mock_sm_cls.return_value = mock_sm

                    result = runner.invoke(app, ["report", "--status", "success", "--reason", "All tests passed"])
                    assert result.exit_code == 0
                    assert "Report filed: success" in result.output
                    assert "All tests passed" in result.output
                    mock_sm.update_session.assert_called_once_with(
                        "child-id",
                        report_status="success",
                        report_reason="All tests passed",
                    )

    def test_report_failure_no_reason(self):
        """Report failure without reason."""
        import os
        env = {
            "OVERCODE_SESSION_NAME": "child-agent",
            "OVERCODE_TMUX_SESSION": "agents",
        }
        with patch.dict(os.environ, env):
            with patch('overcode.settings.get_session_dir') as mock_dir:
                mock_path = MagicMock()
                mock_dir.return_value = mock_path
                mock_file = MagicMock()
                mock_path.__truediv__ = MagicMock(return_value=mock_file)

                with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
                    mock_sm = MagicMock()
                    mock_sm.get_session_by_name.return_value = None
                    mock_sm_cls.return_value = mock_sm

                    result = runner.invoke(app, ["report", "--status", "failure"])
                    assert result.exit_code == 0
                    assert "Report filed: failure" in result.output


class TestCleanupExtended:
    """Test cleanup with --done flag."""

    def test_cleanup_with_done_flag(self):
        """Cleanup with --done also archives done agents."""
        mock_done_session = MagicMock()
        mock_done_session.status = "done"

        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_launcher.cleanup_terminated_sessions.return_value = 1
            mock_launcher.sessions.list_sessions.return_value = [mock_done_session]
            mock_cls.return_value = mock_launcher

            result = runner.invoke(app, ["cleanup", "--done"])
            assert result.exit_code == 0
            assert "1 terminated" in result.output
            assert "1 done" in result.output
            mock_launcher._kill_single_session.assert_called_once_with(mock_done_session)


class TestSetValueExtended:
    """Test set-value command execution."""

    def test_set_value_success(self):
        """Set value successfully updates agent."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_agent = MagicMock()
            mock_agent.id = "agent-id"
            mock_sm.get_session_by_name.return_value = mock_agent
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["set-value", "my-agent", "2000"])
            assert result.exit_code == 0
            assert "Set my-agent value to 2000" in result.output
            mock_sm.set_agent_value.assert_called_once_with("agent-id", 2000)

    def test_set_value_agent_not_found(self):
        """Set value errors when agent not found."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session_by_name.return_value = None
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["set-value", "missing", "2000"])
            assert result.exit_code == 1
            assert "not found" in result.output


class TestSetBudgetCommand:
    """Test set-budget command (deprecated)."""

    def test_set_budget_success(self):
        """Set budget successfully."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_agent = MagicMock()
            mock_agent.id = "agent-id"
            mock_sm.get_session_by_name.return_value = mock_agent
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["set-budget", "my-agent", "5.00"])
            assert result.exit_code == 0
            assert "$5.00" in result.output
            mock_sm.set_cost_budget.assert_called_once_with("agent-id", 5.0)

    def test_set_budget_clear(self):
        """Set budget to 0 clears it."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_agent = MagicMock()
            mock_agent.id = "agent-id"
            mock_sm.get_session_by_name.return_value = mock_agent
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["set-budget", "my-agent", "0"])
            assert result.exit_code == 0
            assert "Cleared budget" in result.output

    def test_set_budget_agent_not_found(self):
        """Set budget errors when agent not found."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session_by_name.return_value = None
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["set-budget", "missing", "5.00"])
            assert result.exit_code == 1
            assert "not found" in result.output

    def test_set_budget_negative(self):
        """Set budget rejects negative values."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_agent = MagicMock()
            mock_agent.id = "agent-id"
            mock_sm.get_session_by_name.return_value = mock_agent
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["set-budget", "my-agent", "-1"])
            # Typer may reject negative float at parsing level (exit code 2)
            assert result.exit_code != 0


class TestBudgetSetCommand:
    """Test budget set subcommand."""

    def test_budget_set_success(self):
        """Budget set works."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_agent = MagicMock()
            mock_agent.id = "agent-id"
            mock_sm.get_session_by_name.return_value = mock_agent
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["budget", "set", "my-agent", "10.0"])
            assert result.exit_code == 0
            assert "$10.00" in result.output

    def test_budget_set_clear(self):
        """Budget set to 0 clears."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_agent = MagicMock()
            mock_agent.id = "agent-id"
            mock_sm.get_session_by_name.return_value = mock_agent
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["budget", "set", "my-agent", "0"])
            assert result.exit_code == 0
            assert "Cleared budget" in result.output

    def test_budget_set_not_found(self):
        """Budget set errors when agent not found."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session_by_name.return_value = None
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["budget", "set", "missing", "5"])
            assert result.exit_code == 1
            assert "not found" in result.output

    def test_budget_set_negative(self):
        """Budget set rejects negative."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_agent = MagicMock()
            mock_agent.id = "agent-id"
            mock_sm.get_session_by_name.return_value = mock_agent
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["budget", "set", "my-agent", "-5"])
            # Typer may reject negative float at parsing level (exit code 2)
            assert result.exit_code != 0


class TestBudgetTransferCommand:
    """Test budget transfer subcommand."""

    def test_budget_transfer_success(self):
        """Budget transfer works."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_src = MagicMock()
            mock_src.id = "src-id"
            mock_tgt = MagicMock()
            mock_tgt.id = "tgt-id"
            mock_sm.get_session_by_name.side_effect = lambda n: mock_src if n == "parent" else mock_tgt
            mock_sm.is_ancestor.return_value = True
            mock_sm.transfer_budget.return_value = True
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["budget", "transfer", "parent", "child", "2.0"])
            assert result.exit_code == 0
            assert "Transferred $2.00" in result.output

    def test_budget_transfer_source_not_found(self):
        """Budget transfer errors when source not found."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session_by_name.return_value = None
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["budget", "transfer", "missing", "child", "2.0"])
            assert result.exit_code == 1
            assert "Source agent" in result.output

    def test_budget_transfer_target_not_found(self):
        """Budget transfer errors when target not found."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_src = MagicMock()
            mock_src.id = "src-id"
            mock_sm.get_session_by_name.side_effect = lambda n: mock_src if n == "parent" else None
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["budget", "transfer", "parent", "missing", "2.0"])
            assert result.exit_code == 1
            assert "Target agent" in result.output

    def test_budget_transfer_non_positive(self):
        """Budget transfer rejects zero/negative amount."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_src = MagicMock()
            mock_src.id = "src-id"
            mock_tgt = MagicMock()
            mock_tgt.id = "tgt-id"
            mock_sm.get_session_by_name.side_effect = lambda n: mock_src if n == "parent" else mock_tgt
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["budget", "transfer", "parent", "child", "0"])
            assert result.exit_code == 1
            assert "must be positive" in result.output

    def test_budget_transfer_not_ancestor(self):
        """Budget transfer errors when source is not ancestor."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_src = MagicMock()
            mock_src.id = "src-id"
            mock_tgt = MagicMock()
            mock_tgt.id = "tgt-id"
            mock_sm.get_session_by_name.side_effect = lambda n: mock_src if n == "parent" else mock_tgt
            mock_sm.is_ancestor.return_value = False
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["budget", "transfer", "parent", "child", "2.0"])
            assert result.exit_code == 1
            assert "not an ancestor" in result.output

    def test_budget_transfer_insufficient(self):
        """Budget transfer fails on insufficient budget."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_src = MagicMock()
            mock_src.id = "src-id"
            mock_tgt = MagicMock()
            mock_tgt.id = "tgt-id"
            mock_sm.get_session_by_name.side_effect = lambda n: mock_src if n == "parent" else mock_tgt
            mock_sm.is_ancestor.return_value = True
            mock_sm.transfer_budget.return_value = False
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["budget", "transfer", "parent", "child", "99.0"])
            assert result.exit_code == 1
            assert "insufficient budget" in result.output


class TestBudgetShowCommand:
    """Test budget show subcommand."""

    def test_budget_show_all_agents(self):
        """Budget show with no name shows all agents."""
        from overcode.session_manager import SessionStats
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            agent = _make_session(name="a1", cost_budget_usd=10.0)
            mock_sm.list_sessions.return_value = [agent]
            mock_sm.get_descendants.return_value = []
            mock_sm.compute_depth.return_value = 0
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["budget", "show"])
            assert result.exit_code == 0
            assert "a1" in result.output
            assert "remaining" in result.output

    def test_budget_show_no_agents(self):
        """Budget show with no agents."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.list_sessions.return_value = []
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["budget", "show"])
            assert result.exit_code == 0
            assert "No running agents" in result.output

    def test_budget_show_specific_agent(self):
        """Budget show for specific agent."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            agent = _make_session(name="my-agent", cost_budget_usd=5.0)
            mock_sm.get_session_by_name.return_value = agent
            mock_sm.get_descendants.return_value = []
            mock_sm.compute_depth.return_value = 0
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["budget", "show", "my-agent"])
            assert result.exit_code == 0
            assert "my-agent" in result.output

    def test_budget_show_agent_not_found(self):
        """Budget show errors when agent not found."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session_by_name.return_value = None
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["budget", "show", "missing"])
            assert result.exit_code == 1
            assert "not found" in result.output

    def test_budget_show_unlimited(self):
        """Budget show for agent with no budget (unlimited)."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            agent = _make_session(name="free", cost_budget_usd=0.0)
            mock_sm.get_session_by_name.return_value = agent
            mock_sm.get_descendants.return_value = []
            mock_sm.compute_depth.return_value = 0
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["budget", "show", "free"])
            assert result.exit_code == 0
            assert "unlimited" in result.output
            assert "no limit" in result.output

    def test_budget_show_with_children(self):
        """Budget show includes subtree spend for parent."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            parent_agent = _make_session(name="parent", cost_budget_usd=10.0)
            child_agent = _make_session(name="child")
            mock_sm.get_session_by_name.return_value = parent_agent
            mock_sm.get_descendants.return_value = [child_agent]
            mock_sm.compute_depth.return_value = 0
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["budget", "show", "parent"])
            assert result.exit_code == 0
            assert "subtree" in result.output


class TestAnnotateCommand:
    """Test annotate command."""

    def test_annotate_set(self):
        """Annotate sets annotation text."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_agent = MagicMock()
            mock_agent.id = "agent-id"
            mock_sm.get_session_by_name.return_value = mock_agent
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["annotate", "my-agent", "Working", "on", "auth"])
            assert result.exit_code == 0
            assert "Annotation set" in result.output
            mock_sm.set_human_annotation.assert_called_once_with("agent-id", "Working on auth")

    def test_annotate_clear(self):
        """Annotate with no text clears annotation."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_agent = MagicMock()
            mock_agent.id = "agent-id"
            mock_sm.get_session_by_name.return_value = mock_agent
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["annotate", "my-agent"])
            assert result.exit_code == 0
            assert "Annotation cleared" in result.output
            mock_sm.set_human_annotation.assert_called_once_with("agent-id", "")

    def test_annotate_not_found(self):
        """Annotate errors when agent not found."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session_by_name.return_value = None
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["annotate", "missing"])
            assert result.exit_code == 1
            assert "not found" in result.output


class TestSendExtended:
    """Extended tests for send command."""

    def test_send_special_key(self):
        """Send special key (enter/escape) shows key name."""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_launcher.send_to_session.return_value = True
            mock_cls.return_value = mock_launcher

            result = runner.invoke(app, ["send", "my-agent", "enter"])
            assert result.exit_code == 0
            assert "ENTER" in result.output

    def test_send_no_enter_mode(self):
        """Send with --no-enter shows (no enter) in output."""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_launcher.send_to_session.return_value = True
            mock_cls.return_value = mock_launcher

            result = runner.invoke(app, ["send", "my-agent", "y", "--no-enter"])
            assert result.exit_code == 0
            assert "no enter" in result.output

    def test_send_long_text_truncated(self):
        """Send long text truncates display to 50 chars."""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_launcher.send_to_session.return_value = True
            mock_cls.return_value = mock_launcher

            long_text = "a" * 100
            result = runner.invoke(app, ["send", "my-agent", long_text])
            assert result.exit_code == 0
            assert "..." in result.output

    def test_send_fails(self):
        """Send failure exits with code 1."""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_launcher.send_to_session.return_value = False
            mock_cls.return_value = mock_launcher

            result = runner.invoke(app, ["send", "my-agent", "hello"])
            assert result.exit_code == 1
            assert "Failed to send" in result.output


class TestShowExtended:
    """Extended show command tests."""

    def test_show_stats_only(self):
        """Show with --stats-only omits pane output."""
        mock_session = _make_session()
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_launcher.sessions.get_session_by_name.return_value = mock_session
            mock_cls.return_value = mock_launcher

            with patch('overcode.status_detector_factory.create_status_detector') as mock_factory:
                mock_sd = MagicMock()
                mock_sd.detect_status.return_value = ("running", "Working", "pane output here")
                mock_factory.return_value = mock_sd

                with patch('overcode.history_reader.get_session_stats', return_value=None):
                    with patch('overcode.tui_helpers.get_git_diff_stats', return_value=None):
                        with patch('overcode.monitor_daemon_state.get_monitor_daemon_state', return_value=None):
                            result = runner.invoke(app, ["show", "test-agent", "--stats-only"])
                            assert result.exit_code == 0
                            assert "=== test-agent ===" in result.output
                            # Should not have the pane output section
                            assert "last 50 lines" not in result.output

    def test_show_terminated_session(self):
        """Show handles terminated session."""
        mock_session = _make_session(status="terminated")
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_launcher.sessions.get_session_by_name.return_value = mock_session
            mock_launcher.get_session_output.return_value = None
            mock_cls.return_value = mock_launcher

            with patch('overcode.history_reader.get_session_stats', return_value=None):
                with patch('overcode.tui_helpers.get_git_diff_stats', return_value=None):
                    with patch('overcode.monitor_daemon_state.get_monitor_daemon_state', return_value=None):
                        result = runner.invoke(app, ["show", "test-agent"])
                        assert result.exit_code == 0
                        assert "terminated" in result.output

    def test_show_with_daemon_state(self):
        """Show uses daemon state when available."""
        mock_session = _make_session()
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_launcher.sessions.get_session_by_name.return_value = mock_session
            mock_cls.return_value = mock_launcher

            mock_daemon_state = MagicMock()
            mock_daemon_state.is_stale.return_value = False
            mock_daemon_session = MagicMock()
            mock_daemon_session.current_status = "running"
            mock_daemon_session.current_activity = "Coding tests"
            mock_daemon_session.activity_summary = "Writing unit tests"
            mock_daemon_session.activity_summary_context = "Test context"
            mock_daemon_state.get_session_by_name.return_value = mock_daemon_session

            with patch('overcode.monitor_daemon_state.get_monitor_daemon_state', return_value=mock_daemon_state):
                with patch('overcode.status_detector_factory.StatusDetectorDispatcher') as mock_dispatcher:
                    mock_d = MagicMock()
                    mock_d.get_pane_content.return_value = "pane content"
                    mock_dispatcher.return_value = mock_d

                    with patch('overcode.history_reader.get_session_stats', return_value=None):
                        with patch('overcode.tui_helpers.get_git_diff_stats', return_value=None):
                            result = runner.invoke(app, ["show", "test-agent"])
                            assert result.exit_code == 0
                            assert "Coding tests" in result.output

    def test_show_asleep_session(self):
        """Show handles asleep session."""
        mock_session = _make_session(is_asleep=True)
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_launcher.sessions.get_session_by_name.return_value = mock_session
            mock_cls.return_value = mock_launcher

            with patch('overcode.status_detector_factory.create_status_detector') as mock_factory:
                mock_sd = MagicMock()
                mock_sd.detect_status.return_value = ("running", "Working", "output")
                mock_factory.return_value = mock_sd

                with patch('overcode.history_reader.get_session_stats', return_value=None):
                    with patch('overcode.tui_helpers.get_git_diff_stats', return_value=None):
                        with patch('overcode.monitor_daemon_state.get_monitor_daemon_state', return_value=None):
                            result = runner.invoke(app, ["show", "test-agent"])
                            assert result.exit_code == 0
                            assert "asleep" in result.output

    def test_show_pane_fallback_for_terminated(self):
        """Show uses launcher.get_session_output for terminated sessions."""
        mock_session = _make_session(status="terminated")
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_launcher.sessions.get_session_by_name.return_value = mock_session
            mock_launcher.get_session_output.return_value = "fallback output here"
            mock_cls.return_value = mock_launcher

            with patch('overcode.history_reader.get_session_stats', return_value=None):
                with patch('overcode.tui_helpers.get_git_diff_stats', return_value=None):
                    with patch('overcode.monitor_daemon_state.get_monitor_daemon_state', return_value=None):
                        result = runner.invoke(app, ["show", "test-agent"])
                        assert result.exit_code == 0
                        assert "fallback output here" in result.output


class TestHooksInstallCommand:
    """Test hooks install command."""

    def test_hooks_install_user_level(self):
        """Hooks install at user level."""
        with patch('overcode.claude_config.ClaudeConfigEditor') as mock_editor_cls:
            mock_editor = MagicMock()
            mock_editor.load.return_value = {}
            mock_editor.add_hook.return_value = True
            mock_editor.path = Path("/tmp/.claude/settings.json")
            mock_editor_cls.user_level.return_value = mock_editor

            with patch('overcode.hook_handler.OVERCODE_HOOKS', [("TestEvent", "test-cmd")]):
                result = runner.invoke(app, ["hooks", "install"])
                assert result.exit_code == 0
                assert "Installed 1 hook" in result.output

    def test_hooks_install_project_level(self):
        """Hooks install at project level."""
        with patch('overcode.claude_config.ClaudeConfigEditor') as mock_editor_cls:
            mock_editor = MagicMock()
            mock_editor.load.return_value = {}
            mock_editor.add_hook.return_value = True
            mock_editor.path = Path("/tmp/.claude/settings.json")
            mock_editor_cls.project_level.return_value = mock_editor

            with patch('overcode.hook_handler.OVERCODE_HOOKS', [("TestEvent", "test-cmd")]):
                result = runner.invoke(app, ["hooks", "install", "--project"])
                assert result.exit_code == 0
                assert "project" in result.output

    def test_hooks_install_already_installed(self):
        """Hooks install when all already installed."""
        with patch('overcode.claude_config.ClaudeConfigEditor') as mock_editor_cls:
            mock_editor = MagicMock()
            mock_editor.load.return_value = {}
            mock_editor.add_hook.return_value = False
            mock_editor.path = Path("/tmp/.claude/settings.json")
            mock_editor_cls.user_level.return_value = mock_editor

            with patch('overcode.hook_handler.OVERCODE_HOOKS', [("TestEvent", "test-cmd")]):
                result = runner.invoke(app, ["hooks", "install"])
                assert result.exit_code == 0
                assert "already installed" in result.output

    def test_hooks_install_invalid_json(self):
        """Hooks install handles invalid JSON."""
        with patch('overcode.claude_config.ClaudeConfigEditor') as mock_editor_cls:
            mock_editor = MagicMock()
            mock_editor.load.side_effect = ValueError("Invalid JSON")
            mock_editor.path = Path("/tmp/.claude/settings.json")
            mock_editor_cls.user_level.return_value = mock_editor

            result = runner.invoke(app, ["hooks", "install"])
            assert result.exit_code == 1
            assert "Invalid JSON" in result.output


class TestHooksUninstallCommand:
    """Test hooks uninstall command."""

    def test_hooks_uninstall_success(self):
        """Hooks uninstall removes hooks."""
        with patch('overcode.claude_config.ClaudeConfigEditor') as mock_editor_cls:
            mock_editor = MagicMock()
            mock_editor.load.return_value = {}
            mock_editor.remove_hook.return_value = True
            mock_editor.path = Path("/tmp/.claude/settings.json")
            mock_editor_cls.user_level.return_value = mock_editor

            with patch('overcode.hook_handler.OVERCODE_HOOKS', [("TestEvent", "test-cmd")]):
                result = runner.invoke(app, ["hooks", "uninstall"])
                assert result.exit_code == 0
                assert "Removed 1 hook" in result.output

    def test_hooks_uninstall_none_found(self):
        """Hooks uninstall when none found."""
        with patch('overcode.claude_config.ClaudeConfigEditor') as mock_editor_cls:
            mock_editor = MagicMock()
            mock_editor.load.return_value = {}
            mock_editor.remove_hook.return_value = False
            mock_editor.path = Path("/tmp/.claude/settings.json")
            mock_editor_cls.user_level.return_value = mock_editor

            with patch('overcode.hook_handler.OVERCODE_HOOKS', [("TestEvent", "test-cmd")]):
                result = runner.invoke(app, ["hooks", "uninstall"])
                assert result.exit_code == 0
                assert "No overcode hooks found" in result.output

    def test_hooks_uninstall_invalid_json(self):
        """Hooks uninstall handles invalid JSON."""
        with patch('overcode.claude_config.ClaudeConfigEditor') as mock_editor_cls:
            mock_editor = MagicMock()
            mock_editor.load.side_effect = ValueError("broken")
            mock_editor.path = Path("/tmp/.claude/settings.json")
            mock_editor_cls.user_level.return_value = mock_editor

            result = runner.invoke(app, ["hooks", "uninstall"])
            assert result.exit_code == 1


class TestHooksStatusCommand:
    """Test hooks status command."""

    def test_hooks_status(self):
        """Hooks status shows installed/not installed."""
        with patch('overcode.claude_config.ClaudeConfigEditor') as mock_editor_cls:
            mock_user = MagicMock()
            mock_user.load.return_value = {}
            mock_user.path = MagicMock()
            mock_user.path.exists.return_value = True
            mock_user.has_hook.return_value = True

            mock_proj = MagicMock()
            mock_proj.load.return_value = {}
            mock_proj.path = MagicMock()
            mock_proj.path.exists.return_value = False

            mock_editor_cls.user_level.return_value = mock_user
            mock_editor_cls.project_level.return_value = mock_proj

            with patch('overcode.hook_handler.OVERCODE_HOOKS', [("TestEvent", "test-cmd")]):
                result = runner.invoke(app, ["hooks", "status"])
                assert result.exit_code == 0
                assert "User-level" in result.output

    def test_hooks_status_invalid_json(self):
        """Hooks status handles invalid JSON."""
        with patch('overcode.claude_config.ClaudeConfigEditor') as mock_editor_cls:
            mock_user = MagicMock()
            mock_user.load.side_effect = ValueError("bad json")
            mock_user.path = Path("/tmp/.claude/settings.json")
            mock_editor_cls.user_level.return_value = mock_user

            mock_proj = MagicMock()
            mock_proj.load.return_value = {}
            mock_proj.path = MagicMock()
            mock_proj.path.exists.return_value = False
            mock_editor_cls.project_level.return_value = mock_proj

            with patch('overcode.hook_handler.OVERCODE_HOOKS', [("TestEvent", "test-cmd")]):
                result = runner.invoke(app, ["hooks", "status"])
                assert result.exit_code == 0
                assert "invalid JSON" in result.output


class TestSkillsInstallCommand:
    """Test skills install command."""

    def test_skills_install_new(self, tmp_path):
        """Skills install creates new skill files."""
        base = tmp_path / ".claude" / "skills"

        with patch('overcode.bundled_skills.OVERCODE_SKILLS', {
            "test-skill": {"content": "# Test Skill"},
        }):
            with patch('overcode.bundled_skills.DEPRECATED_SKILL_NAMES', []):
                with patch('pathlib.Path.home', return_value=tmp_path):
                    result = runner.invoke(app, ["skills", "install"])
                    assert result.exit_code == 0
                    assert "1 installed" in result.output
                    assert (base / "test-skill" / "SKILL.md").read_text() == "# Test Skill"

    def test_skills_install_up_to_date(self, tmp_path):
        """Skills install skips up-to-date skills."""
        base = tmp_path / ".claude" / "skills" / "test-skill"
        base.mkdir(parents=True)
        (base / "SKILL.md").write_text("# Test Skill")

        with patch('overcode.bundled_skills.OVERCODE_SKILLS', {
            "test-skill": {"content": "# Test Skill"},
        }):
            with patch('overcode.bundled_skills.DEPRECATED_SKILL_NAMES', []):
                with patch('pathlib.Path.home', return_value=tmp_path):
                    result = runner.invoke(app, ["skills", "install"])
                    assert result.exit_code == 0
                    assert "up-to-date" in result.output

    def test_skills_install_updated(self, tmp_path):
        """Skills install updates modified skills."""
        base = tmp_path / ".claude" / "skills" / "test-skill"
        base.mkdir(parents=True)
        (base / "SKILL.md").write_text("# Old content")

        with patch('overcode.bundled_skills.OVERCODE_SKILLS', {
            "test-skill": {"content": "# New content"},
        }):
            with patch('overcode.bundled_skills.DEPRECATED_SKILL_NAMES', []):
                with patch('pathlib.Path.home', return_value=tmp_path):
                    result = runner.invoke(app, ["skills", "install"])
                    assert result.exit_code == 0
                    assert "1 updated" in result.output

    def test_skills_install_removes_deprecated(self, tmp_path):
        """Skills install removes deprecated skills."""
        deprecated_dir = tmp_path / ".claude" / "skills" / "old-skill"
        deprecated_dir.mkdir(parents=True)
        (deprecated_dir / "SKILL.md").write_text("# Old")

        with patch('overcode.bundled_skills.OVERCODE_SKILLS', {}):
            with patch('overcode.bundled_skills.DEPRECATED_SKILL_NAMES', ["old-skill"]):
                with patch('pathlib.Path.home', return_value=tmp_path):
                    result = runner.invoke(app, ["skills", "install"])
                    assert result.exit_code == 0
                    assert "Removed deprecated" in result.output
                    assert not deprecated_dir.exists()


class TestSkillsUninstallCommand:
    """Test skills uninstall command."""

    def test_skills_uninstall_matching(self, tmp_path):
        """Skills uninstall removes matching skills."""
        base = tmp_path / ".claude" / "skills" / "test-skill"
        base.mkdir(parents=True)
        (base / "SKILL.md").write_text("# Test Skill")

        with patch('overcode.bundled_skills.OVERCODE_SKILLS', {
            "test-skill": {"content": "# Test Skill"},
        }):
            with patch('pathlib.Path.home', return_value=tmp_path):
                result = runner.invoke(app, ["skills", "uninstall"])
                assert result.exit_code == 0
                assert "Removed 1 skill" in result.output
                assert not base.exists()

    def test_skills_uninstall_skips_modified(self, tmp_path):
        """Skills uninstall skips modified skills."""
        base = tmp_path / ".claude" / "skills" / "test-skill"
        base.mkdir(parents=True)
        (base / "SKILL.md").write_text("# Modified by user")

        with patch('overcode.bundled_skills.OVERCODE_SKILLS', {
            "test-skill": {"content": "# Original content"},
        }):
            with patch('pathlib.Path.home', return_value=tmp_path):
                result = runner.invoke(app, ["skills", "uninstall"])
                assert result.exit_code == 0
                assert "modified" in result.output
                assert base.exists()

    def test_skills_uninstall_none_found(self, tmp_path):
        """Skills uninstall when no skills found."""
        with patch('overcode.bundled_skills.OVERCODE_SKILLS', {
            "test-skill": {"content": "# Test"},
        }):
            with patch('pathlib.Path.home', return_value=tmp_path):
                result = runner.invoke(app, ["skills", "uninstall"])
                assert result.exit_code == 0
                assert "No overcode skills found" in result.output


class TestSkillsStatusCommand:
    """Test skills status command."""

    def test_skills_status(self, tmp_path):
        """Skills status shows installed/not installed."""
        base = tmp_path / ".claude" / "skills" / "test-skill"
        base.mkdir(parents=True)
        (base / "SKILL.md").write_text("# Test Skill")

        with patch('overcode.bundled_skills.OVERCODE_SKILLS', {
            "test-skill": {"content": "# Test Skill"},
        }):
            with patch('pathlib.Path.home', return_value=tmp_path):
                result = runner.invoke(app, ["skills", "status"])
                assert result.exit_code == 0
                assert "installed" in result.output


class TestPermsInstallCommand:
    """Test perms install command."""

    def test_perms_install_safe(self):
        """Perms install adds safe permissions."""
        with patch('overcode.claude_config.ClaudeConfigEditor') as mock_editor_cls:
            mock_editor = MagicMock()
            mock_editor.load.return_value = {}
            mock_editor.add_permission.return_value = True
            mock_editor.path = Path("/tmp/.claude/settings.json")
            mock_editor_cls.user_level.return_value = mock_editor

            result = runner.invoke(app, ["perms", "install"])
            assert result.exit_code == 0
            assert "Installed" in result.output
            assert "safe" in result.output

    def test_perms_install_all(self):
        """Perms install --all adds safe + punchy permissions."""
        with patch('overcode.claude_config.ClaudeConfigEditor') as mock_editor_cls:
            mock_editor = MagicMock()
            mock_editor.load.return_value = {}
            mock_editor.add_permission.return_value = True
            mock_editor.path = Path("/tmp/.claude/settings.json")
            mock_editor_cls.user_level.return_value = mock_editor

            result = runner.invoke(app, ["perms", "install", "--all"])
            assert result.exit_code == 0
            assert "punchy" in result.output

    def test_perms_install_already(self):
        """Perms install when all already installed."""
        with patch('overcode.claude_config.ClaudeConfigEditor') as mock_editor_cls:
            mock_editor = MagicMock()
            mock_editor.load.return_value = {}
            mock_editor.add_permission.return_value = False
            mock_editor.path = Path("/tmp/.claude/settings.json")
            mock_editor_cls.user_level.return_value = mock_editor

            result = runner.invoke(app, ["perms", "install"])
            assert result.exit_code == 0
            assert "already installed" in result.output

    def test_perms_install_invalid_json(self):
        """Perms install handles invalid JSON."""
        with patch('overcode.claude_config.ClaudeConfigEditor') as mock_editor_cls:
            mock_editor = MagicMock()
            mock_editor.load.side_effect = ValueError("bad json")
            mock_editor_cls.user_level.return_value = mock_editor

            result = runner.invoke(app, ["perms", "install"])
            assert result.exit_code == 1


class TestPermsUninstallCommand:
    """Test perms uninstall command."""

    def test_perms_uninstall_success(self):
        """Perms uninstall removes permissions."""
        with patch('overcode.claude_config.ClaudeConfigEditor') as mock_editor_cls:
            mock_editor = MagicMock()
            mock_editor.load.return_value = {}
            mock_editor.remove_permission.return_value = True
            mock_editor.path = Path("/tmp/.claude/settings.json")
            mock_editor_cls.user_level.return_value = mock_editor

            result = runner.invoke(app, ["perms", "uninstall"])
            assert result.exit_code == 0
            assert "Removed" in result.output

    def test_perms_uninstall_none(self):
        """Perms uninstall when none found."""
        with patch('overcode.claude_config.ClaudeConfigEditor') as mock_editor_cls:
            mock_editor = MagicMock()
            mock_editor.load.return_value = {}
            mock_editor.remove_permission.return_value = False
            mock_editor.path = Path("/tmp/.claude/settings.json")
            mock_editor_cls.user_level.return_value = mock_editor

            result = runner.invoke(app, ["perms", "uninstall"])
            assert result.exit_code == 0
            assert "No overcode permissions found" in result.output


class TestPermsStatusCommand:
    """Test perms status command."""

    def test_perms_status(self):
        """Perms status shows installed permissions."""
        with patch('overcode.claude_config.ClaudeConfigEditor') as mock_editor_cls:
            mock_user = MagicMock()
            mock_user.load.return_value = {}
            mock_user.path = MagicMock()
            mock_user.path.exists.return_value = True
            mock_user.list_permissions_matching.return_value = ["Bash(overcode show *)"]

            mock_proj = MagicMock()
            mock_proj.load.return_value = {}
            mock_proj.path = MagicMock()
            mock_proj.path.exists.return_value = False

            mock_editor_cls.user_level.return_value = mock_user
            mock_editor_cls.project_level.return_value = mock_proj

            result = runner.invoke(app, ["perms", "status"])
            assert result.exit_code == 0
            assert "User-level" in result.output

    def test_perms_status_invalid_json(self):
        """Perms status handles invalid JSON."""
        with patch('overcode.claude_config.ClaudeConfigEditor') as mock_editor_cls:
            mock_user = MagicMock()
            mock_user.load.side_effect = ValueError("bad")
            mock_user.path = Path("/tmp/.claude/settings.json")
            mock_editor_cls.user_level.return_value = mock_user

            mock_proj = MagicMock()
            mock_proj.load.return_value = {}
            mock_proj.path = MagicMock()
            mock_proj.path.exists.return_value = False
            mock_editor_cls.project_level.return_value = mock_proj

            result = runner.invoke(app, ["perms", "status"])
            assert result.exit_code == 0
            assert "invalid JSON" in result.output


class TestHookHandlerCommand:
    """Test hook-handler command."""

    def test_hook_handler(self):
        """Hook handler calls handle_hook_event."""
        with patch('overcode.hook_handler.handle_hook_event') as mock_handler:
            result = runner.invoke(app, ["hook-handler"])
            assert result.exit_code == 0
            mock_handler.assert_called_once()


class TestInstructExtended:
    """Extended instruct command tests."""

    def test_instruct_set_preset(self):
        """Instruct sets a preset."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_session = MagicMock()
            mock_session.id = "test-id"
            mock_sm.get_session_by_name.return_value = mock_session
            mock_sm_cls.return_value = mock_sm

            with patch('overcode.standing_instructions.resolve_instructions') as mock_resolve:
                mock_resolve.return_value = ("Do absolutely nothing", "DO_NOTHING")

                result = runner.invoke(app, ["instruct", "my-agent", "DO_NOTHING"])
                assert result.exit_code == 0
                assert "DO_NOTHING" in result.output
                mock_sm.set_standing_instructions.assert_called_once_with(
                    "test-id", "Do absolutely nothing", preset_name="DO_NOTHING"
                )

    def test_instruct_set_custom(self):
        """Instruct sets custom instructions."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_session = MagicMock()
            mock_session.id = "test-id"
            mock_sm.get_session_by_name.return_value = mock_session
            mock_sm_cls.return_value = mock_sm

            with patch('overcode.standing_instructions.resolve_instructions') as mock_resolve:
                mock_resolve.return_value = ("Focus on tests", None)

                result = runner.invoke(app, ["instruct", "my-agent", "Focus", "on", "tests"])
                assert result.exit_code == 0
                assert "standing instructions" in result.output

    def test_instruct_show_current_preset(self):
        """Instruct shows current preset instructions."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_session = MagicMock()
            mock_session.id = "test-id"
            mock_session.standing_instructions = "Do nothing"
            mock_session.standing_instructions_preset = "DO_NOTHING"
            mock_sm.get_session_by_name.return_value = mock_session
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["instruct", "my-agent"])
            assert result.exit_code == 0
            assert "DO_NOTHING" in result.output

    def test_instruct_show_current_custom(self):
        """Instruct shows current custom instructions."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_session = MagicMock()
            mock_session.id = "test-id"
            mock_session.standing_instructions = "Custom stuff"
            mock_session.standing_instructions_preset = None
            mock_sm.get_session_by_name.return_value = mock_session
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["instruct", "my-agent"])
            assert result.exit_code == 0
            assert "Custom stuff" in result.output

    def test_instruct_show_no_instructions(self):
        """Instruct shows message when no instructions set."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_session = MagicMock()
            mock_session.id = "test-id"
            mock_session.standing_instructions = ""
            mock_sm.get_session_by_name.return_value = mock_session
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["instruct", "my-agent"])
            assert result.exit_code == 0
            assert "No standing instructions" in result.output


class TestHeartbeatCommand:
    """Test heartbeat command."""

    def _mock_agent(self, **kwargs):
        defaults = dict(
            id="agent-id",
            name="test-agent",
            heartbeat_enabled=False,
            heartbeat_frequency_seconds=300,
            heartbeat_instruction="",
            heartbeat_paused=False,
            last_heartbeat_time=None,
        )
        defaults.update(kwargs)
        agent = MagicMock()
        for k, v in defaults.items():
            setattr(agent, k, v)
        return agent

    def test_heartbeat_show_disabled(self):
        """Show heartbeat when disabled."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session_by_name.return_value = self._mock_agent()
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["heartbeat", "test-agent", "--show"])
            assert result.exit_code == 0
            assert "disabled" in result.output

    def test_heartbeat_show_enabled(self):
        """Show heartbeat when enabled."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session_by_name.return_value = self._mock_agent(
                heartbeat_enabled=True,
                heartbeat_instruction="Check status",
                last_heartbeat_time="2026-02-06T12:00:00",
            )
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["heartbeat", "test-agent", "--show"])
            assert result.exit_code == 0
            assert "enabled" in result.output
            assert "Check status" in result.output

    def test_heartbeat_show_paused(self):
        """Show heartbeat when paused."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session_by_name.return_value = self._mock_agent(
                heartbeat_enabled=True,
                heartbeat_paused=True,
                heartbeat_instruction="Check",
            )
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["heartbeat", "test-agent", "--show"])
            assert result.exit_code == 0
            assert "paused" in result.output

    def test_heartbeat_default_shows_config(self):
        """Heartbeat with no flags shows config."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session_by_name.return_value = self._mock_agent()
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["heartbeat", "test-agent"])
            assert result.exit_code == 0
            assert "disabled" in result.output

    def test_heartbeat_enable(self):
        """Enable heartbeat."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session_by_name.return_value = self._mock_agent()
            mock_sm_cls.return_value = mock_sm

            with patch('overcode.settings.signal_activity'):
                result = runner.invoke(app, [
                    "heartbeat", "test-agent",
                    "--enable", "--frequency", "5m",
                    "--instruction", "Status check"
                ])
                assert result.exit_code == 0
                assert "enabled" in result.output
                assert "Status check" in result.output

    def test_heartbeat_enable_default_frequency(self):
        """Enable heartbeat uses default 5min frequency."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session_by_name.return_value = self._mock_agent()
            mock_sm_cls.return_value = mock_sm

            with patch('overcode.settings.signal_activity'):
                result = runner.invoke(app, [
                    "heartbeat", "test-agent",
                    "--enable", "--instruction", "Check"
                ])
                assert result.exit_code == 0
                mock_sm.update_session.assert_called_once()
                call_kwargs = mock_sm.update_session.call_args[1]
                assert call_kwargs['heartbeat_frequency_seconds'] == 300

    def test_heartbeat_enable_requires_instruction(self):
        """Enable heartbeat requires --instruction."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session_by_name.return_value = self._mock_agent()
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["heartbeat", "test-agent", "--enable"])
            assert result.exit_code == 1
            assert "instruction required" in result.output

    def test_heartbeat_disable(self):
        """Disable heartbeat."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session_by_name.return_value = self._mock_agent(heartbeat_enabled=True)
            mock_sm_cls.return_value = mock_sm

            with patch('overcode.settings.signal_activity'):
                result = runner.invoke(app, ["heartbeat", "test-agent", "--disable"])
                assert result.exit_code == 0
                assert "disabled" in result.output

    def test_heartbeat_pause(self):
        """Pause heartbeat."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session_by_name.return_value = self._mock_agent(heartbeat_enabled=True)
            mock_sm_cls.return_value = mock_sm

            with patch('overcode.settings.signal_activity'):
                result = runner.invoke(app, ["heartbeat", "test-agent", "--pause"])
                assert result.exit_code == 0
                assert "paused" in result.output

    def test_heartbeat_pause_not_enabled(self):
        """Pause heartbeat when not enabled."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session_by_name.return_value = self._mock_agent(heartbeat_enabled=False)
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["heartbeat", "test-agent", "--pause"])
            assert result.exit_code == 0
            assert "not enabled" in result.output

    def test_heartbeat_resume(self):
        """Resume heartbeat."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session_by_name.return_value = self._mock_agent(
                heartbeat_enabled=True, heartbeat_paused=True
            )
            mock_sm_cls.return_value = mock_sm

            with patch('overcode.settings.signal_activity'):
                result = runner.invoke(app, ["heartbeat", "test-agent", "--resume"])
                assert result.exit_code == 0
                assert "resumed" in result.output

    def test_heartbeat_resume_not_enabled(self):
        """Resume heartbeat when not enabled."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session_by_name.return_value = self._mock_agent(heartbeat_enabled=False)
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["heartbeat", "test-agent", "--resume"])
            assert result.exit_code == 0
            assert "not enabled" in result.output

    def test_heartbeat_update_frequency_only(self):
        """Update just frequency without full enable."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session_by_name.return_value = self._mock_agent(heartbeat_enabled=True)
            mock_sm_cls.return_value = mock_sm

            with patch('overcode.settings.signal_activity'):
                result = runner.invoke(app, ["heartbeat", "test-agent", "--frequency", "10m"])
                assert result.exit_code == 0
                assert "updated" in result.output

    def test_heartbeat_update_instruction_only(self):
        """Update just instruction without full enable."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session_by_name.return_value = self._mock_agent(heartbeat_enabled=True)
            mock_sm_cls.return_value = mock_sm

            with patch('overcode.settings.signal_activity'):
                result = runner.invoke(app, ["heartbeat", "test-agent", "--instruction", "New instruction"])
                assert result.exit_code == 0
                assert "updated" in result.output

    def test_heartbeat_not_found(self):
        """Heartbeat errors when agent not found."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session_by_name.return_value = None
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["heartbeat", "missing"])
            assert result.exit_code == 1
            assert "not found" in result.output

    def test_heartbeat_invalid_frequency(self):
        """Heartbeat rejects invalid frequency."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session_by_name.return_value = self._mock_agent()
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["heartbeat", "test-agent", "--frequency", "abc"])
            assert result.exit_code == 1
            assert "Invalid frequency" in result.output

    def test_heartbeat_frequency_too_low(self):
        """Heartbeat rejects frequency below 30s."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session_by_name.return_value = self._mock_agent()
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["heartbeat", "test-agent", "--frequency", "10s"])
            assert result.exit_code == 1
            assert "Minimum" in result.output

    def test_heartbeat_frequency_seconds_suffix(self):
        """Heartbeat parses seconds suffix."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session_by_name.return_value = self._mock_agent(heartbeat_enabled=True)
            mock_sm_cls.return_value = mock_sm

            with patch('overcode.settings.signal_activity'):
                result = runner.invoke(app, ["heartbeat", "test-agent", "--frequency", "60s"])
                assert result.exit_code == 0

    def test_heartbeat_frequency_hours_suffix(self):
        """Heartbeat parses hours suffix."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session_by_name.return_value = self._mock_agent(heartbeat_enabled=True)
            mock_sm_cls.return_value = mock_sm

            with patch('overcode.settings.signal_activity'):
                result = runner.invoke(app, ["heartbeat", "test-agent", "--frequency", "1h"])
                assert result.exit_code == 0

    def test_heartbeat_frequency_bare_number(self):
        """Heartbeat parses bare number as seconds."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.get_session_by_name.return_value = self._mock_agent(heartbeat_enabled=True)
            mock_sm_cls.return_value = mock_sm

            with patch('overcode.settings.signal_activity'):
                result = runner.invoke(app, ["heartbeat", "test-agent", "--frequency", "120"])
                assert result.exit_code == 0


class TestMonitorDaemonStartCommand:
    """Test monitor-daemon start command."""

    def test_daemon_start_already_running(self):
        """Start errors when already running."""
        with patch('overcode.monitor_daemon.is_monitor_daemon_running', return_value=True):
            with patch('overcode.monitor_daemon.get_monitor_daemon_pid', return_value=1234):
                result = runner.invoke(app, ["monitor-daemon", "start"])
                assert result.exit_code == 1
                assert "already running" in result.output

    def test_daemon_start_success(self):
        """Start launches daemon."""
        with patch('overcode.monitor_daemon.is_monitor_daemon_running', return_value=False):
            with patch('overcode.monitor_daemon.MonitorDaemon') as mock_daemon_cls:
                mock_daemon = MagicMock()
                mock_daemon_cls.return_value = mock_daemon

                result = runner.invoke(app, ["monitor-daemon", "start"])
                assert result.exit_code == 0
                mock_daemon.run.assert_called_once_with(10)


class TestMonitorDaemonStopCommand:
    """Test monitor-daemon stop command."""

    def test_daemon_stop_not_running(self):
        """Stop when not running shows message."""
        with patch('overcode.monitor_daemon.is_monitor_daemon_running', return_value=False):
            result = runner.invoke(app, ["monitor-daemon", "stop"])
            assert result.exit_code == 0
            assert "not running" in result.output

    def test_daemon_stop_success(self):
        """Stop successfully stops daemon."""
        with patch('overcode.monitor_daemon.is_monitor_daemon_running', return_value=True):
            with patch('overcode.monitor_daemon.get_monitor_daemon_pid', return_value=1234):
                with patch('overcode.monitor_daemon.stop_monitor_daemon', return_value=True):
                    result = runner.invoke(app, ["monitor-daemon", "stop"])
                    assert result.exit_code == 0
                    assert "stopped" in result.output

    def test_daemon_stop_failure(self):
        """Stop fails shows error."""
        with patch('overcode.monitor_daemon.is_monitor_daemon_running', return_value=True):
            with patch('overcode.monitor_daemon.get_monitor_daemon_pid', return_value=1234):
                with patch('overcode.monitor_daemon.stop_monitor_daemon', return_value=False):
                    result = runner.invoke(app, ["monitor-daemon", "stop"])
                    assert result.exit_code == 1
                    assert "Failed" in result.output


class TestMonitorDaemonStatusCommand:
    """Test monitor-daemon status command."""

    def test_daemon_status_stopped(self):
        """Status when stopped."""
        with patch('overcode.monitor_daemon.is_monitor_daemon_running', return_value=False):
            with patch('overcode.monitor_daemon_state.get_monitor_daemon_state', return_value=None):
                with patch('overcode.settings.get_monitor_daemon_state_path', return_value=Path("/tmp/test")):
                    result = runner.invoke(app, ["monitor-daemon", "status"])
                    assert result.exit_code == 0
                    assert "stopped" in result.output

    def test_daemon_status_running(self):
        """Status when running."""
        mock_state = MagicMock()
        mock_state.status = "running"
        mock_state.loop_count = 42
        mock_state.current_interval = 10
        mock_state.sessions = [MagicMock()]
        mock_state.last_loop_time = None
        mock_state.presence_available = False

        with patch('overcode.monitor_daemon.is_monitor_daemon_running', return_value=True):
            with patch('overcode.monitor_daemon.get_monitor_daemon_pid', return_value=5678):
                with patch('overcode.monitor_daemon_state.get_monitor_daemon_state', return_value=mock_state):
                    with patch('overcode.settings.get_monitor_daemon_state_path', return_value=Path("/tmp/test")):
                        result = runner.invoke(app, ["monitor-daemon", "status"])
                        assert result.exit_code == 0
                        assert "running" in result.output
                        assert "5678" in result.output

    def test_daemon_status_with_presence(self):
        """Status shows presence info when available."""
        from datetime import datetime
        mock_state = MagicMock()
        mock_state.status = "running"
        mock_state.loop_count = 10
        mock_state.current_interval = 10
        mock_state.sessions = []
        mock_state.last_loop_time = datetime(2026, 2, 6, 12, 0, 0)
        mock_state.presence_available = True
        mock_state.presence_state = "active"
        mock_state.presence_idle_seconds = 0.0

        with patch('overcode.monitor_daemon.is_monitor_daemon_running', return_value=True):
            with patch('overcode.monitor_daemon.get_monitor_daemon_pid', return_value=1234):
                with patch('overcode.monitor_daemon_state.get_monitor_daemon_state', return_value=mock_state):
                    with patch('overcode.settings.get_monitor_daemon_state_path', return_value=Path("/tmp/test")):
                        result = runner.invoke(app, ["monitor-daemon", "status"])
                        assert result.exit_code == 0
                        assert "Presence" in result.output


class TestMonitorDaemonWatchCommand:
    """Test monitor-daemon watch command."""

    def test_watch_no_log(self):
        """Watch errors when log file doesn't exist."""
        with patch('overcode.settings.get_session_dir') as mock_dir:
            mock_path = MagicMock()
            mock_log = MagicMock()
            mock_log.exists.return_value = False
            mock_path.__truediv__ = MagicMock(return_value=mock_log)
            mock_dir.return_value = mock_path

            result = runner.invoke(app, ["monitor-daemon", "watch"])
            assert result.exit_code == 1
            assert "Log file not found" in result.output


class TestSupervisorDaemonStartCommand:
    """Test supervisor-daemon start command."""

    def test_start_already_running(self):
        """Start errors when already running."""
        with patch('overcode.supervisor_daemon.is_supervisor_daemon_running', return_value=True):
            with patch('overcode.supervisor_daemon.get_supervisor_daemon_pid', return_value=1234):
                result = runner.invoke(app, ["supervisor-daemon", "start"])
                assert result.exit_code == 1
                assert "already running" in result.output

    def test_start_success(self):
        """Start launches supervisor daemon."""
        with patch('overcode.supervisor_daemon.is_supervisor_daemon_running', return_value=False):
            with patch('overcode.supervisor_daemon.SupervisorDaemon') as mock_cls:
                mock_daemon = MagicMock()
                mock_cls.return_value = mock_daemon

                result = runner.invoke(app, ["supervisor-daemon", "start"])
                assert result.exit_code == 0
                mock_daemon.run.assert_called_once_with(10)


class TestSupervisorDaemonStopCommand:
    """Test supervisor-daemon stop command."""

    def test_stop_not_running(self):
        """Stop when not running."""
        with patch('overcode.supervisor_daemon.is_supervisor_daemon_running', return_value=False):
            result = runner.invoke(app, ["supervisor-daemon", "stop"])
            assert result.exit_code == 0
            assert "not running" in result.output

    def test_stop_success(self):
        """Stop successfully."""
        with patch('overcode.supervisor_daemon.is_supervisor_daemon_running', return_value=True):
            with patch('overcode.supervisor_daemon.get_supervisor_daemon_pid', return_value=1234):
                with patch('overcode.supervisor_daemon.stop_supervisor_daemon', return_value=True):
                    result = runner.invoke(app, ["supervisor-daemon", "stop"])
                    assert result.exit_code == 0
                    assert "stopped" in result.output

    def test_stop_failure(self):
        """Stop failure."""
        with patch('overcode.supervisor_daemon.is_supervisor_daemon_running', return_value=True):
            with patch('overcode.supervisor_daemon.get_supervisor_daemon_pid', return_value=1234):
                with patch('overcode.supervisor_daemon.stop_supervisor_daemon', return_value=False):
                    result = runner.invoke(app, ["supervisor-daemon", "stop"])
                    assert result.exit_code == 1
                    assert "Failed" in result.output


class TestSupervisorDaemonStatusCommand:
    """Test supervisor-daemon status command."""

    def test_status_stopped(self):
        """Status when stopped."""
        with patch('overcode.supervisor_daemon.is_supervisor_daemon_running', return_value=False):
            result = runner.invoke(app, ["supervisor-daemon", "status"])
            assert result.exit_code == 0
            assert "stopped" in result.output

    def test_status_running(self):
        """Status when running."""
        with patch('overcode.supervisor_daemon.is_supervisor_daemon_running', return_value=True):
            with patch('overcode.supervisor_daemon.get_supervisor_daemon_pid', return_value=9999):
                result = runner.invoke(app, ["supervisor-daemon", "status"])
                assert result.exit_code == 0
                assert "running" in result.output
                assert "9999" in result.output


class TestSupervisorDaemonWatchCommand:
    """Test supervisor-daemon watch command."""

    def test_watch_no_log(self):
        """Watch errors when log file doesn't exist."""
        with patch('overcode.settings.get_session_dir') as mock_dir:
            mock_path = MagicMock()
            mock_log = MagicMock()
            mock_log.exists.return_value = False
            mock_path.__truediv__ = MagicMock(return_value=mock_log)
            mock_dir.return_value = mock_path

            result = runner.invoke(app, ["supervisor-daemon", "watch"])
            assert result.exit_code == 1
            assert "Log file not found" in result.output


class TestSisterCommands:
    """Test sister subcommands."""

    def test_sister_list_none(self):
        """Sister list when none configured."""
        with patch('overcode.config.get_sisters_config', return_value=[]):
            result = runner.invoke(app, ["sister", "list"])
            assert result.exit_code == 0
            assert "No sister instances" in result.output

    def test_sister_list_with_sisters(self):
        """Sister list shows configured sisters."""
        sisters = [
            {"name": "macbook", "url": "http://localhost:5337"},
            {"name": "desktop", "url": "http://192.168.1.10:5337", "api_key": "secret123"},
        ]
        with patch('overcode.config.get_sisters_config', return_value=sisters):
            result = runner.invoke(app, ["sister", "list"])
            assert result.exit_code == 0
            assert "macbook" in result.output
            assert "desktop" in result.output
            assert "secr..." in result.output  # masked API key

    def test_sister_default_lists(self):
        """Sister with no subcommand lists."""
        with patch('overcode.config.get_sisters_config', return_value=[]):
            result = runner.invoke(app, ["sister"])
            assert result.exit_code == 0
            assert "No sister instances" in result.output

    def test_sister_add(self):
        """Sister add creates new entry."""
        with patch('overcode.config.load_config', return_value={"sisters": []}):
            with patch('overcode.config.save_config') as mock_save:
                result = runner.invoke(app, ["sister", "add", "macbook", "http://localhost:5337"])
                assert result.exit_code == 0
                assert "Added sister" in result.output
                saved_config = mock_save.call_args[0][0]
                assert len(saved_config["sisters"]) == 1
                assert saved_config["sisters"][0]["name"] == "macbook"

    def test_sister_add_with_api_key(self):
        """Sister add with API key."""
        with patch('overcode.config.load_config', return_value={"sisters": []}):
            with patch('overcode.config.save_config') as mock_save:
                result = runner.invoke(app, [
                    "sister", "add", "desktop", "http://192.168.1.10:5337",
                    "--api-key", "secret"
                ])
                assert result.exit_code == 0
                saved_config = mock_save.call_args[0][0]
                assert saved_config["sisters"][0]["api_key"] == "secret"

    def test_sister_add_duplicate(self):
        """Sister add rejects duplicate name."""
        with patch('overcode.config.load_config', return_value={
            "sisters": [{"name": "macbook", "url": "http://localhost:5337"}]
        }):
            result = runner.invoke(app, ["sister", "add", "macbook", "http://other:5337"])
            assert result.exit_code == 1
            assert "already exists" in result.output

    def test_sister_remove(self):
        """Sister remove deletes entry."""
        with patch('overcode.config.load_config', return_value={
            "sisters": [{"name": "macbook", "url": "http://localhost:5337"}]
        }):
            with patch('overcode.config.save_config') as mock_save:
                result = runner.invoke(app, ["sister", "remove", "macbook"])
                assert result.exit_code == 0
                assert "Removed sister" in result.output
                saved_config = mock_save.call_args[0][0]
                assert len(saved_config["sisters"]) == 0

    def test_sister_remove_not_found(self):
        """Sister remove errors when not found."""
        with patch('overcode.config.load_config', return_value={"sisters": []}):
            result = runner.invoke(app, ["sister", "remove", "missing"])
            assert result.exit_code == 1
            assert "not found" in result.output

    def test_sister_allow_control_show_disabled(self):
        """Show allow-control status when disabled."""
        with patch('overcode.config.load_config', return_value={"web": {"allow_control": False}}):
            with patch('overcode.config.get_web_api_key', return_value=None):
                result = runner.invoke(app, ["sister", "allow-control"])
                assert result.exit_code == 0
                assert "disabled" in result.output

    def test_sister_allow_control_show_enabled(self):
        """Show allow-control status when enabled."""
        with patch('overcode.config.load_config', return_value={"web": {"allow_control": True}}):
            with patch('overcode.config.get_web_api_key', return_value="secretkey"):
                result = runner.invoke(app, ["sister", "allow-control"])
                assert result.exit_code == 0
                assert "enabled" in result.output

    def test_sister_allow_control_on(self):
        """Enable allow-control."""
        with patch('overcode.config.load_config', return_value={"web": {}}):
            with patch('overcode.config.save_config'):
                with patch('overcode.config.get_web_api_key', return_value="key123"):
                    result = runner.invoke(app, ["sister", "allow-control", "--on"])
                    assert result.exit_code == 0
                    assert "enabled" in result.output

    def test_sister_allow_control_on_no_api_key(self):
        """Enable allow-control warns when no api_key."""
        with patch('overcode.config.load_config', return_value={"web": {}}):
            with patch('overcode.config.save_config'):
                with patch('overcode.config.get_web_api_key', return_value=None):
                    result = runner.invoke(app, ["sister", "allow-control", "--on"])
                    assert result.exit_code == 0
                    assert "Warning" in result.output

    def test_sister_allow_control_off(self):
        """Disable allow-control."""
        with patch('overcode.config.load_config', return_value={"web": {"allow_control": True}}):
            with patch('overcode.config.save_config'):
                result = runner.invoke(app, ["sister", "allow-control", "--off"])
                assert result.exit_code == 0
                assert "disabled" in result.output

    def test_sister_allow_control_on_and_off(self):
        """Both --on and --off errors."""
        with patch('overcode.config.load_config', return_value={"web": {}}):
            result = runner.invoke(app, ["sister", "allow-control", "--on", "--off"])
            assert result.exit_code == 1
            assert "Cannot use --on and --off" in result.output


class TestConfigInitCommand:
    """Test config init command."""

    def test_config_init_creates_file(self, tmp_path):
        """Config init creates config file."""
        config_path = tmp_path / "config.yaml"
        with patch('overcode.config.CONFIG_PATH', config_path):
            result = runner.invoke(app, ["config", "init"])
            assert result.exit_code == 0
            assert "Created config file" in result.output
            assert config_path.exists()

    def test_config_init_exists_no_force(self, tmp_path):
        """Config init refuses to overwrite without --force."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("existing content")
        with patch('overcode.config.CONFIG_PATH', config_path):
            result = runner.invoke(app, ["config", "init"])
            assert result.exit_code == 1
            assert "already exists" in result.output

    def test_config_init_force(self, tmp_path):
        """Config init --force overwrites."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("old content")
        with patch('overcode.config.CONFIG_PATH', config_path):
            result = runner.invoke(app, ["config", "init", "--force"])
            assert result.exit_code == 0
            assert "Created config file" in result.output


class TestConfigShowCommand:
    """Test config show command."""

    def test_config_show_no_file(self, tmp_path):
        """Config show when no config file."""
        config_path = tmp_path / "config.yaml"
        with patch('overcode.config.CONFIG_PATH', config_path):
            result = runner.invoke(app, ["config", "show"])
            assert result.exit_code == 0
            assert "No config file" in result.output

    def test_config_show_empty(self, tmp_path):
        """Config show when config is empty."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("")
        with patch('overcode.config.CONFIG_PATH', config_path):
            with patch('overcode.config.load_config', return_value={}):
                result = runner.invoke(app, ["config", "show"])
                assert result.exit_code == 0
                assert "empty" in result.output

    def test_config_show_with_content(self, tmp_path):
        """Config show with content displays sections."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("test: true")
        config = {
            "default_standing_instructions": "Be concise",
            "summarizer": {"api_url": "https://api.example.com", "model": "gpt-4", "api_key_var": "KEY"},
            "relay": {"enabled": True, "url": "https://relay.example.com", "interval": 30},
            "web": {"time_presets": [{"name": "Morning", "start": "09:00", "end": "12:00"}]},
            "sisters": [{"name": "macbook", "url": "http://localhost:5337"}],
        }
        with patch('overcode.config.CONFIG_PATH', config_path):
            with patch('overcode.config.load_config', return_value=config):
                result = runner.invoke(app, ["config", "show"])
                assert result.exit_code == 0
                assert "Be concise" in result.output
                assert "summarizer" in result.output
                assert "relay" in result.output
                assert "macbook" in result.output


class TestConfigPathCommand:
    """Test config path command."""

    def test_config_path(self):
        """Config path outputs path."""
        with patch('overcode.config.CONFIG_PATH', Path("/tmp/test/config.yaml")):
            result = runner.invoke(app, ["config", "path"])
            assert result.exit_code == 0
            assert "/tmp/test/config.yaml" in result.output


class TestExportExtended:
    """Extended export command tests."""

    def test_export_success(self):
        """Export creates parquet file."""
        mock_result = {
            "sessions_count": 5,
            "archived_count": 10,
            "timeline_rows": 100,
            "presence_rows": 50,
        }
        with patch('overcode.data_export.export_to_parquet', return_value=mock_result):
            result = runner.invoke(app, ["export", "/tmp/output.parquet"])
            assert result.exit_code == 0
            assert "Exported" in result.output
            assert "Sessions: 5" in result.output
            assert "Archived: 10" in result.output
            assert "Timeline rows: 100" in result.output
            assert "Presence rows: 50" in result.output

    def test_export_import_error(self):
        """Export handles missing pyarrow."""
        with patch('overcode.data_export.export_to_parquet', side_effect=ImportError("No pyarrow")):
            result = runner.invoke(app, ["export", "/tmp/output.parquet"])
            assert result.exit_code == 1
            assert "pyarrow" in result.output

    def test_export_generic_error(self):
        """Export handles generic errors."""
        with patch('overcode.data_export.export_to_parquet', side_effect=RuntimeError("Disk full")):
            result = runner.invoke(app, ["export", "/tmp/output.parquet"])
            assert result.exit_code == 1
            assert "Export failed" in result.output


class TestHistoryExtended:
    """Extended history command tests."""

    def test_history_no_archived(self):
        """History shows message when no archived sessions."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.list_archived_sessions.return_value = []
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["history"])
            assert result.exit_code == 0
            assert "No archived sessions" in result.output

    def test_history_list_archived(self):
        """History lists archived sessions."""
        from overcode.session_manager import SessionStats
        mock_session = _make_session(
            name="old-agent",
            stats=SessionStats(
                interaction_count=10,
                total_tokens=50000,
                estimated_cost_usd=2.50,
                green_time_seconds=1800.0,
                non_green_time_seconds=300.0,
                steers_count=5,
            ),
        )

        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.list_archived_sessions.return_value = [mock_session]
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["history"])
            assert result.exit_code == 0
            assert "Archived Sessions" in result.output
            assert "old-agent" in result.output

    def test_history_specific_agent(self):
        """History shows specific archived agent."""
        from overcode.session_manager import SessionStats
        mock_session = _make_session(
            name="old-agent",
            stats=SessionStats(
                interaction_count=10,
                total_tokens=50000,
                estimated_cost_usd=2.50,
                green_time_seconds=1800.0,
                non_green_time_seconds=300.0,
                steers_count=5,
            ),
        )

        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.list_archived_sessions.return_value = [mock_session]
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["history", "old-agent"])
            assert result.exit_code == 0
            assert "old-agent" in result.output
            assert "Stats" in result.output

    def test_history_agent_not_found(self):
        """History errors when agent not in archive."""
        with patch('overcode.session_manager.SessionManager') as mock_sm_cls:
            mock_sm = MagicMock()
            mock_sm.list_archived_sessions.return_value = []
            mock_sm_cls.return_value = mock_sm

            result = runner.invoke(app, ["history", "missing"])
            assert result.exit_code == 1
            assert "No archived session" in result.output


class TestAttachExtended:
    """Extended attach command tests."""

    def test_attach_no_name(self):
        """Attach with no name attaches to session."""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_cls.return_value = mock_launcher

            result = runner.invoke(app, ["attach"])
            assert result.exit_code == 0
            assert "Attaching to overcode" in result.output
            mock_launcher.attach.assert_called_once_with(name=None, bare=False)

    def test_attach_with_name(self):
        """Attach with name jumps to that agent."""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_cls.return_value = mock_launcher

            result = runner.invoke(app, ["attach", "my-agent"])
            assert result.exit_code == 0
            assert "Attaching to 'my-agent'" in result.output

    def test_attach_bare_with_name(self):
        """Attach --bare with name uses bare mode."""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_cls.return_value = mock_launcher

            result = runner.invoke(app, ["attach", "my-agent", "--bare"])
            assert result.exit_code == 0
            assert "bare mode" in result.output
            mock_launcher.attach.assert_called_once_with(name="my-agent", bare=True)


class TestListExtended:
    """Extended list command tests with full execution."""

    def test_list_with_sessions(self):
        """List shows sessions with status columns."""
        mock_session = _make_session()

        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_launcher.list_sessions.return_value = [mock_session]
            mock_cls.return_value = mock_launcher

            with patch('overcode.monitor_daemon_state.get_monitor_daemon_state', return_value=None):
                with patch('overcode.status_detector_factory.StatusDetectorDispatcher') as mock_disp_cls:
                    mock_disp = MagicMock()
                    mock_disp.detect_status.return_value = ("running", "Working on code", "pane")
                    mock_disp_cls.return_value = mock_disp

                    with patch('overcode.history_reader.get_session_stats', return_value=None):
                        with patch('overcode.tui_helpers.get_git_diff_stats', return_value=None):
                            with patch('overcode.tui_logic.sort_sessions_by_tree', return_value=[mock_session]):
                                with patch('overcode.tui_logic.compute_tree_metadata') as mock_tree:
                                    mock_tree.return_value = {
                                        "test-id": MagicMock(depth=0, child_count=0)
                                    }
                                    result = runner.invoke(app, ["list"])
                                    assert result.exit_code == 0
                                    # Should have output (some columns rendered)
                                    assert len(result.output.strip()) > 0

    def test_list_terminated_shows_cleanup_hint(self):
        """List shows cleanup hint when terminated sessions exist."""
        mock_session = _make_session(status="terminated")

        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_launcher.list_sessions.return_value = [mock_session]
            mock_cls.return_value = mock_launcher

            with patch('overcode.monitor_daemon_state.get_monitor_daemon_state', return_value=None):
                with patch('overcode.status_detector_factory.StatusDetectorDispatcher') as mock_disp_cls:
                    mock_disp = MagicMock()
                    mock_disp.detect_status.return_value = ("terminated", "gone", "")
                    mock_disp_cls.return_value = mock_disp

                    with patch('overcode.history_reader.get_session_stats', return_value=None):
                        with patch('overcode.tui_helpers.get_git_diff_stats', return_value=None):
                            with patch('overcode.tui_logic.sort_sessions_by_tree', return_value=[mock_session]):
                                with patch('overcode.tui_logic.compute_tree_metadata') as mock_tree:
                                    mock_tree.return_value = {
                                        "test-id": MagicMock(depth=0, child_count=0)
                                    }
                                    result = runner.invoke(app, ["list"])
                                    assert result.exit_code == 0
                                    assert "cleanup" in result.output

    def test_list_filter_by_name_not_found(self):
        """List with name filter errors when agent not found."""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_launcher.list_sessions.return_value = [_make_session()]
            mock_launcher.sessions.get_session_by_name.return_value = None
            mock_cls.return_value = mock_launcher

            with patch('overcode.monitor_daemon_state.get_monitor_daemon_state', return_value=None):
                result = runner.invoke(app, ["list", "missing"])
                assert result.exit_code == 1
                assert "not found" in result.output

    def test_list_filter_by_name(self):
        """List with name filter shows only matching tree."""
        root = _make_session(name="root")
        child = _make_session(name="child", id="child-id", parent_session_id="test-id")

        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_launcher.list_sessions.return_value = [root, child]
            mock_launcher.sessions.get_session_by_name.return_value = root
            mock_launcher.sessions.get_descendants.return_value = [child]
            mock_cls.return_value = mock_launcher

            with patch('overcode.monitor_daemon_state.get_monitor_daemon_state', return_value=None):
                with patch('overcode.status_detector_factory.StatusDetectorDispatcher') as mock_disp_cls:
                    mock_disp = MagicMock()
                    mock_disp.detect_status.return_value = ("running", "Working", "pane")
                    mock_disp_cls.return_value = mock_disp

                    with patch('overcode.history_reader.get_session_stats', return_value=None):
                        with patch('overcode.tui_helpers.get_git_diff_stats', return_value=None):
                            with patch('overcode.tui_logic.sort_sessions_by_tree', return_value=[root, child]):
                                with patch('overcode.tui_logic.compute_tree_metadata') as mock_tree:
                                    mock_tree.return_value = {
                                        "test-id": MagicMock(depth=0, child_count=1),
                                        "child-id": MagicMock(depth=1, child_count=0),
                                    }
                                    result = runner.invoke(app, ["list", "root"])
                                    assert result.exit_code == 0

    def test_list_with_daemon_state(self):
        """List uses daemon state when available."""
        mock_session = _make_session()

        mock_daemon_state = MagicMock()
        mock_daemon_state.is_stale.return_value = False
        mock_ds = MagicMock()
        mock_ds.current_status = "running"
        mock_ds.current_activity = "Coding"
        mock_daemon_state.get_session_by_name.return_value = mock_ds

        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_launcher.list_sessions.return_value = [mock_session]
            mock_cls.return_value = mock_launcher

            with patch('overcode.monitor_daemon_state.get_monitor_daemon_state', return_value=mock_daemon_state):
                with patch('overcode.history_reader.get_session_stats', return_value=None):
                    with patch('overcode.tui_helpers.get_git_diff_stats', return_value=None):
                        with patch('overcode.tui_logic.sort_sessions_by_tree', return_value=[mock_session]):
                            with patch('overcode.tui_logic.compute_tree_metadata') as mock_tree:
                                mock_tree.return_value = {
                                    "test-id": MagicMock(depth=0, child_count=0)
                                }
                                result = runner.invoke(app, ["list"])
                                assert result.exit_code == 0

    def test_list_show_done(self):
        """List with --show-done includes done agents."""
        done_session = _make_session(name="done-agent", status="done")
        running_session = _make_session(name="running-agent")

        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_launcher.list_sessions.return_value = [running_session, done_session]
            mock_cls.return_value = mock_launcher

            with patch('overcode.monitor_daemon_state.get_monitor_daemon_state', return_value=None):
                with patch('overcode.status_detector_factory.StatusDetectorDispatcher') as mock_disp_cls:
                    mock_disp = MagicMock()
                    mock_disp.detect_status.return_value = ("running", "Working", "pane")
                    mock_disp_cls.return_value = mock_disp

                    with patch('overcode.history_reader.get_session_stats', return_value=None):
                        with patch('overcode.tui_helpers.get_git_diff_stats', return_value=None):
                            with patch('overcode.tui_logic.sort_sessions_by_tree', return_value=[running_session, done_session]):
                                with patch('overcode.tui_logic.compute_tree_metadata') as mock_tree:
                                    mock_tree.return_value = {
                                        "test-id": MagicMock(depth=0, child_count=0),
                                    }
                                    result = runner.invoke(app, ["list", "--show-done"])
                                    assert result.exit_code == 0

    def test_list_filters_done_by_default(self):
        """List without --show-done excludes done agents."""
        done_session = _make_session(name="done-only", status="done", id="done-id")

        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_launcher.list_sessions.return_value = [done_session]
            mock_cls.return_value = mock_launcher

            with patch('overcode.monitor_daemon_state.get_monitor_daemon_state', return_value=None):
                result = runner.invoke(app, ["list"])
                assert result.exit_code == 0
                assert "No running agents" in result.output

    def test_list_asleep_agent(self):
        """List handles asleep agent status."""
        mock_session = _make_session(is_asleep=True)

        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_launcher.list_sessions.return_value = [mock_session]
            mock_cls.return_value = mock_launcher

            with patch('overcode.monitor_daemon_state.get_monitor_daemon_state', return_value=None):
                with patch('overcode.status_detector_factory.StatusDetectorDispatcher') as mock_disp_cls:
                    mock_disp = MagicMock()
                    mock_disp.detect_status.return_value = ("running", "Working", "")
                    mock_disp_cls.return_value = mock_disp

                    with patch('overcode.history_reader.get_session_stats', return_value=None):
                        with patch('overcode.tui_helpers.get_git_diff_stats', return_value=None):
                            with patch('overcode.tui_logic.sort_sessions_by_tree', return_value=[mock_session]):
                                with patch('overcode.tui_logic.compute_tree_metadata') as mock_tree:
                                    mock_tree.return_value = {
                                        "test-id": MagicMock(depth=0, child_count=0)
                                    }
                                    result = runner.invoke(app, ["list"])
                                    assert result.exit_code == 0


class TestKillExtended:
    """Extended kill command tests."""

    def test_kill_with_no_cascade(self):
        """Kill with --no-cascade passes cascade=False."""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_cls.return_value = mock_launcher

            result = runner.invoke(app, ["kill", "my-agent", "--no-cascade"])
            assert result.exit_code == 0
            mock_launcher.kill_session.assert_called_once_with("my-agent", cascade=False)

    def test_kill_default_cascades(self):
        """Kill without --no-cascade passes cascade=True."""
        with patch('overcode.cli.agent.ClaudeLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_cls.return_value = mock_launcher

            result = runner.invoke(app, ["kill", "my-agent"])
            assert result.exit_code == 0
            mock_launcher.kill_session.assert_called_once_with("my-agent", cascade=True)


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
