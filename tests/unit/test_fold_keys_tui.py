"""← / → / X: one tree view over every agent kind.

An agent row is a node whose children are its overcode children and 🌙
background sessions (tree sort only) and its delegated work (👥 subagents,
workflows: virtual rows). → opens a closed node (every kind at once), or
moves onto its first child row when it is open; ← closes an open node
(every kind at once), or moves to its parent; X closes everything when
anything is open, else opens everything. In a flat sort children are not
nested: only delegated work opens and closes.

Pilot tests on the ``make_tui`` harness; the engine publishes virtual rows
only for the parents this view subscribed to, as the real engine does.
"""

import pytest

from overcode import swarm
from overcode.engine_protocol import Snapshot
from overcode.session_manager import Session
from overcode.settings import TUIPreferences
from overcode.swarm_publisher import subscribed_parents
from tests.tui_harness import FakeEngine
from tests.unit.test_swarm_followups_tui import BUSY, _state
from tests.unit.test_swarm_tui import NOW, _view

pytestmark = pytest.mark.unit

BG = "claude-bg:b32fbee4-2c5e-435f-a55e-a2a6da6185ba"

# name, parent, status, asleep
TREE = [
    ("alpha", None, "running", False),
    ("bravo", None, "running", False),
    ("charlie", "bravo", "running", False),
    ("delta", "charlie", "running", False),
    ("echo", "bravo", "done", False),
    ("fox", "bravo", "terminated", False),
    ("golf", "bravo", "running", True),
    ("hotel", None, "running", False),
    ("india", None, "running", False),
    ("juliet", "india", "done", False),
]


def _sessions():
    return [Session(id=f"id-{n}", name=n, tmux_session="test", tmux_window=f"w{i}",
                    command=["claude"], start_directory="/tmp",
                    start_time="2026-09-24T10:00:00", repo_name="repo", branch="main",
                    status=status, is_asleep=asleep,
                    parent_session_id=f"id-{p}" if p else None)
            for i, (n, p, status, asleep) in enumerate(TREE)]


def _bg_view():
    return dict(
        session_id=BG, name="probe", kind="claude_bg", current_status="running",
        current_activity="Running the migration", status_since="2026-10-07T11:00:00",
        start_time="2026-10-07T10:59:00", start_directory="/tmp", repo_name="repo",
        live_colour="color_green", episode_colour="color_green",
        episode_start=1_791_370_010.0, live_since=1_791_370_010.0, backend="claude-code",
        stats_available=True,
        claude_bg={"id": "b32fbee4", "session_id": BG[10:], "parent_id": "id-alpha",
                   "via": "hooks", "cwd": "/tmp"})


class Engine(FakeEngine):
    """bravo and hotel delegate; alpha has a 🌙 background session."""

    def __init__(self):
        super().__init__()
        self.rollups = {"bravo": BUSY, "hotel": BUSY}
        self.tick()

    def tick(self):
        agents, virtual = {}, {}
        parents = subscribed_parents(self.swarm_subscriptions)
        for i, (name, *_rest) in enumerate(TREE):
            rollup = self.rollups.get(name)
            view = _view(name, i, **({"swarm_rollup": rollup} if rollup else {}))
            agents[view["session_id"]] = view
            if view["session_id"] in parents and rollup:
                rows, _ = swarm.virtual_rows(view["session_id"], _state(n_old=0), NOW, 30)
                virtual.update(rows)
        agents[BG] = _bg_view()
        self.publish(Snapshot(agents=agents, fleet={"status": "active", "current_interval": 2,
                                                    "interval_mode": "attended"},
                              virtual=virtual))


def _app(make_tui, sort="by_tree", **prefs):
    saved = TUIPreferences()
    saved.sort_mode = sort
    for k, v in prefs.items():
        setattr(saved, k, v)
    saved.save("test")
    return make_tui(_sessions(), engine=Engine(), size=(200, 50))


async def _start(pilot, name):
    # Not ready(): the timeline leaves the sleeping agent out, so its
    # rows never match the agents'; nothing here measures or clicks a row
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()
    pilot.app.engine.client.visible = True
    _focus(pilot.app, name)
    await pilot.pause()


def _focus(app, name):
    widgets = app._get_widgets_in_session_order()
    app._user_navigated = True
    app.focused_session_index = [w.session.name for w in widgets].index(name)


async def _press(pilot, *keys):
    for key in keys:
        await pilot.press(key)
        pilot.app.engine.tick()
        await pilot.pause()


def _shown(app):
    return [w.session.name for w in app._get_widgets_in_session_order()]


def _at(app):
    widget = app._get_focused_widget()
    return widget.session.name, app.swarm_cursor


