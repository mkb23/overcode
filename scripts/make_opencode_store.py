"""Generate a large, realistic opencode store for reader benchmarks (#476).

The unit fixtures are a few rows; a real ``opencode.db`` after months of
use is gigabytes, and that is where the stats readers' cost shows. This
writes the live schema (v1 ``session``/``message``/``part`` and v2
``session_v2``/``session_message``, with opencode's own indexes) and fills
it with conversations whose row shapes and sizes follow a live store:

* v2 assistant rows carry their tool output inline in ``content``; sizes
  are log-normal with a ~1-2 KB median and a tail past 1 MB (observed
  p90 8.6 KB, max 1.2 MB).
* v1 assistant ``message`` rows are small (~400 B); the bulk lives in
  ``part`` rows (``state.output`` of tool calls) attached to them.
* Conversations are mostly short (a dozen turns) with a long tail, and
  every turn is 10-90 s apart.

Usage:

    python scripts/make_opencode_store.py --out /tmp/meaty/opencode.db --size-gb 2
    OPENCODE_DB=/tmp/meaty/opencode.db python scripts/bench_opencode_scan.py

The generator is deterministic for a given ``--seed``. Content is
synthetic filler, never copied from a real store.
"""

import argparse
import json
import math
import random
import sqlite3
import sys
import time
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=OFF;
CREATE TABLE session (
    id TEXT PRIMARY KEY, project_id TEXT, workspace_id TEXT, parent_id TEXT,
    slug TEXT, directory TEXT, path TEXT, title TEXT, version TEXT,
    share_url TEXT, summary_additions INTEGER, summary_deletions INTEGER,
    summary_files INTEGER, summary_diffs TEXT, metadata TEXT, cost REAL,
    tokens_input INTEGER, tokens_output INTEGER, tokens_reasoning INTEGER,
    tokens_cache_read INTEGER, tokens_cache_write INTEGER, revert TEXT,
    permission TEXT, agent TEXT, model TEXT, time_created INTEGER,
    time_updated INTEGER, time_compacting INTEGER, time_archived INTEGER
);
CREATE INDEX session_parent_idx ON session (parent_id);
CREATE INDEX session_project_idx ON session (project_id);
CREATE TABLE message (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
    time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX message_session_time_created_id_idx
    ON message (session_id, time_created, id);
CREATE TABLE part (
    id TEXT PRIMARY KEY, message_id TEXT NOT NULL, session_id TEXT NOT NULL,
    time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX part_message_id_id_idx ON part (message_id, id);
CREATE INDEX part_session_idx ON part (session_id);

CREATE TABLE session_v2 (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, workspace_id TEXT,
    parent_id TEXT, fork_session_id TEXT, fork_boundary TEXT,
    slug TEXT NOT NULL, directory TEXT NOT NULL, path TEXT, title TEXT,
    version TEXT NOT NULL, share_url TEXT, summary_additions INTEGER,
    summary_deletions INTEGER, summary_files INTEGER, summary_diffs TEXT,
    metadata TEXT, cost REAL DEFAULT 0 NOT NULL,
    tokens_input INTEGER DEFAULT 0 NOT NULL,
    tokens_output INTEGER DEFAULT 0 NOT NULL,
    tokens_reasoning INTEGER DEFAULT 0 NOT NULL,
    tokens_cache_read INTEGER DEFAULT 0 NOT NULL,
    tokens_cache_write INTEGER DEFAULT 0 NOT NULL,
    revert TEXT, permission TEXT, agent TEXT, model TEXT,
    time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL,
    time_compacting INTEGER, time_archived INTEGER, time_suspended INTEGER,
    resume_attempts INTEGER DEFAULT 0 NOT NULL, time_idle INTEGER,
    time_viewed INTEGER, idle_outcome TEXT
);
CREATE INDEX session_v2_project_idx ON session_v2 (project_id);
CREATE INDEX session_v2_workspace_idx ON session_v2 (workspace_id);
CREATE INDEX session_v2_parent_idx ON session_v2 (parent_id);
CREATE TABLE session_message (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, type TEXT NOT NULL,
    seq INTEGER NOT NULL, time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL, data TEXT NOT NULL
);
CREATE UNIQUE INDEX session_message_session_seq_idx
    ON session_message (session_id, seq);
CREATE INDEX session_message_session_type_seq_idx
    ON session_message (session_id, type, seq);
CREATE INDEX session_message_session_time_created_id_idx
    ON session_message (session_id, time_created, id);
CREATE INDEX session_message_time_created_idx ON session_message (time_created);
"""

MODELS = [
    ("openai", "gpt-5-mini"),
    ("openai", "gpt-4o-mini"),
    ("anthropic", "claude-haiku-4-5"),
]
TOOLS = ["bash", "read", "edit", "grep", "glob", "write"]
WORDS = (
    "def class return import self path json rows index session token "
    "cache error warning line file diff commit branch test assert fixture "
    "select where order limit insert update delete sqlite thread worker "
).split()

# Observed on a live store: median ~1 KB, p90 ~9 KB, tail to 1.2 MB.
CONTENT_MEDIAN_BYTES = 1500
CONTENT_SIGMA = 1.7
CONTENT_CAP_BYTES = 1 << 20


class Filler:
    """Fast synthetic text: a big pool of word soup, sliced to size."""

    def __init__(self, rng: random.Random, pool_bytes: int = 4 << 20) -> None:
        self._pool = " ".join(rng.choices(WORDS, k=pool_bytes // 6))[:pool_bytes]
        self._rng = rng

    def text(self, size: int) -> str:
        size = max(1, min(size, len(self._pool) - 1))
        start = self._rng.randrange(0, len(self._pool) - size)
        return self._pool[start : start + size]


def content_size(rng: random.Random) -> int:
    size = int(rng.lognormvariate(math.log(CONTENT_MEDIAN_BYTES), CONTENT_SIGMA))
    return min(max(size, 40), CONTENT_CAP_BYTES)


def turns_in_conversation(rng: random.Random) -> int:
    """Mostly short conversations, a long tail of very long ones."""
    if rng.random() < 0.05:
        return rng.randint(200, 1200)
    return max(1, int(rng.lognormvariate(math.log(8), 0.9)))


def new_id(rng: random.Random, prefix: str) -> str:
    return prefix + "".join(rng.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=26))


class Generator:
    def __init__(self, conn: sqlite3.Connection, rng: random.Random) -> None:
        self.conn = conn
        self.rng = rng
        self.filler = Filler(rng)
        self.now_ms = int(time.time() * 1000)
        self.clock_ms = self.now_ms - 180 * 86_400_000  # six months ago
        self.bytes_written = 0

    # -- one conversation, both schemas -----------------------------------

    def conversation(self, v2: bool, directory: str, project_id: str) -> None:
        rng = self.rng
        sid = new_id(rng, "ses_")
        provider, model_id = rng.choice(MODELS)
        turns = turns_in_conversation(rng)
        start = self.clock_ms
        t = start
        totals = {"input": 0, "output": 0, "reasoning": 0, "read": 0, "write": 0}
        cost = 0.0
        messages, parts = [], []
        for turn in range(turns):
            t += rng.randint(10_000, 90_000)
            user_text = self.filler.text(rng.randint(20, 300))
            msg_u = new_id(rng, "msg_")
            msg_a = new_id(rng, "msg_")
            a_created = t + rng.randint(200, 900)
            a_completed = a_created + rng.randint(1_500, 60_000)
            tokens = {
                "input": rng.randint(2_000, 120_000),
                "output": rng.randint(20, 4_000),
                "reasoning": rng.choice([0, 0, rng.randint(50, 3_000)]),
                "cache": {"read": rng.randint(0, 100_000), "write": rng.randint(0, 20_000)},
            }
            turn_cost = tokens["input"] * 1e-7 + tokens["output"] * 6e-7
            cost += turn_cost
            for key in ("input", "output", "reasoning"):
                totals[key] += tokens[key]
            totals["read"] += tokens["cache"]["read"]
            totals["write"] += tokens["cache"]["write"]
            n_tools = rng.choice([0, 1, 1, 2, 3, 5])
            tool_calls = [
                (rng.choice(TOOLS), self.filler.text(content_size(rng)))
                for _ in range(n_tools)
            ]
            reply = self.filler.text(rng.randint(30, 1_500))

            if v2:
                content = [
                    {
                        "type": "tool",
                        "tool": name,
                        "callID": new_id(rng, "call_"),
                        "state": {"status": "completed", "input": {"cmd": "x"}, "output": out},
                    }
                    for name, out in tool_calls
                ]
                content.append({"type": "text", "text": reply})
                messages.append((msg_u, sid, "user", 2 * turn + 1, t, t,
                                 json.dumps({"text": user_text, "time": {"created": t}})))
                messages.append((msg_a, sid, "assistant", 2 * turn + 2, a_created, a_completed,
                                 json.dumps({
                                     "agent": "build",
                                     "model": {"providerID": provider, "id": model_id, "variant": "default"},
                                     "content": content,
                                     "finish": "tool-calls" if tool_calls else "stop",
                                     "cost": turn_cost,
                                     "tokens": tokens,
                                     "time": {"created": a_created, "completed": a_completed},
                                 })))
            else:
                messages.append((msg_u, sid, t, t, json.dumps({
                    "role": "user", "time": {"created": t}, "agent": "build",
                    "path": {"cwd": directory, "root": directory},
                })))
                messages.append((msg_a, sid, a_created, a_completed, json.dumps({
                    "parentID": msg_u, "role": "assistant", "mode": "build", "agent": "build",
                    "path": {"cwd": directory, "root": directory}, "cost": turn_cost,
                    "tokens": {"total": tokens["input"] + tokens["output"], **tokens},
                    "modelID": model_id, "providerID": provider,
                    "time": {"created": a_created, "completed": a_completed},
                    "finish": "tool-calls" if tool_calls else "stop",
                })))
                parts.append((new_id(rng, "prt_"), msg_u, sid, t, t,
                              json.dumps({"type": "text", "text": user_text})))
                for name, out in tool_calls:
                    parts.append((new_id(rng, "prt_"), msg_a, sid, a_created, a_completed,
                                  json.dumps({
                                      "type": "tool", "tool": name, "callID": new_id(rng, "call_"),
                                      "state": {"status": "completed", "input": {"cmd": "x"},
                                                "output": out, "title": name,
                                                "time": {"start": a_created, "end": a_completed}},
                                  })))
                parts.append((new_id(rng, "prt_"), msg_a, sid, a_created, a_completed,
                              json.dumps({"type": "text", "text": reply})))
            t = a_completed

        model_json = json.dumps({"providerID": provider, "id": model_id})
        if v2:
            self.conn.execute(
                "INSERT INTO session_v2 (id, project_id, slug, directory, path, title,"
                " version, parent_id, cost, tokens_input, tokens_output, tokens_reasoning,"
                " tokens_cache_read, tokens_cache_write, time_created, time_updated)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, project_id, "slug", directory, directory.lstrip("/"), "Session",
                 "1.20.0", None, cost, totals["input"], totals["output"], totals["reasoning"],
                 totals["read"], totals["write"], start, t),
            )
            self.conn.executemany(
                "INSERT INTO session_message VALUES (?,?,?,?,?,?,?)", messages
            )
        else:
            self.conn.execute(
                "INSERT INTO session (id, project_id, slug, directory, path, title,"
                " version, parent_id, cost, tokens_input, tokens_output, tokens_reasoning,"
                " tokens_cache_read, tokens_cache_write, model, time_created, time_updated)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, project_id, "slug", directory, directory.lstrip("/"), "Session",
                 "1.18.19", None, cost, totals["input"], totals["output"], totals["reasoning"],
                 totals["read"], totals["write"], model_json, start, t),
            )
            self.conn.executemany("INSERT INTO message VALUES (?,?,?,?,?)", messages)
            self.conn.executemany("INSERT INTO part VALUES (?,?,?,?,?,?)", parts)
        self.bytes_written += sum(len(m[-1]) for m in messages) + sum(len(p[-1]) for p in parts)
        # Conversations start a little after the previous one ended.
        self.clock_ms = min(t + rng.randint(60_000, 3_600_000), self.now_ms)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, type=Path, help="opencode.db to create")
    ap.add_argument("--size-gb", type=float, default=2.0, help="stop once this many GB of payload are written")
    ap.add_argument("--v1-share", type=float, default=0.5, help="fraction of payload written to the v1 tables")
    ap.add_argument("--seed", type=int, default=476)
    ap.add_argument("--force", action="store_true", help="overwrite an existing file")
    args = ap.parse_args()

    out: Path = args.out
    if out.exists():
        if not args.force:
            print(f"{out} exists; pass --force to overwrite", file=sys.stderr)
            return 1
        for suffix in ("", "-wal", "-shm"):
            Path(str(out) + suffix).unlink(missing_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)

    rng = random.Random(args.seed)
    conn = sqlite3.connect(out)
    conn.executescript(SCHEMA)
    gen = Generator(conn, rng)
    target = int(args.size_gb * 1e9)
    v1_target = int(target * args.v1_share)
    projects = [(f"/work/project{i}", f"prj_{i:03d}") for i in range(12)]
    started = time.time()
    conversations = 0
    last_report = 0
    while gen.bytes_written < target:
        v2 = gen.bytes_written >= v1_target
        directory, project_id = rng.choice(projects)
        gen.conversation(v2=v2, directory=directory, project_id=project_id)
        conversations += 1
        if conversations % 50 == 0:
            conn.commit()
        if gen.bytes_written - last_report > 100_000_000:
            last_report = gen.bytes_written
            print(f"  {gen.bytes_written / 1e9:.1f} GB, {conversations} conversations,"
                  f" {time.time() - started:.0f}s", flush=True)
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    counts = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("session", "message", "part", "session_v2", "session_message")
    }
    conn.close()
    print(f"wrote {out} ({out.stat().st_size / 1e9:.2f} GB on disk) in {time.time() - started:.0f}s")
    print("  " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
