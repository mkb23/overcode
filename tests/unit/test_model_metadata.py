"""model_metadata: the bundled models.dev snapshot behind the curated tables (#473)."""

import json

import pytest

from overcode import model_metadata
from overcode.model_metadata import (
    INCLUDED_PROVIDERS,
    PROVIDER_PRIORITY,
    SNAPSHOT_PATH,
    bare_model_id,
    dump_snapshot,
    known_model_ids,
    lookup,
    snapshot_info,
    transcode_models_dev,
)


def _spec(context=128_000, output=16_000, cost=None, modalities=("text",), **extra):
    spec = {
        "name": extra.pop("name", "Some Model"),
        "modalities": {"input": ["text"], "output": list(modalities)},
        "limit": {"context": context, "output": output},
    }
    if cost is not None:
        spec["cost"] = cost
    spec.update(extra)
    return spec


class TestBareModelId:
    def test_strips_provider_qualifier(self):
        assert bare_model_id("openai/gpt-5.6-sol") == "gpt-5.6-sol"

    def test_keeps_only_last_segment_of_nested_qualifier(self):
        assert bare_model_id("openrouter/qwen/qwen3-coder-plus") == "qwen3-coder-plus"

    def test_strips_capacity_suffix(self):
        assert bare_model_id("claude-opus-5[1m]") == "claude-opus-5"

    def test_bare_id_is_unchanged(self):
        assert bare_model_id("glm-4.6") == "glm-4.6"


class TestTranscode:
    def test_first_party_provider_wins_over_reseller(self):
        raw = {
            "openrouter": {"models": {"zai/glm-4.6": _spec(context=1, cost={"input": 9, "output": 9})}},
            "zai": {"models": {"glm-4.6": _spec(context=204_800, cost={"input": 0.6, "output": 2.2, "cache_read": 0.11})}},
        }
        snap = transcode_models_dev(raw, fetched_at="2026-09-17")
        assert snap["models"]["glm-4.6"]["provider"] == "zai"
        assert snap["models"]["glm-4.6"]["context"] == 204_800
        assert snap["models"]["glm-4.6"]["input"] == 0.6

    def test_reseller_only_adds_ids_the_first_parties_lack(self):
        raw = {
            "openrouter": {"models": {"acme/long-tail-7b": _spec(context=32_000, cost={"input": 0.1, "output": 0.2})}},
            "openai": {"models": {"gpt-5.6-sol": _spec(context=1_050_000, cost={"input": 4, "output": 20})}},
        }
        snap = transcode_models_dev(raw, fetched_at="x")
        assert set(snap["models"]) == {"long-tail-7b", "gpt-5.6-sol"}
        assert snap["models"]["long-tail-7b"]["provider"] == "openrouter"

    def test_providers_outside_the_allowlist_are_ignored(self):
        raw = {"some-random-reseller": {"models": {"anthropic--claude-4-opus": _spec(cost={"input": 1, "output": 2})}}}
        assert transcode_models_dev(raw, fetched_at="x")["models"] == {}

    def test_non_text_models_are_skipped(self):
        raw = {"xai": {"models": {"grok-imagine-image": _spec(modalities=("image",), cost={"input": 1, "output": 1})}}}
        assert transcode_models_dev(raw, fetched_at="x")["models"] == {}

    def test_models_without_a_context_window_are_skipped(self):
        raw = {"openai": {"models": {"text-embedding-3-small": _spec(context=0, cost={"input": 0.02, "output": 0})}}}
        assert transcode_models_dev(raw, fetched_at="x")["models"] == {}

    def test_zero_priced_listing_keeps_limits_but_drops_cost(self):
        raw = {"zai": {"models": {"glm-4.7-flash": _spec(context=200_000, cost={"input": 0, "output": 0})}}}
        record = transcode_models_dev(raw, fetched_at="x")["models"]["glm-4.7-flash"]
        assert record["context"] == 200_000
        assert "input" not in record and "output" not in record

    def test_missing_cost_block_is_tolerated(self):
        raw = {"zai": {"models": {"glm-4.6": _spec(context=204_800)}}}
        record = transcode_models_dev(raw, fetched_at="x")["models"]["glm-4.6"]
        assert record["context"] == 204_800
        assert "input" not in record

    def test_keys_are_lowercased(self):
        raw = {"minimax": {"models": {"MiniMax-M2.5": _spec(context=204_800, cost={"input": 0.3, "output": 1.2})}}}
        assert "minimax-m2.5" in transcode_models_dev(raw, fetched_at="x")["models"]

    def test_records_provenance(self):
        snap = transcode_models_dev({}, fetched_at="2026-09-17")
        assert snap["source"] == model_metadata.MODELS_DEV_URL
        assert snap["fetched_at"] == "2026-09-17"
        assert snap["provider_priority"] == PROVIDER_PRIORITY

    def test_priority_providers_are_all_included(self):
        assert set(PROVIDER_PRIORITY) <= set(INCLUDED_PROVIDERS)


