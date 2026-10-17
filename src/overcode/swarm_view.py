"""How an expanded parent's virtual rows are laid out (pure, no Textual).

docs/design/agent-observability-0.6.1.md §4.1–§4.2, phase 2. The engine
publishes each row's state (``swarm.virtual_rows``); this module only
decides which lines exist under the parent's row, in what order, and what
each one carries for the column grid (``columns.render_virtual`` draws
the cells, ``tui_widgets.swarm_rows`` aligns them with the agent rows):

* groups before agents: one line per workflow run (🧩), then the team,
  then loose subagents (👥 agents); an agent appears only inside its group;
* a group with more than ``AUTO_COLLAPSE_ABOVE`` members opens collapsed
  and shows its top 3 "needs you" members (🟠 first, then 🟢 oldest
  first) and "+k more"; a group the person collapsed shows nothing;
* an open workflow shows its phases ("▶ Verify", 18/30 done), each opening
  on its own (by default the ones with live members, up to the same size);
* finished members fold into one "done" line per group (✅ 25 ❌ 1 in the
  SubAgents cell); that line opens (``<group>/done`` in ``opened``) to list
  them, newest first, as many as the engine published
  (``observability.done_rows``), then "+k older"; while it is open they
  show there and nowhere else;
* ``needs_you`` (the swarm-detail cycle's last step) opens by default only
  the groups and phases where a member waits on the person (🟠);
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

from .status_constants import WORKFLOW_ASCII, emoji_or_ascii
from .tui_helpers import format_duration

AUTO_COLLAPSE_ABOVE = 8
NEEDS_YOU_TOP = 3

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
# "+k more" / "+k older" line's count)
COUNT_GLYPHS = (("running", "▶"), ("yellow", "🟡"), ("waiting", "🟠"), ("idle", "⚪"),
                ("ok", "✅"), ("failed", "❌"), ("more", "+"))
KIND_WORDS = {"subagent": "subagent", "fork": "fork", "teammate": "teammate",
              "workflow": "workflow agent"}

Counts = Tuple[Tuple[str, int], ...]


@dataclass
class SwarmLine:
    """One line under the parent: a row (``key``) or a summary of some.

    It carries data, not text: the grid renders its cells
    (``columns.render_virtual``). ``row`` is the published virtual row for
    a group, phase or member line ({} for the summary lines).
    """

    key: str            # the virtual row, or a synthetic key for a summary line
    owner: str          # the row ← moves back to (a group, a phase, or "" for the parent)
    kind: str           # "group" | "phase" | "member" | "detail" | "more" | "done" | "older"
    depth: int
    row: dict = field(default_factory=dict)
    marker: str = ""    # the NAME cell's kind marker (👥 / 🧩), "" for none
    label: str = ""     # the NAME cell's text after the connector and marker
    fold: str = ""      # "▸" closed / "▾" open, for a line → opens; "" otherwise
    counts: Counts = ()  # the SubAgents cell: (COUNT_GLYPHS key, n), non-zero only
    summary: str = ""   # the summary column (a member: what it is doing); a detail line's text
    context: str = ""   # a member's agent type and phase (the long summary's lead)
    connector: str = ""  # tree connector and indent, set once every line is known

    @property
    def text(self) -> str:
        """The line as plain words (tests, logs): the NAME cell's text,
        its counts and its summary."""
        head = self.connector + (f"{self.marker} " if self.marker else "") + self.label
        counts = " ".join(f"{dict(COUNT_GLYPHS)[k]}{n}" for k, n in self.counts)
        return "  ".join(p for p in (head + (f" {self.fold}" if self.fold else ""),
                                     counts, self.summary) if p)


def rows_of(parent: str, virtual: Mapping[str, dict]) -> Dict[str, dict]:
    return {k: r for k, r in virtual.items() if isinstance(r, dict) and r.get("parent") == parent}


DONE_SUFFIX = "/done"


def done_key(group: str) -> str:
    """The done line's key (also the engine subscription for its members)."""
    return group + DONE_SUFFIX


