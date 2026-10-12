"""Every tmux side effect of the TUI, behind one object.

Since 0.6.0 the TUI runs only as the top pane of the ``overcode tmux``
split (cli/split.py): navigating agents switches the window of the linked
session the bottom pane shows, dialogs zoom the top pane, and so on.
``TmuxSplit`` does all of that. An app built without a linked session
(unit tests) gets ``NullSplit``, whose every method does nothing, so the
"are we in the split?" gate lives in ``make_split`` and nowhere else.
"""

from __future__ import annotations

import subprocess
import threading
from typing import TYPE_CHECKING, Iterable, Optional

from .tmux_utils import SSH_PROXY_WINDOW_PREFIX, get_pane_base_index
from .tmux_utils import _build_tmux_cmd as _tmux_base
from .tui_logic import windows_needing_resize
from .worker_guard import worker_cancelled

if TYPE_CHECKING:  # pragma: no cover
    from .session_manager import Session
    from .tui import SupervisorTUI
    from .tui_widgets import SessionSummary


def split_window_target() -> str:
    """The split's window, ``overcode:overcode-tmux`` (cli/split.py's names)."""
    from .cli.split import SPLIT_SESSION_NAME, SPLIT_WINDOW_NAME
    return f"{SPLIT_SESSION_NAME}:{SPLIT_WINDOW_NAME}"


class NullSplit:
    """Outside the split: no tmux at all."""

    active = False
    sync_target: Optional[str] = None

    def zoom(self) -> None:
        """Zoom the TUI pane (a dialog, the sister view or jobs view opened)."""

    def unzoom(self) -> None:
        """Clear the TUI pane's zoom, revealing the bottom pane."""

    def show_agent(self, widget: Optional["SessionSummary"]) -> None:
        """Switch the bottom pane to ``widget``'s agent."""

    def fit_window(self, widget: "SessionSummary") -> None:
        """Size ``widget``'s agent window to the terminal."""

    def schedule_agent_window_resize(self) -> None:
        """Resize every agent window to the bottom pane (in a worker)."""

    def resize_agent_windows(self, windows: list) -> None:
        """Worker: resize ``windows`` of the linked session to the bottom pane."""

    def resize_focused_window(self, widget: Optional["SessionSummary"]) -> None:
        """The `r` key: size the focused agent's window to the bottom pane."""

    def focus_bottom_pane(self) -> None:
        """Hand the keyboard to the bottom pane."""

    def resize_split(self, delta: int) -> None:
        """Move the split's divider ``delta`` rows (positive: TUI pane grows)."""

    def detach(self) -> None:
        """Detach the tmux client (``q``)."""

    def cleanup_stale_ssh_proxies(self) -> None:
        """Kill ssh proxy windows a previous TUI left behind."""

    def cleanup_ssh_proxies(self) -> None:
        """Kill this TUI's ssh proxy windows (exit)."""


