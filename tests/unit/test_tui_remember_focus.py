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


def _agents(asleep=()):
    sessions = three_agents()
    for s in sessions:
        s.is_asleep = s.name in asleep
    return sessions


@pytest.mark.asyncio
class TestViewChangesKeepTheChoice:
    """The chosen agent is kept by id, never by row (owner, 2026-10-18:
    "Z is losing sync when you re-show")."""

    async def test_hiding_the_chosen_agent_and_showing_it_again_brings_it_back(self, make_tui):
        app = make_tui(_agents(asleep={"bravo"}))
        split = _RecordingSplit()
        app.split = split
        async with app.run_test() as pilot:
            await ready(pilot)
            await pilot.press("j")
            await pilot.pause()
            assert _highlighted(app) == "id-bravo"
            shown = list(split.shown)
            await pilot.press("Z")              # bravo hidden: a neighbour stands in
            await pilot.pause()
            assert _highlighted(app) in ("id-alpha", "id-charlie")
            assert split.shown == shown         # the pane stays on bravo
            await pilot.press("Z")              # shown again: the highlight is back
            await pilot.pause()
            assert _highlighted(app) == "id-bravo"
            assert split.shown == shown
        assert TUIPreferences.load("test").last_focused_agent == "id-bravo"

    async def test_hiding_a_row_above_keeps_the_same_agent(self, make_tui):
        app = make_tui(_agents(asleep={"alpha"}))
        async with app.run_test() as pilot:
            await ready(pilot)
            await pilot.press("j", "j")
            await pilot.pause()
            assert _highlighted(app) == "id-charlie"
            for _ in range(2):
                await pilot.press("Z")
                await pilot.pause()
                assert _highlighted(app) == "id-charlie"

    async def test_moving_while_it_is_hidden_makes_a_new_choice(self, make_tui):
        app = make_tui(_agents(asleep={"bravo"}))
        async with app.run_test() as pilot:
            await ready(pilot)
            await pilot.press("j", "Z")
            await pilot.pause()
            await pilot.press("k")               # a choice made while bravo is hidden
            await pilot.pause()
            chosen = _highlighted(app)
            await pilot.press("Z")
            await pilot.pause()
            assert _highlighted(app) == chosen != "id-bravo"

    async def test_folding_the_chosen_child_away_and_back(self, make_tui):
        prefs = TUIPreferences.load("test")
        prefs.sort_mode = "by_tree"
        prefs.save("test")
        sessions = three_agents()
        sessions[2].parent_session_id = "id-bravo"  # charlie is bravo's child
        app = make_tui(sessions)
        split = _RecordingSplit()
        app.split = split
        async with app.run_test() as pilot:
            await ready(pilot)
            await pilot.press("j", "j")
            await pilot.pause()
            assert _highlighted(app) == "id-charlie"
            shown = list(split.shown)
            await pilot.press("X")               # fold every parent: charlie hidden
            await pilot.pause()
            assert app.collapsed_parents == {"id-bravo"}
            assert _highlighted(app) != "id-charlie" and split.shown == shown
            await pilot.press("X")               # unfold: the highlight is back
            await pilot.pause()
            assert _highlighted(app) == "id-charlie"


class TestJobsKeepTheChoice:
    """The jobs view keeps its highlighted job by id too."""

    def _app(self, jobs):
        from types import SimpleNamespace

        from overcode.tui_actions.jobs import JobsMixin

        class App(JobsMixin):
            tui_mode = "jobs"

            def __init__(self):
                self.focused_job_index = 0
                self.widgets = [SimpleNamespace(job=SimpleNamespace(id=j)) for j in jobs]

            def _get_job_widgets(self):
                return self.widgets

        return App()

    def test_the_chosen_job_keeps_its_row_as_rows_come_and_go(self):
        app = self._app(["j1", "j2", "j3"])
        app._choose_job(1)
        assert app._chosen_job_id == "j2"
        app.widgets = app.widgets[2:] + app.widgets[:2]   # re-listed: j3, j1, j2
        assert app._chosen_job_row(app.widgets) == 2
        app.widgets = [w for w in app.widgets if w.job.id != "j2"]  # hidden
        assert app._chosen_job_row(app.widgets) in (0, 1)
        assert app._chosen_job_id == "j2"                 # still the choice
