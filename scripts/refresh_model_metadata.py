#!/usr/bin/env python3
"""Refresh src/overcode/data/model_metadata.json from models.dev (#473).

    python scripts/refresh_model_metadata.py            # fetch + rewrite
    python scripts/refresh_model_metadata.py --from api.json   # offline
    python scripts/refresh_model_metadata.py --check    # diff only, no write

The transcoder itself lives in ``overcode.model_metadata`` (unit-tested);
this is the fetch-and-write wrapper. Commit the regenerated file — it ships
inside the wheel as package data, and the lookups degrade to "unknown" for
anything not in it.
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from overcode.model_metadata import (  # noqa: E402
    MODELS_DEV_URL,
    SNAPSHOT_PATH,
    dump_snapshot,
    fetch_models_dev,
    transcode_models_dev,
)


def _summarise_diff(old: dict, new: dict) -> str:
    old_models = old.get("models", {}) if isinstance(old, dict) else {}
    new_models = new.get("models", {})
    added = sorted(set(new_models) - set(old_models))
    removed = sorted(set(old_models) - set(new_models))
    changed = sorted(k for k in set(new_models) & set(old_models) if new_models[k] != old_models[k])
    lines = [
        f"models: {len(old_models)} -> {len(new_models)} "
        f"(+{len(added)} / -{len(removed)} / ~{len(changed)} changed)"
    ]
    for label, keys in (("added", added), ("removed", removed), ("changed", changed)):
        if keys:
            shown = ", ".join(keys[:12]) + (" …" if len(keys) > 12 else "")
            lines.append(f"  {label}: {shown}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="source_file", type=Path, help="transcode a local models.dev api.json instead of fetching")
    ap.add_argument("--out", type=Path, default=SNAPSHOT_PATH, help=f"output path (default: {SNAPSHOT_PATH})")
    ap.add_argument("--check", action="store_true", help="print the diff summary and exit non-zero if it would change; write nothing")
    args = ap.parse_args()

    if args.source_file:
        raw = json.loads(args.source_file.read_text())
        origin = str(args.source_file)
    else:
        raw = fetch_models_dev(MODELS_DEV_URL)
        origin = MODELS_DEV_URL

    snapshot = transcode_models_dev(raw, fetched_at=date.today().isoformat())
    text = dump_snapshot(snapshot)

    try:
        existing = json.loads(args.out.read_text())
    except (OSError, ValueError):
        existing = {}

    print(f"source: {origin}")
    print(_summarise_diff(existing, snapshot))

    unchanged = existing.get("models") == snapshot["models"] if isinstance(existing, dict) else False
    if args.check:
        print("up to date" if unchanged else "would change")
        return 0 if unchanged else 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text)
    print(f"wrote {args.out} ({len(text) // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
