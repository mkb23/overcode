"""The ordered column registry, sort keys, and rendering a row's cells."""

from datetime import datetime
from typing import Callable, List, Optional, TYPE_CHECKING, Tuple

from ..clock import seconds_since
from .column import ALL, HIGH_PLUS, MED_PLUS, SummaryColumn
from .context import ColumnContext
from .hover import (
    hover_allowed_tools,
    hover_available_skills,
    hover_child_count,
    hover_loaded_skills,
    hover_permission_mode,
    hover_skill_profile,
    hover_spend,
    hover_status_detail,
    hover_swarm,
    hover_task_result,
    hover_wrapper,
)
from .render_context import (
    EFFORT_LEVELS,
    render_backend,
    render_backend_plain,
    render_context_usage,
    render_effort,
    render_effort_plain,
    render_model,
    render_model_plain,
    render_provider,
    render_provider_plain,
)
from .render_git import (
    render_branch,
    render_branch_plain,
    render_git_diff,
    render_git_diff_plain,
    render_git_untracked,
    render_git_untracked_plain,
    render_pr_number,
    render_repo_name,
    render_repo_name_plain,
)
from .render_identity import (
    render_agent_name,
    render_expand_icon,
    render_kind,
    render_host,
    render_host_plain,
    render_sleep_countdown,
    render_status_plain,
    render_status_symbol,
    render_time_in_state,
    render_unvisited_alert,
)
from .render_performance import (
    render_agents_plain,
    render_bash_count,
    render_child_count,
    render_cpu_pct,
    render_job_count,
    render_median_work_time,
    render_ram,
    render_subagent_count,
    render_swarm,
    render_work_plain,
)
from .render_supervision import (
    _skill_profile,
    render_agent_teams,
    render_agent_value,
    render_allowed_tools,
    render_available_skills,
    render_available_skills_plain,
    render_enhanced_context,
    render_heartbeat,
    render_heartbeat_plain,
    render_human_count,
    render_loaded_skills,
    render_mode_plain,
    render_orders_plain,
    render_oversight_countdown,
    render_permission_mode,
    render_robot_count,
    render_skill_profile,
    render_skill_profile_plain,
    render_skills_plain,
    render_task_result,
    render_task_result_plain,
    render_standing_orders,
    render_teams_plain,
    render_tools_plain,
    render_value_plain,
    render_wrapper,
    render_wrapper_plain,
)
from .render_time import (
    render_active_pct,
    render_running_time,
    render_sleep_time,
    render_stalled_time,
    render_time_plain,
    render_uptime,
    render_uptime_plain,
)
from .render_usage import (
    _billing_code,
    render_billing_code,
    render_billing_code_plain,
    render_budget,
    render_burn_rate,
    render_burn_rate_plain,
    render_cost,
    render_cost_plain,
    render_joules,
    render_subtree_cost,
    render_subtree_cost_plain,
    render_token_count,
    render_token_count_plain,
)

if TYPE_CHECKING:
    from rich.text import Text


# ---------------------------------------------------------------------------
# Sort keys (#487)
# ---------------------------------------------------------------------------
# A column's sort_key; None = not sortable. Rows whose key is None always
# sort last. agent_name / status_symbol / agent_value have keys too, but the
# TUI sorts them with the tree-aware presets in tui_logic (alphabetical,
# by_status, by_value) so they keep their established tie-breaks.

def _age_seconds(when) -> Optional[float]:
    """Seconds since a datetime or ISO string; None when unknown."""
    if not when:
        return None
    # Epoch arithmetic: exact across a DST change (naive or aware, string or datetime)
    return seconds_since(when, datetime.now().timestamp())


def _lower(s) -> Optional[str]:
    return s.lower() if s and s not in ("-", "n/a") else None


def _sort_active_pct(ctx: ColumnContext) -> Optional[float]:
    active = ctx.green_time + ctx.non_green_time
    return ctx.green_time / active if active > 0 else None


