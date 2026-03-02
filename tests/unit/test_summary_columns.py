"""Tests for summary_columns module."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from overcode.summary_columns import (
    ALL,
    MED_PLUS,
    FULL_PLUS,
    TOOL_EMOJI,
    TOOL_EMOJI_DEFAULT,
    MAX_TOOL_EMOJI,
    _tool_emojis,
    ColumnContext,
    SummaryColumn,
    SUMMARY_COLUMNS,
    render_status_symbol,
    render_unvisited_alert,
    render_time_in_state,
    render_sleep_countdown,
    render_expand_icon,
    render_agent_name,
    render_repo_name,
    render_branch,
    render_uptime,
    render_running_time,
    render_stalled_time,
    render_sleep_time,
    render_active_pct,
    render_token_count,
    render_context_usage,
    render_cost,
    render_budget,
    render_tokens,
    render_git_diff,
    render_median_work_time,
    render_subagent_count,
    render_bash_count,
    render_permission_mode,
    render_allowed_tools,
    render_time_context,
    render_human_count,
    render_robot_count,
    render_standing_orders,
    render_heartbeat,
    render_agent_value,
    render_status_plain,
    render_uptime_plain,
    render_time_plain,
    render_token_count_plain,
    render_context_usage_plain,
    render_cost_plain,
    render_tokens_plain,
    render_git_diff_plain,
    render_work_plain,
    render_agents_plain,
    render_repo_name_plain,
    render_branch_plain,
    render_mode_plain,
    render_tools_plain,
    render_heartbeat_plain,
    render_orders_plain,
    render_value_plain,
    render_pr_number,
    render_pr_number_plain,
    build_cli_context,
    render_cli_stats,
)
from overcode.summary_groups import SUMMARY_GROUPS_BY_ID


# ---------------------------------------------------------------------------
# Helper to build a ColumnContext with sensible defaults
# ---------------------------------------------------------------------------

def _make_stats(**overrides):
    defaults = dict(
        state_since=None,
        steers_count=0,
        estimated_cost_usd=0.0,
    )
    defaults.update(overrides)
    mock = MagicMock()
    for k, v in defaults.items():
        setattr(mock, k, v)
    return mock


def _make_session(**overrides):
    defaults = dict(
        name="test-agent",
        repo_name="test-repo",
        branch="main",
        standing_instructions=None,
        standing_orders_complete=False,
        standing_instructions_preset=None,
        is_asleep=False,
        time_context_enabled=False,
        agent_value=1000,
        permissiveness_mode="normal",
        start_time="2025-01-15T10:00:00",
        human_annotation=None,
        heartbeat_enabled=False,
        heartbeat_frequency_seconds=300,
        heartbeat_paused=False,
        last_heartbeat_time=None,
        heartbeat_instruction=None,
        cost_budget_usd=0.0,
        allowed_tools=None,
        pr_number=None,
        stats=_make_stats(),
    )
    defaults.update(overrides)
    mock = MagicMock()
    for k, v in defaults.items():
        setattr(mock, k, v)
    return mock


def _make_claude_stats(**overrides):
    defaults = dict(
        total_tokens=150000,
        current_context_tokens=50000,
        interaction_count=10,
        median_work_time=120.0,
        max_context_tokens=200_000,
    )
    defaults.update(overrides)
    mock = MagicMock()
    for k, v in defaults.items():
        setattr(mock, k, v)
    return mock


def _make_ctx(**overrides) -> ColumnContext:
    session = overrides.pop("session", _make_session())
    stats = overrides.pop("stats", session.stats)
    defaults = dict(
        session=session,
        stats=stats,
        claude_stats=None,
        git_diff_stats=None,
        status_symbol="🟢",
        status_color="bold green on #0d2137",
        bg=" on #0d2137",
        monochrome=False,
        summary_detail="full",
        show_cost=False,
        any_has_budget=False,
        expand_icon="▼",
        is_list_mode=False,
        has_focus=False,
        is_unvisited_stalled=False,
        uptime="2.5h",
        green_time=3600.0,
        non_green_time=1200.0,
        sleep_time=0.0,
        median_work=120.0,
        repo_name="test-repo",
        branch="main",
        display_name="test-agent      ",
        perm_emoji="👮",
        all_names_match_repos=False,
        live_subagent_count=0,
        background_bash_count=0,
        child_count=0,
        status_changed_at=None,
        max_name_width=16,
        max_repo_width=10,
        max_branch_width=10,
    )
    defaults.update(overrides)
    return ColumnContext(**defaults)


# ===========================================================================
# Column structure tests
# ===========================================================================

class TestColumnStructure:
    """Tests for SUMMARY_COLUMNS list structure."""

    def test_no_duplicate_ids(self):
        """All column IDs must be unique."""
        ids = [col.id for col in SUMMARY_COLUMNS]
        assert len(ids) == len(set(ids)), f"Duplicate IDs found: {[x for x in ids if ids.count(x) > 1]}"

    def test_all_group_ids_valid(self):
        """Every column's group must exist in SUMMARY_GROUPS_BY_ID."""
        for col in SUMMARY_COLUMNS:
            assert col.group in SUMMARY_GROUPS_BY_ID, f"Column '{col.id}' references unknown group '{col.group}'"

    def test_detail_levels_are_subsets_of_all(self):
        """All detail_levels sets must be subsets of ALL."""
        for col in SUMMARY_COLUMNS:
            assert col.detail_levels <= ALL, f"Column '{col.id}' has invalid detail levels: {col.detail_levels - ALL}"

    def test_convenience_constants(self):
        """Verify convenience constant values."""
        assert ALL == {"low", "med", "full", "custom"}
        assert MED_PLUS == {"med", "full", "custom"}
        assert FULL_PLUS == {"full", "custom"}

    def test_every_column_has_render_callable(self):
        """Every column must have a callable render function."""
        for col in SUMMARY_COLUMNS:
            assert callable(col.render), f"Column '{col.id}' render is not callable"

    def test_columns_with_label_have_render_plain(self):
        """Every column with a label must also have a render_plain callable."""
        for col in SUMMARY_COLUMNS:
            if col.label:
                assert col.render_plain is not None and callable(col.render_plain), \
                    f"Column '{col.id}' has label '{col.label}' but no render_plain"

    def test_visible_callback_is_callable_when_set(self):
        """Every column with a visible callback must have it be callable."""
        for col in SUMMARY_COLUMNS:
            if col.visible is not None:
                assert callable(col.visible), f"Column '{col.id}' visible is not callable"

    def test_placeholder_width_positive_when_visible_set(self):
        """Columns with a visible gate should have placeholder_width > 0."""
        for col in SUMMARY_COLUMNS:
            if col.visible is not None:
                assert col.placeholder_width > 0, \
                    f"Column '{col.id}' has visible gate but placeholder_width={col.placeholder_width}"

    def test_columns_with_visible_gate(self):
        """Known columns should have visible gates."""
        gated_ids = {c.id for c in SUMMARY_COLUMNS if c.visible is not None}
        assert "pr_number" in gated_ids
        assert "budget" in gated_ids
        assert "oversight_countdown" in gated_ids
        assert "sleep_countdown" in gated_ids


