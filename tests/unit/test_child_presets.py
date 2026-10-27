"""Children presets (child_presets): resolution, config overrides, warnings never raise."""

from datetime import datetime, timedelta

import pytest

from overcode import child_presets, config
from overcode.child_presets import (
    BUILTIN_PRESETS,
    DEFAULT_ARCHIVE_AFTER_SECONDS,
    DEFAULT_PRESET,
    UnknownPresetError,
    archive_after_of,
    describe,
    format_duration,
    load_presets,
    parse_archive_after,
    parse_duration,
    parse_on_stuck,
    resolve_preset,
)
from overcode.monitor_daemon_core import should_auto_archive


@pytest.fixture(autouse=True)
def _clear_config_cache():
    config._clear_config_cache()
    yield
    config._clear_config_cache()


class TestBuiltins:
    def test_default_is_supervised_and_matches_pre_preset_behaviour(self):
        assert DEFAULT_PRESET == "supervised"
        p = BUILTIN_PRESETS["supervised"]
        assert (p.oversight_policy, p.oversight_timeout_seconds) == ("wait", 0.0)
        assert p.archive_after_seconds == DEFAULT_ARCHIVE_AFTER_SECONDS == 3600
        assert p.budget_usd is None

    def test_balanced_times_out_after_a_day(self):
        p = BUILTIN_PRESETS["balanced"]
        assert (p.oversight_policy, p.oversight_timeout_seconds) == ("timeout", 86400)
        assert p.archive_after_seconds == 3600

    def test_autonomous_short_timeout_quick_cleanup(self):
        p = BUILTIN_PRESETS["autonomous"]
        assert (p.oversight_policy, p.oversight_timeout_seconds) == ("timeout", 3600)
        assert p.archive_after_seconds == 600

    def test_describe(self):
        assert describe(BUILTIN_PRESETS["balanced"]) == "oversight timeout 24h, archived 1h after done"
        assert "waits for a report" in describe(BUILTIN_PRESETS["supervised"])


class TestParsing:
    @pytest.mark.parametrize("value,seconds", [
        (90, 90.0), ("30s", 30.0), ("5m", 300.0), ("1h", 3600.0), ("2d", 172800.0),
        (" 10M ", 600.0), ("45", 45.0), (1.5, 1.5),
    ])
    def test_durations(self, value, seconds):
        assert parse_duration(value) == seconds

    @pytest.mark.parametrize("value", ["", "abc", "5x", -1, "-5m", True, None, [1]])
    def test_bad_durations(self, value):
        with pytest.raises(ValueError):
            parse_duration(value)

    def test_on_stuck(self):
        assert parse_on_stuck("wait") == ("wait", 0.0)
        assert parse_on_stuck("FAIL") == ("fail", 0.0)
        assert parse_on_stuck("timeout:2h") == ("timeout", 7200.0)
        for bad in ("timeout:0", "timeout:x", "later", 5):
            with pytest.raises(ValueError):
                parse_on_stuck(bad)

    def test_archive_after_never(self):
        assert parse_archive_after("never") == -1.0
        assert parse_archive_after("10m") == 600.0

    def test_format_duration(self):
        assert format_duration(86400) == "24h"
        assert format_duration(3 * 86400) == "3d"
        assert format_duration(600) == "10m"
        assert format_duration(90) == "90s"
        assert format_duration(-1) == "never"


class TestLoadPresets:
    def test_no_section_is_the_builtins(self):
        presets, default, warnings = load_presets({})
        assert list(presets) == ["supervised", "balanced", "autonomous"]
        assert default == "supervised"
        assert warnings == []

    def test_override_a_builtin_field_by_field(self):
        presets, _, warnings = load_presets({"presets": {"autonomous": {"archive_after": "30m"}}})
        p = presets["autonomous"]
        assert p.archive_after_seconds == 1800
        assert (p.oversight_policy, p.oversight_timeout_seconds) == ("timeout", 3600)
        assert warnings == []

    def test_user_preset_fills_from_supervised(self):
        presets, _, warnings = load_presets({"presets": {"overnight": {
            "on_stuck": "timeout:8h", "budget": 5, "description": "Long runs"}}})
        p = presets["overnight"]
        assert (p.oversight_policy, p.oversight_timeout_seconds) == ("timeout", 28800)
        assert p.archive_after_seconds == DEFAULT_ARCHIVE_AFTER_SECONDS
        assert p.budget_usd == 5.0
        assert p.description == "Long runs"
        assert warnings == []

    def test_oversight_timeout_shorthand_and_zero_means_wait(self):
        presets, _, _ = load_presets({"presets": {
            "a": {"oversight_timeout": "2h"}, "b": {"oversight_timeout": 0}}})
        assert presets["a"].oversight_policy == "timeout"
        assert presets["a"].oversight_timeout_seconds == 7200
        assert presets["b"].oversight_policy == "wait"

    def test_on_stuck_fail_and_archive_never(self):
        presets, _, _ = load_presets({"presets": {"x": {"on_stuck": "fail", "archive_after": "never"}}})
        assert presets["x"].oversight_policy == "fail"
        assert presets["x"].archive_after_seconds == -1

    def test_configured_default(self):
        _, default, warnings = load_presets({"preset": "balanced"})
        assert default == "balanced"
        assert warnings == []

    def test_unknown_default_warns_and_uses_supervised(self):
        _, default, warnings = load_presets({"preset": "relaxed"})
        assert default == "supervised"
        assert any("unknown preset 'relaxed'" in w for w in warnings)

    @pytest.mark.parametrize("section,needle", [
        ("nonsense", "children: should be a mapping"),
        ({"presetz": "x"}, "unknown key 'presetz'"),
        ({"presets": ["a"]}, "should be a mapping of preset names"),
        ({"presets": {"x": "fast"}}, "children.presets.x: should be a mapping"),
        ({"presets": {"x": {"speed": 1}}}, "unknown key 'speed'"),
        ({"presets": {"x": {"on_stuck": "later"}}}, "x.on_stuck"),
        ({"presets": {"x": {"oversight_timeout": "soon"}}}, "x.oversight_timeout"),
        ({"presets": {"x": {"archive_after": "eventually"}}}, "x.archive_after"),
        ({"presets": {"x": {"budget": "lots"}}}, "x.budget"),
        ({"presets": {"x": {"budget": -2}}}, "x.budget"),
        ({"presets": {"x": {"on_stuck": "fail", "oversight_timeout": "1h"}}}, "both"),
    ])
    def test_bad_entries_warn_never_raise(self, section, needle):
        presets, default, warnings = load_presets(section)
        assert any(needle in w for w in warnings), warnings
        assert default == "supervised"
        assert "supervised" in presets

    def test_bad_field_leaves_the_rest_of_the_preset(self):
        presets, _, _ = load_presets({"presets": {"x": {"budget": "lots", "archive_after": "5m"}}})
        assert presets["x"].archive_after_seconds == 300
        assert presets["x"].budget_usd is None

    def test_reads_config_yaml(self, tmp_path, monkeypatch):
        path = tmp_path / "config.yaml"
        path.write_text("children:\n  preset: autonomous\n  presets:\n    mine:\n      budget: 2.5\n")
        monkeypatch.setattr(config, "CONFIG_PATH", path)
        presets, default, warnings = load_presets()
        assert default == "autonomous"
        assert presets["mine"].budget_usd == 2.5
        assert warnings == []


