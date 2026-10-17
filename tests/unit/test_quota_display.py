"""What the person sees of the quota model: the Q% column, its hover, the $
cycler's quota mode, old preferences, the status bar's engine reading and
`overcode billing` / `overcode usage model`. Synthetic data only."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rich.cells import cell_len
from typer.testing import CliRunner

from overcode.columns.context import FleetFlags, FleetRow, compute_fleet_flags
from overcode.columns.render_usage import QUOTA_W
from overcode.monitor_daemon_state import MonitorDaemonState, SessionDaemonState
from overcode.summary_columns import COLUMNS_BY_ID, cell_segments, resolve_column_visible

T0 = 1_800_000_000.0


def _ctx(quota, any_has_quota=True):
    return SimpleNamespace(quota=quota, bg="", fleet=FleetFlags(any_has_quota=any_has_quota),
                           e=lambda c: c)


def _text(segments):
    return "".join(t for t, _ in segments)


class TestColumn:
    col = COLUMNS_BY_ID["quota"]

    def test_two_decimals_in_points(self):
        text = _text(self.col.render(_ctx({"pp7d": 1.3749, "conf": "low"})))
        assert text.strip() == "1.37pp"

    def test_placeholder_has_the_value_s_width(self):
        widths = {cell_len(_text(self.col.render(_ctx(q))))
                  for q in (None, {}, {"pp7d": 0.0}, {"pp7d": 12.34}, {"pp7d": 99.99})}
        assert widths == {QUOTA_W}
        assert _text(self.col.render(_ctx(None))).strip() == "-"

    def test_prior_is_dim_fitted_is_bold(self):
        (_, prior), = self.col.render(_ctx({"pp7d": 1.0, "conf": "prior"}))
        (_, fitted), = self.col.render(_ctx({"pp7d": 1.0, "conf": "medium"}))
        assert "bold" not in prior and "bold" in fitted

    def test_hidden_for_a_fleet_without_a_subscription(self):
        assert cell_segments(self.col, _ctx(None, any_has_quota=False)) == []
        flags = compute_fleet_flags([FleetRow(session=SimpleNamespace(), has_quota=False)])
        assert not flags.any_has_quota
        flags = compute_fleet_flags([FleetRow(session=SimpleNamespace(), has_quota=True)])
        assert flags.any_has_quota

    def test_optional_column(self):
        assert not any(resolve_column_visible(self.col, lvl, {}) for lvl in ("low", "med", "high"))
        assert resolve_column_visible(self.col, "low", {"quota": True})
        assert self.col.header == "Q%" and self.col.description

    def test_plain_and_sort(self):
        assert self.col.render_plain(_ctx({"pp7d": 2.5})) == "2.50pp wk"
        assert self.col.render_plain(_ctx(None)) is None
        assert self.col.sort_key(_ctx({"pp7d": 2.5})) == 2.5

    def test_hover_has_weekly_five_hour_and_confidence(self):
        text = self.col.hover(_ctx({"pp7d": 1.37, "pp5h": 9.1, "w7": 37, "w5": 12, "conf": "prior"})).plain
        assert "1.37pp of the weekly quota" in text
        assert "9.10pp of the current 5-hour window" in text
        assert "weekly 37%" in text and "5-hour 12%" in text
        assert "estimate" in text.lower() and "not fitted yet" in text
        assert self.col.hover(_ctx(None)) is None


class TestEngineToRow:
    def test_session_state_carries_quota_and_fleet_usage(self):
        state = MonitorDaemonState(sessions=[SessionDaemonState(session_id="a", quota={"pp7d": 1.0})],
                                   usage={"w7": 37.0})
        snap = state.to_engine_snapshot(T0)
        assert snap.agents["a"]["quota"] == {"pp7d": 1.0}
        assert snap.fleet["usage"] == {"w7": 37.0}
        back = MonitorDaemonState.from_dict(state.to_dict())
        assert back.usage == {"w7": 37.0} and back.sessions[0].quota == {"pp7d": 1.0}

    def test_old_state_file_loads(self):
        old = MonitorDaemonState.from_dict({"sessions": [{"session_id": "a"}], "status": "active"})
        assert old.usage is None and old.sessions[0].quota is None

    def test_status_bar_takes_the_engine_reading(self):
        from overcode.tui_widgets.daemon_status_bar import engine_usage_snapshot
        snap = engine_usage_snapshot({"w7": 37.0, "w5": 12.0, "r7": T0, "r5": T0 - 60, "fetched_at": T0})
        assert snap.seven_day_pct == 37.0 and snap.five_hour_pct == 12.0
        assert snap.seven_day_resets_at.startswith("2027-")
        assert engine_usage_snapshot(None) is None and engine_usage_snapshot({"w7": None}) is None


class TestPreferences:
    def _load(self, tmp_path, data):
        from overcode.settings import TUIPreferences
        path = tmp_path / "prefs.json"
        path.write_text(json.dumps(data))
        with patch("overcode.settings.get_tui_preferences_path", return_value=path):
            return TUIPreferences.load("agents")

    def test_old_prefs_keep_loading(self, tmp_path):
        prefs = self._load(tmp_path, {"show_cost": "joules", "unknown_future_key": 1})
        assert prefs.show_cost == "joules"
        assert prefs.column_config["low"]["joules"] is True
        assert "quota" not in prefs.column_config["low"]

    def test_quota_mode_round_trips(self, tmp_path):
        config = {lvl: {"quota": True, "token_count": False} for lvl in ("low", "med", "high", "full")}
        prefs = self._load(tmp_path, {"show_cost": "quota", "column_config": config})
        assert prefs.show_cost == "quota"
        assert prefs.column_config["low"]["quota"] is True

    def test_old_prefs_match_their_old_preset(self):
        from overcode.tui_actions.view import ViewActionsMixin
        mixin = SimpleNamespace(_COST_PRESETS=ViewActionsMixin._COST_PRESETS,
                                _COST_PRESET_KEYS=ViewActionsMixin._COST_PRESET_KEYS,
                                _COST_COLUMN_DEFAULTS=ViewActionsMixin._COST_COLUMN_DEFAULTS)
        joules = {"token_count": False, "cost": False, "joules": True}
        idx = ViewActionsMixin._match_cost_preset(mixin, joules)
        assert ViewActionsMixin._COST_PRESETS[idx][2] == "joules"
        quota = dict(joules, joules=False, quota=True)
        idx = ViewActionsMixin._match_cost_preset(mixin, quota)
        assert ViewActionsMixin._COST_PRESETS[idx][2] == "quota"


@pytest.fixture
def usage_files(tmp_path, monkeypatch):
    """A usage directory with one reading and this week's token rows (synthetic)."""
    import time

    from overcode import paths
    from overcode.quota_engine import UsageStore

    root = tmp_path / "usage"
    monkeypatch.setattr(paths, "usage_dir", lambda registry_dir=None: root)
    monkeypatch.setattr("overcode.quota_engine.default_account", lambda: "acct1")
    store = UsageStore(root)
    now = time.time()
    store.append_history("acct1", {"t": now - 60, "src": "api", "w7": 10.0, "w5": 3.0,
                                   "r7": now + 86400, "r5": now + 3600, "plan": "max20x"})
    store.append_tokens([
        {"t": now - 120, "sid": "a", "code": "client-a", "acct": "acct1",
         "tok": {"claude-opus-4-1": {"out": 206_667}}},   # $15.50 of list output
        {"t": now - 100, "sid": "b", "code": "client-b", "acct": "acct1",
         "tok": {"claude-opus-4-1": {"out": 103_333}}},
    ])
    return store


