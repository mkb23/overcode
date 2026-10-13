"""Storage at a year's scale: archive, ledger, agent directories (item 35).

A heavy fan-out user launches ~165 agents a day: ~60,000 a year. Synthetic
fixture (no real data): 12 months of slim archive records (11 gzipped, the
current one plain), a ledger with a final row per agent plus checkpoints,
thousands of dead pre-0.6 hook files beside 50 live agents' directories.
The budgets are the design's (scale-design, "numbers"): the hot paths stay
the size of the live fleet, whatever the history.

Run with ``OVERCODE_SCALE_TESTS=1 uv run pytest tests/scale -q -p no:cacheprovider``.
"""

import gzip
import json
import os
import time
from datetime import datetime, timedelta

import pytest

from tests.scale.budget import budget

pytestmark = [pytest.mark.scale]

AGENTS_PER_MONTH = 5000
MONTHS = 12
LIVE = 50
DEAD_HOOK_FILES = 10_000
NOW = datetime(2026, 10, 12, 12, 0, 0)


def _month(i: int) -> str:
    year, month = NOW.year, NOW.month - (MONTHS - 1 - i)
    while month <= 0:
        month += 12
        year -= 1
    return f"{year:04d}-{month:02d}"


def _record(i: int, month: str) -> dict:
    """A slim archive record of the size the real ones are (~1 KB)."""
    return {
        "id": f"sid-{i:06d}", "name": f"worker-{i % 997}", "previous_names": [],
        "tmux_session": "agents", "tmux_window": f"worker-{i % 997}-{i:04x}"[:20],
        "parent_session_id": f"sid-{i - i % 20:06d}" if i % 20 else None,
        "billing_code": f"project-{i % 13}", "tags": ["batch"] if i % 7 == 0 else [],
        "backend": "claude-code", "model": "sonnet", "skill_profile": None, "wrapper": None,
        "heartbeat_enabled": False, "cost_budget_usd": 0.0, "human_annotation": "",
        "agent_value": 1000, "start_directory": f"/work/repo-{i % 31}",
        "repo_name": f"repo-{i % 31}", "branch": "main",
        "start_time": f"{month}-05T10:00:00", "end_time": f"{month}-05T11:00:00",
        "status": "archived", "report_status": "success", "report_reason": "",
        "stats": {"interaction_count": 12, "estimated_cost_usd": 0.42, "total_tokens": 120000,
                  "input_tokens": 80000, "output_tokens": 20000, "cache_read_tokens": 20000,
                  "green_time_seconds": 1800.0, "non_green_time_seconds": 600.0,
                  "steers_count": 1, "current_task": "Wrote the tests and reported back " * 3},
        "bundle": {"day": f"{month}-05", "off": i * 9000, "len": 9000},
        "command": [], "has_standing_instructions": False,
    }


def _ledger_row(i: int, month: str, kind: str, cost: float) -> dict:
    return {"t": f"{month}-05T11:00:00", "seq": i * 10 + (kind == "final"),
            "sid": f"sid-{i:06d}", "name": f"worker-{i % 997}", "parent": None,
            "billing_code": f"project-{i % 13}", "seg": 0, "model": "sonnet", "cost": cost,
            "tokens": 120000, "tok_in": 80000, "tok_out": 20000, "tok_cache": 20000,
            "base_cost": 0.0, "base_tokens": 0, "kind": kind}


@pytest.fixture(scope="module")
def year(tmp_path_factory):
    """A year of history (see the module docstring), and the manager over it."""
    from overcode.session_manager import SessionManager

    root = tmp_path_factory.mktemp("storage-year")
    previous = os.environ.get("OVERCODE_STATE_DIR")
    os.environ["OVERCODE_STATE_DIR"] = str(root / "sessions")
    sm = SessionManager(skip_git_detection=True)
    sm.archive_dir.mkdir(parents=True)
    sm.ledger.dir.mkdir(parents=True)
    i = 0
    for m in range(MONTHS):
        month = _month(m)
        archive_lines, ledger_lines = [], []
        for _ in range(AGENTS_PER_MONTH):
            archive_lines.append(json.dumps(_record(i, month)))
            ledger_lines.append(json.dumps(_ledger_row(i, month, "checkpoint", 0.2)))
            ledger_lines.append(json.dumps(_ledger_row(i, month, "final", 0.42)))
            i += 1
        archive = "\n".join(archive_lines).encode() + b"\n"
        ledger = "\n".join(ledger_lines).encode() + b"\n"
        if m == MONTHS - 1:
            (sm.archive_dir / f"{month}.jsonl").write_bytes(archive)
            (sm.ledger.dir / f"{month}.jsonl").write_bytes(ledger)
        else:
            (sm.archive_dir / f"{month}.jsonl.gz").write_bytes(gzip.compress(archive, 1))
            (sm.ledger.dir / f"{month}.jsonl.gz").write_bytes(gzip.compress(ledger, 1))
    session_dir = root / "sessions" / "agents"
    session_dir.mkdir(parents=True)
    for n in range(DEAD_HOOK_FILES):  # what earlier versions never removed
        (session_dir / f"hook_state_dead-{n}.json").write_text('{"event": "Stop"}')
    live = []
    for n in range(LIVE):
        s = sm.create_session(name=f"live-{n}", tmux_session="agents",
                              tmux_window=f"live-{n}-1", command=[])
        d = session_dir / "agents" / s.id
        d.mkdir(parents=True)
        (d / "hook_state.json").write_text('{"event": "Stop", "timestamp": 1}')
        live.append(s)
    yield sm, live, root
    if previous is None:
        os.environ.pop("OVERCODE_STATE_DIR", None)
    else:
        os.environ["OVERCODE_STATE_DIR"] = previous