def _sort_context_usage(ctx: ColumnContext) -> Optional[float]:
    cs = ctx.claude_stats
    if cs is None or cs.current_context_tokens <= 0 or not cs.max_context_tokens:
        return None
    return cs.current_context_tokens / cs.max_context_tokens


def _sort_burn_rate(ctx: ColumnContext) -> Optional[float]:
    burn = ctx.window_burn
    if burn is None or burn.window_hours <= 0:
        return None
    # Sort by what the column shows: tokens/h in token mode, else $/h
    return burn.tokens_per_hour if ctx.show_cost == "tokens" else burn.cost_per_hour


def _sort_model(ctx: ColumnContext) -> Optional[str]:
    if not ctx.model:
        return None
    from ..history_reader import model_short_name
    return model_short_name(ctx.model).lower()


def _sort_effort(ctx: ColumnContext) -> Optional[int]:
    if not ctx.effort:
        return None
    level = ctx.effort.strip().lower()
    return EFFORT_LEVELS.index(level) if level in EFFORT_LEVELS else -1


def _sort_backend(ctx: ColumnContext) -> str:
    from ..backends import session_backend_name
    return session_backend_name(ctx.session)


def _sort_heartbeat(ctx: ColumnContext) -> Optional[tuple]:
    s = ctx.session
    if not s.heartbeat_enabled:
        return None
    return (bool(s.heartbeat_paused), s.heartbeat_frequency_seconds)


# Failures first, then what still needs a look, then successes
_RESULT_ORDER = {"failure": 0, "timeout": 1, "unreported": 2, "success": 3}


def _sort_task_result(ctx: ColumnContext) -> Optional[int]:
    return _RESULT_ORDER.get(ctx.task_result or "")


def _sort_oversight(ctx: ColumnContext) -> Optional[str]:
    return ctx.oversight_deadline or None


def _if_stats(fn: Callable[[ColumnContext], object]) -> Callable[[ColumnContext], object]:
    """Sort key that is None for backends reporting no stats (the column shows '-')."""
    return lambda ctx: fn(ctx) if ctx.claude_stats is not None else None


