#!/usr/bin/env python3
"""Scale benchmark harness for overcode's per-tick hot paths.

Builds a synthetic overcode + Claude Code state tree with the REAL on-disk
shapes (``sessions.json`` entries via ``Session.to_dict()``, daemon state via
``MonitorDaemonState.save``, the status/presence CSVs, Claude transcripts with
usage blocks, ``history.jsonl``, hook state/event files), points the production
code at it without touching the real home directory, and times the functions
the TUI and monitor daemon run every tick. While the daemon phases run it
counts how often ``sessions.json`` is opened for reading, how many fsync'd
writes land on it, how many tmux interface calls are issued and how many
subprocesses would be spawned.

    uv run python scripts/bench_scaling.py --quick          # ~1 min
    uv run python scripts/bench_scaling.py --power          # reference scale
    uv run python scripts/bench_scaling.py --quick --json   # machine-readable
    uv run python scripts/bench_scaling.py --dir /tmp/fx    # build once, reuse

Every timed site is a plain function (``time_*``) that takes the fixture and
returns ``SiteResult`` rows, so ``tests/scale`` can assert per-tick budgets
against the same fixture the table is printed from.

Two things are deliberately synthetic in the daemon phases and counted as
the spawn they replace: the ``ps`` process table (one spawn each in
``_sync_process_resources`` / ``_sync_sandbox_state``) and ``lsof`` (one spawn
on macOS). Everything else — status detection, git context, the sessions.json
read-modify-writes, transcript and CSV parsing — is the production code on the
fixture files. The fake tmux answers the ``TmuxInterface`` from the fixture's
pane text; every call it receives stands for at least one real tmux command
(``RealTmux.get_pane_pid`` on a fresh instance is three).

Sessions ever launched but no longer live are spread over other tmux
sessions (the five-instances-per-host design target) so the daemon tick sees
exactly ``--agents`` sessions; ``--stale-in-session`` parks that many
terminated entries in the live tmux session instead, which the daemon
iterates today.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import inspect
import io
import json
import os
import platform
import random
import subprocess
import sys
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Sequence

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# =============================================================================
# Fixture specification
# =============================================================================


@dataclass
class FixtureSpec:
    """Sizes of the synthetic state. Defaults are the ``--quick`` preset."""

    agents: int = 50  # live agents in the benchmarked tmux session
    sessions: int = 2000  # sessions.json entries ever launched (live + terminated)
    transcript_mb: float = 50.0  # total live transcript bytes (primary + subagents)
    history_hours: float = 3.0  # agent_status_history.csv span (one row/agent/2 s)
    presence_days: float = 30.0  # presence_log.csv span (one row/60 s)
    history_lines: int = 20_000  # ~/.claude/history.jsonl entries
    ids_per_agent: int = 3  # agent_session_ids per live agent (/clear history)
    subagents_per_agent: int = 4  # subagents/agent-*.jsonl under the active id
    stale_in_session: int = 0  # terminated entries left in the live tmux session
    tmux_session: str = "agents"
    seed: int = 1

    @classmethod
    def quick(cls) -> "FixtureSpec":
        return cls()

    @classmethod
    def power(cls) -> "FixtureSpec":
        return cls(
            sessions=20_000,
            transcript_mb=1024.0,
            history_hours=24.0,
            presence_days=30.0,
            history_lines=100_000,
        )


@dataclass
class FixturePaths:
    """Where everything lives under one root (a fake ``$HOME``)."""

    root: Path
    tmux_session: str = "agents"

    @property
    def home(self) -> Path:
        return self.root / "home"

    @property
    def overcode_dir(self) -> Path:
        return self.home / ".overcode"

    @property
    def state_dir(self) -> Path:
        return self.overcode_dir / "sessions"

    @property
    def session_dir(self) -> Path:
        return self.state_dir / self.tmux_session

    @property
    def claude_dir(self) -> Path:
        return self.home / ".claude"

    @property
    def projects_dir(self) -> Path:
        return self.claude_dir / "projects"

    @property
    def history_path(self) -> Path:
        return self.claude_dir / "history.jsonl"

    @property
    def sessions_file(self) -> Path:
        return self.state_dir / "sessions.json"

    @property
    def daemon_state_path(self) -> Path:
        return self.session_dir / "monitor_daemon_state.json"

    @property
    def agent_history_path(self) -> Path:
        return self.session_dir / "agent_status_history.csv"

    @property
    def presence_log_path(self) -> Path:
        return self.overcode_dir / "presence_log.csv"

    @property
    def work_dir(self) -> Path:
        return self.root / "work"

    @property
    def manifest(self) -> Path:
        return self.root / "manifest.json"


# =============================================================================
# Synthetic content pools (pre-generated once so 1 GB builds stay fast)
# =============================================================================

_WORDS = (
    "the daemon reads sessions json every tick and rewrites it under an exclusive "
    "lock while the tui parses transcripts for burn rate tokens cost context window "
    "status timeline presence spin mean hooks capture pane tmux command budget "
    "incremental offset mtime cache batch write once per tick event driven update"
).split()

_HOOK_EVENTS = (
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
    "StopFailure",
    "PermissionRequest",
    "SessionEnd",
)

# The launcher passes hook registrations inline as --settings JSON; it is the
# bulk of a real sessions.json entry's ``command`` field.
_SETTINGS_JSON = json.dumps(
    {
        "hooks": {
            ev: [{"hooks": [{"type": "command", "command": "overcode hook-handler"}]}]
            for ev in _HOOK_EVENTS
        },
        "permissions": {"allow": ["Bash(git:*)", "Read", "Edit", "Write", "Glob", "Grep"]},
    }
)

_STANDING_INSTRUCTIONS = (
    "Keep herding this agent on to completion. Run the unit tests before every "
    "commit, keep commits small and focused, never bump the version, and write a "
    "short summary of what changed and why at the end of each work unit. If a test "
    "fails twice in a row stop and report instead of retrying blindly."
)

_SKILLS = [f"skill-{i:02d}" for i in range(12)] + ["overcode", "delegating-to-agents"]

_TOOLS = ("Read", "Edit", "Bash", "Grep", "Glob", "Write")

# A realistic Claude Code idle pane: welcome box, a couple of turns, prompt.
_PANE_BLOCK = """\
> Fix the authentication bug in login.py

⏺ Read(src/auth/login.py)
  ⎿  Read 145 lines from src/auth/login.py

⏺ I found the issue. The token validation was checking expiry incorrectly.
  Let me fix it.

⏺ Edit(src/auth/login.py)
  ⎿  Updated src/auth/login.py (3 edits)

⏺ Bash(uv run pytest tests/unit/test_auth.py -q)
  ⎿  ........                                                        [100%]
     8 passed in 0.42s

⏺ Fixed the authentication bug. The issue was that the token expiry check
  was using `<` instead of `<=`, causing tokens to be rejected one second
  too early.
"""

_PANE_TAIL = """\
────────────────────────────────────────────────────────────────────────────────
>
────────────────────────────────────────────────────────────────────────────────
  ? for shortcuts
