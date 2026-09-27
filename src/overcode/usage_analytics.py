"""
Signals derived from the usage log (#483). Pure functions over records.

Every signal here is experimental: a hypothesis about what is worth
noticing, until real data shows it fires at a plausible rate and the nudges
it drives get engaged (docs/design/483-484/01_usage_log.md, "Proving the
signals"). The summary labels them so.

Records come from activity_log (iter_records, or summarize_log for day
summaries plus recent records); nothing here reads files.
"""

from __future__ import annotations

import re
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Optional

# Key-driven actions: shares by `via` say how an action is usually reached.
EFFICIENT_VIAS = frozenset({"key", "palette"})

WALK_ACTIONS = frozenset({"focus_next_session", "focus_previous_session"})
WALK_MIN_RUN = 6          # this many j/k presses in a row is a walk
REGRET_WINDOW_MS = 3000   # a toggle undone this quickly was probably a mistake
LOOKUP_WINDOW_MS = 10000  # help closed, then this soon a key action: looked it up
HESITATION_MS = 2000      # a gap this long before an action's key is a pause to think


def parse_since(text: str) -> float:
    """"7d", "24h", "30m", "90s" or plain seconds → a duration in seconds."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhdw]?)\s*", text.lower())
    if not m:
        raise ValueError(f"bad duration: {text!r} (try 7d, 24h, 30m)")
    n, unit = float(m.group(1)), m.group(2) or "s"
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]


def since_ms(text: str, now: Optional[float] = None) -> float:
    return ((now if now is not None else time.time()) - parse_since(text)) * 1000


@dataclass
class ActionUse:
    uses: int = 0
    by_via: Counter = field(default_factory=Counter)
    first_t: Optional[float] = None
    last_t: Optional[float] = None

    @property
    def user_uses(self) -> int:
        """Uses the person made: not the overagent's, not automatic ones."""
        return sum(n for via, n in self.by_via.items() if via not in ("agent", "auto"))

    @property
    def efficient_share(self) -> float:
        u = self.user_uses
        return sum(self.by_via[v] for v in EFFICIENT_VIAS) / u if u else 0.0


@dataclass
class Summary:
    since_ms: Optional[float]
    records: int = 0
    tui_runs: int = 0
    keys: int = 0
    clicks: int = 0
    actions: dict[str, ActionUse] = field(default_factory=dict)
    blocked: Counter = field(default_factory=Counter)
    # Keys pressed in the list that no action claimed. Which of them are
    # phantoms depends on what the TUI binds *now*, so finish() derives
    # phantom_keys from this; stored summaries keep the raw counts.
    unclaimed_keys: Counter = field(default_factory=Counter)
    phantom_keys: Counter = field(default_factory=Counter)
    toggle_regret: Counter = field(default_factory=Counter)
    dialogs: dict[str, Counter] = field(default_factory=dict)
    dialog_cancel_ms: dict[str, list] = field(default_factory=dict)
    help_lookups: Counter = field(default_factory=Counter)
    palette_misses: Counter = field(default_factory=Counter)
    palette_picks_with_key: Counter = field(default_factory=Counter)
    hesitations: Counter = field(default_factory=Counter)
    walks: int = 0
    walk_keys: int = 0
    cli: Counter = field(default_factory=Counter)
    contexts: Counter = field(default_factory=Counter)
    active_minutes: int = 0

    def to_dict(self) -> dict:
        actions = {
            name: {"uses": a.uses, "user_uses": a.user_uses, "by_via": dict(a.by_via),
                   "efficient_share": round(a.efficient_share, 3),
                   "first_t": a.first_t, "last_t": a.last_t}
            for name, a in sorted(self.actions.items(), key=lambda kv: -kv[1].uses)
        }
        return {
            "since_ms": self.since_ms, "records": self.records, "tui_runs": self.tui_runs,
            "keys": self.keys, "clicks": self.clicks, "active_minutes": self.active_minutes,
            "actions": actions,
            "cli": dict(self.cli.most_common()),
            "contexts": dict(self.contexts.most_common()),
            "experimental": {
                "blocked_actions": dict(self.blocked.most_common()),
                "phantom_keys": dict(self.phantom_keys.most_common()),
                "toggle_regret": dict(self.toggle_regret.most_common()),
                "dialogs": {n: dict(c) for n, c in self.dialogs.items()},
                "dialog_cancel_median_ms": {
                    n: int(statistics.median(v)) for n, v in self.dialog_cancel_ms.items() if v},
                "help_lookups": dict(self.help_lookups.most_common()),
                "palette_misses": dict(self.palette_misses.most_common()),
                "palette_picks_with_a_key": dict(self.palette_picks_with_key.most_common()),
                "hesitations": dict(self.hesitations.most_common()),
                "walks": {"count": self.walks, "keys": self.walk_keys},
            },
        }


