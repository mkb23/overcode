"""
Auto-naming settings exposed (#547): the TUI dialog, config.yaml, and the CLI.

- "Auto-naming settings…" (palette): each row saves naming.<key> at once and
  the summary line follows; an odd value set by hand shows as it is.
- save_naming_setting keeps the rest of naming: and the {minutes: N} shape.
- `overcode config init` documents naming:, `config show` prints it.
"""

import pytest
from rich.cells import cell_len

from overcode import config
from tests.tui_harness import FakeEngine, ready, three_agents


@pytest.fixture
def app(make_tui):
    return make_tui(three_agents(), engine=FakeEngine(), size=(200, 40))


def _naming():
    config._clear_config_cache()
    return config.get_naming_config()


def _write_config(text):
    path = config.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    config._clear_config_cache()


@pytest.mark.asyncio
class TestDialog:
    async def test_each_row_is_saved_as_it_changes(self, app):
        from overcode.tui_widgets import AutoNamingModal

        async with app.run_test() as pilot:
            await ready(pilot)
            app.action_open_auto_naming_settings()
            await pilot.pause()
            modal = app.query_one(AutoNamingModal)
            assert "visible" in modal.classes
            assert modal.summary().startswith("off")

            await pilot.press("space")                       # Auto-naming: off → on
            await pilot.pause()
            assert _naming()["enabled"] is True
            await pilot.press("j", "space")                  # First name after: 5m → 10m
            await pilot.pause()
            assert _naming()["first_after"] == {"minutes": 10.0}
            await pilot.press("j", "right", "right")         # Re-check every: 30m → 2h
            await pilot.pause()
            assert _naming()["rename_every"] == {"minutes": 120.0}
            assert "re-checked every 120 min of work" in modal.summary()
            await pilot.press("j", "space")                  # Auto renames: no cap → 1
            await pilot.pause()
            assert _naming()["max_renames"] == 1
            await pilot.press("left")                        # back to no cap
            await pilot.pause()
            assert _naming()["max_renames"] is None
            await pilot.press("j", "space")                  # Cost cap: $0.50 → $1
            await pilot.press("j", "space")                  # Claude title: on → off
            await pilot.pause()
            cfg = _naming()
            assert cfg["cost_cap_per_day"] == 1.0 and cfg["cli_title"] is False
            await pilot.press("escape")
            assert "visible" not in modal.classes

    async def test_the_palette_offers_it(self, app):
        from overcode.command_palette import COMMANDS
        cmd = next(c for c in COMMANDS if c.action == "open_auto_naming_settings")
        assert cmd.title == "Auto-naming settings…" and cmd.category == "Settings"
        assert hasattr(app, "action_open_auto_naming_settings")


class TestRendering:
    def _modal(self, values):
        from overcode.tui_widgets.auto_naming_modal import AutoNamingModal
        modal = AutoNamingModal()
        modal._values = dict(values)
        return modal

    def test_rows_fit_the_dialog_and_odd_values_show(self):
        from overcode.tui_widgets.auto_naming_modal import values_from_config
        values = values_from_config(_naming())
        values.update(rename_every=45.0, cost_cap_per_day=0.3)   # set by hand
        modal = self._modal(values)
        text = modal.render().plain.splitlines()
        assert all(cell_len(line) <= modal.inner_width for line in text)
        joined = "\n".join(text)
        assert "45m" in joined and "$0.30" in joined
        assert "Re-check every" in joined and "Auto renames" in joined

    def test_values_from_config_defaults(self):
        from overcode.tui_widgets.auto_naming_modal import values_from_config
        assert values_from_config(_naming()) == {
            "enabled": False, "first_after": 5.0, "rename_every": 30.0, "max_renames": None,
            "cost_cap_per_day": 0.5, "cli_title": True}


class TestSaving:
    def test_minutes_keys_keep_their_shape_and_the_rest_of_naming(self):
        _write_config("naming:\n  enabled: true\n  advice: kebab please\n")
        config.save_naming_setting("rename_every", 60.0)
        config.save_naming_setting("max_renames", 4)
        cfg = _naming()
        assert cfg["rename_every"] == {"minutes": 60.0} and cfg["max_renames"] == 4
        assert cfg["enabled"] is True and cfg["advice"] == "kebab please"
        assert cfg["warnings"] == []


class TestCli:
    def test_config_init_documents_naming(self):
        from overcode.cli.config import CONFIG_TEMPLATE
        for key in ("naming:", "first_after", "rename_every", "max_renames", "cost_cap_per_day"):
            assert key in CONFIG_TEMPLATE

    def test_config_show_prints_naming(self):
        from typer.testing import CliRunner
        from overcode.cli import app as cli_app
        _write_config("naming:\n  enabled: true\n  rename_every: {minutes: 45}\n")
        out = CliRunner().invoke(cli_app, ["config", "show"]).output
        assert "naming:" in out and "rename_every: 45 min of work" in out
        assert "max_renames: no cap" in out
        assert "re-checked every 45 min of work" in out
