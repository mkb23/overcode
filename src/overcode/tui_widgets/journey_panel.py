"""
The journey panel (`u`, #483): tracks, what's next, the hard way, capabilities.

Pull only: it opens when asked and never nudges. j/k picks a track (its
syllabus shows below), t tries the track's next step, a asks the overagent
about it, esc closes.
"""

from typing import Any, List, Optional

from rich.text import Text
from textual import events
from textual.message import Message

from . import dialog_style as ds
from .modal_base import ModalBase

GOOD = "#87d787"
LOCKED = "#626262"


class JourneyPanel(ModalBase):
    TITLE = "Your overcode journey"
    WIDTH = 104

    class TryRequested(Message):
        def __init__(self, action: str) -> None:
            super().__init__()
            self.action = action

    class AskRequested(Message):
        def __init__(self, question: str) -> None:
            super().__init__()
            self.question = question

    class Closed(Message):
        pass

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._journey: Any = None
        self._scroll = 0

    def hints(self) -> str:
        return ds.hints(("j/k", "track"), ("t", "try next"), ("a", "ask the overagent"),
                        ("pgup/pgdn", "scroll"), ("esc", "close"))

    def show(self, journey: Any, app_ref: Optional[Any] = None) -> None:
        self._journey = journey
        self._scroll = 0
        self._save_focus(app_ref)
        # Start on the first track with something next.
        start = next((i for i, t in enumerate(journey.tracks) if t.frontier), 0)
        self._show(start)

    # ── drawing ────────────────────────────────────────────────────────

    def _lines(self) -> List[Text]:
        j, w = self._journey, self.inner_width
        lines: List[Text] = []
        if j is None:
            return [Text("")]
        got = sum(1 for _, e in j.achievements if e)
        head = Text(f"  {j.records} actions recorded · achievements {got}/{len(j.achievements)}",
                    style=ds.MUTED)
        lines.append(head)
        lines.append(ds.rule(w))
        for i, t in enumerate(j.tracks):
            sel = i == self.selected_index
            line = ds.item(sel)
            line.append(f"{t.label:<14}", style=f"bold {ds.ACCENT}" if sel else ds.ACCENT)
            filled = round(12 * t.level / t.core_total) if t.core_total else 0
            line.append("█" * filled, style=GOOD)
            line.append("░" * (12 - filled), style=LOCKED)
            line.append(f" {t.level}/{t.core_total}  ", style="bold" if sel else ds.TEXT)
            if t.frontier:
                line.append("next → ", style=ds.MUTED)
                line.append(t.frontier[0].name, style=ds.TEXT)
            elif t.locked:
                from ..journey import needs
                line.append(f"locked: needs {needs(t.locked[0], j.earned_ids)}", style=LOCKED)
            else:
                line.append("complete", style=GOOD)
            lines.append(ds.finish(line, w))

        track = j.tracks[self.selected_index] if j.tracks else None
        if track is not None:
            lines.append(ds.rule(w))
            lines.append(Text(f"  {track.label} — {track.blurb}", style="bold"))
            for c in track.earned:
                lines.append(self._row("✓", c.name, c.why, GOOD, j))
            for c in track.frontier:
                lines.append(self._row("→", c.name, c.why, ds.ACCENT, j, key_for=c.try_action))
            for c in track.upcoming:
                lines.append(self._row("◦", c.name, c.why, ds.MUTED, j))
            for c in track.locked:
                from ..journey import needs
                lines.append(self._row("🔒", c.name, f"needs {needs(c, j.earned_ids)}", LOCKED, j))

        caps = {c.id: c for c in j.catalog}
        if j.hard_way:
            lines.append(ds.rule(w))
            lines.append(Text("  The hard way", style="bold"))
            for cid in j.hard_way[:4]:
                c, m = caps[cid], j.mastery[cid]
                line = Text(f"  {c.title}: {m.uses}×, {int(m.efficient_share * 100)}% by its key  ",
                            style=ds.TEXT)
                line.append_text(ds.keycaps(list(c.keys)))
                lines.append(line)
        if j.phantom_keys:
            lines.append(Text("  Keys you press that do nothing: "
                              + "  ".join(f"{k} ({n}×)" for k, n in j.phantom_keys), style=ds.MUTED))

        lines.append(ds.rule(w))
        legend = "  ".join(f"{g} {lvl}" for lvl, g in _GLYPHS.items())
        lines.append(Text(f"  Capabilities   {legend}   · not trackable", style=ds.MUTED))
        by_cat: dict = {}
        for c in j.catalog:
            by_cat.setdefault(c.category, []).append(c)
        for cat, cs in by_cat.items():
            line = Text(f"  {cat:<14}", style=ds.ACCENT)
            for c in cs:
                m = j.mastery[c.id]
                glyph = _GLYPHS[m.level] if c.tracked else "·"
                style = {"fluent": GOOD, "habitual": GOOD, "tried": ds.TEXT}.get(m.level, ds.MUTED)
                piece = Text(f"{glyph} {c.title}", style=style)
                if m.level == "unaware" and c.keys and c.tracked:
                    piece.append(f" {c.keys[0]}", style=ds.KEY)
                piece.append("   ")
                if line.cell_len + piece.cell_len > w:
                    lines.append(line)
                    line = Text(" " * 16)
                line.append_text(piece)
            lines.append(line)
        return lines

    def _row(self, mark: str, name: str, why: str, style: str, j: Any,
             key_for: Optional[str] = None) -> Text:
        line = Text(f"   {mark} ", style=style)
        line.append(f"{name}", style=style if mark != "→" else f"bold {style}")
        line.append(f" — {why}", style=ds.MUTED)
        if key_for:
            cap = next((c for c in j.catalog if c.id == key_for), None)
            if cap is not None and cap.keys:
                line.append("  ")
                line.append_text(ds.keycaps(list(cap.keys)))
        return ds.finish(line, self.inner_width)

    def render(self) -> Text:
        lines = self._lines()
        height = max(5, self._screen_height - 8)
        self._scroll = max(0, min(self._scroll, len(lines) - height))
        text = Text(no_wrap=True, overflow="crop")
        for i, line in enumerate(lines[self._scroll:self._scroll + height]):
            if i:
                text.append("\n")
            text.append_text(line)
        return text

    # ── keys ───────────────────────────────────────────────────────────

    def on_key(self, event: events.Key) -> None:
        j = self._journey
        n = len(j.tracks) if j else 0
        key = event.key
        event.stop()
        if n and self._navigate(event, n):
            return
        if key in ("escape", "q", "u"):
            self._hide()
            self.post_message(self.Closed())
        elif key in ("pagedown", "space"):
            self._scroll += 10
            self.refresh()
        elif key == "pageup":
            self._scroll = max(0, self._scroll - 10)
            self.refresh()
        elif key == "t" and n:
            track = j.tracks[self.selected_index]
            target = next((c.try_action for c in track.frontier if c.try_action), None)
            if target:
                self._hide()
                self.post_message(self.TryRequested(target))
        elif key == "a" and n:
            track = j.tracks[self.selected_index]
            if track.frontier:
                c = track.frontier[0]
                q = (f"From my overcode journey, the next step in {track.label} is "
                     f"\"{c.name}\" ({c.why}). Show me how, briefly, and point me at the key.")
            else:
                q = f"What should I learn next in overcode's {track.label} track?"
            self._hide()
            self.post_message(self.AskRequested(q))


_GLYPHS = {"unaware": "○", "seen": "◔", "tried": "◑", "habitual": "◐", "fluent": "●"}
