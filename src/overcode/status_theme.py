"""Status colour themes: which glyph each light shows.

The lights are status_constants.LIGHT_LEGEND's rows, named by their
``key``. A theme gives each light a glyph from GLYPHS, and the glyph
decides its text colour (the time-in-state, the timeline, the detail
badges). The emoji-free form stays the light's own (``[W]`` is "needs
input" in any theme): a glyph's letter would say the wrong thing once a
theme moves it to another light.

config.yaml picks the theme:

    status_theme:
      preset: calm            # classic | calm | colorblind | shapes
      overrides:
        input: 🔵             # a light → a glyph (or its name, "blue circle")

``status_theme: calm`` alone is the preset. The engine publishes status
names; glyphs are a view concern, resolved here in the process that draws.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Tuple

from .status_constants import (
    LIGHT_LEGEND,
    STATUS_COLOR_GREEN,
    STATUS_COLOR_ORANGE,
    STATUS_COLOR_RED,
    STATUS_COLOR_REST,
    STATUS_COLOR_YELLOW,
    _safe_emoji,
)

log = logging.getLogger(__name__)

CONFIG_KEY = "status_theme"
DEFAULT_PRESET = "classic"

# Every light, in legend order, and the main ones (not a variant shade):
# two main lights showing the same glyph is the clash the dialog warns of.
LIGHTS: Tuple[str, ...] = tuple(e.key for e in LIGHT_LEGEND)
MAIN_LIGHTS: Tuple[str, ...] = tuple(e.key for e in LIGHT_LEGEND if not e.variant)
LIGHT_NAMES: Dict[str, str] = {e.key: e.name for e in LIGHT_LEGEND}
LIGHT_ASCII: Dict[str, str] = {e.key: e.ascii for e in LIGHT_LEGEND}
_LIGHT_OF_STATUS: Dict[str, str] = {s: e.key for e in LIGHT_LEGEND for s in e.statuses}

# The detail column's colour bucket (StatusDetail.color) → its light
BUCKET_LIGHTS: Dict[str, str] = {
    STATUS_COLOR_GREEN: "working",
    STATUS_COLOR_YELLOW: "automatic",
    STATUS_COLOR_ORANGE: "approval",
    STATUS_COLOR_RED: "input",
    STATUS_COLOR_REST: "rest",
}

# A subagent row's light (swarm_view) → its light
SWARM_LIGHTS: Dict[str, str] = {
    "green": "working", "yellow": "automatic", "orange": "approval", "idle": "rest",
}


@dataclass(frozen=True)
class Glyph:
    colour: str  # a Rich colour: the light's text colour
    name: str    # what the dialog and config.yaml may call it


# The glyphs a light may show. Each emoji is emoji presentation by
# default (two cells everywhere, no VS16); the monochrome shapes are one
# cell and render_status_symbol pads them to two.
GLYPHS: Dict[str, Glyph] = {
    "🔴": Glyph("red", "red circle"),
    "🟠": Glyph("orange1", "orange circle"),
    "🟡": Glyph("yellow", "yellow circle"),
    "🟢": Glyph("green", "green circle"),
    "🔵": Glyph("dodger_blue1", "blue circle"),
    "🟣": Glyph("magenta", "purple circle"),
    "🟤": Glyph("orange4", "brown circle"),
    "⚫": Glyph("dim", "black circle"),
    "⚪": Glyph("grey85", "white circle"),
    "🟥": Glyph("red", "red square"),
    "🟧": Glyph("orange1", "orange square"),
    "🟨": Glyph("yellow", "yellow square"),
    "🟩": Glyph("green", "green square"),
    "🟦": Glyph("dodger_blue1", "blue square"),
    "🟪": Glyph("magenta", "purple square"),
    "🟫": Glyph("orange4", "brown square"),
    "⬛": Glyph("dim", "black square"),
    "⬜": Glyph("grey85", "white square"),
    "🔶": Glyph("orange1", "orange diamond"),
    "🔷": Glyph("dodger_blue1", "blue diamond"),
    "🔸": Glyph("orange1", "small orange diamond"),
    "🔹": Glyph("dodger_blue1", "small blue diamond"),
    "❔": Glyph("dim", "question mark"),
    "💤": Glyph("dim", "zzz"),
    "💚": Glyph("green", "green heart"),
    "💛": Glyph("yellow", "yellow heart"),
    "●": Glyph("default", "dot"),
    "◐": Glyph("default", "half dot"),
    "○": Glyph("dim", "ring"),
    "▲": Glyph("default", "triangle"),
    "■": Glyph("default", "square"),
    "◆": Glyph("default", "diamond"),
    "·": Glyph("dim", "small dot"),
}
_GLYPH_BY_NAME = {g.name: ch for ch, g in GLYPHS.items()}

CLASSIC: Dict[str, str] = {e.key: e.glyph for e in LIGHT_LEGEND}

# The idle badges (idle_stages.py) beside the needs-input light: fresh,
# then drowsy. Themed like a light, from their own glyphs; their
# emoji-free forms are fixed (BADGE_KINDS: "In", "zz").
IDLE_BADGES: Tuple[str, ...] = ("awaiting_input", "drowsy")
IDLE_BADGE_NAMES: Dict[str, str] = {"awaiting_input": "Idle", "drowsy": "Idle a while"}
BADGE_GLYPHS: Dict[str, str] = {
    "📥": "inbox", "😴": "sleepy face", "💬": "speech balloon", "🔕": "muted bell",
    "💭": "thought balloon", "◇": "open diamond", "z": "z",
}
_BADGE_BY_NAME = {name: ch for ch, name in BADGE_GLYPHS.items()}
CLASSIC_BADGES: Dict[str, str] = {"awaiting_input": "📥", "drowsy": "😴"}

PRESETS: Dict[str, Dict[str, str]] = {
    "classic": CLASSIC,
    # Idle is not an alarm: needs input goes neutral blue, and red is kept
    # for something you must answer (a permission, a question, a plan).
    "calm": {**CLASSIC, "input": "🔵", "approval": "🔴"},
    # Blue and orange (Okabe-Ito) stay apart for deuteranopia and
    # protanopia, which fold red, green and often yellow into one hue:
    # blue means nothing to do, orange means you. Shape is the second
    # channel inside each family (diamond = acting or asking now, square
    # = waiting on something), and there is no green, red or purple left
    # to confuse. White, black and ❔ differ in lightness.
    "colorblind": {
        **CLASSIC,
        "working": "🔷", "automatic": "🟦", "approval": "🟧", "input": "🔶",
        "rest": "⬜", "gone": "⬛",
        "heartbeat": "🔹", "heartbeat_waiting": "🟦", "error": "🔸",
    },
    # Monochrome: the shape alone says it, for a mono terminal or no colour cues
    "shapes": {
        **CLASSIC,
        "working": "●", "automatic": "◐", "approval": "▲", "input": "■",
        "rest": "○", "gone": "·",
        "heartbeat": "●", "heartbeat_waiting": "◐", "error": "◆",
    },
}
PRESET_NAMES: Tuple[str, ...] = tuple(PRESETS)
BADGE_PRESETS: Dict[str, Dict[str, str]] = {
    name: CLASSIC_BADGES for name in PRESETS
}
BADGE_PRESETS["shapes"] = {"awaiting_input": "◇", "drowsy": "z"}


@dataclass(frozen=True)
class StatusTheme:
    """A light → glyph map: a preset with the person's overrides on top."""

    preset: str = DEFAULT_PRESET
    overrides: Mapping[str, str] = field(default_factory=dict)

    def glyph(self, light: str) -> str:
        return self.overrides.get(light) or PRESETS[self.preset].get(light) or CLASSIC.get(light, "❔")

    def badge(self, kind: str) -> str:
        """An idle badge's glyph (IDLE_BADGES)."""
        return self.overrides.get(kind) or BADGE_PRESETS[self.preset].get(kind) or CLASSIC_BADGES[kind]

    def colour(self, light: str) -> str:
        g = GLYPHS.get(self.glyph(light))
        return g.colour if g else "dim"

    def light_symbol(self, light: str, emoji_free: bool = False) -> Tuple[str, str]:
        """(glyph as drawn, colour) for a light."""
        if emoji_free:
            return LIGHT_ASCII.get(light, "[?]"), self.colour(light)
        return _safe_emoji(self.glyph(light)), self.colour(light)

    def symbol_for(self, status: str, emoji_free: bool = False) -> Tuple[str, str]:
        """(glyph, colour) for an agent status; an unknown one is Unknown."""
        return self.light_symbol(_LIGHT_OF_STATUS.get(status, "unknown"), emoji_free)

    def colour_for(self, status: str) -> str:
        return self.colour(_LIGHT_OF_STATUS.get(status, "unknown"))

    def glyphs(self) -> Dict[str, str]:
        return {light: self.glyph(light) for light in LIGHTS}

    def clashes(self) -> List[Tuple[str, str]]:
        """Pairs of main lights that look the same (the same glyph)."""
        out = []
        for i, a in enumerate(MAIN_LIGHTS):
            for b in MAIN_LIGHTS[i + 1:]:
                if self.glyph(a) == self.glyph(b):
                    out.append((a, b))
        return out

    def label(self) -> str:
        """The palette's state: the preset, marked when lights are changed."""
        return f"{self.preset}+" if self.overrides else self.preset

    def to_config(self) -> dict:
        section: dict = {"preset": self.preset}
        if self.overrides:
            section["overrides"] = dict(self.overrides)
        return section


