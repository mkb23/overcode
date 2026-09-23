"""TUI rename (Ctrl+N, #478): the modal, the action, the worker, and the help.

The help test is a guard for every binding, not just Ctrl+N: Ctrl+R, Ctrl+P
and J were bound but missing from the help overlay until this change.
"""

import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from overcode.tui_widgets.rename_agent_modal import RenameAgentModal


def _modal(name="alpha", taken=("alpha", "beta")) -> RenameAgentModal:
    m = RenameAgentModal(id="m")
    m.show(session_id="sid-1", name=name, existing_names=set(taken))
    return m


class TestValidation:

    @pytest.mark.parametrize("value, problem", [
        ("", "enter a name"),
        ("alpha", "current name"),
        ("bad name", "letters, digits"),
        ("x" * 65, "letters, digits"),
        ("daemon_claude", "reserved"),
        ("beta", "already exists"),
    ])
    def test_unusable_names_explain_why(self, value, problem):
        m = _modal()
        m.value = value
        assert problem in m.error()

    def test_a_fresh_valid_name_is_accepted(self):
        m = _modal()
        m.value = "auth-refactor_2"
        assert m.error() is None

    def test_opens_prefilled_with_the_current_name_on_the_name_field(self):
        m = _modal()
        assert (m.value, m.cursor, m.selected_index, m.force) == ("alpha", 5, 0, False)


class TestEditing:

    @staticmethod
    def _press(m, key, character=None):
        m.on_key(SimpleNamespace(key=key, character=character, stop=lambda: None))

    def test_typing_backspace_and_cursor_keys(self):
        m = _modal()
        for _ in range(5):
            self._press(m, "backspace")
        for ch in "beta":
            self._press(m, ch, ch)
        self._press(m, "home")
        self._press(m, "x", "x")
        self._press(m, "end")
        self._press(m, "2", "2")
        self._press(m, "left")
        self._press(m, "delete")
        assert m.value == "xbeta"

    def test_tab_moves_to_force_and_space_toggles_it(self):
        m = _modal()
        self._press(m, "tab")
        self._press(m, "space", " ")
        assert (m.selected_index, m.force, m.value) == (1, True, "alpha")
        self._press(m, "tab")
        self._press(m, "g", "g")
        assert m.value == "alphag"

    def test_enter_with_a_valid_name_posts_the_request(self):
        m = _modal()
        posted = []
        m.post_message = posted.append
        self._press(m, "tab")
        self._press(m, "space", " ")
        self._press(m, "tab")
        self._press(m, "2", "2")
        self._press(m, "enter")
        assert len(posted) == 1
        msg = posted[0]
        assert isinstance(msg, RenameAgentModal.RenameRequested)
        assert (msg.session_id, msg.old_name, msg.new_name, msg.force) == (
            "sid-1", "alpha", "alpha2", True,
        )

    def test_enter_with_an_invalid_name_does_nothing(self):
        m = _modal()
        posted = []
        m.post_message = posted.append
        self._press(m, "enter")  # still the current name
        assert posted == []
        assert m.has_class("visible")

    def test_escape_cancels(self):
        m = _modal()
        posted = []
        m.post_message = posted.append
        self._press(m, "escape")
        assert [type(p) for p in posted] == [RenameAgentModal.Cancelled]
        assert not m.has_class("visible")


class TestInAnApp:
    """The widget in a real Textual app, driven by real key presses."""

    @pytest.mark.asyncio
    async def test_type_a_name_and_press_enter(self):
        from textual.app import App, ComposeResult

        received = []

        class TestApp(App):
            CSS = "RenameAgentModal { display: none; } RenameAgentModal.visible { display: block; }"

            def compose(self) -> ComposeResult:
                yield RenameAgentModal(id="m")

            def on_rename_agent_modal_rename_requested(self, message):
                received.append(message)

        app = TestApp()
        async with app.run_test(size=(100, 30)) as pilot:
            modal = app.query_one("#m", RenameAgentModal)
            modal.show(session_id="sid-1", name="alpha", existing_names={"alpha"}, app_ref=app)
            await pilot.pause()
            assert modal.has_class("visible")
            await pilot.press("backspace", "backspace", "backspace", "backspace", "backspace")
            await pilot.press(*"auth")
            assert "auth" in str(modal.render())
            await pilot.press("enter")
            await pilot.pause()
            assert not modal.has_class("visible")

        assert [(m.old_name, m.new_name, m.force) for m in received] == [("alpha", "auth", False)]


class TestAction:

    @staticmethod
    def _tui(session=None, remote=False):
        from overcode.tui_actions.session import SessionActionsMixin
        from overcode.tui_widgets import SessionSummary

        tui = MagicMock()
        if session is not None:
            widget = MagicMock(spec=SessionSummary)
            widget.session = session
            tui.focused = widget
        else:
            tui.focused = None
        tui._is_remote = lambda s: remote
        return tui, SessionActionsMixin

    def test_no_focused_agent_warns(self):
        tui, mixin = self._tui()
        mixin.action_rename_focused(tui)
        assert "No agent focused" in tui.notify.call_args[0][0]

    def test_remote_agent_is_redirected_to_its_own_machine(self):
        tui, mixin = self._tui(SimpleNamespace(name="far", id="r1"), remote=True)
        mixin.action_rename_focused(tui)
        assert "on its own machine" in tui.notify.call_args[0][0]
        tui.query_one.assert_not_called()

    def test_opens_the_modal_for_the_focused_agent(self):
        tui, mixin = self._tui(SimpleNamespace(name="alpha", id="sid-1"))
        tui.session_manager.list_sessions.return_value = [
            SimpleNamespace(name="alpha"), SimpleNamespace(name="beta"),
        ]
        mixin.action_rename_focused(tui)
        modal = tui.query_one.return_value
        kwargs = modal.show.call_args.kwargs
        assert kwargs["session_id"] == "sid-1"
        assert kwargs["name"] == "alpha"
        assert kwargs["existing_names"] == {"alpha", "beta"}
        tui._dialog_will_open.assert_called_once()


