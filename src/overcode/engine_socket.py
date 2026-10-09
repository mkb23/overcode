"""engine.sock: the engine publishes to views over a Unix stream socket.

See docs/design/engine-0.6.md. ``EngineServer`` runs in the monitor daemon:
one background thread multiplexes every connection with ``selectors``.
``publish`` is called from the daemon's tick with the new snapshot; the
server diffs it against the last one and queues one delta for every
subscriber. Each subscriber's outbound queue is bounded, so a view that
stops reading is disconnected instead of growing the daemon's memory or
slowing its tick. It reconnects for a fresh snapshot.

``EngineClient`` runs in a view (the TUI): a background thread connects,
keeps a ``ViewState`` current, calls back on every change, sends the view's
visibility and focus, and reconnects with backoff when the engine goes away.
"""

from __future__ import annotations

import os
import selectors
import socket
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional

from . import engine_protocol as proto

# A view this far behind (bytes queued) is dropped and must resync
MAX_QUEUED_BYTES = 4 << 20
PING_SECONDS = 5.0
# The daemon's main loop beats every tick and every sleep step (at most a
# second apart). A beat this old means the loop has stopped while this
# thread still runs: views are told (each ping carries the beat's age) and
# the daemon's ``on_stall`` is called each second until it beats again.
STALL_SECONDS = 30.0
# A connected view that hears nothing (not even a ping) for this long is
# looking at an engine whose socket thread has stopped too
SILENT_SECONDS = 3 * PING_SECONDS


def socket_path(state_dir: Path) -> Path:
    return Path(state_dir) / "engine.sock"


class _Peer:
    __slots__ = ("sock", "outbox", "inbox", "visible", "focus", "burn_hours")

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.outbox = bytearray()
        self.inbox = bytearray()
        self.visible = False
        self.focus: Optional[str] = None
        self.burn_hours: Optional[float] = None


