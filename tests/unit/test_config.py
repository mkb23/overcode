"""
Unit tests for config module.
"""

import pytest
import socket

from overcode import config


@pytest.fixture(autouse=True)
def _clear_config_cache():
    """Clear config cache before each test to prevent cross-test contamination."""
    config._clear_config_cache()
    yield
    config._clear_config_cache()


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
            "sisters": [{"name": "mac", "url": "http://example.com"}],
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


class TestGetTimeContextConfig:
    """Test time context configuration retrieval."""

    def test_returns_defaults_when_no_config(self, tmp_path, monkeypatch):
        """Should return defaults when no config file."""
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nonexistent.yaml")

        result = config.get_enhanced_context_config()

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

        result = config.get_enhanced_context_config()

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

        result = config.get_enhanced_context_config()

        assert result["office_start"] == 7
        assert result["office_end"] == 17  # default
        assert result["heartbeat_interval_minutes"] is None  # default

    def test_empty_enhanced_context_uses_defaults(self, tmp_path, monkeypatch):
        """Empty time_context section should return defaults."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("time_context: {}\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_enhanced_context_config()

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
        config_file.write_text("web:\n  port: 8080\n")
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


class TestGetSyncBranch:
    """Tests for the configurable sync-to-main branch (#447)."""

    def test_defaults_to_main_when_unset(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nonexistent.yaml")
        assert config.get_sync_branch() == "main"

    def test_uses_configured_branch(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("sync:\n  branch: master\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        assert config.get_sync_branch() == "master"

    def test_empty_string_falls_back_to_main(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text('sync:\n  branch: ""\n')
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        assert config.get_sync_branch() == "main"

    def test_whitespace_stripped(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text('sync:\n  branch: "  develop  "\n')
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        assert config.get_sync_branch() == "develop"


class TestGetHistoryRetentionConfig:
    """Tests for history_retention config (#465, #468)."""

    def test_returns_defaults_when_no_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nonexistent.yaml")

        result = config.get_history_retention_config()

        assert result == {
            "status_history_max_days": 540.0,
            "status_history_rotate_mb": 50.0,
            "event_loop_timing_cap_mb": 100.0,
            "event_loop_timing_enabled": True,
        }

    def test_returns_custom_values(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "history_retention:\n"
            "  status_history_max_days: 30\n"
            "  status_history_rotate_mb: 10\n"
            "  event_loop_timing_cap_mb: 25\n"
            "  event_loop_timing_enabled: false\n"
        )
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_history_retention_config()

        assert result["status_history_max_days"] == 30.0
        assert result["status_history_rotate_mb"] == 10.0
        assert result["event_loop_timing_cap_mb"] == 25.0
        assert result["event_loop_timing_enabled"] is False

    def test_partial_override_keeps_other_defaults(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("history_retention:\n  status_history_rotate_mb: 5\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_history_retention_config()

        assert result["status_history_rotate_mb"] == 5.0
        assert result["status_history_max_days"] == 540.0
        assert result["event_loop_timing_cap_mb"] == 100.0
        assert result["event_loop_timing_enabled"] is True

    def test_non_dict_value_falls_back_to_defaults(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("history_retention: not_a_dict\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)

        result = config.get_history_retention_config()

        assert result["status_history_max_days"] == 540.0


class TestPassthruKeys:
    """Tests for configurable passthru keys (#446)."""

    def test_defaults_when_no_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nonexistent.yaml")
        result = config.get_passthru_keys()
        assert result == config.DEFAULT_PASSTHRU_KEYS

    def test_null_disables_default_slot(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text('passthru_keys:\n  "5": null\n  escape: null\n')
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        result = config.get_passthru_keys()
        assert "5" not in result
        assert "escape" not in result
        assert "enter" in result

    def test_remap_overrides_default(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text('passthru_keys:\n  ctrl+o: "escape"\n')
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        result = config.get_passthru_keys()
        assert result["ctrl+o"] == "escape"

    def test_user_added_slot_is_included(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text('passthru_keys:\n  f5: "f5"\n')
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        result = config.get_passthru_keys()
        assert result.get("f5") == "f5"

    def test_save_writes_only_deltas(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        # Disable "5" and remap ctrl+o, leave everything else at default
        mapping = dict(config.DEFAULT_PASSTHRU_KEYS)
        del mapping["5"]
        mapping["ctrl+o"] = "escape"
        mapping["f5"] = "f5"  # user-added
        config.save_passthru_keys(mapping)
        import yaml
        written = yaml.safe_load(config_file.read_text())
        pt = written["passthru_keys"]
        assert pt["5"] is None
        assert pt["ctrl+o"] == "escape"
        assert pt["f5"] == "f5"
        # unchanged defaults are NOT written
        assert "enter" not in pt
        assert "1" not in pt

    def test_save_removes_empty_section(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text('passthru_keys:\n  "5": null\n')
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        # Back to full defaults
        config.save_passthru_keys(dict(config.DEFAULT_PASSTHRU_KEYS))
        import yaml
        written = yaml.safe_load(config_file.read_text()) or {}
        assert "passthru_keys" not in written


class TestGetNewAgentDefaultsBackendExplicit:
    """backend_explicit distinguishes "(unset)" from an explicit name (#470)."""

    def test_no_config_is_not_explicit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nonexistent.yaml")
        result = config.get_new_agent_defaults()
        assert result["backend_explicit"] is False
        from overcode.backends import DEFAULT_BACKEND
        assert result["backend"] == DEFAULT_BACKEND

    def test_explicit_backend_is_flagged(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("new_agent_defaults:\n  backend: opencode\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        result = config.get_new_agent_defaults()
        assert result["backend"] == "opencode"
        assert result["backend_explicit"] is True

    def test_explicit_claude_code_is_still_flagged(self, tmp_path, monkeypatch):
        # Distinguishing "(unset)" from an explicit "claude-code" is the
        # whole point of this field — both resolve to the same backend name.
        config_file = tmp_path / "config.yaml"
        config_file.write_text("new_agent_defaults:\n  backend: claude-code\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        result = config.get_new_agent_defaults()
        assert result["backend"] == "claude-code"
        assert result["backend_explicit"] is True

    def test_null_backend_is_not_explicit(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("new_agent_defaults:\n  backend: null\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        result = config.get_new_agent_defaults()
        assert result["backend_explicit"] is False
        from overcode.backends import DEFAULT_BACKEND
        assert result["backend"] == DEFAULT_BACKEND


class TestGetBackendTelemetryEnabled:
    """backend_telemetry: {<name>: on|off} — the per-backend opt-out knob."""

    def test_defaults_to_on_with_no_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nonexistent.yaml")
        for name in ("opencode", "codex", "grok", "hermes", "claude-code"):
            assert config.get_backend_telemetry_enabled(name) is True

    def test_claude_code_is_always_on_even_if_configured_off(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("backend_telemetry:\n  claude-code: off\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        assert config.get_backend_telemetry_enabled("claude-code") is True

    @pytest.mark.parametrize("off_value", ["off", "Off", "false", "False", "0", "no"])
    def test_recognized_off_spellings(self, tmp_path, monkeypatch, off_value):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(f'backend_telemetry:\n  codex: "{off_value}"\n')
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        assert config.get_backend_telemetry_enabled("codex") is False
        # Unconfigured backends are unaffected by another backend's setting.
        assert config.get_backend_telemetry_enabled("opencode") is True
        assert config.get_backend_telemetry_enabled("grok") is True

    def test_literal_bool_false(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("backend_telemetry:\n  grok: false\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        assert config.get_backend_telemetry_enabled("grok") is False

    def test_explicit_on(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("backend_telemetry:\n  opencode: on\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        assert config.get_backend_telemetry_enabled("opencode") is True

    def test_unknown_backend_name_defaults_on(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nonexistent.yaml")
        assert config.get_backend_telemetry_enabled("some-future-backend") is True


class TestModelMetadataConfig:
    """model_metadata: {auto_refresh, max_age_days} — the opt-in catalog refresh knob (#473)."""

    def test_defaults_are_off(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.yaml")
        assert config.get_model_metadata_config() == {"auto_refresh": False, "max_age_days": 7.0}

    def test_parses_values(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("model_metadata:\n  auto_refresh: true\n  max_age_days: 30\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        assert config.get_model_metadata_config() == {"auto_refresh": True, "max_age_days": 30.0}

    def test_garbage_falls_back(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("model_metadata:\n  auto_refresh: true\n  max_age_days: soon\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        assert config.get_model_metadata_config()["max_age_days"] == 7.0


class TestSaveKeepsComments:
    """Saving from the app rewrote config.yaml with yaml.dump, deleting the
    person's comments (quality review item 12)."""

    TEXT = (
        "# My overcode settings\n"
        "tmux_session: agents  # the default session\n"
        "\n"
        "# Sister machines\n"
        "sisters:\n"
        "  - name: laptop   # my laptop\n"
        "    url: http://laptop:8080\n"
        "\n"
        "web:\n"
        "  port: 8080\n"
    )

    def test_only_changed_entries_are_rewritten(self):
        from overcode.config import _merge_into_text
        import yaml

        config = yaml.safe_load(self.TEXT)
        config["web"]["port"] = 9090
        out = _merge_into_text(self.TEXT, config)
        assert "# My overcode settings" in out
        assert "tmux_session: agents  # the default session" in out
        assert "# Sister machines" in out and "# my laptop" in out
        assert yaml.safe_load(out) == config

    def test_a_column_0_list_is_one_block(self):
        """PyYAML's default dump puts a key's list items at column 0."""
        from overcode.config import _merge_into_text
        import yaml

        text = ("# keep me\nsisters:\n- name: a\n  url: http://a\n\n- name: b\n"
                "  url: http://b\nweb:\n  port: 8080\n")
        config = yaml.safe_load(text)
        config["sisters"] = config["sisters"][1:]
        out = _merge_into_text(text, config)
        assert yaml.safe_load(out) == config
        assert "# keep me" in out and "http://a" not in out

    def test_a_merge_that_would_not_read_back_falls_back_to_a_full_dump(self, monkeypatch):
        from overcode import config as config_mod
        import yaml

        monkeypatch.setattr(config_mod, "_top_level_blocks", lambda lines: {})
        config = yaml.safe_load(self.TEXT)
        del config["web"]
        out = config_mod._merge_into_text(self.TEXT, config)
        assert yaml.safe_load(out) == config

    def test_new_and_removed_entries(self):
        from overcode.config import _merge_into_text
        import yaml

        config = yaml.safe_load(self.TEXT)
        del config["web"]
        config["keys"] = {"toggle": "C-o"}
        out = _merge_into_text(self.TEXT, config)
        assert "web:" not in out and "port: 8080" not in out
        assert "# Sister machines" in out
        assert yaml.safe_load(out) == config

    def test_an_unreadable_file_is_replaced_whole(self):
        from overcode.config import _merge_into_text
        import yaml

        out = _merge_into_text("this: [is: not yaml", {"a": 1})
        assert yaml.safe_load(out) == {"a": 1}

    def test_save_is_atomic_and_load_hands_out_a_copy(self, tmp_path, monkeypatch):
        from overcode import config as cfg

        path = tmp_path / "config.yaml"
        path.write_text(self.TEXT)
        monkeypatch.setattr(cfg, "CONFIG_PATH", path)
        cfg._clear_config_cache()
        loaded = cfg.load_config()
        loaded["web"]["port"] = 1  # not saved: the shared cache must not see it
        assert cfg.load_config()["web"]["port"] == 8080
        loaded["web"]["port"] = 9090
        monkeypatch.setattr(cfg.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk")))
        import pytest
        with pytest.raises(OSError):
            cfg.save_config(loaded)
        assert path.read_text() == self.TEXT  # untouched
        assert not list(tmp_path.glob(".config.*.tmp"))

    def test_comments_only_file_keeps_its_comments(self, tmp_path, monkeypatch):
        path = tmp_path / "config.yaml"
        path.write_text("# my notes\n")
        monkeypatch.setattr(config, "CONFIG_PATH", path)
        config.save_config({"a": 1})
        assert "# my notes" in path.read_text()
        assert config.load_config() == {"a": 1}


class TestSaveNeverOverwritesAnUnparseableConfig:
    """A config.yaml with one YAML error loads as {}; the next save (say, the
    tmux toggle key on startup) used to replace the whole file with that one
    key, losing sisters, api keys and billing codes for good."""

    BROKEN = (
        "# my settings\n"
        "sisters:\n"
        "  - name: laptop\n"
        "    url: http://laptop:8080\n"
        "    api_key: s3cret\n"
        " billing:\n"  # the stray indent that breaks the file
        "  codes: [ACME-1]\n"
    )

    @pytest.mark.parametrize("text", [BROKEN, "- a list\n- not a mapping\n", "just a string\n"])
    def test_the_unreadable_file_is_set_aside_not_overwritten(self, tmp_path, monkeypatch, caplog, text):
        path = tmp_path / "config.yaml"
        path.write_text(text)
        monkeypatch.setattr(config, "CONFIG_PATH", path)
        cfg = config.load_config()
        assert cfg == {}
        cfg["tmux"] = {"toggle_key": "C-]"}
        with caplog.at_level("WARNING", logger="overcode.config"):
            config.save_config(cfg)
        aside = list(tmp_path.glob("config.yaml.broken-*"))
        assert len(aside) == 1
        assert aside[0].read_text() == text  # every byte the person wrote
        assert config.load_config() == {"tmux": {"toggle_key": "C-]"}}
        assert str(aside[0]) in caplog.text

    def test_a_second_save_in_the_same_second_keeps_both_copies(self, tmp_path, monkeypatch):
        path = tmp_path / "config.yaml"
        monkeypatch.setattr(config, "CONFIG_PATH", path)
        monkeypatch.setattr(config.time, "strftime", lambda fmt: "20261014-120000")
        path.write_text(self.BROKEN)
        config.save_config({"a": 1})
        path.write_text("still: [broken\n")
        config.save_config({"a": 2})
        texts = sorted(p.read_text() for p in tmp_path.glob("config.yaml.broken-*"))
        assert texts == sorted([self.BROKEN, "still: [broken\n"])

    def test_a_readable_file_is_never_set_aside(self, tmp_path, monkeypatch):
        path = tmp_path / "config.yaml"
        path.write_text("a: 1\n")
        monkeypatch.setattr(config, "CONFIG_PATH", path)
        config.save_config({"a": 2})
        assert not list(tmp_path.glob("config.yaml.broken-*"))


class TestSaveKeepsASymlinkedConfig:
    """save_config's atomic rename replaced a symlinked config.yaml (kept in a
    dotfiles repo) with a private regular file, cutting the link."""

    def test_writes_through_the_link_to_its_target(self, tmp_path, monkeypatch):
        import os

        dotfiles = tmp_path / "dotfiles"
        dotfiles.mkdir()
        target = dotfiles / "overcode.yaml"
        target.write_text("# kept\na: 1\n")
        os.chmod(target, 0o644)
        home = tmp_path / "home"
        home.mkdir()
        link = home / "config.yaml"
        link.symlink_to(target)
        monkeypatch.setattr(config, "CONFIG_PATH", link)

        config.save_config({"a": 2})

        assert link.is_symlink() and link.resolve() == target.resolve()
        assert target.read_text() == "# kept\na: 2\n"
        assert oct(target.stat().st_mode & 0o777) == oct(0o644)
        assert not list(home.glob(".config.*")) and not list(dotfiles.glob(".config.*"))


class TestOneStatGatedLoader:
    """config.yaml has one parse per change, shared by every reader (no TTL)."""

    @staticmethod
    def _write(path, text, mtime_ns):
        import os
        path.write_text(text)
        os.utime(path, ns=(mtime_ns, mtime_ns))  # a settled stamp

    def test_an_edit_is_seen_on_the_next_read(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config.yaml"
        monkeypatch.setattr(config, "CONFIG_PATH", cfg)
        self._write(cfg, "tmux_session: one\n", 1_000_000_000_000_000_000)
        assert config.load_config()["tmux_session"] == "one"
        self._write(cfg, "tmux_session: two\n", 1_000_000_001_000_000_000)
        assert config.load_config()["tmux_session"] == "two"

    def test_unchanged_file_is_parsed_once(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config.yaml"
        monkeypatch.setattr(config, "CONFIG_PATH", cfg)
        self._write(cfg, "tmux_session: one\n", 1_000_000_000_000_000_000)
        assert config._cached_config() is config._cached_config()

    def test_follows_overcode_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OVERCODE_DIR", str(tmp_path))
        (tmp_path / "config.yaml").write_text("tmux_session: here\n")
        assert config.config_path() == tmp_path / "config.yaml"
        assert config.load_config()["tmux_session"] == "here"

    def test_user_config_follows_edits(self, tmp_path, monkeypatch):
        """A long-lived daemon sees a pricing / emoji edit without a restart."""
        from overcode import settings
        cfg = tmp_path / "config.yaml"
        monkeypatch.setattr(config, "CONFIG_PATH", cfg)
        self._write(cfg, "skill_emoji: {a: X}\n", 1_000_000_000_000_000_000)
        first = settings.get_user_config()
        assert first.skill_emoji == {"a": "X"}
        assert settings.get_user_config() is first
        self._write(cfg, "skill_emoji: {a: Y}\npricing: {input: 9.0}\n",
                    1_000_000_001_000_000_000)
        second = settings.get_user_config()
        assert second.skill_emoji == {"a": "Y"}
        assert second.price_input == 9.0

    def test_user_config_without_a_file_is_defaults(self, tmp_path, monkeypatch):
        from overcode import settings
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "missing.yaml")
        assert settings.get_user_config() == settings.UserConfig()
