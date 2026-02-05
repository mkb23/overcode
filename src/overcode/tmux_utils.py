"""
Shared tmux utilities for Overcode.

This module provides shared tmux functions used by multiple components
(launcher, monitor daemon) to avoid code duplication.
"""

import os
import subprocess
import tempfile
import time
from typing import Optional


def send_text_to_tmux_window(
    tmux_session: str,
    window: int,
    text: str,
    send_enter: bool = True,
    startup_delay: float = 0.0,
) -> bool:
    """Send text to a tmux window using load-buffer/paste-buffer.

    This method handles multi-line text and special characters safely
    by using tmux's buffer mechanism instead of send-keys.

    Args:
        tmux_session: Name of the tmux session
        window: Window index within the session
        text: Text to send
        send_enter: Whether to press Enter after sending text (default: True)
        startup_delay: Seconds to wait before sending (default: 0)

    Returns:
        True if successful, False otherwise
    """
    if startup_delay > 0:
        time.sleep(startup_delay)

    # For large prompts, use tmux load-buffer/paste-buffer
    # to avoid escaping issues and line length limits
    lines = text.split('\n')
    batch_size = 10
    target = f"{tmux_session}:{window}"

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

            subprocess.run(['tmux', 'load-buffer', temp_path], timeout=5, check=True)
            subprocess.run([
                'tmux', 'paste-buffer', '-t', target
            ], timeout=5, check=True)
        except subprocess.SubprocessError as e:
            print(f"Failed to send text batch to tmux: {e}")
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
            subprocess.run([
                'tmux', 'send-keys', '-t', target,
                '', 'Enter'
            ], timeout=5, check=True)
        except subprocess.SubprocessError as e:
            print(f"Failed to send Enter to tmux: {e}")
            return False

    return True


def get_tmux_pane_content(
    tmux_session: str,
    window: int,
    lines: int = 50,
) -> Optional[str]:
    """Capture content from a tmux pane.

    Args:
        tmux_session: Name of the tmux session
        window: Window index within the session
        lines: Number of lines to capture (default: 50)

    Returns:
        Captured content as string, or None on error
    """
    try:
        result = subprocess.run(
            [
                "tmux", "capture-pane",
                "-t", f"{tmux_session}:{window}",
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
