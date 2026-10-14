"""
Billing-code modal: move the focused agent's new spend to another code.

A small form in the rename modal's style. The code is edited inline from
the moment the dialog opens (pre-filled with the agent's current code);
space cycles it through the agent's own name (its default code) and the
``billing.codes`` shortlist from config.yaml, and any other code can be
typed. Enter applies it the way ``overcode rebill`` does: forward only,
so the spend so far stays on the old code. Children already running keep
their codes; children launched from now on take the new one.

Keys:
    printable chars  insert at cursor        Space   next code in the cycle
    Backspace / Del  delete                  Enter   apply
    ← / → Home End   move cursor             Esc     cancel

The cycle helpers here are shared with the new-agent modal's Billing row.
"""

from __future__ import annotations

from typing import Any, List, Optional, Sequence

from rich.text import Text
from textual import events
from textual.message import Message

from . import dialog_style as ds
from .modal_base import ModalBase


def billing_cycle(default: str, shortlist: Sequence[str], current: Optional[str] = None) -> List[str]:
    """The codes space steps through: ``default`` (the agent's name), then
    the shortlist in config order; a ``current`` code that is neither leads,
    so the cycle comes back round to where it started."""
    codes = [default] + [c for c in shortlist if c != default]
    if current and current not in codes:
        codes.insert(0, current)
    return codes


def next_in_cycle(codes: Sequence[str], value: str) -> str:
    """The code after ``value``; a typed code not in the cycle goes to its start."""
    if not codes:
        return value
    try:
        idx = list(codes).index(value)
    except ValueError:
        idx = -1
    return codes[(idx + 1) % len(codes)]


def cycle_labels(codes: Sequence[str], default: str) -> List[str]:
    """How a cycle is drawn: the default code as ``(name)``."""
    return [f"({c})" if c == default else c for c in codes]


class BillingCodeModal(ModalBase):
    """Keyboard-driven form for rebilling the focused agent."""

    LABEL_W = 12  # matches the other agent dialogs' label column

    class RebillRequested(Message):
        """Emitted on Enter with a usable new code."""

        def __init__(self, session_id: str, name: str, old_code: str, new_code: str) -> None:
            super().__init__()
            self.session_id = session_id
            self.name = name
            self.old_code = old_code
            self.new_code = new_code

    class Cancelled(Message):
        pass

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.session_id: str = ""
        self.agent_name: str = ""
        self.old_code: str = ""
        self.value: str = ""
        self.cursor: int = 0
        self.codes: List[str] = []
        self.config_warning: str = ""

    # ── public api ───────────────────────────────────────────────────────

    def show(  # type: ignore[override]
        self,
        *,
        session_id: str,
        name: str,
        code: str,
        shortlist: Sequence[str] = (),
        config_warning: str = "",
        app_ref: Optional[Any] = None,
    ) -> None:
        """Open for the agent ``name`` billed to ``code``."""
        self.session_id = session_id
        self.agent_name = name
        self.old_code = code
        self.value = code
        self.cursor = len(code)
        self.codes = billing_cycle(name, shortlist, current=code)
        self.config_warning = config_warning
        self._save_focus(app_ref)
        self._show()

    def error(self) -> Optional[str]:
        """Why the code cannot be applied, or None when it can."""
        code = self.value.strip()
        if not code:
            return "enter a code (space cycles the shortlist)"
        if code == self.old_code:
            return "that is its current code"
        return None

    # ── render ───────────────────────────────────────────────────────────

    TITLE = "Billing code"
    WIDTH = 80

    def hints(self) -> str:
        return ds.hints(("space", "cycle"), ("↵", "apply"), ("esc", "cancel"))

    def render(self) -> Text:
        w = self.inner_width
        t = Text(no_wrap=True, overflow="crop")

        for label, value in (("Agent", self.agent_name), ("Billed to", self.old_code)):
            line = Text(" ")
            line.append(f"{label:<{self.LABEL_W}}", style=ds.MUTED)
            line.append(value, style=ds.MUTED)
            t.append_text(ds.finish(line, w))
            t.append("\n")

        line = ds.item(True)
        line.append(f"{'New code':<{self.LABEL_W}}", style="bold")
        line.append_text(ds.text_value(self.value, self.cursor))
        t.append_text(ds.finish(line, w))
        t.append("\n")

        line = Text(" ")
        line.append(f"{'Cycle':<{self.LABEL_W}}", style=ds.MUTED)
        labels = cycle_labels(self.codes, self.agent_name)
        current = labels[self.codes.index(self.value)] if self.value in self.codes else None
        line.append_text(ds.options(labels, current, w - 1 - self.LABEL_W))
        t.append_text(ds.finish(line, w))
        t.append("\n")

        problem = self.error()
        if problem:
            note = Text(problem, style=ds.ERROR)
        elif self.config_warning:
            note = Text(self.config_warning, style=ds.WARN)
        else:
            note = Text(f"forward only: its spend so far stays on {self.old_code}", style=ds.MUTED)
        t.append_text(ds.tip(w, note))
        t.append("\n")
        t.append_text(ds.finish(Text(
            "children already running keep their codes; new children take this one",
            style=ds.MUTED), w))
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
        elif key == "space":
            self.value = next_in_cycle(self.codes, self.value.strip())
            self.cursor = len(self.value)
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
        elif character and character.isprintable() and character != " ":
            self.value, self.cursor = val[:pos] + character + val[pos:], pos + 1

    def _submit(self) -> None:
        if self.error() is not None:
            return  # the red line already says why
        self.post_message(self.RebillRequested(
            self.session_id, self.agent_name, self.old_code, self.value.strip(),
        ))
        self._hide()
