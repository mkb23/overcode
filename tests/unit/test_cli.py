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


class Fleet:
    """Real agents in a private state dir, their windows on a ``MockTmux``.

    The CLI builds its own ``SessionManager()`` per command, which reads the
    same sessions.json, so a test sets agents up here and reads back what
    the command wrote. ``launcher()`` is a real ``AgentLauncher`` whose tmux
    is the double: commands run their real logic and touch no tmux server.
    """

    def __init__(self):
        from overcode.session_manager import SessionManager
        from tests.tmux_doubles import MockTmux

        self.sm = SessionManager(skip_git_detection=True)
        self.tmux = MockTmux()
        self.tmux.new_session("agents")

    def add(self, name="test-agent", *, window=True, stats=None, **fields):
        """Register an agent (with a live window unless ``window=False``)."""
        s = self.sm.create_session(
            name=name, tmux_session="agents", tmux_window=f"{name}-w",
            command=["claude"], start_directory=None)
        if fields:
            self.sm.update_session(s.id, **fields)
        if stats:
            self.sm.update_stats(s.id, **stats)
        if window:
            self.tmux.new_window("agents", s.tmux_window)
        return self.sm.get_session(s.id)

    def add_with_history(self, name="test-agent", **fields):
        """An agent with a repo, a branch and an hour of recorded stats."""
        stats = dict(green_time_seconds=3600.0, non_green_time_seconds=600.0,
                     estimated_cost_usd=1.50, steers_count=2,
                     state_since="2026-02-06T11:50:00")
        stats.update(fields.pop("stats", {}))
        fields = {"repo_name": "test-repo", "branch": "main", **fields}
        return self.add(name, stats=stats, **fields)

    def publish(self, daemon=None, **by_name):
        """Publish a fresh monitor-daemon state: ``name={SessionDaemonState fields}``.

        ``daemon`` holds MonitorDaemonState fields to set on it.
        """
        from datetime import datetime
        from overcode.monitor_daemon_state import MonitorDaemonState, SessionDaemonState
        from overcode.settings import get_monitor_daemon_state_path

        sessions = [SessionDaemonState(session_id=self.get(name).id, name=name, **fields)
                    for name, fields in by_name.items()]
        fields = dict(pid=1, status="active", last_loop_time=datetime.now().isoformat())
        fields.update(daemon or {})
        MonitorDaemonState(sessions=sessions, **fields).save(get_monitor_daemon_state_path("agents"))

    def get(self, name):
        return self.sm.resolve_session_name(name)

    def launcher(self, tmux_session="agents"):
        from overcode.launcher import AgentLauncher
        from overcode.tmux_manager import TmuxManager

        launcher = AgentLauncher(tmux_session, TmuxManager(tmux_session, tmux=self.tmux), self.sm)
        launcher.sleep = lambda seconds: None
        return launcher


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    """A ``Fleet`` on this test's own state dir; the CLI's launcher uses it."""
    monkeypatch.setenv("OVERCODE_DIR", str(tmp_path / "oc"))
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "oc" / "sessions"))
    f = Fleet()
    # The agent CLI is never started: its window is a MockTmux one.
    monkeypatch.setattr("overcode.launcher.require_tmux", lambda *a, **kw: None)
    monkeypatch.setattr("overcode.launcher.require_agent_cli", lambda *a, **kw: None)
    monkeypatch.setattr("overcode.cli.agent.AgentLauncher",
                        lambda tmux_session="agents", *a, **kw: f.launcher(tmux_session))
    return f


class TestCLICommands:
    """Test CLI commands"""

    def test_main_help(self):
        """Main help shows all commands"""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Manage and supervise coding agents" in result.stdout
        assert "launch" in result.stdout
        assert "list" in result.stdout
        assert "daemon" in result.stdout

    def test_no_args_opens_the_split(self):
        """No arguments opens the monitor, i.e. the `overcode tmux` split (#185, #523)"""
        from unittest.mock import patch
        with patch("overcode.cli.split.open_split") as open_split:
            result = runner.invoke(app, [])
        assert result.exit_code == 0
        open_split.assert_called_once_with("agents")

    def test_monitor_is_an_alias_for_tmux(self):
        """`overcode monitor` opens the split, passing its flags to the pane (#523)"""
        from unittest.mock import patch
        with patch("overcode.cli.split.open_split") as open_split, \
             patch("overcode.tui.run_tui") as run_tui:
            result = runner.invoke(app, ["monitor", "--jobs", "--restart", "--session", "s"])
        assert result.exit_code == 0, result.output
        open_split.assert_called_once_with("s", restart=True, monitor_args="--jobs")
        run_tui.assert_not_called()

    def test_monitor_with_sync_target_runs_the_pane(self):
        """The split's top pane runs `monitor --sync-target`: the TUI itself"""
        from unittest.mock import patch
        with patch("overcode.cli.split.open_split") as open_split, \
             patch("overcode.tui.run_tui") as run_tui:
            result = runner.invoke(app, ["monitor", "--session", "s", "--sync-target", "oc-view-s"])
        assert result.exit_code == 0, result.output
        run_tui.assert_called_once_with("s", "oc-view-s", diagnostics=False, initial_jobs_mode=False)
        open_split.assert_not_called()

    def test_supervisor_is_a_deprecated_alias(self):
        """`overcode supervisor` (removed in 0.6.0) says so and opens the split"""
        from unittest.mock import patch
        with patch("overcode.cli.split.open_split") as open_split:
            result = runner.invoke(app, ["supervisor"])
        assert result.exit_code == 0, result.output
        assert "removed in 0.6.0" in result.output
        open_split.assert_called_once_with("agents", restart=False)


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

from unittest.mock import patch, MagicMock  # noqa: E402


class TestListCommandWithMocks:
    """Test list command with no agents."""

    def test_list_outputs_no_sessions(self, fleet):
        """List outputs message when no sessions exist"""
        result = runner.invoke(app, ["list"])

        assert result.exit_code == 0
        assert "No running agents" in result.output


class TestSendCommandWithMocks:
    """Test send command with mocked sessions"""

    def test_send_text_to_session(self):
        """Send text to existing session"""
        with patch('overcode.cli.agent.AgentLauncher') as mock_launcher_class:
            mock_launcher = MagicMock()
            mock_launcher.send_to_session.return_value = True
            mock_launcher_class.return_value = mock_launcher

            result = runner.invoke(app, ["send", "test-agent", "hello world"])

            assert result.exit_code == 0
            mock_launcher.send_to_session.assert_called()

    def test_send_key_with_no_enter(self):
        """Send key without pressing enter"""
        with patch('overcode.cli.agent.AgentLauncher') as mock_launcher_class:
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


def _detector_seeing(status, activity, pane):
    """Patch show's status detector (it reads the tmux server) to report this."""
    detector = MagicMock()
    detector.detect_status.return_value = (status, activity, pane)
    return patch('overcode.status_detector_factory.create_status_detector', return_value=detector)