# ===========================================================================
# ColumnContext tests
# ===========================================================================

class TestColumnContext:
    """Tests for ColumnContext."""

    def test_mono_returns_colored_when_not_monochrome(self):
        ctx = _make_ctx(monochrome=False)
        assert ctx.mono("bold green on #0d2137", "bold") == "bold green on #0d2137"

    def test_mono_returns_simple_when_monochrome(self):
        ctx = _make_ctx(monochrome=True)
        assert ctx.mono("bold green on #0d2137", "bold") == "bold"

    def test_mono_default_simple_is_bold(self):
        ctx = _make_ctx(monochrome=True)
        assert ctx.mono("bold green") == "bold"


# ===========================================================================
# Column loop gating tests
# ===========================================================================

class TestColumnGating:
    """Test the gating logic that would be applied in the render loop."""

    def test_low_detail_skips_med_plus_columns(self):
        """Columns with MED_PLUS detail should be skipped in 'low' mode."""
        for col in SUMMARY_COLUMNS:
            if col.detail_levels == MED_PLUS:
                assert "low" not in col.detail_levels

    def test_low_detail_skips_full_plus_columns(self):
        """Columns with FULL_PLUS detail should be skipped in 'low' mode."""
        for col in SUMMARY_COLUMNS:
            if col.detail_levels == FULL_PLUS:
                assert "low" not in col.detail_levels

    def test_all_detail_includes_every_level(self):
        """Columns with ALL detail should be visible in every mode."""
        for col in SUMMARY_COLUMNS:
            if col.detail_levels == ALL:
                for level in ("low", "med", "full", "custom"):
                    assert level in col.detail_levels


# ===========================================================================
# Identity column render tests
# ===========================================================================

class TestRenderStatusSymbol:
    def test_returns_symbol_with_status_color(self):
        ctx = _make_ctx(status_symbol="🟢", status_color="bold green")
        result = render_status_symbol(ctx)
        assert result == [("🟢 ", "bold green")]


class TestRenderUnvisitedAlert:
    def test_shows_bell_when_unvisited(self):
        ctx = _make_ctx(is_unvisited_stalled=True)
        result = render_unvisited_alert(ctx)
        assert result[0][0] == "🔔"

    def test_shows_spacer_when_visited(self):
        ctx = _make_ctx(is_unvisited_stalled=False)
        result = render_unvisited_alert(ctx)
        assert result[0][0] == "  "


class TestRenderTimeInState:
    def test_shows_elapsed_time_from_status_changed_at(self):
        now = datetime.now()
        ctx = _make_ctx(status_changed_at=now - timedelta(seconds=90))
        result = render_time_in_state(ctx)
        assert result is not None
        text = result[0][0]
        # Should contain a formatted duration ending with space
        assert text.strip() != "-"

    def test_shows_dash_when_no_state_start(self):
        ctx = _make_ctx(status_changed_at=None)
        result = render_time_in_state(ctx)
        assert "- " in result[0][0]

    def test_uses_daemon_state_since_when_more_recent(self):
        old_local = datetime.now() - timedelta(hours=1)
        recent_daemon = (datetime.now() - timedelta(seconds=30)).isoformat()
        stats = _make_stats(state_since=recent_daemon)
        ctx = _make_ctx(status_changed_at=old_local, stats=stats)
        result = render_time_in_state(ctx)
        text = result[0][0].strip()
        # Should show ~30s, not 1h
        assert text != "-"


