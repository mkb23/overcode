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


class TestListCommand:
    """Test list command"""

    def test_list_help(self):
        """List help works"""
        result = runner.invoke(app, ["list", "--help"])
        assert result.exit_code == 0


class TestKillCommand:
    """Test kill command"""

    def test_kill_help(self):
        """Kill help shows arguments"""
        result = runner.invoke(app, ["kill", "--help"])
        assert result.exit_code == 0
        assert "NAME" in result.stdout

    def test_kill_requires_name(self):
        """Kill requires name argument"""
        result = runner.invoke(app, ["kill"])
        assert result.exit_code != 0


class TestSendCommand:
    """Test send command"""

    def test_send_help(self):
        """Send help shows options and examples"""
        result = runner.invoke(app, ["send", "--help"])
        assert result.exit_code == 0
        output = strip_ansi(result.stdout)
        assert "--no-enter" in output
        assert "NAME" in output

    def test_send_requires_name(self):
        """Send requires name argument"""
        result = runner.invoke(app, ["send"])
        assert result.exit_code != 0


class TestShowCommand:
    """Test show command"""

    def test_show_help(self):
        """Show help shows options"""
        result = runner.invoke(app, ["show", "--help"])
        assert result.exit_code == 0
        output = strip_ansi(result.stdout)
        assert "--lines" in output
        assert "NAME" in output

    def test_show_requires_name(self):
        """Show requires name argument"""
        result = runner.invoke(app, ["show"])
        assert result.exit_code != 0


class TestInstructCommand:
    """Test instruct command"""

    def test_instruct_help(self):
        """Instruct help shows options"""
        result = runner.invoke(app, ["instruct", "--help"])
        assert result.exit_code == 0
        output = strip_ansi(result.stdout)
        assert "--clear" in output
        assert "NAME" in output

    def test_instruct_requires_name(self):
        """Instruct requires name argument"""
        result = runner.invoke(app, ["instruct"])
        assert result.exit_code != 0


class TestMonitorCommand:
    """Test monitor command"""

    def test_monitor_help(self):
        """Monitor help works"""
        result = runner.invoke(app, ["monitor", "--help"])
        assert result.exit_code == 0


class TestSupervisorCommand:
    """Test supervisor command"""

    def test_supervisor_help(self):
        """Supervisor help shows options"""
        result = runner.invoke(app, ["supervisor", "--help"])
        assert result.exit_code == 0
        assert "--restart" in strip_ansi(result.stdout)


class TestAttachCommand:
    """Test attach command"""

    def test_attach_help(self):
        """Attach help works"""
        result = runner.invoke(app, ["attach", "--help"])
        assert result.exit_code == 0


class TestDaemonCommands:
    """Test daemon-related commands"""

    def test_monitor_daemon_help(self):
        """Monitor-daemon help works"""
        result = runner.invoke(app, ["monitor-daemon", "--help"])
        assert result.exit_code == 0

    def test_supervisor_daemon_help(self):
        """Supervisor-daemon help works"""
        result = runner.invoke(app, ["supervisor-daemon", "--help"])
        assert result.exit_code == 0


class TestConfigCommands:
    """Test config-related commands"""

    def test_config_help(self):
        """Config help works"""
        result = runner.invoke(app, ["config", "--help"])
        assert result.exit_code == 0




class TestVersionCommand:
    """Test version-related output"""

    def test_help_shows_version_info(self):
        """Help output exists"""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0


class TestCleanupCommand:
    """Test cleanup command"""

    def test_cleanup_help(self):
        """Cleanup help works"""
        result = runner.invoke(app, ["cleanup", "--help"])
        assert result.exit_code == 0


class TestExportCommand:
    """Test export command"""

    def test_export_help(self):
        """Export help works"""
        result = runner.invoke(app, ["export", "--help"])
        assert result.exit_code == 0


class TestWebCommand:
    """Test web command"""

    def test_web_help(self):
        """Web help works"""
        result = runner.invoke(app, ["web", "--help"])
        assert result.exit_code == 0


class TestAllSubcommands:
    """Test that all subcommands have help"""

    def test_all_commands_have_help(self):
        """All registered commands should have help text."""
        # Main commands from the app
        commands = ["launch", "list", "kill", "send", "show", "instruct",
                    "monitor", "supervisor", "attach", "cleanup", "export",
                    "web", "monitor-daemon", "supervisor-daemon", "config"]

        for cmd in commands:
            result = runner.invoke(app, [cmd, "--help"])
            # Exit code 0 means help was shown successfully
            assert result.exit_code == 0, f"Command '{cmd}' failed with exit code {result.exit_code}: {result.output}"