class TestShowCommandWithMocks:
    """Show renders a real agent record; only tmux and the transcript are stubbed."""

    @staticmethod
    def _claude_stats():
        from overcode.history_reader import AgentSessionStats
        return AgentSessionStats(
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

    def test_show_session_output_no_stats(self, fleet):
        """Show --no-stats outputs only pane content"""
        fleet.add_with_history()
        with _detector_seeing("running", "Working...", "line 1\nline 2\nline 3"):
            result = runner.invoke(app, ["show", "test-agent", "--no-stats"])

        assert result.exit_code == 0
        assert "line 1" in result.output
        # Stats should NOT be present
        assert "Tokens:" not in result.output

    def test_show_displays_stats(self, fleet):
        """Show displays stats section by default"""
        fleet.add_with_history(start_directory="/tmp/test")
        pane = "Some output\n⏵⏵ bypass permissions on · 3 bashes · esc"
        with _detector_seeing("running", "Working on tests", pane), \
                patch('overcode.history_reader.get_session_stats', return_value=self._claude_stats()), \
                patch('overcode.git_stats.get_git_diff_stats', return_value=(3, 120, 45)):
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

    def test_show_agent_not_found(self, fleet):
        """Show exits with error when agent not found"""
        result = runner.invoke(app, ["show", "nonexistent"])
        assert result.exit_code == 1

    def test_show_handles_no_claude_stats(self, fleet):
        """Show handles missing claude stats gracefully"""
        fleet.add_with_history()
        with _detector_seeing("running", "Working...", "output"):
            result = runner.invoke(app, ["show", "test-agent"])

        assert result.exit_code == 0
        # With no claude stats, Tokens/Work lines are omitted
        assert "Tokens" not in result.output
        assert "=== test-agent ===" in result.output

    def test_show_background_bash_count_with_ansi(self, fleet):
        """Show correctly extracts background bash count from ANSI content"""
        fleet.add_with_history()
        # Simulate ANSI-coded status bar
        pane = "Output\n\x1b[36m⏵⏵ auto-approve · 2 bashes · esc\x1b[0m"
        with _detector_seeing("running", "Working", pane), \
                patch('overcode.history_reader.get_session_stats', return_value=self._claude_stats()):
            result = runner.invoke(app, ["show", "test-agent"])

        assert result.exit_code == 0
        assert "2 background bashes" in result.output


class TestKillCommandWithMocks:
    """Kill over real agents (windows on MockTmux)."""

    def test_kill_existing_session(self, fleet):
        """Kill existing session"""
        agent = fleet.add("test-agent")

        result = runner.invoke(app, ["kill", "test-agent"])

        assert result.exit_code == 0
        assert "Killed session 'test-agent'" in result.output
        assert fleet.get("test-agent") is None
        assert agent.tmux_window not in fleet.tmux.sessions["agents"]

    def test_kill_nonexistent_session(self, fleet):
        """Kill nonexistent session shows message"""
        result = runner.invoke(app, ["kill", "nonexistent"])

        assert result.exit_code == 0
        assert "not found" in result.output


class TestLaunchCommandWithMocks:
    """Launch registers the agent and opens its window (MockTmux)."""

    def test_launch_creates_session(self, fleet):
        """Launch creates new session"""
        result = runner.invoke(app, ["launch", "--name", "new-agent", "--directory", "/tmp"])

        assert result.exit_code == 0, result.output
        agent = fleet.get("new-agent")
        assert agent is not None
        assert agent.tmux_window in fleet.tmux.sessions["agents"]


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

    def test_instruct_agent_not_found(self, fleet):
        """Should error when agent not found."""
        result = runner.invoke(app, ["instruct", "nonexistent", "DO_NOTHING"])

        assert result.exit_code == 1
        assert "not found" in result.output

    def test_instruct_clears_instructions(self, fleet):
        """Should clear instructions with --clear flag."""
        fleet.add("test-agent", standing_instructions="Old orders",
                  standing_instructions_preset="CODING", standing_orders_complete=True)

        result = runner.invoke(app, ["instruct", "test-agent", "--clear"])

        assert result.exit_code == 0
        assert "Cleared" in result.output
        agent = fleet.get("test-agent")
        assert agent.standing_instructions == ""
        assert agent.standing_instructions_preset is None
        assert agent.standing_orders_complete is False


class TestCleanupCommandWithMocks:
    """Cleanup over real agents: a gone window is terminated, then removed."""

    def test_cleanup_removes_terminated_sessions(self, fleet):
        """Should remove terminated sessions."""
        for name in ("gone-1", "gone-2", "gone-3"):
            fleet.add(name, window=False)
        fleet.add("alive")

        result = runner.invoke(app, ["cleanup"])

        assert result.exit_code == 0, result.output
        assert "Cleaned up 3" in result.output
        assert [s.name for s in fleet.sm.list_sessions()] == ["alive"]

    def test_cleanup_no_terminated_sessions(self, fleet):
        """Should show message when no terminated sessions."""
        fleet.add("alive")

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
    """Launch runs a real AgentLauncher over MockTmux (the ``fleet`` fixture);
    what the command stored is read back from sessions.json."""

    @staticmethod
    def _launch(*args, name="agent"):
        return runner.invoke(app, ["launch", "--name", name, "--directory", "/tmp", *args])

    def test_launch_with_prompt(self, fleet):
        """Launch with --prompt puts the prompt on the agent's command line."""
        from overcode.launcher import AgentLauncher

        # The startup-dialog wait polls the real tmux server, not the double
        # (agent_io uses tmux_utils directly), so it would spin for its 30 s.
        with patch.object(AgentLauncher, "_wait_for_prompt", return_value=True):
            result = self._launch("--prompt", "do stuff")
        assert result.exit_code == 0, result.output
        assert "Initial prompt sent" in result.output
        agent = fleet.get("agent")
        typed = [keys for _, window, keys, _ in fleet.tmux.sent_keys if window == agent.tmux_window]
        assert any(f"prompts/{agent.id}.txt" in keys for keys in typed)

    def test_launch_backend_arg_reaches_the_launcher(self, fleet):
        """--backend-arg is the neutral spelling of the old --claude-arg."""
        result = self._launch("--backend-arg", "--verbose")
        assert result.exit_code == 0, result.output
        assert fleet.get("agent").extra_cli_args == ["--verbose"]

    def test_launch_deprecated_claude_arg_still_works(self, fleet):
        """The pre-Phase-6 flag keeps working (hidden from --help)."""
        result = self._launch("--claude-arg", "--verbose")
        assert result.exit_code == 0, result.output
        assert fleet.get("agent").extra_cli_args == ["--verbose"]

    def test_launch_merges_both_arg_spellings(self, fleet):
        result = self._launch("--backend-arg", "--new", "--claude-arg", "--old")
        assert result.exit_code == 0, result.output
        assert fleet.get("agent").extra_cli_args == ["--new", "--old"]

    def test_deprecated_claude_arg_is_hidden_from_help(self):
        result = runner.invoke(app, ["launch", "--help"])
        output = strip_ansi(result.output)
        assert "--backend-arg" in output
        assert "--claude-arg" not in output

    def test_launch_with_parent(self, fleet):
        """Launch with --parent sets parent info."""
        dad = fleet.add("dad")
        result = self._launch("--parent", "dad", name="child")
        assert result.exit_code == 0, result.output
        assert "Parent: dad" in result.output
        assert fleet.get("child").parent_session_id == dad.id

    def test_launch_with_oversight_timeout(self, fleet):
        """Launch with --oversight-timeout stores policy."""
        result = self._launch("--oversight-timeout", "5m", name="child")
        assert result.exit_code == 0, result.output
        assert "Oversight: timeout" in result.output
        child = fleet.get("child")
        assert child.oversight_policy == "timeout"
        assert child.oversight_timeout_seconds == 300.0

    def test_a_refused_launch_prints_why_and_exits_1(self, fleet):
        result = self._launch("--wrapper", "no-such-wrapper", name="child")
        assert result.exit_code == 1
        assert "Cannot launch: wrapper 'no-such-wrapper' not found" in result.output
        assert "launched" not in result.output
        assert fleet.get("child") is None

    def test_launch_invalid_oversight_timeout(self, fleet):
        """Launch with invalid --oversight-timeout errors."""
        result = self._launch("--oversight-timeout", "abc", name="child")
        assert result.exit_code == 1
        assert "Invalid duration" in result.output
        assert fleet.get("child") is None

    def test_launch_on_stuck_fail(self, fleet):
        """Launch with --on-stuck fail."""
        result = self._launch("--on-stuck", "fail", name="child")
        assert result.exit_code == 0, result.output
        assert "Oversight: fail" in result.output
        assert fleet.get("child").oversight_policy == "fail"

    def test_launch_on_stuck_wait(self, fleet):
        """Launch with --on-stuck wait keeps the default (no oversight line)."""
        result = self._launch("--on-stuck", "wait", name="child")
        assert result.exit_code == 0, result.output
        assert "Oversight" not in result.output
        assert fleet.get("child").oversight_policy == "wait"

    def test_launch_on_stuck_timeout(self, fleet):
        """Launch with --on-stuck timeout:1h."""
        result = self._launch("--on-stuck", "timeout:1h", name="child")
        assert result.exit_code == 0, result.output
        child = fleet.get("child")
        assert child.oversight_policy == "timeout"
        assert child.oversight_timeout_seconds == 3600.0

    def test_launch_on_stuck_timeout_invalid(self, fleet):
        """Launch with --on-stuck timeout:bad errors."""
        result = self._launch("--on-stuck", "timeout:bad", name="child")
        assert result.exit_code == 1
        assert "Invalid duration" in result.output

    def test_launch_on_stuck_invalid_value(self, fleet):
        """Launch with --on-stuck bogus errors."""
        result = self._launch("--on-stuck", "bogus", name="child")
        assert result.exit_code == 1
        assert "Invalid --on-stuck" in result.output

    def test_launch_with_follow(self, fleet):
        """Launch with --follow hands over to follow mode (patched: it blocks)."""
        with patch('overcode.follow_mode.follow_agent', return_value=0) as mock_follow:
            result = self._launch("--follow", name="child")
        assert result.exit_code == 0, result.output
        mock_follow.assert_called_once_with("child", "agents")

    def test_launch_returns_none(self):
        """The CLI's no-result branch (launch raises today, but is typed Optional)."""
        with patch('overcode.cli.agent.AgentLauncher') as mock_cls:
            mock_cls.return_value.launch.return_value = None

            result = runner.invoke(app, ["launch", "--name", "agent"])
            assert result.exit_code == 0
            assert "launched" not in result.output


class TestLaunchSister:
    """Test launch --sister flag (#310)."""

    def test_launch_help_shows_sister_flag(self):
        """--sister appears in launch help output."""
        result = runner.invoke(app, ["launch", "--help"])
        assert result.exit_code == 0
        output = strip_ansi(result.stdout)
        assert "--sister" in output

    def test_launch_sister_not_found(self):
        """Error when sister name not in config."""
        with patch('overcode.config.get_sister_by_name', return_value=None):
            with patch('overcode.config.get_sisters_config', return_value=[
                {"name": "desktop", "url": "http://localhost:25337"},
            ]):
                result = runner.invoke(app, ["launch", "--name", "test", "--sister", "nonexistent"])
                assert result.exit_code == 1
                assert "not found" in result.output
                assert "desktop" in result.output

    def test_launch_sister_no_sisters_configured(self):
        """Error when no sisters configured at all."""
        with patch('overcode.config.get_sister_by_name', return_value=None):
            with patch('overcode.config.get_sisters_config', return_value=[]):
                result = runner.invoke(app, ["launch", "--name", "test", "--sister", "foo"])
                assert result.exit_code == 1
                assert "No sisters configured" in result.output

    def test_launch_sister_success(self):
        """Successful remote launch via sister."""
        from overcode.sister_controller import ControlResult
        mock_result = ControlResult(ok=True)

        with patch('overcode.config.get_sister_by_name', return_value={
            "name": "desktop", "url": "http://desktop:15337", "api_key": "secret",
        }):
            with patch('overcode.sister_controller.SisterController.launch_agent', return_value=mock_result) as mock_launch:
                result = runner.invoke(app, [
                    "launch", "--name", "test-remote",
                    "--sister", "desktop",
                    "--directory", "/tmp/work",
                    "--prompt", "hello",
                ])
                assert result.exit_code == 0
                assert "test-remote" in result.output
                assert "desktop" in result.output
                mock_launch.assert_called_once_with(
                    sister_url="http://desktop:15337",
                    api_key="secret",
                    directory="/tmp/work",
                    name="test-remote",
                    prompt="hello",
                    permissions="normal",
                )

    def test_launch_sister_remote_error(self):
        """Error from remote sister is displayed."""
        from overcode.sister_controller import ControlResult
        mock_result = ControlResult(ok=False, error="Connection refused")

        with patch('overcode.config.get_sister_by_name', return_value={
            "name": "desktop", "url": "http://desktop:15337",
        }):
            with patch('overcode.sister_controller.SisterController.launch_agent', return_value=mock_result):
                result = runner.invoke(app, [
                    "launch", "--name", "test-remote", "--sister", "desktop",
                ])
                assert result.exit_code == 1
                assert "Connection refused" in result.output

    def test_launch_sister_with_bypass_permissions(self):
        """--bypass-permissions maps to permissions='bypass'."""
        from overcode.sister_controller import ControlResult
        mock_result = ControlResult(ok=True)

        with patch('overcode.config.get_sister_by_name', return_value={
            "name": "desktop", "url": "http://desktop:15337",
        }):
            with patch('overcode.sister_controller.SisterController.launch_agent', return_value=mock_result) as mock_launch:
                result = runner.invoke(app, [
                    "launch", "--name", "test", "--sister", "desktop",
                    "--bypass-permissions",
                ])
                assert result.exit_code == 0
                assert mock_launch.call_args.kwargs["permissions"] == "bypass"

    def test_launch_sister_no_directory_warns(self):
        """Warning when no --directory given for remote launch."""
        from overcode.sister_controller import ControlResult
        mock_result = ControlResult(ok=True)

        with patch('overcode.config.get_sister_by_name', return_value={
            "name": "desktop", "url": "http://desktop:15337",
        }):
            with patch('overcode.sister_controller.SisterController.launch_agent', return_value=mock_result) as mock_launch:
                result = runner.invoke(app, [
                    "launch", "--name", "test", "--sister", "desktop",
                ])
                assert result.exit_code == 0
                assert "Warning" in result.output
                assert mock_launch.call_args.kwargs["directory"] == "."


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
        with patch('overcode.follow_mode.follow_agent', return_value=1):
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

    def test_report_success(self, fleet, monkeypatch):
        """Report writes the file in the agent's own directory and updates the session."""
        import json
        from overcode.paths import agent_file
        child = fleet.add("child-agent")
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "child-agent")
        monkeypatch.setenv("OVERCODE_SESSION_ID", child.id)
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")

        result = runner.invoke(app, ["report", "--status", "success", "--reason", "All tests passed"])
        assert result.exit_code == 0
        assert "Report filed: success" in result.output
        assert "All tests passed" in result.output
        child = fleet.get("child-agent")
        assert (child.report_status, child.report_reason) == ("success", "All tests passed")
        report = json.loads(agent_file("agents", child.id, "report").read_text())
        assert report["status"] == "success"

    def test_report_failure_no_reason(self, fleet, monkeypatch):
        """Report failure without reason, from an agent the registry no longer has."""
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "child-agent")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")

        result = runner.invoke(app, ["report", "--status", "failure"])
        assert result.exit_code == 0
        assert "Report filed: failure" in result.output


