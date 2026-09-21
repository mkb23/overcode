# opencode stats reader performance (#476)

**Status:** Implemented in 0.5.4
**Date:** September 2026
**Scope:** `backends/opencode_stats.py`, `backends/opencode2_stats.py`, the TUI's
per-agent stats and burn-rate polling

## Summary

Issue #476 reported laggy agent switching in the TUI with opencode agents,
and came with a patch adding a 60-second TTL cache around the opencode stats
reader. The measured cost behind the report was real (75 ms per window
call, 115 ms per stats call, per agent, against a 1.9 GB `opencode.db`), but
the patch treated the symptom. Profiling found two independent costs in the
readers' message scan, both of which grow with normal use:

1. **The query shape** was quadratic in the number of conversations an agent
   owns. Every `/new` adds an id, and the scan fetched all of them with one
   `WHERE session_id IN (...) ORDER BY time_created DESC LIMIT 500*N`. SQLite
   answers that through a temp B-tree sorter fed by every candidate row.
2. **The row bodies.** On a real store an opencode2 assistant row carries its
   tool output inline in `content` (median ~1 KB, p90 ~9 KB, tail past
   1 MB). The scan fetched and JSON-parsed 500 bodies per conversation on
   every call, and the TUI makes that call once a second per agent.

Both are fixed at the source: one bounded, index-ordered probe per
conversation, selecting only the narrow columns, and a per-row parse cache
so a body is read once per `(store, row, time_updated)`. On a generated
2.3 GB store an opencode2 `get_stats` with eight owned conversations fell
from 254 ms to 9.6 ms; opencode from 23 ms to 6.7 ms. No TTL, no staleness.

## Why the TTL patch was declined

- It hid a quadratic query rather than fixing it, so the cost would have
  returned on every cache miss and grown with each `/new`.
- It degraded the TUI's opencode stats freshness from 5 s to 60 s while every
  other backend stayed at 5 s.
- It patched only the v1 reader; opencode2 shares the database and had the
  same query shape (and, it turned out, the bigger body problem).