class TestBilling:
    def test_quota_column_and_unattributed_row(self, usage_files):
        from overcode.cli import app
        out = CliRunner().invoke(app, ["billing"]).output
        assert "quota wk" in out
        assert "1.00pp" in out and "0.50pp" in out
        assert "unattributed" in out and "8.50pp" in out
        data = json.loads(CliRunner().invoke(app, ["billing", "--json"]).output)
        assert data["codes"]["client-a"]["quota_pp"] == pytest.approx(1.0, abs=0.01)
        assert data["quota"]["unattributed"] == pytest.approx(8.5, abs=0.02)

    def test_no_subscription_no_quota(self, tmp_path, monkeypatch):
        from overcode import paths
        from overcode.cli import app
        monkeypatch.setattr(paths, "usage_dir", lambda registry_dir=None: tmp_path / "none")
        out = CliRunner().invoke(app, ["billing"]).output
        assert "quota" not in out


class TestUsageModelCommand:
    def test_prints_weights_and_this_week(self, usage_files):
        from overcode.cli import app
        out = CliRunner().invoke(app, ["usage", "model"]).output
        assert "prior only" in out and "max20x" in out
        assert "cw1h" in out and "x:fable" in out
        assert "unattributed 8.50pp" in out
        data = json.loads(CliRunner().invoke(app, ["usage", "model", "--json"]).output)
        assert data["confidence"] == "prior"
        assert data["codes"]["client-b"] == pytest.approx(0.5, abs=0.01)

    def test_refit_saves_a_model(self, usage_files):
        from overcode.cli import app
        result = CliRunner().invoke(app, ["usage", "model", "--refit"])
        assert result.exit_code == 0
        assert usage_files.load_model("acct1") is not None

    def test_calibration_toggle(self, tmp_path, monkeypatch):
        from overcode import config as config_mod
        from overcode.cli import app
        monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "config.yaml")
        config_mod._clear_config_cache()
        try:
            assert "off" in CliRunner().invoke(app, ["usage", "calibration"]).output
            assert CliRunner().invoke(app, ["usage", "calibration", "on"]).exit_code == 0
            assert config_mod.get_quota_config()["calibration"] is True
            assert CliRunner().invoke(app, ["usage", "calibration", "maybe"]).exit_code == 1
            CliRunner().invoke(app, ["usage", "calibration", "off"])
            assert config_mod.get_quota_config()["calibration"] is False
        finally:
            config_mod._clear_config_cache()


