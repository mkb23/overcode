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


class TestGetRelayConfig:
    """Test relay configuration retrieval."""

    def test_returns_none_when_no_config(self, tmp_path, monkeypatch):
        """Should return None when no config file."""
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nonexistent.yaml")
        result = config.get_relay_config()
        assert result is None

    def test_returns_none_when_relay_disabled(self, tmp_path, monkeypatch):
        """Should return None when relay is disabled."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("relay:\n  enabled: false\n  url: http://example.com\n  api_key: secret\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_relay_config()
        assert result is None

    def test_returns_none_when_url_missing(self, tmp_path, monkeypatch):
        """Should return None when URL is missing."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("relay:\n  enabled: true\n  api_key: secret\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_relay_config()
        assert result is None

    def test_returns_none_when_api_key_missing(self, tmp_path, monkeypatch):
        """Should return None when API key is missing."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("relay:\n  enabled: true\n  url: http://example.com\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_relay_config()
        assert result is None

    def test_returns_config_when_enabled(self, tmp_path, monkeypatch):
        """Should return config when relay is properly configured."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("relay:\n  enabled: true\n  url: http://example.com\n  api_key: secret\n  interval: 60\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_relay_config()

        assert result is not None
        assert result["url"] == "http://example.com"
        assert result["api_key"] == "secret"
        assert result["interval"] == 60

    def test_uses_default_interval(self, tmp_path, monkeypatch):
        """Should use default interval when not specified."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("relay:\n  enabled: true\n  url: http://example.com\n  api_key: secret\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_relay_config()

        assert result["interval"] == 30  # Default


class TestGetSummarizerConfig:
    """Test summarizer configuration retrieval."""

    def test_returns_defaults_when_no_config(self, tmp_path, monkeypatch):
        """Should return defaults when no config file."""
        import os
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nonexistent.yaml")
        # Clear env vars
        monkeypatch.delenv("OVERCODE_SUMMARIZER_API_URL", raising=False)
        monkeypatch.delenv("OVERCODE_SUMMARIZER_MODEL", raising=False)
        monkeypatch.delenv("OVERCODE_SUMMARIZER_API_KEY_VAR", raising=False)

        result = config.get_summarizer_config()

        assert result["api_url"] == "https://api.openai.com/v1/chat/completions"
        assert result["model"] == "gpt-4o-mini"
        assert result["api_key_var"] == "OPENAI_API_KEY"

    def test_config_file_takes_precedence(self, tmp_path, monkeypatch):
        """Should use config file values over defaults."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("summarizer:\n  api_url: http://custom.api\n  model: gpt-4\n  api_key_var: MY_KEY\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        # Clear env vars
        monkeypatch.delenv("OVERCODE_SUMMARIZER_API_URL", raising=False)
        monkeypatch.delenv("OVERCODE_SUMMARIZER_MODEL", raising=False)

        result = config.get_summarizer_config()

        assert result["api_url"] == "http://custom.api"
        assert result["model"] == "gpt-4"
        assert result["api_key_var"] == "MY_KEY"

    def test_env_vars_as_fallback(self, tmp_path, monkeypatch):
        """Should use env vars when config not specified."""
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nonexistent.yaml")
        monkeypatch.setenv("OVERCODE_SUMMARIZER_API_URL", "http://env.api")
        monkeypatch.setenv("OVERCODE_SUMMARIZER_MODEL", "gpt-3.5-turbo")
        monkeypatch.setenv("OVERCODE_SUMMARIZER_API_KEY_VAR", "ENV_KEY")

        result = config.get_summarizer_config()

        assert result["api_url"] == "http://env.api"
        assert result["model"] == "gpt-3.5-turbo"
        assert result["api_key_var"] == "ENV_KEY"

    def test_resolves_api_key_from_env_var(self, tmp_path, monkeypatch):
        """Should resolve actual API key from configured env var."""
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nonexistent.yaml")
        monkeypatch.setenv("OPENAI_API_KEY", "actual-secret-key")

        result = config.get_summarizer_config()

        assert result["api_key"] == "actual-secret-key"


