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
    from overcode.claude_bg import is_background

    app = pilot.app
    await pilot.pause()
    # The timeline has a row per agent; a 🌙 background session has none
    rows = len([w for w in app._get_widgets_in_session_order()
                if not is_background(w.session)])

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