def _notes(app):
    return [str(n.message) for n in app._notifications]


@pytest.mark.asyncio
class TestTreeRight:
    async def test_right_opens_children_and_delegated_work_together(self, make_tui):
        app = _app(make_tui, collapsed_parents={"id-bravo"})
        async with app.run_test() as pilot:
            await _start(pilot, "bravo")
            assert "charlie" not in _shown(app)
            await _press(pilot, "right")
            assert "charlie" in _shown(app)
            assert "id-bravo" in app.swarm_expanded
            assert _at(app) == ("bravo", None)

    async def test_right_on_a_half_open_node_opens_the_rest(self, make_tui):
        app = _app(make_tui)  # children shown, delegated work closed
        async with app.run_test() as pilot:
            await _start(pilot, "bravo")
            await _press(pilot, "right")
            assert "id-bravo" in app.swarm_expanded and "charlie" in _shown(app)
            assert _at(app) == ("bravo", None)

    async def test_right_on_an_open_node_moves_onto_its_first_row(self, make_tui):
        app = _app(make_tui)
        async with app.run_test() as pilot:
            await _start(pilot, "bravo")
            await _press(pilot, "right", "right")
            assert _at(app) == ("bravo", "id-bravo/sa/a1")  # the virtual rows come first

    async def test_right_on_an_open_agent_without_delegated_work_moves_to_its_child(self, make_tui):
        app = _app(make_tui)
        async with app.run_test() as pilot:
            await _start(pilot, "charlie")
            await _press(pilot, "right")
            assert _at(app) == ("delta", None)

    async def test_right_moves_onto_a_background_session(self, make_tui):
        app = _app(make_tui)
        async with app.run_test() as pilot:
            await _start(pilot, "alpha")
            await _press(pilot, "right")
            assert _at(app) == ("probe", None)

    async def test_right_on_a_leaf_says_so(self, make_tui):
        app = _app(make_tui)
        async with app.run_test() as pilot:
            await _start(pilot, "delta")
            await _press(pilot, "right")
            assert _at(app) == ("delta", None)
            assert app.collapsed_parents == set() and app.swarm_expanded == set()
            assert any("Nothing to expand" in n for n in _notes(app))

    async def test_children_hidden_by_visibility_do_not_make_a_node(self, make_tui):
        """india's only child is done (hidden): india is a leaf."""
        app = _app(make_tui)
        async with app.run_test() as pilot:
            await _start(pilot, "india")
            await _press(pilot, "left")
            assert app.collapsed_parents == set()
            await _press(pilot, "right")
            assert any("Nothing to expand" in n for n in _notes(app))


@pytest.mark.asyncio
class TestTreeLeft:
    async def test_left_closes_children_and_delegated_work_together(self, make_tui):
        app = _app(make_tui)
        async with app.run_test() as pilot:
            await _start(pilot, "bravo")
            await _press(pilot, "right")  # all open
            await _press(pilot, "left")
            assert app.swarm_expanded == set()
            assert "id-bravo" in app.collapsed_parents
            assert "charlie" not in _shown(app)
            assert _at(app) == ("bravo", None)
            assert TUIPreferences.load("test").collapsed_parents == {"id-bravo"}

    async def test_left_on_a_closed_node_moves_to_its_parent(self, make_tui):
        app = _app(make_tui, collapsed_parents={"id-charlie"})
        async with app.run_test() as pilot:
            await _start(pilot, "charlie")
            await _press(pilot, "left")
            assert _at(app) == ("bravo", None)
            assert app.collapsed_parents == {"id-charlie"}

    async def test_left_on_a_leaf_moves_to_its_parent(self, make_tui):
        app = _app(make_tui)
        async with app.run_test() as pilot:
            await _start(pilot, "delta")
            await _press(pilot, "left")
            assert _at(app) == ("charlie", None)
            await _press(pilot, "left")  # charlie is open: close it
            assert app.collapsed_parents == {"id-charlie"}
            await _press(pilot, "left")  # closed: up to bravo
            assert _at(app) == ("bravo", None)

    async def test_left_on_a_background_session_moves_to_its_agent(self, make_tui):
        app = _app(make_tui)
        async with app.run_test() as pilot:
            await _start(pilot, "probe")
            await _press(pilot, "left")
            assert _at(app) == ("alpha", None)

    async def test_left_at_a_closed_root_does_nothing(self, make_tui):
        app = _app(make_tui)
        async with app.run_test() as pilot:
            await _start(pilot, "india")
            await _press(pilot, "left")
            assert _at(app) == ("india", None) and app.collapsed_parents == set()

    async def test_left_on_a_virtual_row_goes_to_its_owner(self, make_tui):
        app = _app(make_tui)
        async with app.run_test() as pilot:
            await _start(pilot, "bravo")
            await _press(pilot, "right", "right")  # open, then onto the first row
            assert _at(app) == ("bravo", "id-bravo/sa/a1")
            await _press(pilot, "right")  # its details open
            assert "id-bravo/sa/a1" in app.swarm_details
            await _press(pilot, "left")  # details close
            assert app.swarm_details == set() and app.swarm_cursor == "id-bravo/sa/a1"
            await _press(pilot, "left")  # up to the owner
            assert _at(app) == ("bravo", None)
            await _press(pilot, "left")  # and the owner closes, all kinds
            assert app.swarm_expanded == set() and "id-bravo" in app.collapsed_parents

    async def test_right_on_a_member_with_details_open_says_so(self, make_tui):
        app = _app(make_tui)
        async with app.run_test() as pilot:
            await _start(pilot, "bravo")
            await _press(pilot, "right", "right", "right", "right")
            assert app.swarm_cursor == "id-bravo/sa/a1"
            assert any("Nothing to expand" in n for n in _notes(app))