class TestResolvePreset:
    def test_child_without_name_gets_the_default(self):
        preset, warnings = resolve_preset(None, is_child=True, section={})
        assert preset.name == "supervised"
        assert warnings == []

    def test_child_gets_the_configured_default(self):
        preset, _ = resolve_preset(None, is_child=True, section={"preset": "balanced"})
        assert preset.name == "balanced"

    def test_top_level_without_name_gets_none(self):
        assert resolve_preset(None, is_child=False, section={"preset": "balanced"}) == (None, [])

    def test_explicit_name_applies_to_any_launch(self):
        preset, _ = resolve_preset("autonomous", is_child=False, section={})
        assert preset.name == "autonomous"

    def test_unknown_name_raises_with_the_known_names(self):
        """A typo on the command line refuses the launch (config typos only warn)."""
        section = {"preset": "balanced", "presets": {"mine": {"budget": 1}, "x": "fast"}}
        with pytest.raises(UnknownPresetError) as info:
            resolve_preset("autonomus", is_child=True, section=section)
        err = info.value
        assert err.name == "autonomus"
        assert err.known == ["supervised", "balanced", "autonomous", "mine"]
        assert "unknown preset 'autonomus'" in str(err)
        # Config warnings travel with the error so the CLI can still show them
        assert any("children.presets.x" in w for w in err.warnings)

    def test_unknown_configured_default_still_only_warns(self):
        preset, warnings = resolve_preset("balanced", is_child=True, section={"preset": "nope"})
        assert preset.name == "balanced"
        assert any("unknown preset 'nope'" in w for w in warnings)


class TestArchiveAfter:
    def test_archive_after_of(self):
        class S:
            archive_after_seconds = None
        assert archive_after_of(S()) == DEFAULT_ARCHIVE_AFTER_SECONDS
        S.archive_after_seconds = 600.0
        assert archive_after_of(S()) == 600.0
        assert archive_after_of(object()) == DEFAULT_ARCHIVE_AFTER_SECONDS

    def test_should_auto_archive_honours_the_delay_and_never(self):
        now = datetime(2026, 10, 17, 12, 0, 0)
        since = (now - timedelta(minutes=15)).isoformat()
        assert should_auto_archive("done", since, now, timeout_seconds=600)
        assert not should_auto_archive("done", since, now)  # the default hour
        assert not should_auto_archive("done", since, now, timeout_seconds=-1)


def test_module_doc_names_every_preset_key():
    for key in child_presets._PRESET_KEYS:
        assert key in child_presets.__doc__


def test_housekeeping_closes_a_done_child_after_its_own_delay(tmp_path):
    """The daemon's done-archive pass reads each child's archive_after_seconds."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from overcode.daemon_cadence import Cadence
    from overcode.daemon_housekeeping import Housekeeping

    now = datetime(2026, 10, 17, 12, 0, 0)
    done_since = (now - timedelta(minutes=15)).isoformat()

    def child(sid, archive_after):
        return SimpleNamespace(id=sid, name=sid, status="done", tmux_window=f"{sid}-w",
                               archive_after_seconds=archive_after,
                               stats=SimpleNamespace(state_since=done_since))

    sessions = [child("quick", 600.0), child("default", None), child("never", -1.0)]
    hk = Housekeeping("agents", tmux=MagicMock(), history_path=tmp_path / "h.csv",
                      log=MagicMock(), housekeeping=Cadence(120),
                      rotation=Cadence(3600), model_metadata=Cadence(3600))
    pending = MagicMock()
    pending.view.side_effect = lambda s: s  # nothing staged this tick
    with patch("overcode.implementations.RealTmux") as real_tmux:
        hk.auto_archive_done_agents(sessions, pending, now)
    pending.update_session_status.assert_called_once_with("quick", "terminated")
    real_tmux.return_value.kill_window.assert_called_once_with("agents", "quick-w")
