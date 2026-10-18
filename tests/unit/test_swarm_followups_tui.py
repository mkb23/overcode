"""Delegated work, the owner's follow-ups to phase 2 (agent-observability-0.6.1).

* finished members are the agent's ✅/❌ counts, not a line; the
  visibility dialog ("Subagent visibility…") hides them, or all but the failed ones;
* what is open survives a TUI restart (``tui_preferences.json``);
* ``X`` expands or collapses everything, ``f`` cycles the swarm-detail rule
  (off → groups → needs-you), and ← / → also fold children in tree sort.

Pilot tests on the ``make_tui`` harness; the FakeEngine publishes rows only
for what this view subscribed to, as the real engine does.
"""

import pytest

from overcode import swarm
from overcode.engine_protocol import Snapshot
from overcode.settings import TUIPreferences
from overcode.swarm_publisher import split_subscriptions
from tests.tui_harness import FakeEngine, ready, three_agents
from tests.unit.test_swarm_tui import NOW, _view

pytestmark = pytest.mark.unit

BUSY = {"total": 8, "running": 1, "waiting": 0, "idle": 0, "ok": 7, "failed": 0,
        "workflows": 0, "workflows_running": 0}
ASKING = dict(BUSY, running=0, waiting=1)
FINISHED = dict(BUSY, running=0, ok=8)


def _state(n_old=6, waiting=False):
    """One live subagent, one just finished, ``n_old`` long finished."""
    done = {f"old{i}": {"kind": "subagent", "result": "ok", "started_at": NOW - 900,
                        "ended_at": NOW - 800 + i} for i in range(n_old)}
    done["fresh"] = {"kind": "subagent", "result": "ok", "started_at": NOW - 50,
                     "ended_at": NOW - 5}
    live = {"a1": {"kind": "subagent", "type": "Explore", "started_at": NOW - 30,
                   **({"waiting": True} if waiting else {})}}
    return {"event": "Stop", "timestamp": NOW, "subagents": live,
            "swarm": {"bg_at": NOW, "done": done}}


class Engine(FakeEngine):
    """bravo and charlie delegate; rows only for what is subscribed to."""

    def __init__(self, limit=50):
        super().__init__()
        self.limit = limit
        self.rollups = {"bravo": BUSY, "charlie": FINISHED}
        self.states = {"bravo": _state(), "charlie": _state(n_old=2)}
        self.publish(self.snapshot_now())

    def snapshot_now(self):
        agents, virtual = {}, {}
        parents, done = split_subscriptions(self.swarm_subscriptions)
        for i, name in enumerate(("alpha", "bravo", "charlie")):
            rollup = self.rollups.get(name)
            view = _view(name, i, **({"swarm_rollup": rollup} if rollup else {}))
            agents[view["session_id"]] = view
            if view["session_id"] in parents and name in self.states:
                rows, _ = swarm.virtual_rows(view["session_id"], self.states[name], NOW, 30,
                                             done_open=done.get(view["session_id"], ()),
                                             done_limit=self.limit)
                virtual.update(rows)
        return Snapshot(agents=agents, fleet={"status": "active", "current_interval": 2,
                                              "interval_mode": "attended"}, virtual=virtual)

    def tick(self):
        self.publish(self.snapshot_now())


@pytest.fixture
def engine():
    return Engine()


@pytest.fixture
def app(make_tui, engine):
    return make_tui(three_agents(), engine=engine, size=(200, 40))


async def _start(pilot, name="bravo"):
    await ready(pilot)
    app = pilot.app
    app.engine.client.visible = True
    _focus(app, name)
    await pilot.pause()


def _focus(app, name):
    widgets = app._get_widgets_in_session_order()
    app.focused_session_index = [w.session.name for w in widgets].index(name)


async def _settle(pilot, engine):
    engine.tick()  # the engine's next publish after a subscription change
    await pilot.pause()


async def _press(pilot, engine, *keys):
    for key in keys:
        await pilot.press(key)
        await _settle(pilot, engine)


def _lines(app, parent="id-bravo"):
    from overcode.tui_widgets.swarm_rows import SwarmRows

    rows = [r for r in app.query(SwarmRows) if r.parent_id == parent]
    return rows[0].lines if rows else []


