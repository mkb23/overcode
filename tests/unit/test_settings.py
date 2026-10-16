"""
Unit tests for settings module.

Tests configuration, path management, and environment variable handling.
"""

import os
import pytest
from pathlib import Path
from unittest.mock import patch


class TestSessionPaths:
    """Test session-specific path handling."""

    def test_get_session_dir_default(self):
        """get_session_dir should return ~/.overcode/sessions/{session} by default."""
        from overcode.settings import get_session_dir

        # Clear env var if set
        with patch.dict(os.environ, {}, clear=True):
            # Also need to clear OVERCODE_STATE_DIR
            os.environ.pop("OVERCODE_STATE_DIR", None)
            os.environ.pop("OVERCODE_DIR", None)

            result = get_session_dir("agents")
            expected = Path.home() / ".overcode" / "sessions" / "agents"
            assert result == expected

    def test_get_session_dir_respects_state_dir_env(self, tmp_path):
        """get_session_dir should respect OVERCODE_STATE_DIR environment variable."""
        from overcode.settings import get_session_dir

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            result = get_session_dir("test-session")
            expected = tmp_path / "test-session"
            assert result == expected

    def test_monitor_daemon_pid_path_respects_state_dir(self, tmp_path):
        """Monitor daemon PID path should be inside session dir."""
        from overcode.settings import get_monitor_daemon_pid_path

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            result = get_monitor_daemon_pid_path("my-session")
            expected = tmp_path / "my-session" / "monitor_daemon.pid"
            assert result == expected

    def test_supervisor_daemon_pid_path_respects_state_dir(self, tmp_path):
        """Supervisor daemon PID path should be inside session dir."""
        from overcode.settings import get_supervisor_daemon_pid_path

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            result = get_supervisor_daemon_pid_path("my-session")
            expected = tmp_path / "my-session" / "supervisor_daemon.pid"
            assert result == expected


class TestDaemonIsolation:
    """Test that daemons are properly isolated per session."""

    def test_different_sessions_have_different_pid_paths(self, tmp_path):
        """Each session should have its own daemon PID files."""
        from overcode.settings import (
            get_monitor_daemon_pid_path,
            get_supervisor_daemon_pid_path,
        )

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            session1_monitor = get_monitor_daemon_pid_path("session-1")
            session2_monitor = get_monitor_daemon_pid_path("session-2")
            session1_supervisor = get_supervisor_daemon_pid_path("session-1")
            session2_supervisor = get_supervisor_daemon_pid_path("session-2")

            # All should be different
            paths = [session1_monitor, session2_monitor, session1_supervisor, session2_supervisor]
            assert len(set(paths)) == 4, "All PID paths should be unique"

            # Check expected structure
            assert session1_monitor.parent.name == "session-1"
            assert session2_monitor.parent.name == "session-2"


class TestTuiAttendedTouch:
    """The TUI's attended-liveness file: an mtime the daemon reads, no content."""

    def test_touch_creates_and_ages(self, tmp_path, monkeypatch):
        from overcode import settings

        monkeypatch.setattr(settings, "get_session_dir", lambda s: tmp_path / s)
        assert settings.tui_attended_age_seconds("agents") is None
        settings.touch_tui_attended("agents")
        path = settings.get_tui_attended_path("agents")
        assert path == tmp_path / "agents" / "tui_attended" and path.exists()
        assert path.read_bytes() == b""
        age = settings.tui_attended_age_seconds("agents")
        assert 0 <= age < 5
        assert settings.tui_attended_age_seconds("agents", now=path.stat().st_mtime + 42) == 42

    def test_touch_is_best_effort(self, tmp_path, monkeypatch):
        from overcode import settings

        blocker = tmp_path / "file"
        blocker.write_text("x")
        monkeypatch.setattr(settings, "get_session_dir", lambda s: blocker / s)
        settings.touch_tui_attended("agents")  # parent is a file: swallowed