@pytest.mark.asyncio
class TestVisibility:
    async def test_done_killed_and_sleeping_children_fold_with_the_rest(self, make_tui):
        app = _app(make_tui, show_done_children=True, show_terminated=True)
        async with app.run_test() as pilot:
            await _start(pilot, "bravo")
            assert {"echo", "fox", "golf"} <= set(_shown(app))
            await _press(pilot, "right", "left")
            assert not {"charlie", "echo", "fox", "golf"} & set(_shown(app))
            await _press(pilot, "right")
            assert {"charlie", "echo", "fox", "golf"} <= set(_shown(app))

    async def test_a_shown_done_child_makes_its_parent_a_node(self, make_tui):
        app = _app(make_tui, show_done_children=True)
        async with app.run_test() as pilot:
            await _start(pilot, "india")
            await _press(pilot, "left")
            assert app.collapsed_parents == {"id-india"}
            assert "juliet" not in _shown(app)


@pytest.mark.asyncio
class TestHighlight:
    async def test_folded_chosen_agent_has_its_nearest_shown_ancestor_stand_in(self, make_tui):
        app = _app(make_tui)
        async with app.run_test() as pilot:
            await _start(pilot, "delta")
            await _press(pilot, "X")  # nothing delegated open; children are: close all
            assert "delta" not in _shown(app)
            assert _at(app) == ("bravo", None)
            await _press(pilot, "X")  # open everything: delta is back, highlighted
            assert _at(app) == ("delta", None)


@pytest.mark.asyncio
class TestExpandAll:
    async def test_x_closes_everything_then_opens_everything(self, make_tui):
        app = _app(make_tui)
        async with app.run_test() as pilot:
            await _start(pilot, "hotel")
            await _press(pilot, "X")  # children are open: close all
            assert app.collapsed_parents >= {"id-alpha", "id-bravo", "id-charlie"}
            assert app.swarm_expanded == set()
            await _press(pilot, "X")
            assert app.collapsed_parents == set()
            assert app.swarm_expanded == {"id-bravo", "id-hotel"}
            assert {"charlie", "delta", "probe"} <= set(_shown(app))
            await _press(pilot, "X")
            assert app.swarm_expanded == set() and "id-bravo" in app.collapsed_parents

    async def test_hidden_children_do_not_count_as_open(self, make_tui):
        """india's child is hidden: everything visible closed means X opens."""
        app = _app(make_tui, collapsed_parents={"id-alpha", "id-bravo", "id-charlie"})
        async with app.run_test() as pilot:
            await _start(pilot, "hotel")
            await _press(pilot, "X")
            assert app.swarm_expanded == {"id-bravo", "id-hotel"}

    async def test_x_closes_open_details(self, make_tui):
        app = _app(make_tui)
        async with app.run_test() as pilot:
            await _start(pilot, "bravo")
            await _press(pilot, "right", "right", "right")
            assert app.swarm_details
            await _press(pilot, "X")
            assert app.swarm_details == set() and app.swarm_cursor is None


