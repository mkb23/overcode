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

    def test_identity_group_always_visible(self):
        """Test that identity group is always visible."""
        identity = SUMMARY_GROUPS_BY_ID["identity"]
        assert identity.always_visible is True


class TestSummaryGroupsById:
    """Tests for SUMMARY_GROUPS_BY_ID lookup."""

    def test_lookup_by_id(self):
        """Test looking up groups by ID."""
        for group in SUMMARY_GROUPS:
            assert SUMMARY_GROUPS_BY_ID[group.id] is group

    def test_all_groups_in_lookup(self):
        """Test all groups are in the lookup dict."""
        assert len(SUMMARY_GROUPS_BY_ID) == len(SUMMARY_GROUPS)
