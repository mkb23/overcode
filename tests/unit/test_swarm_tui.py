"""→ / ←: delegated work as read-only rows in the TUI (0.6.1 observability, phase 2).

docs/design/agent-observability-0.6.1.md §4. Pilot tests on the ``make_tui``
harness: the FakeEngine plays the engine, publishing virtual rows only for
the parents this view subscribed to, as the real one does.
"""

from datetime import datetime

import pytest

from overcode import swarm
from overcode.engine_protocol import Snapshot
from tests.tui_harness import FakeEngine, ready, three_agents

pytestmark = pytest.mark.unit

NOW = 1_791_370_100.0


def _view(name, i, **fields):
    view = dict(
        session_id=f"id-{name}", name=name, current_status="running",
        current_activity=f"{name} working", status_since="2026-10-07T11:00:00",
        live_colour="green", episode_colour="green", episode_start=1_791_370_000.0 + i,
        live_since=1_791_370_000.0 + i, stats_available=True, input_tokens=1000,
        output_tokens=500, interaction_count=3, estimated_cost_usd=0.5,
        backend="claude-code",
    )
    view.update(fields)
    return view


ROLLUP = {"total": 3, "running": 1, "waiting": 1, "idle": 0, "ok": 1, "failed": 0,
          "workflows": 1, "workflows_running": 1}


def _hook_state(done=True):
    return {
        "event": "Stop", "timestamp": NOW,
        "subagents": {
            "a1": {"kind": "subagent", "type": "Explore", "started_at": NOW - 30,
                   "activity": "Read src/engine.py"},
            "w1": {"kind": "workflow", "started_at": NOW - 20, "waiting": True},
        },
        "swarm": {"bg_at": NOW,
                  "workflows": {"t1": {"status": "running", "name": "review", "run_id": "wf_1"}},
                  "done": ({"a2": {"kind": "subagent", "result": "ok", "ended_at": NOW - 5,
                                   "started_at": NOW - 50}} if done else {})},
    }


class SwarmEngine(FakeEngine):
    """Publishes bravo's virtual rows while (and only while) it is subscribed to."""

    def __init__(self):
        super().__init__()
        self.state = _hook_state()

    def snapshot_now(self):
        agents = {}
        for i, name in enumerate(("alpha", "bravo", "charlie")):
            extra = dict(swarm_rollup=ROLLUP, subagent_cost_usd=0.2) if name == "bravo" else {}
            view = _view(name, i, **extra)
            agents[view["session_id"]] = view
        virtual = {}
        if "id-bravo" in self.swarm_subscriptions:
            virtual, _ = swarm.virtual_rows("id-bravo", self.state, NOW, 30)
        return Snapshot(agents=agents, fleet={"status": "active", "current_interval": 2,
                                              "interval_mode": "attended"}, virtual=virtual)

    def tick(self):
        self.publish(self.snapshot_now())


@pytest.fixture
def engine():
    engine = SwarmEngine()
    engine.publish(engine.snapshot_now())
    return engine


@pytest.fixture
def app(make_tui, engine):
    return make_tui(three_agents(), engine=engine, size=(200, 40))


async def _on_bravo(pilot):
    await ready(pilot)
    app = pilot.app
    pilot.app.engine.client.visible = True
    widgets = app._get_widgets_in_session_order()
    app.focused_session_index = [w.session.name for w in widgets].index("bravo")
    await pilot.pause()


async def _expand(pilot, engine):
    await pilot.press("right")
    engine.tick()  # the engine's next publish after the subscription
    await pilot.pause()


def _rows(app):
    from overcode.tui_widgets.swarm_rows import SwarmRows
    return list(app.query(SwarmRows))


def _texts(app):
    rows = _rows(app)
    return [ln.text for ln in rows[0].lines] if rows else []


