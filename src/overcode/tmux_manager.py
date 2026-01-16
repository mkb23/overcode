"""
Tmux session and window management for Overcode.
"""

import os
import subprocess
from typing import Optional, List, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .interfaces import TmuxInterface


class TmuxManager:
    """Manages tmux sessions and windows for Overcode.

    This class can be used directly (uses subprocess) or with an injected
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
        self._tmux = tmux  # If None, use direct subprocess calls
        # Support OVERCODE_TMUX_SOCKET env var for testing
        self.socket = socket or os.environ.get("OVERCODE_TMUX_SOCKET")

    def _tmux_cmd(self, *args) -> List[str]:
        """Build tmux command with optional socket."""
        cmd = ["tmux"]
        if self.socket:
            cmd.extend(["-L", self.socket])
        cmd.extend(args)
        return cmd

    def ensure_session(self) -> bool:
        """Create tmux session if it doesn't exist"""
        if self.session_exists():
            return True

        if self._tmux:
            return self._tmux.new_session(self.session_name)

        try:
            subprocess.run(
                self._tmux_cmd("new-session", "-d", "-s", self.session_name),
                check=True
            )
            return True
        except subprocess.CalledProcessError:
            return False

    def session_exists(self) -> bool:
        """Check if the tmux session exists"""
        if self._tmux:
            return self._tmux.has_session(self.session_name)

        result = subprocess.run(
            self._tmux_cmd("has-session", "-t", self.session_name),
            capture_output=True
        )
        return result.returncode == 0

    def create_window(self, window_name: str, start_directory: Optional[str] = None) -> Optional[int]:
        """Create a new window in the tmux session"""
        if not self.ensure_session():
            return None

        if self._tmux:
            return self._tmux.new_window(self.session_name, window_name, cwd=start_directory)

        args = [
            "new-window",
            "-t", self.session_name,
            "-n", window_name,
            "-P",  # print window info
            "-F", "#{window_index}"
        ]

        if start_directory:
            args.extend(["-c", start_directory])

        try:
            result = subprocess.run(self._tmux_cmd(*args), capture_output=True, text=True, check=True)
            return int(result.stdout.strip())
        except (subprocess.CalledProcessError, ValueError):
            return None

    def send_keys(self, window_index: int, keys: str, enter: bool = True) -> bool:
        """Send keys to a tmux window.

        For Claude Code: text and Enter must be sent as SEPARATE commands
        with a small delay, otherwise Claude Code doesn't process the Enter.
        """
        import time

        if self._tmux:
            return self._tmux.send_keys(self.session_name, window_index, keys, enter)

        target = f"{self.session_name}:{window_index}"

        try:
            # Send text first (if any)
            if keys:
                subprocess.run(
                    self._tmux_cmd("send-keys", "-t", target, keys),
                    check=True
                )
                # Small delay for Claude Code to process text
                time.sleep(0.1)

            # Send Enter separately
            if enter:
                subprocess.run(
                    self._tmux_cmd("send-keys", "-t", target, "Enter"),
                    check=True
                )
            return True
        except subprocess.CalledProcessError:
            return False

    def attach_session(self):
        """Attach to the tmux session (blocking)"""
        if self._tmux:
            self._tmux.attach(self.session_name)
            return
        subprocess.run(self._tmux_cmd("attach", "-t", self.session_name))

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
            result = subprocess.run(
                self._tmux_cmd(
                    "list-windows",
                    "-t", self.session_name,
                    "-F", "#{window_index}|#{window_name}|#{pane_current_command}"
                ),
                capture_output=True, text=True, check=True
            )

            windows = []
            for line in result.stdout.strip().split("\n"):
                if line:
                    parts = line.split("|")
                    if len(parts) >= 3:
                        try:
                            window_index = int(parts[0])
                        except ValueError:
                            window_index = 0
                        windows.append({
                            "index": window_index,
                            "name": parts[1],
                            "command": parts[2]
                        })
            return windows
        except subprocess.CalledProcessError:
            return []

    def kill_window(self, window_index: int) -> bool:
        """Kill a specific window"""
        if self._tmux:
            return self._tmux.kill_window(self.session_name, window_index)

        try:
            subprocess.run(
                self._tmux_cmd("kill-window", "-t", f"{self.session_name}:{window_index}"),
                check=True
            )
            return True
        except subprocess.CalledProcessError:
            return False

    def kill_session(self) -> bool:
        """Kill the entire tmux session"""
        if self._tmux:
            return self._tmux.kill_session(self.session_name)

        try:
            subprocess.run(
                self._tmux_cmd("kill-session", "-t", self.session_name),
                check=True
            )
            return True
        except subprocess.CalledProcessError:
            return False

    def window_exists(self, window_index: int) -> bool:
        """Check if a specific window exists"""
        if not self.session_exists():
            return False

        if self._tmux:
            windows = self._tmux.list_windows(self.session_name)
            return any(w.get('index') == window_index for w in windows)

        try:
            result = subprocess.run(
                self._tmux_cmd(
                    "list-windows",
                    "-t", self.session_name,
                    "-F", "#{window_index}"
                ),
                capture_output=True, text=True, check=True
            )

            window_indices = [int(idx.strip()) for idx in result.stdout.strip().split("\n") if idx.strip()]
            return window_index in window_indices
        except (subprocess.CalledProcessError, ValueError):
            return False