"""


def _pane_text(lines: int = 600) -> str:
    block_lines = _PANE_BLOCK.splitlines()
    body: List[str] = []
    while len(body) < lines:
        body.extend(block_lines)
    return "\n".join(body[-lines:]) + "\n" + _PANE_TAIL


def _text(rng: random.Random, n_words: int) -> str:
    return " ".join(rng.choice(_WORDS) for _ in range(n_words))


def _iso_z(dt: datetime) -> str:
    """Claude Code transcript timestamp: UTC, millisecond precision, ``Z``."""
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


class _Pools:
    """Pre-generated text blobs so per-line generation is a dict fill + dumps."""

    def __init__(self, rng: random.Random):
        self.prompts = [_text(rng, rng.randint(20, 80)) for _ in range(64)]
        self.thinking = [_text(rng, rng.randint(80, 200)) for _ in range(64)]
        self.answers = [_text(rng, rng.randint(60, 160)) for _ in range(64)]
        self.stdout = [_text(rng, rng.randint(250, 700)) for _ in range(64)]
        self.commands = [
            f"uv run pytest tests/unit/test_{w}.py -q -x --timeout=120" for w in _WORDS[:32]
        ] + [f"git log --oneline -20 -- src/overcode/{w}.py" for w in _WORDS[:32]]


# =============================================================================
# Transcript generation (Claude Code ~/.claude/projects/<enc>/<sid>.jsonl)
# =============================================================================


def _transcript_lines(
    rng: random.Random,
    pools: _Pools,
    sid: str,
    cwd: str,
    start: datetime,
    end: datetime,
    target_bytes: int,
    model: str = "claude-opus-4-6",
    agent_id: Optional[str] = None,
) -> List[str]:
    """Generate JSONL lines with the fields ``history_reader`` reads.

    Each turn is: a user prompt, an assistant thinking block, an assistant
    tool_use, a user tool_result (with a multi-KB ``toolUseResult``), and an
    assistant text answer — three assistant messages with usage blocks and
    two ``user`` lines of which one is a tool_result (skipped by the
    work-time parser). Every ~20 turns a non-message record
    (``file-history-snapshot``, ``last-prompt``, ``system``) is interleaved,
    as Claude Code does.
    """
    # ~5.5 KB per turn; estimate the count then fill until the byte target.
    est_turns = max(1, int(target_bytes / 5500))
    span = (end - start).total_seconds()
    step = span / max(est_turns, 1)
    common = {
        "isSidechain": agent_id is not None,
        "userType": "external",
        "cwd": cwd,
        "sessionId": sid,
        "version": "2.0.75",
        "gitBranch": "main",
        "entrypoint": "cli",
        "sessionKind": "ma" if agent_id else "in",
    }
    if agent_id:
        common["agentId"] = agent_id
    lines: List[str] = []
    size = 0
    parent: Optional[str] = None
    turn = 0
    cache_read = rng.randint(20_000, 60_000)
    t = start
    while size < target_bytes:
        t = start + timedelta(seconds=step * turn)
        if t > end:
            t = end
        turn += 1
        prompt_uuid = str(uuid.uuid4())
        user = {
            "parentUuid": parent,
            **common,
            "type": "user",
            "message": {"role": "user", "content": rng.choice(pools.prompts)},
            "uuid": prompt_uuid,
            "timestamp": _iso_z(t),
            "promptId": str(uuid.uuid4()),
        }
        parent = prompt_uuid
        cache_read = min(cache_read + rng.randint(500, 4000), 180_000)
        cache_creation = rng.randint(0, 3000)
        msg_ts = t + timedelta(seconds=rng.uniform(2, 20))
        assistant_msgs = []
        for kind in ("thinking", "tool_use", "text"):
            msg_uuid = str(uuid.uuid4())
            if kind == "thinking":
                content = [
                    {
                        "type": "thinking",
                        "thinking": rng.choice(pools.thinking),
                        "signature": "Eo0BCk" + "x" * 80,
                    }
                ]
                out = rng.randint(200, 900)
            elif kind == "tool_use":
                tool = rng.choice(_TOOLS)
                content = [
                    {
                        "type": "tool_use",
                        "id": "toolu_01" + uuid.uuid4().hex[:22],
                        "name": tool,
                        "input": (
                            {"command": rng.choice(pools.commands)}
                            if tool == "Bash"
                            else {"file_path": f"{cwd}/src/overcode/{rng.choice(_WORDS)}.py"}
                        ),
                    }
                ]
                out = rng.randint(40, 200)
            else:
                content = [{"type": "text", "text": rng.choice(pools.answers)}]
                out = rng.randint(80, 600)
            usage = {
                "input_tokens": rng.randint(2, 40),
                "cache_creation_input_tokens": cache_creation if kind == "thinking" else 0,
                "cache_read_input_tokens": cache_read,
                "output_tokens": out,
                "server_tool_use": {"web_search_requests": 0},
                "service_tier": "standard",
                "cache_creation": {
                    "ephemeral_5m_input_tokens": cache_creation,
                    "ephemeral_1h_input_tokens": 0,
                },
            }
            msg = {
                "parentUuid": parent,
                **common,
                "message": {
                    "model": model,
                    "id": "msg_01" + uuid.uuid4().hex[:22],
                    "type": "message",
                    "role": "assistant",
                    "content": content,
                    "stop_reason": "tool_use" if kind == "tool_use" else "end_turn",
                    "stop_sequence": None,
                    "usage": usage,
                },
                "requestId": "req_01" + uuid.uuid4().hex[:22],
                "type": "assistant",
                "uuid": msg_uuid,
                "timestamp": _iso_z(msg_ts),
            }
            parent = msg_uuid
            assistant_msgs.append(msg)
            if kind == "tool_use":
                tool_use_id = content[0]["id"]
                result_uuid = str(uuid.uuid4())
                stdout = rng.choice(pools.stdout)
                result = {
                    "parentUuid": parent,
                    **common,
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "tool_use_id": tool_use_id,
                                "type": "tool_result",
                                "content": stdout,
                            }
                        ],
                    },
                    "uuid": result_uuid,
                    "timestamp": _iso_z(msg_ts + timedelta(seconds=1)),
                    "toolUseResult": {
                        "stdout": stdout,
                        "stderr": "",
                        "interrupted": False,
                        "isImage": False,
                    },
                    "sourceToolAssistantUUID": msg_uuid,
                }
                parent = result_uuid
                assistant_msgs.append(result)
            msg_ts += timedelta(seconds=rng.uniform(1, 8))
        records = [user, *assistant_msgs]
        if turn % 20 == 0:
            records.append(
                {
                    "type": "file-history-snapshot",
                    "messageId": prompt_uuid,
                    "snapshot": {
                        "messageId": prompt_uuid,
                        "trackedFileBackups": {},
                        "timestamp": _iso_z(t),
                    },
                    "isSnapshotUpdate": False,
                }
            )
            records.append(
                {
                    "type": "last-prompt",
                    "lastPrompt": rng.choice(pools.prompts)[:200],
                    "leafUuid": parent,
                    "sessionId": sid,
                }
            )
        for rec in records:
            line = json.dumps(rec, separators=(",", ":"))
            lines.append(line)
            size += len(line) + 1
    return lines


def _write_lines(path: Path, lines: Sequence[str], mtime: Optional[datetime] = None) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for line in lines:
            f.write(line)
            f.write("\n")
    if mtime is not None:
        ts = mtime.timestamp()
        os.utime(path, (ts, ts))
    return path.stat().st_size


# =============================================================================
# Fixture builder
# =============================================================================


def _make_repo(path: Path) -> None:
    """A directory ``read_git_context_from_disk`` resolves without subprocesses."""
    (path / ".git").mkdir(parents=True, exist_ok=True)
    (path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (path / "src").mkdir(exist_ok=True)


def _make_session(
    rng: random.Random,
    *,
    name: str,
    tmux_session: str,
    start_directory: Path,
    start_time: datetime,
    status: str,
    ids: List[str],
    live: bool,
    index: int,
    parent_session_id: Optional[str] = None,
    session_id: Optional[str] = None,
):
    """A ``Session`` with the field sizes a real long-lived entry has (~5 KB)."""
    from overcode.session_manager import Session, SessionStats

    sid = session_id or str(uuid.uuid4())
    running = status == "running" and index % 2 == 0
    op_times = [round(rng.uniform(5, 900), 3) for _ in range(100 if live else rng.randint(5, 100))]
    input_tokens = rng.randint(20_000, 400_000)
    output_tokens = rng.randint(5_000, 120_000)
    cache_read = rng.randint(1_000_000, 40_000_000)
    cache_creation = rng.randint(50_000, 900_000)
    now = datetime.now()
    stats = SessionStats(
        interaction_count=rng.randint(10, 400),
        estimated_cost_usd=round(rng.uniform(0.5, 80.0), 4),
        total_tokens=input_tokens + output_tokens + cache_read + cache_creation,
        operation_times=op_times,
        steers_count=rng.randint(0, 20),
        last_activity=(now - timedelta(seconds=rng.randint(0, 3600))).isoformat(),
        current_task=f"Active: {rng.choice(_TOOLS)}(src/overcode/{rng.choice(_WORDS)}.py)",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_tokens=cache_creation,
        cache_read_tokens=cache_read,
        current_context_tokens=rng.randint(20_000, 180_000),
        last_stats_update=(now - timedelta(seconds=rng.randint(0, 60))).isoformat(),
        current_state="running" if running else "waiting_user",
        state_since=(now - timedelta(seconds=rng.randint(2, 1800))).isoformat(),
        green_time_seconds=round(rng.uniform(100, 40_000), 1),
        non_green_time_seconds=round(rng.uniform(100, 40_000), 1),
        sleep_time_seconds=0.0,
        last_time_accumulation=(now - timedelta(seconds=2)).isoformat(),
    )
    return Session(
        id=sid,
        name=name,
        tmux_session=tmux_session,
        tmux_window=f"{name}-{sid[:4]}",
        command=["claude", "--session-id", ids[0], "--settings", _SETTINGS_JSON],
        start_directory=str(start_directory),
        start_time=start_time.isoformat(),
        repo_name=start_directory.name,
        branch="main",
        status=status,
        permissiveness_mode="permissive",
        standing_instructions=_STANDING_INSTRUCTIONS,
        standing_instructions_preset="herd",
        stats=stats,
        is_asleep=False,
        agent_value=1000,
        human_annotation="perf fixture agent; owns the status-bar burn column",
        agent_session_ids=list(ids),
        active_agent_session_id=ids[-1],
        loaded_skills=["overcode"],
        available_skills=list(_SKILLS),
        model="claude-opus-4-6",
        provider="web",
        backend="claude-code",
        tags=["perf"],
        parent_session_id=parent_session_id,
        launcher_version="0.5.4 (64425fa)",
        # Steady-state values: the synthetic ps table reports the same numbers
        # and lsof reports no listeners, so the 5 s / 15 s syncs write nothing
        # (as they do in production once the first sample has landed).
        sandbox_enabled=(False if sys.platform == "darwin" else None) if live else None,
        cpu_percent=round(3.0 + index * 0.1, 1),
        rss_bytes=(300 + index) * 1024 * 1024,
    )


def _hook_state_for(index: int) -> tuple[str, Optional[str], Optional[dict], Optional[str]]:
    """Alternate idle (Stop) and working (PostToolUse) agents."""
    if index % 2 == 0:
        return "PostToolUse", "Bash", {"command": "uv run pytest tests/unit -q"}, "toolu_01bench"
    return "Stop", None, None, None


def build_fixture(spec: FixtureSpec, root: Path, quiet: bool = False) -> FixturePaths:
    """Create the synthetic state tree under ``root`` and return its paths."""
    from overcode import hook_handler
    from overcode.backends import capability_names, session_capabilities
    from overcode.history_reader import encode_project_path
    from overcode.monitor_daemon_state import MonitorDaemonState, SessionDaemonState
    from overcode.session_manager import SessionManager
    from overcode.settings import DAEMON_VERSION
    from overcode.status_history import _HISTORY_HEADER, log_agent_status

    t0 = time.perf_counter()
    rng = random.Random(spec.seed)
    pools = _Pools(rng)
    paths = FixturePaths(root=root, tmux_session=spec.tmux_session)
    now = datetime.now()
    sizes: Dict[str, float] = {}

    def say(msg: str) -> None:
        if not quiet:
            print(f"  [build] {msg}", file=sys.stderr, flush=True)

    for d in (paths.session_dir, paths.projects_dir, paths.work_dir):
        d.mkdir(parents=True, exist_ok=True)
    for skill in ("overcode", "delegating-to-agents"):
        sk = paths.claude_dir / "skills" / skill
        sk.mkdir(parents=True, exist_ok=True)
        (sk / "SKILL.md").write_text(f"# {skill}\n")
    (paths.session_dir / "detection_mode").write_text("hooks")

    # ---- working directories -------------------------------------------
    n_repos = max(1, min(10, spec.agents))
    live_repos = [paths.work_dir / f"repo-{k:02d}" for k in range(n_repos)]
    n_old_dirs = min(100, max(1, spec.sessions // 20))
    old_repos = [paths.work_dir / f"old-{k:03d}" for k in range(n_old_dirs)]
    for repo in [*live_repos, *old_repos]:
        _make_repo(repo)

    # ---- sessions.json ----------------------------------------------------
    say(f"sessions.json: {spec.sessions} entries")
    session_start = now - timedelta(hours=spec.history_hours)
    live_sessions = []
    state: Dict[str, dict] = {}
    for i in range(spec.agents):
        ids = [str(uuid.uuid4()) for _ in range(spec.ids_per_agent)]
        parent = None
        # A shallow hierarchy: agents 10-19 are children of 0-4, 20-21 grandchildren.
        if 10 <= i < 20 and spec.agents > 10:
            parent = live_sessions[i - 10].id
        elif 20 <= i < 22 and spec.agents > 20:
            parent = live_sessions[i - 10].id
        s = _make_session(
            rng,
            name=f"agent-{i:02d}",
            tmux_session=spec.tmux_session,
            start_directory=live_repos[i % n_repos],
            start_time=session_start,
            status="running",
            ids=ids,
            live=True,
            index=i,
            parent_session_id=parent,
        )
        if i in (spec.agents - 1, spec.agents - 2) and spec.agents > 4:
            s.is_asleep = True
            s.stats.current_state = "asleep"
        live_sessions.append(s)
        state[s.id] = s.to_dict()
    other_sessions = [f"{spec.tmux_session}-{k}" for k in range(1, 5)]
    n_hist = max(0, spec.sessions - spec.agents)
    hist_sessions = []
    for j in range(n_hist):
        ids = [str(uuid.uuid4()) for _ in range(rng.randint(1, spec.ids_per_agent))]
        started = now - timedelta(days=spec.presence_days * (j + 1) / max(n_hist, 1))
        tmux = spec.tmux_session if j < spec.stale_in_session else other_sessions[j % 4]
        s = _make_session(
            rng,
            name=f"old-{j:05d}",
            tmux_session=tmux,
            start_directory=old_repos[j % n_old_dirs],
            start_time=started,
            status="terminated",
            ids=ids,
            live=False,
            index=j,
        )
        hist_sessions.append(s)
        state[s.id] = s.to_dict()
    SessionManager(state_dir=paths.state_dir)._save_state(state)
    sizes["sessions_json_entries"] = len(state)
    sizes["sessions_json_bytes"] = paths.sessions_file.stat().st_size

    # ---- monitor_daemon_state.json ---------------------------------------
    by_id = {s.id: s for s in live_sessions}
    children = Counter(s.parent_session_id for s in live_sessions if s.parent_session_id)

    def depth(s) -> int:
        d = 0
        while s.parent_session_id and s.parent_session_id in by_id:
            s = by_id[s.parent_session_id]
            d += 1
        return d

    session_states = []
    for i, s in enumerate(live_sessions):
        st = s.stats
        session_states.append(
            SessionDaemonState(
                session_id=s.id,
                name=s.name,
                tmux_window=s.tmux_window,
                current_status=st.current_state,
                current_activity=st.current_task,
                status_since=st.state_since,
                green_time_seconds=st.green_time_seconds,
                non_green_time_seconds=st.non_green_time_seconds,
                sleep_time_seconds=st.sleep_time_seconds,
                interaction_count=st.interaction_count,
                input_tokens=st.input_tokens,
                output_tokens=st.output_tokens,
                cache_creation_tokens=st.cache_creation_tokens,
                cache_read_tokens=st.cache_read_tokens,
                estimated_cost_usd=st.estimated_cost_usd,
                median_work_time=sorted(st.operation_times)[len(st.operation_times) // 2],
                current_context_tokens=st.current_context_tokens,
                repo_name=s.repo_name,
                branch=s.branch,
                standing_instructions=s.standing_instructions,
                steers_count=st.steers_count,
                start_time=s.start_time,
                permissiveness_mode=s.permissiveness_mode,
                start_directory=s.start_directory,
                is_asleep=s.is_asleep,
                agent_value=s.agent_value,
                model=s.model,
                provider=s.provider,
                backend=s.backend,
                backend_capabilities=capability_names(session_capabilities(s)),
                tags=list(s.tags),
                last_command=rng.choice(pools.prompts)[:120],
                available_skills=list(s.available_skills),
                loaded_skills=list(s.loaded_skills),
                cpu_percent=s.cpu_percent,
                rss_bytes=s.rss_bytes,
                parent_name=by_id[s.parent_session_id].name if s.parent_session_id else None,
                depth=depth(s),
                children_count=children.get(s.id, 0),
            )
        )
    MonitorDaemonState(
        pid=os.getpid(),
        status="active",
        loop_count=int(spec.presence_days * 86400 / 2),
        current_interval=2,
        last_loop_time=now.isoformat(),
        started_at=(now - timedelta(days=spec.presence_days)).isoformat(),
        daemon_version=DAEMON_VERSION,
        sessions=session_states,
        presence_available=True,
        presence_state=4,
        presence_idle_seconds=1.0,
    ).save(paths.daemon_state_path)
    sizes["daemon_state_bytes"] = paths.daemon_state_path.stat().st_size

    # ---- transcripts under ~/.claude/projects ------------------------------
    say(f"transcripts: {spec.transcript_mb:.0f} MB over {spec.agents} agents")
    total_bytes = int(spec.transcript_mb * 1024 * 1024)
    per_agent = total_bytes // max(spec.agents, 1)
    transcript_files = 0
    transcript_bytes = 0
    for i, s in enumerate(live_sessions):
        enc = encode_project_path(s.start_directory)
        ids = s.agent_session_ids
        old_share = 0.25 / max(len(ids) - 1, 1)
        for k, sid in enumerate(ids):
            active = k == len(ids) - 1
            if active:
                target = int(per_agent * 0.70)
                start, end = now - timedelta(hours=spec.history_hours * 0.6), now
            else:
                target = int(per_agent * old_share)
                frac = (k + 1) / len(ids)
                start = session_start
                end = now - timedelta(hours=spec.history_hours * (1 - 0.6 * frac))
            lines = _transcript_lines(rng, pools, sid, s.start_directory, start, end, target)
            transcript_bytes += _write_lines(
                paths.projects_dir / enc / f"{sid}.jsonl", lines, None if active else end
            )
            transcript_files += 1
            if active:
                sub_dir = paths.projects_dir / enc / sid / "subagents"
                sub_target = int(per_agent * 0.05 / max(spec.subagents_per_agent, 1))
                for _ in range(spec.subagents_per_agent):
                    agent_id = "a" + uuid.uuid4().hex[:16]
                    sub_start = now - timedelta(minutes=rng.uniform(5, 90))
                    lines = _transcript_lines(
                        rng,
                        pools,
                        sid,
                        s.start_directory,
                        sub_start,
                        now,
                        sub_target,
                        agent_id=agent_id,
                    )
                    transcript_bytes += _write_lines(sub_dir / f"agent-{agent_id}.jsonl", lines)
                    transcript_files += 1
                # A compaction copy: isMeta first line -> duplicate, skipped by readers.
                dup = [
                    json.dumps(
                        {
                            "type": "user",
                            "isMeta": True,
                            "message": {"role": "user", "content": "compact"},
                            "timestamp": _iso_z(now),
                            "sessionId": sid,
                            "uuid": str(uuid.uuid4()),
                        }
                    )
                ] + _transcript_lines(
                    rng,
                    pools,
                    sid,
                    s.start_directory,
                    now - timedelta(minutes=3),
                    now,
                    per_agent // 100,
                )
                transcript_bytes += _write_lines(
                    sub_dir / f"agent-acompact-{uuid.uuid4().hex[:8]}.jsonl", dup
                )
                transcript_files += 1
    # Every session ever launched left a transcript; historical ones are small.
    small_files = 0
    for s in hist_sessions:
        enc = encode_project_path(s.start_directory)
        started = datetime.fromisoformat(s.start_time)
        for sid in s.agent_session_ids:
            lines = _transcript_lines(
                rng, pools, sid, s.start_directory, started, started + timedelta(minutes=30), 3000
            )
            transcript_bytes += _write_lines(
                paths.projects_dir / enc / f"{sid}.jsonl", lines, started + timedelta(minutes=30)
            )
            small_files += 1
    sizes["transcript_files_live"] = transcript_files
    sizes["transcript_files_total"] = transcript_files + small_files
    sizes["transcript_bytes_total"] = transcript_bytes

    # ---- ~/.claude/history.jsonl -----------------------------------------
    say(f"history.jsonl: {spec.history_lines} lines")
    entries = []
    for s in live_sessions:
        for k, sid in enumerate(s.agent_session_ids):
            n = 10 if k == len(s.agent_session_ids) - 1 else 5
            lo = (
                session_start
                if k < len(s.agent_session_ids) - 1
                else now - timedelta(hours=spec.history_hours * 0.6)
            )
            hi = now if k == len(s.agent_session_ids) - 1 else now - timedelta(hours=1)
            for _ in range(n):
                ts = lo + (hi - lo) * rng.random()
                entries.append((int(ts.timestamp() * 1000), s.start_directory, sid))
    remaining = max(0, spec.history_lines - len(entries))
    hist_pairs = [
        (s.start_directory, sid, datetime.fromisoformat(s.start_time))
        for s in hist_sessions
        for sid in s.agent_session_ids
    ] or [(live_sessions[0].start_directory, live_sessions[0].agent_session_ids[0], session_start)]
    for _ in range(remaining):
        d, sid, started = rng.choice(hist_pairs)
        ts = started + timedelta(minutes=rng.uniform(0, 30))
        entries.append((int(ts.timestamp() * 1000), d, sid))
    entries.sort()
    lines = [
        json.dumps(
            {
                "display": rng.choice(pools.prompts)[: rng.randint(40, 300)],
                "pastedContents": {},
                "timestamp": ts,
                "project": d,
                "sessionId": sid,
            }
        )
        for ts, d, sid in entries
    ]
    sizes["history_bytes"] = _write_lines(paths.history_path, lines)
    sizes["history_lines"] = len(lines)

    # ---- agent_status_history.csv (one row per agent per 2 s) ---------------
    ticks = int(spec.history_hours * 3600 / 2)
    say(f"agent_status_history.csv: {ticks * spec.agents} rows")
    hostname = "bench-host"
    status_of = ["running" if i % 2 == 0 else "waiting_user" for i in range(spec.agents)]
    run_left = [rng.randint(30, 300) for _ in range(spec.agents)]
    activity_of = [s.stats.current_task[:100] for s in live_sessions]
    rows = 0
    with open(paths.agent_history_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(_HISTORY_HEADER)
        t = session_start
        for _ in range(ticks):
            for i, s in enumerate(live_sessions):
                run_left[i] -= 1
                if run_left[i] <= 0:
                    status_of[i] = "waiting_user" if status_of[i] == "running" else "running"
                    run_left[i] = rng.randint(30, 300)
                w.writerow(
                    [
                        (t + timedelta(microseconds=i * 40)).isoformat(),
                        s.name,
                        "asleep" if s.is_asleep else status_of[i],
                        activity_of[i],
                        s.id,
                        hostname,
                    ]
                )
                rows += 1
            t += timedelta(seconds=2)
    # The final row per agent goes through the production writer.
    for i, s in enumerate(live_sessions):
        log_agent_status(
            s.name,
            "asleep" if s.is_asleep else status_of[i],
            activity_of[i],
            history_file=paths.agent_history_path,
            session_id=s.id,
            hostname=hostname,
        )
        rows += 1
    sizes["status_rows"] = rows
    sizes["status_bytes"] = paths.agent_history_path.stat().st_size

    # ---- presence_log.csv (PresenceLogger row format, one row/60 s) ---------
    n_presence = int(spec.presence_days * 1440)
    say(f"presence_log.csv: {n_presence} rows")
    with open(paths.presence_log_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "state", "idle_seconds", "locked", "inferred_sleep"])
        t = now - timedelta(minutes=n_presence)
        for k in range(n_presence):
            hour = t.hour
            if 0 <= hour < 7:
                state, idle, locked = 1, 3600.0, 1
            elif k % 7 == 0:
                state, idle, locked = 2, 120.0, 0
            elif k % 3 == 0:
                state, idle, locked = 4, 1.5, 0
            else:
                state, idle, locked = 3, 8.0, 0
            slept = 1 if hour == 7 and t.minute == 0 else 0
            w.writerow([t.isoformat(), state, f"{idle:.1f}", locked, slept])
            t += timedelta(minutes=1)
    sizes["presence_rows"] = n_presence
    sizes["presence_bytes"] = paths.presence_log_path.stat().st_size

    # ---- hook_state_<name>.json / hook_events_<name>.jsonl -----------------
    say("hook state + event logs")
    with patched_environment(paths):
        for i, s in enumerate(live_sessions):
            for n in range(150):
                ev = "PreToolUse" if n % 2 == 0 else "PostToolUse"
                tool = rng.choice(_TOOLS)
                tool_input = (
                    {"command": rng.choice(pools.commands) + " # " + _text(rng, 60)}
                    if tool == "Bash"
                    else {
                        "file_path": f"{s.start_directory}/src/{rng.choice(_WORDS)}.py",
                        "old_string": _text(rng, 40),
                        "new_string": _text(rng, 40),
                    }
                )
                hook_handler.append_hook_event(
                    ev, spec.tmux_session, s.name, tool_name=tool, tool_input=tool_input
                )
            event, tool, tool_input, tool_use_id = _hook_state_for(i)
            hook_handler.write_hook_state(
                event,
                spec.tmux_session,
                s.name,
                tool_name=tool,
                tool_input=tool_input,
                tool_use_id=tool_use_id,
            )
            hook_handler.write_hook_state(
                "PreToolUse",
                spec.tmux_session,
                s.name,
                tool_name="Skill",
                tool_input={"skill": "overcode"},
            )
            hook_handler.write_hook_state(
                event,
                spec.tmux_session,
                s.name,
                tool_name=tool,
                tool_input=tool_input,
                tool_use_id=tool_use_id,
            )
            hook_handler.append_hook_event(
                event, spec.tmux_session, s.name, tool_name=tool, tool_input=tool_input
            )
    sizes["hook_files"] = 2 * spec.agents
    sizes["hook_events_bytes"] = sum(
        p.stat().st_size for p in paths.session_dir.glob("hook_events_*.jsonl")
    )

    sizes["build_seconds"] = round(time.perf_counter() - t0, 1)
    paths.manifest.write_text(json.dumps({"spec": asdict(spec), "sizes": sizes}, indent=2))
    say(f"done in {sizes['build_seconds']} s")
    return paths


def load_fixture(root: Path, spec: FixtureSpec) -> Optional[FixturePaths]:
    """Return the fixture under ``root`` if it was built from ``spec``."""
    paths = FixturePaths(root=root, tmux_session=spec.tmux_session)
    try:
        manifest = json.loads(paths.manifest.read_text())
    except (OSError, ValueError):
        return None
    if manifest.get("spec") != asdict(spec):
        return None
    return paths


def fixture_sizes(paths: FixturePaths) -> Dict[str, float]:
    try:
        return json.loads(paths.manifest.read_text())["sizes"]
    except (OSError, ValueError, KeyError):
        return {}


# =============================================================================
# Pointing the production code at the fixture
# =============================================================================


@contextlib.contextmanager
def patched_environment(paths: FixturePaths) -> Iterator[None]:
    """Redirect every ``Path.home()`` / module-constant lookup to the fixture.

    Covers: the ``HOME`` / ``OVERCODE_DIR`` / ``OVERCODE_STATE_DIR`` env vars
    (read at call time by settings, hook_handler and hook_status_detector),
    ``Path.home`` itself (time_context, bundled_skills), the import-time
    constants in history_reader / presence_logger / config / settings.PATHS,
    the module-level ``HistoryFile`` singleton, and the *default arguments*
    of the history_reader functions that bound ``CLAUDE_*_PATH`` at import
    time (``get_session_stats`` and friends — patching the constant alone
    leaves those pointing at the real home). Module caches are cleared on
    both entry and exit so measurements start cold and nothing leaks.
    """
    import pathlib

    from overcode import (
        config,
        history_reader,
        presence_logger,
        settings,
        stats_reader,
        status_history,
    )

    env = {
        "HOME": str(paths.home),
        "OVERCODE_DIR": str(paths.overcode_dir),
        "OVERCODE_STATE_DIR": str(paths.state_dir),
    }
    saved_env = {k: os.environ.get(k) for k in env}
    saved_attrs: List[tuple] = []
    saved_defaults: List[tuple] = []

    def patch(obj, name, value):
        original = vars(obj)[name] if name in vars(obj) else getattr(obj, name)
        saved_attrs.append((obj, name, original))
        setattr(obj, name, value)

    def clear_caches():
        history_reader._session_stats_cache.clear()
        status_history._readers.clear()
        stats_reader.clear_reader_cache()
        config._clear_config_cache()
        settings._user_config = None
        presence_logger._presence_cache = []
        presence_logger._presence_cache_mtime = 0.0
        presence_logger._presence_cache_size = 0
        presence_logger._presence_cache_hours = 0.0

    os.environ.update(env)
    try:
        patch(pathlib.Path, "home", classmethod(lambda cls: pathlib.Path(paths.home)))
        mapping = {
            history_reader.CLAUDE_HISTORY_PATH: paths.history_path,
            history_reader.CLAUDE_PROJECTS_PATH: paths.projects_dir,
        }
        patch(history_reader, "CLAUDE_HISTORY_PATH", paths.history_path)
        patch(history_reader, "CLAUDE_PROJECTS_PATH", paths.projects_dir)
        patch(history_reader, "_default_history", history_reader.HistoryFile(paths.history_path))
        funcs = [f for f in vars(history_reader).values() if inspect.isfunction(f)]
        funcs.append(history_reader.HistoryFile.__init__)
        for fn in funcs:
            defaults = fn.__defaults__
            if not defaults:
                continue
            new = tuple(mapping.get(d, d) if isinstance(d, Path) else d for d in defaults)
            if new != defaults:
                saved_defaults.append((fn, defaults))
                fn.__defaults__ = new
        patch(presence_logger, "OVERCODE_DIR", paths.overcode_dir)
        patch(presence_logger, "PRESENCE_PID_FILE", paths.overcode_dir / "presence.pid")
        patch(config, "CONFIG_PATH", paths.overcode_dir / "config.yaml")
        patch(settings.PATHS, "base_dir", paths.overcode_dir)
        clear_caches()
        yield
    finally:
        clear_caches()
        for fn, defaults in saved_defaults:
            fn.__defaults__ = defaults
        for obj, name, original in reversed(saved_attrs):
            setattr(obj, name, original)
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextlib.contextmanager
def _patched(obj, name: str, value) -> Iterator[None]:
    original = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, original)


# =============================================================================
# Counting: sessions.json I/O, tmux interface calls, subprocess spawns
# =============================================================================


@dataclass
class IoCounters:
    reads: int = 0  # sessions.json opened for reading (one parse each)
    writes: int = 0  # fsync'd writes landing on sessions.json
    spawns: int = 0  # subprocesses (ps/lsof stubs count as the spawn they replace)


def _fd_path(fd: int) -> Optional[str]:
    try:
        if sys.platform == "darwin":
            import fcntl

            raw = fcntl.fcntl(fd, fcntl.F_GETPATH, b"\0" * 1024)
            return raw.split(b"\0", 1)[0].decode()
        return os.readlink(f"/proc/self/fd/{fd}")
    except (OSError, AttributeError, ValueError):
        return None


@contextlib.contextmanager
def count_io(paths: FixturePaths, counters: Optional[IoCounters] = None) -> Iterator[IoCounters]:
    """Count sessions.json reads/writes and subprocess spawns in the block.

    Reads are counted at ``open()`` so a future stat-gated cache that skips
    the open shows up as a saved read; writes are counted at ``os.fsync`` on
    ``sessions.json`` or its ``sessions.tmp.*`` temp file so batching shows
    up as fewer writes. Both are independent of SessionManager's internals.
    """
    import builtins

    counters = counters if counters is not None else IoCounters()
    orig_io_open = io.open
    sessions_file = str(paths.sessions_file)
    orig_open, orig_fsync = builtins.open, os.fsync
    orig_run, orig_popen = subprocess.run, subprocess.Popen

    def counting_open(file, mode="r", *args, **kwargs):
        try:
            if os.fspath(file) == sessions_file and ("r" in mode or "+" in mode):
                counters.reads += 1
        except TypeError:
            pass
        return orig_open(file, mode, *args, **kwargs)

    def counting_fsync(fd):
        path = _fd_path(fd)
        if path is None or os.path.basename(path).startswith("sessions."):
            counters.writes += 1
        return orig_fsync(fd)

    def counting_run(*args, **kwargs):
        counters.spawns += 1
        return orig_run(*args, **kwargs)

    class CountingPopen(orig_popen):  # type: ignore[misc,valid-type]
        def __init__(self, *args, **kwargs):
            counters.spawns += 1
            super().__init__(*args, **kwargs)

    builtins.open = counting_open
    io.open = counting_open
    os.fsync = counting_fsync
    subprocess.run = counting_run
    subprocess.Popen = CountingPopen
    try:
        yield counters
    finally:
        builtins.open = orig_open
        io.open = orig_io_open
        os.fsync = orig_fsync
        subprocess.run = orig_run
        subprocess.Popen = orig_popen


class CountingTmux:
    """``TmuxInterface`` double that serves fixture panes and counts calls.

    Each method call stands for at least one real tmux command; a fresh
    ``RealTmux().get_pane_pid`` is three (list-sessions, list-windows,
    list-panes), so ``total`` is a lower bound on the production count.
    """

    def __init__(self, session: str, panes: Dict[str, str], pids: Dict[str, int]):
        self.session = session
        self._panes = dict(panes)
        self._pids = dict(pids)
        self.calls: Counter = Counter()
        self.sent_keys: List[tuple] = []

    @property
    def total(self) -> int:
        return sum(self.calls.values())

    def reset(self) -> None:
        self.calls.clear()

    def capture_pane(self, session: str, window: str, lines: int = 100) -> Optional[str]:
        self.calls["capture_pane"] += 1
        content = self._panes.get(window) if session == self.session else None
        if content is None:
            return None
        return "\n".join(content.split("\n")[-lines:])

    def get_pane_pid(self, session: str, window: str) -> Optional[int]:
        self.calls["get_pane_pid"] += 1
        return self._pids.get(window) if session == self.session else None

    def list_windows(self, session: str) -> List[Dict[str, object]]:
        self.calls["list_windows"] += 1
        if session != self.session:
            return []
        return [
            {"index": i + 1, "name": name, "active": i == 0} for i, name in enumerate(self._panes)
        ]

    def has_session(self, session: str) -> bool:
        self.calls["has_session"] += 1
        return session == self.session

    session_exists = has_session

    def send_keys(self, session: str, window: str, keys: str, enter: bool = True) -> bool:
        self.calls["send_keys"] += 1
        self.sent_keys.append((session, window, keys, enter))
        return window in self._panes

    def kill_window(self, session: str, window: str) -> bool:
        self.calls["kill_window"] += 1
        return self._panes.pop(window, None) is not None

    def new_session(self, session: str) -> bool:
        self.calls["new_session"] += 1
        return False

    def new_window(self, session, name, command=None, cwd=None):
        self.calls["new_window"] += 1
        return None

    def kill_session(self, session: str) -> bool:
        self.calls["kill_session"] += 1
        return False

    def attach(self, session, window=None, bare=False) -> None:
        self.calls["attach"] += 1

    def select_window(self, session: str, window: str) -> bool:
        self.calls["select_window"] += 1
        return True

    def ensure_empty_placeholder_window(self, session, window_name, message) -> bool:
        self.calls["ensure_empty_placeholder_window"] += 1
        return True


# =============================================================================
# Timed sites
# =============================================================================


@dataclass
class SiteResult:
    site: str
    tick: str  # the timer this work hangs off
    ms_per_call: float
    calls_per_tick: float
    ms_per_tick: float
    reads: int = 0
    writes: int = 0
    tmux_cmds: int = 0
    spawns: int = 0
    note: str = ""


def _ms(fn: Callable[[], object], reps: int = 3) -> float:
    """Best-of-``reps`` wall time in milliseconds."""
    best = float("inf")
    for _ in range(max(1, reps)):
        t0 = time.perf_counter()
        fn()
        best = min(best, (time.perf_counter() - t0) * 1000)
    return best


def _result(site, tick, ms_call, calls, **kw) -> SiteResult:
    return SiteResult(site, tick, round(ms_call, 3), calls, round(ms_call * calls, 3), **kw)


def live_sessions(paths: FixturePaths):
    """The sessions the daemon tick iterates (this tmux session only)."""
    from overcode.session_manager import SessionManager

    sm = SessionManager(state_dir=paths.state_dir)
    return [s for s in sm.list_sessions() if s.tmux_session == paths.tmux_session]


def time_list_sessions(paths: FixturePaths, reps: int = 3) -> List[SiteResult]:
    """``SessionManager.list_sessions`` — every TUI worker and the daemon tick call it."""
    from overcode.session_manager import SessionManager

    def cold():
        SessionManager(state_dir=paths.state_dir).list_sessions()

    sm = SessionManager(state_dir=paths.state_dir)
    sm.list_sessions()
    note = "TUI: 4/s fast path + 2/s status bar + 1/5 s + 1/10 s; daemon 1/2 s"
    return [
        _result("list_sessions (cold)", "TUI 1 s status", _ms(cold, reps), 2, note=note),
        _result(
            "list_sessions (warm, unchanged file)",
            "TUI 1 s status",
            _ms(sm.list_sessions, reps),
            2,
            note=note,
        ),
    ]


def time_daemon_state_load(paths: FixturePaths, reps: int = 3) -> List[SiteResult]:
    """``MonitorDaemonState.load`` — 4/s fast path + 1/s status bar + 1/10 s."""
    from overcode.monitor_daemon_state import MonitorDaemonState

    def load():
        MonitorDaemonState.load(paths.daemon_state_path)

    cold = _ms(load, 1)
    note = "TUI: 4/s fast path + 1/s status bar + 1/10 s"
    return [
        _result("daemon-state load (cold)", "TUI 250 ms fast", cold, 1, note=note),
        _result(
            "daemon-state load (warm, unchanged file)",
            "TUI 250 ms fast",
            _ms(load, reps),
            1,
            note=note,
        ),
    ]


def time_window_burn(paths: FixturePaths, hours: float = 1.0, reps: int = 2) -> List[SiteResult]:
    """``tui_logic.compute_window_burn`` over every live session (status bar, 1 Hz)."""
    from overcode import history_reader
    from overcode.tui_logic import compute_window_burn

    sessions = live_sessions(paths)
    asleep = {s.id for s in sessions if s.is_asleep}

    def burn():
        return compute_window_burn(sessions, asleep, hours)

    history_reader._session_stats_cache.clear()
    cold = _ms(burn, 1)
    warm = _ms(burn, reps)
    stats = burn()
    note = f"window {hours:g} h, {len(stats.per_session)} sessions with tokens, {stats.total_tokens} tokens"
    return [
        _result("compute_window_burn (cold)", "TUI 1 s status", cold, 1, note=note),
        _result(
            "compute_window_burn (warm, no file changed)", "TUI 1 s status", warm, 1, note=note
        ),
    ]


def _append_transcript_line(paths: FixturePaths, session) -> None:
    """Append one assistant message to a session's active transcript."""
    from overcode.history_reader import get_session_file_path

    path = get_session_file_path(
        session.start_directory, session.active_agent_session_id, paths.projects_dir
    )
    now = datetime.now()
    line = json.dumps(
        {
            "type": "assistant",
            "uuid": str(uuid.uuid4()),
            "sessionId": session.active_agent_session_id,
            "timestamp": _iso_z(now),
            "message": {
                "model": "claude-opus-4-6",
                "id": "msg_01" + uuid.uuid4().hex[:22],
                "role": "assistant",
                "content": [{"type": "text", "text": "touched"}],
                "usage": {
                    "input_tokens": 7,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 120_000,
                    "output_tokens": 42,
                },
            },
        }
    )
    with open(path, "a") as f:
        f.write(line + "\n")


