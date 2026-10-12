"""
Renaming an agent (#478).

An agent's name is load-bearing: its tmux window, the per-agent files keyed
by it, and its session record. ``RenameMixin`` is the part of
``AgentLauncher`` that stops a live agent, rekeys all of those and resumes
it under the new name. It uses the launcher's ``tmux``, ``sessions``,
``tmux_session``, ``backend_for``, ``_send_graceful_exit`` and
``_send_launch_for_session``.

The name keys exist because per-agent files are keyed by name, not id;
keying them by session id (quality review item 35) would retire most of this.
"""

import os
import time
from pathlib import Path
from typing import List, Tuple

from .exceptions import AgentBusyError
from .session_manager import Session
from .status_constants import GREEN_STATUSES, STATUS_WAITING_APPROVAL
from .tmux_manager import TmuxManager


class RenameMixin:
    """Rename a live or stopped agent (see module docstring)."""

    # Renaming a live agent stops and resumes it. In these statuses that
    # would cut into work — it is acting, or a permission dialog is open
    # (which the exit gesture would answer) — so rename refuses without
    # force (#478).
    RENAME_BUSY_STATUSES = GREEN_STATUSES | {STATUS_WAITING_APPROVAL}

    # Names overcode treats specially: the supervisor excludes an agent
    # called this from supervision.
    RESERVED_AGENT_NAMES = frozenset({"daemon_claude"})

    def _live_status(self, session: Session) -> str:
        """The agent's status now, detected directly (the daemon may be stale)."""
        from .backends import session_backend_name
        from .settings import resolve_detection_mode
        from .status_detector_factory import (
            create_status_detector,
            resolve_session_detection_mode,
        )
        from .status_patterns import get_patterns

        detector = create_status_detector(
            session.tmux_session,
            strategy=resolve_session_detection_mode(
                session, resolve_detection_mode(session.tmux_session)
            ),
            tmux=self.tmux._tmux,
            patterns=get_patterns(session_backend_name(session)),
            backend_name=session_backend_name(session),
        )
        status, _, _ = detector.detect_status(session)
        return status

    @staticmethod
    def _name_keyed_paths(tmux_session: str, name: str) -> List[Path]:
        """Every per-agent file whose name is the agent's name.

        Hook state and event log (status detection, stats, opencode's
        conversation ids) and the child's ``overcode report`` file.
        """
        from .hook_handler import _get_hook_event_log_path, _get_hook_state_path
        from .settings import get_session_dir

        return [
            _get_hook_state_path(tmux_session, name),
            _get_hook_event_log_path(tmux_session, name),
            get_session_dir(tmux_session) / f"report_{name}.json",
        ]

    def _move_name_keyed_files(
        self, tmux_session: str, old_name: str, new_name: str,
    ) -> List[Tuple[Path, Path]]:
        """Rekey the agent's name-keyed files to ``new_name``; returns the moves.

        A file already at a new-name path belongs to no agent (nobody is
        called ``new_name``) — a leftover from an earlier agent of that name
        — and is removed, so the renamed agent cannot inherit a stale status
        or a stale report that would mark it done.
        """
        moved: List[Tuple[Path, Path]] = []
        for old_path, new_path in zip(
            self._name_keyed_paths(tmux_session, old_name),
            self._name_keyed_paths(tmux_session, new_name),
        ):
            try:
                new_path.unlink(missing_ok=True)
                if old_path.exists():
                    new_path.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(old_path, new_path)
                    moved.append((old_path, new_path))
            except OSError:
                pass  # telemetry continuity is best-effort, not blocking
        return moved

    def rename(
        self,
        session: Session,
        new_name: str,
        *,
        force: bool = False,
        graceful_exit_wait: float = 3.0,
    ) -> bool:
        """Rename an agent, preserving its conversation and telemetry (#478).

        An agent's name is load-bearing: the tmux window name, the files
        keyed by it (hook state and event log, which the running process
        writes under the ``OVERCODE_SESSION_NAME`` baked into it, and the
        ``overcode report`` file), and the session record. Renaming therefore
        stops a live agent, rekeys all of those, and relaunches it in place —
        same window, same conversation (resume, not fresh) — under the new
        name. The old name stays on the record as an alias
        (``previous_names``), so anyone still using it reaches the agent with
        a notice, and the agent is told its new name with its next prompt.

        A busy agent (``RENAME_BUSY_STATUSES``) is refused unless ``force``:
        the stop would cancel its turn and kill whatever it was running, and
        the resumed agent would not pick the task back up.

        Failure leaves the agent as it was: if the window rename fails, or a
        concurrent rename takes ``new_name`` first, everything is put back
        and the agent is relaunched under its old name.

        Returns:
            True if the rename completed. False if tmux refused the window
            rename (agent restored under its old name), or if the relaunch
            under the new name could not be sent (the rename itself stands;
            ``restart`` picks it up).

        Raises:
            InvalidSessionNameError: ``new_name`` fails the name pattern.
            AgentBusyError: the agent is busy and ``force`` is False.
            ValueError: ``new_name`` is taken, reserved, or unchanged.
        """
        from .launcher import validate_session_name

        validate_session_name(new_name)
        if new_name in self.RESERVED_AGENT_NAMES:
            raise ValueError(f"'{new_name}' is reserved by overcode")
        if new_name == session.name:
            raise ValueError(f"the agent is already called '{new_name}'")
        # Fast duplicate rejection; the authoritative check runs under the
        # session-state lock at commit time (rename_session below).
        for other in self.sessions.list_sessions():
            if other.id != session.id and other.name == new_name:
                raise ValueError(f"an agent named '{new_name}' already exists")

        # The agent lives in its own tmux session, which need not be the one
        # this launcher was built for (the CLI's --session default): its
        # window, its hook files and the relaunch all belong to that one.
        if session.tmux_session and session.tmux_session != self.tmux_session:
            return type(self)(
                session.tmux_session,
                tmux_manager=TmuxManager(
                    session.tmux_session, tmux=self.tmux._tmux, socket=self.tmux.socket,
                ),
                session_manager=self.sessions,
            ).rename(
                session, new_name, force=force, graceful_exit_wait=graceful_exit_wait,
            )

        old_name = session.name
        old_window = session.tmux_window
        window_exists = self.tmux.window_exists(old_window)
        if window_exists and not force:
            try:
                status = self._live_status(session)
            except Exception:
                status = "unknown"  # can't tell it is idle, so don't assume it
            if status in self.RENAME_BUSY_STATUSES or status == "unknown":
                raise AgentBusyError(old_name, status)

        if window_exists:
            self._send_graceful_exit(self.backend_for(session), old_window)
            time.sleep(graceful_exit_wait)

        new_window = f"{new_name}-{session.id[:4]}"
        if window_exists and not self.tmux.rename_window(old_window, new_window):
            # Nothing has been renamed; bring the agent back as it was.
            self._send_launch_for_session(session, old_window, fresh=False)
            return False

        moved = self._move_name_keyed_files(self.tmux_session, old_name, new_name)

        # Locked duplicate check + write: a concurrent rename of another
        # agent to `new_name` cannot slip between check and update.
        if not self.sessions.rename_session(
            session.id, new_name, tmux_window=new_window
        ):
            # Lost the race — restore the pre-rename identity before bailing.
            for old_path, new_path in reversed(moved):
                try:
                    os.replace(new_path, old_path)
                except OSError:
                    pass
            if window_exists:
                self.tmux.rename_window(new_window, old_window)
                self._send_launch_for_session(session, old_window, fresh=False)
            raise ValueError(f"an agent named '{new_name}' already exists")

        self._write_rename_notice(old_name, new_name)
        if not window_exists:
            return True

        refreshed = self.sessions.get_session(session.id)
        if not self._send_launch_for_session(refreshed, new_window, fresh=False):
            return False
        # The old process may have fired a last hook on its way out, after
        # the files moved; nobody is called old_name any more, so anything
        # under it now is stale and would mislead a later agent of that name.
        if self.sessions.get_session_by_name(old_name) is None:
            for path in self._name_keyed_paths(self.tmux_session, old_name):
                path.unlink(missing_ok=True)
        self.sessions.update_stats(session.id, current_task=f"Renamed from {old_name}")
        return True

    def _write_rename_notice(self, old_name: str, new_name: str) -> None:
        """Leave the agent a note of its new name for its next prompt (#478).

        Delivered as prompt context by the UserPromptSubmit hook
        (``hook_handler._emit_rename_notice``), so it costs no extra turn;
        its resumed conversation otherwise still calls it ``old_name``.
        """
        from .hook_handler import get_rename_notice_path

        get_rename_notice_path(self.tmux_session, old_name).unlink(missing_ok=True)
        path = get_rename_notice_path(self.tmux_session, new_name)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"[overcode] This agent was renamed from '{old_name}' to "
                f"'{new_name}'. Use '{new_name}' as your name from now on; "
                f"other agents and overcode commands now address you by it.\n",
                encoding="utf-8",
            )
        except OSError:
            pass  # a missing note is cosmetic
