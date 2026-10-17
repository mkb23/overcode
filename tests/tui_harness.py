"""One pilot harness for SupervisorTUI tests (quality review item 36).

``make_tui`` (a fixture in tests/unit/conftest.py) builds the app the way
every pilot test needs it:

- its state in a short private dir (Unix socket paths are capped at ~104
  bytes on macOS, so a real ``EngineServer`` fits there too);
- no monitor daemon started, and status never detected (the engine does that);
- the agents a test names, or whatever the private sessions.json holds;
- no periodic timer (``timers=False``): the test calls the tick it is about,
  e.g. ``app._focused_pane_tick()``, so nothing moves under it;
- connected from mount to an in-memory engine (``FakeEngine``) that hands
  snapshots to the app synchronously, so the "engine not running" banner
  never appears and never shifts the rows. ``engine="socket"`` keeps the
  real ``EngineClient`` for tests of the socket path (run an
  ``EngineServer`` on ``tui_engine.engine_socket_path(session)``).

The helpers below (``three_agents``, ``ready``, ``until``, ``palette``,
``CountedWork``) are what the pilot tests share.
"""

from __future__ import annotations

from collections import Counter
from typing import Callable, Optional

from overcode.engine_protocol import Snapshot


class FakeEngine:
    """The engine, in memory: what it publishes reaches the app at once.

    Mirrors the parts of ``EngineServer`` tests use (``publish``, ``ring``,
    ``attended``, ``focused_agents``, ``burn_windows``, ``swarm_subscriptions``,
    ``take_visits``),
    so a test reads the same against either.
    """

    def __init__(self, snapshot: Optional[Snapshot] = None) -> None:
        self.snapshot = snapshot if snapshot is not None else Snapshot()
        self.client: Optional[FakeEngineClient] = None
        self.stalled: Optional[float] = None  # what client.stalled_for() answers
        self._visits: list = []

    # What the TUI builds in place of EngineClient
    def client_factory(self, path, on_change, on_bell=None, on_connection=None):
        self.client = FakeEngineClient(self, on_change, on_bell, on_connection)
        return self.client

    @property
    def app(self):
        return self.client.app if self.client is not None else None

    def publish(self, snapshot: Snapshot) -> None:
        """The engine's new state; applied now if the app is connected.

        Agents whose fields did not change keep their dict, as the real
        client's copy-on-write ViewState does.
        """
        old = self.snapshot.agents
        agents = {aid: (old[aid] if aid in old and old[aid] == view else view)
                  for aid, view in snapshot.agents.items()}
        virtual = snapshot.virtual
        if virtual == self.snapshot.virtual:
            virtual = self.snapshot.virtual  # unchanged: the same dict, as a delta leaves it
        self.snapshot = Snapshot(seq=self.snapshot.seq + 1, agents=agents,
                                 fleet=dict(snapshot.fleet), virtual=virtual)
        if self.client is not None and self.client.connected:
            self.client.link.apply_snapshot(self.snapshot)

    def ring(self, agent: str, episode: dict) -> None:
        self.client.app._ring(agent, episode)

    def drop(self) -> None:
        """The engine went away."""
        self.client.connected = False
        self.client.link.set_connected(False)

    def come_back(self) -> None:
        self.client._connect()

    # What the app told the engine
    @property
    def attended(self) -> bool:
        return bool(self.client and self.client.visible)

    @property
    def focused_agents(self) -> set:
        return {self.client.focus} if self.client and self.client.focus else set()

    @property
    def burn_windows(self) -> set:
        return {self.client.burn_hours} if self.client and self.client.burn_hours else set()

    @property
    def swarm_subscriptions(self) -> frozenset:
        return frozenset(self.client.swarm) if self.client and self.client.visible else frozenset()

    def take_visits(self) -> list:
        visits, self._visits = self._visits, []
        return visits