def _best_ms(fn, runs=5):
    best = float("inf")
    for _ in range(runs):
        start = time.perf_counter()
        fn()
        best = min(best, (time.perf_counter() - start) * 1000)
    return best


class TestHotPaths:
    def test_detection_mode_check_never_touches_dead_files(self, year):
        from overcode.settings import _session_has_hook_activity

        sm, live, _ = year
        pairs = [(s.id, s.name) for s in live]
        ms = _best_ms(lambda: _session_has_hook_activity("agents", pairs), runs=50)
        assert ms <= budget(1.0), f"{ms:.3f} ms with {DEAD_HOOK_FILES} dead hook files"

    def test_wake_scan_is_the_size_of_the_live_fleet(self, year):
        from overcode.engine_wake import WakeScan
        from overcode.monitor_daemon_state import SessionDaemonState

        sm, live, root = year
        scan = WakeScan()
        states = [SessionDaemonState(session_id=s.id, name=s.name) for s in live]
        directory = root / "sessions" / "agents"
        scan.changes(directory, states)
        ms = _best_ms(lambda: scan.changes(directory, states), runs=50)
        assert ms <= budget(1.0), f"{ms:.3f} ms per scan of {LIVE} agents"

    def test_orphan_sweep_with_no_orphans_is_cheap(self, year):
        sm, live, _ = year
        ms = _best_ms(lambda: sm.tidy_agent_storage("agents"), runs=5)
        assert ms <= budget(50), f"{ms:.1f} ms"


class TestHistory:
    def test_a_name_in_the_current_month(self, year):
        sm, _, _ = year
        name = _record(MONTHS * AGENTS_PER_MONTH - 1, _month(MONTHS - 1))["name"]
        ms = _best_ms(lambda: sm.find_archived(name), runs=3)
        assert sm.find_archived(name) is not None
        assert ms <= budget(100), f"{ms:.1f} ms"

    def test_an_id_from_a_year_ago(self, year):
        """The worst case: every month read, newest first, prefiltered."""
        sm, _, _ = year
        ms = _best_ms(lambda: sm.find_archived("sid-000003"), runs=1)
        assert sm.find_archived("sid-000003").id == "sid-000003"
        assert ms <= budget(500), f"{ms:.1f} ms"

    def test_the_last_30_days(self, year):
        sm, _, _ = year
        since = NOW - timedelta(days=30)
        ms = _best_ms(lambda: sm.list_archived_sessions(since=since), runs=1)
        assert ms <= budget(1000), f"{ms:.1f} ms"


class TestLedger:
    def test_totals_fold_once_then_incrementally(self, year):
        sm, _, _ = year
        start = time.perf_counter()
        totals = sm.ledger.totals()
        first = (time.perf_counter() - start) * 1000
        assert len(totals) == 13
        assert sum(t["agents"] for t in totals.values()) == MONTHS * AGENTS_PER_MONTH
        assert first <= budget(3000), f"first fold {first:.0f} ms"
        again = _best_ms(lambda: sm.ledger.totals(), runs=3)
        assert again <= budget(250), f"incremental {again:.1f} ms"


class TestLeakedRecords:
    def test_archiving_2000_leaked_records_is_bounded_per_pass(self, tmp_path, monkeypatch):
        """The first housekeeping pass after the leak fix: 50 archived, the rest wait."""
        from overcode.daemon_housekeeping import ARCHIVE_PER_PASS, Housekeeping
        from overcode.session_manager import PendingUpdates, SessionManager
        from unittest.mock import MagicMock

        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "sessions"))
        sm = SessionManager(skip_git_detection=True)
        state = {}
        for n in range(2000):
            rec = _record(n, "2026-10")
            rec.update(id=f"leak-{n}", status="terminated")
            rec.pop("end_time")
            state[rec["id"]] = rec
            d = tmp_path / "sessions" / "agents" / "agents" / rec["id"]
            d.mkdir(parents=True)
            (d / "hook_state.json").write_text('{"event": "Stop"}')
        sm._save_state(state)
        hk = Housekeeping("agents", tmux=MagicMock(), history_path=None, log=MagicMock(),
                          housekeeping=MagicMock(), rotation=MagicMock(),
                          model_metadata=MagicMock())
        sessions = sm.list_sessions()
        now = datetime.now()
        hk.archive_terminated_sessions(sessions, now, PendingUpdates())  # clocks start
        pending = PendingUpdates()
        hk.archive_terminated_sessions(sessions, now + timedelta(hours=2), pending)
        assert len(pending.archive) == ARCHIVE_PER_PASS
        start = time.perf_counter()
        sm.commit_pending(pending)
        ms = (time.perf_counter() - start) * 1000
        assert len(sm.list_sessions()) == 2000 - ARCHIVE_PER_PASS
        assert ms <= budget(300), f"{ms:.0f} ms to pack, archive and commit {ARCHIVE_PER_PASS}"