# ---------------------------------------------------------------------------
# Ordered column list — reorder by moving items
# ---------------------------------------------------------------------------
SUMMARY_COLUMNS: List[SummaryColumn] = [
    # Identity group — always visible
    SummaryColumn(id="status_symbol", group="identity", detail_levels=ALL, render=render_status_symbol,
                  label="Status", render_plain=render_status_plain, name="Status",
                  description="What the agent is doing and whether it needs you: working, waiting "
                              "on something automatic, needs approval or input, at rest, unknown, gone",
                  sort_key=lambda ctx: ctx.stats.current_state),
    SummaryColumn(id="unvisited_alert", group="identity", detail_levels=ALL, render=render_unvisited_alert,
                  name="Alert",
                  description="Flags an agent that stalled and you haven't looked at since",
                  sort_key=lambda ctx: int(ctx.is_unvisited_stalled),
                  sort_desc=True),
    SummaryColumn(id="time_in_state", ticks=True, group="identity", detail_levels=ALL, render=render_time_in_state,
                  header="ST", name="Time in State",
                  description="How long the agent has been in its current status",
                  sort_key=lambda ctx: _age_seconds(ctx.status_changed_at),
                  sort_desc=True),
    SummaryColumn(id="sleep_countdown", ticks=True, group="identity", detail_levels=ALL, render=render_sleep_countdown,
                  visible=lambda ctx: ctx.fleet.any_is_sleeping or ctx.fleet.any_has_status_detail,
                  header="DTL", name="Status Detail",
                  description="Status detail: current tool, subagents, sleep wake-up estimate",
                  hover=hover_status_detail),
    SummaryColumn(id="task_result", group="identity", detail_levels=ALL, render=render_task_result,
                  label="Result", render_plain=render_task_result_plain,
                  visible=lambda ctx: ctx.fleet.any_has_task_result,
                  header="RES", name="Result",
                  description="A child agent's outcome: 📭 stopped without a report, ✅ success, "
                              "❌ failure, ⌛ oversight timeout (hover for the full reason)",
                  sort_key=_sort_task_result,
                  hover=hover_task_result),
    SummaryColumn(id="swarm", group="identity", detail_levels=ALL, render=render_swarm,
                  label="Delegated",
                  visible=lambda ctx: ctx.fleet.any_has_swarm,
                  header="DLG", name="Delegated Work",
                  description="Work the agent handed out in its own session (subagents, forks, "
                              "workflow agents, teammates): total, ▶ working, 🟠 needs "
                              "approval, ✅ finished, ❌ failed, 🧩 workflow runs",
                  sort_key=lambda ctx: (ctx.swarm_rollup or {}).get("total"),
                  sort_desc=True,
                  hover=hover_swarm),
    SummaryColumn(id="expand_icon", group="identity", detail_levels=ALL, render=render_expand_icon,
                  name="Expand",
                  description="Fold marker for tree view"),
    SummaryColumn(id="kind", group="identity", detail_levels=set(), render=render_kind,
                  label="Kind", header="KIND", name="Kind",
                  description="Agent, or a child another agent launched (→ shows "
                              "delegated work: 👥, 🧩)",
                  sort_key=lambda ctx: ctx.agent_kind, hide_when_uniform=True),
    SummaryColumn(id="agent_name", group="identity", detail_levels=ALL, render=render_agent_name,
                  header="NAME", name="Agent Name",
                  description="Agent name (tree connectors show parent/child in tree sort)",
                  sort_key=lambda ctx: ctx.session.name.lower()),
    SummaryColumn(id="host", group="sisters", detail_levels=ALL, render=render_host,
                  label="Host", render_plain=render_host_plain, header="HST", name="Host",
                  description="Machine the agent runs on (shown when sister hosts are configured)",
                  sort_key=lambda ctx: _lower(ctx.source_host or ctx.local_hostname),
                  hide_when_uniform=True),

    # Git group — repo, branch (high/full only), diff stats
    SummaryColumn(id="repo_name", group="git", detail_levels=HIGH_PLUS, render=render_repo_name,
                  label="Repo", render_plain=render_repo_name_plain, header="RPO", name="Repo Name",
                  description="Git repository the agent is working in",
                  sort_key=lambda ctx: _lower(ctx.repo_name),
                  hide_when_uniform=True),
    SummaryColumn(id="branch", group="git", detail_levels=HIGH_PLUS, render=render_branch,
                  label="Branch", render_plain=render_branch_plain, header="BR", name="Branch",
                  description="Git branch checked out in the agent's working directory",
                  sort_key=lambda ctx: _lower(ctx.branch),
                  hide_when_uniform=True),
    SummaryColumn(id="git_diff", group="git", detail_levels=ALL, render=render_git_diff,
                  label="Git", render_plain=render_git_diff_plain, header="GIT", name="Git Diff",
                  description="Uncommitted changes: files changed, lines added, lines removed",
                  sort_key=lambda ctx: (ctx.git_diff_stats[0], ctx.git_diff_stats[1] + ctx.git_diff_stats[2])
                      if ctx.git_diff_stats else None,
                  sort_desc=True),
    SummaryColumn(id="git_untracked", group="git", detail_levels=HIGH_PLUS, render=render_git_untracked,
                  label="Untracked", render_plain=render_git_untracked_plain, header="UN",
                  name="Untracked Files",
                  description="Untracked files in the working tree (work not yet added to git)",
                  sort_key=lambda ctx: ctx.git_untracked_count,
                  sort_desc=True),
    SummaryColumn(id="pr_number", group="git", detail_levels=ALL, render=render_pr_number,
                  label="PR",
                  visible=lambda ctx: ctx.fleet.any_has_pr, placeholder_width=8, header="PR", name="PR Number",
                  description="Open pull request for the agent's branch",
                  sort_key=lambda ctx: ctx.pr_number,
                  sort_desc=True),

    # Time group — uptime, running, stalled, sleep, active%
    SummaryColumn(id="uptime", ticks=True, group="time", detail_levels=MED_PLUS, render=render_uptime,
                  label="Uptime", render_plain=render_uptime_plain, header="UPT", name="Uptime",
                  description="Time since the agent was launched",
                  sort_key=lambda ctx: _age_seconds(ctx.session.start_time),
                  sort_desc=True),
    SummaryColumn(id="running_time", ticks=True, group="time", detail_levels=MED_PLUS, render=render_running_time,
                  header="RUN", name="Running Time",
                  description="Total time spent working (green) since launch",
                  sort_key=lambda ctx: ctx.green_time,
                  sort_desc=True),
    SummaryColumn(id="stalled_time", ticks=True, group="time", detail_levels=MED_PLUS, render=render_stalled_time,
                  header="STL", name="Stalled Time",
                  description="Total time spent waiting or stalled (non-green) since launch",
                  sort_key=lambda ctx: ctx.non_green_time,
                  sort_desc=True),
    SummaryColumn(id="sleep_time", ticks=True, group="time", detail_levels=MED_PLUS, render=render_sleep_time,
                  header="ZZZ", name="Sleep Time",
                  description="Total time spent asleep",
                  sort_key=lambda ctx: ctx.sleep_time,
                  sort_desc=True),
    SummaryColumn(id="active_pct", ticks=True, group="time", detail_levels=HIGH_PLUS, render=render_active_pct,
                  header="ACT", name="Active %",
                  description="Share of awake time spent working rather than waiting",
                  sort_key=_sort_active_pct,
                  sort_desc=True),
    # Synthetic CLI-only: combined time line
    SummaryColumn(id="time_combined", ticks=True, group="time", detail_levels=set(), render=lambda ctx: None,
                  label="Time", render_plain=render_time_plain, cli_only=True),

    # LLM usage group — TOK, ENRG, $, BDG, SUB$ (energy before cost for readability)
    SummaryColumn(id="token_count", group="llm_usage", detail_levels=ALL, render=render_token_count,
                  label="Tokens", render_plain=render_token_count_plain, header="TOK", name="Token Count",
                  description="Total tokens used (input + output + cache)",
                  sort_key=_if_stats(lambda ctx: ctx.claude_stats.total_tokens),
                  sort_desc=True, hover=hover_spend),
    SummaryColumn(id="joules", group="llm_usage", detail_levels=set(), render=render_joules,
                  header="ENRG", name="Energy (Joules)",
                  description="Estimated energy: tokens x a guess at the model's GPU energy (config: energy)",
                  sort_key=_if_stats(lambda ctx: ctx.session.stats.estimated_energy_j),
                  sort_desc=True),
    SummaryColumn(id="cost", group="llm_usage", detail_levels=set(), render=render_cost,
                  label="Cost", render_plain=render_cost_plain, header="$", name="Cost",
                  description="Estimated spend in dollars, from token usage and model pricing",
                  sort_key=_if_stats(lambda ctx: ctx.session.stats.estimated_cost_usd),
                  sort_desc=True, hover=hover_spend),
    SummaryColumn(id="budget", group="llm_usage", detail_levels=set(), render=render_budget,
                  visible=lambda ctx: ctx.fleet.any_has_budget, placeholder_width=7,
                  header="BDG", name="Budget",
                  description="Cost budget set for this agent (B to edit)",
                  sort_key=lambda ctx: ctx.session.cost_budget_usd or None,
                  sort_desc=True),
    SummaryColumn(id="subtree_cost", group="llm_usage", detail_levels=set(),
                  render=render_subtree_cost, label="Subtree",
                  render_plain=render_subtree_cost_plain,
                  visible=lambda ctx: ctx.fleet.any_has_subtree_cost,
                  placeholder_width=8, header="SUB$", name="Subtree Cost",
                  description="Cost of this agent plus all its children",
                  sort_key=lambda ctx: ctx.subtree_cost_usd or None,
                  sort_desc=True),
    # Hidden below full detail; `overcode list` (full) shows it.
    SummaryColumn(id="billing_code", group="llm_usage", detail_levels={"full"},
                  render=render_billing_code, label="Billing",
                  render_plain=render_billing_code_plain, header="BILL", name="Billing Code",
                  description="Billing code new spend goes to (dim: the agent's own name); "
                              "set it from the palette, / then 'billing'",
                  sort_key=lambda ctx: _billing_code(ctx) or None,
                  hide_when_uniform=True),
    # Burn rate over the timeline window (#174). Hidden until at least one
    # agent has window-scoped activity, so the column doesn't take width
    # when burn data isn't being computed (TUI not running, no recent tokens).
    SummaryColumn(id="burn_rate", group="llm_usage", detail_levels=HIGH_PLUS,
                  render=render_burn_rate, label="Burn",
                  render_plain=render_burn_rate_plain,
                  visible=lambda ctx: ctx.fleet.any_has_burn,
                  placeholder_width=9, header="BURN", name="Burn Rate",
                  description="Spend rate over the timeline window (units follow $)",
                  sort_key=_sort_burn_rate,
                  sort_desc=True),

    # Context group — always visible, independent of $ toggle
    SummaryColumn(id="context_usage", group="context", detail_levels=ALL, render=render_context_usage,
                  header="CTX", name="Context Usage",
                  description="How full the model's context window is",
                  sort_key=_sort_context_usage,
                  sort_desc=True),
    SummaryColumn(id="model", group="context", detail_levels=ALL, render=render_model,
                  label="Model", render_plain=render_model_plain,
                  visible=lambda ctx: ctx.fleet.any_has_model,
                  placeholder_width=8, header="MDL", name="Model",
                  description="Model the agent is running",
                  sort_key=_sort_model,
                  hide_when_uniform=True),
    SummaryColumn(id="effort", group="context", detail_levels=MED_PLUS, render=render_effort,
                  label="Effort", render_plain=render_effort_plain,
                  visible=lambda ctx: ctx.fleet.any_has_effort,
                  placeholder_width=6, header="EFF", name="Effort",
                  description="Reasoning effort the model is running at (low, med, high, xhigh, max…)",
                  sort_key=_sort_effort,
                  sort_desc=True,
                  hide_when_uniform=True),
    SummaryColumn(id="provider", group="context", detail_levels=ALL, render=render_provider,
                  label="Provider", render_plain=render_provider_plain,
                  visible=lambda ctx: ctx.fleet.any_has_provider,
                  placeholder_width=3, header="PRV", name="Provider",
                  description="API provider (web or Bedrock)",
                  sort_key=lambda ctx: getattr(ctx.session, 'provider', 'web') or 'web',
                  hide_when_uniform=True),
    SummaryColumn(id="backend", group="context", detail_levels=ALL, render=render_backend,
                  label="Backend", render_plain=render_backend_plain,
                  visible=lambda ctx: ctx.fleet.mixed_backends,
                  placeholder_width=3, header="BKD", name="Agent Backend",
                  description="Agent CLI: cc Claude Code, oc opencode, cx codex, gk grok, hm hermes",
                  sort_key=_sort_backend,
                  hide_when_uniform=True),

    # Performance group — median work, CPU, RAM
    SummaryColumn(id="median_work_time", group="performance", detail_levels=MED_PLUS, render=render_median_work_time,
                  header="MED", name="Median Work Time",
                  description="Median length of a work cycle between human prompts",
                  sort_key=_if_stats(lambda ctx: ctx.median_work),
                  sort_desc=True),
    SummaryColumn(id="cpu_pct", group="performance", detail_levels=HIGH_PLUS, render=render_cpu_pct,
                  label="CPU",
                  visible=lambda ctx: ctx.fleet.any_has_cpu,
                  placeholder_width=6, header="CPU", name="CPU %",
                  description="CPU used by the agent's process tree (over 100% = several cores)",
                  sort_key=lambda ctx: getattr(ctx.session, 'cpu_percent', 0.0) or 0.0,
                  sort_desc=True),
    SummaryColumn(id="ram", group="performance", detail_levels=HIGH_PLUS, render=render_ram,
                  label="RAM",
                  visible=lambda ctx: ctx.fleet.any_has_ram,
                  placeholder_width=6, header="RAM", name="Memory (RSS)",
                  description="Resident memory of the agent's process tree",
                  sort_key=lambda ctx: getattr(ctx.session, 'rss_bytes', 0) or 0,
                  sort_desc=True),

    # Subprocesses group
    SummaryColumn(id="subagent_count", group="subprocesses", detail_levels=HIGH_PLUS, render=render_subagent_count,
                  header="SUB", name="Subagent Count",
                  description="Subagents running right now",
                  sort_key=lambda ctx: ctx.live_subagent_count,
                  sort_desc=True),
    SummaryColumn(id="bash_count", group="subprocesses", detail_levels=HIGH_PLUS, render=render_bash_count,
                  header="SH", name="Bash Count",
                  description="Background shell commands running right now",
                  sort_key=lambda ctx: ctx.background_bash_count,
                  sort_desc=True),
    SummaryColumn(id="child_count", group="subprocesses", detail_levels=HIGH_PLUS, render=render_child_count,
                  header="CH", name="Child Count",
                  description="Overcode child agents launched by this agent, and how they "
                              "stand: ▶ working, 📭 unreported, ✅ success, ❌ failure",
                  hover=hover_child_count,
                  sort_key=lambda ctx: ctx.child_count,
                  sort_desc=True),
    SummaryColumn(id="job_count", group="subprocesses", detail_levels=HIGH_PLUS, render=render_job_count,
                  header="JOB", name="Job Count",
                  description="Overcode jobs this agent launched that are still running",
                  sort_key=lambda ctx: ctx.job_count,
                  sort_desc=True),
    # Synthetic CLI-only: combined work + interactions line
    SummaryColumn(id="work_combined", group="performance", detail_levels=set(), render=lambda ctx: None,
                  label="Work", render_plain=render_work_plain, cli_only=True),
    # Synthetic CLI-only: combined agents line
    SummaryColumn(id="agents_combined", group="subprocesses", detail_levels=set(), render=lambda ctx: None,
                  label="Agents", render_plain=render_agents_plain, cli_only=True),

    # Supervision group
    SummaryColumn(id="permission_mode", group="supervision", detail_levels=ALL, render=render_permission_mode,
                  label="Mode", render_plain=render_mode_plain, header="MOD", name="Permission Mode",
                  description="Permission mode: 👮 normal, 🏃 permissive, 🔥 bypass; "
                              "switched in the session: ⚡ accept edits, 🧠 auto mode, 🧭 plan mode",
                  sort_key=lambda ctx: ctx.effective_permission_mode,
                  hide_when_uniform=True, hover=hover_permission_mode),
    SummaryColumn(id="agent_teams", group="supervision", detail_levels=ALL, render=render_agent_teams,
                  label="Teams", render_plain=render_teams_plain, header="TM", name="Teams",
                  description="Agent teams enabled",
                  sort_key=lambda ctx: int(bool(ctx.session.agent_teams)),
                  sort_desc=True),
    SummaryColumn(id="wrapper", group="supervision", detail_levels=ALL, render=render_wrapper,
                  label="Wrapper", render_plain=render_wrapper_plain, header="WRP", name="Wrapper",
                  description="Launch wrapper (e.g. devcontainer) and sandbox badge",
                  sort_key=lambda ctx: render_wrapper_plain(ctx),
                  hover=hover_wrapper,
                  hide_when_uniform=True),
    SummaryColumn(id="allowed_tools", group="supervision", detail_levels=ALL, render=render_allowed_tools,
                  label="Tools", render_plain=render_tools_plain, header="TLS", name="Allowed Tools",
                  description="Tools the agent was launched with permission to use",
                  hover=hover_allowed_tools),
    SummaryColumn(id="skill_profile", group="supervision", detail_levels=ALL, render=render_skill_profile,
                  label="Skill Profile", render_plain=render_skill_profile_plain, header="PRF",
                  name="Skill Profile",
                  description="Skill profile the agent launched with, as its emoji; hover a row to name it (W to edit profiles)",
                  sort_key=lambda ctx: _skill_profile(ctx) or "",
                  hover=hover_skill_profile),
    SummaryColumn(id="loaded_skills", group="supervision", detail_levels=ALL, render=render_loaded_skills,
                  label="Loaded Skills", render_plain=render_skills_plain, header="SKL", name="Loaded Skills",
                  description="Skills the agent has loaded this session",
                  sort_key=lambda ctx: len(ctx.session.loaded_skills or []),
                  sort_desc=True,
                  hover=hover_loaded_skills),
    SummaryColumn(id="available_skills", group="supervision", detail_levels=ALL, render=render_available_skills,
                  label="Available Skills", render_plain=render_available_skills_plain, header="ASK", name="Available Skills",
                  description="Skills installed and available to the agent",
                  sort_key=lambda ctx: len(ctx.session.available_skills or []),
                  sort_desc=True,
                  hover=hover_available_skills),
    SummaryColumn(id="enhanced_context", group="supervision", detail_levels=ALL, render=render_enhanced_context,
                  header="EC", name="Enhanced Context",
                  description="Enhanced context hook on (^T to toggle)",
                  sort_key=lambda ctx: int(bool(ctx.session.enhanced_context_enabled)),
                  sort_desc=True),
    SummaryColumn(id="human_count", group="supervision", detail_levels=ALL, render=render_human_count,
                  header="H#", name="Human Count",
                  description="Prompts sent by a human",
                  sort_key=_if_stats(lambda ctx: max(0, ctx.claude_stats.interaction_count
                      - ctx.stats.steers_count)),
                  sort_desc=True),
    SummaryColumn(id="robot_count", group="supervision", detail_levels=ALL, render=render_robot_count,
                  header="R#", name="Robot Count",
                  description="Prompts sent by the supervisor (steers)",
                  sort_key=lambda ctx: ctx.stats.steers_count,
                  sort_desc=True),
    SummaryColumn(id="standing_orders", group="supervision", detail_levels=ALL, render=render_standing_orders,
                  label="Orders", render_plain=render_orders_plain, header="ORD", name="Standing Orders",
                  description="Standing orders: ✓ complete, preset name, 📋 custom, ➖ none",
                  sort_key=lambda ctx: (not ctx.session.standing_orders_complete,)
                      if ctx.session.standing_instructions else None,
                  sort_desc=True),
    SummaryColumn(id="heartbeat", ticks=True, group="supervision", detail_levels=ALL, render=render_heartbeat,
                  label="Heartbeat", render_plain=render_heartbeat_plain, header="HB", name="Heartbeat",
                  description="Heartbeat interval and next send time (⏸ paused)",
                  sort_key=_sort_heartbeat),
    SummaryColumn(id="oversight_countdown", ticks=True, group="supervision", detail_levels=ALL, render=render_oversight_countdown,
                  visible=lambda ctx: ctx.fleet.any_has_oversight_timeout, placeholder_width=8,
                  header="OVR", name="Oversight Countdown",
                  description="Time left before an oversight timeout fires",
                  sort_key=_sort_oversight),

    # Priority group
    SummaryColumn(id="agent_value", group="priority", detail_levels=ALL, render=render_agent_value,
                  label="Value", render_plain=render_value_plain, header="VAL", name="Agent Value",
                  description="Priority value (V to edit; 1000 is neutral)",
                  sort_key=lambda ctx: ctx.session.agent_value,
                  sort_desc=True),
]

