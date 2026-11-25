"""
The typing guard in the TUI (#560): prose typed into the monitor pane by
mistake is noticed, swallowed and rolled back, and Ctrl+Z undoes settings.

typing_guard.py (the pure module) decides when a run of keys is typing;
this mixin feeds it every key and acts:

- **Reversible** keys run at once, as always: stateful palette commands
  (they report their state, so they can be put back), navigation, and
  opening a dialog (closing it is the inverse).
- **Irreversible one-shots** (command_palette.IRREVERSIBLE_ACTIONS: quit,
  launching, sending keys to an agent, restarting the monitor) pressed in
  the middle of a run wait until the run settles, and are dropped if it
  turns out to be typing. One whose letter or punctuation key *starts* a
  run waits START_SETTLE for a second typing key first. Digits, Enter and
  Esc starting a run run at once: approvals stay instant.
- When a run becomes typing, every key is swallowed until RECOVER_QUIET of
  quiet (Ctrl+Z still works), the run's stateful changes are put back
  newest first, dialogs it opened are closed with their text discarded,
  held one-shots and armed double-press confirmations are dropped, the
  chosen agent goes back to the one chosen when the run started, and one
  warning says what happened.

Only keys go through this: the palette, clicks and `overcode view` are
never held back. Every stateful change, from a key or the palette, goes in
an undo history that Ctrl+Z (or the palette's "Undo last change") walks
back. ``tui.typing_guard: false`` in config.yaml (or the palette's
"Typing guard") turns the guard off; undo stays.
"""

from __future__ import annotations

import inspect
import logging
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Deque, Iterator, List, Optional

from textual import events

from ..typing_guard import (
    RECOVER_QUIET,
    RUN_GAP,
    START_SETTLE,
    UNDO_HISTORY,
    TypingRun,
    starts_immediately,
    typed_text,
    warning_text,
)

_log = logging.getLogger("overcode.tui.typing_guard")


@dataclass
class Change:
    """One undoable change: a stateful command run from a key or the palette."""
    action: str
    title: str
    agent_id: Optional[str]  # the agent an agent-scoped command acted on
    before: Optional[str]    # the state's label before (None: stateless, run the inverse)
    after: Optional[str]
    at: float
    stateless: bool = False


@dataclass
class Held:
    """An irreversible one-shot waiting for its run to settle."""
    title: str
    run: Callable[[], Any]
    due: float


@dataclass
class RunStart:
    """What the run may move and the guard puts back."""
    chosen: Optional[str]       # the remembered choice (None: none yet)
    highlighted: Optional[str]  # the agent under the highlight (a stand-in while it is hidden)
    swarm_cursor: Any
    job: Optional[str]
    dialogs: tuple


def _action_name(action: Any) -> str:
    if isinstance(action, str):
        return action.split("(", 1)[0].rsplit(".", 1)[-1]
    try:
        return str(action[1])
    except Exception:
        return str(action)


def _palette():
    from .. import command_palette as cp
    return cp


