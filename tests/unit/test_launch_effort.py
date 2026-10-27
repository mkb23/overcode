"""Reasoning effort at launch (#537): one ``--effort``, mapped per backend.

Each backend's mapping onto its own CLI, the validation, the CLI flags on
``launch`` and ``restart``, and the effort surviving a restart.
"""

import json
import shlex
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from overcode.backends import LaunchSpec, check_effort, effort_applies, get_backend
from overcode.cli import app

runner = CliRunner()


def _after(cmd, flag):
    """The argument that follows ``flag`` in ``cmd``."""
    return cmd[cmd.index(flag) + 1]


class TestBackendMapping:
    def test_claude_passes_its_effort_flag(self):
        cmd = get_backend("claude-code").build_command(LaunchSpec(effort="xhigh"))
        assert _after(cmd, "--effort") == "xhigh"

    def test_claude_leaves_the_flag_out_without_an_effort(self):
        assert "--effort" not in get_backend("claude-code").build_command(LaunchSpec())

    def test_claude_leaves_out_a_value_its_flag_does_not_take(self):
        """An effort read back from a transcript must never break a restart."""
        cmd = get_backend("claude-code").build_command(LaunchSpec(effort="ultra"))
        assert "--effort" not in cmd

    def test_overagent_inherits_claudes_flag(self):
        with patch("overcode.overagent.write_system_prompt", return_value="/x/p.md"), \
                patch("overcode.overagent.write_plugin", return_value="/x/plugin"), \
                patch("overcode.overagent.allow_rules", return_value=[]):
            cmd = get_backend("overagent").build_command(LaunchSpec(effort="low"))
        assert _after(cmd, "--effort") == "low"

    def test_codex_sets_model_reasoning_effort(self):
        cmd = get_backend("codex").build_command(LaunchSpec(effort="high"))
        assert 'model_reasoning_effort="high"' in cmd
        assert cmd[cmd.index('model_reasoning_effort="high"') - 1] == "-c"

    def test_codex_effort_rides_after_a_resume_subcommand(self):
        cmd = get_backend("codex").build_command(
            LaunchSpec(effort="low", resume_session_id="abc"))
        assert cmd[1:3] == ["resume", "abc"]
        assert 'model_reasoning_effort="low"' in cmd

    def test_codex_without_effort_sets_nothing(self):
        cmd = get_backend("codex").build_command(LaunchSpec())
        assert not any("model_reasoning_effort" in arg for arg in cmd)

    def test_grok_passes_reasoning_effort(self):
        cmd = get_backend("grok").build_command(LaunchSpec(effort="max"))
        assert _after(cmd, "--reasoning-effort") == "max"

    def test_grok_passes_a_per_model_menu_id_as_given(self):
        cmd = get_backend("grok").build_command(LaunchSpec(effort="deep"))
        assert _after(cmd, "--reasoning-effort") == "deep"

    def test_hermes_passes_reasoning(self):
        cmd = get_backend("hermes").build_command(LaunchSpec(effort="ultra"))
        assert _after(cmd, "--reasoning") == "ultra"

    def test_opencode_sets_the_agents_model_variant(self):
        backend = get_backend("opencode")
        env = backend.env_prefix(LaunchSpec(model="openai/gpt-5", effort="high"))
        content = json.loads(shlex.split(env["OPENCODE_CONFIG_CONTENT"])[0])
        assert content == {"agent": {"build": {"model": "openai/gpt-5", "variant": "high"}}}
        # The variant lives in config; the command line is unchanged
        assert "high" not in backend.build_command(
            LaunchSpec(model="openai/gpt-5", effort="high"))

    def test_opencode_sets_the_variant_on_the_chosen_persona(self):
        env = get_backend("opencode").env_prefix(
            LaunchSpec(model="anthropic/claude-x", effort="max", agent="plan"))
        content = json.loads(shlex.split(env["OPENCODE_CONFIG_CONTENT"])[0])
        assert content["agent"] == {"plan": {"model": "anthropic/claude-x", "variant": "max"}}

    def test_opencode_without_a_model_sets_no_variant(self):
        env = get_backend("opencode").env_prefix(LaunchSpec(effort="high"))
        assert "OPENCODE_CONFIG_CONTENT" not in env

    def test_opencode2_sets_the_variant_on_the_overlay_model(self):
        env = get_backend("opencode2").env_prefix(LaunchSpec(effort="high", model="a/b"))
        content = json.loads(shlex.split(env["OPENCODE_CONFIG_CONTENT"])[0])
        assert content["model"] == "a/b#high"

    def test_opencode2_without_a_model_sets_no_variant(self):
        env = get_backend("opencode2").env_prefix(LaunchSpec(effort="high"))
        assert "OPENCODE_CONFIG_CONTENT" not in env

    @pytest.mark.parametrize("name", ["shell"])
    def test_backends_without_effort_render_nothing(self, name):
        backend = get_backend(name)
        spec = LaunchSpec(effort="high", model="a/b")
        assert not effort_applies(backend, "high")
        assert "high" not in " ".join(backend.build_command(spec))
        assert "high" not in " ".join(backend.env_prefix(spec).values())


