"""
Launcher for interactive agent-CLI sessions in tmux windows.

All sessions launched by overcode are interactive - users can take over at
any time. Initial prompts are sent as keystrokes after the agent starts,
not as CLI arguments.

Which CLI gets launched, and with what argv, comes from the session's
``AgentBackend`` (see ``overcode.backends``).
"""

import shlex
from contextlib import contextmanager
import os
import sys
import time
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

import re

from . import get_full_version
from .backends import (
    DEFAULT_BACKEND,
    AgentBackend,
    BackendCapability,
    LaunchSpec,
    UnknownBackendError,
    check_effort,
    effort_applies,
    get_backend,
    supports,
)
from .backends.claude_code import (  # noqa: F401  (compat re-export)
    _build_launch_settings,
    _resolve_overcode_bin,
)
from .backends.shell import SHELL_BACKEND
from .tmux_manager import TmuxManager, EMPTY_PLACEHOLDER_WINDOW  # noqa: F401
from .tmux_utils import AGENT_ID_OPTION
from .agent_identity import current_agent, env_agent_id, env_agent_name, in_agent
from .session_manager import SessionManager, Session, rename_notice
from .config import get_default_standing_instructions
from .launch_settings import NORMAL_PERMISSIONS_CONFLICT, LaunchArgs, resolve_launch_settings
from .ledger import billing_lookup
from .dependency_check import require_tmux, require_agent_cli
from .exceptions import (
    AgentCliNotFoundError, InvalidSessionNameError, LaunchError,
    TmuxNotFoundError,
)
from .agent_files import prepare_agent_dir, read_brief, write_brief
from .agent_io import AgentIOMixin
from .lifecycle import (  # noqa: F401  (window_* and LifecycleReconcile re-exported)
    AgentLifecycleMixin,
    LifecycleReconcile,
    window_in_lookup,
    window_lookup,
)
from .protocols import TmuxTimeoutError
from .rename import RenameMixin


TMUX_NOT_ANSWERING = "tmux is not responding; try again in a moment"


@contextmanager
def _tmux_answers(action: str = "launch"):
    """A tmux read that timed out (``TmuxTimeoutError``) as a LaunchError.

    A bounded TmuxManager raises rather than answer "gone" when the server
    is slow; for a launch that is a clean error, not a traceback.
    """
    try:
        yield
    except TmuxTimeoutError as e:
        raise LaunchError(TMUX_NOT_ANSWERING, action) from e


# Valid session name pattern
SESSION_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def drop_model_flag(extra_args: Optional[List[str]]) -> Optional[List[str]]:
    """Remove ``--model X`` / ``--model=X`` from pass-through CLI args.

    Backends append extra args after their own ``--model``, so a model
    passed through at launch would win on every relaunch over
    ``Session.model``, which the daemon keeps at the model the agent last
    ran on (#505).
    """
    if not extra_args:
        return extra_args
    kept = []
    skip = False  # the value may be the next list item
    for arg in extra_args:
        tokens = shlex.split(arg)
        out = []
        for tok in tokens:
            if skip:
                skip = False
            elif tok == "--model":
                skip = True
            elif not tok.startswith("--model="):
                out.append(tok)
        if out:
            kept.append(arg if out == tokens else shlex.join(out))
    return kept


# Variables that mark the process that started the tmux server, never the
# agent: a host Claude session's (CLAUDE_CODE_* identity, socket, pid,
# effort, job dir) and an overcode agent's parent link and backend, which
# the launch line sets itself when they apply. Cleared from every launch
# line (_build_launch_cmd_str) unless the line sets them.
HOST_SESSION_VARS = (
    "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_BRIDGE_SESSION_ID", "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_AGENT", "CLAUDE_CODE_EXECPATH",
    "CLAUDE_PID", "CLAUDE_EFFORT", "CLAUDE_JOB_DIR",
    "OVERCODE_PARENT_SESSION_ID", "OVERCODE_PARENT_NAME", "OVERCODE_BACKEND",
)


# Restart and rename-by-restart wait this long for an agent to give its
# shell the terminal back after the exit gesture, looking this often (#549)
EXIT_WAIT_SECONDS = 15.0
EXIT_POLL_SECONDS = 0.25


def launch_bedrock_routing(model: Optional[str], environ=None) -> str:
    """The routing a Bedrock launch's requests take (``pricing.bedrock_routing``).

    From the model id overcode passes, else ``ANTHROPIC_MODEL``; the prefix
    from ``bedrock.routing`` in config, else ``ANTHROPIC_BEDROCK_REGION_PREFIX``;
    the region overcode sets as ``AWS_REGION`` (``bedrock.region``). The
    environment is overcode's own: agents start with what tmux passes on.
    """
    from .config import get_bedrock_config
    from .pricing import bedrock_routing

    env = os.environ if environ is None else environ
    cfg = get_bedrock_config()
    return bedrock_routing(
        model=model or env.get("ANTHROPIC_MODEL"),
        prefix=cfg.get("routing") or env.get("ANTHROPIC_BEDROCK_REGION_PREFIX"),
        region=cfg.get("region"),
    )


def validate_session_name(name: str) -> None:
    """Validate session name format.

    Args:
        name: Session name to validate

    Raises:
        InvalidSessionNameError: If name is invalid
    """
    if not name:
        raise InvalidSessionNameError(name, "name cannot be empty")
    if not SESSION_NAME_PATTERN.match(name):
        raise InvalidSessionNameError(name)


# The largest first prompt put on an agent's command line; a bigger one is
# sent once the agent is up. Well under Linux's 128 KiB limit on a single
# argument, leaving the rest of the argv and environment room in ARG_MAX.
PROMPT_ARG_MAX_BYTES = 64 * 1024


