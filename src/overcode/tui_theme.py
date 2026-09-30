"""
Light / dark theme for the TUI (#508).

The TUI's colours are Rich style literals written inline across the
widgets and render helpers (and a few hex literals in tui.tcss), all tuned
for a dark surface. Rather than threading a palette object through every
one of them, light mode is a Textual line filter that adapts colours as
lines are drawn:

* a colour that has a named light counterpart in LIGHT_FG / LIGHT_BG
  (the semantic palette below) is swapped for it;
* any other background darker than mid-grey is flipped to its light
  equivalent (same hue, inverted lightness);
* any other foreground is flipped the same way if it is light, then
  darkened until it reads against the background behind it.

Dark mode installs no filter, so it draws exactly what it always has. The
filter also covers what agents print in the preview pane, and any colour
added later, with no per-literal work.
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass, fields
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from rich.color import Color as RichColor
from rich.color_triplet import ColorTriplet
from rich.segment import Segment
from rich.style import Style
from rich.terminal_theme import TerminalTheme
from textual.filter import LineFilter


THEMES: Tuple[str, ...] = ("dark", "light")
DEFAULT_THEME = "dark"

# Textual's own theme for each mode: it drives $background, $surface,
# $panel, $text and friends in tui.tcss.
TEXTUAL_THEMES: Dict[str, str] = {"dark": "textual-dark", "light": "textual-light"}


def normalize_theme(name: Optional[str]) -> str:
    """A stored preference, or anything else, as one of THEMES."""
    return name if name in THEMES else DEFAULT_THEME


def next_theme(name: Optional[str]) -> str:
    return THEMES[(THEMES.index(normalize_theme(name)) + 1) % len(THEMES)]


# ---------------------------------------------------------------------------
# Semantic palette
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Palette:
    """The TUI's named colours. DARK holds the literals the code draws with
    today; LIGHT holds what each becomes on a light surface. Fields ending
    in _bg are backgrounds, the rest foregrounds."""

    row_bg: str                 # agent row (session_summary.py, tui_render.py)
    selected_row_bg: str        # focused / selected agent row (+ tui.tcss)
    sel_bg: str                 # dialog / jobs selection bar (dialog_style.SEL_BG, tui.tcss)
    terminated_bg: str          # killed-agent ghost row (tui.tcss)
    terminated_focus_bg: str
    monitor_bg: str             # "monitor active" header / bar (tui.tcss)
    code_bg: str                # tui.tcss #1c1c1c panel
    monitor: str                # "monitor active" text + rule (tui.tcss)
    text: str                   # dialog_style.TEXT, command palette names
    muted: str                  # dialog_style.MUTED
    state_other: str            # dialog_style.STATE_OTHER, journey LOCKED
    terminated: str             # ghost-row text (tui.tcss)
    accent: str                 # dialog_style.ACCENT, dialog borders
    key: str                    # dialog_style.KEY / WARN
    match: str                  # dialog_style.MATCH
    good: str                   # dialog_style.STATE_ON, journey GOOD
    error: str                  # dialog_style.ERROR
    asleep: str                 # presence "asleep": meant to all but vanish


DARK = Palette(
    row_bg="#0d2137",
    selected_row_bg="#1a3a50",
    sel_bg="#2d4a5a",
    terminated_bg="#1a1a1a",
    terminated_focus_bg="#2a2a2a",
    monitor_bg="#1a4a1a",
    code_bg="#1c1c1c",
    monitor="#44dd44",
    text="#d0d0d0",
    muted="#8a8a8a",
    state_other="#626262",
    terminated="#666666",
    accent="#5fafd7",
    key="#ffaf5f",
    match="#ffd75f",
    good="#87d787",
    error="#ff5f5f",
    asleep="#1a1a2e",
)

LIGHT = Palette(
    row_bg="#e3ebf3",
    selected_row_bg="#b7d1e8",
    sel_bg="#c3dbe8",
    terminated_bg="#d4d4d4",
    terminated_focus_bg="#c6c6c6",
    monitor_bg="#cde8cd",
    code_bg="#e8e8e8",
    monitor="#1b7a1b",
    text="#262626",
    muted="#5a5a5a",
    state_other="#8c8c8c",
    terminated="#7a7a7a",
    accent="#1d6e9e",
    key="#a35200",
    match="#8a6500",
    good="#2e7d32",
    error="#c62828",
    asleep="#b4b4c4",
)

PALETTES: Dict[str, Palette] = {"dark": DARK, "light": LIGHT}


def _override_maps() -> Tuple[Dict[Tuple[int, int, int], Tuple[int, int, int]], ...]:
    fg: Dict[Tuple[int, int, int], Tuple[int, int, int]] = {}
    bg: Dict[Tuple[int, int, int], Tuple[int, int, int]] = {}
    for f in fields(Palette):
        src = RichColor.parse(getattr(DARK, f.name)).get_truecolor()
        dst = RichColor.parse(getattr(LIGHT, f.name)).get_truecolor()
        (bg if f.name.endswith("_bg") else fg)[tuple(src)] = tuple(dst)
    return fg, bg


_LIGHT_FG, _LIGHT_BG = _override_maps()

# How ANSI colour names ("white", "yellow", "bright_cyan") resolve on a light
# surface before adapting: Textual's Alabaster theme. Its white and
# bright_white are near-white, which the filter then flips to near-black.
LIGHT_ANSI = TerminalTheme(
    (247, 247, 247),
    (0, 0, 0),
    [
        (0, 0, 0), (170, 55, 49), (68, 140, 39), (203, 144, 0),
        (50, 92, 192), (122, 62, 157), (0, 131, 178), (247, 247, 247),
    ],
    [
        (119, 119, 119), (240, 80, 80), (96, 203, 0), (255, 188, 93),
        (0, 122, 204), (230, 76, 230), (0, 170, 203), (247, 247, 247),
    ],
)

# Minimum WCAG contrast a foreground is darkened to on a light background.
MIN_CONTRAST = 4.0
# A background darker than this (HLS lightness) is treated as a dark-theme
# surface and flipped.
DARK_BG_LIGHTNESS = 0.4
# A foreground lighter than this is flipped before the contrast pass.
LIGHT_FG_LIGHTNESS = 0.6


# ---------------------------------------------------------------------------
# Colour maths
# ---------------------------------------------------------------------------

RGB = Tuple[int, int, int]


def _luminance(rgb: RGB) -> float:
    def ch(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a: RGB, b: RGB) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _hls(rgb: RGB) -> Tuple[float, float, float]:
    return colorsys.rgb_to_hls(*(c / 255 for c in rgb))


def _rgb(h: float, l: float, s: float) -> RGB:
    r, g, b = colorsys.hls_to_rgb(h, max(0.0, min(1.0, l)), s)
    return (round(r * 255), round(g * 255), round(b * 255))


def light_bg(rgb: RGB) -> RGB:
    """A background adapted for the light theme."""
    if rgb in _LIGHT_BG:
        return _LIGHT_BG[rgb]
    h, l, s = _hls(rgb)
    if l < DARK_BG_LIGHTNESS:
        return _rgb(h, 1.0 - l, s)
    return rgb


def light_fg(rgb: RGB, bg: RGB) -> RGB:
    """A foreground adapted for the light theme, against background `bg`."""
    if rgb in _LIGHT_FG:
        return _LIGHT_FG[rgb]
    h, l, s = _hls(rgb)
    if _hls(bg)[1] < 0.5:
        return rgb  # still a dark background: leave it be
    if l > LIGHT_FG_LIGHTNESS:
        l = 1.0 - l
    out = _rgb(h, l, s)
    while contrast(out, bg) < MIN_CONTRAST and l > 0.0:
        l -= 0.04
        out = _rgb(h, l, s)
    return out


# ---------------------------------------------------------------------------
# The filter
# ---------------------------------------------------------------------------

def _truecolor(color: RichColor, foreground: bool) -> RGB:
    return tuple(color.get_truecolor(LIGHT_ANSI, foreground=foreground))  # type: ignore[return-value]


@lru_cache(4096)
def light_style(style: Style, background: RichColor) -> Style:
    """`style` adapted for the light theme; `background` is what is behind
    it when the style sets no background of its own."""
    color, bgcolor = style.color, style.bgcolor
    if color is None and bgcolor is None:
        return style
    if color is not None and color.is_default and (bgcolor is None or bgcolor.is_default):
        return style
    bg_rgb = (light_bg(_truecolor(bgcolor, False)) if bgcolor is not None and not bgcolor.is_default
              else _truecolor(background, False))
    new_bg = RichColor.from_triplet(ColorTriplet(*bg_rgb)) if bgcolor is not None and not bgcolor.is_default else None
    new_fg = None
    if color is not None and not color.is_default:
        new_fg = RichColor.from_triplet(ColorTriplet(*light_fg(_truecolor(color, True), bg_rgb)))
    return style + Style.from_color(new_fg, new_bg)


class LightThemeFilter(LineFilter):
    """Adapts dark-tuned colours for a light surface (see module docstring).

    It runs ahead of Textual's own ANSI-to-truecolour filter, so `dim` is
    still resolved afterwards against the (now light) background."""

    def apply(self, segments: List[Segment], background) -> List[Segment]:
        bg = background.rich_color
        _style = light_style
        return [
            Segment(text, None if style is None else _style(style, bg), control)
            for text, style, control in segments
        ]
