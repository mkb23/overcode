"""
Tests for standing_instructions module.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from overcode.standing_instructions import (
    InstructionPreset,
    DEFAULT_PRESETS,
    load_presets,
    save_presets,
    get_preset,
    get_preset_names,
    resolve_instructions,
    add_preset,
    remove_preset,
    reset_presets,
    PRESETS_PATH,
)


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


class TestGetPresetNames:
    """Tests for get_preset_names function."""

    def test_do_nothing_first(self):
        names = get_preset_names()
        assert names[0] == "DO_NOTHING"

    def test_rest_alphabetical(self):
        names = get_preset_names()
        rest = names[1:]
        assert rest == sorted(rest)

    def test_all_presets_included(self):
        names = get_preset_names()
        for name in DEFAULT_PRESETS.keys():
            assert name in names


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

    def test_save_writes_file(self, temp_presets_path):
        """Save writes presets to file."""
        presets = {
            "TEST": InstructionPreset(
                name="TEST",
                description="Test desc",
                instructions="Test instructions"
            )
        }
        temp_presets_path.parent.mkdir(parents=True, exist_ok=True)

        with patch("overcode.standing_instructions.PRESETS_PATH", temp_presets_path):
            save_presets(presets)

        assert temp_presets_path.exists()
        with open(temp_presets_path) as f:
            data = json.load(f)
        assert "TEST" in data


class TestAddAndRemovePreset:
    """Tests for add_preset and remove_preset."""

    @pytest.fixture
    def temp_presets_path(self, tmp_path):
        """Create temp presets path."""
        temp_file = tmp_path / "presets.json"
        temp_file.parent.mkdir(parents=True, exist_ok=True)
        # Initialize with defaults
        with open(temp_file, 'w') as f:
            json.dump({
                name: {
                    "name": p.name,
                    "description": p.description,
                    "instructions": p.instructions
                }
                for name, p in DEFAULT_PRESETS.items()
            }, f)
        with patch("overcode.standing_instructions.PRESETS_PATH", temp_file):
            yield temp_file

    def test_add_new_preset(self, temp_presets_path):
        """Adding a new preset saves it."""
        with patch("overcode.standing_instructions.PRESETS_PATH", temp_presets_path):
            add_preset("MYPRESET", "My custom preset", "Do custom things")
            preset = get_preset("MYPRESET")

        assert preset is not None
        assert preset.name == "MYPRESET"
        assert preset.description == "My custom preset"
        assert preset.instructions == "Do custom things"

    def test_add_preset_uppercases_name(self, temp_presets_path):
        """Preset names are uppercased."""
        with patch("overcode.standing_instructions.PRESETS_PATH", temp_presets_path):
            add_preset("lowercase", "Description", "Instructions")
            preset = get_preset("LOWERCASE")

        assert preset is not None
        assert preset.name == "LOWERCASE"

    def test_remove_existing_preset(self, temp_presets_path):
        """Removing an existing preset returns True."""
        with patch("overcode.standing_instructions.PRESETS_PATH", temp_presets_path):
            # First add a preset to remove
            add_preset("TOREMOVE", "To remove", "Will be removed")
            result = remove_preset("TOREMOVE")
            preset = get_preset("TOREMOVE")

        assert result is True
        assert preset is None

    def test_remove_nonexistent_preset(self, temp_presets_path):
        """Removing nonexistent preset returns False."""
        with patch("overcode.standing_instructions.PRESETS_PATH", temp_presets_path):
            result = remove_preset("DOESNOTEXIST")

        assert result is False


class TestResetPresets:
    """Tests for reset_presets function."""

    @pytest.fixture
    def temp_presets_path(self, tmp_path):
        """Create temp presets path with custom content."""
        temp_file = tmp_path / "presets.json"
        temp_file.parent.mkdir(parents=True, exist_ok=True)
        # Write custom presets
        with open(temp_file, 'w') as f:
            json.dump({
                "CUSTOM": {
                    "name": "CUSTOM",
                    "description": "Custom",
                    "instructions": "Custom"
                }
            }, f)
        with patch("overcode.standing_instructions.PRESETS_PATH", temp_file):
            yield temp_file

    def test_reset_restores_defaults(self, temp_presets_path):
        """Reset restores default presets."""
        with patch("overcode.standing_instructions.PRESETS_PATH", temp_presets_path):
            # Verify custom preset exists
            presets = load_presets()
            assert "CUSTOM" in presets
            assert "DO_NOTHING" not in presets

            # Reset
            reset_presets()

            # Verify defaults restored
            presets = load_presets()
            assert "DO_NOTHING" in presets
            assert "CUSTOM" not in presets
            assert len(presets) == 11


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
        standard = DEFAULT_PRESETS["STANDARD"]
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
