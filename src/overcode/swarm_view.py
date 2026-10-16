"""How an expanded parent's virtual rows are laid out (pure, no Textual).

docs/design/agent-observability-0.6.1.md §4.1–§4.2, phase 2. The engine
publishes each row's state (``swarm.virtual_rows``); this module only
arranges what it published into lines under the parent's row:

* groups before agents: one line per workflow run (🧩), then the team,
  then loose subagents (👥 agents); an agent appears only inside its group;
* a group with more than ``AUTO_COLLAPSE_ABOVE`` members opens collapsed
  and shows its top 3 "needs you" members (🟠 first, then 🟢 oldest
  first) and "+k more"; a group the person collapsed shows nothing;
* an open workflow shows its phases ("▶ Verify 18/30"), each opening on
  its own (by default the ones with live members, up to the same size);
* finished members fold into one "✅ 25 done · ❌ 1" line per group;
* → on a member opens its read-only detail line.

Nothing here reads a clock: the one duration shown (a finished member's
run time) is the difference of two published times, so a line changes
only when its rows do.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Mapping, Optional, Set, Tuple

from .status_constants import emoji_or_ascii
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
# Count key → glyph, in the order a group line shows them
COUNT_GLYPHS = (("running", "🟢"), ("yellow", "🟡"), ("waiting", "🟠"), ("idle", "⚪"),
                ("ok", "✅"), ("failed", "❌"))
KIND_WORDS = {"subagent": "subagent", "fork": "fork", "teammate": "teammate",
              "workflow": "workflow agent"}

Segments = List[Tuple[str, str]]


@dataclass
class SwarmLine:
    """One line under the parent: a row (``key``) or a summary of some."""

    key: str            # the virtual row, or a synthetic key for a summary line
    owner: str          # the row ← moves back to (a group, a phase, or "" for the parent)
    kind: str           # "group" | "phase" | "member" | "detail" | "more" | "done"
    depth: int
    segments: Segments = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(t for t, _ in self.segments)


def rows_of(parent: str, virtual: Mapping[str, dict]) -> Dict[str, dict]:
    return {k: r for k, r in virtual.items() if isinstance(r, dict) and r.get("parent") == parent}


def _members(rows: Dict[str, dict], group: str, phase: Optional[str] = None,
             any_phase: bool = False) -> List[Tuple[str, dict]]:
    out = [(k, r) for k, r in rows.items() if r.get("row") == "member" and r.get("group") == group
           and (any_phase or r.get("phase") == phase)]
    out.sort(key=lambda kr: (kr[1].get("light") == "done", kr[1].get("started_at") or 0, kr[0]))
    return out


def needs_you(members: Iterable[Tuple[str, dict]], top: int = NEEDS_YOU_TOP) -> List[Tuple[str, dict]]:
    """The members to show for a collapsed group: 🟠 first, then 🟢 oldest first."""
    order = {"orange": 0, "green": 1}
    live = [kr for kr in members if kr[1].get("light") in order]
    live.sort(key=lambda kr: (order[kr[1]["light"]], kr[1].get("started_at") or 0, kr[0]))
    return live[:top]


class Layout:
    """The lines for one expanded parent."""

    def __init__(self, parent: str, virtual: Mapping[str, dict],
                 opened: Optional[Mapping[str, bool]] = None,
                 details: Optional[Set[str]] = None, emoji_free: bool = False,
                 show_kind: bool = False) -> None:
        self.parent = parent
        self.rows = rows_of(parent, virtual)
        self.opened = opened or {}
        self.details = details or set()
        self.emoji_free = emoji_free
        self.show_kind = show_kind
        self.lines: List[SwarmLine] = []

    def e(self, char: str) -> str:
        if char == MARKER_WORKFLOW and self.emoji_free:
            return "Wf"  # as the 👥 cell's (🧩's own fallback is itself)
        return emoji_or_ascii(char, self.emoji_free)

    # -- public ------------------------------------------------------------

    def build(self) -> List[SwarmLine]:
        groups = sorted(((k, r) for k, r in self.rows.items() if r.get("row") == "group"),
                        key=lambda kr: (kr[1].get("order", 0), kr[0]))
        for gkey, group in groups:
            self._group(gkey, group)
        return self.lines

    def is_open(self, key: str) -> bool:
        """Whether a group or phase is open (the person's choice, else its size)."""
        if key in self.opened:
            return self.opened[key]
        row = self.rows.get(key) or {}
        if row.get("row") == "phase":
            members = _members(self.rows, row.get("group"), key)
            live = any(r.get("light") != "done" for _, r in members)
            return live and len(members) <= AUTO_COLLAPSE_ABOVE
        return len(_members(self.rows, key, any_phase=True)) <= AUTO_COLLAPSE_ABOVE

    # -- lines --------------------------------------------------------------

    def _add(self, key, owner, kind, depth, segments) -> None:
        self.lines.append(SwarmLine(key, owner, kind, depth, segments))

    def _indent(self, depth: int) -> str:
        return " " + "  " * depth + ("└ " if depth == 1 else "· ")

    def _counts(self, counts: Mapping[str, int]) -> str:
        return " ".join(f"{counts[k]}{self.e(g)}" for k, g in COUNT_GLYPHS if counts.get(k))

    def _light(self, row: dict) -> Tuple[str, str]:
        if row.get("light") == "done":
            glyph, style = DONE_GLYPHS.get(row.get("result") or "ok", DONE_GLYPHS["ok"])
            return self.e(glyph), style
        glyph, style = LIGHTS.get(row.get("light"), ("❔", "grey50"))
        return self.e(glyph), style

    def _group(self, gkey: str, group: dict) -> None:
        workflow = group.get("kind") == "workflow"
        members = _members(self.rows, gkey, any_phase=True)
        opened = self.is_open(gkey)
        glyph, style = self._light(group)
        marker = self.e(MARKER_WORKFLOW if workflow else MARKER_SUBAGENT)
        n = group.get("agents") or len(members) or sum((group.get("counts") or {}).values())
        title = f"{marker} {group.get('title') or group.get('kind')} {'▾' if opened else '▸'} {n} agent{'' if n == 1 else 's'}"
        segs: Segments = [(self._indent(1), "dim"), (f"{glyph} ", style), (title, "bold")]
        if group.get("phase"):
            segs.append((f"  phase {group['phase']} {group.get('phase_done', 0)}/"
                         f"{group.get('phase_started', 0)}", "cyan"))
        counts = self._counts(group.get("counts") or {})
        if counts:
            segs.append((f"   {counts}", ""))
        if group.get("cost_usd"):
            segs.append((f"   (${group['cost_usd']:.2f})", "dim"))
        self._add(gkey, "", "group", 1, segs)
        if not opened:
            if gkey not in self.opened:  # auto-collapsed: the top "needs you"
                self._needs_you(gkey, members, 2)
            return
        phases = sorted(((k, r) for k, r in self.rows.items()
                         if r.get("row") == "phase" and r.get("group") == gkey),
                        key=lambda kr: (kr[1].get("order", 0), kr[0]))
        for pkey, phase in phases:
            self._phase(gkey, pkey, phase, group)
        for mkey, member in _members(self.rows, gkey, None):
            self._member(mkey, member, gkey, 2)
        self._done(gkey, group, 2)

    def _phase(self, gkey: str, pkey: str, phase: dict, group: dict) -> None:
        members = _members(self.rows, gkey, pkey)
        started, done = phase.get("started", 0), phase.get("done", 0)
        live = any(r.get("light") != "done" for _, r in members)
        mark = "▶" if live or (started > done) else ("✔" if started and done >= started else "·")
        opened = self.is_open(pkey)
        segs: Segments = [(self._indent(2), "dim"), (f"{mark} ", "cyan"),
                          (f"{phase.get('title')}", "bold"), (f"  {done}/{started}", "cyan")]
        if members and not opened:
            segs.append(("  ▸", "dim"))
        self._add(pkey, gkey, "phase", 2, segs)
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
            self._add(f"{owner}/+more", owner, "more", depth,
                      [(self._indent(depth), "dim"), (f"+{rest} more  (→ to open)", "dim")])

    def _member(self, mkey: str, row: dict, owner: str, depth: int) -> None:
        glyph, style = self._light(row)
        done = row.get("light") == "done"
        name_style = "dim" if done else "bold"
        segs: Segments = [(self._indent(depth), "dim"), (f"{glyph} ", style),
                          (f"{self.e(MARKER_SUBAGENT)} ", "dim" if done else "magenta"),
                          (row.get("title") or mkey.rsplit("/", 1)[-1], name_style)]
        if self.show_kind:
            segs.append((f" [{KIND_WORDS.get(row.get('kind'), row.get('kind') or '?')}]", "dim"))
        what = self._what(row)
        if what:
            segs.append((f"  {what}", "dim" if done else ""))
        if row.get("cost_usd"):
            segs.append((f"  ${row['cost_usd']:.2f}", "dim"))
        self._add(mkey, owner, "member", depth, segs)
        if mkey in self.details:
            self._detail(mkey, row, depth + 1)

    @staticmethod
    def _what(row: dict) -> str:
        light = row.get("light")
        if light == "orange":
            return "needs approval in the parent's pane"
        if light == "idle":
            return "idle, listening"
        if light == "done":
            ran = _ran(row)
            text = row.get("result_text") or ("failed" if row.get("result") == "failed" else "done")
            return f"{text}" + (f" · {ran}" if ran else "")
        if light == "yellow":
            return "waiting on its own background work"
        return row.get("activity") or ""

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
        self._add(f"{mkey}/detail", mkey, "detail", depth,
                  [(self._indent(depth), "dim"), (text, "italic")])

    def _done(self, gkey: str, group: dict, depth: int) -> None:
        counts = group.get("counts") or {}
        ok, failed = counts.get("ok", 0), counts.get("failed", 0)
        if not ok and not failed:
            return
        text = f"{self.e('✅')} {ok} done" + (f" · {self.e('❌')} {failed}" if failed else "")
        self._add(f"{gkey}/done", gkey, "done", depth, [(self._indent(depth), "dim"), (text, "dim")])


def _ran(row: dict) -> Optional[str]:
    start, end = row.get("started_at"), row.get("ended_at")
    if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end >= start:
        return format_duration(end - start)
    return None


def _tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def layout(parent: str, virtual: Mapping[str, dict], opened=None, details=None,
           emoji_free: bool = False, show_kind: bool = False) -> List[SwarmLine]:
    return Layout(parent, virtual, opened, details, emoji_free, show_kind).build()


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

