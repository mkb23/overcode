"""Energy estimates from tokens and a guessed serving replica (#522, #521).

The old estimate treated the whole API price as electricity at $0.07/kWh, so a
$70/h fleet read as 1 MW. These pin the replacement: per-token joules derived
from GPUs x watts x PUE / throughput, model-name rules that pick a size class,
config.yaml overrides, and the watts display.
"""

from unittest.mock import PropertyMock, patch

import pytest

from overcode.energy import (
    DEFAULT_CLASSES,
    EnergyConfig,
    EnergyProfile,
    estimate_energy_joules,
    format_watts,
)

pytestmark = pytest.mark.unit


class TestProfiles:
    def test_joules_per_token_follow_from_the_replica(self):
        large = DEFAULT_CLASSES["large"]
        # 16 GPUs x 1000 W x PUE 1.2 / 1500 output tok/s
        assert large.per_output(1.2) == pytest.approx(12.8)
        assert large.per_input(1.2) == pytest.approx(16 * 1000 * 1.2 / 50_000)

    def test_stated_joules_per_token_win_over_gpu_figures(self):
        p = EnergyProfile(gpus=99, joules_per_output_token=2.0, joules_per_input_token=0.05)
        assert p.per_output(1.2) == 2.0
        assert p.per_input(1.2) == 0.05

    def test_classes_get_cheaper_with_size(self):
        per_out = [DEFAULT_CLASSES[c].per_output(1.2) for c in ("large", "medium", "small")]
        assert per_out == sorted(per_out, reverse=True)

    @pytest.mark.parametrize("model, cls", [
        ("claude-opus-5-5", "large"),
        ("claude-fable-5-1", "large"),
        ("openai/gpt-5.6-sol", "large"),
        ("gpt-5-mini", "small"),  # small markers beat the family name
        ("claude-haiku-4-5", "small"),
        ("claude-sonnet-5", "medium"),
        ("CLAUDE-OPUS-5", "large"),  # case-insensitive
        ("some-unknown-model", "medium"),
        (None, "medium"),
        ("", "medium"),
    ])
    def test_model_names_pick_a_size_class(self, model, cls):
        assert EnergyConfig().profile_for(model) == DEFAULT_CLASSES[cls]


class TestEstimate:
    def test_each_token_kind_is_weighted(self):
        cfg = EnergyConfig()
        p = DEFAULT_CLASSES["medium"]
        out, inp = p.per_output(cfg.pue), p.per_input(cfg.pue)
        got = estimate_energy_joules("claude-sonnet-5", 1000, 100, 200, 5000, config=cfg)
        assert got == pytest.approx(100 * out + 1000 * inp + 200 * inp * 1.0 + 5000 * inp * 0.1)

    def test_no_tokens_no_energy(self):
        assert estimate_energy_joules("claude-opus-5", 0, 0, 0, 0, config=EnergyConfig()) == 0

    def test_an_hour_of_heavy_opus_work_is_kilowatts_not_a_megawatt(self):
        """The #522 report: roughly $70/h of Opus read as 1 MW.

        An hour of busy agentic Opus is mostly cache reads: 40M cache-read,
        2M fresh input, 1M cache write, 0.3M output tokens.
        """
        joules = estimate_energy_joules(
            "claude-opus-5", 2_000_000, 300_000, 1_000_000, 40_000_000, config=EnergyConfig(),
        )
        watts = joules / 3600
        assert 100 < watts < 50_000

    def test_uses_the_configured_coefficients(self, monkeypatch):
        from overcode import settings

        cfg = settings.UserConfig()
        cfg.energy = EnergyConfig(pue=2.4)
        monkeypatch.setattr(settings, "_user_config", cfg)
        doubled = estimate_energy_joules("claude-opus-5", 0, 1000)
        assert doubled == pytest.approx(2 * estimate_energy_joules(
            "claude-opus-5", 0, 1000, config=EnergyConfig()))

    def test_a_broken_config_falls_back_to_defaults(self, monkeypatch):
        """A stub or half-loaded config must never take the stats sync down."""
        from unittest.mock import Mock

        from overcode import settings

        monkeypatch.setattr(settings, "get_user_config", lambda: Mock())
        assert estimate_energy_joules("claude-opus-5", 0, 1000) == pytest.approx(
            estimate_energy_joules("claude-opus-5", 0, 1000, config=EnergyConfig()))


