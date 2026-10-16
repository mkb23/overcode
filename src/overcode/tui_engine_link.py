"""The TUI's link to the engine (docs/design/engine-0.6.md).

The TUI subscribes to engine.sock and renders what the engine publishes.
``EngineLink`` owns that subscription: the ``EngineClient``, the last
snapshot's agents and fleet, the connection and stall state, and the
"engine not running" / "engine frozen" banner. It is composed into
``SupervisorTUI`` as ``app.engine_link``; the app routes the client's
messages (below) to it and asks it to report focus, visits, the burn
window and visibility.

The client's callbacks run on its own thread. They post a typed message to
the app rather than call_from_thread, which would block the client on the
main thread (and deadlock an exit that joins the client). Snapshots
coalesce: a burst of deltas is applied once, as the latest.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

from textual.containers import ScrollableContainer
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import Static

from . import tui_engine
from .engine_socket import EngineClient
from .tui_helpers import format_duration
from .tui_widgets import DaemonStatusBar, SessionSummary

if TYPE_CHECKING:  # pragma: no cover
    from .tui import SupervisorTUI

# A dropped or absent engine.sock is shown after this long (a normal start
# connects well within it), and the daemon is (re)started at most this often
# while the engine stays away.
ENGINE_BANNER_GRACE_SECONDS = 1.5
ENGINE_RESTART_EVERY_SECONDS = 10.0
ENGINE_RETRY_AFTER_DROP_SECONDS = 1.5
ENGINE_BANNER_TEXT = "  ⚠ engine not running — starting…  (showing the last known state)  "
ENGINE_STALLED_TEXT = "  ⚠ engine frozen for {age} — statuses are not updating  (showing the last known state)  "


class EngineSnapshotReady(Message):
    """The client holds a new snapshot (the latest of a burst); take it on the main thread."""


class EngineBellRang(Message):
    """The engine rang the bell for ``agent``: an input-needed episode began."""

    def __init__(self, agent: str, episode: dict) -> None:
        super().__init__()
        self.agent = agent
        self.episode = episode


class EngineConnectionChanged(Message):
    """The subscription came up (``connected``) or went away."""

    def __init__(self, connected: bool) -> None:
        super().__init__()
        self.connected = connected


class EngineLink:
    """The app's engine subscription and what it last heard."""

    def __init__(self, app: "SupervisorTUI") -> None:
        self.app = app
        self.client: Optional[EngineClient] = None
        self.connected: bool = False
        self.agents: dict = {}  # the last snapshot's agents
        self.fleet: dict = {}
        # The expanded parents' virtual rows (agent-observability §6.2)
        self.virtual: dict = {}
        self.down_since: float = time.monotonic()
        self.stalled = False
        self.start_attempt: float = float("-inf")
        self.focus: Optional[str] = None  # the local agent the engine is told has focus
        self._pending = None  # the latest snapshot, not yet applied
        self._apply_posted = False
        # What the status bar was last built from (apply_to_status_bar)
        self._status_bar_inputs: Optional[tuple] = None

    # ── The subscription ──────────────────────────────────────────────

    def start(self) -> None:
        """Subscribe to engine.sock; the client reconnects by itself for good."""
        if self.client is not None:
            return
        app = self.app
        client = EngineClient(
            tui_engine.engine_socket_path(app.tmux_session),
            on_change=self._on_change,
            on_bell=self._on_bell,
            on_connection=self._on_connection,
        )
        self.client = client
        # Stated now, restated by the client on every (re)connect
        client.set_visible(app.attended)
        client.set_burn_window(app._burn_hours() or None)
        self.report_focus(app._get_focused_widget())
        client.set_focus(self.focus)
        client.set_swarm(set(getattr(app, "swarm_expanded", ())))
        client.start()

    def stop(self) -> None:
        if self.client is not None:
            self.client.stop()

    def stalled_for(self) -> Optional[float]:
        """Seconds the connected engine has been frozen (None: live, or not connected)."""
        if not self.connected or self.client is None:
            return None
        return self.client.stalled_for()

    def set_visible(self, visible: bool) -> None:
        """Tell the engine whether anyone is looking (the attended state)."""
        if self.client is not None:
            self.client.set_visible(visible)

    def visit(self, session_id: str) -> None:
        if self.client is not None:
            self.client.visit(session_id)

    def report_focus(self, widget: Optional[SessionSummary]) -> None:
        """Tell the engine which local agent has focus; hand back the old row's pane."""
        sid = widget.session.id if widget is not None and not widget.session.is_remote else None
        if sid == self.focus:
            return
        for w in self.app.query(SessionSummary):
            if w.local_pane and w.session.id != sid:
                w.end_local_pane()
        self.focus = sid
        if self.client is not None:
            self.client.set_focus(sid)

    def report_burn_window(self) -> None:
        """Ask the engine for the current window's burn; re-read what it has."""
        if self.client is not None:
            self.client.set_burn_window(self.app._burn_hours() or None)
        self.apply_views(force=True)

    # The client's callbacks (its thread): post, never block

    def _on_change(self, snapshot) -> None:
        self._pending = snapshot
        if not self._apply_posted:
            self._apply_posted = True
            self.app.post_message(EngineSnapshotReady())

    def _on_bell(self, agent: str, episode: dict) -> None:
        self.app.post_message(EngineBellRang(agent, episode))

    def _on_connection(self, connected: bool) -> None:
        self.app.post_message(EngineConnectionChanged(connected))

    # ── Main thread: applying what the engine said ────────────────────

    def take_snapshot(self) -> None:
        """Main thread: apply the pending snapshot, if one is still pending."""
        self._apply_posted = False
        # Take it: a snapshot landing after the flag reset posts its own
        # message, which must not find this one again and apply it twice
        snapshot, self._pending = self._pending, None
        if snapshot is not None:
            self.apply_snapshot(snapshot)

    def apply_snapshot(self, snapshot) -> None:
        """Main thread: the engine's current snapshot (a full one or a delta applied)."""
        app = self.app
        app._mark_event("apply_engine_start")
        if not self.connected:
            # A snapshot means a live connection, whatever order callbacks land in
            self.set_connected(True)
        self.agents = snapshot.agents
        self.fleet = snapshot.fleet
        self.apply_views()
        virtual = getattr(snapshot, "virtual", None) or {}
        if virtual is not self.virtual and virtual != self.virtual:
            self.virtual = virtual
            if getattr(app, "swarm_expanded", None) or getattr(app, "_swarm_lines", None):
                app._sync_swarm_rows()
        else:
            self.virtual = virtual
        app._mark_event("apply_engine_end")

    def apply_views(self, force: bool = False) -> None:
        """Give each local row its agent's view; repaint only the rows that changed.

        ``force`` re-applies unchanged views too (the burn window moved).
        Fleet-wide flags that decide whether a column shows are recomputed;
        when one flips, or the aligned column widths move, every row
        repaints, since every row's layout changed.
        """
        app = self.app
        agents = self.agents
        hours = app._burn_hours()
        changed = []
        for widget in app.query(SessionSummary):
            if widget.session.is_remote:
                continue
            view = agents.get(widget.session.id)
            # `is` first: an agent the delta didn't touch keeps its dict
            if view is None or ((view is widget.engine or view == widget.engine) and not force):
                continue
            old_status = widget.detected_status
            if widget.apply_engine(view, hours, app._visited_here.get(widget.session.id)):
                changed.append(widget)
                if app._prefs.status_change_logging and old_status != widget.detected_status:
                    app._log_status_change(
                        widget.session.name, old_status, widget.detected_status,
                        source="engine", focused=widget.session.id == self.focus,
                    )
        self.apply_to_status_bar()
        if not changed:
            return
        # The agent on screen (the split's bottom pane) that needs a look:
        # seen after a few seconds, as when it rings while focused
        focused_id = app._selected_session_id()
        for widget in changed:
            sid = widget.session.id
            if widget.is_unvisited_stalled and sid == focused_id and app.attended:
                app._schedule_bell_dismiss(sid)
        if app._update_fleet_flags() | app._update_uniform_columns(changed):
            changed = list(app.query(SessionSummary))
        if not app.attended:
            return  # nothing is drawn while detached; re-attaching repaints every row
        app._repaint(changed)

    def apply_to_status_bar(self) -> None:
        """The daemon status bar's fleet fields and the aggregate burn, from the snapshot.

        Rebuilt only when what it reads moved (the fleet fields, an agent's
        bar fields or burn, which agents are local or asleep, the
        connection): most deltas touch none of that.
        """
        app = self.app
        try:
            bar = app.query_one("#daemon-status", DaemonStatusBar)
        except NoMatches:
            return
        local_ids = {s.id for s in app.sessions if not s.is_remote}
        asleep = {s.id for s in app.sessions if s.is_asleep}
        agents = {sid: v for sid, v in self.agents.items()
                  if not local_ids or sid in local_ids}
        hours = app._burn_hours()
        inputs = (tui_engine.status_bar_inputs(self.fleet, agents, hours),
                  asleep, self.connected)
        if inputs == self._status_bar_inputs:
            return
        self._status_bar_inputs = inputs
        bar.monitor_state = tui_engine.monitor_state(self.fleet, agents)
        bar.presence_idle_since = self.fleet.get("presence_idle_since")
        bar.engine_connected = self.connected
        bar._asleep_session_ids = asleep
        bar._burn_stats = tui_engine.fleet_burn(agents, hours)
        if app.attended:
            bar.refresh_if_changed()

    # ── Connection, banner and stall ──────────────────────────────────

    def set_connected(self, connected: bool) -> None:
        """Main thread: the subscription came up or went away."""
        if connected == self.connected:
            return
        if self.stalled:
            self.show_stalled(None)  # back to the not-running banner text
        self.connected = connected
        if connected:
            self.show_banner(False)
        else:
            self.down_since = time.monotonic()
            # Show the banner at once: the engine was there and went away
            self.show_banner(True)
            self.start_engine()
            # A daemon that is still shutting down holds its lock, so that
            # start was a no-op: try again shortly, then every 10 s
            self.start_attempt = (
                time.monotonic() - ENGINE_RESTART_EVERY_SECONDS + ENGINE_RETRY_AFTER_DROP_SECONDS
            )
        try:
            bar = self.app.query_one("#daemon-status", DaemonStatusBar)
            bar.engine_connected = connected
            bar.refresh()
        except NoMatches:
            pass

    def watch(self) -> None:
        """Each second: a missing or frozen engine is shown; a missing one is started again now and then."""
        if self.connected:
            client = self.client
            self.show_stalled(client.stalled_for() if client is not None else None)
            return
        now = time.monotonic()
        if now - self.down_since >= ENGINE_BANNER_GRACE_SECONDS:
            self.show_banner(True)
        if now - self.start_attempt >= ENGINE_RESTART_EVERY_SECONDS:
            self.start_engine()

    def start_engine(self) -> None:
        """Start the monitor daemon (the engine) unless it is already starting."""
        self.start_attempt = time.monotonic()
        self.app._ensure_monitor_daemon()

    def show_stalled(self, age: Optional[float]) -> None:
        """Connected but frozen (``age`` s, None: live): the banner says so and the list dims."""
        stalled = age is not None
        if not stalled and not self.stalled:
            return
        self.stalled = stalled
        app = self.app
        try:
            banner = app.query_one("#engine-banner", Static)
        except NoMatches:
            return
        if stalled:
            banner.update(ENGINE_STALLED_TEXT.format(age=format_duration(age)))
        else:
            banner.update(ENGINE_BANNER_TEXT)
        self.show_banner(stalled)
        try:
            bar = app.query_one("#daemon-status", DaemonStatusBar)
        except NoMatches:
            return
        bar.engine_stalled_for = age
        bar.refresh()

    def show_banner(self, show: bool) -> None:
        """The "engine not running" banner; the agent list dims while it shows."""
        app = self.app
        try:
            banner = app.query_one("#engine-banner", Static)
            container = app.query_one("#sessions-container", ScrollableContainer)
        except NoMatches:
            return
        if show:
            banner.add_class("visible")
            container.add_class("engine-stale")
        else:
            banner.remove_class("visible")
            container.remove_class("engine-stale")
