"""The typing guard in the TUI (#560): prose typed into the monitor pane.

Every test drives the guard's clock by hand (``Clock``): each key moves it
by the gap the test types at, and ``app.typing_tick()`` is called after
moving it past a deadline, so nothing depends on how fast the test runs.
"""

from unittest.mock import MagicMock

import pytest

from overcode.keymap import _CHAR_NAMES
from overcode.tui_actions.command_bar import CommandBarMixin
from overcode.typing_guard import RECOVER_QUIET, RUN_GAP, START_SETTLE
from tests.tui_harness import palette, ready, three_agents

pytestmark = pytest.mark.asyncio


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _key(ch: str) -> str:
    return {" ": "space", "\n": "enter"}.get(ch, _CHAR_NAMES.get(ch, ch))


async def type_keys(pilot, clock: Clock, keys, gap: float = 0.12) -> None:
    """Press each key ``gap`` seconds (of the guard's clock) after the last."""
    for k in keys:
        clock.t += gap
        await pilot.press(_key(k) if len(k) == 1 else k)


async def settle(pilot, clock: Clock, seconds: float) -> None:
    clock.t += seconds
    await pilot.app.typing_tick()
    await pilot.pause()


def notes(app) -> list:
    return [n.message for n in app._notifications]


@pytest.fixture
def app(make_tui):
    app = make_tui(three_agents(), size=(160, 40))
    app._typing_clock = Clock()
    app._typing_enabled = True
    return app


def timeline_shown(app) -> bool:
    return bool(app.query_one("#timeline").display)


class TestRollback:

    async def test_prose_puts_settings_and_the_chosen_agent_back(self, app):
        clock = app._typing_clock
        async with app.run_test() as pilot:
            await ready(pilot)
            level, timeline = app.summary_level_index, timeline_shown(app)
            assert app._chosen_session_id() in (None, "id-alpha")
            chosen = app._get_focused_widget().session.id
            # s, t and j act; x makes the run typing and is swallowed
            await type_keys(pilot, clock, "stjx")
            assert app._typing_recovering
            assert app.summary_level_index == level
            assert timeline_shown(app) == timeline
            assert app._get_focused_widget().session.id == chosen
            # The rest of the sentence is swallowed: nothing else moves
            await type_keys(pilot, clock, " the build is red")
            assert app.summary_level_index == level and timeline_shown(app) == timeline
            assert not any(n.startswith("Typing went") for n in notes(app))
            await settle(pilot, clock, RECOVER_QUIET + 0.1)
            assert not app._typing_recovering
            toast = [n for n in notes(app) if n.startswith("Typing went to overcode")]
            assert len(toast) == 1
            assert 'ignored "stjx the build is red"' in toast[0]
            assert "undid Timeline, Summary detail" in toast[0]
            assert toast[0].endswith("Ctrl+Z undoes more.")
            # The per-toggle notes were not shown for the rollback
            assert sum(n.startswith("Summary:") for n in notes(app)) == 1

    async def test_a_command_bar_opened_mid_run_closes_without_sending(self, app, monkeypatch):
        clock = app._typing_clock
        sent = []
        monkeypatch.setattr(CommandBarMixin, "on_command_bar_send_requested",
                            lambda self, message: sent.append(message.text))
        async with app.run_test() as pilot:
            await ready(pilot)
            chosen = app._get_focused_widget().session.id
            bar = app.query_one("#command-bar")
            # j moves, t toggles, i opens the bar on bravo; a and b go into it
            await type_keys(pilot, clock, "jtiab")
            assert app._typing_recovering
            await pilot.pause()
            assert not bar.has_class("visible")
            await type_keys(pilot, clock, "out the tests\n")
            await settle(pilot, clock, RECOVER_QUIET + 0.1)
            await pilot.pause()
            assert sent == []
            assert not bar.has_class("visible")
            assert app._get_focused_widget().session.id == chosen
            assert app._chosen_session_id() == chosen

    async def test_help_opened_by_the_run_closes(self, app):
        clock = app._typing_clock
        async with app.run_test() as pilot:
            await ready(pilot)
            await type_keys(pilot, clock, "hi team")
            assert not app.query_one("#help-overlay").has_class("visible")
            await settle(pilot, clock, RECOVER_QUIET + 0.1)
            assert any('ignored "hi team"' in n for n in notes(app))

    async def test_the_run_after_recovery_is_new(self, app):
        clock = app._typing_clock
        async with app.run_test() as pilot:
            await ready(pilot)
            timeline = timeline_shown(app)
            await type_keys(pilot, clock, "vwDN")  # keys bound to nothing
            assert app._typing_recovering
            await settle(pilot, clock, RECOVER_QUIET + 0.1)
            clock.t += 5
            await pilot.press("t")
            assert timeline_shown(app) != timeline


