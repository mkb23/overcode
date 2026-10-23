"""
Auto-naming settings dialog ("Auto-naming settings…" in the palette, #547).

The namer's switches and timings in one place: on or off, when an agent
gets its first name, how much work between checks that the name still
fits, a cap on auto renames, the daily cost cap (a number you type), and
whether Claude's own session title follows. Each change is saved to
config.yaml (``naming:``) at once and the engine reads it on its next
tick; the line at the top says what the settings add up to. Esc closes.
"""

from typing import Any, Callable, List, Optional, Sequence, Tuple

from rich.text import Text
from textual import events
from textual.message import Message

from . import dialog_style as ds
from .modal_base import ModalBase


def _minutes(value: Any) -> str:
    if not value:
        return "0"
    value = float(value)
    return f"{value / 60:g}h" if value >= 60 and value % 60 == 0 else f"{value:g}m"


def _money(value: Any) -> str:
    return f"${float(value):.2f} a day"


def parse_dollars(text: str) -> Optional[float]:
    """Dollars from what was typed ("10", "2.50", "$5"); None if not a number >= 0."""
    try:
        value = float(text.strip().lstrip("$").strip())
    except ValueError:
        return None
    return value if value >= 0 and value == value and value != float("inf") else None


# (setting, label, [(value, shown as)] or None for a number typed in, how a
# value shows when not one of the choices, tip). Settings are naming.<key>;
# first_after / rename_every are their .minutes.
_ROWS: List[Tuple[str, str, Sequence[Tuple[Any, str]], Callable[[Any], str], str]] = [
    ("enabled", "Auto-naming",
     ((True, "on"), (False, "off")), str,
     "The engine names agents with default or auto names; names people or "
     "parents chose are never touched · naming.enabled"),
    ("first_after", "First name after",
     ((0.0, "at once"), (2.0, "2m"), (5.0, "5m"), (10.0, "10m"), (15.0, "15m"), (30.0, "30m")),
     _minutes,
     "How long after an agent's first prompt it gets its first name · "
     "naming.first_after.minutes"),
    ("rename_every", "Re-check every",
     ((0.0, "never"), (10.0, "10m"), (15.0, "15m"), (30.0, "30m"), (60.0, "1h"), (120.0, "2h")),
     _minutes,
     "Then, per this much work, ask whether the name still fits; it changes only "
     "when the work has moved on · naming.rename_every.minutes"),
    ("max_renames", "Auto renames",
     ((None, "no cap"), (1, "1"), (2, "2"), (3, "3"), (5, "5"), (10, "10")), str,
     "The most auto renames one agent gets · naming.max_renames"),
    ("cost_cap_per_day", "Cost cap", None, _money,
     "USD a day for the namer's model calls; past it, naming waits for tomorrow · "
     "↵ to type an amount · naming.cost_cap_per_day"),
    ("cli_title", "Claude title follows",
     ((True, "on"), (False, "off")), str,
     "Type /rename into a Claude agent when its name changes, so Claude shows "
     "the same name · naming.cli_title"),
]


