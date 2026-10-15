"""Upgrade, downgrade, re-upgrade rehearsal over the 0.5.8 fixture tree (local only).

CI has no 0.5.8, so this is not a unit test. It needs a 0.5.8 checkout::

    git worktree add /tmp/main058 <a 0.5.8 commit>
    python tests/fixtures/upgrade_058/downgrade_rehearsal.py \\
        --main058 /tmp/main058/src --workdir /tmp/rehearsal

Each phase runs in its own process on one version's code (``sys.path``
is set by the phase, never PYTHONPATH):

``up1`` (0.6)
    copies ``tree/`` to ``<workdir>/home/.overcode``, runs a first 0.6
    start (``test_upgrade_from_058.first_start``) and its checks, then uses
    0.6 for a while: launches ``new06`` (its own agent directory), writes a
    checkpoint ledger row per live agent, archives ``done-child``, and saves
    0.6's monitor state and TUI preferences.
``down`` (0.5.8, HOME=<workdir>/home)
    what a 0.5.8 TUI, daemon and CLI read at start, each probe recorded in
    ``down.json`` (ok / the exception), then 0.5.8 use: it archives
    ``worker-a`` (after giving it orders with a token in them), launches
    ``new058``, and its hooks write name-keyed files for ``new06``.
``up2`` (0.6)
    a first 0.6 start again, then: every agent of every era listed or
    archived, no secret outside the user's files, every agent closed in the
    ledger once under its root's code, every file written in the downgrade
    kept or packed, and a further start changes nothing.

Prints a report and exits non-zero if a check failed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]


def _phase_env(workdir: Path) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("OVERCODE_") and k not in ("TMUX", "TMUX_PANE", "PYTHONPATH")}
    env["HOME"] = str(workdir / "home")
    env["OVERCODE_ACTIVITY"] = "0"
    return env


# ── 0.6 phases ───────────────────────────────────────────────────────────

def _use_06(workdir: Path):
    sys.path[:0] = [str(REPO / "src"), str(REPO)]
    root = workdir / "home" / ".overcode"
    os.environ["OVERCODE_DIR"] = str(root)
    os.environ["OVERCODE_STATE_DIR"] = str(root / "sessions")
    import overcode
    assert Path(overcode.__file__).resolve().is_relative_to(REPO / "src"), overcode.__file__
    from tests.unit import test_upgrade_from_058 as T
    return root, T


def phase_up1(workdir: Path) -> dict:
    home = workdir / "home"
    if home.exists():
        shutil.rmtree(home)
    home.mkdir(parents=True)
    root, T = _use_06(workdir)
    T.copy_fixture(home)
    T.point_at(root, os.environ.__setitem__)
    before = json.loads((root / "sessions" / "sessions.json").read_text())
    out = T.first_start(root)
    T.check_upgraded(root, out, before, T.FIXTURE / "tree")
    sm = out["sm"]

    from overcode.agent_files import prepare_agent_dir
    from overcode.ledger import make_row
    from overcode.monitor_daemon_state import MonitorDaemonState, SessionDaemonState
    from overcode.paths import agent_file
    from overcode.settings import TUIPreferences, get_monitor_daemon_state_path

    s = sm.create_session("new06", "agents", "new06-6e06", ["claude"],
                          session_id="6e060000-0000-4000-8000-000000000006",
                          launcher_version="0.6.0 (rehearsal)")
    prepare_agent_dir("agents", s.id)
    agent_file("agents", s.id, "hook_state").write_text(json.dumps(
        {"event": "UserPromptSubmit", "timestamp": 1790000000.0}))
    agent_file("agents", s.id, "hook_events").write_text(json.dumps(
        {"event": "UserPromptSubmit", "timestamp": 1790000000.0}) + "\n")
    sm.update_stats(s.id, estimated_cost_usd=0.21, total_tokens=11000)
    s2 = sm.create_session("new06-done", "agents", "new06-done-6e07", ["claude"],
                           session_id="6e070000-0000-4000-8000-000000000007",
                           launcher_version="0.6.0 (rehearsal)")
    prepare_agent_dir("agents", s2.id)
    agent_file("agents", s2.id, "report").write_text(json.dumps({"status": "success"}))
    sm.update_stats(s2.id, estimated_cost_usd=0.31, total_tokens=12000)
    sm.ledger.append([make_row(x, "checkpoint") for x in sm.list_sessions()])
    by_name = {x.name: x for x in sm.list_sessions()}
    sm.delete_session(by_name["done-child"].id)
    for tmux in T.MANIFEST["tmux_sessions"]:
        live = [x for x in sm.list_sessions() if x.tmux_session == tmux]
        MonitorDaemonState(status="active", sessions=[
            SessionDaemonState(session_id=x.id, name=x.name, current_status=x.status,
                               estimated_cost_usd=x.stats.estimated_cost_usd)
            for x in live]).save(get_monitor_daemon_state_path(tmux))
        prefs = TUIPreferences.load(tmux)
        prefs.save(tmux)
    return {"live": sorted(x.name for x in sm.list_sessions()),
            "archived": len(sm.list_archived_sessions())}


def phase_up2(workdir: Path) -> dict:
    root, T = _use_06(workdir)
    T.point_at(root, os.environ.__setitem__)
    pristine = workdir / "pre_up2"
    if pristine.exists():
        shutil.rmtree(pristine)
    shutil.copytree(root, pristine)
    before = json.loads((root / "sessions" / "sessions.json").read_text())
    out = T.first_start(root)
    sm = out["sm"]
    problems = []

    def check(ok, what):
        if not ok:
            problems.append(what)

    from overcode.layout_migration import legacy_name

    live = {x.name: x for x in sm.list_sessions()}
    expect_live = {"orchestrator", "worker-b", "surveyor", "builder", "reviewer",
                   "experiment", "new06", "new058"}
    check(set(live) == expect_live, f"live agents: {sorted(live)}")
    for sid, old in before.items():
        new = sm.get_session(sid)
        check(new is not None and new.name == old["name"]
              and new.stats.estimated_cost_usd == old["stats"]["estimated_cost_usd"],
              f"live record changed: {old['name']}")
    roots = {"new058": "new058", "new06": "new06"}
    for name, x in live.items():
        want = roots.get(name) or T.MANIFEST["expected_live_billing_codes"].get(x.id)
        check(x.billing_code == want, f"{name} billing code {x.billing_code!r} != {want!r}")

    archived = {x.id: x for x in sm.list_archived_sessions()}
    by_name = {x.name: x for x in archived.values()}
    expect_ids = set(T.MANIFEST["archived_ids"])
    check(expect_ids <= set(archived), f"lost archived: {sorted(expect_ids - set(archived))}")
    check({"done-child", "worker-a", "new06-done"} <= set(by_name),
          f"archived names: {sorted(by_name)}")
    check(len(archived) == len(expect_ids) + 3, f"archived count {len(archived)}")
    check(not (root / "sessions" / "archive.jsonl").exists(), "archive.jsonl left")

    secrets = T.MANIFEST["secrets"] + ["FAKE-SI-TOKEN-0099"]
    leaks = []
    for rel, data in T._texts(root):
        if Path(rel).name in T.USER_FILES:
            continue
        for secret in secrets:
            if secret.encode() in data:
                leaks.append((secret, rel))
    check(not leaks, f"secrets kept: {leaks}")

    rows = list(sm.ledger.iter_rows())
    closing = {}
    for row in rows:
        if row["kind"] in ("final", "backfill"):
            closing.setdefault(row["sid"], []).append(row)
    for sid, x in archived.items():
        got = closing.get(sid, [])
        check(len(got) == 1, f"{x.name}: {len(got)} closing ledger rows")
        want = (T.MANIFEST["billing_code_by_archived_id"].get(sid)
                or {"new06-done": "new06-done"}.get(x.name, "orchestrator"))
        check(all(r["billing_code"] == want for r in got),
              f"{x.name} billed to {[r['billing_code'] for r in got]}, want {want}")
    totals = sm.ledger.totals()
    closed_orch = sum(r["cost"] for rs in closing.values() for r in rs
                      if r["billing_code"] == "orchestrator")
    expected_orch = (T.MANIFEST["expected_ledger_totals"]["orchestrator"]["cost"]
                     + by_name["done-child"].stats.estimated_cost_usd
                     + by_name["worker-a"].stats.estimated_cost_usd)
    check(abs(closed_orch - expected_orch) < 1e-6,
          f"orchestrator closed spend {closed_orch} != {expected_orch}")

    packed = {}
    for member in T.bundle_members(root):
        if member.get("legacy"):
            for fname, content in member["files"].items():
                packed[(member["_tmux"], fname)] = content
    for path in sorted((pristine / "sessions").glob("*/*")):
        parsed = legacy_name(path.name)
        if parsed is None or path.name.endswith(".lock"):
            continue
        tmux = path.parent.name
        here = root / "sessions" / tmux / path.name
        if here.exists():
            check(here.read_bytes() == path.read_bytes(), f"{tmux}/{path.name} changed")
            continue
        content = packed.get((tmux, path.name))
        text = path.read_text()
        ok = content == text if isinstance(content, str) else content == json.loads(text)
        check(ok, f"{tmux}/{path.name} removed but not packed whole")

    # 0.5.8 archived new06-done and left its agent directory: once it is
    # old enough the orphan sweep packs it and points its record at it
    from overcode.paths import agent_dir
    orphan = agent_dir("agents", by_name["new06-done"].id)
    check(orphan.is_dir(), "new06-done's directory went before the sweep")
    old = __import__("time").time() - 3600
    for p in [orphan, *orphan.iterdir()]:
        os.utime(p, (old, old))
    swept = sm.tidy_agent_storage("agents")
    check(not orphan.exists(), f"orphan sweep left new06-done's directory: {swept}")
    found = sm.find_archived("new06-done")
    check(found is not None and getattr(found, "_bundle", None), "new06-done has no bundle pointer")
    from overcode.agent_bundle import read_agent
    packed_dir = read_agent("agents", found._bundle) if found is not None and found._bundle else None
    check(packed_dir is not None and packed_dir["files"].get("report.json") == {"status": "success"},
          f"new06-done's files not in its bundle: {packed_dir}")

    digest = T.tree_digest(root)
    again = T.first_start(root)
    check(T.tree_digest(root) == digest, "a further start changed the tree")
    check(all(not (p["packed"] or p["backfilled"] or p["coded"])
              for passes in again["migrate"].values() for p in passes),
          "a further start did migration work")
    return {"problems": problems, "live": sorted(live), "archived": len(archived),
            "orchestrator_total": totals.get("orchestrator"),
            "migrate": {k: v[0] for k, v in out["migrate"].items()}}


# ── 0.5.8 phase ──────────────────────────────────────────────────────────

def phase_down(workdir: Path, main058: Path) -> dict:
    sys.path.insert(0, str(main058))
    import overcode
    assert overcode.__version__.startswith("0.5."), overcode.__version__
    from overcode import hook_handler, settings
    from overcode.session_manager import SessionManager

    assert str(settings.get_state_dir()).startswith(str(workdir)), settings.get_state_dir()
    sessions_dir = settings.get_state_dir()
    probes: dict = {}

    def probe(name, fn):
        try:
            probes[name] = {"ok": True, "value": fn()}
        except Exception as e:  # recorded, not raised: what breaks is the finding
            probes[name] = {"ok": False, "error": repr(e),
                            "where": traceback.format_exc().strip().splitlines()[-3:]}

    sm = SessionManager(skip_git_detection=True)
    probe("sessions.json raw entries / parsed sessions",
          lambda: [len(sm._load_state()), len(sm.list_sessions())])
    probe("list_sessions", lambda: sorted((s.name, s.status) for s in sm.list_sessions()))
    probe("unknown 0.6 keys ignored (billing_code)",
          lambda: sorted({k for r in sm._load_state().values() for k in r}
                         - set(sm.list_sessions()[0].to_dict())))
    probe("list_archived_sessions", lambda: len(sm.list_archived_sessions()))
    probe("archived_sessions_since(0) (the journey's incremental read)",
          lambda: len(sm.archived_sessions_since(0)[0]))

    def cli(args):
        from typer.testing import CliRunner
        from overcode.cli import app
        r = CliRunner().invoke(app, args)
        return {"exit": r.exit_code, "output": r.output.strip().splitlines()[:4]}

    probe("cli: overcode history", lambda: cli(["history"]))
    probe("cli: overcode history pathfinder", lambda: cli(["history", "pathfinder"]))

    from overcode.status_history import read_agent_status_history
    from overcode.monitor_daemon_state import MonitorDaemonState
    from overcode.settings import TUIPreferences, get_supervisor_stats_path
    from overcode.supervisor_daemon import SupervisorStats
    from overcode.hook_status_detector import HookStatusDetector
    from overcode.mocks import MockTmux

    for tmux in ("agents", "work", "lab"):
        d = sessions_dir / tmux
        probe(f"{tmux}: status history rows", lambda d=d: len(read_agent_status_history(
            hours=24 * 3650, history_file=d / "agent_status_history.csv")))
        probe(f"{tmux}: monitor_daemon_state.json (written by 0.6)", lambda d=d: (
            lambda st: None if st is None else [st.status, len(st.sessions)])(
                MonitorDaemonState.load(d / "monitor_daemon_state.json")))
        probe(f"{tmux}: tui_preferences.json (written by 0.6)",
              lambda tmux=tmux: TUIPreferences.load(tmux).sort_mode)
        probe(f"{tmux}: supervisor_stats.json",
              lambda tmux=tmux: SupervisorStats.load(
                  get_supervisor_stats_path(tmux)).supervisor_launches)
        probe(f"{tmux}: entries in session dir",
              lambda d=d: sorted(p.name for p in d.iterdir() if p.is_dir()))

    detector = HookStatusDetector("agents", tmux=MockTmux())
    for name in ("orchestrator", "worker-b", "surveyor", "new06"):
        probe(f"hook state of {name}",
              lambda name=name: (detector._read_hook_state(name) or {}).get("event"))
    from overcode.config import get_sisters_config, load_config
    probe("config sisters", lambda: [s["name"] for s in get_sisters_config()])
    probe("config keys", lambda: sorted(load_config()))
    from overcode.standing_instructions import load_presets
    probe("presets", lambda: sorted(load_presets()))
    from overcode.presence_logger import read_presence_history
    probe("presence history", lambda: len(read_presence_history(hours=24 * 3650)))

    # 0.5.8 in use
    by_name = {s.name: s for s in sm.list_sessions()}
    writes = {}
    try:
        worker = by_name["worker-a"]
        sm.set_standing_instructions(worker.id, "Finish up; staging key FAKE-SI-TOKEN-0099")
        sm.update_stats(worker.id, estimated_cost_usd=1.05, total_tokens=81000)
        sm.update_stats(by_name["orchestrator"].id, estimated_cost_usd=4.60)
        sm.delete_session(worker.id, archive=True)
        sm.delete_session(by_name["new06-done"].id, archive=True)  # its agents/<id>/ stays
        new = sm.create_session("new058", "agents", "new058-5e58", ["claude"],
                                session_id="5e580000-0000-4000-8000-000000000058",
                                launcher_version="0.5.8 (rehearsal)")
        sm.update_stats(new.id, estimated_cost_usd=0.07, total_tokens=3000)
        for name in ("new058", "new06", "orchestrator"):
            hook_handler.write_hook_state("UserPromptSubmit", "agents", name)
            hook_handler.append_hook_event("UserPromptSubmit", "agents", name)
        writes = {"ok": True, "archive.jsonl lines":
                  len((sessions_dir / "archive.jsonl").read_text().splitlines()),
                  "live records still carrying billing_code": sorted(
                      r["name"] for r in sm._load_state().values() if r.get("billing_code"))}
    except Exception as e:
        writes = {"ok": False, "error": repr(e), "where": traceback.format_exc()[-800:]}
    return {"probes": probes, "writes": writes}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--main058", type=Path, help="a 0.5.8 checkout's src directory")
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--phase", choices=("up1", "down", "up2"))
    args = parser.parse_args()
    workdir = args.workdir.resolve()

    if args.phase:
        if args.phase == "down":
            result = phase_down(workdir, args.main058.resolve())
        else:
            result = {"up1": phase_up1, "up2": phase_up2}[args.phase](workdir)
        (workdir / f"{args.phase}.json").write_text(json.dumps(result, indent=1, default=str))
        return

    if not args.main058:
        sys.exit("--main058 is required")
    workdir.mkdir(parents=True, exist_ok=True)
    env = _phase_env(workdir)
    failed = False
    for phase in ("up1", "down", "up2"):
        cmd = [sys.executable, __file__, "--phase", phase, "--workdir", str(workdir),
               "--main058", str(args.main058.resolve())]
        proc = subprocess.run(cmd, env=env, cwd=workdir, capture_output=True, text=True)
        print(f"== {phase}: exit {proc.returncode}")
        if proc.returncode:
            print(proc.stdout[-3000:], proc.stderr[-3000:])
            failed = True
            break
        result = json.loads((workdir / f"{phase}.json").read_text())
        print(json.dumps(result, indent=1, default=str))
        if phase == "up2" and result["problems"]:
            failed = True
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
