"""
Launcher for interactive Claude Code sessions in tmux windows.

All Claude sessions launched by overcode are interactive - users can
take over at any time. Initial prompts are sent as keystrokes after
Claude starts, not as CLI arguments.

"""

import time
import subprocess
import os
import shlex
import uuid
from pathlib import Path
from typing import List, Optional

import re

from .tmux_manager import TmuxManager
from .tmux_utils import send_text_to_tmux_window, get_tmux_pane_content, tmux_window_target
from .session_manager import SessionManager, Session
from .config import get_default_standing_instructions
from .dependency_check import require_tmux, require_claude
from .exceptions import TmuxNotFoundError, ClaudeNotFoundError, InvalidSessionNameError


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

class ClaudeLauncher:
    """Launches interactive Claude Code sessions in tmux windows.

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

    def _build_claude_command(
        self,
        *,
        skip_permissions: bool = False,
        dangerously_skip_permissions: bool = False,
        permissiveness_mode: Optional[str] = None,
        claude_agent: Optional[str] = None,
        allowed_tools: Optional[str] = None,
        extra_claude_args: Optional[List[str]] = None,
        resume_session_id: Optional[str] = None,
        fork: bool = False,
        claude_session_id: Optional[str] = None,
        model: Optional[str] = None,
    ) -> List[str]:
        """Construct the claude CLI argument list.

        For new launches, pass skip_permissions/dangerously_skip_permissions
        and claude_session_id to prescribe the Claude session ID upfront.
        For forks, pass permissiveness_mode (inherited from source) and
        resume_session_id with fork=True.
        """
        claude_command = os.environ.get("CLAUDE_COMMAND", "claude")

        if resume_session_id:
            cmd = (
                ["claude", "--resume", resume_session_id]
                if claude_command == "claude"
                else [claude_command, "--resume", resume_session_id]
            )
            if fork:
                cmd.append("--fork-session")
        else:
            cmd = [claude_command]

        # Prescribe session ID so we know which session file belongs to
        # this agent without needing PID-based discovery (#373).
        if claude_session_id and not resume_session_id:
            cmd.extend(["--session-id", claude_session_id])

        # Permission flags — from explicit args or inherited mode
        if dangerously_skip_permissions or permissiveness_mode == "bypass":
            cmd.append("--dangerously-skip-permissions")
        elif skip_permissions or permissiveness_mode == "permissive":
            cmd.extend(["--permission-mode", "dontAsk"])

        if model:
            cmd.extend(["--model", model])
        if claude_agent:
            cmd.extend(["--agent", claude_agent])
        if allowed_tools:
            cmd.extend(["--allowedTools", allowed_tools])
        if extra_claude_args:
            for arg in extra_claude_args:
                cmd.extend(shlex.split(arg))

        return cmd

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
        extra_claude_args: Optional[List[str]] = None,
        agent_teams: bool = False,
        claude_agent: Optional[str] = None,
        model: Optional[str] = None,
        provider: str = "web",
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
            extra_claude_args=extra_claude_args,
            agent_teams=agent_teams,
            claude_agent=claude_agent,
            model=model,
            provider=provider,
            session_id=session_id,
        )

    def _prepare_and_launch(
        self,
        *,
        name: str,
        session_id: str,
        window_name: str,
        claude_cmd: List[str],
        metadata: dict,
        parent_session: Optional[Session] = None,
    ) -> Optional[Session]:
        """Send command to tmux window, create session, and set parent.

        Builds the environment prefix, constructs the full command string,
        sends it to the tmux window, and registers the session.

        Returns the Session on success, None on failure (kills window on error).
        """
        env_prefix = f"OVERCODE_SESSION_NAME={name} OVERCODE_SESSION_ID={session_id} OVERCODE_TMUX_SESSION={self.tmux.session_name}"

        if parent_session:
            env_prefix += f" OVERCODE_PARENT_SESSION_ID={parent_session.id} OVERCODE_PARENT_NAME={parent_session.name}"

        if metadata.get('agent_teams'):
            env_prefix += " CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1"

        if metadata.get('provider') == 'bedrock':
            env_prefix += " CLAUDE_CODE_USE_BEDROCK=1"
            # Use configured region or default to us-east-1
            from .config import get_bedrock_config
            bedrock_cfg = get_bedrock_config()
            env_prefix += f" AWS_REGION={bedrock_cfg['region']}"

        mock_scenario = os.environ.get("MOCK_SCENARIO")
        if mock_scenario:
            cmd_str = f"MOCK_SCENARIO={mock_scenario} {env_prefix} python {shlex.join(claude_cmd)}"
        else:
            cmd_str = f"{env_prefix} {shlex.join(claude_cmd)}"

        if not self.tmux.send_keys(window_name, cmd_str, enter=True):
            print(f"Failed to send command to window {window_name}")
            self.tmux.kill_window(window_name)
            return None

        session = self.sessions.create_session(**metadata)

        if parent_session:
            self.sessions.update_session(session.id, parent_session_id=parent_session.id)
            session.parent_session_id = parent_session.id

        return session

    def launch(
        self,
        name: str,
        start_directory: Optional[str] = None,
        initial_prompt: Optional[str] = None,
        skip_permissions: bool = False,
        dangerously_skip_permissions: bool = False,
        parent_name: Optional[str] = None,
        allowed_tools: Optional[str] = None,
        extra_claude_args: Optional[List[str]] = None,
        agent_teams: bool = False,
        budget_usd: Optional[float] = None,
        claude_agent: Optional[str] = None,
        model: Optional[str] = None,
        provider: str = "web",
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
            extra_claude_args: Extra Claude CLI flags (each a space-separated string)
            provider: API provider — "web" (Claude.ai OAuth) or "bedrock" (AWS Bedrock)

        Returns:
            Session object if successful, None otherwise
        """
        # Validate session name
        try:
            validate_session_name(name)
        except InvalidSessionNameError as e:
            print(f"Cannot launch: {e}")
            return None

        # Check dependencies before attempting to launch
        try:
            require_tmux()
            require_claude()
        except (TmuxNotFoundError, ClaudeNotFoundError) as e:
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

        # Generate a Claude session ID upfront so we know exactly which
        # session file belongs to this agent — no discovery needed (#373).
        claude_session_id = str(uuid.uuid4())

        # Build command and metadata, then launch
        claude_cmd = self._build_claude_command(
            skip_permissions=skip_permissions,
            dangerously_skip_permissions=dangerously_skip_permissions,
            claude_agent=claude_agent,
            allowed_tools=allowed_tools,
            extra_claude_args=extra_claude_args,
            claude_session_id=claude_session_id,
            model=model,
        )

        if dangerously_skip_permissions:
            perm_mode = "bypass"
        elif skip_permissions:
            perm_mode = "permissive"
        else:
            perm_mode = "normal"

        default_instructions = get_default_standing_instructions()
        metadata = self._build_session_metadata(
            name=name, tmux_window=window_name, command=claude_cmd,
            start_directory=start_directory, session_id=session_id,
            standing_instructions=default_instructions,
            permissiveness_mode=perm_mode, allowed_tools=allowed_tools,
            extra_claude_args=extra_claude_args, agent_teams=agent_teams,
            claude_agent=claude_agent, model=model, provider=provider,
        )

        session = self._prepare_and_launch(
            name=name, session_id=session_id, window_name=window_name,
            claude_cmd=claude_cmd, metadata=metadata,
            parent_session=parent_session,
        )
        if session is None:
            return None

        # Bind the prescribed Claude session ID immediately (#373).
        # No need for PID-based discovery on first sync.
        self.sessions.add_claude_session_id(session.id, claude_session_id)
        self.sessions.set_active_claude_session_id(session.id, claude_session_id)

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
            self._send_prompt_to_window(window_name, initial_prompt)

        return session

    # Characters that indicate Claude's input prompt is ready
    PROMPT_READY_CHARS = {">", "›", "❯"}

    def _wait_for_prompt(
        self,
        window_name: str,
        timeout: float = 30.0,
        poll_interval: float = 0.5,
    ) -> bool:
        """Poll pane content until Claude's input prompt appears.

        Automatically handles startup dialogs:
        1. Workspace trust dialog -- presses Enter to accept default
        2. Bypass permissions warning -- selects "Yes, I accept" (Down + Enter)

        Returns True if prompt detected, False on timeout.
        """
        from .status_patterns import strip_ansi
        from .tmux_utils import _build_tmux_cmd

        target = tmux_window_target(self.tmux.session_name, window_name)
        tmux_cmd = _build_tmux_cmd()
        trust_accepted = False
        perms_accepted = False

        deadline = time.time() + timeout
        while time.time() < deadline:
            content = get_tmux_pane_content(
                self.tmux.session_name, window_name, lines=20
            )
            if content:
                # Trust prompt: default is accept, just Enter
                if not trust_accepted and "I trust this folder" in content:
                    subprocess.run(tmux_cmd + ['send-keys', '-t', target, '', 'Enter'], timeout=5)
                    trust_accepted = True
                    time.sleep(1.5)
                    continue

                # Permissions warning: navigate Down to "Yes, I accept", then Enter
                if not perms_accepted and "Yes, I accept" in content:
                    subprocess.run(tmux_cmd + ['send-keys', '-t', target, 'Down'], timeout=5)
                    time.sleep(0.3)
                    subprocess.run(tmux_cmd + ['send-keys', '-t', target, '', 'Enter'], timeout=5)
                    perms_accepted = True
                    time.sleep(2.0)
                    continue

                for line in content.split('\n'):
                    cleaned = strip_ansi(line).strip()
                    if cleaned in self.PROMPT_READY_CHARS:
                        return True
            time.sleep(poll_interval)
        return False

    def _send_prompt_to_window(
        self,
        window_name: str,
        prompt: str,
        startup_delay: float = 3.0,
    ) -> bool:
        """Send a prompt to a Claude session via tmux load-buffer/paste-buffer.

        Polls for Claude's input prompt before sending. Falls back to
        startup_delay if the prompt is not detected within 30 seconds.
        """
        if self._wait_for_prompt(window_name):
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
            source_session: The session to fork from (must have active_claude_session_id)
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
            require_tmux()
            require_claude()
        except (TmuxNotFoundError, ClaudeNotFoundError) as e:
            print(f"Cannot fork: {e}")
            return None

        if not source_session.active_claude_session_id:
            print(f"Cannot fork: source agent '{source_session.name}' has no active Claude session ID")
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

        # Build command and metadata, then launch
        claude_cmd = self._build_claude_command(
            permissiveness_mode=source_session.permissiveness_mode,
            claude_agent=source_session.claude_agent,
            allowed_tools=source_session.allowed_tools,
            extra_claude_args=source_session.extra_claude_args,
            resume_session_id=source_session.active_claude_session_id,
            fork=True,
        )

        perm_mode = source_session.permissiveness_mode or "normal"

        metadata = self._build_session_metadata(
            name=name, tmux_window=window_name, command=claude_cmd,
            start_directory=source_session.start_directory, session_id=session_id,
            standing_instructions=source_session.standing_instructions,
            permissiveness_mode=perm_mode, allowed_tools=source_session.allowed_tools,
            extra_claude_args=source_session.extra_claude_args,
            agent_teams=source_session.agent_teams, claude_agent=source_session.claude_agent,
        )

        session = self._prepare_and_launch(
            name=name, session_id=session_id, window_name=window_name,
            claude_cmd=claude_cmd, metadata=metadata,
            parent_session=source_session,
        )
        if session is None:
            return None

        print(f"✓ Forked '{source_session.name}' → '{name}' in tmux window {window_name}")

        # Send initial prompt if provided
        if initial_prompt:
            self._send_prompt_to_window(window_name, initial_prompt)

        return session

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
                        self.sessions.update_session_status(session.id, "done")
                        session.status = "done"
                    elif session.status != STATUS_WAITING_OVERSIGHT:
                        self.sessions.update_session_status(session.id, STATUS_WAITING_OVERSIGHT)
                        session.status = STATUS_WAITING_OVERSIGHT

            if newly_terminated:
                print(f"Detected {len(newly_terminated)} terminated session(s): {', '.join(newly_terminated)}")

        # Kill untracked windows (tmux windows exist but not tracked)
        if kill_untracked and self.tmux.session_exists():
            active_sessions = [s for s in my_sessions if s.status != "terminated"]
            tracked_windows = {s.tmux_window for s in active_sessions}
            tmux_windows = self.tmux.list_windows()

            untracked_count = 0
            for window_info in tmux_windows:
                w_name = window_info['name']
                window_idx = int(window_info['index'])
                # Don't kill window 0 (default shell) or tracked windows
                if window_idx != 0 and w_name not in tracked_windows:
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

        # Check if it's a special key
        text_lower = text.lower().strip()
        success = False
        if text_lower in special_keys:
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
            result = subprocess.run(
                [
                    "tmux", "capture-pane",
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