class FakeEngineClient:
    """EngineClient's interface; connects on start(), on the main thread."""

    def __init__(self, engine: FakeEngine, on_change, on_bell, on_connection) -> None:
        self.engine = engine
        # The callbacks are the app's EngineLink's bound methods: the client
        # delivers straight to the link instead of posting messages for later
        self.link = on_change.__self__
        self.app = self.link.app
        self.connected = False
        self.visible = False
        self.focus: Optional[str] = None
        self.burn_hours: Optional[float] = None
        self.swarm: set = set()  # parents subscribed to (swarm:<id>)
        self.stopped = False

    @property
    def state(self):
        from types import SimpleNamespace
        return SimpleNamespace(snapshot=self.engine.snapshot)

    def _connect(self) -> None:
        self.connected = True
        self.link.set_connected(True)
        self.link.apply_snapshot(self.engine.snapshot)

    def start(self) -> None:
        self._connect()

    def stop(self) -> None:
        self.stopped = True
        self.connected = False

    def stalled_for(self) -> Optional[float]:
        return self.engine.stalled if self.connected else None

    def set_visible(self, visible: bool) -> None:
        self.visible = visible

    def set_burn_window(self, hours: Optional[float]) -> None:
        self.burn_hours = hours

    def set_focus(self, agent: Optional[str]) -> None:
        self.focus = agent

    def set_swarm(self, agents) -> None:
        self.swarm = set(agents)

    def visit(self, agent: str) -> None:
        import time
        self.engine._visits.append((agent, time.time()))


def three_agents():
    """alpha, bravo, charlie: three local agents with repos, branches and tags."""
    from overcode.session_manager import Session

    specs = [("alpha", "repo-a", "main", ["ui"]), ("bravo", "repo-b", "main", []),
             ("charlie", "repo-c", "feat/x", ["ui", "api"])]
    return [Session(id=f"id-{n}", name=n, tmux_session="test", tmux_window=f"w{i}",
                    command=["claude"], start_directory="/tmp",
                    start_time="2026-09-24T10:00:00", repo_name=r, branch=b, tags=t)
            for i, (n, r, b, t) in enumerate(specs)]


async def until(pilot, predicate: Callable[[], object], timeout: float = 5.0) -> bool:
    """Pause until ``predicate()`` holds (True) or ``timeout`` passes (its last answer)."""
    for _ in range(int(timeout / 0.02)):
        if predicate():
            return True
        await pilot.pause(0.02)
    return bool(predicate())


async def ready(pilot) -> None:
    """The agents are on screen and the layout has settled.

    The timeline's history arrives from a worker after mount and grows the
    timeline, which moves every agent row down; wait for it to hold the
    agents before measuring or clicking a row.
    """
    app = pilot.app
    await pilot.pause()
    # The timeline has a row per agent, 🌙 background sessions included
    rows = len(app._get_widgets_in_session_order())

    def settled():
        timeline = app.query_one("#timeline")
        return not timeline.display or len(timeline.sessions) == rows

    await until(pilot, settled)
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


def palette(app):
    from overcode.tui_widgets import CommandPalette
    return app.query_one("#command-palette", CommandPalette)


class CountedWork:
    """Counts of the TUI's real work, by kind (and by row name for row work).

    Wraps the real methods (quality review item 37: count work, not time):
    ``SessionSummary.apply_engine`` (a row takes its view), ``_full_builds``
    (its cells built from scratch), ``_compose_line`` (its line padded
    again), ``Widget.refresh`` on a row or the status bar (``"bar"``),
    ``app._repaint`` and ``tui_engine.monitor_state`` (the bar rebuilt).
    ``total()`` is all of it; ``NO_WORK`` is a quiet stretch's total.
    """

    def __init__(self, app, monkeypatch):
        from textual.widget import Widget

        from overcode import tui_engine
        from overcode.tui_widgets import DaemonStatusBar, SessionSummary

        self.app = app
        self.applies = Counter()
        self.refreshes = Counter()  # row name, or "bar"
        self.composes = Counter()
        self.repaints = []  # the rows each _repaint was asked for
        self.bar_builds = 0
        work = self

        real_apply = SessionSummary.apply_engine

        def apply_engine(row, *a, **kw):
            work.applies[row.session.name] += 1
            return real_apply(row, *a, **kw)

        real_compose = SessionSummary._compose_line

        def compose_line(row, *a, **kw):
            work.composes[row.session.name] += 1
            return real_compose(row, *a, **kw)

        real_refresh = Widget.refresh

        def refresh(widget, *a, **kw):
            if isinstance(widget, SessionSummary):
                work.refreshes[widget.session.name] += 1
            elif isinstance(widget, DaemonStatusBar):
                work.refreshes["bar"] += 1
            return real_refresh(widget, *a, **kw)

        real_monitor_state = tui_engine.monitor_state

        def monitor_state(*a, **kw):
            work.bar_builds += 1
            return real_monitor_state(*a, **kw)

        real_repaint = app._repaint

        def repaint(changed):
            work.repaints.append(sorted(w.session.name for w in changed))
            return real_repaint(changed)

        monkeypatch.setattr(SessionSummary, "apply_engine", apply_engine)
        monkeypatch.setattr(SessionSummary, "_compose_line", compose_line)
        monkeypatch.setattr(Widget, "refresh", refresh)
        monkeypatch.setattr(tui_engine, "monitor_state", monitor_state)
        monkeypatch.setattr(app, "_repaint", repaint)
        self.reset()

    def full_builds(self) -> Counter:
        from overcode.tui_widgets import SessionSummary

        return Counter({w.session.name: w._full_builds - self._builds0.get(w.session.name, 0)
                        for w in self.app.query(SessionSummary)}) + Counter()

    def reset(self) -> None:
        from overcode.tui_widgets import SessionSummary

        self.applies.clear()
        self.refreshes.clear()
        self.composes.clear()
        self.repaints.clear()
        self.bar_builds = 0
        self._builds0 = {w.session.name: w._full_builds for w in self.app.query(SessionSummary)}

    def total(self) -> dict:
        return {"applies": dict(self.applies), "refreshes": dict(self.refreshes),
                "composes": dict(self.composes), "repaints": list(self.repaints),
                "bar_builds": self.bar_builds, "full_builds": dict(self.full_builds())}