def _keys(app, parent="id-bravo"):
    return [ln.key for ln in _lines(app, parent)]


def _saved(app):
    return TUIPreferences.load(app.tmux_session)


def _suba(app, name="bravo"):
    from overcode.tui_widgets import SessionSummary

    w = next(w for w in app.query(SessionSummary) if w.session.name == name)
    cols = [c.id for c in w.visible_columns()]
    return w.row_cells()[cols.index("subagents")].plain


@pytest.mark.asyncio
class TestVisibilityDialog:
    async def test_hiding_finished_drops_their_rows_and_counts(self, app,
                                                                             engine):
        from overcode.tui_widgets import AgentVisibilityModal

        engine.rollups["bravo"] = dict(BUSY, failed=1)
        engine.tick()
        async with app.run_test() as pilot:
            await _start(pilot)
            await _press(pilot, engine, "right")
            assert _keys(app) == ["id-bravo/sa/a1", "id-bravo/sa/fresh"]
            assert "✅7" in _suba(app) and "❌1" in _suba(app)
            app.action_open_agent_visibility()
            await pilot.pause()
            modal = app.query_one(AgentVisibilityModal)
            assert "visible" in modal.classes
            await pilot.press("j", "j", "j", "j")  # Subagents: Finished
            await _press(pilot, engine, "space")  # → failures only
            assert app.swarm_finished == "failed"
            assert _keys(app) == ["id-bravo/sa/a1"]  # the fresh one finished ok
            assert "✅" not in _suba(app) and "❌1" in _suba(app)
            await _press(pilot, engine, "space")  # → hide
            assert "❌" not in _suba(app) and "▶1" in _suba(app)
            await pilot.press("escape")
            assert "visible" not in modal.classes
        assert _saved(app).swarm_finished == "hide"

    async def test_the_agent_rows_toggle_killed_sleeping_and_done(self, app, engine):
        async with app.run_test() as pilot:
            await _start(pilot)
            app.action_open_agent_visibility()
            await pilot.pause()
            assert (app.show_terminated, app.hide_asleep, app.show_done) == (False, False, False)
            await pilot.press("space", "j", "space", "j", "space")
            await pilot.pause()
            assert (app.show_terminated, app.hide_asleep, app.show_done) == (True, True, True)
            await pilot.press("escape")
        saved = _saved(app)
        assert saved.show_terminated and saved.hide_asleep

    async def test_the_finished_row_time_is_written_to_config(self, app, engine):
        from overcode.config import get_observability_config

        async with app.run_test() as pilot:
            await _start(pilot)
            app.action_open_agent_visibility()
            await pilot.pause()
            await pilot.press("j", "j", "j", "j", "j", "space")  # Finished row stays: 30s → 2m
            await pilot.pause()
            assert get_observability_config()["finished_row_seconds"] == 120.0
            await pilot.press("left", "left")     # → 10s
            await pilot.pause()
            assert get_observability_config()["finished_row_seconds"] == 10.0

    async def test_an_odd_saved_value_shows_everything(self, make_tui):
        prefs = TUIPreferences()
        prefs.swarm_finished = "sometimes"
        prefs.save("test")
        engine = Engine()
        app = make_tui(three_agents(), engine=engine, size=(200, 40))
        async with app.run_test() as pilot:
            await _start(pilot)
            assert app.swarm_finished == "show"


@pytest.mark.asyncio
class TestNoDoneLine:
    async def test_finished_members_are_the_agent_s_count_not_a_line(self, app, engine):
        """Owner, 2026-10-18: the done line repeated the SubAgents cell."""
        async with app.run_test() as pilot:
            await _start(pilot)
            await _press(pilot, engine, "right")
            assert engine.swarm_subscriptions == {"id-bravo"}
            assert "id-bravo/sa/old5" not in engine.snapshot.virtual  # folded: not paid for
            assert _keys(app) == ["id-bravo/sa/a1", "id-bravo/sa/fresh"]


def _many(n=12):
    """``n`` live general-purpose subagents, one type, nothing finished."""
    live = {f"a{i:02d}": {"kind": "subagent", "type": "general-purpose",
                          "started_at": NOW - 100 + i} for i in range(n)}
    return {"event": "Stop", "timestamp": NOW, "subagents": live,
            "swarm": {"bg_at": NOW, "done": {}}}


