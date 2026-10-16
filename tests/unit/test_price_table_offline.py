"""Prices on an air-gapped host: the local override table, its lookup order,
``overcode models refresh --prices``, the doctor age warning and the
network refresh's offline failure.

Every offer file here is a tiny synthetic one; no test reads the real AWS
file or touches the network.
"""

from __future__ import annotations

import gzip
import json
import urllib.error
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from overcode import price_table
from overcode.cli import app
from overcode.pricing import MODEL_PRICING, lookup_pricing
from overcode.settings import UserConfig, get_model_pricing

runner = CliRunner()

VENDORED_AS_OF = price_table.vendored_table()[1]["as_of"]


def _later(days: int = 30) -> str:
    return (date.fromisoformat(VENDORED_AS_OF) + timedelta(days=days)).isoformat()


def _offer(published: str, models: dict, region: str = "us-east-1") -> dict:
    """A synthetic AmazonBedrockFoundationModels offer.

    ``models``: {AWS model name: (input, output, cache write, cache read)},
    each priced in the global column only.
    """
    doc = {"offerCode": "AmazonBedrockFoundationModels",
           "publicationDate": f"{published}T08:00:00Z", "products": {},
           "terms": {"OnDemand": {}}}
    kinds = ("input", "output", "cache_write", "cache_read")
    i = 0
    for name, prices in models.items():
        for kind, usd in zip(kinds, prices):
            sku = f"SKU{i}"
            i += 1
            doc["products"][sku] = {"attributes": {
                "provider": "Anthropic", "feature": "On-demand Inference", "model": name,
                "regionCode": region,
                "usagetype": f"USE1_{kind}_tokens_global_standard-Units"}}
            doc["terms"]["OnDemand"][sku] = {"t": {"priceDimensions": {"d": {
                "unit": "1M tokens", "pricePerUnit": {"USD": str(usd)}}}}}
    return doc


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """A private OVERCODE_DIR, so an override written here reaches no other test."""
    d = tmp_path / "overcode"
    monkeypatch.setenv("OVERCODE_DIR", str(d))
    price_table.invalidate()
    yield d
    price_table.invalidate()


def _write_offer(tmp_path, doc, name="index.json", gz=False):
    path = tmp_path / name
    raw = json.dumps(doc).encode()
    path.write_bytes(gzip.compress(raw) if gz else raw)
    return path


def _override(data_dir, published, rows):
    """Write an override table straight to the cache path."""
    doc = {"as_of": published, "generated_by": "test", "rows": [
        {"model": m, "name": m, "input": p[0], "output": p[1], "cache_write": p[2],
         "cache_read": p[3], "cache_write_1h": None, "regional": 1.0,
         "source": "test", "as_of": published} for m, p in rows.items()]}
    price_table.write_table(doc, data_dir / "cache" / "claude_prices.json")


class TestLookupOrder:
    def test_no_override_uses_the_vendored_table(self, data_dir):
        _rows, header = price_table.table()
        assert header["tier"] == "vendored"
        assert lookup_pricing("claude-haiku-5-5").input == pytest.approx(0.10)

    def test_newer_override_wins_over_the_vendored_row(self, data_dir):
        _override(data_dir, _later(), {"claude-haiku-5-5": (0.2, 1.0, 0.25, 0.02)})
        assert price_table.table()[1]["tier"] == "local"
        p = lookup_pricing("claude-haiku-5-5")
        assert (p.input, p.output) == pytest.approx((0.2, 1.0))
        assert _later() in p.source

    def test_model_the_override_lacks_keeps_its_vendored_row(self, data_dir):
        _override(data_dir, _later(), {"claude-haiku-5-5": (0.2, 1.0, 0.25, 0.02)})
        assert lookup_pricing("claude-fable-5-1").cache_read == pytest.approx(0.25)

    def test_override_not_newer_is_ignored(self, data_dir):
        _override(data_dir, VENDORED_AS_OF, {"claude-haiku-5-5": (9.0, 9.0, 9.0, 9.0)})
        rows, header = price_table.table()
        assert header["tier"] == "vendored"
        assert header["ignored_local"]["as_of"] == VENDORED_AS_OF
        assert lookup_pricing("claude-haiku-5-5").input == pytest.approx(0.10)

    def test_corrupt_override_is_ignored(self, data_dir):
        path = data_dir / "cache" / "claude_prices.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not json")
        assert price_table.table()[1]["tier"] == "vendored"

    def test_config_row_wins_over_the_override(self, data_dir):
        _override(data_dir, _later(), {"claude-haiku-5-5": (0.2, 1.0, 0.25, 0.02)})
        config = UserConfig.from_dict({"model_pricing": {"haiku-5-5": {
            "input": 7.0, "output": 8.0, "cache_write": 1.0, "cache_read": 0.5}}})
        assert get_model_pricing("claude-haiku-5-5", config).input == pytest.approx(7.0)
        assert get_model_pricing("claude-haiku-5-5", UserConfig()).input == pytest.approx(0.2)

    def test_family_row_only_for_ids_no_table_names(self, data_dir):
        _override(data_dir, _later(), {"claude-haiku-5-5": (0.2, 1.0, 0.25, 0.02)})
        assert lookup_pricing("claude-haiku-6") is MODEL_PRICING["haiku"]

    def test_override_written_while_running_is_picked_up(self, data_dir):
        assert price_table.long_context_threshold("claude-haiku-5-5") == 100_000
        doc = {"as_of": _later(), "generated_by": "test", "rows": [{
            "model": "claude-haiku-5-5", "name": "h", "input": 0.1, "output": 0.5,
            "cache_write": 0.125, "cache_read": 0.01, "regional": 1.0,
            "long_context": {"above": 50_000, "input": 0.5, "output": 2.5,
                             "cache_write": 0.6, "cache_read": 0.05}}]}
        path = data_dir / "cache" / "claude_prices.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(doc))  # not through write_table: no invalidate
        price_table.table()
        assert price_table.long_context_threshold("claude-haiku-5-5") == 50_000