CLASSIC_THEME = StatusTheme()


def lookup_glyph(value) -> Optional[str]:
    """A glyph from config: the glyph itself or its name; None if neither."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text in GLYPHS:
        return text
    stripped = text.replace("️", "")
    if stripped in GLYPHS:
        return stripped
    return _GLYPH_BY_NAME.get(text.lower())


def lookup_badge(value) -> Optional[str]:
    """An idle badge's glyph from config: the glyph or its name; None if neither."""
    if not isinstance(value, str):
        return None
    text = value.strip().replace("\ufe0f", "")
    if text in BADGE_GLYPHS:
        return text
    return _BADGE_BY_NAME.get(text.lower())


def resolve(section) -> Tuple[StatusTheme, List[str]]:
    """The theme config.yaml's ``status_theme`` names, and what it got wrong.

    Anything unreadable falls back (an unknown preset to classic, an
    unknown light or glyph is skipped) with a warning; nothing raises.
    """
    warnings: List[str] = []
    if section is None:
        return CLASSIC_THEME, warnings
    if isinstance(section, str):
        section = {"preset": section}
    if not isinstance(section, dict):
        return CLASSIC_THEME, [f"{CONFIG_KEY}: expected a preset name or a mapping"]
    preset = section.get("preset") or DEFAULT_PRESET
    if preset not in PRESETS:
        warnings.append(f"{CONFIG_KEY}: unknown preset {preset!r} "
                        f"(known: {', '.join(PRESET_NAMES)}); using {DEFAULT_PRESET}")
        preset = DEFAULT_PRESET
    overrides: Dict[str, str] = {}
    raw = section.get("overrides") or {}
    if not isinstance(raw, dict):
        warnings.append(f"{CONFIG_KEY}.overrides: expected a mapping of light to glyph")
        raw = {}
    for light, value in raw.items():
        if light in IDLE_BADGES:
            glyph = lookup_badge(value)
            if glyph is None:
                warnings.append(f"{CONFIG_KEY}.overrides.{light}: unknown glyph {value!r}")
            elif glyph != BADGE_PRESETS[preset][light]:
                overrides[light] = glyph
            continue
        if light not in LIGHTS:
            warnings.append(f"{CONFIG_KEY}.overrides: unknown light {light!r} "
                            f"(known: {', '.join(LIGHTS + IDLE_BADGES)})")
            continue
        glyph = lookup_glyph(value)
        if glyph is None:
            warnings.append(f"{CONFIG_KEY}.overrides.{light}: unknown glyph {value!r}")
            continue
        if glyph != PRESETS[preset][light]:
            overrides[light] = glyph
    return StatusTheme(preset, overrides), warnings


