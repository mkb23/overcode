"""
Integration tests for the CommandBar widget.
"""

import pytest
from unittest.mock import patch, MagicMock


class TestCommandBarWidget:
    """Test CommandBar widget in isolation"""

    @pytest.mark.asyncio
    async def test_command_bar_starts_hidden(self):
        """CommandBar should start hidden (display: none)"""
        from overcode.tui import SupervisorTUI, CommandBar

        app = SupervisorTUI(tmux_session="test")
        async with app.run_test():
            cmd_bar = app.query_one("#command-bar", CommandBar)
            # CSS sets display: none
            assert cmd_bar.display == False
            assert not cmd_bar.has_class("visible")


class TestCommandBarIntegration:
    """Integration tests for CommandBar in SupervisorTUI"""

    @pytest.mark.asyncio
    async def test_command_bar_hidden_on_start(self):
        """Command bar should be hidden when TUI starts"""
        from overcode.tui import SupervisorTUI, CommandBar

        app = SupervisorTUI(tmux_session="test")
        async with app.run_test() as pilot:
            cmd_bar = app.query_one("#command-bar", CommandBar)
            assert not cmd_bar.has_class("visible")
            assert cmd_bar.display == False

    @pytest.mark.asyncio
    async def test_i_key_shows_command_bar(self):
        """Pressing 'i' should show the command bar"""
        from overcode.tui import SupervisorTUI, CommandBar
        from textual.widgets import Input

        app = SupervisorTUI(tmux_session="test")
        async with app.run_test() as pilot:
            cmd_bar = app.query_one("#command-bar", CommandBar)

            # Initially hidden
            assert not cmd_bar.has_class("visible")

            # Press i
            await pilot.press("i")

            # Now visible
            assert cmd_bar.has_class("visible")

            # Input should be focused and enabled
            input_widget = cmd_bar.query_one("#cmd-input", Input)
            assert not input_widget.disabled
            assert app.focused == input_widget

    @pytest.mark.asyncio
    async def test_colon_key_shows_command_bar(self):
        """Pressing ':' should show the command bar (vim-style)"""
        from overcode.tui import SupervisorTUI, CommandBar

        app = SupervisorTUI(tmux_session="test")
        async with app.run_test() as pilot:
            cmd_bar = app.query_one("#command-bar", CommandBar)

            await pilot.press("colon")

            assert cmd_bar.has_class("visible")

    @pytest.mark.asyncio
    async def test_escape_hides_command_bar(self):
        """Pressing Escape should hide the command bar"""
        from overcode.tui import SupervisorTUI, CommandBar

        app = SupervisorTUI(tmux_session="test")
        async with app.run_test() as pilot:
            cmd_bar = app.query_one("#command-bar", CommandBar)

            # Show command bar
            await pilot.press("i")
            assert cmd_bar.has_class("visible")

            # Press escape
            await pilot.press("escape")

            # Should be hidden
            assert not cmd_bar.has_class("visible")

    @pytest.mark.asyncio
    async def test_typing_in_command_bar(self):
        """Should be able to type in the command bar"""
        from overcode.tui import SupervisorTUI, CommandBar
        from textual.widgets import Input

        app = SupervisorTUI(tmux_session="test")
        async with app.run_test() as pilot:
            # Show command bar
            await pilot.press("i")

            # Type some text
            await pilot.press("h", "e", "l", "l", "o")

            input_widget = app.query_one("#cmd-input", Input)
            assert input_widget.value == "hello"

    @pytest.mark.asyncio
    async def test_enter_sends_message(self):
        """Pressing Enter should send the message"""
        from overcode.tui import SupervisorTUI, CommandBar
        from textual.widgets import Input

        app = SupervisorTUI(tmux_session="test")
        async with app.run_test() as pilot:
            # Show command bar
            await pilot.press("i")

            # Type some text
            await pilot.press("t", "e", "s", "t")

            input_widget = app.query_one("#cmd-input", Input)
            assert input_widget.value == "test"

            # Press enter - should clear input (message sent)
            await pilot.press("enter")

            # Input should be cleared after send
            assert input_widget.value == ""

    @pytest.mark.asyncio
    async def test_command_bar_does_not_capture_h_key(self):
        """When hidden, command bar should not capture 'h' key"""
        from overcode.tui import SupervisorTUI, HelpOverlay

        app = SupervisorTUI(tmux_session="test")
        async with app.run_test() as pilot:
            help_overlay = app.query_one("#help-overlay", HelpOverlay)

            # Press h - should toggle help, not go to command bar
            await pilot.press("h")

            assert help_overlay.has_class("visible")

    @pytest.mark.asyncio
    async def test_ctrl_e_toggles_multiline(self):
        """Ctrl+E should toggle between single and multi-line mode"""
        from overcode.tui import SupervisorTUI, CommandBar
        from textual.widgets import Input, TextArea

        app = SupervisorTUI(tmux_session="test")
        async with app.run_test() as pilot:
            cmd_bar = app.query_one("#command-bar", CommandBar)

            # Show command bar
            await pilot.press("i")
            assert not cmd_bar.expanded

            # Toggle to multi-line
            await pilot.press("ctrl+e")
            assert cmd_bar.expanded

            # TextArea should be visible and focused
            textarea = cmd_bar.query_one("#cmd-textarea", TextArea)
            assert not textarea.has_class("hidden")