class TestRenderSleepCountdown:
    """Tests for render_sleep_countdown column (#289)."""

    def test_returns_none_when_not_sleeping(self):
        """Should return None when agent is not sleeping."""
        ctx = _make_ctx(sleep_wake_estimate=None, any_is_sleeping=False)
        assert render_sleep_countdown(ctx) is None

    def test_returns_none_when_no_estimate(self):
        """Should return None (framework handles placeholder) when no estimate."""
        ctx = _make_ctx(sleep_wake_estimate=None, any_is_sleeping=True)
        assert render_sleep_countdown(ctx) is None

    def test_shows_remaining_time(self):
        """Should show countdown when sleep_wake_estimate is in the future."""
        wake_time = datetime.now() + timedelta(seconds=270)  # ~4.5 minutes
        ctx = _make_ctx(sleep_wake_estimate=wake_time, any_is_sleeping=True)
        result = render_sleep_countdown(ctx)
        assert result is not None
        text = result[0][0]
        assert "⏰" in text
        assert "4.5m" in text or "4.4m" in text  # Allow small timing variance

    def test_shows_zero_when_expired(self):
        """Should show '0s' when sleep_wake_estimate is in the past."""
        wake_time = datetime.now() - timedelta(seconds=10)
        ctx = _make_ctx(sleep_wake_estimate=wake_time, any_is_sleeping=True)
        result = render_sleep_countdown(ctx)
        assert result is not None
        text = result[0][0]
        assert "⏰" in text
        assert "0s" in text

    def test_style_is_yellow(self):
        """Should use yellow style."""
        wake_time = datetime.now() + timedelta(seconds=120)
        ctx = _make_ctx(sleep_wake_estimate=wake_time, any_is_sleeping=True, monochrome=False)
        result = render_sleep_countdown(ctx)
        assert "yellow" in result[0][1]

    def test_active_and_expired_same_display_width(self):
        """Active and expired countdowns must both be 9 display cells."""
        import unicodedata

        def display_width(s):
            """Compute terminal display width accounting for wide chars."""
            return sum(2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in s)

        # Active countdown
        wake = datetime.now() + timedelta(seconds=120)
        active = render_sleep_countdown(_make_ctx(sleep_wake_estimate=wake, any_is_sleeping=True))
        # Expired countdown
        expired = render_sleep_countdown(_make_ctx(
            sleep_wake_estimate=datetime.now() - timedelta(seconds=10), any_is_sleeping=True))

        assert display_width(active[0][0]) == 9
        assert display_width(expired[0][0]) == 9


class TestRenderExpandIcon:
    def test_shows_arrow_in_list_mode_with_focus(self):
        ctx = _make_ctx(is_list_mode=True, has_focus=True)
        result = render_expand_icon(ctx)
        assert result[0][0] == "→ "

    def test_shows_space_in_list_mode_without_focus(self):
        ctx = _make_ctx(is_list_mode=True, has_focus=False)
        result = render_expand_icon(ctx)
        assert result[0][0] == "  "

    def test_shows_expand_icon_in_tree_mode(self):
        ctx = _make_ctx(is_list_mode=False, expand_icon="▼")
        result = render_expand_icon(ctx)
        assert result[0][0] == "▼ "


class TestRenderAgentName:
    def test_returns_display_name(self):
        ctx = _make_ctx(display_name="my-agent         ")
        result = render_agent_name(ctx)
        assert result[0][0] == "my-agent         "


# ===========================================================================
# Time column render tests
# ===========================================================================

class TestRenderUptime:
    def test_returns_uptime_string(self):
        ctx = _make_ctx(uptime="2.5h")
        result = render_uptime(ctx)
        assert "2.5h" in result[0][0]


class TestRenderRunningTime:
    def test_formats_green_time(self):
        ctx = _make_ctx(green_time=3600)
        result = render_running_time(ctx)
        assert "1.0h" in result[0][0]


class TestRenderStalledTime:
    def test_formats_non_green_time(self):
        ctx = _make_ctx(non_green_time=300)
        result = render_stalled_time(ctx)
        assert "5.0m" in result[0][0]


class TestRenderSleepTime:
    def test_zero_sleep_shows_dash(self):
        ctx = _make_ctx(sleep_time=0)
        result = render_sleep_time(ctx)
        text = result[0][0]
        assert "-" in text

    def test_nonzero_sleep_shows_duration(self):
        ctx = _make_ctx(sleep_time=600)
        result = render_sleep_time(ctx)
        text = result[0][0]
        assert "10.0m" in text


class TestRenderActivePct:
    def test_shows_percentage(self):
        ctx = _make_ctx(green_time=3600, non_green_time=1200)
        result = render_active_pct(ctx)
        text = result[0][0]
        assert "75%" in text

    def test_zero_active_time_shows_zero(self):
        ctx = _make_ctx(green_time=0, non_green_time=0)
        result = render_active_pct(ctx)
        assert "0%" in result[0][0]


# ===========================================================================
# Tokens column render tests
# ===========================================================================