NO_WORK = {"applies": {}, "refreshes": {}, "composes": {}, "repaints": [],
           "bar_builds": 0, "full_builds": {}}


# ---------------------------------------------------------------------------
# Layout invariants
# ---------------------------------------------------------------------------

def _cells_of(plain: str) -> list:
    """``plain`` as one entry per screen cell: a wide character is itself
    then "" (its second cell)."""
    from rich.cells import cell_len

    out = []
    for ch in plain:
        out.append(ch)
        out.extend([""] * (cell_len(ch) - 1))
    return out


def _crop(plain: str, start: int, width: int) -> str:
    """The cells ``start`` to ``start + width`` of ``plain``; a wide
    character cut by either edge shows as a space, as on screen."""
    cells = _cells_of(plain)[start:start + width]
    if cells and cells[0] == "":
        cells[0] = " "
    if cells and start + len(cells) < len(_cells_of(plain)) and cells[-1] != "" \
            and _cells_of(plain)[start + len(cells)] == "":
        cells[-1] = " "
    return "".join(cells)


def _at(plain: str, x: int) -> str:
    """``plain`` from cell offset ``x`` on."""
    return "".join(_cells_of(plain)[x:]) if x < len(_cells_of(plain)) else ""


def _on_screen(widget) -> list:
    """The widget's lines as painted, inside its padding."""
    from textual.geometry import Region

    size = widget.outer_size
    left = widget.styles.padding.left
    strips = widget.render_lines(Region(0, 0, size.width, size.height))
    return [_crop("".join(seg.text for seg in strip), left, widget.content_size.width)
            for strip in strips]


def _check_painted(widget, lines: list, what: str) -> None:
    """What is on screen is each line cropped at the content edge: nothing
    wrapped onto a hidden second row, and no word-wrap dropped the end of
    a line that fitted."""
    width = widget.content_size.width
    painted = _on_screen(widget)
    assert len(painted) >= len(lines), f"{what}: {len(painted)} rows painted for {len(lines)} lines"
    for i, line in enumerate(lines):
        want = _crop(line, 0, width)
        assert painted[i].rstrip() == want.rstrip(), (
            f"{what} line {i}: painted\n  {painted[i]!r}\nexpected\n  {want!r}")


def _check_cells(line: str, cells: list, widths: list, width: int, what: str) -> None:
    """Each non-empty cell starts at its column's x (the sum of the widths
    before it), up to the edge of the row."""
    x = 0
    for j, cell in enumerate(cells):
        if cell.strip() and x < width:
            want = _crop(cell, 0, width - x)
            got = _crop(_at(line, x), 0, cell_len_(want))
            assert got == want, f"{what} column {j} at x={x}: {got!r} != {want!r}"
        x += widths[j] if j < len(widths) else 0


def cell_len_(text: str) -> int:
    from rich.cells import cell_len

    return cell_len(text)