class EngineServer:
    """The engine's side: accept views, push snapshot then deltas."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._peers: Dict[int, _Peer] = {}
        self._snapshot = proto.Snapshot()
        self._selector = selectors.DefaultSelector()
        self._listener: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        # Wakes the selector when publish() queues output
        self._wake_r, self._wake_w = socket.socketpair()
        self._wake_r.setblocking(False)
        self._wake_w.setblocking(False)
        self._last_ping = time.monotonic()
        self.dropped = 0  # views disconnected for falling behind (diagnostics)
        # (agent id, epoch seconds) the person looked at, drained by the daemon
        self._visits: list = []
        self._beat = time.monotonic()
        # Called on the socket thread with the beat's age while it is stale
        self.on_stall: Optional[Callable[[float], None]] = None

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old_umask = os.umask(0o177)  # the socket is 0600: views of this user only
        try:
            listener.bind(str(self.path))
        finally:
            os.umask(old_umask)
        listener.listen(16)
        listener.setblocking(False)
        self._listener = listener
        self._selector.register(listener, selectors.EVENT_READ, "accept")
        self._selector.register(self._wake_r, selectors.EVENT_READ, "wake")
        self._thread = threading.Thread(target=self._run, name="engine-socket", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._poke()
        if self._thread is not None:
            self._thread.join(timeout=2)
        with self._lock:
            for peer in list(self._peers.values()):
                self._close(peer)
        if self._listener is not None:
            self._listener.close()
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    # -- the daemon's side ---------------------------------------------

    def publish(self, snapshot: proto.Snapshot) -> proto.Delta:
        """Make ``snapshot`` current; send each view the delta. Returns it.

        ``snapshot.seq`` is assigned here: one past the previous, and only
        when something changed, so a quiet tick costs a diff and nothing else.
        """
        with self._lock:
            candidate = snapshot.copy()
            candidate.seq = self._snapshot.seq + 1
            delta = proto.diff(self._snapshot, candidate)
            if delta.empty:
                return delta
            self._snapshot = candidate
            if self._peers:
                message = proto.delta_message(delta)
                for peer in list(self._peers.values()):
                    self._queue(peer, message)
        self._poke()
        return delta

    def beat(self) -> None:
        """The daemon's main loop is alive (called from that loop)."""
        self._beat = time.monotonic()

    @property
    def beat_age(self) -> float:
        return time.monotonic() - self._beat

    def ring(self, agent: str, episode: dict) -> None:
        with self._lock:
            message = proto.bell(self._snapshot.seq, agent, episode)
            for peer in list(self._peers.values()):
                self._queue(peer, message)
        self._poke()

    @property
    def attended(self) -> bool:
        """At least one connected view says it is being looked at."""
        with self._lock:
            return any(peer.visible for peer in self._peers.values())

    @property
    def focused_agents(self) -> set:
        with self._lock:
            return {p.focus for p in self._peers.values() if p.visible and p.focus}

    @property
    def burn_windows(self) -> set:
        """Burn windows (hours) the visible views want computed."""
        with self._lock:
            return {p.burn_hours for p in self._peers.values()
                    if p.visible and p.burn_hours}

    @property
    def view_count(self) -> int:
        with self._lock:
            return len(self._peers)

    # -- the socket thread ----------------------------------------------

    def _poke(self) -> None:
        try:
            self._wake_w.send(b"\0")
        except (BlockingIOError, OSError):
            pass

    def _queue(self, peer: _Peer, message: bytes) -> None:
        """Called with the lock held."""
        if len(peer.outbox) + len(message) > MAX_QUEUED_BYTES:
            self.dropped += 1
            self._close(peer)
            return
        peer.outbox += message

    def _close(self, peer: _Peer) -> None:
        """Called with the lock held."""
        self._peers.pop(id(peer), None)
        try:
            self._selector.unregister(peer.sock)
        except (KeyError, ValueError):
            pass
        peer.sock.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                for peer in self._peers.values():
                    events = selectors.EVENT_READ | (selectors.EVENT_WRITE if peer.outbox else 0)
                    try:
                        self._selector.modify(peer.sock, events, peer)
                    except (KeyError, ValueError):
                        pass
            for key, mask in self._selector.select(timeout=1.0):
                if key.data == "accept":
                    self._accept()
                elif key.data == "wake":
                    try:
                        while self._wake_r.recv(4096):
                            pass
                    except (BlockingIOError, OSError):
                        pass
                else:
                    self._service(key.data, mask)
            now = time.monotonic()
            beat_age = now - self._beat
            if now - self._last_ping >= PING_SECONDS:
                self._last_ping = now
                with self._lock:
                    message = proto.ping(self._snapshot.seq, beat_age)
                    for peer in list(self._peers.values()):
                        self._queue(peer, message)
            if beat_age >= STALL_SECONDS and self.on_stall is not None:
                try:
                    self.on_stall(beat_age)
                except Exception:
                    pass  # the watchdog must never take the socket thread down

    def _accept(self) -> None:
        try:
            conn, _ = self._listener.accept()
        except (BlockingIOError, OSError):
            return
        conn.setblocking(False)
        peer = _Peer(conn)
        with self._lock:
            self._peers[id(peer)] = peer
            peer.outbox += proto.hello(self._snapshot.seq)
            peer.outbox += proto.snapshot_message(self._snapshot)
            self._selector.register(conn, selectors.EVENT_READ | selectors.EVENT_WRITE, peer)

    def _service(self, peer: _Peer, mask: int) -> None:
        if mask & selectors.EVENT_READ:
            try:
                data = peer.sock.recv(65536)
            except (BlockingIOError, InterruptedError):
                data = None
            except OSError:
                data = b""
            if data == b"":
                with self._lock:
                    self._close(peer)
                return
            if data:
                peer.inbox += data
                for message in proto.decode_lines(peer.inbox):
                    self._handle(peer, message)
        if mask & selectors.EVENT_WRITE:
            with self._lock:
                if not peer.outbox:
                    return
                try:
                    sent = peer.sock.send(peer.outbox)
                except (BlockingIOError, InterruptedError):
                    return
                except OSError:
                    self._close(peer)
                    return
                del peer.outbox[:sent]

    def _handle(self, peer: _Peer, message: dict) -> None:
        kind = message.get("t")
        with self._lock:
            if kind == "visible":
                peer.visible = bool(message.get("visible"))
            elif kind == "focus":
                focus = message.get("agent")
                peer.focus = focus if isinstance(focus, str) else None
            elif kind == "burn_window":
                hours = message.get("hours")
                peer.burn_hours = float(hours) if isinstance(hours, (int, float)) and hours > 0 else None
            elif kind == "visit":
                agent = message.get("agent")
                if isinstance(agent, str):
                    self._visits.append((agent, time.time()))

    def take_visits(self) -> list:
        """Visits reported since the last call: [(agent id, epoch seconds)]."""
        with self._lock:
            visits, self._visits = self._visits, []
        return visits


