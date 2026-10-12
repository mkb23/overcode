#!/usr/bin/env python3
"""Code-quality metrics for src/overcode: one summary, raw data in build/metrics.

    make metrics            # this script (complexity, dead code, lint, size)
    make coverage           # the unit suite under branch + thread coverage

radon and vulture run through `uvx` when they are not installed, so nothing
here is a project dependency. The summary is meant to be compared run to
run; the JSON next to it has the detail.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "overcode"
OUT = ROOT / "build" / "metrics"


def tool(name: str) -> list[str]:
    """Run ``name`` from this environment if installed, else through uvx."""
    if importlib.util.find_spec(name) is not None:
        return [sys.executable, "-m", name]
    if shutil.which("uvx"):
        return ["uvx", name]
    sys.exit(f"{name} is not installed and uvx is not available")


def run(cmd: list[str]) -> str:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True).stdout


def rel(path: str) -> str:
    return path.split("src/overcode/")[-1]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    # Size
    raw = json.loads(run([*tool("radon"), "raw", "-s", "-j", str(SRC)]) or "{}")
    (OUT / "radon_raw.json").write_text(json.dumps(raw))
    files = {k: v for k, v in raw.items() if isinstance(v, dict)}
    sloc = sum(v["sloc"] for v in files.values())
    biggest = sorted(((v["loc"], rel(k)) for k, v in files.items()), reverse=True)[:8]

    # Complexity (functions ranked C or worse) and maintainability
    cc = json.loads(run([*tool("radon"), "cc", "-s", "-n", "C", "-j", str(SRC)]) or "{}")
    (OUT / "radon_cc.json").write_text(json.dumps(cc))
    funcs = [
        (it["complexity"], it["rank"], rel(f), it.get("classname") or "", it["name"], it["lineno"])
        for f, items in cc.items() for it in items if isinstance(it, dict)
    ]
    funcs.sort(reverse=True)
    ranks = Counter(f[1] for f in funcs)
    mi = json.loads(run([*tool("radon"), "mi", "-j", str(SRC)]) or "{}")
    (OUT / "radon_mi.json").write_text(json.dumps(mi))
    low_mi = sorted((v["mi"], rel(k)) for k, v in mi.items() if isinstance(v, dict))[:8]

    # Dead code (high confidence only; 60% is mostly framework callbacks)
    vulture = run([*tool("vulture"), str(SRC), "--min-confidence", "80"])
    (OUT / "vulture.txt").write_text(vulture)
    dead = [line for line in vulture.splitlines()
            if line.strip() and "TmuxInterface" not in line]  # TYPE_CHECKING imports

    # Lint
    ruff = run(["ruff", "check", "src", "tests", "--statistics"])
    (OUT / "ruff.txt").write_text(ruff)
    m = re.search(r"Found (\d+) error", ruff)
    ruff_total = int(m.group(1)) if m else 0

    # Tests
    test_files = list((ROOT / "tests").rglob("test_*.py"))
    test_count = sum(len(re.findall(r"^\s*(?:async )?def test_", p.read_text(), re.M))
                     for p in test_files)

    summary = {
        "sloc": sloc, "modules": len(files),
        "complex_functions": dict(ranks), "ruff_errors": ruff_total,
        "dead_code_high_confidence": len(dead), "test_functions": test_count,
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))

    print(f"Source: {sloc:,} SLOC in {len(files)} modules; tests: {test_count:,} test functions")
    print(f"Lint (ruff): {ruff_total} errors   Dead code (vulture ≥80%): {len(dead)}")
    print(f"Functions ranked C+ by cyclomatic complexity: {len(funcs)} {dict(sorted(ranks.items()))}")
    print("\nMost complex functions:")
    for c, r, f, cls, name, line in funcs[:12]:
        print(f"  {c:4} {r}  {f}:{line} {cls + '.' if cls else ''}{name}")
    print("\nLargest modules (lines):")
    for loc, f in biggest:
        print(f"  {loc:6}  {f}")
    print("\nLowest maintainability index (0-100):")
    for score, f in low_mi:
        print(f"  {score:5.1f}  {f}")
    if dead:
        print("\nDead code:")
        for line in dead:
            print(f"  {rel(line)}")
    print(f"\nDetail: {OUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