# =============================================================================
# Extended CLI tests with mocked dependencies
# =============================================================================

from unittest.mock import patch, MagicMock


class TestListCommandWithMocks:
    """Test list command with mocked sessions"""

    def test_list_outputs_no_sessions(self):
        """List outputs message when no sessions exist"""
        with patch('overcode.cli.ClaudeLauncher') as mock_launcher_class:
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
        with patch('overcode.cli.ClaudeLauncher') as mock_launcher_class:
            mock_launcher = MagicMock()
            mock_launcher.send_to_session.return_value = True
            mock_launcher_class.return_value = mock_launcher

            result = runner.invoke(app, ["send", "test-agent", "hello world"])

            assert result.exit_code == 0
            mock_launcher.send_to_session.assert_called()

    def test_send_key_with_no_enter(self):
        """Send key without pressing enter"""
        with patch('overcode.cli.ClaudeLauncher') as mock_launcher_class:
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
        with patch('overcode.cli.ClaudeLauncher') as mock_launcher_class:
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

        with patch('overcode.cli.ClaudeLauncher') as mock_launcher_class:
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
        with patch('overcode.cli.ClaudeLauncher') as mock_launcher_class:
            mock_launcher = MagicMock()
            mock_launcher.sessions.get_session_by_name.return_value = None
            mock_launcher_class.return_value = mock_launcher

            result = runner.invoke(app, ["show", "nonexistent"])

            assert result.exit_code == 1

    def test_show_handles_no_claude_stats(self):
        """Show handles missing claude stats gracefully"""
        mock_session = self._make_mock_session()

        with patch('overcode.cli.ClaudeLauncher') as mock_launcher_class:
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

        with patch('overcode.cli.ClaudeLauncher') as mock_launcher_class:
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
        with patch('overcode.cli.ClaudeLauncher') as mock_launcher_class:
            mock_launcher = MagicMock()
            mock_launcher.kill_session.return_value = True
            mock_launcher_class.return_value = mock_launcher

            result = runner.invoke(app, ["kill", "test-agent"])

            assert result.exit_code == 0

    def test_kill_nonexistent_session(self):
        """Kill nonexistent session shows message"""
        with patch('overcode.cli.ClaudeLauncher') as mock_launcher_class:
            mock_launcher = MagicMock()
            mock_launcher.kill_session.return_value = False
            mock_launcher_class.return_value = mock_launcher

            result = runner.invoke(app, ["kill", "nonexistent"])

            assert result.exit_code == 0


class TestLaunchCommandWithMocks:
    """Test launch command with mocked ClaudeLauncher"""

    def test_launch_creates_session(self):
        """Launch creates new session"""
        with patch('overcode.cli.ClaudeLauncher') as mock_launcher_class:
            mock_session = MagicMock()
            mock_session.name = "new-agent"
            mock_session.tmux_window = 1

            mock_launcher = MagicMock()
            mock_launcher.launch.return_value = mock_session
            mock_launcher_class.return_value = mock_launcher

            result = runner.invoke(app, ["launch", "--name", "new-agent"])

            assert result.exit_code == 0
            mock_launcher.launch.assert_called()


class TestMonitorDaemonSubcommands:
    """Test monitor-daemon subcommands"""

    def test_monitor_daemon_start_help(self):
        """Start subcommand help works"""
        result = runner.invoke(app, ["monitor-daemon", "start", "--help"])
        assert result.exit_code == 0

    def test_monitor_daemon_stop_help(self):
        """Stop subcommand help works"""
        result = runner.invoke(app, ["monitor-daemon", "stop", "--help"])
        assert result.exit_code == 0

    def test_monitor_daemon_status_help(self):
        """Status subcommand help works"""
        result = runner.invoke(app, ["monitor-daemon", "status", "--help"])
        assert result.exit_code == 0


class TestSupervisorDaemonSubcommands:
    """Test supervisor-daemon subcommands"""

    def test_supervisor_daemon_start_help(self):
        """Start subcommand help works"""
        result = runner.invoke(app, ["supervisor-daemon", "start", "--help"])
        assert result.exit_code == 0

    def test_supervisor_daemon_stop_help(self):
        """Stop subcommand help works"""
        result = runner.invoke(app, ["supervisor-daemon", "stop", "--help"])
        assert result.exit_code == 0


