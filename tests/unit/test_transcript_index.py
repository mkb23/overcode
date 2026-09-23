"""TranscriptIndex produces exactly what the full-parse readers produce.

Every test builds a transcript on disk, drives the index through some
sequence of appends / truncations / replacements, and after each step
compares the index's answers with a fresh full parse of the same bytes:
``_parse_session_lines`` (what ``read_session_file_stats`` returns) and the
full-parse window reader (what ``read_window_token_usage`` returns).
"""

import json
import os
import random
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from overcode import history_reader
from overcode.history_reader import _parse_session_lines
from overcode.transcript_index import (
    MAX_STATES_PER_INDEX,
    TranscriptIndex,
    TranscriptRegistry,
    datetime_key,
    parse_message_time,
)

# The full-parse references. ``read_window_token_usage`` has no cache and
# ``_parse_session_lines`` is the parser itself, so neither can return a
# remembered answer for a file whose bytes just changed.
full_window = getattr(
    history_reader, "_read_window_token_usage_full", history_reader.read_window_token_usage
)


def full_stats(path: Path, since=None):
    with open(path, "r") as f:
        return _parse_session_lines(f, since=since)


T0 = datetime(2026, 3, 10, 14, 0, 0)  # local-naive, like every ``since`` the callers pass
EPOCH = 1_700_000_000  # settled mtimes, one second apart per write


def _iso_z(dt_local: datetime) -> str:
    """A local-naive time as the millisecond UTC ISO string Claude Code writes."""
    utc = dt_local.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def _assistant(ts: datetime, inp=100, out=20, cc=0, cr=1000, model="claude-opus-4-6", mid="msg_01"):
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": _iso_z(ts),
            "message": {
                "model": model,
                "id": mid,
                "role": "assistant",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {
                    "input_tokens": inp,
                    "output_tokens": out,
                    "cache_creation_input_tokens": cc,
                    "cache_read_input_tokens": cr,
                },
            },
        }
    )


def _user(ts: datetime, text="do the thing"):
    return json.dumps(
        {"type": "user", "timestamp": _iso_z(ts), "message": {"role": "user", "content": text}}
    )


def _tool_result(ts: datetime):
    return json.dumps(
        {
            "type": "user",
            "timestamp": _iso_z(ts),
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x" * 300}],
            },
        }
    )


def _turns(start: datetime, n: int, seed: int = 1, step=timedelta(seconds=37)):
    """``n`` realistic turns: prompt, two assistant messages, a tool result, an answer."""
    rng = random.Random(seed)
    lines = []
    t = start
    for i in range(n):
        lines.append(_user(t, f"prompt {i}"))
        t += step
        lines.append(_assistant(t, inp=rng.randint(1, 500), cr=rng.randint(0, 50_000)))
        t += step
        lines.append(_tool_result(t))
        t += step
        lines.append(
            _assistant(t, inp=rng.randint(1, 500), cc=rng.randint(0, 2000), mid="msg_bdrk_1")
        )
        t += step
        if i % 5 == 4:
            lines.append(json.dumps({"type": "file-history-snapshot", "snapshot": {}}))
    return lines


class _Writer:
    """Writes a transcript with a distinct, settled mtime per write."""

    def __init__(self, path: Path):
        self.path = path
        self.n = 0

    def _stamp(self):
        self.n += 1
        os.utime(self.path, (EPOCH + self.n, EPOCH + self.n))

    def write(self, text: str) -> None:
        self.path.write_text(text)
        self._stamp()

    def append(self, text: str) -> None:
        with open(self.path, "a") as f:
            f.write(text)
        self._stamp()

    def replace(self, text: str) -> None:
        """Rewrite through a new inode (rename over), as an editor or rsync would."""
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(text)
        os.replace(tmp, self.path)
        self._stamp()