class TestRenderTokenCount:
    def test_no_stats_shows_placeholder(self):
        ctx = _make_ctx(claude_stats=None)
        result = render_token_count(ctx)
        assert len(result) == 1
        assert "-" in result[0][0]

    def test_with_stats_shows_token_count(self):
        stats = _make_claude_stats(total_tokens=150000, current_context_tokens=50000)
        ctx = _make_ctx(claude_stats=stats)
        result = render_token_count(ctx)
        assert len(result) == 1
        assert "Σ" in result[0][0]

    def test_hidden_when_show_cost(self):
        stats = _make_claude_stats()
        ctx = _make_ctx(claude_stats=stats, show_cost=True)
        assert render_token_count(ctx) is None


class TestRenderContextUsage:
    def test_no_stats_shows_placeholder(self):
        ctx = _make_ctx(claude_stats=None)
        result = render_context_usage(ctx)
        assert "📚  -%" in result[0][0]

    def test_with_context_shows_pct(self):
        stats = _make_claude_stats(current_context_tokens=50000)
        ctx = _make_ctx(claude_stats=stats)
        result = render_context_usage(ctx)
        assert "📚" in result[0][0]
        assert "%" in result[0][0]

    def test_zero_context_shows_dash(self):
        stats = _make_claude_stats(current_context_tokens=0)
        ctx = _make_ctx(claude_stats=stats)
        result = render_context_usage(ctx)
        assert "📚  -%" in result[0][0]

    def test_visible_when_show_cost(self):
        """Context usage is always visible regardless of show_cost."""
        stats = _make_claude_stats(current_context_tokens=50000)
        ctx = _make_ctx(claude_stats=stats, show_cost=True)
        result = render_context_usage(ctx)
        assert result is not None
        assert "📚" in result[0][0]


class TestRenderCost:
    def test_hidden_when_not_show_cost(self):
        stats = _make_claude_stats()
        ctx = _make_ctx(claude_stats=stats, show_cost=False)
        assert render_cost(ctx) is None

    def test_shows_cost(self):
        stats = _make_claude_stats()
        session = _make_session()
        session.stats.estimated_cost_usd = 12.5
        ctx = _make_ctx(claude_stats=stats, show_cost=True, session=session)
        result = render_cost(ctx)
        assert "$" in result[0][0]

    def test_no_stats_shows_placeholder(self):
        ctx = _make_ctx(claude_stats=None, show_cost=True)
        result = render_cost(ctx)
        assert result is not None
        assert "-" in result[0][0]

    def test_over_budget_uses_red(self):
        """Cost exceeding budget uses bold red style."""
        stats = _make_claude_stats()
        session = _make_session(cost_budget_usd=5.0)
        session.stats.estimated_cost_usd = 6.0
        ctx = _make_ctx(claude_stats=stats, show_cost=True, session=session,
                        any_has_budget=True, monochrome=False)
        result = render_cost(ctx)
        assert "red" in result[0][1]

    def test_near_budget_uses_yellow(self):
        """Cost at 80%+ of budget uses bold yellow style."""
        stats = _make_claude_stats()
        session = _make_session(cost_budget_usd=5.0)
        session.stats.estimated_cost_usd = 4.5  # 90%
        ctx = _make_ctx(claude_stats=stats, show_cost=True, session=session,
                        any_has_budget=True, monochrome=False)
        result = render_cost(ctx)
        assert "yellow" in result[0][1]


class TestRenderBudget:
    def test_shows_budget_value(self):
        session = _make_session(cost_budget_usd=5.0)
        ctx = _make_ctx(show_cost=True, any_has_budget=True, session=session)
        result = render_budget(ctx)
        assert "/" in result[0][0]
        assert "$" in result[0][0]

    def test_returns_none_when_session_has_no_budget(self):
        """Framework placeholder handles alignment when render returns None."""
        session = _make_session(cost_budget_usd=0.0)
        ctx = _make_ctx(show_cost=True, any_has_budget=True, session=session)
        result = render_budget(ctx)
        assert result is None

    def test_visibility_gate_on_column(self):
        """The budget column's visible callback gates on show_cost and any_has_budget."""
        budget_col = next(c for c in SUMMARY_COLUMNS if c.id == "budget")
        assert budget_col.visible is not None
        # Not visible when show_cost=False
        ctx_no_cost = _make_ctx(show_cost=False, any_has_budget=True)
        assert budget_col.visible(ctx_no_cost) is False
        # Not visible when no budgets
        ctx_no_budget = _make_ctx(show_cost=True, any_has_budget=False)
        assert budget_col.visible(ctx_no_budget) is False
        # Visible when both conditions met
        ctx_visible = _make_ctx(show_cost=True, any_has_budget=True)
        assert budget_col.visible(ctx_visible) is True


# ===========================================================================
# Git diff column render tests
# ===========================================================================

