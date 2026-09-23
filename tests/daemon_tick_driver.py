"""Run a scripted sequence of daemon ticks over 50 sessions and dump the result.

The scenario exercises every writer in the tick body and the periodic
syncs — state transitions (operation times), a window that vanishes and
comes back, a done agent, an asleep agent, a due heartbeat, a PR number in
the pane, a branch change that clears it, a loaded-skills change, the 60 s
stats sync (tokens, model, last command), the 10 s session-id sync (an
unchanged id, then a new one), and the 5 s process-resources sync (an
unchanged reading, then a changed one) — under a frozen clock, so two runs
of the same scenario over the same fixture produce the same sessions.json.

``run_scenario`` is what the unit tests call; ``__main__`` runs it in a
subprocess against whatever ``overcode`` is first on ``PYTHONPATH`` — the
identity test points one run at a frozen copy of an older source tree and
diffs the two result files. Only APIs common to both trees are used.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, Iterator, Optional

TMUX_SESSION = "agents"
N_SESSIONS = 50
START = datetime(2026, 9, 23, 12, 0, 0)
TICKS = (
    6  # t = 0, 2, ..., 10 s: the 5 s resources sync is due again at 6 s, the 10 s id sync at 10 s
)


def _index(session) -> int:
    return int(session.name.rsplit("-", 1)[1])


def status_script(tick: int, session) -> tuple:
    """(status, activity, pane_content) per session per tick."""
    i = _index(session)
    kind = i % 10
    if kind == 0:  # running <-> waiting, an operation completes on tick 2
        seq = ["running", "waiting_user", "running", "waiting_user", "running", "running"]
        st = seq[tick]
        return st, ("Active: Read(a.py)" if st == "running" else "Waiting for input"), "pane"
    if kind == 1:  # a PR link in the pane; the branch moves before tick 2
        return "running", "Active: gh pr view", "see https://github.com/acme/repo/pull/123 now"
    if kind == 2:  # window gone from tick 1, back on tick 4
        if 1 <= tick < 4:
            return "terminated", "Window no longer exists", ""
        return "running", "Active: Bash(pytest)", "pane"
    if kind == 5:  # heartbeat agent, keeps running
        return "running", "Active: Edit(b.py)", "pane"
    return "waiting_user", "Waiting for input", "pane"


def skills_script(tick: int, name: str) -> list:
    i = int(name.rsplit("-", 1)[1])
    if i % 10 == 7 and tick >= 2:
        return ["dataviz", "overcode"]
    return ["overcode"]


class _ScriptedReader:
    """The parts of a stats reader the 10 s / 60 s syncs call."""

    def __init__(self, clock_tick: Callable[[], int]):
        self._tick = clock_tick

    def get_container_stats(self, session):
        return None

    def get_current_session_id(self, session, session_start):
        i = _index(session)
        if i % 10 == 8 and self._tick() >= 5:
            return f"claude-new-{i:04d}"
        return session.active_agent_session_id

    def get_stats(self, session):
        from overcode.history_reader import AgentSessionStats

        i = _index(session)
        return AgentSessionStats(
            interaction_count=5 + i,
            input_tokens=1000 * (i + 1),
            output_tokens=100 * (i + 1),
            cache_creation_tokens=10 * i,
            cache_read_tokens=5000 * i,
            work_times=[12.5, 30.0],
            current_context_tokens=20_000 + i,
            model="claude-opus-4-6",
            provider="web",
            last_command=f"do task {i}",
        )

    def discover_session_ids(self, session, session_start, all_sessions):
        raise AssertionError("the slow path is not part of the scenario")


@contextlib.contextmanager
def _scenario_doubles(sessions, tmux, tick_of: Callable[[], int]) -> Iterator[None]:
    """Patch the seams the periodic syncs reach for, on whichever tree is loaded."""
    from unittest.mock import patch

    from overcode import doctor, implementations, monitor_daemon, process_resources
    from overcode.process_resources import ProcInfo

    def snapshot_processes():
        table = {1: ProcInfo(ppid=0, cpu_pct=0.0, rss_kb=100, argv="/sbin/launchd")}
        for s in sessions:
            i = _index(s)
            pane = tmux.pids[s.tmux_window]
            cpu, rss = s.cpu_percent, s.rss_bytes
            if i % 10 == 9 and tick_of() >= 3:
                cpu, rss = 50.0, rss + 64 * 1024 * 1024
            table[pane] = ProcInfo(ppid=1, cpu_pct=0.0, rss_kb=96, argv="-zsh")
            table[pane + 10_000] = ProcInfo(
                ppid=pane,
                cpu_pct=cpu,
                rss_kb=rss // 1024,
                argv=f"claude --session-id {s.active_agent_session_id}",
            )
        return table

    reader = _ScriptedReader(tick_of)
    with (
        patch.object(implementations, "RealTmux", lambda *a, **k: tmux),
        patch.object(process_resources, "snapshot_processes", snapshot_processes),
        patch.object(doctor, "_snapshot_process_table", lambda: []),
        patch.object(monitor_daemon, "stats_reader_for_session", lambda s: reader),
        patch.object(monitor_daemon, "send_text_to_tmux_window", lambda *a, **k: True),
    ):
        yield


def seed_scenario(root: Path):
    """Write the fixture under ``root`` and return (session_manager, sessions)."""
    from tests.daemon_tick_harness import seed_sessions, settle
    from overcode.session_manager import SessionManager

    state_dir = root / "home" / ".overcode" / "sessions"
    state_dir.mkdir(parents=True, exist_ok=True)
    (root / "home" / ".claude").mkdir(parents=True, exist_ok=True)
    sm = SessionManager(state_dir=state_dir, skip_git_detection=True)
    sessions = seed_sessions(
        sm, N_SESSIONS, TMUX_SESSION, root / "work", START, other_tmux_sessions=5
    )
    state = sm._load_state()
    for s in sessions:
        kind = _index(s) % 10
        entry = state[s.id]
        if kind == 1:
            entry["pr_number"] = 7
            entry["pr_branch"] = "main"
        elif kind == 3:
            entry["status"] = "done"
            entry["stats"]["current_state"] = "done"
        elif kind == 4:
            entry["is_asleep"] = True
        elif kind == 5:
            entry["heartbeat_enabled"] = True
            entry["heartbeat_instruction"] = "continue"
            entry["heartbeat_frequency_seconds"] = 300
            entry["last_heartbeat_time"] = (START - timedelta(minutes=10)).isoformat()
    sm._save_state(state)
    settle(sm.state_file)
    return sm, [sm.get_session(s.id) for s in sessions]


def run_scenario(root: Path, ticks: int = TICKS, counters=None) -> Dict[str, object]:
    """Seed, run ``ticks`` ticks, and return {"sessions": ..., "daemon_sessions": ...}."""
    from tests.daemon_tick_harness import (
        FakeTmux,
        FrozenClock,
        ScriptedDetector,
        make_daemon,
        run_ticks,
    )

    sm, sessions = seed_scenario(root)
    detector = ScriptedDetector(status_script, skills_script)
    tmux = FakeTmux(
        TMUX_SESSION,
        {s.tmux_window: "pane" for s in sessions},
        {s.tmux_window: 10_000 + _index(s) for s in sessions},
    )
    daemon = make_daemon(
        root / "home" / ".overcode", TMUX_SESSION, detector, session_manager=sm, tmux=tmux
    )
    # A real first loop: every periodic sync is due
    for name in (
        "_last_stats_sync",
        "_last_session_id_sync",
        "_last_skills_sync",
        "_last_sandbox_sync",
        "_last_resources_sync",
        "_last_history_rotation_check",
        "_last_model_metadata_check",
    ):
        setattr(daemon, name, None)

    def between(tick: int) -> None:
        if tick == 1:
            for s in sessions:
                if _index(s) % 10 == 1:
                    (Path(s.start_directory) / ".git" / "HEAD").write_text(
                        "ref: refs/heads/feature\n"
                    )

    with (
        _scenario_doubles(sessions, tmux, lambda: detector.tick),
        FrozenClock(START).installed() as clock,
    ):
        run_ticks(daemon, detector, clock, ticks, counters=counters, between_ticks=between)

    with open(sm.state_file) as f:
        state = json.load(f)
    with open(daemon.state_path) as f:
        published = json.load(f)
    volatile = {
        "last_loop_time",
        "tick_started_at",
        "last_tick_duration_seconds",
        "pid",
        "started_at",
    }
    return {
        "sessions": state,
        "daemon": {k: v for k, v in published.items() if k not in volatile},
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--ticks", type=int, default=TICKS)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    os.environ["HOME"] = str(root / "home")
    os.environ["OVERCODE_DIR"] = str(root / "home" / ".overcode")
    os.environ["OVERCODE_STATE_DIR"] = str(root / "home" / ".overcode" / "sessions")
    result = run_scenario(root, args.ticks)
    args.out.write_text(json.dumps(result, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
