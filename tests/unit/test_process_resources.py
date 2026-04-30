"""Tests for process_resources."""

from overcode.process_resources import (
    ProcInfo,
    aggregate_tree,
    build_children_index,
    descendant_pids,
)


def _mk(ppid, cpu=0.0, rss=0, argv=""):
    return ProcInfo(ppid=ppid, cpu_pct=cpu, rss_kb=rss, argv=argv)


def test_descendants_walks_tree_inclusive():
    snap = {
        100: _mk(1),
        101: _mk(100),
        102: _mk(100),
        103: _mk(101),
        999: _mk(1),  # unrelated
    }
    idx = build_children_index(snap)
    pids = sorted(descendant_pids(100, idx))
    assert pids == [100, 101, 102, 103]


def test_aggregate_tree_sums_cpu_and_rss():
    snap = {
        100: _mk(1, cpu=10.0, rss=1024),
        101: _mk(100, cpu=50.0, rss=2048),
        102: _mk(101, cpu=200.0, rss=4096),  # grandchild — runaway tsc
        999: _mk(1, cpu=30.0, rss=8192),     # unrelated
    }
    cpu, rss_bytes = aggregate_tree(100, snap)
    assert cpu == 260.0
    assert rss_bytes == (1024 + 2048 + 4096) * 1024


def test_aggregate_tree_missing_root_returns_zeros():
    assert aggregate_tree(9999, {100: _mk(1, cpu=50)}) == (0.0, 0)


def test_descendants_bounded_depth():
    # Long chain 0 -> 1 -> 2 -> ... -> 20
    snap = {i: _mk(i - 1) for i in range(1, 21)}
    snap[0] = _mk(0)
    idx = build_children_index(snap)
    # Default max_depth=6 means root + children down to depth 6 inclusive
    pids = descendant_pids(0, idx, max_depth=6)
    # Exact count doesn't matter, only that we stop well before 20
    assert len(pids) < 15