@pytest.mark.asyncio
class TestFlatSort:
    async def test_flat_sort_opens_and_closes_delegated_work_only(self, make_tui):
        app = _app(make_tui, sort="alphabetical")
        async with app.run_test() as pilot:
            await _start(pilot, "bravo")
            await _press(pilot, "right")
            assert app.swarm_expanded == {"id-bravo"}
            await _press(pilot, "left")
            assert app.swarm_expanded == set() and app.collapsed_parents == set()
            await _press(pilot, "left")  # no folding in a flat sort: nothing
            assert app.collapsed_parents == set() and _at(app) == ("bravo", None)

    async def test_flat_sort_left_on_a_child_stays(self, make_tui):
        app = _app(make_tui, sort="alphabetical")
        async with app.run_test() as pilot:
            await _start(pilot, "charlie")
            await _press(pilot, "left")
            assert _at(app) == ("charlie", None)

    async def test_flat_sort_x_leaves_children_alone(self, make_tui):
        app = _app(make_tui, sort="alphabetical")
        async with app.run_test() as pilot:
            await _start(pilot, "alpha")
            await _press(pilot, "X")
            assert app.swarm_expanded == {"id-bravo", "id-hotel"}
            await _press(pilot, "X")
            assert app.swarm_expanded == set() and app.collapsed_parents == set()


@pytest.mark.asyncio
class TestPersistence:
    async def test_a_fold_survives_a_refresh(self, make_tui):
        app = _app(make_tui)
        async with app.run_test() as pilot:
            await _start(pilot, "charlie")
            await _press(pilot, "left")
            assert app.collapsed_parents == {"id-charlie"}
            app._apply_sessions(_sessions())
            await pilot.pause()
            assert app.collapsed_parents == {"id-charlie"}
            assert "delta" not in _shown(app)


@pytest.mark.asyncio
class TestFoldUnfoldRow:
    """The palette's "Fold / unfold row" (toggle_collapse_children): → and
    ← as one toggle; a leaf child toggles its parent."""

    async def test_toggles_every_kind_at_once(self, make_tui):
        app = _app(make_tui)
        async with app.run_test() as pilot:
            await _start(pilot, "bravo")
            app.action_toggle_collapse_children()  # half open: close it all
            await pilot.pause()
            assert "id-bravo" in app.collapsed_parents and app.swarm_expanded == set()
            app.action_toggle_collapse_children()
            await pilot.pause()
            assert "id-bravo" not in app.collapsed_parents
            assert "id-bravo" in app.swarm_expanded

    async def test_a_leaf_child_toggles_its_parent(self, make_tui):
        app = _app(make_tui)
        async with app.run_test() as pilot:
            await _start(pilot, "delta")
            app.action_toggle_collapse_children()
            await pilot.pause()
            assert app.collapsed_parents == {"id-charlie"}
            assert _at(app) == ("charlie", None)

    async def test_a_lone_agent_warns(self, make_tui):
        app = _app(make_tui)
        async with app.run_test() as pilot:
            await _start(pilot, "india")
            app.action_toggle_collapse_children()
            await pilot.pause()
            assert app.collapsed_parents == set()
            assert any("No children" in n for n in _notes(app))


def test_config_naming_fold_unfold_row_still_binds():
    from overcode.keymap import build_keymap, default_scope_bindings, extra_app_actions

    km = build_keymap(default_scope_bindings(), None,
                      {"app": {"toggle_collapse_children": ["ctrl+t"]}}, extra_app_actions())
    assert not [w for w in km.warnings if "unknown action" in w or "removed" in w]
    assert any(b.action == "toggle_collapse_children" and b.key == "ctrl+t"
               for b in km.scopes["app"])


@pytest.mark.asyncio
class TestFoldMarker:
    async def test_the_fold_marker_shows_in_tree_sort_only(self, make_tui):
        app = _app(make_tui, sort="alphabetical", collapsed_parents={"id-bravo", "id-india"})
        async with app.run_test() as pilot:
            await _start(pilot, "bravo")
            rows = {w.session.name: w for w in app._get_widgets_in_session_order()}
            assert "charlie" in rows and not rows["bravo"].children_collapsed

    async def test_no_marker_where_the_fold_hides_nothing_shown(self, make_tui):
        app = _app(make_tui, collapsed_parents={"id-bravo", "id-india"})
        async with app.run_test() as pilot:
            await _start(pilot, "bravo")
            rows = {w.session.name: w for w in app._get_widgets_in_session_order()}
            assert rows["bravo"].children_collapsed
            assert not rows["india"].children_collapsed  # juliet is hidden anyway


@pytest.mark.asyncio
class TestExpandAllInsideFolds:
    async def test_x_opens_delegated_work_of_agents_it_unfolds(self, make_tui):
        app = _app(make_tui, collapsed_parents={"id-alpha", "id-bravo", "id-charlie"})
        app.engine.rollups["charlie"] = BUSY
        app.engine.tick()
        async with app.run_test() as pilot:
            await _start(pilot, "hotel")
            assert "charlie" not in _shown(app)
            await _press(pilot, "X")
            assert "charlie" in _shown(app)
            assert app.swarm_expanded == {"id-bravo", "id-charlie", "id-hotel"}
