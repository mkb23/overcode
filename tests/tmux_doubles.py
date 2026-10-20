"""
Mock implementations of protocol interfaces for testing.

These mocks allow unit tests to run without real tmux sessions,
file system access, or subprocess calls.
"""

from typing import Callable, Optional, List, Dict, Any


class MockTmux:
    """Mock implementation of TmuxInterface for testing"""

    def __init__(self):
        self.sessions: Dict[str, Dict[str, str]] = {}  # session -> {window_name: content}
        self.sent_keys: List[tuple] = []  # Record of sent keys
        self._next_window = 1
        # Every new_window call's arguments, in order
        self.windows_created: List[Dict[str, Any]] = []
        # (session, window) -> {option: value}, from set_window_option
        self.window_options: Dict[tuple, Dict[str, str]] = {}
        # True: a wedged server, as a timed RealTmux reports it — the reads
        # whose empty answer means "gone" raise TmuxTimeoutError and
        # list_panes answers None.
        self.unresponsive = False

    def _check_responsive(self) -> None:
        if self.unresponsive:
            from overcode.protocols import TmuxTimeoutError

            raise TmuxTimeoutError("mock tmux server not responding")

    def set_pane_content(self, session: str, window: str, content: str):
        """Set up mock pane content for testing"""
        if session not in self.sessions:
            self.sessions[session] = {}
        self.sessions[session][window] = content

    def capture_pane(self, session: str, window: str, lines: int = 100) -> Optional[str]:
        self._check_responsive()
        if session in self.sessions and window in self.sessions[session]:
            content = self.sessions[session][window]
            # Simulate line limit
            content_lines = content.split('\n')
            return '\n'.join(content_lines[-lines:])
        return None

    def send_keys(self, session: str, window: str, keys: str, enter: bool = True) -> bool:
        self.sent_keys.append((session, window, keys, enter))
        return session in self.sessions

    def has_session(self, session: str) -> bool:
        self._check_responsive()
        return session in self.sessions

    def new_session(self, session: str) -> bool:
        if session not in self.sessions:
            self.sessions[session] = {}
            return True
        return False

    def new_window(self, session: str, name: str, command: Optional[List[str]] = None,
                   cwd: Optional[str] = None, *, wait_for_prompt: bool = False,
                   options: Optional[Dict[str, str]] = None) -> Optional[str]:
        if session not in self.sessions:
            return None
        self.sessions[session][name] = ""
        self.windows_created.append({
            "session": session, "name": name, "command": command, "cwd": cwd,
            "wait_for_prompt": wait_for_prompt, "options": dict(options or {}),
        })
        if options:
            self.window_options[(session, name)] = dict(options)
        return name

    def kill_window(self, session: str, window: str) -> bool:
        if session in self.sessions and window in self.sessions[session]:
            del self.sessions[session][window]
            return True
        return False

    def kill_windows(self, session: str, match: Callable[[str], bool]) -> List[str]:
        windows = self.sessions.get(session, {})
        killed = [name for name, content in windows.items()
                  if isinstance(content, str) and match(name)]
        for name in killed:
            del windows[name]
        return killed

    def resize_window(self, session: str, window: str, width: int, height: int) -> bool:
        return window in self.sessions.get(session, {})

    def rename_window(self, session: str, window: str, new_name: str) -> bool:
        """Rename a window, preserving its pane content."""
        windows = self.sessions.get(session)
        if windows is None or window not in windows:
            return False
        windows[new_name] = windows.pop(window)
        if (session, window) in self.window_options:
            self.window_options[(session, new_name)] = self.window_options.pop((session, window))
        return True

    def set_window_option(self, session: str, window: str, option: str, value: str) -> bool:
        """Record the option; False when the window does not exist."""
        if window not in self.sessions.get(session, {}):
            return False
        self.window_options.setdefault((session, window), {})[option] = value
        return True

    def kill_session(self, session: str) -> bool:
        if session in self.sessions:
            del self.sessions[session]
            return True
        return False

    def list_windows(self, session: str) -> List[Dict[str, Any]]:
        self._check_responsive()
        if session not in self.sessions:
            return []
        # Windows are indexed in creation order, as tmux does, so callers
        # that treat window 0 specially (the default shell) see real indices.
        return [
            {"index": i, "name": key, "active": False}
            for i, key in enumerate(self.sessions[session].keys())
        ]

    def attach(self, session: str, window: Optional[str] = None, bare: bool = False) -> None:
        pass  # No-op in tests

    def get_pane_pid(self, session: str, window: str) -> Optional[int]:
        """Return None in tests — no real pane PIDs."""
        self._check_responsive()
        return None

    def list_panes(self, session: str) -> Optional[Dict[str, Any]]:
        """One ``PaneInfo`` per window; pid 0 (no real processes in tests).

        The change signature is derived from the pane text — tmux's
        ``window_activity`` / ``history_size`` / cursor all move with
        output — so a ``set_pane_content`` shows up as a changed signature.
        """
        from overcode.tmux_utils import PaneInfo

        if self.unresponsive or session not in self.sessions:
            return None
        panes = {}
        for index, (name, content) in enumerate(self.sessions[session].items()):
            if not isinstance(content, str):
                continue  # the placeholder bookkeeping entry, not a pane
            lines = content.split("\n")
            panes[name] = PaneInfo(
                window_name=name,
                window_index=index,
                pane_pid=0,
                activity=hash(content) & 0xFFFFFFFF,
                history_size=len(lines),
                cursor_x=len(lines[-1]),
                cursor_y=len(lines) - 1,
                current_command="claude",
                session_attached=0,
            )
        return panes

    def select_window(self, session: str, window: str) -> bool:
        """Select a window - no-op in tests, just return True."""
        return session in self.sessions

    def ensure_empty_placeholder_window(self, session: str, window_name: str, message: str) -> bool:
        """Pretend the placeholder window exists — sufficient for tests."""
        if session not in self.sessions:
            return False
        windows = self.sessions[session].setdefault("windows", {})
        windows.setdefault(window_name, {"name": window_name, "placeholder": True})
        return True

