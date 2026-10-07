"""Time the non-Claude stats readers at the TUI's cadence over a synthetic fleet.

``bench_scaling.py`` builds a Claude-only fleet, so until this script no budget
covered the opencode, opencode2, codex, grok or hermes readers (audit R15,
#517). This builds one synthetic store per backend with the shapes their unit
tests pin, puts a fleet of agents on it, and drives each reader the way the TUI
does: ``get_stats`` per agent every 5 s (the stats sweep) and
``get_window_token_usage`` per agent every second (the burn rate, with a
spin baseline selected). The figure is milliseconds of reader work per
simulated second, warm: 100 ms/s is a tenth of a core.

    python scripts/bench_backends.py            # quick fleet, all backends
    python scripts/bench_backends.py codex grok # just those

``tests/scale/test_backend_budgets.py`` holds the budgets.
"""

from __future__ import annotations

import json
import random
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR.parent / "src"))
sys.path.insert(0, str(SCRIPTS_DIR))

BACKENDS = ("opencode", "opencode2", "codex", "grok", "hermes")

# Burn window = the spin baseline; 60 min is the TUI's default when one is set.
BASELINE = timedelta(minutes=60)


@dataclass(frozen=True)
class BackendFleetSpec:
    agents: int = 30
    # Live conversations per agent: one per /new (opencode, hermes)
    conversations: int = 3
    # Bytes of log per agent (codex rollout, grok updates.jsonl)
    log_bytes: int = 4 << 20
    # Messages per conversation (hermes)
    messages: int = 3000
    # Agents on the row-cache cliff store: cliff_agents x conversations x 500
    # polled rows must pass the 50k-entry cap
    cliff_agents: int = 50

    @classmethod
    def quick(cls) -> "BackendFleetSpec":
        return cls()


@dataclass
class Fleet:
    backend: str
    reader: Any
    sessions: List[Any]
    log_mb: float = 0.0


@dataclass
class CadenceResult:
    backend: str
    agents: int
    ms_per_second: float
    stats_ms_per_sweep: float
    window_ms_per_second: float

    def line(self) -> str:
        return (
            f"{self.backend:<18} {self.agents:>3} agents  "
            f"{self.ms_per_second:7.1f} ms/s ({self.ms_per_second / 10:4.1f}% of a core)  "
            f"stats sweep {self.stats_ms_per_sweep:7.1f} ms  "
            f"window {self.window_ms_per_second:7.1f} ms/s"
        )


def _session(**fields) -> SimpleNamespace:
    base = dict(
        id=None, name=None, tmux_session=None, wrapper=None,
        agent_session_ids=[], active_agent_session_id=None,
        start_directory=None, start_time=None,
    )
    base.update(fields)
    return SimpleNamespace(**base)


def _pad(rng: random.Random, size: int) -> str:
    return "".join(rng.choices("abcdefghij klmnopqrstuvwxyz", k=size))


# ── opencode / opencode2 ──────────────────────────────────────────────────


def build_opencode(root: Path, name: str, agents: int, spec: BackendFleetSpec,
                   v2: bool = False) -> Fleet:
    """An opencode store whose conversations each fill the reader's 500-row scan.

    The live store's busiest conversations run to thousands of rows (#517's
    report; 2,300 on the 2.3 GB generated store), and every per-second rescan
    walks 500 of them. Short conversations would hide that cost.
    ``agents x conversations x 500`` is the fleet's polled rows: under the row
    cache's 50k cap for the 30-agent fleets, past it for "opencode-cliff".
    """
    import make_opencode_store as gen
    from overcode.backends import opencode2_stats, opencode_stats

    path = root / name / "opencode.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(gen.SCHEMA)
    model = json.dumps({"id": "gpt-5", "providerID": "openai"})
    base = int(time.time() * 1000) - 86_400_000
    sessions, k = [], spec.conversations
    for a in range(agents):
        ids = [f"ses_{name[:6]}{a:05d}{c:02d}{'0' * 13}" for c in range(k)]
        for c, sid in enumerate(ids):
            first = base + (a * k + c) * 1_000_000
            conn.execute(
                f"INSERT INTO {'session_v2' if v2 else 'session'} (id, project_id, slug,"
                " directory, version, parent_id, cost, tokens_input, tokens_output,"
                " tokens_reasoning, tokens_cache_read, tokens_cache_write, model,"
                " time_created, time_updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, "prj", "slug", "/work/opencode", "1", None, 1.0, 100, 200, 0, 0, 0,
                 model, first, first + 600_000))
            rows = []
            for m in range(600):
                t = first + m * 1000
                role = "assistant" if m % 2 else "user"
                data = {"role": role, "time": {"created": t}}
                if role == "assistant":
                    data["time"]["completed"] = t + 500
                    data["tokens"] = {"input": 10, "output": 20, "reasoning": 0, "total": 5000,
                                      "cache": {"read": 1, "write": 2}}
                    data["model"] = {"id": "gpt-5", "providerID": "openai"}
                msg = f"msg_{sid}_{m:04d}"
                if v2:
                    rows.append((msg, sid, role, m + 1, t, t, json.dumps(data)))
                else:
                    rows.append((msg, sid, t, t, json.dumps(data)))
            if v2:
                conn.executemany("INSERT INTO session_message (id, session_id, type, seq,"
                                 " time_created, time_updated, data) VALUES (?,?,?,?,?,?,?)", rows)
            else:
                conn.executemany("INSERT INTO message (id, session_id, time_created,"
                                 " time_updated, data) VALUES (?,?,?,?,?)", rows)
        sessions.append(_session(id=f"{name}-{a}", name=f"{name}{a}", agent_session_ids=ids,
                                 active_agent_session_id=ids[-1], start_directory="/nonexistent",
                                 start_time=datetime(2026, 1, 1).isoformat()))
    conn.commit()
    conn.close()
    reader = (opencode2_stats.Opencode2StatsReader(db_path=path) if v2
              else opencode_stats.OpencodeStatsReader(db_path=path))
    return Fleet(name, reader, sessions, path.stat().st_size / 1e6)


