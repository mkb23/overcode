"""
Renaming an agent (#478).

An agent is its session id (auto-naming §1): its files live in its own
directory (``agents/<id>/``, quality review item 35), its window carries
``@overcode_id``, and everything that runs inside it finds it by
``OVERCODE_SESSION_ID``. Renaming a live agent therefore renames its tmux
window and its record and leaves it a notice; it keeps running. An agent
launched before per-agent directories looks itself up by the
``OVERCODE_SESSION_NAME`` baked into its process, so it is stopped and
resumed under the new name, as every rename used to be.

``RenameMixin`` is the part of ``AgentLauncher`` that does this. It uses the
launcher's ``tmux``, ``sessions``, ``tmux_session``, ``backend_for``,
``_send_graceful_exit`` and ``_send_launch_for_session``.
"""

from typing import Optional

from .exceptions import AgentBusyError
from .session_manager import Session
from .status_constants import GREEN_STATUSES, STATUS_WAITING_APPROVAL
from .tmux_utils import AGENT_ID_OPTION


def renames_in_place(tmux_session: str, session_id: str) -> bool:
    """Whether a rename of this agent leaves it running (see module docstring).

    Every launch since per-agent directories creates ``agents/<id>/`` and
    puts ``OVERCODE_SESSION_ID`` in the agent's environment.
    """
    from .paths import agent_dir

    return agent_dir(tmux_session, session_id).is_dir()


