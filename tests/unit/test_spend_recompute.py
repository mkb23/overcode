"""Archived agents' spend is restated once, in bounded batches, after the reader fix.

An archived agent is never synced again, so ``spend_recompute`` reads the
transcripts of each archived Claude agent that still has them at the
current reader version, rewrites its archive record's spend in place and
writes ledger ``correction`` rows (billing totals drop by the over-count).
An agent whose transcripts are gone keeps its figures. The walk is
bounded per call, resumes from a cursor, waits for the ledger backfill and
ends with a marker; running it again changes nothing.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from overcode import conversation_spend as spend
from overcode import history_reader
from overcode.history_reader import CLAUDE_READER_VERSION, HistoryFile, encode_project_path
from overcode.ledger import CORRECTION
from overcode.session_manager import PendingUpdates, SessionManager
from overcode.spend_recompute import (
    cursor_path,
    done,
    marker_path,
    needs_restating,
    recompute_archived_spend,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "spike_spend"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text())
WORKFLOW = ("05-workflow-small", "f2e4e7d6-9467-4411-b749-c838d9497172")
DEFAULT = ("01-default-agent-call", "f885c53e-528c-4c1d-b4ef-25fa37635879")
START = datetime(2026, 10, 15, 0, 0, 0)


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith("OVERCODE_") and key not in ("OVERCODE_DIR", "OVERCODE_TMUX_SOCKET"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "sessions"))
    history_reader.clear_transcript_caches()
    yield
    history_reader.clear_transcript_caches()


@pytest.fixture
def sm():
    manager = SessionManager(skip_git_detection=True)
    manager.rollover_in_background = False
    return manager


class Claude:
    """A Claude home in the test dir: history.jsonl and project transcripts."""

    def __init__(self, root: Path):
        self.projects = root / "projects"
        self.history_path = root / "history.jsonl"
        root.mkdir(parents=True, exist_ok=True)
        self.history_path.write_text("")

    def add(self, capture: str, sid: str, project_dir: str) -> Path:
        dest = self.projects / encode_project_path(project_dir)
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / capture / f"{sid}.jsonl", dest / f"{sid}.jsonl")
        if (FIXTURES / capture / sid).is_dir():
            shutil.copytree(FIXTURES / capture / sid, dest / sid)
        with open(self.history_path, "a") as f:
            f.write(json.dumps({"display": "go", "timestamp": int(START.timestamp() * 1000) + 1,
                                "project": project_dir, "sessionId": sid}) + "\n")
        return dest

    def run(self, sm, **kw):
        return recompute_archived_spend(sm, history_file=HistoryFile(self.history_path),
                                        projects_path=self.projects, **kw)


def version_1_stats(claude: Claude, capture: str, sid: str, project_dir: str) -> dict:
    """The stats reader version 1 left on a record: every line added, no workflows."""
    from overcode.session_manager import Session

    probe = Session(id="p", name="p", tmux_session="agents", tmux_window="1", command=[],
                    start_directory=project_dir, start_time=START.isoformat())
    reads = history_reader.legacy_session_reads(
        probe, projects_path=claude.projects, history_file=HistoryFile(claude.history_path))
    entries = spend.merge(None, reads, prices=lambda m: (1.0, 5.0, 1.25, 0.1),
                          energy=lambda m, t: 0.0, default_model="claude-haiku-5-5")
    return {**spend.totals(entries), "conversations": entries}


def archive_agent(sm, name: str, project_dir: str, sid: str, stats: dict,
                  billing_code: str | None = None) -> str:
    s = sm.create_session(name=name, tmux_session="agents", tmux_window=f"{name}-1",
                          command=[], start_directory=project_dir, model="claude-haiku-5-5")
    sm.update_session(s.id, agent_session_ids=[sid], start_time=START.isoformat(),
                      **({"billing_code": billing_code} if billing_code else {}))
    sm.update_stats(s.id, **stats)
    pending = PendingUpdates()
    pending.archive_session(s.id)
    sm.commit_pending(pending)  # archive record + its final ledger row
    return s.id


def archived(sm, session_id: str) -> dict:
    return next(r for r in sm.iter_archived_records(newest_first=True) if r["id"] == session_id)


def backfill(sm) -> None:
    sm.ledger.backfill_from_archive(sm)


def test_an_archived_agent_with_transcripts_is_restated(sm, tmp_path):
    claude = Claude(tmp_path / "claude")
    capture, sid = WORKFLOW
    claude.add(capture, sid, "/proj/wf")
    old = version_1_stats(claude, capture, sid, "/proj/wf")
    agent = archive_agent(sm, "wf", "/proj/wf", sid, old)
    backfill(sm)
    before_cost = sm.ledger.totals()["wf"]["cost"]
    assert before_cost == pytest.approx(old["estimated_cost_usd"])
    lines_before = sum(1 for _ in sm.iter_archived_records())

    counts = claude.run(sm)
    assert counts == {"examined": 1, "restated": 1, "rows": 1, "done": 1}
    record = archived(sm, agent)
    want = EXPECTED[capture][sid]["modelUsage"]["claude-haiku-5-5"]
    stats = record["stats"]
    assert (stats["input_tokens"], stats["output_tokens"], stats["cache_creation_tokens"],
            stats["cache_read_tokens"]) == (want["inputTokens"], want["outputTokens"],
                                            want["cacheCreationInputTokens"],
                                            want["cacheReadInputTokens"])
    assert all(e["rv"] == CLAUDE_READER_VERSION for e in stats["conversations"].values())
    assert sum(1 for _ in sm.iter_archived_records()) == lines_before
    [row] = [r for r in sm.ledger.iter_rows() if r.get("kind") == CORRECTION]
    assert row["closing"] is True and row["sid"] == agent
    assert sm.ledger.totals()["wf"]["cost"] == pytest.approx(stats["estimated_cost_usd"])
    assert sm.ledger.totals()["wf"]["agents"] == 1
    assert done(sm) and not needs_restating(record)


def test_running_again_changes_nothing(sm, tmp_path):
    claude = Claude(tmp_path / "claude")
    capture, sid = DEFAULT
    claude.add(capture, sid, "/proj/a")
    archive_agent(sm, "a", "/proj/a", sid, version_1_stats(claude, capture, sid, "/proj/a"))
    backfill(sm)
    claude.run(sm)
    archive_bytes = {p: p.read_bytes() for p in sm.archive_dir.iterdir()}
    rows = list(sm.ledger.iter_rows())
    assert claude.run(sm) == {"examined": 0, "restated": 0, "rows": 0, "done": 1}
    marker_path(sm).unlink()  # even with the marker gone: every record reads at v2
    cursor_path(sm).unlink(missing_ok=True)
    assert claude.run(sm)["rows"] == 0
    assert list(sm.ledger.iter_rows()) == rows
    assert {p: p.read_bytes() for p in sm.archive_dir.iterdir()
            if p.name in {q.name for q in archive_bytes}} == archive_bytes


def test_an_agent_whose_transcripts_are_gone_keeps_its_figures(sm, tmp_path):
    claude = Claude(tmp_path / "claude")
    capture, sid = DEFAULT
    claude.add(capture, sid, "/proj/a")
    old = version_1_stats(claude, capture, sid, "/proj/a")
    agent = archive_agent(sm, "a", "/proj/a", sid, old)
    backfill(sm)
    shutil.rmtree(claude.projects)  # Claude Code's cleanupPeriodDays
    counts = claude.run(sm)
    assert counts["examined"] == 1 and counts["restated"] == 0 and counts["done"] == 1
    assert archived(sm, agent)["stats"]["input_tokens"] == old["input_tokens"]
    assert not [r for r in sm.ledger.iter_rows() if r.get("kind") == CORRECTION]


def test_a_record_with_totals_only_loses_exactly_its_over_count(sm, tmp_path):
    """A 0.5.x record: totals, no entries; one transcript gone, one still there."""
    claude = Claude(tmp_path / "claude")
    capture, sid = DEFAULT
    claude.add(capture, sid, "/proj/a")
    present = version_1_stats(claude, capture, sid, "/proj/a")
    pruned_tokens = 5_000
    stats = {k: present[k] for k in ("input_tokens", "output_tokens",
                                      "cache_creation_tokens", "cache_read_tokens")}
    stats["input_tokens"] += pruned_tokens
    stats["total_tokens"] = sum(stats.values())
    stats["estimated_cost_usd"] = round(present["estimated_cost_usd"] + pruned_tokens / 1e6, 4)
    agent = archive_agent(sm, "a", "/proj/a", sid, stats)
    backfill(sm)
    claude.run(sm)
    record = archived(sm, agent)["stats"]
    want = EXPECTED[capture][sid]["modelUsage"]["claude-haiku-5-5"]
    assert record["input_tokens"] == want["inputTokens"] + pruned_tokens
    assert record["cache_read_tokens"] == want["cacheReadInputTokens"]
    assert record["conversations"][spend.LEGACY_KEY]["in"] == pruned_tokens


def test_bounded_batches_resume_from_the_cursor(sm, tmp_path):
    claude = Claude(tmp_path / "claude")
    agents = []
    for i, (capture, sid) in enumerate([DEFAULT, WORKFLOW]):
        claude.add(capture, sid, f"/proj/{i}")
        agents.append(archive_agent(sm, f"a{i}", f"/proj/{i}", sid,
                                    version_1_stats(claude, capture, sid, f"/proj/{i}")))
    backfill(sm)
    first = claude.run(sm, batch=1)
    assert (first["examined"], first["done"]) == (1, 0)
    assert json.loads(cursor_path(sm).read_text())["line"] >= 1
    second = claude.run(sm, batch=1)
    assert second["examined"] == 1
    third = claude.run(sm, batch=1)
    assert third["done"] == 1
    assert all(not needs_restating(archived(sm, a)) for a in agents)


def test_it_waits_for_the_ledger_backfill(sm, tmp_path):
    claude = Claude(tmp_path / "claude")
    capture, sid = DEFAULT
    claude.add(capture, sid, "/proj/a")
    archive_agent(sm, "a", "/proj/a", sid, version_1_stats(claude, capture, sid, "/proj/a"))
    sm.ledger.invalidate_backfill()
    assert claude.run(sm) == {"examined": 0, "restated": 0, "rows": 0, "done": 0}


def test_a_closed_gzipped_month_is_rewritten_whole(sm, tmp_path):
    claude = Claude(tmp_path / "claude")
    capture, sid = DEFAULT
    claude.add(capture, sid, "/proj/a")
    agent = archive_agent(sm, "a", "/proj/a", sid,
                          version_1_stats(claude, capture, sid, "/proj/a"))
    # Move this month's file to an old month and gzip it
    [(month, path)] = sm.archive_months()
    old = path.with_name("2026-01.jsonl")
    path.rename(old)
    sm.gzip_closed_months(current=month)
    assert [p.name for _, p in sm.archive_months()] == ["2026-01.jsonl.gz"]
    backfill(sm)
    assert claude.run(sm)["restated"] == 1
    assert [p.name for _, p in sm.archive_months()] == ["2026-01.jsonl.gz"]
    assert not needs_restating(archived(sm, agent))


def test_other_backends_are_left_alone():
    record = {"id": "x", "backend": "codex", "stats": {"input_tokens": 10}}
    assert not needs_restating(record)
    assert needs_restating({"id": "y", "stats": {"input_tokens": 10}})
    assert not needs_restating({"id": "z", "stats": {"input_tokens": 0}})