COLUMNS_BY_ID: dict[str, SummaryColumn] = {c.id: c for c in SUMMARY_COLUMNS}


def resolve_column_visible(col: SummaryColumn, level: str, overrides: dict,
                           uniform: Optional[dict] = None) -> bool:
    """Determine if a column is visible at a given level with user overrides.

    - full: default True for all columns, but explicit False overrides still apply
    - low/med/high: start from col.detail_levels default, then apply overrides
    - a column in ``uniform`` (see uniform_columns) is hidden unless the
      user switched it on explicitly
    """
    base = True if level == "full" else level in col.detail_levels
    col_override = overrides.get(col.id)
    if col_override is not None:
        return col_override
    if uniform and col.id in uniform:
        return False
    return base


def uniform_columns(contexts: "List[ColumnContext]") -> dict:
    """``{column id: shared value}`` for hide-when-uniform columns whose
    value is the same on every row.

    Needs two or more rows: a lone agent keeps its details. The value is
    the column's plain text, so rows that differ only in styling count as
    the same; None (nothing to show) is a value like any other.
    """
    if len(contexts) < 2:
        return {}
    shared = {}
    for col in SUMMARY_COLUMNS:
        if not col.hide_when_uniform or col.render_plain is None:
            continue
        try:
            values = {col.render_plain(ctx) for ctx in contexts}
        except Exception:
            continue
        if len(values) == 1:
            shared[col.id] = values.pop()
    return shared


