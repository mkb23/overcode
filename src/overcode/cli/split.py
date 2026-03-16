"""
`overcode tmux` — tmux-native split layout.

Creates (or re-attaches to) a tmux window with two panes:
  - Top pane: overcode monitor (tree mode, sync auto-enabled)
  - Bottom pane: linked tmux session showing the focused agent's terminal

The TUI's tmux_sync feature drives window switching in the bottom pane.
Ctrl+] toggles focus between the nav (top) and terminal (bottom) panes.

Idempotent: running `overcode tmux` again will switch to the existing
split window rather than creating a duplicate.
"""

import os
import subprocess
import time
from typing import Annotated

import typer

from ._shared import app, SessionOption


def _find_overcode_cmd() -> str:
    """Find the overcode command to use in spawned tmux panes.

    Locates the `overcode` binary in the same venv as this package.
    Falls back to `overcode` on PATH.
    """
    import shutil
    from pathlib import Path

    # Find the venv bin dir: walk up from this file to find bin/overcode
    pkg_file = Path(__file__).resolve()
    for ancestor in pkg_file.parents:
        candidate = ancestor / "bin" / "overcode"
        if candidate.is_file():
            return str(candidate)
        # Also check .venv/bin/overcode (for src-layout projects)
        candidate = ancestor / ".venv" / "bin" / "overcode"
        if candidate.is_file():
            return str(candidate)

    # Fallback: whatever is on PATH
    return shutil.which("overcode") or "overcode"


SPLIT_WINDOW_NAME = "overcode-tmux"
LINKED_SESSION_PREFIX = "oc-view"


def _linked_session_name(agents_session: str) -> str:
    """Name for the linked session used by the bottom pane."""
    return f"{LINKED_SESSION_PREFIX}-{agents_session}"


def _tmux(*args: str, capture: bool = True) -> subprocess.CompletedProcess:
    """Run a tmux command."""
    return subprocess.run(
        ["tmux", *args],
        capture_output=capture,
        text=True,
    )


def _tmux_check(*args: str) -> bool:
    """Run a tmux command and return whether it succeeded."""
    return _tmux(*args).returncode == 0


def _tmux_output(*args: str) -> str:
    """Run a tmux command and return stdout, stripped."""
    return _tmux(*args).stdout.strip()


def _setup_linked_session(agents_session: str) -> str:
    """Create (or reuse) a linked session for the bottom pane.

    Returns the linked session name.
    """
    linked = _linked_session_name(agents_session)

    # Check if linked session already exists
    if _tmux_check("has-session", "-t", linked):
        return linked

    # Create linked session sharing the agents window group
    result = _tmux(
        "new-session", "-d", "-s", linked, "-t", agents_session,
    )
    if result.returncode != 0:
        raise typer.Exit(1)

    # Strip chrome from linked session
    _tmux("set", "-t", linked, "status", "off")
    _tmux("set", "-t", linked, "mouse", "off")

    return linked


def _setup_keybindings() -> None:
    """Set up Ctrl+] to toggle between top and bottom panes.

    Uses -n (root table) binding scoped to the split window via
    if-shell checking the current window name.
    """
    # Ctrl+] toggles focus between panes, but only in our split window.
    # We use if-shell to check window name so it doesn't steal Ctrl+]
    # globally — only when you're in the overcode-tmux window.
    _tmux(
        "bind-key", "-n", "C-]",
        "if-shell",
        f"-F '#{{==:#{{window_name}},{SPLIT_WINDOW_NAME}}}'",
        "select-pane -t :.+",  # cycle to next pane (toggles between 2)
        "",  # no-op if not in our window
    )


