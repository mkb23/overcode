"""What a column renders from: ColumnContext, the fleet flags, the CLI's context."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, List, Optional, Tuple

from ..clock import from_iso
from ..status_constants import StatusDetail, effective_permission_mode, get_permissiveness_emoji
from ..status_patterns import extract_sleep_duration
from ..tui_helpers import calculate_uptime, get_current_state_times, get_status_symbol


# ---------------------------------------------------------------------------
# Type alias for column output: list of (text, style) segments, or None to skip
# ---------------------------------------------------------------------------
ColumnOutput = Optional[List[Tuple[str, str]]]


# ---------------------------------------------------------------------------
# FleetFlags: whether a column shows at all, from every row
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FleetFlags:
    """Fleet-wide gates for columns that only earn their width once some
    row has something to show (a budget, a PR, a second backend…).

    One value for the whole table, from compute_fleet_flags(); the TUI and
    ``overcode list`` both build it that way, so they agree. Frozen and
    compared by value: assigning an equal one to a TUI row is no change.
    """

    any_has_budget: bool = False  # a cost budget (#173)
    any_has_oversight_timeout: bool = False  # an oversight timeout policy
    any_has_pr: bool = False  # an open PR
    any_has_model: bool = False  # a model known
    any_has_effort: bool = False  # a reasoning effort detected (#497)
    any_has_provider: bool = False  # a non-web API provider
    # More than one agent CLI. The column only appears once a fleet is
    # mixed: a Claude-only user sees no change from opencode support landing.
    mixed_backends: bool = False
    any_has_cpu: bool = False  # a non-zero CPU reading
    any_has_ram: bool = False  # a non-zero RAM reading
    any_is_sleeping: bool = False  # busy_sleeping (#289)
    # A populated status detail, so the DTL column stays when someone has a
    # non-sleep badge
    any_has_status_detail: bool = False
    any_has_subtree_cost: bool = False  # a parent with subtree cost
    any_has_burn: bool = False  # a non-zero burn rate (#174)
    any_has_task_result: bool = False  # a child with a result (the Result column)
    any_has_swarm: bool = False  # an agent that delegated work in its session (👥)
    # Subagents seen running without a hook rollup (the SubAgents fallback)
    any_has_live_subagents: bool = False


@dataclass(frozen=True)
class FleetRow:
    """What the fleet flags read from one row."""

    session: object  # as the row draws it (sessions.json with the engine's view)
    status: str = ""  # the status the row shows
    pr_number: Optional[int] = None
    subtree_cost_usd: float = 0.0
    window_burn: Optional[object] = None  # Optional[WindowBurnStats]
    has_status_detail: bool = False
    task_result: Optional[str] = None  # the child's result kind, if any
    has_swarm: bool = False  # a 👥 rollup published for it
    has_live_subagents: bool = False  # subagents running by the file / status-bar count


def compute_fleet_flags(rows: Iterable[FleetRow]) -> FleetFlags:
    """The fleet flags for the rows on show."""
    from ..backends import session_backend_name
    rows = list(rows)
    sessions = [r.session for r in rows]
    return FleetFlags(
        any_has_budget=any((getattr(s, 'cost_budget_usd', 0) or 0) > 0 for s in sessions),
        any_has_oversight_timeout=any(
            getattr(s, 'oversight_policy', 'wait') == 'timeout'
            and (getattr(s, 'oversight_timeout_seconds', 0) or 0) > 0
            for s in sessions
        ),
        any_has_pr=any(r.pr_number is not None for r in rows),
        any_has_model=any(bool(getattr(s, 'model', None)) for s in sessions),
        any_has_effort=any(bool(getattr(s, 'effort', None)) for s in sessions),
        any_has_provider=any(getattr(s, 'provider', 'web') not in ('web', None, '') for s in sessions),
        mixed_backends=len({session_backend_name(s) for s in sessions}) > 1,
        any_has_cpu=any((getattr(s, 'cpu_percent', 0.0) or 0.0) > 0.0 for s in sessions),
        any_has_ram=any((getattr(s, 'rss_bytes', 0) or 0) > 0 for s in sessions),
        any_is_sleeping=any(r.status == "busy_sleeping" for r in rows),
        any_has_status_detail=any(r.has_status_detail for r in rows),
        any_has_subtree_cost=any(r.subtree_cost_usd > 0 for r in rows),
        any_has_burn=any(
            r.window_burn is not None
            and (r.window_burn.tokens_per_hour > 0 or r.window_burn.cost_per_hour > 0)
            for r in rows
        ),
        any_has_task_result=any(r.task_result for r in rows),
        any_has_swarm=any(r.has_swarm for r in rows),
        any_has_live_subagents=any(r.has_live_subagents for r in rows),
    )


# ---------------------------------------------------------------------------
# ColumnContext: all pre-computed data a column needs
# ---------------------------------------------------------------------------
@dataclass
class ColumnContext:
    """Pre-computed data passed to every column render function."""

    # Session and stats
    session: object  # Session
    stats: object  # SessionStats
    claude_stats: object  # Optional[AgentSessionStats]
    git_diff_stats: Optional[tuple]  # (files, insertions, deletions) or None

    # Pre-computed values
    status_symbol: str
    status_color: str
    bg: str  # background style suffix, e.g. " on #0d2137" or ""
    emoji_free: bool
    summary_detail: str
    show_cost: str  # "tokens", "cost", "joules"
    expand_icon: str
    is_list_mode: bool
    has_focus: bool
    is_unvisited_stalled: bool

    # Time values
    uptime: str
    green_time: float
    non_green_time: float
    sleep_time: float
    median_work: float

    # Pre-computed strings
    repo_name: str
    branch: str
    display_name: str
    perm_emoji: str

    # Repo column visibility
    all_names_match_repos: bool  # True → hide repo column (redundant with agent name)

    # Live counts
    live_subagent_count: int
    background_bash_count: int
    child_count: int  # Agent hierarchy child count (#244)

    # Time-in-state
    status_changed_at: Optional[datetime]

    # App-level alignment widths
    max_name_width: int
    max_repo_width: int
    max_branch_width: int

    # Running overcode jobs this agent launched (#463)
    job_count: int = 0

    # The status the row shows (the published one; asleep / terminated
    # applied), for the plain status text
    status: str = ""

    # A child's task outcome (RESULT_KINDS key, or None) and its full
    # reason; a parent's children by state (working / unreported /
    # success / failure counts), None without children
    task_result: Optional[str] = None
    task_reason: str = ""
    child_rollup: Optional[dict] = None
    # Its delegated work (swarm.rollup counts), None if it never delegated
    swarm_rollup: Optional[dict] = None
    # Its delegated work is expanded into rows under it (→ / ←, TUI only)
    swarm_expanded: bool = False
    # The delegated work's share of its spend (engine: subagent_cost_usd)
    subagent_cost_usd: Optional[float] = None

    # Fleet-wide column gates, the same for every row
    fleet: FleetFlags = field(default_factory=FleetFlags)

    # Oversight countdown
    oversight_deadline: Optional[str] = None

    # Sleep countdown (#289)
    sleep_wake_estimate: Optional[datetime] = None  # Estimated wake time

    # Two-column status model (#TBD). When populated, the ⏰ column renders
    # emoji-stacked badges from the hook reducer instead of just the sleep
    # countdown. None → legacy sleep countdown rendering.
    status_detail: Optional["StatusDetail"] = None  # noqa: F821 (forward ref)

    # Subtree cost (parent + all descendants)
    subtree_cost_usd: float = 0.0

    # PR number (widget var, not session — survives session replacement)
    pr_number: Optional[int] = None

    # Model
    model: str = ""  # Model short name or full name

    # Reasoning effort (#497)
    effort: str = ""  # Verbatim from the backend ("high", "xhigh", "none", …)

    # Sister integration (#245)
    source_host: str = ""
    is_remote: bool = False
    has_sisters: bool = False
    local_hostname: str = ""

    # Untracked file count (#455). None when unknown (e.g. not a git repo).
    git_untracked_count: Optional[int] = None

    # The in-session mode the status bar shows: "accept_edits", "auto",
    # "plan" or "" (#444). Overlays the launch mode through
    # status_constants.effective_permission_mode.
    in_session_mode: str = ""

    # A delegated-work row drawn on the grid (a swarm_view.SwarmLine), None
    # for an agent's own row. Its cells come from each column's
    # ``virtual_render`` (columns.render_virtual); a column without one
    # shows its placeholder. Built by ColumnContext.for_virtual.
    virtual: Optional[object] = None

    @classmethod
    def for_virtual(cls, line, parent: "ColumnContext", selected: bool = False) -> "ColumnContext":
        """The context for one virtual row (a subagent, a group, a phase, a
        summary line) under the agent ``parent`` draws: the parent's view
        settings (emoji, $ mode, name width, fleet gates), the row's own
        values, and nothing of the parent's agent data."""
        from .render_virtual import virtual_context
        return virtual_context(line, parent, selected)

    @property
    def agent_kind(self) -> str:
        """The KIND column (agent-observability §4.0): an overcode agent,
        ``child`` when another agent launched it, ``bg`` for a 🌙 Claude
        background session nested under the agent it came from. Virtual
        rows (👥 subagents, 🧩 workflows) are drawn under their parent."""
        if getattr(self.session, "kind", None) == "claude_bg":
            return "bg"
        return "child" if getattr(self.session, "parent_session_id", None) else "agent"

    @property
    def effective_permission_mode(self) -> str:
        """The MOD column's mode: launch setting overlaid by the in-session one."""
        from ..status_constants import effective_permission_mode
        return effective_permission_mode(self.session.permissiveness_mode, self.in_session_mode)

    # Window-scoped burn rate for this session (#174). None when the burn
    # column isn't enabled or the session has no recent token activity.
    window_burn: Optional[object] = None  # Optional[WindowBurnStats]

    def e(self, char: str) -> str:
        """Return ASCII fallback if emoji_free mode is active (#315),
        otherwise route through the VS16-stripping safety net so
        Windows Terminal / Konsole don't drift on variation-selector
        emoji clusters (#174-followup)."""
        if self.emoji_free:
            from ..status_constants import EMOJI_ASCII
            return EMOJI_ASCII.get(char, char)
        from ..status_constants import _safe_emoji
        return _safe_emoji(char)


