"""Status colour themes (status_theme.py): presets, overrides, config.yaml,
emoji-free fallbacks, the help legend, the palette cycler and the dialog."""

import unicodedata

import pytest
import yaml
from rich.cells import cell_len

from overcode import status_theme as st
from overcode.config import config_path
from overcode.keymap import effective_keymap
from overcode.status_constants import (
    ALL_STATUSES, LIGHT_LEGEND, STATUS_SYMBOLS, STATUS_WAITING_APPROVAL, STATUS_WAITING_USER,
    get_status_symbol,
)
from overcode.status_theme import StatusTheme, resolve
from tests.tui_harness import ready, three_agents

pytestmark = pytest.mark.unit


def _write_config(section):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump({"status_theme": section}, allow_unicode=True))


class TestGlyphs:
    @pytest.mark.parametrize("glyph", list(st.GLYPHS))
    def test_every_glyph_draws_two_cells_in_the_status_column(self, glyph):
        """An emoji is wide by default; a shape is padded to two."""
        from overcode.columns.render_identity import render_status_symbol

        class Ctx:
            status_symbol, status_color = glyph, "default"
        text = render_status_symbol(Ctx())[0][0]
        assert cell_len(text) == 3  # the glyph's two cells and the gap
        if cell_len(glyph) == 2:
            assert len(glyph) == 1 and unicodedata.east_asian_width(glyph) == "W"

    def test_every_preset_names_known_glyphs_for_every_light(self):
        for name, glyphs in st.PRESETS.items():
            assert set(glyphs) == set(st.LIGHTS), name
            assert all(g in st.GLYPHS for g in glyphs.values()), name

    @pytest.mark.parametrize("preset", st.PRESET_NAMES)
    def test_no_preset_has_two_main_lights_alike(self, preset):
        assert StatusTheme(preset).clashes() == []


class TestClassic:
    def test_classic_is_what_status_symbols_says(self):
        for status in ALL_STATUSES:
            assert st.CLASSIC_THEME.symbol_for(status) == STATUS_SYMBOLS[status], status

    def test_no_config_is_classic(self):
        assert st.active_theme() == st.CLASSIC_THEME
        assert get_status_symbol(STATUS_WAITING_USER) == ("🔴", "red")


class TestResolve:
    def test_a_preset_name_alone(self):
        theme, warnings = resolve("calm")
        assert warnings == []
        assert theme.glyph("input") == "🔵" and theme.glyph("approval") == "🔴"
        assert theme.colour("input") == "dodger_blue1"

    def test_overrides_by_glyph_or_name(self):
        theme, warnings = resolve({"preset": "classic",
                                   "overrides": {"input": "blue circle", "working": "🟩"}})
        assert warnings == []
        assert theme.glyph("input") == "🔵" and theme.glyph("working") == "🟩"
        assert theme.label() == "classic+"

    def test_an_override_equal_to_the_preset_is_dropped(self):
        theme, _ = resolve({"preset": "classic", "overrides": {"working": "🟢"}})
        assert theme.overrides == {}

    def test_unknowns_warn_and_fall_back(self):
        theme, warnings = resolve({"preset": "neon", "overrides": {"bogus": "🔵", "input": "🦄"},
                                   "future_key": 1})
        assert theme == st.CLASSIC_THEME
        assert len(warnings) == 3
        assert "neon" in warnings[0] and "bogus" in warnings[1] and "🦄" in warnings[2]

    def test_a_wrong_shape_warns(self):
        assert resolve(42) == (st.CLASSIC_THEME, [f"{st.CONFIG_KEY}: expected a preset name or a mapping"])
        assert resolve({"overrides": ["x"]})[1]

    def test_emoji_free_keeps_each_lights_letter(self):
        """[W] is "needs input" whatever glyph the theme gives it."""
        calm = resolve("calm")[0]
        assert calm.symbol_for(STATUS_WAITING_USER, emoji_free=True) == ("[W]", "dodger_blue1")
        assert calm.symbol_for(STATUS_WAITING_APPROVAL, emoji_free=True) == ("[A]", "red")
        for e in LIGHT_LEGEND:
            assert StatusTheme("shapes").light_symbol(e.key, True)[0] == e.ascii