class AgentLauncher(AgentIOMixin, RenameMixin, AgentLifecycleMixin):
    """Launches interactive agent-CLI sessions in tmux windows.

    Backend-neutral: the argv grammar, gestures and startup handshake for
    each agent CLI live in ``overcode.backends``; this class owns the tmux
    orchestration around them.

    All sessions are interactive - this is the only supported mode.
    Users can take over any session at any time via tmux.

    Launch, fork, restart and revive live here; the rest is mixed in:
    sending to and reading an agent (``agent_io``), renaming it
    (``rename``), and killing it and settling its lifecycle
    (``lifecycle``).
    """

    # Waits between key presses and around a graceful exit go through this,
    # so tests can skip them (tests/unit/conftest.py) without patching the
    # shared time module
    sleep = staticmethod(time.sleep)

    def __init__(
        self,
        tmux_session: str = "agents",
        tmux_manager: TmuxManager = None,
        session_manager: SessionManager = None,
    ):
        """Initialize the launcher.

        Args:
            tmux_session: Name of the tmux session to use
            tmux_manager: Optional TmuxManager for dependency injection (testing)
            session_manager: Optional SessionManager for dependency injection (testing)
        """
        self.tmux = tmux_manager if tmux_manager else TmuxManager(tmux_session)
        self.sessions = session_manager if session_manager else SessionManager()
        self.tmux_session = tmux_session

    def _resolve(self, name: str) -> Optional[Session]:
        """The agent ``name`` addresses, following renames (#478).

        Prints a notice to stderr when ``name`` is an old name, so a caller
        still holding it learns the new one.
        """
        session = self.sessions.resolve_session_name(name)
        notice = rename_notice(name, session)
        if notice:
            print(notice, file=sys.stderr)
        return session

    def for_agent(self, session: Session) -> "AgentLauncher":
        """A launcher for the tmux session ``session`` lives in (``self`` if this one).

        Agents are looked up fleet-wide, but this launcher is bound to one
        tmux session (the CLI's --session default): acting on an agent of
        another one here would miss its window, so kill would drop a live
        agent's record and restart would start a second copy of it.
        """
        if not session.tmux_session or session.tmux_session == self.tmux_session:
            return self
        return type(self)(
            session.tmux_session,
            tmux_manager=TmuxManager(
                session.tmux_session, tmux=self.tmux.client, socket=self.tmux.socket,
            ),
            session_manager=self.sessions,
        )

    def _window_alive(self, session: Session) -> bool:
        """Whether ``session``'s window exists, in whichever tmux session it lives in."""
        return self.for_agent(session).tmux.window_exists(session.tmux_window)

    # Maximum nesting depth for agent hierarchy (#244)
    MAX_HIERARCHY_DEPTH = 5

    def _parent_billing_code(self, parent: Session) -> Tuple[str, str]:
        """``(id, name)`` of the code a child of ``parent`` bills to: the parent's, else its root's.

        A parent an older overcode launched has no code until a daemon
        housekeeping pass gives it its root's; a child launched before then
        must take that code too, not the parent's name, or its spend is
        split from the rest of its tree for good (codes are sticky). A
        legacy code string is mapped to its code (``codes.adopt``).
        """
        codes = self.sessions.ledger.codes
        if parent.billing_code_id:
            code_id = codes.canonical(parent.billing_code_id)
            return code_id, codes.name_of(code_id)
        lookup: dict = {}
        if not parent.billing_code and parent.parent_session_id:
            try:
                lookup = billing_lookup(self.sessions.list_sessions(),
                                        self.sessions.iter_archived_records(newest_first=True))
            except Exception:  # unreadable archive: the parent's name, as before
                lookup = {}
        return self.sessions._code_id_of(parent, lookup)

    def backend_for(self, session: Session) -> AgentBackend:
        """Resolve the agent backend that owns a session's CLI grammar."""
        return get_backend(getattr(session, "backend", DEFAULT_BACKEND))

    def _send_graceful_exit(self, backend: AgentBackend, window_name: str) -> None:
        """Send the backend's shutdown gesture to a window."""
        for press in backend.graceful_exit_keys():
            self.tmux.send_keys(window_name, press.keys, enter=press.enter)
            if press.delay_after:
                self.sleep(press.delay_after)

    def _shell_in_foreground(self, window_name: str) -> Optional[bool]:
        """Whether the window's shell is at its prompt (nothing running in
        front of it); None when it can't be told. From the process table, not
        the screen (#549)."""
        from .process_resources import shell_in_foreground
        try:
            return shell_in_foreground(self.tmux.get_pane_pid(window_name))
        except TmuxTimeoutError:
            return None

    def _stop_in_window(self, session: Session, window_name: str,
                        graceful_exit_wait: float) -> bool:
        """Stop the agent in a window and wait for its shell (#549).

        Sends the exit gesture only when something runs in front of the
        shell, then waits until the shell has the terminal again, at most
        ``EXIT_WAIT_SECONDS``. False when the agent is still running then
        (a dialog that ate the gesture, a conversation in Claude's background
        worker): the caller types nothing. When the process table can't
        tell, waits ``graceful_exit_wait`` as before.
        """
        if self._shell_in_foreground(window_name) is True:
            return True  # nothing to stop
        self._send_graceful_exit(self.backend_for(session), window_name)
        steps = int(EXIT_WAIT_SECONDS / EXIT_POLL_SECONDS)
        for _ in range(steps):
            state = self._shell_in_foreground(window_name)
            if state is None:
                self.sleep(graceful_exit_wait)
                return True
            if state:
                return True
            self.sleep(EXIT_POLL_SECONDS)
        print(f"'{session.name}' did not exit within {EXIT_WAIT_SECONDS:g}s "
              "(something is still running in its window); nothing was typed into it",
              file=sys.stderr)
        return False

    def _build_session_metadata(
        self,
        *,
        name: str,
        tmux_window: str,
        command: List[str],
        start_directory: Optional[str],
        session_id: str,
        standing_instructions: str = "",
        permissiveness_mode: str = "normal",
        allowed_tools: Optional[str] = None,
        extra_cli_args: Optional[List[str]] = None,
        agent_teams: bool = False,
        agent_persona: Optional[str] = None,
        model: Optional[str] = None,
        provider: str = "web",
        wrapper: Optional[str] = None,
        backend: str = DEFAULT_BACKEND,
        skill_profile: Optional[str] = None,
        billing_code: Optional[str] = None,
        effort: Optional[str] = None,
        name_source: str = "human",
        name_forced: bool = False,
        billing_code_id: Optional[str] = None,
    ) -> dict:
        """Build the kwargs dict for SessionManager.create_session.

        Resolves start_directory to an absolute path (#312).
        """
        resolved_directory = str(Path(start_directory).resolve()) if start_directory else None
        return dict(
            name=name,
            tmux_session=self.tmux.session_name,
            tmux_window=tmux_window,
            command=command,
            start_directory=resolved_directory,
            standing_instructions=standing_instructions,
            permissiveness_mode=permissiveness_mode,
            allowed_tools=allowed_tools,
            extra_cli_args=extra_cli_args,
            agent_teams=agent_teams,
            agent_persona=agent_persona,
            model=model,
            effort=effort,
            provider=provider,
            session_id=session_id,
            wrapper=wrapper,
            backend=backend,
            skill_profile=skill_profile,
            launcher_version=get_full_version(),
            billing_code=billing_code or name,
            name_source=name_source,
            name_forced=name_forced,
            billing_code_id=billing_code_id,
        )

    def _root_billing_code(self, billing_code: Optional[str], name: str, session_id: str,
                           start_directory: Optional[str] = None) -> Tuple[str, str]:
        """``(id, name)`` of the code a new root agent bills to.

        ``billing_code`` if given, else ``billing.new_agent_code``: the
        agent's name (a ``default`` code, which may follow its auto name),
        its folder's repository, or ``billing.default_code`` (both
        ``human``: they never follow a rename). A new code only for an
        unknown name, so every launch under one name rolls up into one code.
        """
        from .codes import DEFAULT, HUMAN
        from .config import get_billing_defaults

        if billing_code:
            code, source = billing_code, HUMAN
        else:
            settings = get_billing_defaults()
            for warning in settings["warnings"]:
                print(f"Warning: config.yaml: {warning}", file=sys.stderr)
            mode = settings["new_agent_code"]
            if mode == "folder" and start_directory:
                from .git_context import repo_name
                code, source = repo_name(start_directory) or name, HUMAN
            elif mode == "default":
                code, source = settings["default_code"], HUMAN
            else:
                code, source = name, DEFAULT
        codes = self.sessions.ledger.codes
        code_id = codes.resolve(code, source=source, root=session_id)
        return code_id, codes.name_of(code_id)

    def _build_launch_cmd_str(
        self,
        backend: AgentBackend,
        spec: LaunchSpec,
        agent_cmd: List[str],
    ) -> str:
        """Build the full shell command string (env prefix + wrapper + agent args).

        This is the single source of truth for the launch/restart shell line,
        so an agent relaunched via restart gets the same env vars, wrapper
        invocation, and mock handling as a fresh launch.
        """
        env = {
            "OVERCODE_SESSION_NAME": spec.name,
            "OVERCODE_SESSION_ID": spec.session_id,
            "OVERCODE_TMUX_SESSION": spec.tmux_session,
        }

        if spec.parent_session_id:
            env["OVERCODE_PARENT_SESSION_ID"] = spec.parent_session_id
            env["OVERCODE_PARENT_NAME"] = spec.parent_name

        # Tell wrappers which agent CLI they are wrapping. Only emitted for
        # non-default backends so a Claude Code launch line stays byte-identical
        # and wrappers keep their "unset means claude-code" default.
        if backend.name != DEFAULT_BACKEND:
            env["OVERCODE_BACKEND"] = backend.name

        env.update(backend.env_prefix(spec))

        if spec.wrapper:
            env["OVERCODE_WRAPPER_DIR"] = shlex.quote(spec.start_directory)

        # A tmux server started from inside a Claude session (an agent's
        # `overcode launch` while the server was down, `overcode tmux` from a
        # Claude shell) hands that session's markers to every window: the
        # agent would take its title, its messaging socket, its effort, or
        # its overcode parent. `env -u` drops them; credentials and the
        # user's CLAUDE_CODE_* settings stay.
        unset = [k for k in HOST_SESSION_VARS if k not in env]
        env_prefix = " ".join([f"env -u {k}" if i == 0 else f"-u {k}"
                               for i, k in enumerate(unset)]
                              + [f"{k}={v}" for k, v in env.items()])
        # The shell reads the prompt from its file: the text itself never
        # rides on the typed line (quoting, length). `--` ends the options:
        # claude's variadic ones (--allowedTools <tools...>, --add-dir,
        # --mcp-config...) would otherwise take the prompt as one more
        # value, and a prompt starting with "-" would read as an option.
        prompt_arg = (f' -- "$(cat {shlex.quote(spec.initial_prompt_file)})"'
                      if spec.initial_prompt_file else "")

        if spec.mock_scenario:
            return f"MOCK_SCENARIO={spec.mock_scenario} {env_prefix} python {shlex.join(agent_cmd)}"
        elif spec.wrapper:
            return f"{env_prefix} {shlex.quote(spec.wrapper)} {shlex.join(agent_cmd)}{prompt_arg}"
        else:
            return f"{env_prefix} {shlex.join(agent_cmd)}{prompt_arg}"

    def launch(
        self,
        name: str,
        start_directory: Optional[str] = None,
        initial_prompt: Optional[str] = None,
        skip_permissions: bool = False,
        dangerously_skip_permissions: bool = False,
        normal_permissions: bool = False,
        parent_name: Optional[str] = None,
        allowed_tools: Optional[str] = None,
        extra_cli_args: Optional[List[str]] = None,
        agent_teams: bool = False,
        budget_usd: Optional[float] = None,
        agent_persona: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        wrapper: Optional[str] = None,
        inherit_parent_settings: bool = True,
        backend: Optional[str] = None,
        no_parent: bool = False,
        skill_profile: Optional[str] = None,
        billing_code: Optional[str] = None,
        name_source: str = "human",
        name_forced: bool = False,
        effort: Optional[str] = None,
    ) -> Session:
        """
        Launch an interactive Claude Code session in a tmux window.

        Args:
            name: Name for this Claude session
            start_directory: Starting directory for the session
            initial_prompt: Optional initial prompt to send after Claude starts
            skip_permissions: If True, use --permission-mode dontAsk
            dangerously_skip_permissions: If True, use --dangerously-skip-permissions
                (for testing only - bypasses folder trust dialog)
            normal_permissions: If True, run in normal permission mode even
                under a bypass or permissive parent (everything else is still
                inherited). Excludes the two flags above.
            parent_name: Optional parent agent name for hierarchy (#244).
                If not set, auto-detects from OVERCODE_SESSION_NAME env var.
            allowed_tools: Comma-separated tool list for --allowedTools
            extra_cli_args: Extra Claude CLI flags (each a space-separated string)
            provider: API provider — "web" (Claude.ai OAuth) or "bedrock" (AWS Bedrock).
                None resolves via parent inheritance, then config defaults, then "web".
            wrapper: Optional wrapper script path. The wrapper receives the claude
                command as arguments and OVERCODE_WRAPPER_DIR for the working directory.
            inherit_parent_settings: If True (default), provider/model/wrapper/
                agent_teams/permission mode not explicitly set are inherited from
                the parent agent (#433).
            backend: Agent CLI backend name. None resolves via parent
                inheritance, then the built-in default.
            skill_profile: Skill profile name (#499), or "none" for no
                profile. None resolves via the parent, the folder's pinned
                profile, then new_agent_defaults.
            no_parent: Launch top-level instead of as a child of the calling
                agent. Only the overagent may do this: it launches agents the
                person will drive themselves.
            billing_code: The billing code the ledger files this agent's spend
                under (default: its name). A child always takes its
                parent's, whatever is passed (a warning says so).
            name_source: Where ``name`` came from (name_source.py): human
                by default; the CLI passes parent from inside an agent, the
                TUI modal default for an untouched directory name.
            name_forced: The name was let past the naming policy (--force-name).
            effort: Reasoning effort (#537), checked against the backend's
                levels (an unknown one raises LaunchError; one the backend
                cannot apply is warned about on stderr and still recorded).

        Returns:
            The new Session, or the existing one if an agent called ``name``
            still has its window.

        Raises:
            LaunchError: the agent could not be launched; ``reason`` says why.
        """
        # Validate session name
        try:
            validate_session_name(name)
            require_tmux()
        except (InvalidSessionNameError, TmuxNotFoundError) as e:
            raise LaunchError(str(e)) from e
        if normal_permissions and (skip_permissions or dangerously_skip_permissions):
            raise LaunchError(NORMAL_PERMISSIONS_CONFLICT)

        # Auto-detect parent from env var if not explicitly set (#244): the
        # calling agent by its id, which a rename keeps (agent_identity)
        parent_session = None
        if no_parent:
            if parent_name is not None:
                raise LaunchError("--no-parent and --parent are mutually exclusive")
            if in_agent():
                from .overagent import OVERAGENT_BACKEND
                caller = current_agent(self.sessions)
                if caller is None or caller.backend != OVERAGENT_BACKEND:
                    raise LaunchError("only the overagent may launch with --no-parent")
        elif parent_name is None and in_agent():
            parent_session = current_agent(self.sessions)
            if not parent_session:
                raise LaunchError(
                    f"parent agent '{env_agent_name() or env_agent_id()}' not found")
            parent_name = parent_session.name

        if parent_name:
            if parent_session is None:
                parent_session = self._resolve(parent_name)
            if not parent_session:
                raise LaunchError(f"parent agent '{parent_name}' not found")

            # Enforce depth limit
            parent_depth = self.sessions.compute_depth(parent_session)
            if parent_depth + 1 >= self.MAX_HIERARCHY_DEPTH:
                raise LaunchError(
                    f"maximum hierarchy depth ({self.MAX_HIERARCHY_DEPTH}) exceeded")

        # Billing code: a child is always billed to its parent's code, even
        # with --no-inherit; its own is ignored
        billing_code_id = None
        if parent_session is not None:
            billing_code_id, inherited = self._parent_billing_code(parent_session)
            if billing_code and self.sessions.ledger.codes.find(billing_code) != billing_code_id:
                print(f"Warning: --billing-code {billing_code} ignored: a child is billed "
                      f"to its parent's code ({inherited})", file=sys.stderr)
            billing_code = inherited

        # Settings resolution (#433, #499): explicit arg > parent setting >
        # config default > built-in default (see launch_settings).
        from . import skill_library
        from .config import get_new_agent_defaults
        plan = resolve_launch_settings(
            LaunchArgs(
                backend=backend, provider=provider, model=model, wrapper=wrapper,
                agent_teams=agent_teams, skip_permissions=skip_permissions,
                dangerously_skip_permissions=dangerously_skip_permissions,
                normal_permissions=normal_permissions,
                skill_profile=skill_profile,
                inherit_parent_settings=inherit_parent_settings,
            ),
            parent_session,
            get_new_agent_defaults(),
            folder_profile=lambda: skill_library.profile_for_folder(
                start_directory or os.getcwd()),
        )
        provider, model, wrapper = plan.provider, plan.model, plan.wrapper
        skill_profile = plan.skill_profile
        if skill_profile and skill_profile not in skill_library.get_profiles():
            raise LaunchError(f"skill profile '{skill_profile}' not found "
                              f"(see `overcode skills profile list`)")

        # Backend resolution is complete (explicit > parent > config >
        # built-in), so the CLI dependency check can finally target the
        # right binary rather than always probing `claude`.
        try:
            agent_backend = get_backend(plan.backend)
        except UnknownBackendError as e:
            raise LaunchError(str(e)) from e

        try:
            effort, effort_warnings = check_effort(agent_backend, effort, model)
        except ValueError as e:
            raise LaunchError(str(e)) from e

        try:
            require_agent_cli(agent_backend)
        except (AgentCliNotFoundError, agent_backend.not_found_error) as e:
            raise LaunchError(str(e)) from e
        for warning in effort_warnings:
            print(f"Warning: {warning}", file=sys.stderr)

        if provider not in ("web", "bedrock"):
            raise LaunchError(f"invalid provider '{provider}'. Use: web, bedrock")

        # A plain shell (#496) has no agent CLI to wrap — a wrapper, whether
        # passed, inherited from a parent or a config default, would be
        # handed `exec <shell>` — and no one to read standing instructions.
        is_shell = agent_backend.name == SHELL_BACKEND
        if is_shell:
            wrapper = None

        # Resolve wrapper if specified
        resolved_wrapper = None
        if wrapper:
            from .wrapper import resolve_wrapper as _resolve_wrapper
            resolved_wrapper = _resolve_wrapper(wrapper)
            if resolved_wrapper is None:
                raise LaunchError(f"wrapper '{wrapper}' not found or not executable")

        # Check if a session with this name already exists
        existing = self.sessions.get_session_by_name(name)
        with _tmux_answers():
            if existing:
                # The name is taken fleet-wide: look for its window in the
                # tmux session it lives in, not this launcher's, or a live
                # agent elsewhere reads as stale and loses its record.
                if self._window_alive(existing):
                    print(f"Session '{name}' already exists in window "
                          f"{existing.tmux_session or self.tmux_session}:{existing.tmux_window}")
                    return existing
                else:
                    # Window is gone, clean up the stale session
                    self.sessions.delete_session(existing.id)

            # Ensure tmux session exists
            if not self.tmux.ensure_session():
                raise LaunchError(f"failed to create tmux session '{self.tmux.session_name}'")

        # Generate session_id FIRST so we can use it in the window name
        session_id = str(uuid.uuid4())
        window_name = f"{name}-{session_id[:4]}"

        # Create window
        window_name = self.tmux.create_window(
            window_name, start_directory, options={AGENT_ID_OPTION: session_id})
        if window_name is None:
            raise LaunchError(f"failed to create tmux window '{name}'")

        # Persist the Session in the DB first with all launch-time state
        # populated. `_send_launch_for_session` is the single place that
        # turns a Session into the shell line, so launch / restart / revive
        # all render the same way — a new launch flag only needs to be
        # plumbed through Session + _send_launch_for_session + metadata,
        # never re-implemented per caller.
        # command is left empty; the helper fills it in via update_session.
        default_instructions = "" if is_shell else get_default_standing_instructions()
        if billing_code_id is None:
            billing_code_id, billing_code = self._root_billing_code(
                billing_code, name, session_id, start_directory)
        metadata = self._build_session_metadata(
            name=name, tmux_window=window_name, command=[],
            start_directory=start_directory, session_id=session_id,
            standing_instructions=default_instructions,
            permissiveness_mode=plan.permissiveness_mode, allowed_tools=allowed_tools,
            extra_cli_args=extra_cli_args, agent_teams=plan.agent_teams,
            agent_persona=agent_persona, model=model, provider=provider,
            wrapper=resolved_wrapper, backend=agent_backend.name,
            skill_profile=skill_profile, billing_code=billing_code,
            billing_code_id=billing_code_id,
            name_source=name_source, name_forced=name_forced, effort=effort,
        )

        session = self.sessions.create_session(**metadata)
        if parent_session:
            self.sessions.update_session(session.id, parent_session_id=parent_session.id)
            session = self.sessions.get_session(session.id)
        write_brief(self.tmux.session_name, session.id, initial_prompt or "")

        prompt_file = self._initial_prompt_file(agent_backend, session, initial_prompt)
        # The prompt file goes on every way out (a failed send, a refused
        # budget, an error or Ctrl-C while waiting): nothing else removes it
        try:
            # fresh=True triggers the helper's "prescribe a new --session-id" branch
            # (#373), giving us the Claude session ID upfront without PID discovery.
            if not self._send_launch_for_session(session, window_name, fresh=True,
                                                 initial_prompt_file=prompt_file):
                self.tmux.kill_window(window_name)
                self.sessions.delete_session(session.id, archive=False)
                raise LaunchError(f"failed to send command to window {window_name}")

            # Reload so caller sees the active_agent_session_id / command set by the helper.
            session = self.sessions.get_session(session.id)

            # Apply budget at launch time
            if budget_usd is not None and budget_usd > 0:
                if parent_session:
                    # transfer_budget handles unlimited parent (budget=0) correctly
                    success = self.sessions.transfer_budget(parent_session.id, session.id, budget_usd)
                    if not success:
                        self.tmux.kill_window(window_name)
                        self.sessions.delete_session(session.id)
                        raise LaunchError(
                            f"parent has insufficient budget for ${budget_usd:.2f}")
                else:
                    self.sessions.set_cost_budget(session.id, budget_usd)

            print(f"✓ Launched '{name}' in tmux window {window_name}")
            # Bundle retention, bounded, so a user with no daemon stays bounded too
            tidy = getattr(self.sessions, "tidy_agent_storage", None)
            if tidy is not None:
                tidy(self.tmux_session, sweep_limit=0, prune_limit=31)

            self._deliver_initial_prompt(window_name, agent_backend, prompt_file, initial_prompt)
        finally:
            self._remove_prompt_file(prompt_file)
        return session

    def _deliver_initial_prompt(self, window_name: str, backend: AgentBackend,
                                prompt_file: Optional[str],
                                prompt: Optional[str]) -> None:
        """Get the first prompt to an agent whose launch line was just sent.

        On the command line already (``prompt_file``): only answer the
        startup dialogs (the caller then removes the file). Otherwise send
        it once the agent is up.
        """
        if prompt_file:
            self._wait_for_prompt(window_name, backend=backend)
        elif prompt:
            if not self._send_prompt_to_window(window_name, prompt, backend=backend):
                print(f"⚠ The first prompt did not reach window {window_name}: "
                      "check the agent and send it again")

    def _initial_prompt_file(self, backend: AgentBackend, session: Session,
                             prompt: Optional[str]) -> Optional[str]:
        """Write the first prompt for a backend that takes it as an argument.

        None (send it once the agent is up: typed for Claude, which does
        not obey a long paste) for other backends, the mock harness, and a
        prompt too big for one argument (Linux caps one at 128 KiB,
        MAX_ARG_STRLEN: exec would fail and claude never start).
        The launch line puts `--` before it, so a prompt starting with "-"
        is still the prompt, not an option.
        """
        if (not prompt or not backend.prompt_as_argument
                or os.environ.get("MOCK_SCENARIO")
                or len(prompt.encode("utf-8")) > PROMPT_ARG_MAX_BYTES):
            return None
        from .settings import get_session_dir
        folder = get_session_dir(self.tmux.session_name) / "prompts"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{session.id}.txt"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(prompt)
        except OSError:
            return None
        return str(path)

    @staticmethod
    def _remove_prompt_file(path: Optional[str]) -> None:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass

    def launch_fork(
        self,
        name: str,
        source_session: Session,
        initial_prompt: Optional[str] = None,
        normal_permissions: bool = False,
        name_source: str = "human",
        name_forced: bool = False,
    ) -> Session:
        """Launch a forked agent from an existing session's context.

        Uses `claude --resume <session-id> --fork-session` to create a new
        Claude session that starts with the source agent's full conversation
        history but gets its own independent session ID.

        The forked agent inherits the source's directory, permissions, agent
        persona, allowed tools, extra CLI args, and standing instructions.
        It is registered as a child of the source in the hierarchy.

        Args:
            name: Name for the forked agent
            source_session: The session to fork from (must have active_agent_session_id)
            initial_prompt: Optional prompt to send after the fork starts
            normal_permissions: Run the fork in normal permission mode instead
                of the source's (everything else is still inherited)
            name_source: Where ``name`` came from (as for ``launch``)
            name_forced: The name was let past the naming policy

        Returns:
            The new Session, or the existing one if an agent called ``name``
            still has its window.

        Raises:
            LaunchError: the fork could not be launched; ``reason`` says why.
        """
        # Validate
        try:
            validate_session_name(name)
            agent_backend = self.backend_for(source_session)
        except (InvalidSessionNameError, UnknownBackendError) as e:
            raise LaunchError(str(e), "fork") from e

        if not supports(agent_backend, BackendCapability.FORK):
            raise LaunchError(
                f"backend '{agent_backend.name}' does not support fork", "fork")

        try:
            require_tmux()
            require_agent_cli(agent_backend)
        except (TmuxNotFoundError, AgentCliNotFoundError, agent_backend.not_found_error) as e:
            raise LaunchError(str(e), "fork") from e

        if not source_session.active_agent_session_id:
            raise LaunchError(
                f"source agent '{source_session.name}' has no active agent session ID",
                "fork")

        # Enforce depth limit
        source_depth = self.sessions.compute_depth(source_session)
        if source_depth + 1 >= self.MAX_HIERARCHY_DEPTH:
            raise LaunchError(
                f"maximum hierarchy depth ({self.MAX_HIERARCHY_DEPTH}) exceeded", "fork")

        # Check for name collision
        existing = self.sessions.get_session_by_name(name)
        with _tmux_answers("fork"):
            if existing:
                if self._window_alive(existing):
                    print(f"Session '{name}' already exists in window "
                          f"{existing.tmux_session or self.tmux_session}:{existing.tmux_window}")
                    return existing
                else:
                    self.sessions.delete_session(existing.id)

            # Ensure tmux session exists
            if not self.tmux.ensure_session():
                raise LaunchError(
                    f"failed to create tmux session '{self.tmux.session_name}'", "fork")

        # Create tmux window in source's directory
        session_id = str(uuid.uuid4())
        window_name = f"{name}-{session_id[:4]}"
        window_name = self.tmux.create_window(
            window_name, source_session.start_directory, options={AGENT_ID_OPTION: session_id})
        if window_name is None:
            raise LaunchError(f"failed to create tmux window '{name}'", "fork")

        perm_mode = "normal" if normal_permissions else (
            source_session.permissiveness_mode or "normal")

        # Persist the forked Session with all inherited launch-time state
        # populated. The shared helper below then renders the shell line.
        # wrapper is inherited so a forked child lands in the same execution
        # environment (e.g. the same container) as its source.
        parent_code = self._parent_billing_code(source_session)
        metadata = self._build_session_metadata(
            name=name, tmux_window=window_name, command=[],
            start_directory=source_session.start_directory, session_id=session_id,
            standing_instructions=source_session.standing_instructions,
            permissiveness_mode=perm_mode, allowed_tools=source_session.allowed_tools,
            extra_cli_args=source_session.extra_cli_args,
            agent_teams=source_session.agent_teams, agent_persona=source_session.agent_persona,
            model=source_session.model, provider=source_session.provider,
            effort=source_session.effort,
            wrapper=source_session.wrapper, backend=agent_backend.name,
            skill_profile=getattr(source_session, "skill_profile", None),
            billing_code=parent_code[1], billing_code_id=parent_code[0],
            name_source=name_source, name_forced=name_forced,
        )

        session = self.sessions.create_session(**metadata)
        self.sessions.update_session(session.id, parent_session_id=source_session.id)
        session = self.sessions.get_session(session.id)
        # The fork carries the source's task unless it is given a new one
        write_brief(self.tmux.session_name, session.id,
                    initial_prompt or read_brief(self.for_agent(source_session).tmux_session,
                                                 source_session.id))

        # fork_from → helper emits --resume <id> --fork-session. For
        # backends with fork_prescribes_new_session_id=True (grok, and
        # Claude Code as of #466) _send_launch_for_session mints a fresh
        # uuid and binds it eagerly, exactly like a fresh launch — the CLI
        # honors the prescribed id on fork. Only backends that mint their
        # own unpredictable fork id and offer no reliable discovery
        # mechanism would need directory+time fallback here instead; none
        # of the four current backends are in that position.
        # The prompt rides on the command line too, as for a launch: Claude
        # does not act on a long pasted prompt (see _initial_prompt_file).
        prompt_file = self._initial_prompt_file(agent_backend, session, initial_prompt)
        try:
            if not self._send_launch_for_session(
                session, window_name,
                fork_from=source_session.active_agent_session_id,
                initial_prompt_file=prompt_file,
            ):
                self.tmux.kill_window(window_name)
                self.sessions.delete_session(session.id, archive=False)
                raise LaunchError(f"failed to send command to window {window_name}", "fork")

            session = self.sessions.get_session(session.id)

            print(f"✓ Forked '{source_session.name}' → '{name}' in tmux window {window_name}")

            self._deliver_initial_prompt(window_name, agent_backend, prompt_file, initial_prompt)
        finally:
            self._remove_prompt_file(prompt_file)
        return session

    def launch_in_worktree(self, name: str, source_session: Session):
        """Launch a fresh agent beside ``source_session``, in a new git worktree.

        The opposite of a fork: a clear context, its own branch and
        checkout (``agent_worktree``), started from the commit the source
        has checked out. It is the source's peer, not its child, and it
        runs the way the source does: backend, model, provider, wrapper,
        permissions, persona, allowed tools, extra CLI args, skill profile.

        Returns:
            ``(session, worktree)``.

        Raises:
            LaunchError: the worktree or the agent could not be made. A
                worktree made for an agent that then failed to launch is
                removed again.
        """
        from .agent_worktree import WorktreeError, create_agent_worktree, remove_agent_worktree
        from .overagent import OVERAGENT_BACKEND

        try:
            validate_session_name(name)
        except InvalidSessionNameError as e:
            raise LaunchError(str(e)) from e
        # launch() hands back a live agent of this name; check first, so no
        # worktree is made for it.
        existing = self.sessions.get_session_by_name(name)
        if existing and self._window_alive(existing):
            raise LaunchError(f"an agent called '{name}' already exists")

        try:
            worktree = create_agent_worktree(source_session.start_directory, name)
        except WorktreeError as e:
            raise LaunchError(str(e)) from e

        perm = source_session.permissiveness_mode or "normal"
        backend = source_session.backend or None
        try:
            session = self.launch(
                name=name,
                start_directory=worktree.start_directory,
                # The overagent is one per install: its sibling is an
                # ordinary agent.
                backend=None if backend == OVERAGENT_BACKEND else backend,
                model=None if backend == OVERAGENT_BACKEND else source_session.model,
                # An effort the launch flag takes (not one only read back)
                effort=(source_session.effort if backend != OVERAGENT_BACKEND
                        and effort_applies(self.backend_for(source_session),
                                           source_session.effort) else None),
                provider=source_session.provider,
                wrapper=source_session.wrapper,
                agent_teams=bool(source_session.agent_teams),
                skip_permissions=perm == "permissive",
                dangerously_skip_permissions=perm == "bypass",
                allowed_tools=source_session.allowed_tools,
                extra_cli_args=source_session.extra_cli_args or None,
                agent_persona=source_session.agent_persona,
                skill_profile=getattr(source_session, "skill_profile", None),
            )
        except BaseException:
            remove_agent_worktree(worktree)
            raise
        return session, worktree

    def _send_launch_for_session(
        self,
        session: Session,
        window_name: str,
        *,
        fresh: bool = False,
        fork_from: Optional[str] = None,
        initial_prompt_file: Optional[str] = None,
        reset_agent_state: bool = False,
    ) -> bool:
        """Rebuild the full launch command from a Session and send it to a window.

        Single source of truth for the launch shell line — shared between
        launch (new agent), restart (same window), revive (new window after
        termination), and launch_fork (new agent inheriting history). Replays
        every launch-time knob the Session records — --settings hooks/permissions,
        wrapper, env prefix (parent linkage, agent_teams, bedrock, wrapper dir),
        model, persona, allowed_tools, extra CLI args — so every lifecycle
        transition produces an identical shell line modulo session resumption.

        Session-selection mode (mutually exclusive in practice):
          * fork_from set: --resume <fork_from> --fork-session. Backends
            that declare both SESSION_ID_PRESCRIPTION and
            ``fork_prescribes_new_session_id`` get a freshly minted uuid
            bound eagerly, same as the fresh-launch case, since their fork
            grammar takes an explicit new id that's authoritative — grok
            from Phase 3, and Claude Code as of #466 (live-verified: `claude
            --resume <id> --fork-session --session-id <new>` honors the
            prescribed id; the fork's transcript is written under the new
            uuid, not a CLI-minted one). Earlier, an intermediate version of
            this code minted unconditionally for any SESSION_ID_PRESCRIPTION
            backend, which was wrong for Claude Code at the time — its fork
            id was believed CLI-generated, so eager binding would have left
            fork sessions tracking a phantom id. That belief turned out to
            be the root cause of #466: without eager binding, a fork's real
            id was left to directory+time discovery, which is ambiguous
            whenever a sibling agent shares the same start_directory — the
            fork could bind to the sibling's id instead of its own. Now that
            Claude Code's fork grammar is confirmed to honor the prescribed
            id too, both backends take the same eager-binding path and
            neither needs discovery for forks.
          * fresh=True or no active_agent_session_id: prescribe a new
            --session-id, bind it on the Session eagerly (no PID discovery).
          * otherwise: --resume <active_agent_session_id> preserves history.

        Does NOT create windows, send graceful-exit keys, or touch session
        stats — callers own those concerns. Refuses (False) when a process
        other than the window's shell holds the terminal: the line would be
        typed into a running agent as a prompt (#549).
        """
        if self._shell_in_foreground(window_name) is False:
            print(f"Not relaunching '{session.name}': something is still running "
                  "in its window", file=sys.stderr)
            return False
        parent_session = None
        if session.parent_session_id:
            parent_session = self.sessions.get_session(session.parent_session_id)

        backend = self.backend_for(session)

        if fork_from:
            resume_sid = fork_from
            # Mint only for backends whose fork grammar takes an explicit,
            # authoritative new session id: `--resume <id> --fork-session
            # --session-id <new-uuid>`, confirmed live for both grok (Phase
            # 3) and Claude Code (#466). Gating on SESSION_ID_PRESCRIPTION
            # alone is not enough — that capability also covers fresh
            # launches, where a backend might prescribe an id but not honor
            # one on fork; fork_prescribes_new_session_id is the separate,
            # narrower signal for the fork case specifically.
            if (
                supports(backend, BackendCapability.SESSION_ID_PRESCRIPTION)
                and backend.fork_prescribes_new_session_id
            ):
                new_claude_sid = str(uuid.uuid4())
            else:
                new_claude_sid = None
            use_fork = True
        elif fresh or not session.active_agent_session_id:
            resume_sid = None
            new_claude_sid = str(uuid.uuid4())
            use_fork = False
        else:
            resume_sid = session.active_agent_session_id
            new_claude_sid = None
            use_fork = False

        # Only backends that let overcode choose the conversation ID get one
        # bound eagerly. opencode mints its own `ses_…` ids, so recording a
        # prescribed UUID would make the next resume pass a nonexistent
        # session; leave it unset and let Phase 5's discovery fill it in.
        if not supports(backend, BackendCapability.SESSION_ID_PRESCRIPTION):
            new_claude_sid = None

        spec = LaunchSpec(
            name=session.name,
            session_id=session.id,
            tmux_session=self.tmux.session_name,
            parent_session_id=parent_session.id if parent_session else None,
            parent_name=parent_session.name if parent_session else None,
            resume_session_id=resume_sid,
            fork=use_fork,
            prescribed_session_id=new_claude_sid,
            permissiveness_mode=session.permissiveness_mode,
            model=session.model,
            effort=session.effort,
            agent=session.agent_persona,
            allowed_tools=session.allowed_tools,
            extra_args=(drop_model_flag(session.extra_cli_args) if session.model
                        else session.extra_cli_args),
            agent_teams=session.agent_teams,
            provider=session.provider,
            start_directory=session.start_directory,
            wrapper=session.wrapper,
            mock_scenario=os.environ.get("MOCK_SCENARIO"),
            initial_prompt_file=initial_prompt_file,
        )
        self._apply_skill_profile(backend, session, spec)

        # The agent's own directory (agents/<id>/, item 35): every launch
        # makes it, which is what tells readers this agent's files are there
        # and not name-keyed. A restart or revive of the same id starts it
        # clean, so the new process is not read through the old one's Stop.
        prepare_agent_dir(self.tmux.session_name, session.id, reset=reset_agent_state)

        # Stage anything the CLI needs on disk before it starts — for opencode,
        # the telemetry plugin in the project's .opencode/plugins/. Failure here
        # costs telemetry, never the launch.
        try:
            backend.prepare_launch(spec)
        except Exception:
            pass

        claude_cmd = backend.build_command(spec)
        cmd_str = self._build_launch_cmd_str(backend, spec, claude_cmd)

        # A fresh launch whose backend mints the new conversation's id leaves
        # the record on the discarded one until the agent's first
        # SessionStart names the new one; this lets that event replace it.
        # Written before the CLI starts, so its first hook sees it.
        fresh_pending = resume_sid is None and new_claude_sid is None
        if fresh_pending or session.agent_session_fresh_pending:
            self.sessions.update_session(session.id, agent_session_fresh_pending=fresh_pending)

        if not self.tmux.send_keys(window_name, cmd_str, enter=True):
            return False

        # Stamp launcher_version on every (re)launch so the session reflects the
        # code that last spawned the claude process, not just the original launch.
        self.sessions.update_session(
            session.id,
            command=claude_cmd,
            launcher_version=get_full_version(),
            **({"bedrock_routing": launch_bedrock_routing(session.model)}
               if session.provider == "bedrock" else {}),
        )

        if new_claude_sid:
            self.sessions.update_session(session.id, agent_session_ids=[new_claude_sid])
            self.sessions.set_active_agent_session_id(session.id, new_claude_sid)

        if backend.display_name_args(spec.name):
            self._record_cli_title(session.id, spec.name)
        return True

    def _record_cli_title(self, session_id: str, name: str) -> None:
        """The CLI's own session name is the ``--name`` just launched with (namer.py)."""
        from .namer import load_state, save_state

        state = load_state(self.tmux_session, session_id)
        if state.cli_title != name:
            state.cli_title = name
            save_state(self.tmux_session, session_id, state)

    def _apply_skill_profile(self, backend: AgentBackend, session: Session,
                             spec: LaunchSpec) -> None:
        """Fill the spec's skill folder and hidden skills from the profile (#499).

        Runs on every launch/restart/revive, so profile edits take effect
        the next time the agent starts. A profile deleted since launch, or
        one that can't be built, costs the profile, never the launch.
        """
        profile = getattr(session, "skill_profile", None)
        if not profile or not supports(backend, BackendCapability.SKILL_PROFILES):
            return
        from . import skill_library
        try:
            prepared = skill_library.prepare_profile(profile, backend.name)
        except OSError as e:
            print(f"Warning: skill profile '{profile}' not applied: {e}", file=sys.stderr)
            return
        if prepared is None:
            print(f"Warning: skill profile '{profile}' not found; launching without it",
                  file=sys.stderr)
            return
        if prepared.missing:
            print(f"Warning: skill profile '{profile}': not in the library: "
                  f"{', '.join(prepared.missing)}", file=sys.stderr)
        spec.skill_dir = prepared.skill_dir
        spec.hidden_skills = list(prepared.hidden)

    def restart(
        self,
        session: Session,
        fresh: bool = False,
        graceful_exit_wait: float = 3.0,
    ) -> bool:
        """Restart an agent in its existing tmux window, preserving all launch context.

        Args:
            session: The Session to restart (must have an existing tmux window).
            fresh: If False (default), resume the prior Claude session with
                --resume <active_agent_session_id> so conversation history is
                preserved. If True, prescribe a brand-new --session-id.
            graceful_exit_wait: Seconds to wait after /exit before relaunching.

        Returns:
            True if the relaunch command was sent, False if the tmux window
            is gone, tmux is not answering, or send failed.
        """
        owner = self.for_agent(session)
        if owner is not self:
            return owner.restart(session, fresh=fresh, graceful_exit_wait=graceful_exit_wait)
        try:
            if not self.tmux.window_exists(session.tmux_window):
                return False
        except TmuxTimeoutError:
            print(f"Cannot restart '{session.name}': {TMUX_NOT_ANSWERING}", file=sys.stderr)
            return False

        if not self._stop_in_window(session, session.tmux_window, graceful_exit_wait):
            return False

        if not self._send_launch_for_session(session, session.tmux_window, fresh=fresh,
                                             reset_agent_state=True):
            return False

        if session.kept_for_revive:
            self.sessions.update_session(session.id, kept_for_revive=False)
        self.sessions.update_stats(session.id, current_task="Restarting...")
        return True

    def revive(
        self,
        session: Session,
        fresh: bool = False,
    ) -> bool:
        """Revive a terminated agent by creating a new tmux window and relaunching.
        Mirrors `restart` semantics (same full-context replay, same resume-by-default
        behavior) but creates a new tmux window because the original one is gone.
        If the original window still exists, degrades to restart so callers can
        dispatch blindly.

        Args:
            session: The Session to revive.
            fresh: If False (default), resume the prior Claude session; if True,
                prescribe a new --session-id.

        Returns:
            True on success, False if tmux is not answering or window
            creation or send failed.
        """
        owner = self.for_agent(session)
        if owner is not self:  # its window, and its new one, are in its own tmux session
            return owner.revive(session, fresh=fresh)

        try:
            # Original window still around — caller probably meant restart.
            if self.tmux.window_exists(session.tmux_window):
                return self.restart(session, fresh=fresh)

            if not self.tmux.ensure_session():
                return False
        except TmuxTimeoutError:
            print(f"Cannot revive '{session.name}': {TMUX_NOT_ANSWERING}", file=sys.stderr)
            return False

        # Reuse the overcode session id as the window suffix (stable across
        # revives, so listings stay consistent).
        window_label = f"{session.name}-{session.id[:4]}"
        window_name = self.tmux.create_window(
            window_label, session.start_directory, options={AGENT_ID_OPTION: session.id})
        if window_name is None:
            return False

        if not self._send_launch_for_session(session, window_name, fresh=fresh,
                                             reset_agent_state=True):
            self.tmux.kill_window(window_name)
            return False

        self.sessions.update_session(
            session.id,
            tmux_window=window_name,
            status="running",
            kept_for_revive=False,
        )
        self.sessions.update_stats(session.id, current_task="Reviving...")
        return True

    def attach(self, name: str = None, bare: bool = False):
        """Attach to the tmux session, optionally targeting a specific agent.

        Args:
            name: optional agent name to focus on
            bare: if True, strip tmux chrome for embedding in other terminals
        """
        if not name:
            return self._attach_to(None, name, bare)
        session = self._resolve(name)
        if session is None:
            print(f"Error: agent '{name}' not found")
            return
        # Found fleet-wide: attach to its own tmux session, not --session's
        return self.for_agent(session)._attach_to(session, name, bare)

    def _attach_to(self, session: Optional[Session], name: Optional[str], bare: bool):
        """Attach to this launcher's tmux session, on ``session``'s window if given."""
        try:
            if not self.tmux.session_exists():
                print(f"Error: tmux session '{self.tmux.session_name}' does not exist")
                print("No active sessions to attach to. Launch a session first with 'overcode launch'")
                return

            window = None
            if session is not None:
                if not self.tmux.window_exists(session.tmux_window):
                    print(f"Error: agent '{name}' tmux window no longer exists")
                    return
                window = session.tmux_window
        except TmuxTimeoutError:
            print(f"Cannot attach: {TMUX_NOT_ANSWERING}", file=sys.stderr)
            return

        self.tmux.attach_session(window=window, bare=bare)

    def list_sessions(self) -> List[Session]:
        """Every registered session in this tmux session, as sessions.json has them.

        Read-only: no tmux call, no status write, no output. The lifecycle
        (terminated, a child's done / waiting_oversight, the #432 budget
        refund) is decided by the monitor daemon's tick; a CLI command that
        must see it settled with no daemon running calls
        ``reconcile_lifecycle`` first. The ``Session`` objects are the
        manager's shared snapshot: read, never mutate.
        """
        return [s for s in self.sessions.list_sessions()
                if s.tmux_session == self.tmux.session_name]

