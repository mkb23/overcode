"""
Tests for standing_instructions module.
"""

import json
import pytest
from unittest.mock import patch

from overcode.standing_instructions import InstructionPreset, DEFAULT_PRESETS, load_presets, get_preset, resolve_instructions


class TestInstructionPreset:
    """Tests for InstructionPreset dataclass."""

    def test_create_preset(self):
        preset = InstructionPreset(
            name="TEST",
            description="Test description",
            instructions="Test instructions"
        )
        assert preset.name == "TEST"
        assert preset.description == "Test description"
        assert preset.instructions == "Test instructions"


class TestDefaultPresets:
    """Tests for default preset definitions."""

    def test_default_presets_exist(self):
        assert "DO_NOTHING" in DEFAULT_PRESETS
        assert "STANDARD" in DEFAULT_PRESETS
        assert "PERMISSIVE" in DEFAULT_PRESETS
        assert "CAUTIOUS" in DEFAULT_PRESETS
        assert "RESEARCH" in DEFAULT_PRESETS
        assert "CODING" in DEFAULT_PRESETS
        assert "TESTING" in DEFAULT_PRESETS
        assert "REVIEW" in DEFAULT_PRESETS
        assert "DEPLOY" in DEFAULT_PRESETS
        assert "AUTONOMOUS" in DEFAULT_PRESETS
        assert "MINIMAL" in DEFAULT_PRESETS

    def test_default_presets_count(self):
        assert len(DEFAULT_PRESETS) == 11

    def test_all_presets_have_required_fields(self):
        for name, preset in DEFAULT_PRESETS.items():
            assert preset.name == name
            assert preset.description, f"Preset {name} missing description"
            assert preset.instructions, f"Preset {name} missing instructions"
            assert len(preset.description) > 10, f"Preset {name} description too short"
            assert len(preset.instructions) > 50, f"Preset {name} instructions too short"


class TestResolveInstructions:
    """Tests for resolve_instructions function."""

    def test_resolve_known_preset(self):
        instructions, preset_name = resolve_instructions("STANDARD")
        assert preset_name == "STANDARD"
        assert instructions == DEFAULT_PRESETS["STANDARD"].instructions

    def test_resolve_preset_case_insensitive(self):
        instructions, preset_name = resolve_instructions("coding")
        assert preset_name == "CODING"
        assert instructions == DEFAULT_PRESETS["CODING"].instructions

    def test_resolve_preset_mixed_case(self):
        instructions, preset_name = resolve_instructions("CoDiNg")
        assert preset_name == "CODING"

    def test_resolve_custom_instructions(self):
        custom = "Focus on fixing the login bug"
        instructions, preset_name = resolve_instructions(custom)
        assert preset_name is None
        assert instructions == custom

    def test_resolve_empty_string(self):
        instructions, preset_name = resolve_instructions("")
        assert preset_name is None
        assert instructions == ""


class TestGetPreset:
    """Tests for get_preset function."""

    def test_get_existing_preset(self):
        preset = get_preset("DO_NOTHING")
        assert preset is not None
        assert preset.name == "DO_NOTHING"

    def test_get_preset_case_insensitive(self):
        preset = get_preset("do_nothing")
        assert preset is not None
        assert preset.name == "DO_NOTHING"

    def test_get_nonexistent_preset(self):
        preset = get_preset("NONEXISTENT")
        assert preset is None


class TestLoadAndSavePresets:
    """Tests for load_presets and save_presets with temp directory."""

    @pytest.fixture
    def temp_presets_path(self, tmp_path):
        """Create a temp presets path and patch PRESETS_PATH."""
        temp_file = tmp_path / "presets.json"
        with patch("overcode.standing_instructions.PRESETS_PATH", temp_file):
            yield temp_file

    def test_load_creates_default_file(self, temp_presets_path):
        """Loading when file doesn't exist creates it with defaults."""
        assert not temp_presets_path.exists()

        with patch("overcode.standing_instructions.PRESETS_PATH", temp_presets_path):
            presets = load_presets()

        assert temp_presets_path.exists()
        assert "DO_NOTHING" in presets
        assert len(presets) == 11

    def test_load_reads_existing_file(self, temp_presets_path):
        """Loading reads from existing file."""
        custom_presets = {
            "CUSTOM": {
                "name": "CUSTOM",
                "description": "Custom preset",
                "instructions": "Custom instructions"
            }
        }
        temp_presets_path.parent.mkdir(parents=True, exist_ok=True)
        with open(temp_presets_path, 'w') as f:
            json.dump(custom_presets, f)

        with patch("overcode.standing_instructions.PRESETS_PATH", temp_presets_path):
            presets = load_presets()

        assert "CUSTOM" in presets
        assert presets["CUSTOM"].description == "Custom preset"


class TestPresetInstructionsContent:
    """Tests to verify preset instruction content quality."""

    def test_do_nothing_tells_supervisor_to_ignore(self):
        preset = DEFAULT_PRESETS["DO_NOTHING"]
        assert "not" in preset.instructions.lower()
        assert "alone" in preset.instructions.lower() or "ignore" in preset.instructions.lower()

    def test_standard_mentions_approve_and_reject(self):
        preset = DEFAULT_PRESETS["STANDARD"]
        assert "approve" in preset.instructions.lower()
        assert "reject" in preset.instructions.lower()

    def test_permissive_is_less_restrictive(self):
        permissive = DEFAULT_PRESETS["PERMISSIVE"]
        # PERMISSIVE should mention trusting the agent
        assert "trust" in permissive.instructions.lower()

    def test_cautious_mentions_conservative(self):
        preset = DEFAULT_PRESETS["CAUTIOUS"]
        assert "conservative" in preset.instructions.lower()

    def test_research_focuses_on_reads(self):
        preset = DEFAULT_PRESETS["RESEARCH"]
        assert "read" in preset.instructions.lower()

    def test_coding_mentions_tests(self):
        preset = DEFAULT_PRESETS["CODING"]
        assert "test" in preset.instructions.lower()

    def test_testing_mentions_pytest_or_jest(self):
        preset = DEFAULT_PRESETS["TESTING"]
        assert "pytest" in preset.instructions.lower() or "jest" in preset.instructions.lower()

    def test_review_is_read_only(self):
        preset = DEFAULT_PRESETS["REVIEW"]
        assert "read" in preset.instructions.lower()
        assert "reject" in preset.instructions.lower()

    def test_deploy_mentions_push(self):
        preset = DEFAULT_PRESETS["DEPLOY"]
        assert "push" in preset.instructions.lower()

    def test_autonomous_minimizes_interruption(self):
        preset = DEFAULT_PRESETS["AUTONOMOUS"]
        assert "minimal" in preset.instructions.lower() or "interruption" in preset.instructions.lower()

    def test_minimal_is_hands_off(self):
        preset = DEFAULT_PRESETS["MINIMAL"]
        assert "permission" in preset.instructions.lower()
