"""The TUI reports what a local launch or fork actually did.

A refused launch used to return None, and the new-agent dialog still said
"Created agent". It now raises ``LaunchError`` and the TUI shows its reason.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from overcode.exceptions import LaunchError
from overcode.tui import SupervisorTUI


def _app():
    app = SimpleNamespace(
        tmux_session="agents",
        session_manager=MagicMock(),
        notify=MagicMock(),
        refresh_sessions=MagicMock(),
        _dialog_did_close=MagicMock(),
    )
    return app


def _launch_message(**overrides):
    fields = dict(
        name="worker", is_remote=False, directory="/tmp", bypass_permissions=False,
        agent_teams=False, agent_persona=None, provider=None, backend=None,
        wrapper=None, extra_cli_args=None, skill_profile=None, billing_code=None,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _notes(app):
    return [(c.args[0], c.kwargs.get("severity")) for c in app.notify.call_args_list]


class TestNewAgentDialog:
    def test_a_refused_launch_is_an_error_with_its_reason(self):
        app = _app()
        with patch("overcode.tui_actions.dialogs.AgentLauncher") as cls:
            cls.return_value.launch.side_effect = LaunchError("wrapper 'w' not found or not executable")
            SupervisorTUI.on_new_agent_modal_launch_requested(app, _launch_message())

        assert _notes(app) == [
            ("Cannot launch: wrapper 'w' not found or not executable", "error"),
        ]
        app.refresh_sessions.assert_not_called()
        app._dialog_did_close.assert_called_once()

    def test_a_launch_says_created(self):
        app = _app()
        with patch("overcode.tui_actions.dialogs.AgentLauncher"):
            SupervisorTUI.on_new_agent_modal_launch_requested(app, _launch_message())

        assert _notes(app) == [("Created agent: worker", "information")]
        app.refresh_sessions.assert_called_once()


class TestFork:
    def test_a_refused_fork_is_an_error_with_its_reason(self):
        app = _app()
        source = SimpleNamespace(name="src", is_remote=False)
        message = SimpleNamespace(source_session=source, fork_name="src-fork")
        with patch("overcode.tui_actions.command_bar.AgentLauncher") as cls:
            cls.return_value.launch_fork.side_effect = LaunchError(
                "backend 'shell' does not support fork", "fork")
            SupervisorTUI.on_command_bar_fork_requested(app, message)

        assert _notes(app) == [("Cannot fork: backend 'shell' does not support fork", "error")]
        app.refresh_sessions.assert_not_called()

    def test_a_fork_says_forked(self):
        app = _app()
        source = SimpleNamespace(name="src", is_remote=False)
        message = SimpleNamespace(source_session=source, fork_name="src-fork")
        with patch("overcode.tui_actions.command_bar.AgentLauncher"):
            SupervisorTUI.on_command_bar_fork_requested(app, message)

        assert _notes(app) == [("Forked 'src' → 'src-fork'", "information")]
