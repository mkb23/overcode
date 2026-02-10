"""
Protocol definitions for external dependencies.

These interfaces allow dependency injection for testing, enabling us to
swap real implementations (subprocess calls to tmux, file I/O) with
mock implementations in tests.
"""

from typing import Protocol, Optional, List, Dict, Any, Tuple, runtime_checkable, TYPE_CHECKING
from pathlib import Path

if TYPE_CHECKING:
    from .session_manager import Session


@runtime_checkable
class TmuxInterface(Protocol):
    """Interface for tmux operations"""

    def capture_pane(self, session: str, window: int, lines: int = 100) -> Optional[str]:
        """Capture content from a tmux pane.

        Args:
            session: tmux session name
            window: window number
            lines: number of lines to capture from scrollback

        Returns:
            Pane content as string, or None on failure
        """
        ...

    def send_keys(self, session: str, window: int, keys: str, enter: bool = True) -> bool:
        """Send keys to a tmux pane.

        Args:
            session: tmux session name
            window: window number
            keys: keys/text to send
            enter: whether to send Enter after keys

        Returns:
            True if successful, False otherwise
        """
        ...

    def has_session(self, session: str) -> bool:
        """Check if a tmux session exists."""
        ...

    def new_session(self, session: str) -> bool:
        """Create a new tmux session."""
        ...

    def new_window(self, session: str, name: str, command: Optional[List[str]] = None,
                   cwd: Optional[str] = None) -> Optional[int]:
        """Create a new window in a session.

        Returns:
            Window number if successful, None otherwise
        """
        ...

    def kill_window(self, session: str, window: int) -> bool:
        """Kill a tmux window."""
        ...

    def kill_session(self, session: str) -> bool:
        """Kill an entire tmux session."""
        ...

    def list_windows(self, session: str) -> List[Dict[str, Any]]:
        """List windows in a session.

        Returns:
            List of window info dicts with 'index', 'name', etc.
        """
        ...

    def attach(self, session: str) -> None:
        """Attach to a tmux session (replaces current process)."""
        ...

    def select_window(self, session: str, window: int) -> bool:
        """Select a window in a tmux session.

        Args:
            session: tmux session name
            window: window number to select

        Returns:
            True if successful, False otherwise
        """
        ...


@runtime_checkable
class StatusDetectorProtocol(Protocol):
    """Interface for status detection strategies.

    Both PollingStatusDetector and HookStatusDetector implement this protocol.
    Consumers can hold references typed as StatusDetectorProtocol to support
    runtime strategy selection (#5).
    """

    tmux_session: str

    def detect_status(self, session: "Session") -> Tuple[str, str, str]:
        """Detect session status and current activity.

        Returns:
            Tuple of (status, current_activity, pane_content)
        """
        ...

    def get_pane_content(self, window: int, num_lines: int = 50) -> Optional[str]:
        """Get the last N meaningful lines from a tmux pane."""
        ...


@runtime_checkable
class FileSystemInterface(Protocol):
    """Interface for file system operations"""

    def read_json(self, path: Path) -> Optional[Dict[str, Any]]:
        """Read and parse a JSON file.

        Returns:
            Parsed JSON data, or None if file doesn't exist/is invalid
        """
        ...

    def write_json(self, path: Path, data: Dict[str, Any]) -> bool:
        """Write data to a JSON file atomically.

        Returns:
            True if successful, False otherwise
        """
        ...

    def exists(self, path: Path) -> bool:
        """Check if a path exists."""
        ...

    def mkdir(self, path: Path, parents: bool = True) -> bool:
        """Create a directory."""
        ...

    def read_text(self, path: Path) -> Optional[str]:
        """Read text from a file."""
        ...

    def write_text(self, path: Path, content: str) -> bool:
        """Write text to a file."""
        ...


@runtime_checkable
class SubprocessInterface(Protocol):
    """Interface for subprocess operations (non-tmux)"""

    def run(self, cmd: List[str], timeout: Optional[int] = None,
            capture_output: bool = True) -> Optional[Dict[str, Any]]:
        """Run a subprocess command.

        Args:
            cmd: command and arguments
            timeout: timeout in seconds
            capture_output: whether to capture stdout/stderr

        Returns:
            Dict with 'returncode', 'stdout', 'stderr', or None on failure
        """
        ...

    def popen(self, cmd: List[str], cwd: Optional[str] = None) -> Any:
        """Start a subprocess without waiting.

        Returns:
            Process handle or None on failure
        """
        ...