@pytest.mark.asyncio
class TestExpandCollapse:
    async def test_right_subscribes_and_draws_the_rows_under_the_parent(self, app, engine):
        async with app.run_test() as pilot:
            await _on_bravo(pilot)
            assert engine.swarm_subscriptions == frozenset()
            await _expand(pilot, engine)
            assert engine.swarm_subscriptions == {"id-bravo"}
            rows = _rows(app)
            assert len(rows) == 1 and rows[0].parent_id == "id-bravo"
            container = app.query_one("#sessions-container")
            children = list(container.children)
            bravo = next(w for w in app._get_widgets_in_session_order()
                         if w.session.name == "bravo")
            assert children.index(rows[0]) == children.index(bravo) + 1
            texts = _texts(app)
            assert any("🧩 review" in t for t in texts)          # the workflow group
            assert any("👥 agents" in t for t in texts)          # loose subagents
            assert any("👥 Explore" in t and "Read src/engine.py" in t for t in texts)
            assert any("needs approval" in t for t in texts)
            assert bravo.swarm_expanded

    async def test_left_on_the_agent_collapses_and_unsubscribes(self, app, engine):
        async with app.run_test() as pilot:
            await _on_bravo(pilot)
            await _expand(pilot, engine)
            await pilot.press("left")
            engine.tick()
            await pilot.pause()
            assert engine.swarm_subscriptions == frozenset()
            assert _rows(app) == []
            assert app.engine_link.virtual == {}

    async def test_the_subagents_cell_shows_the_fold_and_right_turns_it(self, app, engine):
        """The merged SubAgents column carries the ▸ that says → expands the
        row, as DLG did; it turns ▾ once the rows are open."""
        from overcode.tui_widgets.session_summary import SessionSummary

        def bravo_line():
            row = next(r for r in app.query(SessionSummary) if r.session.name == "bravo")
            return row.render().plain

        async with app.run_test() as pilot:
            await _on_bravo(pilot)
            assert "▸👥 ▶1 🟠1 ✅1 🧩1" in bravo_line()  # no total
            await _expand(pilot, engine)
            assert _rows(app)
            assert "▾👥 ▶1 🟠1 ✅1 🧩1" in bravo_line()
            await pilot.press("left")
            await pilot.pause()
            assert "▸👥" in bravo_line()

    async def test_an_agent_that_never_delegated_does_not_expand(self, app, engine):
        async with app.run_test() as pilot:
            await ready(pilot)
            pilot.app.engine.client.visible = True
            app.focused_session_index = 0  # alpha: no rollup
            await pilot.press("right")
            assert app.swarm_expanded == set() and _rows(app) == []


@pytest.mark.asyncio
class TestCursor:
    async def test_j_and_k_walk_the_rows_then_leave_them(self, app, engine):
        async with app.run_test() as pilot:
            await _on_bravo(pilot)
            await _expand(pilot, engine)
            n = len([ln for ln in _rows(app)[0].lines if ln.kind != "detail"])
            await pilot.press("j")
            assert app.swarm_cursor == _rows(app)[0].lines[0].key
            assert app._get_focused_widget().session.name == "bravo"  # focus stays on the parent
            for _ in range(n - 1):
                await pilot.press("j")
            await pilot.press("j")  # past the last row: the next agent
            assert app.swarm_cursor is None
            assert app._get_focused_widget().session.name == "charlie"
            await pilot.press("k")  # back up onto bravo's last row
            assert app._get_focused_widget().session.name == "bravo"
            assert app.swarm_cursor == [ln.key for ln in _rows(app)[0].lines
                                        if ln.kind != "detail"][-1]

    async def test_right_on_a_member_opens_its_detail_and_left_closes_it(self, app, engine):
        async with app.run_test() as pilot:
            await _on_bravo(pilot)
            await _expand(pilot, engine)
            app.swarm_cursor = "id-bravo/sa/a1"
            app._sync_swarm_rows()
            await pilot.press("right")
            detail = [ln for ln in _rows(app)[0].lines if ln.kind == "detail"]
            assert detail and "last: Read src/engine.py" in detail[0].text
            assert "Explore" in detail[0].text
            await pilot.press("left")
            assert not [ln for ln in _rows(app)[0].lines if ln.kind == "detail"]
            await pilot.press("left")  # up to its group
            assert app.swarm_cursor == "id-bravo/sa"

    async def test_a_virtual_row_is_read_only(self, app, engine, monkeypatch):
        killed, sent = [], []
        async with app.run_test() as pilot:
            await _on_bravo(pilot)
            await _expand(pilot, engine)
            monkeypatch.setattr(app, "action_kill_focused", lambda: killed.append(1))
            monkeypatch.setattr(app, "action_send_escape_to_focused", lambda: sent.append(1))
            focused_pane = []
            monkeypatch.setattr(app.split, "focus_bottom_pane", lambda: focused_pane.append(1))
            await pilot.press("j")
            assert app.swarm_cursor is not None
            await pilot.press("x")
            await pilot.press("escape")
            await pilot.press("enter")
            assert killed == [] and sent == []
            assert focused_pane == [1]  # Enter: the parent's pane (a virtual row has none)
            await pilot.press("k")  # back on bravo's own row: x works again
            assert app.swarm_cursor is None
            await pilot.press("x")
            assert killed == [1]


