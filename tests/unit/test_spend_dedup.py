"""Claude spend counts each message once, matching Claude Code's own cost-state.

The fixtures (``tests/fixtures/spike_spend``, built by its ``generate.py``)
are the spike's scrubbed transcript skeletons of Claude Code 2.1.300: no
content, only record shapes, ids, timestamps and usage, plus the
``cost-state`` record Claude Code wrote when each session ended. Claude
writes one transcript line per content block, each repeating the whole
message's usage; workflow agents sit in ``subagents/workflows/<run>/``; a
fork opens with a copy of its parent's last message. Reading each message
once across a conversation and all its subagents gives cost-state's
numbers exactly (print-mode runs), and, priced with 1-hour cache writes at
twice the input price, cost-state's cost.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from overcode import conversation_spend as spend
from overcode import history_reader
from overcode.history_reader import (
    CLAUDE_READER_VERSION,
    _read_session_file_stats_full,
    _read_window_token_usage_full,
    _tree_copies,
    encode_project_path,
    get_session_stats,
    get_session_window_token_usage,
    read_tree_stats,
    subagent_files,
    subagent_key,
)
from overcode.session_manager import Session
from overcode.transcript_index import HEAD_IDS, RECENT_IDS, TranscriptIndex, TranscriptRegistry

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "spike_spend"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text())
MODEL = "claude-haiku-5-5"
# claude-haiku-5-5's per-million prices as Claude Code's cost-state charges
# them (fitted exactly to the 13 cost-state records): input, output, cache
# write (5 min, 1.25x input), cache read, cache write (1 hour, 2x input)
HAIKU_55 = (0.10, 0.50, 0.125, 0.01, 0.20)
# Print-mode runs: every token is in a transcript. Interactive runs also
# make side calls (session naming) that are never written to one.
PRINT_MODE = [
    ("01-default-agent-call", "f885c53e-528c-4c1d-b4ef-25fa37635879"),
    ("02-foreground-subagent", "26e63624-b436-4c93-89e3-d6309967a834"),
    ("03-background-subagent-with-SendMessage", "639573ba-1f23-464c-81a9-4854833300f1"),
    ("04-fork", "4a179f66-13f6-48d9-b295-ffd253f1b709"),
    ("04-fork", "ee4f2e68-8c20-40a0-bb7d-3b7a47b70f08"),
    ("05-workflow-small", "f2e4e7d6-9467-4411-b749-c838d9497172"),
    ("06-workflow-slow-live-watch", "8e8e6344-3a6e-41fa-b105-48f622b02c67"),
    ("07-team-print-mode", "28217765-c6fc-4d54-9aae-6ecbfc63728d"),
]
INTERACTIVE = [
    ("08-team-in-process", "bc0ccc1e-f5d9-4330-b922-702f00f95c48"),
    ("09-team-tmux-teammate", "13e5bccd-e603-4a34-b3e1-e333941eb92a"),
    ("09-team-tmux-teammate", "23aae024-f22d-47e9-85db-25947339c429"),
    ("10-background-session", "b32fbee4-2c5e-435f-a55e-a2a6da6185ba"),
    ("11-interactive-session-registry", "3e6a6fd1-ca05-4dbe-b6c9-5dfb7bc3cd0b"),
]
FORK = ("04-fork", "ee4f2e68-8c20-40a0-bb7d-3b7a47b70f08")
WORKFLOW = ("05-workflow-small", "f2e4e7d6-9467-4411-b749-c838d9497172")
KEYS = ("input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens")
COST_STATE_KEYS = ("inputTokens", "outputTokens", "cacheCreationInputTokens",
                   "cacheReadInputTokens")


@pytest.fixture(autouse=True)
def _fresh_caches():
    history_reader.clear_transcript_caches()
    yield
    history_reader.clear_transcript_caches()


def cost_state(capture: str, sid: str) -> dict:
    return EXPECTED[capture][sid]["modelUsage"][MODEL]


def copy_capture(capture: str, dest: Path) -> Path:
    shutil.copytree(FIXTURES / capture, dest)
    return dest


def tree(project: Path, sid: str):
    return [project / f"{sid}.jsonl", *subagent_files(project / sid / "subagents")]


def summed(reads) -> dict:
    out = dict.fromkeys(KEYS + ("cache_creation_1h_tokens",), 0)
    for totals, _ in reads:
        for key in out:
            out[key] += totals[key]
    return out


def priced(reads) -> float:
    return sum(spend.price(HAIKU_55, {
        "in": t["input_tokens"], "out": t["output_tokens"], "cw": t["cache_creation_tokens"],
        "cr": t["cache_read_tokens"], "cw1h": t["cache_creation_1h_tokens"]})
        for t, _ in reads)


def naive(files) -> dict:
    """What reader version 1 added up: every assistant line."""
    out = dict.fromkeys(KEYS, 0)
    for path in files:
        for line in path.read_text().splitlines():
            record = json.loads(line)
            if record.get("type") == "assistant":
                usage = record["message"]["usage"]
                out["input_tokens"] += usage["input_tokens"]
                out["output_tokens"] += usage["output_tokens"]
                out["cache_creation_tokens"] += usage["cache_creation_input_tokens"]
                out["cache_read_tokens"] += usage["cache_read_input_tokens"]
    return out


class TestMatchesCostState:
    @pytest.mark.parametrize("capture,sid", PRINT_MODE)
    def test_tokens_and_cost_equal_claude_code_own_count(self, tmp_path, capture, sid):
        project = copy_capture(capture, tmp_path / "p")
        reads = read_tree_stats(tree(project, sid), transcripts=TranscriptRegistry())
        got = summed(reads)
        want = cost_state(capture, sid)
        assert [got[k] for k in KEYS] == [want[k] for k in COST_STATE_KEYS]
        assert priced(reads) == pytest.approx(want["costUSD"], abs=1e-9)

    @pytest.mark.parametrize("capture,sid", INTERACTIVE)
    def test_interactive_runs_match_on_cache_and_never_exceed(self, tmp_path, capture, sid):
        project = copy_capture(capture, tmp_path / "p")
        got = summed(read_tree_stats(tree(project, sid), transcripts=TranscriptRegistry()))
        want = cost_state(capture, sid)
        assert got["cache_creation_tokens"] == want["cacheCreationInputTokens"]
        assert got["cache_read_tokens"] == want["cacheReadInputTokens"]
        assert got["input_tokens"] <= want["inputTokens"]
        assert got["output_tokens"] <= want["outputTokens"]

    @pytest.mark.parametrize("capture,sid", PRINT_MODE)
    def test_version_1_over_counted(self, tmp_path, capture, sid):
        project = copy_capture(capture, tmp_path / "p")
        files = tree(project, sid)
        old_files = [f for f in files if f.parent.name == "subagents" or f.parent == project]
        want = cost_state(capture, sid)
        old = naive(old_files)
        # Every multi-block message counted once per block (and workflow
        # agents not at all): never the right figure
        assert [old[k] for k in KEYS] != [want[k] for k in COST_STATE_KEYS]

    def test_each_file_matches_the_full_parse(self, tmp_path):
        for capture, sid in PRINT_MODE + INTERACTIVE:
            project = copy_capture(capture, tmp_path / f"{capture}-{sid[:8]}")
            for path in tree(project, sid):
                index = TranscriptIndex(str(path))
                for since in (None, datetime(2026, 10, 15, 0, 0)):
                    assert index.stats(since) == _read_session_file_stats_full(path, since)
                since = datetime(2026, 10, 14)
                assert index.window_usage(since) == _read_window_token_usage_full(path, since)


class TestScenarios:
    def test_multi_block_messages_count_once_in_one_file(self, tmp_path):
        project = copy_capture("01-default-agent-call", tmp_path / "p")
        parent = project / "f885c53e-528c-4c1d-b4ef-25fa37635879.jsonl"
        ids = [json.loads(line)["message"]["id"] for line in parent.read_text().splitlines()
               if json.loads(line).get("type") == "assistant"]
        assert len(ids) > len(set(ids))  # several lines per message
        totals, _ = history_reader.read_session_file_stats(parent)
        assert totals["output_tokens"] < naive([parent])["output_tokens"]

    def test_background_subagent_is_its_own_conversation(self, tmp_path):
        capture, sid = "03-background-subagent-with-SendMessage", PRINT_MODE[2][1]
        project = copy_capture(capture, tmp_path / "p")
        files = tree(project, sid)
        assert [f.name for f in files[1:]] == ["agent-aa771e1c281ee06c7.jsonl"]
        assert subagent_key(sid, project / sid / "subagents", files[1]) == (
            f"{sid}/agent-aa771e1c281ee06c7")

    def test_workflow_agents_are_found_and_keyed_by_run(self, tmp_path):
        capture, sid = WORKFLOW
        project = copy_capture(capture, tmp_path / "p")
        subagents = project / sid / "subagents"
        files = subagent_files(subagents)
        assert len(files) == 3
        assert {subagent_key(sid, subagents, f).rsplit("/", 1)[0] for f in files} == {
            f"{sid}/workflows/wf_77be7caa-3b5"}

    def test_fork_copy_counts_in_the_parent_only(self, tmp_path):
        capture, sid = FORK
        project = copy_capture(capture, tmp_path / "p")
        files = tree(project, sid)
        registry = TranscriptRegistry()
        reads = read_tree_stats(files, transcripts=registry)
        copies = _tree_copies(files, registry)
        assert list(copies) == [1] and len(copies[1]) == 1
        _key, usage = copies[1][0]
        fork_alone = registry.stats(files[1])[0]
        assert reads[1][0]["output_tokens"] == fork_alone["output_tokens"] - usage[1]
        assert reads[0][0] == registry.stats(files[0])[0]  # the parent keeps it

    def test_fork_found_late_still_counts_once(self, tmp_path):
        """The fork's file appears after its parent's original is far back."""
        capture, sid = FORK
        project = copy_capture(capture, tmp_path / "p")
        files = tree(project, sid)
        fork_text = files[1].read_text()
        files[1].unlink()
        parent_text = files[0].read_text()
        # The parent goes on for a long time after the fork's message
        filler = "".join(json.dumps({
            "type": "assistant", "timestamp": "2026-10-15T11:00:00.000Z",
            "message": {"id": f"msg_filler_{i}", "model": MODEL,
                        "usage": {"input_tokens": 1, "output_tokens": 1}}}) + "\n"
            for i in range(RECENT_IDS * 2))
        files[0].write_text(parent_text + filler)
        registry = TranscriptRegistry()
        read_tree_stats([files[0]], transcripts=registry)
        files[1].write_text(fork_text)
        got = summed(read_tree_stats(files, transcripts=registry))
        want = cost_state(capture, sid)
        assert got["cache_creation_tokens"] == want["cacheCreationInputTokens"]
        assert got["input_tokens"] == want["inputTokens"] + RECENT_IDS * 2
        assert got["output_tokens"] == want["outputTokens"] + RECENT_IDS * 2

    def test_compaction_copies_are_skipped_and_its_own_calls_counted(self, tmp_path):
        def line(mid, inp, meta=False):
            record = {"type": "assistant", "timestamp": "2026-10-15T10:00:00.000Z",
                      "message": {"id": mid, "model": MODEL,
                                  "usage": {"input_tokens": inp, "output_tokens": 1}}}
            if meta:
                record["isMeta"] = True
            return json.dumps(record) + "\n"

        sid = "c0mpact"
        (tmp_path / f"{sid}.jsonl").write_text(line("msg_a", 10) + line("msg_b", 20))
        subagents = tmp_path / sid / "subagents"
        subagents.mkdir(parents=True)
        # A copy of the parent's conversation (isMeta first line): skipped
        (subagents / "agent-acompact-0001.jsonl").write_text(
            line("msg_x", 0, meta=True) + line("msg_a", 10) + line("msg_b", 20))
        (subagents / "agent-aside_question-0002.jsonl").write_text(
            line("msg_y", 0, meta=True) + line("msg_a", 10))
        # The summary call compaction itself made: counted
        (subagents / "agent-acompact-0003.jsonl").write_text(line("msg_c", 300))
        files = tree(tmp_path, sid)
        assert [f.name for f in files[1:]] == ["agent-acompact-0003.jsonl"]
        assert summed(read_tree_stats(files, transcripts=TranscriptRegistry()))[
            "input_tokens"] == 330

    def test_bounded_per_file_state(self, tmp_path):
        path = tmp_path / "big.jsonl"
        path.write_text("".join(json.dumps({
            "type": "assistant", "timestamp": "2026-10-15T10:00:00.000Z",
            "message": {"id": f"m{i}", "usage": {"input_tokens": 1}}}) + "\n"
            for i in range(RECENT_IDS * 3)))
        index = TranscriptIndex(str(path))
        assert index.stats()[0]["input_tokens"] == RECENT_IDS * 3
        assert len(index._recent) == RECENT_IDS
        assert len(index.head()) == HEAD_IDS


def _stream(files, order):
    """Write the lines of ``files`` (fixtures) into place one by one, in ``order``."""
    contents = {path: path.read_text().splitlines(keepends=True) for path in files}
    for path in files:
        path.write_text("")
    for path, index in order(contents):
        with open(path, "a") as f:
            f.write(contents[path][index])
        yield path


def _oracle(files) -> dict:
    """One count per message id over every file (the spike's reconciliation)."""
    last = {}
    loose = dict.fromkeys(KEYS, 0)
    for path in files:
        for line in path.read_text().splitlines():
            record = json.loads(line)
            if record.get("type") != "assistant":
                continue
            usage = record["message"]["usage"]
            counts = (usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                      usage.get("cache_creation_input_tokens", 0),
                      usage.get("cache_read_input_tokens", 0))
            mid = record["message"].get("id")
            if mid:
                last[mid] = counts
            else:
                for key, value in zip(KEYS, counts):
                    loose[key] += value
    return {key: loose[key] + sum(c[i] for c in last.values()) for i, key in enumerate(KEYS)}


class TestIncremental:
    @pytest.mark.parametrize("capture,sid", [FORK, WORKFLOW,
                                             ("03-background-subagent-with-SendMessage",
                                              PRINT_MODE[2][1])])
    def test_streamed_in_time_order_matches_at_every_line(self, tmp_path, capture, sid):
        project = copy_capture(capture, tmp_path / "p")
        files = tree(project, sid)

        def by_time(contents):
            lines = []
            for order, path in enumerate(files):
                for i, text in enumerate(contents[path]):
                    ts = json.loads(text).get("timestamp") or ""
                    lines.append((ts, order, i, path))
            return [(path, i) for _, _, i, path in sorted(lines, key=lambda x: x[:3])]

        registry = TranscriptRegistry()
        for _ in _stream(files, by_time):
            got = summed(read_tree_stats(files, transcripts=registry))
            assert [got[k] for k in KEYS] == [_oracle(files)[k] for k in KEYS]
        want = cost_state(capture, sid)
        assert [got[k] for k in KEYS] == [want[k] for k in COST_STATE_KEYS]


def _session_for(project_root: Path, project_dir: str, sid: str, tmp_path: Path):
    history = tmp_path / "history.jsonl"
    start = datetime(2026, 10, 15, 0, 0, 0)
    history.write_text(json.dumps({"display": "go", "timestamp": int(start.timestamp() * 1000) + 1,
                                   "project": project_dir, "sessionId": sid}) + "\n")
    session = Session(id="agent-1", name="agent-1", tmux_session="agents", tmux_window="1",
                      command=["claude"], start_directory=project_dir,
                      start_time=start.isoformat())
    session.agent_session_ids = [sid]
    return session, history


class TestSessionReaders:
    def test_get_session_stats_reports_each_conversation_at_reader_version_2(self, tmp_path):
        capture, sid = WORKFLOW
        project_dir = "/spike/workflow"
        root = tmp_path / "projects"
        copy_capture(capture, root / encode_project_path(project_dir))
        session, history = _session_for(root, project_dir, sid, tmp_path)
        stats = get_session_stats(session, history_path=history, projects_path=root)
        want = cost_state(capture, sid)
        assert (stats.input_tokens, stats.output_tokens, stats.cache_creation_tokens,
                stats.cache_read_tokens) == tuple(want[k] for k in COST_STATE_KEYS)
        assert stats.subagent_count == 3
        workflow_keys = [k for k in stats.per_conversation if "/workflows/" in k]
        assert len(workflow_keys) == 3
        assert all(entry["rv"] == CLAUDE_READER_VERSION
                   for entry in stats.per_conversation.values())
        total_1h = sum(entry.get("cw1h", 0) for entry in stats.per_conversation.values())
        assert 0 < total_1h <= stats.cache_creation_tokens
        assert sum(spend.price(HAIKU_55, entry) for entry in stats.per_conversation.values()) == (
            pytest.approx(want["costUSD"], abs=1e-9))

    def test_window_counts_each_message_once(self, tmp_path):
        capture, sid = FORK
        project_dir = "/spike/fork"
        root = tmp_path / "projects"
        project = copy_capture(capture, root / encode_project_path(project_dir))
        for path in project.rglob("*.jsonl"):  # recent, so no file is skipped by mtime
            os.utime(path, None)
        session, _ = _session_for(root, project_dir, sid, tmp_path)
        window = get_session_window_token_usage(session, datetime(2026, 10, 14),
                                                projects_path=root)
        want = cost_state(capture, sid)
        assert [window[k] for k in KEYS] == [want[k] for k in COST_STATE_KEYS]
        assert window["cache_creation_1h_tokens"] > 0
        later = get_session_window_token_usage(
            session, datetime(2026, 10, 14) + timedelta(days=30), projects_path=root)
        assert all(v == 0 for v in later.values())


def _line(mid, ts, inp, out=1, **extra):
    return json.dumps({"type": "assistant", "timestamp": ts, **extra,
                       "message": {"id": mid, "model": MODEL,
                                   "usage": {"input_tokens": inp, "output_tokens": out}}}) + "\n"


class TestCopies:
    def test_key_arithmetic_crosses_a_month(self):
        from overcode.transcript_index import datetime_key, key_before, key_datetime

        t = datetime(2026, 10, 1, 0, 30)
        assert key_datetime(datetime_key(t)) == t
        assert key_before(datetime_key(t), 3600) == datetime_key(datetime(2026, 9, 30, 23, 30))
        assert key_before(datetime_key(t), -3600) == datetime_key(datetime(2026, 10, 1, 1, 30))

    def test_a_copy_of_a_multi_line_message_counts_once(self, tmp_path):
        """The fork copies the message's last line; its first was seconds earlier."""
        sid = "p4rent"
        (tmp_path / f"{sid}.jsonl").write_text(
            _line("msg_m", "2026-10-01T00:00:00.000Z", 10, 1)
            + _line("msg_m", "2026-10-01T00:00:05.000Z", 10, 40)
            + _line("msg_n", "2026-10-01T00:00:09.000Z", 20, 2))
        subagents = tmp_path / sid / "subagents"
        subagents.mkdir(parents=True)
        (subagents / "agent-afork.jsonl").write_text(
            json.dumps({"type": "fork-context-ref", "parentSessionId": sid}) + "\n"
            + _line("msg_m", "2026-10-01T00:00:05.000Z", 10, 40, isSidechain=True)
            + _line("msg_f", "2026-10-01T00:00:07.000Z", 300, 3, isSidechain=True))
        got = summed(read_tree_stats(tree(tmp_path, sid), transcripts=TranscriptRegistry()))
        assert (got["input_tokens"], got["output_tokens"]) == (10 + 20 + 300, 40 + 2 + 3)

    def test_a_subagent_copying_another_subagent(self, tmp_path):
        sid = "p4rent"
        (tmp_path / f"{sid}.jsonl").write_text(_line("msg_p", "2026-10-01T00:00:00.000Z", 1))
        subagents = tmp_path / sid / "subagents"
        subagents.mkdir(parents=True)
        # b's own messages; msg_x sits deep in b's file
        lines_b = "".join(_line(f"msg_b{i}", f"2026-10-01T00:01:{i:02d}.000Z", 100)
                          for i in range(HEAD_IDS + 2))
        lines_b += _line("msg_x", "2026-10-01T00:01:30.000Z", 7000)
        (subagents / "agent-b.jsonl").write_text(lines_b)
        # a (sorted first) is a fork of b: it opens with b's message
        (subagents / "agent-a.jsonl").write_text(
            _line("msg_x", "2026-10-01T00:01:30.000Z", 7000)
            + _line("msg_a", "2026-10-01T00:01:40.000Z", 5))
        got = summed(read_tree_stats(tree(tmp_path, sid), transcripts=TranscriptRegistry()))
        assert got["input_tokens"] == 1 + 100 * (HEAD_IDS + 2) + 7000 + 5