def time_stats_sweep(paths: FixturePaths, reps: int = 2) -> List[SiteResult]:
    """The TUI 5 s sweep: fresh ``HistoryFile`` + ``ClaudeStatsReader.get_stats`` per session.

    Mirrors ``tui._update_stats_async`` (one ``HistoryFile()`` per sweep, one
    ``get_stats`` per widget) minus the thread pool — the parse is
    GIL-bound, so serial wall time is the CPU the sweep costs.
    """
    from overcode import history_reader
    from overcode.history_reader import HistoryFile
    from overcode.stats_reader import stats_reader_for_session

    sessions = live_sessions(paths)
    n = len(sessions)

    def sweep():
        hf = HistoryFile()
        for s in sessions:
            stats_reader_for_session(s).get_stats(s, history_file=hf)

    history_reader._session_stats_cache.clear()
    cold = _ms(sweep, 1)
    warm = _ms(sweep, reps)
    _append_transcript_line(paths, sessions[0])
    touched = _ms(sweep, 1)
    tick = "TUI 5 s stats"
    return [
        _result(
            "stats sweep get_stats (cold)",
            tick,
            cold / n,
            n,
            note=f"{n} sessions, fresh HistoryFile per sweep",
        ),
        _result("stats sweep get_stats (warm, nothing changed)", tick, warm / n, n),
        _result("stats sweep get_stats (warm, one transcript appended)", tick, touched / n, n),
    ]


