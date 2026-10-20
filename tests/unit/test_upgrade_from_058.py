"""A 0.5.8 state tree upgraded by a first 0.6 start: nothing lost, no secret kept.

The tree (``tests/fixtures/upgrade_058/tree``, the contents of a default
``~/.overcode``) was written by 0.5.8's own code (``generate.py`` beside
it, which also wrote ``manifest.json``: what the tree holds). Each test
copies it to a temp dir, runs what the first 0.6 start runs (the session
manager, the archive migration, ``migrate_layout`` for every tmux session
until it has nothing left to do, the orphan sweep, the doctor's storage
report) and checks one promise of the upgrade.

``first_start`` and ``check_upgraded`` are also used by the local
downgrade rehearsal (0.5.8 run over the upgraded tree, then 0.6 again),
which CI cannot run because it has no 0.5.8.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "upgrade_058"
MANIFEST = json.loads((FIXTURE / "manifest.json").read_text())
# The user's own files, which hold their secrets on purpose
USER_FILES = {"config.yaml", "presets.json"}


# ── the upgrade ──────────────────────────────────────────────────────────

def point_at(root: Path, setenv) -> None:
    """Point overcode at ``root`` (a ``.overcode`` directory), default layout."""
    setenv("OVERCODE_DIR", str(root))
    setenv("OVERCODE_STATE_DIR", str(root / "sessions"))
    from overcode.config import _clear_config_cache
    _clear_config_cache()


def first_start(root: Path) -> dict:
    """What a first 0.6 start does to the tree at ``root``; returns what each step said."""
    from overcode.doctor import storage_report
    from overcode.layout_migration import migrate_layout
    from overcode.session_manager import SessionManager

    sm = SessionManager(skip_git_detection=True)
    sm.rollover_in_background = False
    assert sm.state_dir == root / "sessions"
    sm.list_sessions()
    sm._migrate_legacy_archive()
    out: dict = {"sm": sm, "migrate": {}, "tidy": {}, "storage": {}}
    for tmux in MANIFEST["tmux_sessions"]:
        passes = []
        for _ in range(20):  # the daemon's passes; each a bounded batch
            counts = migrate_layout(tmux, sm)
            passes.append(counts)
            if counts["done"] or not (counts["packed"] or counts["left"]):
                break
        out["migrate"][tmux] = passes
        out["tidy"][tmux] = sm.tidy_agent_storage(tmux)
        out["storage"][tmux] = storage_report(tmux, sm)
    out["totals"] = sm.ledger.named(sm.ledger.totals())
    return out


def copy_fixture(dest: Path) -> Path:
    root = dest / ".overcode"
    shutil.copytree(FIXTURE / "tree", root)
    return root


def _texts(root: Path):
    """(relative path, bytes) of every file, gzip members decompressed."""
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        data = path.read_bytes()
        if path.name.endswith(".gz"):
            data = gzip.decompress(data)
        yield str(path.relative_to(root)), data


def bundle_members(root: Path) -> list:
    from overcode.agent_bundle import _members

    members = []
    for path in sorted(root.glob("sessions/*/bundles/*.agents.gz")):
        for _, member in _members(path.read_bytes()):
            for line in member.splitlines():
                if line.strip():
                    members.append(dict(json.loads(line), _tmux=path.parent.parent.name))
    return members


def tree_digest(root: Path) -> dict:
    return {rel: hashlib.sha256(data).hexdigest() for rel, data in _texts(root)}


# ── the promises (plain asserts, shared with the downgrade rehearsal) ────

def check_live_sessions(sm, before: dict) -> None:
    """Every live record is still listed, with the same values, and has a billing code."""
    from overcode.session_manager import Session

    sessions = {s.id: s for s in sm.list_sessions()}
    assert sorted(sessions) == MANIFEST["live_ids"]
    fields = Session.__dataclass_fields__
    for sid, old in before.items():
        new = sessions[sid].to_dict()
        for key, value in old.items():
            if key not in fields or key == "stats":
                continue
            assert new[key] == value or (key == "tmux_window" and str(value) == new[key]), \
                (old["name"], key, value, new[key])
        for key, value in old["stats"].items():
            if key in new["stats"]:
                assert new["stats"][key] == value, (old["name"], key)
        assert new["billing_code"] == MANIFEST["expected_live_billing_codes"][sid], old["name"]


def check_archive(sm) -> None:
    """Every archived agent is reachable, once, with its newest record."""
    archived = sm.list_archived_sessions()
    assert sorted(s.id for s in archived) == MANIFEST["archived_ids"]
    assert len(archived) == len(MANIFEST["archived_ids"])
    for sid, name in MANIFEST["archived_names"].items():
        found = sm.find_archived(sid)
        assert found is not None and found.name == name, sid
    renamed = MANIFEST["renamed"]["archived"]
    assert sm.find_archived(renamed["previous"]).id == renamed["id"]
    reused = MANIFEST["reused_name"]
    assert sm.find_archived(reused["name"]).id == max(reused["ids"])  # the newer one
    dup = sm.find_archived(MANIFEST["duplicate_archive_id"])
    assert dup.stats.estimated_cost_usd == MANIFEST["expected_ledger_totals"][dup.name]["cost"]
    months = [m for m, _ in sm.archive_months()]
    assert months == MANIFEST["archive_months"]
    assert not (sm.state_dir / "archive.jsonl").exists()
    assert not (sm.state_dir / "archive.json.migrated").exists()


def check_no_secrets(root: Path) -> None:
    """No archived agent's key, launch command or orders survive outside the user's files."""
    for rel, data in _texts(root):
        if Path(rel).name in USER_FILES:
            continue
        for secret in MANIFEST["secrets"]:
            assert secret.encode() not in data, (secret, rel)
    for path in (root / "archive").iterdir():
        if path.name.startswith("."):
            continue
        data = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
        for line in data.splitlines():
            record = json.loads(line)
            assert record["command"] == [] and "standing_instructions" not in record
            assert "source_api_key" not in record


def check_ledger(sm, totals: dict) -> None:
    """Backfill totals are the archived costs, children billed to their root's code."""
    expected = MANIFEST["expected_ledger_totals"]
    assert sorted(totals) == sorted(expected)
    for code, entry in expected.items():
        assert totals[code]["cost"] == pytest.approx(entry["cost"]), code
        assert totals[code]["tokens"] == entry["tokens"], code
        assert totals[code]["agents"] == entry["agents"], code
        assert totals[code]["running"] == 0, code
    rows = list(sm.ledger.iter_rows())
    assert {r["sid"] for r in rows} == set(MANIFEST["archived_ids"])
    assert all(r["kind"] == "backfill" for r in rows)
    for row in rows:
        assert row["billing_code"] == MANIFEST["billing_code_by_archived_id"][row["sid"]], row["name"]


