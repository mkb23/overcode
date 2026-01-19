"""
Mock implementations of protocol interfaces for testing.

These mocks allow unit tests to run without real tmux sessions,
file system access, or subprocess calls.
"""

import json
from pathlib import Path
from typing import Optional, List, Dict, Any


class MockTmux:
    """Mock implementation of TmuxInterface for testing"""

    def __init__(self):
        self.sessions: Dict[str, Dict[int, str]] = {}  # session -> {window: content}
        self.sent_keys: List[tuple] = []  # Record of sent keys
        self._next_window = 1

    def set_pane_content(self, session: str, window: int, content: str):
        """Set up mock pane content for testing"""
        if session not in self.sessions:
            self.sessions[session] = {}
        self.sessions[session][window] = content

    def capture_pane(self, session: str, window: int, lines: int = 100) -> Optional[str]:
        if session in self.sessions and window in self.sessions[session]:
            content = self.sessions[session][window]
            # Simulate line limit
            content_lines = content.split('\n')
            return '\n'.join(content_lines[-lines:])
        return None

    def send_keys(self, session: str, window: int, keys: str, enter: bool = True) -> bool:
        self.sent_keys.append((session, window, keys, enter))
        return session in self.sessions

    def has_session(self, session: str) -> bool:
        return session in self.sessions

    def new_session(self, session: str) -> bool:
        if session not in self.sessions:
            self.sessions[session] = {}
            return True
        return False

    def new_window(self, session: str, name: str, command: Optional[List[str]] = None,
                   cwd: Optional[str] = None) -> Optional[int]:
        if session not in self.sessions:
            return None
        window = self._next_window
        self._next_window += 1
        self.sessions[session][window] = ""
        return window

    def kill_window(self, session: str, window: int) -> bool:
        if session in self.sessions and window in self.sessions[session]:
            del self.sessions[session][window]
            return True
        return False

    def kill_session(self, session: str) -> bool:
        if session in self.sessions:
            del self.sessions[session]
            return True
        return False

    def list_windows(self, session: str) -> List[Dict[str, Any]]:
        if session not in self.sessions:
            return []
        return [
            {'index': idx, 'name': f'window-{idx}', 'active': False}
            for idx in self.sessions[session].keys()
        ]

    def attach(self, session: str) -> None:
        pass  # No-op in tests

    def select_window(self, session: str, window: int) -> bool:
        """Select a window - no-op in tests, just return True."""
        return session in self.sessions


class MockFileSystem:
    """Mock implementation of FileSystemInterface for testing"""

    def __init__(self):
        self.files: Dict[str, Any] = {}  # path_str -> content
        self.dirs: set = set()

    def read_json(self, path: Path) -> Optional[Dict[str, Any]]:
        content = self.files.get(str(path))
        if content is None:
            return None
        if isinstance(content, dict):
            return content
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return None

    def write_json(self, path: Path, data: Dict[str, Any]) -> bool:
        self.files[str(path)] = data
        return True

    def exists(self, path: Path) -> bool:
        return str(path) in self.files or str(path) in self.dirs

    def mkdir(self, path: Path, parents: bool = True) -> bool:
        self.dirs.add(str(path))
        return True

    def read_text(self, path: Path) -> Optional[str]:
        content = self.files.get(str(path))
        if content is None:
            return None
        return str(content)

    def write_text(self, path: Path, content: str) -> bool:
        self.files[str(path)] = content
        return True


class MockSubprocess:
    """Mock implementation of SubprocessInterface for testing"""

    def __init__(self):
        self.commands: List[List[str]] = []  # Record of run commands
        self.responses: Dict[str, Dict[str, Any]] = {}  # cmd_key -> response

    def set_response(self, cmd_prefix: str, returncode: int = 0,
                     stdout: str = "", stderr: str = ""):
        """Set up a mock response for commands starting with prefix"""
        self.responses[cmd_prefix] = {
            'returncode': returncode,
            'stdout': stdout,
            'stderr': stderr
        }

    def run(self, cmd: List[str], timeout: Optional[int] = None,
            capture_output: bool = True) -> Optional[Dict[str, Any]]:
        self.commands.append(cmd)
        cmd_str = ' '.join(cmd)

        # Check for matching response
        for prefix, response in self.responses.items():
            if cmd_str.startswith(prefix):
                return response

        # Default response
        return {'returncode': 0, 'stdout': '', 'stderr': ''}

    def popen(self, cmd: List[str], cwd: Optional[str] = None) -> Any:
        self.commands.append(cmd)
        return None