def time_status_history(
    paths: FixturePaths, baseline_minutes: int = 60, reps: int = 3
) -> List[SiteResult]:
    """``StatusHistoryFile.read`` + ``calculate_mean_spin_from_history`` (status bar, 1 Hz)."""
    from overcode.status_history import StatusHistoryFile, log_agent_status
    from overcode.tui_logic import calculate_mean_spin_from_history

    hours = baseline_minutes / 60.0 + 0.1
    sessions = live_sessions(paths)
    names = [s.name for s in sessions if not s.is_asleep]

    def cold():
        StatusHistoryFile(paths.agent_history_path).read(hours=hours)

    reader = StatusHistoryFile(paths.agent_history_path)
    history = reader.read(hours=hours)
    warm = _ms(lambda: reader.read(hours=hours), reps)

    def appended():
        for s in sessions:
            log_agent_status(
                s.name,
                "running",
                "Active: Read(x.py)",
                history_file=paths.agent_history_path,
                session_id=s.id,
                hostname="bench-host",
            )
        reader.read(hours=hours)

    incremental = _ms(appended, 1)
    spin = _ms(lambda: calculate_mean_spin_from_history(history, names, baseline_minutes), reps)
    tick = "TUI 1 s status"
    note = f"{len(history)} rows in window, {len(names)} active agents"
    return [
        _result("status-history read (cold)", tick, _ms(cold, 1), 1, note=note),
        _result("status-history read (warm, unchanged)", tick, warm, 1, note=note),
        _result(
            "status-history read (incremental, one daemon tick appended)", tick, incremental, 1
        ),
        _result("calculate_mean_spin_from_history", tick, spin, 1, note=note),
    ]


