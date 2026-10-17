"""A forked Claude conversation does not bill the history it copied.

Claude Code 2.1.302 (live, 2026-10-17): ``claude --bg --resume X "..."``
appends to X's transcript under X's id, but ``--fork-session`` (and
``/bg`` from an interactive session, ``/branch``, ``overcode fork``) writes
a new conversation whose transcript starts with a copy of X's whole
history: the same ``message.id``s, timestamps and usage. That history was
spent once, by X; the fork's own spend starts after it.

The fixtures (``tests/fixtures/bg_fork``, built by its ``generate.py``)
reproduce that shape with synthetic ids: ORIGINAL holds the ALPHA and
BRAVO replies, FORK a copy of both and then its own CHARLIE reply.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from overcode import claude_bg
from overcode import conversation_spend as spend
from overcode import history_reader
from overcode.claude_bg import FleetAgent
from overcode.history_reader import (
    CLAUDE_READER_VERSION,
    HistoryFile,
    encode_project_path,
    get_session_stats,
    read_tree_stats,
)
from overcode.session_manager import Session
from overcode.transcript_index import TranscriptIndex, TranscriptRegistry

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "bg_fork"
ORIGINAL = "0f0f0f0f-0000-4000-8000-00000000a1fa"
FORK = "0f0f0f0f-0000-4000-8000-0000000c4a71"
KEYS = ("input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens")
# The fixture replies' usage: (in, out, cache write, cache read)
ALPHA = (2, 7, 10288, 11228)
BRAVO = (2, 7, 24157, 14314)
CHARLIE = (2, 8, 16981, 21537)
PROJECT = "/work/repo"
START = datetime(2026, 10, 16, 0, 0, 0)  # before every message, in any zone

pytestmark = pytest.mark.unit


def usage_of(*messages) -> dict:
    return {key: sum(m[i] for m in messages) for i, key in enumerate(KEYS)}


def tokens(totals) -> dict:
    return {key: totals[key] for key in KEYS}


@pytest.fixture(autouse=True)
def _fresh_caches():
    history_reader.clear_transcript_caches()
    yield
    history_reader.clear_transcript_caches()


class Claude:
    """A Claude home under the test dir: history.jsonl and one project's transcripts."""

    def __init__(self, root: Path):
        self.projects = root / "projects"
        self.dir = self.projects / encode_project_path(PROJECT)
        self.dir.mkdir(parents=True)
        self.history_path = root / "history.jsonl"
        self.history_path.write_text("")
        self.minute = 0

    def add(self, name: str, sid: str) -> Path:
        """A transcript from the fixtures, and its first prompt in history."""
        path = self.dir / f"{sid}.jsonl"
        shutil.copy(FIXTURES / f"{name}.jsonl", path)
        self.minute += 1
        with open(self.history_path, "a") as f:
            f.write(json.dumps({"display": "go", "project": PROJECT, "sessionId": sid,
                                "timestamp": int(START.timestamp() * 1000) + self.minute * 60_000}) + "\n")
        return path

    def stats(self, session, **kw):
        return get_session_stats(session, history_path=self.history_path,
                                 projects_path=self.projects,
                                 history_file=HistoryFile(self.history_path), **kw)


def agent(*sids, name="alpha", **kw) -> Session:
    return Session(id=f"id-{name}", name=name, tmux_session="agents", tmux_window="1",
                   command=[], start_directory=PROJECT, start_time=START.isoformat(),
                   agent_session_ids=list(sids), **kw)


@pytest.fixture
def claude(tmp_path):
    return Claude(tmp_path / "claude")


