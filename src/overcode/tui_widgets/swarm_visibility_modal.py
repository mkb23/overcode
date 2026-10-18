"""
Child and subagent visibility dialog (palette: "Subagent visibility…").

One place for what the table shows of the work agents hand out: which
agents' subagents open by themselves (the f rule), what a finished
subagent shows as (its row and ✅/❌ count, only the ❌ ones, or nothing),
how long a finished subagent keeps its own row (config.yaml
``observability.finished_row_seconds``) and whether finished overcode
child agents show (D). Each change applies at once; Esc closes.
"""

from typing import Any, List, Optional, Sequence, Tuple

from rich.text import Text
from textual import events
from textual.message import Message

from . import dialog_style as ds
from .modal_base import ModalBase

# (setting, label, [(value, shown as)], tip for the selected row)
_ROWS: List[Tuple[str, str, Sequence[Tuple[Any, str]], str]] = [
    ("swarm_detail", "Open by itself",
     (("off", "off"), ("groups", "with work"), ("needs_you", "needs you")),
     "Whose subagents open as rows without → (same as f)"),
    ("swarm_finished", "Finished subagents",
     (("show", "show"), ("failed", "failures only"), ("hide", "hide")),
     "Their row and ✅/❌ count; only the ❌ ones; or live work only"),
    ("finished_row_seconds", "Finished row stays",
     ((0.0, "0s"), (10.0, "10s"), (30.0, "30s"), (120.0, "2m")),
     "Then only the count shows · saved in ~/.overcode/config.yaml"),
    ("show_done", "Done child agents",
     ((False, "hidden"), (True, "shown")),
     "Finished overcode child agents (same as D; resets on restart)"),
]


class SwarmVisibilityModal(ModalBase):
    """space / → next option, ← previous; j/k move; esc closes."""

    class Changed(Message):
        """One setting moved to a new value."""

        def __init__(self, setting: str, value: Any) -> None:
            super().__init__()
            self.setting = setting
            self.value = value

    class Closed(Message):
        """The dialog closed (its changes were applied as they were made)."""

    TITLE = "Child & subagent visibility"
    WIDTH = 78
    _LABEL_W = 22

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._values: dict = {}

    def hints(self) -> str:
        return ds.hints(("space", "change"), ("esc", "close"))

    def _current(self, setting: str, choices: Sequence[Tuple[Any, str]]) -> int:
        value = self._values.get(setting)
        for i, (v, _) in enumerate(choices):
            if v == value:
                return i
        return -1

    def render(self) -> Text:
        w = self.inner_width
        text = Text(no_wrap=True, overflow="crop")
        for i, (setting, label, choices, _) in enumerate(_ROWS):
            line = ds.item(i == self.selected_index)
            line.append(f" {label:<{self._LABEL_W - 1}}", style="bold" if i == self.selected_index
                        else ds.TEXT)
            j = self._current(setting, choices)
            shown = [s for _, s in choices]
            current = shown[j] if j >= 0 else None
            # A value set by hand in config.yaml that is not one of the choices
            if j < 0 and isinstance(self._values.get(setting), (int, float)):
                shown = shown + [f"{self._values.get(setting):g}s"]
                current = shown[-1]
            line.append_text(ds.options(shown, current, w - 1 - self._LABEL_W))
            text.append_text(ds.finish(line, w))
            text.append("\n")
        tip = _ROWS[self.selected_index][3] if 0 <= self.selected_index < len(_ROWS) else ""
        text.append_text(ds.tip(w, Text(tip, style=ds.MUTED)))
        return text

    def on_key(self, event: events.Key) -> None:
        if self._navigate(event, len(_ROWS)):
            return
        key = event.key
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

    def _step(self, direction: int) -> None:
        setting, _, choices, _ = _ROWS[self.selected_index]
        j = self._current(setting, choices)
        j = (j + direction) % len(choices) if j >= 0 else (0 if direction > 0 else len(choices) - 1)
        value = choices[j][0]
        self._values[setting] = value
        self.post_message(self.Changed(setting, value))
        self.refresh()

    def show(self, values: dict, app_ref: Optional[Any] = None) -> None:
        """Open on the current values ({setting: value})."""
        self._values = dict(values)
        self._save_focus(app_ref)
        self._show(self.selected_index if 0 <= self.selected_index < len(_ROWS) else 0)
