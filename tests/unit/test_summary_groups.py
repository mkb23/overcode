"""Tests for summary_groups module."""

import pytest

from overcode.summary_groups import (
    SUMMARY_GROUPS,
    SUMMARY_GROUPS_BY_ID,
    SummaryGroup,
    get_default_group_visibility,
    get_toggleable_groups,
    get_default_columns_for_level,
)


class TestSummaryGroups:
    """Tests for summary group definitions."""

    def test_summary_groups_structure(self):
        """Test that SUMMARY_GROUPS has expected structure."""
        # identity, sisters, git, time, llm_usage, context, subprocesses,
        # supervision, priority — performance folded into time
        assert len(SUMMARY_GROUPS) == 9

        for group in SUMMARY_GROUPS:
            assert isinstance(group, SummaryGroup)
            assert group.id
            assert group.name

    def test_group_order_matches_render_order(self):
        """Configurator group order should match first-appearance in SUMMARY_COLUMNS."""
        from overcode.summary_columns import SUMMARY_COLUMNS
        render_order: list[str] = []
        for col in SUMMARY_COLUMNS:
            if col.group not in render_order:
                render_order.append(col.group)
        configurator_order = [g.id for g in SUMMARY_GROUPS]
        assert configurator_order == render_order

    def test_every_column_group_is_defined(self):
        """Every SummaryColumn.group must resolve to a SummaryGroup."""
        from overcode.summary_columns import SUMMARY_COLUMNS
        for col in SUMMARY_COLUMNS:
            assert col.group in SUMMARY_GROUPS_BY_ID, (
                f"Column {col.id} references unknown group {col.group!r}"
            )

    def test_identity_group_always_visible(self):
        """Test that identity group is always visible."""
        identity = SUMMARY_GROUPS_BY_ID["identity"]
        assert identity.always_visible is True

    def test_other_groups_toggleable(self):
        """Test that non-identity groups are toggleable."""
        toggleable = get_toggleable_groups()
        assert len(toggleable) == len(SUMMARY_GROUPS) - 1  # All except identity

        for group in toggleable:
            assert group.always_visible is False
            assert group.id != "identity"

    def test_get_default_group_visibility(self):
        """Test default visibility configuration."""
        defaults = get_default_group_visibility()

        # Should not include identity (always visible)
        assert "identity" not in defaults

        # Should include all toggleable groups
        assert len(defaults) == len(SUMMARY_GROUPS) - 1

        # All should be enabled by default
        for group_id, enabled in defaults.items():
            assert enabled is True


class TestSummaryGroupsById:
    """Tests for SUMMARY_GROUPS_BY_ID lookup."""

    def test_lookup_by_id(self):
        """Test looking up groups by ID."""
        for group in SUMMARY_GROUPS:
            assert SUMMARY_GROUPS_BY_ID[group.id] is group

    def test_all_groups_in_lookup(self):
        """Test all groups are in the lookup dict."""
        assert len(SUMMARY_GROUPS_BY_ID) == len(SUMMARY_GROUPS)


class TestGetDefaultColumnsForLevel:
    """Tests for get_default_columns_for_level helper."""

    def test_low_level_basics(self):
        """Test that low level has identity columns visible."""
        cols = get_default_columns_for_level("low")
        assert cols["status_symbol"] is True
        assert cols["agent_name"] is True
        # med+ columns should be off
        assert cols["uptime"] is False

    def test_med_level_includes_time(self):
        """Test that med level includes time columns."""
        cols = get_default_columns_for_level("med")
        assert cols["uptime"] is True
        assert cols["running_time"] is True
        # high+ columns should be off
        assert cols["active_pct"] is False

    def test_high_level_includes_subprocess(self):
        """Test that high level includes subprocess columns."""
        cols = get_default_columns_for_level("high")
        assert cols["subagent_count"] is True
        assert cols["active_pct"] is True

    def test_full_level_shows_all_real_columns(self):
        """Test that full level shows all columns with non-empty detail_levels."""
        from overcode.summary_columns import SUMMARY_COLUMNS
        cols = get_default_columns_for_level("full")
        for col in SUMMARY_COLUMNS:
            if col.detail_levels:  # Skip synthetic CLI-only columns with empty set
                assert cols[col.id] is True, f"Column {col.id} should be visible at full"
