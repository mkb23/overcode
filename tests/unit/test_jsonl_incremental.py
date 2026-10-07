"""The incremental codex/grok log readers agree exactly with a full re-read (#524, #525).

``stats_jsonl_reference`` holds the old from-byte-0 parsers verbatim. Every
test here mutates a log the way a live agent (or a user) might — appends
that split lines and UTF-8 characters, truncation, replacement, in-place
rewrites — and checks after each step that the incremental answer is
identical to the reference's, field for field (floats included).
"""

import json
import math
import os
import random
import threading
from pathlib import Path

import pytest

from overcode.backends import codex_stats, grok_stats, jsonl_tail
from tests.unit import stats_jsonl_reference as ref
from tests.unit.test_codex_stats import (
    scaffolding_item,
    session_meta_line,
    token_count_event,
    turn_context_event,
    user_turn_item,
)
from tests.unit.test_grok_stats import (
    OTHER_SID,
    SID,
    context_update,
    turn_completed,
    update_envelope,
)


@pytest.fixture(autouse=True)
def _fresh_folds():
    jsonl_tail.clear()
    yield
    jsonl_tail.clear()


# ── a log that changes ────────────────────────────────────────────────────


class Log:
    """A file on disk plus the operations a live log goes through."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")

    def append(self, data: bytes) -> None:
        with self.path.open("ab") as handle:
            handle.write(data)

    def truncate(self, size: int) -> None:
        os.truncate(self.path, size)

    def replace(self, data: bytes) -> None:
        """New inode at the same path, as an editor's save or a log rotation."""
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, self.path)

    def rewrite_in_place(self, data: bytes) -> None:
        """Same inode, new contents, no observation in between."""
        with self.path.open("r+b") as handle:
            handle.truncate(0)
            handle.write(data)

    def size(self) -> int:
        return self.path.stat().st_size


def lines_to_bytes(lines) -> bytes:
    return b"".join(line.encode("utf-8") + b"\n" for line in lines)


JUNK_LINES = [
    "", "   ", "not json", "[1, 2]", "42", '"str"', "{", '{"type": "event_msg"}',
    '{"payload": "x"}', "null", "\t{\"type\": \"turn_context\", \"payload\": {}}  ",
]


# ── codex ─────────────────────────────────────────────────────────────────


def codex_line(rng: random.Random) -> str:
    pick = rng.random()
    if pick < 0.35:
        return json.dumps(token_count_event(
            input_tokens=rng.randint(0, 10**7), cached=rng.randint(0, 10**6),
            cache_write=rng.randint(0, 999), output=rng.randint(0, 10**5),
            reasoning=rng.randint(0, 9999), total=rng.randint(0, 10**7),
            context_window=rng.choice([0, 128000, 272000]),
            last_total=rng.choice([None, rng.randint(1, 300000)]),
        ))
    if pick < 0.5:
        return json.dumps(turn_context_event(
            model=rng.choice(["gpt-5.6-sol", "gpt-5.5", ""]),
            effort=rng.choice(["high", "low", ""]),
        ))
    if pick < 0.65:
        return json.dumps(user_turn_item(text=rng.choice(["hi", "héllo ✓ — ünïcode", "x" * 200])))
    if pick < 0.75:
        return json.dumps(scaffolding_item())
    if pick < 0.8:
        return json.dumps(session_meta_line())
    if pick < 0.9:
        return json.dumps({"type": "response_item", "payload": {
            "type": "function_call_output", "output": "∑ " * rng.randint(1, 80)}})
    return rng.choice(JUNK_LINES)


def assert_codex_exact(path: Path) -> None:
    assert codex_stats._scan_rollout(path) == ref.scan_rollout(path)


# ── grok ──────────────────────────────────────────────────────────────────