def check_codes(sm, totals: dict) -> None:
    """Every billing code is a code with an id: live agents carry one, the
    backfilled strings map to one each, and reports are keyed by them."""
    from overcode.codes import is_code_id
    from overcode.doctor import billing_findings

    codes = sm.ledger.codes
    assert codes.migrated()
    for session in sm.list_sessions():
        assert is_code_id(session.billing_code_id), session.name
        assert codes.name_of(session.billing_code_id) == session.billing_code, session.name
    strings = set(MANIFEST["billing_code_by_archived_id"].values())
    strings |= set(MANIFEST["expected_live_billing_codes"].values())
    legacy = codes.load()["legacy"]
    assert strings <= set(legacy)
    assert len({legacy[s] for s in strings}) == len(strings)  # one code per string
    raw = sm.ledger.totals(save=False)
    assert all(is_code_id(code) for code in raw)
    assert sm.ledger.named(raw) == totals
    assert billing_findings(sm) == []


def check_files_kept(root: Path, sm, pristine: Path) -> None:
    """Every name-keyed file is where it was (a live agent's) or packed whole into a bundle."""
    live_names = {(s.tmux_session, s.name) for s in sm.list_sessions()}
    packed: dict = {}
    for member in bundle_members(root):
        if member.get("legacy"):
            for fname, content in member["files"].items():
                packed[(member["_tmux"], fname)] = content
    for tmux, names in MANIFEST["legacy_files"].items():
        for fname in names:
            original = pristine / "sessions" / tmux / fname
            here = root / "sessions" / tmux / fname
            if here.exists():
                assert here.read_bytes() == original.read_bytes()
                from overcode.layout_migration import legacy_name
                assert (tmux, legacy_name(fname)[0]) in live_names, f"{fname} left behind"
                continue
            if fname.endswith(".lock"):
                continue  # a lock file holds nothing
            assert (tmux, fname) in packed, f"{tmux}/{fname} removed but not packed"
            content = packed[(tmux, fname)]
            text = original.read_text()
            if isinstance(content, str):
                assert content == text, fname
            else:
                assert content == json.loads(text), fname


