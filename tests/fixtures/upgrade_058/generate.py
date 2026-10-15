"""Build the synthetic 0.5.8 state tree for the upgrade test.

Run it on **0.5.8 code**, never on this checkout's ``src``::

    git worktree add /tmp/main058 <a 0.5.8 commit, e.g. origin/main before 0.6.0>
    PYTHONPATH=/tmp/main058/src python tests/fixtures/upgrade_058/generate.py \\
        --workdir /tmp/upgrade-gen --write-fixture

It writes a default-layout ``~/.overcode`` under ``<workdir>/home`` (HOME
and OVERCODE_DIR point there; OVERCODE_STATE_DIR is unset, because 0.5.8's
SessionManager took that variable as the *parent* of ``sessions/``, which
is not the layout a real install has). With ``--write-fixture`` the tree
replaces ``tests/fixtures/upgrade_058/tree/`` and ``manifest.json`` (what
the tree is expected to hold) is written beside it.

Everything is synthetic: names, ids, paths (``/home/dev/...``) and keys
(``FAKE-...``). It goes through 0.5.8's own APIs where there is one
(SessionManager create/update/rename/delete-with-archive, the hook
handler's writers, ``log_agent_status``, MonitorDaemonState,
SupervisorStats, TUIPreferences, presets) with a fake clock so the archive
spans several months. It never runs overcode, a daemon or tmux, and
refuses to run when HOME would be the real one.

Run from the repository root. ``downgrade_rehearsal.py`` beside this file
takes the same tree through 0.6, back to 0.5.8 and to 0.6 again (local
only: it needs the 0.5.8 checkout too).

What the tree has, and why (tests/unit/test_upgrade_from_058.py checks
each after a 0.6 first start):

- three tmux sessions (``agents``, ``work``, ``lab``) with live agents:
  a root with children (one running, one ``waiting_oversight`` whose
  window is gone, one terminated with a report), a renamed agent, an
  asleep agent, a bedrock agent with a wrapper, an opencode agent;
- an archive spanning 2026-02 .. 2026-09: a 0.4-era ``archive.json``
  (migrated by 0.5.8 itself into ``archive.jsonl``, leaving
  ``archive.json.migrated``), a parent/child/grandchild tree, a name
  reused by two agents, a renamed agent, children of a live parent, a
  remote agent carrying a sister API key, launch commands with keys,
  standing instructions with a token in them, and a duplicate archive
  line (a crash between the archive append and the live write);
- name-keyed agent files (hook state and events, rotated ``.1`` logs,
  lock files, reports, heartbeats, rename notices, an episodes log) for
  live, archived, renamed and never-archived (failed launch) names;
- ``config.yaml`` with ``sisters`` as a column-0 list with an api_key,
  ``presets.json``, the presence log, status history CSVs, monitor and
  supervisor state, TUI preferences.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The fake clock every 0.5.8 module that stamps a time reads
_NOW = [datetime(2026, 2, 1, 9, 0, 0)]


class FakeDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: D102
        value = _NOW[0]
        return value.replace(tzinfo=tz) if tz else value


def at(text: str) -> None:
    _NOW[0] = datetime.fromisoformat(text)


def setup_env(workdir: Path) -> Path:
    home = workdir / "home"
    real_home = Path(os.path.expanduser("~")).resolve()
    if home.resolve() == real_home or str(home.resolve()).startswith(str(real_home / ".overcode")):
        sys.exit("refusing to write into the real home directory")
    if home.exists():
        shutil.rmtree(home)
    home.mkdir(parents=True)
    os.environ["HOME"] = str(home)
    os.environ["OVERCODE_DIR"] = str(home / ".overcode")
    for var in ("OVERCODE_STATE_DIR", "TMUX", "TMUX_PANE", "OVERCODE_SESSION_NAME",
                "OVERCODE_SESSION_ID", "OVERCODE_TMUX_SESSION", "OVERCODE_PARENT_NAME",
                "OVERCODE_PARENT_SESSION_ID"):
        os.environ.pop(var, None)
    os.environ["OVERCODE_ACTIVITY"] = "0"
    return home / ".overcode"


CONFIG_YAML = """\
# overcode config (synthetic fixture)
hostname: fixture-host
default_standing_instructions: "Keep going until the task is done."

web:
  port: 15337
  api_key: FAKE-WEB-KEY-0000

