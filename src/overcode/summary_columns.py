"""
Summary line columns, for the TUI rows and ``overcode list`` / ``show``.

The code lives in the ``overcode.columns`` package; this module re-exports
it so ``from overcode.summary_columns import ...`` keeps working:

- ``columns.context``: ColumnContext (what a cell renders from), the fleet
  flags (FleetFlags, compute_fleet_flags) and the CLI's build_cli_context.
- ``columns.column``: SummaryColumn, with each column's metadata (help,
  hover, sort key, hide-when-uniform, ``ticks``) on its definition.
- ``columns.render_*``: the cell renderers, one module per column group.
- ``columns.registry``: SUMMARY_COLUMNS in order, and rendering a row.
- ``columns.layout``: column widths, padding, the header row, column_at.

Each column is a SummaryColumn with an ID, group, detail-level gate, and a
render function that produces styled text segments. Its plain text (the
CLI's) defaults to the styled cell's text; a column gives its own
``render_plain`` only where the CLI wants different words.

A column whose cell reads the clock (a duration, a countdown, an "at
HH:MM") is marked ``ticks=True``. The TUI keeps each row's other cells
until one of the row's inputs changes and, on its 250 ms clock, redraws
only the ticking cells; a clock-reading column left unmarked would freeze
between deltas (test_row_cache checks every column an hour apart).
"""

from .columns.context import (
    ColumnOutput, FleetFlags, FleetRow, compute_fleet_flags, ColumnContext, build_cli_context,
)
from .columns.column import (
    ALL, MED_PLUS, HIGH_PLUS, plain_from_render, SummaryColumn,
)
from .columns.emoji import (
    TOOL_EMOJI, TOOL_EMOJI_DEFAULT, MAX_TOOL_EMOJI, SKILL_EMOJI_DEFAULT, WRAPPER_EMOJI_DEFAULT,
    SANDBOX_EMOJI, get_skill_emoji, get_wrapper_emoji, _tool_emojis,
)
from .columns.render_identity import (
    render_status_symbol, render_unvisited_alert, render_time_in_state, render_sleep_countdown,
    render_expand_icon, render_agent_name, render_status_plain, render_host, render_host_plain,
)
from .columns.render_git import (
    render_repo_name, render_branch, render_git_diff, render_git_untracked,
    render_git_untracked_plain, render_pr_number, render_git_diff_plain, render_repo_name_plain,
    render_branch_plain,
)
from .columns.render_time import (
    render_uptime, render_running_time, render_stalled_time, render_sleep_time,
    render_active_pct, render_uptime_plain, render_time_plain,
)
from .columns.render_usage import (
    render_token_count, render_cost, render_joules, render_budget, render_subtree_cost,
    render_subtree_cost_plain, burn_color_for_rate, render_burn_rate_plain, render_burn_rate,
    render_token_count_plain, render_cost_plain, render_billing_code, render_billing_code_plain,
)
from .columns.render_context import (
    render_context_usage, render_model, render_model_plain, EFFORT_LEVELS, effort_short_name,
    render_effort, render_effort_plain, render_provider, render_provider_plain, BACKEND_BADGES,
    _backend_badge, render_backend, render_backend_plain,
)
from .columns.render_performance import (
    render_median_work_time, render_cpu_pct, render_ram, render_subagents,
    render_subagents_plain, render_bash_count, render_child_count, render_job_count,
    render_work_plain, render_agents_plain,
)
from .columns.render_supervision import (
    render_permission_mode, render_agent_teams, render_teams_plain, render_allowed_tools,
    render_loaded_skills, render_skills_plain, render_skill_profile, render_skill_profile_plain,
    render_available_skills, render_available_skills_plain, render_enhanced_context,
    render_wrapper, render_wrapper_plain, render_human_count, render_robot_count,
    render_standing_orders, render_oversight_countdown, render_heartbeat, render_agent_value,
    render_mode_plain, render_tools_plain, render_heartbeat_plain, render_orders_plain,
    render_value_plain,
)
from .columns.hover import (
    hover_loaded_skills, hover_available_skills, hover_skill_profile, hover_allowed_tools,
    hover_wrapper, hover_status_detail,
)
from .columns.registry import (
    SUMMARY_COLUMNS, COLUMNS_BY_ID, COLUMN_ALIASES, canonical_column_id,
    migrate_column_overrides, resolve_column_visible, uniform_columns, render_summary_cells, cell_segments, cell_text, render_cell, render_summary_line,
    render_cli_stats,
)
from .columns.layout import (
    column_at, compute_column_widths, pad_and_join_cells, align_summary_rows,
    render_header_cells, grid_room,
)