def check_config() -> None:
    """config.yaml still parses with the sisters (and their key) intact."""
    from overcode.config import get_sisters_config, load_config

    config = load_config()
    assert config["hostname"] == "fixture-host"
    sisters = get_sisters_config()
    assert [s["name"] for s in sisters] == MANIFEST["sisters"]
    assert sisters[0]["api_key"] == "FAKE-SISTER-KEY-0002"
    assert sisters[1]["url"] == "http://build-box.local:15337"


def check_storage(out: dict) -> None:
    """The doctor's storage report: nothing left to migrate but live agents' files."""
    from overcode.doctor import storage_findings

    sm = out["sm"]
    for tmux, report in out["storage"].items():
        live = [s for s in sm.list_sessions() if s.tmux_session == tmux]
        assert report["live"] == len(live)
        assert report["legacy_names"] == 0, tmux
        assert report["legacy_live"] == len(live), tmux
        assert report["orphans"] == 0 and report["agent_dirs"] == 0
        assert report["archive_files"] >= len(MANIFEST["archive_months"])
        assert storage_findings(report) == []


def check_upgraded(root: Path, out: dict, before: dict, pristine: Path) -> None:
    sm = out["sm"]
    check_live_sessions(sm, before)
    check_archive(sm)
    check_no_secrets(root)
    check_ledger(sm, out["totals"])
    check_codes(sm, out["totals"])
    check_files_kept(root, sm, pristine)
    check_config()
    check_storage(out)


# ── the tests ────────────────────────────────────────────────────────────

@pytest.fixture
def upgraded(tmp_path, monkeypatch):
    root = copy_fixture(tmp_path)
    point_at(root, monkeypatch.setenv)
    before = json.loads((root / "sessions" / "sessions.json").read_text())
    out = first_start(root)
    yield root, out, before
    from overcode.config import _clear_config_cache
    _clear_config_cache()


def test_fixture_is_058_and_synthetic():
    tree = FIXTURE / "tree"
    assert (tree / "sessions" / "archive.jsonl").exists()
    assert (tree / "sessions" / "archive.json.migrated").exists()
    assert not (tree / "archive").exists() and not (tree / "ledger").exists()
    size = sum(p.stat().st_size for p in tree.rglob("*") if p.is_file())
    assert size < 1_000_000
    for _, data in _texts(tree):
        assert b"/Users/" not in data and b"/private/" not in data


def test_live_sessions_kept(upgraded):
    root, out, before = upgraded
    check_live_sessions(out["sm"], before)
    # Live agents keep their orders and launch command (only the archive drops them)
    sessions = json.loads((root / "sessions" / "sessions.json").read_text())
    text = json.dumps(sessions)
    for secret in MANIFEST["live_secrets"]:
        assert secret in text


def test_archive_reachable(upgraded):
    check_archive(upgraded[1]["sm"])


def test_history_cli_lists_every_archived_agent(upgraded):
    from typer.testing import CliRunner

    from overcode.cli import app

    result = CliRunner().invoke(app, ["history", "--all"])
    assert result.exit_code == 0, result.output
    assert f"Archived Sessions ({len(MANIFEST['archived_ids'])})" in result.output
    for name in set(MANIFEST["archived_names"].values()):
        assert name in result.output
    result = CliRunner().invoke(app, ["history", MANIFEST["renamed"]["archived"]["previous"]])
    assert result.exit_code == 0 and MANIFEST["renamed"]["archived"]["name"] in result.output


def test_no_secrets_in_kept_records(upgraded):
    check_no_secrets(upgraded[0])


