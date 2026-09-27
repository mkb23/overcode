"""
The mentor in the TUI (#483 P3): an occasional tip in the footer.

Every second (on the view-control tick) the cheap gates are checked first:
the mentor is switched on, someone is watching, no key for 5 s, nothing
open, the command bar not in use, and no agent waiting on you — a tip
must never compete with a permission prompt. Only then is the journey
worked out (off the UI thread, at most every 10 minutes) and a tip chosen.

Taking the tip's action, or opening the journey, is "engaged"; letting it
time out is "ignored". Both feed back into how often tips come.
"""

from __future__ import annotations

import time
from typing import Optional

from rich.text import Text

IDLE_SECONDS = 5.0
TIP_SECONDS = 90.0
JOURNEY_TTL_SECONDS = 600.0


class MentorMixin:
    _mentor_state = None
    _mentor_shown_this_run = 0
    _active_nudge = None
    _nudge_shown_at = 0.0
    _mentor_journey = None
    _mentor_journey_at = 0.0
    _mentor_busy = False

    def _init_mentor(self) -> None:
        from ..mentor import load_state, now_ms, save_state
        state = load_state()
        if not state.first_seen_ms:
            state.first_seen_ms = now_ms()
            save_state(state)
        self._mentor_state = state

    # ── the tick ───────────────────────────────────────────────────────

    def _mentor_tick(self) -> None:
        from ..mentor import mentor_dial, may_nudge, now_ms
        if self._active_nudge is not None:
            if time.monotonic() - self._nudge_shown_at > TIP_SECONDS:
                self._end_nudge("ignored")
            return
        if self._mentor_state is None or self._mentor_busy:
            return
        dial = mentor_dial()
        if dial == "off" or not getattr(self, "attended", True):
            return
        if not may_nudge(dial, self._mentor_state, self._mentor_shown_this_run, now_ms()):
            return
        if time.monotonic() - getattr(self, "_last_keypress", 0.0) < IDLE_SECONDS:
            return
        if self._any_dialog_visible() or self._command_bar_in_use() or self._someone_waiting():
            return
        self._mentor_busy = True
        self.run_worker(self._mentor_pick, thread=True, group="mentor", exclusive=True)

    def _command_bar_in_use(self) -> bool:
        from ..tui_widgets import CommandBar
        focused = self.focused
        return focused is not None and any(isinstance(n, CommandBar) for n in focused.ancestors_with_self)

    def _someone_waiting(self) -> bool:
        from ..status_constants import STATUS_WAITING_APPROVAL, STATUS_WAITING_USER
        for w in self._get_widgets_in_session_order():
            if getattr(w, "is_unvisited_stalled", False):
                return True
            if getattr(w, "detected_status", None) in (STATUS_WAITING_USER, STATUS_WAITING_APPROVAL):
                return True
        return False

    def _mentor_pick(self) -> None:
        """Worker: work out the journey (cached), choose a tip, hand it to the UI thread."""
        from ..command_palette import keys_by_action
        from ..journey import load_journey
        from ..mentor import choose, new_achievements, now_ms
        try:
            if self._mentor_journey is None or time.monotonic() - self._mentor_journey_at > JOURNEY_TTL_SECONDS:
                keymap = keys_by_action(self.BINDINGS)
                bound = frozenset(k for keys in keymap.values() for k in keys)
                self._activity.flush()
                self._mentor_journey = load_journey(keymap, bound)
                self._mentor_journey_at = time.monotonic()
            journey = self._mentor_journey
            fresh = new_achievements(journey, self._mentor_state)
            nudge = choose(journey, self._mentor_state, now_ms())
        except Exception:
            fresh, nudge = [], None
        self.call_from_thread(self._mentor_picked, nudge, fresh)

    def _mentor_picked(self, nudge, fresh) -> None:
        from ..mentor import save_state
        self._mentor_busy = False
        for c in fresh:
            self.notify(f"🏆 {c.name} — {c.why}", severity="information", timeout=10)
            self.record_activity("nudge", id=f"achievement:{c.id}", outcome="celebrated")
        if nudge is not None and not self._any_dialog_visible() and not self._someone_waiting():
            self._show_nudge(nudge)
        else:
            save_state(self._mentor_state)

    # ── showing and ending ─────────────────────────────────────────────

    def _show_nudge(self, nudge) -> None:
        from ..mentor import now_ms, save_state
        try:
            footer = self.query_one("#help-text")
        except Exception:
            return
        tip = Text()
        tip.append("💡 ", style="bold")
        tip.append(nudge.text, style="bold #ffd75f")
        tip.append("   ·   u your journey", style="dim")
        footer.update(tip)
        self._active_nudge = nudge
        self._nudge_shown_at = time.monotonic()
        self._mentor_shown_this_run += 1
        self._mentor_state.last_nudge_ms = now_ms()
        save_state(self._mentor_state)
        self.record_activity("nudge", id=nudge.id, kind_of=nudge.kind, surface="footer", outcome="shown")

    def _end_nudge(self, outcome: str) -> None:
        from ..mentor import now_ms, record_outcome, save_state
        nudge = self._active_nudge
        if nudge is None:
            return
        self._active_nudge = None
        record_outcome(self._mentor_state, nudge, outcome, now_ms())
        save_state(self._mentor_state)
        self.record_activity("nudge", id=nudge.id, surface="footer", outcome=outcome)
        self._update_footer()

    def mentor_saw_action(self, action: str, via: str) -> None:
        """Called for every user action: taking the tip (or opening the journey) is engaging."""
        nudge = self._active_nudge
        if nudge is None or via in ("auto", "agent"):
            return
        if action == nudge.action or action == "open_journey":
            self._end_nudge("engaged")
