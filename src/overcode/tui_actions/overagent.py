"""
The `e` key: open the overagent (#484).

Focuses the most recent overagent row; in `overcode tmux` split mode the
bottom pane follows it and gets the keyboard, so you can type to it at
once. With none running, launches one named "overagent" in ~/.overcode.
"""

from __future__ import annotations

import subprocess


class OveragentMixin:
    """Mixed into SupervisorTUI."""

    _overagent_pending: bool = False

    def action_open_overagent(self) -> None:
        from ..overagent import find_overagent
        session = find_overagent(list(self.sessions))
        if session is not None:
            self._focus_overagent(session.id)
            return
        if self._overagent_pending:
            self.notify("The overagent is starting…", severity="information")
            return
        self._launch_overagent()

    def _focus_overagent(self, session_id: str) -> None:
        for i, w in enumerate(self._get_widgets_in_session_order()):
            if w.session.id == session_id:
                self._user_navigated = True
                self.focused_session_index = i
                break
        else:
            self.notify("The overagent is hidden by a filter", severity="warning")
            return
        if self.compact:
            # The bottom pane now shows the overagent's window: hand it the keyboard.
            try:
                from ..tui import _tmux_base
                subprocess.run([*_tmux_base(), "select-pane", "-t", self._bottom_pane_target()],
                               capture_output=True, timeout=2)
            except (subprocess.SubprocessError, OSError):
                pass
        else:
            self.notify("Overagent focused — i to talk to it, or attach its window",
                        severity="information")

    def _launch_overagent(self) -> None:
        from ..overagent import DEFAULT_NAME, OVERAGENT_BACKEND
        from ..settings import get_overcode_dir

        taken = {s.name for s in self.sessions}
        name, n = DEFAULT_NAME, 2
        while name in taken:
            name, n = f"{DEFAULT_NAME}-{n}", n + 1
        directory = get_overcode_dir()
        directory.mkdir(parents=True, exist_ok=True)
        self._overagent_pending = True
        self.notify(f"Starting the overagent ({name})…", severity="information")
        self._launch_overagent_async(name, str(directory), OVERAGENT_BACKEND)

    def _launch_overagent_async(self, name: str, directory: str, backend: str) -> None:
        # A question from the journey panel rides along as the first prompt.
        prompt = getattr(self, "_overagent_question", None)
        self._overagent_question = None

        def work() -> None:
            error = None
            session = None
            try:
                session = self.launcher.launch(name=name, start_directory=directory, backend=backend,
                                               initial_prompt=prompt)
            except Exception as e:  # launcher raises a variety of errors
                error = str(e)
            self.call_from_thread(self._overagent_launched, session, error)

        self.run_worker(work, thread=True, group="overagent_launch", exclusive=True)

    def _overagent_launched(self, session, error) -> None:
        self._overagent_pending = False
        if error or session is None:
            self.notify(f"Could not start the overagent: {error or 'launch failed'}", severity="error")
            return
        self.refresh_sessions()
        self.set_timer(1.0, lambda: self._focus_overagent(session.id))


class JourneyMixin:
    """The `u` key: the learning journey panel (#483)."""

    def action_open_journey(self) -> None:
        if getattr(self, "_journey_loading", False):
            return
        self._journey_loading = True

        def work() -> None:
            from ..command_palette import keys_by_action
            from ..journey import load_journey
            try:
                keymap = keys_by_action(self.BINDINGS)
                bound = frozenset(k for keys in keymap.values() for k in keys)
                journey, error = load_journey(keymap, bound), None
            except Exception as e:
                journey, error = None, e
            self.call_from_thread(self._show_journey, journey, error)

        # Include this session's latest. Flushed here, on the UI thread that
        # appends to the buffer, never from the worker.
        self._flush_activity()
        self.run_worker(work, thread=True, group="journey", exclusive=True)

    def _show_journey(self, journey, error) -> None:
        from ..tui_widgets import JourneyPanel
        self._journey_loading = False
        if journey is None:
            self.notify(f"Could not load your journey: {error}", severity="error")
            return
        try:
            panel = self.query_one("#journey-panel", JourneyPanel)
        except Exception:
            return
        self._dialog_will_open()
        panel.show(journey, self)

    def on_journey_panel_closed(self, message) -> None:
        self._dialog_did_close()

    def on_journey_panel_try_requested(self, message) -> None:
        self._dialog_did_close()
        self.record_activity("action", action="journey:try", via="key", target=message.action)
        method = getattr(self, f"action_{message.action}", None)
        if method is not None:
            method()

    def on_journey_panel_ask_requested(self, message) -> None:
        """Hand a question to the overagent: send it to the live one, or start one with it."""
        from ..overagent import find_overagent
        self._dialog_did_close()
        self.record_activity("action", action="journey:ask", via="key")
        session = find_overagent(list(self.sessions))
        if session is not None:
            if self.launcher.send_to_session_by_id(session.id, message.question):
                self._focus_overagent(session.id)
            else:
                self.notify("Could not reach the overagent", severity="error")
            return
        self._overagent_question = message.question
        self.action_open_overagent()