def _members(rows: Dict[str, dict], group: str, phase: Optional[str] = None,
             any_phase: bool = False, live_only: bool = False) -> List[Tuple[str, dict]]:
    out = [(k, r) for k, r in rows.items() if r.get("row") == "member" and r.get("group") == group
           and (any_phase or r.get("phase") == phase)
           and not (live_only and r.get("light") == "done")]
    out.sort(key=lambda kr: (kr[1].get("light") == "done", kr[1].get("started_at") or 0, kr[0]))
    return out


def needs_you(members: Iterable[Tuple[str, dict]], top: int = NEEDS_YOU_TOP) -> List[Tuple[str, dict]]:
    """The members to show for a collapsed group: 🟠 first, then 🟢 oldest first."""
    order = {"orange": 0, "green": 1}
    live = [kr for kr in members if kr[1].get("light") in order]
    live.sort(key=lambda kr: (order[kr[1]["light"]], kr[1].get("started_at") or 0, kr[0]))
    return live[:top]


def light_glyph(row: dict, emoji_free: bool = False) -> Tuple[str, str]:
    """A group's or member's light: (glyph, style); a finished one's result."""
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


class Layout:
    """The lines for one expanded parent."""

    def __init__(self, parent: str, virtual: Mapping[str, dict],
                 opened: Optional[Mapping[str, bool]] = None,
                 details: Optional[Set[str]] = None, emoji_free: bool = False,
                 needs_you: bool = False, base_depth: int = 0) -> None:
        self.parent = parent
        self.needs_you = needs_you
        self.rows = rows_of(parent, virtual)
        self.opened = opened or {}
        self.details = details or set()
        self.emoji_free = emoji_free
        self.base_depth = base_depth  # the parent's own tree depth
        self.lines: List[SwarmLine] = []

    # -- public ------------------------------------------------------------

    def build(self) -> List[SwarmLine]:
        groups = sorted(((k, r) for k, r in self.rows.items() if r.get("row") == "group"),
                        key=lambda kr: (kr[1].get("order", 0), kr[0]))
        for gkey, group in groups:
            self._group(gkey, group)
        self._connect()
        return self.lines

    def is_open(self, key: str) -> bool:
        """Whether a group, phase or done line is open (the person's choice,
        else its size; a done line only when opened; in ``needs_you``, only
        where a member is 🟠)."""
        if key in self.opened:
            return self.opened[key]
        if key.endswith(DONE_SUFFIX):
            return False
        row = self.rows.get(key) or {}
        if row.get("row") == "phase":
            members = _members(self.rows, row.get("group"), key)
            live = any(r.get("light") != "done" for _, r in members)
            if self.needs_you:
                return any(r.get("light") == "orange" for _, r in members)
            return live and len(members) <= AUTO_COLLAPSE_ABOVE
        if self.needs_you:
            return bool((row.get("counts") or {}).get("waiting"))
        return len(_members(self.rows, key, any_phase=True)) <= AUTO_COLLAPSE_ABOVE

    # -- lines --------------------------------------------------------------

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

    def _group(self, gkey: str, group: dict) -> None:
        workflow = group.get("kind") == "workflow"
        members = _members(self.rows, gkey, any_phase=True)
        opened = self.is_open(gkey)
        n = group.get("agents") or len(members) or sum((group.get("counts") or {}).values())
        summary = f"{n} agent{'' if n == 1 else 's'}"
        if group.get("phase"):
            summary += (f" · phase {group['phase']} {group.get('phase_done', 0)}/"
                        f"{group.get('phase_started', 0)}")
        self._add(SwarmLine(gkey, "", "group", 1, row=group,
                            marker=MARKER_WORKFLOW if workflow else MARKER_SUBAGENT,
                            label=str(group.get("title") or group.get("kind")),
                            fold="▾" if opened else "▸",
                            counts=self._counts(group.get("counts") or {}), summary=summary))
        if not opened:
            if gkey not in self.opened:  # auto-collapsed: the top "needs you"
                self._needs_you(gkey, members, 2)
            return
        phases = sorted(((k, r) for k, r in self.rows.items()
                         if r.get("row") == "phase" and r.get("group") == gkey),
                        key=lambda kr: (kr[1].get("order", 0), kr[0]))
        done_open = self.is_open(done_key(gkey))
        for pkey, phase in phases:
            self._phase(gkey, pkey, phase, live_only=done_open)
        for mkey, member in _members(self.rows, gkey, None, live_only=done_open):
            self._member(mkey, member, gkey, 2)
        self._done(gkey, group, 2, done_open)

    def _phase(self, gkey: str, pkey: str, phase: dict, live_only: bool = False) -> None:
        members = _members(self.rows, gkey, pkey, live_only=live_only)
        started, done = phase.get("started", 0), phase.get("done", 0)
        live = any(r.get("light") != "done" for _, r in members)
        mark = "▶" if live or (started > done) else ("✔" if started and done >= started else "·")
        opened = self.is_open(pkey)
        self._add(SwarmLine(pkey, gkey, "phase", 2, row=phase,
                            label=f"{mark} {phase.get('title')}",
                            fold=("▾" if opened else "▸") if members else "",
                            counts=self._counts({"running": max(0, started - done), "ok": done}),
                            summary=f"phase {phase.get('title')} {done}/{started}"))
        if opened:
            for mkey, member in members:
                self._member(mkey, member, pkey, 3)
        elif pkey not in self.opened:
            self._needs_you(pkey, members, 3)

    def _needs_you(self, owner: str, members, depth: int) -> None:
        top = needs_you(members)
        for mkey, member in top:
            self._member(mkey, member, owner, depth)
        rest = len(members) - len(top)
        if top and rest > 0:
            self._add(SwarmLine(f"{owner}/+more", owner, "more", depth,
                                label=f"+{rest} more", counts=(("more", rest),),
                                summary="→ to open"))

    def _member(self, mkey: str, row: dict, owner: str, depth: int) -> None:
        context = [p for p in (row.get("agent_type"), KIND_WORDS.get(row.get("kind"))) if p]
        phase = self.rows.get(row.get("phase") or "") or {}
        if phase.get("title"):
            context.append(f"phase {phase['title']}")
        self._add(SwarmLine(mkey, owner, "member", depth, row=row, marker=MARKER_SUBAGENT,
                            label=str(row.get("title") or mkey.rsplit("/", 1)[-1]),
                            summary=what(row), context=" · ".join(context)))
        if mkey in self.details:
            self._detail(mkey, row, depth + 1)

    def _detail(self, mkey: str, row: dict, depth: int) -> None:
        parts = [p for p in (row.get("model"), row.get("agent_type"),
                             KIND_WORDS.get(row.get("kind"))) if p]
        phase = self.rows.get(row.get("phase") or "") or {}
        if phase.get("title"):
            parts.append(f"phase {phase['title']}")
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

    def _done(self, gkey: str, group: dict, depth: int, opened: bool = False) -> None:
        counts = group.get("counts") or {}
        ok, failed = counts.get("ok", 0), counts.get("failed", 0)
        if not ok and not failed:
            return
        key = done_key(gkey)
        self._add(SwarmLine(key, gkey, "done", depth, label="done", fold="▾" if opened else "▸",
                            counts=self._counts({"ok": ok, "failed": failed})))
        if not opened:
            return
        # Newest first: what the engine published (its most recent ``done_rows``)
        finished = [(k, r) for k, r in self.rows.items() if r.get("row") == "member"
                    and r.get("group") == gkey and r.get("light") == "done"]
        finished.sort(key=lambda kr: (kr[1].get("ended_at") or 0, kr[0]), reverse=True)
        for mkey, member in finished:
            self._member(mkey, member, key, depth + 1)
        older = ok + failed - len(finished)
        if older > 0:
            self._add(SwarmLine(f"{key}/+older", key, "older", depth + 1,
                                label=f"+{older} older" if finished else f"{older} finished",
                                counts=(("more", older),)))


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
           base_depth: int = 0) -> List[SwarmLine]:
    return Layout(parent, virtual, opened, details, emoji_free, needs_you, base_depth).build()


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