class TestCheckEffort:
    def test_no_effort_is_no_change(self):
        assert check_effort(get_backend("claude-code"), None) == (None, [])

    def test_normalizes_case_and_space(self):
        assert check_effort(get_backend("claude-code"), " High ") == ("high", [])

    @pytest.mark.parametrize("name,bad", [
        ("claude-code", "minimal"), ("codex", "ultra"), ("hermes", "extreme")])
    def test_rejects_a_level_the_backend_does_not_take(self, name, bad):
        with pytest.raises(ValueError) as e:
            check_effort(get_backend(name), bad)
        assert f"unknown effort '{bad}'" in str(e.value)
        assert "Use one of:" in str(e.value)

    def test_claude_error_lists_its_levels(self):
        with pytest.raises(ValueError, match="low, medium, high, xhigh, max"):
            check_effort(get_backend("claude-code"), "turbo")

    def test_an_open_list_warns_rather_than_refuses(self):
        effort, warnings = check_effort(get_backend("grok"), "deep")
        assert effort == "deep"
        assert len(warnings) == 1 and "not one of the levels known" in warnings[0]

    def test_opencode_warns_without_a_model(self):
        _, warnings = check_effort(get_backend("opencode"), "high")
        assert any("--model" in w for w in warnings)
        assert check_effort(get_backend("opencode"), "high", "openai/gpt-5") == ("high", [])

    def test_opencode2_warns_without_a_model(self):
        _, warnings = check_effort(get_backend("opencode2"), "high")
        assert any("--model" in w for w in warnings)
        assert check_effort(get_backend("opencode2"), "high", "a/b") == ("high", [])

    def test_shell_warns(self):
        _, warnings = check_effort(get_backend("shell"), "high")
        assert "ignored on backend 'shell'" in warnings[0]


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    """The CLI's launcher on a private state dir and a MockTmux (see test_cli.Fleet)."""
    from tests.unit.test_cli import Fleet

    monkeypatch.setenv("OVERCODE_DIR", str(tmp_path / "oc"))
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "oc" / "sessions"))
    f = Fleet()
    monkeypatch.setattr("overcode.launcher.require_tmux", lambda *a, **kw: None)
    monkeypatch.setattr("overcode.launcher.require_agent_cli", lambda *a, **kw: None)
    monkeypatch.setattr("overcode.cli.agent.AgentLauncher",
                        lambda tmux_session="agents", *a, **kw: f.launcher(tmux_session))
    monkeypatch.setattr("overcode.cli.split.open_split", lambda *a, **kw: None)
    return f


def _typed(fleet, agent):
    return [keys for _, window, keys, _ in fleet.tmux.sent_keys if window == agent.tmux_window]


def _launch(*args, name="agent"):
    return runner.invoke(app, ["launch", "--name", name, "--directory", "/tmp", *args])