class TestConfig:
    def test_missing_or_malformed_section_is_the_default(self):
        assert EnergyConfig.from_dict(None) == EnergyConfig()
        assert EnergyConfig.from_dict("nonsense") == EnergyConfig()

    def test_scalars(self):
        cfg = EnergyConfig.from_dict({"pue": 1.5, "cache_read_factor": 0.2,
                                      "cache_write_factor": 1.25})
        assert (cfg.pue, cfg.cache_read_factor, cfg.cache_write_factor) == (1.5, 0.2, 1.25)

    def test_bad_scalars_keep_defaults(self):
        cfg = EnergyConfig.from_dict({"pue": -1, "cache_read_factor": "lots"})
        assert cfg.pue == 1.2 and cfg.cache_read_factor == 0.1

    def test_class_override_keeps_unstated_fields(self):
        cfg = EnergyConfig.from_dict({"classes": {"large": {"gpus": 32}}})
        large = cfg.classes["large"]
        assert large.gpus == 32
        assert large.decode_tps == DEFAULT_CLASSES["large"].decode_tps

    def test_new_class_and_model_rule(self):
        cfg = EnergyConfig.from_dict({
            "classes": {"local": {"gpus": 1, "gpu_watts": 450, "decode_tps": 40,
                                  "prefill_tps": 1500}},
            "models": {"llama": "local"},
        })
        assert cfg.profile_for("llama-3-70b").gpu_watts == 450

    def test_model_rules_run_before_the_built_in_ones(self):
        cfg = EnergyConfig.from_dict({"models": {"opus": "small"}})
        assert cfg.profile_for("claude-opus-5") == DEFAULT_CLASSES["small"]

    def test_inline_profile_rule(self):
        cfg = EnergyConfig.from_dict({"models": {
            "my-model": {"joules_per_output_token": 3.0, "joules_per_input_token": 0.1}}})
        assert cfg.profile_for("my-model-v2").per_output(cfg.pue) == 3.0

    def test_rule_to_an_unknown_class_falls_through(self):
        cfg = EnergyConfig.from_dict({"models": {"opus": "nonexistent"}})
        assert cfg.profile_for("claude-opus-5") == DEFAULT_CLASSES["large"]

    def test_invalid_profile_is_skipped(self):
        cfg = EnergyConfig.from_dict({"classes": {"large": {"gpus": 0}},
                                      "models": {"x": {"gpus": "many"}}})
        assert cfg.classes["large"] == DEFAULT_CLASSES["large"]
        assert cfg.rules == ()

    def test_default_class(self):
        assert EnergyConfig.from_dict({"default_class": "small"}).profile_for("mystery") == \
            DEFAULT_CLASSES["small"]
        assert EnergyConfig.from_dict({"default_class": "bogus"}).default_class == "medium"

    def test_loaded_from_config_yaml(self, tmp_path):
        from overcode.settings import PATHS, UserConfig

        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("energy:\n  pue: 1.4\n  models:\n    opus: medium\n")
        with patch.object(type(PATHS), "config_file", new_callable=PropertyMock,
                          return_value=cfg_file):
            cfg = UserConfig.load()
        assert cfg.energy.pue == 1.4
        assert cfg.energy.profile_for("claude-opus-5") == DEFAULT_CLASSES["medium"]

    def test_config_yaml_without_energy_gets_defaults(self, tmp_path):
        from overcode.settings import PATHS, UserConfig

        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("tmux_session: agents\n")
        with patch.object(type(PATHS), "config_file", new_callable=PropertyMock,
                          return_value=cfg_file):
            assert UserConfig.load().energy == EnergyConfig()


class TestWatts:
    @pytest.mark.parametrize("watts, text", [
        (0, "   0W"), (850, " 850W"), (1234, "1.2kW"), (12_345, " 12kW"),
        (999.6, "1.0kW"), (9_960, " 10kW"), (999_999, "1.0MW"), (2_500_000, "2.5MW"),
    ])
    def test_format(self, watts, text):
        assert format_watts(watts) == text.rjust(5)

    @pytest.mark.parametrize("watts", [0, 5, 50, 500, 999.5, 5e3, 9_999, 5e4, 5e5, 999_999,
                                       5e6, 5e7, 999_999_999, 5e9, 5e11])
    def test_always_five_cells(self, watts):
        assert len(format_watts(watts)) == 5

    @pytest.mark.parametrize("joules, text", [
        (0, "0J"), (999.6, "1.0kJ"), (5_100_000, "5.1MJ"), (999_999, "1.0MJ"),
        (2.6e9, "2.6GJ"),
    ])
    def test_joules_share_the_formatter(self, joules, text):
        from overcode.tui_helpers import format_joules

        assert format_joules(joules) == text.rjust(5)

    def test_window_watts(self):
        from overcode.tui_logic import WindowBurnStats

        assert WindowBurnStats(window_hours=2.0, energy_j=7_200_000).watts == pytest.approx(1000)
        assert WindowBurnStats(window_hours=0, energy_j=100).watts == 0


class TestDisplay:
    def _ctx(self, **overrides):
        from tests.unit.test_summary_columns import _make_ctx

        return _make_ctx(**overrides)

    def test_burn_column_shows_watts_at_the_token_column_width(self):
        from rich.cells import cell_len

        from overcode.summary_columns import render_burn_rate
        from overcode.tui_logic import WindowBurnStats

        burn = WindowBurnStats(window_hours=1.0, input_tokens=300, output_tokens=600,
                               cost_usd=1.0, energy_j=3_600_000)
        watts = render_burn_rate(self._ctx(window_burn=burn, show_cost="joules"))[0][0]
        tokens = render_burn_rate(self._ctx(window_burn=burn, show_cost="tokens"))[0][0]
        assert "1.0kW" in watts and "/h" not in watts
        assert cell_len(watts) == cell_len(tokens)

    def test_burn_plain_is_watts(self):
        from overcode.summary_columns import render_burn_rate_plain
        from overcode.tui_logic import WindowBurnStats

        burn = WindowBurnStats(window_hours=1.0, energy_j=3_600_000)
        assert render_burn_rate_plain(self._ctx(window_burn=burn, show_cost="joules")) == "1.0kW"

    def test_energy_column_reads_the_published_estimate(self):
        from overcode.summary_columns import render_joules

        ctx = self._ctx(claude_stats=object())
        ctx.session.stats.estimated_cost_usd = 70.0  # must not drive energy any more
        ctx.session.stats.estimated_energy_j = 5_000_000
        assert "5.0MJ" in render_joules(ctx)[0][0]
