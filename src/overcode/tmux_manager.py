"""
Tmux session and window management for Overcode.

``TmuxManager`` is one tmux session's view of a ``TmuxInterface`` client:
the session name is bound once, so callers pass only window names. The
default client is a ``RealTmux`` with a command timeout, so every call
here is bounded: a wedged tmux server costs the launcher, the TUI, the
supervisor or the web API a few seconds, never a hang. Reads whose empty
answer would mean "gone" (``session_exists``, ``window_exists``,
``list_windows``, ``get_pane_pid``, ``capture_pane``) raise
``TmuxTimeoutError`` on a timeout rather than report the agent gone;
writes return their failure value.
"""

import os
import shlex
from typing import Any, Callable, Dict, List, Optional

from .implementations import TMUX_COMMAND_TIMEOUT_SECONDS, RealTmux
from .protocols import TmuxInterface

# Window name reserved for the dead-window placeholder (#457). Lives on the
# agents tmux session so the TUI can fall back to it when select_window
# fails for a missing/terminated agent window. Lowercase + dashed so it's
# clearly distinct from agent windows in tmux's window list. Defined in
# tmux_utils beside the other overcode-owned window names so the
# untracked-window checks skip it; re-exported here for existing callers.
from .tmux_utils import EMPTY_PLACEHOLDER_WINDOW  # noqa: F401


class TmuxManager:
    """Manages one tmux session's windows for Overcode."""

    def __init__(self, session_name: str = "agents", tmux: Optional[TmuxInterface] = None,
                 socket: Optional[str] = None):
        """Initialize the tmux manager.

        Args:
            session_name: Name of the tmux session to manage
            tmux: The tmux client (a test double in tests). Default: a
                ``RealTmux`` on ``socket`` with a command timeout and no
                object cache (this client creates and kills windows).
            socket: tmux socket name for the default client (default:
                ``OVERCODE_TMUX_SOCKET``)
        """
        self.session_name = session_name
        self.socket = socket or os.environ.get("OVERCODE_TMUX_SOCKET")
        if tmux is None:
            tmux = RealTmux(
                socket_name=self.socket,
                command_timeout=TMUX_COMMAND_TIMEOUT_SECONDS,
                cache_ttl=0,
            )
        self.client: TmuxInterface = tmux

    def ensure_session(self) -> bool:
        """Create the tmux session if it doesn't exist."""
        return self.session_exists() or self.client.new_session(self.session_name)

    def session_exists(self) -> bool:
        """Check if the tmux session exists."""
        return self.client.has_session(self.session_name)

    def create_window(self, window_name: str, start_directory: Optional[str] = None) -> Optional[str]:
        """Create a shell window, ready to be typed into; its name, or None.

        Returns once the shell has drawn its prompt (see
        ``RealTmux._wait_for_prompt``), so a long launch command sent next
        is not cut off.
        """
        if not self.ensure_session():
            return None
        return self.client.new_window(
            self.session_name, window_name, cwd=start_directory, wait_for_prompt=True,
        )

    def create_ssh_proxy_window(
        self,
        window_name: str,
        ssh_target: str,
        remote_tmux_session: str,
        remote_window: str,
    ) -> Optional[str]:
        """Create a tmux window that SSH-attaches to a remote agent's tmux window.

        Uses ControlMaster multiplexing so subsequent connections to the same
        host are near-instant. The window is tagged ``@is_ssh_proxy`` so the
        keybindings forward scrollback to the remote tmux.

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
        return self.client.new_window(
            self.session_name,
            window_name,
            command=[ssh_proxy_command(ssh_target, remote_tmux_session, remote_window)],
            options={'@is_ssh_proxy': 'on'},
        )

    def send_keys(self, window_name: str, keys: str, enter: bool = True) -> bool:
        """Send keys to a tmux window.

        For Claude Code: text and Enter must be sent as SEPARATE commands
        with a small delay, otherwise Claude Code doesn't process the Enter.
        """
        return self.client.send_keys(self.session_name, window_name, keys, enter)

    def capture_pane(self, window_name: str, lines: int = 100) -> Optional[str]:
        """The window's last ``lines`` lines (with escape sequences), or None."""
        return self.client.capture_pane(self.session_name, window_name, lines=lines)

    def attach_session(self, window: Optional[str] = None, bare: bool = False):
        """Attach to the tmux session (replaces this process).

        Args:
            window: optional window name to target
            bare: if True, create a linked session with tmux chrome stripped,
                  isolated from other clients attached to the main session
        """
        self.client.attach(self.session_name, window=window, bare=bare)

    def list_windows(self) -> List[Dict[str, Any]]:
        """The session's windows: dicts with 'index' (int) and 'name' (str).

        Empty when the session does not exist. One tmux round-trip.
        """
        return self.client.list_windows(self.session_name)

    def rename_window(self, window_name: str, new_name: str) -> bool:
        """Rename a window; False when tmux refused (see ``rename_tmux_window``).

        tmux's ``rename-window`` also turns ``automatic-rename`` off for the
        window, so the new name sticks.
        """
        return self.client.rename_window(self.session_name, window_name, new_name)

    def kill_window(self, window_name: str) -> bool:
        """Kill a specific window."""
        return self.client.kill_window(self.session_name, window_name)

    def kill_windows(self, match: Callable[[str], bool]) -> List[str]:
        """Kill every window whose name ``match`` accepts (duplicates too)."""
        return self.client.kill_windows(self.session_name, match)

    def kill_session(self) -> bool:
        """Kill the entire tmux session."""
        return self.client.kill_session(self.session_name)

    def get_pane_pid(self, window_name: str) -> Optional[int]:
        """The PID of the shell in a window's first pane; None if no window."""
        return self.client.get_pane_pid(self.session_name, window_name)

    def window_exists(self, window_name: str) -> bool:
        """Check if a specific window exists.

        Falls back to index-based lookup for legacy digit-string values.
        """
        windows = self.list_windows()
        if any(w.get('name') == window_name for w in windows):
            return True
        if window_name.isdigit():
            return any(str(w.get('index')) == window_name for w in windows)
        return False