# ── codex ─────────────────────────────────────────────────────────────────


def build_codex(root: Path, spec: BackendFleetSpec, launch: datetime) -> Fleet:
    """One rollout per agent: session_meta, then turns until log_bytes."""
    from overcode.backends.codex_stats import CodexStatsReader

    rng = random.Random(13)
    sessions_dir = root / "codex" / "sessions"
    day = sessions_dir / f"{launch.year:04d}" / f"{launch.month:02d}" / f"{launch.day:02d}"
    day.mkdir(parents=True, exist_ok=True)
    pad = _pad(rng, 1500)
    sessions, total = [], 0
    for a in range(spec.agents):
        sid = f"01a0439d-63b8-71d0-bf11-{a:012d}"
        cwd = f"/work/codex{a}"
        path = day / f"rollout-{launch:%Y-%m-%dT%H-%M-%S}-{sid}.jsonl"
        tokens = 0
        with path.open("w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "session_meta",
                                "payload": {"id": sid, "cwd": cwd, "cli_version": "0.150.1"}}) + "\n")
            while f.tell() < spec.log_bytes:
                tokens += 7000
                for entry in (
                    {"type": "turn_context", "payload": {
                        "model": "gpt-5.6-sol", "effort": "high",
                        "collaboration_mode": {"settings": {"model": "gpt-5.6-sol"}}}},
                    {"type": "response_item", "payload": {
                        "type": "message", "role": "user",
                        "content": [{"type": "input_text", "text": "next step"}],
                        "internal_chat_message_metadata_passthrough": {
                            "content_item_kinds": ["user.text"]}}},
                    {"type": "response_item", "payload": {
                        "type": "function_call_output", "output": pad}},
                    {"type": "event_msg", "payload": {"type": "token_count", "info": {
                        "total_token_usage": {
                            "input_tokens": tokens, "cached_input_tokens": tokens // 2,
                            "cache_write_input_tokens": 64, "output_tokens": tokens // 70,
                            "reasoning_output_tokens": 20, "total_tokens": tokens},
                        "last_token_usage": {"total_tokens": 7120},
                        "model_context_window": 272000}}},
                ):
                    f.write(json.dumps(entry) + "\n")
            total += f.tell()
        sessions.append(_session(id=f"cx-{a}", name=f"cx{a}", agent_session_ids=[sid],
                                 active_agent_session_id=sid, start_directory=cwd,
                                 start_time=launch.isoformat()))
    return Fleet("codex", CodexStatsReader(sessions_dir=sessions_dir), sessions, total / 1e6)


# ── grok ──────────────────────────────────────────────────────────────────