class TestFixtureShape:
    def test_the_fork_copies_the_original_s_message_ids(self):
        def ids(name):
            return [json.loads(line)["message"]["id"]
                    for line in (FIXTURES / f"{name}.jsonl").read_text().splitlines()
                    if json.loads(line).get("type") == "assistant"]

        assert ids("fork")[:2] == ids("original")
        assert len(ids("fork")) == 3

    def test_each_file_alone_counts_every_message(self, claude):
        original, fork = claude.add("original", ORIGINAL), claude.add("fork", FORK)
        assert tokens(read_tree_stats([original])[0][0]) == usage_of(ALPHA, BRAVO)
        # Read alone, the fork bills the copied history again: the double count
        assert tokens(read_tree_stats([fork])[0][0]) == usage_of(ALPHA, BRAVO, CHARLIE)


class TestForkPrefix:
    def test_the_copied_prefix_is_found_and_left_out(self, claude):
        original, fork = claude.add("original", ORIGINAL), claude.add("fork", FORK)
        registry = TranscriptRegistry()
        registry.stats(original)
        [(totals, _)] = read_tree_stats([fork], transcripts=registry, earlier=[original])
        assert registry.fork_prefix(fork, [original]) == 2
        assert tokens(totals) == usage_of(CHARLIE)
        assert totals["cache_creation_1h_tokens"] == CHARLIE[2]

    def test_a_conversation_that_is_not_a_fork_keeps_everything(self, claude, tmp_path):
        original, fork = claude.add("original", ORIGINAL), claude.add("fork", FORK)
        other = tmp_path / "other.jsonl"
        other.write_text(json.dumps({"type": "assistant", "timestamp": "2026-10-16T20:00:00Z",
                                     "message": {"id": "msg_other", "usage": {"input_tokens": 5}}}) + "\n")
        registry = TranscriptRegistry()
        assert registry.fork_prefix(original, [other]) == 0
        assert registry.fork_prefix(other, [original, fork]) == 0
        # The original never loses its own history to its fork
        [(totals, _)] = read_tree_stats([original], transcripts=registry, earlier=[other])
        assert tokens(totals) == usage_of(ALPHA, BRAVO)

    def test_a_missing_original_leaves_the_read_as_it_was(self, claude, tmp_path):
        fork = claude.add("fork", FORK)
        [(totals, _)] = read_tree_stats([fork], earlier=[tmp_path / "gone.jsonl"])
        assert tokens(totals) == usage_of(ALPHA, BRAVO, CHARLIE)

    def test_a_fork_with_no_message_of_its_own_yet_then_one(self, claude):
        original = claude.add("original", ORIGINAL)
        lines = (FIXTURES / "fork.jsonl").read_text().splitlines(keepends=True)
        reply = next(i for i, line in enumerate(lines)
                     if '"msg_synthetic_3"' in line)
        fork = claude.dir / f"{FORK}.jsonl"
        fork.write_text("".join(lines[:reply]))  # just forked: the copy only
        registry = TranscriptRegistry()
        [(totals, _)] = read_tree_stats([fork], transcripts=registry, earlier=[original])
        assert tokens(totals) == usage_of()
        with open(fork, "a") as f:
            f.write("".join(lines[reply:]))
        [(totals, _)] = read_tree_stats([fork], transcripts=registry, earlier=[original])
        assert tokens(totals) == usage_of(CHARLIE)

    def test_a_settled_answer_asks_the_original_nothing(self, claude, monkeypatch):
        original, fork = claude.add("original", ORIGINAL), claude.add("fork", FORK)
        registry = TranscriptRegistry()
        read_tree_stats([fork], transcripts=registry, earlier=[original])
        monkeypatch.setattr(TranscriptIndex, "holds",
                            lambda *a, **k: pytest.fail("asked the original again"))
        for _ in range(3):
            [(totals, _)] = read_tree_stats([fork], transcripts=registry, earlier=[original])
            assert tokens(totals) == usage_of(CHARLIE)

    def test_a_rewritten_fork_is_checked_again(self, claude):
        original, fork = claude.add("original", ORIGINAL), claude.add("fork", FORK)
        registry = TranscriptRegistry()
        read_tree_stats([fork], transcripts=registry, earlier=[original])
        # Replaced by a conversation of its own (a new inode, no copy)
        own = [line for line in (FIXTURES / "fork.jsonl").read_text().splitlines(keepends=True)
               if '"msg_synthetic_1"' not in line and '"msg_synthetic_2"' not in line]
        tmp = fork.with_suffix(".tmp")
        tmp.write_text("".join(own))
        os.replace(tmp, fork)
        [(totals, _)] = read_tree_stats([fork], transcripts=registry, earlier=[original])
        assert registry.fork_prefix(fork, [original]) == 0
        assert tokens(totals) == usage_of(CHARLIE)

    def test_since_takes_off_only_what_it_counted(self, claude):
        original, fork = claude.add("original", ORIGINAL), claude.add("fork", FORK)
        # A since between BRAVO and CHARLIE: the copies were not counted at all
        since = datetime.fromisoformat("2026-10-16T23:56:25+00:00").astimezone().replace(tzinfo=None)
        [(totals, _)] = read_tree_stats([fork], since=since, earlier=[original])
        assert tokens(totals) == usage_of(CHARLIE)


