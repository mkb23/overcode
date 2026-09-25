"""
Rename-agent modal (Ctrl+N, #478).

A small form in the new-agent modal's style: the new name is edited inline
from the moment the dialog opens (pre-filled with the current name), and a
force toggle covers renaming a busy agent. The name is validated as it is
typed, so Enter only ever submits a name ``overcode rename`` would accept;
the rename itself runs in the app (it stops and resumes the agent).

Keys:
    printable chars  insert at cursor        Tab / ↑ / ↓  switch field
    Backspace / Del  delete                  Space        toggle force
    ← / → Home End   move cursor             Enter        rename
    Esc              cancel
"""

from __future__ import annotations

import re
from typing import Any, Optional

from rich.text import Text
from textual import events
from textual.message import Message

from ..exceptions import InvalidSessionNameError
from . import dialog_style as ds
from .modal_base import ModalBase


class RenameAgentModal(ModalBase):
    """Keyboard-driven form for renaming the focused agent."""

    LABEL_W = 12  # matches NewAgentModal's label column

    class RenameRequested(Message):
        """Emitted on Enter with a valid new name."""

        def __init__(self, session_id: str, old_name: str, new_name: str, force: bool) -> None:
            super().__init__()
            self.session_id = session_id
            self.old_name = old_name
            self.new_name = new_name
            self.force = force

    class Cancelled(Message):
        pass

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.session_id: str = ""
        self.old_name: str = ""
        self.value: str = ""
        self.cursor: int = 0
        self.force: bool = False
        self._taken: set[str] = set()

    # ── public api ───────────────────────────────────────────────────────

    def show(  # type: ignore[override]
        self,
        *,
        session_id: str,
        name: str,
        existing_names: set[str],
        app_ref: Optional[Any] = None,
    ) -> None:
        """Open for the agent ``name``; ``existing_names`` are all agents' names."""
        self.session_id = session_id
        self.old_name = name
        self.value = name
        self.cursor = len(name)
        self.force = False
        self._taken = set(existing_names) - {name}
        self._save_focus(app_ref)
        self._show()  # resets selected_index to 0: the name field

    def error(self) -> Optional[str]:
        """Why the typed name cannot be used, or None when it can."""
        name = self.value
        if not name:
            return "enter a name"
        if name == self.old_name:
            return "that is its current name"
        if not re.match(InvalidSessionNameError.VALID_PATTERN, name):
            return "letters, digits, - and _ only (max 64)"
        from ..launcher import AgentLauncher
        if name in AgentLauncher.RESERVED_AGENT_NAMES:
            return f"'{name}' is reserved by overcode"
        if name in self._taken:
            return f"an agent named '{name}' already exists"
        return None

    # ── render ───────────────────────────────────────────────────────────

    TITLE = "Rename agent"
    WIDTH = 72

    def hints(self) -> str:
        return ds.hints(("↵", "rename"), ("tab", "field"), ("esc", "cancel"))

    def render(self) -> Text:
        w = self.inner_width
        t = Text(no_wrap=True, overflow="crop")

        line = Text(" ")
        line.append(f"{'Agent':<{self.LABEL_W}}", style=ds.MUTED)
        line.append(self.old_name, style=ds.MUTED)
        t.append_text(ds.finish(line, w))
        t.append("\n")

        on_name = self.selected_index == 0
        line = ds.item(on_name)
        line.append(f"{'New name':<{self.LABEL_W}}", style="bold" if on_name else ds.TEXT)
        line.append_text(ds.text_value(self.value, self.cursor if on_name else None))
        t.append_text(ds.finish(line, w))
        t.append("\n")

        on_force = self.selected_index == 1
        line = ds.item(on_force)
        line.append(f"{'Force':<{self.LABEL_W}}", style="bold" if on_force else ds.TEXT)
        line.append_text(ds.options(("off", "on"), "on" if self.force else "off"))
        t.append_text(ds.finish(line, w))
        t.append("\n")

        problem = self.error()
        if problem:
            note = Text(problem, style=ds.ERROR)
        elif self.force:
            note = Text("restarts it even mid-turn — the turn is cancelled", style=ds.WARN)
        else:
            note = Text("a busy agent is refused unless force is on", style=ds.MUTED)
        t.append_text(ds.tip(w, note))
        t.append("\n")
        t.append_text(ds.finish(Text("the old name keeps working as an alias", style=ds.MUTED), w))
        return t

    # ── keys ─────────────────────────────────────────────────────────────

    def on_key(self, event: events.Key) -> None:
        key = event.key
        event.stop()  # a modal swallows every key

        if key == "escape":
            self.post_message(self.Cancelled())
            self._hide()
        elif key == "enter":
            self._submit()
        elif key in ("tab", "shift+tab", "up", "down"):
            self.selected_index = 1 - self.selected_index
        elif self.selected_index == 1:
            if key in ("space", "left", "right"):
                self.force = not self.force
        else:
            self._edit(key, event.character)
        self.refresh()

    def _edit(self, key: str, character: Optional[str]) -> None:
        val, pos = self.value, self.cursor
        if key == "backspace":
            if pos > 0:
                self.value, self.cursor = val[:pos - 1] + val[pos:], pos - 1
        elif key == "delete":
            self.value = val[:pos] + val[pos + 1:]
        elif key == "left":
            self.cursor = max(0, pos - 1)
        elif key == "right":
            self.cursor = min(len(val), pos + 1)
        elif key == "home":
            self.cursor = 0
        elif key == "end":
            self.cursor = len(val)
        elif character and character.isprintable():
            self.value, self.cursor = val[:pos] + character + val[pos:], pos + 1

    def _submit(self) -> None:
        if self.error() is not None:
            return  # the red line already says why
        self.post_message(self.RenameRequested(
            self.session_id, self.old_name, self.value, self.force,
        ))
        self._hide()
