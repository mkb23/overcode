"""
The `!` key: open a new shell row (#496's shell backend).

Always launches another one, in your home directory: "shell" when no row
has that name, else "shell-2", "shell-3", … It is focused once it shows up,
and the split's bottom pane gets the keyboard, so you can type at once.
"""

from __future__ import annotations

from pathlib import Path

SHELL_NAME = "shell"


def next_shell_name(taken) -> str:
    """"shell", or the first free "shell-N" from 2 up."""
    name, n = SHELL_NAME, 2
    while name in taken:
        name, n = f"{SHELL_NAME}-{n}", n + 1
    return name


class NewShellMixin:
    """Mixed into SupervisorTUI."""

    def action_new_shell(self) -> None:
        from ..backends.shell import SHELL_BACKEND

        name = next_shell_name({s.name for s in self.sessions})
        directory = str(Path.home())

        def work() -> None:
            from ..exceptions import LaunchError
            error = None
            session = None
            try:
                session = self.launcher.launch(name=name, start_directory=directory,
                                               backend=SHELL_BACKEND)
            except LaunchError as e:
                error = e.reason
            except Exception as e:  # a bug, not a refused launch: still report it
                error = str(e)
            self.call_from_thread(self._shell_launched, session, error)

        self.notify(f"Opening {name}…", severity="information")
        self.run_worker(work, thread=True, group="shell_launch")

    def _shell_launched(self, session, error) -> None:
        if error or session is None:
            self.notify(f"Could not open a shell: {error or 'launch failed'}", severity="error")
            return
        self.refresh_sessions()
        self.set_timer(1.0, lambda: self._focus_new_shell(session.id))

    def _focus_new_shell(self, session_id: str) -> None:
        for i, w in enumerate(self._get_widgets_in_session_order()):
            if w.session.id == session_id:
                self._user_navigated = True
                self.focused_session_index = i
                break
        else:
            return  # hidden by a filter: it is there, just not shown
        self.split.focus_bottom_pane()