def ssh_proxy_command(ssh_target: str, remote_tmux_session: str, remote_window: str) -> str:
    """The shell command of an SSH proxy window (see ``create_ssh_proxy_window``).

    Remote windows are named "agent-name-uuid" but we may only have
    "agent-name", so grep finds the full window name, then select + attach
    (tmux's "=name" prefix match needs tmux 3.5+, not universally available).

    The proxy attaches to a linked session on the remote, so it has its own
    window selection: the remote's own TUI/daemon also selects windows on
    the agents session, which would hijack the view if we attached
    directly. The linked session destroys itself via a client-detached
    hook (destroy-unattached fires before the first attach).
    """
    from .ssh_provisioner import SSH_CONTROL_OPTS

    ssh_opts_str = " ".join(shlex.quote(o) for o in SSH_CONTROL_OPTS)
    q_target = shlex.quote(ssh_target)
    return (
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
        # Smooth scroll: rebind PPage/NPage in copy-mode to scroll 3 lines
        # instead of a full page, so mouse wheel forwarded from the local
        # TUI feels natural.
        ' tmux bind-key -T copy-mode-vi PPage send-keys -X -N 3 scroll-up;'
        ' tmux bind-key -T copy-mode-vi NPage send-keys -X -N 3 scroll-down;'
        ' tmux bind-key -T copy-mode PPage send-keys -X -N 3 scroll-up;'
        ' tmux bind-key -T copy-mode NPage send-keys -X -N 3 scroll-down;'
        # Auto-destroy when SSH disconnects
        f' tmux set-hook -t $LS client-detached "kill-session -t $LS";'
        # Attach to the isolated linked session
        f" tmux attach-session -t $LS'"
    )
