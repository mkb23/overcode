"""The e2e report gate: scripts/e2e_reports.py.

The expensive tiers run locally and leave a committed report; the release
gate must refuse a release unless each required tier passed recently and
close enough to the commit being released.
"""

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SPEC = importlib.util.spec_from_file_location(
    "e2e_reports", Path(__file__).resolve().parents[2] / "scripts" / "e2e_reports.py")
e2e_reports = importlib.util.module_from_spec(_SPEC)
sys.modules["e2e_reports"] = e2e_reports  # dataclasses look their module up
_SPEC.loader.exec_module(e2e_reports)

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
POLICY = {"max_age_days": 14, "max_src_lines_changed": 5000}
TIER = {"required": True, "must_pass": ["test_a"]}


def history(ancestor=True, lines=100):
    return e2e_reports.History(lambda c, r: ancestor, lambda c, r: lines)


def report(**overrides):
    base = {
        "tier": "t", "result": "passed", "commit": "a" * 40, "src_dirty": False,
        "ran_at": (NOW - timedelta(days=2)).isoformat(),
        "counts": {"passed": 1, "failed": 0, "skipped": 0}, "missing_required": [],
    }
    base.update(overrides)
    return base


def verdict(rep, hist=None, tier=TIER):
    return e2e_reports.evaluate("t", tier, rep, POLICY, "b" * 40, NOW, hist or history())


class TestEvaluate:
    def test_a_fresh_passing_report_is_ok(self):
        v = verdict(report())
        assert v.ok and not v.problems

    def test_no_report(self):
        v = verdict(None)
        assert not v.ok and v.required

    def test_a_failed_run(self):
        v = verdict(report(result="failed", counts={"failed": 2}, missing_required=["test_a"]))
        assert not v.ok
        assert "missing test_a" in v.problems[0]

    def test_too_old(self):
        v = verdict(report(ran_at=(NOW - timedelta(days=15)).isoformat()))
        assert not v.ok and "days old" in v.problems[0]

    def test_too_far_behind(self):
        v = verdict(report(), history(lines=5001))
        assert not v.ok and "5,001 src lines" in v.problems[0]
        assert verdict(report(), history(lines=5000)).ok

    def test_not_an_ancestor(self):
        v = verdict(report(), history(ancestor=False))
        assert not v.ok and "not an ancestor" in v.problems[0]

    def test_commit_not_in_history(self):
        v = verdict(report(), history(ancestor=None))
        assert not v.ok and "fetch-depth" in v.problems[0]

    def test_recorded_on_a_dirty_tree(self):
        assert not verdict(report(src_dirty=True)).ok

    def test_optional_tiers_are_marked_so(self):
        v = verdict(None, tier={"required": False})
        assert not v.required


class TestRecordingHelpers:
    def test_parse_junit(self, tmp_path):
        xml = tmp_path / "j.xml"
        xml.write_text(
            '<testsuites><testsuite>'
            '<testcase classname="m.C" name="test_ok[claude-code]"/>'
            '<testcase classname="m.C" name="test_bad"><failure message="x"/></testcase>'
            '<testcase classname="m.C" name="test_err"><error message="x"/></testcase>'
            '<testcase classname="m.C" name="test_skip[grok]"><skipped message="x"/></testcase>'
            '</testsuite></testsuites>')
        assert e2e_reports.parse_junit(xml) == {
            "m.C::test_ok[claude-code]": "passed", "m.C::test_bad": "failed",
            "m.C::test_err": "failed", "m.C::test_skip[grok]": "skipped",
        }

    def test_skipped_is_not_passed(self):
        outcomes = {"m::test_a[x]": "passed", "m::test_a[y]": "skipped"}
        assert e2e_reports.missing_required(outcomes, ["test_a[x]", "test_a[y]"]) == ["test_a[y]"]


class TestTheRealConfig:
    def test_the_tiers_file_loads_and_names_required_tiers(self):
        config = e2e_reports.load_config()
        assert config["policy"]["max_age_days"] > 0
        assert any(t.get("required") for t in config["tiers"].values())
        for name, tier in config["tiers"].items():
            assert tier["command"], name
            assert "{junit}" in " ".join(tier["command"]) or "junit" in tier, name

    def test_evaluate_all_reads_reports_from_a_directory(self, tmp_path):
        config = {"policy": POLICY, "tiers": {"t": TIER, "u": {"required": False}}}
        (tmp_path / "t.json").write_text(json.dumps(report()))
        verdicts = e2e_reports.evaluate_all(config, "b" * 40, NOW, history(), tmp_path)
        assert [(v.tier, v.ok) for v in verdicts] == [("t", True), ("u", False)]
