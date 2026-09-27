"""
The `e` key: open the overagent (#484).

Focuses the most recent overagent row; in `overcode tmux` split mode the
bottom pane follows it and gets the keyboard, so you can type to it at
once. With none running, launches one named "overcode" in ~/.overcode.
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
        def work() -> None:
            error = None
            session = None
            try:
                session = self.launcher.launch(name=name, start_directory=directory, backend=backend)
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
