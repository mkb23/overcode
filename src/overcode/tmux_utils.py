"""
Shared tmux utilities for Overcode.

This module provides shared tmux functions used by multiple components
(launcher, monitor daemon) to avoid code duplication.
"""

import logging
import os
import subprocess
import tempfile
import time
from typing import List, Optional

logger = logging.getLogger(__name__)


def _build_tmux_cmd() -> List[str]:
    """Build base tmux command, respecting OVERCODE_TMUX_SOCKET env var."""
    socket = os.environ.get("OVERCODE_TMUX_SOCKET")
    return ["tmux", "-L", socket] if socket else ["tmux"]


def send_keys_to_pane(pane, keys: str, enter: bool = True) -> None:
    """Send keys to a tmux pane with special-case handling for ! and / prefixes.

    For Claude Code: text and Enter must be sent as SEPARATE commands
    with a small delay, otherwise Claude Code doesn't process the Enter.

    Args:
        pane: A libtmux Pane object
        keys: Text to send
        enter: Whether to press Enter after sending text
    """
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
        elif keys.startswith('/') and len(keys) > 1:
            # Send slash commands as one literal string so the full text
            # lands in the input buffer before the autocomplete menu can
            # interfere.  A 0.5s delay before Enter lets Claude Code
            # process the text and match the correct command.
            pane.send_keys(keys, enter=False, literal=True)
            time.sleep(0.5)
        else:
            pane.send_keys(keys, enter=False)
            # Small delay for Claude Code to process text
            time.sleep(0.1)

    if enter:
        pane.send_keys('', enter=True)


def attach_bare(session_name: str, window_name: str, socket_path: str = None) -> None:
    """Attach to a tmux window in bare mode (no chrome).

    Creates a linked session sharing the same window group, strips the
    status bar and mouse, and selects the target window before attaching.
    Uses a client-attached hook to defer destroy-unattached (setting it
    on a detached session would kill it immediately).
    """
    bare_session = f"bare-{session_name}-{window_name}"

    tmux_cmd = ["tmux"]
    if socket_path:
        tmux_cmd = ["tmux", "-L", socket_path]

    # Kill any stale bare session with the same name
    subprocess.run(
        tmux_cmd + ["kill-session", "-t", bare_session],
        capture_output=True,
    )

    # Create linked session sharing the same window group
    result = subprocess.run(
        tmux_cmd + ["new-session", "-d", "-s", bare_session, "-t", session_name],
        capture_output=True,
    )
    if result.returncode != 0:
        return

    # Configure the linked session (isolated from main session)
    target = tmux_window_target(bare_session, window_name)
    for cmd in [
        tmux_cmd + ["set", "-t", bare_session, "status", "off"],
        tmux_cmd + ["set", "-t", bare_session, "mouse", "off"],
        tmux_cmd + ["set-hook", "-t", bare_session, "client-attached",
         "set destroy-unattached on"],
        tmux_cmd + ["select-window", "-t", target],
    ]:
        subprocess.run(cmd, capture_output=True)

    # Attach (replaces process)
    os.execlp(tmux_cmd[0], *tmux_cmd, "attach-session", "-t", bare_session)


_pane_base_index: Optional[int] = None


def get_pane_base_index() -> int:
    """Return the tmux pane-base-index setting (default 0, commonly set to 1).

    The result is cached for the lifetime of the process.
    """
    global _pane_base_index
    if _pane_base_index is not None:
        return _pane_base_index
    try:
        result = subprocess.run(
            _build_tmux_cmd() + ["show-options", "-gv", "pane-base-index"],
            capture_output=True, text=True, timeout=5,
        )
        _pane_base_index = int(result.stdout.strip()) if result.returncode == 0 and result.stdout.strip() else 0
    except (subprocess.TimeoutExpired, ValueError, OSError):
        _pane_base_index = 0
    return _pane_base_index


def tmux_window_target(session: str, window) -> str:
    """Build tmux target string for a window.

    For name-based windows (new style), uses `session:=name` (exact name match).
    For legacy digit-string/int windows (e.g. "4" or 4), uses `session:4` (index match).
    """
    window = str(window)
    if window.isdigit():
        return f"{session}:{window}"
    return f"{session}:={window}"


def send_text_to_tmux_window(
    tmux_session: str,
    window: str,
    text: str,
    send_enter: bool = True,
    startup_delay: float = 0.0,
) -> bool:
    """Send text to a tmux window using load-buffer/paste-buffer.

    This method handles multi-line text and special characters safely
    by using tmux's buffer mechanism instead of send-keys.

    Args:
        tmux_session: Name of the tmux session
        window: Window name within the session
        text: Text to send
        send_enter: Whether to press Enter after sending text (default: True)
        startup_delay: Seconds to wait before sending (default: 0)

    Returns:
        True if successful, False otherwise
    """
    if startup_delay > 0:
        time.sleep(startup_delay)

    tmux_cmd = _build_tmux_cmd()

    # For large prompts, use tmux load-buffer/paste-buffer
    # to avoid escaping issues and line length limits
    lines = text.split('\n')
    batch_size = 10
    target = tmux_window_target(tmux_session, window)

    for i in range(0, len(lines), batch_size):
        batch = lines[i:i + batch_size]
        batch_text = '\n'.join(batch)
        if i + batch_size < len(lines):
            batch_text += '\n'  # Add newline between batches

        # Use tempfile for the buffer
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
                temp_path = f.name
                f.write(batch_text)

            subprocess.run(tmux_cmd + ['load-buffer', temp_path], timeout=5, check=True)
            subprocess.run(tmux_cmd + [
                'paste-buffer', '-t', target
            ], timeout=5, check=True)
        except subprocess.SubprocessError as e:
            logger.warning("Failed to send text batch to tmux: %s", e)
            return False
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

        time.sleep(0.1)

    # Send Enter to submit if requested
    if send_enter:
        try:
            subprocess.run(tmux_cmd + [
                'send-keys', '-t', target,
                '', 'Enter'
            ], timeout=5, check=True)
        except subprocess.SubprocessError as e:
            logger.warning("Failed to send Enter to tmux: %s", e)
            return False

    return True


def get_tmux_pane_content(
    tmux_session: str,
    window: str,
    lines: int = 50,
) -> Optional[str]:
    """Capture content from a tmux pane.

    Args:
        tmux_session: Name of the tmux session
        window: Window name within the session
        lines: Number of lines to capture (default: 50)

    Returns:
        Captured content as string, or None on error
    """
    tmux_cmd = _build_tmux_cmd()

    try:
        result = subprocess.run(
            tmux_cmd + [
                "capture-pane",
                "-t", tmux_window_target(tmux_session, window),
                "-p",  # Print to stdout
                "-S", f"-{lines}",  # Capture last N lines
            ],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return result.stdout.rstrip()
        return None
    except subprocess.SubprocessError:
        return None