# Sister machines, written as a column-0 list (PyYAML's own dump style)
sisters:
- name: desk-mini
  url: http://desk-mini.local:15337
  api_key: FAKE-SISTER-KEY-0002
  ssh: dev@desk-mini.local
- name: build-box
  url: http://build-box.local:15337/
  tmux_session: agents

summarizer:
  api_key_var: FAKE_SUMMARIZER_KEY_VAR

session_archive:
  terminated_grace_seconds: 600
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--workdir", required=True, type=Path)
    parser.add_argument("--write-fixture", action="store_true",
                        help="replace tests/fixtures/upgrade_058/tree and manifest.json")
    args = parser.parse_args()
    root = setup_env(args.workdir.resolve())

    import overcode
    if not overcode.__version__.startswith("0.5."):
        sys.exit(f"run on 0.5.x code (PYTHONPATH), not overcode {overcode.__version__}")

    import overcode.session_manager as sm_mod
    import overcode.status_history as sh_mod
    from overcode import hook_handler, settings
    from overcode.launcher import AgentLauncher
    from overcode.monitor_daemon_state import MonitorDaemonState, SessionDaemonState
    from overcode.session_manager import SessionManager
    from overcode.settings import TUIPreferences, get_session_dir
    from overcode.standing_instructions import add_preset
    from overcode.supervisor_daemon import SupervisorStats

    sm_mod.datetime = FakeDatetime
    sh_mod.datetime = FakeDatetime
    assert str(settings.get_state_dir()).startswith(str(root)), settings.get_state_dir()

    sessions_dir = root / "sessions"
    sessions_dir.mkdir(parents=True)
    (root / "config.yaml").write_text(CONFIG_YAML)
    add_preset("ship-it", "Drive to a merged PR",
               "Push until the PR is merged. Never paste FAKE-SI-TOKEN-0009.")

    # ── a 0.4-era archive.json (0.5.8 migrates it on its first archive) ──
    legacy = {
        "0a000000-0000-4000-8000-000000000001": {
            "id": "0a000000-0000-4000-8000-000000000001", "name": "legacy-alpha",
            "tmux_session": "agents", "tmux_window": 3,
            "command": ["claude", "--api-key", "FAKE-CMD-KEY-0010"],
            "start_directory": "/home/dev/src/legacy", "start_time": "2026-02-02T10:00:00",
            "end_time": "2026-02-03T18:30:00", "status": "archived",
            "standing_instructions": "Old orders with FAKE-SI-TOKEN-0011 inside",
            "claude_session_ids": ["c1a0c1a0-0000-4000-8000-00000000aaaa"],
            "time_context_enabled": True,
            "stats": {"estimated_cost_usd": 0.5, "total_tokens": 40000,
                      "input_tokens": 30000, "output_tokens": 10000,
                      "interaction_count": 4, "model": "claude-sonnet-4-5"},
        },
        "0a000000-0000-4000-8000-000000000002": {
            "id": "0a000000-0000-4000-8000-000000000002", "name": "legacy-beta",
            "tmux_session": "agents", "tmux_window": 4,
            "command": ["claude"], "start_directory": "/home/dev/src/legacy",
            "start_time": "2026-03-01T08:00:00", "end_time": "2026-03-01T09:15:00",
            "status": "archived",
            "parent_session_id": "0a000000-0000-4000-8000-000000000001",
            "stats": {"estimated_cost_usd": 0.25, "total_tokens": 9000,
                      "interaction_count": 2},
        },
    }
    (sessions_dir / "archive.json").write_text(json.dumps(legacy, indent=2))

    sm = SessionManager(skip_git_detection=True)
    assert sm.state_dir == sessions_dir, sm.state_dir

    ids = {}

    def uid(n: int) -> str:
        return f"5e55{n:04d}-0000-4000-8000-{n:012d}"

    def create(name, tmux, n, *, when, parent=None, **kw):
        at(when)
        command = kw.pop("command", ["claude", "--session-id", uid(n)])
        s = sm.create_session(name, tmux, f"{name}-{uid(n)[:4]}", command,
                              start_directory=kw.pop("start_directory", f"/home/dev/src/{tmux}"),
                              session_id=uid(n), launcher_version="0.5.8 (fixture)", **kw)
        if parent:
            sm.update_session(s.id, parent_session_id=ids[parent])
        ids[name] = s.id
        return s.id

    def spend(sid, cost, tokens, *, model=None, interactions=5):
        tin, tout = int(tokens * 0.6), int(tokens * 0.1)
        cw, cr = int(tokens * 0.1), tokens - tin - tout - int(tokens * 0.1)
        sm.update_stats(sid, estimated_cost_usd=cost, total_tokens=tokens,
                        input_tokens=tin, output_tokens=tout, cache_creation_tokens=cw,
                        cache_read_tokens=cr, interaction_count=interactions,
                        last_activity=_NOW[0].isoformat(), current_task="working")
        if model:
            sm.update_session(sid, model=model)

    def archive(sid, when):
        at(when)
        sm.delete_session(sid, archive=True)

    def hook_files(tmux, name, *, events=("SessionStart", "UserPromptSubmit", "Stop"),
                   rotated=False, lock=False):
        for event in events:
            hook_handler.write_hook_state(event, tmux, name)
            hook_handler.append_hook_event(event, tmux, name,
                                           tool_name="Bash" if event == "PreToolUse" else None,
                                           tool_input={"command": "pytest -q"}
                                           if event == "PreToolUse" else None)
        d = get_session_dir(tmux)
        if rotated:
            (d / f"hook_events_{name}.jsonl.1").write_text(
                json.dumps({"event": "Stop", "timestamp": 1770000000.0}) + "\n")
        if lock:
            (d / f"hook_state_{name}.json.lock").write_text("")

    def report(tmux, name, status, reason):
        (get_session_dir(tmux) / f"report_{name}.json").write_text(json.dumps(
            {"status": status, "reason": reason, "timestamp": _NOW[0].isoformat()}, indent=2))

    def history(tmux, rows):
        path = get_session_dir(tmux) / "agent_status_history.csv"
        for when, name, status, activity in rows:
            at(when)
            sh_mod.log_agent_status(name, status, activity, history_file=path,
                                    session_id=ids.get(name, ""), hostname="fixture-host")

    # ── archived, oldest first (0.5.8 appended in end-time order) ─────────
    create("nightly-audit", "work", 1, when="2026-04-06T22:00:00", model="opus",
           standing_instructions="Audit every service; the deploy token is FAKE-SI-TOKEN-0001",
           command=["claude", "--model", "opus", "--api-key", "FAKE-CMD-KEY-0012"])
    spend(ids["nightly-audit"], 3.10, 210000, model="opus")
    hook_files("work", "nightly-audit")
    archive(ids["nightly-audit"], "2026-04-07T06:00:00")

    create("migrate-db", "agents", 10, when="2026-04-20T10:00:00", model="sonnet")
    sm.set_standing_instructions(ids["migrate-db"],
                                 "Push until the PR is merged. Never paste FAKE-SI-TOKEN-0009.",
                                 preset_name="ship-it")
    create("migrate-db-schema", "agents", 11, when="2026-04-20T10:05:00", parent="migrate-db",
           model="sonnet")
    create("schema-check", "agents", 12, when="2026-04-20T10:10:00", parent="migrate-db-schema",
           model="haiku")
    spend(ids["migrate-db"], 5.00, 400000)
    spend(ids["migrate-db-schema"], 1.50, 120000)
    spend(ids["schema-check"], 0.25, 30000)
    for name in ("migrate-db", "migrate-db-schema", "schema-check"):
        hook_files("agents", name, rotated=name == "migrate-db")
    report("agents", "migrate-db-schema", "success", "schema migrated")
    report("agents", "schema-check", "failure", "two checks flaky")
    sm.update_session(ids["migrate-db-schema"], report_status="success",
                      report_reason="schema migrated")
    sm.update_session(ids["schema-check"], report_status="failure",
                      report_reason="two checks flaky")
    archive(ids["schema-check"], "2026-05-01T23:00:00")
    archive(ids["migrate-db-schema"], "2026-05-02T09:00:00")
    archive(ids["migrate-db"], "2026-05-02T10:00:00")

    # A name reused by a later agent (tmux "lab")
    create("fix-tests", "lab", 50, when="2026-05-10T09:00:00")
    spend(ids["fix-tests"], 0.80, 60000)
    first_fix = ids["fix-tests"]
    archive(first_fix, "2026-05-11T09:00:00")
    create("fix-tests", "lab", 51, when="2026-06-02T09:00:00")
    spend(ids["fix-tests"], 1.20, 90000)
    hook_files("lab", "fix-tests")
    archive(ids["fix-tests"], "2026-06-03T09:00:00")

    create("scout-old", "agents", 20, when="2026-06-15T09:00:00")
    hook_files("agents", "scout-old")
    at("2026-06-16T09:00:00")
    AgentLauncher._move_name_keyed_files(AgentLauncher, "agents", "scout-old", "pathfinder")
    sm.rename_session(ids["scout-old"], "pathfinder", tmux_window="pathfinder-5e55")
    ids["pathfinder"] = ids["scout-old"]
    spend(ids["pathfinder"], 0.66, 50000)
    archive(ids["pathfinder"], "2026-07-01T12:00:00")

    create("sister-mirror", "agents", 21, when="2026-07-08T09:00:00",
           command=["claude", "--api-key", "FAKE-CMD-KEY-0003"])
    sm.update_session(ids["sister-mirror"], is_remote=True, source_host="desk-mini",
                      source_url="http://desk-mini.local:15337",
                      source_api_key="FAKE-SISTER-KEY-0002", source_ssh="dev@desk-mini.local")
    spend(ids["sister-mirror"], 0.40, 25000)
    archive(ids["sister-mirror"], "2026-07-09T09:00:00")

    # A launch that failed: dropped with no archive record, its files stay
    create("ghost", "agents", 22, when="2026-07-20T09:00:00")
    hook_files("agents", "ghost", events=("SessionStart",))
    at("2026-07-20T09:01:00")
    sm.delete_session(ids.pop("ghost"), archive=False)

    create("bedrock-run", "work", 2, when="2026-08-12T09:00:00", provider="bedrock",
           model="us.anthropic.claude-sonnet-4-5", wrapper="/opt/wrappers/devcontainer.sh",
           extra_cli_args=["--max-turns", "40"])
    spend(ids["bedrock-run"], 2.00, 150000)
    # A crash between the archive append and the live write leaves the
    # record archived and still live; it is archived again, newer
    snapshot = sm._load_state()[ids["bedrock-run"]]
    at("2026-08-19T23:59:00")
    sm._archive_session(dict(snapshot, end_time=_NOW[0].isoformat(), status="archived"))
    spend(ids["bedrock-run"], 2.25, 165000)
    hook_files("work", "bedrock-run", rotated=True)
    archive(ids["bedrock-run"], "2026-08-20T08:00:00")

    # ── live: tmux "agents" ───────────────────────────────────────────────
    create("orchestrator", "agents", 30, when="2026-09-20T08:00:00", model="opus",
           standing_instructions="Supervise the workers; release key FAKE-SI-TOKEN-0004",
           command=["claude", "--model", "opus", "--api-key", "FAKE-CMD-KEY-0005"])
    sm.update_session(ids["orchestrator"], heartbeat_enabled=True,
                      heartbeat_frequency_seconds=600,
                      heartbeat_instruction="Check on the workers", tags=["release"],
                      agent_value=2000, human_annotation="the release lead",
                      cost_budget_usd=50.0)
    spend(ids["orchestrator"], 4.20, 330000, model="opus", interactions=40)
    hook_files("agents", "orchestrator", lock=True)
    (get_session_dir("agents") / "heartbeat_orchestrator.last").write_text(
        "2026-09-30T11:50:00")

    create("orch-helper-1", "agents", 31, when="2026-09-21T09:00:00", parent="orchestrator")
    create("orch-helper-1-sub", "agents", 32, when="2026-09-21T09:30:00", parent="orch-helper-1")
    spend(ids["orch-helper-1"], 0.70, 60000)
    spend(ids["orch-helper-1-sub"], 0.10, 8000)
    hook_files("agents", "orch-helper-1")
    hook_files("agents", "orch-helper-1-sub")
    (get_session_dir("agents") / "episodes_orch-helper-1.jsonl").write_text(
        json.dumps({"t": "2026-09-21T10:00:00", "kind": "turn", "status": "running"}) + "\n")
    report("agents", "orch-helper-1-sub", "success", "done")
    archive(ids["orch-helper-1-sub"], "2026-09-25T10:00:00")
    archive(ids["orch-helper-1"], "2026-09-25T10:05:00")

    create("worker-a", "agents", 33, when="2026-09-28T09:00:00", parent="orchestrator",
           model="sonnet")
    spend(ids["worker-a"], 0.90, 70000)
    hook_files("agents", "worker-a", events=("SessionStart", "UserPromptSubmit", "PreToolUse"))

    create("worker-b", "agents", 34, when="2026-09-28T09:10:00", parent="orchestrator",
           model="sonnet")
    spend(ids["worker-b"], 0.35, 20000)
    hook_files("agents", "worker-b")  # last event: Stop, window gone
    at("2026-09-30T10:00:00")
    sm.update_session(ids["worker-b"], status="waiting_oversight",
                      oversight_policy="timeout", oversight_timeout_seconds=3600,
                      oversight_deadline="2026-09-30T11:00:00")

    create("done-child", "agents", 35, when="2026-09-29T09:00:00", parent="orchestrator")
    spend(ids["done-child"], 0.15, 9000)
    hook_files("agents", "done-child")
    report("agents", "done-child", "success", "tests green")
    sm.update_session(ids["done-child"], report_status="success", report_reason="tests green")
    sm.update_session_status(ids["done-child"], "terminated")

    create("scout", "agents", 36, when="2026-09-29T12:00:00")
    hook_files("agents", "scout")
    at("2026-09-30T08:00:00")
    AgentLauncher._move_name_keyed_files(AgentLauncher, "agents", "scout", "surveyor")
    sm.rename_session(ids["scout"], "surveyor", tmux_window="surveyor-5e55")
    AgentLauncher._write_rename_notice(
        type("L", (), {"tmux_session": "agents"})(), "scout", "surveyor")
    ids["surveyor"] = ids.pop("scout")
    # The old process's last hook, after the files moved (launcher.rename)
    hook_handler.write_hook_state("SessionEnd", "agents", "scout")
    spend(ids["surveyor"], 0.33, 21000)

    # ── tmux "work" live ──────────────────────────────────────────────────
    create("builder", "work", 40, when="2026-09-29T07:00:00", provider="bedrock",
           model="us.anthropic.claude-opus-4-1", wrapper="/opt/wrappers/devcontainer.sh",
           permissiveness_mode="bypass")
    spend(ids["builder"], 1.75, 90000)
    hook_files("work", "builder")
    create("reviewer", "work", 41, when="2026-09-29T07:30:00", model="haiku")
    sm.update_session(ids["reviewer"], is_asleep=True)
    spend(ids["reviewer"], 0.05, 4000)
    hook_files("work", "reviewer")

    # ── tmux "lab": one opencode agent live ─────
    create("experiment", "lab", 52, when="2026-09-30T09:00:00", backend="opencode",
           model="openai/gpt-4o-mini")
    spend(ids["experiment"], 0.12, 15000)
    hook_files("lab", "experiment")

    # ── per-session daemon files ──────────────────────────────────────────
    history("agents", [
        ("2026-09-30T09:00:00", "orchestrator", "running", "planning"),
        ("2026-09-30T09:05:00", "worker-a", "running", "pytest -q"),
        ("2026-09-30T09:20:00", "worker-b", "waiting_oversight", ""),
        ("2026-09-30T09:30:00", "surveyor", "waiting_user", "asked a question"),
    ])
    history("work", [("2026-09-30T09:00:00", "builder", "running", "make build")])
    history("lab", [("2026-09-30T09:00:00", "experiment", "running", "")])
    at("2026-09-30T12:00:00")
    for tmux in ("agents", "work", "lab"):
        live = [s for s in sm.list_sessions() if s.tmux_session == tmux]
        MonitorDaemonState(
            pid=0, status="stopped", loop_count=1234, started_at="2026-09-30T08:00:00",
            last_loop_time="2026-09-30T12:00:00", daemon_version=1,
            sessions=[SessionDaemonState(
                session_id=s.id, name=s.name, tmux_window=s.tmux_window,
                current_status=s.status, estimated_cost_usd=s.stats.estimated_cost_usd,
                model=s.model, start_time=s.start_time,
                parent_name=next((p.name for p in sm.list_sessions()
                                  if p.id == s.parent_session_id), None),
            ) for s in live],
        ).save(get_session_dir(tmux) / "monitor_daemon_state.json")
        prefs = TUIPreferences(sort_mode="by_tree", show_cost="cost",
                               collapsed_parents={ids["orchestrator"]} if tmux == "agents" else set())
        prefs.save(tmux)
        SupervisorStats(supervisor_launches=3, supervisor_tokens=12000).save(
            settings.get_supervisor_stats_path(tmux))
    (root / "presence_log.csv").write_text(
        "timestamp,state,idle_seconds,locked,inferred_sleep\n"
        "2026-09-30T09:00:00,3,1.0,False,False\n"
        "2026-09-30T09:01:00,2,61.0,False,False\n")

    manifest = build_manifest(root, sm, ids, first_fix)
    if args.write_fixture:
        write_fixture(root, manifest, args.workdir.resolve())
    print(json.dumps({k: (len(v) if isinstance(v, (list, dict)) else v)
                      for k, v in manifest.items()}, indent=1))


