"""Backend seam: the protocol every agent CLI adapter implements.

An ``AgentBackend`` owns everything that is specific to one agent CLI —
argv grammar, env vars, startup-dialog handshake, key gestures — so the
rest of overcode can stay backend-neutral. See
``docs/design/agent-agnostic-backends-opencode.md`` §2.1.
"""

from dataclasses import dataclass, field
from enum import Flag, auto
from typing import (
    TYPE_CHECKING,
    Dict,
    List,
    Optional,
    Protocol,
    Sequence,
    Set,
    Tuple,
    Type,
)

from .. import hook_dialects

if TYPE_CHECKING:
    from ..stats_reader import StatsReader
    from ..status_patterns import StatusPatterns


class BackendCapability(Flag):
    """What an agent CLI can do. Used to gate UI actions and telemetry."""

    NONE = 0
    RESUME = auto()                   # relaunch continuing a prior conversation
    FORK = auto()                     # branch a conversation into a new agent
    SESSION_ID_PRESCRIPTION = auto()  # overcode chooses the session id up front
    HOOK_EVENTS = auto()              # push telemetry (hook-state files) available
    TRANSCRIPT_STATS = auto()         # tokens/cost/context readable from disk
    PERMISSION_INJECTION = auto()     # per-launch permission allowlist
    AGENT_INJECTION = auto()           # launch-time persona/agent flag (--agent) is honored
    SKILLS = auto()                   # skills/persona file discovery
    SANDBOX_PROBE = auto()
    SUBSCRIPTION_USAGE = auto()
    AGENT_TEAMS = auto()
    SKILL_PROFILES = auto()           # per-launch skill profile: add a skill folder, hide personal skills (#499)


@dataclass(frozen=True)
class KeyPress:
    """One tmux send-keys gesture, with the pause that must follow it."""

    keys: str
    enter: bool = False
    delay_after: float = 0.0
    # Send only once this text is on screen (e.g. the cursor reached the
    # option to choose). If it never shows, the press is skipped and the
    # dialog is retried, rather than confirming whatever is selected.
    wait_for: Optional[str] = None


@dataclass(frozen=True)
class DialogRule:
    """A startup dialog to dismiss while waiting for the input prompt.

    ``marker`` is matched against captured pane content; ``presses`` are
    sent in order, then the poller sleeps ``settle_seconds``.
    """

    marker: str
    presses: Sequence[KeyPress]
    settle_seconds: float = 1.0
    # Other rules' markers this one answers too: once it is handled they are
    # never fired (an older wording that also matches this dialog's text)
    supersedes: Sequence[str] = ()


@dataclass
class LaunchSpec:
    """Everything a backend needs to render one launch invocation.

    Carries the launch-time knobs recorded on a ``Session`` plus the
    backend-neutral OVERCODE_* identity fields, so a backend can inject
    them into its own env/config if it needs to.
    """

    # Session identity (exported into the child process env)
    name: str = ""
    session_id: str = ""
    tmux_session: str = ""
    parent_session_id: Optional[str] = None
    parent_name: Optional[str] = None

    # Conversation selection
    resume_session_id: Optional[str] = None
    fork: bool = False
    prescribed_session_id: Optional[str] = None

    # Permissions
    permissiveness_mode: Optional[str] = None
    skip_permissions: bool = False
    dangerously_skip_permissions: bool = False
    include_punchy_perms: bool = False

    # Agent configuration
    model: Optional[str] = None
    agent: Optional[str] = None
    allowed_tools: Optional[str] = None
    extra_args: List[str] = field(default_factory=list)
    agent_teams: bool = False
    provider: str = "web"

    # Execution environment
    start_directory: Optional[str] = None
    wrapper: Optional[str] = None
    mock_scenario: Optional[str] = None

    # Skill profile (#499), resolved by the launcher: a folder of linked
    # skills to add for this session, and personal skills to hide.
    skill_dir: Optional[str] = None
    hidden_skills: List[str] = field(default_factory=list)

    # A file holding the first prompt, for a backend that takes it on the
    # command line (``prompt_as_argument``); the launcher writes it.
    initial_prompt_file: Optional[str] = None


