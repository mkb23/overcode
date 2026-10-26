"""
send.prefer_claude_messaging (#553): Claude Code -> Claude Code sends point at SendMessage.

- Off by default; saved beside the rest of send:.
- On: `overcode send` from a Claude Code agent to another Claude Code agent
  is refused (exit 1, nothing typed) with a pointer to SendMessage, unless
  --force. Gestures, --no-enter keys, a human sender, a non-Claude end and
  sending to yourself go through.
- The palette's toggle flips it; `config show`, `config init` and the
  overcode skill mention it.
"""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from overcode import config
from overcode.cli import app as cli_app
from tests.tui_harness import FakeEngine, ready, three_agents

runner = CliRunner()


@pytest.fixture(autouse=True)
def _own_config(tmp_path, monkeypatch):
    """A config.yaml of this test's own: none of it outlives the test."""
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.yaml")
    config._clear_config_cache()
    yield
    config._clear_config_cache()


def _write_config(text):
    path = config.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    config._clear_config_cache()


ON = "send:\n  prefer_claude_messaging: true\n"


class TestSetting:
    def test_default_is_off(self):
        _write_config("")
        assert config.get_prefer_claude_messaging() is False

    def test_saved_beside_the_rest_of_send(self):
        _write_config("send:\n  other: 1\n")
        config.save_prefer_claude_messaging(True)
        config._clear_config_cache()
        assert config.get_prefer_claude_messaging() is True
        assert config.load_config()["send"]["other"] == 1
        config.save_prefer_claude_messaging(False)
        config._clear_config_cache()
        assert config.get_prefer_claude_messaging() is False


@pytest.fixture
def fleet(monkeypatch):
    """A Claude Code sender, a Claude Code target and an opencode target."""
    from overcode.session_manager import SessionManager
    sm = SessionManager(skip_git_detection=True)
    agents = {
        name: sm.create_session(name=name, tmux_session="agents", tmux_window=name,
                                command=["claude"], backend=backend)
        for name, backend in (("boss", "claude-code"), ("worker", "claude-code"),
                              ("oc", "opencode"))
    }
    monkeypatch.setenv("OVERCODE_SESSION_ID", agents["boss"].id)
    return agents


def _send(*args):
    with patch("overcode.cli.agent.AgentLauncher") as launcher_class:
        launcher = MagicMock()
        launcher.send_to_session.return_value = True
        launcher_class.return_value = launcher
        result = runner.invoke(cli_app, ["send", *args])
    return result, launcher.send_to_session


class TestSend:
    def test_off_sends(self, fleet):
        _write_config("")
        result, sent = _send("worker", "hello")
        assert result.exit_code == 0
        sent.assert_called_once()

    def test_on_refuses_claude_to_claude_and_points_at_send_message(self, fleet):
        _write_config(ON)
        result, sent = _send("worker", "please", "rebase")
        assert result.exit_code == 1
        sent.assert_not_called()
        assert "SendMessage" in result.output and '"worker"' in result.output
        assert "--force" in result.output

    def test_force_sends(self, fleet):
        _write_config(ON)
        result, sent = _send("--force", "worker", "hello")
        assert result.exit_code == 0
        sent.assert_called_once()

    @pytest.mark.parametrize("args", [("worker", "approve"), ("worker", "Escape"),
                                      ("worker", "enter"), ("worker", "--no-enter", "y")])
    def test_gestures_and_keys_go_through(self, fleet, args):
        _write_config(ON)
        result, sent = _send(*args)
        assert result.exit_code == 0
        sent.assert_called_once()

    def test_a_non_claude_target_goes_through(self, fleet):
        _write_config(ON)
        result, sent = _send("oc", "hello")
        assert result.exit_code == 0
        sent.assert_called_once()

    def test_a_non_claude_sender_goes_through(self, fleet, monkeypatch):
        _write_config(ON)
        monkeypatch.setenv("OVERCODE_SESSION_ID", fleet["oc"].id)
        result, sent = _send("worker", "hello")
        assert result.exit_code == 0
        sent.assert_called_once()

    def test_a_human_goes_through(self, fleet, monkeypatch):
        _write_config(ON)
        monkeypatch.delenv("OVERCODE_SESSION_ID")
        monkeypatch.delenv("OVERCODE_SESSION_NAME", raising=False)
        result, sent = _send("worker", "hello")
        assert result.exit_code == 0
        sent.assert_called_once()

    def test_refusing_does_not_wake_a_sleeping_target(self, fleet):
        from overcode.session_manager import SessionManager
        _write_config(ON)
        SessionManager().update_session(fleet["worker"].id, is_asleep=True)
        result, _ = _send("worker", "hello")
        assert result.exit_code == 1
        assert SessionManager().get_session(fleet["worker"].id).is_asleep


@pytest.fixture
def app(make_tui):
    return make_tui(three_agents(), engine=FakeEngine(), size=(200, 40))


@pytest.mark.asyncio
class TestPalette:
    async def test_the_toggle_flips_and_persists(self, app):
        from overcode.command_palette import COMMANDS
        cmd = next(c for c in COMMANDS if c.action == "toggle_prefer_claude_messaging")
        assert cmd.category == "Settings"
        _write_config("")
        async with app.run_test() as pilot:
            await ready(pilot)
            assert cmd.state(app).current == 0
            app.action_toggle_prefer_claude_messaging()
            await pilot.pause()
            config._clear_config_cache()
            assert config.get_prefer_claude_messaging() is True
            assert cmd.state(app).current == 1
            app.action_toggle_prefer_claude_messaging()
            config._clear_config_cache()
            assert config.get_prefer_claude_messaging() is False


class TestSurfaces:
    def test_config_show_prints_it(self):
        _write_config(ON)
        out = runner.invoke(cli_app, ["config", "show"]).output
        assert "send.prefer_claude_messaging: True" in out

    def test_config_init_and_the_skill_document_it(self):
        from overcode.bundled_skills import OVERCODE_SKILLS
        from overcode.cli.config import CONFIG_TEMPLATE
        assert "prefer_claude_messaging" in CONFIG_TEMPLATE
        text = "".join(str(v) for skill in OVERCODE_SKILLS.values() for v in skill.values())
        assert "SendMessage" in text and "overcode send --force" in text