def build_grok(root: Path, spec: BackendFleetSpec, launch: datetime) -> Fleet:
    """updates.jsonl per agent: streamed chunks with a turn_completed per turn."""
    from overcode.backends.grok_stats import GrokStatsReader, project_dir, session_dir

    rng = random.Random(14)
    sessions_dir = root / "grok" / "sessions"
    text = _pad(rng, 600)
    sessions, total = [], 0
    t0 = launch.timestamp()
    for a in range(spec.agents):
        sid = f"01a015cb-a815-7d92-87a8-{a:012d}"
        cwd = f"/work/grok{a}"
        sdir = session_dir(cwd, sid, root=sessions_dir)
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / "summary.json").write_text(json.dumps({
            "info": {"id": sid, "cwd": cwd}, "current_model_id": "grok-4.6",
            "reasoning_effort": "high", "num_messages": 10}))
        prompts = []
        with (sdir / "updates.jsonl").open("w", encoding="utf-8") as f:
            turn, ts = 0, t0
            while f.tell() < spec.log_bytes:
                turn += 1
                for _ in range(4):
                    ts += 1
                    f.write(json.dumps({"timestamp": ts, "method": "session/update", "params": {
                        "sessionId": sid, "_meta": {"totalTokens": 50_000 + turn, "eventId": "e"},
                        "update": {"sessionUpdate": "agent_message_chunk",
                                   "content": {"type": "text", "text": text}}}}) + "\n")
                f.write(json.dumps({"timestamp": ts, "method": "session/update", "params": {
                    "sessionId": sid, "update": {
                        "sessionUpdate": "turn_completed", "prompt_id": f"p{turn}",
                        "stop_reason": "end_turn", "usage": {
                            "inputTokens": 9000, "outputTokens": 300, "totalTokens": 9300,
                            "cachedReadTokens": 4000, "cacheCreationTokens": 0,
                            "reasoningTokens": 100, "modelCalls": 1, "apiDurationMs": 900,
                            "costUsdTicks": 1000, "numTurns": 1}}}}) + "\n")
                prompts.append({"timestamp": "2026-10-01T00:00:00Z", "session_id": sid,
                                "prompt": f"step {turn}", "is_bash": False})
            total += f.tell()
        with (project_dir(cwd, root=sessions_dir) / "prompt_history.jsonl").open("w") as f:
            for p in prompts:
                f.write(json.dumps(p) + "\n")
        sessions.append(_session(id=f"gk-{a}", name=f"gk{a}", agent_session_ids=[sid],
                                 active_agent_session_id=sid, start_directory=cwd,
                                 start_time=launch.isoformat()))
    return Fleet("grok", GrokStatsReader(sessions_dir=sessions_dir), sessions, total / 1e6)


# ── hermes ────────────────────────────────────────────────────────────────