def render_summary_cells(
    ctx: ColumnContext,
    column_filter: Optional[Callable[[SummaryColumn], bool]] = None,
    group_filter: Optional[Callable[[str], bool]] = None,
) -> "List[Text]":
    """Render each visible column as a separate Text cell.

    Returns one Text per visible column position (including placeholders).
    Used by the CLI for automatic cross-row alignment via align_summary_rows().
    The TUI calls render_summary_line() which concatenates these.

    Args:
        ctx: Pre-computed column context.
        column_filter: Optional callback to check column visibility. Takes a
            SummaryColumn and returns bool. When provided, replaces the default
            detail_levels check and group_filter.
        group_filter: Legacy callback to check group visibility. Ignored when
            column_filter is provided.
    """
    cells = []
    for col in SUMMARY_COLUMNS:
        if column_filter is not None:
            if not column_filter(col):
                continue
        else:
            if ctx.summary_detail not in col.detail_levels:
                continue
            if group_filter is not None and not group_filter(col.group):
                continue
        cells.append(render_cell(col, ctx))
    return cells


def cell_segments(col: SummaryColumn, ctx: ColumnContext) -> list:
    """One column's (text, style) segments for one row, its placeholder
    included; [] for an empty cell."""
    if col.visible is not None and not col.visible(ctx):
        segments = None
    else:
        segments = col.render(ctx)
    if segments:
        return segments
    if col.placeholder_width > 0:
        return [(" " * col.placeholder_width, f"dim{ctx.bg}")]
    return []


