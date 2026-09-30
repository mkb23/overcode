"""
Skill profiles dialog (#499).

One list of every skill overcode can find — the library (installed but off)
and each CLI's always-on personal skills — with a checkbox per skill for the
profile being edited. Most-used skills come first, so building a profile is
mostly ticking the top of the list. Changes save as they're made; agents pick
them up the next time they start.

Keys:
    j / k / ↑ / ↓     move
    space / enter     switch the skill on or off in this profile
    ← / →  [ / ]      previous / next profile
    n                 new profile (type a name, enter)
    p                 pin this profile to the focused agent's folder
    D D               delete this profile (press twice)
    esc / q           close
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.text import Text
from textual import events
from textual.message import Message

from . import dialog_style as ds
from .modal_base import ModalBase


class SkillsModal(ModalBase):
    """Build and edit skill profiles."""

    class Closed(Message):
        pass

    TITLE = "Skill profiles"
    WIDTH = 110
    # Name and source columns fit the longest value, within these bounds;
    # the full path of the highlighted skill is in the tip line (#511).
    _NAME_W = (12, 32)
    _SOURCE_W = (8, 24)
    _USES_W = 8
    _CHROME = 9  # border, header, rules, tip

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.entries: List[Any] = []            # skill_library.CatalogEntry
        self.profiles: Dict[str, List[str]] = {}
        self.profile: Optional[str] = None
        self.folder: Optional[str] = None       # where `p` pins
        self._scroll = 0
        self._naming: Optional[str] = None      # new profile name being typed
        self._confirm_delete = False
        self._message: Optional[Text] = None
        self._name_w = self._NAME_W[0]
        self._source_w = self._SOURCE_W[0]

    # ── public api ───────────────────────────────────────────────────────

    def show(                               # type: ignore[override]
        self,
        *,
        usage: Dict[str, int],
        profile: Optional[str] = None,
        folder: Optional[str] = None,
        app_ref: Optional[Any] = None,
    ) -> None:
        """Open on ``profile`` (else the folder's, else the first one)."""
        from .. import skill_library
        self.entries = skill_library.catalog(usage)
        self._fit_columns()
        self.profiles = skill_library.get_profiles()
        self.folder = folder
        pinned = skill_library.profile_for_folder(folder)
        for candidate in (profile, pinned):
            if candidate in self.profiles:
                self.profile = candidate
                break
        else:
            self.profile = next(iter(self.profiles), None)
        self._scroll = 0
        self._naming = None
        self._confirm_delete = False
        self._message = None
        self._save_focus(app_ref)
        self._show(0)

    # ── state ────────────────────────────────────────────────────────────

    @property
    def _members(self) -> set:
        if not self.profile:
            return set()
        from ..skill_library import resolve_skill_names
        known = {n for e in self.entries for n in e.skill.names}
        return set(resolve_skill_names(self.profiles.get(self.profile, []), known))

    def _in_profile(self, entry: Any) -> bool:
        return bool(entry.skill.names & self._members)

    def _visible_rows(self) -> int:
        try:
            height = self.app.size.height
        except Exception:
            height = 40
        return max(3, min(len(self.entries), height - 4 - self._CHROME))

    def _keep_in_view(self) -> None:
        h = self._visible_rows()
        if self.selected_index < self._scroll:
            self._scroll = self.selected_index
        elif self.selected_index >= self._scroll + h:
            self._scroll = self.selected_index - h + 1

    def _say(self, text: str, style: str = ds.MUTED) -> None:
        self._message = Text(text, style=style)

    # ── actions ──────────────────────────────────────────────────────────

    def _toggle(self) -> None:
        from .. import skill_library
        if not self.entries:
            return
        if not self.profile:
            self._say("No profile yet — press n to make one", ds.WARN)
            return
        entry = self.entries[self.selected_index]
        current = self.profiles.get(self.profile, [])
        if self._in_profile(entry):
            updated = [s for s in current if s not in entry.skill.names]
        else:
            updated = current + [entry.name]
        skill_library.save_profile(self.profile, updated)
        self.profiles[self.profile] = updated
        self._say("Saved · agents pick it up when they next start")

    def _switch(self, step: int) -> None:
        names = list(self.profiles)
        if not names:
            return
        i = names.index(self.profile) if self.profile in names else 0
        self.profile = names[(i + step) % len(names)]
        self._confirm_delete = False
        self._message = None

    def _create(self, name: str) -> None:
        from .. import skill_library
        if name in self.profiles:
            self.profile = name
            return
        try:
            skill_library.save_profile(name, [])
        except ValueError as e:
            self._say(str(e), ds.ERROR)
            return
        self.profiles[name] = []
        self.profile = name
        self._say(f"Created '{name}' · space adds the highlighted skill")

    def _delete(self) -> None:
        from .. import skill_library
        if not self.profile:
            return
        if not self._confirm_delete:
            self._confirm_delete = True
            self._say(f"Press D again to delete '{self.profile}'", ds.WARN)
            return
        skill_library.delete_profile(self.profile)
        gone = self.profile
        self.profiles.pop(gone, None)
        self.profile = next(iter(self.profiles), None)
        self._confirm_delete = False
        self._say(f"Deleted '{gone}'")

    def _pin(self) -> None:
        from .. import skill_library
        if not self.profile or not self.folder:
            return
        recorded = skill_library.pin_folder(self.folder, self.profile)
        self._say(f"New agents in {recorded} get '{self.profile}'")

    # ── render ───────────────────────────────────────────────────────────

    def hints(self) -> str:
        if self._naming is not None:
            return ds.hints(("↵", "create"), ("esc", "cancel"))
        return ds.hints(("space", "on/off"), ("← →", "profile"), ("n", "new"),
                        ("p", "pin folder"), ("D", "delete"), ("esc", "close"))

    def _header(self, w: int) -> Text:
        line = Text(" ")
        line.append("Profile  ", style=f"bold {ds.TEXT}")
        if self._naming is not None:
            line.append("new: ", style=ds.MUTED)
            line.append_text(ds.text_value(self._naming, len(self._naming)))
        elif self.profiles:
            line.append_text(ds.options(list(self.profiles), self.profile, w - 40))
            from ..skill_library import profile_for_folder
            if self.folder and self.profile and profile_for_folder(self.folder) == self.profile:
                line.append("   pinned to this folder", style=ds.STATE_ON)
            count = len(self.profiles.get(self.profile, []))
            line.append(f"   {count} skill{'s' if count != 1 else ''}", style=ds.MUTED)
        else:
            line.append("none yet — press n to make one", style=f"italic {ds.MUTED}")
        return ds.finish(line, w)

    @staticmethod
    def _source(entry: Any) -> str:
        if entry.always_on:
            return "on: " + "+".join(entry.always_on)
        return entry.skill.source_label

    def _fit_columns(self) -> None:
        def width(values, bounds):
            lo, hi = bounds
            return max(lo, min(hi, max((len(v) for v in values), default=0) + 2))
        self._name_w = width((e.name for e in self.entries), self._NAME_W)
        self._source_w = width((self._source(e) for e in self.entries), self._SOURCE_W)

    def _row(self, entry: Any, selected: bool, w: int) -> Text:
        on = self._in_profile(entry)
        line = ds.item(selected)
        line.append_text(ds.check(on if self.profile else False))
        line.append(" ")
        line.append_text(ds.fit(Text(entry.name, style="bold" if selected else ds.TEXT),
                                self._name_w))
        style = ds.WARN if entry.always_on else ds.ACCENT
        line.append_text(ds.fit(Text(self._source(entry), style=style), self._source_w))
        uses = f"{entry.uses} used" if entry.uses else ""
        line.append_text(ds.fit(Text(uses, style=ds.MUTED), self._USES_W))
        if entry.always_on and self.profile and not on:
            line.append("hidden ", style=ds.STATE_OTHER)
        line.append(entry.skill.description, style=ds.MUTED)
        return ds.finish(line, w)

    def _tip(self, w: int) -> Text:
        if self._message is not None:
            return ds.tip(w, self._message)
        if not self.entries:
            return ds.tip(w, Text("No skills found. Copy skill folders into ~/.overcode/skills, "
                                  "or: overcode skills library add <path>", style=ds.MUTED))
        entry = self.entries[self.selected_index]
        where = str(entry.skill.path).replace(str(Path.home()), "~", 1)
        if entry.always_on:
            left = Text(f"Always on in {', '.join(entry.always_on)} ({where}); "
                        f"a profile without it hides it", style=ds.MUTED)
        else:
            left = Text(where, style=ds.MUTED)
        right = Text(f"pin → {self.folder.replace(str(Path.home()), '~', 1)}",
                     style=ds.STATE_OTHER) if self.folder and self.profile else None
        return ds.tip(w, left, right)

    def render(self) -> Text:
        w = self.inner_width
        t = Text(no_wrap=True, overflow="crop")
        t.append_text(self._header(w))
        t.append("\n")
        t.append_text(ds.rule(w))
        t.append("\n")
        h = self._visible_rows()
        for i in range(self._scroll, min(len(self.entries), self._scroll + h)):
            t.append_text(self._row(self.entries[i], i == self.selected_index, w))
            t.append("\n")
        t.append_text(self._tip(w))
        return t

    # ── keys ─────────────────────────────────────────────────────────────

    def on_key(self, event: events.Key) -> None:
        if self._naming is not None:
            self._naming_key(event)
            return
        key = event.key
        if key != "D":
            self._confirm_delete = False
        if self.entries and self._navigate(event, len(self.entries)):
            self._message = None
            self._keep_in_view()
            self.refresh()
            return
        if key in ("space", "enter"):
            self._toggle()
        elif key in ("left", "left_square_bracket"):
            self._switch(-1)
        elif key in ("right", "right_square_bracket"):
            self._switch(1)
        elif key == "n":
            default = Path(self.folder).name.lower() if self.folder else ""
            self._naming = "".join(c if c.isalnum() else "-" for c in default).strip("-")[:40]
        elif key == "D":
            self._delete()
        elif key == "p":
            self._pin()
        elif key in ("escape", "q", "Q"):
            self._hide()
            self.post_message(self.Closed())
        else:
            return
        event.stop()
        self.refresh()

    def _naming_key(self, event: events.Key) -> None:
        key = event.key
        if key == "enter":
            name, self._naming = self._naming, None
            if name:
                self._create(name)
        elif key == "escape":
            self._naming = None
        elif key == "backspace":
            self._naming = self._naming[:-1]
        elif event.character and (event.character.isalnum() or event.character == "-"):
            self._naming = (self._naming + event.character.lower())[:40]
        event.stop()
        self.refresh()
