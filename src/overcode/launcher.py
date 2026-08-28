"""
Launcher for interactive agent-CLI sessions in tmux windows.

All sessions launched by overcode are interactive - users can take over at
any time. Initial prompts are sent as keystrokes after the agent starts,
not as CLI arguments.

Which CLI gets launched, and with what argv, comes from the session's
``AgentBackend`` (see ``overcode.backends``).
"""

import shlex
import subprocess
import os
import time
import uuid
from pathlib import Path
from typing import List, Optional

import re

from . import get_full_version
from .backends import (
    DEFAULT_BACKEND,
    AgentBackend,
    BackendCapability,
    LaunchSpec,
    UnknownBackendError,
    get_backend,
    supports,
)
from .backends.claude_code import (  # noqa: F401  (compat re-export)
    _build_launch_settings,
    _resolve_overcode_bin,
)
from .tmux_manager import TmuxManager, EMPTY_PLACEHOLDER_WINDOW  # noqa: F401
from .tmux_utils import send_text_to_tmux_window, get_tmux_pane_content, tmux_window_target
from .session_manager import SessionManager, Session
from .config import get_default_standing_instructions
from .dependency_check import require_tmux, require_agent_cli
from .exceptions import TmuxNotFoundError, AgentCliNotFoundError, InvalidSessionNameError


# Valid session name pattern
SESSION_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


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