class TestDoctor:
    def _status(self, observed, attributed, **model_fields):
        from overcode.quota_model import prior_model
        model = prior_model("max20x")
        for k, v in model_fields.items():
            setattr(model, k, v)
        return {"latest": {"t": T0, "w7": observed}, "model": model,
                "observed": observed, "attributed": attributed}

    def test_over_prediction_warns_under_does_not(self):
        from overcode.doctor import quota_findings
        assert quota_findings(self._status(10.0, 8.0), T0) == []  # the rest is unattributed
        (finding,) = quota_findings(self._status(10.0, 15.0), T0)
        assert "too high" in finding

    def test_poor_and_stale_fit(self):
        from overcode.doctor import quota_findings
        found = quota_findings(self._status(10.0, 9.0, fitted=True, rmse_pp=3.0, coverage=0.3,
                                            fitted_at=T0 - 5 * 86400), T0)
        assert any("poor fit" in f for f in found)
        assert any("days ago" in f for f in found)

    def test_nothing_without_readings(self):
        from overcode.doctor import quota_findings
        assert quota_findings(None, T0) == []
        assert quota_findings({"latest": None}, T0) == []


class TestQuotaConfig:
    def test_defaults_and_warnings(self, tmp_path, monkeypatch):
        from overcode import config as config_mod
        path = tmp_path / "config.yaml"
        monkeypatch.setattr(config_mod, "CONFIG_PATH", path)
        config_mod._clear_config_cache()
        try:
            cfg = config_mod.get_quota_config()
            assert cfg["enabled"] and not cfg["calibration"] and cfg["poll_minutes"] == 5.0
            path.write_text("quota:\n  plan: platinum\n  weekly_usd: -3\n  weeks: 99\n"
                            "  poll_minutes: 1\n  breaks: [2026-05-06, nope]\n  colour: red\n")
            config_mod._clear_config_cache()
            cfg = config_mod.get_quota_config()
            assert cfg["plan"] is None and cfg["weekly_usd"] is None
            assert cfg["weeks"] == 8 and cfg["poll_minutes"] == 5.0
            assert len(cfg["breaks"]) == 1
            assert len(cfg["warnings"]) == 4
        finally:
            config_mod._clear_config_cache()