@pytest.mark.asyncio
class TestLargeList:
    async def test_more_than_eight_collapse_to_the_top_three_and_more_opens_them(self, app,
                                                                                engine):
        engine.states["bravo"] = _many()
        async with app.run_test() as pilot:
            await _start(pilot)
            await _press(pilot, engine, "right")
            assert _keys(app) == ["id-bravo/sa/a00", "id-bravo/sa/a01", "id-bravo/sa/a02",
                                  "id-bravo/+more"]
            assert "+9 more" in _lines(app)[-1].text
            # One type on screen: the TYPE column takes no width
            assert not app.swarm_types_shown and not app.fleet.swarm_types_shown
            app.swarm_cursor = "id-bravo/+more"
            app._sync_swarm_rows()
            await _press(pilot, engine, "right")
            assert len(_keys(app)) == 12
            assert app.swarm_opened == {"id-bravo/members": True}
            assert _saved(app).swarm_opened == {"id-bravo/members": True}
            # ← back on the agent and ← again: collapsed; it reopens compact
            app.swarm_cursor = None
            await _press(pilot, engine, "left")
            assert app.swarm_expanded == set() and app.swarm_opened == {}
            await _press(pilot, engine, "right")
            assert _keys(app)[-1] == "id-bravo/+more"


@pytest.mark.asyncio
class TestRestart:
    async def test_expansion_and_open_lines_survive_a_restart(self, make_tui):
        engine = Engine()
        engine.states["bravo"] = _many()
        first = make_tui(three_agents(), engine=engine, size=(200, 40))
        async with first.run_test() as pilot:
            await _start(pilot)
            await _press(pilot, engine, "right")
            first.swarm_cursor = "id-bravo/+more"
            first._sync_swarm_rows()
            await _press(pilot, engine, "right")
        saved = _saved(first)
        assert saved.swarm_expanded == {"id-bravo": True}
        assert saved.swarm_opened == {"id-bravo/members": True}

        engine = Engine()
        engine.states["bravo"] = _many()
        second = make_tui(three_agents(), engine=engine, size=(200, 40))
        async with second.run_test() as pilot:
            await ready(pilot)
            second.engine.client.visible = True
            await _settle(pilot, engine)
            assert second.swarm_expanded == {"id-bravo"}
            assert engine.client.swarm == {"id-bravo"}
            assert len(_keys(second)) == 12

    async def test_stale_and_odd_saved_values_are_dropped(self, make_tui):
        prefs = TUIPreferences()
        prefs.swarm_detail = "everything"            # not a mode
        prefs.swarm_expanded = {"id-bravo": True, "id-gone": True, "id-charlie": "yes"}
        # Keys of earlier layouts (a group, any done line) are dropped
        prefs.swarm_opened = {"id-gone/done": True, "id-bravo/sa": False, "x": True,
                              "id-bravo/sa/done": True, "id-bravo/wf/r/ph/0": True}
        prefs.save("test")
        engine = Engine()
        app = make_tui(three_agents(), engine=engine, size=(200, 40))
        async with app.run_test() as pilot:
            await _start(pilot)
            await _settle(pilot, engine)
            assert app.swarm_detail == "off"
            assert app.swarm_expanded == {"id-bravo"}
            assert app.swarm_opened == {}  # done lines are gone, every kind of them
            assert engine.client.swarm == {"id-bravo"}
            assert _keys(app) == ["id-bravo/sa/a1", "id-bravo/sa/fresh"]
            app._save_swarm_prefs()  # the next save prunes the gone agent
        saved = _saved(app)
        assert saved.swarm_expanded == {"id-bravo": True}
        assert saved.swarm_opened == {}

    async def test_an_old_preferences_file_still_loads(self, tui_state_dir):
        import json

        from overcode.settings import get_tui_preferences_path

        path = get_tui_preferences_path("test")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"sort_mode": "by_tree", "collapsed_parents": ["a"],
                                    "some_future_key": 1}))
        prefs = TUIPreferences.load("test")
        assert prefs.collapsed_parents == {"a"}
        assert (prefs.swarm_detail, prefs.swarm_expanded, prefs.swarm_opened) == ("off", {}, {})