HERMES_SCHEMA = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, user_id TEXT, model TEXT,
    model_config TEXT, parent_session_id TEXT, started_at REAL NOT NULL,
    ended_at REAL, end_reason TEXT, message_count INTEGER DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0, input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0, cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0, reasoning_tokens INTEGER DEFAULT 0,
    cwd TEXT, estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT,
    cost_source TEXT, title TEXT, api_call_count INTEGER DEFAULT 0
);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL,
    content TEXT, tool_name TEXT, timestamp REAL NOT NULL, token_count INTEGER,
    active INTEGER DEFAULT 1
);
CREATE INDEX idx_messages_session ON messages(session_id, timestamp);
CREATE INDEX idx_sessions_parent ON sessions(parent_session_id);
"""


def _hermes_config(rng: random.Random) -> str:
    """A ~4 KB config.yaml shaped like a real one (model, providers, toolsets)."""
    lines = ["model:", "  default: gpt-5-mini", "  provider: openrouter",
             "  context_length: 400000", "agent:", "  max_turns: 90",
             "  reasoning_effort: medium", "toolsets:"]
    lines += [f"  - toolset_{i}" for i in range(20)]
    lines += ["providers:"]
    for i in range(12):
        lines += [f"  provider_{i}:", f"    base_url: https://api{i}.example.com/v1",
                  f"    api_key_env: PROVIDER_{i}_KEY", "    models:"]
        lines += [f"      - model-{i}-{j}" for j in range(4)]
    lines += ["display:", "  compact: false", "  tool_progress: all",
              "memory:", "  memory_enabled: true", "  user_profile_enabled: true"]
    return "\n".join(lines) + "\n"


def build_hermes(root: Path, spec: BackendFleetSpec, launch: datetime) -> Fleet:
    """state.db plus a config.yaml in a fixture HERMES_HOME.

    The reader parses ``$HERMES_HOME/config.yaml`` for the context window,
    so ``patched_hermes_home`` must be active while timing, or the host's
    own config is read instead.
    """
    from overcode.backends.hermes_stats import HermesStatsReader

    rng = random.Random(15)
    path = root / "hermes" / "state.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.parent / "config.yaml").write_text(_hermes_config(rng))
    conn = sqlite3.connect(path)
    conn.executescript(HERMES_SCHEMA)
    config = json.dumps({"reasoning_config": {"enabled": True, "effort": "medium"},
                         "_usage_anchor": {"prompt_tokens": 12256, "completion_tokens": 42}})
    content = _pad(rng, 400)
    sessions = []
    t0 = launch.timestamp()
    for a in range(spec.agents):
        cwd = f"/work/hermes{a}"
        ids = [f"20260917_{a:04d}{c:02d}_abcdef" for c in range(spec.conversations)]
        for c, sid in enumerate(ids):
            started = t0 + c * 600
            conn.execute(
                "INSERT INTO sessions (id, source, model, model_config, started_at, "
                "input_tokens, output_tokens, cache_read_tokens, reasoning_tokens, cwd, "
                "estimated_cost_usd, cost_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, "cli", "gpt-5-mini", config, started, 12786, 559, 36224, 384, cwd,
                 0.0, "unknown"))
            roles = ("user", "assistant", "tool", "assistant")
            conn.executemany(
                "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
                [(sid, roles[m % 4], content, started + m) for m in range(spec.messages)])
        sessions.append(_session(id=f"hm-{a}", name=f"hm{a}", agent_session_ids=ids,
                                 active_agent_session_id=ids[-1], start_directory=cwd,
                                 start_time=launch.isoformat()))
    conn.commit()
    conn.close()
    return Fleet("hermes", HermesStatsReader(db_path=path), sessions, path.stat().st_size / 1e6)


def hermes_home_for(fleets: Dict[str, Fleet]) -> Optional[Path]:
    """The fixture HERMES_HOME, when a hermes fleet was built."""
    fleet = fleets.get("hermes")
    return Path(fleet.reader._db_path).parent if fleet else None


# ── building and timing ───────────────────────────────────────────────────


def build_fleets(root: Path, spec: BackendFleetSpec, backends=BACKENDS,
                 quiet: bool = False) -> Dict[str, Fleet]:
    """Every requested fleet, keyed by name ("opencode-cliff" is the row-cache case)."""
    # Launched inside the burn window: the young-agent case, where codex's
    # reader still scans for the window (older agents return unknown).
    launch = datetime.now() - timedelta(minutes=10)
    fleets: Dict[str, Fleet] = {}

    def note(msg: str) -> None:
        if not quiet:
            print(msg, flush=True)

    for name, agents, v2, wanted in (
        ("opencode", spec.agents, False, "opencode"),
        ("opencode-cliff", spec.cliff_agents, False, "opencode"),
        ("opencode2", spec.agents, True, "opencode2"),
    ):
        if wanted in backends:
            started = time.time()
            fleets[name] = build_opencode(root, name, agents, spec, v2=v2)
            note(f"{name} store {fleets[name].log_mb:.0f} MB in {time.time() - started:.0f}s")
    builders: Dict[str, Callable[..., Fleet]] = {
        "codex": build_codex, "grok": build_grok, "hermes": build_hermes,
    }
    for name, build in builders.items():
        if name in backends:
            started = time.time()
            fleets[name] = build(root, spec, launch)
            note(f"{name} fleet {fleets[name].log_mb:.0f} MB in {time.time() - started:.0f}s")
    return fleets


def clear_reader_caches() -> None:
    """Cold module caches (row cache, window indexes, JSONL folds) so each fleet starts equal."""
    from overcode.backends import jsonl_tail, opencode_stats

    opencode_stats.clear_row_cache()
    opencode_stats.clear_window_indexes()
    jsonl_tail.clear()


def time_cadence(fleet: Fleet, seconds: int = 10, now: Optional[datetime] = None) -> CadenceResult:
    """Reader work per simulated second at the TUI's cadence, warm.

    One full sweep first fills whatever caches the reader keeps. Then, per
    simulated second, every agent's window usage is read, and every fifth
    second every agent's stats. Wall time is not simulated: the readers'
    own clocks run, so a cache with a max age behaves as it would live.
    """
    since = (now or datetime.now()) - BASELINE
    for s in fleet.sessions:
        fleet.reader.get_stats(s)
        fleet.reader.get_window_token_usage(s, since)
    stats_s = window_s = 0.0
    for second in range(seconds):
        if second % 5 == 0:
            start = time.perf_counter()
            for s in fleet.sessions:
                fleet.reader.get_stats(s)
            stats_s += time.perf_counter() - start
        start = time.perf_counter()
        for s in fleet.sessions:
            fleet.reader.get_window_token_usage(s, since)
        window_s += time.perf_counter() - start
    sweeps = (seconds + 4) // 5
    return CadenceResult(
        backend=fleet.backend,
        agents=len(fleet.sessions),
        ms_per_second=(stats_s + window_s) / seconds * 1000,
        stats_ms_per_sweep=stats_s / sweeps * 1000,
        window_ms_per_second=window_s / seconds * 1000,
    )


def main(argv: List[str]) -> int:
    import tempfile

    backends = tuple(a for a in argv if a in BACKENDS) or BACKENDS
    spec = BackendFleetSpec.quick()
    import os

    with tempfile.TemporaryDirectory(prefix="overcode-backends-") as tmp:
        fleets = build_fleets(Path(tmp), spec, backends)
        home = hermes_home_for(fleets)
        if home is not None:
            os.environ["HERMES_HOME"] = str(home)
        print()
        for name, fleet in fleets.items():
            clear_reader_caches()
            print(time_cadence(fleet).line())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