def summarize(records: Iterable[dict], bound_keys: frozenset[str] = frozenset(),
              keys_by_action: Optional[dict[str, list[str]]] = None,
              since: Optional[float] = None) -> Summary:
    """Fold records into a finished Summary (fold, then finish).

    bound_keys: keys the TUI binds, so an unhandled key that is bound anyway
    (a modal's own key, say) is never counted as a phantom.
    keys_by_action: for "picked in the palette although it has a key".
    """
    return finish(fold(records, since=since), bound_keys, keys_by_action)


def fold(records: Iterable[dict], since: Optional[float] = None) -> Summary:
    """Fold records into an unfinished Summary, one record at a time.

    Records are never held: each TUI run (sid) keeps only the little state
    its signals need, so memory does not grow with the log. A record whose
    fields have unexpected types is skipped, not fatal. Nothing here depends
    on the TUI's current key bindings, so a folded Summary can be stored and
    merged with others (the usage log's day rollups) and finished later.
    """
    s = Summary(since_ms=since)
    runs: dict[str, _RunFolder] = {}
    minutes: set[int] = set()
    for r in records:
        if not isinstance(r, dict):
            continue
        try:
            s.records += 1
            kind = r.get("kind")
            if kind in ("key", "click", "action"):
                minutes.add(int(_num(r.get("t")) // 60000))
            if kind == "cli":
                cmd = str(r.get("cmd", "?"))
                s.cli[cmd] += 1
                use = s.actions.setdefault("cli:" + cmd, ActionUse())
                _count_use(use, str(r.get("via", "cli")), _num_or_none(r.get("t")))
            sid = str(r.get("sid", ""))
            run = runs.get(sid)
            if run is None:
                run = runs[sid] = _RunFolder(s)
            run.feed(r)
        except (TypeError, ValueError, AttributeError, KeyError):
            continue
    s.active_minutes = len(minutes)
    for run in runs.values():
        run.finish()
    return s


def finish(s: Summary, bound_keys: frozenset[str] = frozenset(),
           keys_by_action: Optional[dict[str, list[str]]] = None) -> Summary:
    """The signals that depend on today's bindings, and the palette-miss cleanup."""
    keymap = keys_by_action or {}
    s.phantom_keys = Counter({k: n for k, n in s.unclaimed_keys.items() if k not in bound_keys})
    s.palette_picks_with_key = Counter({
        name: a.by_via["palette"] for name, a in s.actions.items()
        if a.by_via.get("palette") and keymap.get(name)})
    # A miss is the query as finally typed: drop prefixes of a longer miss.
    # Sorted, any longer query starting with q comes straight after q.
    ordered = sorted(s.palette_misses)
    for q, nxt in zip(ordered, ordered[1:]):
        if nxt.startswith(q):
            del s.palette_misses[q]
    return s


# Cancel durations kept per dialog when summaries are merged: enough for a median.
MAX_CANCEL_SAMPLES = 500

_COUNTERS = ("blocked", "unclaimed_keys", "toggle_regret", "help_lookups", "palette_misses",
             "hesitations", "cli", "contexts")
_INTS = ("records", "tui_runs", "keys", "clicks", "walks", "walk_keys", "active_minutes")


def merge(into: Summary, other: Summary) -> Summary:
    """Add an unfinished Summary into another (both from fold or from_state)."""
    for name in _INTS:
        setattr(into, name, getattr(into, name) + getattr(other, name))
    for name in _COUNTERS:
        getattr(into, name).update(getattr(other, name))
    for name, a in other.actions.items():
        mine = into.actions.setdefault(name, ActionUse())
        mine.uses += a.uses
        mine.by_via.update(a.by_via)
        for t in (a.first_t, a.last_t):
            if t is not None:
                mine.first_t = t if mine.first_t is None else min(mine.first_t, t)
                mine.last_t = t if mine.last_t is None else max(mine.last_t, t)
    for name, c in other.dialogs.items():
        into.dialogs.setdefault(name, Counter()).update(c)
    for name, v in other.dialog_cancel_ms.items():
        kept = into.dialog_cancel_ms.setdefault(name, [])
        kept.extend(v)
        del kept[:-MAX_CANCEL_SAMPLES]
    return into


def to_state(s: Summary) -> dict:
    """An unfinished Summary as plain JSON, for the usage log's rollups."""
    return {
        **{name: getattr(s, name) for name in _INTS},
        **{name: dict(getattr(s, name)) for name in _COUNTERS},
        "actions": {n: {"uses": a.uses, "by_via": dict(a.by_via),
                        "first_t": a.first_t, "last_t": a.last_t}
                    for n, a in s.actions.items()},
        "dialogs": {n: dict(c) for n, c in s.dialogs.items()},
        "dialog_cancel_ms": {n: v[-MAX_CANCEL_SAMPLES:] for n, v in s.dialog_cancel_ms.items()},
    }


def from_state(d: dict) -> Summary:
    """The inverse of to_state. Raises ValueError on a malformed state."""
    try:
        s = Summary(since_ms=None)
        for name in _INTS:
            setattr(s, name, int(d.get(name, 0)))
        for name in _COUNTERS:
            getattr(s, name).update({str(k): int(n) for k, n in (d.get(name) or {}).items()})
        for n, a in (d.get("actions") or {}).items():
            s.actions[str(n)] = ActionUse(
                uses=int(a["uses"]),
                by_via=Counter({str(k): int(v) for k, v in a["by_via"].items()}),
                first_t=_num_or_none(a.get("first_t")), last_t=_num_or_none(a.get("last_t")))
        for n, c in (d.get("dialogs") or {}).items():
            s.dialogs[str(n)] = Counter({str(k): int(v) for k, v in c.items()})
        for n, v in (d.get("dialog_cancel_ms") or {}).items():
            s.dialog_cancel_ms[str(n)] = [float(x) for x in v]
        return s
    except (TypeError, KeyError, AttributeError) as e:
        raise ValueError(f"bad summary state: {e}") from e


def _num(v: object) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def _num_or_none(v: object) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _count_use(use: ActionUse, via: str, t: Optional[float]) -> None:
    use.uses += 1
    use.by_via[via] += 1
    if t is not None:
        use.first_t = t if use.first_t is None else min(use.first_t, t)
        use.last_t = t if use.last_t is None else max(use.last_t, t)


class _RunFolder:
    """One TUI run's records, fed in order, folded straight into the Summary."""

    def __init__(self, s: Summary) -> None:
        self.s = s
        self.pending_key: Optional[str] = None   # last list-context key not yet claimed by an action
        self.last_toggle: Optional[tuple[str, float]] = None
        self.help_closed_t: Optional[float] = None
        self.walk_run = 0
        self.last_key_dt: Optional[float] = None

    def _settle_pending(self) -> None:
        if self.pending_key is not None:
            self.s.unclaimed_keys[self.pending_key] += 1
        self.pending_key = None

    def _end_walk(self) -> None:
        if self.walk_run >= WALK_MIN_RUN:
            self.s.walks += 1
            self.s.walk_keys += self.walk_run
        self.walk_run = 0

    def finish(self) -> None:
        self._settle_pending()
        self._end_walk()

    def feed(self, r: dict) -> None:
        s = self.s
        kind = r.get("kind")
        t = _num(r.get("t"))
        if kind == "tui" and r.get("phase") == "start":
            s.tui_runs += 1
        elif kind == "key":
            self._settle_pending()
            s.keys += 1
            ctx = str(r.get("ctx", ""))
            s.contexts[ctx] += 1
            self.last_key_dt = _num_or_none(r.get("dt"))
            if ctx == "list":
                self.pending_key = str(r.get("key", "?"))
        elif kind == "click":
            self._settle_pending()
            s.clicks += 1
            self._end_walk()
        elif kind == "action":
            name = str(r.get("action", "?"))
            via = str(r.get("via", "auto"))
            if via == "key":
                self.pending_key = None
            if r.get("ns"):
                return  # a widget's own binding (a modal's cursor), not a TUI action
            use = s.actions.setdefault(name, ActionUse())
            _count_use(use, via, t)
            if r.get("ok") is False:
                s.blocked[name] += 1
            if via == "key" and self.last_key_dt is not None and self.last_key_dt >= HESITATION_MS:
                s.hesitations[name] += 1
            if name in WALK_ACTIONS and via == "key":
                self.walk_run += 1
            else:
                self._end_walk()
            if name.startswith("toggle_") and via in ("key", "palette", "click"):
                if self.last_toggle is not None and self.last_toggle[0] == name \
                        and t - self.last_toggle[1] <= REGRET_WINDOW_MS:
                    s.toggle_regret[name] += 1
                    self.last_toggle = None
                else:
                    self.last_toggle = (name, t)
            if self.help_closed_t is not None and via == "key" and name != "toggle_help":
                if t - self.help_closed_t <= LOOKUP_WINDOW_MS:
                    s.help_lookups[name] += 1
                self.help_closed_t = None
        elif kind == "dialog":
            name, phase = str(r.get("name", "?")), str(r.get("phase", "?"))
            s.dialogs.setdefault(name, Counter())[phase] += 1
            dur = _num_or_none(r.get("dur_ms"))
            if phase == "cancel" and dur is not None:
                s.dialog_cancel_ms.setdefault(name, []).append(dur)
            if name == "help" and phase in ("ok", "cancel"):
                self.help_closed_t = t
        elif kind == "palette_query":
            q = r.get("q")
            if r.get("n") == 0 and isinstance(q, str) and q.strip():
                s.palette_misses[q.strip().lower()] += 1


def render_summary(s: Summary, top: int = 12) -> str:
    """Plain-text report for `overcode activity summary`."""
    out: list[str] = []
    span = "all time" if s.since_ms is None else time.strftime(
        "since %Y-%m-%d %H:%M", time.localtime(s.since_ms / 1000))
    out.append(f"Activity {span}: {s.tui_runs} TUI runs, {s.active_minutes} active minutes, "
               f"{s.keys} keys, {s.clicks} clicks, {s.records} records")
    if not s.records:
        out.append("\nNothing recorded yet. Recording is on unless activity.record: false "
                   "in config.yaml or OVERCODE_ACTIVITY=0.")
        return "\n".join(out)

    def table(title: str, rows: list[tuple[str, str]]) -> None:
        if not rows:
            return
        out.append(f"\n{title}")
        width = max(len(a) for a, _ in rows)
        for a, b in rows:
            out.append(f"  {a:<{width}}  {b}")

    ranked = sorted(s.actions.items(), key=lambda kv: -kv[1].user_uses)
    table("Most used", [
        (name, f"{a.user_uses:>5}×  {_via_mix(a)}")
        for name, a in ranked[:top] if a.user_uses])
    exp = s.to_dict()["experimental"]
    out.append("\n── experimental signals (not yet proven useful) ──")
    table("Keys pressed that do nothing", [(k, f"{n}×") for k, n in list(exp["phantom_keys"].items())[:top]])
    table("Picked in the palette though it has a key", [
        (k, f"{n}×") for k, n in list(exp["palette_picks_with_a_key"].items())[:top]])
    if s.walks:
        table("Walking the list", [("j/k runs of 6+", f"{s.walks} runs, {s.walk_keys} keys")])
    table("Toggled then undone within 3 s", [(k, f"{n}×") for k, n in list(exp["toggle_regret"].items())[:top]])
    table("Blocked (key pressed while it could not run)", [
        (k, f"{n}×") for k, n in list(exp["blocked_actions"].items())[:top]])
    table("Dialogs (opened / ok / cancelled)", [
        (n, f"{c.get('open', 0)} / {c.get('ok', 0)} / {c.get('cancel', 0)}")
        for n, c in sorted(exp["dialogs"].items(), key=lambda kv: -kv[1].get("open", 0))[:top]])
    table("Looked up in help, then used", [(k, f"{n}×") for k, n in list(exp["help_lookups"].items())[:top]])
    table("Palette searches that found nothing", [
        (q, f"{n}×") for q, n in list(exp["palette_misses"].items())[:top]])
    table("Paused 2 s+ before", [(k, f"{n}×") for k, n in list(exp["hesitations"].items())[:top]])
    table("CLI", [(k, f"{n}×") for k, n in s.cli.most_common(top)])
    return "\n".join(out)


def _via_mix(a: ActionUse) -> str:
    u = a.user_uses
    parts = [f"{via} {n * 100 // u}%" for via, n in a.by_via.most_common()
             if via not in ("agent", "auto") and u]
    return " · ".join(parts)


# ── live stream helpers (overcode activity stream) ─────────────────────

def is_significant(rec: dict) -> bool:
    """Worth an agent's attention as it happens: not every key and click."""
    kind = rec.get("kind")
    if kind == "action":
        return rec.get("via") not in ("auto", "agent") and not rec.get("ns")
    if kind == "dialog":
        return rec.get("phase") == "cancel"
    return kind in ("nudge", "cli", "recording", "palette_query") and not (
        kind == "palette_query" and rec.get("n", 1) != 0)


def digest_line(records: list, window_s: float, bound_keys: frozenset = frozenset()) -> dict:
    """One digest for a window of records, with a sentence an agent can quote."""
    s = summarize(records, bound_keys)
    user_actions = [(n, a.user_uses) for n, a in s.actions.items() if a.user_uses]
    user_actions.sort(key=lambda x: -x[1])
    top = ", ".join(f"{n} ×{c}" for n, c in user_actions[:5]) or "nothing"
    span = f"{int(window_s // 60)}m" if window_s >= 60 else f"{int(window_s)}s"
    parts = [f"In the last {span}: {s.keys} keys, {s.clicks} clicks; most used {top}."]
    if s.phantom_keys:
        parts.append("Pressed with no effect: " + ", ".join(f"{k} ×{n}" for k, n in s.phantom_keys.most_common(3)) + ".")
    if s.palette_misses:
        parts.append("Searched the palette and found nothing: " + ", ".join(list(s.palette_misses)[:3]) + ".")
    cancels = {n: c.get("cancel", 0) for n, c in s.dialogs.items() if c.get("cancel")}
    if cancels:
        parts.append("Cancelled: " + ", ".join(f"{n} ×{c}" for n, c in cancels.items()) + ".")
    if s.walks:
        parts.append(f"Walked the list with j/k {s.walks}× ({s.walk_keys} keys).")
    return {"type": "rollup", "at": int(time.time() * 1000), "window_s": int(window_s),
            "description": " ".join(parts), "summary": s.to_dict()}
