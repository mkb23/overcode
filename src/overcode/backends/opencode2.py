"""opencode2 backend — the OpenCode 2.0 preview CLI.

Sibling of ``backends/opencode.py`` (v1). The two CLIs differ enough that
they are separate backends rather than one version-gated one: v2 renamed
its plugin API (TUI plugins only), moved session storage to the
``session_v2``/``session_message`` tables, and dropped ``--model``/``--agent``
/``--fork`` from the bare TUI. Launch-time model selection is currently
unsupported: ``OPENCODE_MODEL`` is set in ``env_prefix`` as forward-compat
but verified inert on v0.0.0-dev-19272 (the global-config model wins), so
``--model`` is ignored by opencode2 agents until a build honors it.
Everything below was verified against
``opencode2 v0.0.0-dev-19272`` (live, Sep 15 2026); the verified
behaviours are documented by the per-method comments.
"""

import re
from pathlib import Path
from typing import List, Optional, Tuple

from ..exceptions import AgentCliNotFoundError
from ..status_patterns import StatusPatterns
from .base import BackendCapability, KeyPress, LaunchSpec
from .opencode import parse_version

class Opencode2NotFoundError(AgentCliNotFoundError):
    """Raised when the opencode2 CLI isn't on PATH."""

# v2 is a rolling preview ("v0.0.0-dev-<build>"); there is no stable tag to
# test against, so the doctor never claims a "tested range" — it flags the
# installed build as a moving target instead.
OPENCODE2_PREVIEW_RE = re.compile(r"v0\.0\.0-dev-(\d+)")


# No env blob overrides a v2 project deny, verified against v0.0.0-dev-19272,
# Sep 15 2026: `opencode2 debug config` (with OPENCODE_PERMISSION /
# OPENCODE_PERMISSIONS set to an allow-everything blob in both grammars)
# shows only three permission layers — defaults, global
# ~/.config/opencode/opencode.json, project opencode.json — and no
# env-derived layer; with a project shell-deny in place, `opencode2 run
# --standalone --auto` plus either env blob still left the shell tool out
# of the model's catalog entirely. None until a build proves one.
_OPENCODE2_BYPASS_PERMISSION = None


