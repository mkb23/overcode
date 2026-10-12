"""The row clock redraws only what ticks (quality review item 19).

Each row keeps its cells and, on the 250 ms clock, redraws only the cells
of ``ticks`` columns. These tests hold the cache to the uncached render
(``SessionSummary._render_row``) through a scripted run of everything that
can change a row, check that every column reading the clock is marked
``ticks``, and count the work a quiet fleet costs.

A real ``EngineServer`` publishes to a real TUI (Textual pilot), as in
test_engine_view_tui; the clock the rows read is frozen and moved by hand.
"""

import shutil
import tempfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from overcode.engine_protocol import Snapshot
from overcode.engine_socket import EngineServer
from overcode.summary_columns import COLUMNS_BY_ID

pytestmark = pytest.mark.unit

AGENTS = ("alpha", "bravo", "charlie", "delta")
T0 = datetime(2026, 10, 7, 12, 0, 0).timestamp()


def _sessions():
    from overcode.session_manager import Session

    out = []
    for i, n in enumerate(AGENTS):
        out.append(Session(
            id=f"id-{n}", name=n, tmux_session="test", tmux_window=f"w{i}",
            command=["claude"], start_directory="/tmp",
            start_time="2026-10-07T10:00:00", repo_name=f"repo-{n}", branch="main",
            # alpha: a heartbeat due (its "@HH:MM" moves with the clock);
            # bravo: an oversight countdown; delta: asleep
            heartbeat_enabled=n == "alpha", heartbeat_frequency_seconds=300,
            last_heartbeat_time="2026-10-07T11:58:00" if n == "alpha" else None,
            oversight_deadline="2026-10-07T12:30:00" if n == "bravo" else None,
            oversight_policy="timeout" if n == "bravo" else "wait",
            oversight_timeout_seconds=1800.0 if n == "bravo" else 0.0,
            is_asleep=n == "delta",
        ))
    return out


def _view(name, **fields):
    view = dict(
        session_id=f"id-{name}", name=name, current_status="running",
        current_activity=f"{name} working", status_since="2026-10-07T11:59:30",
        live_colour="green", episode_colour="green", episode_start=T0 - 30,
        live_since=T0 - 30, stats_available=True, input_tokens=1000,
        output_tokens=500, interaction_count=3, estimated_cost_usd=0.5,
        backend="claude-code", time_base=[100.0, 50.0, 20.0], time_base_at=T0 - 60,
    )
    if name == "bravo":
        view.update(current_status="waiting_oversight", live_colour="red",
                    episode_colour="red")
    if name == "charlie":
        view.update(current_status="busy_sleeping", current_activity="sleep 900",
                    status_detail={"color": "yellow", "legacy_status": "busy_sleeping",
                                   "badges": [{"kind": "schedule_wakeup",
                                               "eta_at": T0 + 7200}]})
    if name == "delta":
        view.update(current_status="asleep", live_colour="grey", episode_colour="grey")
    view.update(fields)
    return view


def _snapshot(**per_agent):
    agents = {}
    for name in AGENTS:
        view = _view(name, **per_agent.get(name, {}))
        agents[view["session_id"]] = view
    return Snapshot(agents=agents, fleet={"status": "active", "current_interval": 2,
                                          "interval_mode": "attended"})


class _Clock:
    """The time the rows read (datetime.now in the column code, time.time
    for badge ETAs), frozen at ``t`` until moved."""

    def __init__(self):
        self.t = T0

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def clock():
    c = _Clock()

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(c.t, tz)

    with patch("overcode.summary_columns.datetime", Frozen), \
            patch("overcode.tui_helpers.datetime", Frozen), \
            patch("overcode.tui_widgets.session_summary.time",
                  SimpleNamespace(time=lambda: c.t)):
        yield c