class TestAgentFork:
    """An agent's own conversations: ``/branch``, ``--fork-session``, ``/resume`` + fork."""

    def test_each_message_counts_once_across_its_conversations(self, claude):
        claude.add("original", ORIGINAL)
        claude.add("fork", FORK)
        stats = claude.stats(agent(ORIGINAL, FORK))
        assert stats.per_conversation[ORIGINAL]["in"] == ALPHA[0] + BRAVO[0]
        assert stats.per_conversation[FORK] == spend.usage(
            *CHARLIE, model="claude-haiku-5-5", cache_creation_1h_tokens=CHARLIE[2],
            rv=CLAUDE_READER_VERSION)
        assert {k: getattr(stats, k) for k in KEYS} == usage_of(ALPHA, BRAVO, CHARLIE)

    def test_the_order_conversations_began_in_decides_not_the_id_list(self, claude):
        claude.add("original", ORIGINAL)
        claude.add("fork", FORK)
        stats = claude.stats(agent(FORK, ORIGINAL))
        assert stats.per_conversation[FORK]["cr"] == CHARLIE[3]
        assert stats.per_conversation[ORIGINAL]["cr"] == ALPHA[3] + BRAVO[3]

    def test_a_restart_reads_the_same_and_bills_nothing_more(self, claude):
        claude.add("original", ORIGINAL)
        claude.add("fork", FORK)
        session = agent(ORIGINAL, FORK)
        kw = dict(prices=lambda m: (0.10, 0.50, 0.125, 0.01, 0.20), energy=lambda m, t: 0.0)
        entries = spend.merge(None, claude.stats(session).per_conversation, **kw)
        history_reader.clear_transcript_caches()  # the daemon restarts: every index rebuilt
        again = spend.merge(entries, claude.stats(session).per_conversation, **kw)
        assert again == entries

    def test_entries_read_before_the_fix_are_restated_down(self, claude):
        """The double count already on record (reader version 2) is replaced
        by the next read, which the daemon books as a ledger correction."""
        claude.add("original", ORIGINAL)
        claude.add("fork", FORK)
        kw = dict(prices=lambda m: (0.10, 0.50, 0.125, 0.01, 0.20), energy=lambda m, t: 0.0)
        def read_v2(*messages):
            counts = usage_of(*messages)
            return spend.usage(*counts.values(), model="claude-haiku-5-5", rv=2,
                               cache_creation_1h_tokens=counts["cache_creation_tokens"])

        old = spend.merge(None, {ORIGINAL: read_v2(ALPHA, BRAVO),
                                 FORK: read_v2(ALPHA, BRAVO, CHARLIE)}, **kw)
        new = spend.merge(old, claude.stats(agent(ORIGINAL, FORK)).per_conversation, **kw)
        assert set(spend.replaced(old, new)) == {ORIGINAL, FORK}
        assert new[FORK]["cr"] == CHARLIE[3]
        assert new[FORK]["cost"] < old[FORK]["cost"]
        assert new[ORIGINAL]["cost"] == pytest.approx(old[ORIGINAL]["cost"])