class TestCleanupExtended:
    """Test cleanup with --done flag."""

    def test_cleanup_with_done_flag(self, fleet):
        """Cleanup with --done also archives done agents."""
        parent = fleet.add("parent")
        done = fleet.add("finished", status="done", parent_session_id=parent.id)
        fleet.add("gone", window=False)  # its window is gone: terminated

        result = runner.invoke(app, ["cleanup", "--done"])
        assert result.exit_code == 0, result.output
        assert "1 terminated" in result.output
        assert "1 done" in result.output
        assert [s.name for s in fleet.sm.list_sessions()] == ["parent"]
        assert done.tmux_window not in fleet.tmux.sessions["agents"]


class TestSetValueExtended:
    """Test set-value command execution."""

    def test_set_value_success(self, fleet):
        """Set value successfully updates agent."""
        fleet.add("my-agent")
        result = runner.invoke(app, ["set-value", "my-agent", "2000"])
        assert result.exit_code == 0
        assert "Set my-agent value to 2000" in result.output
        assert fleet.get("my-agent").agent_value == 2000

    def test_set_value_agent_not_found(self, fleet):
        """Set value errors when agent not found."""
        result = runner.invoke(app, ["set-value", "missing", "2000"])
        assert result.exit_code == 1
        assert "not found" in result.output


