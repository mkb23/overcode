"""Light / dark theme (#508)."""

import dataclasses

import pytest
from rich.color import Color as RichColor
from rich.color_triplet import ColorTriplet
from rich.segment import Segment
from rich.style import Style

from overcode import tui_theme
from overcode.tui_theme import (
    DARK, LIGHT, PALETTES, LightThemeFilter, contrast, light_bg, light_fg,
    light_style, next_theme, normalize_theme,
)


LIGHT_SURFACE = (0xE0, 0xE0, 0xE0)


def _rgb(hex_: str):
    return tuple(RichColor.parse(hex_).get_truecolor())


class TestPalette:

    def test_both_variants_name_the_same_colours(self):
        names = {f.name for f in dataclasses.fields(tui_theme.Palette)}
        for pal in PALETTES.values():
            assert {f.name for f in dataclasses.fields(pal)} == names
            assert all(getattr(pal, n) for n in names)

    def test_dark_matches_the_literals_the_code_draws_with(self):
        from overcode.tui_widgets import dialog_style as ds
        assert DARK.sel_bg == ds.SEL_BG
        assert DARK.accent == ds.ACCENT
        assert DARK.text == ds.TEXT
        assert DARK.muted == ds.MUTED
        assert DARK.match in ds.MATCH
        assert DARK.error == ds.ERROR
        assert DARK.key == ds.WARN
        from overcode.status_constants import PRESENCE_COLORS, PRESENCE_ASLEEP
        assert DARK.asleep == PRESENCE_COLORS[PRESENCE_ASLEEP]
        import inspect
        from overcode.tui_widgets import session_summary
        src = inspect.getsource(session_summary)
        assert f'" on {DARK.selected_row_bg}" if is_highlighted else " on {DARK.row_bg}"' in src

    def test_light_foregrounds_read_on_the_light_surface(self):
        for f in dataclasses.fields(LIGHT):
            if f.name.endswith("_bg") or f.name in ("asleep", "state_other", "terminated"):
                continue
            assert contrast(_rgb(getattr(LIGHT, f.name)), LIGHT_SURFACE) >= 3.0, f.name

    def test_selected_row_stands_out_from_plain_row(self):
        assert _rgb(LIGHT.selected_row_bg) != _rgb(LIGHT.row_bg)
        assert light_bg(_rgb(DARK.row_bg)) == _rgb(LIGHT.row_bg)


class TestAdapt:

    def test_names_cycle(self):
        assert normalize_theme(None) == "dark"
        assert normalize_theme("bogus") == "dark"
        assert next_theme("dark") == "light"
        assert next_theme("light") == "dark"

    @pytest.mark.parametrize("colour", ["white", "bright_white", "yellow", "#ffd75f", "grey70", "orange1"])
    def test_light_colours_become_readable(self, colour):
        out = light_style(Style.parse(colour), RichColor.from_triplet(ColorTriplet(*LIGHT_SURFACE)))
        assert contrast(tuple(out.color.get_truecolor()), LIGHT_SURFACE) >= tui_theme.MIN_CONTRAST - 0.01

    def test_white_on_dark_row_becomes_dark_on_light_row(self):
        out = light_style(Style.parse(f"bold white on {DARK.row_bg}"), RichColor.from_triplet(ColorTriplet(*LIGHT_SURFACE)))
        assert tuple(out.bgcolor.get_truecolor()) == _rgb(LIGHT.row_bg)
        assert contrast(tuple(out.color.get_truecolor()), _rgb(LIGHT.row_bg)) >= tui_theme.MIN_CONTRAST - 0.01
        assert out.bold

    def test_dark_text_is_left_alone(self):
        assert light_fg((0x10, 0x10, 0x10), LIGHT_SURFACE) == (0x10, 0x10, 0x10)

    def test_filter_keeps_text_and_unstyled_segments(self):
        segs = [Segment("a", None), Segment("b", Style(bold=True)), Segment("c", Style.parse("white"))]
        from textual.color import Color
        out = LightThemeFilter().apply(segs, Color(224, 224, 224))
        assert [s.text for s in out] == ["a", "b", "c"]
        assert out[0].style is None
        assert out[1].style == Style(bold=True)


class TestPrefs:

    def test_theme_persists(self, tmp_path, monkeypatch):
        from overcode import settings
        monkeypatch.setattr(settings, "get_tui_preferences_path", lambda s: tmp_path / "p.json")
        p = settings.TUIPreferences.load("x")
        assert p.theme == "dark"
        p.theme = "light"
        p.save("x")
        assert settings.TUIPreferences.load("x").theme == "light"


class TestSupervisorTUIPilot:  # name opts into conftest state isolation

    @pytest.mark.asyncio
    async def test_toggle_flips_theme_prefs_and_filters(self):
        from overcode.tui import SupervisorTUI

        app = SupervisorTUI(tmux_session="test-pilot")
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.ui_theme == "dark"
            assert app.theme == "textual-dark"
            assert not any(isinstance(f, LightThemeFilter) for f in app.get_line_filters())

            await pilot.press("Y")
            await pilot.pause()
            assert app.ui_theme == "light"
            assert app._prefs.theme == "light"
            assert app.theme == "textual-light"
            assert isinstance(app.get_line_filters()[0], LightThemeFilter)

            await pilot.press("Y")
            await pilot.pause()
            assert app.ui_theme == "dark"
            assert app._prefs.theme == "dark"
            assert app.theme == "textual-dark"

    def test_palette_offers_theme_with_its_states(self):
        from overcode.command_palette import COMMANDS

        cmd = next(c for c in COMMANDS if c.action == "toggle_theme")
        assert cmd.category == "Display"

        class _App:
            ui_theme = "light"
        view = cmd.state(_App())
        assert view.options == ("dark", "light")
        assert view.current_label == "light"
