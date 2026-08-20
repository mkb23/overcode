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
    Type,
)

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
    SKILLS = auto()                   # skills/persona file discovery
    SANDBOX_PROBE = auto()
    SUBSCRIPTION_USAGE = auto()
    AGENT_TEAMS = auto()


@dataclass(frozen=True)
class KeyPress:
    """One tmux send-keys gesture, with the pause that must follow it."""

    keys: str
    enter: bool = False
    delay_after: float = 0.0


@dataclass(frozen=True)
class DialogRule:
    """A startup dialog to dismiss while waiting for the input prompt.

    ``marker`` is matched against captured pane content; ``presses`` are
    sent in order, then the poller sleeps ``settle_seconds``.
    """

    marker: str
    presses: Sequence[KeyPress]
    settle_seconds: float = 1.0


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


class AgentBackend(Protocol):
    """Adapter for one agent CLI."""

    name: str                       # "claude-code" | "opencode"
    display_name: str
    binary: str                     # for dependency_check + doctor process matching
    version_args: Sequence[str]
    install_hint: str
    process_basenames: Sequence[str]
    not_found_error: Type[Exception]
    capabilities: BackendCapability

    def build_command(self, spec: LaunchSpec) -> List[str]: ...

    def env_prefix(self, spec: LaunchSpec) -> Dict[str, str]: ...

    def resume_args(self, session_id: str, fork: bool) -> List[str]: ...

    def graceful_exit_keys(self) -> List[KeyPress]: ...

    def clear_conversation_keys(self) -> List[KeyPress]: ...

    def approve_keys(self) -> List[KeyPress]: ...

    def reject_keys(self) -> List[KeyPress]: ...

    def startup_dialog_rules(self) -> List[DialogRule]: ...

    def prompt_ready_chars(self) -> Set[str]: ...

    def status_patterns(self) -> "StatusPatterns": ...

    def make_stats_reader(self) -> "StatsReader": ...


def supports(backend: AgentBackend, capability: BackendCapability) -> bool:
    """True when ``backend`` declares ``capability``."""
    return bool(backend.capabilities & capability)
