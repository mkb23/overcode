"""Claude Code backend — the argv/env grammar overcode was built around."""

import json
import os
import shlex
import shutil
import sys
from typing import TYPE_CHECKING, Dict, List, Optional, Set

from ..exceptions import ClaudeNotFoundError
from .base import (
    AgentBackend,
    BackendCapability,
    DialogRule,
    KeyPress,
    LaunchSpec,
)

if TYPE_CHECKING:
    from ..stats_reader import StatsReader


PROMPT_READY_CHARS = {">", "›", "❯"}


def _resolve_overcode_bin() -> str:
    """Resolve absolute path to the overcode binary.

    Tries shutil.which first (covers global/pipx installs), then falls
    back to invoking via the current Python interpreter (covers uv run,
    venv-only installs, etc.).
    """
    which = shutil.which("overcode")
    if which:
        return which
    return f"{sys.executable} -m overcode.cli"


def _build_launch_settings(overcode_bin: str, include_punchy_perms: bool = False) -> dict:
    """Build the --settings JSON for overcode-launched agents.

    Includes all overcode hooks (with absolute-path commands) and
    permissions so agents don't depend on user-level settings.json
    containing these entries.
    """
    from ..hook_handler import OVERCODE_HOOKS
    from ..cli.perms import OVERCODE_SAFE_PERMS, OVERCODE_PUNCHY_PERMS

    # Build hooks dict: event -> [matcher group]
    hooks: dict[str, list] = {}
    for event, _bare_command in OVERCODE_HOOKS:
        hooks.setdefault(event, []).append({
            "matcher": "",
            "hooks": [{"type": "command", "command": f"{overcode_bin} hook-handler"}],
        })

    perms = list(OVERCODE_SAFE_PERMS)
    if include_punchy_perms:
        perms.extend(OVERCODE_PUNCHY_PERMS)

    return {
        "hooks": hooks,
        "permissions": {"allow": perms},
    }


class ClaudeCodeBackend:
    """Claude Code CLI adapter."""

    name = "claude-code"
    display_name = "Claude Code"
    binary = "claude"
    version_args = ("--version",)
    install_hint = (
        "Claude Code CLI is required but not found. "
        "Install it from: https://claude.ai/claude-code"
    )
    process_basenames = ("claude",)
    not_found_error = ClaudeNotFoundError
    capabilities = (
        BackendCapability.RESUME
        | BackendCapability.FORK
        | BackendCapability.SESSION_ID_PRESCRIPTION
        | BackendCapability.HOOK_EVENTS
        | BackendCapability.TRANSCRIPT_STATS
        | BackendCapability.PERMISSION_INJECTION
        | BackendCapability.SKILLS
        | BackendCapability.SANDBOX_PROBE
        | BackendCapability.SUBSCRIPTION_USAGE
        | BackendCapability.AGENT_TEAMS
    )

    def executable(self) -> str:
        """The binary to invoke, honouring the CLAUDE_COMMAND override.

        The override is how the e2e mock harness substitutes a fake TUI.
        """
        return os.environ.get("CLAUDE_COMMAND", "claude")

    def resume_args(self, session_id: str, fork: bool) -> List[str]:
        args = ["--resume", session_id]
        if fork:
            args.append("--fork-session")
        return args

    def build_command(self, spec: LaunchSpec) -> List[str]:
        """Construct the claude CLI argument list.

        For new launches, ``prescribed_session_id`` pins the Claude session
        ID upfront. For resumes/forks, ``resume_session_id`` (plus ``fork``)
        continues an existing conversation.
        """
        claude_command = self.executable()

        if spec.resume_session_id:
            cmd = (
                ["claude"]
                if claude_command == "claude"
                else [claude_command]
            )
            cmd.extend(self.resume_args(spec.resume_session_id, spec.fork))
        else:
            cmd = [claude_command]

        # Prescribe session ID so we know which session file belongs to
        # this agent without needing PID-based discovery (#373).
        if spec.prescribed_session_id and not spec.resume_session_id:
            cmd.extend(["--session-id", spec.prescribed_session_id])

        # Inject overcode hooks and permissions via --settings so launched
        # agents don't depend on user-level settings.json (#435).
        overcode_bin = _resolve_overcode_bin()
        settings = _build_launch_settings(
            overcode_bin, include_punchy_perms=spec.include_punchy_perms
        )
        cmd.extend(["--settings", json.dumps(settings)])

        # Permission flags — from explicit args or inherited mode
        if spec.dangerously_skip_permissions or spec.permissiveness_mode == "bypass":
            cmd.append("--dangerously-skip-permissions")
        elif spec.skip_permissions or spec.permissiveness_mode == "permissive":
            cmd.extend(["--permission-mode", "dontAsk"])

        if spec.model:
            cmd.extend(["--model", spec.model])
        if spec.agent:
            cmd.extend(["--agent", spec.agent])
        if spec.allowed_tools:
            cmd.extend(["--allowedTools", spec.allowed_tools])
        if spec.extra_args:
            for arg in spec.extra_args:
                cmd.extend(shlex.split(arg))

        return cmd

    def env_prefix(self, spec: LaunchSpec) -> Dict[str, str]:
        """Claude-specific env vars for the launch shell line."""
        env: Dict[str, str] = {}
        if spec.agent_teams:
            env["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] = "1"
        if spec.provider == "bedrock":
            env["CLAUDE_CODE_USE_BEDROCK"] = "1"
            from ..config import get_bedrock_config
            bedrock_cfg = get_bedrock_config()
            env["AWS_REGION"] = bedrock_cfg["region"]
        return env

    def graceful_exit_keys(self) -> List[KeyPress]:
        # Ctrl-C cancels any in-flight tool call, then /exit reliably
        # terminates the claude process.
        return [
            KeyPress("C-c", enter=False, delay_after=0.5),
            KeyPress("/exit", enter=True),
        ]

    def clear_conversation_keys(self) -> List[KeyPress]:
        return [KeyPress("/clear", enter=True)]

    def approve_keys(self) -> List[KeyPress]:
        return [KeyPress("", enter=True)]

    def reject_keys(self) -> List[KeyPress]:
        return [KeyPress("Escape", enter=False)]

    def startup_dialog_rules(self) -> List[DialogRule]:
        return [
            # Trust prompt: default is accept, just Enter
            DialogRule(
                marker="I trust this folder",
                presses=[KeyPress("", enter=True)],
                settle_seconds=1.5,
            ),
            # Permissions warning: navigate Down to "Yes, I accept", then Enter
            DialogRule(
                marker="Yes, I accept",
                presses=[
                    KeyPress("Down", enter=False, delay_after=0.3),
                    KeyPress("", enter=True),
                ],
                settle_seconds=2.0,
            ),
        ]

    def prompt_ready_chars(self) -> Set[str]:
        return PROMPT_READY_CHARS

    def make_stats_reader(self) -> "StatsReader":
        from ..stats_reader import ClaudeStatsReader
        return ClaudeStatsReader()


_backend: Optional[AgentBackend] = None


def get_claude_backend() -> "ClaudeCodeBackend":
    """Module-level singleton — backends are stateless."""
    global _backend
    if _backend is None:
        _backend = ClaudeCodeBackend()
    return _backend
