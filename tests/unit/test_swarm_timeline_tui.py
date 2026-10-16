"""The timeline's density rows for delegated work (0.6.1 observability, phase 4).

docs/design/agent-observability-0.6.1.md §5. Pilot tests on the ``make_tui``
harness: a parent's swarm_history.csv rows become one strip under its
timeline row while it is collapsed in the list, and one strip per group
once → expands it; ← folds them back. Folding reads no file; a fold
change on a parent with no strips does not repaint.
"""

import csv
from datetime import datetime, timedelta

import pytest

from overcode import swarm_history as sh
from overcode.settings import get_swarm_history_path
from overcode.tui_widgets.status_timeline import StatusTimeline, swarm_strips
from tests.tui_harness import ready, three_agents
from tests.unit.test_swarm_tui import SwarmEngine, _expand, _on_bravo

pytestmark = pytest.mark.unit


def _seed(tmux_session="test"):
    path = get_swarm_history_path(tmux_session)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    rows = [
        (40, "*", "swarm", "", (2, 0, 1, 0, 0, 0, 1)),
        (40, "wf/wf_1", "workflow", "review", (0, 0, 1, 0, 0, 0, 0)),
        (40, "sa", "agents", "agents", (2, 0, 0, 0, 0, 0, 0)),
        (20, "*", "swarm", "", (1, 0, 0, 0, 1, 1, 1)),
        (20, "sa", "agents", "agents", (1, 0, 0, 0, 0, 1, 0)),
    ]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(sh.HEADER)
        for minutes, group, kind, title, counts in rows:
            w.writerow([(now - timedelta(minutes=minutes)).isoformat(), "bravo", "id-bravo",
                        group, kind, title, *counts])
    return path


@pytest.fixture
def engine():
    engine = SwarmEngine()
    engine.publish(engine.snapshot_now())
    return engine


@pytest.fixture
def app(make_tui, engine):
    _seed()
    return make_tui(three_agents(), engine=engine, size=(200, 50))


def _text(app):
    return app.query_one("#timeline", StatusTimeline).render().plain


async def _show_timeline(pilot):
    app = pilot.app
    timeline = app.query_one("#timeline", StatusTimeline)
    timeline.display = True
    app.update_timeline()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()
    return timeline


def _strip_lines(text):
    return [ln for ln in text.splitlines() if "└ " in ln]


@pytest.mark.asyncio
class TestStrips:
    async def test_a_collapsed_parent_has_one_strip_under_its_row(self, app, engine):
        async with app.run_test() as pilot:
            await _on_bravo(pilot)
            await _show_timeline(pilot)
            lines = _text(app).splitlines()
            bravo = next(i for i, ln in enumerate(lines) if ln.strip().startswith("bravo"))
            assert "└ 👥 swarm" in lines[bravo + 1]
            assert len(_strip_lines(_text(app))) == 1
            assert "↑3" in lines[bravo + 1]          # the peak live count
            assert any(g in lines[bravo + 1] for g in sh.LEVELS)
            assert "delegated work" in lines[-1]      # the legend says what it is

    async def test_expanding_in_the_list_unfolds_the_strips_per_group(self, app, engine):
        async with app.run_test() as pilot:
            await _on_bravo(pilot)
            timeline = await _show_timeline(pilot)
            await _expand(pilot, engine)
            assert timeline.swarm_expanded == {"id-bravo"}
            strips = _strip_lines(_text(app))
            assert [s.split("└ ")[1].split()[1] for s in strips] == ["review", "agents"]
            await pilot.press("left")
            await pilot.pause()
            assert timeline.swarm_expanded == frozenset()
            assert len(_strip_lines(_text(app))) == 1

    async def test_folding_reads_no_file(self, app, engine, monkeypatch):
        async with app.run_test() as pilot:
            await _on_bravo(pilot)
            await _show_timeline(pilot)
            reads = []
            monkeypatch.setattr(sh, "read_swarm_history",
                                lambda *a, **kw: reads.append(a) or {})
            await _expand(pilot, engine)
            await pilot.press("left")
            await pilot.pause()
            assert reads == []

    async def test_no_history_no_strips(self, make_tui, engine):
        get_swarm_history_path("test").unlink(missing_ok=True)
        app = make_tui(three_agents(), engine=engine, size=(200, 50))
        async with app.run_test() as pilot:
            await ready(pilot)
            await _show_timeline(pilot)
            assert _strip_lines(_text(app)) == []
            assert "delegated work" not in _text(app)


class TestFold:
    def _widget(self, histories):
        from types import SimpleNamespace

        widget = StatusTimeline.__new__(StatusTimeline)
        widget.sessions = [SimpleNamespace(id="id-a", name="a", is_remote=False),
                           SimpleNamespace(id="id-b", name="b", is_remote=False)]
        widget._swarm_histories = histories
        widget.refreshes = 0

        def refresh(*a, **kw):
            widget.refreshes += 1
        widget.refresh = refresh
        return widget

    def test_a_fold_change_without_strips_does_not_repaint(self):
        widget = self._widget({"id-b": {}})
        assert widget.set_swarm_fold({"id-a"}) is False
        assert widget.refreshes == 0
        assert widget.set_swarm_fold({"id-a", "id-b"}) is True
        assert widget.refreshes == 1
        assert widget.set_swarm_fold({"id-a", "id-b"}) is False  # the same set: nothing
        assert widget.refreshes == 1

    def test_strip_order_follows_the_list(self):
        now = datetime.now()

        def pts(minutes, title, live=1):
            return [sh.Point(now - timedelta(minutes=minutes), (live, 0, 0, 0, 0, 0, 0), "", title)]

        groups = {"*": pts(50, ""), "sa": pts(50, "agents"), "tm": pts(50, "team"),
                  "wf/b": pts(10, "second"), "wf/a": pts(30, "first"),
                  "wf/dead": pts(5, "idle", live=0)}
        assert [t for _, t, _ in swarm_strips("p", {"p": groups}, True)] == [
            "first", "second", "team", "agents"]
        assert [t for _, t, _ in swarm_strips("p", {"p": groups}, False)] == ["swarm"]
        assert swarm_strips("q", {"p": groups}, False) == []
