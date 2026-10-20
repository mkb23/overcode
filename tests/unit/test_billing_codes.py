"""Billing codes as ids with renamable names (docs/design/billing-code-ids.md).

The registry (``codes``), resolution at launch, rename and merge without
touching a ledger row, and the migration of 0.6.0 string-keyed ledgers,
sessions and archives. Synthetic records only.
"""

import gzip
import hashlib
import json
import os
from datetime import datetime
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from overcode.cli import app
from overcode.codes import CodeError, CodeRegistry, is_code_id
from overcode.launcher import AgentLauncher
from overcode.layout_migration import fill_billing_codes, mint_legacy_codes
from overcode.ledger import Ledger
from overcode.session_manager import SessionManager
from overcode.tmux_manager import TmuxManager
from tests.tmux_doubles import MockTmux

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith("OVERCODE_") and key not in ("OVERCODE_DIR", "OVERCODE_TMUX_SOCKET"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "sessions"))
    with patch("overcode.launcher.require_tmux"), \
         patch("overcode.launcher.require_agent_cli"), \
         patch("overcode.launcher.time.sleep"):
        yield


@pytest.fixture
def sm():
    return SessionManager(skip_git_detection=True)


@pytest.fixture
def launcher(sm):
    return AgentLauncher("agents", TmuxManager("agents", tmux=MockTmux()), sm)


@pytest.fixture
def codes(tmp_path):
    return CodeRegistry(tmp_path / "ledger")


def _agent(sm, name, cost=0.0, **kwargs):
    s = sm.create_session(name=name, tmux_session="agents", tmux_window=f"{name}-1",
                          command=[], **kwargs)
    if cost:
        sm.update_stats(s.id, estimated_cost_usd=cost, total_tokens=int(cost * 100))
    return sm.get_session(s.id)


def _row(sid, string, cost, kind="checkpoint", seq=1, t="2026-09-10T10:00:00", seg=0, **extra):
    return {"t": t, "seq": seq, "sid": sid, "name": sid, "billing_code": string, "seg": seg,
            "cost": cost, "tokens": int(cost * 100), "tok_in": 0, "tok_out": 0, "tok_cache": 0,
            "base_cost": 0.0, "base_tokens": 0, "kind": kind, **extra}


def _ledger_digest(ledger):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in ledger.dir.iterdir() if p.name.endswith((".jsonl", ".gz"))}


class TestRegistry:
    def test_a_code_is_an_id_and_a_name(self, codes):
        code_id = codes.resolve("shirka")
        assert is_code_id(code_id)
        entry = codes.get(code_id)
        assert entry["name"] == "shirka" and entry["source"] == "default"
        assert entry["previous_names"] == [] and entry["legacy"] == []
        on_disk = json.loads(codes.path.read_text())
        assert on_disk["v"] == 1 and code_id in on_disk["codes"]

    def test_the_same_name_resolves_to_the_same_code(self, codes):
        assert codes.resolve("shirka", root="a") == codes.resolve("shirka", root="b")
        assert len(codes.codes()) == 1

    def test_a_given_name_makes_the_code_human(self, codes):
        code_id = codes.resolve("api")
        codes.resolve("api", source="human")
        assert codes.get(code_id)["source"] == "human"

    def test_resolution_order_current_then_previous_then_legacy(self, codes):
        old = codes.adopt(["shirka"])["shirka"]
        codes.rename("shirka", "shirka-old")
        assert codes.find("shirka") == old  # a previous name still resolves
        newer = codes.resolve("other")
        codes.rename("other", "shirka")
        assert codes.find("shirka") == newer  # a current name wins
        assert codes.find("shirka-old") == old
        assert codes.canonical("shirka") == old  # the legacy string stays with its code

    def test_rename_refuses_a_name_another_code_has(self, codes):
        codes.resolve("a")
        codes.resolve("b")
        with pytest.raises(CodeError):
            codes.rename("a", "b")
        with pytest.raises(CodeError):
            codes.rename("nope", "c")
        with pytest.raises(CodeError):
            codes.rename("a", " ")

    def test_writes_are_atomic_and_leave_no_temp_file(self, codes):
        codes.resolve("a")
        codes.rename("a", "b")
        assert sorted(p.name for p in codes.dir.iterdir()) == [".codes.lock", "codes.json"]

    def test_a_corrupt_registry_reads_as_empty(self, codes):
        codes.dir.mkdir(parents=True)
        codes.path.write_text("{not json")
        assert codes.codes() == {} and codes.find("x") is None

    def test_adopt_is_idempotent_and_never_steals_a_current_name(self, codes):
        current = codes.resolve("x")
        codes.rename("x", "y")
        codes.resolve("x-new")
        codes.rename("x-new", "x")
        mapped = codes.adopt(["x", "z"])
        assert mapped == codes.adopt(["x", "z"])
        assert mapped["x"] not in (current,) and codes.get(mapped["x"])["name"] == "x~2"
        assert codes.get(mapped["z"])["legacy"] == ["z"]


