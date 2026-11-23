"""Tests for summary_groups module."""


from overcode.summary_groups import SUMMARY_GROUPS, SUMMARY_GROUPS_BY_ID, SummaryGroup


class TestSummaryGroups:
    """Tests for summary group definitions."""

    def test_summary_groups_structure(self):
        """Test that SUMMARY_GROUPS has expected structure."""
        # identity, sisters, git, time, llm_usage, context, performance,
        # subprocesses, supervision, priority
        assert len(SUMMARY_GROUPS) == 10

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

    def test_only_a_few_columns_are_locked(self):
        """The light, the alert, ST, DTL, the fold marker and the name are
        always shown; every other column, identity group or not, switches."""
        from overcode.summary_columns import SUMMARY_COLUMNS
        assert {c.id for c in SUMMARY_COLUMNS if c.locked} == {
            "status_symbol", "unvisited_alert", "time_in_state", "sleep_countdown",
            "expand_icon", "agent_name"}


class TestSummaryGroupsById:
    """Tests for SUMMARY_GROUPS_BY_ID lookup."""

    def test_lookup_by_id(self):
        """Test looking up groups by ID."""
        for group in SUMMARY_GROUPS:
            assert SUMMARY_GROUPS_BY_ID[group.id] is group

    def test_all_groups_in_lookup(self):
        """Test all groups are in the lookup dict."""
        assert len(SUMMARY_GROUPS_BY_ID) == len(SUMMARY_GROUPS)
