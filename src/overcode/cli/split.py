"""
`overcode tmux` — tmux-native split layout.

Creates (or re-attaches to) a tmux window with two panes:
  - Top pane: overcode monitor (tree mode, sync auto-enabled)
  - Bottom pane: linked tmux session showing the focused agent's terminal

The TUI's tmux_sync feature drives window switching in the bottom pane.
Tab toggles focus between the nav (top) and terminal (bottom) panes.

Idempotent: running `overcode tmux` again will switch to the existing
split window rather than creating a duplicate.

Global tmux side effects
~~~~~~~~~~~~~~~~~~~~~~~~
This command installs keybindings in tmux's root key table and sets
some global server options. All keybindings are scoped with if-shell
checks so they only activate inside the ``overcode-tmux`` window and
pass through normally elsewhere. However, custom user bindings for the
same keys (Tab, PageUp/PageDown, WheelUp/WheelDown, M-j, M-k) in the
root table will be overridden. Global options set: ``focus-events on``
and ``terminal-features *:sync``.

Use ``overcode tmux --uninstall`` to remove all overcode keybindings
and restore tmux defaults.
"""

import os
import subprocess
import time
from pathlib import Path
from typing import Annotated

import typer

from ._shared import app, SessionOption
from ..tmux_utils import get_pane_base_index


def _acquire_setup_lock() -> bool:
    """Acquire an exclusive lock to prevent concurrent `overcode tmux` runs.

    Uses a lockfile in ~/.overcode/. Returns True if lock acquired,
    False if another instance is already setting up.
    """
    lock_dir = Path.home() / ".overcode"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "tmux-setup.lock"
    try:
        # O_CREAT | O_EXCL: atomic create-if-not-exists
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        # Check if the holding process is still alive (stale lock)
        try:
            pid = int(lock_path.read_text().strip())
            os.kill(pid, 0)  # signal 0 = check if alive
            return False  # Process is alive — genuine lock
        except (ValueError, ProcessLookupError, PermissionError, OSError):
            # Stale lock — remove and retry
            lock_path.unlink(missing_ok=True)
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return True
            except FileExistsError:
                return False  # Another process beat us


def _release_setup_lock() -> None:
    """Release the setup lock."""
    lock_path = Path.home() / ".overcode" / "tmux-setup.lock"
    lock_path.unlink(missing_ok=True)


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

# Choices for the pane-toggle key (displayed label → tmux key name)
TOGGLE_KEY_CHOICES: list[tuple[str, str]] = [
    ("Tab", "Tab"),
    ("Ctrl+]", "C-]"),
    ("Ctrl+Space", "C-Space"),
]
DEFAULT_TOGGLE_KEY = "Tab"


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

    # Strip chrome from linked session, generous scrollback.
    # window-size smallest: shared windows size to the nested client's
    # viewport, preventing re-wrapping jitter from wider stale sizes.
    _tmux("set", "-t", linked, "status", "off")
    _tmux("set", "-t", linked, "history-limit", "50000")

    # Resize shared windows to match the nested client and keep them
    # in sync when the terminal is resized. window-size smallest alone
    # doesn't reliably trigger resizes for linked session window groups,
    # so we use a client-resized hook to force it.
    _tmux(
        "set-hook", "-t", linked, "client-resized",
        "run-shell 'for w in $(tmux list-windows -t " + linked +
        " -F \"#{window_id}\"); do tmux resize-window -t $w -A 2>/dev/null; done'",
    )
    # Also resize now for any stale sizes
    result = _tmux("list-windows", "-t", linked, "-F", "#{window_id}")
    if result.returncode == 0:
        for win_id in result.stdout.strip().splitlines():
            _tmux("resize-window", "-t", win_id, "-A")

    return linked


def _are_keybindings_installed() -> bool:
    """Check if overcode keybindings are already installed."""
    # Check for our M-j binding as a sentinel — look for the exact
    # send-keys target rather than a substring match on the window name,
    # to avoid false positives from unrelated bindings (#384).
    result = _tmux("list-keys", "-T", "root")
    if result.returncode != 0:
        return False
    return f"send-keys -t overcode:{SPLIT_WINDOW_NAME}." in result.stdout