@pytest.mark.asyncio
class TestDetailCycle:
    async def test_f_cycles_off_groups_needs_you(self, app, engine):
        async with app.run_test() as pilot:
            await _start(pilot)
            assert app.swarm_expanded == set()
            await _press(pilot, engine, "f")
            # groups: every agent with work in flight (charlie's is all finished)
            assert app.swarm_detail == "groups"
            assert app.swarm_expanded == {"id-bravo"} == engine.client.swarm
            assert _keys(app)
            await _press(pilot, engine, "f")
            # needs-you: nobody waits on a permission
            assert app.swarm_detail == "needs_you"
            assert app.swarm_expanded == set()
            engine.rollups["bravo"] = ASKING
            engine.states["bravo"] = _state(waiting=True)
            await _settle(pilot, engine)
            assert app.swarm_expanded == {"id-bravo"}  # opened by itself
            await _settle(pilot, engine)  # the engine answers the new subscription
            assert any("needs approval" in ln.text for ln in _lines(app))
            await _press(pilot, engine, "f")
            assert app.swarm_detail == "off" and app.swarm_expanded == set()
            assert _saved(app).swarm_detail == "off"

    async def test_left_overrides_the_rule_for_one_agent(self, app, engine):
        async with app.run_test() as pilot:
            await _start(pilot)
            await _press(pilot, engine, "f", "left")
            assert app.swarm_expanded == set()
            assert app.swarm_manual == {"id-bravo": False}
            await _press(pilot, engine, "right")
            assert app.swarm_expanded == {"id-bravo"}
            assert app.swarm_manual == {}  # the rule agrees again: no override kept

    async def test_the_palette_shows_the_mode(self, app, engine):
        from overcode.command_palette import COMMANDS

        async with app.run_test() as pilot:
            await _start(pilot)
            await _press(pilot, engine, "f")
            command = next(c for c in COMMANDS if c.action == "cycle_swarm_detail")
            state = command.state(app)
            assert state.options[state.current] == "groups"


@pytest.mark.asyncio
class TestExpandAll:
    async def test_x_expands_every_delegating_agent_then_collapses_all(self, app, engine):
        async with app.run_test() as pilot:
            await _start(pilot, "alpha")
            await _press(pilot, engine, "X")
            assert app.swarm_expanded == {"id-bravo", "id-charlie"}
            assert engine.client.swarm == {"id-bravo", "id-charlie"}
            assert _keys(app, "id-charlie")
            await _press(pilot, engine, "X")
            assert app.swarm_expanded == set() and engine.client.swarm == set()
            assert _saved(app).swarm_expanded == {}

    async def test_x_collapses_when_anything_is_open(self, app, engine):
        async with app.run_test() as pilot:
            await _start(pilot)
            await _press(pilot, engine, "right", "X")
            assert app.swarm_expanded == set()


def _tree():
    sessions = three_agents()
    sessions[2].parent_session_id = "id-bravo"  # charlie is bravo's child
    return sessions


@pytest.mark.asyncio
class TestTreeFold:
    async def test_left_and_right_fold_children_and_x_folds_them_all(self, make_tui):
        prefs = TUIPreferences()
        prefs.sort_mode = "by_tree"
        prefs.save("test")
        engine = Engine()
        engine.rollups = {}  # nobody delegates: only the children fold
        app = make_tui(_tree(), engine=engine, size=(200, 40))
        async with app.run_test() as pilot:
            await _start(pilot, "charlie")

            def shown():
                return [w.session.name for w in app._get_widgets_in_session_order() if w.display]

            assert "charlie" in shown()
            await pilot.press("left")  # on a child: up to its parent
            assert app._get_focused_widget().session.name == "bravo"
            await pilot.press("left")  # on the parent: fold its children
            await pilot.pause()
            assert app.collapsed_parents == {"id-bravo"}
            await pilot.press("right")
            await pilot.pause()
            assert app.collapsed_parents == set()
            await pilot.press("X")  # everything open: fold all
            await pilot.pause()
            assert app.collapsed_parents == {"id-bravo"}
            assert _saved(app).collapsed_parents == {"id-bravo"}
            await pilot.press("X")
            await pilot.pause()
            assert app.collapsed_parents == set()
