"""
Status colours dialog ("Status colours…" in the palette, Settings).

The preset on top, then one row per light and per idle badge (📥 / 😴):
←/→ steps the selected row (a preset, or that row's glyph), r puts a
light back to its preset's glyph (on the preset row: every light). A
sample fleet under the rows shows the theme as it stands, and the tip
warns when two main lights look the same. Each change is saved to
config.yaml ``status_theme`` at once; esc closes.
"""

from typing import Any, Optional

from rich.cells import cell_len
from rich.text import Text
from textual import events
from textual.message import Message

from . import dialog_style as ds
from .modal_base import ModalBase
from ..status_constants import BADGE_KINDS
from ..status_theme import (
    BADGE_GLYPHS, BADGE_PRESETS, GLYPHS, IDLE_BADGE_NAMES, IDLE_BADGES, LIGHT_NAMES, LIGHTS,
    PRESET_NAMES, PRESETS, StatusTheme, cycle_badge, cycle_glyph, sample_line,
)


class StatusColoursModal(ModalBase):
    """←/→ / space change the row; r resets it; j/k move; esc closes."""

    class Changed(Message):
        """The theme changed (the app saves and redraws)."""

        def __init__(self, theme: StatusTheme) -> None:
            super().__init__()
            self.theme = theme

    class Closed(Message):
        """The dialog closed (its changes were saved as they were made)."""

    TITLE = "Status colours"
    WIDTH = 72
    _LABEL_W = 32

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.theme = StatusTheme()
        self.emoji_free = False

    def hints(self) -> str:
        return ds.hints(("←/→", "change"), ("r", "reset"), ("esc", "close"))

    @property
    def _rows(self) -> int:
        return 1 + len(LIGHTS) + len(IDLE_BADGES)

    def _key(self, index: int) -> str:
        """The light or idle badge on row ``index`` (row 0 is the preset)."""
        return (LIGHTS + IDLE_BADGES)[index - 1]

    def _glyph_text(self, light: str) -> Text:
        glyph = self.theme.glyph(light)
        spec = GLYPHS.get(glyph)
        shown = glyph + " " * (2 - cell_len(glyph))
        t = Text()
        if self.emoji_free:
            t.append(self.theme.light_symbol(light, True)[0], style=self.theme.colour(light))
        else:
            t.append(shown)
        t.append(f" {spec.name if spec else glyph}", style=self.theme.colour(light))
        if light in self.theme.overrides:
            t.append("  changed", style=ds.WARN)
        return t

    def render(self) -> Text:
        w = self.inner_width
        text = Text(no_wrap=True, overflow="crop")
        line = ds.item(self.selected_index == 0)
        line.append(f" {'Preset':<{self._LABEL_W - 1}}",
                    style="bold" if self.selected_index == 0 else ds.TEXT)
        line.append_text(ds.options(PRESET_NAMES, self.theme.preset, w - 1 - self._LABEL_W))
        text.append_text(ds.finish(line, w))
        text.append("\n")
        text.append_text(ds.section("Lights", w))
        text.append("\n")
        for i, light in enumerate(LIGHTS, start=1):
            if light == "heartbeat":
                text.append_text(ds.section("Shades", w))
                text.append("\n")
            line = ds.item(i == self.selected_index)
            line.append(f" {LIGHT_NAMES[light]:<{self._LABEL_W - 1}}",
                        style="bold" if i == self.selected_index else ds.TEXT)
            line.append_text(self._glyph_text(light))
            text.append_text(ds.finish(line, w))
            text.append("\n")
        text.append_text(ds.section("Idle badges", w))
        text.append("\n")
        for i, kind in enumerate(IDLE_BADGES, start=1 + len(LIGHTS)):
            line = ds.item(i == self.selected_index)
            line.append(f" {IDLE_BADGE_NAMES[kind]:<{self._LABEL_W - 1}}",
                        style="bold" if i == self.selected_index else ds.TEXT)
            glyph = self.theme.badge(kind)
            shown = BADGE_KINDS[kind][2] if self.emoji_free else glyph + " " * (2 - cell_len(glyph))
            line.append(f"{shown} {BADGE_GLYPHS.get(glyph, glyph)}",
                        style="dim" if kind == "drowsy" else self.theme.colour("input"))
            if kind in self.theme.overrides:
                line.append("  changed", style=ds.WARN)
            text.append_text(ds.finish(line, w))
            text.append("\n")
        text.append_text(ds.section("Sample", w))
        text.append("\n")
        sample = Text(" ")
        for piece, style in sample_line(self.theme, self.emoji_free):
            sample.append(piece, style=style)
        text.append_text(ds.finish(sample, w))
        text.append("\n")
        text.append_text(ds.tip(w, self._tip()))
        return text

    def _tip(self) -> Text:
        clashes = self.theme.clashes()
        if clashes:
            a, b = clashes[0]
            return Text(f"⚠ {LIGHT_NAMES[a]} and {LIGHT_NAMES[b]} look the same", style=ds.WARN)
        if self.selected_index == 0:
            return Text("Presets: classic, calm (idle is blue), colorblind (blue/orange), "
                        "shapes (monochrome)", style=ds.MUTED)
        return Text("Saved in ~/.overcode/config.yaml (status_theme)", style=ds.MUTED)

    def on_key(self, event: events.Key) -> None:
        if self._navigate(event, self._rows):
            return
        key = event.key
        if key in ("space", "enter", "right", "l"):
            self._step(1)
            event.stop()
        elif key in ("left", "h"):
            self._step(-1)
            event.stop()
        elif key == "r":
            self._reset()
            event.stop()
        elif key in ("escape", "q", "Q"):
            self.post_message(self.Closed())
            self._hide()
            event.stop()

    def _set(self, preset: str, overrides: dict) -> None:
        # An override equal to the preset's glyph is no override
        overrides = {k: v for k, v in overrides.items()
                     if v != PRESETS[preset].get(k) and v != BADGE_PRESETS[preset].get(k)}
        self.theme = StatusTheme(preset, overrides)
        self.post_message(self.Changed(self.theme))
        self.refresh()

    def _step(self, direction: int) -> None:
        if self.selected_index == 0:
            names = list(PRESET_NAMES)
            i = names.index(self.theme.preset) if self.theme.preset in names else 0
            self._set(names[(i + direction) % len(names)], dict(self.theme.overrides))
            return
        key = self._key(self.selected_index)
        glyph = (cycle_badge(self.theme.badge(key), direction) if key in IDLE_BADGES
                 else cycle_glyph(self.theme.glyph(key), direction))
        self._set(self.theme.preset, {**self.theme.overrides, key: glyph})

    def _reset(self) -> None:
        if self.selected_index == 0:
            self._set(self.theme.preset, {})
            return
        key = self._key(self.selected_index)
        self._set(self.theme.preset, {k: v for k, v in self.theme.overrides.items() if k != key})

    def show(self, theme: StatusTheme, emoji_free: bool = False, app_ref: Optional[Any] = None) -> None:
        """Open on the active theme."""
        self.theme = theme
        self.emoji_free = emoji_free
        self._save_focus(app_ref)
        self._show(self.selected_index if 0 <= self.selected_index < self._rows else 0)