class TestRefreshLocalOverride:
    def test_builds_the_override_with_the_overlay(self, data_dir, tmp_path):
        src = _write_offer(tmp_path, _offer(_later(), {
            "Claude Haiku 5.5": (0.2, 1.0, 0.25, 0.02), "Claude Opus 9": (6, 30, 7.5, 0.6)}))
        result = price_table.refresh_local_override(src)
        assert result["newer"]
        assert result["path"] == data_dir / "cache" / "claude_prices.json"
        doc = json.loads(result["path"].read_text())
        assert doc["as_of"] == _later() and "index.json" in doc["source"]
        assert doc["generated_by"] == "overcode models refresh --prices"
        haiku = next(r for r in doc["rows"] if r["model"] == "claude-haiku-5-5")
        assert "100K" in haiku["source"]  # OVERLAY note, shared with the release build
        assert "+ claude-opus-9" in result["diff"]
        assert any(line.startswith("~ claude-haiku-5-5") for line in result["diff"])
        assert lookup_pricing("claude-opus-9").input == pytest.approx(6.0)

    def test_never_writes_the_package(self, data_dir, tmp_path):
        before = price_table.DATA_PATH.read_bytes()
        src = _write_offer(tmp_path, _offer(_later(), {"Claude Opus 9": (6, 30, 7.5, 0.6)}))
        price_table.refresh_local_override(src)
        assert price_table.DATA_PATH.read_bytes() == before

    def test_check_writes_nothing(self, data_dir, tmp_path):
        src = _write_offer(tmp_path, _offer(_later(), {"Claude Opus 9": (6, 30, 7.5, 0.6)}))
        result = price_table.refresh_local_override(src, write=False)
        assert result["diff"] == ["+ claude-opus-9"]
        assert not result["path"].exists()

    def test_older_file_is_not_written(self, data_dir, tmp_path):
        src = _write_offer(tmp_path, _offer("2025-01-01", {"Claude Opus 9": (6, 30, 7.5, 0.6)}))
        result = price_table.refresh_local_override(src)
        assert not result["newer"] and not result["path"].exists()

    def test_gzipped_file(self, data_dir, tmp_path):
        src = _write_offer(tmp_path, _offer(_later(), {"Claude Opus 9": (6, 30, 7.5, 0.6)}),
                           name="index.json.gz", gz=True)
        assert price_table.refresh_local_override(src)["newer"]

    @pytest.mark.parametrize("content,message", [
        (b"{nope", "not JSON"),
        (b'{"offerCode": "AmazonEC2", "products": {}}', "AmazonEC2 offer"),
        (b'{"terms": {}}', "no 'products'"),
        (b'{"offerCode": "AmazonBedrockFoundationModels", "publicationDate": "2027-01-01",'
         b' "products": {}}', "no Anthropic"),
    ])
    def test_unusable_files_are_refused(self, data_dir, tmp_path, content, message):
        src = tmp_path / "f.json"
        src.write_bytes(content)
        with pytest.raises(price_table.PriceListError, match=message):
            price_table.refresh_local_override(src)
        assert not (data_dir / "cache" / "claude_prices.json").exists()

    def test_missing_file(self, data_dir, tmp_path):
        with pytest.raises(price_table.PriceListError, match="cannot read"):
            price_table.refresh_local_override(tmp_path / "absent.json")

    def test_region_of_the_offer_names_the_source(self, data_dir, tmp_path):
        src = _write_offer(tmp_path, _offer(_later(), {"Claude Opus 9": (6, 30, 7.5, 0.6)},
                                            region="eu-west-1"))
        doc = price_table.refresh_local_override(src)["doc"]
        assert "eu-west-1" in doc["rows"][0]["source"] and "eu-west-1" in doc["source"]