class TestDaemonSettings:
    """Test DaemonSettings dataclass."""

    def test_default_intervals(self):
        """DaemonSettings should have sensible default intervals."""
        from overcode.settings import DaemonSettings

        settings = DaemonSettings()
        assert settings.interval_fast == 2
        assert settings.interval_slow == 300
        assert settings.interval_idle == 3600

    def test_daemon_claude_settings(self):
        """DaemonSettings should have daemon claude settings."""
        from overcode.settings import DaemonSettings

        settings = DaemonSettings()
        assert settings.daemon_claude_timeout == 300
        assert settings.daemon_claude_poll == 5

    def test_default_tmux_session(self):
        """DaemonSettings should have default tmux session."""
        from overcode.settings import DaemonSettings

        settings = DaemonSettings()
        assert settings.default_tmux_session == "agents"


class TestSessionHelpers:
    """Test session-related helper functions."""

    def test_get_agent_history_path(self, tmp_path):
        """get_agent_history_path should return correct path."""
        from overcode.settings import get_agent_history_path

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            result = get_agent_history_path("my-session")
            expected = tmp_path / "my-session" / "agent_status_history.csv"
            assert result == expected

    def test_get_activity_signal_path(self, tmp_path):
        """get_activity_signal_path should return correct path."""
        from overcode.settings import get_activity_signal_path

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            result = get_activity_signal_path("my-session")
            expected = tmp_path / "my-session" / "activity_signal"
            assert result == expected

    def test_get_supervisor_stats_path(self, tmp_path):
        """get_supervisor_stats_path should return correct path."""
        from overcode.settings import get_supervisor_stats_path

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            result = get_supervisor_stats_path("my-session")
            expected = tmp_path / "my-session" / "supervisor_stats.json"
            assert result == expected

    def test_get_supervisor_log_path(self, tmp_path):
        """get_supervisor_log_path should return correct path."""
        from overcode.settings import get_supervisor_log_path

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            result = get_supervisor_log_path("my-session")
            expected = tmp_path / "my-session" / "supervisor.log"
            assert result == expected

    def test_get_web_server_pid_path(self, tmp_path):
        """get_web_server_pid_path should return correct path."""
        from overcode.settings import get_web_server_pid_path

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            result = get_web_server_pid_path("my-session")
            expected = tmp_path / "my-session" / "web_server.pid"
            assert result == expected

    def test_get_web_server_port_path(self, tmp_path):
        """get_web_server_port_path should return correct path."""
        from overcode.settings import get_web_server_port_path

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            result = get_web_server_port_path("my-session")
            expected = tmp_path / "my-session" / "web_server.port"
            assert result == expected

    def test_get_tui_preferences_path(self, tmp_path):
        """get_tui_preferences_path should return correct path."""
        from overcode.settings import get_tui_preferences_path

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            result = get_tui_preferences_path("my-session")
            expected = tmp_path / "my-session" / "tui_preferences.json"
            assert result == expected

    def test_ensure_session_dir_creates_directory(self, tmp_path):
        """ensure_session_dir should create the directory."""
        from overcode.settings import ensure_session_dir

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            result = ensure_session_dir("new-session")
            expected = tmp_path / "new-session"
            assert result == expected
            assert expected.exists()
            assert expected.is_dir()


class TestSignalActivity:
    """Test signal_activity function."""

    def test_signal_activity_creates_file(self, tmp_path):
        """signal_activity should create signal file."""
        from overcode.settings import signal_activity, get_activity_signal_path

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            # Create session dir
            session_dir = tmp_path / "test-session"
            session_dir.mkdir()

            signal_activity("test-session")

            signal_path = get_activity_signal_path("test-session")
            assert signal_path.exists()

    def test_signal_activity_uses_default_session(self, tmp_path):
        """signal_activity should use default session when None."""
        from overcode.settings import signal_activity, DAEMON

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            # Create session dir with default name
            session_dir = tmp_path / DAEMON.default_tmux_session
            session_dir.mkdir()

            # Should not raise
            signal_activity()


class TestUserConfig:
    """Test UserConfig and related functions."""

    def test_get_user_config_returns_config(self):
        """get_user_config should return UserConfig instance."""
        from overcode.settings import get_user_config, UserConfig

        config = get_user_config()
        assert isinstance(config, UserConfig)