class AgentBackend(Protocol):
    """Adapter for one agent CLI."""

    name: str                       # "claude-code" | "opencode" | "opencode2" | "codex" | "grok" | "hermes" | "shell"
    display_name: str
    binary: str                     # for dependency_check + doctor process matching
    version_args: Sequence[str]
    install_hint: str
    process_basenames: Sequence[str]
    not_found_error: Type[Exception]
    capabilities: BackendCapability

    # See AgentBackendBase for the defaults of the members below.
    fork_prescribes_new_session_id: bool
    process_argv_markers: Sequence[str]
    hook_events: Sequence[str]
    hook_dialect: hook_dialects.HookDialect

    def build_command(self, spec: LaunchSpec) -> List[str]: ...

    def prepare_launch(self, spec: LaunchSpec) -> None:
        """Side effects the CLI needs in place before the process starts.

        Called once per launch/restart/revive/fork, after the binary check and
        before the shell line is sent. Claude Code needs nothing here — its
        telemetry rides on ``--settings`` — but opencode installs its
        telemetry plugin into the project directory. Must be idempotent and
        must never raise: a failure costs telemetry, not the launch.
        """
        ...

    def env_prefix(self, spec: LaunchSpec) -> Dict[str, str]: ...

    def resume_args(self, session_id: str, fork: bool) -> List[str]: ...

    def graceful_exit_keys(self) -> List[KeyPress]: ...

    def approve_keys(self) -> List[KeyPress]: ...

    def reject_keys(self) -> List[KeyPress]: ...

    def startup_dialog_rules(self) -> List[DialogRule]: ...

    def prompt_ready_chars(self) -> Set[str]: ...

    def prompt_ready_line(self, line: str) -> bool: ...

    def uninstall_telemetry(self, project_dir: Optional[str] = None) -> Tuple[bool, str]: ...

    def doctor_findings(self) -> List[str]: ...

    def status_patterns(self) -> "StatusPatterns": ...

    def make_stats_reader(self) -> "StatsReader": ...

    def health_verdict(self, argv: str) -> Optional[Tuple[str, str]]:
        """Doctor's "is observability wired up?" answer for a live process.

        Returns ``(verdict, details)`` from the ``doctor.VERDICT_*``
        vocabulary, given the agent process's full argv.
        """
        ...

    def refine_health_verdict(self, session, verdict: str, details: str) -> Tuple[str, str]: ...

    def make_polling_detector(self, tmux_session: str, tmux=None, patterns=None): ...

    def normalize_hook_payload(self, data: dict) -> Optional[dict]: ...