class AgentLauncher:
    """Launches interactive agent-CLI sessions in tmux windows.

    Backend-neutral: the argv grammar, gestures and startup handshake for
    each agent CLI live in ``overcode.backends``; this class owns the tmux
    orchestration around them.

    All sessions are interactive - this is the only supported mode.
    Users can take over any session at any time via tmux.
    """

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

    # Maximum nesting depth for agent hierarchy (#244)
    MAX_HIERARCHY_DEPTH = 5

    def backend_for(self, session: Session) -> AgentBackend:
        """Resolve the agent backend that owns a session's CLI grammar."""
        return get_backend(getattr(session, "backend", DEFAULT_BACKEND))

    def build_relaunch_command(self, session: Session) -> List[str]:
        """Public argv builder for out-of-module relaunchers (web control API).

        Renders only the session's permission mode — callers that need the
        full launch context should use `restart`/`revive` instead.
        """
        backend = self.backend_for(session)
        return backend.build_command(
            LaunchSpec(permissiveness_mode=session.permissiveness_mode)
        )

    def _send_graceful_exit(self, backend: AgentBackend, window_name: str) -> None:
        """Send the backend's shutdown gesture to a window."""
        for press in backend.graceful_exit_keys():
            self.tmux.send_keys(window_name, press.keys, enter=press.enter)
            if press.delay_after:
                time.sleep(press.delay_after)

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
            provider=provider,
            session_id=session_id,
            wrapper=wrapper,
            backend=backend,
            launcher_version=get_full_version(),
        )

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

        env_prefix = " ".join(f"{k}={v}" for k, v in env.items())

        if spec.mock_scenario:
            return f"MOCK_SCENARIO={spec.mock_scenario} {env_prefix} python {shlex.join(agent_cmd)}"
        elif spec.wrapper:
            return f"{env_prefix} {shlex.quote(spec.wrapper)} {shlex.join(agent_cmd)}"
        else:
            return f"{env_prefix} {shlex.join(agent_cmd)}"

    def launch(
        self,
        name: str,
        start_directory: Optional[str] = None,
        initial_prompt: Optional[str] = None,
        skip_permissions: bool = False,
        dangerously_skip_permissions: bool = False,
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
    ) -> Optional[Session]:
        """
        Launch an interactive Claude Code session in a tmux window.

        Args:
            name: Name for this Claude session
            start_directory: Starting directory for the session
            initial_prompt: Optional initial prompt to send after Claude starts
            skip_permissions: If True, use --permission-mode dontAsk
            dangerously_skip_permissions: If True, use --dangerously-skip-permissions
                (for testing only - bypasses folder trust dialog)
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

        Returns:
            Session object if successful, None otherwise
        """
        # Validate session name
        try:
            validate_session_name(name)
        except InvalidSessionNameError as e:
            print(f"Cannot launch: {e}")
            return None

        try:
            require_tmux()
        except TmuxNotFoundError as e:
            print(f"Cannot launch: {e}")
            return None

        # Auto-detect parent from env var if not explicitly set (#244)
        parent_session = None
        if parent_name is None:
            env_parent_name = os.environ.get("OVERCODE_SESSION_NAME")
            if env_parent_name:
                parent_name = env_parent_name

        if parent_name:
            parent_session = self.sessions.get_session_by_name(parent_name)
            if not parent_session:
                print(f"Parent agent '{parent_name}' not found")
                return None

            # Enforce depth limit
            parent_depth = self.sessions.compute_depth(parent_session)
            if parent_depth + 1 >= self.MAX_HIERARCHY_DEPTH:
                print(f"Cannot launch: maximum hierarchy depth ({self.MAX_HIERARCHY_DEPTH}) exceeded")
                return None

        # Settings resolution (#433): explicit arg > parent setting > config
        # default > built-in default. Children inherit the parent's provider,
        # model, wrapper, agent_teams, and permission mode so a bedrock-pinned
        # or model-constrained parent doesn't spawn children on different
        # infrastructure. Opt out with inherit_parent_settings=False.
        if parent_session and inherit_parent_settings:
            if backend is None:
                backend = getattr(parent_session, "backend", None) or None
            # Model ids and provider transports are backend-specific grammar —
            # a Claude `sonnet`/`claude-fable-5` or a bedrock pin means nothing
            # to codex/grok (and vice versa), so they only flow to a child on
            # the same backend. A cross-backend child falls through to config
            # defaults / the CLI's own default model instead.
            parent_backend = getattr(parent_session, "backend", None) or DEFAULT_BACKEND
            same_backend = backend is None or backend == parent_backend
            if provider is None and same_backend:
                provider = parent_session.provider
            if model is None and same_backend:
                model = parent_session.model
            if wrapper is None:
                wrapper = parent_session.wrapper
            if not agent_teams:
                agent_teams = parent_session.agent_teams
            if not skip_permissions and not dangerously_skip_permissions:
                if parent_session.permissiveness_mode == "bypass":
                    dangerously_skip_permissions = True
                elif parent_session.permissiveness_mode == "permissive":
                    skip_permissions = True

        from .config import get_new_agent_defaults
        agent_defaults = get_new_agent_defaults()
        if provider is None:
            provider = agent_defaults.get("provider") or "web"
        if wrapper is None:
            wrapper = agent_defaults.get("wrapper") or None
        if backend is None:
            backend = agent_defaults.get("backend") or DEFAULT_BACKEND

        # Backend resolution is complete (explicit > parent > config >
        # built-in), so the CLI dependency check can finally target the
        # right binary rather than always probing `claude`.
        try:
            agent_backend = get_backend(backend)
        except UnknownBackendError as e:
            print(f"Cannot launch: {e}")
            return None

        try:
            require_agent_cli(agent_backend)
        except (AgentCliNotFoundError, agent_backend.not_found_error) as e:
            print(f"Cannot launch: {e}")
            return None

        if provider not in ("web", "bedrock"):
            print(f"Cannot launch: invalid provider '{provider}'. Use: web, bedrock")
            return None

        # Resolve wrapper if specified
        resolved_wrapper = None
        if wrapper:
            from .wrapper import resolve_wrapper as _resolve_wrapper
            resolved_wrapper = _resolve_wrapper(wrapper)
            if resolved_wrapper is None:
                print(f"Cannot launch: wrapper '{wrapper}' not found or not executable")
                return None

        # Check if a session with this name already exists
        existing = self.sessions.get_session_by_name(name)
        if existing:
            # Check if its tmux window still exists
            if self.tmux.window_exists(existing.tmux_window):
                print(f"Session '{name}' already exists in window {existing.tmux_window}")
                return existing
            else:
                # Window is gone, clean up the stale session
                self.sessions.delete_session(existing.id)

        # Ensure tmux session exists
        if not self.tmux.ensure_session():
            print(f"Failed to create tmux session '{self.tmux.session_name}'")
            return None

        # Generate session_id FIRST so we can use it in the window name
        session_id = str(uuid.uuid4())
        window_name = f"{name}-{session_id[:4]}"

        # Create window
        window_name = self.tmux.create_window(window_name, start_directory)
        if window_name is None:
            print(f"Failed to create tmux window '{name}'")
            return None

        if dangerously_skip_permissions:
            perm_mode = "bypass"
        elif skip_permissions:
            perm_mode = "permissive"
        else:
            perm_mode = "normal"

        # Persist the Session in the DB first with all launch-time state
        # populated. `_send_launch_for_session` is the single place that
        # turns a Session into the shell line, so launch / restart / revive
        # all render the same way — a new launch flag only needs to be
        # plumbed through Session + _send_launch_for_session + metadata,
        # never re-implemented per caller.
        # command is left empty; the helper fills it in via update_session.
        default_instructions = get_default_standing_instructions()
        metadata = self._build_session_metadata(
            name=name, tmux_window=window_name, command=[],
            start_directory=start_directory, session_id=session_id,
            standing_instructions=default_instructions,
            permissiveness_mode=perm_mode, allowed_tools=allowed_tools,
            extra_cli_args=extra_cli_args, agent_teams=agent_teams,
            agent_persona=agent_persona, model=model, provider=provider,
            wrapper=resolved_wrapper, backend=agent_backend.name,
        )

        session = self.sessions.create_session(**metadata)
        if parent_session:
            self.sessions.update_session(session.id, parent_session_id=parent_session.id)
            session = self.sessions.get_session(session.id)

        # fresh=True triggers the helper's "prescribe a new --session-id" branch
        # (#373), giving us the Claude session ID upfront without PID discovery.
        if not self._send_launch_for_session(session, window_name, fresh=True):
            print(f"Failed to send command to window {window_name}")
            self.tmux.kill_window(window_name)
            self.sessions.delete_session(session.id, archive=False)
            return None

        # Reload so caller sees the active_agent_session_id / command set by the helper.
        session = self.sessions.get_session(session.id)

        # Apply budget at launch time
        if budget_usd is not None and budget_usd > 0:
            if parent_session:
                # transfer_budget handles unlimited parent (budget=0) correctly
                success = self.sessions.transfer_budget(parent_session.id, session.id, budget_usd)
                if not success:
                    print(f"Cannot launch: parent has insufficient budget for ${budget_usd:.2f}")
                    self.tmux.kill_window(window_name)
                    self.sessions.delete_session(session.id)
                    return None
            else:
                self.sessions.set_cost_budget(session.id, budget_usd)

        print(f"✓ Launched '{name}' in tmux window {window_name}")

        # Send initial prompt if provided (after Claude starts)
        if initial_prompt:
            self._send_prompt_to_window(window_name, initial_prompt, backend=agent_backend)

        return session

    # Characters that indicate the agent's input prompt is ready
    PROMPT_READY_CHARS = get_backend().prompt_ready_chars()

    def _wait_for_prompt(
        self,
        window_name: str,
        timeout: float = 30.0,
        poll_interval: float = 0.5,
        backend: Optional[AgentBackend] = None,
    ) -> bool:
        """Poll pane content until the agent's input prompt appears.

        Dismisses the backend's startup dialogs on the way (for Claude:
        the workspace trust dialog and the bypass-permissions warning).

        Returns True if prompt detected, False on timeout.
        """
        from .status_patterns import strip_ansi
        from .tmux_utils import _build_tmux_cmd

        backend = backend or get_backend()
        target = tmux_window_target(self.tmux.session_name, window_name)
        tmux_cmd = _build_tmux_cmd()
        prompt_chars = backend.prompt_ready_chars()
        rules = backend.startup_dialog_rules()
        handled: set[str] = set()

        deadline = time.time() + timeout
        while time.time() < deadline:
            content = get_tmux_pane_content(
                self.tmux.session_name, window_name, lines=20
            )
            if content:
                dismissed = False
                for rule in rules:
                    if rule.marker in handled or rule.marker not in content:
                        continue
                    for press in rule.presses:
                        keys = [press.keys, 'Enter'] if press.enter else [press.keys]
                        subprocess.run(tmux_cmd + ['send-keys', '-t', target] + keys, timeout=5)
                        if press.delay_after:
                            time.sleep(press.delay_after)
                    handled.add(rule.marker)
                    time.sleep(rule.settle_seconds)
                    dismissed = True
                    break
                if dismissed:
                    continue

                for line in content.split('\n'):
                    cleaned = strip_ansi(line).strip()
                    if cleaned in prompt_chars:
                        return True
            time.sleep(poll_interval)
        return False

    def _send_prompt_to_window(
        self,
        window_name: str,
        prompt: str,
        startup_delay: float = 3.0,
        backend: Optional[AgentBackend] = None,
    ) -> bool:
        """Send a prompt to an agent session via tmux load-buffer/paste-buffer.

        Polls for the agent's input prompt before sending. Falls back to
        startup_delay if the prompt is not detected within 30 seconds.
        """
        if self._wait_for_prompt(window_name, backend=backend):
            # Prompt detected — send immediately, no delay needed
            return send_text_to_tmux_window(
                self.tmux.session_name,
                window_name,
                prompt,
                send_enter=True,
                startup_delay=0,
            )
        # Fallback: prompt not detected, use original delay
        return send_text_to_tmux_window(
            self.tmux.session_name,
            window_name,
            prompt,
            send_enter=True,
            startup_delay=startup_delay,
        )

    def launch_fork(
        self,
        name: str,
        source_session: Session,
        initial_prompt: Optional[str] = None,
    ) -> Optional[Session]:
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

        Returns:
            Session object if successful, None otherwise
        """
        # Validate
        try:
            validate_session_name(name)
        except InvalidSessionNameError as e:
            print(f"Cannot fork: {e}")
            return None

        try:
            agent_backend = self.backend_for(source_session)
        except UnknownBackendError as e:
            print(f"Cannot fork: {e}")
            return None

        if not supports(agent_backend, BackendCapability.FORK):
            print(f"Cannot fork: backend '{agent_backend.name}' does not support fork")
            return None

        try:
            require_tmux()
            require_agent_cli(agent_backend)
        except (TmuxNotFoundError, AgentCliNotFoundError, agent_backend.not_found_error) as e:
            print(f"Cannot fork: {e}")
            return None

        if not source_session.active_agent_session_id:
            print(f"Cannot fork: source agent '{source_session.name}' has no active agent session ID")
            return None

        # Enforce depth limit
        source_depth = self.sessions.compute_depth(source_session)
        if source_depth + 1 >= self.MAX_HIERARCHY_DEPTH:
            print(f"Cannot fork: maximum hierarchy depth ({self.MAX_HIERARCHY_DEPTH}) exceeded")
            return None

        # Check for name collision
        existing = self.sessions.get_session_by_name(name)
        if existing:
            if self.tmux.window_exists(existing.tmux_window):
                print(f"Session '{name}' already exists in window {existing.tmux_window}")
                return existing
            else:
                self.sessions.delete_session(existing.id)

        # Ensure tmux session exists
        if not self.tmux.ensure_session():
            print(f"Failed to create tmux session '{self.tmux.session_name}'")
            return None

        # Create tmux window in source's directory
        session_id = str(uuid.uuid4())
        window_name = f"{name}-{session_id[:4]}"
        window_name = self.tmux.create_window(window_name, source_session.start_directory)
        if window_name is None:
            print(f"Failed to create tmux window '{name}'")
            return None

        perm_mode = source_session.permissiveness_mode or "normal"

        # Persist the forked Session with all inherited launch-time state
        # populated. The shared helper below then renders the shell line.
        # wrapper is inherited so a forked child lands in the same execution
        # environment (e.g. the same container) as its source.
        metadata = self._build_session_metadata(
            name=name, tmux_window=window_name, command=[],
            start_directory=source_session.start_directory, session_id=session_id,
            standing_instructions=source_session.standing_instructions,
            permissiveness_mode=perm_mode, allowed_tools=source_session.allowed_tools,
            extra_cli_args=source_session.extra_cli_args,
            agent_teams=source_session.agent_teams, agent_persona=source_session.agent_persona,
            model=source_session.model, provider=source_session.provider,
            wrapper=source_session.wrapper, backend=agent_backend.name,
        )

        session = self.sessions.create_session(**metadata)
        self.sessions.update_session(session.id, parent_session_id=source_session.id)
        session = self.sessions.get_session(session.id)

        # fork_from → helper emits --resume <id> --fork-session. For most
        # backends (Claude Code included) no session id is prescribed here;
        # the CLI generates the fork's new ID and the monitor daemon
        # discovers it. Backends with fork_prescribes_new_session_id=True
        # (grok) are the exception — _send_launch_for_session mints and
        # eagerly binds a fresh uuid for those instead.
        if not self._send_launch_for_session(
            session, window_name,
            fork_from=source_session.active_agent_session_id,
        ):
            print(f"Failed to send command to window {window_name}")
            self.tmux.kill_window(window_name)
            self.sessions.delete_session(session.id, archive=False)
            return None

        session = self.sessions.get_session(session.id)

        print(f"✓ Forked '{source_session.name}' → '{name}' in tmux window {window_name}")

        # Send initial prompt if provided
        if initial_prompt:
            self._send_prompt_to_window(window_name, initial_prompt, backend=agent_backend)

        return session

    def _send_launch_for_session(
        self,
        session: Session,
        window_name: str,
        *,
        fresh: bool = False,
        fork_from: Optional[str] = None,
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
          * fork_from set: --resume <fork_from> --fork-session. Claude
            generates its own new session ID; monitor daemon discovers it.
            Backends that declare both SESSION_ID_PRESCRIPTION and
            ``fork_prescribes_new_session_id`` (grok) instead get a freshly
            minted uuid bound eagerly, same as the fresh-launch case, since
            their fork grammar takes an explicit new id that's authoritative.
            Claude Code declares SESSION_ID_PRESCRIPTION too but mints its
            own different id on fork, so it must NOT get eager binding here
            — that was a real regression caught in review: minting
            unconditionally left Claude fork sessions tracking a phantom id
            no process would ever report, until/unless discovery overwrote
            it.
          * fresh=True or no active_agent_session_id: prescribe a new
            --session-id, bind it on the Session eagerly (no PID discovery).
          * otherwise: --resume <active_agent_session_id> preserves history.

        Does NOT create windows, send graceful-exit keys, or touch session
        stats — callers own those concerns.
        """
        parent_session = None
        if session.parent_session_id:
            parent_session = self.sessions.get_session(session.parent_session_id)

        backend = self.backend_for(session)

        if fork_from:
            resume_sid = fork_from
            # Mint only for backends whose fork grammar takes an explicit,
            # authoritative new session id (grok: `--resume <id>
            # --fork-session --session-id <new-uuid>`). Gating on
            # SESSION_ID_PRESCRIPTION alone is not enough — Claude Code also
            # declares that capability (for fresh launches) but mints its
            # own different id on fork, so it needs
            # fork_prescribes_new_session_id=False to keep leaving this
            # unset for discovery to fill in, exactly as before this flag
            # existed.
            if (
                supports(backend, BackendCapability.SESSION_ID_PRESCRIPTION)
                and getattr(backend, "fork_prescribes_new_session_id", False)
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
            agent=session.agent_persona,
            allowed_tools=session.allowed_tools,
            extra_args=session.extra_cli_args,
            agent_teams=session.agent_teams,
            provider=session.provider,
            start_directory=session.start_directory,
            wrapper=session.wrapper,
            mock_scenario=os.environ.get("MOCK_SCENARIO"),
        )

        # Stage anything the CLI needs on disk before it starts — for opencode,
        # the telemetry plugin in the project's .opencode/plugins/. Failure here
        # costs telemetry, never the launch.
        try:
            backend.prepare_launch(spec)
        except Exception:
            pass

        claude_cmd = backend.build_command(spec)
        cmd_str = self._build_launch_cmd_str(backend, spec, claude_cmd)

        if not self.tmux.send_keys(window_name, cmd_str, enter=True):
            return False

        # Stamp launcher_version on every (re)launch so the session reflects the
        # code that last spawned the claude process, not just the original launch.
        self.sessions.update_session(
            session.id,
            command=claude_cmd,
            launcher_version=get_full_version(),
        )

        if new_claude_sid:
            self.sessions.update_session(session.id, agent_session_ids=[new_claude_sid])
            self.sessions.set_active_agent_session_id(session.id, new_claude_sid)

        return True

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
            is gone or send failed.
        """
        if not self.tmux.window_exists(session.tmux_window):
            return False

        self._send_graceful_exit(self.backend_for(session), session.tmux_window)
        time.sleep(graceful_exit_wait)

        if not self._send_launch_for_session(session, session.tmux_window, fresh=fresh):
            return False

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
            True on success, False if window creation or send failed.
        """
        # Original window still around — caller probably meant restart.
        if self.tmux.window_exists(session.tmux_window):
            return self.restart(session, fresh=fresh)

        if not self.tmux.ensure_session():
            return False

        # Reuse the overcode session id as the window suffix (stable across
        # revives, so listings stay consistent).
        window_label = f"{session.name}-{session.id[:4]}"
        window_name = self.tmux.create_window(window_label, session.start_directory)
        if window_name is None:
            return False

        if not self._send_launch_for_session(session, window_name, fresh=fresh):
            self.tmux.kill_window(window_name)
            return False

        self.sessions.update_session(
            session.id,
            tmux_window=window_name,
            status="running",
        )
        self.sessions.update_stats(session.id, current_task="Reviving...")
        return True

    def attach(self, name: str = None, bare: bool = False):
        """Attach to the tmux session, optionally targeting a specific agent.

        Args:
            name: optional agent name to focus on
            bare: if True, strip tmux chrome for embedding in other terminals
        """
        if not self.tmux.session_exists():
            print(f"Error: tmux session '{self.tmux.session_name}' does not exist")
            print("No active sessions to attach to. Launch a session first with 'overcode launch'")
            return

        window = None
        if name:
            session = self.sessions.get_session_by_name(name)
            if session is None:
                print(f"Error: agent '{name}' not found")
                return
            if not self.tmux.window_exists(session.tmux_window):
                print(f"Error: agent '{name}' tmux window no longer exists")
                return
            window = session.tmux_window

        self.tmux.attach_session(window=window, bare=bare)

    def list_sessions(self, detect_terminated: bool = True, kill_untracked: bool = False) -> List[Session]:
        """
        List all registered sessions, detecting terminated ones.

        Args:
            detect_terminated: If True (default), check tmux and mark sessions as
                             "terminated" if their window no longer exists
            kill_untracked: If True, kill tmux windows that aren't tracked in sessions.json

        Returns:
            List of all Session objects (including terminated ones)
        """
        all_sessions = self.sessions.list_sessions()

        # Filter to only sessions belonging to this tmux session
        my_sessions = [s for s in all_sessions if s.tmux_session == self.tmux.session_name]

        # Migrate legacy digit-string tmux_window values to actual window names.
        # Pre-name-based sessions stored window index (e.g. 4 → "4") but the new
        # code expects the window name (e.g. "overcode2"). Resolve via tmux.
        self._migrate_legacy_window_ids(my_sessions)

        # Detect terminated sessions (tmux window gone but session still tracked)
        if detect_terminated:
            from .follow_mode import _check_hook_stop, _check_report
            from .status_constants import STATUS_WAITING_OVERSIGHT

            newly_terminated = []
            newly_done: list[str] = []  # session ids of children that just flipped to done (#432)
            for session in my_sessions:
                # Only check non-terminated sessions
                if session.status not in ("terminated", "done"):
                    if not self.tmux.window_exists(session.tmux_window):
                        # Child agents with Stop hook: check for report first
                        if (session.parent_session_id is not None
                                and _check_hook_stop(self.tmux.session_name, session.name)):
                            report = _check_report(self.tmux.session_name, session.name)
                            if report:
                                self.sessions.update_session_status(session.id, "done")
                                session.status = "done"
                                newly_done.append(session.id)
                            else:
                                self.sessions.update_session_status(session.id, STATUS_WAITING_OVERSIGHT)
                                session.status = STATUS_WAITING_OVERSIGHT
                        else:
                            self.sessions.update_session_status(session.id, "terminated")
                            session.status = "terminated"
                            newly_terminated.append(session.name)

            # Detect child agents with Stop hook but no report → waiting_oversight (#244)
            # Also handle children that already have reports → done
            for session in my_sessions:
                if (session.status in ("running", "terminated", STATUS_WAITING_OVERSIGHT)
                        and session.parent_session_id is not None
                        and _check_hook_stop(self.tmux.session_name, session.name)):
                    report = _check_report(self.tmux.session_name, session.name)
                    if report:
                        prev_status = session.status
                        self.sessions.update_session_status(session.id, "done")
                        session.status = "done"
                        if prev_status != "done":
                            newly_done.append(session.id)
                    elif session.status != STATUS_WAITING_OVERSIGHT:
                        self.sessions.update_session_status(session.id, STATUS_WAITING_OVERSIGHT)
                        session.status = STATUS_WAITING_OVERSIGHT

            # Auto-refund unused budget on done children (#432). Idempotent —
            # reclaim_budget caps the child's budget at the spent amount, so a
            # later list_sessions call won't double-refund.
            for child_id in newly_done:
                refunded = self.sessions.reclaim_budget(child_id)
                if refunded and refunded > 0:
                    print(f"  Refunded ${refunded:.4f} from child to parent")

            if newly_terminated:
                print(f"Detected {len(newly_terminated)} terminated session(s): {', '.join(newly_terminated)}")

        # Kill untracked windows (tmux windows exist but not tracked)
        if kill_untracked and self.tmux.session_exists():
            placeholder = EMPTY_PLACEHOLDER_WINDOW
            active_sessions = [s for s in my_sessions if s.status != "terminated"]
            tracked_windows = {s.tmux_window for s in active_sessions}
            tmux_windows = self.tmux.list_windows()

            untracked_count = 0
            for window_info in tmux_windows:
                w_name = window_info['name']
                window_idx = int(window_info['index'])
                # Don't kill window 0 (default shell), tracked windows, or
                # the dead-window placeholder created by the TUI (#457).
                if (window_idx != 0
                        and w_name not in tracked_windows
                        and w_name != placeholder):
                    print(f"Killing untracked window {w_name}")
                    self.tmux.kill_window(w_name)
                    untracked_count += 1

            if untracked_count > 0:
                print(f"Killed {untracked_count} untracked window(s)")

        return my_sessions

    def cleanup_terminated_sessions(self) -> int:
        """Remove all terminated sessions from state.

        Returns:
            Number of sessions cleaned up
        """
        all_sessions = self.sessions.list_sessions()
        terminated = [s for s in all_sessions if s.status == "terminated"]

        for session in terminated:
            self.sessions.delete_session(session.id)

        return len(terminated)

    def kill_session(self, name: str, cascade: bool = True) -> bool:
        """Kill a session by name.

        Handles both active sessions and stale sessions (where tmux window/session
        no longer exists, e.g., after a machine reboot).

        Args:
            name: Name of the session to kill
            cascade: If True (default), also kill all descendant agents.
                If False, orphan children (set their parent_session_id to None).
        """
        session = self.sessions.get_session_by_name(name)
        if session is None:
            print(f"Session '{name}' not found")
            return False

        # Handle cascade: kill descendants deepest-first (#244)
        if cascade:
            descendants = self.sessions.get_descendants(session.id)
            # Sort by depth (deepest first) for clean teardown
            descendants.sort(key=lambda s: self.sessions.compute_depth(s), reverse=True)
            for desc in descendants:
                self._kill_single_session(desc)
        else:
            # Orphan children: set their parent_session_id to None
            children = self.sessions.get_children(session.id)
            for child in children:
                self.sessions.update_session(child.id, parent_session_id=None)

        return self._kill_single_session(session)

    def _kill_single_session(self, session: Session) -> bool:
        """Kill a single session (no cascade). Internal helper."""
        # Check if the tmux window/session still exists
        window_exists = self.tmux.window_exists(session.tmux_window)

        if window_exists:
            # Active session - try to kill the tmux window
            if self.tmux.kill_window(session.tmux_window):
                self.sessions.delete_session(session.id)
                print(f"✓ Killed session '{session.name}'")
                return True
            else:
                print(f"Failed to kill tmux window for '{session.name}'")
                return False
        else:
            # Stale session - tmux window/session is already gone (e.g., after reboot)
            # Just clean up the state file
            self.sessions.delete_session(session.id)
            print(f"✓ Cleaned up stale session '{session.name}' (tmux window no longer exists)")
            return True

    def send_to_session(self, name: str, text: str, enter: bool = True) -> bool:
        """Send text/keys to a session by name.

        Args:
            name: Name of the session
            text: Text to send (or special key like "Enter", "Escape")
            enter: Whether to press Enter after the text (default: True)

        Returns:
            True if successful, False otherwise
        """
        session = self.sessions.get_session_by_name(name)
        if session is None:
            print(f"Session '{name}' not found")
            return False

        return self._send_to_resolved_session(session, text, enter)

    def send_to_session_by_id(self, session_id: str, text: str, enter: bool = True) -> bool:
        """Send text/keys to a session by ID.

        Preferred over send_to_session() when the session ID is known,
        since IDs are unique even when local and remote agents share a name.

        Args:
            session_id: Unique session ID
            text: Text to send (or special key like "Enter", "Escape")
            enter: Whether to press Enter after the text (default: True)

        Returns:
            True if successful, False otherwise
        """
        session = self.sessions.get_session(session_id)
        if session is None:
            return False

        return self._send_to_resolved_session(session, text, enter)

    def _send_to_resolved_session(self, session: Session, text: str, enter: bool = True) -> bool:
        """Send text/keys to an already-resolved session.

        Internal helper shared by send_to_session() and send_to_session_by_id().
        """
        # Handle special keys
        special_keys = {
            "enter": "",  # Empty string + Enter = just press Enter
            "escape": "Escape",
            "esc": "Escape",
            "tab": "Tab",
            "up": "Up",
            "down": "Down",
            "left": "Left",
            "right": "Right",
        }

        # Backend-resolved gestures. "enter"/"escape" are raw keys and stay
        # raw; "approve"/"reject" ask the session's backend what its permission
        # dialog wants, so a supervisor recipe can stay backend-neutral.
        gesture_keys = {
            "approve": "approve_keys",
            "reject": "reject_keys",
        }

        # Check if it's a special key
        text_lower = text.lower().strip()
        success = False
        if text_lower in gesture_keys:
            backend = self.backend_for(session)
            presses = getattr(backend, gesture_keys[text_lower])()
            success = bool(presses)
            for press in presses:
                if not self.tmux.send_keys(
                    session.tmux_window, press.keys, enter=press.enter
                ):
                    success = False
                    break
                if press.delay_after:
                    time.sleep(press.delay_after)
        elif text_lower in special_keys:
            key = special_keys[text_lower]
            if key == "":
                # Just press Enter
                success = self.tmux.send_keys(session.tmux_window, "", enter=True)
            else:
                # Send special key without Enter
                success = self.tmux.send_keys(session.tmux_window, key, enter=False)
        elif '\n' in text:
            # Multi-line text: use load-buffer/paste-buffer so newlines are
            # pasted as a single block instead of being interpreted as Enter
            # keypresses by tmux send-keys (#376)
            success = send_text_to_tmux_window(
                self.tmux.session_name, session.tmux_window, text,
                send_enter=enter,
            )
        else:
            # Single-line regular text
            success = self.tmux.send_keys(session.tmux_window, text, enter=enter)

        # Update last activity on success (steers_count is tracked via supervisor log parsing)
        if success:
            self.sessions.update_stats(
                session.id,
                last_activity=time.strftime("%Y-%m-%dT%H:%M:%S")
            )

        return success

    def _migrate_legacy_window_ids(self, sessions: List[Session]) -> None:
        """Migrate legacy digit-string tmux_window values to actual window names.

        Legacy sessions stored the window index (int) which got converted to a
        digit string like "4". We resolve these to the actual window name by
        looking up the tmux window list by index.
        """
        windows = self.tmux.list_windows()
        if not windows:
            return
        index_to_name = {str(w['index']): w['name'] for w in windows}

        for session in sessions:
            if session.tmux_window.isdigit() and session.tmux_window in index_to_name:
                new_name = index_to_name[session.tmux_window]
                session.tmux_window = new_name
                self.sessions.update_session(session.id, tmux_window=new_name)

    def get_session_output(self, name: str, lines: int = 50) -> Optional[str]:
        """Get recent output from a session.

        Args:
            name: Name of the session
            lines: Number of lines to capture (default: 50)

        Returns:
            The captured output, or None if session not found
        """
        session = self.sessions.get_session_by_name(name)
        if session is None:
            print(f"Session '{name}' not found")
            return None

        try:
            from .tmux_utils import _build_tmux_cmd

            result = subprocess.run(
                [
                    *_build_tmux_cmd(), "capture-pane",
                    "-t", tmux_window_target(self.tmux.session_name, session.tmux_window),
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


# Pre-Phase-6 name. Kept so `from overcode.launcher import ClaudeLauncher`
# — and mock.patch targets naming it — keep working.
ClaudeLauncher = AgentLauncher