def build_cli_context(
    session, stats, claude_stats, git_diff_stats,
    status: str, bg_bash_count: int, live_sub_count: int,
    git_untracked_count: Optional[int] = None,
    in_session_mode: str = "",
    fleet: Optional[FleetFlags] = None, child_count: int = 0,
    oversight_deadline: Optional[str] = None,
    pr_number: Optional[int] = None,
    emoji_free: bool = False, summary_detail: str = "full",
    has_sisters: bool = False, local_hostname: str = "",
    max_name_width: int = 16, max_repo_width: int = 10,
    max_branch_width: int = 10, all_names_match_repos: bool = False,
    subtree_cost_usd: float = 0.0,
    status_detail: Optional[StatusDetail] = None,
    window_burn: Optional[object] = None,
    task_result: Optional[str] = None,
    task_reason: str = "",
    child_rollup: Optional[dict] = None,
    swarm_rollup: Optional[dict] = None,
) -> ColumnContext:
    """Build a ColumnContext from CLI data (no TUI widget needed).

    ``fleet`` is the table's compute_fleet_flags(); None leaves every
    fleet-gated column off.
    """
    status_symbol, _ = get_status_symbol(status, emoji_free=emoji_free)
    uptime = calculate_uptime(session.start_time) if session.start_time else "-"
    green_time, non_green_time, sleep_time = get_current_state_times(
        stats, is_asleep=session.is_asleep
    )
    median_work = claude_stats.median_work_time if claude_stats else 0.0
    perm_emoji = get_permissiveness_emoji(
        effective_permission_mode(session.permissiveness_mode, in_session_mode), emoji_free)

    # Parse state_since for time-in-state
    status_changed_at = None
    if stats.state_since:
        try:
            status_changed_at = from_iso(stats.state_since)
        except (ValueError, TypeError):
            pass

    # Compute sleep wake estimate (#289)
    sleep_wake_estimate = None
    if status == "busy_sleeping" and status_changed_at:
        dur = extract_sleep_duration(getattr(stats, 'current_task', '') or '')
        if dur:
            sleep_wake_estimate = status_changed_at + timedelta(seconds=dur)

    return ColumnContext(
        session=session,
        stats=stats,
        claude_stats=claude_stats,
        git_diff_stats=git_diff_stats,
        git_untracked_count=git_untracked_count,
        in_session_mode=in_session_mode,
        status_symbol=status_symbol,
        status_color="bold",
        bg="",
        fleet=fleet if fleet is not None else FleetFlags(),
        emoji_free=emoji_free,
        summary_detail=summary_detail,
        show_cost="cost",
        expand_icon="",
        is_list_mode=False,
        has_focus=False,
        is_unvisited_stalled=False,
        uptime=uptime,
        green_time=green_time,
        non_green_time=non_green_time,
        sleep_time=sleep_time,
        median_work=median_work,
        repo_name=session.repo_name or "-",
        branch=session.branch or "-",
        display_name=session.name,
        perm_emoji=perm_emoji,
        all_names_match_repos=all_names_match_repos,
        live_subagent_count=live_sub_count,
        background_bash_count=bg_bash_count,
        child_count=child_count,
        status=status,
        task_result=task_result,
        task_reason=task_reason,
        child_rollup=child_rollup,
        swarm_rollup=swarm_rollup,
        status_changed_at=status_changed_at,
        max_name_width=max_name_width,
        max_repo_width=max_repo_width,
        max_branch_width=max_branch_width,
        oversight_deadline=oversight_deadline,
        sleep_wake_estimate=sleep_wake_estimate,
        status_detail=status_detail,
        pr_number=pr_number,
        model=getattr(session, 'model', '') or '',
        effort=getattr(session, 'effort', '') or '',
        source_host=getattr(session, 'source_host', ''),
        is_remote=getattr(session, 'is_remote', False),
        has_sisters=has_sisters,
        local_hostname=local_hostname,
        subtree_cost_usd=subtree_cost_usd,
        window_burn=window_burn,
    )
