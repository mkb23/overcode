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
from typing import Any, Collection, Dict, Iterable, List, Mapping, NamedTuple, Optional, Tuple

logger = logging.getLogger(__name__)


# Windows overcode creates for itself on the agents tmux session. None of
# them belongs to an agent in sessions.json, so every "is this window
# untracked?" check (the daemon's untracked count, `overcode cleanup
# --untracked`) must skip them explicitly: otherwise the count shows a
# permanent warning and the cleanup kills overcode's own windows.
EMPTY_PLACEHOLDER_WINDOW = "oc-empty"  # TUI dead-window placeholder (#457)
DAEMON_CLAUDE_WINDOW_NAME = "_daemon_claude"  # supervisor daemon's claude
SSH_PROXY_WINDOW_PREFIX = "ssh:"  # TUI proxies to sister agents over SSH


def is_overcode_owned_window(name: str) -> bool:
    """Whether ``name`` is a window overcode created for itself (see above)."""
    return (
        name == EMPTY_PLACEHOLDER_WINDOW
        or name == DAEMON_CLAUDE_WINDOW_NAME
        or name.startswith(SSH_PROXY_WINDOW_PREFIX)
    )


def untracked_window_names(
    windows: Iterable[Mapping[str, Any]], tracked_windows: Collection[str]
) -> List[str]:
    """Names of the windows no live agent owns and overcode did not create.

    The one definition behind the monitor daemon's untracked-window count
    and ``overcode cleanup --untracked`` (#344), so the two can never
    disagree about what is safe to kill. Skips window 0 (the session's
    default shell), every name in ``tracked_windows`` (the ``tmux_window``
    of each non-terminated session) and overcode's own windows. ``windows``
    are the dicts ``TmuxInterface.list_windows`` returns (``index`` as int
    or str, ``name``); listing order is preserved.
    """
    names: List[str] = []
    for window in windows:
        name = window["name"]
        if int(window["index"]) == 0:
            continue
        if name in tracked_windows or is_overcode_owned_window(name):
            continue
        names.append(name)
    return names


def _build_tmux_cmd() -> List[str]:
    """Build base tmux command, respecting OVERCODE_TMUX_SOCKET env var."""
    socket = os.environ.get("OVERCODE_TMUX_SOCKET")
    return ["tmux", "-L", socket] if socket else ["tmux"]


# Everything the monitor daemon's tick and the TUI's fast path want to know
# about a session's panes, from ONE ``list-panes -s -t <session>``. A
# ``get_pane_pid`` per window on a fresh RealTmux was three commands
# (list-sessions, list-windows, list-panes) plus a libtmux object per window
# for each, so a 50-agent sync cost 101 commands (audit R7); the change
# signature is what lets a loop skip the capture-pane of an unchanged pane
# (audit R11). Fields are tab-separated; tmux does not interpret escapes in a
# format, so the literal tab goes in the argument.
PANE_LISTING_FORMAT = "\t".join(
    (
        "#{window_name}",
        "#{window_index}",
        "#{pane_pid}",
        "#{window_activity}",
        "#{history_size}",
        "#{cursor_x}",
        "#{cursor_y}",
        "#{pane_current_command}",
        "#{session_attached}",
    )
)


class PaneInfo(NamedTuple):
    """One window's first pane, as ``list-panes -s -F PANE_LISTING_FORMAT`` reports it.

    ``signature`` is what moves when the pane's content does: tmux stamps
    ``window_activity`` (whole seconds) on every parsed byte of output,
    ``history_size`` grows as lines scroll into the scrollback, the cursor
    moves as text is drawn and ``pane_current_command`` changes with the
    foreground process. ``session_attached`` (clients attached to the
    session) is carried for consumers that want it and is not part of the
    signature — attaching a client changes no pane.
    """

    window_name: str
    window_index: int
    pane_pid: int
    activity: int
    history_size: int
    cursor_x: int
    cursor_y: int
    current_command: str
    session_attached: int

    @property
    def signature(self) -> Tuple[int, int, int, int, str]:
        return (
            self.activity,
            self.history_size,
            self.cursor_x,
            self.cursor_y,
            self.current_command,
        )


