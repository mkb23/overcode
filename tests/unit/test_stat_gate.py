"""Tests for the stat-signature gate behind the shared state-file caches (audit R4)."""

import json
import os
import threading
from pathlib import Path

from overcode.stat_gate import (
    SETTLE_NS,
    FileSignature,
    StatGatedCache,
    is_settled,
    stat_signature,
)


def _settle(path: Path, seconds_ago: float = 1.0) -> None:
    """Move ``path``'s mtime into the past so its signature becomes trusted."""
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns - int(seconds_ago * 1e9)))


def _write(path: Path, payload) -> None:
    """Truncate+dump in place — the same inode, like ``_locked_state``."""
    with open(path, "w") as f:
        json.dump(payload, f)


class _Reader:
    """A ``read`` callable that counts parses and takes the fstat signature."""

    def __init__(self, path: Path):
        self.path = path
        self.calls = 0

    def __call__(self):
        self.calls += 1
        try:
            with open(self.path) as f:
                sig = FileSignature.of(os.fstat(f.fileno()))
                return sig, json.load(f)
        except FileNotFoundError:
            return None, None


class TestSignature:
    def test_stat_signature_of_missing_file_is_none(self, tmp_path):
        assert stat_signature(tmp_path / "nope.json") is None

    def test_signature_fields_come_from_stat(self, tmp_path):
        path = tmp_path / "f.json"
        _write(path, {"a": 1})
        st = os.stat(path)
        assert stat_signature(path) == FileSignature(st.st_mtime_ns, st.st_size, st.st_ino)

    def test_is_settled_needs_the_stamp_older_than_settle_ns(self):
        sig = FileSignature(mtime_ns=1_000_000_000_000, size=1, ino=1)
        assert not is_settled(sig, now_ns=sig.mtime_ns + SETTLE_NS)
        assert is_settled(sig, now_ns=sig.mtime_ns + SETTLE_NS + 1)
        # A clock that stepped backwards never trusts a stamp from the future
        assert not is_settled(sig, now_ns=sig.mtime_ns - 1)


class TestStatGatedCache:
    def test_unchanged_settled_file_is_parsed_once(self, tmp_path):
        path = tmp_path / "f.json"
        _write(path, {"v": 1})
        _settle(path)
        reader = _Reader(path)
        cache = StatGatedCache()
        first = cache.get(path, reader)
        for _ in range(5):
            assert cache.get(path, reader) is first
        assert reader.calls == 1
        assert cache.signature == stat_signature(path)

    def test_same_size_in_place_rewrite_is_seen(self, tmp_path):
        path = tmp_path / "f.json"
        _write(path, {"v": 1})
        _settle(path, 2.0)
        reader = _Reader(path)
        cache = StatGatedCache()
        assert cache.get(path, reader) == {"v": 1}
        _write(path, {"v": 2})  # same byte length, same inode: only mtime_ns moves
        _settle(path, 1.0)
        assert cache.get(path, reader) == {"v": 2}
        assert reader.calls == 2

    def test_rename_replacement_is_seen(self, tmp_path):
        path = tmp_path / "f.json"
        _write(path, {"v": 1})
        _settle(path)
        reader = _Reader(path)
        cache = StatGatedCache()
        cache.get(path, reader)
        tmp = tmp_path / "f.json.tmp"
        _write(tmp, {"v": 2})
        _settle(tmp)
        tmp.rename(path)
        assert cache.get(path, reader) == {"v": 2}

    def test_just_written_file_is_returned_but_not_remembered(self, tmp_path):
        """The settle rule: a parse of a fresh mtime is never trusted.

        On a coarse-timestamp filesystem two same-size writes inside one clock
        tick share a signature; simulate that by giving the second write the
        first write's stamp. The cache must still return the second content,
        which it only can if it never remembered the first parse.
        """
        path = tmp_path / "f.json"
        _write(path, {"v": 1})
        stamp = os.stat(path).st_mtime_ns  # fresh: within SETTLE_NS of now
        reader = _Reader(path)
        cache = StatGatedCache()
        assert cache.get(path, reader) == {"v": 1}
        assert cache.signature is None  # not trusted yet
        _write(path, {"v": 2})
        os.utime(path, ns=(stamp, stamp))  # same signature as the first write
        assert cache.get(path, reader) == {"v": 2}
        assert reader.calls == 2
        # Once the stamp has aged, the next parse is remembered and reused
        _settle(path)
        assert cache.get(path, reader) == {"v": 2}
        assert cache.signature is not None
        assert cache.get(path, reader) == {"v": 2}
        assert reader.calls == 3

    def test_missing_file_passes_the_value_through_uncached(self, tmp_path):
        path = tmp_path / "f.json"
        reader = _Reader(path)
        cache = StatGatedCache()
        assert cache.get(path, reader) is None
        assert cache.get(path, reader) is None
        assert reader.calls == 2
        assert cache.signature is None

    def test_file_disappearing_drops_the_remembered_parse(self, tmp_path):
        path = tmp_path / "f.json"
        _write(path, {"v": 1})
        _settle(path)
        reader = _Reader(path)
        cache = StatGatedCache()
        cache.get(path, reader)
        path.unlink()
        assert cache.get(path, reader) is None
        assert cache.signature is None

    def test_invalidate_forces_a_reparse(self, tmp_path):
        path = tmp_path / "f.json"
        _write(path, {"v": 1})
        _settle(path)
        reader = _Reader(path)
        cache = StatGatedCache()
        cache.get(path, reader)
        cache.invalidate()
        assert cache.signature is None
        cache.get(path, reader)
        assert reader.calls == 2

    def test_concurrent_readers_share_one_parse(self, tmp_path):
        path = tmp_path / "f.json"
        _write(path, {"v": 1})
        _settle(path)
        reader = _Reader(path)
        cache = StatGatedCache()
        results = []
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            results.append(cache.get(path, reader))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert reader.calls == 1
        assert all(r is results[0] for r in results)