def _get_first_agent_window(agents_session: str) -> str | None:
    """Get the name of the first agent window, if any."""
    result = _tmux(
        "list-windows", "-t", agents_session, "-F", "#{window_name}",
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.strip().splitlines()[0]


def _find_existing_split_window(tmux_session: str) -> str | None:
    """Find the split window in a tmux session, if it exists.

    Returns the window ID (e.g. '@3') or None.
    """
    result = _tmux(
        "list-windows", "-t", tmux_session,
        "-F", "#{window_name} #{window_id}",
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.strip().splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0] == SPLIT_WINDOW_NAME:
            return parts[1]
    return None


def _find_existing_split_window_any_session() -> tuple[str, str] | None:
    """Find the split window across all tmux sessions.

    Returns (session_name, window_id) or None.
    """
    result = _tmux(
        "list-windows", "-a",
        "-F", "#{session_name} #{window_name} #{window_id}",
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.strip().splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[1] == SPLIT_WINDOW_NAME:
            return (parts[0], parts[2])
    return None


@app.command("tmux")
def tmux_layout(
    session: SessionOption = "agents",
    ratio: Annotated[
        int, typer.Option("--ratio", "-r", help="Percentage of height for the monitor pane (top)")
    ] = 40,
):
    """Open the tmux split layout: monitor on top, agent terminal on bottom.

    Creates the layout if it doesn't exist, or switches to it if it does.
    The top pane runs `overcode monitor` with auto-sync enabled. The bottom
    pane shows the focused agent's terminal natively — full speed, full
    color, full scrollback.

    Navigation: use j/k in the top pane to browse agents. The bottom pane
    follows automatically. Press Ctrl+] to toggle focus between panes.
    When the bottom pane has focus, all keys go directly to the agent's
    terminal.
    """
    from rich import print as rprint

    # Verify agents session exists
    if not _tmux_check("has-session", "-t", session):
        rprint(f"[red]No tmux session '{session}' found.[/red]")
        rprint(f"[dim]Launch some agents first, or create it: tmux new-session -d -s {session}[/dim]")
        raise typer.Exit(1)

    # Always ensure keybindings are set up (idempotent)
    _setup_keybindings()

    # Create (or find) the linked session for the bottom pane
    linked = _setup_linked_session(session)

    # Select a window in the linked session (first agent, or whatever's current)
    first_window = _get_first_agent_window(session)
    if first_window:
        _tmux("select-window", "-t", f"{linked}:={first_window}")

    in_tmux = os.environ.get("TMUX")

    # --- Check if split window already exists ---

    if in_tmux:
        # Look for existing split window in the current session
        current_session = _tmux_output(
            "display-message", "-p", "#{session_name}",
        )
        existing = _find_existing_split_window(current_session)
        if existing:
            # Just switch to it
            _tmux("select-window", "-t", existing)
            rprint(f"[green]Switched to existing {SPLIT_WINDOW_NAME} window[/green]")
            return
    else:
        # Not in tmux — check if split window exists anywhere
        existing = _find_existing_split_window_any_session()
        if existing:
            sess_name, win_id = existing
            rprint(f"[green]Attaching to existing {SPLIT_WINDOW_NAME} window...[/green]")
            time.sleep(0.2)
            os.execlp("tmux", "tmux", "attach-session", "-t", f"{sess_name}")

    # --- Create the split layout ---

    # Resolve the overcode binary from the same package installation,
    # not whatever `overcode` resolves to on PATH (may be a different install).
    overcode_cmd = _find_overcode_cmd()
    monitor_cmd = f"{overcode_cmd} monitor --session {session} --sync-target {linked}"
    # The bottom pane runs a nested tmux attach. We must unset $TMUX
    # because tmux refuses to attach from inside an existing session.
    attach_cmd = f"unset TMUX; tmux attach-session -t {linked}"

    if in_tmux:
        # We're already in a tmux session — create a new window for the split
        result = _tmux(
            "new-window", "-n", SPLIT_WINDOW_NAME, "-P", "-F", "#{pane_id}",
            monitor_cmd,
        )
        if result.returncode != 0:
            rprint(f"[red]Failed to create split window: {result.stderr}[/red]")
            raise typer.Exit(1)
        top_pane_id = result.stdout.strip()

        # Split horizontally (top/bottom), bottom pane gets (100-ratio)%
        result = _tmux(
            "split-window", "-v", "-t", top_pane_id,
            "-p", str(100 - ratio),
            "-P", "-F", "#{pane_id}",
            attach_cmd,
        )
        if result.returncode != 0:
            rprint(f"[red]Failed to create bottom pane: {result.stderr}[/red]")
            raise typer.Exit(1)

        # Focus the top pane (monitor) by default
        _tmux("select-pane", "-t", top_pane_id)

        rprint(f"[green]Split layout ready.[/green] Ctrl+] toggles panes.")

    else:
        # Not in tmux — create a new session with the split layout
        new_session = "overcode"

        # Kill stale session if it exists but has no split window
        # (shouldn't normally happen, but be safe)
        if _tmux_check("has-session", "-t", new_session):
            existing = _find_existing_split_window(new_session)
            if not existing:
                _tmux("kill-session", "-t", new_session)

        if not _tmux_check("has-session", "-t", new_session):
            result = _tmux(
                "new-session", "-d", "-s", new_session,
                "-n", SPLIT_WINDOW_NAME,
                "-x", "200", "-y", "50",
                monitor_cmd,
            )
            if result.returncode != 0:
                rprint(f"[red]Failed to create session: {result.stderr}[/red]")
                raise typer.Exit(1)

            # Split for bottom pane
            _tmux(
                "split-window", "-v",
                "-t", f"{new_session}:{SPLIT_WINDOW_NAME}",
                "-p", str(100 - ratio),
                attach_cmd,
            )

            # Focus top pane
            _tmux("select-pane", "-t", f"{new_session}:{SPLIT_WINDOW_NAME}.0")

        # Attach to the session (replaces this process)
        rprint(f"[green]Attaching to split layout...[/green]")
        time.sleep(0.2)
        os.execlp("tmux", "tmux", "attach-session", "-t", new_session)
