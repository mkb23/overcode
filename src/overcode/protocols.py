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
    from .tmux_utils import PaneInfo


class TmuxTimeoutError(Exception):
    """The tmux server did not answer a read within the client's timeout.

    Only a tmux client built with a command timeout raises it (the monitor
    daemon's: ``RealTmux(command_timeout=...)``). It is raised, not turned
    into None/False/[], from the reads whose empty answer means "gone"
    (``capture_pane``, ``has_session``, ``list_windows``, ``get_pane_pid``),
    so a wedged server reads as "no answer this tick", never as a dead
    agent. ``list_panes`` returns its documented no-answer None instead.
    """


@runtime_checkable
class TmuxInterface(Protocol):
    """Interface for tmux operations.

    Reads may raise ``TmuxTimeoutError`` when the client has a command
    timeout and the server does not answer in time.
    """

    def capture_pane(self, session: str, window: str, lines: int = 100) -> Optional[str]:
        """Capture content from a tmux pane.

        Args:
            session: tmux session name
            window: window name
            lines: number of lines to capture from scrollback

        Returns:
            Pane content as string, or None on failure

        Raises:
            TmuxTimeoutError: the server did not answer in time (timed
                clients only)
        """
        ...

    def send_keys(self, session: str, window: str, keys: str, enter: bool = True) -> bool:
        """Send keys to a tmux pane.

        Args:
            session: tmux session name
            window: window name
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
                   cwd: Optional[str] = None) -> Optional[str]:
        """Create a new window in a session.

        Returns:
            Window name if successful, None otherwise
        """
        ...

    def kill_window(self, session: str, window: str) -> bool:
        """Kill a tmux window."""
        ...

    def rename_window(self, session: str, window: str, new_name: str) -> bool:
        """Rename a tmux window.

        Returns True if the window was renamed, False otherwise.
        """
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

    def attach(self, session: str, window: Optional[str] = None, bare: bool = False) -> None:
        """Attach to a tmux session (replaces current process).

        Args:
            session: tmux session name
            window: optional window name to target
            bare: if True, strip tmux chrome (no status bar, no prefix, mouse passthrough)
        """
        ...

    def get_pane_pid(self, session: str, window: str) -> Optional[int]:
        """Get the PID of the shell process in a window's first pane.

        Returns:
            PID as int, or None if window doesn't exist
        """
        ...

    def list_panes(self, session: str) -> Optional[Dict[str, "PaneInfo"]]:
        """Every window's first pane in a session, from ONE tmux command.

        Returns:
            {window_name: tmux_utils.PaneInfo} (pid, change signature,
            attached-client count), or None when tmux or the session is
            unavailable, or the server did not answer in time
        """
        ...

    def select_window(self, session: str, window: str) -> bool:
        """Select a window in a tmux session.

        Args:
            session: tmux session name
            window: window name to select

        Returns:
            True if successful, False otherwise
        """
        ...

    def ensure_empty_placeholder_window(self, session: str, window_name: str, message: str) -> bool:
        """Create a static placeholder window if it doesn't already exist (#457).

        The placeholder is a tmux window that runs a long-lived no-op process
        showing ``message``. The TUI selects it when it cannot select the
        intended agent window — this guarantees the bottom pane always reflects
        the focused agent's state instead of silently lingering on the
        previous agent.

        Idempotent: returns True if the window already exists or was created.
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
    capture_lines: int

    def detect_status(self, session: "Session", num_lines: int = 0) -> Tuple[str, str, str]:
        """Detect session status and current activity.

        Returns:
            Tuple of (status, current_activity, pane_content)
        """
        ...

    def get_pane_content(self, window: str, num_lines: int = 0) -> Optional[str]:
        """Get the last N meaningful lines from a tmux pane."""
        ...


@runtime_checkable
class FileSystemInterface(Protocol):
    """Interface for file system operations"""

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
