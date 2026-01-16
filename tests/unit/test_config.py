"""
Unit tests for config module.
"""

import pytest
import sys
from pathlib import Path
import tempfile

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode import config


class TestLoadConfig:
    """Test config loading functionality."""

    def test_returns_empty_dict_when_no_file(self, tmp_path, monkeypatch):
        """Should return empty dict when config file doesn't exist."""
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nonexistent.yaml")
        result = config.load_config()
        assert result == {}

    def test_loads_valid_yaml(self, tmp_path, monkeypatch):
        """Should load valid YAML config."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("default_standing_instructions: 'Approve file writes'\ntmux_session: agents\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.load_config()

        assert result["default_standing_instructions"] == "Approve file writes"
        assert result["tmux_session"] == "agents"

    def test_returns_empty_dict_on_invalid_yaml(self, tmp_path, monkeypatch):
        """Should return empty dict when YAML is invalid."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("invalid: yaml: content: [")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.load_config()

        assert result == {}

    def test_returns_empty_dict_when_yaml_is_not_dict(self, tmp_path, monkeypatch):
        """Should return empty dict when YAML root is not a dict."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("- item1\n- item2\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.load_config()

        assert result == {}


class TestGetDefaultStandingInstructions:
    """Test default standing instructions retrieval."""

    def test_returns_empty_string_when_no_config(self, tmp_path, monkeypatch):
        """Should return empty string when no config file."""
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nonexistent.yaml")
        result = config.get_default_standing_instructions()
        assert result == ""

    def test_returns_empty_string_when_key_missing(self, tmp_path, monkeypatch):
        """Should return empty string when key not in config."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("tmux_session: agents\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_default_standing_instructions()

        assert result == ""

    def test_returns_configured_instructions(self, tmp_path, monkeypatch):
        """Should return instructions from config."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("default_standing_instructions: 'Approve file read/write permissions'\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_default_standing_instructions()

        assert result == "Approve file read/write permissions"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