class Opencode2Backend:
    """opencode2 CLI adapter (verified against v0.0.0-dev-19272).

    Deliberately absent vs the v1 backend: ``--model``/``--agent`` flags
    (rejected by the bare TUI — model selection is currently unsupported:
    ``OPENCODE_MODEL`` is set in ``env_prefix`` as forward-compat but
    inert on dev-19272, so the global-config model wins; agent has no
    launch-time knob at all), ``--fork``
    (exists only on ``mini``/``run``), and the ``OPENCODE_PERMISSION``
    per-tool env grammar (v2 permissions are ``{action, resource, effect}``
    arrays, and no env blob overrides a project ``deny`` — see
    ``_OPENCODE2_BYPASS_PERMISSION``). The v2 bypass is ``--auto`` only;
    project ``deny`` rules win (verified Sep 15 2026, build
    v0.0.0-dev-19272) — closer to Claude's ``dontAsk`` than to
    ``--dangerously-skip-permissions``.
    """

    name = "opencode2"
    display_name = "opencode2"
    binary = "opencode2"
    version_args = ("--version",)
    install_hint = (
        "opencode2 CLI is required but not found. "
        "Install the OpenCode 2.0 preview from: https://opencode.ai/docs/"
    )
    process_basenames = ("opencode2",)
    not_found_error = Opencode2NotFoundError
    capabilities = (
        BackendCapability.RESUME
        | BackendCapability.HOOK_EVENTS
        | BackendCapability.TRANSCRIPT_STATS
    )

    def executable(self) -> str:
        import os
        return os.environ.get("OPENCODE2_COMMAND", self.binary)

    def resume_args(self, session_id: str, fork: bool) -> List[str]:
        # --fork does not exist on the bare v2 TUI; the launcher gates on
        # the FORK capability bit, which this backend does not set.
        return ["--session", session_id]

    def build_command(self, spec: LaunchSpec) -> List[str]:
        import shlex
        # --standalone: every overcode-launched agent runs its own private
        # server. The v2 SSE event bus is directory-scoped — two agents
        # launched in the SAME directory each receive BOTH sessions' events
        # (cross-talk verified live, Sep 15 2026, v0.0.0-dev-19272), and the
        # telemetry reducer adopts the first session id it sees, so agent
        # B would mirror agent A's turns. A private server gives each agent
        # a process-scoped bus — the same isolation the v1 plugin has by
        # construction. Multiple agents per repo is overcode's core use
        # case, so this flag is unconditional, not a default.
        cmd = [self.executable(), "--standalone"]
        if spec.resume_session_id:
            cmd.extend(self.resume_args(spec.resume_session_id, spec.fork))
        # No --model/--agent here: the bare v2 TUI rejects them (verified).
        # model is exported via OPENCODE_MODEL in env_prefix — forward-compat
        # only, inert on dev-19272 (see env_prefix).
        if (
            spec.dangerously_skip_permissions
            or spec.skip_permissions
            or spec.permissiveness_mode in ("bypass", "permissive")
        ):
            cmd.append("--auto")
        if spec.extra_args:
            for arg in spec.extra_args:
                cmd.extend(shlex.split(arg))
        return cmd

    def prepare_launch(self, spec: LaunchSpec) -> None:
        from .opencode2_plugin_install import ensure_plugin_installed
        from ..config import get_backend_telemetry_enabled
        if not get_backend_telemetry_enabled(self.name):
            return None
        ensure_plugin_installed(spec.start_directory)

    def env_prefix(self, spec: LaunchSpec) -> "dict":
        import json, os, shlex
        env = {}
        state_dir = os.environ.get("OVERCODE_STATE_DIR")
        if state_dir:
            env["OVERCODE_STATE_DIR"] = shlex.quote(state_dir)
        if spec.model:
            # Forward-compat only, verified inert on v0.0.0-dev-19272: with
            # OPENCODE_MODEL=… in the environment the TUI still launches the
            # global-config model (live test, Sep 16 2026), so --model is
            # ignored by opencode2 agents until a build honors this var.
            # Kept so such a build needs no change here; the --model flag
            # does not exist on the bare TUI.
            env["OPENCODE_MODEL"] = shlex.quote(spec.model)
        if spec.dangerously_skip_permissions or spec.permissiveness_mode == "bypass":
            # No verified v2 env override exists (see
            # _OPENCODE2_BYPASS_PERMISSION); --auto in build_command is the
            # whole bypass, and project deny rules still win.
            if _OPENCODE2_BYPASS_PERMISSION is not None:  # None until a build proves one
                env["OPENCODE_PERMISSION"] = shlex.quote(
                    json.dumps(_OPENCODE2_BYPASS_PERMISSION)
                )
        return env

    def graceful_exit_keys(self) -> List[KeyPress]:
        # Verified against v0.0.0-dev-19272, Sep 15 2026: the busy footer
        # hints "esc interrupt" then "esc again to interrupt", so the two
        # Escapes arm and fire the interrupt exactly like v1. But v2's
        # command autocomplete consumes the first Enter to accept the
        # highlighted row: `/exit`+Enter only fills the input box (menu
        # stays open), and a trailing bare Enter executes the command —
        # the app closes and the tmux session ends.
        return [
            KeyPress("Escape", enter=False, delay_after=0.3),
            KeyPress("Escape", enter=False, delay_after=0.5),
            KeyPress("/exit", enter=True, delay_after=0.5),
            KeyPress("", enter=True, delay_after=0.3),
        ]

    def clear_conversation_keys(self) -> List[KeyPress]:
        # "/new  New session" resets the pane to the banner + empty input
        # box — verified against v0.0.0-dev-19272, Sep 15 2026. Same
        # autocomplete quirk as /exit: the first Enter accepts the
        # highlighted row, the trailing bare Enter runs it.
        return [
            KeyPress("/new", enter=True, delay_after=0.5),
            KeyPress("", enter=True, delay_after=0.3),
        ]

    def approve_keys(self) -> List[KeyPress]:
        # "Allow once" is preselected; Enter confirms it.
        return [KeyPress("", enter=True)]

    def reject_keys(self) -> List[KeyPress]:
        # Escape alone dismisses the v2 permission dialog and rejects the
        # tool call — verified against v0.0.0-dev-19272, Sep 15 2026:
        # after Escape the pane shows "The user declined this tool call"
        # and returns to the idle input box.
        return [KeyPress("Escape", enter=False)]

    def startup_dialog_rules(self) -> list:
        return []

    def prompt_ready_chars(self) -> "set":
        return {"┃"}

    def status_patterns(self) -> StatusPatterns:
        from .opencode2_patterns import OPENCODE2_PATTERNS
        return OPENCODE2_PATTERNS

    def make_stats_reader(self):
        from .opencode2_stats import Opencode2StatsReader
        return Opencode2StatsReader()

    def health_verdict(self, argv: str) -> Optional[Tuple[str, str]]:
        from ..doctor import VERDICT_OK
        return VERDICT_OK, "opencode2 process running"

    def refine_health_verdict(self, session, verdict: str, details: str):
        from ..doctor import VERDICT_MISSING_SETTINGS, VERDICT_OK
        if verdict != VERDICT_OK:
            return verdict, details
        start_directory = getattr(session, "start_directory", None)
        if not start_directory:
            return verdict, details
        from .opencode2_plugin_install import plugin_installed
        if plugin_installed(start_directory):
            return VERDICT_OK, "opencode2 process running, telemetry plugin installed"
        return VERDICT_MISSING_SETTINGS, (
            "opencode2 running without overcode's telemetry plugin in "
            f"{Path(start_directory) / '.opencode' / 'plugins'} — status falls "
            "back to pane polling. Relaunch via `overcode restart` to install it."
        )

    def check_binary(self):
        from ..dependency_check import check_agent_cli
        return check_agent_cli(self)


_backend: Optional[Opencode2Backend] = None


def get_opencode2_backend() -> Opencode2Backend:
    global _backend
    if _backend is None:
        _backend = Opencode2Backend()
    return _backend


def installed_version() -> Optional[str]:
    from ..dependency_check import check_agent_cli
    available, _path, version = check_agent_cli(
        get_opencode2_backend(), respect_override=False
    )
    if not available or not version:
        return None
    return version.strip() or None


def version_findings(version: Optional[str] = None) -> List[str]:
    resolved = version if version is not None else installed_version()
    if resolved is None:
        return [
            "could not determine the installed opencode2 version "
            "(`opencode2 --version` failed) — opencode2 is a rolling preview; "
            "pin the binary version your fleet was verified against"
        ]
    findings: List[str] = []
    if OPENCODE2_PREVIEW_RE.search(resolved):
        findings.append(
            f"opencode2 {resolved} is a dev preview — pane chrome, the plugin "
            "API and the SQLite schema move without notice; expect status/"
            "stats drift between builds"
        )
    elif parse_version(resolved) is None:
        findings.append(
            f"unrecognised opencode2 version string '{resolved}' — overcode "
            "cannot tell which preview build this is"
        )
    try:
        from .opencode2_stats import schema_findings
        findings.extend(schema_findings())
    except Exception:
        pass
    return findings