def time_presence(paths: FixturePaths, hours: float = 3.0, reps: int = 3) -> List[SiteResult]:
    """``presence_logger.read_presence_history`` (timeline worker, 30 s)."""
    from overcode import presence_logger

    def reset():
        presence_logger._presence_cache_mtime = 0.0
        presence_logger._presence_cache_size = 0

    reset()
    cold = _ms(lambda: presence_logger.read_presence_history(hours), 1)
    warm = _ms(lambda: presence_logger.read_presence_history(hours), reps)

    def appended():
        with open(paths.presence_log_path, "a", newline="") as f:
            csv.writer(f).writerow([datetime.now().isoformat(), 4, "1.0", 0, 0])
        presence_logger.read_presence_history(hours)

    grown = _ms(appended, 1)
    tick = "TUI 30 s timeline"
    return [
        _result("presence read (cold)", tick, cold, 1),
        _result("presence read (warm, unchanged)", tick, warm, 1),
        _result("presence read (one row appended)", tick, grown, 1),
    ]


def time_timeline_slots(
    paths: FixturePaths, hours: float = 3.0, width: int = 200, reps: int = 3
) -> List[SiteResult]:
    """Timeline: history grouping in the worker + ``build_timeline_slots`` per row (main thread)."""
    from overcode.presence_logger import read_presence_history
    from overcode.status_history import read_agent_status_history
    from overcode.tui_helpers import build_timeline_slots

    sessions = live_sessions(paths)
    read_agent_status_history(hours=hours, history_file=paths.agent_history_path)
    read_presence_history(hours)

    def fetch():
        presence = read_presence_history(hours)
        agent_histories: Dict[str, list] = {}
        for ts, agent, status, *_ in read_agent_status_history(
            hours=hours, history_file=paths.agent_history_path
        ):
            agent_histories.setdefault(agent, []).append((ts, status))
        return presence, agent_histories

    presence, agent_histories = fetch()
    fetch_ms = _ms(fetch, reps)
    now = datetime.now()

    def render_slots():
        build_timeline_slots(presence, width, hours, now)
        for s in sessions:
            build_timeline_slots(agent_histories.get(s.name, []), width, hours, now)

    slots_ms = _ms(render_slots, reps)
    tick = "TUI 30 s timeline"
    n = len(sessions) + 1
    return [
        _result(
            "timeline fetch_history_data (warm reads + grouping)",
            tick,
            fetch_ms,
            1,
            note=f"{sum(map(len, agent_histories.values()))} rows",
        ),
        _result(
            "timeline build_timeline_slots (render, main thread)",
            tick,
            slots_ms / n,
            n,
            note=f"width {width}",
        ),
    ]


