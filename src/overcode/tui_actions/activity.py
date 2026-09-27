"""
Usage-log capture for the TUI (#483).

Three chokepoints see everything the user does:

- on_event: every key, click and paste, before any widget handles it, so
  keys a modal swallows are seen too.
- run_action: every action a binding (or anything else) runs, with whether
  check_action let it through and how it was invoked (key / click / auto).
  An action is `key` when it runs inside _check_bindings, Textual's one
  binding dispatcher, and carries that key; a burst of keys can't blur it.
- the palette handler in tui.py, which calls action_* directly and so
  records its own `action` with via=palette.

Dialog open/close is inferred after each input by comparing which dialog is
showing, so no modal needs to know about the log.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from textual import events

from ..activity_log import ActivityRecorder


class ActivityMixin:
    """Feeds SupervisorTUI's input into an ActivityRecorder."""

    _activity: ActivityRecorder
    _activity_via: Optional[str] = None
    _activity_via_at: float = 0.0
    _activity_binding_key: Optional[str] = None
    _activity_last_key: Optional[str] = None
    _activity_dialog: Optional[str] = None
    _activity_dialog_since: float = 0.0

    def _init_activity(self) -> None:
        self._activity = ActivityRecorder(tmux_session=self.tmux_session)

    def record_activity(self, kind: str, **fields: Any) -> None:
        """For widgets: add a record if recording is on. Never raises."""
        try:
            self._activity.record(kind, **fields)
        except Exception:
            pass

    # ── capture ────────────────────────────────────────────────────────

    async def on_event(self, event: events.Event) -> None:
        if isinstance(event, (events.Key, events.MouseDown, events.Paste)) \
                and not getattr(event, "is_forwarded", False):
            try:
                self._activity_observe(event)
            except Exception:
                pass
        await super().on_event(event)

    def _activity_observe(self, event: events.Event) -> None:
        rec = self._activity
        if not rec.active:
            return
        ctx = self._activity_ctx()
        if isinstance(event, events.Key):
            rec.record_key(event.key, event.character, ctx, event.is_printable)
            self._activity_last_key = event.key
        elif isinstance(event, events.MouseDown):
            try:
                widget, _ = self.get_widget_at(event.x, event.y)
            except Exception:
                widget = None
            rec.record_click(_describe_widget(widget), ctx)
            self._set_activity_via("click")
            self._activity_last_key = None
        else:  # Paste: length only, anywhere
            rec.record("paste", n=len(event.text), ctx=ctx)
        self.call_after_refresh(self._activity_settle)

    async def _check_bindings(self, key: str, priority: bool = False) -> bool:
        outer = self._activity_binding_key
        self._activity_binding_key = key
        try:
            return await super()._check_bindings(key, priority)
        finally:
            self._activity_binding_key = outer

    async def run_action(self, action: Any, default_namespace: Any = None,
                         namespaces: Any = None) -> bool:
        handled = await super().run_action(action, default_namespace, namespaces)
        try:
            if self._activity.active:
                ns = None
                if default_namespace is not None and default_namespace is not self:
                    ns = type(default_namespace).__name__
                if self._activity_binding_key is not None:
                    via, key = "key", self._activity_binding_key
                else:
                    via, key = self._take_activity_via(), None
                self._activity.record(
                    "action", action=_action_name(action), via=via, key=key,
                    ok=bool(handled), ns=ns)
                self.call_after_refresh(self._activity_settle)
        except Exception:
            pass
        return handled

    # An action a click caused (a link, a button) runs after the click's
    # on_event returns: the click is the cause if it came within this long
    # and no other action has claimed it.
    _VIA_WINDOW_S = 1.0

    def _set_activity_via(self, via: str) -> None:
        self._activity_via = via
        self._activity_via_at = time.monotonic()

    def _take_activity_via(self) -> str:
        via = self._activity_via
        self._activity_via = None
        if via is None or time.monotonic() - self._activity_via_at > self._VIA_WINDOW_S:
            return "auto"
        return via

    def _activity_settle(self) -> None:
        """After an input or action has been handled: note dialog changes."""
        try:
            current = self._activity_current_dialog()
        except Exception:
            return
        previous = self._activity_dialog
        if current == previous:
            return
        now = time.monotonic()
        if previous is not None:
            outcome = "cancel" if self._activity_last_key == "escape" else "ok"
            self._activity.record("dialog", name=previous, phase=outcome,
                                  dur_ms=int((now - self._activity_dialog_since) * 1000))
        if current is not None:
            self._activity.record("dialog", name=current, phase="open")
        self._activity_dialog = current
        self._activity_dialog_since = now

    # ── context ────────────────────────────────────────────────────────

    def _activity_current_dialog(self) -> Optional[str]:
        """The dialog showing now: a modal's id, "help", "fullscreen", or None."""
        for modal in self.query(".modal.visible"):
            return modal.id or type(modal).__name__
        for dialog_id, name in (("#help-overlay", "help"), ("#fullscreen-preview", "fullscreen")):
            try:
                if self.query_one(dialog_id).has_class("visible"):
                    return name
            except Exception:
                pass
        return None

    def _activity_ctx(self) -> str:
        """Where input is going: command_bar:<mode>, modal:<id>, help, fullscreen, jobs or list."""
        from ..tui_widgets import CommandBar
        focused = self.focused
        if focused is not None:
            for node in focused.ancestors_with_self:
                if isinstance(node, CommandBar):
                    return f"command_bar:{node.mode}"
                if "modal" in getattr(node, "classes", ()):
                    return f"modal:{node.id or type(node).__name__}"
        dialog = self._activity_current_dialog()
        if dialog is not None:
            return dialog if dialog in ("help", "fullscreen") else f"modal:{dialog}"
        if getattr(self, "tui_mode", "") == "jobs":
            return "jobs"
        return "list"

    # ── lifecycle ──────────────────────────────────────────────────────

    def _flush_activity(self) -> None:
        try:
            self._activity.flush()
        except Exception:
            pass

    def action_toggle_activity_recording(self) -> None:
        """Pause or resume the usage log for this TUI run (#483)."""
        rec = self._activity
        if not rec.enabled:
            self.notify("Activity recording is off in config (activity.record)",
                        severity="information")
            return
        if rec.paused:
            rec.paused = False
            rec.record("recording", phase="resume")
            self.notify("Activity recording resumed", severity="information")
        else:
            rec.record("recording", phase="pause")
            rec.flush()
            rec.paused = True
            self.notify("Activity recording paused for this session", severity="information")


def _action_name(action: Any) -> str:
    if isinstance(action, str):
        return action.split("(", 1)[0].rsplit(".", 1)[-1]
    try:
        return str(action[1])
    except Exception:
        return str(action)


def _describe_widget(widget: Any) -> str:
    if widget is None:
        return "none"
    name = type(widget).__name__
    return f"{name}#{widget.id}" if getattr(widget, "id", None) else name
