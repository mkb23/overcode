#!/usr/bin/env python3
"""Record the expensive local e2e tiers, and check their reports before a release.

The tiers that need real agent CLIs, credentials and tokens run on a Mac,
not in GitHub Actions. Each run leaves a report in tests/e2e-reports/; the
report is committed, and the release gate reads it.

    scripts/e2e_reports.py record live-backends   # run a tier, write its report
    scripts/e2e_reports.py status                 # every tier's freshness
    scripts/e2e_reports.py check [--ref SHA]      # exit 1 unless the required tiers are fresh

Tiers and the freshness policy live in tests/e2e-reports/tiers.toml.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "tests" / "e2e-reports"
CONFIG = REPORTS / "tiers.toml"

# Agent CLIs whose versions a report records (what the run actually spoke to)
CLI_VERSIONS = ("claude", "codex", "opencode", "hermes", "grok")


def load_config(path: Path = CONFIG) -> dict:
    return tomllib.loads(path.read_text())


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


# -- recording ---------------------------------------------------------------


def parse_junit(path: Path) -> dict[str, str]:
    """{test id: passed|failed|skipped} from a junit XML file."""
    outcomes: dict[str, str] = {}
    for case in ET.parse(path).getroot().iter("testcase"):
        test_id = f"{case.get('classname', '')}::{case.get('name', '')}"
        if case.find("failure") is not None or case.find("error") is not None:
            outcomes[test_id] = "failed"
        elif case.find("skipped") is not None:
            outcomes[test_id] = "skipped"
        else:
            outcomes[test_id] = "passed"
    return outcomes


def missing_required(outcomes: dict[str, str], must_pass: list[str]) -> list[str]:
    """The must_pass names with no passing test (matched on the test's own name)."""
    passed = {test_id.split("::")[-1] for test_id, out in outcomes.items() if out == "passed"}
    return [name for name in must_pass if name not in passed]


def cli_versions() -> dict[str, str]:
    versions = {}
    for binary in CLI_VERSIONS:
        try:
            out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            continue
        text = (out.stdout or out.stderr).strip().splitlines()
        if out.returncode == 0 and text:
            versions[binary] = text[0][:80]
    return versions


def src_is_dirty() -> bool:
    return bool(git("status", "--porcelain", "--", "src").stdout.strip())


def record(tier_name: str, config: dict) -> int:
    tier = config["tiers"].get(tier_name)
    if tier is None:
        print(f"unknown tier {tier_name!r}; known: {', '.join(config['tiers'])}", file=sys.stderr)
        return 2
    commit = git("rev-parse", "HEAD").stdout.strip()
    dirty = src_is_dirty()
    if dirty:
        print("warning: src/ has uncommitted changes; this report will not count for a release",
              file=sys.stderr)

    with tempfile.TemporaryDirectory(prefix="e2e-report-") as tmp:
        junit = Path(tmp) / "junit.xml"
        command = [sys.executable if part == "python" else part.replace("{junit}", str(junit))
                   for part in tier["command"]]
        if "junit" in tier:  # the command writes its junit somewhere fixed
            junit = ROOT / tier["junit"]
            junit.unlink(missing_ok=True)
        env = {**os.environ, **tier.get("env", {})}
        started = time.monotonic()
        print(f"$ {' '.join(command)}", flush=True)
        returncode = subprocess.run(command, cwd=ROOT, env=env).returncode
        duration = time.monotonic() - started
        outcomes = parse_junit(junit) if junit.exists() else {}

    counts = {k: sum(1 for v in outcomes.values() if v == k) for k in ("passed", "failed", "skipped")}
    missing = missing_required(outcomes, tier.get("must_pass", []))
    passed = returncode == 0 and counts["failed"] == 0 and bool(outcomes) and not missing
    report = {
        "tier": tier_name,
        "result": "passed" if passed else "failed",
        "commit": commit,
        "src_dirty": dirty,
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_s": round(duration),
        "exit_code": returncode,
        "counts": counts,
        "missing_required": missing,
        "tests": dict(sorted(outcomes.items())),
        "env": tier.get("env", {}),
        "versions": {
            "python": platform.python_version(),
            "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
            **cli_versions(),
        },
    }
    path = REPORTS / f"{tier_name}.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"\n{tier_name}: {report['result'].upper()} "
          f"({counts['passed']} passed, {counts['failed']} failed, {counts['skipped']} skipped"
          f"{', missing ' + ', '.join(missing) if missing else ''}) in {round(duration)}s")
    print(f"wrote {path.relative_to(ROOT)}; commit it so the release gate can see it")
    return 0 if passed else 1


