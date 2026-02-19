"""
Unit tests for config module.
"""

import pytest
import socket
from pathlib import Path

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

    def test_returns_empty_dict_when_yaml_is_scalar(self, tmp_path, monkeypatch):
        """Should return empty dict when YAML root is a scalar string."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("just a string\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.load_config()

        assert result == {}

    def test_returns_empty_dict_when_yaml_is_null(self, tmp_path, monkeypatch):
        """Should return empty dict when YAML file is empty (null)."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.load_config()

        assert result == {}


class TestSaveConfig:
    """Test config saving functionality."""

    def test_saves_config_to_file(self, tmp_path, monkeypatch):
        """Should save dict as YAML to config path."""
        config_file = tmp_path / "config.yaml"
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        config.save_config({"tmux_session": "agents", "hostname": "my-mac"})

        assert config_file.exists()
        content = config_file.read_text()
        assert "tmux_session: agents" in content
        assert "hostname: my-mac" in content

    def test_creates_parent_directories(self, tmp_path, monkeypatch):
        """Should create parent directories if they don't exist."""
        config_file = tmp_path / "subdir" / "config.yaml"
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        config.save_config({"key": "value"})

        assert config_file.exists()

    def test_overwrites_existing_config(self, tmp_path, monkeypatch):
        """Should overwrite existing config file."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("old_key: old_value\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        config.save_config({"new_key": "new_value"})

        content = config_file.read_text()
        assert "new_key: new_value" in content
        assert "old_key" not in content

    def test_roundtrip_save_load(self, tmp_path, monkeypatch):
        """Save then load should return the same config."""
        config_file = tmp_path / "config.yaml"
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        original = {
            "tmux_session": "agents",
            "hostname": "my-mac",
            "relay": {"enabled": True, "url": "http://example.com"},
        }
        config.save_config(original)
        loaded = config.load_config()

        assert loaded == original

    def test_saves_empty_dict(self, tmp_path, monkeypatch):
        """Should handle saving an empty dict."""
        config_file = tmp_path / "config.yaml"
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        config.save_config({})

        assert config_file.exists()
        loaded = config.load_config()
        assert loaded == {}


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

    def test_returns_none_when_relay_not_configured(self, tmp_path, monkeypatch):
        """Should return None when relay key is absent."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("tmux_session: agents\n")
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
        monkeypatch.delenv("OVERCODE_SUMMARIZER_API_KEY_VAR", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "actual-secret-key")

        result = config.get_summarizer_config()

        assert result["api_key"] == "actual-secret-key"

    def test_api_key_none_when_env_var_not_set(self, tmp_path, monkeypatch):
        """Should return None api_key when the env var is not set."""
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nonexistent.yaml")
        monkeypatch.delenv("OVERCODE_SUMMARIZER_API_KEY_VAR", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        result = config.get_summarizer_config()

        assert result["api_key"] is None

    def test_config_file_overrides_env_vars(self, tmp_path, monkeypatch):
        """Config file values should override env var values."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("summarizer:\n  api_url: http://config.api\n  model: config-model\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        monkeypatch.setenv("OVERCODE_SUMMARIZER_API_URL", "http://env.api")
        monkeypatch.setenv("OVERCODE_SUMMARIZER_MODEL", "env-model")

        result = config.get_summarizer_config()

        assert result["api_url"] == "http://config.api"
        assert result["model"] == "config-model"

    def test_custom_api_key_var_resolves(self, tmp_path, monkeypatch):
        """Custom api_key_var should resolve from that env var."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("summarizer:\n  api_key_var: MY_CUSTOM_KEY\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        monkeypatch.delenv("OVERCODE_SUMMARIZER_API_KEY_VAR", raising=False)
        monkeypatch.setenv("MY_CUSTOM_KEY", "custom-key-value")

        result = config.get_summarizer_config()

        assert result["api_key_var"] == "MY_CUSTOM_KEY"
        assert result["api_key"] == "custom-key-value"


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

    def test_returns_default_when_timeline_key_missing(self, tmp_path, monkeypatch):
        """Should return defaults when timeline key exists but hours missing."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("timeline:\n  other_setting: true\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_timeline_config()

        assert result["hours"] == 3.0


class TestGetWebTimePresets:
    """Test web time presets retrieval."""

    def test_returns_defaults_when_no_config(self, tmp_path, monkeypatch):
        """Should return default presets when no config file."""
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nonexistent.yaml")

        result = config.get_web_time_presets()

        assert len(result) > 0
        preset_names = [p["name"] for p in result]
        assert "Morning" in preset_names
        assert "Afternoon" in preset_names
        assert "Full Day" in preset_names
        assert "Evening" in preset_names
        assert "All Time" in preset_names

    def test_default_presets_have_correct_times(self, tmp_path, monkeypatch):
        """Default presets should have correct start/end times."""
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nonexistent.yaml")

        result = config.get_web_time_presets()

        morning = next(p for p in result if p["name"] == "Morning")
        assert morning["start"] == "09:00"
        assert morning["end"] == "12:00"

        all_time = next(p for p in result if p["name"] == "All Time")
        assert all_time["start"] is None
        assert all_time["end"] is None

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

    def test_custom_presets_with_all_time_no_duplicate(self, tmp_path, monkeypatch):
        """If custom presets already include All Time, don't add a duplicate."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
web:
  time_presets:
    - name: "Custom"
      start: "08:00"
      end: "16:00"
    - name: "All Time"
      start: null
      end: null
""")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_web_time_presets()

        all_time_count = sum(1 for p in result if p["name"] == "All Time")
        assert all_time_count == 1

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

    def test_ignores_non_dict_presets(self, tmp_path, monkeypatch):
        """Should ignore preset entries that are not dicts."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
web:
  time_presets:
    - name: "Valid"
      start: "08:00"
      end: "16:00"
    - "just a string"
""")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_web_time_presets()

        names = [p["name"] for p in result]
        assert "Valid" in names
        assert len(result) == 2  # Valid + All Time

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

    def test_returns_defaults_when_presets_not_list(self, tmp_path, monkeypatch):
        """Should return defaults when presets is not a list."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
web:
  time_presets: "not a list"
""")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_web_time_presets()

        preset_names = [p["name"] for p in result]
        assert "Morning" in preset_names

    def test_returns_defaults_when_all_presets_invalid(self, tmp_path, monkeypatch):
        """Should return defaults when all presets are invalid (no name)."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
web:
  time_presets:
    - start: "08:00"
      end: "16:00"
    - start: "10:00"
      end: "14:00"
""")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_web_time_presets()

        # All presets are invalid (no name), should fall back to defaults
        preset_names = [p["name"] for p in result]
        assert "Morning" in preset_names


class TestGetTimeContextConfig:
    """Test time context configuration retrieval."""

    def test_returns_defaults_when_no_config(self, tmp_path, monkeypatch):
        """Should return defaults when no config file."""
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nonexistent.yaml")

        result = config.get_time_context_config()

        assert result["office_start"] == 9
        assert result["office_end"] == 17
        assert result["heartbeat_interval_minutes"] is None

    def test_returns_custom_values(self, tmp_path, monkeypatch):
        """Should return custom values from config."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
time_context:
  office_start: 8
  office_end: 18
  heartbeat_interval_minutes: 15
""")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_time_context_config()

        assert result["office_start"] == 8
        assert result["office_end"] == 18
        assert result["heartbeat_interval_minutes"] == 15

    def test_partial_config_uses_defaults(self, tmp_path, monkeypatch):
        """Should use defaults for unspecified values."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
time_context:
  office_start: 7
""")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_time_context_config()

        assert result["office_start"] == 7
        assert result["office_end"] == 17  # default
        assert result["heartbeat_interval_minutes"] is None  # default

    def test_empty_time_context_uses_defaults(self, tmp_path, monkeypatch):
        """Empty time_context section should return defaults."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("time_context: {}\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_time_context_config()

        assert result["office_start"] == 9
        assert result["office_end"] == 17


class TestGetHostname:
    """Test hostname configuration."""

    def test_returns_system_hostname_when_no_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nonexistent.yaml")
        result = config.get_hostname()
        assert result == socket.gethostname()

    def test_returns_configured_hostname(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("hostname: mac-studio\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        assert config.get_hostname() == "mac-studio"

    def test_returns_system_hostname_when_hostname_is_empty(self, tmp_path, monkeypatch):
        """Empty hostname string should fall back to system hostname."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text('hostname: ""\n')
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_hostname()
        assert result == socket.gethostname()

    def test_returns_system_hostname_when_hostname_is_null(self, tmp_path, monkeypatch):
        """null hostname should fall back to system hostname."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("hostname: null\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_hostname()
        assert result == socket.gethostname()


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

    def test_returns_none_for_empty_api_key(self, tmp_path, monkeypatch):
        """Empty string api_key should return None."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text('web:\n  api_key: ""\n')
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        assert config.get_web_api_key() is None

    def test_returns_none_when_web_section_missing(self, tmp_path, monkeypatch):
        """Should return None when web section doesn't exist at all."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("tmux_session: agents\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        assert config.get_web_api_key() is None


class TestGetWebAllowControl:
    """Test web allow_control configuration."""

    def test_returns_false_when_no_config(self, tmp_path, monkeypatch):
        """Should default to False when no config file."""
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nonexistent.yaml")
        assert config.get_web_allow_control() is False

    def test_returns_false_when_not_set(self, tmp_path, monkeypatch):
        """Should default to False when allow_control not specified."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("web:\n  api_key: secret\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        assert config.get_web_allow_control() is False

    def test_returns_true_when_enabled(self, tmp_path, monkeypatch):
        """Should return True when allow_control is true."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("web:\n  allow_control: true\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        assert config.get_web_allow_control() is True

    def test_returns_false_when_explicitly_disabled(self, tmp_path, monkeypatch):
        """Should return False when allow_control is explicitly false."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("web:\n  allow_control: false\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        assert config.get_web_allow_control() is False

    def test_returns_false_when_web_section_missing(self, tmp_path, monkeypatch):
        """Should return False when web section doesn't exist."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("tmux_session: agents\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        assert config.get_web_allow_control() is False


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

    def test_skips_non_dict_entries(self, tmp_path, monkeypatch):
        """Non-dict entries in sisters list should be skipped."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
sisters:
  - "just a string"
  - 42
  - name: "valid"
    url: "http://localhost:25337"
""")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_sisters_config()
        assert len(result) == 1
        assert result[0]["name"] == "valid"

    def test_api_key_not_included_when_absent(self, tmp_path, monkeypatch):
        """api_key key should not be in result dict when not configured."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
sisters:
  - name: "host"
    url: "http://localhost:8080"
""")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_sisters_config()
        assert "api_key" not in result[0]

    def test_empty_sisters_list(self, tmp_path, monkeypatch):
        """Empty sisters list should return empty list."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("sisters: []\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_sisters_config()
        assert result == []