class TestChildFork:
    """``overcode fork``: a child whose conversation is a copy of its parent's."""

    def test_the_parent_s_history_is_not_billed_to_the_child(self, claude):
        claude.add("original", ORIGINAL)
        claude.add("fork", FORK)
        child = agent(FORK, name="kid")
        assert claude.stats(child).per_conversation[FORK]["cr"] == sum(
            m[3] for m in (ALPHA, BRAVO, CHARLIE))  # the copy, without its source
        stats = claude.stats(child, fork_sources=[ORIGINAL])
        assert {k: getattr(stats, k) for k in KEYS} == usage_of(CHARLIE)

    def test_the_daemon_passes_the_parent_s_conversations(self, tmp_path, monkeypatch):
        from overcode.daemon_stats_sync import StatsSync, parent_conversations
        from overcode.session_manager import SessionManager

        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state"))
        sm = SessionManager(state_dir=tmp_path / "state", skip_git_detection=True)
        parent = sm.create_session(name="mom", tmux_session="agents", tmux_window="m",
                                   command=["claude"], start_directory=PROJECT)
        sm.update_session(parent.id, agent_session_ids=[ORIGINAL])
        sm.update_stats(parent.id, conversations={
            ORIGINAL: {"in": 1}, f"{ORIGINAL}/agent-x": {"in": 1}, spend.LEGACY_KEY: {"in": 1}})
        kid = sm.create_session(name="kid", tmux_session="agents", tmux_window="k",
                                command=["claude"], start_directory=PROJECT)
        sm.update_session(kid.id, parent_session_id=parent.id, agent_session_ids=[FORK])
        kid = sm.get_session(kid.id)
        assert StatsSync(session_manager=sm, log=None).fork_sources(kid) == [ORIGINAL]
        assert parent_conversations(sm.get_session(parent.id), sm) == []
        # An archived parent's are found in the archive, once
        sm.delete_session(parent.id, archive=True)
        cache: dict = {}
        assert parent_conversations(kid, sm, cache) == [ORIGINAL]
        monkeypatch.setattr(sm, "newest_archived_records", lambda ids: pytest.fail("again"))
        assert parent_conversations(kid, sm, cache) == [ORIGINAL]