class TestLaunchFlag:
    def test_launch_effort_reaches_the_command_and_the_record(self, fleet):
        result = _launch("--effort", "xhigh")
        assert result.exit_code == 0, result.output
        agent = fleet.get("agent")
        assert agent.effort == "xhigh"
        assert "--effort" in agent.command and _after(agent.command, "--effort") == "xhigh"
        assert any("--effort xhigh" in keys for keys in _typed(fleet, agent))
        assert "Effort: xhigh" in result.output

    def test_launch_refuses_an_unknown_effort(self, fleet):
        result = _launch("--effort", "ludicrous")
        assert result.exit_code == 1
        assert "unknown effort 'ludicrous'" in result.output
        assert fleet.get("agent") is None

    def test_launch_on_codex_maps_the_effort(self, fleet):
        result = _launch("--backend", "codex", "--effort", "minimal")
        assert result.exit_code == 0, result.output
        assert 'model_reasoning_effort="minimal"' in fleet.get("agent").command

    def test_launch_warns_when_the_backend_cannot_apply_it(self, fleet, capsys):
        result = _launch("--backend", "shell", "--effort", "high")
        assert result.exit_code == 0, result.output
        assert "ignored on backend 'shell'" in result.output + capsys.readouterr().err

    def test_help_lists_the_flag(self):
        assert "--effort" in runner.invoke(app, ["launch", "--help"]).output


class TestRestart:
    @staticmethod
    def _worker(fleet, **fields):
        agent = fleet.add("worker", **fields)
        fleet.sm.update_session(agent.id, active_agent_session_id="conv-1")
        return fleet.get("worker")

    def _relaunch_line(self, fleet, agent):
        lines = [k for k in _typed(fleet, agent) if "--settings" in k]
        assert lines, _typed(fleet, agent)
        return lines[-1]

    def test_restart_keeps_the_launch_effort(self, fleet):
        assert _launch("--effort", "low", name="worker").exit_code == 0
        agent = fleet.get("worker")
        fleet.sm.update_session(agent.id, active_agent_session_id="conv-1")
        fleet.tmux.sent_keys.clear()

        result = runner.invoke(app, ["restart", "worker"])

        assert result.exit_code == 0, result.output
        line = self._relaunch_line(fleet, agent)
        assert "--resume conv-1" in line and "--effort low" in line

    def test_restart_effort_is_kept_for_later_restarts(self, fleet):
        self._worker(fleet, effort="high")

        result = runner.invoke(app, ["restart", "worker", "--effort", "max"])

        assert result.exit_code == 0, result.output
        agent = fleet.get("worker")
        assert agent.effort == "max"
        assert isinstance(agent.model_chosen_at, float)
        assert "--effort max" in self._relaunch_line(fleet, agent)

    def test_restart_with_model_and_effort_keeps_both(self, fleet):
        self._worker(fleet, model="sonnet", effort="high")

        result = runner.invoke(app, ["restart", "worker", "--model", "opus", "--effort", "low"])

        assert result.exit_code == 0, result.output
        agent = fleet.get("worker")
        assert (agent.model, agent.effort) == ("opus", "low")

    def test_restart_refuses_an_unknown_effort(self, fleet):
        self._worker(fleet, effort="high")

        result = runner.invoke(app, ["restart", "worker", "--effort", "bogus"])

        assert result.exit_code == 1
        assert "unknown effort 'bogus'" in result.output
        assert fleet.get("worker").effort == "high"


class TestLauncherPaths:
    def test_fork_inherits_the_effort(self, fleet):
        source = fleet.add("source", effort="xhigh", active_agent_session_id="conv-1")
        launcher = fleet.launcher()
        with patch.object(type(launcher), "_wait_for_prompt", return_value=True):
            fork = launcher.launch_fork("source-fork", source)
        assert fork.effort == "xhigh"
        assert "--effort" in fork.command and _after(fork.command, "--effort") == "xhigh"

    def test_revive_passes_the_stored_effort(self, fleet):
        agent = fleet.add("dead", window=False, effort="medium",
                          active_agent_session_id="conv-9")
        assert fleet.launcher().revive(agent) is True
        command = fleet.get("dead").command
        assert _after(command, "--effort") == "medium"

    def test_old_records_without_effort_still_load_and_launch(self, fleet):
        """A sessions.json written before #537 (no effort key) relaunches as before."""
        agent = fleet.add("legacy", window=False, active_agent_session_id="conv-2")
        assert agent.effort is None
        assert fleet.launcher().revive(agent) is True
        assert "--effort" not in fleet.get("legacy").command
