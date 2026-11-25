"""
Typing guard (#560): notice prose typed into the TUI pane by mistake.

Most of overcode's keys are bare printable characters, so a person who
forgets the monitor pane has focus and types a sentence into it fires a
key per character: settings flip, dialogs open and swallow the rest, and
a few keys act outside the TUI (quit, launch, send a choice to an agent).

This module is the pure half: it watches keystrokes and says when a burst
of them is typing rather than a run of commands. It knows nothing about
Textual; the TUI side (tui_actions/typing_guard.py) feeds it every key and
acts on what it says. The clock is passed in, so tests drive it with
plain numbers.

A **run** is consecutive keystrokes that are printable characters, space,
Enter or Backspace, each within ``RUN_GAP`` of the one before. A run is
**typing** once it has ``MIN_KEYS`` keys of which ``MIN_DISTINCT`` differ,
so a held key, ``jjjj`` or ``x x`` never is.

Keys count in the agent list, the jobs list and the help overlay, and in a
dialog or command bar that a key of the same run *other than its first*
opened. A dialog the run's first key opened (``i`` then an instruction)
is a deliberate flow: the run ends there and what is typed into the
dialog is not counted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

# A keystroke closer than this to the one before continues the run.
RUN_GAP = 0.6
# An irreversible one-shot whose letter or punctuation key starts a run
# waits this long for a second typing key before it runs.
START_SETTLE = 0.25
# Once a run is typing, keys are swallowed until this long passes quietly.
RECOVER_QUIET = 1.0
# A run is typing at this many keys, this many of them different.
MIN_KEYS = 4
MIN_DISTINCT = 3
# Stateful changes kept for Ctrl+Z.
UNDO_HISTORY = 20
# How much of the ignored text the warning quotes.
QUOTE_CHARS = 40

# Where a typed key goes to the main screen's bindings (ActivityMixin._activity_ctx).
TYPING_CONTEXTS = frozenset({"list", "help", "jobs"})

_RUN_KEY_NAMES = frozenset({"space", "enter", "backspace"})


def is_run_key(key: str, character: Optional[str]) -> bool:
    """A keystroke that typing produces: a printable character, space,
    Enter or Backspace (no Ctrl or Alt)."""
    if key in _RUN_KEY_NAMES:
        return True
    if key.startswith(("ctrl+", "alt+", "meta+", "super+")):
        return False
    return character is not None and len(character) == 1 and character.isprintable()


def starts_immediately(key: str) -> bool:
    """A one-shot on this key runs at once when it starts a run: digits,
    Enter and Esc answer an agent's prompt, and prose rarely starts with
    them."""
    return key in ("enter", "escape") or (len(key) == 1 and key.isdigit())


@dataclass
class RunKey:
    key: str
    character: Optional[str]
    ctx: str
    at: float


@dataclass
class KeyVerdict:
    """What one keystroke was to the run.

    counted: part of a run (False: not a typing key, or typed into a
             dialog the run did not open).
    index:   its place in the run (0: it started the run).
    typing:  it made the run typing (only on that key).
    """
    counted: bool = False
    index: int = -1
    typing: bool = False


@dataclass
class TypingRun:
    """The current run of typing keys, fed one keystroke at a time."""
    gap: float = RUN_GAP
    min_keys: int = MIN_KEYS
    min_distinct: int = MIN_DISTINCT
    keys: List[RunKey] = field(default_factory=list)
    typing: bool = False

    def reset(self) -> None:
        self.keys = []
        self.typing = False

    def active(self, now: float) -> bool:
        """A run is going: its last key came within the gap."""
        return bool(self.keys) and now - self.keys[-1].at <= self.gap

    @property
    def last_at(self) -> Optional[float]:
        return self.keys[-1].at if self.keys else None

    def feed(self, key: str, character: Optional[str], ctx: str, now: float) -> KeyVerdict:
        if not is_run_key(key, character):
            self.reset()
            return KeyVerdict()
        if self.keys and not self.active(now):
            self.reset()
        in_list = ctx in TYPING_CONTEXTS
        if not self.keys:
            if not in_list:
                return KeyVerdict()  # typing into a dialog that was already open
            self.keys.append(RunKey(key, character, ctx, now))
            return KeyVerdict(True, 0, False)
        if not in_list and self._opener_index() == 0:
            # The run's first key opened this dialog: the person meant it
            self.reset()
            return KeyVerdict()
        self.keys.append(RunKey(key, character, ctx, now))
        became = False
        if not self.typing and len(self.keys) >= self.min_keys \
                and len({k.key for k in self.keys}) >= self.min_distinct:
            self.typing = became = True
        return KeyVerdict(True, len(self.keys) - 1, became)

    def _opener_index(self) -> int:
        """The run key that opened the dialog keys now go to: the last one
        typed outside a dialog."""
        for i in range(len(self.keys) - 1, -1, -1):
            if self.keys[i].ctx in TYPING_CONTEXTS:
                return i
        return 0

    @property
    def text(self) -> str:
        return typed_text(self.keys)


def typed_text(keys) -> str:
    """What the keys would have typed into a text field."""
    out: List[str] = []
    for k in keys:
        key, ch = (k.key, k.character) if isinstance(k, RunKey) else k
        if key == "backspace":
            if out:
                out.pop()
        elif key == "enter":
            out.append("⏎")
        elif key == "space":
            out.append(" ")
        elif ch:
            out.append(ch)
    return "".join(out)


def quote(text: str, limit: int = QUOTE_CHARS) -> str:
    text = " ".join(text.split("\n"))
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def warning_text(ignored: str, undone: List[str], failed: List[str], held: List[str],
                 undo_key: str = "Ctrl+Z") -> str:
    """The one toast after a typing run: what was ignored and what was put back."""
    parts = [f'Typing went to overcode — ignored "{quote(ignored)}"']
    if undone:
        parts.append("undid " + ", ".join(_dedupe(undone)))
    if held:
        parts.append("didn't run " + ", ".join(_dedupe(held)))
    if failed:
        parts.append("couldn't undo " + ", ".join(_dedupe(failed)))
    text = "; ".join(parts) + "."
    if undo_key:
        text += f" {undo_key} undoes more."
    return text


def _dedupe(items: List[str]) -> List[str]:
    seen: List[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen
