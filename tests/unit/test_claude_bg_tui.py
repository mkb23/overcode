"""🌙 rows in the TUI: Claude background sessions (0.6.1 observability, phase 3).

Pilot tests on the ``make_tui`` harness. The FakeEngine publishes a
background session as the real engine does: an ordinary agent view with
``kind: "claude_bg"``, nested under the agent it came from. A recording
split stands in for tmux and the ``claude`` CLI.
"""

import pytest

from overcode.engine_protocol import Snapshot
from overcode.tui_split import NullSplit
from tests.tui_harness import FakeEngine, palette, ready, three_agents

pytestmark = pytest.mark.unit

SID = "b32fbee4-2c5e-435f-a55e-a2a6da6185ba"
BG = f"claude-bg:{SID}"


def _view(name, i, **fields):
    view = dict(
        session_id=f"id-{name}", name=name, current_status="running",
        current_activity=f"{name} working", status_since="2026-10-07T11:00:00",
        live_colour="color_green", episode_colour="color_green",
        episode_start=1_791_370_000.0 + i, live_since=1_791_370_000.0 + i,
        stats_available=True, input_tokens=1000, output_tokens=500, interaction_count=3,
        estimated_cost_usd=0.5, backend="claude-code",
    )
    view.update(fields)
    return view


def _bg_view(**fields):
    view = dict(
        session_id=BG, name="probe", kind="claude_bg", current_status="running",
        current_activity="Running the migration", status_since="2026-10-07T11:00:00",
        start_time="2026-10-07T10:59:00", start_directory="/tmp", repo_name="repo-b",
        parent_name="bravo", live_colour="color_green", episode_colour="color_green",
        episode_start=1_791_370_010.0, live_since=1_791_370_010.0, backend="claude-code",
        estimated_cost_usd=0.31, stats_available=True,
        claude_bg={"id": SID[:8], "session_id": SID, "parent_id": "id-bravo",
                   "via": "hooks", "cwd": "/tmp"},
    )
    view.update(fields)
    return view


class BgEngine(FakeEngine):
    def __init__(self):
        super().__init__()
        self.bg = _bg_view()

    def tick(self):
        agents = {f"id-{n}": _view(n, i) for i, n in enumerate(("alpha", "bravo", "charlie"))}
        if self.bg is not None:
            agents[BG] = self.bg
        self.publish(Snapshot(agents=agents, fleet={"status": "active", "current_interval": 2,
                                                    "interval_mode": "attended"}))


class RecordingSplit(NullSplit):
    """The split's tmux and claude side effects, recorded."""

    def __init__(self):
        self.attached, self.ran, self.shown, self.bottom = [], [], [], 0

    def attach_background(self, session):
        self.attached.append(session.short_id)
        return True

    def run_claude(self, args):
        self.ran.append(list(args))
        return True, f"{args[0]} done"

    def show_agent(self, widget):
        self.shown.append(widget.session.id if widget is not None else None)

    def focus_bottom_pane(self):
        self.bottom += 1


@pytest.fixture
def engine():
    engine = BgEngine()
    engine.tick()
    return engine


@pytest.fixture
def app(make_tui, engine):
    app = make_tui(three_agents(), engine=engine, size=(200, 40))
    app.split = RecordingSplit()
    return app


def _names(app):
    return [w.session.name for w in app._get_widgets_in_session_order()]


def _widget(app, sid=BG):
    return next(w for w in app._get_widgets_in_session_order() if w.session.id == sid)


async def _on_bg(pilot):
    await ready(pilot)
    app = pilot.app
    app.focused_session_index = [w.session.id for w in app._get_widgets_in_session_order()
                                 ].index(BG)
    await pilot.pause()


@pytest.mark.asyncio
class TestRow:
    async def test_it_is_a_row_nested_under_the_agent_it_came_from(self, app, engine):
        async with app.run_test() as pilot:
            await ready(pilot)
            names = _names(app)
            assert names.index("probe") == names.index("bravo") + 1
            row = _widget(app)
            assert row.session.parent_session_id == "id-bravo"
            assert row.detected_status == "running"
            assert row.current_activity == "Running the migration"

    async def test_its_marker_leads_the_name_and_kind_says_bg(self, app, engine):
        async with app.run_test() as pilot:
            await ready(pilot)
            ctx = _widget(app)._build_column_context()
            assert ctx.display_name.startswith("🌙 probe")
            assert ctx.agent_kind == "bg"
            assert _widget(app, "id-bravo")._build_column_context().agent_kind == "agent"
            app.emoji_free = True
            _widget(app).emoji_free = True
            assert _widget(app)._build_column_context().display_name.startswith("Bs probe")

    async def test_it_is_not_one_of_the_agent_s_children(self, app, engine):
        async with app.run_test() as pilot:
            await ready(pilot)
            assert _widget(app, "id-bravo").child_count == 0

    async def test_it_comes_and_goes_with_the_engine(self, app, engine):
        async with app.run_test() as pilot:
            await ready(pilot)
            assert "probe" in _names(app)
            engine.bg = None  # finished 30 s ago, or claude rm
            engine.tick()
            await pilot.pause()
            assert "probe" not in _names(app)
            engine.bg = _bg_view(name="renamed")
            engine.tick()
            await pilot.pause()
            assert "renamed" in _names(app)

    async def test_a_status_change_repaints_without_rebuilding_the_list(self, app, engine):
        async with app.run_test() as pilot:
            await ready(pilot)
            applied = []
            real = app._apply_sessions
            app._apply_sessions = lambda s: (applied.append(1), real(s))
            engine.bg = _bg_view(current_status="waiting_user", current_activity="Proceed?",
                                 live_colour="color_red", episode_colour="color_red")
            engine.tick()
            await pilot.pause()
            assert _widget(app).detected_status == "waiting_user"
            assert _widget(app).current_activity == "Proceed?"
            assert applied == []  # same row, new view

    async def test_the_engine_is_not_told_a_background_row_has_focus(self, app, engine):
        async with app.run_test() as pilot:
            await _on_bg(pilot)
            assert engine.client.focus is None


@pytest.mark.asyncio
class TestActions:
    async def test_enter_attaches(self, app, engine):
        async with app.run_test() as pilot:
            await _on_bg(pilot)
            await pilot.press("enter")
            assert app.split.attached == [SID[:8]]

    async def test_x_twice_stops_it_with_claude_stop(self, app, engine):
        async with app.run_test() as pilot:
            await _on_bg(pilot)
            await pilot.press("x")
            assert app.split.ran == []  # the first press only asks
            await pilot.press("x")
            await app.workers.wait_for_complete()
            assert app.split.ran == [["stop", SID[:8]]]

    async def test_a_finished_session_is_removed_from_the_palette(self, app, engine):
        engine.bg = _bg_view(current_status="done", task_result="success",
                             live_colour="color_rest", episode_colour="color_rest")
        engine.tick()
        async with app.run_test() as pilot:
            await _on_bg(pilot)
            await pilot.press("x")
            await pilot.press("x")
            assert app.split.ran == []  # already over: nothing to stop
            app.action_remove_background_session()
            app.action_remove_background_session()
            await app.workers.wait_for_complete()
            assert app.split.ran == [["rm", SID[:8]]]

    async def test_agent_actions_are_refused(self, app, engine, monkeypatch):
        calls = []
        for name in ("action_send_escape_to_focused", "action_focus_command_bar",
                     "action_rename_focused", "action_toggle_sleep", "action_restart_focused"):
            monkeypatch.setattr(app, name, lambda n=name: calls.append(n))
        async with app.run_test() as pilot:
            await _on_bg(pilot)
            for key in ("escape", "i", "ctrl+n", "z", "R"):
                await pilot.press(key)
            assert calls == []
            assert app.split.attached == [] and app.split.ran == []

    async def test_the_agent_row_still_takes_agent_actions(self, app, engine, monkeypatch):
        killed = []
        monkeypatch.setattr(app, "action_kill_focused", lambda: killed.append(1))
        async with app.run_test() as pilot:
            await _on_bg(pilot)
            await pilot.press("k")  # up onto bravo
            assert app._get_focused_widget().session.name == "bravo"
            await pilot.press("x")
            assert killed == [1]

    async def test_the_palette_lists_the_three_actions(self, app, engine):
        from overcode.command_palette import COMMANDS

        actions = {c.action for c in COMMANDS}
        assert {"attach_background_session", "stop_background_session",
                "remove_background_session"} <= actions
        async with app.run_test() as pilot:
            await _on_bg(pilot)
            assert palette(app) is not None