class TestDumpSnapshot:
    def test_round_trips_and_is_one_model_per_line(self):
        raw = {"zai": {"models": {
            "glm-4.6": _spec(context=204_800, cost={"input": 0.6, "output": 2.2}),
            "glm-4.7": _spec(context=204_800, cost={"input": 0.6, "output": 2.2}),
        }}}
        snap = transcode_models_dev(raw, fetched_at="2026-09-17")
        text = dump_snapshot(snap)
        assert json.loads(text) == snap
        model_lines = [ln for ln in text.splitlines() if ln.startswith('    "glm-')]
        assert len(model_lines) == 2


class TestBundledSnapshot:
    """The committed data file, as shipped in the wheel."""

    def test_snapshot_file_ships_and_parses(self):
        assert SNAPSHOT_PATH.exists(), SNAPSHOT_PATH
        info = snapshot_info()
        assert info["model_count"] > 500
        assert info["fetched_at"] != "unknown"

    def test_open_weights_families_from_the_issue_resolve(self):
        """#473 named GLM and Kimi; both must carry a window *and* a price."""
        for model_id in ("glm-4.6", "kimi-k2.6"):
            meta = lookup(model_id)
            assert meta is not None, model_id
            assert meta.context_window and meta.context_window > 100_000
            assert meta.has_pricing
            assert meta.open_weights is True

    def test_lookup_accepts_every_reported_spelling(self):
        bare = lookup("glm-4.6")
        assert lookup("zai/glm-4.6") == bare
        assert lookup("ZAI/GLM-4.6") == bare
        assert lookup("openrouter/z-ai/glm-4.6") == bare

    def test_unknown_id_is_none(self):
        assert lookup("acme-internal-model-x1") is None
        assert lookup("") is None
        assert lookup(None) is None

    def test_context_window_helper(self):
        assert model_metadata.context_window("moonshotai/kimi-k3") == lookup("kimi-k3").context_window
        assert model_metadata.context_window("nope-nope") is None

    def test_known_model_ids_is_sorted_and_bare(self):
        ids = known_model_ids()
        assert ids == sorted(ids)
        assert "glm-4.6" in ids
        assert all("/" not in i for i in ids)

    def test_corrupt_snapshot_degrades_to_empty(self, tmp_path, monkeypatch):
        bad = tmp_path / "model_metadata.json"
        bad.write_text("{not json")
        monkeypatch.setattr(model_metadata, "SNAPSHOT_PATH", bad)
        model_metadata.invalidate_cache()
        try:
            assert lookup("glm-4.6") is None
            assert snapshot_info()["model_count"] == 0
        finally:
            model_metadata.invalidate_cache()


