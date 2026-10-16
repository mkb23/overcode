"""The TUI highlights the agent it last had highlighted, and the split's
bottom pane opens on that agent's window."""

import pytest

from overcode.settings import TUIPreferences
from tests.tui_harness import ready, three_agents


class _RecordingSplit:
    """A split that is active and records which agent it was pointed at."""

    active = True

    def __init__(self):
        self.shown = []

    def show_agent(self, widget):
        self.shown.append(widget.session.name)

    def __getattr__(self, name):
        return lambda *a, **kw: None


def _highlighted(app):
    return app._selected_session_id()


@pytest.mark.asyncio
class TestRememberFocus:
    async def test_moving_by_hand_is_remembered(self, make_tui):
        app = make_tui(three_agents())
        async with app.run_test() as pilot:
            await ready(pilot)
            await pilot.press("j", "j")
            await pilot.pause()
            assert _highlighted(app) == "id-charlie"
        assert TUIPreferences.load("test").last_focused_agent == "id-charlie"

    async def test_the_next_start_highlights_it_and_opens_its_window(self, make_tui):
        prefs = TUIPreferences.load("test")
        prefs.last_focused_agent = "id-bravo"
        prefs.save("test")
        app = make_tui(three_agents())
        split = _RecordingSplit()
        app.split = split
        async with app.run_test() as pilot:
            await ready(pilot)
            assert _highlighted(app) == "id-bravo"
            assert split.shown[:1] == ["bravo"]

    async def test_a_gone_agent_falls_back_to_the_first(self, make_tui):
        prefs = TUIPreferences.load("test")
        prefs.last_focused_agent = "id-killed"
        prefs.save("test")
        app = make_tui(three_agents())
        async with app.run_test() as pilot:
            await ready(pilot)
            assert _highlighted(app) == "id-alpha"

    async def test_starting_up_does_not_overwrite_it(self, make_tui):
        """Only a move by hand is remembered, not the start-up highlight."""
        prefs = TUIPreferences.load("test")
        prefs.last_focused_agent = "id-killed"
        prefs.save("test")
        app = make_tui(three_agents())
        async with app.run_test() as pilot:
            await ready(pilot)
        assert TUIPreferences.load("test").last_focused_agent == "id-killed"


def test_an_older_preferences_file_loads_without_it(tui_state_dir):
    import json
    from overcode.settings import get_tui_preferences_path

    path = get_tui_preferences_path("test")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sort_mode": "by_tree"}))
    prefs = TUIPreferences.load("test")
    assert prefs.sort_mode == "by_tree" and prefs.last_focused_agent == ""