class TestCli:
    def test_refresh_prices_writes_the_override_and_fetches_nothing(self, data_dir, tmp_path):
        src = _write_offer(tmp_path, _offer(_later(), {"Claude Opus 9": (6, 30, 7.5, 0.6)}))
        with patch("overcode.model_metadata.fetch_models_dev") as fetch, \
                patch("urllib.request.urlopen") as urlopen:
            result = runner.invoke(app, ["models", "refresh", "--prices", str(src)])
        assert result.exit_code == 0, result.output
        assert "+ claude-opus-9" in result.output
        assert (data_dir / "cache" / "claude_prices.json").exists()
        fetch.assert_not_called()
        urlopen.assert_not_called()

    def test_refresh_prices_check(self, data_dir, tmp_path):
        src = _write_offer(tmp_path, _offer(_later(), {"Claude Opus 9": (6, 30, 7.5, 0.6)}))
        result = runner.invoke(app, ["models", "refresh", "--prices", str(src), "--check"])
        assert result.exit_code == 0, result.output
        assert "nothing written" in result.output
        assert not (data_dir / "cache" / "claude_prices.json").exists()

    def test_refresh_prices_bad_file_exits_1(self, data_dir, tmp_path):
        src = tmp_path / "f.json"
        src.write_text("[]")
        result = runner.invoke(app, ["models", "refresh", "--prices", str(src)])
        assert result.exit_code == 1
        assert "not rebuilt" in result.output

    def test_refresh_prices_older_than_shipped_exits_1(self, data_dir, tmp_path):
        src = _write_offer(tmp_path, _offer("2025-01-01", {"Claude Opus 9": (6, 30, 7.5, 0.6)}))
        result = runner.invoke(app, ["models", "refresh", "--prices", str(src)])
        assert result.exit_code == 1
        assert "nothing written" in result.output

    def test_info_shows_the_table_in_use(self, data_dir):
        result = runner.invoke(app, ["models", "info"])
        assert result.exit_code == 0
        assert "Claude price table" in result.output
        assert "shipped with this overcode" in result.output
        assert f"as of:      {VENDORED_AS_OF}" in result.output
        assert "AWS Price List" in result.output
        _override(data_dir, _later(), {"claude-haiku-5-5": (0.2, 1.0, 0.25, 0.02)})
        result = runner.invoke(app, ["models", "info"])
        assert "local override" in result.output
        assert f"as of:      {_later()}" in result.output

    def test_network_refresh_offline_fails_fast_with_the_offline_hint(self, data_dir):
        err = urllib.error.URLError(OSError(8, "nodename nor servname provided"))
        with patch("urllib.request.urlopen", side_effect=err):
            result = runner.invoke(app, ["models", "refresh"])
        assert result.exit_code == 1
        out = " ".join(result.output.split())
        assert "cannot reach models.dev" in out
        assert "--prices" in out

    def test_network_refresh_hung_resolver_is_abandoned(self, data_dir):
        import threading

        from overcode.cli import models as models_cli
        release = threading.Event()
        with patch("overcode.model_metadata.fetch_models_dev",
                   side_effect=lambda **_: release.wait(5)), \
                patch.object(models_cli, "CLI_FETCH_DEADLINE_SECONDS", 0.05):
            result = runner.invoke(app, ["models", "refresh"])
        release.set()
        assert result.exit_code == 1
        assert "cannot reach models.dev" in result.output


class TestDoctorAgeWarning:
    def test_fresh_table_no_warning(self, data_dir):
        today = date.fromisoformat(VENDORED_AS_OF) + timedelta(days=10)
        assert price_table.staleness_findings(90, today=today) == []

    def test_old_table_warns_with_an_air_gapped_hint(self, data_dir):
        today = date.fromisoformat(VENDORED_AS_OF) + timedelta(days=120)
        [finding] = price_table.staleness_findings(90, today=today)
        assert "120 days old" in finding and "vendored" in finding
        assert "upgrade overcode" in finding and "--prices" in finding

    def test_threshold_from_config(self):
        assert price_table.max_age_days_from({}) == 90
        assert price_table.max_age_days_from({"pricing": {"table_max_age_days": 30}}) == 30
        for bad in ("x", 0, -5, True, None):
            assert price_table.max_age_days_from({"pricing": {"table_max_age_days": bad}}) == 90
        assert price_table.max_age_days_from("not a mapping") == 90

    def test_override_age_is_what_counts(self, data_dir):
        _override(data_dir, _later(), {"claude-haiku-5-5": (0.2, 1.0, 0.25, 0.02)})
        today = date.fromisoformat(_later()) + timedelta(days=10)
        assert price_table.staleness_findings(90, today=today) == []