# -- checking ----------------------------------------------------------------


@dataclass
class Verdict:
    tier: str
    required: bool
    ok: bool
    summary: str
    problems: list[str] = field(default_factory=list)


@dataclass
class History:
    """The git questions the check asks, injectable for tests."""
    is_ancestor: Callable[[str, str], Optional[bool]]  # None: commit unknown here
    src_lines_changed: Callable[[str, str], int]


def git_history() -> History:
    def is_ancestor(commit: str, ref: str) -> Optional[bool]:
        if git("cat-file", "-e", f"{commit}^{{commit}}").returncode != 0:
            return None
        return git("merge-base", "--is-ancestor", commit, ref).returncode == 0

    def src_lines_changed(commit: str, ref: str) -> int:
        total = 0
        for line in git("diff", "--numstat", commit, ref, "--", "src").stdout.splitlines():
            added, deleted, _ = line.split("\t", 2)
            if added != "-":
                total += int(added) + int(deleted)
        return total

    return History(is_ancestor, src_lines_changed)


def evaluate(tier_name: str, tier: dict, report: Optional[dict], policy: dict,
             ref: str, now: datetime, history: History) -> Verdict:
    required = bool(tier.get("required", False))
    if report is None:
        return Verdict(tier_name, required, False, "no report", ["never recorded"])
    problems = []
    if report.get("result") != "passed":
        problems.append(f"last run failed ({report.get('counts', {}).get('failed', '?')} failed"
                        + (f", missing {', '.join(report['missing_required'])}"
                           if report.get("missing_required") else "") + ")")
    if report.get("src_dirty"):
        problems.append("recorded with uncommitted src/ changes")
    ran_at = datetime.fromisoformat(report["ran_at"])
    age_days = (now - ran_at).total_seconds() / 86400
    if age_days > policy["max_age_days"]:
        problems.append(f"{age_days:.0f} days old (max {policy['max_age_days']})")
    commit = report.get("commit", "")
    ancestor = history.is_ancestor(commit, ref)
    lines = None
    if ancestor is None:
        problems.append(f"commit {commit[:9]} not in this history (fetch-depth 0?)")
    elif not ancestor:
        problems.append(f"commit {commit[:9]} is not an ancestor of {ref[:9]}")
    else:
        lines = history.src_lines_changed(commit, ref)
        if lines > policy["max_src_lines_changed"]:
            problems.append(f"{lines:,} src lines changed since (max {policy['max_src_lines_changed']:,})")
    summary = (f"{report.get('result', '?')} {age_days:.1f}d ago at {commit[:9]}"
               + (f", {lines:,} src lines since" if lines is not None else ""))
    return Verdict(tier_name, required, not problems, summary, problems)


def evaluate_all(config: dict, ref: str, now: datetime, history: History,
                 reports_dir: Path = REPORTS) -> list[Verdict]:
    verdicts = []
    for name, tier in config["tiers"].items():
        path = reports_dir / f"{name}.json"
        report = json.loads(path.read_text()) if path.exists() else None
        verdicts.append(evaluate(name, tier, report, config["policy"], ref, now, history))
    return verdicts


def print_verdicts(verdicts: list[Verdict]) -> None:
    width = max(len(v.tier) for v in verdicts)
    for v in verdicts:
        mark = "ok  " if v.ok else ("FAIL" if v.required else "warn")
        print(f"{mark}  {v.tier:<{width}}  {'required' if v.required else 'optional'}  {v.summary}")
        for problem in v.problems:
            print(f"{'':6}{'':<{width}}  - {problem}")


def resolve(ref: str) -> str:
    out = git("rev-parse", ref)
    if out.returncode != 0:
        sys.exit(f"unknown ref {ref!r}")
    return out.stdout.strip()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    rec = sub.add_parser("record", help="run a tier and write its report")
    rec.add_argument("tier")
    for name in ("status", "check"):
        p = sub.add_parser(name, help="report freshness" + (" (exit 1 if a required tier is stale)"
                                                           if name == "check" else ""))
        p.add_argument("--ref", default="HEAD", help="the commit being released (default HEAD)")
    args = parser.parse_args(argv)

    config = load_config()
    if args.cmd == "record":
        return record(args.tier, config)
    ref = resolve(args.ref)
    verdicts = evaluate_all(config, ref, datetime.now(timezone.utc), git_history())
    print_verdicts(verdicts)
    stale = [v.tier for v in verdicts if v.required and not v.ok]
    if args.cmd == "check" and stale:
        print(f"\nrequired e2e tiers not fresh: {', '.join(stale)}. "
              f"Run `make e2e-record TIER=<tier>` and commit the report.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