class TestBedrockDiscount:
    """Test bedrock discount handling in get_model_pricing."""

    def _config(self, **kw):
        from overcode.settings import UserConfig
        return UserConfig(**kw)

    def test_no_discount_returns_list_price(self):
        from overcode.settings import get_model_pricing
        cfg = self._config(bedrock_discount=0.30)
        # web/None provider keeps list price even with a discount configured
        mp = get_model_pricing("claude-opus-4-6", cfg, provider="web")
        assert mp.input == 5.0
        assert mp.output == 25.0

    def test_flat_discount_applied_to_bedrock(self):
        from overcode.settings import get_model_pricing
        cfg = self._config(bedrock_discount=0.30)
        mp = get_model_pricing("claude-opus-4-6", cfg, provider="bedrock", routing="global")
        assert mp.input == pytest.approx(5.0 * 0.7)
        assert mp.output == pytest.approx(25.0 * 0.7)
        assert mp.cache_write == pytest.approx(6.25 * 0.7)
        assert mp.cache_read == pytest.approx(0.50 * 0.7)

    def test_per_model_discount_overrides_flat(self):
        from overcode.settings import get_model_pricing
        cfg = self._config(
            bedrock_discount=0.30,
            bedrock_model_discount={"opus": 0.25, "sonnet": 0.40},
        )
        opus = get_model_pricing("claude-opus-4-6", cfg, provider="bedrock", routing="global")
        assert opus.input == pytest.approx(5.0 * 0.75)
        sonnet = get_model_pricing("claude-sonnet-4-6", cfg, provider="bedrock", routing="global")
        assert sonnet.input == pytest.approx(3.0 * 0.60)
        # haiku has no per-model entry -> falls back to the flat discount
        haiku = get_model_pricing("claude-haiku-4-5", cfg, provider="bedrock", routing="global")
        assert haiku.input == pytest.approx(1.0 * 0.70)

    def test_default_config_has_no_discount(self):
        from overcode.settings import get_model_pricing
        cfg = self._config()
        mp = get_model_pricing("claude-opus-4-6", cfg, provider="bedrock", routing="global")
        assert mp.input == 5.0  # unchanged

    def test_discount_loaded_from_yaml(self, tmp_path):
        from overcode import config
        from overcode.settings import UserConfig
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "bedrock:\n"
            "  discount: 0.35\n"
            "  model_discount:\n"
            "    opus: 0.2\n"
        )
        with patch.object(config, "CONFIG_PATH", cfg_file):
            cfg = UserConfig.load()
        assert cfg.bedrock_discount == pytest.approx(0.35)
        assert cfg.bedrock_model_discount == {"opus": pytest.approx(0.2)}

    def test_discount_clamped(self):
        from overcode.settings import _clamp_fraction
        assert _clamp_fraction(-0.5) == 0.0
        assert _clamp_fraction(1.5) == 0.9999
        assert _clamp_fraction("nope") == 0.0
        assert _clamp_fraction(0.3) == pytest.approx(0.3)