class EngineClient:
    """A view's side: stay connected, keep a ViewState, report changes.

    ``on_change(snapshot)`` and ``on_bell(agent, episode)`` run on the
    client's thread; a Textual app hands them to its main thread with
    ``call_from_thread``.
    """

    def __init__(
        self,
        path: Path,
        on_change: Callable[[proto.Snapshot], None],
        on_bell: Optional[Callable[[str, dict], None]] = None,
        on_connection: Optional[Callable[[bool], None]] = None,
    ) -> None:
        self.path = Path(path)
        self.on_change = on_change
        self.on_bell = on_bell
        self.on_connection = on_connection
        self.state = proto.ViewState()
        self.connected = False
        self._sock: Optional[socket.socket] = None
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._visible = False
        self._focus: Optional[str] = None
        self._burn_hours: Optional[float] = None
        # When this view last heard the engine, and the main loop's beat age
        # in its last ping (None: an engine too old to send one)
        self._heard_at = time.monotonic()
        self._beat_age: Optional[float] = None

    def stalled_for(self) -> Optional[float]:
        """Seconds the connected engine has been frozen, or None while it is live.

        Frozen is either its main loop not beating (the ping says so) or
        the connection going silent. Both clocks are monotonic, so a
        machine sleep is not mistaken for a stall.
        """
        if not self.connected:
            return None
        silent = time.monotonic() - self._heard_at
        if silent >= SILENT_SECONDS:
            return silent
        if self._beat_age is not None and self._beat_age >= STALL_SECONDS:
            return self._beat_age
        return None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="engine-client", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        sock = self._sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)

    def set_visible(self, visible: bool) -> None:
        self._visible = visible
        self._send({"t": "visible", "visible": visible})

    def set_burn_window(self, hours: Optional[float]) -> None:
        """Ask the engine for burn over the last ``hours`` (None or 0: none)."""
        self._burn_hours = hours
        self._send({"t": "burn_window", "hours": hours or 0})

    def visit(self, agent: str) -> None:
        """The person looked at ``agent``: its next stall may ring again."""
        self._send({"t": "visit", "agent": agent})

    def set_focus(self, agent: Optional[str]) -> None:
        self._focus = agent
        self._send({"t": "focus", "agent": agent})

    def _send(self, message: dict) -> None:
        with self._send_lock:
            sock = self._sock
            if sock is None:
                return
            try:
                sock.sendall(proto.encode(message))
            except OSError:
                pass

    def _set_connected(self, connected: bool) -> None:
        if connected != self.connected:
            self.connected = connected
            if self.on_connection is not None:
                self.on_connection(connected)

    def _run(self) -> None:
        backoff = 0.1
        while not self._stop.is_set():
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.connect(str(self.path))
            except OSError:
                sock.close()
                self._set_connected(False)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 2.0)
                continue
            backoff = 0.1
            self._sock = sock
            self.state = proto.ViewState()
            self._heard_at = time.monotonic()
            self._beat_age = None
            # Restate what this view is, on every (re)connect
            self._send({"t": "visible", "visible": self._visible})
            if self._focus is not None:
                self._send({"t": "focus", "agent": self._focus})
            if self._burn_hours:
                self._send({"t": "burn_window", "hours": self._burn_hours})
            self._read(sock)
            with self._send_lock:
                self._sock = None
            sock.close()
            self._set_connected(False)

    def _read(self, sock: socket.socket) -> None:
        buffer = bytearray()
        while not self._stop.is_set():
            try:
                data = sock.recv(65536)
            except OSError:
                return
            if not data:
                return
            buffer += data
            self._heard_at = time.monotonic()
            for message in proto.decode_lines(buffer):
                if message.get("t") == "ping":
                    age = message.get("beat_age")
                    self._beat_age = float(age) if isinstance(age, (int, float)) else None
                if message.get("t") == "bell":
                    if self.on_bell is not None:
                        self.on_bell(str(message.get("agent")), message.get("episode") or {})
                    continue
                changed = self.state.feed(message)
                if self.state.needs_resync:
                    return  # reconnect for a fresh snapshot
                if message.get("t") == "snapshot":
                    self._set_connected(True)
                if changed and self.state.snapshot is not None:
                    self.on_change(self.state.snapshot)