def parse_pane_listing(lines: Iterable[str]) -> Dict[str, PaneInfo]:
    """``{window_name: PaneInfo}`` from ``list-panes -s -F PANE_LISTING_FORMAT`` output.

    A window with several panes lists each; the first is the one
    ``RealTmux`` addresses (``window.panes[0]``: capture, send-keys, pid), so
    the first row per window wins. A window name may itself contain a tab
    (the trailing eight fields never do), and a row that does not parse is
    skipped.
    """
    panes: Dict[str, PaneInfo] = {}
    for line in lines:
        parts = line.split("\t")
        if len(parts) < 9:
            continue
        name = "\t".join(parts[:-8])
        if name in panes:
            continue
        index, pid, activity, history, cx, cy, command, attached = parts[-8:]
        try:
            panes[name] = PaneInfo(
                name,
                int(index),
                int(pid),
                int(activity),
                int(history),
                int(cx),
                int(cy),
                command,
                int(attached),
            )
        except ValueError:
            continue
    return panes


def list_panes(session: str, timeout: float = 5) -> Optional[Dict[str, PaneInfo]]:
    """Every window's first pane in ``session`` from ONE tmux command.

    None when tmux is not running or the session does not exist, so the
    caller can tell "no answer" from "no windows" (a live session always has
    one). Honours ``OVERCODE_TMUX_SOCKET``; ``RealTmux.list_panes`` is the
    same listing over its own server connection.
    """
    try:
        result = subprocess.run(
            _build_tmux_cmd() + ["list-panes", "-s", "-t", session, "-F", PANE_LISTING_FORMAT],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    return parse_pane_listing(result.stdout.splitlines())


def tui_pane_target(environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The tmux pane id this process runs in, or None outside tmux.

    tmux sets ``TMUX`` and ``TMUX_PANE`` in every pane's environment; a
    pane id (``%12``) is a valid target for any tmux command and resolves
    to the pane's own session, whichever session that is.
    """
    env = os.environ if environ is None else environ
    if not env.get("TMUX"):
        return None
    pane = env.get("TMUX_PANE", "")
    return pane if pane.startswith("%") else None


def tui_tmux_socket(environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The socket path of the tmux server this process runs under, or None.

    ``TMUX`` is ``<socket path>,<server pid>,<session index>``. It names the
    server holding this pane — not necessarily the one overcode's commands
    target: ``-L $OVERCODE_TMUX_SOCKET`` makes tmux ignore ``TMUX``, so the
    attended poll addresses this server with ``-S`` explicitly.
    """
    env = os.environ if environ is None else environ
    path = env.get("TMUX", "").split(",", 1)[0]
    return path or None


def tmux_cmd_targets_own_server(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Whether :func:`_build_tmux_cmd` reaches the server this process runs under.

    A bare ``tmux`` inside a pane follows ``TMUX``, so without
    ``OVERCODE_TMUX_SOCKET`` the answer is yes. With it, ``-L <label>`` is
    ``$TMUX_TMPDIR/tmux-<uid>/<label>`` (``/tmp`` when unset — the man
    page's rule for ``-L``), compared with ``TMUX``'s path resolved, since
    ``/tmp`` is a link to ``/private/tmp`` on macOS. False outside tmux.
    The TUI uses this to decide whether the agents session's pane listing
    describes its own server; only then can the listing's attached-client
    count stand in for a poll of this pane.
    """
    env = os.environ if environ is None else environ
    own = tui_tmux_socket(env)
    if own is None:
        return False
    label = env.get("OVERCODE_TMUX_SOCKET")
    if not label:
        return True
    tmpdir = env.get("TMUX_TMPDIR") or "/tmp"
    label_path = os.path.join(tmpdir, f"tmux-{os.getuid()}", label)
    return os.path.realpath(label_path) == os.path.realpath(own)


def query_pane_attended(
    pane: str, timeout: float = 2, socket_path: Optional[str] = None
) -> Optional[Tuple[str, int]]:
    """``(session_name, attached_clients)`` for the session holding ``pane``.

    ONE tmux command (``display-message -p``), the TUI's per-second
    "is anyone looking" signal. None when tmux cannot answer — no server,
    a timeout, or a pane that no longer exists (tmux 3.5 prints an empty
    line with status 0 for an unresolvable ``-t``) — so the caller can
    leave its state as it was rather than mistake silence for "detached".

    ``socket_path`` addresses that server with ``-S``: a pane id only means
    something on the server that issued it, and ``-L $OVERCODE_TMUX_SOCKET``
    would resolve it against another server's pane of the same id (the TUI
    passes its own, :func:`tui_tmux_socket`). Without it the command goes
    where every other one does.
    """
    base = ["tmux", "-S", socket_path] if socket_path else _build_tmux_cmd()
    try:
        result = subprocess.run(
            base + ["display-message", "-p", "-t", pane, "#{session_name}\t#{session_attached}"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    line = result.stdout.rstrip("\n")
    name, sep, attached = line.rpartition("\t")
    if not sep or not name:
        return None
    try:
        return name, int(attached)
    except ValueError:
        return None


def list_pane_pids(session: str, timeout: float = 5) -> Optional[Dict[str, int]]:
    """``{window_name: pane_pid}`` for ``session`` from one ``list-panes -s``; None if unavailable."""
    panes = list_panes(session, timeout=timeout)
    if panes is None:
        return None
    return {name: info.pane_pid for name, info in panes.items()}


def pane_for_window(panes: Mapping[str, PaneInfo], window: str) -> Optional[PaneInfo]:
    """The pane ``RealTmux`` would address for ``window``.

    By name, else — for a legacy digit-string ``tmux_window`` from before
    windows were name-addressed — by index, the order ``RealTmux._get_window``
    tries. The index scan only runs for digit-string names.
    """
    info = panes.get(window)
    if info is None and window.isdigit():
        index = int(window)
        for candidate in panes.values():
            if candidate.window_index == index:
                return candidate
    return info


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


def rename_tmux_window(server, session: str, window, new_name: str) -> bool:
    """Rename ``session``'s window ``window`` to ``new_name``; True on success.

    The target is exact (``session:=name``): a bare ``session:name`` falls
    back to a prefix match, so renaming a window that has gone would rename
    whichever window's name starts with it — another agent's (#478).
    libtmux's ``Server.cmd`` does not raise when tmux fails; it returns the
    result, so success is read from its return code and stderr.
    """
    try:
        result = server.cmd(
            "rename-window", "-t", tmux_window_target(session, window), new_name,
        )
    except Exception:
        return False
    return getattr(result, "returncode", 0) == 0 and not getattr(result, "stderr", None)


def exit_copy_mode_if_active(
    tmux_session: str,
    window: str,
) -> None:
    """Drop the target pane out of copy-mode if it's currently in it.

    tmux's copy-mode (often entered accidentally by scrolling) silently
    swallows paste-buffer inputs and interprets Enter as a copy-mode
    command. If a heartbeat (or any programmatic send) is dispatched
    while the pane is in copy-mode, the text queues up invisibly and
    only flushes when the user manually exits copy-mode — by which
    point many heartbeats may have piled up (#401).

    Calling this before send is a no-op when copy-mode is inactive.
    """
    tmux_cmd = _build_tmux_cmd()
    target = tmux_window_target(tmux_session, window)
    try:
        result = subprocess.run(
            tmux_cmd + ["display-message", "-p", "-t", target, "#{pane_in_mode}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip() == "1":
            subprocess.run(
                tmux_cmd + ["send-keys", "-t", target, "-X", "cancel"],
                timeout=5, check=False,
            )
    except (subprocess.SubprocessError, OSError) as e:
        logger.debug("copy-mode check failed for %s: %s", target, e)


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

    # Exit copy-mode first so paste-buffer / Enter aren't swallowed (#401)
    exit_copy_mode_if_active(tmux_session, window)

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