def test_ledger_backfill(upgraded):
    check_ledger(upgraded[1]["sm"], upgraded[1]["totals"])


def test_billing_codes_land_on_ids(upgraded):
    check_codes(upgraded[1]["sm"], upgraded[1]["totals"])


def test_no_file_lost(upgraded):
    root, out, _ = upgraded
    check_files_kept(root, out["sm"], FIXTURE / "tree")


def test_config_and_daemon_files_still_read(upgraded):
    check_config()
    from overcode.config import get_sisters_config, save_new_agent_defaults
    from overcode.monitor_daemon_state import MonitorDaemonState
    from overcode.settings import TUIPreferences, get_monitor_daemon_state_path

    root = upgraded[0]
    # A 0.6 write to config.yaml keeps the column-0 sisters list and the comments
    save_new_agent_defaults({"model": "sonnet"})
    assert [s["name"] for s in get_sisters_config()] == MANIFEST["sisters"]
    assert "# Sister machines" in (root / "config.yaml").read_text()
    for tmux in MANIFEST["tmux_sessions"]:
        state = MonitorDaemonState.load(get_monitor_daemon_state_path(tmux))
        assert state is not None and state.sessions
        assert TUIPreferences.load(tmux).sort_mode == "by_tree"


def test_storage_report(upgraded):
    check_storage(upgraded[1])


def test_second_start_changes_nothing(upgraded):
    root, out, before = upgraded
    digest = tree_digest(root)
    again = first_start(root)
    for tmux, passes in again["migrate"].items():
        assert all(not (p["packed"] or p["backfilled"] or p["minted"] or p["coded"])
                   for p in passes), tmux
    assert tree_digest(root) == digest
    check_upgraded(root, again, before, FIXTURE / "tree")


def test_archiving_a_pre_upgrade_agent_keeps_its_spend(upgraded):
    root, out, before = upgraded
    sm = out["sm"]
    child = next(s for s in sm.list_sessions() if s.name == "done-child")
    spent = child.stats.estimated_cost_usd
    assert spent > 0
    sm.update_session(child.id, agent_session_ids=["c1a0c1a0-0000-4000-8000-0000000000dc"])
    sm.delete_session(child.id)  # `overcode cleanup` / kill
    archived = sm.find_archived(child.id)
    final = [r for r in sm.ledger.iter_rows() if r["sid"] == child.id and r["kind"] == "final"]
    assert archived.stats.estimated_cost_usd == spent
    assert final and final[-1]["cost"] == spent


def test_the_daemon_stats_sync_keeps_a_pre_upgrade_agents_spend(upgraded):
    """The daemon reads a done agent's transcripts too; gone ones read as zeros."""
    from overcode.daemon_stats_sync import StatsSync
    from overcode.session_manager import PendingUpdates, _StatsLog

    root, out, before = upgraded
    sm = out["sm"]
    child = next(s for s in sm.list_sessions() if s.name == "done-child")
    spent, tokens = child.stats.estimated_cost_usd, child.stats.total_tokens
    assert spent > 0
    sm.update_session(child.id, agent_session_ids=["c1a0c1a0-0000-4000-8000-0000000000dc"])
    pending = PendingUpdates()
    StatsSync(session_manager=sm, log=_StatsLog()).sync_agent_stats(
        sm.get_session(child.id), pending)  # the daemon's tick
    sm.commit_pending(pending)
    kept = sm.get_session(child.id).stats
    assert (kept.estimated_cost_usd, kept.total_tokens) == (spent, tokens)
    archive = PendingUpdates()
    archive.archive_session(child.id)  # housekeeping's archive
    sm.commit_pending(archive)
    final = [r for r in sm.ledger.iter_rows() if r["sid"] == child.id and r["kind"] == "final"]
    assert final and final[-1]["cost"] == spent