def _remove_keybindings() -> None:
    """Remove all overcode keybindings from tmux's root key table."""
    from ..config import get_tmux_toggle_key

    toggle_key = get_tmux_toggle_key() or DEFAULT_TOGGLE_KEY
    keys = [toggle_key, "M-j", "M-k", "M-b", "PPage", "NPage", "WheelUpPane", "WheelDownPane"]
    # Also unbind Tab if toggle_key changed away from it (stale binding)
    if toggle_key != "Tab":
        keys.append("Tab")
    for key in keys:
        _tmux("unbind-key", "-n", key)
    # Restore tmux defaults for mouse scroll
    _tmux(
        "bind-key", "-n", "WheelUpPane",
        "if-shell", "-F", "#{||:#{pane_in_mode},#{mouse_any_flag}}",
        "send-keys -M", "copy-mode -e",
    )


def _setup_keybindings(linked_session: str = "", toggle_key: str = "") -> None:
    """Set up split-window keybindings (toggle key, PageUp/PageDown, etc.).

    Uses -n (root table) bindings scoped to the split window via
    if-shell checking the current window name. Outside the split
    window, keys pass through normally.

    Args:
        linked_session: Name of the linked tmux session for scrollback bindings.
        toggle_key: tmux key name for pane toggle (default from config or "Tab").
    """
    if not toggle_key:
        from ..config import get_tmux_toggle_key
        toggle_key = get_tmux_toggle_key() or DEFAULT_TOGGLE_KEY

    # Toggle key switches focus between panes, but only in our split window.
    # Note: -F must be a separate argument from the format string.
    _tmux(
        "bind-key", "-n", toggle_key,
        "if-shell", "-F",
        f"#{{==:#{{window_name}},{SPLIT_WINDOW_NAME}}}",
        "select-pane -t :.+",  # cycle to next pane (toggles between 2)
        f"send-keys {toggle_key}",  # pass key through in other windows
    )
    # --- Agent navigation from the bottom (terminal) pane ---
    # Option+J / Option+K (Meta+j/k) cycle agents by sending j/k
    # to the monitor pane, which navigates and syncs the terminal.
    _base = get_pane_base_index()
    _in_bottom = (
        f"#{{&&:#{{==:#{{window_name}},{SPLIT_WINDOW_NAME}}},"
        f"#{{!=:#{{pane_index}},{_base}}}}}"
    )
    _tmux(
        "bind-key", "-n", "M-j",
        "if-shell", "-F", _in_bottom,
        f"send-keys -t overcode:{SPLIT_WINDOW_NAME}.{_base} j",
        "send-keys M-j",
    )
    _tmux(
        "bind-key", "-n", "M-k",
        "if-shell", "-F", _in_bottom,
        f"send-keys -t overcode:{SPLIT_WINDOW_NAME}.{_base} k",
        "send-keys M-k",
    )
    # Option+B: navigate to bell (next agent needing attention)
    _tmux(
        "bind-key", "-n", "M-b",
        "if-shell", "-F", _in_bottom,
        f"send-keys -t overcode:{SPLIT_WINDOW_NAME}.{_base} b",
        "send-keys M-b",
    )

    # --- Scrollback for the nested tmux in the bottom pane ---
    # The bottom pane runs a nested tmux client. The outer tmux
    # intercepts the prefix key, so copy-mode can't be entered
    # normally. These bindings use `copy-mode -t` to directly enter
    # copy mode in the inner session via the tmux API.
    if linked_session:
        # PageUp: enter copy mode + scroll up, but only when in the
        # bottom pane (pane_index != base) of the split window.
        # Top pane (Textual TUI) and other windows get normal PageUp.
        _tmux(
            "bind-key", "-n", "PPage",
            "if-shell", "-F",
            f"#{{&&:#{{==:#{{window_name}},{SPLIT_WINDOW_NAME}}},#{{!=:#{{pane_index}},{_base}}}}}",
            f"copy-mode -t {linked_session} -u",
            "send-keys PPage",
        )
        # PageDown in the inner session's copy mode
        _tmux(
            "bind-key", "-n", "NPage",
            "if-shell", "-F",
            f"#{{&&:#{{==:#{{window_name}},{SPLIT_WINDOW_NAME}}},#{{!=:#{{pane_index}},{_base}}}}}",
            f"send-keys -t {linked_session} NPage",
            "send-keys NPage",
        )
        # Mouse scroll: redirect to inner tmux copy mode.
        # Without this, scrolling enters copy mode in the outer pane
        # which has no scrollback (just rendered inner tmux frames).
        #
        # Strategy: enter copy mode in the inner session (no-op if
        # already active), then send scroll-up/down commands to it.
        _in_bottom = (
            f"#{{&&:#{{==:#{{window_name}},{SPLIT_WINDOW_NAME}}},"
            f"#{{!=:#{{pane_index}},{_base}}}}}"
        )
        _tmux(
            "bind-key", "-n", "WheelUpPane",
            "if-shell", "-F", _in_bottom,
            f"copy-mode -t {linked_session} -e ; "
            f"send-keys -t {linked_session} -X -N 3 scroll-up",
            # Default behaviour for other contexts
            "if-shell -F '#{||:#{pane_in_mode},#{mouse_any_flag}}' "
            "'send-keys -M' 'copy-mode -e'",
        )
        _tmux(
            "bind-key", "-n", "WheelDownPane",
            "if-shell", "-F", _in_bottom,
            f"send-keys -t {linked_session} -X -N 3 scroll-down",
            "send-keys -M",
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
    _tmux("resize-pane", "-t", f"{target}.{get_pane_base_index()}", "-y", str(new_height))


@app.command("tmux")
def tmux_layout(
    session: SessionOption = "agents",
    ratio: Annotated[
        int, typer.Option("--ratio", "-r", help="Percentage of height for the monitor pane (top)")
    ] = 25,
    uninstall: Annotated[
        bool, typer.Option("--uninstall", help="Remove overcode tmux keybindings and exit")
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Skip the first-run confirmation prompt")
    ] = False,
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

    This command installs keybindings in tmux's global root key table.
    All bindings are scoped to the overcode-tmux window and pass through
    normally elsewhere, but they do override any custom user bindings for
    the same keys. Use --uninstall to remove them.
    """
    from rich import print as rprint

    # --- Uninstall mode ---
    if uninstall:
        removed_anything = False

        if _are_keybindings_installed():
            _remove_keybindings()
            rprint("[green]Keybindings removed.[/green]")
            removed_anything = True

        # Kill the split window and overcode session
        existing = _find_existing_split_window_any_session()
        if existing:
            sess, win_id = existing
            _tmux("kill-window", "-t", win_id)
            # If the session is now empty, kill it too
            result = _tmux("list-windows", "-t", sess)
            if result.returncode != 0 or not result.stdout.strip():
                _tmux("kill-session", "-t", sess)
                rprint(f"[green]Killed session '{sess}'.[/green]")
            else:
                rprint(f"[green]Killed split window in '{sess}'.[/green]")
            removed_anything = True

        # Kill any linked sessions (oc-view-*)
        result = _tmux("list-sessions", "-F", "#{session_name}")
        if result.returncode == 0:
            for sess_name in result.stdout.strip().splitlines():
                if sess_name.startswith(LINKED_SESSION_PREFIX + "-"):
                    _tmux("kill-session", "-t", sess_name)
                    rprint(f"[green]Killed linked session '{sess_name}'.[/green]")
                    removed_anything = True

        if not removed_anything:
            rprint("[dim]No overcode tmux state found.[/dim]")

        rprint("")
        rprint("[dim]Note: global tmux options (focus-events on, terminal-features *:sync)")
        rprint("are left in place as they are generally harmless. To remove them:[/dim]")
        rprint("  tmux set -g focus-events off")
        rprint("  tmux set -g terminal-features ''")
        return

    # Verify agents session exists
    if not _tmux_check("has-session", "-t", session):
        rprint(f"[red]No tmux session '{session}' found.[/red]")
        rprint(f"[dim]Launch some agents first, or create it: tmux new-session -d -s {session}[/dim]")
        raise typer.Exit(1)

    # --- Toggle key selection (runs if not yet configured) ---
    from ..config import get_tmux_toggle_key, set_tmux_toggle_key

    configured_key = get_tmux_toggle_key()

    if not yes and not configured_key:
        rprint("\n[bold]overcode tmux[/bold] — choose a key to toggle between panes:\n")
        for i, (label, _tmux_key) in enumerate(TOGGLE_KEY_CHOICES, 1):
            default_tag = " [dim](default)[/dim]" if _tmux_key == DEFAULT_TOGGLE_KEY else ""
            rprint(f"  [cyan]{i}[/cyan]) {label}{default_tag}")
        rprint("")
        try:
            choice = input("  Choice [1]: ").strip()
        except (EOFError, KeyboardInterrupt):
            rprint("")
            raise typer.Exit(0)
        if choice == "":
            idx = 0
        elif choice.isdigit() and 1 <= int(choice) <= len(TOGGLE_KEY_CHOICES):
            idx = int(choice) - 1
        else:
            rprint("[dim]Invalid choice — using default (Tab).[/dim]")
            idx = 0
        chosen_label, chosen_key = TOGGLE_KEY_CHOICES[idx]
        set_tmux_toggle_key(chosen_key)
        rprint(f"\n  Saved [cyan]{chosen_label}[/cyan] as toggle key in ~/.overcode/config.yaml")
        rprint(f"  [dim]Change later: set tmux.toggle_key in config.yaml[/dim]\n")
    elif not configured_key:
        # --yes passed but no key configured: save the default silently
        set_tmux_toggle_key(DEFAULT_TOGGLE_KEY)

    # --- First-run keybinding confirmation ---
    if not yes and not _are_keybindings_installed():
        toggle_key = get_tmux_toggle_key() or DEFAULT_TOGGLE_KEY
        chosen_label = next(
            (label for label, k in TOGGLE_KEY_CHOICES if k == toggle_key),
            toggle_key,
        )

        rprint("[bold]overcode tmux[/bold] will install keybindings in tmux's global root key table:\n")
        rprint(f"  [cyan]{chosen_label}[/cyan]{'  ' if len(chosen_label) < 8 else ' '}Toggle monitor/terminal pane focus")
        rprint("  [cyan]Option+J/K[/cyan]   Cycle agents from terminal pane")
        rprint("  [cyan]PageUp/Down[/cyan]  Scrollback in nested terminal")
        rprint("  [cyan]WheelUp/Down[/cyan] Mouse scroll in nested terminal")
        rprint("\n  All bindings are scoped to the [bold]overcode-tmux[/bold] window and")
        rprint("  pass through normally elsewhere. Custom bindings for these")
        rprint("  keys in the root table will be overridden.\n")
        rprint("  Global options set: [dim]focus-events on, terminal-features *:sync[/dim]")
        rprint("  Run [dim]overcode tmux --uninstall[/dim] to remove.\n")
        try:
            confirm = input("  Proceed? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            rprint("")
            raise typer.Exit(0)
        if confirm and confirm != "y":
            rprint("[dim]Aborted.[/dim]")
            raise typer.Exit(0)
        rprint("")

    # Prevent concurrent `overcode tmux` invocations from racing
    if not _acquire_setup_lock():
        rprint("[yellow]Another `overcode tmux` is already setting up. Try again in a moment.[/yellow]")
        raise typer.Exit(1)

    try:
        _tmux_layout_locked(session, ratio, rprint)
    finally:
        _release_setup_lock()


def _tmux_layout_locked(session: str, ratio: int, rprint) -> None:
    """Create or re-attach to the tmux split layout. Called under setup lock."""
    in_tmux = os.environ.get("TMUX")

    _tmux("set", "-g", "focus-events", "on")
    # Enable synchronized output (DEC mode 2026) — batches screen updates
    # so the terminal renders them atomically, preventing mid-redraw tearing.
    _tmux("set", "-as", "terminal-features", ",*:sync")

    # Create (or find) the linked session for the bottom pane
    linked = _setup_linked_session(session)

    # Always ensure keybindings are set up (idempotent, needs linked session name)
    _setup_keybindings(linked_session=linked)

    # Select a window in the linked session (first agent, or whatever's current)
    first_window = _get_first_agent_window(session)
    if first_window:
        _tmux("select-window", "-t", f"{linked}:={first_window}")

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
                # Check if a real (non-nested) client is already on overcode.
                # If so, no switch needed — the user is already there or
                # another terminal has it open.  Switching blindly can
                # accidentally move the bottom pane's nested client from
                # oc-view-agents to overcode, creating a recursive display
                # that collapses the window.
                #
                # Detect nested clients by comparing client TTYs against
                # pane TTYs in the split window (#387). A nested tmux
                # client's TTY matches one of the pane TTYs.
                pane_ttys = set(
                    _tmux_output(
                        "list-panes", "-t", f"{oc_session}:{SPLIT_WINDOW_NAME}",
                        "-F", "#{pane_tty}",
                    ).splitlines()
                )
                oc_clients = _tmux_output(
                    "list-clients", "-t", oc_session,
                    "-F", "#{client_tty}",
                )
                has_real_client = any(
                    tty.strip() not in pane_ttys
                    for tty in oc_clients.splitlines()
                    if tty.strip()
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
                      "-t", f"{oc_session}:{SPLIT_WINDOW_NAME}.{get_pane_base_index()}",
                      monitor_cmd)
                rprint(f"[green]Attaching to existing {SPLIT_WINDOW_NAME} window (monitor restarted)...[/green]")
                time.sleep(0.2)
                os.execlp("tmux", "tmux", "attach-session", "-t", oc_session)
            # Always restart the monitor so code changes take effect
            _tmux("respawn-pane", "-k",
                  "-t", f"{oc_session}:{SPLIT_WINDOW_NAME}.{get_pane_base_index()}",
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
    attach_cmd = f"sh -c 'unset TMUX; exec tmux attach-session -t {linked}'"

    # If an "overcode" session exists but has no split window, it might be:
    # (a) a stale session from a previous overcode run, or
    # (b) a user's own session that happens to share the name.
    # Only kill it if it has exactly one window (likely stale from us).
    # If it has multiple windows, add our split window to it instead.
    if _tmux_check("has-session", "-t", oc_session):
        existing = _find_existing_split_window(oc_session)
        if not existing:
            win_count = _tmux_output(
                "list-windows", "-t", oc_session, "-F", "#{window_id}",
            )
            if win_count.count("\n") == 0 and win_count.strip():
                # Single window — safe to assume it's a stale overcode session
                _tmux("kill-session", "-t", oc_session)
            # If multiple windows, leave it alone — we'll add our window to it

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
            "-l", f"{100 - ratio}%",
            attach_cmd,
        )
        if result.returncode != 0:
            rprint(f"[red]Failed to create bottom pane: {result.stderr}[/red]")
            raise typer.Exit(1)

        # Focus top pane
        _tmux("select-pane", "-t", f"{oc_session}:{SPLIT_WINDOW_NAME}.{get_pane_base_index()}")

        rprint(f"[green]Split layout ready.[/green] Tab toggles panes.")

    else:
        # --- Outside tmux: create detached, split, then attach ---
        if need_new_session:
            # Use actual terminal size (fall back to 200x50 if unavailable)
            try:
                term_size = os.get_terminal_size()
                term_x, term_y = str(term_size.columns), str(term_size.lines)
            except OSError:
                term_x, term_y = "200", "50"
            result = _tmux(
                "new-session", "-d", "-s", oc_session,
                "-n", SPLIT_WINDOW_NAME,
                "-x", term_x, "-y", term_y,
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
            "-l", f"{100 - ratio}%",
            attach_cmd,
        )

        # Focus top pane
        _tmux("select-pane", "-t", f"{oc_session}:{SPLIT_WINDOW_NAME}.{get_pane_base_index()}")

        # Attach to the session (replaces this process)
        rprint(f"[green]Attaching to split layout...[/green]")
        time.sleep(0.2)
        os.execlp("tmux", "tmux", "attach-session", "-t", oc_session)
