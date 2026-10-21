"""
Naming lab (#542, docs/design/auto-naming.md §9): edit the naming advice and
watch the names it gives the whole fleet.

The top half is the naming advice (Ctrl+O switches to the namer's whole
prompt, ``naming.md``); the bottom half is one row per agent: its name and
where it came from, the draft's pick, the candidates passed over and why,
and the pick's width against ``naming.max_length``. Picks two agents share
are flagged. ``human`` and ``parent`` names are locked: the namer never
renames them, but the lab still shows what it would pick.

The draft runs about every two seconds once it has settled, only for agents
whose prompt changed (see naming_lab.py). Ctrl+T switches between the live
fleet and the last archived agents; Ctrl+D pins the draft as A, so the rows
show A | B while B is edited. Enter on a row renames that agent to the
draft's pick as a human choice (locked), after a confirm. A (in the rows) renames
every agent to its pick the same way: those with a default or auto name
(y), or, after a second confirm, every one, names people chose included
(a). Picks two agents share are left out.

Saving writes ~/.overcode/prompts/naming-advice.md (or naming.md); saving
the built-in text (Ctrl+L then Ctrl+S) deletes the file instead.

Keys (the editor keeps its own: arrows, Ctrl+Z undo, Ctrl+C/X/V…):
    Ctrl+S  save               Ctrl+T  live / archived
    Ctrl+R  revert to saved    Ctrl+O  advice / whole prompt
    Ctrl+L  built-in default   Ctrl+D  pin the draft as A (again: unpin)
    Ctrl+N  editor / rows      ↑↓ Enter  pick a row, apply its name
    A       (in the rows) apply every pick (y: default and auto names; a: all, after a confirm)
    Esc     close (twice to discard unsaved edits)
"""

from __future__ import annotations

import time
from typing import Any, Callable, List, Optional, Tuple

from rich.cells import cell_len
from rich.text import Text
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static, TextArea

from .. import summarizer_prompts as sp
from ..naming_lab import LabAgent, LabPick, NamingLabRunner, collided, stats
from . import dialog_style as ds

RUN_INTERVAL = 2.0    # seconds between lab passes
SETTLE_SECONDS = 1.0  # a draft must be this still before it is run

KIND_LABELS = {"advice": "advice", "prompt": "whole prompt"}
VIEW_LABELS = {"live": "live", "archived": "archived"}

SOURCE_W = 13         # "parent locked"
LEN_W = 5             # "14/18"


class _Rows(Static, can_focus=True):
    """The fleet rows: focusable, so the focus can move here from the editor."""


