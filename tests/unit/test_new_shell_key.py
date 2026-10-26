"""
The `!` key opens a new shell row: "shell", then "shell-2", "shell-3", …
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from overcode.tui_actions.new_shell import NewShellMixin, next_shell_name


class TestName:
    def test_shell_when_free(self):
        assert next_shell_name({"overagent", "worker"}) == "shell"

    def test_the_first_free_number(self):
        assert next_shell_name({"shell"}) == "shell-2"
        assert next_shell_name({"shell", "shell-2", "shell-4"}) == "shell-3"


def _tui(sessions, widgets=()):
    from overcode.tui_split import NULL_SPLIT
    tui = MagicMock()
    tui.sessions = sessions
    tui._get_widgets_in_session_order.return_value = list(widgets)
    tui.split = NULL_SPLIT
    # Run the launch worker inline, and its UI-thread callback directly.
    tui.run_worker.side_effect = lambda work, **kw: work()
    tui.call_from_thread.side_effect = lambda fn, *a: fn(*a)
    for name in ("action_new_shell", "_shell_launched", "_focus_new_shell"):
        setattr(tui, name, getattr(NewShellMixin, name).__get__(tui))
    return tui


class TestKey:
    def test_launches_a_shell_named_after_what_is_taken(self):
        tui = _tui([SimpleNamespace(name="shell")])
        tui.launcher.launch.return_value = SimpleNamespace(id="new")
        tui.action_new_shell()
        kwargs = tui.launcher.launch.call_args.kwargs
        assert kwargs["name"] == "shell-2" and kwargs["backend"] == "shell"
        assert kwargs["start_directory"] == str(Path.home())

    def test_focuses_the_new_shell_once_it_shows(self):
        new = SimpleNamespace(id="new", name="shell")
        tui = _tui([], [SimpleNamespace(session=SimpleNamespace(id="x")),
                        SimpleNamespace(session=new)])
        tui.launcher.launch.return_value = new
        tui.set_timer.side_effect = lambda delay, fn: fn()
        tui.action_new_shell()
        assert tui.focused_session_index == 1

    def test_a_refused_launch_says_why(self):
        from overcode.exceptions import LaunchError
        tui = _tui([])
        tui.launcher.launch.side_effect = LaunchError("no tmux")
        tui.action_new_shell()
        message = tui.notify.call_args.args[0]
        assert "Could not open a shell" in message and "no tmux" in message


class TestBinding:
    def test_bang_is_bound_and_in_the_palette(self):
        from overcode.command_palette import COMMANDS
        from overcode.tui import SupervisorTUI
        assert ("exclamation_mark", "new_shell", "New shell") in SupervisorTUI.BINDINGS
        cmd = next(c for c in COMMANDS if c.action == "new_shell")
        assert cmd.category == "Agent"
