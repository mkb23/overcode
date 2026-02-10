"""Tests for summary_groups module."""

import pytest

from overcode.summary_groups import (
    SUMMARY_GROUPS,
    SUMMARY_GROUPS_BY_ID,
    PRESETS,
    SummaryGroup,
    get_default_group_visibility,
    get_toggleable_groups,
)


class TestSummaryGroups:
    """Tests for summary group definitions."""

    def test_summary_groups_structure(self):
        """Test that SUMMARY_GROUPS has expected structure."""
        assert len(SUMMARY_GROUPS) == 9  # identity, time, llm_usage, context, git, supervision, priority, performance, subprocesses

        # All groups should have required fields
        for group in SUMMARY_GROUPS:
            assert isinstance(group, SummaryGroup)
            assert group.id
            assert group.name
            assert isinstance(group.fields, list)
            assert len(group.fields) > 0

    def test_identity_group_always_visible(self):
        """Test that identity group is always visible."""
        identity = SUMMARY_GROUPS_BY_ID["identity"]
        assert identity.always_visible is True

    def test_other_groups_toggleable(self):
        """Test that non-identity groups are toggleable."""
        toggleable = get_toggleable_groups()
        assert len(toggleable) == 8  # All except identity

        for group in toggleable:
            assert group.always_visible is False
            assert group.id != "identity"

    def test_presets_have_all_toggleable_groups(self):
        """Test that presets define visibility for all toggleable groups."""
        toggleable_ids = {g.id for g in get_toggleable_groups()}

        for preset_name, preset_config in PRESETS.items():
            preset_ids = set(preset_config.keys())
            assert preset_ids == toggleable_ids, f"Preset '{preset_name}' missing groups"

    def test_minimal_preset_values(self):
        """Test minimal preset configuration."""
        minimal = PRESETS["minimal"]
        assert minimal["time"] is False
        assert minimal["llm_usage"] is True
        assert minimal["context"] is True
        assert minimal["git"] is False
        assert minimal["supervision"] is False
        assert minimal["priority"] is False
        assert minimal["performance"] is False
        assert minimal["subprocesses"] is False

    def test_full_preset_enables_all(self):
        """Test full preset enables all groups."""
        full = PRESETS["full"]
        for group_id, enabled in full.items():
            assert enabled is True, f"Full preset should enable '{group_id}'"

    def test_get_default_group_visibility(self):
        """Test default visibility configuration."""
        defaults = get_default_group_visibility()

        # Should not include identity (always visible)
        assert "identity" not in defaults

        # Should include all toggleable groups
        assert len(defaults) == 8

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