class TestBackgroundFork:
    """A 🌙 row forked from its agent's conversation (``/bg``, ``--fork-session``)."""

    def job(self, transcript: Path, **extra) -> dict:
        data = {"sessionId": FORK, "resumeSessionId": FORK, "linkScanPath": str(transcript),
                "cwd": PROJECT, "respawnFlags": ["--model", "haiku"]}
        data.update(extra)
        return data

    def test_its_spend_is_only_what_it_added(self, claude):
        original, fork = claude.add("original", ORIGINAL), claude.add("fork", FORK)
        parent = FleetAgent(id="a1", name="alpha", conversations=frozenset({ORIGINAL}))
        origins = claude_bg.fork_origins(self.job(fork), parent)
        assert origins == (original,)
        value = claude_bg.read_spend(self.job(fork), origins)
        assert tokens(value) == usage_of(CHARLIE)
        alone = claude_bg.read_spend(self.job(fork))
        assert value["cost_usd"] < alone["cost_usd"]

    def test_the_conversation_it_resumed_and_earlier_sessions_are_origins(self, claude):
        fork = claude.add("fork", FORK)
        origins = claude_bg.fork_origins(self.job(fork, resumeSessionId=ORIGINAL), None,
                                         ["earlier-bg"])
        assert origins == (claude.dir / f"{ORIGINAL}.jsonl", claude.dir / "earlier-bg.jsonl")

    def test_the_ledger_bills_only_the_new_message(self, claude, tmp_path, monkeypatch):
        from overcode.claude_bg_ledger import BgLedger
        from overcode.ledger import Ledger

        original, fork = claude.add("original", ORIGINAL), claude.add("fork", FORK)
        parent = FleetAgent(id="a1", name="alpha", conversations=frozenset({ORIGINAL}),
                            directory=PROJECT, billing_code="proj-x")
        jobs = {FORK[:8]: dict(self.job(fork), state="working", tempo="active",
                               createdAt="2026-10-16T23:56:27Z", updatedAt="2026-10-16T23:56:34Z")}
        now = datetime(2026, 10, 17, 1, 0).timestamp()
        rows, _ = claude_bg.build_rows(jobs, [parent], now, 30.0)
        spend_now = claude_bg.BgSpend()
        spend_now.sync(claude_bg.row_reads(rows, {FORK: jobs[FORK[:8]]}, [parent]), now, 60.0)
        rows, _ = claude_bg.build_rows(jobs, [parent], now, 30.0, spend=spend_now.values)
        ledger = Ledger(tmp_path / "reg")
        writer = BgLedger(lambda: ledger.dir, owner="agents")
        assert writer.sync(rows, [parent], ledger, now) == 1
        [row] = [r for r in ledger.iter_rows() if r.get("claude_bg")]
        assert row["tokens"] == sum(CHARLIE)
        assert row["cost"] == pytest.approx(
            claude_bg.read_spend(self.job(fork), (original,))["cost_usd"])
        assert ledger.totals(save=False)["proj-x"]["tokens"] == sum(CHARLIE)


class TestBgLedgerCorrection:
    """Spend billed before the fix (the copy included) is restated by a correction row."""

    class _Row:
        def __init__(self, cost, tokens, ended=None):
            self.state = SimpleNamespace(
                session_id=f"claude-bg:{FORK}", name="probe", model="claude-haiku-5-5",
                estimated_cost_usd=cost, input_tokens=tokens, output_tokens=0,
                cache_creation_tokens=0, cache_read_tokens=0,
                claude_bg={"parent_id": "a1", "session_id": FORK})
            self.light = SimpleNamespace(ended_at=ended)

    AGENTS = (FleetAgent(id="a1", name="alpha", billing_code="code-a"),)

    def _ledger(self, tmp_path):
        from overcode.claude_bg_ledger import BgLedger
        from overcode.ledger import Ledger

        ledger = Ledger(tmp_path / "reg")
        return ledger, BgLedger(lambda: ledger.dir, owner="agents", checkpoint_seconds=lambda: 0.0)

    def test_an_open_segment_is_scaled_down(self, tmp_path):
        from overcode.ledger import CORRECTION

        ledger, writer = self._ledger(tmp_path)
        now = datetime(2026, 10, 17, 1, 0).timestamp()
        writer.sync([self._Row(3.0, 300)], self.AGENTS, ledger, now)
        assert writer.sync([self._Row(1.0, 100)], self.AGENTS, ledger, now + 1) == 1
        rows = list(ledger.iter_rows())
        assert [r["kind"] for r in rows] == ["checkpoint", CORRECTION]
        assert rows[-1]["closing"] is False and rows[-1]["claude_bg"] is True
        totals = ledger.totals(save=False)["code-a"]
        assert totals["cost"] == pytest.approx(1.0) and totals["tokens"] == 100
        # It spends again from there: a checkpoint, no second correction
        writer.sync([self._Row(1.5, 150)], self.AGENTS, ledger, now + 2)
        assert [r["kind"] for r in ledger.iter_rows()][-1] == "checkpoint"
        assert ledger.totals(save=False)["code-a"]["cost"] == pytest.approx(1.5)

    def test_a_closed_segment_s_share_is_replaced(self, tmp_path):
        from overcode.ledger import CORRECTION

        ledger, writer = self._ledger(tmp_path)
        now = datetime(2026, 10, 17, 1, 0).timestamp()
        writer.sync([self._Row(3.0, 300, ended=now)], self.AGENTS, ledger, now)
        assert ledger.totals(save=False)["code-a"]["cost"] == pytest.approx(3.0)
        writer.sync([self._Row(1.0, 100, ended=now)], self.AGENTS, ledger, now + 1)
        rows = list(ledger.iter_rows())
        assert [r["kind"] for r in rows] == ["final", CORRECTION]
        assert rows[-1]["closing"] is True
        totals = ledger.totals(save=False)["code-a"]
        assert totals["cost"] == pytest.approx(1.0) and totals["agents"] == 1
        # Read the same again (a restart): nothing more
        assert writer.sync([self._Row(1.0, 100, ended=now)], self.AGENTS, ledger, now + 2) == 0


