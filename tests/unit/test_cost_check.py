"""overcode doctor checks Claude spend against Claude Code's own cost-state.

The spike captures (``tests/fixtures/spike_spend``) end with the
``cost-state`` record Claude Code wrote: its token counts and charge per
model. At the price table's Haiku 5.5 row overcode's estimate matches the
charge; at the family row Haiku 5.5 was priced by before the table, the
check reports the drift.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from overcode import conversation_spend as spend
from overcode import history_reader
from overcode.cost_check import (
    conversation_drift,
    drift_messages,
    latest_cost_state,
    spend_drift,
)
from overcode.history_reader import (
    encode_project_path,
    read_tree_stats,
    subagent_files,
    subagent_key,
)
from overcode.pricing import MODEL_PRICING, lookup_pricing
from overcode.session_manager import Session
from overcode.transcript_index import TranscriptRegistry

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "spike_spend"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text())
CAPTURES = [(capture, sid) for capture, sessions in sorted(EXPECTED.items())
            for sid in sorted(sessions)]
PROJECT = "/work/project"


@pytest.fixture(autouse=True)
def _fresh_caches():
    history_reader.clear_transcript_caches()
    yield
    history_reader.clear_transcript_caches()


def _layout(tmp_path: Path, capture: str):
    projects = tmp_path / "projects"
    project = projects / encode_project_path(PROJECT)
    shutil.copytree(FIXTURES / capture, project)
    return projects, project


def _reads(project: Path, sid: str) -> dict:
    """What the Claude reader reports per conversation for ``sid``'s tree."""
    subagents = project / sid / "subagents"
    files = [project / f"{sid}.jsonl", *subagent_files(subagents)]
    reads = read_tree_stats(files, transcripts=TranscriptRegistry())
    out = {}
    for i, (path, (totals, _)) in enumerate(zip(files, reads)):
        key = sid if i == 0 else subagent_key(sid, subagents, path)
        out[key] = spend.usage(
            totals["input_tokens"], totals["output_tokens"], totals["cache_creation_tokens"],
            totals["cache_read_tokens"], model=totals["model"] or "claude-haiku-5-5",
            cache_creation_1h_tokens=totals["cache_creation_1h_tokens"])
    return out


def _table_prices(model):
    return lookup_pricing(model).per_million()


def _family_prices(model):
    return MODEL_PRICING["haiku"].per_million()


def _session() -> Session:
    return Session(id="s1", name="a", tmux_session="agents", tmux_window="a-1", command=[],
                   start_directory=PROJECT, start_time="2026-10-15T10:00:00")


class TestCostState:
    def test_latest_cost_state_is_read_from_the_transcript(self, tmp_path):
        capture, sid = CAPTURES[0]
        _, project = _layout(tmp_path, capture)
        record = latest_cost_state(project / f"{sid}.jsonl")
        assert record == EXPECTED[capture][sid]

    def test_no_cost_state_yet(self, tmp_path):
        path = tmp_path / "t.jsonl"
        path.write_text('{"type": "user"}\n')
        assert latest_cost_state(path) is None
        assert latest_cost_state(tmp_path / "missing.jsonl") is None


class TestDrift:
    @pytest.mark.parametrize("capture,sid", CAPTURES)
    def test_price_table_matches_claude_codes_charge(self, tmp_path, capture, sid):
        _, project = _layout(tmp_path, capture)
        record = latest_cost_state(project / f"{sid}.jsonl")
        assert conversation_drift(sid, _reads(project, sid), record, _table_prices) == []

    @pytest.mark.parametrize("capture,sid", CAPTURES)
    def test_the_old_family_row_drifts(self, tmp_path, capture, sid):
        """Haiku 5.5 at Haiku 4.5's $1 / $5: ten times the charge."""
        _, project = _layout(tmp_path, capture)
        record = latest_cost_state(project / f"{sid}.jsonl")
        drifts = conversation_drift(sid, _reads(project, sid), record, _family_prices)
        charged = EXPECTED[capture][sid]["totalCostUSD"]
        if charged * 9 < 0.05:
            pytest.skip("too small to tell from side calls")
        assert [d.model for d in drifts] == ["claude-haiku-5-5"]
        assert drifts[0].ratio == pytest.approx(10.0, rel=0.05)
        assert "claude-haiku-5-5" in drift_messages(drifts)[0]

    def test_a_conversation_with_other_tokens_is_not_compared(self, tmp_path):
        capture, sid = CAPTURES[0]
        _, project = _layout(tmp_path, capture)
        record = latest_cost_state(project / f"{sid}.jsonl")
        reads = _reads(project, sid)
        reads[sid] = dict(reads[sid], cr=reads[sid]["cr"] + 500_000)  # resumed since
        assert conversation_drift(sid, reads, record, _family_prices) == []

    def test_spend_drift_finds_each_conversations_transcript(self, tmp_path):
        capture, sid = "07-team-print-mode", "28217765-c6fc-4d54-9aae-6ecbfc63728d"
        projects, project = _layout(tmp_path, capture)
        reads = _reads(project, sid)
        assert spend_drift(_session(), reads, _table_prices, projects_path=projects) == []
        drifts = spend_drift(_session(), reads, _family_prices, projects_path=projects)
        assert [d.conversation for d in drifts] == [sid]


class TestDoctorFindings:
    def test_spend_drift_is_a_doctor_warning(self, tmp_path):
        from types import SimpleNamespace
        from unittest.mock import patch

        from overcode import doctor
        from overcode.settings import UserConfig

        capture, sid = "07-team-print-mode", "28217765-c6fc-4d54-9aae-6ecbfc63728d"
        projects, project = _layout(tmp_path, capture)
        stats = SimpleNamespace(per_conversation=_reads(project, sid))
        cfg = UserConfig.from_dict(
            {"model_pricing": {"haiku": {"input": 1.0, "output": 5.0, "cache_write": 1.25,
                                         "cache_read": 0.10}}})
        with patch.object(history_reader, "CLAUDE_PROJECTS_PATH", projects), \
                patch("overcode.settings.get_user_config", return_value=cfg):
            findings = doctor.spend_findings(_session(), stats)
        assert [f.code for f in findings] == [doctor.FINDING_SPEND_DRIFT]
        with patch.object(history_reader, "CLAUDE_PROJECTS_PATH", projects), \
                patch("overcode.settings.get_user_config", return_value=UserConfig()):
            assert doctor.spend_findings(_session(), stats) == []

    def test_family_priced_claude_model_is_a_warning(self):
        from types import SimpleNamespace

        from overcode import doctor

        session = _session()
        session.backend = "opencode"  # no transcript check, only the price row
        stats = SimpleNamespace(per_conversation={
            "a": spend.usage(1, 1, model="claude-haiku-6"),
            "b": spend.usage(1, 1, model="claude-haiku-5-5")})
        findings = doctor.spend_findings(session, stats)
        assert [f.code for f in findings] == [doctor.FINDING_PRICE_GUESS]
        assert "claude-haiku-6" in findings[0].message

    def test_unmarked_model_pricing_with_a_bedrock_discount(self):
        from overcode.doctor import pricing_config_findings

        found = pricing_config_findings({
            "bedrock": {"discount": 0.3},
            "model_pricing": {"opus": {"input": 3.5}, "sonnet": {"input": 2.0, "net": True}}})
        assert len(found) == 1 and "opus" in found[0] and "sonnet" not in found[0]
        assert pricing_config_findings({"model_pricing": {"opus": {"input": 3.5}}}) == []
        assert pricing_config_findings({"bedrock": {"discount": 0.3}}) == []
