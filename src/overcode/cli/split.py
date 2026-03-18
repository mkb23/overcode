"""
`overcode tmux` — tmux-native split layout.

Creates (or re-attaches to) a tmux window with two panes:
  - Top pane: overcode monitor (tree mode, sync auto-enabled)
  - Bottom pane: linked tmux session showing the focused agent's terminal

The TUI's tmux_sync feature drives window switching in the bottom pane.
Tab toggles focus between the nav (top) and terminal (bottom) panes.

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

    # Strip chrome from linked session, generous scrollback
    _tmux("set", "-t", linked, "status", "off")
    _tmux("set", "-t", linked, "history-limit", "50000")

    return linked


def _setup_keybindings(linked_session: str = "") -> None:
    """Set up split-window keybindings (Tab, PageUp/PageDown, R).

    Uses -n (root table) bindings scoped to the split window via
    if-shell checking the current window name. Outside the split
    window, keys pass through normally.
    """
    # Tab toggles focus between panes, but only in our split window.
    # Note: -F must be a separate argument from the format string.
    _tmux(
        "bind-key", "-n", "Tab",
        "if-shell", "-F",
        f"#{{==:#{{window_name}},{SPLIT_WINDOW_NAME}}}",
        "select-pane -t :.+",  # cycle to next pane (toggles between 2)
        "send-keys Tab",  # pass Tab through in other windows
    )
    # R cycles the split ratio (25% → 33% → 50% → 25%), scoped to
    # the split window and only when the top (monitor) pane is active.
    _tmux(
        "bind-key", "-n", "R",
        "if-shell", "-F",
        f"#{{==:#{{window_name}},{SPLIT_WINDOW_NAME}}}",
        f"run-shell '{_find_overcode_cmd()} tmux-resize'",
        "send-keys R",  # pass R through in other windows
    )

    # --- Scrollback for the nested tmux in the bottom pane ---
    # The bottom pane runs a nested tmux client. The outer tmux
    # intercepts the prefix key, so copy-mode can't be entered
    # normally. These bindings use `copy-mode -t` to directly enter
    # copy mode in the inner session via the tmux API.
    if linked_session:
        # PageUp: enter copy mode + scroll up, but only when in the
        # bottom pane (pane_index != 0) of the split window.
        # Top pane (Textual TUI) and other windows get normal PageUp.
        _tmux(
            "bind-key", "-n", "PPage",
            "if-shell", "-F",
            f"#{{&&:#{{==:#{{window_name}},{SPLIT_WINDOW_NAME}}},#{{!=:#{{pane_index}},0}}}}",
            f"copy-mode -t {linked_session} -u",
            "send-keys PPage",
        )
        # PageDown in the inner session's copy mode
        _tmux(
            "bind-key", "-n", "NPage",
            "if-shell", "-F",
            f"#{{&&:#{{==:#{{window_name}},{SPLIT_WINDOW_NAME}}},#{{!=:#{{pane_index}},0}}}}",
            f"send-keys -t {linked_session} NPage",
            "send-keys NPage",
        )
        # Mouse scroll: redirect to inner session copy mode.
        # Without this, scrolling enters copy mode in the outer pane
        # which has no scrollback (just rendered inner tmux frames).
        _in_bottom = (
            f"#{{&&:#{{==:#{{window_name}},{SPLIT_WINDOW_NAME}}},"
            f"#{{!=:#{{pane_index}},0}}}}"
        )
        _tmux(
            "bind-key", "-n", "WheelUpPane",
            "if-shell", "-F", _in_bottom,
            f"copy-mode -t {linked_session} -e",
            # Default behaviour for other contexts
            "if-shell -F '#{||:#{pane_in_mode},#{mouse_any_flag}}' "
            "'send-keys -M' 'copy-mode -e'",
        )
        # Once in copy mode, wheel events need to scroll the inner session
        _tmux(
            "bind-key", "-T", "copy-mode", "WheelUpPane",
            "if-shell", "-F", _in_bottom,
            f"send-keys -t {linked_session} -X -N 5 scroll-up",
            "select-pane ; send-keys -X -N 5 scroll-up",
        )
        _tmux(
            "bind-key", "-T", "copy-mode", "WheelDownPane",
            "if-shell", "-F", _in_bottom,
            f"send-keys -t {linked_session} -X -N 5 scroll-down",
            "select-pane ; send-keys -X -N 5 scroll-down",
        )
        _tmux(
            "bind-key", "-T", "copy-mode-vi", "WheelUpPane",
            "if-shell", "-F", _in_bottom,
            f"send-keys -t {linked_session} -X -N 5 scroll-up",
            "select-pane ; send-keys -X -N 5 scroll-up",
        )
        _tmux(
            "bind-key", "-T", "copy-mode-vi", "WheelDownPane",
            "if-shell", "-F", _in_bottom,
            f"send-keys -t {linked_session} -X -N 5 scroll-down",
            "select-pane ; send-keys -X -N 5 scroll-down",
        )


def _get_first_agent_window(agents_session: str) -> str | None:
    """Get the name of the first agent window, if any."""
    result = _tmux(
        "list-windows", "-t", agents_session, "-F", "#{window_name}",
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.strip().splitlines()[0]


def _is_split_window_healthy(window_id: str) -> bool:
    """Check that the split window has 2 panes with the expected processes.

    Returns False if the monitor pane died (leaving a bare shell) or
    the window lost a pane entirely.
    """
    result = _tmux(
        "list-panes", "-t", window_id,
        "-F", "#{pane_current_command}",
    )
    if result.returncode != 0:
        return False
    panes = result.stdout.strip().splitlines()
    if len(panes) != 2:
        return False
    # Top pane should be running python/overcode, not a bare shell
    top_cmd = panes[0].strip()
    shell_names = {"zsh", "bash", "sh", "fish"}
    if top_cmd in shell_names:
        return False
    return True


def _kill_split_window(window_id: str) -> None:
    """Kill a stale split window so it can be recreated."""
    _tmux("kill-window", "-t", window_id)


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


RATIO_CYCLE = [25, 33, 50]  # Percentages to cycle through for top pane


@app.command("tmux-resize", hidden=True)
def tmux_resize():
    """Cycle the split ratio (internal, called by R keybinding)."""
    target = f"overcode:{SPLIT_WINDOW_NAME}"
    # Get current top pane height and window height
    info = _tmux_output(
        "display-message", "-t", target,
        "-p", "#{pane_height}:#{window_height}",
    )
    if not info or ":" not in info:
        return
    try:
        pane_h, win_h = info.split(":")
        current_pct = round(int(pane_h) / int(win_h) * 100)
    except (ValueError, ZeroDivisionError):
        return

    # Find the next ratio in the cycle
    next_ratio = RATIO_CYCLE[0]
    for i, r in enumerate(RATIO_CYCLE):
        if current_pct <= r + 3:  # within 3% tolerance
            next_ratio = RATIO_CYCLE[(i + 1) % len(RATIO_CYCLE)]
            break

    # Apply the new ratio
    new_height = max(int(int(info.split(":")[1]) * next_ratio / 100), 5)
    _tmux("resize-pane", "-t", f"{target}.0", "-y", str(new_height))


@app.command("tmux")
def tmux_layout(
    session: SessionOption = "agents",
    ratio: Annotated[
        int, typer.Option("--ratio", "-r", help="Percentage of height for the monitor pane (top)")
    ] = 25,
):
    """Open the tmux split layout: monitor on top, agent terminal on bottom.

    Creates the layout if it doesn't exist, or switches to it if it does.
    The top pane runs `overcode monitor` with auto-sync enabled. The bottom
    pane shows the focused agent's terminal natively — full speed, full
    color, full scrollback.

    Navigation: use j/k in the top pane to browse agents. The bottom pane
    follows automatically. Press Tab to toggle focus between panes.
    When the bottom pane has focus, all keys go directly to the agent's
    terminal.
    """
    from rich import print as rprint

    # Verify agents session exists
    if not _tmux_check("has-session", "-t", session):
        rprint(f"[red]No tmux session '{session}' found.[/red]")
        rprint(f"[dim]Launch some agents first, or create it: tmux new-session -d -s {session}[/dim]")
        raise typer.Exit(1)

    _tmux("set", "-g", "focus-events", "on")

    # Create (or find) the linked session for the bottom pane
    linked = _setup_linked_session(session)

    # Always ensure keybindings are set up (idempotent, needs linked session name)
    _setup_keybindings(linked_session=linked)

    # Select a window in the linked session (first agent, or whatever's current)
    first_window = _get_first_agent_window(session)
    if first_window:
        _tmux("select-window", "-t", f"{linked}:={first_window}")

    in_tmux = os.environ.get("TMUX")

    # --- Check if split window already exists ---

    # --- The split layout always lives in a dedicated "overcode" session ---
    # This is critical: if the split window were inside the "agents" session,
    # the monitor's tmux sync (which switches windows on the linked session)
    # would also navigate the outer window away from the split view.
    oc_session = "overcode"

    # Build the monitor command early — needed for both fresh creation
    # and respawning the top pane on relaunch.
    overcode_cmd = _find_overcode_cmd()
    monitor_cmd = f"{overcode_cmd} monitor --session {session} --sync-target {linked}"

    # Check if the split window already exists in the overcode session
    existing = _find_existing_split_window(oc_session)
    if existing:
        if _is_split_window_healthy(existing):
            if in_tmux:
                # Check if a real (non-tiny) client is already on overcode.
                # If so, no switch needed — the user is already there or
                # another terminal has it open.  Switching blindly can
                # accidentally move the bottom pane's nested client from
                # oc-view-agents to overcode, creating a recursive display
                # that collapses the window.
                oc_clients = _tmux_output(
                    "list-clients", "-t", oc_session,
                    "-F", "#{client_height}",
                )
                has_real_client = any(
                    int(h) > 10
                    for h in oc_clients.splitlines()
                    if h.strip().isdigit()
                )
                if not has_real_client:
                    # No real client on overcode yet — switch the caller's
                    client_tty = _tmux_output(
                        "display-message", "-p", "#{client_tty}",
                    )
                    if client_tty:
                        _tmux("switch-client", "-c", client_tty,
                              "-t", f"{oc_session}:{SPLIT_WINDOW_NAME}")
                    else:
                        # Can't determine client — tell user how to get there
                        rprint(f"[green]Split layout is running.[/green] Switch to it with:")
                        rprint(f"  tmux switch-client -t {oc_session}")
                        return
            else:
                # Respawn before attach — execlp replaces this process
                _tmux("respawn-pane", "-k",
                      "-t", f"{oc_session}:{SPLIT_WINDOW_NAME}.0",
                      monitor_cmd)
                rprint(f"[green]Attaching to existing {SPLIT_WINDOW_NAME} window (monitor restarted)...[/green]")
                time.sleep(0.2)
                os.execlp("tmux", "tmux", "attach-session", "-t", oc_session)
            # Always restart the monitor so code changes take effect
            _tmux("respawn-pane", "-k",
                  "-t", f"{oc_session}:{SPLIT_WINDOW_NAME}.0",
                  monitor_cmd)
            rprint(f"[green]Switched to existing {SPLIT_WINDOW_NAME} window (monitor restarted)[/green]")
            return
        else:
            # Monitor died — kill stale window and recreate.
            # Switch the client back to agents first, because killing
            # the only window in the overcode session destroys it and
            # would detach the client.
            rprint(f"[yellow]Stale {SPLIT_WINDOW_NAME} window detected, recreating...[/yellow]")
            if in_tmux:
                client_tty = _tmux_output("display-message", "-p", "#{client_tty}")
                if client_tty:
                    _tmux("switch-client", "-c", client_tty, "-t", session)
                else:
                    _tmux("switch-client", "-t", session)
            _kill_split_window(existing)
            # Also kill the overcode + linked sessions so they're cleanly recreated
            _tmux("kill-session", "-t", oc_session)
            _tmux("kill-session", "-t", linked)
            # Re-setup linked session (was killed above)
            linked = _setup_linked_session(session)
            if first_window:
                _tmux("select-window", "-t", f"{linked}:={first_window}")

    # --- Create the split layout ---

    # The bottom pane runs a nested tmux attach. We must unset $TMUX
    # because tmux refuses to attach from inside an existing session.
    attach_cmd = f"unset TMUX; tmux attach-session -t {linked}"

    # Kill stale overcode session if it exists but has no split window
    if _tmux_check("has-session", "-t", oc_session):
        existing = _find_existing_split_window(oc_session)
        if not existing:
            _tmux("kill-session", "-t", oc_session)

    need_new_session = not _tmux_check("has-session", "-t", oc_session)

    if in_tmux:
        # --- Inside tmux ---
        # Get the actual client dimensions so we create the session at
        # the right size. Creating detached at a wrong size and then
        # switch-client causes pane collapse.
        size_str = _tmux_output("display-message", "-p", "#{client_width}x#{client_height}")
        try:
            w, h = size_str.split("x")
            client_width, client_height = int(w), int(h)
        except (ValueError, AttributeError):
            client_width, client_height = 200, 50

        if need_new_session:
            result = _tmux(
                "new-session", "-d", "-s", oc_session,
                "-n", SPLIT_WINDOW_NAME,
                "-x", str(client_width), "-y", str(client_height),
                monitor_cmd,
            )
            if result.returncode != 0:
                rprint(f"[red]Failed to create session: {result.stderr}[/red]")
                raise typer.Exit(1)
        else:
            # Session exists but no split window — add one
            result = _tmux(
                "new-window", "-t", oc_session,
                "-n", SPLIT_WINDOW_NAME, "-P", "-F", "#{pane_id}",
                monitor_cmd,
            )
            if result.returncode != 0:
                rprint(f"[red]Failed to create split window: {result.stderr}[/red]")
                raise typer.Exit(1)

        # No status bar — the Textual TUI has its own header.
        # detach-on-destroy off: if the session dies, move the client
        # to another session instead of detaching the user's terminal.
        # window-size largest: the bottom pane's nested tmux creates a
        # small client on this session; "largest" ensures the real
        # terminal (the largest client) always determines window size.
        _tmux("set", "-t", oc_session, "status", "off")
        _tmux("set", "-t", oc_session, "detach-on-destroy", "off")
        _tmux("set", "-t", oc_session, "window-size", "largest")

        # Identify the current client explicitly so switch-client
        # doesn't accidentally target the bottom pane's nested client.
        client_tty = _tmux_output("display-message", "-p", "#{client_tty}")

        # Switch the client BEFORE splitting. This ensures:
        # 1. The window resizes to the real client terminal size
        # 2. The split-window creates panes at the correct dimensions
        if client_tty:
            _tmux("switch-client", "-c", client_tty,
                  "-t", f"{oc_session}:{SPLIT_WINDOW_NAME}")
        else:
            _tmux("switch-client", "-t", f"{oc_session}:{SPLIT_WINDOW_NAME}")

        # Split for bottom pane (client is now viewing this window at
        # the correct size, so pane heights will be correct)
        result = _tmux(
            "split-window", "-v",
            "-t", f"{oc_session}:{SPLIT_WINDOW_NAME}",
            "-p", str(100 - ratio),
            attach_cmd,
        )
        if result.returncode != 0:
            rprint(f"[red]Failed to create bottom pane: {result.stderr}[/red]")
            raise typer.Exit(1)

        # Focus top pane
        _tmux("select-pane", "-t", f"{oc_session}:{SPLIT_WINDOW_NAME}.0")

        rprint(f"[green]Split layout ready.[/green] Tab toggles panes.")

    else:
        # --- Outside tmux: create detached, split, then attach ---
        if need_new_session:
            result = _tmux(
                "new-session", "-d", "-s", oc_session,
                "-n", SPLIT_WINDOW_NAME,
                "-x", "200", "-y", "50",
                monitor_cmd,
            )
            if result.returncode != 0:
                rprint(f"[red]Failed to create session: {result.stderr}[/red]")
                raise typer.Exit(1)
        else:
            result = _tmux(
                "new-window", "-t", oc_session,
                "-n", SPLIT_WINDOW_NAME, "-P", "-F", "#{pane_id}",
                monitor_cmd,
            )
            if result.returncode != 0:
                rprint(f"[red]Failed to create split window: {result.stderr}[/red]")
                raise typer.Exit(1)

        # No status bar — the Textual TUI has its own header
        _tmux("set", "-t", oc_session, "status", "off")
        _tmux("set", "-t", oc_session, "detach-on-destroy", "off")
        _tmux("set", "-t", oc_session, "window-size", "largest")

        # Split for bottom pane
        _tmux(
            "split-window", "-v",
            "-t", f"{oc_session}:{SPLIT_WINDOW_NAME}",
            "-p", str(100 - ratio),
            attach_cmd,
        )

        # Focus top pane
        _tmux("select-pane", "-t", f"{oc_session}:{SPLIT_WINDOW_NAME}.0")

        # Attach to the session (replaces this process)
        rprint(f"[green]Attaching to split layout...[/green]")
        time.sleep(0.2)
        os.execlp("tmux", "tmux", "attach-session", "-t", oc_session)