@pytest.mark.asyncio
class TestEngineDriven:
    async def test_a_finished_row_leaves_when_the_engine_drops_it(self, app, engine):
        async with app.run_test() as pilot:
            await _on_bravo(pilot)
            await _expand(pilot, engine)
            app.swarm_cursor = "id-bravo/sa/a2"
            app._sync_swarm_rows()
            assert any("a2" in ln.key for ln in _rows(app)[0].lines)
            engine.state = _hook_state(done=False)  # 30 s on: folded into the counts
            engine.tick()
            await pilot.pause()
            assert not any(ln.key == "id-bravo/sa/a2" for ln in _rows(app)[0].lines)
            assert app.swarm_cursor is None  # its row went: back to the parent

    async def test_a_quiet_engine_repaints_nothing(self, app, engine):
        async with app.run_test() as pilot:
            await _on_bravo(pilot)
            await _expand(pilot, engine)
            rows = _rows(app)[0]
            paints = rows.paints
            syncs = []
            real = app._sync_swarm_rows
            app._sync_swarm_rows = lambda: (syncs.append(1), real())
            for _ in range(10):
                engine.tick()
                await pilot.pause()
            assert rows.paints == paints and syncs == []

    async def test_virtual_rows_never_ring(self, app, engine, monkeypatch):
        rang = []
        async with app.run_test() as pilot:
            await _on_bravo(pilot)
            monkeypatch.setattr(app, "_ring", lambda *a: rang.append(a))
            await _expand(pilot, engine)
            assert any(r.get("light") == "orange" for r in app.engine_link.virtual.values())
            assert rang == []
            assert all(not w.is_unvisited_stalled for w in app._get_widgets_in_session_order())


@pytest.mark.asyncio
class TestKindAndSpend:
    async def test_the_kind_column_is_hidden_by_default(self, app, engine):
        """Off below full detail, and at full while every row is the same kind."""
        from overcode.summary_columns import COLUMNS_BY_ID, resolve_column_visible

        kind = COLUMNS_BY_ID["kind"]
        for level in ("low", "med", "high"):
            assert not resolve_column_visible(kind, level, {})
        async with app.run_test() as pilot:
            await _on_bravo(pilot)
            bravo = app._get_focused_widget()
            assert not bravo.column_visible(kind)
            assert app.uniform_columns.get("kind") == "agent"
            assert bravo._build_column_context().agent_kind == "agent"

    async def test_the_spend_hover_splits_own_and_delegated(self, app, engine):
        from overcode.columns.hover import hover_spend

        async with app.run_test() as pilot:
            await _on_bravo(pilot)
            text = hover_spend(app._get_focused_widget()._build_column_context()).plain
            assert "$0.50 in all" in text and "$0.30 its own" in text and "$0.20 its sub" in text


def test_timestamps_in_detail_lines_hold_still():
    """A member's detail shows when it started (a clock time), never a
    running duration: lines change only when the engine's rows do."""
    from overcode import swarm_view

    rows, _ = swarm.virtual_rows("p", _hook_state(), NOW, 30)
    a = swarm_view.layout("p", rows, details={"p/sa/a1"})
    b = swarm_view.layout("p", rows, details={"p/sa/a1"})
    assert [ln.segments for ln in a] == [ln.segments for ln in b]
    detail = next(ln for ln in a if ln.kind == "detail").text
    assert datetime.fromtimestamp(NOW - 30).strftime("%H:%M:%S") in detail
