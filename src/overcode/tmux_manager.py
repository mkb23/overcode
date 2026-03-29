"""
Tmux session and window management for Overcode.

Uses libtmux for reliable tmux interaction.
"""

import os
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


    def _get_window(self, window_name: str) -> Optional[libtmux.Window]:
        """Get a window by name.

        Falls back to index-based lookup for legacy digit-string values.
        """
        sess = self._get_session()
        if sess is None:
            return None
        try:
            return sess.windows.get(window_name=window_name)
        except (LibTmuxException, ObjectDoesNotExist):
            # Fallback: legacy sessions may have digit-string window indices
            if window_name.isdigit():
                try:
                    return sess.windows.get(window_index=window_name)
                except (LibTmuxException, ObjectDoesNotExist):
                    pass
            return None

    def _get_pane(self, window_name: str) -> Optional[libtmux.Pane]:
        """Get the first pane of a window."""
        win = self._get_window(window_name)
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

    def create_window(self, window_name: str, start_directory: Optional[str] = None) -> Optional[str]:
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
            # Prevent tmux from auto-renaming the window based on the
            # running process — we rely on stable window names for lookups.
            window.set_window_option('automatic-rename', 'off')
            return window.window_name
        except (LibTmuxException, ValueError):
            return None

    def create_ssh_proxy_window(
        self,
        window_name: str,
        ssh_target: str,
        remote_tmux_session: str,
        remote_window: str,
    ) -> Optional[str]:
        """Create a tmux window that SSH-attaches to a remote agent's tmux window.

        Uses ControlMaster multiplexing so subsequent connections to the same
        host are near-instant.

        Args:
            window_name: Local window name (e.g., "ssh:desktop:myagent")
            ssh_target: SSH target (e.g., "user@host")
            remote_tmux_session: Remote tmux session name
            remote_window: Remote tmux window name

        Returns:
            Window name on success, None on failure
        """
        if not self.ensure_session():
            return None

        try:
            sess = self._get_session()
            if sess is None:
                return None

            # Build the SSH command with ControlMaster for fast reconnection.
            # Remote windows are named "agent-name-uuid" but we may only
            # have "agent-name". Use grep to find the full window name,
            # then select + attach. (tmux prefix match "=name" requires
            # tmux 3.5+ which isn't universally available.)
            from .ssh_provisioner import SSH_CONTROL_OPTS
            import shlex

            ssh_opts_str = " ".join(shlex.quote(o) for o in SSH_CONTROL_OPTS)
            q_target = shlex.quote(ssh_target)

            # Create a linked session on the remote so our proxy has an
            # independent window selection — the remote's own TUI/daemon
            # also calls select-window on the agents session, which would
            # hijack our view if we attached directly.
            # The linked session auto-destroys via a client-detached hook
            # (destroy-unattached can't be used because it fires before
            # the first attach).
            ssh_cmd_str = (
                f"ssh -t {ssh_opts_str} {q_target} "
                f"'W=$(tmux list-windows -t {remote_tmux_session}"
                ' -F "#{window_name}"'
                f" | grep -m1 ^{remote_window});"
                f" LS=oc-proxy-$W;"
                # Create linked session sharing the agents window group
                f" tmux new-session -d -s $LS -t {remote_tmux_session} 2>/dev/null;"
                # Select our target window in the linked session
                f" tmux select-window -t $LS:$W;"
                # Strip status bar, generous scrollback
                f" tmux set -t $LS status off;"
                f" tmux set -t $LS history-limit 50000;"
                # Force resize to match our SSH terminal size
                f" tmux set -t $LS window-size latest;"
                f" tmux resize-window -t $LS -A 2>/dev/null;"
                # Auto-destroy when SSH disconnects
                f' tmux set-hook -t $LS client-detached "kill-session -t $LS";'
                # Attach to the isolated linked session
                f" tmux attach-session -t $LS'"
            )

            window = sess.new_window(
                window_name=window_name,
                attach=False,
                window_shell=ssh_cmd_str,
            )
            window.set_window_option('automatic-rename', 'off')
            return window.window_name
        except (LibTmuxException, ValueError) as e:
            import logging
            logging.getLogger(__name__).debug(
                "Failed to create SSH proxy window %s: %s", window_name, e
            )
            return None

    def send_keys(self, window_name: str, keys: str, enter: bool = True) -> bool:
        """Send keys to a tmux window.

        For Claude Code: text and Enter must be sent as SEPARATE commands
        with a small delay, otherwise Claude Code doesn't process the Enter.
        """
        if self._tmux:
            return self._tmux.send_keys(self.session_name, window_name, keys, enter)

        try:
            pane = self._get_pane(window_name)
            if pane is None:
                return False

            from .tmux_utils import send_keys_to_pane
            send_keys_to_pane(pane, keys, enter=enter)
            return True
        except LibTmuxException:
            return False

    def attach_session(self, window: Optional[str] = None, bare: bool = False):
        """Attach to the tmux session (blocking).

        Args:
            window: optional window name to target
            bare: if True, create a linked session with tmux chrome stripped,
                  isolated from other clients attached to the main session
        """
        if self._tmux:
            self._tmux.attach(self.session_name, window=window, bare=bare)
            return
        if bare:
            self._attach_bare(window)
        else:
            from .tmux_utils import tmux_window_target
            target = tmux_window_target(self.session_name, window) if window is not None else self.session_name
            os.execlp("tmux", "tmux", "attach-session", "-t", target)

    def _attach_bare(self, window: str):
        """Create a linked session with stripped chrome and attach to it."""
        from .tmux_utils import attach_bare
        attach_bare(self.session_name, window, socket_path=self.socket)

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

    def kill_window(self, window_name: str) -> bool:
        """Kill a specific window"""
        if self._tmux:
            return self._tmux.kill_window(self.session_name, window_name)

        try:
            win = self._get_window(window_name)
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

    def get_pane_pid(self, window_name: str) -> Optional[int]:
        """Get the PID of the shell process in a window's first pane.

        Returns None if the window doesn't exist.
        """
        if self._tmux:
            return self._tmux.get_pane_pid(self.session_name, window_name)

        pane = self._get_pane(window_name)
        if pane is None:
            return None
        try:
            return int(pane.pane_pid)
        except (ValueError, TypeError):
            return None

    def window_exists(self, window_name: str) -> bool:
        """Check if a specific window exists.

        Falls back to index-based lookup for legacy digit-string values
        (handled by _get_window).
        """
        if self._tmux:
            windows = self._tmux.list_windows(self.session_name)
            if any(w.get('name') == window_name for w in windows):
                return True
            # Fallback: legacy digit-string index
            if window_name.isdigit():
                return any(str(w.get('index')) == window_name for w in windows)
            return False

        return self._get_window(window_name) is not None