class TestCatalogTiers:
    """The freshest local models.dev vintage wins; the bundled snapshot is
    the fallback; OVERCODE_MODEL_METADATA_BUNDLED_ONLY pins to bundled."""

    @pytest.fixture(autouse=True)
    def _local_tiers(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OVERCODE_MODEL_METADATA_BUNDLED_ONLY", raising=False)
        monkeypatch.setenv("OVERCODE_MODEL_METADATA_CACHE", str(tmp_path / "local" / "model_metadata.json"))
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
        model_metadata.invalidate_cache()
        yield
        model_metadata.invalidate_cache()

    @staticmethod
    def _raw(context):
        return {"zai": {"models": {"glm-4.6": _spec(context=context, cost={"input": 0.6, "output": 2.2})}}}

    def _write_local(self, context, fetched_at="2026-10-01"):
        path = model_metadata.local_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_snapshot(transcode_models_dev(self._raw(context), fetched_at=fetched_at)))
        return path

    def _write_opencode(self, context):
        path = model_metadata.opencode_models_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._raw(context)))
        return path

    def test_no_local_tier_falls_back_to_bundled(self):
        info = snapshot_info()
        assert info["tier"] == "bundled"
        assert info["path"] == str(SNAPSHOT_PATH)

    def test_local_refreshed_cache_is_used(self):
        self._write_local(111_111)
        assert lookup("glm-4.6").context_window == 111_111
        assert snapshot_info()["tier"] == "local"
        assert snapshot_info()["fetched_at"] == "2026-10-01"

    def test_opencode_raw_cache_is_transcoded_and_used(self):
        self._write_opencode(222_222)
        assert lookup("glm-4.6").context_window == 222_222
        assert snapshot_info()["tier"] == "opencode"

    def test_newest_local_vintage_wins(self):
        import os
        import time
        local = self._write_local(111_111)
        oc = self._write_opencode(222_222)
        old = time.time() - 3600
        os.utime(local, (old, old))
        assert lookup("glm-4.6").context_window == 222_222
        model_metadata.invalidate_cache()
        os.utime(oc, (old - 3600, old - 3600))
        assert lookup("glm-4.6").context_window == 111_111

    def test_corrupt_local_tier_is_skipped(self):
        path = model_metadata.local_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{nope")
        assert snapshot_info()["tier"] == "bundled"

    def test_local_file_change_is_picked_up_after_ttl(self, monkeypatch):
        self._write_local(111_111)
        assert lookup("glm-4.6").context_window == 111_111
        self._write_local(333_333)
        monkeypatch.setattr(model_metadata, "_ACTIVE_TTL_SECONDS", 0.0)
        import os
        import time
        os.utime(model_metadata.local_cache_path(), (time.time() + 5, time.time() + 5))
        assert lookup("glm-4.6").context_window == 333_333

    def test_bundled_only_env_ignores_local_tiers(self, monkeypatch):
        self._write_local(111_111)
        monkeypatch.setenv("OVERCODE_MODEL_METADATA_BUNDLED_ONLY", "1")
        model_metadata.invalidate_cache()
        assert snapshot_info()["tier"] == "bundled"

    def test_curated_table_still_wins_over_any_tier(self):
        from overcode.history_reader import model_context_window
        self._write_local(111_111)
        raw = {"openai": {"models": {"gpt-5.6-sol": _spec(context=999, cost={"input": 4, "output": 20})}}}
        path = model_metadata.local_cache_path()
        path.write_text(dump_snapshot(transcode_models_dev(raw, fetched_at="2026-10-01")))
        model_metadata.invalidate_cache()
        assert lookup("gpt-5.6-sol").context_window == 999
        assert model_context_window("gpt-5.6-sol") == 258_400


class TestRefreshLocalCache:
    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OVERCODE_MODEL_METADATA_BUNDLED_ONLY", raising=False)
        monkeypatch.setenv("OVERCODE_MODEL_METADATA_CACHE", str(tmp_path / "cache" / "model_metadata.json"))
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
        model_metadata.invalidate_cache()
        yield
        model_metadata.invalidate_cache()

    def test_writes_cache_and_becomes_active(self):
        from datetime import date
        raw = {"zai": {"models": {"glm-4.6": _spec(context=424_242, cost={"input": 0.6, "output": 2.2})}}}
        info = model_metadata.refresh_local_cache(fetch=lambda: raw, today=date(2026, 10, 2))
        assert info["model_count"] == 1
        assert info["fetched_at"] == "2026-10-02"
        assert model_metadata.local_cache_path().exists()
        assert not list(model_metadata.local_cache_path().parent.glob("*.tmp"))
        assert lookup("glm-4.6").context_window == 424_242
        assert model_metadata.local_cache_age_days() is not None

    def test_empty_catalog_does_not_overwrite(self):
        raw = {"zai": {"models": {"glm-4.6": _spec(context=424_242, cost={"input": 0.6, "output": 2.2})}}}
        model_metadata.refresh_local_cache(fetch=lambda: raw)
        with pytest.raises(ValueError):
            model_metadata.refresh_local_cache(fetch=lambda: {})
        assert lookup("glm-4.6").context_window == 424_242

    def test_fetch_failure_propagates_and_leaves_no_file(self):
        def boom():
            raise OSError("no network")
        with pytest.raises(OSError):
            model_metadata.refresh_local_cache(fetch=boom)
        assert not model_metadata.local_cache_path().exists()
        assert model_metadata.local_cache_age_days() is None


class TestStalenessFindings:
    def test_fresh_catalog_has_no_finding(self):
        from datetime import date
        vintage = date.fromisoformat(snapshot_info()["fetched_at"])
        assert model_metadata.staleness_findings(90, today=vintage) == []

    def test_old_catalog_nudges_refresh(self):
        from datetime import date, timedelta
        vintage = date.fromisoformat(snapshot_info()["fetched_at"])
        findings = model_metadata.staleness_findings(90, today=vintage + timedelta(days=91))
        assert len(findings) == 1
        assert "overcode models refresh" in findings[0]
