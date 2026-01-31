"""
Real implementations of protocol interfaces.

These are production implementations that use libtmux for tmux operations
and perform real file I/O.
"""

import json
import os
import time
from pathlib import Path
from typing import Optional, List, Dict, Any

import libtmux
from libtmux.exc import LibTmuxException
from libtmux._internal.query_list import ObjectDoesNotExist


class RealTmux:
    """Production implementation of TmuxInterface using libtmux"""

    def __init__(self, socket_name: Optional[str] = None):
        """Initialize with optional socket name for test isolation.

        If no socket_name is provided, checks OVERCODE_TMUX_SOCKET env var.
        """
        # Support OVERCODE_TMUX_SOCKET env var for testing
        self._socket_name = socket_name or os.environ.get("OVERCODE_TMUX_SOCKET")
        self._server: Optional[libtmux.Server] = None

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
        """Get a session by name, or None if it doesn't exist."""
        try:
            return self.server.sessions.get(session_name=session)
        except (LibTmuxException, ObjectDoesNotExist):
            return None

    def _get_window(self, session: str, window: int) -> Optional[libtmux.Window]:
        """Get a window by session name and window index."""
        sess = self._get_session(session)
        if sess is None:
            return None
        try:
            return sess.windows.get(window_index=str(window))
        except (LibTmuxException, ObjectDoesNotExist):
            return None

    def _get_pane(self, session: str, window: int) -> Optional[libtmux.Pane]:
        """Get the first pane of a window."""
        win = self._get_window(session, window)
        if win is None or not win.panes:
            return None
        return win.panes[0]

    def capture_pane(self, session: str, window: int, lines: int = 100) -> Optional[str]:
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
            return None

    def send_keys(self, session: str, window: int, keys: str, enter: bool = True) -> bool:
        try:
            pane = self._get_pane(session, window)
            if pane is None:
                return False

            # For Claude Code: text and Enter must be sent as SEPARATE commands
            # with a small delay, otherwise Claude Code doesn't process the Enter.
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

            if enter:
                pane.send_keys('', enter=True)

            return True
        except LibTmuxException:
            return False

    def has_session(self, session: str) -> bool:
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
                   cwd: Optional[str] = None) -> Optional[int]:
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
            return int(window.window_index)
        except (LibTmuxException, ValueError):
            return None

    def kill_window(self, session: str, window: int) -> bool:
        try:
            win = self._get_window(session, window)
            if win is None:
                return False
            win.kill()
            return True
        except LibTmuxException:
            return False

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
            return []

    def attach(self, session: str) -> None:
        os.execlp("tmux", "tmux", "attach-session", "-t", session)

    def select_window(self, session: str, window: int) -> bool:
        """Select a window in a tmux session (for external pane sync)."""
        try:
            win = self._get_window(session, window)
            if win is None:
                return False
            win.select()
            return True
        except LibTmuxException:
            return False


class RealFileSystem:
    """Production implementation of FileSystemInterface"""

    def read_json(self, path: Path) -> Optional[Dict[str, Any]]:
        try:
            if not path.exists():
                return None
            with open(path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

    def write_json(self, path: Path, data: Dict[str, Any]) -> bool:
        try:
            # Write atomically via temp file
            temp_path = path.with_suffix('.tmp')
            with open(temp_path, 'w') as f:
                json.dump(data, f, indent=2)
            temp_path.replace(path)
            return True
        except IOError:
            return False

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

    def popen(self, cmd: List[str], cwd: Optional[str] = None) -> Any:
        try:
            return subprocess.Popen(cmd, cwd=cwd)
        except subprocess.SubprocessError:
            return None