class TestBedrockRouting:
    """Cost model 0.6.1 §5: only a global. profile is billed at list price;
    a geographic or in-region one costs the model's regional multiple (1.1)."""

    def _config(self, **kw):
        from overcode.settings import UserConfig
        return UserConfig(**kw)

    def test_default_region_derives_a_geographic_profile(self):
        from overcode.settings import get_model_pricing
        # bedrock.region defaults to us-east-1, so Claude Code picks us.
        mp = get_model_pricing("claude-opus-4-6", self._config(), provider="bedrock")
        assert (mp.input, mp.output, mp.cache_read) == pytest.approx((5.5, 27.5, 0.55))
        assert mp.cache_write_1h_price == pytest.approx(11.0)

    def test_global_profile_is_list_price(self):
        from overcode.settings import get_model_pricing
        mp = get_model_pricing("claude-opus-4-6", self._config(), provider="bedrock",
                               routing="global")
        assert mp.input == pytest.approx(5.0)

    def test_config_routing_for_agents_without_a_recorded_one(self):
        from overcode.settings import get_model_pricing
        cfg = self._config(bedrock_routing="global")
        assert get_model_pricing("claude-opus-4-6", cfg, provider="bedrock").input \
            == pytest.approx(5.0)
        # The routing recorded at launch wins over config
        assert get_model_pricing("claude-opus-4-6", cfg, provider="bedrock",
                                 routing="eu").input == pytest.approx(5.5)
        # A region outside us/eu/ap derives global.
        cfg = self._config(bedrock_region="ca-central-1")
        assert get_model_pricing("claude-opus-4-6", cfg, provider="bedrock").input \
            == pytest.approx(5.0)

    def test_older_models_have_no_premium(self):
        from overcode.settings import get_model_pricing
        mp = get_model_pricing("claude-sonnet-4-20250514", self._config(), provider="bedrock",
                               routing="us")
        assert mp.input == pytest.approx(3.0)

    def test_premium_scales_the_long_context_tier(self):
        from overcode.settings import get_model_pricing
        mp = get_model_pricing("claude-haiku-5-5", self._config(), provider="bedrock",
                               routing="us")
        assert mp.long.input == pytest.approx(0.55)

    def test_premium_then_discount(self):
        from overcode.settings import get_model_pricing
        cfg = self._config(bedrock_discount=0.30)
        mp = get_model_pricing("claude-opus-4-6", cfg, provider="bedrock", routing="us")
        assert mp.input == pytest.approx(5.0 * 1.1 * 0.7)

    def test_web_sessions_are_untouched(self):
        from overcode.settings import get_model_pricing
        mp = get_model_pricing("claude-opus-4-6", self._config(bedrock_discount=0.3),
                               provider="web", routing="us")
        assert mp.input == pytest.approx(5.0)


class TestDiscountOnce:
    """Cost model 0.6.1 §2 item 7: the bedrock discount was applied on top of
    model_pricing rows the docs told users to fill with *net* rates."""

    def _config(self, **kw):
        from overcode.settings import UserConfig
        return UserConfig.from_dict(kw)

    def test_net_row_is_taken_as_it_is(self):
        from overcode.settings import get_model_pricing
        cfg = self._config(
            bedrock={"discount": 0.30},
            model_pricing={"opus": {"input": 3.5, "output": 17.5, "cache_write": 4.375,
                                    "cache_read": 0.35, "net": True}})
        mp = get_model_pricing("claude-opus-4-6", cfg, provider="bedrock")
        assert (mp.input, mp.output, mp.cache_read) == pytest.approx((3.5, 17.5, 0.35))

    def test_list_row_gets_premium_and_discount(self):
        from overcode.settings import get_model_pricing
        cfg = self._config(
            bedrock={"discount": 0.30},
            model_pricing={"opus": {"input": 5.0, "output": 25.0}})
        mp = get_model_pricing("claude-opus-4-6", cfg, provider="bedrock", routing="us")
        # regional comes from the built-in row: a list override keeps the premium
        assert mp.regional == pytest.approx(1.1)
        assert mp.input == pytest.approx(5.0 * 1.1 * 0.7)

    def test_user_row_wins_over_a_more_specific_builtin_row(self):
        from overcode.settings import get_model_pricing
        cfg = self._config(model_pricing={"sonnet": {"input": 9.0, "output": 9.0}})
        assert get_model_pricing("claude-sonnet-5-5", cfg).input == 9.0

    def test_config_parses_routing_region_and_net(self):
        cfg = self._config(bedrock={"routing": "global", "region": "eu-west-1"},
                           model_pricing={"x": {"input": 1.0, "net": True},
                                          "y": {"input": 1.0, "net": "yes"}})
        assert cfg.bedrock_routing == "global" and cfg.bedrock_region == "eu-west-1"
        assert cfg.model_pricing["x"].net is True
        assert cfg.model_pricing["y"].net is False  # only a real true counts