class TestFollowAgentName:
    def test_a_default_code_of_one_tree_follows(self, codes):
        code_id = codes.resolve("overcode-main2", root="root-1")
        assert codes.follow_agent_name(code_id, "billing-ids")
        assert codes.get(code_id)["name"] == "billing-ids"
        assert codes.get(code_id)["source"] == "default"
        assert codes.find("overcode-main2") == code_id

    def test_a_human_code_never_changes_by_itself(self, codes):
        code_id = codes.resolve("client-x", source="human", root="root-1")
        assert not codes.follow_agent_name(code_id, "renamed")
        assert codes.get(code_id)["name"] == "client-x"

    def test_a_code_two_trees_used_stays(self, codes):
        code_id = codes.resolve("shirka", root="root-1")
        codes.resolve("shirka", root="root-2")
        assert not codes.follow_agent_name(code_id, "renamed")

    def test_a_code_with_no_known_tree_stays(self, codes):
        code_id = codes.adopt(["legacy"])["legacy"]
        assert not codes.follow_agent_name(code_id, "renamed")

    def test_only_to_a_free_name(self, codes):
        code_id = codes.resolve("tmp", root="root-1")
        codes.resolve("taken")
        assert not codes.follow_agent_name(code_id, "taken")
        assert codes.follow_agent_name(code_id, "tmp")  # its own name already
        assert not codes.follow_agent_name("c_" + "0" * 32, "x")


class TestLaunch:
    def test_same_name_launches_roll_up_into_one_code(self, launcher, sm):
        a = launcher.launch(name="shirka")
        sm.delete_session(a.id)
        b = launcher.launch(name="shirka")
        assert sm.find_archived(a.id).billing_code_id == sm.get_session(b.id).billing_code_id
        assert sm.ledger.codes.roots_of(sm.get_session(b.id).billing_code_id) == [a.id, b.id]

    def test_a_launch_flag_names_a_human_code(self, launcher, sm):
        agent = launcher.launch(name="a", billing_code="client-x")
        code = sm.ledger.codes.get(sm.get_session(agent.id).billing_code_id)
        assert code["name"] == "client-x" and code["source"] == "human"

    def test_children_inherit_the_id(self, launcher, sm):
        parent = launcher.launch(name="boss", billing_code="client-x")
        with patch.dict(os.environ, {"OVERCODE_SESSION_NAME": "boss"}):
            child = launcher.launch(name="kid")
        assert sm.get_session(child.id).billing_code_id == sm.get_session(parent.id).billing_code_id
        # The child's tree is its parent's: the code is not used by another tree
        assert sm.ledger.codes.roots_of(sm.get_session(parent.id).billing_code_id) == [parent.id]

    def test_a_previous_name_given_to_a_child_is_its_parents_code(self, launcher, sm, capsys):
        parent = launcher.launch(name="boss", billing_code="client-x")
        sm.ledger.codes.rename("client-x", "client-y")
        with patch.dict(os.environ, {"OVERCODE_SESSION_NAME": "boss"}):
            launcher.launch(name="kid", billing_code="client-x")
        assert "ignored" not in capsys.readouterr().err
        assert parent.parent_session_id is None

    def test_a_fork_inherits_the_id(self, launcher, sm):
        source = launcher.launch(name="src", billing_code="client-x")
        sm.update_session(source.id, active_agent_session_id="conv-1")
        with patch.object(launcher, "_send_launch_for_session", return_value=True):
            fork = launcher.launch_fork("src-fork", sm.get_session(source.id))
        assert sm.get_session(fork.id).billing_code_id == sm.get_session(source.id).billing_code_id
        assert sm.get_session(fork.id).billing_code == "client-x"


