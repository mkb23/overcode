"""
Billing codes dialog: every code's spend, and renaming codes in place.

One row per billing code (merged ones folded away), by total spend: its
name, the total, this month's and last month's spend, its agents (and how
many are running) and its source. The figures come from the ledger, read
once in a worker when the dialog opens (``load_code_rows``), never per key.

A rename is ``overcode billing rename``: the registry only, so every report,
past and future, shows the new name, and the old one goes in the code's
previous names. Typing the name of another code offers ``overcode billing
merge`` instead, applied on a second Enter.

Keys:
    j / k / ↑ / ↓     move                    a / →    the code's agents
    Enter / r         rename (Enter applies)   /        filter as you type
    Esc               cancel / back / close    q        close
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from rich.text import Text
from textual import events
from textual.message import Message

from . import dialog_style as ds
from .modal_base import ModalBase


@dataclass
class CodeRow:
    """One billing code as the dialog shows it."""

    id: str                      # the code id (an unmapped legacy string: that string)
    name: str
    source: str = "-"
    previous_names: List[str] = field(default_factory=list)
    total: float = 0.0
    this_month: float = 0.0
    last_month: float = 0.0
    agents: int = 0
    running: int = 0
    registered: bool = True      # False: a legacy string the registry does not map
    members: List[Tuple[str, float, bool]] = field(default_factory=list)  # (name, cost, running)


def month_starts(now: datetime) -> Tuple[datetime, datetime]:
    """The start of this month and of last month."""
    this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return this, (this - timedelta(days=1)).replace(day=1)


def load_code_rows(ledger, now: Optional[datetime] = None) -> List[CodeRow]:
    """Every code with its spend, by total spend (most first). Reads the ledger: run it in a worker.

    The total is ``totals()`` (what ``overcode billing`` shows); the two
    months and each code's agents are ``report`` over one read of the rows.
    """
    now = now or datetime.now()
    this_start, last_start = month_starts(now)
    totals = ledger.totals(now=now, save=False)
    rows = list(ledger.iter_rows())
    members: Dict[str, Dict[str, dict]] = {}
    ledger.report(rows=rows, agents=members)
    this_month = ledger.report(since=this_start, rows=rows)
    last_month = ledger.report(since=last_start, until=this_start, rows=rows)
    out: Dict[str, CodeRow] = {}
    for code_id, entry in ledger.codes.codes().items():
        if not entry.get("merged_into"):
            out[code_id] = CodeRow(code_id, str(entry.get("name") or code_id),
                                   source=str(entry.get("source") or "-"),
                                   previous_names=list(entry.get("previous_names") or ()))
    for key in set(totals) | set(this_month) | set(last_month):
        if key not in out:
            out[key] = CodeRow(key, ledger.codes.name_of(key), registered=False)
    for key, row in out.items():
        spent = totals.get(key) or {}
        row.total = float(spent.get("cost", 0) or 0)
        row.agents = int(spent.get("agents", 0) or 0)
        row.running = int(spent.get("running", 0) or 0)
        row.this_month = float((this_month.get(key) or {}).get("cost", 0) or 0)
        row.last_month = float((last_month.get(key) or {}).get("cost", 0) or 0)
        row.members = sorted(((a["name"], a["cost"], a["running"])
                              for a in (members.get(key) or {}).values()),
                             key=lambda m: (-m[1], m[0]))
    return sorted(out.values(), key=lambda r: (-r.total, r.name))


def money(value: float) -> str:
    return f"${value:,.2f}"


class BillingCodesModal(ModalBase):
    """Spend per billing code, with renames and merges."""

    class RenameRequested(Message):
        def __init__(self, code_id: str, old: str, new: str) -> None:
            super().__init__()
            self.code_id = code_id
            self.old = old
            self.new = new

    class MergeRequested(Message):
        def __init__(self, code_id: str, name: str, into_id: str, into_name: str) -> None:
            super().__init__()
            self.code_id = code_id
            self.name = name
            self.into_id = into_id
            self.into_name = into_name

    class Closed(Message):
        pass

    TITLE = "Billing codes"
    WIDTH = 100
    _MONEY_W = 12
    _COUNT_W = 8
    _SOURCE_W = 9
    _CHROME = 8  # border, header, rule, tip

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.rows: Optional[List[CodeRow]] = None   # None: still reading the ledger
        self.mode = "list"                          # list | edit | filter | agents
        self.filter = ""
        self.value = ""
        self.cursor = 0
        self._merge_into: Optional[CodeRow] = None  # Enter again merges into it
        self._scroll = 0
        self._list_scroll = 0                       # the list's, while showing agents
        self._message: Optional[Text] = None
        self._chosen: Optional[str] = None          # the highlighted code, by id

    # ── public api ───────────────────────────────────────────────────────

    def show(self, *, app_ref: Optional[Any] = None) -> None:  # type: ignore[override]
        """Open, waiting for ``set_rows`` (the ledger is read in a worker)."""
        self.rows = None
        self.mode = "list"
        self.filter = ""
        self._merge_into = None
        self._scroll = 0
        self._message = None
        self._chosen = None
        self._save_focus(app_ref)
        self._show(0)

    def set_rows(self, rows: List[CodeRow], message: Optional[str] = None,
                 style: str = ds.MUTED) -> None:
        """The codes read from the ledger; the highlight stays on its code."""
        self.rows = rows
        if message is not None:
            self._message = Text(message, style=style)
        self._reselect()
        self.refresh()

    def renamed(self, code_id: str, name: str, previous_names: List[str]) -> None:
        """A rename went through: the row shows it at once (no ledger read)."""
        for row in self.rows or ():
            if row.id == code_id:
                row.name, row.previous_names, row.source = name, list(previous_names), "human"
        self.refresh()

    def say(self, text: str, style: str = ds.MUTED) -> None:
        self._message = Text(text, style=style)
        self.refresh()

    # ── state ────────────────────────────────────────────────────────────

    @property
    def shown(self) -> List[CodeRow]:
        rows = self.rows or []
        needle = self.filter.strip().lower()
        if not needle:
            return rows
        return [r for r in rows if needle in r.name.lower()
                or any(needle in n.lower() for n in r.previous_names)]

    @property
    def current(self) -> Optional[CodeRow]:
        rows = self.shown
        return rows[self.selected_index] if 0 <= self.selected_index < len(rows) else None

    def _reselect(self) -> None:
        rows = self.shown
        ids = [r.id for r in rows]
        if self._chosen in ids:
            self.selected_index = ids.index(self._chosen)
        else:
            self.selected_index = min(self.selected_index, max(0, len(rows) - 1))
            if self._chosen is None and rows:
                self._chosen = rows[self.selected_index].id
        self._keep_in_view()

    def _visible_rows(self) -> int:
        height = getattr(self, "_screen_height", 40)
        return max(3, height - 4 - self._CHROME)

    def _keep_in_view(self) -> None:
        h = self._visible_rows()
        if self.selected_index < self._scroll:
            self._scroll = self.selected_index
        elif self.selected_index >= self._scroll + h:
            self._scroll = self.selected_index - h + 1

    def _owner_of(self, name: str, *, previous: bool) -> Optional[CodeRow]:
        """The other code called ``name`` (or, with ``previous``, that once was)."""
        me = self.current
        for row in self.rows or ():
            if row is me or not row.registered:
                continue
            if (name in row.previous_names) if previous else row.name == name:
                return row
        return None

    def error(self) -> Optional[str]:
        """Why the typed name cannot be applied (a merge offer is not an error)."""
        from ..codes import is_code_id

        row, new = self.current, self.value.strip()
        if row is None:
            return "no code"
        if not row.registered:
            return f"'{row.name}' is not in the code registry (overcode doctor)"
        if not new:
            return "a billing code cannot be empty"
        if is_code_id(new):
            return "that looks like a code id; pick another name"
        return None

    # ── render ───────────────────────────────────────────────────────────

    def hints(self) -> str:
        if self.mode == "edit":
            return ds.hints(("↵", "apply"), ("esc", "cancel"))
        if self.mode == "filter":
            return ds.hints(("↵", "keep filter"), ("esc", "clear"))
        if self.mode == "agents":
            return ds.hints(("esc ←", "back"), ("q", "close"))
        return ds.hints(("↵ r", "rename"), ("a →", "agents"), ("/", "filter"), ("esc", "close"))

    def _name_w(self, w: int) -> int:
        return max(12, w - 1 - 3 * self._MONEY_W - 2 * self._COUNT_W - self._SOURCE_W)

    def _cells(self, w: int, name: Text, cells: List[str], style: str) -> Text:
        line = Text()
        line.append_text(ds.fit(name, self._name_w(w)))
        widths = [self._MONEY_W] * 3 + [self._COUNT_W] * 2
        for value, width in zip(cells, widths):
            line.append(value.rjust(width)[-width:], style=style)
        line.append(" " * 2)
        line.append_text(ds.fit(Text(cells[5], style=style), self._SOURCE_W - 2))
        return line

    def _header(self, w: int) -> Text:
        line = Text(" ")
        line.append_text(self._cells(
            w, Text("code", style=f"bold {ds.MUTED}"),
            ["total", "this month", "last month", "agents", "running", "source"],
            f"bold {ds.MUTED}"))
        return ds.finish(line, w)

    def _row(self, row: CodeRow, selected: bool, w: int) -> Text:
        line = ds.item(selected)
        if selected and self.mode == "edit":
            name = ds.text_value(self.value, self.cursor)
        else:
            style = ("bold" if selected else ds.TEXT) if row.registered else ds.WARN
            name = Text(row.name, style=style)
            if self.filter.strip() and self.mode != "agents":
                at = row.name.lower().find(self.filter.strip().lower())
                if at >= 0:
                    name.stylize(ds.MATCH, at, at + len(self.filter.strip()))
        line.append_text(self._cells(
            w, name,
            [money(row.total), money(row.this_month), money(row.last_month),
             str(row.agents), str(row.running) if row.running else "-",
             row.source if row.registered else "legacy"],
            ds.TEXT if selected else ds.MUTED))
        return ds.finish(line, w)

    def _member(self, member: Tuple[str, float, bool], w: int) -> Text:
        name, cost, running = member
        line = Text("  ")
        line.append("▶ " if running else "  ", style=ds.STATE_ON)
        line.append_text(ds.fit(Text(name, style=ds.TEXT), max(12, w - 4 - self._MONEY_W - 10)))
        line.append(money(cost).rjust(self._MONEY_W), style=ds.TEXT)
        if running:
            line.append("  running", style=ds.STATE_ON)
        return ds.finish(line, w)

    def _tip(self, w: int) -> Text:
        row = self.current
        if self.mode == "edit" and row is not None:
            problem = self.error()
            new = self.value.strip()
            if problem:
                return ds.tip(w, Text(problem, style=ds.ERROR))
            if new == row.name:
                return ds.tip(w, Text("unchanged · type a new name", style=ds.MUTED))
            if self._merge_into is not None:
                return ds.tip(w, Text(f"merge {row.name} into {self._merge_into.name}? "
                                      "press Enter again", style=f"bold {ds.WARN}"))
            owner = self._owner_of(new, previous=False)
            if owner is not None:
                return ds.tip(w, Text(f"'{new}' is another code: ↵ merges {row.name} into it "
                                      "(its spend reports there from now on)", style=ds.WARN))
            owner = self._owner_of(new, previous=True)
            if owner is not None:
                return ds.tip(w, Text(f"'{new}' was a name of {owner.name}; "
                                      f"it will mean this code", style=ds.WARN))
            return ds.tip(w, Text(f"↵ renames {row.name} to {new} in every report, "
                                  "past and future", style=ds.MUTED))
        if self._message is not None:
            return ds.tip(w, self._message)
        if self.rows is None:
            return ds.tip(w, Text("reading the ledger…", style=ds.MUTED))
        if row is None:
            text = "no billing codes yet" if not self.rows else "no code matches the filter"
            return ds.tip(w, Text(text, style=ds.MUTED))
        prev = ", ".join(row.previous_names)
        left = Text(f"previously: {prev}" if prev else "no previous names", style=ds.MUTED)
        right = Text(row.id if row.registered else "not in the registry", style=ds.STATE_OTHER)
        return ds.tip(w, left, right)

    def render(self) -> Text:
        w = self.inner_width
        t = Text(no_wrap=True, overflow="crop")
        row = self.current
        if self.mode == "agents" and row is not None:
            head = Text(" ")
            head.append(row.name, style=f"bold {ds.TEXT}")
            head.append(f"  {money(row.total)} · {row.agents} agent"
                        f"{'s' if row.agents != 1 else ''}", style=ds.MUTED)
            t.append_text(ds.finish(head, w))
            t.append("\n")
            t.append_text(ds.rule(w))
            t.append("\n")
            h = self._visible_rows()
            members = row.members[self._scroll:self._scroll + h]
            for member in members:
                t.append_text(self._member(member, w))
                t.append("\n")
            if not members:
                t.append_text(ds.finish(Text("  no spend recorded", style=f"italic {ds.MUTED}"), w))
                t.append("\n")
            t.append_text(ds.tip(w, Text("spend by agent, all time", style=ds.MUTED)))
            return t
        if self.mode == "filter" or self.filter:
            line = Text(" ")
            line.append("filter  ", style=f"bold {ds.TEXT}")
            line.append_text(ds.text_value(self.filter, len(self.filter) if self.mode == "filter"
                                           else None))
            t.append_text(ds.finish(line, w))
            t.append("\n")
        t.append_text(self._header(w))
        t.append("\n")
        t.append_text(ds.rule(w))
        t.append("\n")
        rows = self.shown
        h = self._visible_rows()
        for i in range(self._scroll, min(len(rows), self._scroll + h)):
            t.append_text(self._row(rows[i], i == self.selected_index, w))
            t.append("\n")
        if self.rows:
            total = sum(r.total for r in rows)
            foot = Text(" ")
            foot.append_text(self._cells(
                w, Text(f"{len(rows)} code{'s' if len(rows) != 1 else ''}", style=ds.MUTED),
                [money(total), money(sum(r.this_month for r in rows)),
                 money(sum(r.last_month for r in rows)), "", "", ""], f"bold {ds.MUTED}"))
            t.append_text(ds.finish(foot, w))
            t.append("\n")
        t.append_text(self._tip(w))
        return t

    # ── keys ─────────────────────────────────────────────────────────────

    def on_key(self, event: events.Key) -> None:
        event.stop()  # a modal swallows every key
        handler = {"edit": self._edit_key, "filter": self._filter_key,
                   "agents": self._agents_key}.get(self.mode, self._list_key)
        handler(event.key, event.character)
        self.update_frame()
        self.refresh()

    def _close(self) -> None:
        self._hide()
        self.post_message(self.Closed())

    def _list_key(self, key: str, character: Optional[str]) -> None:
        rows = self.shown
        if key in ("j", "down", "k", "up") and rows:
            step = 1 if key in ("j", "down") else -1
            self.selected_index = (self.selected_index + step) % len(rows)
            self._chosen = rows[self.selected_index].id
            self._message = None
            self._keep_in_view()
        elif key in ("enter", "r") and self.current is not None:
            self.mode = "edit"
            self.value = self.current.name
            self.cursor = len(self.value)
            self._merge_into = None
            self._message = None
        elif key in ("a", "right") and self.current is not None:
            self.mode = "agents"
            self._list_scroll, self._scroll = self._scroll, 0
        elif key == "slash":
            self.mode = "filter"
        elif key == "escape" and self.filter:
            self.filter = ""
            self._reselect()
        elif key in ("escape", "q"):
            self._close()

    def _agents_key(self, key: str, character: Optional[str]) -> None:
        row = self.current
        count = len(row.members) if row is not None else 0
        if key in ("j", "down"):
            self._scroll = min(self._scroll + 1, max(0, count - self._visible_rows()))
        elif key in ("k", "up"):
            self._scroll = max(0, self._scroll - 1)
        elif key in ("escape", "left", "a"):
            self.mode = "list"
            self._scroll = self._list_scroll
        elif key == "q":
            self._close()

    def _filter_key(self, key: str, character: Optional[str]) -> None:
        if key == "enter":
            self.mode = "list"
        elif key == "escape":
            self.filter = ""
            self.mode = "list"
        elif key == "backspace":
            if not self.filter:
                self.mode = "list"
            self.filter = self.filter[:-1]
        elif key in ("down", "up"):
            self._list_key(key, None)
            return
        elif character and character.isprintable():
            self.filter += character
        else:
            return
        self.selected_index = 0
        self._reselect()
        if self.current is not None:
            self._chosen = self.current.id

    def _edit_key(self, key: str, character: Optional[str]) -> None:
        if key == "escape":
            self.mode = "list"
            self._merge_into = None
            return
        if key == "enter":
            self._submit()
            return
        before = self.value
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
        if self.value != before:
            self._merge_into = None

    def _submit(self) -> None:
        row, new = self.current, self.value.strip()
        if row is None or self.error() is not None:
            return  # the red line already says why
        if new == row.name:
            self.mode = "list"
            return
        owner = self._owner_of(new, previous=False)
        if owner is not None:
            if self._merge_into is not owner:
                self._merge_into = owner  # the tip asks for a second Enter
                return
            self.post_message(self.MergeRequested(row.id, row.name, owner.id, owner.name))
            self._chosen = owner.id
            self._message = Text(f"merging {row.name} into {owner.name}…", style=ds.MUTED)
        else:
            self.post_message(self.RenameRequested(row.id, row.name, new))
            self._chosen = row.id
            self._message = Text(f"renaming {row.name} to {new}…", style=ds.MUTED)
        self._merge_into = None
        self.mode = "list"