class AgentBackendBase:
    """Defaults for the AgentBackend members most backends don't need.

    Every backend inherits this, so callers call these members directly.
    """

    # True when the backend's fork grammar takes an explicit new session id
    # (--session-id alongside --fork-session) AND the prescribed id is
    # authoritative for the forked session; False when the CLI mints its own
    # fork id and discovery must fill it in. Only meaningful alongside
    # SESSION_ID_PRESCRIPTION — launcher.py's fork branch mints a fresh uuid
    # to bind eagerly only when both are true. Both grok (Phase 3) and Claude
    # Code (#466, 2026-08-28) declare this True: Claude Code was originally
    # assumed to mint its own, different fork id, but a live check of
    # `claude --resume <id> --fork-session --session-id <new>` found the
    # prescribed id IS honored — the CLI writes the fork's transcript under
    # the prescribed uuid. That wrong assumption was #466's root cause:
    # without eager binding, a fork's id was discovered via directory+time
    # matching, which is ambiguous whenever another agent shares the same
    # start_directory (the fork's record could bind to a sibling's id
    # instead of its own).
    fork_prescribes_new_session_id: bool = False

    # Substrings that identify the agent's live process by argv when its
    # basename is not distinctive — hermes runs as Hermes's own venv python
    # (`.../venv/bin/python .../hermes-agent/hermes --cli`), so matching
    # ``process_basenames`` alone would have to match "python".
    # ``doctor.find_agent_process`` accepts a process whose basename is in
    # ``process_basenames`` OR whose argv contains any marker.
    process_argv_markers: Sequence[str] = ()

    # Launch and send, for CLIs that need more than a paste once the input
    # line shows (agent_io): seconds to look again for a dialog drawn over
    # the input line, whether to check the first prompt reached the screen,
    # whether the first prompt goes on the command line, and whether a
    # multi-line send is typed rather than pasted.
    prompt_settle_seconds: float = 0.0
    confirm_prompt_delivery: bool = False
    prompt_as_argument: bool = False
    type_multiline_text: bool = False

    # The hook events the backend registers ``overcode hook-handler`` for,
    # where overcode chooses them per event (codex's -c overrides, grok's
    # hooks file). Empty when the CLI's own plugin or
    # settings decide (Claude Code's are OVERCODE_HOOKS in hook_handler).
    hook_events: Sequence[str] = ()

    # How the backend's hook payloads translate into Claude Code's
    # vocabulary. Lives in hook_dialects (stdlib-only, for the hook path's
    # import cost); hook_dialects.for_backend must agree with it.
    hook_dialect: hook_dialects.HookDialect = hook_dialects.CLAUDE

    def normalize_hook_payload(self, data: dict) -> Optional[dict]:
        """A hook payload in Claude Code's vocabulary, or None to drop it."""
        return self.hook_dialect.normalize(data)

    def prompt_ready_line(self, line: str) -> bool:
        """Is this (ANSI-stripped, whitespace-trimmed) pane line the live,
        empty input prompt?

        ``AgentIO._wait_for_prompt`` asks this of every pane line. The
        default is an exact match against ``prompt_ready_chars()``; a
        backend whose prompt is never drawn bare (hermes always follows its
        glyph with a rotating placeholder hint) overrides it.
        """
        return line in self.prompt_ready_chars()

    def status_patterns(self) -> "StatusPatterns":
        from ..status_patterns import DEFAULT_PATTERNS
        return DEFAULT_PATTERNS

    def make_polling_detector(self, tmux_session: str, tmux=None, patterns=None):
        """The polling status detector for this backend's panes.

        Pane scraping with the backend's patterns by default; a backend
        whose status can't come from pane text (the plain shell, #496)
        supplies its own.
        """
        from ..status_detector import PollingStatusDetector
        return PollingStatusDetector(tmux_session, tmux=tmux, patterns=patterns)

    def refine_health_verdict(self, session, verdict: str, details: str) -> Tuple[str, str]:
        """A second look at ``health_verdict`` with the session in hand.

        For a backend whose telemetry leaves no trace on the command line —
        opencode checks for its telemetry plugin in the project directory.
        """
        return verdict, details

    def uninstall_telemetry(self, project_dir: Optional[str] = None) -> Tuple[bool, str]:
        """Remove this backend's on-disk telemetry footprint.

        Backs ``overcode hooks uninstall-backend <name>``. Returns ``(ok,
        message)``: ``ok`` False means the footprint was found but *not*
        removed (not overcode-managed, or ``project_dir`` was needed and
        missing) and the CLI exits non-zero. The default is for a backend
        whose telemetry leaves nothing on disk (Claude Code's
        ``--settings``, codex's ``-c`` overrides).
        """
        return True, f"nothing installed on disk for this backend ({self.name})"

    def doctor_findings(self) -> List[str]:
        """Fleet-level warnings for ``overcode doctor`` — version outside
        the tested range, missing auth, stats-store schema drift. Only
        called when the fleet actually has an agent on this backend.
        """
        return []


def supports(backend: AgentBackend, capability: BackendCapability) -> bool:
    """True when ``backend`` declares ``capability``."""
    return bool(backend.capabilities & capability)
