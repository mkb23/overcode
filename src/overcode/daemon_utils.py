"""
Shared utilities for Overcode daemons.

Provides factory functions for creating daemon PID management helpers,
avoiding code duplication between monitor_daemon and supervisor_daemon.
"""

import os
import signal
from pathlib import Path
from typing import Callable, Optional, Tuple

from .pid_utils import (
    get_process_pid,
    is_process_running,
    remove_pid_file,
)
from .settings import DAEMON


def create_daemon_helpers(
    get_pid_path: Callable[[str], Path],
    daemon_name: str,
) -> Tuple[
    Callable[[Optional[str]], bool],
    Callable[[Optional[str]], Optional[int]],
    Callable[[Optional[str]], bool],
]:
    """Factory to create is_*_running, get_*_pid, stop_* functions for a daemon.

    Args:
        get_pid_path: Function that takes session name and returns PID file path
        daemon_name: Human-readable name for error messages

    Returns:
        Tuple of (is_running_fn, get_pid_fn, stop_fn)
    """

    def is_running(session: str = None) -> bool:
        """Check if the daemon process is currently running for a session.

        Args:
            session: tmux session name (default: from config)
        """
        if session is None:
            session = DAEMON.default_tmux_session
        return is_process_running(get_pid_path(session))

    def get_pid(session: str = None) -> Optional[int]:
        """Get the daemon PID if running, None otherwise.

        Args:
            session: tmux session name (default: from config)
        """
        if session is None:
            session = DAEMON.default_tmux_session
        return get_process_pid(get_pid_path(session))

    def stop(session: str = None) -> bool:
        """Stop the daemon process if running.

        Args:
            session: tmux session name (default: from config)

        Returns:
            True if daemon was stopped, False if it wasn't running.
        """
        if session is None:
            session = DAEMON.default_tmux_session
        pid_path = get_pid_path(session)
        pid = get_process_pid(pid_path)
        if pid is None:
            remove_pid_file(pid_path)
            return False

        try:
            os.kill(pid, signal.SIGTERM)
            remove_pid_file(pid_path)
            return True
        except (OSError, ProcessLookupError):
            remove_pid_file(pid_path)
            return False

    return is_running, get_pid, stop