__all__ = [
    "ColumnOutput", "FleetFlags", "FleetRow", "compute_fleet_flags", "ColumnContext",
    "build_cli_context", "ALL", "MED_PLUS", "HIGH_PLUS", "plain_from_render", "SummaryColumn",
    "TOOL_EMOJI", "TOOL_EMOJI_DEFAULT", "MAX_TOOL_EMOJI", "SKILL_EMOJI_DEFAULT",
    "WRAPPER_EMOJI_DEFAULT", "SANDBOX_EMOJI", "get_skill_emoji", "get_wrapper_emoji",
    "_tool_emojis", "render_status_symbol", "render_unvisited_alert", "render_time_in_state",
    "render_sleep_countdown", "render_expand_icon", "render_agent_name", "render_status_plain",
    "render_host", "render_host_plain", "render_repo_name", "render_branch", "render_git_diff",
    "render_git_untracked", "render_git_untracked_plain", "render_pr_number",
    "render_git_diff_plain", "render_repo_name_plain", "render_branch_plain", "render_uptime",
    "render_running_time", "render_stalled_time", "render_sleep_time", "render_active_pct",
    "render_uptime_plain", "render_time_plain", "render_token_count", "render_cost",
    "render_joules", "render_budget", "render_subtree_cost", "render_subtree_cost_plain",
    "burn_color_for_rate", "render_burn_rate_plain", "render_burn_rate",
    "render_token_count_plain", "render_cost_plain", "render_billing_code",
    "render_billing_code_plain", "render_context_usage", "render_model",
    "render_model_plain", "EFFORT_LEVELS", "effort_short_name", "render_effort",
    "render_effort_plain", "render_provider", "render_provider_plain", "BACKEND_BADGES",
    "_backend_badge", "render_backend", "render_backend_plain", "render_median_work_time",
    "render_cpu_pct", "render_ram", "render_subagents", "render_subagents_plain",
    "render_bash_count", "render_child_count", "render_job_count", "render_work_plain",
    "render_agents_plain",
    "render_permission_mode", "render_agent_teams", "render_teams_plain",
    "render_allowed_tools", "render_loaded_skills", "render_skills_plain",
    "render_skill_profile", "render_skill_profile_plain", "render_available_skills",
    "render_available_skills_plain", "render_enhanced_context", "render_wrapper",
    "render_wrapper_plain", "render_human_count", "render_robot_count",
    "render_standing_orders", "render_oversight_countdown", "render_heartbeat",
    "render_agent_value", "render_mode_plain", "render_tools_plain", "render_heartbeat_plain",
    "render_orders_plain", "render_value_plain", "hover_loaded_skills",
    "hover_available_skills", "hover_skill_profile", "hover_allowed_tools", "hover_wrapper",
    "hover_status_detail", "SUMMARY_COLUMNS", "COLUMNS_BY_ID", "COLUMN_ALIASES",
    "canonical_column_id", "migrate_column_overrides", "resolve_column_visible",
    "uniform_columns", "render_summary_cells", "cell_segments", "cell_text", "render_cell",
    "render_summary_line", "render_cli_stats", "column_at", "compute_column_widths",
    "pad_and_join_cells", "align_summary_rows", "render_header_cells", "grid_room",
]