@pytest.mark.asyncio
class TestBell:
    async def test_a_blocked_session_rings_and_lights_its_row(self, app, engine):
        async with app.run_test() as pilot:
            await ready(pilot)
            app.engine_link.engine = engine
            engine.ring(BG, {"colour": "color_red", "start": 1_791_370_100.0, "name": "probe"})
            await pilot.pause()
            assert _widget(app).is_unvisited_stalled
            app._notifier.queue.assert_called_with("probe", "Running the migration")


class TestSplit:
    """TmuxSplit's 🌙 side effects, with tmux and the CLI mocked."""

    @staticmethod
    def _split():
        from unittest.mock import MagicMock

        from overcode.tui_split import TmuxSplit

        split = TmuxSplit.__new__(TmuxSplit)
        split.app = MagicMock(_sister_zoom_active=False)
        split.sync_target = "overcode-linked"
        split._tmux = MagicMock()
        split._tmux_manager = MagicMock()
        return split

    @staticmethod
    def _session():
        from overcode.claude_bg import session_from_view
        return session_from_view(_bg_view())

    def test_attach_opens_claude_attach_in_a_window_and_shows_it(self, monkeypatch):
        split = self._split()
        split._tmux_manager.window_exists.return_value = False
        split._tmux_manager.create_claude_attach_window.return_value = "bg:b32fbee4"
        split._tmux.select_window.return_value = True
        monkeypatch.setattr(split, "focus_bottom_pane", lambda: None)
        assert split.attach_background(self._session()) is True
        split._tmux_manager.create_claude_attach_window.assert_called_once_with(
            "bg:b32fbee4", "b32fbee4", "/tmp")
        split._tmux.select_window.assert_called_with("overcode-linked", "bg:b32fbee4")

    def test_focusing_the_row_shows_its_attach_window_or_a_placeholder(self):
        from types import SimpleNamespace

        from overcode.tmux_utils import CLAUDE_BG_PLACEHOLDER_WINDOW

        split = self._split()
        widget = SimpleNamespace(session=self._session())
        split._tmux_manager.window_exists.return_value = False
        split._tmux.ensure_empty_placeholder_window.return_value = True
        split._show_background(widget.session)
        split._tmux.select_window.assert_called_with("overcode-linked",
                                                     CLAUDE_BG_PLACEHOLDER_WINDOW)
        split._tmux_manager.window_exists.return_value = True
        split._show_background(widget.session)
        split._tmux.select_window.assert_called_with("overcode-linked", "bg:b32fbee4")

    def test_run_claude_reports_the_cli_s_last_line(self, monkeypatch):
        import subprocess
        from types import SimpleNamespace

        calls = []

        def run(args, **kw):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout="stopped b32fbee4\n", stderr="")

        monkeypatch.setattr(subprocess, "run", run)
        assert self._split().run_claude(["stop", "b32fbee4"]) == (True, "stopped b32fbee4")
        assert calls == [["claude", "stop", "b32fbee4"]]

    def test_overcode_owns_the_attach_windows(self):
        from overcode.tmux_utils import is_overcode_owned_window

        assert is_overcode_owned_window("bg:b32fbee4")
        assert is_overcode_owned_window("oc-bg")

    def test_the_attach_window_drops_the_agent_s_overcode_env(self):
        from unittest.mock import MagicMock

        from overcode.tmux_manager import TmuxManager

        manager = TmuxManager.__new__(TmuxManager)
        manager.session_name = "agents"
        manager.client = MagicMock()
        manager._session_ready = lambda: True
        manager.create_claude_attach_window("bg:x", "b32fbee4", "/w")
        (session, name), kw = manager.client.new_window.call_args
        assert (session, name, kw["cwd"]) == ("agents", "bg:x", "/w")
        assert kw["command"][0].startswith("unset OVERCODE_SESSION_NAME")
        assert kw["command"][0].endswith("exec claude attach b32fbee4")
