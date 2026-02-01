"""tmux session management for TUI testing."""

import time
from typing import Optional
import libtmux


class TUIDriver:
    """Manages a tmux session for testing TUI applications.

    This class provides a simple interface to:
    - Start a TUI app in a tmux session with controlled dimensions
    - Send keystrokes to the app
    - Capture the screen content (with ANSI codes)
    - Wait for specific content to appear
    - Clean up the session
    """

    def __init__(
        self,
        session_name: str = "tui-eye",
        socket_name: Optional[str] = None,
    ):
        """Initialize the TUI driver.

        Args:
            session_name: Name for the tmux session
            socket_name: Optional tmux socket name (for isolation)
        """
        self.session_name = session_name
        self.socket_name = socket_name
        self.server: Optional[libtmux.Server] = None
        self.session: Optional[libtmux.Session] = None

    def start(
        self,
        command: str,
        width: int = 120,
        height: int = 40,
        env: Optional[dict[str, str]] = None,
    ) -> None:
        """Start a TUI application in a new tmux session.

        Args:
            command: The command to run (e.g., "overcode supervisor")
            width: Terminal width in characters
            height: Terminal height in characters
            env: Optional environment variables to set
        """
        # Kill any existing session with this name
        self.stop()

        # Create server connection
        if self.socket_name:
            self.server = libtmux.Server(socket_name=self.socket_name)
        else:
            self.server = libtmux.Server()

        # Create new session
        self.session = self.server.new_session(
            session_name=self.session_name,
            window_command=command,
            x=width,
            y=height,
            environment=env,
        )

    def send_keys(self, *keys: str, enter: bool = False) -> None:
        """Send keystrokes to the TUI.

        Args:
            keys: Key names to send (e.g., "j", "k", "Enter", "escape")
            enter: Whether to send Enter after all keys
        """
        if not self.session:
            self.connect()
        if not self.session:
            raise RuntimeError("No active session. Call start() first.")

        pane = self.session.active_window.active_pane

        for key in keys:
            # Map common key names
            key_map = {
                "enter": "Enter",
                "escape": "Escape",
                "esc": "Escape",
                "tab": "Tab",
                "space": "Space",
                "up": "Up",
                "down": "Down",
                "left": "Left",
                "right": "Right",
                "backspace": "BSpace",
            }
            mapped_key = key_map.get(key.lower(), key)
            pane.send_keys(mapped_key, enter=False)

        if enter:
            pane.send_keys("Enter", enter=False)

    def capture(self, with_ansi: bool = True) -> str:
        """Capture the current screen content.

        Args:
            with_ansi: Whether to include ANSI escape codes

        Returns:
            The screen content as a string
        """
        if not self.session:
            self.connect()
        if not self.session:
            raise RuntimeError("No active session. Call start() first.")

        pane = self.session.active_window.active_pane

        # Use capture_pane with escape sequences if requested
        if with_ansi:
            # -e preserves ANSI escape sequences
            # -p prints to stdout
            content = pane.cmd("capture-pane", "-e", "-p").stdout
        else:
            content = pane.cmd("capture-pane", "-p").stdout

        return "\n".join(content) if isinstance(content, list) else content

    def wait_for(
        self,
        text: str,
        timeout: float = 10.0,
        poll_interval: float = 0.2,
    ) -> bool:
        """Wait for specific text to appear on screen.

        Args:
            text: The text to wait for
            timeout: Maximum time to wait in seconds
            poll_interval: Time between checks in seconds

        Returns:
            True if text was found, False if timeout occurred
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            content = self.capture(with_ansi=False)
            if text in content:
                return True
            time.sleep(poll_interval)

        return False

    def stop(self) -> None:
        """Stop and clean up the tmux session."""
        if self.session:
            try:
                self.session.kill()
            except Exception:
                pass
            self.session = None

        # Always try to kill by name, connecting first if needed
        try:
            if not self.server:
                if self.socket_name:
                    self.server = libtmux.Server(socket_name=self.socket_name)
                else:
                    self.server = libtmux.Server()

            for session in self.server.sessions:
                if session.name == self.session_name:
                    session.kill()
                    break
        except Exception:
            pass

    def __enter__(self) -> "TUIDriver":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - ensures cleanup."""
        self.stop()

    def connect(self) -> bool:
        """Connect to an existing tmux session.

        Returns:
            True if successfully connected, False if session doesn't exist
        """
        if self.socket_name:
            self.server = libtmux.Server(socket_name=self.socket_name)
        else:
            self.server = libtmux.Server()

        try:
            for session in self.server.sessions:
                if session.name == self.session_name:
                    self.session = session
                    return True
        except Exception:
            pass

        return False

    @property
    def is_running(self) -> bool:
        """Check if the session is still running."""
        # First try to connect if we don't have a session reference
        if not self.session:
            self.connect()

        if not self.session:
            return False

        try:
            # Try to access the session to verify it exists
            _ = self.session.name
            return True
        except Exception:
            self.session = None
            return False
