"""
Real implementations of protocol interfaces.

These are production implementations that use libtmux for tmux operations
and perform real file I/O.
"""

import os
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, List, Dict, Any

import libtmux
import libtmux.neo
import libtmux.server
from libtmux import exc as libtmux_exc
from libtmux.common import tmux_cmd as _libtmux_cmd
from libtmux.exc import LibTmuxException
from libtmux._internal.query_list import ObjectDoesNotExist

from .protocols import TmuxTimeoutError
from .tmux_utils import PANE_LISTING_FORMAT, PaneInfo, _build_tmux_cmd, parse_pane_listing

# How long the monitor daemon waits for any one tmux read. A healthy server
# answers in milliseconds; one that has wedged (tmux 3.3-3.6 server bugs, a
# client stuck on a blocked tty) never does, and libtmux waits on it with no
# limit, which froze every agent's status at its last value.
TMUX_COMMAND_TIMEOUT_SECONDS = 3.0

# The timeout of the RealTmux call running on this thread, read by
# _BoundedTmuxCmd. libtmux builds its commands deep inside its objects
# (Server.cmd, neo.fetch_objs), so a per-call timeout cannot be passed down.
_command_timeout = threading.local()


class _BoundedTmuxCmd(_libtmux_cmd):
    """libtmux's ``tmux_cmd`` that honours the calling RealTmux's timeout.

    Without one (every caller but a timed RealTmux) it is libtmux's own
    command, unchanged. With one, it is the same command with the wait
    bounded: a server that does not answer gets its client killed and the
    call raises TmuxTimeoutError.
    """

    def __init__(self, *args: Any) -> None:
        timeout = getattr(_command_timeout, "seconds", None)
        if timeout is None:
            super().__init__(*args)
            return
        tmux_bin = shutil.which("tmux")
        if not tmux_bin:
            raise libtmux_exc.TmuxCommandNotFound
        self.cmd = [str(c) for c in (tmux_bin, *args)]
        self.process = subprocess.Popen(
            self.cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="backslashreplace",
        )
        try:
            stdout, stderr = self.process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.communicate()
            # Some libtmux listings swallow every exception (Server.sessions
            # reads as "no sessions"); the flag lets RealTmux see it anyway
            _command_timeout.timed_out = True
            raise TmuxTimeoutError(
                f"tmux did not answer in {timeout:g}s: {' '.join(self.cmd[1:3])}"
            ) from None
        self.returncode = self.process.returncode
        # The rest is libtmux's own output handling
        stdout_split = stdout.split("\n")
        while stdout_split and stdout_split[-1] == "":
            stdout_split.pop()
        self.stderr = [line for line in stderr.split("\n") if line]
        if "has-session" in self.cmd and self.stderr and not stdout_split:
            self.stdout = [self.stderr[0]]
        else:
            self.stdout = stdout_split


# Every libtmux command goes through these two names (Server.cmd and the
# object listings); the subclass is a no-op for any caller without a timeout.
libtmux.server.tmux_cmd = _BoundedTmuxCmd
libtmux.neo.tmux_cmd = _BoundedTmuxCmd