def test_spend_restatement_keeps_agents_with_no_transcripts(upgraded, tmp_path):
    """The reader-fix pass over the archive (``spend_recompute``): the 0.5.8
    agents' transcripts are gone, so every record and ledger total stays."""
    from overcode.history_reader import HistoryFile
    from overcode.spend_recompute import recompute_archived_spend

    root, out, before = upgraded
    sm = out["sm"]
    digest = tree_digest(root / "sessions")
    totals = sm.ledger.named(sm.ledger.totals())
    history = tmp_path / "no-claude" / "history.jsonl"
    history.parent.mkdir()
    history.write_text("")
    counts = recompute_archived_spend(sm, history_file=HistoryFile(history),
                                      projects_path=tmp_path / "no-claude" / "projects")
    assert counts["done"] == 1 and counts["restated"] == 0 and counts["rows"] == 0
    after = tree_digest(root / "sessions")
    assert {k: v for k, v in after.items() if ".spend_rv" not in k} == digest
    assert sm.ledger.named(sm.ledger.totals()) == totals
    check_archive(sm)


def test_ledger_by_month(upgraded):
    """A month's report holds the agents archived that month (backfill rows keep their time)."""
    from datetime import datetime

    sm = upgraded[1]["sm"]
    newest = {}
    for line in (FIXTURE / "tree" / "sessions" / "archive.jsonl").read_text().splitlines():
        record = json.loads(line)
        newest[record["id"]] = record
    by_month: dict = {}
    for sid, record in newest.items():
        month = by_month.setdefault(record["end_time"][:7], {})
        code = MANIFEST["billing_code_by_archived_id"][sid]
        month[code] = month.get(code, 0.0) + record["stats"]["estimated_cost_usd"]
    for month, expected in by_month.items():
        year, mon = map(int, month.split("-"))
        start = datetime(year, mon, 1)
        end = datetime(year + mon // 12, mon % 12 + 1, 1)
        report = sm.ledger.named(sm.ledger.report(start, end))
        assert {c: e["cost"] for c, e in report.items()} == \
            {c: pytest.approx(v) for c, v in expected.items()}, month


def test_reupgrade_takes_in_a_058_archive_written_after_a_downgrade(upgraded):
    """0.5.8 run on the upgraded tree archives into sessions/archive.jsonl again.

    The next 0.6 start splits it into the months, drops its secrets,
    backfills the ledger for it (billed to its root) and packs the
    name-keyed files 0.5.8 left, without counting anyone twice.
    """
    root, out, _ = upgraded
    sessions_file = root / "sessions" / "sessions.json"
    state = json.loads(sessions_file.read_text())
    worker = next(sid for sid, r in state.items() if r["name"] == "worker-a")
    # What 0.5.8's delete_session does: the whole record appended, then dropped
    record = dict(state.pop(worker), end_time="2026-10-01T12:00:00", status="archived",
                  standing_instructions="Finish; staging key FAKE-SI-TOKEN-0099")
    record["stats"] = dict(record["stats"], estimated_cost_usd=1.05)
    with open(root / "sessions" / "archive.jsonl", "a") as f:
        f.write(json.dumps(record) + "\n")
    sessions_file.write_text(json.dumps(state, indent=2))
    (root / "sessions" / "agents" / "hook_state_worker-a.json").write_text('{"event": "Stop"}')

    again = first_start(root)
    sm = again["sm"]
    assert not (root / "sessions" / "archive.jsonl").exists()
    found = sm.find_archived("worker-a")
    assert found is not None and found.stats.estimated_cost_usd == 1.05
    assert len(sm.list_archived_sessions()) == len(MANIFEST["archived_ids"]) + 1
    rows = [r for r in sm.ledger.iter_rows() if r["kind"] in ("final", "backfill")]
    assert sorted(r["sid"] for r in rows) == sorted(MANIFEST["archived_ids"] + [worker])
    (row,) = [r for r in rows if r["sid"] == worker]
    assert row["billing_code"] == "orchestrator" and row["cost"] == 1.05
    expected = MANIFEST["expected_ledger_totals"]["orchestrator"]["cost"] + 1.05
    assert again["totals"]["orchestrator"]["cost"] == pytest.approx(expected)
    for rel, data in _texts(root):
        if Path(rel).name not in USER_FILES:
            assert b"FAKE-SI-TOKEN-0099" not in data, rel
    packed = {fname for m in bundle_members(root) if m.get("legacy") for fname in m["files"]}
    assert "hook_state_worker-a.json" in packed
    assert not (root / "sessions" / "agents" / "hook_state_worker-a.json").exists()