def _archive_lines(root: Path) -> list:
    out = []
    for line in (root / "sessions" / "archive.jsonl").read_text().splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def build_manifest(root: Path, sm, ids: dict, first_fix: str) -> dict:
    """What the tree holds, from the files themselves (the test's expectations)."""
    records = _archive_lines(root)
    newest = {}
    for record in records:
        newest[record["id"]] = record  # file order: the last line wins
    live = {s.id: s.to_dict() for s in sm.list_sessions()}
    lookup = {**{sid: r for sid, r in newest.items()}, **live}

    def root_name(record):
        seen, current = set(), record
        while current.get("parent_session_id") in lookup and current["parent_session_id"] not in seen:
            seen.add(current["parent_session_id"])
            current = lookup[current["parent_session_id"]]
        return current["name"]

    codes = {sid: root_name(r) for sid, r in newest.items()}
    totals: dict = {}
    for sid, record in newest.items():
        entry = totals.setdefault(codes[sid], {"cost": 0.0, "tokens": 0, "agents": 0})
        entry["cost"] = round(entry["cost"] + float(record["stats"].get("estimated_cost_usd", 0)), 6)
        entry["tokens"] += int(record["stats"].get("total_tokens", 0))
        entry["agents"] += 1

    legacy_files = {}
    for tmux_dir in sorted(p for p in (root / "sessions").iterdir() if p.is_dir()):
        for f in sorted(tmux_dir.iterdir()):
            if f.name.startswith(("hook_state_", "hook_events_", "report_", "episodes_",
                                  "rename_notice_", "heartbeat_")):
                legacy_files.setdefault(tmux_dir.name, []).append(f.name)

    return {
        "generated_by": "tests/fixtures/upgrade_058/generate.py on overcode 0.5.8",
        "tmux_sessions": ["agents", "lab", "work"],
        "live_ids": sorted(live),
        "live_names": {sid: r["name"] for sid, r in live.items()},
        "archive_lines": len(records),
        "archived_ids": sorted(newest),
        "archived_names": {sid: r["name"] for sid, r in newest.items()},
        "archive_months": sorted({r["end_time"][:7] for r in newest.values()}),
        "renamed": {"archived": {"id": ids["pathfinder"], "name": "pathfinder",
                                 "previous": "scout-old"},
                    "live": {"id": ids["surveyor"], "name": "surveyor", "previous": "scout"}},
        "reused_name": {"name": "fix-tests", "ids": sorted([first_fix, ids["fix-tests"]])},
        "duplicate_archive_id": ids["bedrock-run"],
        "never_archived_name": "ghost",
        "billing_code_by_archived_id": codes,
        "expected_ledger_totals": totals,
        "expected_live_billing_codes": {sid: root_name(r) for sid, r in live.items()},
        "secrets": ["FAKE-SISTER-KEY-0002", "FAKE-CMD-KEY-0003", "FAKE-CMD-KEY-0010",
                    "FAKE-CMD-KEY-0012", "FAKE-SI-TOKEN-0001", "FAKE-SI-TOKEN-0009",
                    "FAKE-SI-TOKEN-0011"],
        "live_secrets": ["FAKE-SI-TOKEN-0004", "FAKE-CMD-KEY-0005"],
        "legacy_files": legacy_files,
        "sisters": ["desk-mini", "build-box"],
    }


def write_fixture(root: Path, manifest: dict, workdir: Path) -> None:
    tree = HERE / "tree"
    if tree.exists():
        shutil.rmtree(tree)
    shutil.copytree(root, tree, ignore=shutil.ignore_patterns("*.pid", "*.tmp"))
    leaked = [str(p.relative_to(tree)) for p in tree.rglob("*")
              if p.is_file() and str(workdir).encode() in p.read_bytes()]
    if leaked:
        sys.exit(f"absolute paths of this machine leaked into: {leaked}")
    (HERE / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