def time_discover_session_ids(paths: FixturePaths, reps: int = 2) -> List[SiteResult]:
    """``ClaudeStatsReader.discover_session_ids`` — the daemon's zero-token recovery path (10 s)."""
    from overcode.stats_reader import ClaudeStatsReader

    sessions = live_sessions(paths)
    session = sessions[0]
    since = datetime.fromisoformat(session.start_time)
    reader = ClaudeStatsReader()

    def discover():
        return reader.discover_session_ids(session, since, sessions)

    cold = _ms(discover, 1)
    warm = _ms(discover, reps)
    found = discover()
    tick = "daemon 10 s per zero-token agent"
    note = f"{len(found.ids)} unowned ids found"
    return [
        _result("discover_session_ids (cold)", tick, cold, 1, note=note),
        _result("discover_session_ids (warm, history unchanged)", tick, warm, 1, note=note),
    ]


# ---- daemon -----------------------------------------------------------------


def _process_table(sessions, pids: Dict[str, int]) -> List[tuple]:
    """Synthetic ``ps`` rows: a shell per pane with a claude child, plus noise."""
    rows: List[tuple] = [(1, 0, "/sbin/launchd")]
    for i, s in enumerate(sessions):
        pane = pids[s.tmux_window]
        rows.append((pane, 1, "-zsh"))
        rows.append(
            (
                pane + 10_000,
                pane,
                f"claude --session-id {s.active_agent_session_id} --settings {{...}}",
            )
        )
        rows.append(
            (
                pane + 20_000,
                pane + 10_000,
                "node /opt/homebrew/lib/node_modules/typescript/bin/tsc --watch",
            )
        )
    for k in range(500):
        rows.append((60_000 + k, 1, f"/usr/libexec/noise-{k}"))
    return rows


