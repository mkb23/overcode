"""`overcode doctor`'s storage section: counts, bytes, orphans, layout."""

import pytest
from typer.testing import CliRunner

from overcode.cli import app
from overcode.doctor import AGENT_DIRS_SLACK, storage_findings, storage_notes, storage_report
from overcode.paths import agent_dir, bundle_path, legacy_agent_file
from overcode.session_manager import SessionManager


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "sessions"))
    return tmp_path / "sessions"


@pytest.fixture
def sm():
    return SessionManager(skip_git_detection=True)


def _live(sm, name):
    s = sm.create_session(name=name, tmux_session="agents", tmux_window=f"{name}-1", command=[])
    agent_dir("agents", s.id).mkdir(parents=True)
    (agent_dir("agents", s.id) / "hook_state.json").write_text("x" * 100)
    return s


def test_report_counts(sm):
    _live(sm, "a")
    _live(sm, "b")
    agent_dir("agents", "orphan").mkdir(parents=True)
    path = bundle_path("agents", "2026-10-12")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"z" * 1000)
    legacy_agent_file("agents", "old", "report").write_text("{}")
    sm.ledger.append([{"sid": "s", "billing_code": "x", "cost": 1.0, "kind": "final"}])
    report = storage_report("agents", sm)
    assert report["live"] == 2 and report["agent_dirs"] == 3 and report["orphans"] == 1
    assert report["agent_bytes"] == 200
    assert report["bundles"] == 1 and report["bundle_bytes"] == 1000
    assert report["ledger_files"] == 1 and report["ledger_bytes"] > 0
    assert report["layout"] == "1" and report["legacy_names"] == 1


def test_findings():
    base = {"live": 10, "agent_dirs": 12, "orphans": 2, "layout": "2", "legacy_names": 0}
    assert storage_findings(base) == []
    many = dict(base, agent_dirs=10 + AGENT_DIRS_SLACK + 1, orphans=AGENT_DIRS_SLACK + 1)
    (finding,) = storage_findings(many)
    assert "61 directories for 10 live agents" in finding and "orphan sweep" in finding
    (finding,) = storage_findings(many, terminated_grace_seconds=-1)
    assert "nothing bounds it" in finding
    (finding,) = storage_findings(base, terminated_grace_seconds=-1)
    assert "archiving is off" in finding
    pending = dict(base, layout="1", legacy_names=40)
    assert "pre-0.6 layout" in storage_findings(pending)[0]
    assert storage_notes(base) == []


def test_cli_prints_the_section(sm):
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "Storage" in result.output and "agents/" in result.output and "ledger/" in result.output


def test_live_agents_from_before_the_upgrade_are_a_note_not_a_warning(sm):
    """Their hooks still write the name-keyed files: nothing to migrate yet."""
    prior = sm.create_session(name="prior", tmux_session="agents", tmux_window="p-1", command=[])
    legacy_agent_file("agents", prior.name, "hook_state").parent.mkdir(parents=True, exist_ok=True)
    legacy_agent_file("agents", prior.name, "hook_state").write_text("{}")
    legacy_agent_file("agents", "gone", "report").write_text("{}")
    report = storage_report("agents", sm)
    assert report["legacy_live"] == 1 and report["legacy_names"] == 1
    (note,) = storage_notes(report)
    assert "1 live agent launched before the upgrade" in note
    (finding,) = storage_findings(report)
    assert finding.startswith("1 finished agents' files")