class TestRenderGitDiff:
    def test_no_diff_full_detail_shows_placeholder(self):
        ctx = _make_ctx(git_diff_stats=None, summary_detail="full")
        result = render_git_diff(ctx)
        assert "Δ -" in result[0][0]

    def test_no_diff_low_detail_shows_compact_placeholder(self):
        ctx = _make_ctx(git_diff_stats=None, summary_detail="low")
        result = render_git_diff(ctx)
        assert "Δ -" in result[0][0]

    def test_with_diff_full_shows_files_ins_dels(self):
        ctx = _make_ctx(git_diff_stats=(5, 100, 20), summary_detail="full")
        result = render_git_diff(ctx)
        assert len(result) == 3  # files, insertions, deletions
        assert "Δ" in result[0][0]
        assert "+" in result[1][0]
        assert "-" in result[2][0]

    def test_with_diff_low_shows_files_only(self):
        ctx = _make_ctx(git_diff_stats=(3, 50, 10), summary_detail="low")
        result = render_git_diff(ctx)
        assert len(result) == 1
        assert "Δ" in result[0][0]

    def test_full_detail_uses_5_char_width(self):
        """Line counts in full mode use 5 chars for format_line_count."""
        ctx = _make_ctx(git_diff_stats=(5, 1500, 200), summary_detail="full")
        result = render_git_diff(ctx)
        # +1.5K should be in a 5-char field
        assert "+" in result[1][0]


# ===========================================================================
# Performance column render tests
# ===========================================================================

class TestRenderMedianWorkTime:
    def test_nonzero_shows_duration(self):
        ctx = _make_ctx(median_work=120)
        result = render_median_work_time(ctx)
        assert "2.0m" in result[0][0]

    def test_zero_shows_0s(self):
        ctx = _make_ctx(median_work=0)
        result = render_median_work_time(ctx)
        assert "0s" in result[0][0]


# ===========================================================================
# Activity column render tests
# ===========================================================================

class TestRenderSubagentCount:
    def test_zero_uses_dim_style(self):
        ctx = _make_ctx(live_subagent_count=0)
        result = render_subagent_count(ctx)
        assert "dim" in result[0][1]

    def test_nonzero_uses_bold_style(self):
        ctx = _make_ctx(live_subagent_count=3)
        result = render_subagent_count(ctx)
        assert "bold" in result[0][1]
        assert " 3" in result[0][0]


class TestRenderBashCount:
    def test_zero_uses_dim_style(self):
        ctx = _make_ctx(background_bash_count=0)
        result = render_bash_count(ctx)
        assert "dim" in result[0][1]

    def test_nonzero_uses_bold_style(self):
        ctx = _make_ctx(background_bash_count=2)
        result = render_bash_count(ctx)
        assert "bold" in result[0][1]


# ===========================================================================
# Supervision column render tests
# ===========================================================================

class TestRenderPermissionMode:
    def test_returns_perm_emoji(self):
        ctx = _make_ctx(perm_emoji="🔥")
        result = render_permission_mode(ctx)
        assert "🔥" in result[0][0]


class TestToolEmojisHelper:
    def test_empty_string_returns_empty(self):
        assert _tool_emojis("") == ""
        assert _tool_emojis(None) == ""

    def test_known_tools(self):
        assert _tool_emojis("Read") == "📖"
        assert _tool_emojis("Read,Glob") == "📖🔍"

    def test_unknown_tool_uses_fallback(self):
        assert _tool_emojis("Read,CustomTool") == "📖🔹"

    def test_truncation_adds_ellipsis(self):
        tools = ",".join(["Read"] * 12)
        result = _tool_emojis(tools)
        assert result.endswith("…")
        # Should have exactly MAX_TOOL_EMOJI emojis + ellipsis
        assert result == "📖" * 10 + "…"

    def test_whitespace_stripped(self):
        assert _tool_emojis("Read , Glob") == "📖🔍"

    def test_custom_max(self):
        result = _tool_emojis("Read,Glob,Grep", max_n=2)
        assert result == "📖🔍…"


class TestRenderAllowedTools:
    def test_none_returns_none(self):
        session = _make_session(allowed_tools=None)
        ctx = _make_ctx(session=session)
        assert render_allowed_tools(ctx) is None

    def test_empty_returns_none(self):
        session = _make_session(allowed_tools="")
        ctx = _make_ctx(session=session)
        assert render_allowed_tools(ctx) is None

    def test_known_tools(self):
        session = _make_session(allowed_tools="Read,Glob,Grep")
        ctx = _make_ctx(session=session)
        result = render_allowed_tools(ctx)
        assert result is not None
        assert "📖🔍🔎" in result[0][0]

    def test_unknown_fallback(self):
        session = _make_session(allowed_tools="Read,CustomTool")
        ctx = _make_ctx(session=session)
        result = render_allowed_tools(ctx)
        assert "📖🔹" in result[0][0]

    def test_truncation(self):
        tools = ",".join(["Read"] * 12)
        session = _make_session(allowed_tools=tools)
        ctx = _make_ctx(session=session)
        result = render_allowed_tools(ctx)
        assert result[0][0].strip().endswith("…")


class TestRenderToolsPlain:
    def test_none_returns_none(self):
        session = _make_session(allowed_tools=None)
        ctx = _make_ctx(session=session)
        assert render_tools_plain(ctx) is None

    def test_shows_emojis_and_text(self):
        session = _make_session(allowed_tools="Read,Glob")
        ctx = _make_ctx(session=session)
        result = render_tools_plain(ctx)
        assert "📖🔍" in result
        assert "(Read,Glob)" in result


class TestRenderTimeContext:
    def test_enabled_shows_clock(self):
        session = _make_session(time_context_enabled=True)
        ctx = _make_ctx(session=session)
        result = render_time_context(ctx)
        assert "🕐" in result[0][0]

    def test_disabled_shows_dot(self):
        session = _make_session(time_context_enabled=False)
        ctx = _make_ctx(session=session)
        result = render_time_context(ctx)
        assert "·" in result[0][0]