class TestRename:
    def test_renaming_changes_every_report_and_no_ledger_file(self, sm):
        a = _agent(sm, "a", cost=2.0, billing_code="old")
        sm.delete_session(a.id)
        b = _agent(sm, "b", cost=1.0, billing_code="old")
        sm.ledger.append([_row(b.id, None, 1.0, code=b.billing_code_id,
                               t=datetime.now().isoformat(), seq=10**20)])
        digest = _ledger_digest(sm.ledger)
        result = runner.invoke(app, ["billing", "rename", "old", "new"])
        assert result.exit_code == 0, result.output
        assert _ledger_digest(sm.ledger) == digest
        totals = sm.ledger.named(sm.ledger.totals())
        assert set(totals) == {"new"} and totals["new"]["cost"] == pytest.approx(3.0)
        period = sm.ledger.named(sm.ledger.report(since=datetime(2000, 1, 1)))
        assert set(period) == {"new"}
        assert sm.get_session(b.id).billing_code == "new"  # the display copy follows
        assert "new" in runner.invoke(app, ["billing"]).output
        assert runner.invoke(app, ["billing", "rename", "nope", "x"]).exit_code == 1

    def test_a_new_code_given_a_renamed_codes_old_name_gets_none_of_its_spend(self, sm):
        sm.ledger.append([_row("s-old", "shirka", 5.0, kind="final")])
        mint_legacy_codes(sm)
        old_id = sm.ledger.codes.find("shirka")
        sm.ledger.codes.rename("shirka", "shirka-2025")
        newer = _agent(sm, "other", cost=1.0, billing_code="fresh")
        sm.ledger.codes.rename("fresh", "shirka")
        sm.delete_session(newer.id)
        totals = sm.ledger.totals()
        new_id = sm.ledger.codes.find("shirka")
        assert new_id != old_id
        assert totals[old_id]["cost"] == 5.0 and totals[new_id]["cost"] == 1.0
        assert sm.ledger.named(totals)["shirka"]["cost"] == 1.0

    def test_rebill_by_name(self, sm):
        a = _agent(sm, "a", cost=1.0)
        assert sm.rebill(a.id, "client-x") == "a"
        sm.ledger.codes.rename("client-x", "client-y")
        assert sm.rebill(a.id, "client-x") == "client-y"  # the same code: no rows
        rows = list(sm.ledger.iter_rows())
        assert [(r["kind"], r["billing_code"]) for r in rows] == [("close", "a"),
                                                                    ("open", "client-x")]
        assert all(is_code_id(r["code"]) for r in rows)


