"""billing.codes in config.yaml: the TUI's billing-code shortlist (warns, never raises)."""

import pytest

from overcode import config


@pytest.fixture(autouse=True)
def _clear_config_cache():
    config._clear_config_cache()
    yield
    config._clear_config_cache()


def _load(tmp_path, monkeypatch, text):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(text)
    monkeypatch.setattr(config, "CONFIG_PATH", config_file)
    return config.get_billing_config()


def test_empty_by_default(tmp_path, monkeypatch):
    assert _load(tmp_path, monkeypatch, "tmux_session: agents\n") == {"codes": [], "warnings": []}


def test_no_config_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "missing.yaml")
    assert config.get_billing_config() == {"codes": [], "warnings": []}


def test_codes_in_order_stripped_and_deduplicated(tmp_path, monkeypatch):
    cfg = _load(tmp_path, monkeypatch,
                "billing:\n  codes: [clientA, ' clientB ', internal, clientA, 1234]\n")
    assert cfg == {"codes": ["clientA", "clientB", "internal", "1234"], "warnings": []}


def test_block_list_form(tmp_path, monkeypatch):
    assert _load(tmp_path, monkeypatch, "billing:\n  codes:\n  - a\n  - b\n")["codes"] == ["a", "b"]


@pytest.mark.parametrize("text, warning", [
    ("billing: [a, b]\n", "should be a mapping"),
    ("billing: oops\n", "should be a mapping"),
    ("billing:\n  codes: clientA\n", "should be a list"),
    ("billing:\n  codes: {a: 1}\n", "should be a list"),
    ("billing:\n  codez: [a]\n", "unknown key 'codez'"),
])
def test_malformed_shapes_warn_and_give_no_codes(tmp_path, monkeypatch, text, warning):
    cfg = _load(tmp_path, monkeypatch, text)
    assert cfg["codes"] == []
    assert any(warning in w for w in cfg["warnings"])


def test_bad_entries_are_skipped_with_a_warning(tmp_path, monkeypatch):
    cfg = _load(tmp_path, monkeypatch, "billing:\n  codes: [good, '', [x], {k: v}, null, true]\n")
    assert cfg["codes"] == ["good"]
    assert len(cfg["warnings"]) == 5