class TestOneShots:

    async def test_one_shots_mid_run_never_fire(self, app):
        clock = app._typing_clock
        for name in ("open_overagent", "new_shell", "quit", "send_1_to_focused"):
            setattr(app, f"action_{name}", MagicMock(name=name))
        app.exit = MagicMock()
        async with app.run_test() as pilot:
            await ready(pilot)
            # v and w are unbound; q e ! 1 come mid-run; the fourth key makes
            # the run typing (and is swallowed)
            await type_keys(pilot, clock, ["v", "q", "e", "v"])
            for _ in range(3):
                await settle(pilot, clock, RUN_GAP + RECOVER_QUIET)
            await type_keys(pilot, clock, ["w", "!", "1", "w"])
            for _ in range(3):
                await settle(pilot, clock, RUN_GAP + RECOVER_QUIET)
            for name in ("open_overagent", "new_shell", "quit", "send_1_to_focused"):
                getattr(app, f"action_{name}").assert_not_called()
            app.exit.assert_not_called()
            toasts = [n for n in notes(app) if n.startswith("Typing went")]
            assert "didn't run Quit, Overagent: ask overcode." in toasts[0]
            assert "didn't run New shell, Send 1." in toasts[1]

    async def test_a_one_shot_mid_run_runs_when_the_run_settles(self, app):
        clock = app._typing_clock
        app.action_send_1_to_focused = MagicMock()
        async with app.run_test() as pilot:
            await ready(pilot)
            await type_keys(pilot, clock, ["v", "1"])
            app.action_send_1_to_focused.assert_not_called()
            await settle(pilot, clock, RUN_GAP / 2)
            app.action_send_1_to_focused.assert_not_called()
            await settle(pilot, clock, RUN_GAP)
            app.action_send_1_to_focused.assert_called_once()

    @pytest.mark.parametrize("key,action", [("1", "send_1_to_focused"),
                                            ("enter", "send_enter_to_focused")])
    async def test_a_lone_digit_or_enter_fires_at_once(self, app, key, action):
        clock = app._typing_clock
        setattr(app, f"action_{action}", MagicMock())
        async with app.run_test() as pilot:
            await ready(pilot)
            await type_keys(pilot, clock, [key])
            getattr(app, f"action_{action}").assert_called_once()

    async def test_a_lone_e_fires_after_the_settle(self, app):
        clock = app._typing_clock
        app.action_open_overagent = MagicMock()
        async with app.run_test() as pilot:
            await ready(pilot)
            await type_keys(pilot, clock, ["e"])
            app.action_open_overagent.assert_not_called()
            await settle(pilot, clock, START_SETTLE + 0.01)
            app.action_open_overagent.assert_called_once()

    async def test_e_followed_by_a_word_is_dropped(self, app):
        clock = app._typing_clock
        app.action_open_overagent = MagicMock()
        async with app.run_test() as pilot:
            await ready(pilot)
            await type_keys(pilot, clock, "every")
            for _ in range(3):
                await settle(pilot, clock, RUN_GAP + RECOVER_QUIET)
            app.action_open_overagent.assert_not_called()

    async def test_a_double_press_inside_typing_is_dropped(self, app):
        """x then x confirms a kill; inside a typing run the kill never runs."""
        clock = app._typing_clock
        kills = []
        app._execute_kill = lambda *a: kills.append(a)
        async with app.run_test() as pilot:
            await ready(pilot)
            await type_keys(pilot, clock, "x x", gap=0.25)   # one key and space: not typing
            await type_keys(pilot, clock, "yz")            # now it is
            for _ in range(3):
                await settle(pilot, clock, RUN_GAP + RECOVER_QUIET)
            await pilot.pause(0.3)  # the confirmation's own KEY_REPEAT_GAP timer
            assert kills == []
            assert app._pending_confirmations == {}