class TestBedrockRoutingDetection:
    @pytest.mark.parametrize("kw,expected", [
        ({"model": "global.anthropic.claude-opus-5-5"}, "global"),
        ({"model": "us.anthropic.claude-sonnet-4-6", "prefix": "global"}, "us"),
        ({"model": "eu.anthropic.claude-opus-4-8"}, "eu"),
        ({"model": "anthropic.claude-opus-4-8"}, "in-region"),
        ({"model": "arn:aws:bedrock:us-east-1:1:application-inference-profile/abc"}, "profile"),
        ({"model": "arn:aws:bedrock:us-east-1:1:inference-profile/global.anthropic.claude-x"},
         "global"),
        ({"model": "opus", "prefix": "apac"}, "apac"),
        ({"model": "opus", "prefix": "nonsense", "region": "eu-west-1"}, "eu"),
        ({"region": "us-east-1"}, "us"),
        ({"region": "us-gov-west-1"}, "us-gov"),
        ({"region": "ap-southeast-2"}, "apac"),
        ({"region": "sa-east-1"}, "global"),
        ({}, "global"),
    ])
    def test_routing(self, kw, expected):
        from overcode.pricing import bedrock_routing
        assert bedrock_routing(**kw) == expected

    def test_launch_reads_config_then_environment(self):
        from overcode import launcher
        with patch("overcode.config.get_bedrock_config",
                   return_value={"region": "us-east-1", "routing": None}):
            assert launcher.launch_bedrock_routing(None, environ={}) == "us"
            assert launcher.launch_bedrock_routing(
                None, environ={"ANTHROPIC_BEDROCK_REGION_PREFIX": "global"}) == "global"
            assert launcher.launch_bedrock_routing(
                None, environ={"ANTHROPIC_MODEL": "eu.anthropic.claude-opus-5-5"}) == "eu"
            assert launcher.launch_bedrock_routing(
                "global.anthropic.claude-opus-5-5",
                environ={"ANTHROPIC_MODEL": "eu.anthropic.claude-opus-5-5"}) == "global"
        with patch("overcode.config.get_bedrock_config",
                   return_value={"region": "us-east-1", "routing": "global"}):
            assert launcher.launch_bedrock_routing(
                "sonnet", environ={"ANTHROPIC_BEDROCK_REGION_PREFIX": "us"}) == "global"

    def test_daemon_prices_at_the_recorded_routing(self):
        from types import SimpleNamespace
        from overcode.daemon_stats_sync import spend_pricing
        from overcode.settings import UserConfig
        session = SimpleNamespace(provider="bedrock", model="claude-opus-4-6",
                                  bedrock_routing="global")
        with patch("overcode.settings.get_user_config", return_value=UserConfig()):
            prices, _, _ = spend_pricing(session)
            assert prices("claude-opus-4-6")[0] == pytest.approx(5.0)
            session.bedrock_routing = "us"
            prices, _, _ = spend_pricing(session)
            assert prices("claude-opus-4-6")[0] == pytest.approx(5.5)