class GrokLines:
    """Mostly-ascending timestamps, with the odd stray one."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.ts = 1_787_072_000
        self.seen = []

    def __call__(self) -> str:
        rng = self.rng
        self.ts += rng.choice([0, 0, 1, 3, 10])
        pick = rng.random()
        if pick < 0.45:
            stray = rng.random()
            if stray < 0.08:
                ts = self.ts - rng.randint(1, 50)  # out of order
            elif stray < 0.12:
                ts = None
            elif stray < 0.14:
                ts = "2026-08-18T16:56:54Z"
            elif stray < 0.16:
                ts = float("nan")
            else:
                ts = self.ts
            entry = turn_completed(
                input_tokens=rng.randint(0, 10**6), output_tokens=rng.randint(0, 10**4),
                cached_read=rng.randint(0, 10**5), reasoning=rng.randint(0, 999),
                cache_creation=rng.randint(0, 99),
                cost_ticks=rng.choice([rng.randint(0, 10**10), rng.random() * 1e8, "12"]),
                timestamp=ts,
            )
            if ts is None:
                del entry["timestamp"]
            if isinstance(ts, (int, float)) and not math.isnan(ts):
                self.seen.append(ts)
            return json.dumps(entry)  # NaN is written as the bare token NaN
        if pick < 0.7:
            return json.dumps(context_update(rng.randint(0, 300000), timestamp=self.ts))
        if pick < 0.85:
            return json.dumps(update_envelope(
                {"sessionUpdate": "agent_message_chunk",
                 "content": {"type": "text", "text": "chunk ✓ " * rng.randint(1, 40)}},
                timestamp=self.ts, meta=rng.choice([None, {"eventId": "e"}])))
        return rng.choice(JUNK_LINES)

    def windows(self):
        out = [None, 0, 1e12]
        for ts in self.rng.sample(self.seen, min(6, len(self.seen))):
            out += [ts, ts - 0.5, ts + 0.5]  # exactly at the start, and either side
        return out


def assert_grok_exact(path: Path, windows) -> None:
    for since in windows:
        got = grok_stats._scan_updates(path, since_ts=since)
        want = ref.scan_updates(path, since_ts=since)
        if since is not None:
            # The old scan also summed a windowed cost no caller read; the
            # fold offers cost for the whole file only (see _UpdatesFold.view).
            del want["cost_usd"]
        assert got == want, since


def prompt_line(rng: random.Random) -> str:
    if rng.random() < 0.1:
        return rng.choice(JUNK_LINES + ['{"session_id": 7}', '{"session_id": ["x"]}'])
    return json.dumps({"timestamp": "2026-08-18T16:56:54Z",
                       "session_id": rng.choice([SID, SID, OTHER_SID, "third"]),
                       "prompt": rng.choice(["go", "ünïcode ✓"]), "is_bash": False})


def assert_prompts_exact(project: Path) -> None:
    for sid in (SID, OTHER_SID, "third", "absent"):
        assert grok_stats._count_prompts(project, sid) == ref.count_prompts(project, sid)


# ── the property: random edits, exact after every one ─────────────────────


def drive(log: Log, make_line, check, rng: random.Random, steps: int) -> None:
    """Apply random edits to ``log``, calling ``check()`` after each."""
    pending = b""
    for _ in range(steps):
        op = rng.random()
        if op < 0.6:
            while len(pending) < 400:
                pending += make_line().encode("utf-8") + b"\n"
            # Any cut point: mid-line, mid-UTF-8 character, or at a newline.
            cut = rng.randint(1, min(len(pending), rng.choice([3, 40, 400])))
            log.append(pending[:cut])
            pending = pending[cut:]
        elif op < 0.7:
            pass  # unchanged file
        elif op < 0.78 and log.size():
            log.truncate(rng.randint(0, log.size() - 1))
            pending = b""
        elif op < 0.84:
            log.replace(lines_to_bytes(make_line() for _ in range(rng.randint(0, 8))))
            pending = b""
        elif op < 0.9:
            # Truncate and regrow larger before anyone looks: only the
            # check bytes can tell this from an append.
            log.rewrite_in_place(lines_to_bytes(
                make_line() for _ in range(rng.randint(5, 30))))
            pending = b""
        else:
            # A complete object without its newline, then something that
            # makes the whole line invalid (or just the newline).
            log.append(make_line().encode("utf-8"))
            check()
            log.append(rng.choice([b"\n", b"  \n", b" trailing junk\n", b"}\n"]))
            pending = b""
        check()


@pytest.mark.parametrize("chunk", [7, 1 << 20])
@pytest.mark.parametrize("seed", range(12))
def test_codex_random_edits_match_full_scan(tmp_path, monkeypatch, seed, chunk):
    # A 7-byte read chunk puts every line across several reads.
    monkeypatch.setattr(jsonl_tail, "_CHUNK_BYTES", chunk)
    rng = random.Random(seed)
    log = Log(tmp_path / "rollout-x.jsonl")
    drive(log, lambda: codex_line(rng), lambda: assert_codex_exact(log.path), rng, 120)


@pytest.mark.parametrize("seed", range(12))
def test_grok_random_edits_match_full_scan(tmp_path, seed):
    rng = random.Random(1000 + seed)
    lines = GrokLines(rng)
    log = Log(tmp_path / "updates.jsonl")
    drive(log, lines, lambda: assert_grok_exact(log.path, lines.windows()), rng, 120)


@pytest.mark.parametrize("seed", range(6))
def test_grok_prompt_counts_random_edits_match_full_scan(tmp_path, seed):
    rng = random.Random(2000 + seed)
    log = Log(tmp_path / "proj" / "prompt_history.jsonl")
    drive(log, lambda: prompt_line(rng), lambda: assert_prompts_exact(log.path.parent), rng, 80)


# ── the named cases, one at a time ────────────────────────────────────────


def test_codex_lines_appended_one_at_a_time(tmp_path):
    rng = random.Random(7)
    log = Log(tmp_path / "r.jsonl")
    for _ in range(200):
        log.append(codex_line(rng).encode("utf-8") + b"\n")
        assert_codex_exact(log.path)


def test_codex_partial_line_then_completed(tmp_path):
    log = Log(tmp_path / "r.jsonl")
    log.append(lines_to_bytes([json.dumps(user_turn_item())]))
    line = json.dumps(token_count_event(input_tokens=123)).encode()
    log.append(line[:30])
    assert_codex_exact(log.path)
    assert codex_stats._scan_rollout(log.path)["input_tokens"] == 0
    log.append(line[30:] + b"\n")
    assert_codex_exact(log.path)
    assert codex_stats._scan_rollout(log.path)["input_tokens"] == 123


def test_complete_object_without_newline_counts_now_and_once(tmp_path):
    log = Log(tmp_path / "r.jsonl")
    log.append(json.dumps(user_turn_item()).encode())  # no newline yet
    assert codex_stats._scan_rollout(log.path)["interaction_count"] == 1
    log.append(b"\n")
    assert codex_stats._scan_rollout(log.path)["interaction_count"] == 1
    log.append(lines_to_bytes([json.dumps(user_turn_item())]))
    assert codex_stats._scan_rollout(log.path)["interaction_count"] == 2
    assert_codex_exact(log.path)


def test_object_continued_into_an_invalid_line_is_uncounted(tmp_path):
    log = Log(tmp_path / "r.jsonl")
    log.append(json.dumps(user_turn_item()).encode())
    assert codex_stats._scan_rollout(log.path)["interaction_count"] == 1
    log.append(b" and then garbage\n")
    assert codex_stats._scan_rollout(log.path)["interaction_count"] == 0
    assert_codex_exact(log.path)


def test_whitespace_after_an_open_object_is_part_of_its_line(tmp_path):
    log = Log(tmp_path / "r.jsonl")
    log.append(json.dumps(user_turn_item()).encode())
    assert_codex_exact(log.path)
    log.append(b"   ")
    assert_codex_exact(log.path)
    log.append(b" \n" + lines_to_bytes([json.dumps(user_turn_item())]))
    assert_codex_exact(log.path)
    assert codex_stats._scan_rollout(log.path)["interaction_count"] == 2


def test_invalid_utf8_line_is_skipped_not_fatal(tmp_path):
    # The old text-mode read raised UnicodeDecodeError out of the reader;
    # the fold skips just that line.
    log = Log(tmp_path / "r.jsonl")
    log.append(lines_to_bytes([json.dumps(user_turn_item())]) + b'{"x": "\xff\xfe"}\n')
    log.append(lines_to_bytes([json.dumps(user_turn_item())]))
    assert codex_stats._scan_rollout(log.path)["interaction_count"] == 2


def test_unreadable_file_reads_empty_then_recovers(tmp_path, monkeypatch):
    log = Log(tmp_path / "r.jsonl")
    log.append(lines_to_bytes([json.dumps(user_turn_item())] * 2))
    assert codex_stats._scan_rollout(log.path)["interaction_count"] == 2
    log.append(lines_to_bytes([json.dumps(user_turn_item())]))

    def denied(*_args, **_kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(jsonl_tail, "_open", denied)
    assert codex_stats._scan_rollout(log.path) == codex_stats._new_rollout_state()
    monkeypatch.undo()
    assert codex_stats._scan_rollout(log.path)["interaction_count"] == 3


def test_short_read_is_retried_from_scratch(tmp_path, monkeypatch):
    """The file shrinks between the stat and the read."""
    log = Log(tmp_path / "r.jsonl")
    log.append(lines_to_bytes([json.dumps(user_turn_item())] * 3))
    real_open = jsonl_tail._open

    def shrinking_open(path, mode):
        handle = real_open(path, mode)
        log.truncate(0)
        return handle

    monkeypatch.setattr(jsonl_tail, "_open", shrinking_open)
    codex_stats._scan_rollout(log.path)
    monkeypatch.undo()
    log.append(lines_to_bytes([json.dumps(user_turn_item())] * 5))
    assert_codex_exact(log.path)
    assert codex_stats._scan_rollout(log.path)["interaction_count"] == 5


def test_codex_truncated(tmp_path):
    log = Log(tmp_path / "r.jsonl")
    first = lines_to_bytes([json.dumps(user_turn_item())])
    log.append(first + lines_to_bytes([json.dumps(user_turn_item())] * 3))
    assert codex_stats._scan_rollout(log.path)["interaction_count"] == 4
    log.truncate(len(first))
    assert codex_stats._scan_rollout(log.path)["interaction_count"] == 1
    assert_codex_exact(log.path)


def test_codex_replaced_with_a_new_inode_of_the_same_size(tmp_path):
    log = Log(tmp_path / "r.jsonl")
    log.append(lines_to_bytes([json.dumps(turn_context_event(model="model-aa"))]))
    assert codex_stats._scan_rollout(log.path)["model"] == "model-aa"
    log.replace(lines_to_bytes([json.dumps(turn_context_event(model="model-bb"))]))
    assert codex_stats._scan_rollout(log.path)["model"] == "model-bb"
    assert_codex_exact(log.path)


def test_codex_truncated_and_regrown_unobserved(tmp_path):
    log = Log(tmp_path / "r.jsonl")
    log.append(lines_to_bytes([json.dumps(user_turn_item())] * 3))
    assert codex_stats._scan_rollout(log.path)["interaction_count"] == 3
    log.rewrite_in_place(lines_to_bytes([json.dumps(scaffolding_item())] * 6))
    assert codex_stats._scan_rollout(log.path)["interaction_count"] == 0
    assert_codex_exact(log.path)


@pytest.mark.parametrize("disguise", ["new_inode_same_mtime", "older_mtime", "same_size_newer_mtime"])
def test_rewrite_that_keeps_the_last_parsed_bytes_still_resets(tmp_path, disguise):
    """Rewrites the check bytes cannot see; the file's identity must.

    Old and new contents differ only in their first line, so the bytes just
    before the parsed offset match, and only (inode, size, mtime) tell.
    """
    log = Log(tmp_path / "r.jsonl")
    common = lines_to_bytes([json.dumps(user_turn_item())] * 3)
    old = lines_to_bytes([json.dumps(turn_context_event(model="model-aa"))]) + common
    new = lines_to_bytes([json.dumps(turn_context_event(model="model-bb"))]) + common
    log.append(old)
    assert codex_stats._scan_rollout(log.path)["model"] == "model-aa"
    old_mtime = log.path.stat().st_mtime_ns
    extra = lines_to_bytes([json.dumps(user_turn_item())])
    if disguise == "new_inode_same_mtime":  # e.g. `cp -p` over it
        log.replace(new + extra)
        os.utime(log.path, ns=(old_mtime, old_mtime))
    elif disguise == "older_mtime":  # restored from a backup in place
        log.rewrite_in_place(new + extra)
        os.utime(log.path, ns=(old_mtime - 10**9, old_mtime - 10**9))
    else:
        log.rewrite_in_place(new)
        os.utime(log.path, ns=(old_mtime + 10**9, old_mtime + 10**9))
    assert codex_stats._scan_rollout(log.path)["model"] == "model-bb"
    assert_codex_exact(log.path)


def test_missing_file_reads_empty_and_forgets(tmp_path):
    log = Log(tmp_path / "r.jsonl")
    log.append(lines_to_bytes([json.dumps(user_turn_item())]))
    assert codex_stats._scan_rollout(log.path)["interaction_count"] == 1
    log.path.unlink()
    assert codex_stats._scan_rollout(log.path) == ref.scan_rollout(log.path)
    log = Log(log.path)
    assert codex_stats._scan_rollout(log.path)["interaction_count"] == 0


def test_grok_windows_at_turn_boundaries(tmp_path):
    log = Log(tmp_path / "updates.jsonl")
    lines = []
    for i, ts in enumerate([100, 100, 105, 110, 103, 120]):  # 103 arrives late
        lines.append(json.dumps(turn_completed(
            input_tokens=10 ** i, output_tokens=i, cost_ticks=10 ** (i + 3), timestamp=ts)))
        log.append(lines_to_bytes(lines[-1:]))
        assert_grok_exact(log.path, [None, 99, 100, 100.5, 103, 104, 105, 110, 120, 121])
    window = grok_stats._scan_updates(log.path, since_ts=105)
    assert window["input_tokens"] == 10**2 + 10**3 + 10**5


def test_grok_window_on_unchanged_file_is_a_lookup(tmp_path, monkeypatch):
    log = Log(tmp_path / "updates.jsonl")
    log.append(lines_to_bytes(json.dumps(turn_completed(
        input_tokens=5, output_tokens=1, timestamp=1000 + i)) for i in range(50)))
    grok_stats._scan_updates(log.path)
    opened = count_io(monkeypatch)
    assert_grok_exact(log.path, [None, 1000, 1025, 1049, 1050])
    assert opened.opens == 0


# ── the readers: every call reflects the file as it is now ────────────────


def test_codex_reader_answers_track_each_append(tmp_path):
    from datetime import timedelta

    from tests.unit import test_codex_stats as cx

    root = tmp_path / "sessions"
    path = cx.rollout_path(root, cx.SID)
    cx.write_rollout(path, [session_meta_line(), turn_context_event(), user_turn_item()])
    reader = codex_stats.CodexStatsReader(sessions_dir=root)
    session = cx.make_session()
    since = cx.LAUNCH - timedelta(minutes=1)
    assert reader.get_window_token_usage(session, since)["input_tokens"] == 0
    for n in range(1, 6):
        with path.open("a") as handle:
            handle.write(json.dumps(token_count_event(input_tokens=1000 * n)) + "\n")
            handle.write(json.dumps(user_turn_item()) + "\n")
        stats = reader.get_stats(session)
        assert (stats.input_tokens, stats.interaction_count) == (1000 * n, 1 + n)
        assert reader.get_window_token_usage(session, since)["input_tokens"] == 1000 * n
    # Launched before the window: still "unknown", however warm the fold.
    later = cx.LAUNCH + timedelta(minutes=1)
    assert reader.get_window_token_usage(session, later)["input_tokens"] == 0


def test_grok_reader_answers_track_each_append(tmp_path):
    from tests.unit import test_grok_stats as gk

    root = tmp_path / "sessions"
    sdir = grok_stats.session_dir(gk.PROJECT_DIR, SID, root=root)
    gk.write_jsonl(sdir / "updates.jsonl", [context_update(10, timestamp=100)])
    gk.write_summary(sdir / "summary.json")
    prompts = gk.write_prompt_history(sdir.parent / "prompt_history.jsonl", [])
    reader = grok_stats.GrokStatsReader(sessions_dir=root)
    session = gk.make_session()
    from datetime import datetime

    for n in range(1, 6):
        with (sdir / "updates.jsonl").open("a") as handle:
            handle.write(json.dumps(turn_completed(
                input_tokens=100, output_tokens=1, cost_ticks=10**9, timestamp=100 + n)) + "\n")
        with prompts.open("a") as handle:
            handle.write(json.dumps({"session_id": SID, "prompt": "go"}) + "\n")
        stats = reader.get_stats(session)
        assert (stats.input_tokens, stats.interaction_count) == (100 * n, n)
        assert reader.get_stored_cost(session) == float(n)
        window = reader.get_window_token_usage(session, datetime.fromtimestamp(102))
        assert window["input_tokens"] == 100 * max(0, n - 1)


# ── cost: an unchanged file reads nothing, an append reads the append ─────


class IoCounter:
    def __init__(self) -> None:
        self.opens = 0
        self.bytes = 0


def count_io(monkeypatch) -> IoCounter:
    counter = IoCounter()
    real_open = jsonl_tail._open

    class Counted:
        def __init__(self, handle):
            self._handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._handle.close()

        def seek(self, *args):
            return self._handle.seek(*args)

        def read(self, *args):
            data = self._handle.read(*args)
            counter.bytes += len(data)
            return data

    def counting_open(*args, **kwargs):
        counter.opens += 1
        return Counted(real_open(*args, **kwargs))

    monkeypatch.setattr(jsonl_tail, "_open", counting_open)
    return counter


@pytest.mark.parametrize("scan", ["codex", "grok", "prompts"])
def test_unchanged_file_costs_no_read_and_append_reads_only_the_append(
    tmp_path, monkeypatch, scan,
):
    rng = random.Random(3)
    if scan == "codex":
        path = tmp_path / "r.jsonl"
        make_line = lambda: codex_line(rng)  # noqa: E731
        call = lambda: codex_stats._scan_rollout(path)  # noqa: E731
    elif scan == "grok":
        path = tmp_path / "updates.jsonl"
        make_line = GrokLines(rng)
        call = lambda: grok_stats._scan_updates(path, since_ts=1_787_072_050)  # noqa: E731
    else:
        path = tmp_path / "prompt_history.jsonl"
        make_line = lambda: prompt_line(rng)  # noqa: E731
        call = lambda: grok_stats._count_prompts(tmp_path, SID)  # noqa: E731
    log = Log(path)
    log.append(lines_to_bytes(make_line() for _ in range(3000)))
    assert log.size() > 100_000
    call()

    io = count_io(monkeypatch)
    for _ in range(5):
        call()
    assert (io.opens, io.bytes) == (0, 0)

    appended = lines_to_bytes(make_line() for _ in range(10))
    log.append(appended)
    call()
    assert io.opens == 1
    # The appended bytes plus the few already-parsed bytes re-verified.
    assert len(appended) <= io.bytes <= len(appended) + jsonl_tail._CHECK_BYTES


# ── thread safety ─────────────────────────────────────────────────────────


def test_concurrent_readers_during_appends_see_only_real_prefixes(tmp_path, monkeypatch):
    """Eight readers (the TUI's pool size) race one writer.

    Every answer must be the reference answer for some whole-line prefix of
    the file — a double-applied append (a race on the fold) gives a count no
    prefix has. ``apply`` is slowed so an unguarded fold would race.
    """
    log = Log(tmp_path / "r.jsonl")
    real_apply = codex_stats._apply_rollout_entry
    pause = threading.Event()

    def slow_apply(state, entry):
        pause.wait(0.0002)
        real_apply(state, entry)

    monkeypatch.setattr(codex_stats, "_apply_rollout_entry", slow_apply)
    lines = [json.dumps(user_turn_item())] * 300
    valid_counts = set(range(len(lines) + 1))
    seen, errors = [], []
    done = threading.Event()

    def reader():
        try:
            last = 0
            while not done.is_set():
                count = codex_stats._scan_rollout(log.path)["interaction_count"]
                assert count in valid_counts and count >= last, (count, last)
                last = count
                seen.append(count)
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(8)]
    for t in threads:
        t.start()
    try:
        for i in range(0, len(lines), 5):
            log.append(lines_to_bytes(lines[i:i + 5]))
    finally:
        done.set()
        for t in threads:
            t.join()
    assert not errors, errors[0]
    assert codex_stats._scan_rollout(log.path)["interaction_count"] == len(lines)


def test_concurrent_grok_window_and_stats_callers(tmp_path):
    rng = random.Random(11)
    lines = GrokLines(rng)
    log = Log(tmp_path / "updates.jsonl")
    errors = []
    done = threading.Event()

    def reader(since):
        try:
            while not done.is_set():
                grok_stats._scan_updates(log.path, since_ts=since)
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=reader, args=(s,))
               for s in (None, 1_787_072_000, 1_787_072_100, 1_787_072_500)]
    for t in threads:
        t.start()
    try:
        for _ in range(300):
            log.append(lines().encode("utf-8") + b"\n")
    finally:
        done.set()
        for t in threads:
            t.join()
    assert not errors, errors[0]
    assert_grok_exact(log.path, lines.windows())


# ── the cache stays bounded ───────────────────────────────────────────────


def test_fold_table_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(jsonl_tail, "_MAX_ENTRIES", 8)
    for i in range(40):
        log = Log(tmp_path / f"r{i}.jsonl")
        log.append(lines_to_bytes([json.dumps(user_turn_item())] * (i + 1)))
        assert codex_stats._scan_rollout(log.path)["interaction_count"] == i + 1
        assert len(jsonl_tail._entries) <= 8


def test_idle_folds_are_dropped_first(tmp_path, monkeypatch):
    monkeypatch.setattr(jsonl_tail, "_MAX_ENTRIES", 4)
    now = [0.0]
    monkeypatch.setattr(jsonl_tail, "clock", lambda: now[0])
    logs = []
    for i in range(4):
        logs.append(Log(tmp_path / f"r{i}.jsonl"))
        codex_stats._scan_rollout(logs[-1].path)
    now[0] = jsonl_tail._IDLE_SECONDS + 1
    codex_stats._scan_rollout(logs[0].path)  # still in use
    codex_stats._scan_rollout(Log(tmp_path / "new.jsonl").path)  # over the cap
    kept = {Path(path).name for _kind, path in jsonl_tail._entries}
    assert kept == {"r0.jsonl", "new.jsonl"}