@pytest.fixture
def state_dir(monkeypatch):
    """A short state dir: Unix socket paths are limited to ~104 bytes on macOS."""
    import os

    for k in list(os.environ):
        if k.startswith("OVERCODE_"):
            monkeypatch.delenv(k)
    d = Path(tempfile.mkdtemp(prefix="oc-rc-", dir="/tmp"))
    monkeypatch.setenv("OVERCODE_DIR", str(d))
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(d / "s"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def engine(state_dir):
    from overcode import tui_engine

    server = EngineServer(tui_engine.engine_socket_path("test"))
    server.start()
    yield server
    server.stop()


@pytest.fixture
def app(state_dir, clock):
    from overcode.status_detector_factory import StatusDetectorDispatcher
    from overcode.tui import SupervisorTUI

    sessions = _sessions()
    with patch.object(SupervisorTUI, "_ensure_monitor_daemon", MagicMock()), \
            patch.object(StatusDetectorDispatcher, "detect_status", MagicMock()), \
            patch("overcode.launcher.AgentLauncher.list_sessions", lambda self: sessions):
        tui = SupervisorTUI(tmux_session="test")
        tui._notifier = MagicMock()
        yield tui


async def _until(pilot, predicate, timeout=5.0):
    for _ in range(int(timeout / 0.05)):
        if predicate():
            return True
        await pilot.pause(0.05)
    return predicate()


def _rows(app):
    from overcode.tui_widgets import SessionSummary

    return list(app.query(SessionSummary))


def _row(app, name):
    return next(w for w in _rows(app) if w.session.name == name)


async def _connected(pilot, app):
    assert await _until(pilot, lambda: app._engine_connected
                        and len(app._engine_agents) == len(AGENTS))
    await pilot.pause(0.1)


async def _settle_and_check(pilot, app, step: str) -> None:
    """Tick every row as the clock does, let Textual draw, then hold each
    row's cached line, and what is on screen, to a render from scratch."""
    await pilot.pause()
    for row in _rows(app):
        row.refresh_if_changed()
    await pilot.pause()
    for row in _rows(app):
        fresh = row._render_row()
        cached = row._row_text()
        assert cached == fresh, (
            f"after {step}: {row.session.name}\n cached {cached.plain!r}\n fresh  {fresh.plain!r}")
        assert row._last_rendered == fresh, (
            f"after {step}: {row.session.name} on screen\n"
            f" drawn {row._last_rendered.plain!r}\n fresh {fresh.plain!r}")


def _set_level(app, level: str) -> None:
    app._set_summary_level(level)


@pytest.mark.asyncio
class TestCachedRowEqualsAFreshRender:

    async def test_a_scripted_run(self, app, engine, clock):
        engine.publish(_snapshot())
        async with app.run_test(size=(240, 40)) as pilot:
            await _connected(pilot, app)
            await _settle_and_check(pilot, app, "connect")

            async def step(name, action):
                result = action()
                if hasattr(result, "__await__"):
                    await result
                await _settle_and_check(pilot, app, name)

            async def publish(snapshot, done):
                engine.publish(snapshot)
                assert await _until(pilot, done)

            for level in ("low", "med", "high", "full"):
                await step(f"detail {level}", lambda lv=level: _set_level(app, lv))
                await step(f"{level}: 1s later", lambda: clock.advance(1))
                await step(f"{level}: a minute later", lambda: clock.advance(61))

            # Engine deltas: a status change with a badge, a wider cell
            # (every row realigns), a fleet flag flipping (PR column)
            await step("status delta", lambda: publish(
                _snapshot(alpha=dict(current_status="waiting_user", live_colour="red",
                                     episode_colour="red", status_since="2026-10-07T12:01:00")),
                lambda: _row(app, "alpha").detected_status == "waiting_user"))
            await step("wider git cell", lambda: publish(
                _snapshot(alpha=dict(current_status="waiting_user", live_colour="red",
                                     episode_colour="red", status_since="2026-10-07T12:01:00",
                                     git_diff=[123, 99999, 9999])),
                lambda: _row(app, "alpha").git_diff_stats == (123, 99999, 9999)))
            await step("a PR appears", lambda: publish(
                _snapshot(alpha=dict(current_status="waiting_user", live_colour="red",
                                     episode_colour="red", status_since="2026-10-07T12:01:00",
                                     git_diff=[123, 99999, 9999]),
                          bravo=dict(pr_number=512)),
                lambda: _row(app, "bravo").pr_number == 512))
            await step("10s later", lambda: clock.advance(10))

            # Focus and selection move
            await step("focus next", lambda: pilot.press("j"))
            await step("focus next again", lambda: pilot.press("j"))
            await step("focus back", lambda: pilot.press("k"))

            # The focused agent's pane (its pane-derived counts)
            def pane():
                sid = app._selected_session_id()
                app._apply_focused_capture(sid, "working\n  ⎿  Running 2 background tasks\n", {})
            await step("pane content", pane)

            # A session swapped for a changed copy (the session-table re-read)
            def swap():
                bravo = _row(app, "bravo").session
                app._apply_focused_capture(None, None, {bravo.id: replace(
                    bravo, human_annotation="look here", standing_instructions="Be brief")})
            await step("session swap", swap)

            # Changed in place, then refresh() (as the actions and the column
            # dialog do): a session, and the overrides dict the rows share
            def in_place():
                row = _row(app, "charlie")
                row.session.heartbeat_enabled = True
                row.column_overrides["model"] = not row.column_visible(
                    COLUMNS_BY_ID["model"])
                for each in _rows(app):
                    each.refresh()
            await step("in-place change + refresh", in_place)

            # Width, display modes, columns
            await step("narrower", lambda: pilot.resize_terminal(150, 40))
            await step("wider", lambda: pilot.resize_terminal(260, 40))
            await step("emoji-free on", app.action_toggle_emoji_free)
            await step("content mode", app.action_cycle_summary_content)
            await step("theme", app.action_toggle_theme)
            await step("monochrome", app.action_toggle_monochrome)
            await step("column override", lambda: app._apply_column_config(
                "full", {"repo_name": False, "cost": True}))
            await step("cost display", app.action_toggle_cost_display)
            await step("detail low", lambda: _set_level(app, "low"))
            await step("an hour later", lambda: clock.advance(3600))
            await step("emoji-free off", app.action_toggle_emoji_free)
            await step("a day later", lambda: clock.advance(86400))


@pytest.mark.asyncio
class TestTicksMarksEveryClockColumn:

    async def test_unmarked_columns_draw_the_same_an_hour_later(self, app, engine, clock):
        """A column not marked ``ticks`` must not read the clock: its cell is
        only rebuilt when the row's inputs change, so it would freeze."""
        from overcode.summary_columns import SUMMARY_COLUMNS, cell_segments

        engine.publish(_snapshot())
        async with app.run_test(size=(240, 40)) as pilot:
            await _connected(pilot, app)
            _set_level(app, "full")
            await pilot.pause()
            moved = set()
            for row in _rows(app):
                clock.t = T0
                before = row._build_column_context()
                cells = {c.id: cell_segments(c, before) for c in SUMMARY_COLUMNS}
                clock.advance(3725)
                after = row._build_column_context()
                for col in SUMMARY_COLUMNS:
                    now = cell_segments(col, after)
                    if now != cells[col.id]:
                        assert col.ticks, f"{col.id} reads the clock: mark it ticks=True"
                        moved.add(col.id)
            # The fleet above exercises every ticking column the TUI shows
            ticking = {c.id for c in SUMMARY_COLUMNS if c.ticks and not c.cli_only}
            assert moved == ticking


@pytest.mark.asyncio
class TestAQuietFleetCostsLittle:

    async def test_ticks_rebuild_no_row_and_repaint_only_moved_cells(self, app, engine, clock):
        engine.publish(_snapshot())
        async with app.run_test(size=(240, 40)) as pilot:
            await _connected(pilot, app)
            _set_level(app, "full")
            await pilot.pause()
            rows = _rows(app)
            for row in rows:
                row.refresh_if_changed()
            await pilot.pause()
            builds = sum(r._full_builds for r in rows)
            repaints = 0
            ticks = 40  # 10 s of the 250 ms clock
            for _ in range(ticks):
                clock.advance(0.25)
                for row in rows:
                    repaints += row.refresh_if_changed()
                await pilot.pause()  # Textual draws what changed
            # Only the ticking cells were redrawn; no row was built again
            assert sum(r._full_builds for r in rows) == builds
            # A row repaints when a duration it shows moves: here the
            # seconds of time-in-state, so about once a second, not every tick
            assert len(rows) * 10 <= repaints <= len(rows) * 15
            for row in rows:
                assert row._row_text() == row._render_row()

    async def test_visible_columns_are_worked_out_once_per_change(self, app, engine, clock):
        from overcode import summary_columns

        engine.publish(_snapshot())
        async with app.run_test(size=(240, 40)) as pilot:
            await _connected(pilot, app)
            await pilot.pause()
            calls = []
            original = summary_columns.resolve_column_visible
            with patch("overcode.tui_widgets.session_summary.resolve_column_visible",
                       lambda *a, **k: (calls.append(1), original(*a, **k))[1]):
                for _ in range(8):
                    clock.advance(0.25)
                    for row in _rows(app):
                        row.refresh_if_changed()
                assert calls == []
                _set_level(app, "high")
                for row in _rows(app):
                    row.refresh_if_changed()
                # Once per row per column for the new level, and no more
                n = len(summary_columns.SUMMARY_COLUMNS) * len(AGENTS)
                assert len(calls) == n