class AutoNamingModal(ModalBase):
    """space / → next option, ← previous; j/k move; esc closes."""

    class Changed(Message):
        """One setting moved to a new value."""

        def __init__(self, setting: str, value: Any) -> None:
            super().__init__()
            self.setting = setting
            self.value = value

    class Closed(Message):
        """The dialog closed (its changes were saved as they were made)."""

    TITLE = "Auto-naming settings"
    WIDTH = 78
    _LABEL_W = 22
    _SUMMARY_LINES = 3
    _TIP_LINES = 2

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._values: dict = {}
        self.editing = False   # typing into a number row
        self.buffer = ""
        self.cursor = 0
        self._error = ""

    def hints(self) -> str:
        if self.editing:
            return ds.hints(("↵", "save"), ("esc", "undo edit"))
        return ds.hints(("space", "change"), ("esc", "close"))

    def _current(self, setting: str, choices: Sequence[Tuple[Any, str]]) -> int:
        value = self._values.get(setting)
        for i, (v, _) in enumerate(choices):
            if v == value:  # each row's choices share one type
                return i
        return -1

    def summary(self) -> str:
        """What the settings add up to (describe_auto_naming of these values)."""
        from ..config import describe_auto_naming
        v = self._values
        return describe_auto_naming({
            "enabled": v.get("enabled"),
            "first_after": {"minutes": v.get("first_after") or 0},
            "rename_every": {"minutes": v.get("rename_every") or 0},
            "max_renames": v.get("max_renames"),
        })

    def render(self) -> Text:
        w = self.inner_width
        text = Text(no_wrap=True, overflow="crop")
        for line in ds.wrap("Auto-naming " + self.summary(), w, self._SUMMARY_LINES, ds.MUTED):
            text.append_text(line)
            text.append("\n")
        text.append_text(ds.section("Naming", w))
        text.append("\n")
        for i, (setting, label, choices, fmt, _) in enumerate(_ROWS):
            line = ds.item(i == self.selected_index)
            line.append(f" {label:<{self._LABEL_W - 1}}", style="bold" if i == self.selected_index
                        else ds.TEXT)
            if choices is None:  # a number typed in
                if self.editing and i == self.selected_index:
                    line.append("$", style="bold")
                    line.append_text(ds.text_value(self.buffer, self.cursor))
                    line.append(" a day", style=ds.MUTED)
                else:
                    line.append(fmt(self._values.get(setting, 0)))
                text.append_text(ds.finish(line, w))
                text.append("\n")
                continue
            j = self._current(setting, choices)
            shown = [s for _, s in choices]
            current = shown[j] if j >= 0 else None
            if j < 0 and setting in self._values:  # set by hand in config.yaml
                shown = shown + [fmt(self._values[setting])]
                current = shown[-1]
            line.append_text(ds.options(shown, current, w - 1 - self._LABEL_W))
            text.append_text(ds.finish(line, w))
            text.append("\n")
        tip = _ROWS[self.selected_index][4] if 0 <= self.selected_index < len(_ROWS) else ""
        if self.editing:
            tip = self._error or "type dollars a day, e.g. 10 or 2.50 · ↵ save · esc undo"
        text.append_text(ds.rule(w))
        for line in ds.wrap(tip, w, self._TIP_LINES, ds.MUTED):
            text.append("\n")
            text.append_text(line)
        return text

    def on_key(self, event: events.Key) -> None:
        if self.editing:
            self._edit_key(event.key, event.character)
            event.stop()
            self.update_frame()
            self.refresh()
            return
        if self._navigate(event, len(_ROWS)):
            return
        key = event.key
        if _ROWS[self.selected_index][2] is None:  # a number row
            if key in ("space", "enter", "right", "l"):
                self._start_edit()
                event.stop()
                return
            if key in ("left", "h"):
                event.stop()
                return
        if key in ("space", "enter", "right", "l"):
            self._step(1)
            event.stop()
        elif key in ("left", "h"):
            self._step(-1)
            event.stop()
        elif key in ("escape", "q", "Q"):
            self.post_message(self.Closed())
            self._hide()
            event.stop()

    def _start_edit(self) -> None:
        value = self._values.get(_ROWS[self.selected_index][0])
        self.buffer = f"{float(value):.2f}" if isinstance(value, (int, float)) else ""
        self.cursor = len(self.buffer)
        self._error = ""
        self.editing = True
        self.update_frame()
        self.refresh()

    def _edit_key(self, key: str, character: Optional[str]) -> None:
        val, pos = self.buffer, self.cursor
        if key == "enter":
            value = parse_dollars(val)
            if value is None:
                self._error = f"not an amount of dollars: {val!r} · esc undo"
                return
            setting = _ROWS[self.selected_index][0]
            self._values[setting] = value
            self.editing = False
            self.post_message(self.Changed(setting, value))
        elif key == "escape":
            self.editing = False
        elif key == "backspace":
            if pos > 0:
                self.buffer, self.cursor = val[:pos - 1] + val[pos:], pos - 1
        elif key == "delete":
            self.buffer = val[:pos] + val[pos + 1:]
        elif key == "left":
            self.cursor = max(0, pos - 1)
        elif key == "right":
            self.cursor = min(len(val), pos + 1)
        elif key == "home":
            self.cursor = 0
        elif key == "end":
            self.cursor = len(val)
        elif character and (character.isdigit() or character in ".$"):
            self.buffer, self.cursor = val[:pos] + character + val[pos:], pos + 1
            self._error = ""

    def _step(self, direction: int) -> None:
        setting, _, choices, _, _ = _ROWS[self.selected_index]
        j = self._current(setting, choices)
        j = (j + direction) % len(choices) if j >= 0 else (0 if direction > 0 else len(choices) - 1)
        value = choices[j][0]
        self._values[setting] = value
        self.post_message(self.Changed(setting, value))
        self.refresh()

    def show(self, values: dict, app_ref: Optional[Any] = None) -> None:
        """Open on the current values ({setting: value}, as get_naming_config reads them)."""
        self._values = dict(values)
        self.editing = False
        self._save_focus(app_ref)
        self._show(self.selected_index if 0 <= self.selected_index < len(_ROWS) else 0)


def values_from_config(cfg: dict) -> dict:
    """The dialog's values from get_naming_config()."""
    from ..config import NAMING_COST_CAP_DEFAULT
    return {
        "enabled": bool(cfg.get("enabled")),
        "first_after": float((cfg.get("first_after") or {}).get("minutes", 0) or 0),
        "rename_every": float((cfg.get("rename_every") or {}).get("minutes", 0) or 0),
        "max_renames": cfg.get("max_renames"),
        "cost_cap_per_day": float(cfg.get("cost_cap_per_day", NAMING_COST_CAP_DEFAULT) or 0),
        "cli_title": cfg.get("cli_title") is not False,
    }
