"""The traffic light and Result legends say the same thing everywhere.

status_constants.LIGHT_LEGEND and RESULT_KINDS are the one source: the
help overlay is built from them, and docs/tui-guide.md must show every
glyph they name. The retired glyphs (an eye for oversight, a checked box
for done) must not come back in src/ or docs/.
"""

import unicodedata
from pathlib import Path

import pytest

from overcode.keymap import effective_keymap
from overcode.status_constants import (
    ALL_STATUSES,
    LIGHT_LEGEND,
    RESULT_KINDS,
    STATUS_HEARTBEAT_START,
    STATUS_SYMBOLS,
)
from overcode.tui_widgets.help_overlay import build_help

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
RETIRED = ("\U0001f441", "\U0001f440", "☑")  # eye, eyes, ballot box with check


def _help_text() -> str:
    km = effective_keymap(preset=None, section={}, passthru={}, tmux_toggle_key="Tab")
    return build_help(200, km).plain


def _glyphs():
    return [e.glyph for e in LIGHT_LEGEND] + [spec[0] for spec in RESULT_KINDS.values()]


@pytest.mark.parametrize("glyph", _glyphs())
def test_every_glyph_is_in_the_help_overlay(glyph):
    assert glyph in _help_text()


@pytest.mark.parametrize("glyph", _glyphs())
def test_every_glyph_is_in_the_tui_guide(glyph):
    assert glyph in (ROOT / "docs" / "tui-guide.md").read_text()


@pytest.mark.parametrize("glyph", _glyphs())
def test_every_glyph_is_emoji_presentation_by_default(glyph):
    """Two cells in every terminal: no glyph that needs VS16 to be an emoji."""
    assert len(glyph) == 1 and unicodedata.east_asian_width(glyph) == "W"


def test_the_legend_names_the_light_every_status_shows():
    """Each status is in one legend row, and that row's glyph is what it shows."""
    for status in ALL_STATUSES:
        rows = [e for e in LIGHT_LEGEND if status in e.statuses]
        assert len(rows) == 1, status
        assert STATUS_SYMBOLS[status][0] == rows[0].glyph, status
    assert any(STATUS_HEARTBEAT_START in e.statuses for e in LIGHT_LEGEND)


def test_the_retired_glyphs_are_gone():
    hits = []
    for top in ("src", "docs"):
        for path in (ROOT / top).rglob("*"):
            if path.suffix not in (".py", ".md", ".yaml", ".tcss", ".js", ".ts", ".html"):
                continue
            text = path.read_text(errors="ignore")
            hits += [f"{path.relative_to(ROOT)}: {g}" for g in RETIRED if g in text]
    assert hits == []