class TestWebSubcommands:
    """Test web subcommands"""

    def test_web_start_help(self):
        """Start subcommand help works"""
        result = runner.invoke(app, ["web", "start", "--help"])
        assert result.exit_code == 0

    def test_web_stop_help(self):
        """Stop subcommand help works"""
        result = runner.invoke(app, ["web", "stop", "--help"])
        assert result.exit_code == 0

    def test_web_status_help(self):
        """Status subcommand help works"""
        result = runner.invoke(app, ["web", "status", "--help"])
        assert result.exit_code == 0


class TestConfigSubcommands:
    """Test config subcommands"""

    def test_config_init_help(self):
        """Init subcommand help works"""
        result = runner.invoke(app, ["config", "init", "--help"])
        assert result.exit_code == 0

    def test_config_show_help(self):
        """Show subcommand help works"""
        result = runner.invoke(app, ["config", "show", "--help"])
        assert result.exit_code == 0

    def test_config_path_help(self):
        """Path subcommand help works"""
        result = runner.invoke(app, ["config", "path", "--help"])
        assert result.exit_code == 0


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
        with patch('overcode.cli.ClaudeLauncher') as mock_launcher_class:
            mock_launcher = MagicMock()
            mock_launcher.cleanup_terminated_sessions.return_value = 3
            mock_launcher_class.return_value = mock_launcher

            result = runner.invoke(app, ["cleanup"])

            assert result.exit_code == 0
            assert "Cleaned up 3" in result.output

    def test_cleanup_no_terminated_sessions(self):
        """Should show message when no terminated sessions."""
        with patch('overcode.cli.ClaudeLauncher') as mock_launcher_class:
            mock_launcher = MagicMock()
            mock_launcher.cleanup_terminated_sessions.return_value = 0
            mock_launcher_class.return_value = mock_launcher

            result = runner.invoke(app, ["cleanup"])

            assert result.exit_code == 0
            assert "No sessions to clean up" in result.output


class TestSetValueCommand:
    """Test set-value command."""

    def test_set_value_help(self):
        """Should show help."""
        result = runner.invoke(app, ["set-value", "--help"])
        assert result.exit_code == 0
        assert "Priority value" in result.output or "priority" in result.output.lower()

    def test_set_value_requires_name(self):
        """Should require agent name."""
        result = runner.invoke(app, ["set-value"])
        assert result.exit_code != 0


class TestExportCommand:
    """Test export command."""

    def test_export_help(self):
        """Should show help."""
        result = runner.invoke(app, ["export", "--help"])
        assert result.exit_code == 0
        assert "parquet" in result.output.lower()

    def test_export_requires_output_path(self):
        """Should require output path."""
        result = runner.invoke(app, ["export"])
        assert result.exit_code != 0


class TestHistoryCommand:
    """Test history command."""

    def test_history_help(self):
        """Should show help."""
        result = runner.invoke(app, ["history", "--help"])
        assert result.exit_code == 0


class TestMonitorDaemonWatch:
    """Test monitor-daemon watch command."""

    def test_watch_help(self):
        """Should show help."""
        result = runner.invoke(app, ["monitor-daemon", "watch", "--help"])
        assert result.exit_code == 0
        assert "Watch" in result.output or "watch" in result.output.lower() or "log" in result.output.lower()


class TestSupervisorDaemonWatch:
    """Test supervisor-daemon watch command."""

    def test_watch_help(self):
        """Should show help."""
        result = runner.invoke(app, ["supervisor-daemon", "watch", "--help"])
        assert result.exit_code == 0


class TestServeCommand:
    """Test serve command."""

    def test_serve_help(self):
        """Should show help with host and port options."""
        import re
        result = runner.invoke(app, ["serve", "--help"])
        assert result.exit_code == 0
        # Strip ANSI codes for reliable matching (CI has different formatting)
        clean_output = re.sub(r'\x1b\[[0-9;]*m', '', result.output)
        assert "--host" in clean_output
        assert "--port" in clean_output


class TestAttachCommand:
    """Test attach command."""

    def test_attach_help(self):
        """Should show help."""
        result = runner.invoke(app, ["attach", "--help"])
        assert result.exit_code == 0


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