_active: Tuple[object, Optional[StatusTheme]] = (object(), None)
_warned: set = set()


def active_theme() -> StatusTheme:
    """The theme config.yaml names now (one stat of config.yaml per call).

    Each warning is logged once per process.
    """
    global _active
    from .config import _cached_config
    section = _cached_config().get(CONFIG_KEY)
    cached_section, theme = _active
    if theme is not None and section is cached_section:
        return theme
    theme, warnings = resolve(section)
    for w in warnings:
        if w not in _warned:
            _warned.add(w)
            log.warning(w)
    _active = (section, theme)
    return theme


def theme_warnings() -> List[str]:
    """What is wrong with config.yaml's status_theme (``overcode config``, the dialog)."""
    from .config import _cached_config
    return resolve(_cached_config().get(CONFIG_KEY))[1]


def save_theme(theme: StatusTheme) -> None:
    """Write ``theme`` to config.yaml (classic with no overrides removes the key)."""
    from .config import load_config, save_config
    config = load_config()
    if theme.preset == DEFAULT_PRESET and not theme.overrides:
        config.pop(CONFIG_KEY, None)
    else:
        config[CONFIG_KEY] = theme.to_config()
    save_config(config)


def next_preset(preset: str) -> str:
    names = PRESET_NAMES
    return names[(names.index(preset) + 1) % len(names)] if preset in names else names[0]


def cycle_glyph(current: str, step: int = 1) -> str:
    """The glyph before or after ``current`` in GLYPHS (the dialog's ←/→)."""
    order = list(GLYPHS)
    i = order.index(current) if current in order else 0
    return order[(i + step) % len(order)]


def cycle_badge(current: str, step: int = 1) -> str:
    """The badge glyph before or after ``current`` (the dialog's ←/→)."""
    order = list(BADGE_GLYPHS)
    i = order.index(current) if current in order else 0
    return order[(i + step) % len(order)]


def sample_line(theme: StatusTheme, emoji_free: bool = False) -> List[Tuple[str, str]]:
    """A sample fleet's lights, one per main light: (text, style) pieces."""
    out: List[Tuple[str, str]] = []
    for light in MAIN_LIGHTS:
        glyph, colour = theme.light_symbol(light, emoji_free)
        out.append((glyph, colour))
        out.append((f" {LIGHT_NAMES[light].split()[0].lower()}  ", colour))
    return out