class TestSetBudgetCommand:
    """Test set-budget command (deprecated)."""

    def test_set_budget_success(self, fleet):
        """Set budget successfully."""
        fleet.add("my-agent")
        result = runner.invoke(app, ["set-budget", "my-agent", "5.00"])
        assert result.exit_code == 0
        assert "$5.00" in result.output
        assert fleet.get("my-agent").cost_budget_usd == 5.0

    def test_set_budget_clear(self, fleet):
        """Set budget to 0 clears it."""
        fleet.add("my-agent", cost_budget_usd=5.0)
        result = runner.invoke(app, ["set-budget", "my-agent", "0"])
        assert result.exit_code == 0
        assert "Cleared budget" in result.output
        assert fleet.get("my-agent").cost_budget_usd == 0.0

    def test_set_budget_agent_not_found(self, fleet):
        """Set budget errors when agent not found."""
        result = runner.invoke(app, ["set-budget", "missing", "5.00"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_set_budget_negative(self, fleet):
        """Set budget rejects negative values."""
        fleet.add("my-agent", cost_budget_usd=5.0)
        result = runner.invoke(app, ["set-budget", "my-agent", "-1"])
        # Typer may reject negative float at parsing level (exit code 2)
        assert result.exit_code != 0
        assert fleet.get("my-agent").cost_budget_usd == 5.0


class TestBudgetSetCommand:
    """Test budget set subcommand."""

    def test_budget_set_success(self, fleet):
        """Budget set works."""
        fleet.add("my-agent")
        result = runner.invoke(app, ["budget", "set", "my-agent", "10.0"])
        assert result.exit_code == 0
        assert "$10.00" in result.output
        assert fleet.get("my-agent").cost_budget_usd == 10.0

    def test_budget_set_clear(self, fleet):
        """Budget set to 0 clears."""
        fleet.add("my-agent", cost_budget_usd=10.0)
        result = runner.invoke(app, ["budget", "set", "my-agent", "0"])
        assert result.exit_code == 0
        assert "Cleared budget" in result.output
        assert fleet.get("my-agent").cost_budget_usd == 0.0

    def test_budget_set_not_found(self, fleet):
        """Budget set errors when agent not found."""
        result = runner.invoke(app, ["budget", "set", "missing", "5"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_budget_set_negative(self, fleet):
        """Budget set rejects negative."""
        fleet.add("my-agent")
        result = runner.invoke(app, ["budget", "set", "my-agent", "-5"])
        # Typer may reject negative float at parsing level (exit code 2)
        assert result.exit_code != 0
        assert fleet.get("my-agent").cost_budget_usd == 0.0


class TestBudgetTransferCommand:
    """Test budget transfer subcommand."""

    @staticmethod
    def _family(fleet, parent_budget=5.0, related=True):
        parent = fleet.add("parent", cost_budget_usd=parent_budget)
        fleet.add("child", parent_session_id=parent.id if related else None)

    def test_budget_transfer_success(self, fleet):
        """Budget transfer works."""
        self._family(fleet)
        result = runner.invoke(app, ["budget", "transfer", "parent", "child", "2.0"])
        assert result.exit_code == 0
        assert "Transferred $2.00" in result.output
        assert fleet.get("parent").cost_budget_usd == 3.0
        assert fleet.get("child").cost_budget_usd == 2.0

    def test_budget_transfer_source_not_found(self, fleet):
        """Budget transfer errors when source not found."""
        result = runner.invoke(app, ["budget", "transfer", "missing", "child", "2.0"])
        assert result.exit_code == 1
        assert "Source agent" in result.output

    def test_budget_transfer_target_not_found(self, fleet):
        """Budget transfer errors when target not found."""
        fleet.add("parent")
        result = runner.invoke(app, ["budget", "transfer", "parent", "missing", "2.0"])
        assert result.exit_code == 1
        assert "Target agent" in result.output

    def test_budget_transfer_non_positive(self, fleet):
        """Budget transfer rejects zero/negative amount."""
        self._family(fleet)
        result = runner.invoke(app, ["budget", "transfer", "parent", "child", "0"])
        assert result.exit_code == 1
        assert "must be positive" in result.output

    def test_budget_transfer_not_ancestor(self, fleet):
        """Budget transfer errors when source is not ancestor."""
        self._family(fleet, related=False)
        result = runner.invoke(app, ["budget", "transfer", "parent", "child", "2.0"])
        assert result.exit_code == 1
        assert "not an ancestor" in result.output

    def test_budget_transfer_insufficient(self, fleet):
        """Budget transfer fails on insufficient budget."""
        self._family(fleet)
        result = runner.invoke(app, ["budget", "transfer", "parent", "child", "99.0"])
        assert result.exit_code == 1
        assert "insufficient budget" in result.output
        assert fleet.get("parent").cost_budget_usd == 5.0


class TestBudgetShowCommand:
    """Test budget show subcommand."""

    def test_budget_show_all_agents(self, fleet):
        """Budget show with no name shows all agents."""
        fleet.add("a1", cost_budget_usd=10.0, stats={"estimated_cost_usd": 2.5})
        result = runner.invoke(app, ["budget", "show"])
        assert result.exit_code == 0
        assert "a1" in result.output
        assert "$7.50 remaining (25% used)" in result.output

    def test_budget_show_no_agents(self, fleet):
        """Budget show with no agents."""
        result = runner.invoke(app, ["budget", "show"])
        assert result.exit_code == 0
        assert "No running agents" in result.output

    def test_budget_show_specific_agent(self, fleet):
        """Budget show for specific agent."""
        fleet.add("my-agent", cost_budget_usd=5.0)
        fleet.add("other-agent", cost_budget_usd=5.0)
        result = runner.invoke(app, ["budget", "show", "my-agent"])
        assert result.exit_code == 0
        assert "my-agent" in result.output
        assert "other-agent" not in result.output

    def test_budget_show_agent_not_found(self, fleet):
        """Budget show errors when agent not found."""
        result = runner.invoke(app, ["budget", "show", "missing"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_budget_show_unlimited(self, fleet):
        """Budget show for agent with no budget (unlimited)."""
        fleet.add("free")
        result = runner.invoke(app, ["budget", "show", "free"])
        assert result.exit_code == 0
        assert "unlimited" in result.output
        assert "no limit" in result.output

    def test_budget_show_with_children(self, fleet):
        """Budget show includes subtree spend for parent."""
        parent = fleet.add("parent", cost_budget_usd=10.0, stats={"estimated_cost_usd": 1.0})
        fleet.add("child", parent_session_id=parent.id, stats={"estimated_cost_usd": 0.5})
        result = runner.invoke(app, ["budget", "show", "parent"])
        assert result.exit_code == 0
        assert "(subtree: $1.5000)" in result.output


class TestAnnotateCommand:
    """Test annotate command."""

    def test_annotate_set(self, fleet):
        """Annotate sets annotation text."""
        fleet.add("my-agent")
        result = runner.invoke(app, ["annotate", "my-agent", "Working", "on", "auth"])
        assert result.exit_code == 0
        assert "Annotation set" in result.output
        assert fleet.get("my-agent").human_annotation == "Working on auth"

    def test_annotate_clear(self, fleet):
        """Annotate with no text clears annotation."""
        fleet.add("my-agent", human_annotation="old note")
        result = runner.invoke(app, ["annotate", "my-agent"])
        assert result.exit_code == 0
        assert "Annotation cleared" in result.output
        assert fleet.get("my-agent").human_annotation == ""

    def test_annotate_not_found(self, fleet):
        """Annotate errors when agent not found."""
        result = runner.invoke(app, ["annotate", "missing"])
        assert result.exit_code == 1
        assert "not found" in result.output


class TestSendExtended:
    """Extended tests for send command."""

    def test_send_special_key(self):
        """Send special key (enter/escape) shows key name."""
        with patch('overcode.cli.agent.AgentLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_launcher.send_to_session.return_value = True
            mock_cls.return_value = mock_launcher

            result = runner.invoke(app, ["send", "my-agent", "enter"])
            assert result.exit_code == 0
            assert "ENTER" in result.output

    def test_send_no_enter_mode(self):
        """Send with --no-enter shows (no enter) in output."""
        with patch('overcode.cli.agent.AgentLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_launcher.send_to_session.return_value = True
            mock_cls.return_value = mock_launcher

            result = runner.invoke(app, ["send", "my-agent", "y", "--no-enter"])
            assert result.exit_code == 0
            assert "no enter" in result.output

    def test_send_long_text_truncated(self):
        """Send long text truncates display to 50 chars."""
        with patch('overcode.cli.agent.AgentLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_launcher.send_to_session.return_value = True
            mock_cls.return_value = mock_launcher

            long_text = "a" * 100
            result = runner.invoke(app, ["send", "my-agent", long_text])
            assert result.exit_code == 0
            assert "..." in result.output

    def test_send_fails(self):
        """Send failure exits with code 1."""
        with patch('overcode.cli.agent.AgentLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_launcher.send_to_session.return_value = False
            mock_cls.return_value = mock_launcher

            result = runner.invoke(app, ["send", "my-agent", "hello"])
            assert result.exit_code == 1
            assert "Failed to send" in result.output


class TestShowExtended:
    """Extended show command tests."""

    def test_show_stats_only(self, fleet):
        """Show with --stats-only omits pane output."""
        fleet.add_with_history()
        with _detector_seeing("running", "Working", "pane output here"):
            result = runner.invoke(app, ["show", "test-agent", "--stats-only"])
        assert result.exit_code == 0
        assert "=== test-agent ===" in result.output
        # Should not have the pane output section
        assert "last 50 lines" not in result.output
        assert "pane output here" not in result.output

    def test_show_terminated_session(self, fleet):
        """Show handles terminated session."""
        from overcode.launcher import AgentLauncher
        fleet.add_with_history(status="terminated", window=False)
        with patch.object(AgentLauncher, "get_session_output", return_value=None):
            result = runner.invoke(app, ["show", "test-agent"])
        assert result.exit_code == 0
        assert "terminated" in result.output
        assert "No pane output available" in result.output

    def test_show_with_daemon_state(self, fleet):
        """Show uses daemon state when available."""
        fleet.add_with_history()
        fleet.publish(**{"test-agent": dict(
            current_status="running", current_activity="Coding tests",
            activity_summary="Writing unit tests", activity_summary_context="Test context")})

        with patch('overcode.status_detector_factory.StatusDetectorDispatcher') as mock_dispatcher:
            mock_dispatcher.return_value.get_pane_content.return_value = "pane content"
            result = runner.invoke(app, ["show", "test-agent"])
        assert result.exit_code == 0
        assert "Coding tests" in result.output
        assert "Writing unit tests" in result.output
        assert "Test context" in result.output
        assert "pane content" in result.output

    def test_show_asleep_session(self, fleet):
        """Show handles asleep session."""
        fleet.add_with_history(is_asleep=True)
        with _detector_seeing("running", "Working", "output"):
            result = runner.invoke(app, ["show", "test-agent"])
        assert result.exit_code == 0
        assert "asleep" in result.output

    def test_show_pane_fallback_for_terminated(self, fleet):
        """Show uses launcher.get_session_output for terminated sessions."""
        from overcode.launcher import AgentLauncher
        fleet.add_with_history(status="terminated", window=False)
        # get_session_output captures from the tmux server directly, not the double
        with patch.object(AgentLauncher, "get_session_output",
                          return_value="fallback output here") as output:
            result = runner.invoke(app, ["show", "test-agent"])
        assert result.exit_code == 0
        assert "fallback output here" in result.output
        output.assert_called_once_with("test-agent", lines=50)


@pytest.fixture
def claude_settings(tmp_path, monkeypatch):
    """Claude's user and project settings files, in this test's own HOME and cwd.

    Returns ``write(level, data_or_text)`` and the two paths.
    """
    import json
    from types import SimpleNamespace

    home, project = tmp_path / "home", tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)
    paths = {"user": home / ".claude" / "settings.json",
             "project": project / ".claude" / "settings.json"}

    def write(level, data):
        paths[level].parent.mkdir(parents=True, exist_ok=True)
        paths[level].write_text(data if isinstance(data, str) else json.dumps(data))

    def read(level):
        return json.loads(paths[level].read_text())

    return SimpleNamespace(write=write, read=read, home=home, **paths)


def _hooks(*events, command="overcode hook-handler"):
    return {"hooks": {e: [{"hooks": [{"type": "command", "command": command}]}] for e in events}}


class TestHooksInstallCommand:
    """Test hooks install command (deprecated)."""

    def test_hooks_install_shows_deprecation(self):
        """Hooks install shows deprecation notice and exits."""
        result = runner.invoke(app, ["hooks", "install"])
        assert result.exit_code == 0
        assert "deprecated" in result.output.lower()


class TestHooksUninstallCommand:
    """Test hooks uninstall command, on a real settings file."""

    def test_hooks_uninstall_success(self, claude_settings):
        """Hooks uninstall removes overcode's hooks and keeps the user's own."""
        settings = _hooks("Stop", "PreToolUse")
        settings["hooks"]["Notification"] = [{"hooks": [{"type": "command", "command": "my-notifier"}]}]
        claude_settings.write("user", settings)

        result = runner.invoke(app, ["hooks", "uninstall"])
        assert result.exit_code == 0
        assert "Removed 2 hook" in result.output
        assert claude_settings.read("user") == {"hooks": {"Notification": [
            {"hooks": [{"type": "command", "command": "my-notifier"}]}]}}

    def test_hooks_uninstall_none_found(self, claude_settings):
        """Hooks uninstall when none found."""
        claude_settings.write("user", {"model": "opus"})
        result = runner.invoke(app, ["hooks", "uninstall"])
        assert result.exit_code == 0
        assert "No overcode hooks found" in result.output

    def test_hooks_uninstall_invalid_json(self, claude_settings):
        """Hooks uninstall handles invalid JSON."""
        claude_settings.write("user", "{broken")
        result = runner.invoke(app, ["hooks", "uninstall"])
        assert result.exit_code == 1
        assert claude_settings.user.read_text() == "{broken"


class TestHooksStatusCommand:
    """Test hooks status command."""

    def test_hooks_status(self, claude_settings):
        """Hooks status shows installed/not installed."""
        claude_settings.write("user", _hooks("Stop"))
        result = runner.invoke(app, ["hooks", "status"])
        out = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "User-level" in out
        assert "Project-level" in out and "(no settings file)" in out

    def test_hooks_status_invalid_json(self, claude_settings):
        """Hooks status handles invalid JSON."""
        claude_settings.write("user", "{bad json")
        result = runner.invoke(app, ["hooks", "status"])
        assert result.exit_code == 0
        assert "invalid JSON" in result.output


class TestSkillsInstallCommand:
    """Test skills install command, into this test's own HOME."""

    @pytest.fixture(autouse=True)
    def home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))

    def test_skills_install_new(self, tmp_path):
        """Skills install creates new skill files."""
        base = tmp_path / ".claude" / "skills"

        with patch('overcode.bundled_skills.OVERCODE_SKILLS', {
            "test-skill": {"content": "# Test Skill"},
        }), patch('overcode.bundled_skills.DEPRECATED_SKILL_NAMES', []):
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
        }), patch('overcode.bundled_skills.DEPRECATED_SKILL_NAMES', []):
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
        }), patch('overcode.bundled_skills.DEPRECATED_SKILL_NAMES', []):
            result = runner.invoke(app, ["skills", "install"])
        assert result.exit_code == 0
        assert "1 updated" in result.output
        assert (base / "SKILL.md").read_text() == "# New content"

    def test_skills_install_removes_deprecated(self, tmp_path):
        """Skills install removes deprecated skills."""
        deprecated_dir = tmp_path / ".claude" / "skills" / "old-skill"
        deprecated_dir.mkdir(parents=True)
        (deprecated_dir / "SKILL.md").write_text("# Old")

        with patch('overcode.bundled_skills.OVERCODE_SKILLS', {}), \
                patch('overcode.bundled_skills.DEPRECATED_SKILL_NAMES', ["old-skill"]):
            result = runner.invoke(app, ["skills", "install"])
        assert result.exit_code == 0
        assert "Removed deprecated" in result.output
        assert not deprecated_dir.exists()


class TestSkillsUninstallCommand:
    """Test skills uninstall command, in this test's own HOME."""

    @pytest.fixture(autouse=True)
    def home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))

    def test_skills_uninstall_matching(self, tmp_path):
        """Skills uninstall removes matching skills."""
        base = tmp_path / ".claude" / "skills" / "test-skill"
        base.mkdir(parents=True)
        (base / "SKILL.md").write_text("# Test Skill")

        with patch('overcode.bundled_skills.OVERCODE_SKILLS', {
            "test-skill": {"content": "# Test Skill"},
        }):
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
            result = runner.invoke(app, ["skills", "uninstall"])
        assert result.exit_code == 0
        assert "modified" in result.output
        assert base.exists()

    def test_skills_uninstall_none_found(self):
        """Skills uninstall when no skills found."""
        with patch('overcode.bundled_skills.OVERCODE_SKILLS', {
            "test-skill": {"content": "# Test"},
        }):
            result = runner.invoke(app, ["skills", "uninstall"])
        assert result.exit_code == 0
        assert "No overcode skills found" in result.output


class TestSkillsStatusCommand:
    """Test skills status command."""

    def test_skills_status(self, tmp_path, monkeypatch):
        """Skills status shows installed/not installed."""
        monkeypatch.setenv("HOME", str(tmp_path))
        base = tmp_path / ".claude" / "skills" / "test-skill"
        base.mkdir(parents=True)
        (base / "SKILL.md").write_text("# Test Skill")

        with patch('overcode.bundled_skills.OVERCODE_SKILLS', {
            "test-skill": {"content": "# Test Skill"},
        }):
            result = runner.invoke(app, ["skills", "status"])
        assert result.exit_code == 0
        assert "installed" in result.output


class TestPermsInstallCommand:
    """Test perms install command (deprecated)."""

    def test_perms_install_shows_deprecation(self):
        """Perms install shows deprecation notice and exits."""
        result = runner.invoke(app, ["perms", "install"])
        assert result.exit_code == 0
        assert "deprecated" in result.output.lower()


class TestPermsUninstallCommand:
    """Test perms uninstall command, on a real settings file."""

    def test_perms_uninstall_success(self, claude_settings):
        """Perms uninstall removes overcode's permissions and keeps the user's own."""
        claude_settings.write("user", {"permissions": {"allow": [
            "Bash(overcode show *)", "Bash(git status)", "Bash(overcode list *)"]}})

        result = runner.invoke(app, ["perms", "uninstall"])
        assert result.exit_code == 0
        assert "Removed 2 permission(s)" in result.output
        assert claude_settings.read("user") == {"permissions": {"allow": ["Bash(git status)"]}}

    def test_perms_uninstall_none(self, claude_settings):
        """Perms uninstall when none found."""
        claude_settings.write("user", {"permissions": {"allow": ["Bash(git status)"]}})
        result = runner.invoke(app, ["perms", "uninstall"])
        assert result.exit_code == 0
        assert "No overcode permissions found" in result.output


class TestPermsStatusCommand:
    """Test perms status command."""

    def test_perms_status(self, claude_settings):
        """Perms status shows installed permissions."""
        claude_settings.write("user", {"permissions": {"allow": ["Bash(overcode show *)"]}})
        result = runner.invoke(app, ["perms", "status"])
        out = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "User-level" in out
        assert "Bash(overcode show *)  ✓" in out
        assert "(no settings file)" in out  # the project has none

    def test_perms_status_invalid_json(self, claude_settings):
        """Perms status handles invalid JSON."""
        claude_settings.write("user", "{bad")
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

    def test_instruct_set_preset(self, fleet):
        """Instruct sets a preset."""
        from overcode.standing_instructions import DEFAULT_PRESETS
        fleet.add("my-agent")

        result = runner.invoke(app, ["instruct", "my-agent", "DO_NOTHING"])
        assert result.exit_code == 0
        assert "DO_NOTHING" in result.output
        agent = fleet.get("my-agent")
        assert agent.standing_instructions_preset == "DO_NOTHING"
        assert agent.standing_instructions == DEFAULT_PRESETS["DO_NOTHING"].instructions

    def test_instruct_set_custom(self, fleet):
        """Instruct sets custom instructions."""
        fleet.add("my-agent")

        result = runner.invoke(app, ["instruct", "my-agent", "Focus", "on", "tests"])
        assert result.exit_code == 0
        assert "standing instructions" in result.output
        agent = fleet.get("my-agent")
        assert agent.standing_instructions == "Focus on tests"
        assert agent.standing_instructions_preset is None

    def test_instruct_show_current_preset(self, fleet):
        """Instruct shows current preset instructions."""
        fleet.add("my-agent", standing_instructions="Do nothing",
                  standing_instructions_preset="DO_NOTHING")

        result = runner.invoke(app, ["instruct", "my-agent"])
        assert result.exit_code == 0
        assert "DO_NOTHING" in result.output

    def test_instruct_show_current_custom(self, fleet):
        """Instruct shows current custom instructions."""
        fleet.add("my-agent", standing_instructions="Custom stuff")

        result = runner.invoke(app, ["instruct", "my-agent"])
        assert result.exit_code == 0
        assert "Custom stuff" in result.output

    def test_instruct_show_no_instructions(self, fleet):
        """Instruct shows message when no instructions set."""
        fleet.add("my-agent")

        result = runner.invoke(app, ["instruct", "my-agent"])
        assert result.exit_code == 0
        assert "No standing instructions" in result.output


class TestHeartbeatCommand:
    """Test heartbeat command, against a real agent record."""

    @staticmethod
    def _heartbeat(*args):
        return runner.invoke(app, ["heartbeat", "test-agent", *args])

    def test_heartbeat_show_disabled(self, fleet):
        """Show heartbeat when disabled."""
        fleet.add("test-agent")
        result = self._heartbeat("--show")
        assert result.exit_code == 0
        assert "disabled" in result.output

    def test_heartbeat_show_enabled(self, fleet):
        """Show heartbeat when enabled."""
        fleet.add("test-agent", heartbeat_enabled=True, heartbeat_instruction="Check status",
                  last_heartbeat_time="2026-02-06T12:00:00")
        result = self._heartbeat("--show")
        assert result.exit_code == 0
        assert "enabled" in result.output
        assert "Check status" in result.output
        assert "2026-02-06T12:00:00" in result.output

    def test_heartbeat_show_paused(self, fleet):
        """Show heartbeat when paused."""
        fleet.add("test-agent", heartbeat_enabled=True, heartbeat_paused=True,
                  heartbeat_instruction="Check")
        result = self._heartbeat("--show")
        assert result.exit_code == 0
        assert "paused" in result.output

    def test_heartbeat_default_shows_config(self, fleet):
        """Heartbeat with no flags shows config."""
        fleet.add("test-agent")
        result = self._heartbeat()
        assert result.exit_code == 0
        assert "disabled" in result.output

    def test_heartbeat_enable(self, fleet):
        """Enable heartbeat."""
        fleet.add("test-agent")
        result = self._heartbeat("--enable", "--frequency", "5m", "--instruction", "Status check")
        assert result.exit_code == 0
        assert "enabled" in result.output
        assert "Status check" in result.output
        agent = fleet.get("test-agent")
        assert agent.heartbeat_enabled is True
        assert agent.heartbeat_instruction == "Status check"
        assert agent.heartbeat_frequency_seconds == 300

    def test_heartbeat_enable_default_frequency(self, fleet):
        """Enable heartbeat uses default 5min frequency."""
        fleet.add("test-agent", heartbeat_frequency_seconds=900)
        result = self._heartbeat("--enable", "--instruction", "Check")
        assert result.exit_code == 0
        assert fleet.get("test-agent").heartbeat_frequency_seconds == 300

    def test_heartbeat_enable_requires_instruction(self, fleet):
        """Enable heartbeat requires --instruction."""
        fleet.add("test-agent")
        result = self._heartbeat("--enable")
        assert result.exit_code == 1
        assert "instruction required" in result.output
        assert fleet.get("test-agent").heartbeat_enabled is False

    def test_heartbeat_disable(self, fleet):
        """Disable heartbeat."""
        fleet.add("test-agent", heartbeat_enabled=True, heartbeat_paused=True,
                  heartbeat_instruction="Check")
        result = self._heartbeat("--disable")
        assert result.exit_code == 0
        assert "disabled" in result.output
        agent = fleet.get("test-agent")
        assert (agent.heartbeat_enabled, agent.heartbeat_paused, agent.heartbeat_instruction) == (
            False, False, "")

    def test_heartbeat_pause(self, fleet):
        """Pause heartbeat."""
        fleet.add("test-agent", heartbeat_enabled=True)
        result = self._heartbeat("--pause")
        assert result.exit_code == 0
        assert "paused" in result.output
        assert fleet.get("test-agent").heartbeat_paused is True

    def test_heartbeat_pause_not_enabled(self, fleet):
        """Pause heartbeat when not enabled."""
        fleet.add("test-agent")
        result = self._heartbeat("--pause")
        assert result.exit_code == 0
        assert "not enabled" in result.output
        assert fleet.get("test-agent").heartbeat_paused is False

    def test_heartbeat_resume(self, fleet):
        """Resume heartbeat."""
        fleet.add("test-agent", heartbeat_enabled=True, heartbeat_paused=True)
        result = self._heartbeat("--resume")
        assert result.exit_code == 0
        assert "resumed" in result.output
        assert fleet.get("test-agent").heartbeat_paused is False

    def test_heartbeat_resume_not_enabled(self, fleet):
        """Resume heartbeat when not enabled."""
        fleet.add("test-agent")
        result = self._heartbeat("--resume")
        assert result.exit_code == 0
        assert "not enabled" in result.output

    def test_heartbeat_update_frequency_only(self, fleet):
        """Update just frequency without full enable."""
        fleet.add("test-agent", heartbeat_enabled=True, heartbeat_instruction="Check")
        result = self._heartbeat("--frequency", "10m")
        assert result.exit_code == 0
        assert "updated" in result.output
        agent = fleet.get("test-agent")
        assert agent.heartbeat_frequency_seconds == 600
        assert agent.heartbeat_instruction == "Check"

    def test_heartbeat_update_instruction_only(self, fleet):
        """Update just instruction without full enable."""
        fleet.add("test-agent", heartbeat_enabled=True, heartbeat_frequency_seconds=600)
        result = self._heartbeat("--instruction", "New instruction")
        assert result.exit_code == 0
        assert "updated" in result.output
        agent = fleet.get("test-agent")
        assert agent.heartbeat_instruction == "New instruction"
        assert agent.heartbeat_frequency_seconds == 600

    def test_heartbeat_not_found(self, fleet):
        """Heartbeat errors when agent not found."""
        result = runner.invoke(app, ["heartbeat", "missing"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_heartbeat_invalid_frequency(self, fleet):
        """Heartbeat rejects invalid frequency."""
        fleet.add("test-agent")
        result = self._heartbeat("--frequency", "abc")
        assert result.exit_code == 1
        assert "Invalid frequency" in result.output

    def test_heartbeat_frequency_too_low(self, fleet):
        """Heartbeat rejects frequency below 30s."""
        fleet.add("test-agent")
        result = self._heartbeat("--frequency", "10s")
        assert result.exit_code == 1
        assert "Minimum" in result.output

    @pytest.mark.parametrize("frequency, seconds", [("60s", 60), ("1h", 3600), ("120", 120)])
    def test_heartbeat_frequency_formats(self, fleet, frequency, seconds):
        """Heartbeat parses a seconds or hours suffix, or a bare number of seconds."""
        fleet.add("test-agent", heartbeat_enabled=True)
        result = self._heartbeat("--frequency", frequency)
        assert result.exit_code == 0
        assert fleet.get("test-agent").heartbeat_frequency_seconds == seconds


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

    def test_daemon_stop_not_running(self, fleet):
        """Stop when not running shows message."""
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

    def test_daemon_status_stopped(self, fleet):
        """Status when stopped, with the last state it published."""
        fleet.publish(daemon={"last_loop_time": "2026-02-06T12:00:00"})
        result = runner.invoke(app, ["monitor-daemon", "status"])
        assert result.exit_code == 0
        assert "stopped" in result.output
        assert "Last active" in result.output

    @staticmethod
    def _running(pid):
        """The daemon process is alive (a PID check against a real process)."""
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch('overcode.monitor_daemon.is_monitor_daemon_running', return_value=True))
        stack.enter_context(patch('overcode.monitor_daemon.get_monitor_daemon_pid', return_value=pid))
        return stack

    def test_daemon_status_running(self, fleet):
        """Status when running."""
        fleet.add("one")
        fleet.publish(daemon={"loop_count": 42, "current_interval": 10}, one={})
        with self._running(5678):
            result = runner.invoke(app, ["monitor-daemon", "status"])
        assert result.exit_code == 0
        assert "running (PID 5678)" in result.output
        assert "Loop count: 42" in result.output
        assert "Sessions: 1" in result.output

    def test_daemon_status_with_presence(self, fleet):
        """Status shows presence info when available."""
        fleet.publish(daemon={"presence_available": True, "presence_state": 3,
                              "presence_idle_seconds": 12.0})
        with self._running(1234):
            result = runner.invoke(app, ["monitor-daemon", "status"])
        assert result.exit_code == 0
        assert "Presence: state=3, idle=12s" in result.output


class TestMonitorDaemonWatchCommand:
    """Test monitor-daemon watch command."""

    def test_watch_no_log(self, fleet):
        """Watch errors when log file doesn't exist."""
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

    def test_stop_not_running(self, fleet):
        """Stop when not running."""
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

    def test_status_stopped(self, fleet):
        """Status when stopped."""
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

    def test_watch_no_log(self, fleet):
        """Watch errors when log file doesn't exist."""
        result = runner.invoke(app, ["supervisor-daemon", "watch"])
        assert result.exit_code == 1
        assert "Log file not found" in result.output


@pytest.fixture
def config_yaml(tmp_path, monkeypatch):
    """This test's own config.yaml (under a private OVERCODE_DIR).

    ``write(dict)`` saves it, ``read()`` loads it back, ``path`` is the file.
    """
    import yaml
    from types import SimpleNamespace

    monkeypatch.setenv("OVERCODE_DIR", str(tmp_path / "oc"))
    path = tmp_path / "oc" / "config.yaml"

    class _Indented(yaml.SafeDumper):
        """Lists indented under their key, as the config template writes them."""

        def increase_indent(self, flow=False, indentless=False):
            return super().increase_indent(flow, False)

    def write(data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else yaml.dump(data, Dumper=_Indented))

    def read():
        return yaml.safe_load(path.read_text()) or {}

    return SimpleNamespace(write=write, read=read, path=path)


class TestSisterCommands:
    """Sister subcommands, over a real config.yaml."""

    def test_sister_list_none(self, config_yaml):
        """Sister list when none configured."""
        result = runner.invoke(app, ["sister", "list"])
        assert result.exit_code == 0
        assert "No sister instances" in result.output

    def test_sister_list_with_sisters(self, config_yaml):
        """Sister list shows configured sisters."""
        config_yaml.write({"sisters": [
            {"name": "macbook", "url": "http://localhost:5337"},
            {"name": "desktop", "url": "http://192.168.1.10:5337", "api_key": "secret123"},
        ]})
        result = runner.invoke(app, ["sister", "list"])
        assert result.exit_code == 0
        assert "macbook" in result.output
        assert "desktop" in result.output
        assert "secr..." in result.output  # masked API key
        assert "secret123" not in result.output

    def test_sister_default_lists(self, config_yaml):
        """Sister with no subcommand lists."""
        result = runner.invoke(app, ["sister"])
        assert result.exit_code == 0
        assert "No sister instances" in result.output

    def test_sister_add(self, config_yaml):
        """Sister add creates new entry."""
        config_yaml.write({"sisters": []})
        result = runner.invoke(app, ["sister", "add", "macbook", "http://localhost:5337"])
        assert result.exit_code == 0
        assert "Added sister" in result.output
        assert config_yaml.read()["sisters"] == [{"name": "macbook", "url": "http://localhost:5337"}]

    def test_sister_add_with_api_key(self, config_yaml):
        """Sister add with API key."""
        result = runner.invoke(app, [
            "sister", "add", "desktop", "http://192.168.1.10:5337",
            "--api-key", "secret"
        ])
        assert result.exit_code == 0
        assert config_yaml.read()["sisters"][0]["api_key"] == "secret"

    def test_sister_add_duplicate(self, config_yaml):
        """Sister add rejects duplicate name."""
        config_yaml.write({"sisters": [{"name": "macbook", "url": "http://localhost:5337"}]})
        result = runner.invoke(app, ["sister", "add", "macbook", "http://other:5337"])
        assert result.exit_code == 1
        assert "already exists" in result.output
        assert config_yaml.read()["sisters"] == [{"name": "macbook", "url": "http://localhost:5337"}]

    def test_sister_remove(self, config_yaml):
        """Sister remove deletes entry."""
        config_yaml.write({"sisters": [{"name": "macbook", "url": "http://localhost:5337"}]})
        result = runner.invoke(app, ["sister", "remove", "macbook"])
        assert result.exit_code == 0
        assert "Removed sister" in result.output
        assert config_yaml.read().get("sisters", []) == []

    @pytest.mark.xfail(strict=True, reason=(
        "src bug: config._top_level_blocks ends a key's block at a column-0 "
        "'- item' line (valid YAML, and PyYAML's own default style), so "
        "save_config rewrites only the 'sisters:' line and leaves the old items "
        "after it: the saved config.yaml no longer parses"))
    def test_sister_remove_from_a_column_0_list(self, config_yaml):
        config_yaml.write("sisters:\n- name: macbook\n  url: http://localhost:5337\n")
        result = runner.invoke(app, ["sister", "remove", "macbook"])
        assert result.exit_code == 0
        assert config_yaml.read().get("sisters", []) == []

    def test_sister_remove_not_found(self, config_yaml):
        """Sister remove errors when not found."""
        config_yaml.write({"sisters": []})
        result = runner.invoke(app, ["sister", "remove", "missing"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_sister_allow_control_show_disabled(self, config_yaml):
        """Show allow-control status when disabled."""
        config_yaml.write({"web": {"allow_control": False}})
        result = runner.invoke(app, ["sister", "allow-control"])
        assert result.exit_code == 0
        assert "disabled" in result.output

    def test_sister_allow_control_show_enabled(self, config_yaml):
        """Show allow-control status when enabled."""
        config_yaml.write({"web": {"allow_control": True, "api_key": "secretkey"}})
        result = runner.invoke(app, ["sister", "allow-control"])
        assert result.exit_code == 0
        assert "enabled" in result.output

    def test_sister_allow_control_on(self, config_yaml):
        """Enable allow-control."""
        config_yaml.write({"web": {"api_key": "key123"}})
        result = runner.invoke(app, ["sister", "allow-control", "--on"])
        assert result.exit_code == 0
        assert "enabled" in result.output
        assert config_yaml.read()["web"] == {"api_key": "key123", "allow_control": True}

    def test_sister_allow_control_on_no_api_key(self, config_yaml):
        """Enabling control without web.api_key is refused and saves nothing:
        the control routes would answer 403 anyway."""
        config_yaml.write({"web": {}})
        result = runner.invoke(app, ["sister", "allow-control", "--on"])
        assert result.exit_code == 1
        assert "needs web.api_key" in result.output
        assert config_yaml.read() == {"web": {}}

    def test_sister_allow_control_show_enabled_without_key(self, config_yaml):
        """Status says control is refused when it is on but no key is set."""
        config_yaml.write({"web": {"allow_control": True}})
        result = runner.invoke(app, ["sister", "allow-control"])
        assert result.exit_code == 0
        assert "refused" in result.output

    def test_web_off_localhost_without_key_is_refused(self, config_yaml):
        """`overcode web --host 0.0.0.0` with no key fails, via start_web_server."""
        with patch('overcode.web_server.is_web_server_running', return_value=False), \
             patch('subprocess.Popen') as popen:
            result = runner.invoke(app, ["web", "--host", "0.0.0.0"])
        assert result.exit_code == 1
        assert "web.api_key" in result.output
        popen.assert_not_called()

    def test_sister_allow_control_off(self, config_yaml):
        """Disable allow-control."""
        config_yaml.write({"web": {"allow_control": True}})
        result = runner.invoke(app, ["sister", "allow-control", "--off"])
        assert result.exit_code == 0
        assert "disabled" in result.output
        assert config_yaml.read()["web"]["allow_control"] is False

    def test_sister_allow_control_on_and_off(self, config_yaml):
        """Both --on and --off errors."""
        result = runner.invoke(app, ["sister", "allow-control", "--on", "--off"])
        assert result.exit_code == 1
        assert "Cannot use --on and --off" in result.output


class TestConfigInitCommand:
    """Test config init command."""

    def test_config_init_creates_file(self, config_yaml):
        """Config init creates config file."""
        result = runner.invoke(app, ["config", "init"])
        assert result.exit_code == 0
        assert "Created config file" in result.output
        assert config_yaml.path.exists()

    def test_config_init_exists_no_force(self, config_yaml):
        """Config init refuses to overwrite without --force."""
        config_yaml.write("existing content")
        result = runner.invoke(app, ["config", "init"])
        assert result.exit_code == 1
        assert "already exists" in result.output
        assert config_yaml.path.read_text() == "existing content"

    def test_config_init_force(self, config_yaml):
        """Config init --force overwrites."""
        from overcode.cli.config import CONFIG_TEMPLATE
        config_yaml.write("old content")
        result = runner.invoke(app, ["config", "init", "--force"])
        assert result.exit_code == 0
        assert "Created config file" in result.output
        assert config_yaml.path.read_text() == CONFIG_TEMPLATE


class TestConfigShowCommand:
    """Test config show command."""

    def test_config_show_no_file(self, config_yaml):
        """Config show when no config file."""
        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0
        assert "No config file" in result.output

    def test_config_show_empty(self, config_yaml):
        """Config show when config is empty."""
        config_yaml.write("")
        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0
        assert "empty" in result.output

    def test_config_show_with_content(self, config_yaml):
        """Config show with content displays sections."""
        config_yaml.write({
            "default_standing_instructions": "Be concise",
            "summarizer": {"api_url": "https://api.example.com", "model": "gpt-4", "api_key_var": "KEY"},
            "sisters": [{"name": "macbook", "url": "http://localhost:5337"}],
        })
        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0
        assert "Be concise" in result.output
        assert "summarizer" in result.output
        assert "relay" not in result.output
        assert "macbook" in result.output

    def test_config_show_lists_new_agent_defaults(self, config_yaml):
        """The default backend (#470) is visible without opening the file."""
        config_yaml.write({"new_agent_defaults": {"backend": "opencode", "provider": "web", "wrapper": ""}})
        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0
        assert "new_agent_defaults" in result.output
        assert "backend: opencode" in result.output
        assert "wrapper" not in result.output  # empty values are skipped

    def test_config_init_template_documents_default_backend(self):
        from overcode.cli.config import CONFIG_TEMPLATE
        assert "new_agent_defaults" in CONFIG_TEMPLATE
        assert "backend: claude-code" in CONFIG_TEMPLATE


class TestConfigPathCommand:
    """Test config path command."""

    def test_config_path(self, config_yaml):
        """Config path outputs path."""
        result = runner.invoke(app, ["config", "path"], env={"COLUMNS": "400"})
        assert result.exit_code == 0
        assert str(config_yaml.path) in result.output


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

    def test_history_no_archived(self, fleet):
        """History shows message when no archived sessions."""
        fleet.add("still-live")
        result = runner.invoke(app, ["history"])
        assert result.exit_code == 0
        assert "No archived sessions" in result.output

    @pytest.fixture
    def archive(self, tmp_path, monkeypatch):
        """A real archive in a private state dir, with one archived agent."""
        from overcode.session_manager import SessionManager, SessionStats
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "sessions"))
        sm = SessionManager(skip_git_detection=True)
        s = sm.create_session(name="old-agent", tmux_session="agents",
                              tmux_window="old-agent-1", command=["claude", "--secret"])
        sm.update_stats(s.id, **SessionStats(
            interaction_count=10, total_tokens=50000, estimated_cost_usd=2.50,
            green_time_seconds=1800.0, non_green_time_seconds=300.0, steers_count=5,
        ).to_dict())
        sm.delete_session(s.id)
        return sm, s

    def test_history_list_archived(self, archive):
        """History lists archived sessions."""
        result = runner.invoke(app, ["history"])
        assert result.exit_code == 0
        assert "Archived Sessions (1, since 30d)" in result.output
        assert "old-agent" in result.output

    def test_history_since_and_all(self, archive):
        result = runner.invoke(app, ["history", "--since", "2099-01"])
        assert result.exit_code == 0 and "No archived sessions" in result.output
        result = runner.invoke(app, ["history", "--all", "--files"])
        assert "old-agent" in result.output and "files: none" in result.output
        result = runner.invoke(app, ["history", "--since", "soon"])
        assert result.exit_code == 1

    def test_history_specific_agent(self, archive):
        """History shows specific archived agent, by name or id."""
        sm, s = archive
        for key in ("old-agent", s.id):
            result = runner.invoke(app, ["history", key])
            assert result.exit_code == 0
            assert "old-agent" in result.output
            assert "Stats" in result.output and "$2.5000" in result.output

    def test_history_agent_not_found(self, archive):
        """History errors when agent not in archive."""
        result = runner.invoke(app, ["history", "missing"])
        assert result.exit_code == 1
        assert "No archived session" in result.output

    def test_show_falls_back_to_the_archive(self, archive):
        result = runner.invoke(app, ["show", "old-agent"])
        assert result.exit_code == 0
        assert "(archived)" in result.output and "Files: none" in result.output


class TestAttachExtended:
    """Extended attach command tests."""

    def test_attach_no_name(self):
        """Attach with no name attaches to session."""
        with patch('overcode.cli.agent.AgentLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_cls.return_value = mock_launcher

            result = runner.invoke(app, ["attach"])
            assert result.exit_code == 0
            assert "Attaching to overcode" in result.output
            mock_launcher.attach.assert_called_once_with(name=None, bare=False)

    def test_attach_with_name(self):
        """Attach with name jumps to that agent."""
        with patch('overcode.cli.agent.AgentLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_cls.return_value = mock_launcher

            result = runner.invoke(app, ["attach", "my-agent"])
            assert result.exit_code == 0
            assert "Attaching to 'my-agent'" in result.output

    def test_attach_bare_with_name(self):
        """Attach --bare with name uses bare mode."""
        with patch('overcode.cli.agent.AgentLauncher') as mock_cls:
            mock_launcher = MagicMock()
            mock_cls.return_value = mock_launcher

            result = runner.invoke(app, ["attach", "my-agent", "--bare"])
            assert result.exit_code == 0
            assert "bare mode" in result.output
            mock_launcher.attach.assert_called_once_with(name="my-agent", bare=True)


def _dispatcher_seeing(status, activity="Working", pane="pane"):
    """Patch list's fallback status detection (it reads the tmux server).

    ``status`` is one status for every agent, or a callable of the session.
    """
    dispatcher = MagicMock()
    if callable(status):
        dispatcher.detect_status.side_effect = lambda s, **kw: status(s)
    else:
        dispatcher.detect_status.return_value = (status, activity, pane)
    return patch('overcode.status_detector_factory.StatusDetectorDispatcher',
                 return_value=dispatcher)


class TestListExtended:
    """List over real agents: lifecycle, tree order and columns run for real."""

    def test_list_with_sessions(self, fleet):
        """List shows sessions with status columns."""
        fleet.add_with_history()
        with _dispatcher_seeing("running", "Working on code"):
            result = runner.invoke(app, ["list"], env={"COLUMNS": "400"})
        assert result.exit_code == 0, result.output
        assert "test-agent" in result.output
        assert "Working on code" in result.output

    def test_list_terminated_shows_cleanup_hint(self, fleet):
        """List shows cleanup hint when terminated sessions exist."""
        fleet.add_with_history(status="terminated", window=False)
        with _dispatcher_seeing("running"):
            result = runner.invoke(app, ["list"])
        assert result.exit_code == 0, result.output
        assert "cleanup" in result.output

    def test_a_gone_window_is_found_terminated_with_no_daemon(self, fleet):
        """With no daemon running, list settles the lifecycle itself."""
        fleet.add_with_history(window=False)
        with _dispatcher_seeing("running"):
            result = runner.invoke(app, ["list"])
        assert result.exit_code == 0, result.output
        assert "Detected 1 terminated session(s): test-agent" in result.output
        assert fleet.get("test-agent").status == "terminated"

    def test_list_filter_by_name_not_found(self, fleet):
        """List with name filter errors when agent not found."""
        fleet.add_with_history()
        result = runner.invoke(app, ["list", "missing"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_list_filter_by_name(self, fleet):
        """List with name filter shows only matching tree."""
        root = fleet.add_with_history("root")
        fleet.add_with_history("child", parent_session_id=root.id)
        fleet.add_with_history("stranger")
        with _dispatcher_seeing("running"):
            result = runner.invoke(app, ["list", "root"])
        assert result.exit_code == 0, result.output
        assert "root" in result.output and "child" in result.output
        assert "stranger" not in result.output

    def test_list_with_daemon_state(self, fleet):
        """List uses daemon state when available (no detection at all)."""
        fleet.add_with_history()
        fleet.publish(**{"test-agent": dict(current_status="running",
                                            current_activity="Coding")})
        with patch('overcode.status_detector_factory.StatusDetectorDispatcher') as detector:
            result = runner.invoke(app, ["list"], env={"COLUMNS": "400"})
        assert result.exit_code == 0, result.output
        assert "Coding" in result.output
        detector.assert_not_called()

    def test_list_show_done(self, fleet):
        """List with --show-done includes done agents."""
        fleet.add_with_history("running-agent")
        fleet.add_with_history("done-agent", status="done")
        with _dispatcher_seeing("running"):
            result = runner.invoke(app, ["list", "--show-done"])
        assert result.exit_code == 0, result.output
        assert "done-agent" in result.output
        assert "running-agent" in result.output

    def test_list_shows_a_childs_result_and_its_full_reason(self, fleet):
        """The Result column, and the whole reason after the activity."""
        parent = fleet.add_with_history("parent")
        fleet.add_with_history(
            "child", parent_session_id=parent.id, status="waiting_oversight",
            report_status="failure", report_reason="three integration tests still fail on main")

        def detect(s):
            if s.name == "child":
                return ("waiting_oversight", "At rest: no report yet", "pane")
            return ("running", "Working", "pane")

        with _dispatcher_seeing(detect):
            result = runner.invoke(app, ["list", "--full"], env={"COLUMNS": "400"})

        assert result.exit_code == 0, result.output
        assert "❌ three integrati…" in result.output  # the column, cut
        assert "❌ three integration tests still fail on main" in result.output  # in full
        assert "1❌" in result.output  # the parent's rollup

    def test_list_filters_done_by_default(self, fleet):
        """List without --show-done excludes done agents."""
        fleet.add_with_history("done-only", status="done")
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "No running agents" in result.output

    def test_list_asleep_agent(self, fleet):
        """List handles asleep agent status."""
        from overcode.tui_helpers import get_status_symbol
        fleet.add_with_history(is_asleep=True)
        with _dispatcher_seeing("running", "Working", ""):
            result = runner.invoke(app, ["list"])
        assert result.exit_code == 0, result.output
        assert get_status_symbol("asleep")[0] in result.output


class TestKillExtended:
    """Extended kill command tests."""

    @staticmethod
    def _family(fleet):
        parent = fleet.add("my-agent")
        fleet.add("kid", parent_session_id=parent.id)

    def test_kill_with_no_cascade(self, fleet):
        """Kill with --no-cascade leaves the children running."""
        self._family(fleet)
        result = runner.invoke(app, ["kill", "my-agent", "--no-cascade"])
        assert result.exit_code == 0
        assert fleet.get("my-agent") is None
        assert fleet.get("kid") is not None

    def test_kill_default_cascades(self, fleet):
        """Kill without --no-cascade kills the children too."""
        self._family(fleet)
        result = runner.invoke(app, ["kill", "my-agent"])
        assert result.exit_code == 0
        assert fleet.sm.list_sessions() == []


class TestCleanupUntracked:
    """Test cleanup --untracked flag (#344)."""

    def test_cleanup_untracked_kills_untracked_windows(self, fleet):
        """Cleanup with --untracked kills windows no agent owns."""
        agent = fleet.add("tracked")
        fleet.tmux.new_window("agents", "stray")

        result = runner.invoke(app, ["cleanup", "--untracked"])
        assert result.exit_code == 0, result.output
        assert "Killing untracked window stray" in result.output
        assert "stray" not in fleet.tmux.sessions["agents"]
        assert agent.tmux_window in fleet.tmux.sessions["agents"]

    def test_cleanup_untracked_with_terminated(self, fleet):
        """Cleanup with --untracked also cleans terminated sessions."""
        fleet.add("gone-1", window=False)
        fleet.add("gone-2", window=False)

        result = runner.invoke(app, ["cleanup", "--untracked"])
        assert result.exit_code == 0, result.output
        assert "2 terminated" in result.output

    def test_cleanup_untracked_no_message_when_nothing_terminated(self, fleet):
        """Cleanup with --untracked doesn't show 'no sessions' message."""
        result = runner.invoke(app, ["cleanup", "--untracked"])
        assert result.exit_code == 0
        assert "No sessions to clean up" not in result.output


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestModelsCommands:
    """overcode models info / lookup / refresh (#473)."""

    def test_info_reports_bundled_tier_under_test_pin(self):
        result = runner.invoke(app, ["models", "info"])
        assert result.exit_code == 0
        assert "tier:       bundled" in result.output
        assert "models:" in result.output

    def test_lookup_curated_model(self):
        result = runner.invoke(app, ["models", "lookup", "openai/gpt-5.6-sol"])
        assert result.exit_code == 0
        assert "258,400" in result.output
        assert "[curated]" in result.output

    def test_lookup_catalog_model(self):
        result = runner.invoke(app, ["models", "lookup", "zai/glm-4.6"])
        assert result.exit_code == 0
        assert "list price:     $0.6 in / $2.2 out" in result.output
        assert "catalog (bundled)" in result.output

    def test_lookup_unknown_model(self):
        result = runner.invoke(app, ["models", "lookup", "acme-internal-x1"])
        assert result.exit_code == 0
        assert "context window: —" in result.output
        assert "model-alias-resolution" in result.output

    def test_refresh_check_fetches_nothing(self):
        with patch('overcode.model_metadata.refresh_local_cache') as mock_refresh:
            result = runner.invoke(app, ["models", "refresh", "--check"])
        assert result.exit_code == 0
        mock_refresh.assert_not_called()

    def test_refresh_reports_result(self):
        with patch('overcode.model_metadata.refresh_local_cache',
                   return_value={"model_count": 7, "source": "https://models.dev/api.json",
                                 "fetched_at": "2026-10-01", "path": "/tmp/x.json", "tier": "local"}):
            result = runner.invoke(app, ["models", "refresh"])
        assert result.exit_code == 0
        assert "7 models" in result.output

    def test_refresh_failure_is_a_clean_error(self):
        with patch('overcode.model_metadata.refresh_local_cache', side_effect=OSError("no network")):
            result = runner.invoke(app, ["models", "refresh"])
        assert result.exit_code == 1
        assert "Refresh failed" in result.output
class TestRenameCommand:
    """`overcode rename` — the CLI wrapper around AgentLauncher.rename (#478)."""

    @staticmethod
    def _stub_launcher(monkeypatch, sess=None, rename_result=True, name_after=None):
        """Patch the CLI's AgentLauncher with a stub; return its rename calls.

        ``rename_result`` is what ``rename`` returns, or an exception it
        raises. ``name_after`` is the record's name once rename returns (what
        the CLI reads to tell the two ``False`` outcomes apart).
        """
        from types import SimpleNamespace

        calls = {}

        class _Sessions:
            def resolve_session_name(self, name):
                return sess

            def get_session(self, session_id):
                if sess is None:
                    return None
                return SimpleNamespace(
                    id=sess.id, name=name_after if name_after else sess.name,
                )

        class _Launcher:
            def __init__(self, tmux_session="agents"):
                self.sessions = _Sessions()

            def rename(self, session, new_name, **kwargs):
                calls["old"] = getattr(session, "name", session)
                calls["new"] = new_name
                calls["force"] = kwargs.get("force", False)
                if isinstance(rename_result, Exception):
                    raise rename_result
                return rename_result

        monkeypatch.setattr("overcode.launcher.AgentLauncher", _Launcher)
        return calls

    @staticmethod
    def _sess(name="ghost"):
        from types import SimpleNamespace
        return SimpleNamespace(name=name, id="abc123", tmux_window=f"{name}-abc1")

    def test_rename_unknown_agent_errors(self, monkeypatch):
        self._stub_launcher(monkeypatch, sess=None)
        result = runner.invoke(app, ["rename", "ghost", "newname"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_rename_invalid_name_errors(self, monkeypatch):
        from overcode.exceptions import InvalidSessionNameError

        self._stub_launcher(
            monkeypatch, sess=self._sess(),
            rename_result=InvalidSessionNameError("bad name!"),
        )
        result = runner.invoke(app, ["rename", "ghost", "bad name!"])
        assert result.exit_code == 1
        assert "not a valid agent name" in strip_ansi(result.output)

    def test_rename_duplicate_name_errors(self, monkeypatch):
        self._stub_launcher(
            monkeypatch, sess=self._sess(),
            rename_result=ValueError("an agent named 'first' already exists"),
        )
        result = runner.invoke(app, ["rename", "ghost", "first"])
        assert result.exit_code == 1
        assert "already exists" in strip_ansi(result.output)

    def test_busy_agent_is_refused_with_a_force_hint(self, monkeypatch):
        from overcode.exceptions import AgentBusyError

        calls = self._stub_launcher(
            monkeypatch, sess=self._sess(),
            rename_result=AgentBusyError("ghost", "running"),
        )
        result = runner.invoke(app, ["rename", "ghost", "auth-refactor"])
        out = " ".join(strip_ansi(result.output).split())  # rich wraps lines
        assert result.exit_code == 1
        assert "busy (running)" in out
        assert "--force" in out
        assert "Renamed agent" not in out
        assert calls["force"] is False

    def test_force_is_passed_through(self, monkeypatch):
        calls = self._stub_launcher(monkeypatch, sess=self._sess())
        result = runner.invoke(app, ["rename", "ghost", "auth-refactor", "--force"])
        assert result.exit_code == 0
        assert calls["force"] is True

    def test_window_rename_refused_says_agent_kept_its_name(self, monkeypatch):
        """False with the record unchanged: tmux refused, agent restored."""
        self._stub_launcher(monkeypatch, sess=self._sess(), rename_result=False)
        result = runner.invoke(app, ["rename", "ghost", "auth-refactor"])
        out = " ".join(strip_ansi(result.output).split())  # rich wraps lines
        assert result.exit_code == 1
        assert "restarted under its old name" in out
        assert "Renamed agent" not in out

    def test_relaunch_failure_says_renamed_and_how_to_restart(self, monkeypatch):
        """False with the record renamed: the rename stands, relaunch did not."""
        self._stub_launcher(
            monkeypatch, sess=self._sess(), rename_result=False,
            name_after="auth-refactor",
        )
        result = runner.invoke(app, ["rename", "ghost", "auth-refactor"])
        out = " ".join(strip_ansi(result.output).split())  # rich wraps lines
        assert result.exit_code == 1
        assert "overcode restart auth-refactor" in out

    def test_rename_success(self, monkeypatch):
        calls = self._stub_launcher(monkeypatch, sess=self._sess())
        result = runner.invoke(app, ["rename", "ghost", "auth-refactor"])
        assert result.exit_code == 0
        assert "Renamed agent: ghost" in strip_ansi(result.output)
        assert calls == {"old": "ghost", "new": "auth-refactor", "force": False}

    def test_old_name_reaches_the_agent_with_a_notice(self, monkeypatch):
        """Renaming by a previous name works and says what the name is now."""
        calls = self._stub_launcher(monkeypatch, sess=self._sess("current"))
        result = runner.invoke(app, ["rename", "original", "third"])
        out = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "'original' was renamed to 'current'" in out
        assert calls["old"] == "current"


class TestSplitTmuxNeverHangs:
    """`overcode tmux` is the recovery tool: a wedged tmux server must make it
    fail, not hang (quality review item 10)."""

    def test_a_hung_tmux_call_returns_a_failure(self, monkeypatch):
        import subprocess
        from overcode.cli import split

        def hang(cmd, **kwargs):
            assert kwargs.get("timeout") == split.SPLIT_TMUX_TIMEOUT_SECONDS
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

        monkeypatch.setattr(split.subprocess, "run", hang)
        result = split._tmux("list-sessions")
        assert result.returncode == 1
        assert "did not answer" in result.stderr
        assert split._tmux_check("has-session", "-t", "x") is False
