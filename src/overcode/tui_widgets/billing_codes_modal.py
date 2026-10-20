"""
Billing codes dialog: every code's spend, and renaming codes in place.

One row per billing code (merged ones folded away), by total spend: its
name, the total, this month's and last month's spend, its agents (and how
many are running) and its source. The figures come from the ledger, read
once in a worker when the dialog opens (``load_code_rows``), never per key.

A rename is ``overcode billing rename``: the registry only, so every report,
past and future, shows the new name, and the old one goes in the code's
previous names. Another code's current name is refused; merging is its own
action. ``M`` is ``overcode billing merge``: the marked codes, else the
highlighted one, into a code picked by type-ahead (existing codes only),
on a confirm that states the result.

Collapsing is ``overcode billing collapse``: a merge into the default code
(``billing.default_code``, marked ★), which itself never collapses. ``c``
collapses the marked codes, else the highlighted one, on a second ``c``.
Merged codes are hidden until ``m``; ``u`` on one undoes its merge (its
live agents stay where the merge moved them). The line above the table is
where new agents' codes come from; ``s`` changes it (the New agent
defaults dialog).

Keys:
    j / k / ↑ / ↓     move                    a / →    the code's agents
    Enter / r         rename (Enter applies)   /        filter as you type
    M                 merge into… (picker)     *        mark / unmark every shown code
    space             mark                     $ / i    mark under $X / idle N days
    c                 collapse into default    m        show merged codes
    u                 unmerge (a merged code)  s        new-agent billing settings
    ?                 the other keys           Esc      cancel / back / close
    q                 close
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
    last: Optional[datetime] = None      # its latest ledger row
    merged_into: Optional[str] = None    # merged: the name of the code it reports under


def month_starts(now: datetime) -> Tuple[datetime, datetime]:
    """The start of this month and of last month."""
    this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return this, (this - timedelta(days=1)).replace(day=1)


def load_code_rows(ledger, now: Optional[datetime] = None) -> List[CodeRow]:
    """Every code with its spend, by total spend (most first). Reads the ledger: run it in a worker.

    The total is ``totals()`` (what ``overcode billing`` shows); the two
    months and each code's agents are ``report`` over one read of the rows.
    Merged codes are rows too (``merged_into``), with no spend of their own.
    """
    now = now or datetime.now()
    this_start, last_start = month_starts(now)
    totals = ledger.totals(now=now, save=False)
    rows = list(ledger.iter_rows())
    members: Dict[str, Dict[str, dict]] = {}
    ledger.report(rows=rows, agents=members)
    this_month = ledger.report(since=this_start, rows=rows)
    last_month = ledger.report(since=last_start, until=this_start, rows=rows)
    last = ledger.last_activity(rows)
    out: Dict[str, CodeRow] = {}
    for code_id, entry in ledger.codes.codes().items():
        out[code_id] = CodeRow(code_id, str(entry.get("name") or code_id),
                               source=str(entry.get("source") or "-"),
                               previous_names=list(entry.get("previous_names") or ()),
                               last=last.get(code_id))
        if entry.get("merged_into"):
            out[code_id].merged_into = ledger.codes.name_of(code_id)
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
        def __init__(self, code_ids: List[str], names: List[str], into_id: str,
                     into_name: str) -> None:
            super().__init__()
            self.code_ids = code_ids
            self.names = names
            self.into_id = into_id
            self.into_name = into_name

    class CollapseRequested(Message):
        def __init__(self, code_ids: List[str], names: List[str]) -> None:
            super().__init__()
            self.code_ids = code_ids
            self.names = names

    class UnmergeRequested(Message):
        def __init__(self, code_id: str, name: str, into: str) -> None:
            super().__init__()
            self.code_id = code_id
            self.name = name
            self.into = into

    class SettingsRequested(Message):
        """``s``: the New agent defaults dialog, on its billing rows."""

    class Closed(Message):
        pass

    TITLE = "Billing codes"
    WIDTH = 100
    _MONEY_W = 12
    _COUNT_W = 8
    _SOURCE_W = 9
    _CHROME = 9  # border, settings, header, rule, tip
    _MARK_W = 2

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.rows: Optional[List[CodeRow]] = None   # None: still reading the ledger
        self.mode = "list"                          # list | edit | filter | agents | prompt | merge
        self.filter = ""
        self.value = ""
        self.cursor = 0
        self.query = ""                             # the merge picker's type-ahead
        self._pick = 0                              # its highlighted target
        self._pick_scroll = 0
        self._sources: List[CodeRow] = []           # what M merges
        self._target: Optional[CodeRow] = None      # picked: Enter again merges
        self._taken: Optional[Tuple[str, str]] = None  # (code, name) a rename was refused
        self._scroll = 0
        self._list_scroll = 0                       # the list's, while showing agents
        self._message: Optional[Text] = None
        self._chosen: Optional[str] = None          # the highlighted code, by id
        self.marks: set = set()                     # marked code ids
        self.show_merged = False
        self.settings: dict = {"new_agent_code": "name", "default_code": "general", "warnings": []}
        self.default_id: Optional[str] = None       # the code collapse folds into, once it exists
        self._confirm: Optional[str] = None         # "c" / "u": pressed once, press again
        self.prompt = ""                            # "under" / "idle", in prompt mode

    # ── public api ───────────────────────────────────────────────────────

    def show(self, *, app_ref: Optional[Any] = None) -> None:  # type: ignore[override]
        """Open, waiting for ``set_rows`` (the ledger is read in a worker)."""
        self.rows = None
        self.mode = "list"
        self.filter = ""
        self._target = None
        self._taken = None
        self._scroll = 0
        self._message = None
        self._chosen = None
        self.marks = set()
        self._confirm = None
        self._save_focus(app_ref)
        self._show(0)

    def set_rows(self, rows: List[CodeRow], message: Optional[str] = None,
                 style: str = ds.MUTED, *, settings: Optional[dict] = None,
                 default_id: Optional[str] = None) -> None:
        """The codes read from the ledger; the highlight stays on its code.

        ``settings``: ``config.get_billing_defaults()``; ``default_id``: the
        default code's id (None until it exists).
        """
        self.rows = rows
        if settings is not None:
            self.settings = settings
        self.default_id = default_id
        live = {r.id for r in rows if r.merged_into is None}
        self.marks &= live
        self._confirm = None
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
        rows = [r for r in self.rows or [] if self.show_merged or r.merged_into is None]
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

    def collapsible(self, row: Optional[CodeRow]) -> bool:
        """A code that may be marked, merged or collapsed: registered, not merged, not the default."""
        return (row is not None and row.registered and row.merged_into is None
                and row.id != self.default_id and row.name != self.settings["default_code"])

    def _targets(self) -> List[CodeRow]:
        """What ``c`` collapses: the marked codes, else the highlighted one."""
        if self.marks:
            return [r for r in self.rows or () if r.id in self.marks]
        row = self.current
        return [row] if self.collapsible(row) else []

    def _mark_where(self, kind: str, text: str) -> None:
        """``$``/``i``: mark every collapsible code under $X total, or idle N days."""
        from ..ledger import collapsible

        try:
            value = float(text.strip().lstrip("$"))
        except ValueError:
            self._message = Text(f"not a number: {text!r}", style=ds.ERROR)
            return
        now = datetime.now()
        under = value if kind == "under" else None
        idle = value if kind == "idle" else None
        picked = [r for r in self.shown if self.collapsible(r)
                  and collapsible(r.total, r.running, r.last, now, under=under, idle_days=idle)]
        self.marks |= {r.id for r in picked}
        what = f"under {money(value)}" if under is not None else f"idle {value:g}+ days"
        if not picked:
            self._message = Text(f"no code {what}", style=ds.MUTED)
            return
        marked = [r for r in self.rows or () if r.id in self.marks]
        self._message = Text(f"marked {len(picked)} code{'s' if len(picked) != 1 else ''} {what}"
                             f" · {len(marked)} marked, {money(sum(r.total for r in marked))}"
                             f" · c collapses them", style=ds.MUTED)

    def _owner_of(self, name: str, *, previous: bool) -> Optional[CodeRow]:
        """The other code called ``name`` (or, with ``previous``, that once was)."""
        me = self.current
        for row in self.rows or ():
            if row is me or not row.registered or row.merged_into is not None:
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
        if row.merged_into is not None:
            return f"'{row.name}' is merged into {row.merged_into}; u unmerges it"
        if not new:
            return "a billing code cannot be empty"
        if is_code_id(new):
            return "that looks like a code id; pick another name"
        owner = self._owner_of(new, previous=False)
        if owner is not None:
            return f"taken by '{owner.name}' — press M to merge into it"
        return None

    # ── merge ────────────────────────────────────────────────────────────

    def _merge_sources(self) -> List[CodeRow]:
        """What ``M`` merges: the marked codes, else the highlighted one."""
        if self.marks:
            return [r for r in self.rows or () if r.id in self.marks]
        row = self.current
        return [row] if self.collapsible(row) else []

    def candidates(self) -> List[CodeRow]:
        """Codes the picker offers: live ones, not being merged; names starting with the query first."""
        skip = {r.id for r in self._sources}
        rows = [r for r in self.rows or () if r.registered and r.merged_into is None
                and r.id not in skip]
        needle = self.query.strip().lower()
        if not needle:
            return rows
        hits = [r for r in rows if needle in r.name.lower()
                or any(needle in n.lower() for n in r.previous_names)]
        return sorted(hits, key=lambda r: not r.name.lower().startswith(needle))

    def _sources_label(self) -> str:
        return self._sources[0].name if len(self._sources) == 1 else f"{len(self._sources)} codes"

    def merge_summary(self) -> str:
        """The confirm: what merges, with its spend and agents, into what."""
        agents = sum(r.agents for r in self._sources)
        return (f"merge {self._sources_label()} ({money(sum(r.total for r in self._sources))}, "
                f"{agents} agent{'s' if agents != 1 else ''}) into {self._target.name}? "
                "↵ confirm · esc cancel")

    def _start_merge(self) -> None:
        row = self.current
        sources = self._merge_sources()
        if not sources:
            self._message = Text(
                "the default code can't be merged away; M on another code merges into it"
                if row is not None and row.id == self.default_id
                else "nothing to merge here" if row is None
                else f"{row.name} can't be merged", style=ds.WARN)
            return
        self._sources, self._target = sources, None
        taken, self._taken = self._taken, None
        self.query = taken[1] if taken and row is not None and taken[0] == row.id else ""
        self.mode, self._pick, self._pick_scroll = "merge", 0, 0
        self._message = None

    def _pick_target(self) -> None:
        """Enter in the picker: an exact name wins, else the highlighted code."""
        typed = self.query.strip()
        exact = next((r for r in self.rows or () if r.name == typed), None) if typed else None
        if exact is not None and exact.merged_into is None and exact.registered:
            if exact.id in {r.id for r in self._sources}:
                self._message = Text(f"{exact.name} is one of the codes being merged",
                                     style=ds.ERROR)
                return
            self._target = exact
            return
        cands = self.candidates()
        if not cands:
            self._message = Text(f"no code called '{typed}' · merge never makes a code "
                                 "(r renames)", style=ds.ERROR)
            return
        self._target = cands[min(self._pick, len(cands) - 1)]

    # ── render ───────────────────────────────────────────────────────────

    def hints(self) -> str:
        if self.mode == "edit":
            return ds.hints(("↵", "apply"), ("esc", "cancel"))
        if self.mode == "filter":
            return ds.hints(("↵", "keep filter"), ("esc", "clear"))
        if self.mode == "agents":
            return ds.hints(("esc ←", "back"), ("q", "close"))
        if self.mode == "prompt":
            return ds.hints(("↵", "mark"), ("esc", "cancel"))
        if self.mode == "merge":
            if self._target is not None:
                return ds.hints(("↵", "confirm"), ("esc", "cancel"))
            return ds.hints(("type", "filter"), ("↑↓", "choose"), ("↵", "pick"), ("esc", "cancel"))
        # The border has no room for every key: ? names the rest
        return ds.hints(("r", "rename"), ("M", "merge"), ("c", "collapse"), ("u", "unmerge"),
                        ("/", "filter"), ("*", "mark shown"), ("space", "mark")) + " · ? · esc"

    OTHER_KEYS = ("↵ rename · a agents · $ i mark under $X / idle · m merged · s settings · "
                  "esc close")

    def _name_w(self, w: int) -> int:
        return max(12, w - 1 - self._MARK_W - 3 * self._MONEY_W - 2 * self._COUNT_W
                   - self._SOURCE_W)

    def _cells(self, w: int, name: Text, cells: List[str], style: str,
               mark: Optional[Text] = None) -> Text:
        line = Text()
        line.append_text(mark if mark is not None else Text(" " * self._MARK_W))
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
            if row.merged_into is not None:
                style = f"italic {ds.STATE_OTHER}"
            name = Text(row.name, style=style)
            needle = (self.query if self.mode == "merge" else self.filter).strip()
            if needle and self.mode != "agents":
                at = row.name.lower().find(needle.lower())
                if at >= 0:
                    name.stylize(ds.MATCH, at, at + len(needle))
            if row.id == self.default_id:
                name.append(" ★", style=ds.ACCENT)
        mark = Text(f"{ds.CHECK_ON} " if row.id in self.marks else "  ", style=ds.STATE_ON)
        if row.merged_into is not None:
            cells = ["-", "-", "-", "-", "-", "merged"]
        else:
            cells = [money(row.total), money(row.this_month), money(row.last_month),
                     str(row.agents), str(row.running) if row.running else "-",
                     row.source if row.registered else "legacy"]
        line.append_text(self._cells(w, name, cells, ds.TEXT if selected else ds.MUTED, mark))
        return ds.finish(line, w)

    def _settings_line(self, w: int) -> Text:
        """Where new agents' codes come from (``s`` changes it)."""
        mode = self.settings.get("new_agent_code", "name")
        default = self.settings.get("default_code", "general")
        line = Text(" new agents bill to ", style=ds.MUTED)
        line.append({"name": "their name", "folder": "their repo",
                     "default": default}.get(mode, mode), style=ds.TEXT)
        line.append(" · default code ", style=ds.MUTED)
        line.append(default, style=ds.ACCENT)
        if self.settings.get("warnings"):
            line.append(f" · config.yaml: {self.settings['warnings'][0]}", style=ds.WARN)
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
            owner = self._owner_of(new, previous=True)
            if owner is not None:
                return ds.tip(w, Text(f"'{new}' was a name of {owner.name}; "
                                      f"it will mean this code", style=ds.WARN))
            return ds.tip(w, Text(f"↵ renames {row.name} to {new} in every report, "
                                  "past and future", style=ds.MUTED))
        if self.mode == "merge":
            if self._target is not None:
                return ds.tip(w, Text(self.merge_summary(), style=f"bold {ds.WARN}"))
            if self._message is not None:
                return ds.tip(w, self._message)
            cands = self.candidates()
            if not cands:
                return ds.tip(w, Text("no code matches · merge only folds into an existing code",
                                      style=ds.MUTED))
            pick = cands[min(self._pick, len(cands) - 1)]
            return ds.tip(w, Text(f"↵ merges {self._sources_label()} into {pick.name}",
                                  style=ds.MUTED))
        if self._confirm == "c":
            targets = self._targets()
            what = (targets[0].name if len(targets) == 1
                    else f"{len(targets)} codes")
            return ds.tip(w, Text(
                f"collapse {what} ({money(sum(r.total for r in targets))}) into "
                f"{self.settings['default_code']}? press c again · undo: m, then u",
                style=f"bold {ds.WARN}"))
        if self._confirm == "u" and row is not None:
            return ds.tip(w, Text(
                f"unmerge {row.name} from {row.merged_into}? press u again · its live "
                f"agents stay on {row.merged_into}", style=f"bold {ds.WARN}"))
        if self._message is not None:
            return ds.tip(w, self._message)
        if self.rows is None:
            return ds.tip(w, Text("reading the ledger…", style=ds.MUTED))
        if row is None:
            text = "no billing codes yet" if not self.rows else "no code matches the filter"
            return ds.tip(w, Text(text, style=ds.MUTED))
        prev = ", ".join(row.previous_names)
        left = Text(f"previously: {prev}" if prev else "no previous names", style=ds.MUTED)
        if row.merged_into is not None:
            left = Text(f"merged into {row.merged_into} · u unmerges", style=ds.MUTED)
        elif row.id == self.default_id:
            left = Text("★ the default code: c folds codes into it", style=ds.MUTED)
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
        t.append_text(self._settings_line(w))
        t.append("\n")
        if self.mode == "merge":
            return self._render_picker(t, w)
        if self.mode == "prompt":
            line = Text(" ")
            line.append("mark codes under $" if self.prompt == "under"
                        else "mark codes idle for days ", style=f"bold {ds.TEXT}")
            line.append_text(ds.text_value(self.value, len(self.value), placeholder=""))
            t.append_text(ds.finish(line, w))
            t.append("\n")
        elif self.mode == "filter" or self.filter:
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
            count = sum(1 for r in rows if r.merged_into is None)
            label = f"{count} code{'s' if count != 1 else ''}"
            if self.marks:
                label += f" · {len(self.marks)} marked"
            foot = Text(" ")
            foot.append_text(self._cells(
                w, Text(label, style=ds.MUTED),
                [money(total), money(sum(r.this_month for r in rows)),
                 money(sum(r.last_month for r in rows)), "", "", ""], f"bold {ds.MUTED}"))
            t.append_text(ds.finish(foot, w))
            t.append("\n")
        t.append_text(self._tip(w))
        return t

    def _render_picker(self, t: Text, w: int) -> Text:
        """``M``: the codes to merge into, filtered by the type-ahead line."""
        line = Text(" ")
        line.append(f"merge {self._sources_label()} into  ", style=f"bold {ds.TEXT}")
        line.append_text(ds.text_value(self.query, len(self.query) if self._target is None
                                       else None))
        t.append_text(ds.finish(line, w))
        t.append("\n")
        t.append_text(self._header(w))
        t.append("\n")
        t.append_text(ds.rule(w))
        t.append("\n")
        cands = self.candidates()
        pick = (cands.index(self._target) if self._target in cands
                else min(self._pick, max(0, len(cands) - 1)))
        h = self._visible_rows()
        for i in range(self._pick_scroll, min(len(cands), self._pick_scroll + h)):
            t.append_text(self._row(cands[i], i == pick, w))
            t.append("\n")
        t.append_text(self._tip(w))
        return t

    # ── keys ─────────────────────────────────────────────────────────────

    def on_key(self, event: events.Key) -> None:
        event.stop()  # a modal swallows every key
        handler = {"edit": self._edit_key, "filter": self._filter_key,
                   "agents": self._agents_key, "prompt": self._prompt_key,
                   "merge": self._merge_key,
                   }.get(self.mode, self._list_key)
        handler(event.key, event.character)
        self.update_frame()
        self.refresh()

    def _close(self) -> None:
        self._hide()
        self.post_message(self.Closed())

    def _list_key(self, key: str, character: Optional[str]) -> None:
        rows = self.shown
        confirm, self._confirm = self._confirm, None
        if key == "c":
            targets = self._targets()
            if not targets:
                row = self.current
                self._message = Text(
                    "the default code cannot be collapsed" if row is not None
                    and row.id == self.default_id else "nothing to collapse here", style=ds.WARN)
            elif confirm == "c":
                self.post_message(self.CollapseRequested([r.id for r in targets],
                                                         [r.name for r in targets]))
                self._message = Text(f"collapsing into {self.settings['default_code']}…",
                                     style=ds.MUTED)
                self.marks = set()
            else:
                self._confirm = "c"
            return
        if key == "u":
            row = self.current
            if row is None or row.merged_into is None:
                self._message = Text("u unmerges a merged code (m shows them)", style=ds.MUTED)
            elif confirm == "u":
                self.post_message(self.UnmergeRequested(row.id, row.name, row.merged_into))
                self._message = Text(f"unmerging {row.name}…", style=ds.MUTED)
            else:
                self._confirm = "u"
            return
        if key == "space":
            row = self.current
            if row is not None and row.id in self.marks:
                self.marks.discard(row.id)
            elif self.collapsible(row):
                self.marks.add(row.id)
            elif row is not None:
                self._message = Text(f"{row.name} cannot be collapsed", style=ds.MUTED)
            return
        if character == "M":
            self._start_merge()
            return
        if character == "*":
            self._mark_shown()
            return
        if character == "?":
            self._message = Text(self.OTHER_KEYS, style=ds.MUTED)
            return
        if character in ("$", "i"):
            self.mode, self.prompt, self.value = "prompt", "under" if character == "$" else "idle", ""
            self._message = None
            return
        if key == "m":
            self.show_merged = not self.show_merged
            self._reselect()
            self._message = Text("merged codes shown · u unmerges one" if self.show_merged
                                 else "merged codes hidden", style=ds.MUTED)
            return
        if key == "s":
            self._hide()
            self.post_message(self.SettingsRequested())
            return
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
            self._message = None
        elif key in ("a", "right") and self.current is not None:
            self.mode = "agents"
            self._list_scroll, self._scroll = self._scroll, 0
        elif key == "slash":
            self.mode = "filter"
        elif key == "escape" and self.filter:
            self.filter = ""
            self._reselect()
        elif key == "escape" and self.marks:
            self.marks = set()
        elif key in ("escape", "q"):
            self._close()

    def _mark_shown(self) -> None:
        """``*``: mark every code the list shows (after a filter); again unmarks them."""
        markable = [r for r in self.shown if self.collapsible(r)]
        if not markable:
            self._message = Text("no code here can be marked", style=ds.MUTED)
            return
        ids = {r.id for r in markable}
        if ids <= self.marks:
            self.marks -= ids
            what = f"unmarked {len(ids)} shown code{'s' if len(ids) != 1 else ''}"
        else:
            self.marks |= ids
            what = f"marked {len(ids)} shown code{'s' if len(ids) != 1 else ''}"
        marked = [r for r in self.rows or () if r.id in self.marks]
        tail = (f" · {len(marked)} marked, {money(sum(r.total for r in marked))} · M merges, "
                "c collapses" if marked else "")
        self._message = Text(what + tail, style=ds.MUTED)

    def _merge_key(self, key: str, character: Optional[str]) -> None:
        if self._target is not None:
            if key == "enter":
                ids, names = [r.id for r in self._sources], [r.name for r in self._sources]
                target = self._target
                self.post_message(self.MergeRequested(ids, names, target.id, target.name))
                self._message = Text(f"merging {self._sources_label()} into {target.name}…",
                                     style=ds.MUTED)
                self._chosen = target.id
                self.marks = set()
                self._sources, self._target, self.mode = [], None, "list"
                return
            self._target = None  # esc goes back to the picker; typing edits the query
            if key == "escape":
                return
        if key == "escape":
            self.mode, self._sources = "list", []
            return
        self._message = None
        cands = self.candidates()
        if key == "enter":
            self._pick_target()
            return
        if key in ("down", "up") and cands:
            step = 1 if key == "down" else -1
            self._pick = (min(self._pick, len(cands) - 1) + step) % len(cands)
            h = self._visible_rows()
            if self._pick < self._pick_scroll:
                self._pick_scroll = self._pick
            elif self._pick >= self._pick_scroll + h:
                self._pick_scroll = self._pick - h + 1
            return
        if key == "backspace":
            self.query = self.query[:-1]
        elif character and character.isprintable():
            self.query += character
        else:
            return
        self._pick, self._pick_scroll = 0, 0

    def _prompt_key(self, key: str, character: Optional[str]) -> None:
        if key == "escape":
            self.mode = "list"
        elif key == "enter":
            self.mode = "list"
            if self.value.strip():
                self._mark_where(self.prompt, self.value)
        elif key == "backspace":
            self.value = self.value[:-1]
        elif character and (character.isdigit() or character in ".$"):
            self.value += character

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
            return
        if key == "enter":
            self._submit()
            return
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
        row, new = self.current, self.value.strip()
        if row is None:
            return
        problem = self.error()
        if problem is not None:
            if problem.startswith("taken by"):
                # Refused: back to the list, where M merges (its picker starts on the name)
                self._taken = (row.id, new)
                self._message = Text(f"can't rename {row.name}: {problem}", style=ds.ERROR)
                self.mode = "list"
            return  # else the red line already says why
        if new == row.name:
            self.mode = "list"
            return
        self.post_message(self.RenameRequested(row.id, row.name, new))
        self._chosen = row.id
        self._message = Text(f"renaming {row.name} to {new}…", style=ds.MUTED)
        self.mode = "list"