class TestArchived:
    """An archived agent's double count is restated once (``spend_recompute``)."""

    @pytest.fixture
    def sm(self, tmp_path, monkeypatch):
        from overcode.session_manager import SessionManager

        for key in list(os.environ):
            if key.startswith("OVERCODE_") and key not in ("OVERCODE_DIR", "OVERCODE_TMUX_SOCKET"):
                monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "sessions"))
        manager = SessionManager(skip_git_detection=True)
        manager.rollover_in_background = False
        return manager

    def test_the_fork_s_copy_is_taken_off_once_with_a_correction(self, sm, claude):
        from overcode.ledger import CORRECTION
        from overcode.session_manager import PendingUpdates
        from overcode.spend_recompute import recompute_archived_spend

        claude.add("original", ORIGINAL)
        claude.add("fork", FORK)
        kw = dict(prices=lambda m: (0.10, 0.50, 0.125, 0.01, 0.20), energy=lambda m, t: 0.0)

        def read_v2(*messages):
            counts = usage_of(*messages)
            return spend.usage(*counts.values(), model="claude-haiku-5-5", rv=2,
                               cache_creation_1h_tokens=counts["cache_creation_tokens"])

        entries = spend.merge(None, {ORIGINAL: read_v2(ALPHA, BRAVO),
                                     FORK: read_v2(ALPHA, BRAVO, CHARLIE)}, **kw)
        s = sm.create_session(name="alpha", tmux_session="agents", tmux_window="a-1",
                              command=[], start_directory=PROJECT, model="claude-haiku-5-5")
        sm.update_session(s.id, agent_session_ids=[ORIGINAL, FORK], start_time=START.isoformat())
        sm.update_stats(s.id, **spend.totals(entries), conversations=entries)
        pending = PendingUpdates()
        pending.archive_session(s.id)
        sm.commit_pending(pending)
        sm.ledger.backfill_from_archive(sm)
        before = sm.ledger.totals()["alpha"]["cost"]

        def run():
            return recompute_archived_spend(sm, history_file=HistoryFile(claude.history_path),
                                            projects_path=claude.projects)

        assert run()["rows"] == 1
        record = next(r for r in sm.iter_archived_records(newest_first=True) if r["id"] == s.id)
        stats = record["stats"]
        assert {k: stats[k] for k in KEYS} == usage_of(ALPHA, BRAVO, CHARLIE)
        assert stats["conversations"][FORK]["cr"] == CHARLIE[3]
        [row] = [r for r in sm.ledger.iter_rows() if r.get("kind") == CORRECTION]
        assert row["closing"] is True
        after = sm.ledger.totals()["alpha"]["cost"]
        assert after < before
        assert after == pytest.approx(stats["estimated_cost_usd"])
        # Again: the record reads at the new version, nothing is billed again
        from overcode.spend_recompute import cursor_path, marker_path

        marker_path(sm).unlink()
        cursor_path(sm).unlink(missing_ok=True)
        assert run()["rows"] == 0
        assert sm.ledger.totals()["alpha"]["cost"] == pytest.approx(after)
