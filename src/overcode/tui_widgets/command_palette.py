"""
Command palette — one fuzzy picker for agents, tags and commands (#482).

Ctrl+P opens it on agents (as the old jump modal did, #420), `/` opens it
on commands, and `>` typed into an empty agent query switches to commands
the way VSCode's quick-open does; backspace past the `>` switches back.
`T` opens it on tags (#357), and `S` on sort choices (#487): every
sortable column plus tree order, where choosing the current sort again
reverses it.

Command rows show the command, the states it cycles between with the
current one lit, and its key, so the palette doubles as a way to learn
the keys. Enter runs the selection and closes; Tab runs a stateful
command and stays open, to step through its states and watch them change.

The registry, key labels and matcher live in overcode/command_palette.py;
this widget only lays them out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from rich.cells import cell_len
from rich.text import Text
from textual import events
from textual.message import Message

from ..command_palette import (
    CATEGORIES, COMMANDS, MODE_SWITCHES, OFF_ON, Match, PaletteCommand, SortChoice,
    StateView, filter_sort_choices, rank_commands,
)
from .jump_modal import JumpCandidate, filter_candidates
from .modal_base import ModalBase


# Colours tuned against the TUI's dark surface; the selected-row blue is
# the same one the jobs list uses.
SEL_BG = "#2d4a5a"
ACCENT = "#5fafd7"
KEY = "bold #ffaf5f"                    # keys in the list
KEYCAP = "bold #101010 on #ffaf5f"      # the key in the tip line
STATE_ON = "bold #87d787"               # a toggle that is on
STATE_CUR = "bold #5fafd7"              # the current step of a cycle
STATE_OTHER = "#626262"
MATCH = "bold #ffd75f"
MUTED = "#8a8a8a"

PLACEHOLDERS = {
    "commands": "search commands, or type a key to see what it does",
    "agents": "filter agents by name, repo or branch",
    "tags": "filter tags",
    "sort": "filter columns by name, header or meaning",
}
TITLES = {"commands": "Commands", "agents": "Jump to agent", "tags": "Filter by tag",
          "sort": "Sort agents by"}
MAX_ROWS = 16      # tallest the list gets; shorter terminals get fewer
MAX_WIDTH = 88
RECENT_SHOWN = 3


@dataclass(frozen=True)
class _Row:
    """One line of the list: a section header, or a selectable item."""
    header: str = ""
    match: Optional[Match] = None              # commands mode
    cand: Optional[JumpCandidate] = None       # agents / tags mode
    sort: Optional[SortChoice] = None          # sort mode
    positions: Tuple[int, ...] = ()            # highlighted chars of cand.name / sort.name


class CommandPalette(ModalBase):
    """Fuzzy picker over agents, tags or commands."""

    class CommandChosen(Message):
        """`focus_target` is what had focus before the palette opened: the
        app focuses it for the run, since actions act on the focused agent."""
        def __init__(self, action: str, keep_open: bool, focus_target: Any = None) -> None:
            super().__init__()
            self.action = action
            self.keep_open = keep_open
            self.focus_target = focus_target

    class AgentChosen(Message):
        def __init__(self, session_id: str) -> None:
            super().__init__()
            self.session_id = session_id

    class TagChosen(Message):
        """tag is None for "(clear filter)"."""
        def __init__(self, tag: Optional[str]) -> None:
            super().__init__()
            self.tag = tag

    class SortChosen(Message):
        """A sort was picked (#487); the app reverses it if already current."""
        def __init__(self, mode: str, keep_open: bool) -> None:
            super().__init__()
            self.mode = mode
            self.keep_open = keep_open

    class Closed(Message):
        pass

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.mode: str = "commands"
        self.text: str = ""  # what the user typed (Widget.query is taken)
        self._agents: List[JumpCandidate] = []
        self._tags: List[JumpCandidate] = []
        self._sorts: List[SortChoice] = []
        self._keymap: Dict[str, List[str]] = {}
        self._recent: List[str] = []
        self._rows: List[_Row] = []
        self._items: List[int] = []   # indexes into _rows of selectable rows
        self._scroll: int = 0
        self._list_height: int = MAX_ROWS
        self._max_list_height: int = MAX_ROWS
        self._inner_width: int = MAX_WIDTH - 4
        self._title_width: int = 24
        self._key_width: int = 5

    # -- opening ------------------------------------------------------------

    def open(
        self,
        mode: str,
        *,
        agents: Sequence[JumpCandidate] = (),
        tags: Sequence[JumpCandidate] = (),
        sorts: Sequence[SortChoice] = (),
        keymap: Optional[Dict[str, List[str]]] = None,
        recent: Sequence[str] = (),
        app_ref: Optional[Any] = None,
    ) -> None:
        self._agents = list(agents)
        self._tags = list(tags)
        self._sorts = list(sorts)
        self._keymap = dict(keymap or {})
        self._recent = list(recent)
        self._title_width = max(cell_len(c.title) for c in COMMANDS)
        self._key_width = max(
            (cell_len(" ".join(k)) for k in self._keymap.values()), default=3,
        )
        self._save_focus(app_ref)
        self._switch(mode)
        self._show()
        self._select(self._default_index())

    def relayout(self) -> None:
        """Size and centre the palette for the current terminal."""
        try:
            screen_w, screen_h = self.app.size
        except Exception:
            return
        width = min(MAX_WIDTH, max(40, screen_w - 4), screen_w)
        top = 1 if screen_h < 30 else 3
        self._max_list_height = max(3, min(MAX_ROWS, screen_h - top - self._chrome - 1))
        self._inner_width = width - 4  # border + 1 col padding each side
        self.styles.width = width
        self.styles.offset = (max(0, (screen_w - width) // 2), top)
        self._fit_height()

    def update_sorts(self, sorts: Sequence[SortChoice]) -> None:
        """New sort choices after one was run with the palette kept open,
        so the active row and its arrow move; keeps the query and the
        selected row."""
        self._sorts = list(sorts)
        if self.mode == "sort":
            index = self.selected_index
            self._recompute()
            self._select(index)

    @property
    def _has_tip(self) -> bool:
        return self.mode in ("commands", "sort")

    @property
    def _chrome(self) -> int:
        """Rows around the list: border, query, rule — plus rule and tip
        line in commands and sort mode."""
        return 6 if self._has_tip else 4

    def _fit_height(self) -> None:
        """Commands fill the palette; agents and tags take only the rows
        they need. Sized on the full list, not the filtered one, so the
        palette keeps still while you type."""
        if self.mode == "commands":
            need = self._max_list_height
        elif self.mode == "sort":
            need = len(self._sorts)
        else:
            need = len(self._agents if self.mode == "agents" else self._tags)
        self._list_height = max(3, min(self._max_list_height, need))
        self.styles.height = self._list_height + self._chrome
        self._ensure_visible()
        self.refresh()

    # -- state --------------------------------------------------------------

    def _switch(self, mode: str) -> None:
        self.mode = mode
        self.text = ""
        self.border_title = TITLES[mode]
        self.border_subtitle = {
            "commands": "↵ run · esc close",
            "agents": "↵ jump · > commands · esc close",
            "tags": "↵ filter · > commands · esc close",
            "sort": "> commands · esc close",
        }[mode]
        self._recompute()
        self.relayout()

    def _recompute(self) -> None:
        rows: List[_Row] = []
        if self.mode == "commands":
            matches = rank_commands(COMMANDS, self.text, self._keymap)
            if self.text.strip():
                rows = [_Row(match=m) for m in matches]
            else:
                rows = self._grouped(matches)
        elif self.mode == "sort":
            rows = [_Row(sort=c, positions=pos) for c, pos in filter_sort_choices(self._sorts, self.text)]
        else:
            cands = self._agents if self.mode == "agents" else self._tags
            q = self.text.lower()
            for c in filter_candidates(cands, self.text):
                start = c.name.lower().find(q) if q else -1
                pos = tuple(range(start, start + len(q))) if start >= 0 else ()
                rows.append(_Row(cand=c, positions=pos))
        self._rows = rows
        self._items = [i for i, r in enumerate(rows) if not r.header]
        self._scroll = 0
        self._select(self._default_index())

    def _default_index(self) -> int:
        """Where the selection starts: the current sort in the unfiltered
        sort list, so Enter reverses it and arrows move from it; else the
        top (best) row."""
        if self.mode == "sort" and not self.text.strip():
            for n, i in enumerate(self._items):
                if self._rows[i].sort is not None and self._rows[i].sort.active:
                    return n
        return 0

    def _grouped(self, matches: List[Match]) -> List[_Row]:
        by_action = {m.command.action: m for m in matches}
        rows: List[_Row] = []
        recent = [by_action[a] for a in self._recent if a in by_action][:RECENT_SHOWN]
        if recent:
            rows.append(_Row(header="Recent"))
            rows.extend(_Row(match=m) for m in recent)
        for cat in CATEGORIES:
            group = [m for m in matches if m.command.category == cat]
            if group:
                rows.append(_Row(header=cat))
                rows.extend(_Row(match=m) for m in group)
        return rows

    def _select(self, index: int) -> None:
        self.selected_index = max(0, min(index, len(self._items) - 1)) if self._items else 0
        self._ensure_visible()
        self.refresh()

    def _ensure_visible(self) -> None:
        if not self._items:
            self._scroll = 0
            return
        row = self._items[self.selected_index]
        # Keep the section header above the first item of a group in view.
        top = row - 1 if row > 0 and self._rows[row - 1].header else row
        if top < self._scroll:
            self._scroll = top
        elif row >= self._scroll + self._list_height:
            self._scroll = row - self._list_height + 1
        self._scroll = max(0, min(self._scroll, max(0, len(self._rows) - self._list_height)))

    @property
    def selected_row(self) -> Optional[_Row]:
        return self._rows[self._items[self.selected_index]] if self._items else None

    # -- keys ---------------------------------------------------------------

    def on_key(self, event: events.Key) -> None:
        event.stop()
        event.prevent_default()
        key = event.key
        n = len(self._items)
        if key == "escape":
            self._close()
        elif key == "enter":
            self._choose(keep_open=False)
        elif key == "tab":
            self._choose(keep_open=True)
        elif key in ("down", "ctrl+n", "ctrl+j"):
            if n:
                self._select((self.selected_index + 1) % n)
        elif key in ("up", "ctrl+p", "ctrl+k"):
            if n:
                self._select((self.selected_index - 1) % n)
        elif key == "pagedown":
            self._select(self.selected_index + self._list_height - 1)
        elif key == "pageup":
            self._select(self.selected_index - self._list_height + 1)
        elif key == "home":
            self._select(0)
        elif key == "end":
            self._select(n - 1)
        elif key in ("backspace", "ctrl+h"):
            if self.text:
                self.text = self.text[:-1]
                self._recompute()
            elif self.mode == "commands" and self._agents:
                self._switch("agents")
        elif key == "ctrl+u":
            self.text = ""
            self._recompute()
        elif key == "ctrl+w":
            self.text = self.text.rstrip()
            self.text = self.text[: self.text.rfind(" ") + 1] if " " in self.text else ""
            self._recompute()
        elif event.character and len(event.character) == 1 and event.character.isprintable():
            if event.character == ">" and not self.text and self.mode != "commands":
                self._switch("commands")
            else:
                self.text += event.character
                self._recompute()

    def _choose(self, keep_open: bool) -> None:
        row = self.selected_row
        if row is None:
            return
        if row.sort is not None:
            if not keep_open:
                self._hide()
            self.post_message(self.SortChosen(row.sort.mode, keep_open))
            return
        if row.cand is not None:
            if self.mode == "agents":
                self._hide()
                self.post_message(self.AgentChosen(row.cand.session_id))
            else:
                self._hide()
                self.post_message(self.TagChosen(row.cand.session_id or None))
            return
        cmd = row.match.command
        if cmd.action in MODE_SWITCHES:
            target = MODE_SWITCHES[cmd.action]
            if {"agents": self._agents, "tags": self._tags, "sort": self._sorts}[target]:
                self._switch(target)
                return
        keep_open = keep_open and cmd.repeatable
        target = self._previous_focus
        if not keep_open:
            self._hide()
        self.post_message(self.CommandChosen(cmd.action, keep_open, target))

    def _close(self) -> None:
        self._hide()
        self.post_message(self.Closed())

    # -- drawing ------------------------------------------------------------

    def render(self) -> Text:
        w = self._inner_width
        out = Text(no_wrap=True, overflow="crop")
        out.append_text(self._query_line(w))
        out.append("\n" + "─" * w + "\n", style=MUTED)

        visible = self._rows[self._scroll:self._scroll + self._list_height]
        selected = self._items[self.selected_index] if self._items else -1
        for offset, row in enumerate(visible):
            out.append_text(self._row_line(row, self._scroll + offset == selected, w))
            out.append("\n")
        if not self._rows:
            empty = {"commands": "No matching commands",
                     "agents": "No matching agents" if self.text else "No agents",
                     "tags": "No matching tags",
                     "sort": "No matching columns"}[self.mode]
            out.append(f"  {empty}\n", style=f"italic {MUTED}")
            visible = [None]
        out.append("\n" * (self._list_height - len(visible)))

        if self._has_tip:
            out.append("─" * w + "\n", style=MUTED)
            out.append_text(self._sort_tip_line(w) if self.mode == "sort" else self._tip_line(w))
        return out

    def _query_line(self, w: int) -> Text:
        line = Text()
        prompt = "> " if self.mode == "commands" else "❯ "
        line.append(prompt, style=f"bold {ACCENT}")
        line.append(self.text, style="bold")
        line.append("▏", style=f"bold {ACCENT}")
        if not self.text:
            line.append(PLACEHOLDERS[self.mode], style=f"italic {STATE_OTHER}")
        n = len(self._items)
        total = {"commands": len(COMMANDS), "agents": len(self._agents), "tags": len(self._tags),
                 "sort": len(self._sorts)}[self.mode]
        count = f"{n}/{total}" if self.text else f"{total}"
        right = Text(count, style=MUTED)
        return _spread(line, right, w)

    def _row_line(self, row: _Row, selected: bool, w: int) -> Text:
        if row.header:
            line = Text(" " + row.header.upper() + " ", style=f"bold {MUTED}")
            line.append("─" * (w - cell_len(line.plain)), style=STATE_OTHER)
            return line
        base = f"on {SEL_BG}" if selected else ""
        line = Text(style=base)
        line.append("▌" if selected else " ", style=f"bold {ACCENT}")
        if row.cand is not None:
            return self._cand_line(line, row, selected, w)
        if row.sort is not None:
            return self._sort_line(line, row, selected, w)

        cmd = row.match.command
        title = _highlight(cmd.title, row.match.positions, "bold" if selected else "")
        tw = min(self._title_width, w // 2)
        line.append_text(_fit(title, tw))
        line.append("  ")

        keys = _keys(self._keymap.get(cmd.action, []), row.match.by_key)
        states_w = w - 1 - tw - 2 - self._key_width - 1
        if cmd.state is not None and states_w >= 6:
            line.append_text(_states(self._state(cmd), states_w))
        return _spread(line, keys, w)

    def _cand_line(self, line: Text, row: _Row, selected: bool, w: int) -> Text:
        c = row.cand
        if c.status:
            line.append(c.status + " ", style=c.status_style)
        line.append_text(_highlight(c.name, row.positions, "bold" if selected else "bold #d0d0d0"))
        meta = [p for p in (c.repo, c.branch if c.branch != c.repo else "") if p]
        if meta:
            line.append("  " + " · ".join(meta), style=MUTED)
        return _pad(_fit(line, w), w)

    def _sort_line(self, line: Text, row: _Row, selected: bool, w: int) -> Text:
        """Name, header code, direction and meaning; the current sort is
        lit and says so."""
        c = row.sort
        line.append_text(_fit(_highlight(c.name, row.positions, "bold" if selected else ""), 20))
        line.append_text(_fit(Text(c.header, style=KEY), 5))
        if c.mode == "by_tree":
            arrow = " "
        else:
            arrow = "▼" if c.descending else "▲"
        if c.active:
            line.append(f" {arrow} sorted ", style=STATE_CUR)
        else:
            line.append(f" {arrow}        ", style=STATE_OTHER)
        line.append_text(Text(c.description, style=MUTED))
        return _pad(_fit(line, w), w)

    def _sort_tip_line(self, w: int) -> Text:
        row = self.selected_row
        tip = Text()
        if row is None or row.sort is None:
            tip.append("Or click a column header to sort by it", style=MUTED)
            return _pad(_fit(tip, w), w)
        c = row.sort
        if c.mode == "by_tree":
            tip.append("↵ ", style="bold")
            tip.append("tree order" + (" (current)" if c.active else ""), style=MUTED)
        elif c.active:
            flip = "▲ ascending" if c.descending else "▼ descending"
            tip.append("↵ ", style="bold")
            tip.append(f"reverse to {flip}", style=MUTED)
        else:
            way = "▼ largest first" if c.descending else "▲ smallest / A→Z first"
            tip.append("↵ ", style="bold")
            tip.append(f"sort {way}", style=MUTED)
        tip.append("  · or click its header", style=MUTED)
        hints = Text()
        hints.append("⇥", style="bold")
        hints.append(" keep open", style=MUTED)
        return _spread(_fit(tip, w - cell_len(hints.plain) - 2), hints, w)

    def _tip_line(self, w: int) -> Text:
        row = self.selected_row
        tip = Text()
        if row is None:
            tip.append("Fuzzy search: \"sd\" finds Summary detail; a key like S or $ finds its command",
                       style=MUTED)
            return _pad(_fit(tip, w), w)

        cmd = row.match.command
        keys = self._keymap.get(cmd.action, [])
        if keys:
            tip.append("press ", style=MUTED)
            tip.append_text(_keycaps(keys))
            if cmd.action in MODE_SWITCHES:
                listed = {"sort": "sort choices"}.get(MODE_SWITCHES[cmd.action], MODE_SWITCHES[cmd.action])
                tip.append(f"  lists {listed} here", style=MUTED)
        else:
            tip.append("palette only", style=f"italic {MUTED}")
        state = self._state(cmd) if cmd.state is not None else None
        if state is not None and len(state.options) > 1:
            tip.append("  cycles  " if len(state.options) > 2 else "  toggles  ", style=MUTED)
            for i, opt in enumerate(state.options):
                if i:
                    tip.append(" → ", style=MUTED)
                tip.append(opt, style=f"bold {ACCENT}" if i == state.current else "")
        elif state is not None and state.current_label:
            tip.append(f"  now {state.current_label}", style=MUTED)
        if state is not None and state.note:
            tip.append(f"  · {state.note}", style=MUTED)
        elif cmd.agent and state is None:
            tip.append("  · focused agent", style=MUTED)
        hints = Text()
        if cmd.repeatable:
            hints.append("⇥", style="bold")
            hints.append(" keep open", style=MUTED)
        return _spread(_fit(tip, w - cell_len(hints.plain) - 2), hints, w)

    def _state(self, cmd: PaletteCommand) -> StateView:
        try:
            return cmd.state(self._app_ref if self._app_ref is not None else self.app)
        except Exception:
            return StateView(("?",), None)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _pad(text: Text, width: int) -> Text:
    gap = width - cell_len(text.plain)
    if gap > 0:
        text.append(" " * gap)
    return text


def _fit(text: Text, width: int) -> Text:
    """Truncate to `width` cells with an ellipsis, then pad to it."""
    if cell_len(text.plain) > width:
        text = text.copy()
        text.truncate(max(0, width - 1), overflow="crop")
        text.append("…", style=MUTED)
    return _pad(text, width)


def _spread(left: Text, right: Text, width: int) -> Text:
    """left, then right flush against the right edge."""
    gap = width - cell_len(left.plain) - cell_len(right.plain)
    if gap < 1:
        left = _fit(left, max(0, width - cell_len(right.plain) - 1))
        gap = 1
    left.append(" " * gap)
    left.append_text(right)
    return left


def _highlight(s: str, positions: Sequence[int], style: str) -> Text:
    text = Text(s, style=style)
    for p in positions:
        if 0 <= p < len(s):
            text.stylize(MATCH, p, p + 1)
    return text


def _keys(keys: Sequence[str], lit: bool = False) -> Text:
    """A row's keys as plain coloured text; `lit` when the query named one."""
    text = Text()
    for i, k in enumerate(keys):
        if i:
            text.append(" ")
        text.append(k, style=MATCH if lit else KEY)
    return text


def _keycaps(keys: Sequence[str]) -> Text:
    """Keys drawn as caps — used once, in the tip line, where they stand out."""
    text = Text()
    for i, k in enumerate(keys):
        if i:
            text.append(" or ", style=MUTED)
        text.append(f" {k} ", style=KEYCAP)
    return text


def _state_style(state: StateView, i: int) -> str:
    if i != state.current:
        return STATE_OTHER
    if state.options == OFF_ON:
        return STATE_ON if i == 1 else "bold #d0d0d0"
    return STATE_CUR


def _states(state: StateView, width: int) -> Text:
    """Every option with the current one lit; just the current one and its
    position when the whole cycle does not fit."""
    full = Text()
    for i, opt in enumerate(state.options):
        if i:
            full.append("  ")
        full.append(opt, style=_state_style(state, i))
    if state.note and state.current is None:
        full.append(f"  {state.note}", style=f"italic {STATE_OTHER}")
    if cell_len(full.plain) <= width:
        return full
    label = state.current_label
    if label is None:
        return Text("–", style=STATE_OTHER)
    short = Text(label, style=_state_style(state, state.current))
    short.append(f"  {state.current + 1} of {len(state.options)}", style=STATE_OTHER)
    return short
