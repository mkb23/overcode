"""🌙 rows: what the person can do to a Claude background session (0.6.1, phase 3).

docs/design/agent-observability-0.6.1.md §4.4. A background session is
Claude's, not overcode's: the only actions are the ones the ``claude`` CLI
provides. Enter attaches (``claude attach <id>`` in a window shown in the
split's bottom pane), ``x`` stops it after a second press (``claude
stop``), and the palette removes it (``claude rm``, also confirmed). Every
other agent action refuses with a notice: there is no tmux window to send
keys to and no overcode record to change.

Everything that touches tmux or runs ``claude`` goes through ``app.split``.
"""

from __future__ import annotations

from typing import Any, Optional

from textual import work

from ..claude_bg import is_background
from .swarm import READ_ONLY_BLOCKED

# Agent actions a 🌙 row refuses (send keys, rename, sleep, heartbeat, ...);
# Enter and x are mapped to attach and stop instead.
BACKGROUND_BLOCKED = (READ_ONLY_BLOCKED | {"toggle_hook_detection", "open_instruction_history"}) \
    - {"kill_focused"}


class BackgroundSessionsMixin:
    """Attach / stop / rm for the focused 🌙 row."""

    def _background_widget(self) -> Optional[Any]:
        """The focused row when it is a 🌙 background session (and no virtual row is)."""
        if getattr(self, "swarm_cursor", None) is not None:
            return None
        widget = self._get_focused_widget()
        if widget is None or not is_background(widget.session):
            return None
        return widget

    def _background_action(self, name: str) -> bool:
        """``run_action``'s hook: True if ``name`` was handled for a 🌙 row."""
        widget = self._background_widget()
        if widget is None:
            return False
        if name == "send_enter_to_focused":
            self.action_attach_background_session()
            return True
        if name == "kill_focused":
            self.action_stop_background_session()
            return True
        if name in BACKGROUND_BLOCKED:
            self.notify("A Claude background session: Enter attaches, x stops it, "
                        "the palette (/) removes it", severity="information")
            return True
        return False

    def _focused_background(self, verb: str) -> Optional[Any]:
        widget = self._background_widget()
        if widget is None:
            self.notify(f"{verb}: focus a 🌙 background session first", severity="warning")
            return None
        return widget

    def action_attach_background_session(self) -> None:
        """Open the focused background session in the bottom pane (claude attach)."""
        widget = self._focused_background("Attach")
        if widget is None:
            return
        if not self.split.attach_background(widget.session):
            self.notify(f"Could not attach to {widget.session.name}: run "
                        f"`claude attach {widget.session.short_id}`", severity="warning")

    def action_stop_background_session(self) -> None:
        """Stop the focused background session (claude stop), after a second press."""
        widget = self._focused_background("Stop")
        if widget is None:
            return
        session = widget.session
        if widget.task_result:
            self.notify(f"{session.name} has already finished: remove it from the "
                        "palette (/ → Remove background session)", severity="information")
            return
        self._confirm_double_press(
            "kill",
            f"Press x again to stop background session '{session.name}' (claude stop)",
            lambda: self._run_claude_bg("stop", session.short_id, session.name),
            session_name=session.name,
        )

    def action_remove_background_session(self) -> None:
        """Delete the focused background session (claude rm), after a second request."""
        widget = self._focused_background("Remove")
        if widget is None:
            return
        session = widget.session
        self._confirm_double_press(
            "rm",
            f"Run again to remove background session '{session.name}' (claude rm)",
            lambda: self._run_claude_bg("rm", session.short_id, session.name),
            session_name=session.name,
            timeout=15.0,  # long enough to open the palette again
        )

    @work(thread=True, group="claude_bg")
    def _run_claude_bg(self, verb: str, short_id: str, name: str) -> None:
        """Worker: ``claude <verb> <id>``; the engine's next scan shows the outcome."""
        ok, said = self.split.run_claude([verb, short_id])
        if ok:
            self.call_from_thread(self.notify, f"{name}: {said or verb + ' done'}")
        else:
            self.call_from_thread(self.notify, f"claude {verb} {short_id} failed: {said}",
                                  severity="error")