class NamingLab(Vertical):
    """The lab dialog. Mounted hidden; ``show()`` opens it."""

    DEFAULT_CLASSES = "modal"
    TITLE = "Naming lab"
    WIDTH = 160

    BINDINGS = [
        Binding("ctrl+s", "save", "Save", show=False),
        Binding("ctrl+r", "revert", "Revert", show=False),
        Binding("ctrl+l", "load_default", "Default", show=False),
        Binding("ctrl+t", "switch_view", "Live/archived", show=False),
        Binding("ctrl+o", "switch_text", "Advice/prompt", show=False),
        # The editor deletes right on Ctrl+D (Delete still does)
        Binding("ctrl+d", "pin", "Pin as A", show=False, priority=True),
        # Not Tab: in the split, tmux takes it to switch panes
        Binding("ctrl+n", "switch_focus", "Editor/rows", show=False),
        Binding("up", "cursor_up", "Row up", show=False),
        Binding("down", "cursor_down", "Row down", show=False),
        Binding("enter", "apply", "Apply the pick", show=False),
        # In the rows (Ctrl+N), like Enter: in the editor A is typed
        Binding("A", "apply_all", "Apply every pick", show=False),
        Binding("escape", "close", "Close", show=False),
    ]

    class Closed(Message):
        pass

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Keys come from the `naming_lab` key scope (#510); BINDINGS above are its defaults
        from ..keymap import apply_to_widget
        apply_to_widget(self, "naming_lab")
        self.kind: str = "advice"
        self.view: str = "live"
        self.agents: List[LabAgent] = []
        self.loading: bool = False
        self.selected: int = 0
        self.pinned: Optional[Tuple[str, str]] = None   # A: (template, advice)
        self._saved: str = ""
        self._config_advice: str = ""
        self._max_length: int = 18
        self._runner: Optional[NamingLabRunner] = None
        self._load_fn: Optional[Callable[[str], List[LabAgent]]] = None
        self._apply_fn: Optional[Callable[[LabAgent, str], None]] = None
        self._apply_all_fn: Optional[Callable[[List[Tuple[LabAgent, str]]], None]] = None
        self._confirm_all: Optional[str] = None   # "ask", then "all" for every name
        self._auto_naming: str = ""
        self._available: bool = True
        self._app_ref: Any = None
        self._previous_focus: Any = None
        self._timer: Any = None
        self._edited_at: float = 0.0
        self._confirm_discard: bool = False
        self._confirm_apply: Optional[str] = None
        self._note: Optional[Text] = None
        self._loaded_text: Optional[str] = None
        self._today_usd: float = 0.0
        self._inner_width: int = self.WIDTH - 4
        self._rows_h: int = 8

    def compose(self):
        yield Static(id="naming-lab-head")
        yield TextArea(id="naming-lab-editor", soft_wrap=True, show_line_numbers=False)
        yield _Rows(id="naming-lab-rows")

    # ── public api ───────────────────────────────────────────────────────

    def show(
        self,
        *,
        runner: NamingLabRunner,
        load: Callable[[str], List[LabAgent]],
        available: bool,
        apply: Optional[Callable[[LabAgent, str], None]] = None,
        apply_all: Optional[Callable[[List[Tuple[LabAgent, str]]], None]] = None,
        auto_naming: str = "",
        config_advice: str = "",
        max_length: int = 18,
        app_ref: Any = None,
    ) -> None:
        """Open the lab. ``load(view)`` reads the agents for "live" or "archived"
        (run in a thread); ``apply(agent, name)`` renames one as a human choice,
        ``apply_all([(agent, name), ...])`` several, one after another.
        ``auto_naming`` says whether the daemon names agents (describe_auto_naming)."""
        self._runner = runner
        self._load_fn = load
        self._apply_fn = apply
        self._apply_all_fn = apply_all
        self._auto_naming = auto_naming
        self._confirm_all = None
        self._available = available
        self._config_advice = config_advice or ""
        self._max_length = max_length
        self._app_ref = app_ref
        self._previous_focus = getattr(app_ref, "focused", None) if app_ref else None
        self._load_text(self.kind)
        self.relayout()
        self.add_class("visible")
        self.editor.focus()
        self._load_agents(self.view)
        if self._timer is None:
            self._timer = self.set_interval(RUN_INTERVAL, self._tick)
        else:
            self._timer.resume()

    def hide(self) -> None:
        self.remove_class("visible")
        if self._timer is not None:
            self._timer.pause()
        if self._previous_focus is not None:
            try:
                self._previous_focus.focus()
            except Exception:
                pass
        self._previous_focus = None
        self.post_message(self.Closed())

    @property
    def editor(self) -> TextArea:
        return self.query_one("#naming-lab-editor", TextArea)

    @property
    def rows_widget(self) -> _Rows:
        return self.query_one("#naming-lab-rows", _Rows)

    @property
    def draft(self) -> str:
        try:
            return self.editor.text
        except Exception:   # not mounted
            return self._saved

    def _rows_focused(self) -> bool:
        try:
            return self.rows_widget.has_focus
        except Exception:
            return False

    @property
    def dirty(self) -> bool:
        return self.draft != self._saved

    def current(self) -> Tuple[str, str]:
        """The draft as the namer would use it: (template, advice)."""
        if self.kind == "prompt":
            return self.draft, self._saved_advice()
        return sp.load_naming_prompt(), self.draft

    def set_agents(self, view: str, agents: List[LabAgent]) -> None:
        """The agents ``load`` read (from the UI thread)."""
        if view != self.view:
            return
        self.agents = agents
        self.loading = False
        self.selected = min(self.selected, max(0, len(agents) - 1))
        self.relayout()
        self._tick()

    def renamed(self, session) -> None:
        """An agent the lab applied a name to, as it is now."""
        for agent in self.agents:
            if agent.id == session.id:
                agent.session = session
        self._draw()

    # ── layout ───────────────────────────────────────────────────────────

    def relayout(self) -> None:
        """Size and centre for the terminal, like ModalBase.relayout."""
        try:
            screen_w, screen_h = self.app.size
        except Exception:
            return
        width = max(60, min(self.WIDTH, screen_w - 2))
        top = 1 if screen_h < 30 else 2
        self._inner_width = width - 4
        self.styles.width = width
        self.styles.max_height = max(14, screen_h - top - 1)
        self.styles.offset = (max(0, (screen_w - width) // 2), top)
        avail = screen_h - top - 1 - 2 - 2    # borders, head
        editor_h = max(4, min(10, avail // 3))
        self._rows_h = max(3, min(len(self.agents) or 3, avail - editor_h - 3))
        self.editor.styles.height = editor_h
        self.rows_widget.styles.height = self._rows_h + 3
        self.border_title = self.TITLE
        self._draw()

    def _legend(self) -> str:
        """The keys along the bottom border; while A asks, its answers."""
        close = self._key("close").replace("Esc", "esc")
        if self._confirm_all == "ask":
            keys = (("y", "rename default/auto names"), ("a", "all names…"), (close, "cancel"))
        elif self._confirm_all == "all":
            keys = (("a", "rename ALL, names people chose too"), (close, "cancel"))
        elif self._rows_focused():
            # Enter and A act on the rows; in the editor they are typed
            keys = [
                ("↑↓", "pick"), (self._key("apply"), "rename"),
                (self._key("apply_all"), "rename all"),
                (self._key("switch_focus"), "back to the editor"),
                (self._key("save"), "save"), (self._key("switch_view"), "live/archived"),
                (self._key("pin"), "pin A"), (self._key("revert"), "revert"),
                (self._key("load_default"), "default"), (close, "close"),
            ]
        else:
            keys = [
                (self._key("switch_focus"), "focus the agents (to rename)"),
                (self._key("save"), "save"), (self._key("switch_view"), "live/archived"),
                (self._key("switch_text"), "advice/prompt"), (self._key("pin"), "pin A"),
                (self._key("revert"), "revert"), (self._key("load_default"), "default"),
                (close, "close"),
            ]
        # A narrow lab drops the keys least needed first: they stay in the help
        for least in ("default", "revert", "pin A", "live/archived", "advice/prompt", "save"):
            if cell_len(Text.from_markup(ds.hints(*((k, w) for k, w in keys if k))).plain) \
                    <= self._inner_width - 2:
                break
            keys = [(k, w) for k, w in keys if w != least]
        return ds.hints(*((k, what) for k, what in keys if k))

    @staticmethod
    def _key(action: str) -> str:
        from ..keymap import active
        return active().label(action, "naming_lab")

    # ── the text ─────────────────────────────────────────────────────────

    def _saved_advice(self) -> str:
        from ..naming_policy import user_advice
        return user_advice(self._config_advice) or sp.DEFAULT_NAMING_ADVICE

    def _saved_text(self, kind: str) -> str:
        return self._saved_advice() if kind == "advice" else sp.load_naming_prompt()

    @staticmethod
    def _default_text(kind: str) -> str:
        return sp.DEFAULT_NAMING_ADVICE if kind == "advice" else sp.DEFAULT_NAMING_PROMPT

    def _load_text(self, kind: str) -> None:
        self.kind = kind
        self._saved = self._saved_text(kind)
        self._set_text(self._saved)
        self._edited_at = 0.0
        self._confirm_discard = False
        self._draw()

    def _set_text(self, text: str) -> None:
        """Replace the editor's text from code (see SummaryPromptLab._set_text)."""
        self._loaded_text = text
        self.editor.load_text(text)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if self.draft == self._loaded_text:
            self._loaded_text = None
            self._draw()
            return
        self._loaded_text = None
        self._edited_at = time.monotonic()
        self._confirm_discard = False
        self._note = None
        self._draw()

    def action_save(self) -> None:
        text = self.draft
        kind = self.kind
        if text.strip() == self._default_text(kind).strip() or not text.strip():
            sp.reset_naming_file(kind)
            text = self._saved_text(kind)
            if kind == "advice" and self._config_advice.strip():
                self._note = Text("saved — file removed; naming.advice in config.yaml applies",
                                  style=ds.STATE_ON)
            else:
                self._note = Text("saved — using the built-in default (file removed)",
                                  style=ds.STATE_ON)
            self._set_text(text)
        else:
            path = sp.save_naming_file(kind, text)
            self._note = Text(f"saved to {_tilde(path)} — the namer uses it now", style=ds.STATE_ON)
        self._saved = text
        self._confirm_discard = False
        self._draw()

    def action_revert(self) -> None:
        self._set_text(self._saved)
        self._note = Text("reverted to the saved text", style=ds.MUTED)
        self._draw()

    def action_load_default(self) -> None:
        self._set_text(self._default_text(self.kind))
        self._note = Text(f"built-in default loaded — {self._key('save')} to save it", style=ds.MUTED)
        self._draw()

    def _unsaved_guard(self, action: str) -> bool:
        """True (and a warning) on the first try to leave unsaved edits."""
        if self.dirty and not self._confirm_discard:
            self._confirm_discard = True
            self._note = Text(f"unsaved edits — {self._key('save')} to save, or "
                              f"{self._key(action)} again to discard", style=ds.WARN)
            self._draw()
            return True
        return False

    def action_switch_text(self) -> None:
        if self._unsaved_guard("switch_text"):
            return
        self._load_text("prompt" if self.kind == "advice" else "advice")
        self._tick()

    def action_switch_view(self) -> None:
        self.view = "archived" if self.view == "live" else "live"
        self.selected = 0
        self._confirm_apply = None
        self._load_agents(self.view)

    def action_pin(self) -> None:
        draft = self.current()
        if self.pinned == draft:
            self.pinned = None
            self._note = Text("unpinned", style=ds.MUTED)
        else:
            self.pinned = draft
            self._note = Text("pinned as A — edits from here are B", style=ds.STATE_ON)
        self._draw()

    def on_descendant_focus(self, event) -> None:
        """The editor or the rows took the focus (Ctrl+N, a click): the legend follows."""
        self._draw()

    def action_switch_focus(self) -> None:
        if self.editor.has_focus:
            self.rows_widget.focus()
        else:
            self.editor.focus()
        self._confirm_apply = None
        self._draw()

    def action_cursor_up(self) -> None:
        self._move(-1)

    def action_cursor_down(self) -> None:
        self._move(1)

    def _move(self, step: int) -> None:
        if not self.agents:
            return
        self.selected = max(0, min(len(self.agents) - 1, self.selected + step))
        self._confirm_apply = None
        self._draw()

    def action_apply(self) -> None:
        if not self.agents:
            return
        agent = self.agents[self.selected]
        pick = self._pick(agent, self.current())
        if agent.archived:
            self._note = Text(f"'{agent.name}' is archived — nothing to rename", style=ds.MUTED)
        elif pick is None or not pick.pick or pick.error:
            self._note = Text(f"no pick for '{agent.name}' yet", style=ds.MUTED)
        elif pick.pick == agent.name:
            self._note = Text(f"'{agent.name}' already has that name", style=ds.MUTED)
        elif self._confirm_apply != agent.id:
            self._confirm_apply = agent.id
        else:
            self._confirm_apply = None
            if self._apply_fn is not None:
                self._apply_fn(agent, pick.pick)
            self._note = Text(f"renaming '{agent.name}' → '{pick.pick}' (your choice: locked)",
                              style=ds.STATE_ON)
        self._draw()

    def _bulk_plan(self, include_locked: bool) -> List[Tuple[LabAgent, str]]:
        """The (agent, pick) pairs A would apply: live agents with a pick
        that changes their name and that no other agent's pick shares."""
        if self.view != "live":
            return []
        draft = self.current()
        picks = [self._pick(a, draft) for a in self.agents]
        clash = _clashes(self.agents, picks)
        plan = []
        for agent, p in zip(self.agents, picks):
            if (agent.archived or p is None or p.running or p.error or p.keep or not p.pick
                    or p.pick == agent.name or agent.id in clash):
                continue
            if agent.locked and not include_locked:
                continue
            plan.append((agent, p.pick))
        return plan

    def action_apply_all(self) -> None:
        """A: ask, then rename every agent to its pick (see the module docstring)."""
        if self._confirm_all is not None:
            self._apply_plan(include_locked=self._confirm_all == "all")
            return
        self._confirm_apply = None
        if not self._bulk_plan(include_locked=True):
            self._note = Text("no picks to apply" + ("" if self.view == "live"
                                                     else " (archived agents are not renamed)"),
                              style=ds.MUTED)
            self._draw()
            return
        self._confirm_all = "ask"
        try:
            self.rows_widget.focus()   # y / a reach the lab, not the editor
        except Exception:
            pass
        self._draw()

    def on_key(self, event) -> None:
        if self._confirm_all is None or event.key not in ("y", "a"):
            return
        event.stop()
        event.prevent_default()
        if event.key == "y":
            self._apply_plan(include_locked=False)
        elif self._confirm_all == "ask":
            self._confirm_all = "all"
            self._draw()
        else:
            self._apply_plan(include_locked=True)

    def _apply_plan(self, include_locked: bool) -> None:
        plan = self._bulk_plan(include_locked)
        self._confirm_all = None
        if not plan:
            self._note = Text("no picks to apply", style=ds.MUTED)
        else:
            if self._apply_all_fn is not None:
                self._apply_all_fn(plan)
            self._note = Text(f"renaming {len(plan)} agent{'s' if len(plan) != 1 else ''} to "
                              "the draft's picks (your choice: locked)", style=ds.STATE_ON)
        self._draw()

    def action_close(self) -> None:
        if self._confirm_all is not None:
            self._confirm_all = None
            self._draw()
            return
        if self._confirm_apply is not None:
            self._confirm_apply = None
            self._draw()
            return
        if self._unsaved_guard("close"):
            return
        self.hide()

    # ── running ──────────────────────────────────────────────────────────

    def _load_agents(self, view: str) -> None:
        if self._load_fn is None:
            return
        self.loading = True
        self.agents = []
        self._draw()
        load = self._load_fn

        def work() -> None:
            try:
                agents = load(view)
            except Exception:
                agents = []
            try:
                self.app.call_from_thread(self.set_agents, view, agents)
            except Exception:
                pass
        self.run_worker(work, thread=True, group="naming-lab-load", exit_on_error=False)

    def _tick(self) -> None:
        """Start a pass in a thread (the draft, then A if pinned), unless one is
        running, the draft is still being typed, or no calls may be made."""
        runner = self._runner
        if runner is not None and runner.spend is not None:
            self._today_usd = runner.spend.daily.today(runner.spend._now())
        if not self.has_class("visible") or runner is None or not self._available or not self.agents:
            self._draw()
            return
        if runner.busy:
            return
        if self._edited_at and time.monotonic() - self._edited_at < SETTLE_SECONDS:
            return
        drafts = [self.current()] + ([self.pinned] if self.pinned else [])
        agents = list(self.agents)
        if not any(runner.pending(agents, t, a) for t, a in drafts):
            self._draw()
            return
        self._draw()

        def work() -> None:
            for template, advice in drafts:
                runner.run(agents, template, advice, on_update=self._redraw_from_thread)
            self._redraw_from_thread()
        self.run_worker(work, thread=True, group="naming-lab", exit_on_error=False)

    def _redraw_from_thread(self) -> None:
        try:
            self.app.call_from_thread(self._draw)
        except Exception:
            pass

    # ── drawing ──────────────────────────────────────────────────────────

    def _pick(self, agent: LabAgent, draft: Tuple[str, str]) -> Optional[LabPick]:
        return self._runner.result(agent, *draft) if self._runner is not None else None

    def _draw(self) -> None:
        try:
            head = self.query_one("#naming-lab-head", Static)
            rows = self.query_one("#naming-lab-rows", _Rows)
        except Exception:
            return
        head.update(self.render_head())
        rows.update(self.render_rows())
        self.border_subtitle = self._legend()

    def render_head(self) -> Text:
        w = self._inner_width
        t = Text(no_wrap=True, overflow="crop")
        line = Text(" ")
        line.append("Edit  ", style=f"bold {ds.TEXT}")
        line.append_text(ds.options(tuple(KIND_LABELS.values()), KIND_LABELS[self.kind]))
        line.append("   ")
        path = sp.naming_file_path(self.kind)
        if self.dirty:
            state = Text("● unsaved", style=f"bold {ds.WARN}")
        elif path.exists():
            state = Text(f"saved · {_tilde(path)}", style=ds.MUTED)
        elif self.kind == "advice" and self._config_advice.strip():
            state = Text(f"naming.advice in config.yaml · {self._key('save')} saves to "
                         f"{_tilde(path)}", style=ds.MUTED)
        else:
            state = Text(f"built-in default · {self._key('save')} saves to {_tilde(path)}",
                         style=ds.MUTED)
        room = max(8, w - cell_len(line.plain) - 1)
        if cell_len(state.plain) > room:
            state = ds.fit(state, room)
        t.append_text(ds.spread(line, state, w))
        t.append("\n")
        t.append_text(ds.finish(self._warning_line(), w))
        return t

    def _warning_line(self) -> Text:
        if self._note is not None:
            return Text(" ").append_text(self._note)
        if self.kind == "prompt":
            draft = self.draft
            if "{advice}" not in draft:
                return Text(" no {advice} — the naming advice never reaches the model", style=ds.WARN)
            import re
            unknown = sorted({n for n in re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", draft)
                              if n not in sp.NAMING_PLACEHOLDERS})
            if unknown:
                names = ", ".join("{" + n + "}" for n in unknown)
                return Text(f" left as typed (not placeholders): {names}", style=ds.WARN)
            return Text(" placeholders: " + " ".join("{" + n + "}" for n in sp.NAMING_PLACEHOLDERS),
                        style=ds.STATE_OTHER)
        out = Text(" ")
        if self._auto_naming:
            on = self._auto_naming.startswith("on")
            out.append("auto-naming ", style=f"bold {ds.TEXT}")
            out.append(self._auto_naming, style=ds.STATE_ON if on else ds.STATE_OTHER)
            out.append("  ·  ", style=ds.STATE_OTHER)
        out.append("the advice is the naming policy: the namer follows it; a repo's "
                   ".overcode/naming.md is added after it", style=ds.STATE_OTHER)
        return out

    def _rows_header(self, w: int) -> Text:
        focused = self._rows_focused()
        left = Text(" FLEET ", style=f"bold {ds.ACCENT}" if focused else f"bold {ds.MUTED}")
        left.append_text(ds.options(tuple(VIEW_LABELS.values()), VIEW_LABELS[self.view]))
        left.append(f"  {len(self.agents)} " if not self.loading else "  … ", style=ds.STATE_OTHER)
        left.append("· focused " if focused else f"· {self._key('switch_focus')} to focus ",
                    style=ds.ACCENT if focused else ds.STATE_OTHER)
        right = Text(style=ds.STATE_OTHER)
        b = stats(self.agents, [self._pick(a, self.current()) for a in self.agents])
        if self.pinned:
            a = stats(self.agents, [self._pick(x, self.pinned) for x in self.agents])
            right.append_text(_stats_text("A", a))
            right.append(" | ")
            right.append_text(_stats_text("B", b))
        else:
            right.append_text(_stats_text("", b))
        right.append(" ")
        rule_len = max(0, w - cell_len(left.plain) - cell_len(right.plain) - 1)
        left.append(" " + "─" * max(0, rule_len - 1), style=ds.STATE_OTHER)
        return ds.spread(left, right, w)

    def render_rows(self) -> Text:
        w = self._inner_width
        t = Text(no_wrap=True, overflow="crop")
        t.append_text(self._rows_header(w))
        body: List[Text] = []
        if self.loading:
            body.append(Text(" reading the agents…", style=ds.MUTED))
        elif not self.agents:
            body.append(Text(" no live local agents" if self.view == "live"
                             else " no archived agents", style=ds.MUTED))
        if body:
            for line in body[:self._rows_h]:
                t.append("\n")
                t.append_text(ds.finish(line, w))
            for _ in range(self._rows_h - len(body[:self._rows_h])):
                t.append("\n")
                t.append_text(ds.finish(Text(""), w))
            t.append("\n")
            t.append_text(ds.tip(w, self._problem() or Text(""), self._cost()))
            return t
        draft = self.current()
        picks_b = [self._pick(a, draft) for a in self.agents]
        picks_a = [self._pick(a, self.pinned) for a in self.agents] if self.pinned else None
        clash = _clashes(self.agents, picks_b)
        name_w = min(22, max(cell_len(a.name) for a in self.agents) + 1)
        pick_w = min(26, max([cell_len(p.pick) for p in picks_b + (picks_a or [])
                              if p is not None and p.pick] + [6]))
        start = max(0, min(self.selected - self._rows_h // 2, len(self.agents) - self._rows_h))
        for i in range(start, start + self._rows_h):
            t.append("\n")
            if i >= len(self.agents):
                t.append_text(ds.finish(Text(""), w))
                continue
            agent = self.agents[i]
            t.append_text(self._row(agent, picks_b[i], picks_a[i] if picks_a else None,
                                    clash.get(agent.id), i == self.selected, name_w, pick_w, w))
        t.append("\n")
        # A's question gets the whole line: its y / a must not be cropped
        cost = Text("") if self._confirm_all is not None else self._cost()
        t.append_text(ds.tip(w, self._tip_line(picks_b), cost))
        return t

    def _cost(self) -> Text:
        """What the lab spent, and the naming spend today against the cap."""
        runner = self._runner
        if runner is None:
            return Text("")
        out = Text(f"{runner.calls} calls ${runner.spent_usd:.4f}", style=ds.STATE_OTHER)
        if runner.spend is not None:
            out.append(f" · naming today ${self._today_usd:.2f} of ${runner.spend.cap:.2f} ")
        return out

    def _problem(self) -> Optional[Text]:
        """Why no calls are made, for the tip line, or None."""
        if not self._available:
            return Text(" no model — set summarizer: (or naming.provider) and its API key "
                        "(editing and saving still work)", style=ds.WARN)
        blocked = self._blocked()
        return Text(f" {blocked}", style=ds.ERROR) if blocked else None

    def _blocked(self) -> Optional[str]:
        runner = self._runner
        if runner is None or runner.spend is None:
            return None
        if runner.blocked:
            return runner.blocked
        cap = runner.spend.cap
        if self._today_usd >= cap:
            return f"naming cost cap (${cap:.2f} a day) reached — calls resume tomorrow"
        return None

    def _row(self, agent: LabAgent, b: Optional[LabPick], a: Optional[LabPick], clash: Optional[str],
             selected: bool, name_w: int, pick_w: int, w: int) -> Text:
        from ..name_source import name_marker
        focused = self._rows_focused()
        line = ds.item(selected and focused)
        if selected and not focused:
            line = Text("›", style=ds.MUTED)
        name = name_marker(agent.source) + agent.name
        line.append_text(ds.fit(Text(name, style=f"bold {ds.TEXT}"), name_w))
        line.append(" ")
        source = agent.source + (" locked" if agent.locked else "")
        line.append_text(ds.fit(Text(source, style=ds.MUTED if agent.locked else ds.STATE_OTHER),
                                SOURCE_W))
        line.append(" ")
        if self.pinned:
            line.append_text(self._pick_cell(agent, a, False, pick_w))
            line.append(" | ", style=ds.STATE_OTHER)
        line.append_text(self._pick_cell(agent, b, clash is not None, pick_w))
        line.append(" ")
        line.append_text(self._len_cell(b))
        line.append("  ")
        line.append_text(self._detail(agent, b, clash))
        return ds.finish(line, w)

    def _pick_cell(self, agent: LabAgent, p: Optional[LabPick], clash: bool, pick_w: int) -> Text:
        if not agent.has_inputs:
            cell = Text("-", style=ds.STATE_OTHER)
        elif p is None:
            cell = Text("…", style=ds.MUTED)
        elif p.running:
            cell = Text("running…", style=ds.ACCENT)
        elif p.error:
            cell = Text("error", style=ds.ERROR)
        elif p.keep or not p.pick or p.pick == agent.name:
            cell = Text("(keeps)", style=ds.MUTED)
        elif clash:
            cell = Text(p.pick, style=f"bold {ds.ERROR}")
        elif agent.locked:
            cell = Text(p.pick, style=ds.MUTED)
        else:
            cell = Text(p.pick, style="bold")
        return ds.fit(cell, pick_w)

    def _len_cell(self, p: Optional[LabPick]) -> Text:
        if p is None or not p.pick or p.error or p.running:
            return Text("-".rjust(LEN_W), style=ds.STATE_OTHER)
        n = len(p.pick)
        text = f"{n}/{self._max_length}"[:LEN_W].rjust(LEN_W)
        return Text(text, style=ds.WARN if n > self._max_length else ds.STATE_OTHER)

    @staticmethod
    def _detail(agent: LabAgent, p: Optional[LabPick], clash: Optional[str]) -> Text:
        if not agent.has_inputs:
            return Text("no recorded brief or prompts", style=ds.STATE_OTHER)
        if p is None or p.running:
            return Text("")
        if p.error:
            return Text(p.error, style=ds.ERROR)
        out = Text()
        if clash:
            out.append(f"same pick as {clash}  ", style=f"bold {ds.ERROR}")
        for i, (name, why) in enumerate(p.rejected):
            if i:
                out.append(" · ", style=ds.STATE_OTHER)
            out.append(name, style=ds.MUTED)
            out.append(f": {why}", style=ds.STATE_OTHER)
        if not p.rejected and p.why:
            out.append(p.why, style=ds.STATE_OTHER)
        return out

    def _tip_line(self, picks: List[Optional[LabPick]]) -> Text:
        if not self.agents:
            return Text("")
        if self._confirm_all is not None:
            return self._confirm_all_line()
        agent = self.agents[self.selected]
        p = picks[self.selected]
        if self._confirm_apply == agent.id and p is not None and p.pick:
            return Text(f" rename '{agent.name}' → '{p.pick}' as your choice (locked)? "
                        f"{self._key('apply')} to confirm, {self._key('close')} to cancel",
                        style=f"bold {ds.WARN}")
        problem = self._problem()
        if problem is not None:
            return problem
        if not self._rows_focused():
            return Text(f" in the advice · {self._key('switch_focus')} to the agents: "
                        f"{self._key('apply')} renames one, {self._key('apply_all')} all",
                        style=ds.STATE_OTHER)
        tip = Text(" ")
        tip.append(agent.name, style=f"bold {ds.TEXT}")
        if p is not None and p.candidates:
            tip.append(f"  candidates: {', '.join(p.candidates)}", style=ds.MUTED)
        if p is not None and p.why:
            tip.append(f"  why: {p.why}", style=ds.STATE_OTHER)
        return tip


    def _confirm_all_line(self) -> Text:
        """A's question: default and auto names (y), or every name (a, twice)."""
        free = len(self._bulk_plan(include_locked=False))
        every = len(self._bulk_plan(include_locked=True))
        locked = every - free
        if self._confirm_all == "all":
            return Text(f" rename ALL {_plural(every, 'agent')}, with {locked} named by people "
                        f"or parents? a: yes · {self._key('close')}: cancel",
                        style=f"bold {ds.ERROR}")
        t = Text(" ", style=f"bold {ds.WARN}")
        if free:
            t.append(f"rename {_plural(free, 'agent')} (default/auto names) to their picks? y: yes · ")
        else:
            t.append("no default or auto names to rename · ")
        if locked:
            t.append(f"a: all {every}, with {locked} people named · ")
        t.append(f"{self._key('close')}: cancel")
        return t


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _clashes(agents: List[LabAgent], picks: List[Optional[LabPick]]) -> dict:
    """{agent id: another agent with the same pick} for the picks that collide."""
    ids = collided(agents, picks)
    by_pick: dict = {}
    for agent, p in zip(agents, picks):
        if agent.id in ids:
            by_pick.setdefault(p.pick, []).append(agent.name)
    return {agent.id: next(n for n in by_pick[p.pick] if n != agent.name)
            for agent, p in zip(agents, picks) if agent.id in ids}


def _stats_text(label: str, s) -> Text:
    out = Text(style=ds.STATE_OTHER)
    if label:
        out.append(f"{label} ", style=f"bold {ds.TEXT}")
    out.append(f"{s.changed} changed · ")
    out.append(f"{s.collisions} collisions", style=f"bold {ds.ERROR}" if s.collisions else "")
    out.append(f" · mean {s.mean_length:.1f}")
    return out


def _tilde(path) -> str:
    import os
    home = os.path.expanduser("~")
    s = str(path)
    return "~" + s[len(home):] if s.startswith(home) else s