class TestMerge:
    def test_a_merge_folds_a_codes_rows_into_another_without_rewriting(self, sm):
        sm.ledger.append([
            _row("s1", "api", 2.0, kind="final", t="2026-09-10T10:00:00"),
            _row("s2", "api-v2", 1.0, kind="final", t="2026-09-20T10:00:00"),
        ])
        mint_legacy_codes(sm)
        live = _agent(sm, "c", billing_code="api-v2")
        digest = _ledger_digest(sm.ledger)
        result = runner.invoke(app, ["billing", "merge", "api-v2", "api"])
        assert result.exit_code == 0, result.output
        assert _ledger_digest(sm.ledger) == digest
        api = sm.ledger.codes.find("api")
        assert sm.ledger.codes.find("api-v2") == api
        assert sm.ledger.totals()[api]["cost"] == pytest.approx(3.0)
        # A period spanning the merge: both codes' rows, under the kept code
        sept = sm.ledger.named(sm.ledger.report(since=datetime(2026, 9, 15),
                                                until=datetime(2026, 10, 1)))
        assert sept == {"api": sept["api"]} and sept["api"]["cost"] == pytest.approx(1.0)
        assert sm.get_session(live.id).billing_code_id == api
        assert sm.get_session(live.id).billing_code == "api"
        assert runner.invoke(app, ["billing", "merge", "api", "api-v2"]).exit_code == 1

    def test_codes_lists_names_ids_and_previous_names(self, sm):
        from overcode.ledger import make_row

        sm.ledger.append([make_row(_agent(sm, "a", cost=1.0, billing_code="x"), "checkpoint")])
        sm.ledger.codes.rename("x", "y")
        out = runner.invoke(app, ["billing", "codes"]).output
        code_id = sm.ledger.codes.find("y")
        assert "y" in out and code_id in out and "x" in out
        data = json.loads(runner.invoke(app, ["billing", "codes", "--json"]).output)
        assert any(c["id"] == code_id and c["previous_names"] == ["x"] and c["live"] == 1
                   for c in data)
        assert code_id in runner.invoke(app, ["billing", "--ids"]).output


def _write_060_fixtures(sm):
    """A 0.6.0 tree: string-keyed rows over a closed (gzipped) and an open
    month, live and archived records with only ``billing_code``, an old
    totals.json and claude_bg.json."""
    ledger = sm.ledger
    ledger.dir.mkdir(parents=True, exist_ok=True)
    august = [
        _row("arch-1", "shirka", 1.25, kind="final", seq=1, t="2026-08-03T10:00:00"),
        _row("arch-2", "client-x", 0.10, kind="checkpoint", seq=2, t="2026-08-04T10:00:00"),
        _row("arch-2", "client-x", 0.333, kind="close", seq=3, t="2026-08-05T10:00:00"),
        _row("arch-2", "shirka", 0.5, kind="open", seq=4, t="2026-08-05T10:00:00", seg=1,
             base_cost=0.333),
    ]
    with gzip.open(ledger.dir / "2026-08.jsonl.gz", "wt") as f:
        f.writelines(json.dumps(r) + "\n" for r in august)
    september = [
        _row("arch-2", "shirka", 0.9, kind="final", seq=5, t="2026-09-01T10:00:00", seg=1,
             base_cost=0.333),
        _row("live-1", "client-x", 0.42, kind="checkpoint", seq=6, t="2026-09-02T10:00:00"),
    ]
    (ledger.dir / "2026-09.jsonl").write_text("".join(json.dumps(r) + "\n" for r in september))
    (ledger.dir / "totals.json").write_text(json.dumps(
        {"v": 1, "months": {}, "open": {}, "closed": {}, "codes": {"shirka": {"cost": 99}}}))
    state = {"live-1": {"id": "live-1", "name": "worker", "tmux_session": "agents",
                        "tmux_window": "worker-1", "command": [], "start_directory": None, "start_time": "2026-09-02T09:00:00",
                        "billing_code": "client-x",
                        "stats": {"estimated_cost_usd": 0.42, "total_tokens": 42}},
             "live-2": {"id": "live-2", "name": "helper", "tmux_session": "agents",
                        "tmux_window": "helper-1", "command": [], "start_directory": None, "start_time": "2026-09-02T09:00:00",
                        "parent_session_id": "live-1",
                        "stats": {"estimated_cost_usd": 0.0, "total_tokens": 0}}}
    sm.state_file.parent.mkdir(parents=True, exist_ok=True)
    sm.state_file.write_text(json.dumps(state))
    archive = {"id": "arch-3", "name": "old", "tmux_session": "agents", "tmux_window": "old-1",
               "command": [], "billing_code": "archived-only", "status": "archived",
               "start_time": "2026-07-01T09:00:00", "end_time": "2026-07-01T10:00:00",
               "stats": {"estimated_cost_usd": 0.0}}
    sm._append_archive_records([archive])