class TmuxSplit(NullSplit):
    """The top pane of the ``overcode tmux`` split, with ``sync_target`` below."""

    active = True

    def __init__(self, app: "SupervisorTUI", sync_target: str) -> None:
        from .implementations import RealTmux
        from .tmux_manager import TmuxManager

        self.app = app
        self.sync_target = sync_target
        self._tmux = RealTmux()
        # TmuxManager for the agents session, where ssh proxy windows live
        self._tmux_manager = TmuxManager(app.tmux_session)
        # SSH proxy windows for remote agents: session_id -> tmux window name
        self._ssh_proxies: dict[str, str] = {}

    # ── Pane targets ───────────────────────────────────────────────────

    def tui_pane_target(self) -> str:
        """The TUI (top) pane, respecting pane-base-index."""
        return f"{split_window_target()}.{get_pane_base_index()}"

    def bottom_pane_target(self) -> str:
        """The bottom (terminal) pane, respecting pane-base-index."""
        return f"{split_window_target()}.{get_pane_base_index() + 1}"

    def _bottom_pane_size(self, timeout: Optional[float] = 2) -> Optional[tuple[int, int]]:
        """(width, height) of the bottom pane, or None if tmux cannot say."""
        try:
            result = subprocess.run(
                [*_tmux_base(), "display-message", "-t", self.bottom_pane_target(),
                 "-p", "#{pane_width} #{pane_height}"],
                capture_output=True, text=True, timeout=timeout,
            )
        except (subprocess.SubprocessError, OSError):
            return None
        if result.returncode != 0 or not result.stdout.strip():
            return None
        parts = result.stdout.strip().split()
        if len(parts) != 2:
            return None
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            return None

    # ── Zoom ───────────────────────────────────────────────────────────

    def _zoomed(self, target: str) -> bool:
        info = subprocess.run(
            [*_tmux_base(), "display-message", "-t", target, "-p", "#{window_zoomed_flag}"],
            capture_output=True, text=True,
        )
        return info.returncode == 0 and info.stdout.strip() == "1"

    def zoom(self) -> None:
        """Zoom the TUI pane, hiding the bottom pane, unless already zoomed."""
        target = self.tui_pane_target()
        if self._zoomed(target):
            return
        subprocess.run([*_tmux_base(), "resize-pane", "-t", target, "-Z"], capture_output=True)

    def unzoom(self) -> None:
        """Clear the zoom, if set. ``resize-pane -Z`` toggles, so check first."""
        target = self.tui_pane_target()
        if self._zoomed(target):
            subprocess.run([*_tmux_base(), "resize-pane", "-t", target, "-Z"], capture_output=True)

    # ── The bottom pane follows the focused agent ─────────────────────

    def show_agent(self, widget: Optional["SessionSummary"]) -> None:
        """Switch the linked session's window to ``widget``'s agent.

        For remote agents with SSH configured, creates a local SSH proxy
        window that attaches to the remote tmux window. The proxy persists
        so switching back is instant. For remote agents without SSH, the
        app opens the sister view (zoom + preview of polled content).
        Silent on failure: navigation must never be disrupted.
        """
        from .tui_widgets import SessionSummary

        app = self.app
        try:
            if not isinstance(widget, SessionSummary):
                return
            session = widget.session
            sync_session = self.sync_target

            if session.is_remote and session.source_ssh:
                # The SSH proxy uses the bottom pane: leave the sister view
                if app._sister_zoom_active:
                    app._exit_sister_view()
                proxy_window = self._get_or_create_ssh_proxy(session)
                if proxy_window:
                    self._tmux.select_window(sync_session, proxy_window)
                return

            # Non-SSH remote: the sister view
            if session.is_remote and not app._sister_zoom_active:
                app._enter_sister_view()
            elif not session.is_remote and app._sister_zoom_active:
                app._exit_sister_view()

            window_index = session.tmux_window
            # For remote agents without a tmux window, try to sync to parent's window
            if (not window_index or window_index == "") and session.is_remote and session.parent_session_id:
                parent = app.session_manager.get_session(session.parent_session_id)
                if parent:
                    window_index = parent.tmux_window

            if window_index is not None and window_index != "":
                if not self._tmux.select_window(sync_session, window_index):
                    self._select_empty_placeholder(sync_session, session.name)
                    app.notify(
                        f"Window for '{session.name}' no longer exists",
                        severity="warning",
                    )
            else:
                # Local agent with no tmux_window value — same dead-window
                # symptom for the user. Fall back to the placeholder.
                self._select_empty_placeholder(sync_session, session.name)
        except Exception:
            pass  # Silent fail - don't disrupt navigation

    def _select_empty_placeholder(self, sync_session: str, agent_name: str) -> None:
        """Switch the bottom pane to a static placeholder window (#457).

        Called when a real agent window is missing so the user sees a clear
        empty-state instead of stale content from the previous focus target.
        Idempotent — the placeholder is created on first need and reused.
        """
        from .tmux_manager import EMPTY_PLACEHOLDER_WINDOW
        msg = (
            f"\n  This agent has no tmux window.\n"
            f"  ({agent_name} may have been killed, is mid-launch,\n"
            f"   or its window vanished.)\n"
        )
        try:
            if self._tmux.ensure_empty_placeholder_window(
                sync_session, EMPTY_PLACEHOLDER_WINDOW, msg
            ):
                self._tmux.select_window(sync_session, EMPTY_PLACEHOLDER_WINDOW)
        except Exception:
            pass

    # ── SSH proxies for sisters' agents ───────────────────────────────

    def _get_or_create_ssh_proxy(self, session: "Session") -> Optional[str]:
        """Get or create an SSH proxy window for a remote agent.

        Returns the local tmux window name, or None on failure.
        """
        proxy_name = f"{SSH_PROXY_WINDOW_PREFIX}{session.source_host}:{session.name}"

        # Check if proxy already exists in our cache
        if session.id in self._ssh_proxies:
            window_name = self._ssh_proxies[session.id]
            # Verify it still exists
            if self._tmux_manager.window_exists(window_name):
                return window_name
            # Window was closed — remove stale entry
            del self._ssh_proxies[session.id]

        # Kill any stale proxy windows with this name (e.g., leftover from
        # a previous TUI session) to avoid duplicates that break window lookup.
        try:
            sess = self._tmux_manager._get_session()
            if sess:
                for win in list(sess.windows):
                    if win.window_name == proxy_name:
                        win.kill()
        except Exception:
            pass

        # Need the remote window name to attach to.
        # tmux_window may be empty if remote runs an older version — fall back to agent name
        # (the SSH command does prefix matching to find "name-uuid" windows).
        remote_window = session.tmux_window or session.name
        if not remote_window:
            return None

        # Create proxy window
        remote_tmux_session = session.source_tmux_session or "agents"

        window_name = self._tmux_manager.create_ssh_proxy_window(
            window_name=proxy_name,
            ssh_target=session.source_ssh,
            remote_tmux_session=remote_tmux_session,
            remote_window=remote_window,
        )
        if window_name:
            self._ssh_proxies[session.id] = window_name
        return window_name

    def cleanup_stale_ssh_proxies(self) -> None:
        """Kill any ssh: proxy windows left over from a previous TUI session.

        Called on startup to prevent duplicate window names that break
        libtmux's window lookup. Uses direct libtmux iteration to handle
        duplicates safely (TmuxManager.kill_window fails on duplicate names).
        """
        try:
            sess = self._tmux_manager._get_session()
            if sess is None:
                return
            for win in list(sess.windows):
                if win.window_name.startswith(SSH_PROXY_WINDOW_PREFIX):
                    try:
                        win.kill()
                    except Exception:
                        pass
        except Exception:
            pass

    def cleanup_ssh_proxies(self) -> None:
        """Kill all SSH proxy windows. Called on TUI exit."""
        for session_id, window_name in list(self._ssh_proxies.items()):
            try:
                self._tmux_manager.kill_window(window_name)
            except Exception:
                pass
        self._ssh_proxies.clear()

    # ── Window sizes ───────────────────────────────────────────────────

    def fit_window(self, widget: "SessionSummary") -> None:
        """Auto-fix tmux window size mismatch when switching sessions.

        When terminal is resized while a session is not active, only the active
        window gets resized. When switching to that session, its window might be
        the wrong size. This sends a tmux command to resize it to match the
        current terminal dimensions.

        For remote agents with SSH, sends a resize command via the sister API
        so the remote tmux window matches the local proxy pane dimensions.

        The fix is silent and non-blocking — if it fails, navigation continues.
        """
        size = self.app.size
        if not size or size.height <= 0:
            return
        try:
            session = widget.session

            # Remote agents with SSH: resize via API
            if session.is_remote and session.source_ssh and session.name:
                self._resize_remote_agent(session)
                return

            window_index = session.tmux_window
            if not window_index or window_index == "":
                return
            self._tmux.resize_window(self.sync_target, window_index, size.width, size.height)
        except Exception:
            pass  # Silent fail - size mismatch isn't critical

    def _resize_remote_agent(self, session: "Session") -> None:
        """Resize a remote agent's tmux window via the sister API, off the main thread."""
        threading.Thread(target=self._resize_remote_agent_sync, args=(session,),
                         name="remote-resize", daemon=True).start()

    def _resize_remote_agent_sync(self, session: "Session") -> None:
        """Read the bottom pane's size and send it to the agent's sister."""
        try:
            tui_pane_index = get_pane_base_index()
            result = subprocess.run(
                [*_tmux_base(), "list-panes", "-t", split_window_target(),
                 "-F", "#{pane_index} #{pane_width} #{pane_height}"],
                capture_output=True, text=True, timeout=2,
            )
            if result.returncode != 0:
                return

            width = height = 0
            for line in result.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) == 3 and parts[0] != str(tui_pane_index):
                    width, height = int(parts[1]), int(parts[2])
                    break

            if width <= 0 or height <= 0:
                return

            from .sister_controller import SisterController
            SisterController(timeout=3).resize_agent(
                session.source_url, session.source_api_key,
                session.name, width, height,
            )
        except Exception:
            pass  # Silent fail

    def schedule_agent_window_resize(self) -> None:
        """Resize every local agent's window to the bottom pane, in the app's worker.

        The window list is read here, on the main thread; the worker only
        talks to tmux.
        """
        windows = [
            session.tmux_window for session in list(self.app.sessions)
            if not getattr(session, "is_remote", False)
            and getattr(session, "tmux_window", None)
        ]
        self.app._resize_agent_windows_async(windows)

    def resize_agent_windows(self, windows: Iterable[str]) -> None:
        """Worker: read the bottom pane's size, then resize each window that differs."""
        size = self._bottom_pane_size()
        if size is None:
            return
        width, height = size
        sync_session = self.sync_target
        # One list-windows call tells us which windows are already the right
        # size; resize-window is not free even when nothing changes (it fires
        # layout hooks and redraws), so in the steady state this sweep sends
        # no per-window commands at all.
        current_sizes: dict[str, tuple[int, int]] = {}
        try:
            listed = subprocess.run(
                [*_tmux_base(), "list-windows", "-t", sync_session,
                 "-F", "#{window_name}\t#{window_width}\t#{window_height}"],
                capture_output=True, text=True, timeout=2,
            )
            if listed.returncode == 0:
                for line in listed.stdout.splitlines():
                    parts = line.split("\t")
                    if len(parts) == 3:
                        try:
                            current_sizes[parts[0]] = (int(parts[1]), int(parts[2]))
                        except ValueError:
                            continue
        except (subprocess.SubprocessError, OSError):
            pass  # Unknown sizes → resize everything, as before
        for window in windows_needing_resize(current_sizes, list(windows), width, height):
            if worker_cancelled():
                return  # app exit; the next pass picks up the remaining windows
            target = f"{sync_session}:{window}"
            try:
                subprocess.run(
                    [*_tmux_base(), "resize-window", "-t", target,
                     "-x", str(width), "-y", str(height)],
                    capture_output=True, timeout=2,
                )
            except (subprocess.SubprocessError, OSError):
                continue

    def resize_focused_window(self, widget: Optional["SessionSummary"]) -> None:
        """Resize the focused agent's window to the bottom pane (the `r` key).

        For remote agents with SSH, sends the resize via the sister API.
        """
        app = self.app
        try:
            if not widget:
                return
            session = widget.session
            size = self._bottom_pane_size(timeout=None)
            if size is None:
                app.notify("Could not read pane size", severity="warning", timeout=2)
                return
            pane_width, pane_height = size

            if session.is_remote and session.source_ssh:
                # Remote agent: resize via sister API
                from .sister_controller import SisterController
                ctrl_result = SisterController(timeout=3).resize_agent(
                    session.source_url, session.source_api_key,
                    session.name, pane_width, pane_height,
                )
                if ctrl_result.ok:
                    app.notify(f"Resized {session.name} to {pane_width}x{pane_height}",
                               severity="information", timeout=2)
                else:
                    app.notify(f"Resize failed: {ctrl_result.error}", severity="error", timeout=3)
            else:
                # Local agent: resize via tmux directly
                window_name = session.tmux_window
                if not window_name:
                    return
                result = subprocess.run(
                    [*_tmux_base(), "resize-window", "-t", f"{self.sync_target}:{window_name}",
                     "-x", str(pane_width), "-y", str(pane_height)],
                    capture_output=True, text=True,
                )
                if result.returncode != 0:
                    app.notify(f"Resize failed: {result.stderr.strip()}", severity="error", timeout=3)
                else:
                    app.notify(f"Resized {session.name} to {pane_width}x{pane_height}",
                               severity="information", timeout=2)
        except Exception as e:
            app.notify(f"Resize failed: {e}", severity="error", timeout=3)

    # ── The split itself ───────────────────────────────────────────────

    def focus_bottom_pane(self) -> None:
        try:
            subprocess.run([*_tmux_base(), "select-pane", "-t", self.bottom_pane_target()],
                           capture_output=True, timeout=2)
        except (subprocess.SubprocessError, OSError):
            pass

    def resize_split(self, delta: int) -> None:
        """Move the divider, then let the linked session's windows follow the bottom pane."""
        subprocess.run(
            [*_tmux_base(), "resize-pane", "-t", self.tui_pane_target(),
             "-U" if delta > 0 else "-D", str(abs(delta))],
            capture_output=True,
        )
        linked = self.sync_target
        if linked:
            result = subprocess.run(
                [*_tmux_base(), "list-windows", "-t", linked, "-F", "#{window_id}"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                for win_id in result.stdout.strip().splitlines():
                    subprocess.run(
                        [*_tmux_base(), "resize-window", "-t", win_id, "-A"],
                        capture_output=True,
                    )

    def detach(self) -> None:
        subprocess.run([*_tmux_base(), "detach-client"], capture_output=True)


NULL_SPLIT = NullSplit()


def make_split(app: "SupervisorTUI", sync_target: Optional[str]) -> NullSplit:
    """The one gate: a TmuxSplit with a linked session, else the null split."""
    if sync_target is None:
        return NULL_SPLIT
    return TmuxSplit(app, sync_target)