@pytest.mark.skip(reason="Integration test with timing issues - needs investigation")
class TestCommandBarWithSessions:
    """Test CommandBar interaction with sessions"""

    @pytest.mark.asyncio
    async def test_target_session_from_focused(self, tmp_path):
        """Command bar should target the focused session"""
        from overcode.tui import SupervisorTUI, CommandBar, SessionSummary
        from overcode.session_manager import SessionManager

        # Create a session
        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        session = session_manager.create_session(
            name="test-agent",
            tmux_session="test",
            tmux_window=1,
            command=["claude"]
        )

        app = SupervisorTUI(tmux_session="test")
        app.session_manager = session_manager

        async with app.run_test() as pilot:
            # Refresh to load sessions
            app.refresh_sessions()
            await pilot.pause()

            cmd_bar = app.query_one("#command-bar", CommandBar)

            # Focus a session first
            session_widgets = list(app.query(SessionSummary))
            if session_widgets:
                session_widgets[0].focus()
                await pilot.pause()

            # Show command bar
            await pilot.press("i")

            # Should target the session
            assert cmd_bar.target_session == "test-agent"


class TestUniqueAgentName:
    """Test unique agent name generation (#131)"""

    @pytest.mark.asyncio
    async def test_unique_name_no_conflict(self, tmp_path):
        """When no conflict, returns base name unchanged"""
        from overcode.tui import SupervisorTUI, CommandBar
        from overcode.session_manager import SessionManager

        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        app = SupervisorTUI(tmux_session="test")
        app.session_manager = session_manager

        async with app.run_test() as pilot:
            cmd_bar = app.query_one("#command-bar", CommandBar)
            # No sessions exist, so name should be unchanged
            result = cmd_bar._get_unique_agent_name("myagent")
            assert result == "myagent"

    @pytest.mark.asyncio
    async def test_unique_name_with_one_conflict(self, tmp_path):
        """When name exists, returns name2"""
        from overcode.tui import SupervisorTUI, CommandBar
        from overcode.session_manager import SessionManager

        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        session_manager.create_session(
            name="foo",
            tmux_session="test",
            tmux_window=1,
            command=["claude"]
        )

        app = SupervisorTUI(tmux_session="test")
        app.session_manager = session_manager

        async with app.run_test() as pilot:
            cmd_bar = app.query_one("#command-bar", CommandBar)
            # "foo" exists, so should get "foo2"
            result = cmd_bar._get_unique_agent_name("foo")
            assert result == "foo2"

    @pytest.mark.asyncio
    async def test_unique_name_with_multiple_conflicts(self, tmp_path):
        """When name and name2 exist, returns name3"""
        from overcode.tui import SupervisorTUI, CommandBar
        from overcode.session_manager import SessionManager

        session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        session_manager.create_session(
            name="bar",
            tmux_session="test",
            tmux_window=1,
            command=["claude"]
        )
        session_manager.create_session(
            name="bar2",
            tmux_session="test",
            tmux_window=2,
            command=["claude"]
        )

        app = SupervisorTUI(tmux_session="test")
        app.session_manager = session_manager

        async with app.run_test() as pilot:
            cmd_bar = app.query_one("#command-bar", CommandBar)
            # "bar" and "bar2" exist, so should get "bar3"
            result = cmd_bar._get_unique_agent_name("bar")
            assert result == "bar3"