- Its rationale for module-level caches ("`stats_reader_for_session` hands out
  a fresh reader per call") was wrong: `_READERS` memoizes one reader per
  backend name.
- Its window cache keyed on a 60 s wall-clock bucket and never evicted, so
  it leaked one entry per agent per minute.

## Who calls the readers, and how often

| caller | cadence | call |
|---|---|---|
| TUI `_fetch_daemon_status_async` → `compute_window_burn` | 1 Hz, every awake agent | `get_window_token_usage` |
| TUI `_update_stats_async` | every 5 s, every agent, 8-thread pool | `get_stats` |
| monitor daemon `sync_stats` | every 60 s | `get_stats`, `get_stored_cost` |

The 1 Hz burn-rate worker is a Textual `exclusive` thread worker. Thread
workers cannot be killed, so when one call takes longer than a second the
old scans keep running alongside the new ones and the pile-up is what the
user feels as lag. Cutting per-call cost is the only thing that stops it.

## Profiling

### Setup

The local store was 17 MB with 123 messages, far too small to reproduce the
report. Two synthetic stores were used:

- A unit-test fixture (`tests/unit/test_opencode_scan_cost.py`): eight
  conversations of 2,500 messages with 2 KB bodies, written with opencode's
  own indexes. The reader fixtures elsewhere in the suite have no indexes,
  which hides the difference between query shapes.
- A generated 2.3 GB store (`scripts/make_opencode_store.py --size-gb 2`):
  1,680 v1 and 1,568 v2 conversations, 137k `message` rows with 274k `part`
  rows, and 142k `session_message` rows. Row shapes and size distributions
  were calibrated against the live store: v2 assistant bodies are log-normal
  around a ~1.5 KB median with a 1 MB cap, v1 bodies are ~400 B with the
  bulk in `part`, conversations are mostly a dozen turns with a 5 % tail of
  200-1,200 turns.

`scripts/bench_opencode_scan.py` times `get_stats` and
`get_window_token_usage` against whichever store `OPENCODE_DB` names, with
1, 3 and 8 owned ids drawn from the busiest conversations.

### Finding 1: the shared sort is quadratic in owned ids

Raw SQL on the fixture (8 conversations × 4,000 rows, 1.5 KB bodies):

| owned ids | `IN (...) ORDER BY ... LIMIT 500N` | per-id probes, `UNION ALL` |
|---|---|---|
| 1 | 0.3 ms | 0.4 ms |
| 2 | 6.9 ms | 1.1 ms |
| 3 | 14.6 ms | 1.8 ms |
| 5 | 34.7 ms | 3.8 ms |
| 8 | 73.5 ms | 6.3 ms |

`EXPLAIN QUERY PLAN` shows the difference: the shared form is
`SEARCH message USING INDEX ... (session_id=?)` followed by
`USE TEMP B-TREE FOR ORDER BY`. SQLite walks each conversation's index
backwards, but every row that might make the cut is materialised into the
sorter before the `LIMIT` applies, and with `LIMIT 500*N` each conversation
can contribute up to `500*N` candidates. The result is roughly
`N² × 500` record copies. With one id the index walk stops at 500 and the
sort is skipped, which is why the problem only appears after `/new`.

Through the reader, this reproduced the issue's number almost exactly:
`get_stats` with 8 owned ids cost 117 ms on the fixture against the 115 ms
in the report.

The opencode2 reader was worse. Its `ORDER BY time_created DESC, seq DESC`
cannot be served by the `(session_id, time_created, id)` index, so the
per-conversation walk also needs a sort: 157x from one to eight ids on the
fixture, against 22x for v1.

### Finding 2: the bodies dominate on a real store

After fixing the query shape, the 2.3 GB store still showed opencode2 at
7.6 ms per call with a single owned id and 67 ms with eight. Decomposing one
conversation's probe:

| step | cost |
|---|---|
| narrow probe (`id, session_id, type, seq, time_created, time_updated`), 500 rows | 0.5 ms |
| the same probe with `data` | 1.0 ms (3 MB fetched) |
| `json.loads` of those 500 bodies | 2.4 ms |
| outer SQL `ORDER BY` over 8 × 500 rows with bodies | ~4 ms |
| Python sort of 4,000 narrow rows | 0.2 ms |
| fetch 5 bodies by primary key | 0.01 ms |

Every column but `data` sits in the row's first page, so the narrow probe
never touches SQLite's overflow pages. The per-row cost is in copying and
parsing bodies that have not changed since the previous second.

### Results on the 2.3 GB store

`get_stats`, mean of 20 calls, busiest conversations:

| reader | ids | unfixed | query fix only | query fix + row cache |
|---|---|---|---|---|
| opencode | 1 | 1.7 ms | 1.8 ms | 1.1 ms |
| opencode | 3 | 12.4 ms | 5.4 ms | 2.6 ms |
| opencode | 8 | 23.3 ms | 15.0 ms | 6.7 ms |
| opencode2 | 1 | 4.5 ms | 7.6 ms | 1.2 ms |
| opencode2 | 3 | 157.4 ms | 22.5 ms | 3.5 ms |
| opencode2 | 8 | 254.4 ms | 67.2 ms | 9.6 ms |

`get_window_token_usage` tracks the same numbers within a millisecond. The
query-fix-only column is worse than unfixed for opencode2 at one id because
its outer `UNION ALL ... ORDER BY` re-materialised the bodies; the final
design sorts narrow rows in Python instead.

## Design

### Per-conversation probes (`_scan_sql`)

```sql
SELECT * FROM (SELECT id, session_id, time_created, time_updated
               FROM message WHERE session_id = ?
               ORDER BY time_created DESC LIMIT 500)
UNION ALL ...
```

One sub-select per owned id, each walking its index backwards and stopping
at its own limit. There is no outer `ORDER BY`; the caller sorts the merged
rows in Python (`time_created` for v1, `(time_created, seq)` for v2). The
opencode2 probe orders by `seq DESC` on the unique `(session_id, seq)`
index: `seq` is assigned in insertion order within a conversation, so the
newest 500 by `seq` are the newest 500 by time and no sort is needed.

This also changes one documented-but-unmet behaviour: `_MESSAGE_SCAN_LIMIT`
was always described as per session, but the shared `LIMIT 500*N` let the
newest conversations swallow the whole budget. The test pins the
difference: on the fixture the old scan counted 2,000 interactions where
2,500 is correct.

### Per-row parse cache (`_cached_records`)

The probe returns `(id, time_updated)` for each row. The cache, a
module-level dict in `opencode_stats.py` shared by both readers, maps
`(store file, table, row id)` to `(time_updated, record)`, where a record
is the handful of extracted fields the scan consumes (role or type, token
counts, `time.created`/`completed`, and for v2 the model and agent). Rows
whose `time_updated` matches are served from the cache; the rest are
fetched by primary key in one query and parsed.

Invalidation relies on opencode rewriting `time_updated` whenever it
rewrites a row. The live store confirms this for completed assistant turns
(their `time_updated` equals `time.completed`). An assistant row that has
not completed yet is never cached, so a turn that is still streaming is
re-read every tick until it finishes and a stale key cannot freeze it.
User rows are immutable. Unparseable rows are not cached either.

The store file is read from `PRAGMA database_list` on each call so entries
never cross databases: the unit suites reuse row ids like `msg_a1` across
many temporary stores. The cache is bounded at 50,000 records and drops
its oldest half when full; a record is a few hundred bytes, so the ceiling
is a few megabytes. Dict reads are lock-free; inserts and eviction take a
lock, and the worst race is one duplicated parse.

### What was considered and not done

- **TTL result cache** (the original patch): see above.
- **`PRAGMA data_version` or file mtime** as an invalidation key: the store
  is shared by every opencode agent on the machine, so any agent's write
  would invalidate every agent's entry.
- **`session.time_updated` as a version key**: on the live store it lags the
  newest message's `time_updated` by up to tens of seconds, so it can miss a
  late row rewrite.
- **`octet_length(data)` in the narrow probe** as an extra version signal:
  it would catch an in-place rewrite that keeps `time_updated`, but it needs
  SQLite 3.43 and the observed writer does not do that.
- **Moving the scan to the daemon** so the TUI reads pre-computed numbers:
  a larger change to the TUI/daemon contract, and the per-call cost is now
  low enough not to need it.

## Verification

- `tests/unit/test_opencode_scan_cost.py`: 18 tests. Query shape
  (deterministic), the per-conversation budget, a 1-versus-8-ids timing
  ratio with a 15x ceiling (old code 22x and 83-157x, new code 9-10x), and
  the row cache's behaviours: bodies fetched once, in-flight rows re-read
  until they complete, rewritten rows re-parsed, stores never sharing
  entries, bounded size.
- Full unit suite and the mock backend matrix (`make test-matrix`) pass.
- Not verified here: the readers against the 1.9 GB store that produced the
  original report. `scripts/bench_opencode_scan.py` exists for that.

## Open questions

- The daemon's `sync_stats` at 60 s and the TUI's own 5 s refresh both call
  `get_stats`; the TUI could read the daemon's numbers for backends whose
  stats are cheap to compute, but that is a separate contract change.
- The Claude backend's `read_window_token_usage` re-parses JSONL at the
  same 1 Hz cadence with no cache. It has not been reported as a problem.

## References

- Issue #476
- `scripts/make_opencode_store.py`, `scripts/bench_opencode_scan.py`
- `docs/design/agent-agnostic-backends-opencode.md` (the reader's origin)