class TestTUIPreferences:
    """Test TUIPreferences dataclass."""

    def test_default_values(self):
        """TUIPreferences should have sensible defaults."""
        from overcode.settings import TUIPreferences

        prefs = TUIPreferences()
        assert hasattr(prefs, 'show_terminated')
        assert isinstance(prefs.show_terminated, bool)

    def test_has_summary_detail(self):
        """TUIPreferences should have summary_detail field."""
        from overcode.settings import TUIPreferences

        prefs = TUIPreferences()
        assert hasattr(prefs, 'summary_detail')
        assert prefs.summary_detail in ["low", "med", "full"]

    def test_loads_prefs_saved_before_0_6_0(self, tmp_path):
        """A prefs file with the standalone monitor's fields still loads (#523)."""
        import json
        from overcode.settings import TUIPreferences, ensure_session_dir

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            ensure_session_dir("old")
            (tmp_path / "old" / "tui_preferences.json").write_text(json.dumps(
                {"preview_visible": True, "tmux_sync": True, "show_terminated": True}))
            prefs = TUIPreferences.load("old")
        assert prefs.show_terminated is True
        assert not hasattr(prefs, "preview_visible")

    def test_load_returns_default_when_no_file(self, tmp_path):
        """TUIPreferences.load should return defaults when file doesn't exist."""
        from overcode.settings import TUIPreferences

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            prefs = TUIPreferences.load("nonexistent-session")
            assert isinstance(prefs, TUIPreferences)
            assert prefs.show_terminated is False  # Default value

    def test_save_creates_file(self, tmp_path):
        """TUIPreferences.save should create preferences file."""
        from overcode.settings import TUIPreferences, ensure_session_dir

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            ensure_session_dir("test-session")
            prefs = TUIPreferences(show_terminated=True)
            prefs.save("test-session")

            prefs_file = tmp_path / "test-session" / "tui_preferences.json"
            assert prefs_file.exists()

    def test_load_reads_saved_values(self, tmp_path):
        """TUIPreferences.load should read saved values."""
        from overcode.settings import TUIPreferences, ensure_session_dir

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            ensure_session_dir("test-session")

            # Save preferences
            original = TUIPreferences(show_terminated=True, hide_asleep=True)
            original.save("test-session")

            # Load and verify
            loaded = TUIPreferences.load("test-session")
            assert loaded.show_terminated is True
            assert loaded.hide_asleep is True

    def test_has_column_config(self):
        """TUIPreferences should have column_config field for per-level overrides."""
        from overcode.settings import TUIPreferences

        prefs = TUIPreferences()
        assert hasattr(prefs, 'column_config')
        assert isinstance(prefs.column_config, dict)
        # Starts empty (no overrides)
        assert prefs.column_config == {}
        # Also has show_column_headers
        assert hasattr(prefs, 'show_column_headers')
        assert prefs.show_column_headers is True

    def test_show_done_always_starts_false(self, tmp_path):
        """show_done should always load as False, even if saved as True (#319).

        The TUI's D toggle is ephemeral (matching CLI --show-done behavior):
        done agents are always hidden on launch.
        """
        from overcode.settings import TUIPreferences, ensure_session_dir
        import json

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            ensure_session_dir("test-session")

            # Manually write a prefs file with show_done=true
            prefs_file = tmp_path / "test-session" / "tui_preferences.json"
            prefs_file.write_text(json.dumps({"show_done": True}))

            # Load should ignore the persisted value
            loaded = TUIPreferences.load("test-session")
            assert loaded.show_done is False

    def test_show_done_not_written_to_disk(self, tmp_path):
        """show_done should not appear in saved preferences file (#319)."""
        from overcode.settings import TUIPreferences, ensure_session_dir
        import json

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            ensure_session_dir("test-session")

            prefs = TUIPreferences(show_done=True)
            prefs.save("test-session")

            prefs_file = tmp_path / "test-session" / "tui_preferences.json"
            data = json.loads(prefs_file.read_text())
            assert "show_done" not in data

    def test_column_config_persist(self, tmp_path):
        """TUIPreferences should persist column_config settings."""
        from overcode.settings import TUIPreferences, ensure_session_dir

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            ensure_session_dir("test-session")

            # Save preferences with per-level column overrides
            original = TUIPreferences()
            original.column_config = {
                "low": {"uptime": True, "running_time": True},
                "med": {"active_pct": True},
            }
            original.show_column_headers = True
            original.save("test-session")

            # Load and verify
            loaded = TUIPreferences.load("test-session")
            assert loaded.column_config["low"]["uptime"] is True
            assert loaded.column_config["low"]["running_time"] is True
            assert loaded.column_config["med"]["active_pct"] is True
            assert loaded.show_column_headers is True

    def test_disabled_sisters_persist(self, tmp_path):
        """disabled_sisters should round-trip through save/load (#323)."""
        from overcode.settings import TUIPreferences, ensure_session_dir

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            ensure_session_dir("test-session")

            original = TUIPreferences()
            original.disabled_sisters = {"desktop", "server"}
            original.save("test-session")

            loaded = TUIPreferences.load("test-session")
            assert loaded.disabled_sisters == {"desktop", "server"}

    def test_disabled_sisters_default_empty(self):
        """disabled_sisters should default to empty set."""
        from overcode.settings import TUIPreferences

        prefs = TUIPreferences()
        assert prefs.disabled_sisters == set()

    def test_custom_detail_level_migrates_to_full(self, tmp_path):
        """Loading 'custom' summary_detail should migrate to 'full'."""
        from overcode.settings import TUIPreferences, ensure_session_dir
        import json

        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            ensure_session_dir("test-session")

            # Write old-style prefs with custom detail level
            prefs_file = tmp_path / "test-session" / "tui_preferences.json"
            prefs_file.write_text(json.dumps({"summary_detail": "custom"}))

            loaded = TUIPreferences.load("test-session")
            assert loaded.summary_detail == "full"