class RenameMixin:
    """Rename a live or stopped agent (see module docstring)."""

    # Renaming a pre-0.6 agent stops and resumes it. In these statuses that
    # would cut into work — it is acting, or a permission dialog is open
    # (which the exit gesture would answer) — so that rename refuses
    # without force (#478).
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
            tmux=self.tmux.client,
            patterns=get_patterns(session_backend_name(session)),
            backend_name=session_backend_name(session),
        )
        status, _, _ = detector.detect_status(session)
        return status

    def rename(
        self,
        session: Session,
        new_name: str,
        *,
        force: bool = False,
        source: Optional[str] = None,
        graceful_exit_wait: float = 3.0,
    ) -> bool:
        """Rename an agent, preserving its conversation and telemetry (#478).

        The window becomes ``<new_name>-<id4>``, the record takes the new
        name (the old one stays on it as an alias, ``previous_names``, so
        anyone still using it reaches the agent with a notice), and the
        agent is told its new name with its next prompt. A live agent keeps
        running: its process finds itself by id (auto-naming §1).

        An agent launched before per-agent directories finds itself by the
        name in its environment, so it is stopped and resumed in place under
        the new name (``_rename_with_restart``); ``force`` applies only to
        it. Elsewhere ``force`` is accepted and ignored.

        ``source`` says who chose the name (``name_source``: ``human``,
        ``auto``, ...); it is stored with the name in the same write. None
        leaves the record's source as it is.

        Failure leaves the agent as it was: if the window rename fails, or a
        concurrent rename takes ``new_name`` first, the window keeps (or
        gets back) its old name.

        Returns:
            True if the rename completed. False if tmux refused the window
            rename (nothing changed), or, for a restarted agent, if the
            relaunch under the new name could not be sent (the rename
            itself stands; ``restart`` picks it up).

        Raises:
            InvalidSessionNameError: ``new_name`` fails the name pattern.
            AgentBusyError: a pre-0.6 agent is busy and ``force`` is False.
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
        # window and its files belong to that one.
        owner = self.for_agent(session)
        if owner is not self:
            return owner.rename(
                session, new_name, force=force, source=source,
                graceful_exit_wait=graceful_exit_wait,
            )
        if not renames_in_place(self.tmux_session, session.id):
            return self._rename_with_restart(
                session, new_name, force=force, source=source,
                graceful_exit_wait=graceful_exit_wait)

        old_name = session.name
        old_window = session.tmux_window
        new_window = f"{new_name}-{session.id[:4]}"
        window_exists = self.tmux.window_exists(old_window)
        if window_exists:
            if not self.tmux.rename_window(old_window, new_window):
                return False
            # Kept by rename-window; set for a window launched before it
            self.tmux.set_window_option(new_window, AGENT_ID_OPTION, session.id)

        # Locked duplicate check + write: a concurrent rename of another
        # agent to `new_name` cannot slip between check and update.
        if not self.sessions.rename_session(
            session.id, new_name, tmux_window=new_window, **_source_field(source)
        ):
            if window_exists:
                self.tmux.rename_window(new_window, old_window)
            raise ValueError(f"an agent named '{new_name}' already exists")

        self._keep_cli_title(session, old_name)
        self._write_rename_notice(session, old_name, new_name)
        self._write_parent_notice(session, old_name, new_name)
        self._drop_name_keyed_files(old_name)  # an older overcode's leftovers
        try:
            self._type_cli_title(session.id)
        except Exception:  # the daemon's namer tries again on its next tick
            pass
        return True

    def _type_cli_title(self, session_id: str) -> bool:
        """Type ``/rename <name>`` into a Claude agent now, if it is idle with an empty input line.

        The daemon's namer does the same on its next tick (``Namer._align``)
        and keeps trying while it can't; doing it here shows a person who
        just renamed the agent the new name in Claude at once. A busy agent
        or a half-typed input line is left to the namer.
        """
        from .config import get_naming_config
        from .namer import IDLE_STATUSES, input_line_empty, load_state, save_state

        session = self.sessions.get_session(session_id)
        if session is None or not get_naming_config().get("cli_title", True):
            return False
        backend = self.backend_for(session)
        if not backend.display_name_args(session.name):
            return False
        if load_state(self.tmux_session, session_id).cli_title == session.name:
            return False
        if not self.tmux.window_exists(session.tmux_window):
            return False
        if self._live_status(session) not in IDLE_STATUSES:
            return False
        if not input_line_empty(self.tmux.capture_pane(session.tmux_window, lines=15) or "",
                                backend):
            return False
        if not self.tmux.send_keys(session.tmux_window, f"/rename {session.name}", enter=True):
            return False
        state = load_state(self.tmux_session, session_id)
        state.cli_title = session.name
        save_state(self.tmux_session, session_id, state)
        return True

    def _keep_cli_title(self, session: Session, old_name: str) -> None:
        """Record the CLI title as the old name if nothing has recorded it yet.

        It is the launch's ``--name``; the namer types ``/rename <new>`` when
        the title lags the name (``Namer._align``). Unrecorded, it would be
        taken to be the new name.
        """
        from .namer import load_state, save_state

        state = load_state(self.tmux_session, session.id)
        if state.cli_title is None:
            state.cli_title = old_name
            save_state(self.tmux_session, session.id, state)

    def _rename_with_restart(
        self, session: Session, new_name: str, *, force: bool, graceful_exit_wait: float,
        source: Optional[str] = None,
    ) -> bool:
        """Rename an agent launched before per-agent directories (see ``rename``).

        Its process carries the old name as its identity, so a live one is
        stopped, renamed, and relaunched in place — same window, same
        conversation (resume, not fresh). A busy one
        (``RENAME_BUSY_STATUSES``) is refused unless ``force``: the stop
        would cancel its turn and kill whatever it was running, and the
        resumed agent would not pick the task back up. On failure the agent
        is relaunched under its old name.
        """
        old_name = session.name
        old_window = session.tmux_window
        window_exists = self.tmux.window_exists(old_window)
        if window_exists and not force:
            try:
                status = self._live_status(session)
            except Exception:
                # can't tell it is idle, so don't assume it
                raise AgentBusyError(old_name, "unknown") from None
            # A detected "unknown" is an agent still starting (nothing on
            # screen but its launch line): as renameable as an idle one
            if status in self.RENAME_BUSY_STATUSES:
                raise AgentBusyError(old_name, status)

        if window_exists and not self._stop_in_window(session, old_window, graceful_exit_wait):
            return False  # still running: nothing renamed, nothing typed

        new_window = f"{new_name}-{session.id[:4]}"
        if window_exists and not self.tmux.rename_window(old_window, new_window):
            # Nothing has been renamed; bring the agent back as it was.
            self._send_launch_for_session(session, old_window, fresh=False)
            return False

        # Locked duplicate check + write; the relaunch and the rename notice
        # move the agent into its own directory (agents/<id>/, item 35).
        if not self.sessions.rename_session(
            session.id, new_name, tmux_window=new_window, **_source_field(source)
        ):
            # Lost the race — restore the pre-rename identity before bailing.
            if window_exists:
                self.tmux.rename_window(new_window, old_window)
                self._send_launch_for_session(session, old_window, fresh=False)
            raise ValueError(f"an agent named '{new_name}' already exists")

        self._write_rename_notice(session, old_name, new_name)
        self._write_parent_notice(session, old_name, new_name)
        if not window_exists:
            self._drop_name_keyed_files(old_name)
            return True

        refreshed = self.sessions.get_session(session.id)
        if not self._send_launch_for_session(refreshed, new_window, fresh=False):
            return False
        # After the relaunch: the old process may have fired a last hook on
        # its way out
        self._drop_name_keyed_files(old_name)
        self.sessions.update_stats(session.id, current_task=f"Renamed from {old_name}")
        return True

    def _drop_name_keyed_files(self, old_name: str) -> None:
        """Bundle and remove what an agent launched before per-agent directories left under ``old_name``.

        The renamed agent now lives in its own directory (its relaunch and
        its rename notice create it); nobody is called ``old_name`` any
        more, so a name-keyed file under it would only mislead an older
        reader. They are its history (hook event log, report), so they are
        packed into today's bundle first, as the layout migration does.
        """
        if self.sessions.get_session_by_name(old_name) is not None:
            return
        from .layout_migration import pack_name

        pack_name(self.tmux_session, old_name)

    def _write_rename_notice(self, session: Session, old_name: str, new_name: str) -> None:
        """Leave the agent a note of its new name for its next prompt (#478).

        Delivered as prompt context by the UserPromptSubmit hook, or the
        opencode plugins' ``hook-handler --prompt-context``
        (``hook_handler._emit_rename_notice``), so it costs no extra turn;
        its conversation otherwise still calls it ``old_name``.
        Written in the agent's own directory, created here if it has none
        yet (an agent launched before them moves to it with this rename).
        """
        from .paths import agent_dir, agent_file

        path = agent_file(self.tmux_session, session.id, "rename_notice")
        try:
            agent_dir(self.tmux_session, session.id).mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"overcode: this agent was renamed from '{old_name}' to "
                f"'{new_name}'. Use '{new_name}' as your name from now on; "
                f"other agents and overcode commands now address you by it.\n",
                encoding="utf-8",
            )
        except OSError:
            pass  # a missing note is cosmetic

    def _write_parent_notice(self, session: Session, old_name: str, new_name: str) -> None:
        """Tell the agent's parent, with its next prompt, that its child has a new name (§8).

        Courtesy, not correctness: the old name still reaches the child
        (``previous_names``). Appended, so two renames before the parent's
        next prompt are both told; the hook handler consumes the file.
        """
        from .paths import agent_dir, agent_file

        parent = (self.sessions.get_session(session.parent_session_id)
                  if session.parent_session_id else None)
        if parent is None or parent.is_remote:
            return
        tmux_session = parent.tmux_session or self.tmux_session
        if not agent_dir(tmux_session, parent.id).is_dir():
            return  # a parent launched before per-agent directories has no notices
        try:
            with open(agent_file(tmux_session, parent.id, "child_notice"), "a",
                      encoding="utf-8") as f:
                f.write(f"overcode: your child agent '{old_name}' is now called "
                        f"'{new_name}'; the old name still reaches it.\n")
        except OSError:
            pass  # a missing note is cosmetic


def _source_field(source: Optional[str]) -> dict:
    """The ``rename_session`` field that stores who chose the name, if known."""
    return {"name_source": source} if source else {}