@contextlib.contextmanager
def daemon_doubles(paths: FixturePaths, sessions, counters: IoCounters) -> Iterator[CountingTmux]:
    """Fake tmux + synthetic ps/lsof for the daemon phases.

    The daemon builds a fresh ``RealTmux()`` inside ``_sync_process_resources``
    and ``_sync_sandbox_state``, so the class is replaced by a factory that
    hands back one counting double. ``ps`` and ``lsof`` are replaced by
    stubs that return the synthetic table and count themselves as the one
    spawn each would cost.
    """
    from overcode import doctor, implementations, process_resources, sandbox_detect
    from overcode.process_resources import ProcInfo

    pane = _pane_text()
    pids = {s.tmux_window: 10_000 + i for i, s in enumerate(sessions)}
    tmux = CountingTmux(paths.tmux_session, {s.tmux_window: pane for s in sessions}, pids)
    rows = _process_table(sessions, pids)
    cpu_of = {s.tmux_window: (s.cpu_percent, s.rss_bytes) for s in sessions}

    def snapshot_processes():
        counters.spawns += 1
        table = {}
        for pid, ppid, argv in rows:
            cpu, rss_kb = 0.0, 96  # noise stays under the 1 MiB write threshold
            if argv.startswith("claude "):
                cpu, rss = cpu_of[next(w for w, p in pids.items() if p == ppid)]
                rss_kb = rss // 1024
            table[pid] = ProcInfo(ppid=ppid, cpu_pct=cpu, rss_kb=rss_kb, argv=argv)
        return table

    def snapshot_process_table():
        counters.spawns += 1
        return list(rows)

    def run_lsof(pids_iter, timeout=3.0):
        counters.spawns += 1
        return ""

    with contextlib.ExitStack() as stack:
        stack.enter_context(_patched(implementations, "RealTmux", lambda *a, **k: tmux))
        stack.enter_context(_patched(process_resources, "snapshot_processes", snapshot_processes))
        stack.enter_context(_patched(doctor, "_snapshot_process_table", snapshot_process_table))
        stack.enter_context(_patched(sandbox_detect, "_run_lsof", run_lsof))
        yield tmux


def make_daemon(paths: FixturePaths, tmux: CountingTmux):
    """A ``MonitorDaemon`` on the fixture with the tmux double wired into both detectors."""
    from rich.console import Console

    from overcode import monitor_daemon
    from overcode.daemon_logging import DAEMON_THEME
    from overcode.session_manager import SessionManager
    from overcode.status_detector import PollingStatusDetector
    from overcode.status_detector_factory import StatusDetectorDispatcher

    with _patched(monitor_daemon, "PresenceLogger", None):
        daemon = monitor_daemon.MonitorDaemon(
            tmux_session=paths.tmux_session,
            session_manager=SessionManager(state_dir=paths.state_dir),
            status_detector=PollingStatusDetector(paths.tmux_session, tmux=tmux),
        )
    # The dispatcher built by __init__ gives the hook detector no tmux, which
    # would fall back to a real capture-pane subprocess; rebuild it on the double.
    daemon.detector = StatusDetectorDispatcher(
        paths.tmux_session, tmux=tmux, mode=daemon.detector.mode
    )
    daemon.log.console = Console(file=io.StringIO(), theme=DAEMON_THEME, force_terminal=True)
    daemon._legacy_windows_migrated = True
    daemon.state.loop_count = 1
    return daemon


