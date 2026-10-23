"""
Budget reclaim mode (#546): children.budget_reclaim auto | manual.

- The setting reads from config.yaml (default auto), survives a save that
  keeps the rest of children:, and warns (never raises) on a bad value.
- The palette's "Budget reclaim" shows and flips it; `config show` prints it;
  the overcode skill tells a parent what manual means.
"""

import pytest

from overcode import config
from tests.tui_harness import FakeEngine, ready, three_agents


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


class TestSetting:
    def test_default_is_auto(self):
        _write_config("")
        assert config.get_budget_reclaim_mode() == "auto"

    def test_manual_is_read_and_saved_beside_the_presets(self):
        _write_config("children:\n  preset: balanced\n")
        config.save_budget_reclaim_mode("manual")
        config._clear_config_cache()
        assert config.get_budget_reclaim_mode() == "manual"
        from overcode.child_presets import load_presets
        _, default, warnings = load_presets()
        assert default == "balanced" and warnings == []

    def test_a_bad_value_reads_as_auto_with_a_warning(self):
        _write_config("children:\n  budget_reclaim: sometimes\n")
        assert config.get_budget_reclaim_mode() == "auto"
        from overcode.child_presets import load_presets
        _, _, warnings = load_presets()
        assert any("budget_reclaim" in w for w in warnings)
        with pytest.raises(ValueError):
            config.save_budget_reclaim_mode("sometimes")


@pytest.fixture
def app(make_tui):
    return make_tui(three_agents(), engine=FakeEngine(), size=(200, 40))


@pytest.mark.asyncio
class TestPalette:
    async def test_the_toggle_flips_and_persists(self, app):
        from overcode.command_palette import COMMANDS
        cmd = next(c for c in COMMANDS if c.action == "toggle_budget_reclaim")
        assert cmd.title == "Budget reclaim" and cmd.category == "Settings"
        _write_config("")
        async with app.run_test() as pilot:
            await ready(pilot)
            assert cmd.state(app).options[cmd.state(app).current] == "auto"
            app.action_toggle_budget_reclaim()
            await pilot.pause()
            config._clear_config_cache()
            assert config.get_budget_reclaim_mode() == "manual"
            assert cmd.state(app).options[cmd.state(app).current] == "manual"
            app.action_toggle_budget_reclaim()
            config._clear_config_cache()
            assert config.get_budget_reclaim_mode() == "auto"


class TestSurfaces:
    def test_config_show_prints_it(self):
        from typer.testing import CliRunner
        from overcode.cli import app as cli_app
        _write_config("children:\n  budget_reclaim: manual\n")
        out = CliRunner().invoke(cli_app, ["config", "show"]).output
        assert "children.budget_reclaim: manual" in out

    def test_config_init_and_the_skill_document_it(self):
        from overcode.bundled_skills import OVERCODE_SKILLS
        from overcode.cli.config import CONFIG_TEMPLATE
        assert "budget_reclaim" in CONFIG_TEMPLATE
        text = "".join(str(v) for skill in OVERCODE_SKILLS.values() for v in skill.values())
        assert "overcode budget reclaim" in text and "budget_reclaim: manual" in text
