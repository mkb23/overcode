"""How an expanded parent's virtual rows are laid out (pure, no Textual).

docs/design/agent-observability-0.6.1.md §4.1–§4.2, phase 2. The engine
publishes each row's state (``swarm.virtual_rows``); this module only
decides which lines exist under the parent's row, in what order, and what
each one carries for the column grid (``columns.render_virtual`` draws
the cells, ``tui_widgets.swarm_rows`` aligns them with the agent rows).

Flat: no group or phase lines. The parent's row already says what its
delegated work adds up to (the SubAgents cell), so the lines under it are
its members themselves, one level down:

* live members first: 🟠 (needs approval) first, then the rest by start
  time, oldest first; then members that finished moments ago (newest
  first), for as long as the engine keeps them
  (``observability.finished_row_seconds``);
* what kind of agent each member is (subagent type, ``fork``, the
  workflow's name and phase, ``teammate``) is its ``type_label``, drawn in
  the TYPE column when the members on screen are of two or more types;
* more than ``AUTO_COLLAPSE_ABOVE`` live members: the top
  ``NEEDS_YOU_TOP`` "needs you" (🟠 first, then 🟢, 🟡, ⚪, oldest first)
  and "+k more"; → on it opens the full list (``members_key`` in
  ``opened``);
* after that a finished member is only a count in the parent's
  SubAgents cell (✅ 25 ❌ 1): no "done" line (owner, 2026-10-18: it
  repeated that cell and cost a line);
* ``needs_you`` (the swarm-detail cycle's last step) lists only the 🟠
  members until the person opens the rest;
* → on a member opens its read-only detail line.

Each line gets a tree connector (``├─`` / ``└─``) indented one level below
the parent, as tree sort indents a child agent. Nothing here reads a
clock: the one duration a line states (a finished member's run time) is
the difference of two published times, so a line changes only when its
rows do.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Mapping, Optional, Set, Tuple

from rich.cells import cell_len

from .status_constants import WORKFLOW_ASCII, emoji_or_ascii
from .tui_helpers import format_duration

AUTO_COLLAPSE_ABOVE = 8
NEEDS_YOU_TOP = 3
# The TYPE cell's widest label: a workflow's phase goes there only if it fits
TYPE_MAX = 22

MARKER_SUBAGENT = "👥"
MARKER_WORKFLOW = "🧩"

# Light → (glyph, style)
LIGHTS = {
    "green": ("🟢", "green"),
    "yellow": ("🟡", "yellow"),
    "orange": ("🟠", "orange1"),
    "idle": ("⚪", "grey70"),
}
DONE_GLYPHS = {"ok": ("✅", "green"), "failed": ("❌", "red")}
# Count key → glyph, in the order a SubAgents cell shows them ("more" is a
# "+k more" line's count)
COUNT_GLYPHS = (("running", "▶"), ("yellow", "🟡"), ("waiting", "🟠"), ("idle", "⚪"),
                ("ok", "✅"), ("failed", "❌"), ("more", "+"))
KIND_WORDS = {"subagent": "subagent", "fork": "fork", "teammate": "teammate",
              "workflow": "workflow agent"}

Counts = Tuple[Tuple[str, int], ...]


@dataclass
class SwarmLine:
    """One line under the parent: a member (``key``) or a summary of some.

    It carries data, not text: the grid renders its cells
    (``columns.render_virtual``). ``row`` is the published virtual row for
    a member line ({} for the summary lines).
    """

    key: str            # the virtual row, or a synthetic key for a summary line
    owner: str          # the line ← moves back to ("" for the parent's row)
    kind: str           # "member" | "detail" | "more"
    depth: int
    row: dict = field(default_factory=dict)
    marker: str = ""    # the NAME cell's kind marker (👥 / 🧩), "" for none
    label: str = ""     # the NAME cell's text after the connector and marker
    fold: str = ""      # "▸" closed / "▾" open, for a line → opens; "" otherwise
    counts: Counts = ()  # the SubAgents cell: (COUNT_GLYPHS key, n), non-zero only
    summary: str = ""   # the summary column (a member: what it is doing); a detail line's text
    context: str = ""   # a member's agent type and phase (the long summary's lead)
    type_label: str = ""  # a member's type, for the TYPE column
    connector: str = ""  # tree connector and indent, set once every line is known

    @property
    def text(self) -> str:
        """The line as plain words (tests, logs): the NAME cell's text,
        its type, its counts and its summary."""
        head = self.connector + (f"{self.marker} " if self.marker else "") + self.label
        counts = " ".join(f"{dict(COUNT_GLYPHS)[k]}{n}" for k, n in self.counts)
        return "  ".join(p for p in (head + (f" {self.fold}" if self.fold else ""),
                                     self.type_label, counts, self.summary) if p)


def rows_of(parent: str, virtual: Mapping[str, dict]) -> Dict[str, dict]:
    return {k: r for k, r in virtual.items() if isinstance(r, dict) and r.get("parent") == parent}


MEMBERS_SUFFIX = "/members"

# What a finished member shows as (the person's choice, the visibility
# dialog): its row for a while and its ✅/❌ count; only the ❌ ones; or
# nothing (live work only)
FINISHED_SHOW = "show"
FINISHED_FAILED = "failed"
FINISHED_HIDE = "hide"
FINISHED_MODES = (FINISHED_SHOW, FINISHED_FAILED, FINISHED_HIDE)


def shown_rollup(rollup: Optional[dict], finished: str) -> Optional[dict]:
    """The SubAgents counts as shown: ✅ dropped unless finished members
    show, ❌ dropped too when they are hidden. The engine's rollup is kept
    whole (the 👥 hover's total, the auto-open rule)."""
    if not rollup or finished == FINISHED_SHOW:
        return rollup
    drop = ("ok",) if finished == FINISHED_FAILED else ("ok", "failed")
    return {k: (0 if k in drop else v) for k, v in rollup.items()}


def finished_shown(row: dict, finished: str) -> bool:
    """Whether a finished member keeps its row under this setting."""
    if finished == FINISHED_HIDE:
        return False
    return finished == FINISHED_SHOW or row.get("result") == "failed"


def members_key(parent: str) -> str:
    """Opened: every live member listed, however many."""
    return parent + MEMBERS_SUFFIX


_NEEDS_YOU_ORDER = {"orange": 0, "green": 1, "yellow": 2, "idle": 3}


def needs_you(members: Iterable[Tuple[str, dict]], top: int = NEEDS_YOU_TOP) -> List[Tuple[str, dict]]:
    """The members a collapsed list shows: 🟠 first, then 🟢, 🟡, ⚪, oldest first."""
    live = [kr for kr in members if kr[1].get("light") in _NEEDS_YOU_ORDER]
    live.sort(key=lambda kr: (_NEEDS_YOU_ORDER[kr[1]["light"]], kr[1].get("started_at") or 0, kr[0]))
    return live[:top]


def light_glyph(row: dict, emoji_free: bool = False) -> Tuple[str, str]:
    """A member's light: (glyph, style); a finished one's result."""
    if row.get("light") == "done":
        glyph, style = DONE_GLYPHS.get(row.get("result") or "ok", DONE_GLYPHS["ok"])
    else:
        glyph, style = LIGHTS.get(row.get("light"), ("❔", "grey50"))
    return emoji_or_ascii(glyph, emoji_free), style


def marker_glyph(marker: str, emoji_free: bool = False) -> str:
    """A kind marker as drawn: 🧩's fallback is the SubAgents cell's."""
    if marker == MARKER_WORKFLOW and emoji_free:
        return WORKFLOW_ASCII
    return emoji_or_ascii(marker, emoji_free)


def type_label(row: dict) -> Tuple[str, bool]:
    """What kind of agent a member is, for the TYPE column, and whether its
    workflow phase is in it: the subagent type (``general-purpose``,
    ``Explore``…), ``fork``, ``teammate``, or the workflow's name with its
    phase when that fits (``review-060 · Verify``)."""
    kind = row.get("kind")
    if kind == "workflow":
        name = str(row.get("workflow") or "workflow")
        phase = row.get("phase_title")
        if phase:
            both = f"{name} · {phase}"
            if cell_len(both) <= TYPE_MAX:
                return both, True
        return name, False
    if kind in ("fork", "teammate"):
        return kind, False
    return str(row.get("agent_type") or "subagent"), False


def what(row: dict) -> str:
    """What a member is doing (its live light's meaning, or its activity),
    or how it ended: the short summary."""
    light = row.get("light")
    if light == "orange":
        return "needs approval in the parent's pane"
    if light == "idle":
        return "idle, listening"
    if light == "done":
        ran = _ran(row)
        text = row.get("result_text") or ("failed" if row.get("result") == "failed" else "done")
        return f"{text}" + (f" · ran {ran}" if ran else "")
    if light == "yellow":
        return "waiting on its own background work"
    return row.get("activity") or ""


def _live_order(kr: Tuple[str, dict]):
    return (kr[1].get("light") != "orange", kr[1].get("started_at") or 0, kr[0])


def _ended_order(kr: Tuple[str, dict]):
    return (kr[1].get("ended_at") or 0, kr[0])


class Layout:
    """The lines for one expanded parent."""

    def __init__(self, parent: str, virtual: Mapping[str, dict],
                 opened: Optional[Mapping[str, bool]] = None,
                 details: Optional[Set[str]] = None, emoji_free: bool = False,
                 needs_you: bool = False, base_depth: int = 0,
                 finished: str = FINISHED_SHOW) -> None:
        self.parent = parent
        self.needs_you = needs_you
        self.finished = finished
        self.rows = rows_of(parent, virtual)
        self.opened = opened or {}
        self.details = details or set()
        self.emoji_free = emoji_free
        self.base_depth = base_depth  # the parent's own tree depth
        self.lines: List[SwarmLine] = []

    # -- public ------------------------------------------------------------

    def build(self) -> List[SwarmLine]:
        members = [(k, r) for k, r in self.rows.items() if r.get("row") == "member"]
        live = sorted((kr for kr in members if kr[1].get("light") != "done"), key=_live_order)
        finished = sorted((kr for kr in members if kr[1].get("light") == "done"
                           and finished_shown(kr[1], self.finished)),
                          key=_ended_order, reverse=True)
        top = self._collapsed(live)
        for mkey, member in (top if top is not None else live):
            self._member(mkey, member, "", 1)
        if top is not None:
            rest = len(live) - len(top)
            self._add(SwarmLine(f"{self.parent}/+more", "", "more", 1,
                                label=f"+{rest} more", counts=(("more", rest),),
                                summary="→ to open"))
        else:
            # Finished moments ago (the engine keeps them a while): still here
            for mkey, member in finished:
                self._member(mkey, member, "", 1)
        self._connect()
        return self.lines

    def is_open(self, key: str) -> bool:
        """Whether the full member list is open (the person's choice;
        it is not by default)."""
        return bool(self.opened.get(key))

    # -- lines --------------------------------------------------------------

    def _collapsed(self, live: List[Tuple[str, dict]]) -> Optional[List[Tuple[str, dict]]]:
        """The live members a collapsed list shows, or None: all of them."""
        if self.is_open(members_key(self.parent)):
            return None
        if self.needs_you:
            orange = [kr for kr in live if kr[1].get("light") == "orange"]
            if orange and len(orange) < len(live):
                return orange
        if len(live) > AUTO_COLLAPSE_ABOVE:
            top = needs_you(live)
            return top if top else None
        return None

    def _add(self, line: SwarmLine) -> None:
        self.lines.append(line)

    def _connect(self) -> None:
        """Tree connectors: ``└─`` on the last line under its owner, ``├─``
        on the others, indented a level per depth below the parent's row."""
        last_of: Dict[str, int] = {}
        for i, line in enumerate(self.lines):
            if line.kind != "detail":
                last_of[line.owner] = i
        for i, line in enumerate(self.lines):
            indent = "  " * (self.base_depth + line.depth - 1)
            if line.kind == "detail":
                line.connector = indent + "  "
            else:
                line.connector = indent + ("└─" if last_of.get(line.owner) == i else "├─")

    @staticmethod
    def _counts(counts: Mapping[str, int]) -> Counts:
        return tuple((k, counts[k]) for k, _ in COUNT_GLYPHS if counts.get(k))

    def _member(self, mkey: str, row: dict, owner: str, depth: int) -> None:
        label, phase_shown = type_label(row)
        context = [p for p in (row.get("workflow") or row.get("agent_type"),
                               KIND_WORDS.get(row.get("kind"))) if p]
        phase = row.get("phase_title")
        if phase:
            context.append(f"phase {phase}")
        summary = what(row)
        if phase and not phase_shown and row.get("light") != "done":
            summary = f"{phase} · {summary}" if summary else str(phase)
        workflow = row.get("kind") == "workflow"
        self._add(SwarmLine(mkey, owner, "member", depth, row=row,
                            marker=MARKER_WORKFLOW if workflow else MARKER_SUBAGENT,
                            label=str(row.get("title") or mkey.rsplit("/", 1)[-1]),
                            summary=summary, context=" · ".join(context), type_label=label))
        if mkey in self.details:
            self._detail(mkey, row, depth + 1)

    def _detail(self, mkey: str, row: dict, depth: int) -> None:
        model = row.get("model")
        if model and row.get("model_inherited"):
            model = f"{model} (inherited)"
        parts = [p for p in (model, row.get("workflow") or row.get("agent_type"),
                             KIND_WORDS.get(row.get("kind"))) if p]
        if row.get("phase_title"):
            parts.append(f"phase {row['phase_title']}")
        started = row.get("started_at")
        if isinstance(started, (int, float)):
            parts.append(f"since {datetime.fromtimestamp(started).strftime('%H:%M:%S')}")
        ran = _ran(row)
        if ran:
            parts.append(f"ran {ran}")
        if row.get("tokens"):
            parts.append(f"{_tokens(row['tokens'])} tok")
        if row.get("cost_usd"):
            parts.append(f"${row['cost_usd']:.2f}")
        last = row.get("activity") or "-"
        result = row.get("result_text") or ({"ok": "done", "failed": "failed"}.get(row.get("result"))
                                            if row.get("result") else "(running)")
        text = " · ".join(parts) + f"   last: {last}   result: {result}"
        self._add(SwarmLine(f"{mkey}/detail", mkey, "detail", depth, summary=text))


def types_of(lines: Iterable[SwarmLine]) -> Set[str]:
    """The distinct types of the member lines (the TYPE column shows at two)."""
    return {ln.type_label for ln in lines if ln.kind == "member"}


def _ran(row: dict) -> Optional[str]:
    seconds = run_seconds(row)
    return format_duration(seconds) if seconds is not None else None


def run_seconds(row: dict) -> Optional[float]:
    """A finished member's run time, from its two published times."""
    start, end = row.get("started_at"), row.get("ended_at")
    if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end >= start:
        return end - start
    return None


def _tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def layout(parent: str, virtual: Mapping[str, dict], opened=None, details=None,
           emoji_free: bool = False, needs_you: bool = False,
           base_depth: int = 0, finished: str = FINISHED_SHOW) -> List[SwarmLine]:
    return Layout(parent, virtual, opened, details, emoji_free, needs_you, base_depth,
                  finished).build()


def step(lines: List[SwarmLine], cursor: Optional[str], direction: int) -> Tuple[Optional[str], bool]:
    """Move a cursor through the lines: (new cursor, still inside).

    ``cursor`` None is the parent's own row. Moving down from it enters the
    first line; moving past the last line, or up from the first, leaves
    (``inside`` False); detail lines are skipped.
    """
    keys = [ln.key for ln in lines if ln.kind != "detail"]
    if not keys:
        return None, False
    if cursor is None:
        return (keys[0], True) if direction > 0 else (None, False)
    try:
        i = keys.index(cursor)
    except ValueError:
        return None, True  # its line went away: back to the parent's row
    j = i + direction
    if j < 0:
        return None, True
    if j >= len(keys):
        return None, False
    return keys[j], True


def line_for(lines: List[SwarmLine], key: Optional[str]) -> Optional[SwarmLine]:
    return next((ln for ln in lines if ln.key == key), None)