class TestRenderHumanCount:
    def test_no_stats_shows_placeholder(self):
        ctx = _make_ctx(claude_stats=None)
        result = render_human_count(ctx)
        assert "👤  -" in result[0][0]

    def test_with_stats_shows_count(self):
        stats = _make_claude_stats(interaction_count=10)
        ctx = _make_ctx(claude_stats=stats, stats=_make_stats(steers_count=3))
        result = render_human_count(ctx)
        assert "7" in result[0][0]


class TestRenderRobotCount:
    def test_shows_steers_count(self):
        ctx = _make_ctx(stats=_make_stats(steers_count=5))
        result = render_robot_count(ctx)
        assert "5" in result[0][0]


class TestRenderStandingOrders:
    def test_no_instructions_shows_dash(self):
        session = _make_session(standing_instructions=None)
        ctx = _make_ctx(session=session)
        result = render_standing_orders(ctx)
        assert "➖" in result[0][0]

    def test_complete_instructions_show_checkmark(self):
        session = _make_session(standing_instructions="do stuff", standing_orders_complete=True)
        ctx = _make_ctx(session=session)
        result = render_standing_orders(ctx)
        assert "✓" in result[0][0]

    def test_preset_instructions_show_preset_name(self):
        session = _make_session(standing_instructions="do stuff", standing_instructions_preset="mypreset")
        ctx = _make_ctx(session=session)
        result = render_standing_orders(ctx)
        assert "mypreset" in result[0][0]

    def test_custom_instructions_show_clipboard(self):
        session = _make_session(standing_instructions="custom orders")
        ctx = _make_ctx(session=session)
        result = render_standing_orders(ctx)
        assert "📋" in result[0][0]


class TestRenderHeartbeat:
    def test_disabled_shows_placeholder(self):
        session = _make_session(heartbeat_enabled=False)
        ctx = _make_ctx(session=session)
        result = render_heartbeat(ctx)
        assert "💓    -" in result[0][0]

    def test_paused_shows_frequency_dimmed_with_pause_symbol(self):
        session = _make_session(heartbeat_enabled=True, heartbeat_paused=True)
        ctx = _make_ctx(session=session)
        result = render_heartbeat(ctx)
        # Frequency still visible (dimmed) in first segment
        assert "💓" in result[0][0]
        assert "5.0m" in result[0][0]
        # Pause symbol in second segment
        assert "⏸" in result[1][0]

    def test_active_shows_frequency_and_next_time(self):
        now = datetime.now()
        last_hb = (now - timedelta(minutes=2)).isoformat()
        session = _make_session(
            heartbeat_enabled=True,
            heartbeat_paused=False,
            heartbeat_frequency_seconds=300,
            last_heartbeat_time=last_hb,
        )
        ctx = _make_ctx(session=session)
        result = render_heartbeat(ctx)
        assert len(result) == 2
        assert "💓" in result[0][0]
        assert "@" in result[1][0]

    def test_active_no_last_hb_uses_start_time(self):
        session = _make_session(
            heartbeat_enabled=True,
            heartbeat_paused=False,
            heartbeat_frequency_seconds=300,
            last_heartbeat_time=None,
            start_time="2025-01-15T10:00:00",
        )
        ctx = _make_ctx(session=session)
        result = render_heartbeat(ctx)
        assert len(result) == 2
        assert "@" in result[1][0]


# ===========================================================================
# Priority column render tests
# ===========================================================================

class TestRenderAgentValue:
    def test_full_detail_shows_numeric(self):
        session = _make_session(agent_value=1500)
        ctx = _make_ctx(session=session, summary_detail="full")
        result = render_agent_value(ctx)
        assert "1500" in result[0][0]

    def test_low_detail_high_priority(self):
        session = _make_session(agent_value=2000)
        ctx = _make_ctx(session=session, summary_detail="low")
        result = render_agent_value(ctx)
        assert "⏫" in result[0][0]

    def test_low_detail_low_priority(self):
        session = _make_session(agent_value=500)
        ctx = _make_ctx(session=session, summary_detail="low")
        result = render_agent_value(ctx)
        assert "⏬" in result[0][0]

    def test_low_detail_normal_priority(self):
        session = _make_session(agent_value=1000)
        ctx = _make_ctx(session=session, summary_detail="low")
        result = render_agent_value(ctx)
        assert "⏹" in result[0][0]


# ===========================================================================
# Plain render function tests
# ===========================================================================

class TestRenderStatusPlain:
    def test_returns_symbol_and_status_name(self):
        ctx = _make_ctx(status_symbol="🟢")
        result = render_status_plain(ctx)
        assert "🟢" in result
        assert "running" in result

    def test_includes_time_in_state(self):
        state_since = (datetime.now() - timedelta(seconds=90)).isoformat()
        stats = _make_stats(state_since=state_since)
        ctx = _make_ctx(stats=stats)
        result = render_status_plain(ctx)
        assert "(" in result  # has time in parens


class TestRenderUptimePlain:
    def test_returns_uptime(self):
        ctx = _make_ctx(uptime="2.5h")
        assert render_uptime_plain(ctx) == "↑2.5h"