class TestMigrationFrom060:
    def test_legacy_rows_and_new_rows_fold_into_one_code_to_the_cent(self, sm):
        _write_060_fixtures(sm)
        before = Ledger(sm.state_dir).totals(save=False)  # no registry yet: by string
        assert set(before) == {"shirka", "client-x"}
        digest = _ledger_digest(sm.ledger)
        minted = mint_legacy_codes(sm)
        assert minted == 3  # shirka, client-x, archived-only
        assert mint_legacy_codes(sm) == 0  # idempotent
        filled = fill_billing_codes(sm)
        client = sm.ledger.codes.find("client-x")
        assert filled["live-1"] == (client, "client-x")
        assert filled["live-2"] == (client, "client-x")  # a child takes its parent's code
        assert _ledger_digest(sm.ledger) == digest  # no row rewritten
        after = sm.ledger.totals()
        assert all(is_code_id(k) for k in after)
        named = sm.ledger.named(after)
        for code, entry in before.items():
            assert round(named[code]["cost"], 2) == round(entry["cost"], 2), code
            assert named[code]["agents"] == entry["agents"], code
        assert json.loads(sm.ledger.totals_path.read_text())["v"] == 2  # rebuilt once
        # The live agent's next checkpoint carries the id: the same segment, one code
        sm.update_stats("live-1", estimated_cost_usd=1.0, total_tokens=100)
        from overcode.ledger import make_row

        row = make_row(sm.get_session("live-1"), "checkpoint")
        assert row["code"] == client and row["billing_code"] == "client-x"
        sm.ledger.append([row])
        totals = sm.ledger.totals()
        assert totals[client]["cost"] == pytest.approx(0.333 + 1.0)
        assert totals[client]["agents"] == 2 and totals[client]["running"] == 1

    def test_period_reports_map_legacy_rows(self, sm):
        _write_060_fixtures(sm)
        before = Ledger(sm.state_dir).report(since=datetime(2026, 8, 1),
                                             until=datetime(2026, 9, 1))
        mint_legacy_codes(sm)
        after = sm.ledger.named(sm.ledger.report(since=datetime(2026, 8, 1),
                                                 until=datetime(2026, 9, 1)))
        assert {k: round(v["cost"], 6) for k, v in after.items()} == \
               {k: round(v["cost"], 6) for k, v in before.items()}

    def test_the_layout_migration_mints_them(self, sm):
        from overcode.layout_migration import migrate_layout

        _write_060_fixtures(sm)
        counts = migrate_layout("agents", sm)
        assert counts["minted"] == 3 and sm.ledger.codes.migrated()
        assert all(s.billing_code_id for s in sm.list_sessions())

    def test_doctor_flags_what_the_registry_cannot_account_for(self, sm):
        from overcode.doctor import billing_findings

        _write_060_fixtures(sm)
        findings = billing_findings(sm)
        assert len(findings) == 1 and "shirka" in findings[0]
        mint_legacy_codes(sm)
        fill_billing_codes(sm)
        assert billing_findings(sm) == []
        sm.update_session("live-1", billing_code_id="c_" + "f" * 32)
        assert "worker" in billing_findings(sm)[0]


class TestBackgroundSessions:
    def test_a_legacy_code_and_its_id_are_one_code_not_a_rebill(self, sm):
        from overcode.claude_bg_ledger import BgLedger

        sm.ledger.append([_row("s1", "proj-x", 0.0, kind="final")])
        mint_legacy_codes(sm)
        code_id = sm.ledger.codes.find("proj-x")
        bg = BgLedger(lambda: sm.ledger.dir, "agents", checkpoint_seconds=lambda: 0.0)
        bg._codes = sm.ledger.codes
        record = {"id": "claude-bg:x", "name": "bg", "parent_session_id": "p",
                  "billing_code_id": code_id, "billing_code": "proj-x",
                  "stats": {"estimated_cost_usd": 2.0, "total_tokens": 20}}
        entry = {"seg": 0, "closed": False, "base": {}, "code": "proj-x",
                 "last": {"cost": 1.0, "tokens": 10}, "at": 0.0}
        rows, entry = bg._rows_for(record, False, entry, 100.0, datetime.now())
        assert [r["kind"] for r in rows] == ["checkpoint"]
        assert rows[0]["code"] == code_id and entry["code"] == code_id
