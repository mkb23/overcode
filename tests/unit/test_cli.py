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

    def test_no_args_shows_help(self):
        """No arguments shows help"""
        result = runner.invoke(app, [])
        # Shows help output (Typer may return exit code 2 for no args)
        assert "Usage:" in result.output


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


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