class TestDeliberateUse:

    async def test_i_then_fast_typing_and_enter_sends(self, app, monkeypatch):
        clock = app._typing_clock
        sent = []
        monkeypatch.setattr(CommandBarMixin, "on_command_bar_send_requested",
                            lambda self, message: sent.append(message.text))
        async with app.run_test() as pilot:
            await ready(pilot)
            await type_keys(pilot, clock, "ifix the tests\n", gap=0.06)
            await pilot.pause()
            assert sent == ["fix the tests"]
            assert not app._typing_recovering

    async def test_palette_runs_are_unaffected(self, app):
        clock = app._typing_clock
        async with app.run_test() as pilot:
            await ready(pilot)
            timeline = timeline_shown(app)
            await type_keys(pilot, clock, ["slash", *"timeline", "enter"], gap=0.05)
            await pilot.pause()
            assert timeline_shown(app) != timeline
            assert not palette(app).has_class("visible")
            assert not app._typing_recovering

    async def test_the_palette_switch_turns_it_off_and_saves(self, app, tmp_path, monkeypatch):
        from overcode import config
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.yaml")
        config._clear_config_cache()
        clock = app._typing_clock
        async with app.run_test() as pilot:
            await ready(pilot)
            await type_keys(pilot, clock, ["slash", *"typing guard", "enter"], gap=0.05)
            await pilot.pause()
            assert app._typing_enabled is False
            config._clear_config_cache()
            assert config.get_typing_guard_enabled() is False
            assert "Typing guard off: every key acts at once" in notes(app)
        config._clear_config_cache()

    async def test_guard_off_is_the_old_behaviour(self, app):
        clock = app._typing_clock
        app._typing_enabled = False
        app.action_open_overagent = MagicMock()
        async with app.run_test() as pilot:
            await ready(pilot)
            level, timeline = app.summary_level_index, timeline_shown(app)
            await type_keys(pilot, clock, "stje")
            assert app.summary_level_index != level
            assert timeline_shown(app) != timeline
            app.action_open_overagent.assert_called_once()
            assert not app._typing_recovering


class TestUndo:

    async def test_ctrl_z_undoes_the_last_toggle(self, app):
        clock = app._typing_clock
        async with app.run_test() as pilot:
            await ready(pilot)
            timeline = timeline_shown(app)
            await type_keys(pilot, clock, ["t"])
            assert timeline_shown(app) != timeline
            clock.t += 2
            await pilot.press("ctrl+z")
            assert timeline_shown(app) == timeline
            assert any(n.startswith("Undid Timeline") for n in notes(app))
            clock.t += 2
            await pilot.press("ctrl+z")
            assert "Nothing to undo" in notes(app)

    async def test_ctrl_z_undoes_an_agent_toggle_on_that_agent(self, app):
        clock = app._typing_clock
        async with app.run_test() as pilot:
            await ready(pilot)
            await type_keys(pilot, clock, ["j"])                   # bravo
            clock.t += 2
            app.session_manager.update_session = MagicMock()
            await pilot.press("ctrl+t")                            # enhanced context on bravo
            app.session_manager.update_session.assert_called_once_with(
                "id-bravo", enhanced_context_enabled=True)
            clock.t += 2
            await type_keys(pilot, clock, ["j"])                   # charlie
            clock.t += 2
            await pilot.press("ctrl+z")
            await pilot.pause()
            assert app.session_manager.update_session.call_args_list[-1] == (
                ("id-bravo",), {"enhanced_context_enabled": False})
            assert app._get_focused_widget().session.name == "charlie"
            assert app.focused is app._get_focused_widget()

    async def test_palette_changes_can_be_undone(self, app):
        clock = app._typing_clock
        async with app.run_test() as pilot:
            await ready(pilot)
            level = app.summary_level_index
            await type_keys(pilot, clock, ["slash", *"sumdet", "enter"], gap=0.05)
            await pilot.pause()
            assert app.summary_level_index != level
            clock.t += 2
            await pilot.press("ctrl+z")
            assert app.summary_level_index == level

    async def test_undo_from_the_palette(self, app):
        clock = app._typing_clock
        async with app.run_test() as pilot:
            await ready(pilot)
            level = app.summary_level_index
            await type_keys(pilot, clock, ["s"])
            clock.t += 2
            await type_keys(pilot, clock, ["slash", *"undo last", "enter"], gap=0.05)
            await pilot.pause()
            await pilot.pause()
            assert app.summary_level_index == level

    async def test_baseline_steps_back_with_its_inverse(self, app):
        clock = app._typing_clock
        async with app.run_test() as pilot:
            await ready(pilot)
            start = app.baseline_minutes
            await type_keys(pilot, clock, [","])
            clock.t += 2
            await type_keys(pilot, clock, [","])
            assert app.baseline_minutes == start + 30
            clock.t += 2
            await pilot.press("ctrl+z")
            assert app.baseline_minutes == start + 15
            clock.t += 2
            await type_keys(pilot, clock, ["0"])  # reset to now: undone by stepping back
            assert app.baseline_minutes == 0
            clock.t += 2
            await pilot.press("ctrl+z")
            assert app.baseline_minutes == start + 15