class TestRenameWorker:
    """SupervisorTUI._run_rename: every outcome ends in a notification."""

    @staticmethod
    def _run(outcome, name_after="alpha", force=False):
        from overcode.tui import SupervisorTUI

        tui = MagicMock()
        tui.tmux_session = "agents"
        tui.call_from_thread = lambda fn, *a, **kw: fn(*a, **kw)
        tui.session_manager.get_session.return_value = SimpleNamespace(name=name_after)
        session = SimpleNamespace(id="sid-1", name="alpha")
        launcher = MagicMock()
        if isinstance(outcome, Exception):
            launcher.rename.side_effect = outcome
        else:
            launcher.rename.return_value = outcome
        with patch("overcode.launcher.AgentLauncher", return_value=launcher):
            SupervisorTUI._run_rename(tui, session, "beta", force)
        launcher.rename.assert_called_once_with(session, "beta", force=force)
        return tui

    @staticmethod
    def _said(tui):
        return " ".join(tui.notify.call_args[0][0].split()), tui.notify.call_args[1]["severity"]

    def test_success(self):
        tui = self._run(True, name_after="beta")
        assert self._said(tui) == ("Renamed 'alpha' → 'beta'", "information")
        tui.refresh_sessions.assert_called_once()

    def test_busy_says_so_and_points_at_force(self):
        from overcode.exceptions import AgentBusyError

        text, severity = self._said(self._run(AgentBusyError("alpha", "running")))
        assert "is busy (running)" in text and "force" in text and severity == "warning"

    def test_unknown_status(self):
        from overcode.exceptions import AgentBusyError

        text, _ = self._said(self._run(AgentBusyError("alpha", "unknown")))
        assert "could not be checked" in text

    def test_refused_window_rename(self):
        text, severity = self._said(self._run(False, name_after="alpha"))
        assert "restarted under its old name" in text and severity == "error"

    def test_relaunch_failure_after_the_rename(self):
        text, severity = self._said(self._run(False, name_after="beta"))
        assert "press R" in text and severity == "warning"

    def test_value_error(self):
        text, severity = self._said(self._run(ValueError("an agent named 'beta' already exists")))
        assert "already exists" in text and severity == "error"

    def test_unexpected_error_is_reported_not_swallowed(self):
        text, severity = self._said(self._run(RuntimeError("boom")))
        assert "boom" in text and severity == "error"

    def test_force_is_passed_through(self):
        self._run(True, name_after="beta", force=True)


class TestHelpCoversEveryBinding:

    # How the help overlay writes keys the binding table spells out.
    LABELS = {
        "left_square_bracket": "[", "right_square_bracket": "]", "backslash": "\\",
        "question_mark": "?", "colon": ":", "dollar_sign": "$", "comma": ",",
        "full_stop": ".", "less_than_sign": "<", "down": "↓", "up": "↑",
        "enter": "Enter", "escape": "Esc", "equals_sign": "=", "minus": "-", "slash": "/",
    }
    # Documented as a range or pair rather than one key each.
    GROUPED = {"1": "1-5", "2": "1-5", "3": "1-5", "4": "1-5", "5": "1-5",
               "=": "=/-", "-": "=/-"}

    def _label(self, key: str) -> str:
        if key.startswith("ctrl+"):
            return "^" + key[len("ctrl+"):].upper()
        label = self.LABELS.get(key, key)
        return self.GROUPED.get(label, label)

    def test_every_binding_is_in_the_help(self):
        from overcode.tui import SupervisorTUI
        from overcode.tui_widgets.help_overlay import HelpOverlay

        text = HelpOverlay()._build_keybindings().plain
        missing = []
        for binding in SupervisorTUI.BINDINGS:
            key, action = (binding[0], binding[1]) if isinstance(binding, tuple) else (
                binding.key, binding.action,
            )
            label = self._label(key)
            if not re.search(r"(^|\s|/)" + re.escape(label) + r"(\s|/|$)", text, re.M):
                missing.append(f"{key} ({action})")
        assert missing == []

    def test_no_key_is_bound_twice(self):
        """Textual runs only the first binding for a key, so a second one is
        dead: that is how `T` (handover) was lost to the tag filter."""
        from collections import Counter

        from overcode.tui import SupervisorTUI

        keys = [b[0] if isinstance(b, tuple) else b.key for b in SupervisorTUI.BINDINGS]
        assert {k: n for k, n in Counter(keys).items() if n > 1} == {}

    def test_rename_is_in_the_help(self):
        from overcode.tui_widgets.help_overlay import HelpOverlay

        text = HelpOverlay()._build_keybindings().plain
        assert re.search(r"\^N\s+Rename agent", text)
        assert re.search(r"\^R\s+Cycle focal repo", text)