class TypingGuardMixin:
    """Mixed into SupervisorTUI first, so it sees every key and binding action."""

    # The clock the guard reads (tests replace it on the instance)
    _typing_clock: Callable[[], float] = staticmethod(time.monotonic)
    _typing_enabled: bool = False

    def _init_typing_guard(self) -> None:
        try:
            from ..config import get_typing_guard_enabled
            self._typing_enabled = get_typing_guard_enabled()
        except Exception:
            self._typing_enabled = True
        self._typing_run = TypingRun()
        self._typing_start: Optional[RunStart] = None
        self._typing_changes: List[Change] = []  # made by keys of the current run
        self._typing_held: List[Held] = []
        self._typing_released: set = set()  # action strings let through once
        self._typing_recovering = False
        self._typing_recover_last = 0.0
        self._typing_ignored: list = []  # (key, character) swallowed
        self._typing_report: dict = {"undone": [], "failed": [], "held": []}
        self._typing_timer: Any = None
        self._typing_quiet = False
        self._undo_history: Deque[Change] = deque(maxlen=UNDO_HISTORY)

    # ── keys ───────────────────────────────────────────────────────────

    async def on_event(self, event: events.Event) -> None:
        if isinstance(event, events.Key) and not getattr(event, "is_forwarded", False) \
                and self._typing_enabled:
            try:
                swallow = await self._typing_observe(event)
            except Exception:
                _log.exception("typing guard")
                swallow = False
            if swallow:
                event.stop()
                event.prevent_default()
                return
        await super().on_event(event)

    async def _typing_observe(self, event: events.Key) -> bool:
        """Feed one key to the run; True to swallow it."""
        now = self._typing_clock()
        if self._typing_recovering:
            if event.key == "ctrl+z":
                return False
            self._typing_recover_last = now
            self._typing_ignored.append((event.key, event.character))
            self._typing_arm()
            return True
        run = self._typing_run
        verdict = run.feed(event.key, event.character, self._activity_ctx(), now)
        if not verdict.counted:
            return False
        if verdict.index == 0:
            self._typing_start = self._typing_snapshot()
            self._typing_changes = []
        # Whatever is held waits for the run to settle
        for held in self._typing_held:
            held.due = max(held.due, now + RUN_GAP)
        if verdict.typing:
            await self._typing_recover(now)
            return True
        return False

    # ── actions ────────────────────────────────────────────────────────

    async def run_action(self, action: Any, default_namespace: Any = None,
                         namespaces: Any = None) -> bool:
        if default_namespace is not None and default_namespace is not self:
            return await super().run_action(action, default_namespace, namespaces)
        name = _action_name(action)
        released = isinstance(action, str) and action in self._typing_released
        if released:
            self._typing_released.discard(action)
        key = getattr(self, "_activity_binding_key", None)
        if key is not None and not released and self._typing_enabled:
            if self._typing_recovering and name != "undo_last_change":
                # A key of the run, dispatched after the run turned out typing
                if self._typing_irreversible(name):
                    self._typing_report["held"].append(self._typing_title(name))
                return True
            if self._typing_irreversible(name) and not self._any_dialog_visible():
                due = self._typing_hold_until(key)
                if due is not None:
                    self._typing_hold(self._typing_title(name),
                                      lambda: self._typing_release(action), due)
                    return True

        async def call() -> bool:
            return await super(TypingGuardMixin, self).run_action(
                action, default_namespace, namespaces)

        return await self._typing_recorded(name, call, from_key=key is not None)

    def _typing_irreversible(self, name: str) -> bool:
        cp = _palette()
        if name in cp.IRREVERSIBLE_ACTIONS:
            return True
        return getattr(self, "tui_mode", "agents") == "jobs" and name in cp.IRREVERSIBLE_IN_JOBS

    def _typing_hold_until(self, key: str) -> Optional[float]:
        """When a one-shot on ``key`` may run, or None to run it now."""
        run = self._typing_run
        now = self._typing_clock()
        if not run.active(now):
            return None
        if len(run.keys) >= 2 or run.keys[-1].key != key:
            return run.last_at + RUN_GAP  # mid-run: after the run settles
        if starts_immediately(key):
            return None
        return now + START_SETTLE

    def typing_hold_confirmed(self, title: str, callback: Callable[[], Any]) -> bool:
        """For a double-press confirmation about to fire: hold it while a run of
        two or more different keys is going (a deliberate double press is
        one key). True if held."""
        if not self._typing_enabled:
            return False
        run = self._typing_run
        now = self._typing_clock()
        if not run.active(now) or len({k.key for k in run.keys}) < 2:
            return False
        self._typing_hold(title, callback, run.last_at + RUN_GAP)
        return True

    def _typing_hold(self, title: str, run: Callable[[], Any], due: float) -> None:
        self._typing_held.append(Held(title, run, due))
        self._typing_arm()

    async def _typing_release(self, action: Any) -> None:
        if isinstance(action, str):
            self._typing_released.add(action)
        try:
            await self.run_action(action)
        finally:
            if isinstance(action, str):
                self._typing_released.discard(action)

    def _typing_title(self, name: str) -> str:
        cp = _palette()
        cmd = cp.COMMANDS_BY_ACTION.get(name)
        return cmd.title.rstrip("…") if cmd is not None else name

    # ── recording changes ──────────────────────────────────────────────

    def _typing_state(self, cmd: Any) -> Optional[str]:
        try:
            return cmd.state(self).current_label
        except Exception:
            return None

    def _typing_focused_agent(self) -> Optional[str]:
        try:
            widget = self._get_focused_widget()
        except Exception:
            return None
        return widget.session.id if widget is not None else None

    def _typing_fold_print(self) -> tuple:
        return (frozenset(getattr(self, "swarm_expanded", ()) or ()),
                frozenset(getattr(self, "swarm_details", ()) or ()),
                frozenset(getattr(self, "collapsed_parents", ()) or ()))

    def _typing_before(self, name: str) -> Optional[tuple]:
        """What to compare after ``name`` runs; None if it is not undoable."""
        cp = _palette()
        cmd = cp.COMMANDS_BY_ACTION.get(name)
        if cmd is not None and cmd.state is not None:
            agent = self._typing_focused_agent() if cmd.agent else None
            return (cmd, agent, self._typing_state(cmd))
        if name in cp.STATELESS_UNDO:
            return (None, None, self._typing_fold_print())
        return None

    def _typing_after(self, name: str, before: Optional[tuple], from_key: bool) -> None:
        if before is None:
            return
        cmd, agent, was = before
        now = self._typing_state(cmd) if cmd is not None else self._typing_fold_print()
        # A split resize has nothing to read back: it always counts
        if now == was and name not in ("split_grow", "split_shrink"):
            return
        self._typing_note(Change(
            name, self._typing_title(name), agent,
            was if cmd is not None else None, now if cmd is not None else None,
            self._typing_clock(), stateless=cmd is None), from_key)

    async def _typing_recorded(self, name: str, call: Callable[[], Any], from_key: bool) -> Any:
        """Run ``call`` (an action), noting the change if it is undoable."""
        before = self._typing_before(name)
        result = call()
        if inspect.isawaitable(result):
            result = await result
        self._typing_after(name, before, from_key)
        return result

    def typing_record_palette(self, name: str, method: Callable[[], Any]) -> None:
        """Run a palette command (as the palette does, not held back), noting
        the change so Ctrl+Z can put it back."""
        before = self._typing_before(name)
        result = method()
        if inspect.isawaitable(result):
            async def finish() -> None:
                await result
                self._typing_after(name, before, from_key=False)
            self.call_later(finish)
            return
        self._typing_after(name, before, from_key=False)

    def _typing_note(self, change: Change, from_key: bool) -> None:
        self._undo_history.append(change)
        if from_key and self._typing_run.keys:
            self._typing_changes.append(change)

    # ── undo ───────────────────────────────────────────────────────────

    @contextmanager
    def _typing_on_agent(self, agent_id: Optional[str]) -> Iterator[bool]:
        """Act on ``agent_id`` as if it were highlighted, without moving the
        choice or the bottom pane (agent-scoped undo). False if it is gone."""
        if agent_id is None:
            yield True
            return
        widgets = self._get_widgets_in_session_order()
        index = next((i for i, w in enumerate(widgets) if w.session.id == agent_id), None)
        if index is None:
            yield False
            return
        saved_index, saved_focus = self.focused_session_index, self.focused
        if index == saved_index and saved_focus is widgets[index]:
            yield True
            return
        was = self._suppress_focus_watcher
        self._suppress_focus_watcher = True
        try:
            self.focused_session_index = index
            self.screen.set_focus(widgets[index], scroll_visible=False)
            yield True
        finally:
            self.focused_session_index = saved_index
            try:
                keep = saved_focus if saved_focus is not None and saved_focus.is_attached else None
                self.screen.set_focus(keep, scroll_visible=False)
            except Exception:
                pass
            self._suppress_focus_watcher = was

    async def _typing_call(self, name: str) -> None:
        method = getattr(self, f"action_{name}", None)
        if method is None:
            raise LookupError(name)
        result = method()
        if inspect.isawaitable(result):
            await result

    async def _typing_undo(self, change: Change) -> bool:
        """Put one change back through its own command. True if it is back."""
        cp = _palette()
        driver = cp.UNDO_INVERSES.get(change.action, change.action)
        with self._typing_on_agent(change.agent_id) as here:
            if not here:
                return False
            if change.stateless:
                await self._typing_call(driver)
                return True
            cmd = cp.COMMANDS_BY_ACTION[change.action]
            try:
                steps = len(cmd.state(self).options) + 1
            except Exception:
                steps = 2
            steps = max(steps, 16 if change.action in cp.UNDO_INVERSES else steps)
            for _ in range(steps):
                now = self._typing_state(cmd)
                if now == change.before:
                    return True
                await self._typing_call(driver)
                if self._typing_state(cmd) == now:
                    return False  # it does not move (refused, or not now)
            return self._typing_state(cmd) == change.before

    async def _typing_undo_all(self, changes: List[Change]) -> tuple:
        undone: List[str] = []
        failed: List[str] = []
        self._typing_quiet = True
        try:
            for change in reversed(changes):
                try:
                    ok = await self._typing_undo(change)
                except Exception:
                    _log.exception("typing guard: undo %s", change.action)
                    ok = False
                (undone if ok else failed).append(change.title)
                try:
                    self._undo_history.remove(change)
                except ValueError:
                    pass
        finally:
            self._typing_quiet = False
        return undone, failed

    async def action_undo_last_change(self) -> None:
        """Ctrl+Z: put back the newest setting changed from a key or the palette."""
        if not self._undo_history:
            self.notify("Nothing to undo", severity="information")
            return
        change = self._undo_history[-1]
        undone, failed = await self._typing_undo_all([change])
        if undone:
            back = f": {change.before}" if change.before else ""
            self.notify(f"Undid {change.title}{back}", severity="information")
        else:
            self.notify(f"Couldn't undo {change.title}", severity="warning")

    def notify(self, *args: Any, **kwargs: Any) -> None:
        if self._typing_quiet:
            return
        return super().notify(*args, **kwargs)

    # ── recovering from a typing run ───────────────────────────────────

    def _typing_dialogs(self) -> list:
        out = list(self.query(".modal.visible"))
        for selector in ("#command-bar", "#help-overlay"):
            try:
                widget = self.query_one(selector)
            except Exception:
                continue
            if widget.has_class("visible"):
                out.append(widget)
        return out

    def _typing_snapshot(self) -> RunStart:
        try:
            chosen = self._chosen_session_id()
        except Exception:
            chosen = None
        return RunStart(chosen, self._typing_focused_agent(), getattr(self, "swarm_cursor", None),
                        getattr(self, "_chosen_job_id", None), tuple(self._typing_dialogs()))

    async def _typing_recover(self, now: float) -> None:
        run = self._typing_run
        self._typing_recovering = True
        self._typing_recover_last = now
        self._typing_ignored = [(k.key, k.character) for k in run.keys]
        held = [h.title for h in self._typing_held]
        self._typing_held = []
        self._typing_report = {"undone": [], "failed": [], "held": held}
        self._typing_disarm_confirmations()
        undone, failed = await self._typing_undo_all(self._typing_changes)
        self._typing_changes = []
        start = self._typing_start or self._typing_snapshot()
        await self._typing_close_dialogs(start)
        self._typing_restore_choice(start)
        self._typing_report["undone"] = undone
        self._typing_report["failed"] = failed
        try:
            self.record_activity("typing_guard", keys=len(run.keys), undone=len(undone),
                                 held=len(held), failed=len(failed))
        except Exception:
            pass
        self._typing_arm()

    def _typing_disarm_confirmations(self) -> None:
        pending = getattr(self, "_pending_confirmations", None)
        if pending:
            pending.clear()
        timers = getattr(self, "_confirm_timers", None)
        if timers:
            for timer in list(timers.values()):
                try:
                    timer.stop()
                except Exception:
                    pass
            timers.clear()

    async def _typing_close_dialogs(self, start: RunStart) -> None:
        """Close what the run opened, discarding what was typed into it."""
        from ..tui_widgets import CommandBar, HelpOverlay
        opened = [w for w in self._typing_dialogs() if w not in start.dialogs]
        for widget in reversed(opened):
            try:
                if isinstance(widget, CommandBar):
                    if start.chosen or start.highlighted:
                        # Its close puts the highlight on its target: make that the start's
                        widget.target_session_id = start.chosen or start.highlighted
                    widget.action_clear_and_unfocus()
                    continue
                if isinstance(widget, HelpOverlay):
                    widget.remove_class("visible")
                    continue
                for _ in range(4):
                    if not widget.has_class("visible"):
                        break
                    bindings = getattr(widget, "_bindings", None)
                    hit = bindings.key_to_bindings.get("escape") if bindings is not None else None
                    if hit:
                        await self.run_action(hit[0].action, widget)
                    elif hasattr(widget, "on_key"):
                        result = widget.on_key(events.Key("escape", None))
                        if inspect.isawaitable(result):
                            await result
                    else:
                        break
                if widget.has_class("visible"):
                    widget.remove_class("visible")
            except Exception:
                _log.exception("typing guard: closing %s", widget)
        if opened:
            self._dialog_did_close()

    def _typing_restore_choice(self, start: RunStart) -> None:
        """The agent (or job) chosen when the run started is chosen again."""
        try:
            moved = (self._chosen_session_id() != start.chosen if start.chosen
                     else self._typing_focused_agent() != start.highlighted)
            if moved:
                if not (start.chosen and self._focus_session_id(start.chosen)) \
                        and start.highlighted:
                    # The choice is hidden: its stand-in again, the choice kept
                    self._focus_session_id(start.highlighted)
                    if start.chosen:
                        self._remember_focus(start.chosen)
            elif getattr(self, "swarm_cursor", None) != start.swarm_cursor:
                self.swarm_cursor = start.swarm_cursor
                self._sync_swarm_rows()
            if start.job is not None and getattr(self, "_chosen_job_id", None) != start.job:
                widgets = self._get_job_widgets()
                for i, w in enumerate(widgets):
                    if w.job.id == start.job:
                        self._choose_job(i)
                        break
        except Exception:
            _log.exception("typing guard: restoring the choice")

    def _typing_finish(self) -> None:
        self._typing_recovering = False
        self._typing_run.reset()
        self._typing_start = None
        report = self._typing_report
        self._typing_report = {"undone": [], "failed": [], "held": []}
        from ..keymap import keymap_of
        label = keymap_of(self).label("undo_last_change", sep=" or ")
        if label.startswith("^"):
            label = "Ctrl+" + label[1:]
        self.notify(warning_text(typed_text(self._typing_ignored), report["undone"],
                                 report["failed"], report["held"], undo_key=label),
                    severity="warning", timeout=10)
        self._typing_ignored = []

    # ── the clock ──────────────────────────────────────────────────────

    def _typing_arm(self) -> None:
        """One timer, for the next thing due (a held one-shot, the end of a recovery)."""
        dues = [h.due for h in self._typing_held]
        if self._typing_recovering:
            dues.append(self._typing_recover_last + RECOVER_QUIET)
        if self._typing_timer is not None:
            try:
                self._typing_timer.stop()
            except Exception:
                pass
            self._typing_timer = None
        if not dues:
            return
        delay = max(0.02, min(dues) - self._typing_clock())
        try:
            self._typing_timer = self.set_timer(delay, self.typing_tick)
        except Exception:
            self._typing_timer = None

    async def typing_tick(self) -> None:
        """Run what is due: held one-shots whose run settled, the end of a recovery."""
        self._typing_timer = None
        now = self._typing_clock()
        if self._typing_recovering and now - self._typing_recover_last >= RECOVER_QUIET:
            self._typing_finish()
        if not self._typing_recovering:
            due = [h for h in self._typing_held if h.due <= now]
            self._typing_held = [h for h in self._typing_held if h.due > now]
            for held in due:
                try:
                    result = held.run()
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    _log.exception("typing guard: running %s", held.title)
        self._typing_arm()

    # ── the switch ─────────────────────────────────────────────────────

    def action_toggle_typing_guard(self) -> None:
        """tui.typing_guard on <-> off in config.yaml (#560)."""
        from ..config import save_typing_guard_enabled
        on = not self._typing_enabled
        try:
            save_typing_guard_enabled(on)
        except Exception as e:
            self.notify(f"Typing guard not changed: {e}", severity="error")
            return
        self._typing_enabled = on
        if not on:
            self._typing_run.reset()
            self._typing_held = []
            self._typing_recovering = False
        if on:
            message = ("Typing guard on: prose typed into this pane is ignored and "
                       "what it changed is put back")
        else:
            message = "Typing guard off: every key acts at once"
        self.notify(message, severity="information")