class TestCapDiagnosticsCsv:
    """Tests for cap_diagnostics_csv (#465) — the event_loop_timing.csv backstop."""

    def test_no_truncation_below_cap(self, tmp_path):
        from overcode.settings import cap_diagnostics_csv

        path = tmp_path / "diag.csv"
        path.write_text("timestamp,delta_ms,event\n1,2,\n")

        truncated = cap_diagnostics_csv(path, max_mb=100)

        assert truncated is False
        assert path.read_text() == "timestamp,delta_ms,event\n1,2,\n"

    def test_truncates_when_over_cap(self, tmp_path):
        from overcode.settings import cap_diagnostics_csv

        path = tmp_path / "diag.csv"
        header = "timestamp,delta_ms,event\n"
        # Build many small rows so we can identify "kept" (newest) rows precisely.
        rows = [f"row{i},1.0,\n" for i in range(10000)]
        path.write_text(header + "".join(rows))
        original_size = path.stat().st_size

        truncated = cap_diagnostics_csv(path, max_mb=original_size / (1024 * 1024) * 0.5)

        assert truncated is True
        new_content = path.read_text()
        assert new_content.startswith(header)
        # The newest row must have survived; the very first data row must not.
        assert "row9999" in new_content
        assert "row0,1.0" not in new_content
        assert path.stat().st_size < original_size

    def test_nonexistent_file_returns_false(self, tmp_path):
        from overcode.settings import cap_diagnostics_csv

        truncated = cap_diagnostics_csv(tmp_path / "missing.csv", max_mb=100)

        assert truncated is False


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestListPricingSnapshotFallback:
    """#473 — settings.get_model_pricing consults the bundled snapshot before
    the configured default rates, but after config.yaml overrides."""

    def test_snapshot_beats_configured_default(self):
        from overcode.settings import UserConfig, get_model_pricing
        from overcode.pricing import snapshot_pricing
        cfg = UserConfig(price_input=99.0, price_output=99.0)
        assert get_model_pricing("zai/glm-4.6", cfg) == snapshot_pricing("glm-4.6")

    def test_user_override_beats_snapshot(self):
        from overcode.settings import UserConfig, get_model_pricing
        from overcode.pricing import ModelPricing
        cfg = UserConfig(model_pricing={"glm-4.6": ModelPricing(input=1.0, output=2.0)})
        assert get_model_pricing("zai/glm-4.6", cfg).input == 1.0

    def test_unknown_model_keeps_configured_default(self):
        from overcode.settings import UserConfig, get_model_pricing
        cfg = UserConfig(price_input=99.0, price_output=98.0)
        p = get_model_pricing("acme-internal-model-x1", cfg)
        assert (p.input, p.output) == (99.0, 98.0)


class TestCollapsedParentsPersistence:
    """#464 — folded parents survive a TUI restart via tui_preferences.json."""

    def test_round_trip(self, tmp_path):
        from overcode.settings import TUIPreferences, ensure_session_dir
        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            ensure_session_dir("s")
            TUIPreferences(collapsed_parents={"parent-b", "parent-a"}).save("s")
            loaded = TUIPreferences.load("s")
            assert loaded.collapsed_parents == {"parent-a", "parent-b"}
            assert isinstance(loaded.collapsed_parents, set)

    def test_default_is_empty_and_old_files_load(self, tmp_path):
        from overcode.settings import TUIPreferences, ensure_session_dir, get_tui_preferences_path
        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": str(tmp_path)}):
            ensure_session_dir("s")
            get_tui_preferences_path("s").write_text('{"sort_mode": "by_tree"}')
            loaded = TUIPreferences.load("s")
            assert loaded.collapsed_parents == set()
            assert loaded.sort_mode == "by_tree"
