"""
Tmux session and window management for Overcode.

Uses libtmux for reliable tmux interaction.
"""

import os
import time
from typing import Optional, List, Dict, Any, TYPE_CHECKING

import libtmux
from libtmux.exc import LibTmuxException
from libtmux._internal.query_list import ObjectDoesNotExist

if TYPE_CHECKING:
    from .interfaces import TmuxInterface


class TmuxManager:
    """Manages tmux sessions and windows for Overcode.

    This class can be used directly (uses libtmux) or with an injected
    TmuxInterface for testing.
    """

    def __init__(self, session_name: str = "agents", tmux: "TmuxInterface" = None, socket: str = None):
        """Initialize the tmux manager.

        Args:
            session_name: Name of the tmux session to manage
            tmux: Optional TmuxInterface for dependency injection (testing)
            socket: Optional tmux socket name (for testing isolation)
        """
        self.session_name = session_name
        self._tmux = tmux  # If None, use libtmux directly
        # Support OVERCODE_TMUX_SOCKET env var for testing
        self.socket = socket or os.environ.get("OVERCODE_TMUX_SOCKET")
        self._server: Optional[libtmux.Server] = None

    @property
    def server(self) -> libtmux.Server:
        """Lazy-load the tmux server connection."""
        if self._server is None:
            if self.socket:
                self._server = libtmux.Server(socket_name=self.socket)
            else:
                self._server = libtmux.Server()
        return self._server

    def _get_session(self) -> Optional[libtmux.Session]:
        """Get the managed session, or None if it doesn't exist."""
        try:
            return self.server.sessions.get(session_name=self.session_name)
        except (LibTmuxException, ObjectDoesNotExist):
            return None

    def _get_window(self, window_index: int) -> Optional[libtmux.Window]:
        """Get a window by index."""
        sess = self._get_session()
        if sess is None:
            return None
        try:
            return sess.windows.get(window_index=str(window_index))
        except (LibTmuxException, ObjectDoesNotExist):
            return None

    def _get_pane(self, window_index: int) -> Optional[libtmux.Pane]:
        """Get the first pane of a window."""
        win = self._get_window(window_index)
        if win is None or not win.panes:
            return None
        return win.panes[0]

    def ensure_session(self) -> bool:
        """Create tmux session if it doesn't exist"""
        if self.session_exists():
            return True

        if self._tmux:
            return self._tmux.new_session(self.session_name)

        try:
            self.server.new_session(session_name=self.session_name, attach=False)
            return True
        except LibTmuxException:
            return False

    def session_exists(self) -> bool:
        """Check if the tmux session exists"""
        if self._tmux:
            return self._tmux.has_session(self.session_name)

        try:
            return self.server.has_session(self.session_name)
        except LibTmuxException:
            return False

    def create_window(self, window_name: str, start_directory: Optional[str] = None) -> Optional[int]:
        """Create a new window in the tmux session"""
        if not self.ensure_session():
            return None

        if self._tmux:
            return self._tmux.new_window(self.session_name, window_name, cwd=start_directory)

        try:
            sess = self._get_session()
            if sess is None:
                return None

            kwargs: Dict[str, Any] = {'window_name': window_name, 'attach': False}
            if start_directory:
                kwargs['start_directory'] = start_directory

            window = sess.new_window(**kwargs)
            return int(window.window_index)
        except (LibTmuxException, ValueError):
            return None

    def send_keys(self, window_index: int, keys: str, enter: bool = True) -> bool:
        """Send keys to a tmux window.

        For Claude Code: text and Enter must be sent as SEPARATE commands
        with a small delay, otherwise Claude Code doesn't process the Enter.
        """
        if self._tmux:
            return self._tmux.send_keys(self.session_name, window_index, keys, enter)

        try:
            pane = self._get_pane(window_index)
            if pane is None:
                return False

            # Send text first (if any)
            if keys:
                # Special handling for ! commands (#139)
                # Claude Code requires ! to be sent separately to trigger mode switch
                # to bash mode before receiving the rest of the command
                if keys.startswith('!') and len(keys) > 1:
                    # Send ! first
                    pane.send_keys('!', enter=False)
                    # Wait for mode switch to process
                    time.sleep(0.15)
                    # Send the rest (without the !)
                    rest = keys[1:]
                    if rest:
                        pane.send_keys(rest, enter=False)
                        time.sleep(0.1)
                else:
                    pane.send_keys(keys, enter=False)
                    # Small delay for Claude Code to process text
                    time.sleep(0.1)

            # Send Enter separately
            if enter:
                pane.send_keys('', enter=True)

            return True
        except LibTmuxException:
            return False

    def attach_session(self, window: Optional[int] = None, bare: bool = False):
        """Attach to the tmux session (blocking).

        Args:
            window: optional window index to target
            bare: if True, create a linked session with tmux chrome stripped,
                  isolated from other clients attached to the main session
        """
        if self._tmux:
            self._tmux.attach(self.session_name, window=window, bare=bare)
            return
        if bare:
            self._attach_bare(window)
        else:
            target = f"{self.session_name}:={window}" if window is not None else self.session_name
            os.execlp("tmux", "tmux", "attach-session", "-t", target)

    def _attach_bare(self, window: int):
        """Create a linked session with stripped chrome and attach to it.

        Uses a grouped session (new-session -t) so options are isolated
        from the main session. Sets destroy-unattached so the linked
        session is cleaned up when the terminal closes.
        """
        import subprocess

        bare_session = f"bare-{self.session_name}-{window}"

        # Kill any stale bare session with the same name
        subprocess.run(
            ["tmux", "kill-session", "-t", bare_session],
            capture_output=True,
        )

        # Create linked session sharing the same window group
        result = subprocess.run(
            ["tmux", "new-session", "-d", "-s", bare_session, "-t", self.session_name],
            capture_output=True,
        )
        if result.returncode != 0:
            print(f"Failed to create bare session: {result.stderr.decode().strip()}")
            return

        # Configure the linked session (isolated from main session)
        for cmd in [
            ["tmux", "set", "-t", bare_session, "status", "off"],
            ["tmux", "set", "-t", bare_session, "mouse", "off"],
            ["tmux", "set", "-t", bare_session, "destroy-unattached", "on"],
            ["tmux", "select-window", "-t", f"{bare_session}:={window}"],
        ]:
            subprocess.run(cmd, capture_output=True)

        # Attach (replaces process)
        os.execlp("tmux", "tmux", "attach-session", "-t", bare_session)

    def list_windows(self) -> List[Dict[str, Any]]:
        """List all windows in the session.

        Returns list of dicts with 'index' (int), 'name' (str), 'command' (str).
        """
        if not self.session_exists():
            return []

        if self._tmux:
            # Convert from interface format to our format
            raw_windows = self._tmux.list_windows(self.session_name)
            return [
                {"index": w.get('index', 0), "name": w.get('name', ''), "command": ""}
                for w in raw_windows
            ]

        try:
            sess = self._get_session()
            if sess is None:
                return []

            windows = []
            for win in sess.windows:
                # Get command from first pane
                command = ""
                if win.panes:
                    command = win.panes[0].pane_current_command or ""
                windows.append({
                    "index": int(win.window_index),
                    "name": win.window_name,
                    "command": command
                })
            return windows
        except LibTmuxException:
            return []

    def kill_window(self, window_index: int) -> bool:
        """Kill a specific window"""
        if self._tmux:
            return self._tmux.kill_window(self.session_name, window_index)

        try:
            win = self._get_window(window_index)
            if win is None:
                return False
            win.kill()
            return True
        except LibTmuxException:
            return False

    def kill_session(self) -> bool:
        """Kill the entire tmux session"""
        if self._tmux:
            return self._tmux.kill_session(self.session_name)

        try:
            sess = self._get_session()
            if sess is None:
                return False
            sess.kill()
            return True
        except LibTmuxException:
            return False

    def window_exists(self, window_index: int) -> bool:
        """Check if a specific window exists"""
        if not self.session_exists():
            return False

        if self._tmux:
            windows = self._tmux.list_windows(self.session_name)
            return any(w.get('index') == window_index for w in windows)

        try:
            sess = self._get_session()
            if sess is None:
                return False

            for win in sess.windows:
                if int(win.window_index) == window_index:
                    return True
            return False
        except LibTmuxException:
            return False
