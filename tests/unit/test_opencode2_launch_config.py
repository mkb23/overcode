"""opencode2 model, effort and agent at launch (#529).

The bare v2 TUI rejects ``--model``/``--agent`` and ignores
``OPENCODE_MODEL``; the choice goes in as an ``OPENCODE_CONFIG_CONTENT``
overlay, which a new session honours and a resumed one does not (its model
stays). Backends warn about what they will not apply, at launch and restart.
"""

import json
import shlex

import pytest
from typer.testing import CliRunner

from overcode.backends import LaunchSpec, get_backend
from overcode.cli import app

runner = CliRunner()


def _overlay(spec):
    env = get_backend("opencode2").env_prefix(spec)
    raw = env.get("OPENCODE_CONFIG_CONTENT")
    return None if raw is None else json.loads(shlex.split(raw)[0])


class TestOverlay:
    def test_model_goes_in_the_overlay(self):
        assert _overlay(LaunchSpec(model="openai/gpt-5")) == {"model": "openai/gpt-5"}

    def test_the_inert_env_var_is_gone(self):
        env = get_backend("opencode2").env_prefix(LaunchSpec(model="openai/gpt-5"))
        assert "OPENCODE_MODEL" not in env

    def test_agent_is_the_default_agent(self):
        assert _overlay(LaunchSpec(agent="plan")) == {"default_agent": "plan"}

    def test_effort_is_the_model_variant(self):
        assert _overlay(LaunchSpec(model="a/b", effort="high")) == {"model": "a/b#high"}

    def test_a_model_naming_its_own_variant_is_kept(self):
        assert _overlay(LaunchSpec(model="a/b#low", effort="high")) == {"model": "a/b#low"}

    def test_nothing_chosen_sets_no_overlay(self):
        assert _overlay(LaunchSpec()) is None

    def test_the_tui_gets_no_model_or_agent_flags(self):
        cmd = get_backend("opencode2").build_command(LaunchSpec(model="a/b", agent="plan"))
        assert "--model" not in cmd and "--agent" not in cmd


class TestLaunchWarnings:
    def test_opencode2_applies_model_and_agent_on_a_new_session(self):
        assert get_backend("opencode2").launch_warnings(
            model="a/b", agent="plan", effort="high") == []

    @pytest.mark.parametrize("flag,kwargs", [
        ("--model a/b", {"model": "a/b"}), ("--effort high", {"effort": "high"})])
    def test_opencode2_warns_a_resume_keeps_its_model(self, flag, kwargs):
        warnings = get_backend("opencode2").launch_warnings(resuming=True, **kwargs)
        assert len(warnings) == 1
        assert warnings[0].startswith(f"{flag} is not applied")
        assert "--fresh" in warnings[0]

    def test_claude_applies_both_on_a_resume(self):
        assert get_backend("claude-code").launch_warnings(
            model="opus", agent="reviewer", resuming=True) == []

    @pytest.mark.parametrize("name", ["codex", "hermes", "shell"])
    def test_a_backend_with_no_persona_knob_warns(self, name):
        warnings = get_backend(name).launch_warnings(agent="reviewer")
        assert warnings == [f"--agent reviewer is ignored on backend '{name}': "
                            f"it has no way to pick an agent persona at launch"]


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
    # No telemetry plugin written into the launch directory
    monkeypatch.setattr("overcode.config.get_backend_telemetry_enabled", lambda *a: False)
    return f


def _typed(fleet, agent):
    return [keys for _, window, keys, _ in fleet.tmux.sent_keys if window == agent.tmux_window]


def _launch(tmp_path, *args, name="agent"):
    return runner.invoke(app, ["launch", "--name", name, "--directory", str(tmp_path), *args])


class TestLaunchCli:
    def test_opencode2_model_and_agent_reach_the_overlay(self, fleet, tmp_path, capsys):
        result = _launch(tmp_path, "--backend", "opencode2", "--model", "a/b", "--agent", "plan")
        assert result.exit_code == 0, result.output
        assert "Warning" not in result.output + capsys.readouterr().err
        typed = " ".join(_typed(fleet, fleet.get("agent")))
        assert "OPENCODE_CONFIG_CONTENT=" in typed
        assert '"default_agent": "plan"' in typed and '"model": "a/b"' in typed

    def test_codex_agent_is_warned_about(self, fleet, tmp_path, capsys):
        result = _launch(tmp_path, "--backend", "codex", "--agent", "reviewer")
        assert result.exit_code == 0, result.output
        out = result.output + capsys.readouterr().err
        assert "--agent reviewer is ignored on backend 'codex'" in out

    def test_the_launcher_keeps_the_warnings_for_the_tui(self, fleet, tmp_path):
        launcher = fleet.launcher()
        launcher.launch(name="agent", start_directory=str(tmp_path), backend="codex",
                        agent_persona="reviewer")
        assert launcher.last_launch_warnings == [
            "--agent reviewer is ignored on backend 'codex': "
            "it has no way to pick an agent persona at launch"]


class TestRestartCli:
    @staticmethod
    def _worker(fleet):
        agent = fleet.add("worker", backend="opencode2", model="a/old")
        fleet.sm.update_session(agent.id, active_agent_session_id="ses_1")
        return fleet.get("worker")

    def test_resume_warns_and_records_nothing(self, fleet, capsys):
        self._worker(fleet)

        result = runner.invoke(app, ["restart", "worker", "--model", "a/new", "--effort", "high"])

        assert result.exit_code == 0, result.output
        out = result.output + capsys.readouterr().err
        assert "--model a/new is not applied" in out
        assert "--effort high is not applied" in out
        agent = fleet.get("worker")
        assert agent.model == "a/old" and agent.effort is None
        assert agent.model_chosen_at is None

    def test_fresh_applies_and_records_the_model(self, fleet, capsys):
        self._worker(fleet)

        result = runner.invoke(app, ["restart", "worker", "--fresh", "--model", "a/new"])

        assert result.exit_code == 0, result.output
        assert "not applied" not in result.output + capsys.readouterr().err
        assert fleet.get("worker").model == "a/new"