class RealTmux:
    """Production implementation of TmuxInterface using libtmux.

    Includes caching to reduce subprocess overhead. libtmux spawns a new
    subprocess for every tmux command, which is expensive at high frequencies.
    """

    # Cache TTL in seconds - pane objects rarely change
    _CACHE_TTL = 30.0

    # The command timeout of an instance built without one. None: libtmux's
    # unbounded wait. The monitor daemon's entry point sets it for its whole
    # process, so the status detectors' own clients are bounded too.
    default_command_timeout: Optional[float] = None

    def __init__(self, socket_name: Optional[str] = None,
                 command_timeout: Optional[float] = None):
        """Initialize with optional socket name for test isolation.

        If no socket_name is provided, checks OVERCODE_TMUX_SOCKET env var.

        ``command_timeout`` bounds each read (capture_pane, has_session,
        list_windows, list_panes, get_pane_pid): one the server does not
        answer in time raises TmuxTimeoutError (list_panes returns None),
        and for ``command_timeout`` seconds after that the reads fail at
        once instead of each waiting out its own timeout, so a tick over
        many agents costs one timeout, not one per agent. None (the
        default) takes ``default_command_timeout``.
        """
        # Support OVERCODE_TMUX_SOCKET env var for testing
        self._socket_name = socket_name or os.environ.get("OVERCODE_TMUX_SOCKET")
        self._server: Optional[libtmux.Server] = None
        self._command_timeout = (
            command_timeout if command_timeout is not None else self.default_command_timeout
        )
        self._unresponsive_until = 0.0  # time.monotonic() deadline
        # Cache: (session_name, window_index) -> (pane, timestamp)
        self._pane_cache: Dict[tuple, tuple] = {}
        # Cache: session_name -> (session_obj, timestamp)
        self._session_cache: Dict[str, tuple] = {}

    @contextmanager
    def _bounded(self) -> Iterator[None]:
        """Run the enclosed tmux reads under this client's command timeout."""
        timeout = self._command_timeout
        if timeout is None:
            yield
            return
        if time.monotonic() < self._unresponsive_until:
            raise TmuxTimeoutError("tmux server not responding (recent timeout)")
        previous = getattr(_command_timeout, "seconds", None)
        _command_timeout.seconds = timeout
        _command_timeout.timed_out = False
        try:
            yield
            if _command_timeout.timed_out:
                # libtmux turned the timeout into an empty answer ("no such
                # session"), which would read as the agent being gone
                raise TmuxTimeoutError(f"tmux did not answer in {timeout:g}s")
        except TmuxTimeoutError:
            self._unresponsive_until = time.monotonic() + timeout
            raise
        finally:
            _command_timeout.seconds = previous
            _command_timeout.timed_out = False

    @property
    def server(self) -> libtmux.Server:
        """Lazy-load the tmux server connection."""
        if self._server is None:
            if self._socket_name:
                self._server = libtmux.Server(socket_name=self._socket_name)
            else:
                self._server = libtmux.Server()
        return self._server

    def _get_session(self, session: str) -> Optional[libtmux.Session]:
        """Get a session by name, with caching."""
        now = time.time()
        if session in self._session_cache:
            cached_session, cached_time = self._session_cache[session]
            if now - cached_time < self._CACHE_TTL:
                return cached_session

        try:
            sess = self.server.sessions.get(session_name=session)
            self._session_cache[session] = (sess, now)
            return sess
        except (LibTmuxException, ObjectDoesNotExist):
            return None

    def _get_window(self, session: str, window: str) -> Optional[libtmux.Window]:
        """Get a window by session name and window name.

        Falls back to index-based lookup for legacy sessions that still have
        digit-string window values (e.g. "4" from pre-name-based era).
        """
        sess = self._get_session(session)
        if sess is None:
            return None
        try:
            return sess.windows.get(window_name=window)
        except (LibTmuxException, ObjectDoesNotExist):
            # Fallback: if window looks like a legacy index, try index lookup
            if window.isdigit():
                try:
                    return sess.windows.get(window_index=window)
                except (LibTmuxException, ObjectDoesNotExist):
                    pass
            return None

    def _get_pane(self, session: str, window: str) -> Optional[libtmux.Pane]:
        """Get the first pane of a window, with caching."""
        cache_key = (session, window)
        now = time.time()

        # Check cache first
        if cache_key in self._pane_cache:
            cached_pane, cached_time = self._pane_cache[cache_key]
            if now - cached_time < self._CACHE_TTL:
                return cached_pane

        # Cache miss - fetch from tmux
        win = self._get_window(session, window)
        if win is None or not win.panes:
            return None
        pane = win.panes[0]
        self._pane_cache[cache_key] = (pane, now)
        return pane

    def invalidate_cache(self, session: str = None, window: str = None) -> None:
        """Invalidate cached objects.

        Args:
            session: If provided, invalidate only this session's cache
            window: If provided with session, invalidate only this window's pane
        """
        if session is None:
            self._pane_cache.clear()
            self._session_cache.clear()
        elif window is not None:
            self._pane_cache.pop((session, window), None)
        else:
            self._session_cache.pop(session, None)
            # Remove all panes for this session
            keys_to_remove = [k for k in self._pane_cache if k[0] == session]
            for k in keys_to_remove:
                del self._pane_cache[k]

    def capture_pane(self, session: str, window: str, lines: int = 100) -> Optional[str]:
        with self._bounded():
            try:
                pane = self._get_pane(session, window)
                if pane is None:
                    return None
                # capture_pane returns list of lines
                # escape_sequences=True preserves ANSI color codes for TUI rendering
                captured = pane.capture_pane(start=-lines, escape_sequences=True)
                if isinstance(captured, list):
                    return '\n'.join(captured)
                return captured
            except LibTmuxException:
                # Pane may have been killed - invalidate cache and retry once
                self.invalidate_cache(session, window)
                return None

    def send_keys(self, session: str, window: str, keys: str, enter: bool = True) -> bool:
        try:
            pane = self._get_pane(session, window)
            if pane is None:
                return False

            from .tmux_utils import send_keys_to_pane
            send_keys_to_pane(pane, keys, enter=enter)
            return True
        except LibTmuxException:
            return False

    def has_session(self, session: str) -> bool:
        with self._bounded():
            try:
                return self.server.has_session(session)
            except LibTmuxException:
                return False

    def new_session(self, session: str) -> bool:
        try:
            self.server.new_session(session_name=session, attach=False)
            return True
        except LibTmuxException:
            return False

    def new_window(self, session: str, name: str, command: Optional[List[str]] = None,
                   cwd: Optional[str] = None) -> Optional[str]:
        try:
            sess = self._get_session(session)
            if sess is None:
                return None

            kwargs: Dict[str, Any] = {'window_name': name, 'attach': False}
            if cwd:
                kwargs['start_directory'] = cwd
            if command:
                kwargs['window_shell'] = ' '.join(command)

            window = sess.new_window(**kwargs)
            # Prevent tmux from auto-renaming the window based on the
            # running process — we rely on stable window names for lookups.
            window.set_window_option('automatic-rename', 'off')
            return window.window_name
        except (LibTmuxException, ValueError):
            return None

    def kill_window(self, session: str, window: str) -> bool:
        try:
            win = self._get_window(session, window)
            if win is None:
                return False
            win.kill()
            return True
        except LibTmuxException:
            return False

    def rename_window(self, session: str, window: str, new_name: str) -> bool:
        """Rename a window (exact target; see ``rename_tmux_window``)."""
        from .tmux_utils import rename_tmux_window
        return rename_tmux_window(self.server, session, window, new_name)

    def kill_session(self, session: str) -> bool:
        try:
            sess = self._get_session(session)
            if sess is None:
                return False
            sess.kill()
            return True
        except LibTmuxException:
            return False

    def list_windows(self, session: str) -> List[Dict[str, Any]]:
        with self._bounded():
            try:
                sess = self._get_session(session)
                if sess is None:
                    return []

                windows = []
                for win in sess.windows:
                    windows.append({
                        'index': int(win.window_index),
                        'name': win.window_name,
                        'active': win.window_active == '1'
                    })
                return windows
            except LibTmuxException:
                # The cached session object is stale (server restarted, session
                # gone); drop it so the next call looks the session up again.
                self.invalidate_cache(session)
                return []

    def list_panes(self, session: str) -> Optional[Dict[str, PaneInfo]]:
        """Every window's first pane in ``session`` from one ``list-panes -s``.

        The whole session in a single command — pid, change signature and
        attached-client count per window (``tmux_utils.PaneInfo``) — for
        callers that would otherwise ask per window: ``get_pane_pid`` costs
        three commands each on a fresh instance. Goes straight to the server
        (the object cache is not involved). None when the server or session
        is unavailable, in which case the cached objects for the session are
        dropped as well. Also None when the server does not answer within
        the command timeout (the cache is kept: nothing is known to be gone).
        """
        try:
            with self._bounded():
                proc = self.server.cmd("list-panes", "-s", "-t", session, "-F", PANE_LISTING_FORMAT)
        except TmuxTimeoutError:
            return None
        except LibTmuxException:
            self.invalidate_cache(session)
            return None
        if proc.returncode != 0:
            self.invalidate_cache(session)
            return None
        return parse_pane_listing(proc.stdout)

    def attach(self, session: str, window: Optional[str] = None, bare: bool = False) -> None:
        if bare:
            self._attach_bare(session, window)
        else:
            from .tmux_utils import tmux_window_target
            target = tmux_window_target(session, window) if window is not None else session
            cmd = [*_build_tmux_cmd(), "attach-session", "-t", target]
            os.execvp("tmux", cmd)

    def _attach_bare(self, session: str, window: str) -> None:
        """Create a linked session with stripped chrome and attach to it."""
        from .tmux_utils import attach_bare
        attach_bare(session, window)

    def get_pane_pid(self, session: str, window: str) -> Optional[int]:
        """Get the PID of the shell process in a window's first pane."""
        with self._bounded():
            try:
                pane = self._get_pane(session, window)
                if pane is None:
                    return None
                return int(pane.pane_pid)
            except (LibTmuxException, ValueError, TypeError):
                return None

    def select_window(self, session: str, window: str) -> bool:
        """Select a window in a tmux session (for external pane sync)."""
        try:
            win = self._get_window(session, window)
            if win is None:
                return False
            win.select()
            return True
        except LibTmuxException:
            return False

    def ensure_empty_placeholder_window(self, session: str, window_name: str, message: str) -> bool:
        """Create a static placeholder window if it doesn't already exist (#457).

        Used as a fallback target when select_window() fails — guarantees the
        bottom pane shows a clear empty-state instead of lingering on the
        previously-focused agent.
        """
        try:
            sess = self._get_session(session)
            if sess is None:
                return False
            for win in sess.windows:
                if win.window_name == window_name:
                    return True
            # Use printf for embedded newlines + tail -f /dev/null to keep the
            # window alive without burning CPU. Single-quoted to keep the
            # shell-passed message escaped safely below.
            import shlex
            safe_msg = shlex.quote(message)
            cmd = f"clear; printf %s {safe_msg}; tail -f /dev/null"
            window = sess.new_window(
                window_name=window_name,
                attach=False,
                window_shell=cmd,
            )
            window.set_window_option('automatic-rename', 'off')
            # Tag the window so cleanup logic and untracked-window kill paths
            # can recognise it as overcode infrastructure rather than an agent.
            window.set_window_option('@is_overcode_placeholder', 'on')
            return True
        except (LibTmuxException, ValueError):
            return False

    def resize_window(self, session: str, window: str, width: int, height: int) -> bool:
        """Resize a tmux window to match terminal dimensions.

        Used to fix window size mismatches when switching between sessions
        after terminal resize (#245).

        Args:
            session: Session name
            window: Window name/index
            width: Target width in columns
            height: Target height in rows

        Returns:
            True if resize succeeded, False otherwise
        """
        try:
            # Use tmux resize-window command
            # Format: session:window.pane
            target = f"{session}:{window}"
            result = self.run(
                [*_build_tmux_cmd(), "resize-window", "-t", target, "-x", str(width), "-y", str(height)],
                timeout=2
            )
            return result is not None and result.get("returncode") == 0
        except Exception:
            return False


class RealFileSystem:
    """Production implementation of FileSystemInterface"""

    def exists(self, path: Path) -> bool:
        return path.exists()

    def mkdir(self, path: Path, parents: bool = True) -> bool:
        try:
            path.mkdir(parents=parents, exist_ok=True)
            return True
        except IOError:
            return False

    def read_text(self, path: Path) -> Optional[str]:
        try:
            return path.read_text()
        except IOError:
            return None

    def write_text(self, path: Path, content: str) -> bool:
        try:
            path.write_text(content)
            return True
        except IOError:
            return False


class RealSubprocess:
    """Production implementation of SubprocessInterface"""

    def run(self, cmd: List[str], timeout: Optional[int] = None,
            capture_output: bool = True) -> Optional[Dict[str, Any]]:
        try:
            result = subprocess.run(
                cmd, timeout=timeout, capture_output=capture_output, text=True
            )
            return {
                'returncode': result.returncode,
                'stdout': result.stdout if capture_output else '',
                'stderr': result.stderr if capture_output else ''
            }
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            return None