class TestConfig:
    def test_the_active_theme_follows_config_yaml(self):
        _write_config({"preset": "colorblind", "overrides": {"input": "🔶"}})
        assert get_status_symbol(STATUS_WAITING_USER) == ("🔶", "orange1")
        assert get_status_symbol(STATUS_WAITING_APPROVAL)[0] == "🟧"

    def test_save_and_reload(self):
        st.save_theme(StatusTheme("shapes", {"error": "🟣"}))
        data = yaml.safe_load(config_path().read_text())
        assert data["status_theme"] == {"preset": "shapes", "overrides": {"error": "🟣"}}
        assert st.active_theme() == StatusTheme("shapes", {"error": "🟣"})
        st.save_theme(st.CLASSIC_THEME)
        assert "status_theme" not in (yaml.safe_load(config_path().read_text()) or {})

    def test_save_keeps_the_rest_of_config(self):
        config_path().parent.mkdir(parents=True, exist_ok=True)
        config_path().write_text("# mine\nhostname: box\n")
        st.save_theme(StatusTheme("calm"))
        text = config_path().read_text()
        assert "# mine" in text and "hostname: box" in text and "calm" in text


class TestViews:
    def test_the_help_legend_shows_the_active_glyphs(self):
        from overcode.tui_widgets.help_overlay import build_help
        _write_config("colorblind")
        km = effective_keymap(preset=None, section={}, passthru={}, tmux_toggle_key="Tab")
        text = build_help(200, km).plain
        for glyph in ("🔷", "🟦", "🟧", "🔶", "⬜", "⬛"):
            assert glyph in text
        assert "Status colours: colorblind" in text

    def test_subagent_counts_and_lights_follow_the_theme(self):
        from overcode.swarm_view import count_glyph, light_glyph
        _write_config("shapes")
        assert count_glyph("waiting") == "▲" and count_glyph("idle") == "○"
        assert count_glyph("waiting", emoji_free=True) == "[A]"
        assert count_glyph("ok") == "✅"
        assert light_glyph({"light": "green"}) == ("●", "default")

    def test_the_web_api_keeps_classic(self):
        from overcode import web_api
        _write_config("calm")
        assert web_api.CLASSIC_THEME.symbol_for(STATUS_WAITING_USER) == ("🔴", "red")


class TestTui:
    async def test_the_palette_cycles_and_remembers(self, make_tui):
        from overcode.command_palette import COMMANDS
        cmd = next(c for c in COMMANDS if c.action == "cycle_status_theme")
        app = make_tui(three_agents(), size=(200, 40))
        async with app.run_test() as pilot:
            await ready(pilot)
            state = cmd.state(app)
            assert state.options[state.current] == "classic"
            app.action_cycle_status_theme()
            await pilot.pause()
            assert st.active_theme().preset == "calm"
            state = cmd.state(app)
            assert state.options[state.current] == "calm"
        assert yaml.safe_load(config_path().read_text())["status_theme"]["preset"] == "calm"

    async def test_the_dialog_changes_a_light_and_warns_of_a_clash(self, make_tui):
        from overcode.tui_widgets import StatusColoursModal
        app = make_tui(three_agents(), size=(200, 40))
        async with app.run_test() as pilot:
            await ready(pilot)
            app.action_open_status_colours()
            await pilot.pause()
            modal = app.query_one("#status-colours-modal", StatusColoursModal)
            assert "visible" in modal.classes
            await pilot.press("right")          # preset → calm
            await pilot.pause()
            assert st.active_theme().preset == "calm"
            await pilot.press("j", "j", "j", "j")  # Needs input (🔵)
            await pilot.press("left")           # the glyph before 🔵
            await pilot.pause()
            assert st.active_theme().overrides["input"] == st.cycle_glyph("🔵", -1)
            await pilot.press("r")
            await pilot.pause()
            assert st.active_theme().overrides == {}
            # Needs input as Needs approval's 🔴: the tip warns
            modal._set("calm", {"input": "🔴"})
            await pilot.pause()
            assert "look the same" in modal.render().plain
            await pilot.press("escape")
            assert "visible" not in modal.classes
