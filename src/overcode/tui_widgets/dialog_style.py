"""
Shared look for the TUI's dialogs (#488).

Every dialog follows the command palette (#482): a rounded accent border
with the title set into it and the keys in the bottom edge, full-width
rows with the selected one on a blue bar behind a ▌ marker, section
headers ruled off in capitals, choices drawn as a row of options with the
current one lit, and a tip line under a rule at the foot that explains
the highlighted row.

This module holds the colours and the Text helpers the dialogs build
their rows from; ModalBase does the sizing, centring and border text.
"""

from __future__ import annotations

from typing import Optional, Sequence

from rich.cells import cell_len
from rich.text import Text


# Colours tuned against the TUI's dark surface; the selected-row blue is
# the same one the jobs list uses.
SEL_BG = "#2d4a5a"
ACCENT = "#5fafd7"
KEY = "bold #ffaf5f"                    # keys in a list
KEYCAP = "bold #101010 on #ffaf5f"      # a key in a tip line
STATE_ON = "bold #87d787"               # a toggle that is on
STATE_CUR = "bold #5fafd7"              # the current step of a cycle
STATE_OTHER = "#626262"
MATCH = "bold #ffd75f"
MUTED = "#8a8a8a"
WARN = "#ffaf5f"
ERROR = "#ff5f5f"
TEXT = "#d0d0d0"

CHECK_ON = "✓"
CHECK_OFF = "·"
CHECK_MIXED = "–"


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------

def item(selected: bool) -> Text:
    """Start a list row: the ▌ marker, on the selection bar when selected.
    Finish it with finish() so the bar runs the full width."""
    line = Text(style=f"on {SEL_BG}" if selected else "")
    line.append("▌" if selected else " ", style=f"bold {ACCENT}")
    return line


def finish(line: Text, width: int) -> Text:
    """Fit a row to exactly `width` cells."""
    return pad(fit(line, width), width)


def section(title: str, width: int, selected: bool = False, lead: Optional[Text] = None) -> Text:
    """A section header — ` TITLE ────` — optionally selectable (the
    column configurator toggles a whole group from its header)."""
    line = item(selected) if selected or lead is not None else Text(" ")
    if lead is not None:
        line.append_text(lead)
        line.append(" ")
    line.append(title.upper() + " ", style=f"bold {TEXT}" if selected else f"bold {MUTED}")
    line.append("─" * max(0, width - cell_len(line.plain)), style=STATE_OTHER)
    return finish(line, width)


def rule(width: int) -> Text:
    return Text("─" * width, style=MUTED)


def check(on: Optional[bool], locked: bool = False) -> Text:
    """✓ on, · off, – mixed (None); dimmed when it can't be changed."""
    if on is None:
        return Text(CHECK_MIXED, style=f"bold {WARN}")
    if locked:
        return Text(CHECK_ON if on else CHECK_OFF, style=STATE_OTHER)
    return Text(CHECK_ON, style=STATE_ON) if on else Text(CHECK_OFF, style=STATE_OTHER)


def options(opts: Sequence[str], current: Optional[str], width: Optional[int] = None) -> Text:
    """A choice drawn as its options with the current one lit — the
    palette's state display. Collapses to the current option and its
    position when the row would not fit in `width`."""
    full = Text()
    for i, opt in enumerate(opts):
        if i:
            full.append("  ")
        full.append(opt, style=option_style(opts, opt == current, i))
    if width is None or cell_len(full.plain) <= width or current not in opts:
        return full
    i = list(opts).index(current)
    short = Text(current, style=option_style(opts, True, i))
    short.append(f"  {i + 1} of {len(opts)} · space cycles", style=STATE_OTHER)
    return short


def option_style(opts: Sequence[str], is_current: bool, index: int) -> str:
    if not is_current:
        return STATE_OTHER
    if tuple(opts) == ("off", "on"):
        return STATE_ON if index == 1 else f"bold {TEXT}"
    return STATE_CUR


def text_value(value: str, cursor: Optional[int] = None, placeholder: str = "(none)") -> Text:
    """A text field's value; with a block cursor at `cursor` when editing."""
    if cursor is None:
        return Text(value) if value else Text(placeholder, style=f"italic {STATE_OTHER}")
    pos = min(cursor, len(value))
    t = Text(value[:pos], style="bold")
    if pos < len(value):
        t.append(value[pos], style=f"bold #101010 on {ACCENT}")
        t.append(value[pos + 1:], style="bold")
    else:
        t.append(" ", style=f"on {ACCENT}")
    return t


def tip(width: int, left: Text, right: Optional[Text] = None) -> Text:
    """The foot of a dialog: a rule and one tip line."""
    out = rule(width)
    out.append("\n")
    if right is not None and right.plain:
        out.append_text(spread(fit(left, width - cell_len(right.plain) - 2), right, width))
    else:
        out.append_text(finish(left, width))
    return out


def hints(*pairs: tuple) -> str:
    """Border-subtitle key hints: hints(("↵", "run"), ("esc", "close"))."""
    return " · ".join(f"{k} {what}" for k, what in pairs)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def pad(text: Text, width: int) -> Text:
    gap = width - cell_len(text.plain)
    if gap > 0:
        text.append(" " * gap)
    return text


def fit(text: Text, width: int) -> Text:
    """Truncate to `width` cells with an ellipsis, then pad to it."""
    if cell_len(text.plain) > width:
        text = text.copy()
        text.truncate(max(0, width - 1), overflow="crop")
        text.append("…", style=MUTED)
    return pad(text, width)


def spread(left: Text, right: Text, width: int) -> Text:
    """left, then right flush against the right edge."""
    gap = width - cell_len(left.plain) - cell_len(right.plain)
    if gap < 1:
        left = fit(left, max(0, width - cell_len(right.plain) - 1))
        gap = 1
    left.append(" " * gap)
    left.append_text(right)
    return left


def highlight(s: str, positions: Sequence[int], style: str) -> Text:
    text = Text(s, style=style)
    for p in positions:
        if 0 <= p < len(s):
            text.stylize(MATCH, p, p + 1)
    return text


def keycaps(keys: Sequence[str]) -> Text:
    """Keys drawn as caps — for a tip line, where they stand out."""
    text = Text()
    for i, k in enumerate(keys):
        if i:
            text.append(" or ", style=MUTED)
        text.append(f" {k} ", style=KEYCAP)
    return text


def wrap(s: str, width: int, lines: int, style: str = "") -> list:
    """`s` word-wrapped to `width`, exactly `lines` Text lines long."""
    import textwrap
    out = textwrap.wrap(s, max(10, width), max_lines=lines, placeholder="…") if s else []
    out += [""] * (lines - len(out))
    return [pad(Text(x, style=style), width) for x in out]