def _check(index: TranscriptIndex, path: Path, sinces, windows):
    """Every stats(since) and window_usage(since) equals a fresh full parse."""
    for since in sinces:
        assert index.stats(since) == full_stats(path, since), f"stats(since={since})"
    for since in windows:
        assert index.window_usage(since) == full_window(path, since), f"window(since={since})"


SINCES = [None, T0 + timedelta(minutes=3), T0 + timedelta(hours=5)]
WINDOWS = [T0 - timedelta(hours=1), T0 + timedelta(minutes=10), T0 + timedelta(hours=5)]


class TestIdentity:
    def test_appended_in_three_steps(self, tmp_path):
        path = tmp_path / "s.jsonl"
        w = _Writer(path)
        index = TranscriptIndex(str(path))
        lines = _turns(T0, 30)
        w.write("\n".join(lines[:20]) + "\n")
        _check(index, path, SINCES, WINDOWS)
        first_offset = index.offset
        w.append("\n".join(lines[20:80]) + "\n")
        _check(index, path, SINCES, WINDOWS)
        assert index.offset > first_offset
        w.append("\n".join(lines[80:]) + "\n")
        _check(index, path, SINCES, WINDOWS)
        assert index.offset == path.stat().st_size

    def test_appended_bytes_only_are_read(self, tmp_path, monkeypatch):
        """After the first parse, a query reads the appended bytes, not the file."""
        path = tmp_path / "s.jsonl"
        w = _Writer(path)
        index = TranscriptIndex(str(path))
        w.write("\n".join(_turns(T0, 200)) + "\n")
        index.stats(None)
        size_before = path.stat().st_size
        extra = "\n".join(_turns(T0 + timedelta(days=1), 2)) + "\n"
        w.append(extra)

        import builtins

        read_bytes = []

        real_open = builtins.open

        def spying_open(file, mode="r", *a, **kw):
            f = real_open(file, mode, *a, **kw)
            if "b" in mode and os.fspath(file) == str(path):
                orig = f.read

                def read(n=-1):
                    data = orig(n)
                    read_bytes.append(len(data))
                    return data

                f.read = read
            return f

        monkeypatch.setattr(builtins, "open", spying_open)
        assert index.stats(None) == full_stats(path, None)
        # 64 tail bytes for the rewrite check, the appended bytes, then EOF
        assert sum(read_bytes) == 64 + len(extra.encode())
        assert size_before + len(extra.encode()) == path.stat().st_size

    def test_partial_line_at_eof_then_completed(self, tmp_path):
        path = tmp_path / "s.jsonl"
        w = _Writer(path)
        index = TranscriptIndex(str(path))
        lines = _turns(T0, 4)
        last = _assistant(T0 + timedelta(hours=1), inp=777, out=99)
        w.write("\n".join(lines) + "\n" + last[: len(last) // 2])
        _check(index, path, SINCES, WINDOWS)  # the fragment parses as nothing, in both
        committed = index.offset
        w.append(last[len(last) // 2 :])
        # Still no trailing newline: a complete last line counts, in both.
        _check(index, path, SINCES, WINDOWS)
        assert index.stats(None)[0]["input_tokens"] == full_stats(path)[0]["input_tokens"]
        assert index.offset == committed  # not committed until the newline lands
        w.append("\n")
        _check(index, path, SINCES, WINDOWS)
        assert index.offset == path.stat().st_size
        w.append(_user(T0 + timedelta(hours=2)) + "\n")
        _check(index, path, SINCES, WINDOWS)

    def test_no_trailing_newline_counts_last_line_once(self, tmp_path):
        path = tmp_path / "s.jsonl"
        w = _Writer(path)
        index = TranscriptIndex(str(path))
        lines = _turns(T0, 3)
        w.write("\n".join(lines))
        for _ in range(3):  # repeated queries do not double count the tail
            _check(index, path, SINCES, WINDOWS)

    def test_truncated_and_rewritten(self, tmp_path):
        path = tmp_path / "s.jsonl"
        w = _Writer(path)
        index = TranscriptIndex(str(path))
        w.write("\n".join(_turns(T0, 40)) + "\n")
        _check(index, path, SINCES, WINDOWS)
        # Shorter, different content, same inode
        w.write("\n".join(_turns(T0 + timedelta(minutes=7), 6, seed=9)) + "\n")
        _check(index, path, SINCES, WINDOWS)
        # Same size, different bytes: an in-place rewrite
        a = _assistant(T0 + timedelta(minutes=1), inp=100, out=200)
        b = _assistant(T0 + timedelta(minutes=1), inp=300, out=400)
        assert len(a) == len(b)
        w.write(a + "\n")
        _check(index, path, SINCES, WINDOWS)
        w.write(b + "\n")
        _check(index, path, SINCES, WINDOWS)
        assert index.stats(None)[0]["input_tokens"] == 300

    def test_rewritten_same_size_same_tail_different_middle(self, tmp_path):
        """A same-size rewrite whose last 64 bytes match is caught by the mtime change."""
        path = tmp_path / "s.jsonl"
        w = _Writer(path)
        index = TranscriptIndex(str(path))
        a = _assistant(T0, inp=100, out=20) + "\n" + _assistant(T0 + timedelta(minutes=1)) + "\n"
        b = _assistant(T0, inp=999, out=20) + "\n" + _assistant(T0 + timedelta(minutes=1)) + "\n"
        assert len(a) == len(b) and a[-64:] == b[-64:]
        w.write(a)
        _check(index, path, SINCES, WINDOWS)
        w.write(b)
        _check(index, path, SINCES, WINDOWS)
        assert index.stats(None)[0]["input_tokens"] == 999 + 100

    def test_replaced_by_new_inode(self, tmp_path):
        path = tmp_path / "s.jsonl"
        w = _Writer(path)
        index = TranscriptIndex(str(path))
        w.write("\n".join(_turns(T0, 10)) + "\n")
        _check(index, path, SINCES, WINDOWS)
        ino = path.stat().st_ino
        w.replace("\n".join(_turns(T0, 12, seed=3)) + "\n")
        assert path.stat().st_ino != ino
        _check(index, path, SINCES, WINDOWS)
        w.append(_assistant(T0 + timedelta(hours=3)) + "\n")
        _check(index, path, SINCES, WINDOWS)

    def test_file_missing_then_created_then_deleted(self, tmp_path):
        path = tmp_path / "s.jsonl"
        index = TranscriptIndex(str(path))
        assert index.stats(None) == history_reader.read_session_file_stats(path)
        assert index.window_usage(T0) == full_window(path, T0)
        w = _Writer(path)
        w.write("\n".join(_turns(T0, 3)) + "\n")
        _check(index, path, SINCES, WINDOWS)
        path.unlink()
        assert index.stats(T0) == history_reader.read_session_file_stats(path, T0)
        assert index.window_usage(T0) == full_window(path, T0)
        w.write("\n".join(_turns(T0, 2, seed=5)) + "\n")
        _check(index, path, SINCES, WINDOWS)

    def test_window_boundary_exactly_on_a_message_timestamp(self, tmp_path):
        path = tmp_path / "s.jsonl"
        w = _Writer(path)
        index = TranscriptIndex(str(path))
        t1 = T0 + timedelta(minutes=1)
        t2 = T0 + timedelta(minutes=2, milliseconds=500)
        w.write(
            "\n".join([_assistant(T0, inp=1), _assistant(t1, inp=10), _assistant(t2, inp=100)])
            + "\n"
        )
        boundaries = [
            T0,
            t1,
            t2,
            t1 - timedelta(microseconds=1),
            t1 + timedelta(microseconds=1),
            t2 - timedelta(microseconds=1),
            t2 + timedelta(microseconds=1),
            t2 - timedelta(milliseconds=1),
            t2 + timedelta(milliseconds=1),
        ]
        _check(index, path, boundaries, boundaries)
        assert index.window_usage(t1)["input_tokens"] == 110
        assert index.window_usage(t1 + timedelta(microseconds=1))["input_tokens"] == 100
        assert index.stats(t1)[0]["input_tokens"] == 110

    def test_out_of_order_timestamps(self, tmp_path):
        """A stepped clock puts later lines earlier in time; window sums stay exact."""
        path = tmp_path / "s.jsonl"
        w = _Writer(path)
        index = TranscriptIndex(str(path))
        lines = [
            _assistant(T0 + timedelta(minutes=5), inp=1),
            _assistant(T0 + timedelta(minutes=1), inp=10),
            _user(T0 + timedelta(minutes=9)),
            _user(T0 + timedelta(minutes=2)),
            _assistant(T0 + timedelta(minutes=8), inp=100),
        ]
        w.write("\n".join(lines) + "\n")
        _check(index, path, SINCES, WINDOWS + [T0 + timedelta(minutes=3)])
        w.append(_assistant(T0 + timedelta(minutes=4), inp=1000) + "\n")
        _check(index, path, SINCES, WINDOWS + [T0 + timedelta(minutes=3)])

    def test_unicode_content(self, tmp_path):
        path = tmp_path / "s.jsonl"
        w = _Writer(path)
        index = TranscriptIndex(str(path))
        lines = [
            _user(T0, "héllo wörld — 日本語 🚀   \r inside"),
            _assistant(T0 + timedelta(seconds=5), model="claude-opus-4-6-日本"),
            _user(T0 + timedelta(minutes=1), "emoji 🎉🎉🎉" * 200),
            _assistant(T0 + timedelta(minutes=2), inp=5, model="claude-opus-4-6-日本"),
        ]
        w.write("\n".join(lines) + "\n")
        _check(index, path, SINCES, WINDOWS)
        w.append(_user(T0 + timedelta(minutes=3), "ünïcödé") + "\n")
        _check(index, path, SINCES, WINDOWS)
        assert index.stats(None)[0]["model"] == "claude-opus-4-6-日本"

    def test_lines_that_are_not_json(self, tmp_path):
        path = tmp_path / "s.jsonl"
        w = _Writer(path)
        index = TranscriptIndex(str(path))
        lines = [
            "not json at all",
            "",
            "   ",
            _assistant(T0, inp=42),
            "{truncated json",
            json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": 7}}}),  # no ts
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "garbage",
                    "message": {"usage": {"input_tokens": 9}},
                }
            ),
            json.dumps({"type": "assistant", "timestamp": _iso_z(T0), "message": {"usage": {}}}),
            json.dumps({"type": "assistant", "timestamp": _iso_z(T0), "message": {}}),
            json.dumps({"type": "user", "message": {"content": "no timestamp"}}),
            json.dumps({"type": "user", "timestamp": "nope", "message": {"content": "bad ts"}}),
            json.dumps({"type": "user", "timestamp": _iso_z(T0), "message": {"content": []}}),
            json.dumps({"type": "summary", "summary": "x"}),
            _user(T0 + timedelta(minutes=1)),
        ]
        w.write("\n".join(lines) + "\n")
        _check(index, path, SINCES, WINDOWS)
        # The since filter counts an assistant message with no/bad timestamp
        # (the full parser does) but the window path does not.
        assert index.stats(T0 + timedelta(hours=9))[0]["input_tokens"] == 7 + 9
        assert index.window_usage(T0 + timedelta(hours=9))["input_tokens"] == 0

    def test_crlf_line_endings(self, tmp_path):
        path = tmp_path / "s.jsonl"
        w = _Writer(path)
        index = TranscriptIndex(str(path))
        w.write("\r\n".join(_turns(T0, 5)) + "\r\n")
        _check(index, path, SINCES, WINDOWS)

    def test_unsettled_mtimes_are_reread_not_trusted(self, tmp_path):
        """Writes stamped 'now' (within the settle window) are re-checked each query."""
        path = tmp_path / "s.jsonl"
        index = TranscriptIndex(str(path))
        lines = _turns(T0, 6)
        path.write_text("\n".join(lines[:5]) + "\n")
        _check(index, path, SINCES, WINDOWS)
        with open(path, "a") as f:
            f.write("\n".join(lines[5:]) + "\n")
        _check(index, path, SINCES, WINDOWS)
        path.write_text("\n".join(lines[:3]) + "\n")
        _check(index, path, SINCES, WINDOWS)

    def test_large_lines_span_chunks(self, tmp_path, monkeypatch):
        from overcode import transcript_index

        monkeypatch.setattr(transcript_index, "CHUNK_BYTES", 256)
        path = tmp_path / "s.jsonl"
        w = _Writer(path)
        index = TranscriptIndex(str(path))
        lines = [_user(T0, "x" * 2000), _assistant(T0 + timedelta(seconds=1), inp=3)]
        lines += _turns(T0 + timedelta(minutes=1), 3)
        w.write("\n".join(lines) + "\n")
        _check(index, path, SINCES, WINDOWS)
        w.append(_user(T0 + timedelta(hours=1), "y" * 5000) + "\n")
        _check(index, path, SINCES, WINDOWS)
        w.append(_user(T0 + timedelta(hours=2), "z" * 700))  # no newline
        _check(index, path, SINCES, WINDOWS)


class TestSinceStates:
    def test_new_since_after_window_only_use_rebuilds_once(self, tmp_path):
        path = tmp_path / "s.jsonl"
        w = _Writer(path)
        index = TranscriptIndex(str(path))
        w.write("\n".join(_turns(T0, 8)) + "\n")
        index.window_usage(T0)
        since = T0 + timedelta(minutes=2)
        assert index.stats(since) == full_stats(path, since)
        assert index.stats(None) == full_stats(path, None)
        w.append(_user(T0 + timedelta(days=1)) + "\n")
        _check(index, path, [since, None], WINDOWS)

    def test_state_cap_evicts_least_recently_used(self, tmp_path):
        path = tmp_path / "s.jsonl"
        w = _Writer(path)
        index = TranscriptIndex(str(path))
        w.write("\n".join(_turns(T0, 8)) + "\n")
        sinces = [T0 + timedelta(minutes=i) for i in range(MAX_STATES_PER_INDEX + 2)]
        for s in sinces:
            assert index.stats(s) == full_stats(path, s)
        assert len(index._states) == MAX_STATES_PER_INDEX
        for s in sinces:  # evicted ones are rebuilt, still exact
            assert index.stats(s) == full_stats(path, s)

    def test_aware_since_is_left_to_the_wrapper(self):
        # datetime_key ignores tzinfo; the wrappers route aware datetimes
        # to the full parsers. This pins that the key itself orders naively.
        a = datetime(2026, 1, 1, 0, 0, 0, 1)
        b = datetime(2026, 1, 1, 0, 0, 0, 2)
        assert datetime_key(a) < datetime_key(b)
        assert datetime_key(datetime(2025, 12, 31, 23, 59, 59, 999999)) < datetime_key(a)


class TestParsing:
    def test_parse_message_time_matches_full_parser(self):
        for ts in [
            "2026-03-10T14:00:00.000Z",
            "2026-03-10T14:00:00Z",
            "2026-03-10T14:00:00+02:00",
            "2026-03-10T14:00:00",
        ]:
            expected = (
                datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
            )
            assert parse_message_time(ts) == expected
        assert parse_message_time("garbage") is None
        assert parse_message_time(None) is None
        assert parse_message_time(12345) is None

    def test_datetime_key_orders_like_datetime(self):
        rng = random.Random(4)
        pts = [
            datetime(
                rng.randint(1970, 2100),
                rng.randint(1, 12),
                rng.randint(1, 28),
                rng.randint(0, 23),
                rng.randint(0, 59),
                rng.randint(0, 59),
                rng.randint(0, 999999),
            )
            for _ in range(500)
        ]
        assert sorted(pts) == sorted(pts, key=datetime_key)
        assert [datetime_key(p) for p in sorted(pts)] == sorted(datetime_key(p) for p in pts)


class TestThreadSafety:
    def test_two_threads_updating_the_same_index(self, tmp_path):
        path = tmp_path / "s.jsonl"
        w = _Writer(path)
        index = TranscriptIndex(str(path))
        base = _turns(T0, 20)
        w.write("\n".join(base) + "\n")
        errors = []
        stop = threading.Event()

        def reader():
            try:
                while not stop.is_set():
                    for since in (None, T0 + timedelta(minutes=1)):
                        totals, work = index.stats(since)
                        assert totals["input_tokens"] >= 0 and all(x > 0 for x in work)
                    index.window_usage(T0)
            except Exception as e:  # pragma: no cover - reported below
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(2)]
        for t in threads:
            t.start()
        for i in range(30):
            w.append("\n".join(_turns(T0 + timedelta(hours=i + 1), 2, seed=i)) + "\n")
        stop.set()
        for t in threads:
            t.join(timeout=30)
        assert not errors
        _check(index, path, SINCES, WINDOWS)


class TestRegistry:
    def _file(self, tmp_path, name, n=3):
        path = tmp_path / f"{name}.jsonl"
        _Writer(path).write("\n".join(_turns(T0, n)) + "\n")
        return path

    def test_same_path_same_index(self, tmp_path):
        reg = TranscriptRegistry()
        path = self._file(tmp_path, "a")
        assert reg.get(path) is reg.get(str(path))
        assert reg.stats(path) == full_stats(path)
        assert reg.window_usage(path, T0) == full_window(path, T0)
        assert len(reg) == 1

    def test_lru_eviction_by_count(self, tmp_path):
        reg = TranscriptRegistry(max_indexes=3)
        paths = [self._file(tmp_path, f"p{i}") for i in range(5)]
        for p in paths:
            reg.stats(p)
        assert len(reg) == 3
        assert paths[0] not in reg and paths[1] not in reg
        assert all(p in reg for p in paths[2:])
        reg.stats(paths[2])  # touch: now the LRU is paths[3]
        reg.stats(paths[0])
        assert paths[3] not in reg and paths[2] in reg
        # evicted entries are simply re-parsed
        for p in paths:
            assert reg.stats(p) == full_stats(p)

    def test_lru_eviction_by_retained_records(self, tmp_path):
        reg = TranscriptRegistry(max_records=20)
        big = self._file(tmp_path, "big", n=10)  # 20 assistant messages
        small = self._file(tmp_path, "small", n=1)  # 2
        reg.window_usage(small, T0)
        reg.window_usage(big, T0)
        assert reg.record_count <= 20 + 20  # the just-used index is never evicted
        assert big in reg and small not in reg
        reg.window_usage(small, T0)
        assert small in reg and big not in reg
        assert reg.record_count == 2
        assert reg.window_usage(big, T0) == full_window(big, T0)

    def test_clear(self, tmp_path):
        reg = TranscriptRegistry()
        path = self._file(tmp_path, "a")
        reg.stats(path)
        reg.clear()
        assert len(reg) == 0 and reg.record_count == 0
        assert reg.stats(path) == full_stats(path)

    def test_concurrent_registry_use(self, tmp_path):
        reg = TranscriptRegistry(max_indexes=4)
        paths = [self._file(tmp_path, f"p{i}") for i in range(8)]
        errors = []

        def worker(seed):
            rng = random.Random(seed)
            try:
                for _ in range(100):
                    p = rng.choice(paths)
                    assert reg.stats(p) == full_stats(p)
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert not errors
        assert len(reg) <= 4
