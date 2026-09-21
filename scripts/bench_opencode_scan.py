"""Time the opencode/opencode2 stats readers against the local opencode store.

Companion to #476 and ``tests/unit/test_opencode_scan_cost.py``. Picks the
busiest conversations in ``opencode.db`` and times ``get_stats`` with 1, 3
and 8 owned ids — the shape of an agent that has run ``/new`` a few times.
Run it before and after a change (``git stash`` / checkout) to compare:

    python scripts/bench_opencode_scan.py

Honours ``OPENCODE_DB`` / ``OPENCODE_DATA_DIR`` like the readers do.
"""

import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from overcode.backends import opencode2_stats as v2  # noqa: E402
from overcode.backends import opencode_stats as v1  # noqa: E402


def session_with(ids):
    return SimpleNamespace(
        agent_session_ids=ids,
        active_agent_session_id=ids[-1],
        start_directory="/nonexistent",
        start_time=datetime(2026, 1, 1).isoformat(),
        tmux_session=None,
        name=None,
    )


def mean_ms(fn, runs=20):
    fn()  # warm the page cache
    start = time.perf_counter()
    for _ in range(runs):
        fn()
    return (time.perf_counter() - start) / runs * 1000


def busiest(conn, table, where=""):
    sql = f"SELECT session_id FROM {table} {where} GROUP BY session_id ORDER BY COUNT(*) DESC LIMIT 8"
    try:
        return [row[0] for row in conn.execute(sql)]
    except sqlite3.Error:
        return []


def main() -> int:
    db = v1.database_path()
    if not db.exists():
        print(f"no opencode store at {db}")
        return 1
    conn = sqlite3.connect(db)
    ids1 = busiest(conn, "message")
    ids2 = busiest(conn, "session_message", "WHERE type IN ('user', 'assistant')")
    print(f"store: {db} ({db.stat().st_size / 1e6:.0f} MB)")
    for label, table in (("v1 message", "message"), ("v2 session_message", "session_message")):
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.Error:
            n = "absent"
        print(f"  {label}: {n} rows")
    conn.close()

    readers = []
    if ids1:
        readers.append(("opencode", v1.OpencodeStatsReader(), ids1))
    if ids2:
        readers.append(("opencode2", v2.Opencode2StatsReader(), ids2))
    for name, reader, ids in readers:
        print(f"\n{name} (busiest {len(ids)} conversations), mean of 20 calls:")
        for n in (1, 3, 8):
            if n > len(ids):
                break
            stats = mean_ms(lambda: reader.get_stats(session_with(ids[:n])))
            window = mean_ms(
                lambda: reader.get_window_token_usage(
                    session_with(ids[:n]), datetime(2026, 1, 1)
                )
            )
            print(f"  ids={n}  get_stats {stats:7.1f} ms   window {window:7.1f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