def cell_text(segments: list) -> "Text":
    """A cell's Text from its segments."""
    from rich.text import Text
    cell = Text()
    for text, style in segments:
        cell.append(text, style=style)
    return cell


def render_cell(col: SummaryColumn, ctx: ColumnContext) -> "Text":
    """One column's cell for one row: its segments, or its placeholder."""
    return cell_text(cell_segments(col, ctx))


def render_summary_line(
    ctx: ColumnContext,
    column_filter: Optional[Callable[[SummaryColumn], bool]] = None,
    group_filter: Optional[Callable[[str], bool]] = None,
) -> "Text":
    """Render a single summary line from SUMMARY_COLUMNS.

    This is the canonical render loop — both TUI and CLI call this.
    Concatenates cells from render_summary_cells() into a single Text.
    """
    from rich.text import Text
    content = Text()
    for cell in render_summary_cells(ctx, column_filter=column_filter, group_filter=group_filter):
        content.append_text(cell)
    return content


def render_cli_stats(ctx: ColumnContext) -> List[Tuple[str, str]]:
    """Render columns as CLI key-value lines from SUMMARY_COLUMNS.

    Iterates SUMMARY_COLUMNS. For each column with a render_plain,
    calls it and collects (label, value). Columns sharing a label
    are merged onto one line separated by "  ".

    Returns list of (label, value) tuples for print formatting.
    """
    result: List[Tuple[str, str]] = []
    seen_labels: dict = {}  # label -> index in result

    for col in SUMMARY_COLUMNS:
        if not col.label or col.render_plain is None:
            continue
        value = col.render_plain(ctx)
        if value is None:
            continue
        if col.label in seen_labels:
            idx = seen_labels[col.label]
            old_label, old_value = result[idx]
            result[idx] = (old_label, f"{old_value}  {value}")
        else:
            seen_labels[col.label] = len(result)
            result.append((col.label, value))

    return result