class TestRenderTimePlain:
    def test_includes_active_stalled_pct(self):
        ctx = _make_ctx(green_time=3600, non_green_time=1200, sleep_time=0)
        result = render_time_plain(ctx)
        assert "▶" in result
        assert "⏸" in result
        assert "75%" in result

    def test_includes_sleep_when_nonzero(self):
        ctx = _make_ctx(green_time=3600, non_green_time=1200, sleep_time=600)
        result = render_time_plain(ctx)
        assert "💤" in result


class TestRenderTokenCountPlain:
    def test_no_stats_returns_none(self):
        ctx = _make_ctx(claude_stats=None)
        assert render_token_count_plain(ctx) is None

    def test_with_stats_shows_tokens(self):
        stats = _make_claude_stats(total_tokens=150000, current_context_tokens=50000)
        ctx = _make_ctx(claude_stats=stats)
        result = render_token_count_plain(ctx)
        assert "Σ" in result


class TestRenderContextUsagePlain:
    def test_no_stats_returns_none(self):
        ctx = _make_ctx(claude_stats=None)
        assert render_context_usage_plain(ctx) is None

    def test_with_context_shows_pct(self):
        stats = _make_claude_stats(current_context_tokens=50000)
        ctx = _make_ctx(claude_stats=stats)
        result = render_context_usage_plain(ctx)
        assert "context" in result

    def test_zero_context_returns_none(self):
        stats = _make_claude_stats(current_context_tokens=0)
        ctx = _make_ctx(claude_stats=stats)
        assert render_context_usage_plain(ctx) is None


class TestRenderCostPlain:
    def test_no_stats_returns_none(self):
        ctx = _make_ctx(claude_stats=None)
        assert render_cost_plain(ctx) is None

    def test_shows_cost(self):
        stats = _make_claude_stats()
        session = _make_session()
        session.stats.estimated_cost_usd = 1.5
        ctx = _make_ctx(claude_stats=stats, session=session)
        result = render_cost_plain(ctx)
        assert "$" in result

    def test_with_budget_shows_slash(self):
        stats = _make_claude_stats()
        session = _make_session(cost_budget_usd=5.0)
        session.stats.estimated_cost_usd = 1.5
        ctx = _make_ctx(claude_stats=stats, session=session)
        result = render_cost_plain(ctx)
        assert "/" in result


class TestRenderGitDiffPlain:
    def test_no_diff_returns_none(self):
        ctx = _make_ctx(git_diff_stats=None)
        assert render_git_diff_plain(ctx) is None

    def test_with_diff_shows_files_and_lines(self):
        ctx = _make_ctx(git_diff_stats=(5, 100, 20))
        result = render_git_diff_plain(ctx)
        assert "Δ5" in result
        assert "+100" in result
        assert "-20" in result


class TestRenderWorkPlain:
    def test_no_stats_returns_none(self):
        ctx = _make_ctx(claude_stats=None)
        assert render_work_plain(ctx) is None

    def test_with_stats_shows_median_and_counts(self):
        stats = _make_claude_stats(interaction_count=10, median_work_time=120)
        ctx = _make_ctx(claude_stats=stats, stats=_make_stats(steers_count=3), median_work=120)
        result = render_work_plain(ctx)
        assert "⏱" in result
        assert "👤 7 human" in result
        assert "🤖 3 robot" in result


class TestRenderAgentsPlain:
    def test_shows_counts(self):
        ctx = _make_ctx(live_subagent_count=2, background_bash_count=3)
        result = render_agents_plain(ctx)
        assert "🤿 2" in result
        assert "🐚 3" in result


class TestRenderRepoNamePlain:
    def test_returns_repo_name(self):
        ctx = _make_ctx(repo_name="my-repo")
        assert render_repo_name_plain(ctx) == "my-repo"

    def test_hidden_when_all_names_match(self):
        ctx = _make_ctx(repo_name="my-repo", all_names_match_repos=True)
        assert render_repo_name_plain(ctx) is None


class TestRenderBranchPlain:
    def test_returns_branch(self):
        ctx = _make_ctx(branch="feature/foo")
        assert render_branch_plain(ctx) == "feature/foo"


class TestRenderModePlain:
    def test_normal_mode(self):
        session = _make_session(permissiveness_mode="normal", time_context_enabled=False)
        ctx = _make_ctx(session=session)
        result = render_mode_plain(ctx)
        assert "👮 normal" in result
        assert "disabled" in result

    def test_bypass_with_time_context(self):
        session = _make_session(permissiveness_mode="bypass", time_context_enabled=True)
        ctx = _make_ctx(session=session)
        result = render_mode_plain(ctx)
        assert "🔥 bypass" in result
        assert "🕐 enabled" in result


class TestRenderHeartbeatPlain:
    def test_disabled(self):
        session = _make_session(heartbeat_enabled=False)
        ctx = _make_ctx(session=session)
        assert render_heartbeat_plain(ctx) == "disabled"

    def test_paused(self):
        session = _make_session(heartbeat_enabled=True, heartbeat_paused=True)
        ctx = _make_ctx(session=session)
        result = render_heartbeat_plain(ctx)
        assert "paused" in result

    def test_active_with_next_time(self):
        now = datetime.now()
        last_hb = (now - timedelta(minutes=2)).isoformat()
        session = _make_session(
            heartbeat_enabled=True,
            heartbeat_paused=False,
            heartbeat_frequency_seconds=300,
            last_heartbeat_time=last_hb,
        )
        ctx = _make_ctx(session=session)
        result = render_heartbeat_plain(ctx)
        assert "💓" in result
        assert "@" in result