def assert_layout_invariants(app) -> None:
    """The agent table is laid out as designed, at whatever width and detail.

    - every agent row (``SessionSummary``) is exactly one screen row high,
      and every ``SwarmRows`` is exactly as high as its lines;
    - no line (agent row, virtual row, column header) runs past its widget's
      content width, and what is painted is that line, cropped at the edge
      (a line that wraps either shifts every row below or, on a one-row
      widget, loses its end at a word boundary);
    - the rows stack with no gap and no overlap;
    - the header's labels, the agent rows' cells and the virtual rows' cells
      start at the same x offsets: the sums of ``app.column_widths``, and the
      summary bar " │ " is at the same x on every row that reaches it.

    The header never widens a column: a label longer than its column is cut
    to the column's width (``render_header_cells``), so a label is checked
    as cut.
    """
    from overcode.summary_columns import COLUMNS_BY_ID
    from overcode.tui_widgets import ColumnHeader, SessionSummary
    from overcode.tui_widgets.swarm_rows import SwarmRows

    agents = [w for w in app.query(SessionSummary) if w.display]
    swarms = [w for w in app.query(SwarmRows) if w.display]
    widths = list(app.column_widths)
    ids = [c.id for c in agents[0].visible_columns()] if agents else []
    assert not agents or len(widths) == len(ids), (widths, ids)

    bars = set()
    for row in agents:
        what = f"agent row {row.session.name}"
        assert row.outer_size.height == 1, f"{what}: {row.outer_size.height} rows high"
        assert [c.id for c in row.visible_columns()] == ids, f"{what}: other columns"
        width = row.content_size.width
        line = row.render().plain
        assert "\n" not in line, f"{what}: more than one line"
        assert cell_len_(line) <= width, f"{what}: {cell_len_(line)} cells in {width}"
        _check_painted(row, [line], what)
        _check_cells(line, [c.plain for c in row.row_cells()], widths, width, what)
        if " │ " in line:
            bar = cell_len_(line[:line.index(" │ ")])
            if bar + 3 <= width:
                bars.add(bar)

    for rows in swarms:
        what = f"virtual rows of {rows.parent_id}"
        lines = rows._shown.plain.split("\n") if rows._shown is not None else []
        assert len(lines) == len(rows.lines), f"{what}: {len(lines)} painted, {len(rows.lines)} lines"
        assert rows.outer_size.height == len(rows.lines), (
            f"{what}: {rows.outer_size.height} rows high for {len(rows.lines)} lines")
        width = rows.content_size.width
        for i, line in enumerate(lines):
            assert cell_len_(line) <= width, f"{what} line {i}: {cell_len_(line)} cells in {width}"
        _check_painted(rows, lines, what)
        for i, (line, cells) in enumerate(zip(lines, rows._cells)):
            if cells is None:
                continue  # a detail line: free text from the NAME column
            assert [c.id for c in rows._columns] == ids, f"{what}: other columns"
            _check_cells(line, ["".join(t for t, _ in segs) for segs in cells], widths,
                         width, f"{what} line {i}")
            if " │ " in line:
                bar = cell_len_(line[:line.index(" │ ")])
                if bar + 3 <= width:
                    bars.add(bar)
    assert len(bars) <= 1, f"the summary bar is at different x on different rows: {sorted(bars)}"

    # Stacked: each row starts where the one above ends
    stack = sorted(agents + swarms, key=lambda w: w.virtual_region.y)
    for above, below in zip(stack, stack[1:]):
        assert below.virtual_region.y == above.virtual_region.y + above.outer_size.height, (
            f"{type(below).__name__} at y={below.virtual_region.y} under "
            f"{type(above).__name__} at y={above.virtual_region.y} h={above.outer_size.height}")

    header = app.query_one("#column-headers", ColumnHeader)
    if header.display and getattr(app, "tui_mode", "") != "jobs":
        what = "column header"
        rendered = header.render()
        assert hasattr(rendered, "plain"), f"{what}: not a Text"
        line = rendered.plain
        width = header.content_size.width
        assert header.outer_size.height == 1, f"{what}: {header.outer_size.height} rows high"
        assert "\n" not in line and cell_len_(line) <= width, (
            f"{what}: {cell_len_(line)} cells in {width}")
        _check_painted(header, [line], what)
        assert header.column_ids == ids and header.column_widths == widths, what
        labels = []
        for cid, w in zip(ids, widths):
            label = " " + COLUMNS_BY_ID[cid].header if COLUMNS_BY_ID[cid].header else ""
            if cid == header.sort_column:
                label = label.rstrip("▲▼")
            labels.append(_crop(label, 0, max(0, w - (cid == header.sort_column))))
        _check_cells(line, labels, widths, width, what)