def seed_steady_state(daemon, sessions, now: datetime) -> None:
    """Make the next tick look like any tick after the first (no first-observation skips)."""
    for s in sessions:
        daemon.previous_states[s.id] = s.stats.current_state
        daemon.last_state_times[s.id] = now - timedelta(seconds=2)
    for name in (
        "_last_stats_sync",
        "_last_session_id_sync",
        "_last_skills_sync",
        "_last_sandbox_sync",
        "_last_resources_sync",
        "_last_history_rotation_check",
        "_last_model_metadata_check",
    ):
        setattr(daemon, name, now)


def time_daemon_phases(paths: FixturePaths, include_syncs: bool = False) -> List[SiteResult]:
    """The daemon's per-tick session body plus its periodic syncs, with I/O counts.

    ``_detect_and_enrich`` is timed once in steady state (the tick after the
    first), with sessions.json reads/writes, tmux calls and spawns counted.
    ``_sync_process_resources`` (5 s) and ``_sync_sandbox_state`` (15 s) are
    forced due. With ``include_syncs`` the 10 s session-id sync and the 60 s
    stats sync run too (they rewrite sessions.json per agent and are slow at
    scale, so they are opt-in for the quick preset).
    """
    sessions = live_sessions(paths)
    n = len(sessions)
    now = datetime.now()
    counters = IoCounters()
    results: List[SiteResult] = []
    with daemon_doubles(paths, sessions, counters) as tmux:
        daemon = make_daemon(paths, tmux)
        seed_steady_state(daemon, sessions, now)

        def phase(site, tick, fn, calls=n, note=""):
            counters.reads = counters.writes = counters.spawns = 0
            tmux.reset()
            with count_io(paths, counters):
                ms = _ms(fn, 1)
            results.append(
                SiteResult(
                    site,
                    tick,
                    round(ms / calls, 3),
                    calls,
                    round(ms, 3),
                    reads=counters.reads,
                    writes=counters.writes,
                    tmux_cmds=tmux.total,
                    spawns=counters.spawns,
                    note=note,
                )
            )

        produced: List[list] = []
        phase(
            "daemon _detect_and_enrich (steady state)",
            "daemon 2 s",
            lambda: produced.append(daemon._detect_and_enrich(sessions, datetime.now())[0]),
            note=f"{n} sessions, hooks mode; reads/writes are sessions.json",
        )
        daemon._last_resources_sync = None
        phase(
            "daemon _sync_process_resources",
            "daemon 5 s",
            lambda: daemon._sync_process_resources(sessions, datetime.now()),
        )
        daemon._last_sandbox_sync = None
        phase(
            "daemon _sync_sandbox_state",
            "daemon 15 s",
            lambda: daemon._sync_sandbox_state(sessions, datetime.now()),
        )
        phase(
            "daemon _publish_state",
            "daemon 2 s",
            lambda: daemon._publish_state(produced[0]),
            calls=1,
        )
        if include_syncs:
            daemon._last_session_id_sync = None
            phase(
                "daemon _sync_session_ids",
                "daemon 10 s",
                lambda: daemon._sync_session_ids(sessions, datetime.now()),
            )
            daemon._last_stats_sync = None
            phase(
                "daemon _sync_session_stats",
                "daemon 60 s",
                lambda: daemon._sync_session_stats(sessions, datetime.now()),
            )
    return results


# =============================================================================
# Driver
# =============================================================================

SITES: Dict[str, Callable[..., List[SiteResult]]] = {
    "list_sessions": time_list_sessions,
    "daemon_state": time_daemon_state_load,
    "window_burn": time_window_burn,
    "stats_sweep": time_stats_sweep,
    "status_history": time_status_history,
    "presence": time_presence,
    "timeline": time_timeline_slots,
    "discover_ids": time_discover_session_ids,
    "daemon": time_daemon_phases,
}


def run_all(
    paths: FixturePaths,
    sites: Optional[Sequence[str]] = None,
    include_syncs: bool = False,
    quiet: bool = False,
) -> List[SiteResult]:
    results: List[SiteResult] = []
    for name, fn in SITES.items():
        if sites and name not in sites:
            continue
        if not quiet:
            print(f"  [time] {name}", file=sys.stderr, flush=True)
        kwargs = {"include_syncs": include_syncs} if name == "daemon" else {}
        results.extend(fn(paths, **kwargs))
    return results


def format_table(results: Sequence[SiteResult], sizes: Dict[str, float]) -> str:
    cols = ["site", "tick", "ms/call", "calls/tick", "ms/tick", "reads", "writes", "tmux", "spawns"]
    rows = [
        [
            r.site,
            r.tick,
            f"{r.ms_per_call:.3f}",
            f"{r.calls_per_tick:g}",
            f"{r.ms_per_tick:.1f}",
            str(r.reads),
            str(r.writes),
            str(r.tmux_cmds),
            str(r.spawns),
        ]
        for r in results
    ]
    widths = [max(len(c), *(len(row[i]) for row in rows)) for i, c in enumerate(cols)]
    out = []
    out.append(
        "  ".join(c.ljust(widths[i]) if i < 2 else c.rjust(widths[i]) for i, c in enumerate(cols))
    )
    out.append("  ".join("-" * w for w in widths))
    for row in rows:
        out.append(
            "  ".join(
                v.ljust(widths[i]) if i < 2 else v.rjust(widths[i]) for i, v in enumerate(row)
            )
        )
    notes = [f"  {r.site}: {r.note}" for r in results if r.note]
    if notes:
        out.append("")
        out.append("notes:")
        out.extend(notes)
    out.append("")
    out.append("fixture:")
    for k, v in sizes.items():
        out.append(f"  {k}: {v:,}" if isinstance(v, int) else f"  {k}: {v}")
    return "\n".join(out)


def _spec_from_args(args: argparse.Namespace) -> FixtureSpec:
    spec = FixtureSpec.power() if args.power else FixtureSpec.quick()
    for f in fields(FixtureSpec):
        value = getattr(args, f.name, None)
        if value is not None:
            setattr(spec, f.name, value)
    return spec


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    preset = parser.add_mutually_exclusive_group()
    preset.add_argument(
        "--quick", action="store_true", help="50 agents, 2k sessions, 50 MB, 3 h (default)"
    )
    preset.add_argument("--power", action="store_true", help="50 agents, 20k sessions, 1 GB, 24 h")
    for f in fields(FixtureSpec):
        if f.name == "tmux_session":
            continue
        parser.add_argument(
            f"--{f.name.replace('_', '-')}",
            type=type(f.default),
            default=None,
            help=f"override preset ({f.default})",
        )
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="fixture root to build in / reuse (default: fresh tempdir)",
    )
    parser.add_argument(
        "--sites", default=None, help="comma-separated subset of: " + ",".join(SITES)
    )
    parser.add_argument(
        "--daemon-syncs",
        action="store_true",
        help="also time the daemon's 10 s / 60 s syncs (slow at scale)",
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable results")
    parser.add_argument(
        "--keep", action="store_true", help="keep a fresh tempdir fixture after the run"
    )
    args = parser.parse_args(argv)

    spec = _spec_from_args(args)
    quiet = args.json
    if args.dir is not None:
        root = args.dir
        paths = load_fixture(root, spec)
        if paths is None:
            paths = build_fixture(spec, root, quiet=quiet)
        cleanup = None
    else:
        import tempfile

        tmp = tempfile.mkdtemp(prefix="overcode-bench-")
        paths = build_fixture(spec, Path(tmp), quiet=quiet)
        cleanup = None if args.keep else tmp

    sites = args.sites.split(",") if args.sites else None
    t0 = time.perf_counter()
    with patched_environment(paths):
        results = run_all(paths, sites, include_syncs=args.daemon_syncs, quiet=quiet)
    elapsed = time.perf_counter() - t0
    sizes = fixture_sizes(paths)
    sizes["timing_seconds"] = round(elapsed, 1)
    sizes["platform"] = f"{platform.system()} {platform.machine()} py{platform.python_version()}"

    if args.json:
        print(
            json.dumps(
                {
                    "spec": asdict(spec),
                    "fixture": str(paths.root),
                    "sizes": sizes,
                    "results": [asdict(r) for r in results],
                },
                indent=2,
            )
        )
    else:
        print(f"fixture: {paths.root}")
        print(format_table(results, sizes))
    if cleanup:
        import shutil

        shutil.rmtree(cleanup, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