class TestGetTimelineConfig:
    """Test timeline configuration retrieval."""

    def test_returns_defaults_when_no_config(self, tmp_path, monkeypatch):
        """Should return defaults when no config file."""
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nonexistent.yaml")

        result = config.get_timeline_config()

        assert result["hours"] == 3.0

    def test_returns_custom_hours(self, tmp_path, monkeypatch):
        """Should return custom hours from config."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("timeline:\n  hours: 12.0\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_timeline_config()

        assert result["hours"] == 12.0


class TestGetWebTimePresets:
    """Test web time presets retrieval."""

    def test_returns_defaults_when_no_config(self, tmp_path, monkeypatch):
        """Should return default presets when no config file."""
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nonexistent.yaml")

        result = config.get_web_time_presets()

        assert len(result) > 0
        preset_names = [p["name"] for p in result]
        assert "Morning" in preset_names
        assert "All Time" in preset_names

    def test_returns_custom_presets(self, tmp_path, monkeypatch):
        """Should return custom presets from config."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
web:
  time_presets:
    - name: "Custom"
      start: "08:00"
      end: "16:00"
""")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_web_time_presets()

        assert any(p["name"] == "Custom" for p in result)
        # Should add "All Time" automatically
        assert any(p["name"] == "All Time" for p in result)

    def test_ignores_invalid_presets(self, tmp_path, monkeypatch):
        """Should ignore presets missing required fields."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
web:
  time_presets:
    - name: "Valid"
      start: "08:00"
      end: "16:00"
    - start: "00:00"
      end: "01:00"
""")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_web_time_presets()

        # Should only have Valid and All Time
        names = [p["name"] for p in result]
        assert "Valid" in names
        assert "" not in names  # Invalid preset should be filtered

    def test_returns_defaults_for_empty_presets(self, tmp_path, monkeypatch):
        """Should return defaults when presets list is empty or invalid."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
web:
  time_presets: []
""")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_web_time_presets()

        # Should return defaults
        preset_names = [p["name"] for p in result]
        assert "Morning" in preset_names


class TestGetHostname:
    """Test hostname configuration."""

    def test_returns_system_hostname_when_no_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nonexistent.yaml")
        result = config.get_hostname()
        import socket
        assert result == socket.gethostname()

    def test_returns_configured_hostname(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("hostname: mac-studio\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        assert config.get_hostname() == "mac-studio"


class TestGetWebApiKey:
    """Test web API key configuration."""

    def test_returns_none_when_no_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nonexistent.yaml")
        assert config.get_web_api_key() is None

    def test_returns_none_when_key_not_set(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("web:\n  time_presets: []\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        assert config.get_web_api_key() is None

    def test_returns_api_key(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text('web:\n  api_key: "my-secret"\n')
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        assert config.get_web_api_key() == "my-secret"


class TestGetSistersConfig:
    """Test sisters configuration."""

    def test_returns_empty_list_when_no_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nonexistent.yaml")
        assert config.get_sisters_config() == []

    def test_returns_empty_when_sisters_not_list(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("sisters: not-a-list\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        assert config.get_sisters_config() == []

    def test_returns_valid_sisters(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
sisters:
  - name: "macbook"
    url: "http://localhost:15337"
  - name: "desktop"
    url: "http://localhost:25337/"
    api_key: "secret"
""")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_sisters_config()

        assert len(result) == 2
        assert result[0] == {"name": "macbook", "url": "http://localhost:15337"}
        assert result[1] == {"name": "desktop", "url": "http://localhost:25337", "api_key": "secret"}

    def test_skips_entries_without_name(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
sisters:
  - url: "http://localhost:15337"
  - name: "valid"
    url: "http://localhost:25337"
""")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_sisters_config()
        assert len(result) == 1
        assert result[0]["name"] == "valid"

    def test_skips_entries_without_url(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
sisters:
  - name: "no-url"
  - name: "valid"
    url: "http://localhost:25337"
""")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_sisters_config()
        assert len(result) == 1
        assert result[0]["name"] == "valid"

    def test_strips_trailing_slash_from_url(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
sisters:
  - name: "host"
    url: "http://localhost:8080/"
""")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_sisters_config()
        assert result[0]["url"] == "http://localhost:8080"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