class TestRenderOrdersPlain:
    def test_no_instructions_returns_none(self):
        session = _make_session(standing_instructions=None)
        ctx = _make_ctx(session=session)
        assert render_orders_plain(ctx) is None

    def test_with_instructions_shows_text(self):
        session = _make_session(standing_instructions="do the thing")
        ctx = _make_ctx(session=session)
        result = render_orders_plain(ctx)
        assert "📋" in result
        assert "do the thing" in result

    def test_complete_shows_checkmark(self):
        session = _make_session(standing_instructions="done", standing_orders_complete=True)
        ctx = _make_ctx(session=session)
        result = render_orders_plain(ctx)
        assert "✓" in result


class TestRenderValuePlain:
    def test_returns_value_string(self):
        session = _make_session(agent_value=1500)
        ctx = _make_ctx(session=session)
        assert render_value_plain(ctx) == "1500"


# ===========================================================================
# build_cli_context tests
# ===========================================================================

class TestBuildCliContext:
    def test_returns_column_context(self):
        session = _make_session()
        stats = session.stats
        ctx = build_cli_context(session, stats, None, None, "running", 0, 0)
        assert isinstance(ctx, ColumnContext)

    def test_monochrome_is_true(self):
        session = _make_session()
        ctx = build_cli_context(session, session.stats, None, None, "running", 0, 0)
        assert ctx.monochrome is True

    def test_passes_budget_flag(self):
        session = _make_session()
        ctx = build_cli_context(session, session.stats, None, None, "running", 0, 0,
                                any_has_budget=True)
        assert ctx.any_has_budget is True

    def test_sets_live_counts(self):
        session = _make_session()
        ctx = build_cli_context(session, session.stats, None, None, "running", 5, 3)
        assert ctx.background_bash_count == 5
        assert ctx.live_subagent_count == 3

    def test_parses_state_since(self):
        session = _make_session()
        state_since = "2025-01-15T12:00:00"
        session.stats.state_since = state_since
        ctx = build_cli_context(session, session.stats, None, None, "running", 0, 0)
        assert ctx.status_changed_at is not None


# ===========================================================================
# render_cli_stats tests
# ===========================================================================

class TestRenderCliStats:
    def test_returns_list_of_tuples(self):
        ctx = _make_ctx()
        result = render_cli_stats(ctx)
        assert isinstance(result, list)
        for label, value in result:
            assert isinstance(label, str)
            assert isinstance(value, str)

    def test_includes_status_label(self):
        ctx = _make_ctx()
        result = render_cli_stats(ctx)
        labels = [label for label, _ in result]
        assert "Status" in labels

    def test_skips_columns_returning_none(self):
        """Columns whose render_plain returns None are excluded."""
        ctx = _make_ctx(claude_stats=None, git_diff_stats=None)
        result = render_cli_stats(ctx)
        labels = [label for label, _ in result]
        # Tokens/Cost return None when no claude_stats
        assert "Tokens" not in labels
        assert "Cost" not in labels
        # Git returns None when no diff stats
        assert "Git" not in labels

    def test_includes_tokens_when_stats_present(self):
        stats = _make_claude_stats()
        session = _make_session()
        session.stats.estimated_cost_usd = 1.0
        ctx = _make_ctx(claude_stats=stats, session=session)
        result = render_cli_stats(ctx)
        labels = [label for label, _ in result]
        assert "Tokens" in labels
        assert "Cost" in labels

    def test_all_expected_labels_present(self):
        """With full data, all expected labels appear."""
        stats = _make_claude_stats()
        session = _make_session(standing_instructions="test orders")
        session.stats.estimated_cost_usd = 1.0
        ctx = _make_ctx(
            claude_stats=stats,
            session=session,
            git_diff_stats=(5, 100, 20),
        )
        result = render_cli_stats(ctx)
        labels = [label for label, _ in result]
        for expected in ["Status", "Repo", "Branch", "Uptime", "Time", "Tokens", "Cost", "Git",
                         "Work", "Agents", "Mode", "Orders", "Heartbeat", "Value"]:
            assert expected in labels, f"Missing label: {expected}"


class TestRenderPrNumber:
    """Tests for PR number column rendering."""

    def test_pr_number_set(self):
        """Should render PR# when pr_number is set on context."""
        ctx = _make_ctx(pr_number=123)
        result = render_pr_number(ctx)
        assert result is not None
        text = result[0][0]
        assert "PR#123" in text

    def test_pr_number_none(self):
        """Should return None when pr_number is not set."""
        ctx = _make_ctx(pr_number=None)
        result = render_pr_number(ctx)
        assert result is None

    def test_pr_number_plain_set(self):
        """Should render plain text PR# when set."""
        ctx = _make_ctx(pr_number=42)
        result = render_pr_number_plain(ctx)
        assert result == "PR#42"

    def test_pr_number_plain_none(self):
        """Should return None when pr_number not set."""
        ctx = _make_ctx(pr_number=None)
        result = render_pr_number_plain(ctx)
        assert result is None
